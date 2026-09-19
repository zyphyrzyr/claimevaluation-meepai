"""
L1 庭审中止：POST /api/moot/{case_id}/stop

为什么要这个文件：庭审跑起来是 SSE 长流，真实模式下一轮就是一次十秒级的
LLM 调用。以前没有任何办法让它停下来——界面上只能干等，或者刷新页面。
现在补了中止，这里钉住四件事：

  1. 空闲时调它是无害的空操作（前端会点到已经跑完的庭审）
  2. 别人的案件中止不了（归属校验真的生效，不是只看路径里有 case_id）
  3. 中止真的会打断正在跑的庭审
  4. 中止 = 作废：**不回写系数、不改评分**。这个语义一旦反了，
     用户以为停了、评分却悄悄改了，是最难发现的那种 bug

并发那一条用两个 TestClient：同一个 client 上还挂着没读完的 SSE 流时，
再发一个请求会被那道流堵住——等它读完，庭审早就跑完了，stop 永远打不中。
"""

import threading
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from core import moot_service
from main import app
from routers.moot import _MOOT_STOP, _pump, _register_stop, _release_stop

PW = "moot-stop-1234"


def _email(tag: str) -> str:
    return f"{tag}-{uuid.uuid4().hex[:10]}@example.com"


@pytest.fixture(scope="module", autouse=True)
def _real_auth():
    """摘掉 conftest 注入的默认登录用户，用真实 cookie 鉴权。

    不摘的话 require_user 永远返回同一个人，「别人的案件中止不了」这条
    断言会假绿——越权测试必须走真实的两份身份。
    """
    from core.auth import current_user_optional, require_user

    saved = {}
    for dep in (require_user, current_user_optional):
        if dep in app.dependency_overrides:
            saved[dep] = app.dependency_overrides.pop(dep)
    yield
    app.dependency_overrides.update(saved)


def _register(tag: str) -> TestClient:
    c = TestClient(app)
    r = c.post("/api/auth/register", json={"email": _email(tag), "password": PW})
    assert r.status_code == 200, r.text
    return c


@pytest.fixture(scope="module")
def owner():
    with _register("mootOwner") as c:
        yield c


@pytest.fixture(scope="module")
def other():
    with _register("mootOther") as c:
        yield c


def _make_case(client) -> str:
    r = client.post("/api/cases", json={
        "name": "中止测试案",
        "cause_type": "商标侵权",
        "goal_type": "要钱",
        "client_org": "我方公司",
        "defendant_name": "被告公司",
        "case_description": "被告在电商平台销售带有我方注册商标的商品。",
        "evidence_texts": "商标注册证、公证购买记录、平台销量截图等材料齐备。",
    })
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _seed_scores(case_id: str):
    """直接塞一份评估结果，省得真跑一遍评估（内嵌庭审要求先有 final 分）。"""
    from core.case_context import CaseContext
    from core.database import Case, SessionLocal

    db = SessionLocal()
    try:
        case = db.query(Case).filter(Case.id == case_id).first()
        ctx = CaseContext.from_dict(case.context_json or {})
        ctx.case_id = case_id
        ctx.scores = {"final": 70.0, "legal_feasibility": 70.0, "business": 70.0}
        case.context_json = ctx.to_dict()
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------- 1. 空操作

def test_stop_when_idle_is_noop(owner):
    """没在跑的庭审：返回 stopped=false，而不是报错。

    前端很可能因为网络延迟点到刚跑完的庭审，这里挂了会让界面莫名其妙红一片。
    """
    r = owner.post(f"/api/moot/{_make_case(owner)}/stop")
    assert r.status_code == 200, r.text
    assert r.json()["stopped"] is False


# ---------------------------------------------------------------- 2. 归属校验

def test_stop_other_users_case_denied(owner, other):
    """B 中止不了 A 的案件：一律 404（不泄露案件存在性）。"""
    cid = _make_case(owner)
    r = other.post(f"/api/moot/{cid}/stop")
    assert r.status_code == 404, r.text


def test_stop_requires_login(other):
    """未登录中止不了：401 而不是 500（依赖项缺口会变成 AttributeError）。"""
    from core.auth import current_user_optional, require_user

    cid = _make_case(other)
    saved = {}
    for dep in (require_user, current_user_optional):
        if dep in app.dependency_overrides:
            saved[dep] = app.dependency_overrides.pop(dep)

    anon = TestClient(app)
    try:
        r = anon.post(f"/api/moot/{cid}/stop")
        assert r.status_code == 401, f"未登录应当 401，实际 {r.status_code} {r.text}"
    finally:
        app.dependency_overrides.update(saved)


# ---------------------------------------------------------------- 3. 标志登记

def test_stop_flag_lifecycle():
    """登记 → 未置位 → 置位 → 释放。中止机制的骨架。"""
    ev = _register_stop("flag-probe")
    try:
        assert "flag-probe" in _MOOT_STOP
        assert ev.is_set() is False
        ev.set()
        assert ev.is_set() is True
    finally:
        _release_stop("flag-probe")
    assert "flag-probe" not in _MOOT_STOP


def test_release_is_idempotent():
    _release_stop("never-registered")  # 不该抛
    assert True


# ---------------------------------------------------------------- 4. _pump 的时序
#
# 这两条钉住「中止到底停在哪一步」。之所以用假生成器而不是真的跑一遍庭审：
# httpx 的 ASGI 传输不会增量返回 SSE（整个响应体跑完才交给调用方），TestClient
# 里根本抓不到「跑到一半」这一刻——mock 模式下庭审更是瞬间跑完，等 stop 请求
# 发出去，标志早就被 finally 释放了。所以在生成器里卡一道闸门，把时序握在手里。

def test_pump_stops_after_current_round():
    """按下停止后：当前这轮说完就停，不会继续取下一轮。

    这是能对用户承诺的全部——真实模式下一轮就是一次 LLM 调用，调用中途打断不了。
    """
    def gen():
        for i in range(1, 6):
            yield {"event": "round", "index": i}
        yield {"event": "moot_finished", "rounds": []}

    stop_ev = threading.Event()
    seen = []

    def on_event(e):
        seen.append(e)
        if e.get("event") == "round" and e["index"] == 2:
            stop_ev.set()  # 第二轮落地的瞬间按下停止

    stopped = _pump(gen(), stop_ev, on_event, {"rounds": 0})

    assert stopped is True
    assert [e["index"] for e in seen if e.get("event") == "round"] == [1, 2], "停止后又多跑了轮次"
    assert seen[-1]["event"] == "moot_stopped", f"没有以中止事件收尾：{seen}"
    assert seen[-1]["rounds"] == 2, "中止事件里带的轮次不对"
    assert not any(e.get("event") == "moot_finished" for e in seen), "中止了却还是跑完了"


def test_pump_completes_when_never_stopped():
    """没按停止就正常跑完：返回 False，不发中止事件。"""
    def gen():
        for i in range(1, 4):
            yield {"event": "round", "index": i}
        yield {"event": "moot_finished", "rounds": []}

    counter = {"rounds": 0}
    seen = []

    stopped = _pump(gen(), threading.Event(), seen.append, counter)

    assert stopped is False
    assert counter["rounds"] == 3, "轮次计数不对"
    assert seen[-1]["event"] == "moot_finished"
    assert not any(e.get("event") == "moot_stopped" for e in seen)


def test_pump_without_stop_flag_never_stops():
    """纯独立演练（无 case_id）拿不到停止标志：一路跑完，不受任何影响。"""
    counter = {"rounds": 0}
    seen = []
    stopped = _pump(({"event": "round", "index": i} for i in range(2)), None, seen.append, counter)
    assert stopped is False
    assert counter["rounds"] == 2


# ---------------------------------------------------------------- 5. 端到端

def test_stop_aborts_running_moot(owner, monkeypatch):
    """真的按下去：流以 moot_stopped 收尾，评分与系数都没被改。

    用假生成器顶替 moot_service.run_embedded，在里面卡一道闸门 —— 真实模式
    一轮十秒，这里把它压成一个可控的「等测试按停止」的信号，时序才是确定的。
    """
    cid = _make_case(owner)
    _seed_scores(cid)

    started = threading.Event()
    release = threading.Event()

    def _fake_run(ctx, shared_legal_context=""):
        def gen():
            started.set()
            yield {"event": "round", "step": 1, "step_name": "开庭陈述",
                   "role": "plaintiff", "role_name": "原告", "content": "第一句话。第二句话。"}
            release.wait(timeout=30)  # 卡住：等测试按下停止
            yield {"event": "round", "step": 2, "step_name": "被告答辩",
                   "role": "defendant", "role_name": "被告", "content": "第三句话。"}
            yield {"event": "moot_finished", "rounds": [], "correction_coeff": 0.85}
        return gen()

    monkeypatch.setattr(moot_service, "run_embedded", _fake_run)

    events = []
    done = threading.Event()

    def _run():
        try:
            with owner.stream("POST", f"/api/moot/{cid}/run", json={}) as resp:
                for line in resp.iter_lines():
                    if line.startswith("data: "):
                        events.append(line[6:])
        finally:
            done.set()

    threading.Thread(target=_run, daemon=True).start()

    assert started.wait(timeout=10), "庭审没有开跑"
    # 等「本次运行」登记进来——太早按会撞在还没登记上
    for _ in range(200):
        if cid in _MOOT_STOP:
            break
        time.sleep(0.02)
    assert cid in _MOOT_STOP, "庭审开跑了却没登记停止标志"

    # 另起一个 client 按停止：原 client 上还挂着没读完的 SSE 流
    stopper = TestClient(app)
    stopper.cookies.update(owner.cookies)
    r = stopper.post(f"/api/moot/{cid}/stop")
    assert r.status_code == 200, r.text
    assert r.json()["stopped"] is True, "明明在跑，却说没在跑"

    release.set()  # 放行当前这一轮
    assert done.wait(timeout=30), "中止后流没有结束"

    body = "".join(events)
    assert "moot_stopped" in body, f"流里没有中止事件：{body[:400]}"
    assert "moot_finished" not in body, "中止了却还是跑完了"
    assert "scores_updated" not in body, "中止了却回写了评分"

    got = owner.get(f"/api/moot/{cid}")
    assert got.status_code == 200, got.text
    assert got.json()["correction_coeff"] in (1.0, 1, None), "中止却回写了系数"

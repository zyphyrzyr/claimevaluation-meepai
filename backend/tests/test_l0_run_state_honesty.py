"""
L0 评估运行状态的「诚实性」——run-state 不得谎报 running

背景（真实事故）：评估运行状态有两份来源——
  · RUNS（内存），唯一可实际操作的那份：pause / resume / stop 都只看它
  · case.status（数据库），一份持久化镜像，没人负责在进程死亡时修它
进程重启或运行中抛异常后，RUNS 被清空而 case.status 永远停在 'evaluating'。
原实现里 run-state 在 RUNS 找不到句柄时会回退去读这份陈旧镜像并把它翻译成
"running"，于是前端显示「评估进行中」并挂出暂停/终止控制条，用户一点暂停就撞
404「当前没有进行中的评估」——两个端点自相矛盾，报错还以裸 JSON 形式甩到界面上。

本文件锁死三件事：
  1. run-state 不再拿陈旧镜像冒充 running（核心回归）
  2. 异常路径与启动自愈不再留下 evaluating 僵尸状态
  3. 终态推导口径唯一（derive_status_from_ctx 被两处复用）

运行：backend/ 目录 pytest tests/test_l0_run_state_honesty.py -v
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("USE_MOCK", "True")

from core.case_context import CaseContext
from routers.evaluation import (RUNS, RunController, derive_status_from_ctx,
                                heal_stuck_evaluations)


# ============================================================
# helpers
# ============================================================

def _ctx() -> CaseContext:
    return CaseContext(case_id="state-1", case_description="原告持有注册商标，被告销售近似标识商品。",
                       cause_type="商标侵权", goal_type="要钱")


def _make_case(case_id: str, status: str, ctx: CaseContext | None) -> None:
    """种一个案件；ctx 为 None 时 context_json 留空（模拟什么都没跑出来）"""
    from core.database import Case, SessionLocal, init_db
    init_db()
    db = SessionLocal()
    try:
        db.query(Case).filter(Case.id == case_id).delete()
        db.add(Case(id=case_id, name=f"状态用例-{case_id}", cause_type="商标侵权",
                    goal_type="要钱", status=status,
                    context_json=(ctx.to_dict() if ctx else {})))
        db.commit()
    finally:
        db.close()


def _drop_case(case_id: str) -> None:
    from core.database import Case, SessionLocal
    db = SessionLocal()
    try:
        db.query(Case).filter(Case.id == case_id).delete()
        db.commit()
    finally:
        db.close()


def _status_of(case_id: str) -> str:
    from core.database import Case, SessionLocal
    db = SessionLocal()
    try:
        return db.query(Case).filter(Case.id == case_id).first().status
    finally:
        db.close()


@pytest.fixture
def client():
    """先起服务再种数据。

    启动钩子会跑僵尸状态自愈，所以「需要保持 evaluating 的脏数据」必须在这之后
    种进去，否则会被自愈提前修掉，用例就测不到 run-state 的诚实性了。
    """
    from fastapi.testclient import TestClient

    from core.database import init_db
    from main import app
    init_db()
    with TestClient(app) as c:
        yield c


# ============================================================
# 1. 终态推导：口径唯一，不能靠猜
# ============================================================

class TestDeriveStatusFromCtx:

    def test_nothing_produced_goes_back_to_pending(self):
        """一个从没跑出东西的案件应回到 pending，而不是被标成「部分完成」——
        后者比留在 evaluating 更误导：用户会以为有结果可看。"""
        assert derive_status_from_ctx(_ctx()) == "pending"

    def test_final_score_means_completed(self):
        ctx = _ctx()
        ctx.set_dimension("synthesize", {"score": 72.5}, status="ok")
        ctx.scores = {"final": 72.5}
        assert derive_status_from_ctx(ctx) == "completed"

    def test_output_but_no_final_means_partial(self):
        """跑到一半被打断：有维度结果但合成没出分"""
        ctx = _ctx()
        ctx.set_dimension("rights", {"score": 80}, status="ok")
        assert derive_status_from_ctx(ctx) == "partial"

    def test_block_level_wins_over_final(self):
        """红线拦截优先于分数——与 work() 收尾处的判定顺序保持一致"""
        ctx = _ctx()
        ctx.set_dimension("red_gate", {"blocked": True}, status="blocked")
        ctx.scores = {"final": 80.0}
        ctx.recommendation = {"level": "block"}
        assert derive_status_from_ctx(ctx) == "blocked"

    def test_failed_dimension_counts_as_output(self):
        """失败也算「跑过了」：否则一个全失败的案件会被当成从没启动过"""
        ctx = _ctx()
        ctx.set_dimension("infringement", {}, status="failed")
        assert derive_status_from_ctx(ctx) == "partial"


# ============================================================
# 2. 核心回归：run-state 不得拿陈旧镜像冒充 running
# ============================================================

class TestRunStateNeverLiesAboutRunning:

    @pytest.fixture
    def stale_case(self, client):
        """模拟进程重启后的现场：DB 说 evaluating，RUNS 里没有句柄"""
        _make_case("stale-evaluating", "evaluating", None)
        RUNS.pop("stale-evaluating", None)
        yield "stale-evaluating"
        RUNS.pop("stale-evaluating", None)
        _drop_case("stale-evaluating")

    def test_run_state_reports_interrupted_not_running(self, client, stale_case):
        """这条就是事故本身的断言：以前这里返回 'running'，前端据此挂出控制条"""
        r = client.get(f"/api/evaluation/{stale_case}/run-state")
        assert r.status_code == 200
        assert r.json()["run"] == "interrupted"
        assert r.json()["run"] != "running"

    def test_run_state_and_pause_no_longer_contradict(self, client, stale_case):
        """两端点必须一致：以前一个说 running、另一个 404，界面上就成了
        「显示正在运行」+「点暂停报错」的组合。现在前者如实报中断，后者照旧 404。"""
        run_state = client.get(f"/api/evaluation/{stale_case}/run-state").json()["run"]
        pause = client.post(f"/api/evaluation/{stale_case}/pause")

        assert run_state == "interrupted"
        assert pause.status_code == 404
        assert run_state not in ("running", "paused"), "既然 pause 说没有句柄，run-state 就不能说在跑"

    def test_live_controller_wins_over_db_status(self, client, stale_case):
        """有句柄时以 RUNS 为准（DB 镜像说什么都不算）"""
        ctrl = RunController()
        ctrl.status = "paused"
        RUNS[stale_case] = ctrl
        try:
            assert client.get(f"/api/evaluation/{stale_case}/run-state").json()["run"] == "paused"
            # 有句柄了，暂停就该成功——对照组：证明上面的 404 是因为真的没有运行
            assert client.post(f"/api/evaluation/{stale_case}/pause").status_code == 200
        finally:
            RUNS.pop(stale_case, None)

    def test_finished_case_still_reports_done(self, client):
        """别误伤正常终态：completed 仍应报 done"""
        _make_case("done-case", "completed", None)
        try:
            assert client.get("/api/evaluation/done-case/run-state").json()["run"] == "done"
        finally:
            _drop_case("done-case")


# ============================================================
# 3. 启动自愈：修掉僵尸状态
# ============================================================

class TestHealStuckEvaluations:

    def test_heals_stuck_case_with_results_to_completed(self):
        """有 final 的僵尸 → 已完成（真实事故里那个 72.5 分的案件就是这一类）"""
        ctx = _ctx()
        ctx.set_dimension("synthesize", {"score": 72.5}, status="ok")
        ctx.scores = {"final": 72.5}
        _make_case("heal-completed", "evaluating", ctx)
        try:
            from core.database import SessionLocal
            db = SessionLocal()
            try:
                fixed = heal_stuck_evaluations(db)
            finally:
                db.close()
            assert any(f["case_id"] == "heal-completed" and f["to"] == "completed" for f in fixed)
            assert _status_of("heal-completed") == "completed"
        finally:
            _drop_case("heal-completed")

    def test_heals_stuck_case_without_results_to_pending(self):
        _make_case("heal-empty", "evaluating", None)
        try:
            from core.database import SessionLocal
            db = SessionLocal()
            try:
                heal_stuck_evaluations(db)
            finally:
                db.close()
            assert _status_of("heal-empty") == "pending"
        finally:
            _drop_case("heal-empty")

    def test_leaves_healthy_statuses_alone(self):
        """自愈只碰 evaluating，别把正常案件改坏"""
        for cid, st in (("heal-ok-1", "completed"), ("heal-ok-2", "pending"), ("heal-ok-3", "aborted")):
            _make_case(cid, st, None)
        try:
            from core.database import SessionLocal
            db = SessionLocal()
            try:
                heal_stuck_evaluations(db)
            finally:
                db.close()
            assert _status_of("heal-ok-1") == "completed"
            assert _status_of("heal-ok-2") == "pending"
            assert _status_of("heal-ok-3") == "aborted"
        finally:
            for cid in ("heal-ok-1", "heal-ok-2", "heal-ok-3"):
                _drop_case(cid)

    def test_startup_hook_actually_runs_the_heal(self):
        """验证 main.py 的启动钩子真的接了自愈——只测函数本体的话，
        忘了接线（自愈写了但没人调）会全绿通过。"""
        from fastapi.testclient import TestClient

        from core.database import init_db
        from main import app
        init_db()
        _make_case("heal-on-startup", "evaluating", None)   # 故意留在启动前
        try:
            with TestClient(app):
                pass          # 进入上下文即触发 startup
            assert _status_of("heal-on-startup") == "pending", "启动钩子没有跑自愈"
        finally:
            _drop_case("heal-on-startup")


# ============================================================
# 4. 端到端：自愈之后同一个案件不再自相矛盾
# ============================================================

class TestAfterHealThePageIsConsistent:

    def test_heal_then_run_state_is_no_longer_running(self, client):
        """把事故现场走一遍：自愈 → 案件不再显示评估中，也不再挂出控制条"""
        ctx = _ctx()
        ctx.set_dimension("synthesize", {"score": 72.5}, status="ok")
        ctx.scores = {"final": 72.5}
        _make_case("e2e-stale", "evaluating", ctx)
        try:
            # 自愈前：如实报 interrupted（前端据此不进 running 态）
            assert client.get("/api/evaluation/e2e-stale/run-state").json()["run"] == "interrupted"

            from core.database import SessionLocal
            db = SessionLocal()
            try:
                heal_stuck_evaluations(db)
            finally:
                db.close()

            # 自愈后：状态与 run-state 一致地表示「已完成」
            assert _status_of("e2e-stale") == "completed"
            assert client.get("/api/evaluation/e2e-stale/run-state").json()["run"] == "done"
        finally:
            _drop_case("e2e-stale")

"""
L1 方案 B 后台自动召回评测（routers.evaluation._auto_recall）

覆盖点与动机：
1. 查询词是**确定性拼接**（案由 + 目标 + 案情截断），不用 LLM——
   否则同一份输入两次评估会命中不同材料，「可复现」的卖点就废了；
2. 分数阈值过滤，且缺 score 字段的命中按 0 处理（不静默塞进注入集）；
3. 检索异常**降级为空集**而非抛出——召回是增强项，不该让整个评估起不来；
4. case_id 与 scope=None（双库）按原样透传给 search_knowledge，
   防跨案污染的规则由 service 层保证，这里只保证参数没被写死/漏传；
5. 重跑时**覆盖**而非追加注入集，否则第二次评估会带着上一次的旧材料。

检索出口统一用 monkeypatch 拦 `core.knowledge.search_knowledge`，
断言的是「真正发出去的查询词与参数」，而不是「返回的假数据」。
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("USE_MOCK", "True")

from core.case_context import CaseContext
from core.config import (AUTO_RECALL_MIN_SCORE_REAL, AUTO_RECALL_MIN_SCORE_HASH,
                         AUTO_RECALL_TOP_K, auto_recall_min_score)
from core.database import Case, SessionLocal, init_db
from core import knowledge as kb
from core.knowledge import embeddings as emb
from routers.evaluation import _auto_recall


# ============================================================
# fixtures
# ============================================================

@pytest.fixture(scope="module")
def db():
    init_db()
    session = SessionLocal()
    yield session
    session.close()


class _FakeSearch:
    """记录实参的假检索。hits 传 Exception 时模拟检索失败。"""

    def __init__(self, hits):
        self.hits = hits
        self.calls = []

    def __call__(self, db, query, *, case_id=None, scope=None, top_k=5):
        self.calls.append({
            "query": query, "case_id": case_id,
            "scope": scope, "top_k": top_k,
        })
        if isinstance(self.hits, BaseException):
            raise self.hits
        return self.hits


def _patch_search(monkeypatch, hits):
    fake = _FakeSearch(hits)
    monkeypatch.setattr(kb, "search_knowledge", fake)
    return fake


def _new_case(db, cause="商标侵权", desc="被告在电商平台销售带有我方注册商标的商品。",
              name="自动召回用例"):
    c = Case(name=name, cause_type=cause, goal_type="要钱", case_description=desc)
    db.add(c)
    db.flush()
    return c


def _hit(eid, score, scope="case", title=None):
    return {
        "id": eid, "title": title or f"材料-{eid}", "scope": scope,
        "source_type": "A", "score": score,
        "content": "内容" * 200,          # 远超 200，用于验证 snippet 截断
        "matched_chunk": "命中块",
    }


def _last_event(ctx, event_type="knowledge_auto_recall"):
    hits = [e for e in ctx.audit_trail if e["event_type"] == event_type]
    assert hits, f"审计轨迹里没有 {event_type}：{[e['event_type'] for e in ctx.audit_trail]}"
    return hits[-1]


# ============================================================
# 1. 查询词构造（确定性）
# ============================================================

class TestQueryConstruction:

    def test_query_is_cause_goal_and_description(self, db, monkeypatch):
        """查询词 = 案由 + 业务目标 + 案情描述，纯拼接，不含任何随机/时间成分。"""
        fake = _patch_search(monkeypatch, [])
        case = _new_case(db, cause="著作权侵权", desc="被告未经许可复制我方美术作品并用于包装。")
        ctx = CaseContext(case_id=case.id, goal_type="要名")

        _auto_recall(db, case, ctx)

        assert len(fake.calls) == 1
        assert fake.calls[0]["query"] == (
            "著作权侵权 要名 被告未经许可复制我方美术作品并用于包装。"
        )

    def test_description_truncated_at_500_chars(self, db, monkeypatch):
        """长案情只取前 500 字，避免查询词把向量化拖垮/稀释语义。"""
        fake = _patch_search(monkeypatch, [])
        case = _new_case(db, desc="甲" * 900)
        ctx = CaseContext(case_id=case.id, goal_type="要钱")

        _auto_recall(db, case, ctx)

        query = fake.calls[0]["query"]
        desc_part = query.split(" ", 2)[2]
        assert len(desc_part) == 500
        assert desc_part == "甲" * 500

    def test_empty_description_skips_search_and_still_audits(self, db, monkeypatch):
        """案情为空时不发检索请求，但审计要留痕（否则事后无法解释为何零注入）。"""
        fake = _patch_search(monkeypatch, [])
        case = Case(name="空案情", cause_type="", goal_type="", case_description="")
        db.add(case)
        db.flush()
        ctx = CaseContext(case_id=case.id, goal_type="")

        n = _auto_recall(db, case, ctx)

        assert n == 0
        assert fake.calls == []
        assert ctx.injected_knowledge == []
        assert "未执行自动召回" in _last_event(ctx)["effect"]

    def test_blank_only_fields_are_skipped_not_joined(self, db, monkeypatch):
        """空片段不参与拼接，避免出现连续空格污染查询词。"""
        fake = _patch_search(monkeypatch, [])
        case = Case(name="仅案由", cause_type="不正当竞争", goal_type="", case_description="   ")
        db.add(case)
        db.flush()
        ctx = CaseContext(case_id=case.id, goal_type="")

        _auto_recall(db, case, ctx)

        # 案由非空 → 仍会检索，但查询词不该带空格尾巴
        assert fake.calls[0]["query"] == "不正当竞争"


# ============================================================
# 2. 阈值过滤与注入集结构
# ============================================================

class TestSelectionAndShape:

    def test_threshold_filters_low_score_hits(self, db, monkeypatch):
        # 合格线随后端自适应，本条断言的是 bge-m3 刻度（0.3），
        # 必须把后端 pin 死——否则哈希模式下合格线是 0.12，k3 会「意外过关」，
        # 测试变绿却什么都没验到（正是变异检验最容易漏的一类假绿）。
        monkeypatch.setattr(emb, "use_mock_embedding", lambda: False)
        _patch_search(monkeypatch, [
            _hit("k1", 0.92), _hit("k2", 0.31),
            _hit("k3", AUTO_RECALL_MIN_SCORE_REAL - 0.01), _hit("k4", 0.05),
        ])
        case = _new_case(db)
        ctx = CaseContext(case_id=case.id)

        n = _auto_recall(db, case, ctx)

        assert n == 2
        assert [e["id"] for e in ctx.injected_knowledge] == ["k1", "k2"]
        assert "2/4" in _last_event(ctx)["effect"]

    def test_hit_without_score_is_dropped(self, db, monkeypatch):
        """缺 score 字段按 0 处理，不允许「没有分也算命中」蒙混进注入集。"""
        no_score = _hit("k9", 0.0)
        no_score.pop("score")
        _patch_search(monkeypatch, [no_score, _hit("k1", 0.8)])
        case = _new_case(db)
        ctx = CaseContext(case_id=case.id)

        assert _auto_recall(db, case, ctx) == 1
        assert [e["id"] for e in ctx.injected_knowledge] == ["k1"]

    def test_injected_entry_shape_matches_manual_injection(self, db, monkeypatch):
        """注入快照逐条字段与原手动注入一致——下游 evaluate_nodes 靠这些键拼 prompt。"""
        _patch_search(monkeypatch, [_hit("k1", 0.9, scope="global", title="裁判口径")])
        case = _new_case(db)
        ctx = CaseContext(case_id=case.id)

        _auto_recall(db, case, ctx)

        entry = ctx.injected_knowledge[0]
        assert set(entry) == {"id", "title", "scope", "source_type", "snippet"}
        assert entry["title"] == "裁判口径"
        assert entry["scope"] == "global"
        assert entry["source_type"] == "A"
        assert len(entry["snippet"]) == 200      # 原文 400 字被截断

    def test_rerun_overwrites_previous_injection(self, db, monkeypatch):
        """重跑必须覆盖而不是追加，否则第二次评估会带着第一次的旧材料。"""
        _patch_search(monkeypatch, [_hit("new1", 0.9)])
        case = _new_case(db)
        ctx = CaseContext(case_id=case.id,
                          injected_knowledge=[{"id": "old1", "title": "过期材料"}])

        _auto_recall(db, case, ctx)

        assert [e["id"] for e in ctx.injected_knowledge] == ["new1"]


# ============================================================
# 2b. 合格线随后端自适应
# ============================================================

class TestThresholdAdaptsToBackend:
    """同一条 0.3 不能通吃两个后端。

    哈希兜底向量只数字符二元组，余弦相似度系统性偏低：实测全局经验库的
    短条目（20-40 字）只有 0.13-0.23，而 0.3 这条线当初是按 bge-m3
    （相关文本 0.5-0.9）标定的。共用一条线的后果是：长条目侥幸过关，
    短条目全军覆没——「经验沉淀」在演示里等于没生效，界面还只显示
    「自动召回 0 条」，与空库无从区分。
    """

    def test_hash_backend_lowers_threshold_to_let_short_entries_in(self, db, monkeypatch):
        """哈希模式下 0.13-0.23 的短条目必须能进注入集（这正是修复的目标区间）。"""
        monkeypatch.setattr(emb, "use_mock_embedding", lambda: True)
        _patch_search(monkeypatch, [
            _hit("exp1", 0.23, scope="global", title="服装类商标判赔区间"),
            _hit("exp2", 0.13, scope="global", title="判赔规模经验"),
            _hit("noise", 0.05, scope="global", title="八竿子打不着"),
        ])
        case = _new_case(db)
        ctx = CaseContext(case_id=case.id)

        n = _auto_recall(db, case, ctx)

        assert n == 2, f"0.13-0.23 应全部过线，实际注入 {n} 条"
        assert [e["id"] for e in ctx.injected_knowledge] == ["exp1", "exp2"]

    def test_real_backend_still_filters_the_same_range(self, db, monkeypatch):
        """对照组：bge-m3 刻度下同样的 0.13-0.23 应当被判为不相关而滤掉。

        少了这条，上一条的「2 条」可能只是阈值被无脑砍到 0，看不出两后端有区别。
        """
        monkeypatch.setattr(emb, "use_mock_embedding", lambda: False)
        _patch_search(monkeypatch, [
            _hit("exp1", 0.23, scope="global", title="服装类商标判赔区间"),
            _hit("exp2", 0.13, scope="global", title="判赔规模经验"),
        ])
        case = _new_case(db)
        ctx = CaseContext(case_id=case.id)

        assert _auto_recall(db, case, ctx) == 0

    def test_two_backends_yield_different_thresholds(self):
        """纯口径断言：两个后端的合格线必须不同，且哈希更宽松。"""
        assert auto_recall_min_score(True) == AUTO_RECALL_MIN_SCORE_HASH
        assert auto_recall_min_score(False) == AUTO_RECALL_MIN_SCORE_REAL
        assert auto_recall_min_score(True) < auto_recall_min_score(False)

    def test_audit_records_which_backend_and_threshold(self, db, monkeypatch):
        """审计要写清后端与阈值——否则事后无法解释「为何换了 key 召回结果变了」。"""
        monkeypatch.setattr(emb, "use_mock_embedding", lambda: True)
        _patch_search(monkeypatch, [_hit("k1", 0.5)])
        case = _new_case(db)
        ctx = CaseContext(case_id=case.id)

        _auto_recall(db, case, ctx)

        effect = _last_event(ctx)["effect"]
        assert "哈希兜底" in effect
        assert f"阈值={AUTO_RECALL_MIN_SCORE_HASH}" in effect


# ============================================================
# 3. 检索参数透传（隔离前提）
# ============================================================

class TestSearchArguments:

    def test_case_id_double_scope_and_top_k_passed_through(self, db, monkeypatch):
        fake = _patch_search(monkeypatch, [])
        case = _new_case(db)
        ctx = CaseContext(case_id=case.id)

        _auto_recall(db, case, ctx)

        call = fake.calls[0]
        assert call["case_id"] == case.id
        assert call["scope"] is None          # 双库：本案材料库 + 全局经验库
        assert call["top_k"] == AUTO_RECALL_TOP_K

    def test_search_failure_degrades_to_empty(self, db, monkeypatch):
        """向量库不可用时不抛异常、注入集置空、评估照常继续。"""
        _patch_search(monkeypatch, RuntimeError("向量库不可用"))
        case = _new_case(db)
        ctx = CaseContext(case_id=case.id)

        n = _auto_recall(db, case, ctx)

        assert n == 0
        assert ctx.injected_knowledge == []
        event = _last_event(ctx)
        assert "向量库不可用" in event["content"]
        assert "降级" in event["effect"]


# ============================================================
# 4. 真实检索路径：双库命中 + 跨案零污染
# ============================================================

class TestRealSearchPath:

    def test_recalls_own_material_and_never_other_cases(self, db):
        """走真实 search_knowledge（哈希向量兜底）：命中本案材料，绝不串到别的案子。

        这是本文件唯一不拦检索出口的用例——前面几条断言的是「发出去什么」，
        这条断言的是「双库与隔离在真实链路上真的成立」。
        """
        own = "被告在电商平台大量销售带有我方第12345678号注册商标的运动鞋，侵权规模较大。"
        other = "另案被告擅自使用他人企业名称造成市场混淆，与本案商标侵权毫无关联。"

        case_a = Case(name="A案", cause_type="商标侵权", goal_type="要钱",
                      case_description=own)
        case_b = Case(name="B案", cause_type="不正当竞争", goal_type="要钱",
                      case_description=other)
        db.add(case_a)
        db.add(case_b)
        db.flush()

        # 两案各自的材料库 + 一条全局经验库条目
        kb.ingest_case_materials(db, case_a.id, own)
        kb.ingest_case_materials(db, case_b.id, other)
        kb.add_knowledge(db, scope="global", source_type="B",
                         title="全局：商标侵权判赔口径",
                         content="商标侵权案件判赔规模通常参照权利人损失与侵权获利。")
        db.commit()

        ctx = CaseContext(case_id=case_a.id, goal_type="要钱")
        n = _auto_recall(db, case_a, ctx)

        assert n >= 1, "本案材料应当被召回，否则自动召回形同虚设"

        # 注入集全部来自本案材料库或全局经验库，不得出现 B 案的材料
        for e in ctx.injected_knowledge:
            assert e["scope"] in ("case", "global")
        titles = " ".join(e["title"] for e in ctx.injected_knowledge)
        assert "另案" not in titles

    def test_short_global_experience_is_actually_recalled(self, db):
        """真实哈希向量下端到端：全局经验库的短条目必须真的进注入集。

        前面几条用的都是假 hits、分数是我编的，只能证明阈值逻辑对；
        这条走真实检索，证明的是「实测分数落在 0.3 以下、但确实被救了回来」
        这个真实缺陷被解决，而不是同义反复。
        """
        if not emb.use_mock_embedding():
            pytest.skip("已配置 bge-m3，本用例只针对哈希兜底后端")

        case = Case(name="经验召回案", cause_type="商标侵权", goal_type="要钱",
                    case_description="被告在电商平台大量销售带有我方注册商标的服装，销量逾三万件。")
        db.add(case)
        db.flush()
        kb.ingest_case_materials(
            db, case.id,
            "商标注册证载明原告系涉案注册商标权利人，核定使用商品为第25类服装。")
        kb.add_knowledge(db, scope="global", source_type="B",
                         title="全局经验：服装类商标判赔区间",
                         content="服装类商标侵权案件判赔规模一般在三万到五十万元之间。")
        db.commit()

        # 先量出真实分数：修复前的 0.3 门槛必然把它挡在门外
        query = f"{case.cause_type} {case.goal_type} {case.case_description}"
        hits = kb.search_knowledge(db, query, case_id=case.id, scope=None, top_k=8)
        mine = [h for h in hits if h["title"].startswith("全局经验：")]
        assert mine, "刚写入的全局经验条目应能被检索到"
        top = max(float(h["score"]) for h in mine)

        ctx = CaseContext(case_id=case.id, goal_type="要钱")
        _auto_recall(db, case, ctx)

        titles = [e["title"] for e in ctx.injected_knowledge]
        assert any(t.startswith("全局经验：") for t in titles), (
            f"全局经验条目实测 {top:.3f} 分却未注入，哈希兜底下合格线未生效")

        # 钉住「修复前必然全军覆没」这个前提。少了它，一旦分数漂到 0.3 以上，
        # 这条测试就退化成谁都能过的同义反复，看不出门槛自适应用在哪。
        assert top < AUTO_RECALL_MIN_SCORE_REAL, (
            f"实测 {top:.3f} 已高于 bge-m3 刻度 {AUTO_RECALL_MIN_SCORE_REAL}，"
            f"本用例不再能证明自适应门槛的必要性，请换一条更短的条目")


# ============================================================
# 5. 启动评估的 SSE 契约（前端靠 recall_done 显示注入提示）
# ============================================================

def _sse_events(text):
    out = []
    for line in text.splitlines():
        if line.startswith("data: "):
            out.append(json.loads(line[6:]))
    return out


def _full_payload(**overrides):
    p = {
        "name": "自动召回端到端",
        "cause_type": "商标侵权",
        "goal_type": "要钱",
        "client_org": "我方公司",
        "defendant_name": "被告公司",
        "case_description": "被告在电商平台销售带有我方注册商标的商品，侵权规模较大。",
        "evidence_texts": "商标注册证、公证购买记录、平台销量截图等材料齐备。",
    }
    p.update(overrides)
    return p


class TestRunEndpointEmitsRecallEvent:
    """启动评估时必须在节点事件之前推 recall_done，且出现在 flow_finished 之前。"""

    def test_recall_done_is_streamed_before_flow_finished(self):
        from fastapi.testclient import TestClient

        from core.database import init_db
        from main import app

        init_db()
        with TestClient(app) as client:
            case_id = client.post("/api/cases", json=_full_payload()).json()["id"]
            resp = client.post(f"/api/evaluation/{case_id}/run")
            assert resp.status_code == 200, resp.text

            events = _sse_events(resp.text)
            kinds = [e.get("event") for e in events]

        assert "recall_done" in kinds, f"缺少 recall_done 事件：{kinds}"
        assert "flow_finished" in kinds, f"流程未正常结束：{kinds}"
        assert kinds.index("recall_done") < kinds.index("flow_finished")

        recall = next(e for e in events if e.get("event") == "recall_done")
        assert recall["status"] == "ok"
        assert "自动召回" in recall["label"]

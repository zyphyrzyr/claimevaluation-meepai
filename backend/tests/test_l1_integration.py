"""
L1 组件集成评测：知识库（DB+向量库）/ CaseContext 序列化 / 四象限与建议一致性
运行：backend/ 目录 pytest tests/test_l1_integration.py -v
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("USE_MOCK", "True")

from core.database import init_db, SessionLocal, Case, KnowledgeEntry
from core.case_context import CaseContext
from core.knowledge import (
    chunk_text, add_knowledge, delete_knowledge, list_entries,
    search_knowledge, ingest_case_materials, info,
)
from core.knowledge.vector_store import get_store
from core import report_generator, scoring
from core.config import SCORE_THRESHOLD_GO, QUADRANT_AXIS_MID


# ============================================================
# fixtures
# ============================================================

@pytest.fixture(scope="module")
def db():
    init_db()
    session = SessionLocal()
    yield session
    session.close()


def _new_case(db, name: str, cause: str = "商标侵权", goal: str = "要钱") -> Case:
    c = Case(name=name, cause_type=cause, goal_type=goal)
    db.add(c)
    db.flush()
    return c


# ============================================================
# 1. 分块边界
# ============================================================

class TestChunking:
    def test_empty(self):
        assert chunk_text("") == []
        assert chunk_text(None) == []
        assert chunk_text("   ") == []

    def test_shorter_than_size_single_chunk(self):
        assert chunk_text("短文本") == ["短文本"]

    def test_exactly_at_size(self):
        """正好 400 字 → 单块，不切"""
        text = "甲" * 400
        chunks = chunk_text(text)
        assert len(chunks) == 1
        assert len(chunks[0]) == 400

    def test_one_over_size(self):
        """401 字 → 切两块（步长 340）"""
        text = "甲" * 401
        chunks = chunk_text(text)
        assert len(chunks) == 2
        assert len(chunks[0]) == 400
        assert len(chunks[1]) == 61

    def test_overlap_coverage(self):
        """重叠 60 字：相邻块首尾须有 60 字重复，且不丢中间内容"""
        text = "".join(str(i % 10) for i in range(1000))
        chunks = chunk_text(text)
        assert len(chunks) == 3, f"1000 字应为 3 块（400/400/...），实际 {len(chunks)}"
        for i in range(len(chunks) - 1):
            assert chunks[i][-60:] == chunks[i + 1][:60], f"块 {i}/{i+1} 重叠不为 60 字"

    def test_no_content_loss(self):
        """分块并集须覆盖原文全部字符"""
        text = "".join(str(i % 10) for i in range(1000))
        chunks = chunk_text(text)
        covered = set()
        pos = 0
        for c in chunks:
            for ch in c:
                covered.add(pos)
                pos += 1
            pos -= 60  # 回退重叠
        assert len(covered) == len(text)


# ============================================================
# 2. 知识库增删一致性（SQLite ↔ 向量库）
# ============================================================

class TestKnowledgeConsistency:
    def test_backend_is_chromadb(self):
        """确认跑在真实向量库上，而非静默降级"""
        assert info()["backend"] == "chromadb", "已降级到内存存储，本组结论需重新评估"

    def test_add_then_delete_removes_vectors(self, db):
        """删除条目后，向量库中的分块必须一并清除（否则检索到幽灵数据）"""
        entry = add_knowledge(db, "global", "B", "一致性测试条目X",
                              "这是一段用于验证删除一致性的测试内容。" * 20)
        store = get_store()
        before = store.query([0.1] * 256, {"entry_id": entry.id}, top_k=50)
        assert len(before) > 0, "入库后向量库应能查到分块"

        assert delete_knowledge(db, entry.id) is True
        after = store.query([0.1] * 256, {"entry_id": entry.id}, top_k=50)
        assert len(after) == 0, f"删除后向量库仍残留 {len(after)} 个分块"

    def test_delete_removes_sqlite_row(self, db):
        entry = add_knowledge(db, "global", "B", "一致性测试条目Y", "内容" * 100)
        eid = entry.id
        delete_knowledge(db, eid)
        assert db.query(KnowledgeEntry).filter(KnowledgeEntry.id == eid).first() is None

    def test_delete_nonexistent_returns_false(self, db):
        assert delete_knowledge(db, "不存在的ID") is False

    def test_chunk_count_matches_sqlite_and_vector(self, db):
        """SQLite 记录的 chunk_count 必须与向量库实际分块数一致"""
        entry = add_knowledge(db, "global", "B", "分块计数校验", "乙" * 1000)
        listed = [e for e in list_entries(db, scope="global") if e["id"] == entry.id][0]
        store = get_store()
        actual = len(store.query([0.1] * 256, {"entry_id": entry.id}, top_k=100))
        assert listed["chunk_count"] == actual == len(chunk_text("乙" * 1000))

    def test_scope_validation(self, db):
        with pytest.raises(ValueError):
            add_knowledge(db, "invalid_scope", "B", "t", "c")
        with pytest.raises(ValueError):
            add_knowledge(db, "case", "A", "t", "c")  # 缺 case_id


# ============================================================
# 3. 跨案隔离（防污染核心）
# ============================================================

class TestIsolation:
    def test_case_materials_never_leak(self, db):
        c1 = _new_case(db, "L1-案A")
        c2 = _new_case(db, "L1-案B")
        ingest_case_materials(db, c1.id, "原告商标注册证第1111111号，核定使用商品第25类服装鞋帽。")
        ingest_case_materials(db, c2.id, "被告已被列入失信被执行人名单，名下无可供执行财产。")
        db.commit()

        hits = search_knowledge(db, "失信被执行人 财产", case_id=c1.id)
        leaked = [h for h in hits if h["scope"] == "case" and h["case_id"] != c1.id]
        assert not leaked, f"案A 检索泄漏了其他案件材料：{[h['title'] for h in leaked]}"

    def test_global_library_shared_across_cases(self, db):
        c1 = _new_case(db, "L1-案C")
        add_knowledge(db, "global", "D", "L1-共享经验",
                      "商标侵权案件法定赔偿的裁判口径与酌定因素。")
        db.commit()
        hits = search_knowledge(db, "法定赔偿 裁判口径", case_id=c1.id)
        assert any(h["scope"] == "global" and "L1-共享经验" in h["title"] for h in hits)

    def test_scope_case_without_case_id_returns_nothing(self, db):
        """scope=case 但不给 case_id → 必须为空，不能退回全库"""
        assert search_knowledge(db, "任意查询", scope="case") == []

    def test_empty_query_returns_empty(self, db):
        assert search_knowledge(db, "") == []
        assert search_knowledge(db, "   ") == []


# ============================================================
# 4. CaseContext 序列化往返
# ============================================================

class TestCaseContextSerialization:
    def test_roundtrip_preserves_p3_fields(self):
        ctx = CaseContext(
            case_id="t1", case_description="测试案情", cause_type="著作权侵权", goal_type="要名",
            injected_knowledge=[{"id": "e1", "scope": "case", "title": "证据材料-1",
                                 "snippet": "注册证内容" * 20}],
            advisor_messages=[{"role": "user", "content": "问题"},
                              {"role": "assistant", "content": "回答"}],
            user_viewpoints=["观点一"],
        )
        restored = CaseContext.from_dict(ctx.to_dict())
        assert restored.injected_knowledge == ctx.injected_knowledge
        assert restored.advisor_messages == ctx.advisor_messages
        assert restored.user_viewpoints == ctx.user_viewpoints
        assert restored.cause_type == "著作权侵权"

    def test_unknown_keys_ignored(self):
        restored = CaseContext.from_dict({"case_id": "x", "未来字段": "值"})
        assert restored.case_id == "x"
        assert not hasattr(restored, "未来字段")

    def test_empty_dict_gives_defaults(self):
        ctx = CaseContext.from_dict({})
        assert ctx.cause_type == "商标侵权"
        assert ctx.injected_knowledge == []

    def test_audit_trail_accumulates(self):
        ctx = CaseContext(case_id="t2")
        ctx.add_viewpoint("补充观点", source="advisor")
        ctx.log_event("rerun", node="rights", content="重跑", effect="下游失效")
        assert len(ctx.audit_trail) == 2
        assert ctx.audit_trail[0]["event_type"] == "viewpoint_inject"
        assert ctx.audit_trail[1]["node"] == "rights"

    def test_mark_stale_only_touches_existing(self):
        ctx = CaseContext()
        ctx.set_dimension("rights", {"score": 80})
        ctx.mark_stale(["rights", "infringement"], reason="上游变更")
        assert ctx.dimension_results["rights"]["status"] == "stale"
        assert "infringement" not in ctx.dimension_results, "未跑过的节点不应被造出 stale 记录"

    def test_roundtrip_survives_json_persist(self):
        """模拟真实落库：dict → sqlite JSON 往返"""
        import json
        ctx = CaseContext(case_id="t3", injected_knowledge=[{"title": "注入"}],
                          scores={"final": 26.0}, correction_coeff=0.85)
        raw = json.dumps(ctx.to_dict(), ensure_ascii=False)
        restored = CaseContext.from_dict(json.loads(raw))
        assert restored.scores["final"] == 26.0
        assert restored.correction_coeff == 0.85


# ============================================================
# 5. 四象限 与 决策建议 的一致性
# ============================================================

class TestQuadrantConsistency:
    def test_quadrant_known_values(self):
        assert report_generator.quadrant(80, 80) == "双优区（强推起诉）"
        assert report_generator.quadrant(80, 50) == "可行低回报区（可诉，控制成本）"
        assert report_generator.quadrant(50, 80) == "高回报风险区（先补证据短板）"
        assert report_generator.quadrant(50, 50) == "双低区（暂缓，评估替代方案）"

    def test_quadrant_boundary_follows_config(self):
        """四象限中线改由 QUADRANT_AXIS_MID 配置，不再是硬编码 70"""
        mid = QUADRANT_AXIS_MID
        assert report_generator.quadrant(mid, mid) == "双优区（强推起诉）"
        assert report_generator.quadrant(mid - 0.1, 90) == "高回报风险区（先补证据短板）"
        assert report_generator.quadrant(90, mid - 0.1) == "可行低回报区（可诉，控制成本）"

    def test_quadrant_and_recommendation_no_longer_conflict(self):
        """
        【已修复 P1-1】原缺陷：四象限硬编码 70、决策建议阈值 75，两者不同源。
        法律 70 × 业务 70 → 四象限"双优区（强推起诉）"、建议"暂缓"，同屏自相矛盾。

        修复：四象限中线改为 QUADRANT_AXIS_MID 配置并与 SCORE_THRESHOLD_GO 对齐。
        幂平均恒有 min(x,y) <= M_p(x,y) <= max(x,y)，故两轴均过线时总分必过线，
        冲突在数学上不可能出现。
        """
        legal, business = 70.0, 70.0
        final = scoring.calculate_overall_score(legal, business)
        quad = report_generator.quadrant(legal, business)
        rec = scoring.generate_recommendation(final, [], is_complete=True)

        # 70/70 现在落在双低区，与「补短板」建议一致，不再矛盾
        assert quad == "双低区（暂缓，评估替代方案）"
        assert rec["recommendation"] == "补充短板后启动"

    def test_quadrant_mid_equals_go_threshold(self):
        """四象限中线与决策 GO 阈值对齐，杜绝同屏矛盾"""
        assert QUADRANT_AXIS_MID == SCORE_THRESHOLD_GO

"""
L2 编排器层评测：红线门禁真实模式 / 重跑失效传播 / 完整主流程 / 失败态处理
运行：backend/ 目录 pytest tests/test_l2_pipeline.py -v
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("USE_MOCK", "True")

from core import orchestrator, scoring
from core.case_context import CaseContext
from core.database import init_db, SessionLocal


# ============================================================
# helpers
# ============================================================

def _good_ctx(**overrides) -> CaseContext:
    """证据齐备、无争议的标准优质案件"""
    kwargs = dict(
        case_id="pipe-1",
        case_description="原告持有第1234567号注册商标（第25类服装），被告在天猫店铺销售近似标识卫衣，已公证取证。",
        cause_type="商标侵权",
        goal_type="要钱",
    )
    kwargs.update(overrides)
    return CaseContext(**kwargs)


SUFFICIENT_MATRIX = [
    {"id": "权利基础证据-1", "category": "权利基础证据", "item": "商标注册证", "status": "sufficient"},
    {"id": "侵权认定证据-1", "category": "侵权认定证据", "item": "侵权截图", "status": "sufficient"},
    {"id": "损害赔偿证据-1", "category": "损害赔偿证据", "item": "销量证据", "status": "sufficient"},
]


# ============================================================
# 1. 【P0】红线硬门禁：真实模式下的字段缺失
# ============================================================

class TestRedGateRealMode:
    """
    编排器 _run_red_gate 在真实模式构造的 case_facts 只有三个键：
        timeline / case_description / evidence_matrix
    而规则引擎期望的是：
        timeline / case_description / parties / evidence_checklist
    → parties 缺失 → 主体资格 block
    → evidence_checklist 缺失 → 权利证明 block + 侵权证据 block
    结果：真实模式下【每一个案件】都会被硬门禁拦截，评估卡在 red_gate 直接输出"暂不建议起诉"。
    Mock 模式走 mock_rule_hits()（6 条全 pass/warning），完全掩盖了此缺陷。
    """

    def test_real_mode_false_block_on_healthy_case(self, monkeypatch):
        """证据齐备的优质案件在真实模式下不应被拦截——当前会失败，即为缺陷证据"""
        monkeypatch.setattr(orchestrator, "_use_mock", lambda: False)
        ctx = _good_ctx(evidence_matrix=SUFFICIENT_MATRIX)
        orch = orchestrator.Orchestrator(ctx)

        orch.run_node("red_gate")

        blocks = [r["rule_name"] for r in ctx.red_flags if r["severity"] == "block"]
        assert not blocks, (
            f"真实模式误拦截：{blocks}\n"
            f"原因：orchestrator 未把 evidence_matrix 映射为规则引擎所需的 "
            f"evidence_checklist，也未传入 parties"
        )

    def test_rule_engine_is_fine_when_fields_supplied(self):
        """同样的案件，补齐字段后规则引擎判定正常 → 证明缺陷在编排器的字段映射，不在引擎"""
        from core.legal_rules import run_rule_engine
        hits = run_rule_engine({
            "timeline": [{"event": "发现侵权行为", "date": "2026-01-01"}],
            "case_description": "原告持有注册商标，被告销售侵权商品，已公证。",
            "parties": [{"role": "plaintiff", "name": "原告公司"}],
            "evidence_checklist": {"has_rights_proof": True,
                                   "has_infringement_proof": True,
                                   "has_damage_proof": True},
        })
        assert [h["severity"] for h in hits] == ["pass"] * 6

    def test_mock_mode_shares_the_same_engine_path(self, monkeypatch):
        """
        曾经：mock 模式走 mock_rule_hits() 直接返回硬编码全 pass，
        真实模式的字段缺失缺陷在 mock 下永远看不见（P0-1 被掩盖的根因）。

        现在 mock 与真实模式共用同一段规则引擎代码，改任何一边都会同时影响两边。
        """
        ctx = _good_ctx(evidence_matrix=SUFFICIENT_MATRIX)

        monkeypatch.setattr(orchestrator, "_use_mock", lambda: True)
        Orchestrator = orchestrator.Orchestrator
        m = Orchestrator(_good_ctx(evidence_matrix=SUFFICIENT_MATRIX))
        m.run_node("red_gate")
        mock_hits = [(h["rule_code"], h["severity"]) for h in m.ctx.red_flags]

        monkeypatch.setattr(orchestrator, "_use_mock", lambda: False)
        r = Orchestrator(_good_ctx(evidence_matrix=SUFFICIENT_MATRIX))
        r.run_node("red_gate")
        real_hits = [(h["rule_code"], h["severity"]) for h in r.ctx.red_flags]

        assert mock_hits, "mock 模式必须真的跑出规则结果，不能是硬编码空表"
        assert mock_hits == real_hits, (
            f"mock 与真实模式的红线判定不一致，说明又出现了分叉代码路径\n"
            f"mock: {mock_hits}\nreal: {real_hits}")

    def test_mock_rule_hits_is_gone(self):
        """mock_rule_hits 硬编码出口已删除，防止有人图省事再把它加回来"""
        import core.mock as mock_pkg
        assert not hasattr(mock_pkg, "mock_rule_hits")


# ============================================================
# 2. 节点依赖图与失效传播
# ============================================================

class TestDependencyGraph:
    def test_all_downstream_nodes_exist(self):
        """
        依赖图的键是 dimension_results 的键，不是流程节点名。
        dimension_results 的合法键 = NODE_ORDER ∪ BUSINESS_DIMENSIONS
        （"business" 是流程节点，但它写的是 damages/recovery/precedent 三个子维度键）。
        早期实现把两者混为一谈，依赖图指向不存在的 "business" 键，
        导致 mark_stale 静默跳过（评测报告 P1-2）。
        """
        valid = set(orchestrator.NODE_ORDER) | set(orchestrator.BUSINESS_DIMENSIONS)
        for node, deps in orchestrator.DOWNSTREAM.items():
            assert node in orchestrator.NODE_ORDER, f"{node} 不在节点序中"
            for d in deps:
                assert d in valid, (
                    f"{node} 的下游 {d} 不是合法的 dimension_results 键"
                    f"（既不在 NODE_ORDER 也不在 BUSINESS_DIMENSIONS）"
                )

    def test_business_node_maps_to_real_dimension_keys(self):
        """business 的下游不能是不存在的 'business' 键，也不得是它自己的产出"""
        deps = orchestrator.DOWNSTREAM["business"]
        assert "business" not in deps, "指向不存在的 business 键会让 mark_stale 静默失效"
        for d in orchestrator.BUSINESS_DIMENSIONS:
            assert d not in deps, (
                f"{d} 是 business 的产出而不是下游；列进 business 的下游会让"
                f"重跑 business 时先算出 ok 再立刻标 stale，自相矛盾")
        assert "synthesize" in deps

    def test_rerun_business_leaves_its_own_outputs_fresh(self):
        """
        回归：修 P1-2 时曾把 damages/recovery/precedent 列为 business 的下游，
        导致 rerun('business') 刚算完就把三个子维度标成 stale。
        """
        ctx = _good_ctx(goal_type="要钱")
        orch = orchestrator.Orchestrator(ctx)
        orch.run_all()
        orch.rerun_node("business")
        for d in ("damages", "recovery"):
            assert ctx.dimension_results[d]["status"] == "ok", (
                f"重跑 business 后 {d} 不应为 stale，实际："
                f"{ctx.dimension_results[d].get('status')}")

    def test_graph_is_acyclic(self):
        """下游不得回指上游，否则失效传播会死循环"""
        for node, deps in orchestrator.DOWNSTREAM.items():
            assert node not in deps, f"{node} 自环"
        # 逐节点沿依赖走一遍，确认能终止
        for start in orchestrator.NODE_ORDER:
            seen, stack = set(), [start]
            while stack:
                cur = stack.pop()
                for d in orchestrator.DOWNSTREAM.get(cur, []):
                    assert d not in seen or True
                    if d not in seen:
                        seen.add(d)
                        stack.append(d)
            assert start not in seen, f"{start} 存在依赖环"

    def test_labels_cover_all_nodes(self):
        for node in orchestrator.NODE_ORDER:
            assert node in orchestrator.NODE_LABELS, f"{node} 缺中文标签（前端会显示英文键名）"


class TestRerunPropagation:
    def test_rerun_rights_stales_infringement_and_recomputes_synthesize(self):
        ctx = _good_ctx()
        orch = orchestrator.Orchestrator(ctx)
        orch.run_all()                       # mock 全流程
        before_final = ctx.scores.get("final")

        orch.rerun_node("rights", guidance="商标续展证明已补充提交")

        assert ctx.dimension_results["infringement"]["status"] == "stale", "下游侵权认定应标记待确认重跑"
        assert "stale_reason" in ctx.dimension_results["infringement"]
        assert ctx.dimension_results["synthesize"]["status"] != "stale", "规则环节须瞬时重算，不留 stale"
        assert "商标续展证明已补充提交" in ctx.user_viewpoints, "引导意见应注入观点列表"
        assert ctx.audit_trail[-1]["event_type"] == "node_rerun"

    def test_rerun_emits_pending_downstream_in_audit(self):
        ctx = _good_ctx()
        orch = orchestrator.Orchestrator(ctx)
        orch.run_all()
        orch.rerun_node("rights", guidance="补充观点X")
        effect = ctx.audit_trail[-1]["effect"]
        assert "侵权认定" in effect, f"审计轨迹应写明待确认重跑的下游，实际：{effect}"

    def test_rerun_unknown_node_raises(self):
        ctx = _good_ctx()
        orch = orchestrator.Orchestrator(ctx)
        with pytest.raises(ValueError):
            orch.rerun_node("不存在的节点")

    def test_evidence_review_rerun_fails_to_stale_damages(self):
        """
        【实测缺陷 P1】DOWNSTREAM['evidence_review'] 声明下游含 'business'，
        但 dimension_results 中并不存在 'business' 键（实际是 damages/recovery/precedent），
        mark_stale 静默跳过 → 重跑证据盘点后，判赔规模、回款能力、判例价值
        都不会被标记 stale。用户补充证据后看到的判赔分仍是旧证据算出来的，
        且界面无任何"待重跑"提示。
        """
        ctx = _good_ctx()
        orch = orchestrator.Orchestrator(ctx)
        orch.run_all()
        assert ctx.dimension_results["damages"]["status"] == "ok"

        orch.rerun_node("evidence_review", guidance="补充了销量公证书")

        stale_or_absent = ctx.dimension_results.get("damages", {}).get("status")
        assert stale_or_absent == "stale", (
            f"重跑证据盘点后，判赔规模状态仍为 {stale_or_absent}（未失效）——"
            f"依赖图引用了不存在的 'business' 节点"
        )

    def test_rerun_synthesize_has_no_downstream(self):
        ctx = _good_ctx()
        orch = orchestrator.Orchestrator(ctx)
        orch.run_all()
        orch.rerun_node("synthesize")
        assert "无下游待办" in ctx.audit_trail[-1]["effect"]

    def test_downstream_is_pruned_to_the_goal_path(self):
        """
        要钱案子的重跑提示里不得出现「判例价值」，反之亦然。

        DOWNSTREAM 静态列了 damages/recovery/precedent 三个业务子维度，但一个案子
        只会走其中一条路径。不剪枝的话，effect 文案会告诉用户「待确认重跑：判例价值」，
        可这个案子压根没有该节点——用户点进去找不到，顶部 stale 计数也虚高一项。
        """
        assert orchestrator.active_downstream("evidence_review", "要钱") == [
            "rights", "infringement", "procedure",
            "damages", "recovery", "business", "synthesize",
        ]
        assert orchestrator.active_downstream("evidence_review", "要名") == [
            "rights", "infringement", "procedure",
            "precedent", "business", "synthesize",
        ]

    def test_rerun_effect_never_mentions_the_other_goal_branch(self):
        """端到端：两种目标下的 effect 文案都只提自己那条路径"""
        money = _good_ctx(goal_type="要钱")
        orchestrator.Orchestrator(money).run_all()
        orchestrator.Orchestrator(money).rerun_node("evidence_review")
        effect_money = money.audit_trail[-1]["effect"]
        assert "判例价值" not in effect_money, f"要钱案子却提示重跑判例价值：{effect_money}"
        assert "判赔规模" in effect_money and "回款能力" in effect_money

        fame = _good_ctx(goal_type="要名")
        orchestrator.Orchestrator(fame).run_all()
        orchestrator.Orchestrator(fame).rerun_node("evidence_review")
        effect_fame = fame.audit_trail[-1]["effect"]
        assert "判赔规模" not in effect_fame and "回款能力" not in effect_fame, (
            f"要名案子却提示重跑判赔/回款：{effect_fame}")
        assert "判例价值" in effect_fame


# ============================================================
# 3. 完整主流程（mock）
# ============================================================

class TestFullPipelineMock:
    def test_run_all_produces_complete_decision(self):
        ctx = _good_ctx()
        orch = orchestrator.Orchestrator(ctx)
        orch.run_all()

        assert ctx.evidence_matrix, "证据盘点应产出矩阵"
        assert ctx.scores["final"] is not None, "mock 全流程应产出决策分"
        assert ctx.recommendation["level"] in ("green", "yellow", "red")
        assert ctx.confidence is not None
        for node in ("evidence_review", "red_gate", "rights", "infringement",
                     "procedure", "damages", "recovery", "synthesize"):
            assert node in ctx.dimension_results, f"{node} 未产出结果"

    def test_business_node_has_its_own_rollup_record(self):
        """
        NODE_ORDER 里的 'business' 节点必须写自己的 dimension_results 汇总记录。

        修复前它只写子维度 damages/recovery（要钱）或 precedent（要名），business 键
        本身不存在 → 编排器查状态永远拿到默认 "ok"，业务预期算失败时流程进度仍显示
        成功；前端按 NODE_ORDER 渲染也只能画出「有进度、无卡片」的空节点。
        """
        ctx = _good_ctx()  # 要钱
        orchestrator.Orchestrator(ctx).run_all()

        rollup = ctx.dimension_results.get("business")
        assert rollup, "business 节点未写汇总记录"
        assert rollup["status"] == "ok"
        assert rollup["result"]["goal_type"] == "要钱"
        assert rollup["result"]["sub_dimensions"] == ["damages", "recovery"]
        assert set(rollup["result"]["scores"]) == {"damages", "recovery"}
        assert "damages" in ctx.dimension_results and "recovery" in ctx.dimension_results

    def test_business_rollup_reflects_failed_sub_dimension(self):
        """子维度失败时汇总记录必须跟着失败，不能一直显示 ok"""
        ctx = _good_ctx()
        orchestrator.Orchestrator(ctx).run_all()
        ctx.set_dimension("recovery", {"score": 0}, status="failed", error="回款能力数据缺失")

        orchestrator.Orchestrator(ctx)._record_business_rollup()
        rollup = ctx.dimension_results["business"]
        assert rollup["status"] == "failed", "子维度失败但汇总仍为 ok"
        assert rollup["error"]

    def test_business_rollup_switches_with_goal_type(self):
        """要名路径的汇总只含 precedent，不残留 damages/recovery"""
        ctx = _good_ctx(goal_type="要名")
        orchestrator.Orchestrator(ctx).run_all()

        rollup = ctx.dimension_results["business"]
        assert rollup["result"]["goal_type"] == "要名"
        assert rollup["result"]["sub_dimensions"] == ["precedent"]
        assert set(rollup["result"]["scores"]) == {"precedent"}

    def test_event_sequence_contract(self):
        """SSE 事件契约：每节点 node_started → node_finished，顺序与 NODE_ORDER 一致"""
        events = []
        ctx = _good_ctx()
        orch = orchestrator.Orchestrator(ctx, on_event=events.append)
        orch.run_all()

        started = [e["node"] for e in events if e["event"] == "node_started"]
        finished = [e["node"] for e in events if e["event"] == "node_finished"]
        assert started == list(orchestrator.NODE_ORDER), f"节点启动顺序不符：{started}"
        assert finished == list(orchestrator.NODE_ORDER)
        for e in events:
            assert "label" in e, f"事件缺中文标签：{e}"

    def test_goal_fame_branch_ignores_recovery(self):
        """要名案件：业务预期取判例价值，不依赖回款能力"""
        ctx = _good_ctx(goal_type="要名")
        orch = orchestrator.Orchestrator(ctx)
        orch.run_all()
        assert ctx.scores["business_expectation"] is not None
        assert "回款能力" not in " ".join(
            ctx.dimension_results.get("synthesize", {}).get("result", {}).get("missing", []))

    def test_goal_money_requires_recovery(self):
        """要钱案件缺回款能力 → 业务预期为 None，标记未完成"""
        ctx = _good_ctx(goal_type="要钱")
        orch = orchestrator.Orchestrator(ctx)
        orch.run_all()
        ctx.recovery_ability = None
        orch.run_node("synthesize")
        missing = ctx.dimension_results["synthesize"]["result"]["missing"]
        assert any("回款能力" in m for m in missing), f"缺回款能力应标记未完成，实际 missing={missing}"
        assert ctx.scores["final"] is None

    def test_damages_failure_does_not_fabricate_business_score(self):
        """判赔规模节点失败时，业务预期须为 None，不得落默认分"""
        ctx = _good_ctx(goal_type="要钱")
        orch = orchestrator.Orchestrator(ctx)
        orch.run_all()
        ctx.dimension_results["damages"] = {
            "status": "failed", "result": {}, "error": "LLM 超时", "updated_at": ""}
        orch.run_node("synthesize")
        assert ctx.scores["business_expectation"] is None
        assert ctx.recommendation["recommendation"] == "评估未完成"


# ============================================================
# 4. 失败态处理
# ============================================================

class TestFailureHandling:
    def test_llm_failure_marks_failed_not_default_score(self):
        """LLM 节点异常时状态须为 failed，不得静默落默认分"""
        ctx = _good_ctx()
        orch = orchestrator.Orchestrator(ctx)

        def boom(*a, **k):
            raise RuntimeError("LLM 超时")

        import core.evaluate_nodes as en
        original = en.evaluate_rights
        en.evaluate_rights = boom
        try:
            orch.run_node("rights")
        finally:
            en.evaluate_rights = original

        dim = ctx.dimension_results["rights"]
        assert dim["status"] == "failed"
        assert dim["error"]
        assert dim["result"].get("score") is None, "失败节点不得带出分数"

    def test_failed_node_propagates_to_incomplete_synthesis(self):
        ctx = _good_ctx()
        orch = orchestrator.Orchestrator(ctx)
        orch.run_all()
        ctx.dimension_results["infringement"] = {
            "status": "failed", "result": {}, "error": "解析失败", "updated_at": ""}
        orch.run_node("synthesize")
        assert ctx.scores["final"] is None
        assert ctx.recommendation["recommendation"] == "评估未完成"

    def test_blocked_flow_skips_downstream_llm_nodes(self, monkeypatch):
        """硬门禁命中：后续 LLM 节点不得被调用"""
        import core.evaluate_nodes as en

        calls = []

        def make_spy(name):
            def spy(ctx, use_mock=True):
                calls.append(name)
                return {"score": 80, "analysis": f"{name} 结果"}
            return spy

        for name in ("evaluate_rights", "evaluate_infringement", "evaluate_procedure"):
            monkeypatch.setattr(en, name, make_spy(name))

        class BlockOrch(orchestrator.Orchestrator):
            def _run_red_gate(self):
                self.ctx.red_flags = [{"severity": "block", "rule_name": "测试拦截"}]
                self.ctx.set_dimension("red_gate", {"blocked": True}, status="blocked")

        ctx = _good_ctx()
        BlockOrch(ctx).run_all()

        assert calls == [], f"拦截后仍调用了 LLM 节点：{calls}"
        assert ctx.recommendation["level"] == "block"
        assert ctx.scores["final"] is None


class TestRecoveryFailureVisibility:
    """
    回款能力「取不到」必须和「回款能力正常」区分开。

    早期实现里 qcc_api 在未提供被告名称时只返回空 metrics、不带 error 键，
    编排器因此按「无异常」把 recovery 节点标成 ok。界面上用户看到的是
    「回款能力：正常」，而实际值是 None —— 一个典型的静默兜底
    （2026-09-03 接真实 key 联调时发现）。

    两条断言缺一不可：只钉住 qcc_api 的返回值，将来有人改了编排器的
    状态判定照样会兜底回去；只钉住编排器，又会在 qcc_api 换写法时漏掉。

    【必须伪装成「已配置」】测试环境里 QCC token 是空的，_qcc_configured()
    会先短路返回「未配置」分支——而那个分支本来就带 error，于是两条测试
    不修代码也能绿。第一版就是这么写的，变异检验（把修复撤掉）发现毫无反应，
    才发现测的压根不是要修的那行。这里统一把 _qcc_configured 顶成 True。
    """

    def test_qcc_reports_error_when_defendant_name_missing(self, monkeypatch):
        from core import qcc_api
        monkeypatch.setattr(qcc_api, "_qcc_configured", lambda: True)

        profile = qcc_api.search_for_financial_qcc_full({})
        assert profile.get("error"), (
            "未提供被告名称时必须带 error 键；只给空 metrics 会让调用方把"
            "「取不到」误判成「成功但没数据」")
        assert (profile.get("metrics") or {}).get("recovery_probability") is None

    def test_recovery_is_marked_failed_not_ok(self, monkeypatch):
        import core.evaluate_nodes as en
        from core import qcc_api

        monkeypatch.setattr(qcc_api, "_qcc_configured", lambda: True)
        monkeypatch.setattr(orchestrator, "_use_mock", lambda: False)
        # 判赔规模走 LLM，与本用例无关，钉成固定值隔离掉
        monkeypatch.setattr(en, "evaluate_damages",
                            lambda ctx, use_mock=True: {"score": 70, "analysis": "判赔规模"})

        ctx = _good_ctx(goal_type="要钱",
                        defendant_info={"name": "   ", "type": "enterprise"})
        orchestrator.Orchestrator(ctx).run_node("business")

        dim = ctx.dimension_results["recovery"]
        assert dim["status"] == "failed", (
            f"回款能力取不到时必须标 failed；标成 {dim['status']!r} 会让界面"
            f"把「未知」显示成「正常」")
        assert ctx.recovery_ability is None

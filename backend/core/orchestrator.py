"""
评估编排器（确定性状态机，§6.2 编排层）
流程：证据盘点 → 硬门禁 → 权利基础 → 侵权认定 → 诉讼程序 → 业务预期 → 决策合成
（模拟法庭在 P2 插入 诉讼程序 → 决策合成 之间，修正系数默认 1.0）

- 非自主 Agent：节点顺序确定，保证分数可复现、可审计
- 失败态：LLM 节点失败 → 该维度标记 failed，不静默落默认分
- 节点级重跑：rerun(node) → 下游按 DAG 标记 stale → 规则环节瞬时重算
"""

from typing import Any, Callable, Dict, List, Optional

from . import evaluate_nodes, scoring
from .case_context import CaseContext
from .config import get_runtime_settings
from .evidence_review import review_evidence
from .legal_rules import run_rule_engine

NODE_ORDER = [
    "evidence_review", "red_gate",
    "rights", "infringement", "procedure",
    "business", "synthesize",
]

NODE_LABELS = {
    "evidence_review": "证据盘点",
    "red_gate": "硬门禁检查",
    "rights": "权利基础",
    "infringement": "侵权认定",
    "procedure": "诉讼程序",
    "business": "业务预期",
    "synthesize": "决策合成",
}

# 下游依赖（重跑某节点时失效传播；moot 在 P2 加入 infringement 下游）
DOWNSTREAM = {
    "evidence_review": ["rights", "infringement", "procedure", "business", "synthesize"],
    "red_gate": ["synthesize"],
    "rights": ["infringement", "synthesize"],
    "infringement": ["synthesize"],
    "procedure": ["synthesize"],
    "business": ["synthesize"],
    "synthesize": [],
}

EventCallback = Optional[Callable[[Dict[str, Any]], None]]


def _emit(cb: EventCallback, event: str, node: str, **extra) -> None:
    if cb:
        cb({"event": event, "node": node, "label": NODE_LABELS.get(node, node), **extra})


def _use_mock() -> bool:
    return get_runtime_settings()["use_mock"]


class Orchestrator:
    def __init__(self, ctx: CaseContext, on_event: EventCallback = None):
        self.ctx = ctx
        self.on_event = on_event
        self.mock = _use_mock()

    # -------------------------------------------------- 各节点执行

    def _run_evidence_review(self) -> None:
        ctx = self.ctx
        evidence_texts = ctx.defendant_info.get("evidence_texts", "")
        result = review_evidence(
            ctx.case_description, ctx.cause_type,
            evidence_texts=evidence_texts,
            user_viewpoints=ctx.viewpoints_text(),
            use_mock=self.mock,
        )
        ctx.evidence_matrix = result["matrix"]
        ctx.gap_list = result["gap_list"]
        ctx.extra_evidence = result["extra_evidence"]
        ctx.evidence_completeness = result["completeness"]
        ctx.evidence_note = result.get("note", "")
        ctx.set_dimension("evidence_review",
                          {"completeness": result["completeness"],
                           "gap_count": len(result["gap_list"])},
                          status="failed" if result.get("error") else "ok",
                          error=result.get("error"))

    def _run_red_gate(self) -> None:
        ctx = self.ctx
        if self.mock:
            from .mock import mock_rule_hits
            hits = mock_rule_hits()
        else:
            case_facts = {
                "timeline": ctx.defendant_info.get("timeline", []),
                "case_description": ctx.case_description,
                "evidence_matrix": ctx.evidence_matrix,
            }
            hits = run_rule_engine(case_facts)
        ctx.red_flags = hits
        blocked = scoring.has_block_red_flag(hits)
        ctx.set_dimension("red_gate", {"hits": hits, "blocked": blocked},
                          status="blocked" if blocked else "ok")

    def _run_llm_node(self, node: str) -> None:
        fn = {
            "rights": evaluate_nodes.evaluate_rights,
            "infringement": evaluate_nodes.evaluate_infringement,
            "procedure": evaluate_nodes.evaluate_procedure,
        }[node]
        try:
            result = fn(self.ctx, use_mock=self.mock)
            failed = "error" in result
            self.ctx.set_dimension(node, result,
                                   status="failed" if failed else "ok",
                                   error=result.get("error"))
        except Exception as e:  # 失败态：不静默落默认分
            self.ctx.set_dimension(node, {}, status="failed", error=str(e))

    def _run_business(self) -> None:
        ctx = self.ctx
        if ctx.goal_type == "要钱":
            # 判赔规模（LLM）× 回款能力（企查查规则，零 LLM）
            try:
                damages = evaluate_nodes.evaluate_damages(ctx, use_mock=self.mock)
                ctx.set_dimension("damages", damages,
                                  status="failed" if "error" in damages else "ok",
                                  error=damages.get("error"))
            except Exception as e:
                ctx.set_dimension("damages", {}, status="failed", error=str(e))

            if self.mock:
                from .mock import mock_defendant_profile
                profile = mock_defendant_profile()
            else:
                from .qcc_api import search_for_financial_qcc_full
                try:
                    profile = search_for_financial_qcc_full(ctx.defendant_info)
                except Exception as e:
                    profile = {"error": str(e)}
            ctx.defendant_profile = profile
            metrics = profile.get("metrics", {}) if isinstance(profile, dict) else {}
            ctx.recovery_ability = metrics.get("recovery_probability")
            ctx.set_dimension("recovery",
                              {"recovery_ability": ctx.recovery_ability,
                               "red_flags": metrics.get("red_flags", []),
                               "green_flags": metrics.get("green_flags", [])},
                              status="failed" if profile.get("error") else "ok",
                              error=profile.get("error"))
        else:
            try:
                precedent = evaluate_nodes.evaluate_precedent(ctx, use_mock=self.mock)
                ctx.set_dimension("precedent", precedent,
                                  status="failed" if "error" in precedent else "ok",
                                  error=precedent.get("error"))
            except Exception as e:
                ctx.set_dimension("precedent", {}, status="failed", error=str(e))

    def _run_synthesize(self) -> None:
        ctx = self.ctx

        def score_of(node: str) -> Optional[float]:
            d = ctx.dimension_results.get(node, {})
            if d.get("status") != "ok":
                return None
            return (d.get("result") or {}).get("score")

        rights_s = score_of("rights")
        infr_s = score_of("infringement")
        proc_s = score_of("procedure")

        missing = [NODE_LABELS[n] for n, s in
                   [("rights", rights_s), ("infringement", infr_s), ("procedure", proc_s)]
                   if s is None]

        legal = None
        if rights_s is not None and infr_s is not None and proc_s is not None:
            legal = scoring.calculate_legal_feasibility(
                rights_s, infr_s, proc_s, ctx.correction_coeff)

        if ctx.goal_type == "要钱":
            damages_s = score_of("damages")
            recovery_s = ctx.recovery_ability
            business = None
            if damages_s is not None and recovery_s is not None:
                business = scoring.calculate_business_expectation(
                    "要钱", damages_scale=damages_s, recovery_ability=recovery_s)
            if damages_s is None:
                missing.append(NODE_LABELS["business"] + "(判赔规模)")
            if recovery_s is None:
                missing.append(NODE_LABELS["business"] + "(回款能力)")
        else:
            precedent_s = score_of("precedent")
            business = None
            if precedent_s is not None:
                business = scoring.calculate_business_expectation(
                    "要名", precedent_value=precedent_s)
            else:
                missing.append(NODE_LABELS["business"] + "(判例价值)")

        final = None
        if legal is not None and business is not None:
            final = scoring.calculate_overall_score(legal, business)

        blocked = scoring.has_block_red_flag(ctx.red_flags)
        ctx.confidence = scoring.calculate_confidence(
            ctx.evidence_completeness,
            retrieval_complete=(ctx.dimension_results.get("recovery", {}).get("status") != "failed"),
        )
        ctx.scores = {
            "legal_feasibility": legal,
            "business_expectation": business,
            "final": final,
        }
        is_complete = final is not None and not missing
        ctx.recommendation = scoring.generate_recommendation(
            final, ctx.red_flags,
            is_complete=is_complete, missing_dimensions=missing,
            dimension_scores={
                "权利基础": rights_s, "侵权认定": infr_s, "诉讼程序": proc_s,
            } if not blocked else {},
            confidence=ctx.confidence,
        )
        ctx.set_dimension("synthesize", {
            "scores": ctx.scores,
            "confidence": ctx.confidence,
            "recommendation": ctx.recommendation,
            "missing": missing,
        }, status="blocked" if blocked else ("ok" if is_complete else "partial"))

        # 硬门禁命中：流程终止信号
        if blocked:
            ctx.log_event("red_gate_block", node="red_gate",
                          effect="命中程序性红线，输出暂不建议起诉，流程终止")

    # -------------------------------------------------- 流程控制

    def run_all(self) -> CaseContext:
        """完整跑一遍主流程；硬门禁命中即终止"""
        for node in NODE_ORDER:
            self.run_node(node)
            if node == "red_gate" and scoring.has_block_red_flag(self.ctx.red_flags):
                # 红线命中：直接合成"暂不建议起诉"，后续节点不再调用
                self.run_node("synthesize")
                _emit(self.on_event, "flow_blocked", "red_gate")
                break
        return self.ctx

    def run_node(self, node: str) -> None:
        _emit(self.on_event, "node_started", node)
        if node == "evidence_review":
            self._run_evidence_review()
        elif node == "red_gate":
            self._run_red_gate()
        elif node in ("rights", "infringement", "procedure"):
            self._run_llm_node(node)
        elif node == "business":
            self._run_business()
        elif node == "synthesize":
            self._run_synthesize()
        status = self.ctx.dimension_results.get(node, {}).get("status", "ok")
        _emit(self.on_event, "node_finished", node, status=status)

    def rerun_node(self, node: str, guidance: str = "") -> CaseContext:
        """
        节点级重跑（§6.4）：
        1. 引导意见注入 user_viewpoints（自动影响后续所有节点）
        2. 重跑该节点
        3. 下游按 DAG 标记 stale；规则环节（synthesize）瞬时重算，LLM 环节标记待确认
        """
        if node not in NODE_ORDER:
            raise ValueError(f"未知节点: {node}")
        if guidance:
            self.ctx.add_viewpoint(guidance, source=f"节点重跑引导({NODE_LABELS[node]})")

        self.run_node(node)
        downstream = DOWNSTREAM.get(node, [])
        rule_nodes = [n for n in downstream if n == "synthesize"]
        llm_nodes = [n for n in downstream if n != "synthesize"]

        self.ctx.mark_stale(llm_nodes, reason=f"上游「{NODE_LABELS[node]}」已重跑")
        for n in rule_nodes:
            self.run_node(n)  # 纯规则，瞬时重算

        self.ctx.log_event(
            "node_rerun", node=node,
            content=guidance,
            effect=f"「{NODE_LABELS[node]}」已重跑；决策合成已重算；"
                   + (f"待确认重跑：{'、'.join(NODE_LABELS[n] for n in llm_nodes)}" if llm_nodes else "无下游待办"),
        )
        return self.ctx

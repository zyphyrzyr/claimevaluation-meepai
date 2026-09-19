"""
模拟法庭服务层（P2，双模式）
- 内嵌模式 run_embedded：从 CaseContext 组料（权利基础/侵权认定结论 + 证据概要），
  跑五步庭审 → 修正系数回写 ctx.correction_coeff → 重算决策合成（瞬时，纯规则）
- 独立模式 run_standalone：手动组料（案情 + 要点），纯演练，产出演练报告，不回写评分

事件流：yield {"event": "round", ...} / {"event": "moot_finished", ...}
"""

import time
from typing import Any, Dict, Generator, List, Optional

from .case_context import CaseContext
from .config import CAUSE_TRADEMARK, get_runtime_settings
from .legal_rules import build_evidence_checklist
from .moot_court.coefficient import derive_coefficient
from .moot_court.procedure import MootCourtProcedure, MootCourtResult, STEP_NAMES


def _use_mock() -> bool:
    return get_runtime_settings()["use_mock"]


# ============================================================
# 组料
# ============================================================

def _materials_from_ctx(ctx: CaseContext) -> Dict[str, Any]:
    """内嵌模式：从评估结果组庭审材料"""
    def _analysis(node: str) -> str:
        d = ctx.dimension_results.get(node, {})
        r = d.get("result") or {}
        parts = []
        if r.get("analysis"):
            parts.append(r["analysis"])
        if r.get("score") is not None:
            parts.append(f"（评估分：{r['score']}）")
        return " ".join(parts) if parts else "未提供"

    # 证据清单（供原告举证环节）——映射口径统一收敛到 legal_rules，避免两处漂移
    matrix = ctx.evidence_matrix or []
    checklist = build_evidence_checklist(matrix)
    evidence_summary = "\n".join(
        f"- [{m.get('status', '')}] {m.get('item', '')}：{m.get('reason', '')}"
        for m in matrix[:12])
    return {
        "rights_assessment": _analysis("rights"),
        "infringement_assessment": _analysis("infringement"),
        "evidence_summary": evidence_summary or "未上传证据文件",
        "evidence_checklist": checklist,
    }


# ============================================================
# 运行（生成器：逐轮 yield 事件）
# ============================================================

def _mock_run(materials: Dict[str, Any],
              cause_type: str = CAUSE_TRADEMARK) -> Generator[Dict[str, Any], None, MootCourtResult]:
    """Mock 模式：逐轮吐出预置发言（按案由取对应剧本）"""
    from .mock import mock_moot_rounds, mock_judge_result
    result = MootCourtResult()
    for rnd in mock_moot_rounds(cause_type):
        time.sleep(0.6)  # 演示节奏
        result.rounds.append(rnd)
        yield {"event": "round", **rnd}
    # 解析法官结果
    judge_raw = mock_judge_result(cause_type)
    # 与真实模式同一条推导路径：mock 也必须用 10 项子分算出系数，
    # 不能直接读 mock 里的 correction_coefficient——否则 mock 与真实两套逻辑，
    # 演示时系数怎么来的说不清，且子分与系数脱节的问题在 mock 下永远暴露不了。
    detail = derive_coefficient(judge_raw)
    result.correction_coefficient = detail["coefficient"]
    result.coefficient_source = detail["source"]
    result.coefficient_detail = detail
    result.defense_strength = int(judge_raw.get("defense_strength", 50))
    result.judge_summary = judge_raw.get("summary", "")
    result.summary_structured = judge_raw.get("summary_structured", {})
    result.weak_points = judge_raw.get("weak_points", [])
    result.focus_points = judge_raw.get("focus_points", [])
    result.judge_scores = {
        "plaintiff": judge_raw.get("plaintiff_scores", {}),
        "defendant": judge_raw.get("defendant_scores", {}),
        "plaintiff_detail": judge_raw.get("plaintiff_scores_detail", {}),
        "defendant_detail": judge_raw.get("defendant_scores_detail", {}),
        "coefficient_reasoning": judge_raw.get("coefficient_reasoning", ""),
    }
    result.legal_basis = judge_raw.get("legal_basis", []) or []
    result.precedents = judge_raw.get("precedents", []) or []
    result.experience_refs = judge_raw.get("experience_refs", []) or []
    return result


def _real_run(case_description: str, materials: Dict[str, Any],
              viewpoints: str = "",
              cause_type: str = CAUSE_TRADEMARK,
              shared_legal_context: str = "") -> Generator[Dict[str, Any], None, MootCourtResult]:
    """真实模式：MootCourtProcedure 分步运行"""
    if viewpoints:
        # 用户观点仅注入原告侧（影响我方主张组织，不影响被告抗辩生成）
        materials = {**materials,
                     "rights_assessment": materials["rights_assessment"] + "\n用户特别意见：" + viewpoints}
    procedure = MootCourtProcedure(
        case_description=case_description,
        rights_assessment=materials.get("rights_assessment", ""),
        infringement_assessment=materials.get("infringement_assessment", ""),
        evidence_summary=materials.get("evidence_summary", ""),
        evidence_checklist=materials.get("evidence_checklist"),
        cause_type=cause_type,
        shared_legal_context=shared_legal_context,
    )
    result = MootCourtResult()
    for rr in procedure._run_steps():
        rnd = {"step": rr.step, "step_name": rr.step_name,
               "role": rr.speaker, "role_name": rr.role_name, "content": rr.content}
        result.rounds.append(rnd)
        yield {"event": "round", **rnd}
    procedure._parse_judge_result(result)
    return result


def _final_event(result: MootCourtResult, mode: str) -> Dict[str, Any]:
    return {
        "event": "moot_finished",
        "mode": mode,
        "rounds": [
            {"step": r["step"], "step_name": r["step_name"], "role": r["role"],
             "role_name": r["role_name"], "content": r["content"]}
            for r in result.rounds
        ],
        "correction_coefficient": result.correction_coefficient,
        "coefficient_source": result.coefficient_source,
        "coefficient_detail": result.coefficient_detail,
        "defense_strength": result.defense_strength,
        "judge_summary": result.judge_summary,
        "summary_structured": result.summary_structured,
        "weak_points": result.weak_points,
        "focus_points": result.focus_points,
        "judge_scores": result.judge_scores,
        "legal_basis": result.legal_basis,
        "precedents": result.precedents,
        "experience_refs": result.experience_refs,
        "error": result.error,
    }


def run_embedded(ctx: CaseContext, shared_legal_context: str = ""):
    """
    内嵌模式：评估流程内的压力测试。
    shared_legal_context：moot_court.context_sources 渲染的「可引用依据」片段
    （法定基准 + 北大法宝复用 + 经验库召回），路由层组好传入，注入三方 system prompt。
    生成器：yield 逐轮事件；最后返回 moot_finished 事件 dict。
    调用方负责：ctx.correction_coeff 回写后重算 synthesize（见 routers/moot.py）。
    """
    materials = _materials_from_ctx(ctx)
    gen = _mock_run(materials, ctx.cause_type) if _use_mock() else _real_run(
        ctx.case_description, materials, ctx.viewpoints_text(), ctx.cause_type, shared_legal_context)
    result = None
    try:
        while True:
            try:
                event = next(gen)
                yield event
            except StopIteration as stop:
                result = stop.value
                break
    except Exception as e:
        yield {"event": "moot_error", "error": str(e)}
        return

    # 回写 CaseContext
    ctx.moot_transcript = [
        {"step": r["step"], "step_name": r["step_name"], "role": r["role"],
         "role_name": r["role_name"], "content": r["content"]}
        for r in result.rounds
    ]
    prev_coeff = ctx.correction_coeff
    ctx.correction_coeff = result.correction_coefficient
    # 法官归纳的结构化结果整体落库（刷新页面后历史记录要能回显判决书正文与明细，
    # 而不是只回显一个系数）。transcript 第 5 轮只有正文，弱项/补强建议/系数推导
    # 这些字段只在这里有，必须单独存。
    ctx.moot_judge = {
        "judge_summary": result.judge_summary,
        "summary_structured": result.summary_structured,
        "weak_points": result.weak_points,
        "focus_points": result.focus_points,
        "judge_scores": result.judge_scores,
        "legal_basis": result.legal_basis,
        "precedents": result.precedents,
        "experience_refs": result.experience_refs,
        "defense_strength": result.defense_strength,
        "coefficient_source": result.coefficient_source,
        "coefficient_detail": result.coefficient_detail,
    }
    ctx.log_event(
        "moot_finished", node="moot",
        effect=f"内嵌模拟法庭完成，修正系数 {prev_coeff} → {result.correction_coefficient}，"
               f"决策合成已重算；抗辩强度 {result.defense_strength}")
    yield _final_event(result, "embedded")


def run_standalone(case_description: str, cause_type: str = CAUSE_TRADEMARK,
                   viewpoints: List[str] = None,
                   plaintiff_points: str = "",
                   shared_legal_context: str = "") -> Generator[Dict[str, Any], None, None]:
    """
    独立演练模式：跳过评估，手动组料。产出演练报告，不回写任何评分。
    shared_legal_context：moot_court.context_sources 渲染的「可引用依据」片段
    （法定基准 + 经验库召回；独立演练不触发 on-demand 北大法宝）。
    cause_type：决定三方 Agent 的案由画像（请求权基础/抗辩路径/证据类型）。
    """
    viewpoints = viewpoints or []
    materials = {
        "rights_assessment": plaintiff_points or "（独立演练：未提供权利基础评估，由 AI 从案情自行组织）",
        "infringement_assessment": "",
        "evidence_summary": "",
        "evidence_checklist": {"has_rights_proof": True,
                               "has_infringement_proof": True,
                               "has_damage_proof": False},
    }
    gen = _mock_run(materials, cause_type) if _use_mock() else _real_run(
        case_description, materials, "\n".join(viewpoints), cause_type, shared_legal_context)
    result = None
    try:
        while True:
            try:
                event = next(gen)
                yield event
            except StopIteration as stop:
                result = stop.value
                break
    except Exception as e:
        yield {"event": "moot_error", "error": str(e)}
        return

    final = _final_event(result, "standalone")
    # 独立模式：不产修正系数（无评分可修正），输出演练报告要点
    final["drill_report"] = {
        "weak_points": result.weak_points,           # 我方薄弱点
        "focus_points": result.focus_points,         # 补强建议
        "defense_strength": result.defense_strength, # 对方抗辩强度
    }
    yield final

"""
模拟法庭五步庭审编排器
管理步骤流转、Agent 调用顺序、产出修正系数

五步流程:
  1. 开庭陈述（原告）
  2. 被告答辩（被告）
  3. 举证质证（原告举证 → 被告质证）
  4. 法庭辩论（原告 → 被告）
  5. 法官归纳（法官，产出修正系数）

数据结构:
  RoundResult  - 单轮发言记录
  MootCourtResult - 完整庭审结果
  MootCourtProcedure - 编排器
"""

import time
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Callable

from .agents import PlaintiffAgent, DefendantAgent, JudgeAgent


# ============================================================
# 数据结构
# ============================================================

@dataclass
class RoundResult:
    """单轮发言记录"""
    step: int                    # 步骤号 1-5
    step_name: str               # 步骤名称
    speaker: str                 # plaintiff / defendant / judge
    role_name: str               # 显示名（原告代理律师等）
    content: str                 # 发言内容
    timestamp: float = field(default_factory=time.time)


@dataclass
class MootCourtResult:
    """完整庭审结果"""
    rounds: List[RoundResult] = field(default_factory=list)
    correction_coefficient: float = 1.0
    defense_strength: int = 50
    judge_summary: str = ""
    summary_structured: Dict[str, str] = field(default_factory=dict)
    weak_points: List[str] = field(default_factory=list)
    focus_points: List[str] = field(default_factory=list)
    judge_scores: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """转换为兼容旧接口的 dict 格式"""
        return {
            "rounds": [
                {
                    "step": r.step,
                    "step_name": r.step_name,
                    "role": r.speaker,
                    "role_name": r.role_name,
                    "content": r.content
                }
                for r in self.rounds
            ],
            "correction_coefficient": self.correction_coefficient,
            "defense_strength": self.defense_strength,
            "judge_summary": self.judge_summary,
            "summary_structured": self.summary_structured,
            "weak_points": self.weak_points,
            "focus_points": self.focus_points,
            "judge_scores": self.judge_scores,
            "error": self.error
        }


# ============================================================
# 庭审步骤定义
# ============================================================

STEP_NAMES = {
    1: "开庭陈述",
    2: "被告答辩",
    3: "举证质证",
    4: "法庭辩论",
    5: "法官归纳",
}


# ============================================================
# 五步庭审编排器
# ============================================================

class MootCourtProcedure:
    """
    五步庭审编排器

    用法:
        procedure = MootCourtProcedure(
            case_description="...",
            rights_assessment="...",
            infringement_assessment="...",
            evidence_summary="..."
        )
        result = procedure.run()  # 一次性运行

        # 或分步运行（用于 Streamlit 实时展示）
        for step_result in procedure.run_iter():
            # 每完成一步 yield 一个 RoundResult
            display(step_result)
    """

    def __init__(
        self,
        case_description: str,
        rights_assessment: str = "",
        infringement_assessment: str = "",
        evidence_summary: str = "",
        evidence_checklist: Optional[Dict[str, bool]] = None
    ):
        self.case_description = case_description
        self.rights_assessment = rights_assessment
        self.infringement_assessment = infringement_assessment
        self.evidence_summary = evidence_summary
        self.evidence_checklist = evidence_checklist or {
            "has_rights_proof": True,
            "has_infringement_proof": True,
            "has_damage_proof": False
        }

        self.plaintiff = PlaintiffAgent()
        self.defendant = DefendantAgent()
        self.judge = JudgeAgent()

        self.rounds: List[RoundResult] = []
        self._transcript_parts: List[str] = []

    def _build_transcript(self) -> str:
        """构建当前完整庭审记录"""
        return "\n\n---\n\n".join(self._transcript_parts)

    def _add_round(self, step: int, speaker: str, role_name: str, content: str):
        """记录一轮发言"""
        round_result = RoundResult(
            step=step,
            step_name=STEP_NAMES.get(step, ""),
            speaker=speaker,
            role_name=role_name,
            content=content
        )
        self.rounds.append(round_result)
        self._transcript_parts.append(f"【{role_name}】{content}")

    def run(self) -> MootCourtResult:
        """一次性运行全部五步"""
        result = MootCourtResult()
        try:
            for round_result in self._run_steps():
                result.rounds.append(round_result)
        except RuntimeError as e:
            result.error = str(e)
            result.correction_coefficient = 1.0
            return result

        # 解析法官归纳
        self._parse_judge_result(result)
        return result

    def run_iter(self):
        """
        迭代器模式运行，每完成一步 yield 一个 RoundResult
        用于 Streamlit 实时展示
        """
        for round_result in self._run_steps():
            self.rounds.append(round_result)
            yield round_result

        # 法官步骤完成后，法官的 RoundResult 已被 yield
        # 修正系数等结构化数据需要调用方从最后一轮中提取

    def _run_steps(self):
        """内部：按顺序执行五个步骤"""
        # ── 步骤 1：原告开庭陈述 ──
        opening = self.plaintiff.opening_statement(
            self.case_description,
            self.rights_assessment,
            self.infringement_assessment
        )
        rr = RoundResult(1, STEP_NAMES[1], "plaintiff",
                         self.plaintiff.role_name, opening)
        self.rounds.append(rr)
        self._transcript_parts.append(f"【{self.plaintiff.role_name}】{opening}")
        yield rr

        # ── 步骤 2：被告答辩 ──
        defense = self.defendant.defense_response(
            opening, self.case_description
        )
        rr = RoundResult(2, STEP_NAMES[2], "defendant",
                         self.defendant.role_name, defense)
        self.rounds.append(rr)
        self._transcript_parts.append(f"【{self.defendant.role_name}】{defense}")
        yield rr

        # ── 步骤 3a：原告举证 ──
        evidence_pl = self.plaintiff.evidence_presentation(
            defense, self.case_description, self.evidence_summary,
            has_rights_proof=self.evidence_checklist.get("has_rights_proof", True),
            has_infringement_proof=self.evidence_checklist.get("has_infringement_proof", True),
            has_damage_proof=self.evidence_checklist.get("has_damage_proof", False)
        )
        rr = RoundResult(3, "举证质证-原告举证", "plaintiff",
                         self.plaintiff.role_name, evidence_pl)
        self.rounds.append(rr)
        self._transcript_parts.append(f"【{self.plaintiff.role_name}】{evidence_pl}")
        yield rr

        # ── 步骤 3b：被告质证 ──
        cross_exam = self.defendant.cross_examination(evidence_pl)
        rr = RoundResult(3, "举证质证-被告质证", "defendant",
                         self.defendant.role_name, cross_exam)
        self.rounds.append(rr)
        self._transcript_parts.append(f"【{self.defendant.role_name}】{cross_exam}")
        yield rr

        # ── 步骤 4a：原告法庭辩论 ──
        transcript_before_debate = self._build_transcript()
        debate_pl = self.plaintiff.closing_argument(transcript_before_debate)
        rr = RoundResult(4, "法庭辩论-原告", "plaintiff",
                         self.plaintiff.role_name, debate_pl)
        self.rounds.append(rr)
        self._transcript_parts.append(f"【{self.plaintiff.role_name}】{debate_pl}")
        yield rr

        # ── 步骤 4b：被告法庭辩论 ──
        debate_def = self.defendant.closing_argument(
            self._build_transcript()
        )
        rr = RoundResult(4, "法庭辩论-被告", "defendant",
                         self.defendant.role_name, debate_def)
        self.rounds.append(rr)
        self._transcript_parts.append(f"【{self.defendant.role_name}】{debate_def}")
        yield rr

        # ── 步骤 5：法官归纳 ──
        full_transcript = self._build_transcript()
        judge_result = self.judge.final_summary(
            full_transcript, self.case_description
        )

        # 法官归纳可能是 JSON dict 或 error
        if isinstance(judge_result, dict) and "error" not in judge_result:
            judge_content = judge_result.get("summary", str(judge_result))
        else:
            judge_content = str(judge_result)

        rr = RoundResult(5, STEP_NAMES[5], "judge",
                         self.judge.role_name, judge_content)
        self.rounds.append(rr)
        self._transcript_parts.append(f"【{self.judge.role_name}】{judge_content}")
        yield rr

        # 保存法官结果供后续解析
        self._judge_raw_result = judge_result

    def _parse_judge_result(self, result: MootCourtResult):
        """从法官归纳中提取结构化数据"""
        judge_raw = getattr(self, "_judge_raw_result", None)
        if not judge_raw or not isinstance(judge_raw, dict):
            result.correction_coefficient = 1.0
            result.error = "法官归纳解析失败"
            return

        if "error" in judge_raw:
            result.correction_coefficient = 1.0
            result.error = f"法官归纳异常: {judge_raw.get('error', '')}"
            return

        # 提取修正系数（带安全裁剪）
        coeff = judge_raw.get("correction_coefficient", 1.0)
        try:
            coeff = float(coeff)
            coeff = max(0.70, min(1.30, coeff))  # 裁剪到合法范围
        except (TypeError, ValueError):
            coeff = 1.0
        result.correction_coefficient = coeff

        result.defense_strength = int(judge_raw.get("defense_strength", 50))
        result.judge_summary = judge_raw.get("summary", "")
        result.summary_structured = judge_raw.get("summary_structured", {}) or {}
        result.weak_points = judge_raw.get("weak_points", [])
        result.focus_points = judge_raw.get("focus_points", [])
        result.judge_scores = {
            "plaintiff": judge_raw.get("plaintiff_scores", {}),
            "defendant": judge_raw.get("defendant_scores", {}),
            "plaintiff_detail": judge_raw.get("plaintiff_scores_detail", {}),
            "defendant_detail": judge_raw.get("defendant_scores_detail", {}),
            "coefficient_reasoning": judge_raw.get("coefficient_reasoning", "")
        }


# ============================================================
# 便捷函数：一次性运行模拟法庭
# ============================================================

def run_moot_court(
    case_description: str,
    rights_assessment: str = "",
    infringement_assessment: str = "",
    evidence_summary: str = "",
    evidence_checklist: Optional[Dict[str, bool]] = None
) -> Dict[str, Any]:
    """
    便捷函数：运行完整模拟法庭，返回 dict 格式结果
    兼容 app.py 原有的 run_moot_court_simulation 接口

    返回结构:
    {
        "rounds": [{"step": 1, "step_name": "开庭陈述", "role": "plaintiff", "role_name": "原告代理律师", "content": "..."}],
        "correction_coefficient": 0.90,
        "defense_strength": 65,
        "judge_summary": "...",
        "weak_points": ["..."],
        "focus_points": ["..."],
        "judge_scores": {...},
        "error": null
    }
    """
    procedure = MootCourtProcedure(
        case_description=case_description,
        rights_assessment=rights_assessment,
        infringement_assessment=infringement_assessment,
        evidence_summary=evidence_summary,
        evidence_checklist=evidence_checklist
    )
    result = procedure.run()
    return result.to_dict()

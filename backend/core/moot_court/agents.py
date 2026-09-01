"""
模拟法庭 Agent 定义
三个独立 Agent：原告、被告、法官
每个 Agent 维护自己的角色设定和对话历史

LLM 调用统一走 core.llm_gateway（模型路由：法官归纳 node="judge" 用强模型）
"""

from typing import Dict, Any, Optional

from ..config import CAUSE_TRADEMARK
from ..llm_gateway import call_text, call_json

from . import prompts


def call_llm_text(system_prompt: str, user_prompt: str, temperature: float = 0.3) -> str:
    """纯文本（原告/被告发言），兼容旧接口"""
    return call_text(system_prompt, user_prompt, temperature=temperature)


def call_llm_json(system_prompt: str, user_prompt: str, temperature: float = 0.2) -> dict:
    """JSON（法官归纳），走强模型，兼容旧接口"""
    return call_json(system_prompt, user_prompt, node="judge", temperature=temperature)


# ============================================================
# 原告 Agent
# ============================================================

class PlaintiffAgent:
    """原告代理律师 Agent - 拥有完整案件信息和单方评估结果"""

    def __init__(self, cause_type: str = CAUSE_TRADEMARK):
        self.cause_type = cause_type
        self.profile = prompts.get_cause_profile(cause_type)
        self.system_prompt = prompts.build_system_prompt("plaintiff", cause_type)
        self.role_name = "原告代理律师"
        self.history = []

    def opening_statement(
        self,
        case_description: str,
        rights_assessment: str,
        infringement_assessment: str
    ) -> str:
        """第一步：开庭陈述"""
        user_prompt = prompts.OPENING_STATEMENT_USER.format(
            cause_guide=self.profile["opening_guide"],
            case_description=case_description[:3000],
            rights_assessment=rights_assessment[:1500] if rights_assessment else "未提供",
            infringement_assessment=infringement_assessment[:1500] if infringement_assessment else "未提供"
        )
        response = call_llm_text(self.system_prompt, user_prompt, temperature=0.3)
        self.history.append({"step": "opening", "content": response})
        return response

    def evidence_presentation(
        self,
        defendant_response: str,
        case_description: str,
        evidence_summary: str,
        has_rights_proof: bool = True,
        has_infringement_proof: bool = True,
        has_damage_proof: bool = False
    ) -> str:
        """第三步：举证回应"""
        user_prompt = prompts.EVIDENCE_PLAINTIFF_USER.format(
            cause_guide=self.profile["evidence_guide"],
            defendant_response=defendant_response[:2000],
            case_description=case_description[:2000],
            evidence_summary=evidence_summary[:1500] if evidence_summary else "未上传证据文件",
            has_rights_proof="是" if has_rights_proof else "否",
            has_infringement_proof="是" if has_infringement_proof else "否",
            has_damage_proof="是" if has_damage_proof else "否"
        )
        response = call_llm_text(self.system_prompt, user_prompt, temperature=0.3)
        self.history.append({"step": "evidence", "content": response})
        return response

    def closing_argument(self, full_transcript: str) -> str:
        """第四步：法庭辩论"""
        user_prompt = prompts.DEBATE_PLAINTIFF_USER.format(
            full_transcript=full_transcript[:4000]
        )
        response = call_llm_text(self.system_prompt, user_prompt, temperature=0.4)
        self.history.append({"step": "debate", "content": response})
        return response


# ============================================================
# 被告 Agent
# ============================================================

class DefendantAgent:
    """被告代理律师 Agent - 只知道公开案情，不知道原告内部评估"""

    def __init__(self, cause_type: str = CAUSE_TRADEMARK):
        self.cause_type = cause_type
        self.profile = prompts.get_cause_profile(cause_type)
        self.system_prompt = prompts.build_system_prompt("defendant", cause_type)
        self.role_name = "被告代理律师"
        self.history = []

    def defense_response(
        self,
        plaintiff_opening: str,
        case_description: str
    ) -> str:
        """第二步：被告答辩"""
        user_prompt = prompts.DEFENSE_RESPONSE_USER.format(
            cause_guide=self.profile["defense_guide"],
            plaintiff_opening=plaintiff_opening[:2000],
            case_description=case_description[:2000]
        )
        response = call_llm_text(self.system_prompt, user_prompt, temperature=0.3)
        self.history.append({"step": "defense", "content": response})
        return response

    def cross_examination(self, plaintiff_evidence_response: str) -> str:
        """第三步：质证"""
        user_prompt = prompts.EVIDENCE_DEFENDANT_USER.format(
            plaintiff_evidence_response=plaintiff_evidence_response[:2000]
        )
        response = call_llm_text(self.system_prompt, user_prompt, temperature=0.3)
        self.history.append({"step": "cross_exam", "content": response})
        return response

    def closing_argument(self, full_transcript: str) -> str:
        """第四步：法庭辩论"""
        user_prompt = prompts.DEBATE_DEFENDANT_USER.format(
            full_transcript=full_transcript[:4000]
        )
        response = call_llm_text(self.system_prompt, user_prompt, temperature=0.4)
        self.history.append({"step": "debate", "content": response})
        return response


# ============================================================
# 法官 Agent
# ============================================================

class JudgeAgent:
    """法官 Agent - 中立评判，产出修正系数"""

    def __init__(self, cause_type: str = CAUSE_TRADEMARK):
        self.cause_type = cause_type
        self.profile = prompts.get_cause_profile(cause_type)
        self.system_prompt = prompts.build_system_prompt("judge", cause_type)
        self.role_name = "审判法官"
        self.history = []

    def final_summary(
        self,
        full_transcript: str,
        case_description: str
    ) -> Dict[str, Any]:
        """第五步：法官归纳，返回结构化 JSON"""
        user_prompt = prompts.JUDGE_SUMMARY_USER.format(
            cause_guide=self.profile["judge"],
            full_transcript=full_transcript[:5000],
            case_description=case_description[:2000]
        )
        result = call_llm_json(self.system_prompt, user_prompt, temperature=0.2)
        self.history.append({"step": "summary", "content": result})
        return result

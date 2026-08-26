"""
模拟法庭模块 - Soft IP 主诉评估系统
多Agent对抗式庭审模拟，产出对抗检验修正系数

核心组件:
  - prompts.py:    三角色 x 五步庭审的完整提示词
  - agents.py:     原告/被告/法官三个Agent类 + LLM调用封装
  - procedure.py:  五步庭审编排器 + 结果数据结构
"""

from .procedure import MootCourtProcedure, MootCourtResult, RoundResult, run_moot_court
from .agents import PlaintiffAgent, DefendantAgent, JudgeAgent

__all__ = [
    "MootCourtProcedure",
    "MootCourtResult",
    "RoundResult",
    "run_moot_court",
    "PlaintiffAgent",
    "DefendantAgent",
    "JudgeAgent",
]

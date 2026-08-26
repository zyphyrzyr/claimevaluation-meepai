"""
Mock 数据模块 - 离线演示兜底（USE_MOCK=True 时全链路可用）
按 v4 二维模型重写：证据盘点（不评分）+ 二维评估 + 回款能力 + 决策合成
P3 扩展三案由 Mock 数据
"""

from .mock_data import (
    mock_evidence_review,
    mock_rights,
    mock_infringement,
    mock_procedure,
    mock_damages,
    mock_precedent,
    mock_defendant_profile,
    mock_rule_hits,
)

__all__ = [
    "mock_evidence_review",
    "mock_rights",
    "mock_infringement",
    "mock_procedure",
    "mock_damages",
    "mock_precedent",
    "mock_defendant_profile",
    "mock_rule_hits",
]

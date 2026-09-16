"""
CaseContext - 案件共享状态总线（§6.3）
所有评估环节不通过对话历史传信息，而是读写本对象（落 SQLite: cases.context_json）
审计轨迹（§6.4）：观点注入/引导/节点重跑/版本定稿全留痕
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class CaseContext:
    case_id: str = ""
    case_description: str = ""
    cause_type: str = "商标侵权"
    goal_type: str = "要钱"
    defendant_info: Dict[str, Any] = field(default_factory=dict)

    # 当事人列表 [{"role": "plaintiff"|"defendant", "name": str, "party_type": str}]
    # 供红线引擎的主体资格检查使用——早期实现缺这个字段，导致真实模式下
    # 每个案件都被「缺少原告信息」block 掉（评测报告 P0-1）
    parties: List[Dict[str, Any]] = field(default_factory=list)

    # 已上传证据附件的轻量元数据（不入向量库、不参与评分，仅用于红线门禁去误杀）
    # 每个元素：{file_name, file_type, parse_status}；parse_status 为 "ok"/"failed"/空
    # 用途：把「系统未能读取」与「证据缺失」区分开——文件已上传但未成功解析时，
    # 红线规则降级为 warning 而非 block，避免把系统故障记在客户头上（2026-09-16）。
    evidence_files_meta: List[Dict[str, Any]] = field(default_factory=list)

    # 证据盘点产出
    evidence_matrix: List[Dict[str, Any]] = field(default_factory=list)
    gap_list: List[Dict[str, Any]] = field(default_factory=list)
    extra_evidence: List[Dict[str, Any]] = field(default_factory=list)
    evidence_completeness: float = 0.0
    evidence_note: str = ""

    # 用户观点（注入所有 LLM 节点 prompt；§5.4 手动勾选为主）
    user_viewpoints: List[str] = field(default_factory=list)

    # 知识库注入（§7 手动勾选条目，注入所有 LLM 评估节点；保复现性）
    injected_knowledge: List[Dict[str, Any]] = field(default_factory=list)

    # 伴随式追问顾问（唯一对话 Agent）的历史
    advisor_messages: List[Dict[str, Any]] = field(default_factory=list)

    # 各维度结果 {node: {status: ok/failed/stale, result: {...}, error, updated_at}}
    dimension_results: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # 模拟法庭
    moot_transcript: List[Dict[str, Any]] = field(default_factory=list)
    correction_coeff: float = 1.0

    # 企查查画像与回款能力
    defendant_profile: Dict[str, Any] = field(default_factory=dict)
    recovery_ability: Optional[float] = None

    # 决策合成
    scores: Dict[str, float] = field(default_factory=dict)   # legal/business/final
    confidence: Optional[float] = None
    red_flags: List[Dict[str, Any]] = field(default_factory=list)
    recommendation: Dict[str, Any] = field(default_factory=dict)

    # 审计轨迹
    audit_trail: List[Dict[str, Any]] = field(default_factory=list)

    # ------------------------------------------------------

    def viewpoints_text(self) -> str:
        return "\n".join(f"- {v}" for v in self.user_viewpoints)

    def injected_knowledge_text(self) -> str:
        if not self.injected_knowledge:
            return ""
        lines = []
        for e in self.injected_knowledge:
            tag = "案件材料" if e.get("scope") == "case" else "经验库"
            lines.append(f"- [{tag}] {e.get('title', '')}：{(e.get('snippet') or '')[:200]}")
        return "\n".join(lines)

    def add_viewpoint(self, text: str, source: str = "manual") -> None:
        self.user_viewpoints.append(text)
        self.log_event("viewpoint_inject", content=text, effect=f"来源：{source}，将注入后续全部评估节点")

    def set_dimension(self, node: str, result: Dict[str, Any],
                      status: str = "ok", error: Optional[str] = None) -> None:
        self.dimension_results[node] = {
            "status": status,
            "result": result,
            "error": error,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }

    def mark_stale(self, nodes: List[str], reason: str = "") -> None:
        for node in nodes:
            if node in self.dimension_results:
                self.dimension_results[node]["status"] = "stale"
                self.dimension_results[node]["stale_reason"] = reason

    def log_event(self, event_type: str, node: Optional[str] = None,
                  content: str = "", effect: str = "") -> None:
        self.audit_trail.append({
            "event_type": event_type,
            "node": node,
            "content": content,
            "effect": effect,
            "time": datetime.now().isoformat(timespec="seconds"),
        })

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CaseContext":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in (data or {}).items() if k in known})

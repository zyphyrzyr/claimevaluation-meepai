"""
规则引擎 - 商标侵权案件红线检查
第一版规则化内容：
1. 诉讼时效是否临期
2. 主体资格是否明显缺失
3. 仲裁条款是否存在
4. 是否缺少关键权利证明
5. 是否缺少关键侵权固定证据
6. 是否缺少损害赔偿主张基础材料
"""

from typing import List, Dict, Any
from datetime import datetime, timedelta
from dataclasses import dataclass

@dataclass
class RuleResult:
    """规则检查结果"""
    rule_code: str
    rule_name: str
    severity: str  # pass, warning, block
    result: str
    reason: str

class RuleEngine:
    """规则引擎"""

    def __init__(self):
        self.rules = []
        self._register_rules()

    def _register_rules(self):
        """注册所有规则"""
        self.rules = [
            self.check_statute_of_limitations,
            self.check_subject_qualification,
            self.check_arbitration_clause,
            self.check_missing_rights_proof,
            self.check_missing_infringement_proof,
            self.check_missing_damage_proof
        ]

    def run_all(self, case_facts: Dict[str, Any]) -> List[RuleResult]:
        """运行所有规则"""
        results = []
        for rule_func in self.rules:
            result = rule_func(case_facts)
            results.append(result)
        return results

    def check_statute_of_limitations(self, case_facts: Dict) -> RuleResult:
        """
        规则1: 诉讼时效检查
        商标侵权诉讼时效为3年（知道或应当知道侵权行为之日起）
        """
        timeline = case_facts.get("timeline", [])

        # 查找侵权行为发现时间
        infringement_discovery_date = None
        for event in timeline:
            if "侵权" in event.get("event", "") or "发现" in event.get("event", ""):
                try:
                    infringement_discovery_date = datetime.strptime(event["date"], "%Y-%m-%d")
                except:
                    pass

        if not infringement_discovery_date:
            return RuleResult(
                rule_code="statute_of_limitations",
                rule_name="诉讼时效检查",
                severity="warning",
                result="无法确定时效起算点",
                reason="案情描述中未明确侵权行为发现时间，建议补充"
            )

        # 计算时效到期日
        limitation_deadline = infringement_discovery_date + timedelta(days=3*365)

        # 判断是否过期或临期
        now = datetime.now()
        days_remaining = (limitation_deadline - now).days

        if days_remaining < 0:
            return RuleResult(
                rule_code="statute_of_limitations",
                rule_name="诉讼时效检查",
                severity="block",
                result="诉讼时效已过",
                reason=f"侵权行为发现于 {infringement_discovery_date.strftime('%Y-%m-%d')}，诉讼时效已于 {limitation_deadline.strftime('%Y-%m-%d')} 届满"
            )
        elif days_remaining < 180:  # 6个月内临期
            return RuleResult(
                rule_code="statute_of_limitations",
                rule_name="诉讼时效检查",
                severity="warning",
                result="诉讼时效临期",
                reason=f"诉讼时效剩余 {days_remaining} 天，建议尽快启动诉讼"
            )
        else:
            return RuleResult(
                rule_code="statute_of_limitations",
                rule_name="诉讼时效检查",
                severity="pass",
                result="诉讼时效未过期",
                reason=f"诉讼时效剩余 {days_remaining} 天，在有效期内"
            )

    def check_subject_qualification(self, case_facts: Dict) -> RuleResult:
        """
        规则2: 主体资格检查
        原告必须是商标注册人或独占许可被许可人
        """
        parties = case_facts.get("parties", [])
        evidence_checklist = case_facts.get("evidence_checklist", {})

        # 检查是否有原告
        has_plaintiff = any(p["role"] == "plaintiff" for p in parties)
        if not has_plaintiff:
            return RuleResult(
                rule_code="subject_qualification",
                rule_name="主体资格检查",
                severity="block",
                result="缺少原告信息",
                reason="未识别到原告（权利人）信息"
            )

        # 检查是否有权利证明
        has_rights_proof = evidence_checklist.get("has_rights_proof", False)
        if not has_rights_proof:
            return RuleResult(
                rule_code="subject_qualification",
                rule_name="主体资格检查",
                severity="warning",
                result="权利证明可能缺失",
                reason="未检测到商标注册证或独占许可合同上传"
            )

        return RuleResult(
            rule_code="subject_qualification",
            rule_name="主体资格检查",
            severity="pass",
            result="原告主体资格完整",
            reason="原告为注册商标持有人或独占被许可人"
        )

    def check_arbitration_clause(self, case_facts: Dict) -> RuleResult:
        """
        规则3: 仲裁协议检查
        如果合同中存在仲裁条款，法院可能无管辖权
        """
        case_description = case_facts.get("case_description", "")

        # 关键词检测
        arbitration_keywords = ["仲裁", "仲裁委员会", "arbitration", "仲裁条款"]
        has_arbitration = any(kw in case_description for kw in arbitration_keywords)

        if has_arbitration:
            return RuleResult(
                rule_code="arbitration_clause",
                rule_name="仲裁协议检查",
                severity="warning",
                result="可能存在仲裁协议",
                reason="案情描述中提及仲裁相关表述，建议核查合同是否存在仲裁条款"
            )

        return RuleResult(
            rule_code="arbitration_clause",
            rule_name="仲裁协议检查",
            severity="pass",
            result="未发现仲裁协议",
            reason="案情描述中未提及仲裁条款"
        )

    def check_missing_rights_proof(self, case_facts: Dict) -> RuleResult:
        """
        规则4: 权利证明缺失检查
        必须提供：商标注册证、续展证明（如需要）
        """
        evidence_checklist = case_facts.get("evidence_checklist", {})
        has_rights_proof = evidence_checklist.get("has_rights_proof", False)

        if not has_rights_proof:
            return RuleResult(
                rule_code="missing_rights_proof",
                rule_name="权利证明缺失检查",
                severity="block",
                result="缺少权利证明",
                reason="未上传商标注册证或商标权利证明文件的，无法证明原告权利基础"
            )

        return RuleResult(
            rule_code="missing_rights_proof",
            rule_name="权利证明缺失检查",
            severity="pass",
            result="权利证明文件齐全",
            reason="已上传商标注册证"
        )

    def check_missing_infringement_proof(self, case_facts: Dict) -> RuleResult:
        """
        规则5: 侵权固定证据缺失检查
        必须提供：侵权商品截图/实物、公证文书（建议）
        """
        evidence_checklist = case_facts.get("evidence_checklist", {})
        has_infringement_proof = evidence_checklist.get("has_infringement_proof", False)

        if not has_infringement_proof:
            return RuleResult(
                rule_code="missing_infringement_proof",
                rule_name="侵权固定证据缺失检查",
                severity="block",
                result="缺少侵权固定证据",
                reason="未上传侵权商品截图、购买记录或公证文书，无法证明侵权行为存在"
            )

        # 检查是否有公证文书（最佳实践）
        case_description = case_facts.get("case_description", "")
        has_notarization = "公证" in case_description

        if not has_notarization:
            return RuleResult(
                rule_code="missing_infringement_proof",
                rule_name="侵权固定证据缺失检查",
                severity="warning",
                result="侵权证据未公证",
                reason="侵权证据未进行公证固定，建议补充公证文书以增强证据效力"
            )

        return RuleResult(
            rule_code="missing_infringement_proof",
            rule_name="侵权固定证据缺失检查",
            severity="pass",
            result="侵权证据完整",
            reason="已上传侵权证据及公证文书"
        )

    def check_missing_damage_proof(self, case_facts: Dict) -> RuleResult:
        """
        规则6: 损害赔偿证据缺失检查
        赔偿依据：被告获利 / 原告损失 / 许可费倍数 / 法定赔偿
        """
        evidence_checklist = case_facts.get("evidence_checklist", {})
        has_damage_proof = evidence_checklist.get("has_damage_proof", False)

        if not has_damage_proof:
            return RuleResult(
                rule_code="missing_damage_proof",
                rule_name="损害赔偿证据缺失检查",
                severity="warning",
                result="损害赔偿计算依据不足",
                reason="未提供被告获利证据、原告损失证据或许可费证据，赔偿额可能依赖法定赔偿（通常较低）"
            )

        return RuleResult(
            rule_code="missing_damage_proof",
            rule_name="损害赔偿证据缺失检查",
            severity="pass",
            result="损害赔偿证据齐全",
            reason="已提供赔偿计算依据"
        )

def run_rule_engine(case_facts: Dict) -> List[Dict[str, Any]]:
    """
    对外接口：运行规则引擎
    返回格式化的规则检查结果
    """
    engine = RuleEngine()
    results = engine.run_all(case_facts)

    return [
        {
            "rule_code": r.rule_code,
            "rule_name": r.rule_name,
            "severity": r.severity,
            "result": r.result,
            "reason": r.reason
        }
        for r in results
    ]

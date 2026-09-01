"""
规则引擎 - 主诉红线硬门禁（三案由通用：商标侵权 / 著作权侵权 / 不正当竞争）

六条规则（纯规则、零 LLM）：
1. 诉讼时效是否临期
2. 主体资格是否明显缺失
3. 仲裁条款是否存在
4. 是否缺少关键权利证明          ← block
5. 是否缺少关键侵权固定证据      ← block
6. 是否缺少损害赔偿主张基础材料

文案按案由中立撰写：权利证明在不同案由下分别是商标注册证、著作权登记证书、
「有一定影响」的知名度证据，规则只判断是否缺失，不预设具体证据名称。
"""

from typing import List, Dict, Any, Iterable
from datetime import datetime, timedelta
from dataclasses import dataclass

# v4 证据矩阵（evidence_matrix）的类别 -> 规则引擎证据清单（evidence_checklist）的字段
# 映射口径：该类别下存在任一 status == "sufficient" 的条目即为 True
EVIDENCE_CATEGORY_TO_CHECK = {
    "has_rights_proof": "权利基础证据",
    "has_infringement_proof": "侵权认定证据",
    "has_damage_proof": "损害赔偿证据",
}


def build_evidence_checklist(evidence_matrix: Iterable[Dict[str, Any]]) -> Dict[str, bool]:
    """
    v4 的 evidence_matrix（每项带 category/status）→ 规则引擎所需的
    evidence_checklist（三个布尔值）。

    这是两套数据结构之间唯一的映射层。编排器与模拟法庭都走这里，
    避免各写一份导致口径漂移。

    口径：某类别下存在任一 status == "sufficient" 的条目即为 True。
    partial / missing 均视为 False——证据不完整在民事诉讼中通常不足以支撑主张。
    """
    matrix = list(evidence_matrix or [])
    return {
        field: any(m.get("category") == category and m.get("status") == "sufficient"
                   for m in matrix)
        for field, category in EVIDENCE_CATEGORY_TO_CHECK.items()
    }


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
        原告必须是权利人或独占许可被许可人
        （商标案由下为商标注册人，著作权案由下为著作权人/专有使用权人，
          不正当竞争案由下为受影响经营者）"""
        parties = case_facts.get("parties", [])
        evidence_checklist = case_facts.get("evidence_checklist", {})

        if not parties:
            # 关键区分：「没采集到当事人数据」不等于「经核实没有原告」。
            # 前者是信息缺失（warning），后者才是实体缺陷（block）。
            # 早期实现把两者混为一谈，导致 parties 字段一空就 block 掉每一个案件。
            return RuleResult(
                rule_code="subject_qualification",
                rule_name="主体资格检查",
                severity="warning",
                result="未采集当事人信息",
                reason="未获取到原被告主体信息，无法校验主体资格，建议补充填写"
            )

        # 检查是否有原告
        has_plaintiff = any(p.get("role") == "plaintiff" for p in parties)
        if not has_plaintiff:
            return RuleResult(
                rule_code="subject_qualification",
                rule_name="主体资格检查",
                severity="block",
                result="缺少原告信息",
                reason=f"已录入 {len(parties)} 位当事人，但其中没有原告（权利人）"
            )

        # 检查是否有权利证明
        has_rights_proof = evidence_checklist.get("has_rights_proof", False)
        if not has_rights_proof:
            return RuleResult(
                rule_code="subject_qualification",
                rule_name="主体资格检查",
                severity="warning",
                result="权利证明可能缺失",
                reason="未检测到权利基础证明文件（商标注册证 / 作品登记证书 / 权属合同）上传"
            )

        return RuleResult(
            rule_code="subject_qualification",
            rule_name="主体资格检查",
            severity="pass",
            result="原告主体资格完整",
            reason="已录入原告，且持有权利基础证明"
        )

    def check_arbitration_clause(self, case_facts: Dict) -> RuleResult:
        """
        规则3: 仲裁协议检查
        如果合同中存在仲裁条款，法院可能无管辖权
        """
        case_description = case_facts.get("case_description", "")

        # 关键词检测。先排除否定表述——「双方未约定仲裁协议」同样含「仲裁」二字，
        # 不排除的话会把「明确没有仲裁条款」的案件误报为「可能存在仲裁协议」。
        arbitration_keywords = ["仲裁", "仲裁委员会", "arbitration", "仲裁条款"]
        negation_phrases = ["无仲裁", "没有仲裁", "未约定仲裁", "未签仲裁",
                            "不存在仲裁", "不涉仲裁", "不通过仲裁", "未经仲裁"]
        has_arbitration = (any(kw in case_description for kw in arbitration_keywords)
                           and not any(p in case_description for p in negation_phrases))

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
        必须提供：商标注册证 / 作品登记证书 / 权属与知名度证据（按案由而定）
        """
        evidence_checklist = case_facts.get("evidence_checklist", {})
        has_rights_proof = evidence_checklist.get("has_rights_proof", False)

        if not has_rights_proof:
            return RuleResult(
                rule_code="missing_rights_proof",
                rule_name="权利证明缺失检查",
                severity="block",
                result="缺少权利证明",
                reason="未上传权利基础证明文件，无法证明原告权利基础"
            )

        return RuleResult(
            rule_code="missing_rights_proof",
            rule_name="权利证明缺失检查",
            severity="pass",
            result="权利证明文件齐全",
            reason="已上传权利基础证明"
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

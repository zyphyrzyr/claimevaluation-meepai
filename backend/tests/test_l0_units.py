"""
L0 单元层评测：评分引擎 / 红线引擎 / 证据盘点（纯函数，零 LLM 零 IO）
运行：backend/ 目录下 pytest tests/test_l0_units.py -v
"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("USE_MOCK", "True")

from core import scoring, legal_rules, evidence_review
from core import config
from core.config import SCORE_THRESHOLD_GO, SCORE_THRESHOLD_PATCH


# ============================================================
# 1. normalize / clip_coefficient 边界
# ============================================================

class TestNormalize:
    def test_normal_in_range(self):
        assert scoring.normalize(50) == 0.5
        assert scoring.normalize(0) == 0.0
        assert scoring.normalize(100) == 1.0

    def test_negative_clamped_to_zero(self):
        assert scoring.normalize(-20) == 0.0

    def test_over_100_clamped_to_one(self):
        assert scoring.normalize(150) == 1.0

    def test_custom_scale(self):
        assert scoring.normalize(5, scale=10) == 0.5

    def test_nan_silently_collapses_to_zero(self):
        """
        【实测风险 P2】NaN 输入不抛异常，被静默归零：
        min(nan,100)=nan → max(0,nan)=0（因为 nan>0 为 False）
        后果：LLM 返回 NaN/None 参与运算时，节点分数静默变 0，
        结论直接落到"暂缓"，而前端看不到任何错误提示。
        """
        assert scoring.normalize(float("nan")) == 0.0
        assert scoring.calculate_legal_feasibility(float("nan"), 80, 80) == 0.0


class TestClipCoefficient:
    def test_within_range_unchanged(self):
        assert scoring.clip_coefficient(1.0) == 1.0

    def test_below_min_clamped(self):
        assert scoring.clip_coefficient(0.1) == 0.7

    def test_above_max_clamped(self):
        assert scoring.clip_coefficient(5.0) == 1.3

    def test_negative_clamped(self):
        assert scoring.clip_coefficient(-3.0) == 0.7

    def test_boundary_values(self):
        assert scoring.clip_coefficient(0.7) == 0.7
        assert scoring.clip_coefficient(1.3) == 1.3


# ============================================================
# 2. 法律可行性
# ============================================================

class TestLegalFeasibility:
    def test_full_marks(self):
        assert scoring.calculate_legal_feasibility(100, 100, 100) == 100.0

    def test_zero_if_any_dimension_zero(self):
        """一票否决：任一维度为 0，整体为 0"""
        assert scoring.calculate_legal_feasibility(0, 90, 90) == 0.0
        assert scoring.calculate_legal_feasibility(90, 0, 90) == 0.0
        assert scoring.calculate_legal_feasibility(90, 90, 0) == 0.0

    def test_self_consistency(self):
        """v2 校准：三维同分时法律可行性等于该分数（旧连乘模型下是 80³=51.2）"""
        assert scoring.calculate_legal_feasibility(80, 80, 80) == pytest.approx(80.0, abs=0.05)
        assert scoring.calculate_legal_feasibility(60, 60, 60) == pytest.approx(60.0, abs=0.05)

    def test_short_board_penalty(self):
        """幂平均的短板惩罚：结果低于算术平均，且 p 越负惩罚越重"""
        # 90,90,30 的算术平均是 70；p=-0.5 的结果必须低于 70
        assert scoring.calculate_legal_feasibility(90, 90, 30) < 70.0
        # 且高于最小值 30
        assert scoring.calculate_legal_feasibility(90, 90, 30) > 30.0

    def test_coefficient_applied(self):
        """修正系数仍为乘法语义（取不会触发 clip 的取值）"""
        base = scoring.calculate_legal_feasibility(60, 60, 60)
        boosted = scoring.calculate_legal_feasibility(60, 60, 60, 1.3)
        assert boosted == pytest.approx(base * 1.3, abs=0.05)

    def test_coefficient_result_clipped_to_100(self):
        """修正系数放大后须截断到 100，不得溢出"""
        assert scoring.calculate_legal_feasibility(95, 95, 95, 1.3) == 100.0

    def test_negative_input(self):
        assert scoring.calculate_legal_feasibility(-10, 50, 50) == 0.0


# ============================================================
# 3. 业务预期分流
# ============================================================

class TestBusinessExpectation:
    def test_fame_goal_equals_precedent_value(self):
        result = scoring.calculate_business_expectation("要名", precedent_value=80)
        assert result == 80.0

    def test_money_goal(self):
        """v2 校准：判赔 80 × 回款 80 的幂平均 = 80（旧连乘模型下是 64.0）"""
        result = scoring.calculate_business_expectation("要钱", damages_scale=80, recovery_ability=80)
        assert result == pytest.approx(80.0, abs=0.05)

    def test_fame_goal_ignores_money_factors(self):
        """要名：只算判例价值，判赔/回款不参与"""
        result = scoring.calculate_business_expectation(
            "要名", damages_scale=100, recovery_ability=100, precedent_value=60)
        assert result == 60.0

    def test_money_goal_missing_recovery_defaults_to_zero(self):
        """回款能力缺失 → 业务预期 0（一票否决）"""
        result = scoring.calculate_business_expectation("要钱", damages_scale=90, recovery_ability=None)
        assert result == 0.0

    def test_fame_goal_missing_precedent_defaults_to_zero(self):
        result = scoring.calculate_business_expectation("要名", precedent_value=None)
        assert result == 0.0

    def test_unknown_goal_falls_back_to_fame_branch(self):
        """非'要钱'的任意值都走要名分支——隐含兜底，需确认是否符合预期"""
        result = scoring.calculate_business_expectation("要流量", precedent_value=70)
        assert result == 70.0


# ============================================================
# 4. 总分 / 置信度
# ============================================================

class TestOverallScore:
    def test_self_consistency(self):
        """v2 校准：五维各 80 分 → 最终 80 分（旧连乘模型下是 32.8）"""
        legal = scoring.calculate_legal_feasibility(80, 80, 80)
        biz = scoring.calculate_business_expectation("要钱", damages_scale=80, recovery_ability=80)
        final = scoring.calculate_overall_score(legal, biz)
        assert final == pytest.approx(80.0, abs=0.1)

    def test_full_marks(self):
        assert scoring.calculate_overall_score(100, 100) == 100.0

    def test_zero_propagation(self):
        assert scoring.calculate_overall_score(0, 80) == 0.0
        assert scoring.calculate_overall_score(80, 0) == 0.0


class TestConfidence:
    def test_direct_mapping(self):
        assert scoring.calculate_confidence(75) == 75.0

    def test_retrieval_incomplete_downgrade(self):
        result = scoring.calculate_confidence(80, retrieval_complete=False)
        assert result == pytest.approx(68.0, abs=0.05)

    def test_upper_bound(self):
        assert scoring.calculate_confidence(150) == 100.0


# ============================================================
# 5. 决策建议四档
# ============================================================

class TestRecommendation:
    def test_block_flag_overrides_high_score(self):
        """红线硬门禁优先级最高，95 分也要拦截"""
        result = scoring.generate_recommendation(
            95.0, [{"severity": "block"}], is_complete=True)
        assert result["level"] == "block"
        assert result["recommendation"] == "暂不建议起诉"

    def test_incomplete_evaluation(self):
        result = scoring.generate_recommendation(
            80.0, [], is_complete=False, missing_dimensions=["侵权认定", "判赔规模"])
        assert result["level"] == "yellow"
        assert "侵权认定、判赔规模" in result["reason"]

    def test_none_score_is_incomplete(self):
        result = scoring.generate_recommendation(None, [], is_complete=True)
        assert result["level"] == "yellow"

    def test_go_threshold(self):
        result = scoring.generate_recommendation(SCORE_THRESHOLD_GO, [], is_complete=True)
        assert result["level"] == "green"

    def test_patch_threshold(self):
        result = scoring.generate_recommendation(
            SCORE_THRESHOLD_PATCH, [], is_complete=True,
            dimension_scores={"权利基础": 90, "侵权认定": 45, "诉讼程序": 80})
        assert result["level"] == "yellow"
        assert "侵权认定" in result["reason"], "60-74 档应指出最弱维度"

    def test_below_patch(self):
        result = scoring.generate_recommendation(
            SCORE_THRESHOLD_PATCH - 0.1, [], is_complete=True)
        assert result["level"] == "red"

    def test_low_confidence_note_appended(self):
        result = scoring.generate_recommendation(
            80.0, [], is_complete=True, confidence=30)
        assert "置信度 30%" in result["reason"]

    def test_confidence_note_absent_when_high(self):
        result = scoring.generate_recommendation(
            80.0, [], is_complete=True, confidence=80)
        assert "置信度" not in result["reason"]

    def test_weakest_dimension_empty(self):
        assert scoring.weakest_dimension({}) is None

    def test_has_block_red_flag_variants(self):
        assert scoring.has_block_red_flag([{"severity": "block"}]) is True
        assert scoring.has_block_red_flag([{"status": "block"}]) is True
        assert scoring.has_block_red_flag([{"severity": "warning"}]) is False
        assert scoring.has_block_red_flag([]) is False


# ============================================================
# 6. 红线引擎 6 条规则
# ============================================================

def _base_case_facts(**overrides):
    facts = {
        "case_description": "原告持有注册商标，被告销售侵权商品，已办理侵权公证。",
        "timeline": [{"event": "发现侵权行为", "date": "2026-01-01"}],
        "parties": [{"role": "plaintiff", "name": "原告公司"}],
        "evidence_checklist": {
            "has_rights_proof": True,
            "has_infringement_proof": True,
            "has_damage_proof": True,
        },
    }
    facts.update(overrides)
    return facts


class TestRuleEngine:
    def test_all_pass_baseline(self):
        results = legal_rules.run_rule_engine(_base_case_facts())
        assert len(results) == 6
        severities = [r["severity"] for r in results]
        assert severities == ["pass"] * 6, f"基线案件应全部 pass，实际：{severities}"

    def test_expired_statute_blocks(self):
        facts = _base_case_facts(
            timeline=[{"event": "发现侵权行为", "date": "2015-01-01"}])
        results = {r["rule_code"]: r for r in legal_rules.run_rule_engine(facts)}
        assert results["statute_of_limitations"]["severity"] == "block"

    def test_near_expiry_warns(self):
        """时效剩余 <180 天 → warning（用动态日期，避免用例随时间腐坏）"""
        from datetime import datetime, timedelta
        near = (datetime.now() - timedelta(days=3 * 365 - 30)).strftime("%Y-%m-%d")
        facts = _base_case_facts(timeline=[{"event": "发现侵权行为", "date": near}])
        results = {r["rule_code"]: r for r in legal_rules.run_rule_engine(facts)}
        assert results["statute_of_limitations"]["severity"] == "warning"

    def test_missing_timeline_warns(self):
        facts = _base_case_facts(timeline=[])
        results = {r["rule_code"]: r for r in legal_rules.run_rule_engine(facts)}
        assert results["statute_of_limitations"]["severity"] == "warning"

    def test_parties_not_collected_is_warning_not_block(self):
        """
        「没采集到当事人数据」是信息缺失，不是实体缺陷，只应 warning。

        早期实现把两者混为一谈：parties=[] 时直接 block，而编排器又从未传过
        parties 字段 —— 结果是真实模式下每一个案件都被硬门禁拦截（评测报告 P0-1）。
        """
        facts = _base_case_facts(parties=[])
        results = {r["rule_code"]: r for r in legal_rules.run_rule_engine(facts)}
        hit = results["subject_qualification"]
        assert hit["severity"] == "warning"
        assert "未采集" in hit["result"]

    def test_parties_present_but_no_plaintiff_blocks(self):
        """有当事人数据、但其中没有原告 —— 这才是真正的实体缺陷，必须 block"""
        facts = _base_case_facts(parties=[{"role": "defendant", "name": "被告公司"}])
        results = {r["rule_code"]: r for r in legal_rules.run_rule_engine(facts)}
        hit = results["subject_qualification"]
        assert hit["severity"] == "block"
        assert "原告" in hit["reason"]

    def test_party_without_role_key_does_not_crash(self):
        """当事人缺 role 字段时不得抛 KeyError"""
        facts = _base_case_facts(parties=[{"name": "某公司"}])
        results = {r["rule_code"]: r for r in legal_rules.run_rule_engine(facts)}
        assert results["subject_qualification"]["severity"] == "block"

    def test_missing_rights_proof_blocks(self):
        facts = _base_case_facts(
            evidence_checklist={"has_rights_proof": False,
                                "has_infringement_proof": True,
                                "has_damage_proof": True})
        results = {r["rule_code"]: r for r in legal_rules.run_rule_engine(facts)}
        assert results["missing_rights_proof"]["severity"] == "block"

    def test_arbitration_keyword_detected(self):
        facts = _base_case_facts(
            case_description="双方合同中约定提交北京仲裁委员会仲裁。")
        results = {r["rule_code"]: r for r in legal_rules.run_rule_engine(facts)}
        assert results["arbitration_clause"]["severity"] == "warning"

    def test_arbitration_false_positive_risk(self):
        """案情里出现'仲裁'以外的同形词会误报——此用例钉住关键词匹配的边界"""
        facts = _base_case_facts(case_description="原告主张仲裁时效中断，法院未支持。")
        results = {r["rule_code"]: r for r in legal_rules.run_rule_engine(facts)}
        assert results["arbitration_clause"]["severity"] == "warning"


# ============================================================
# 7. 证据盘点完整度
# ============================================================

class TestCompleteness:
    def test_empty_matrix(self):
        assert evidence_review._completeness([]) == 0.0

    def test_all_sufficient(self):
        matrix = [{"status": "sufficient"}] * 4
        assert evidence_review._completeness(matrix) == 100.0

    def test_all_missing(self):
        matrix = [{"status": "missing"}] * 4
        assert evidence_review._completeness(matrix) == 0.0

    def test_mixed_weighting(self):
        """2 充足 + 1 不足 + 1 缺失 = (1+1+0.5+0)/4 = 62.5"""
        matrix = [{"status": "sufficient"}, {"status": "sufficient"},
                  {"status": "partial"}, {"status": "missing"}]
        assert evidence_review._completeness(matrix) == 62.5

    def test_unknown_status_treated_as_missing(self):
        matrix = [{"status": "sufficient"}, {"status": "乱码"}]
        assert evidence_review._completeness(matrix) == 50.0

    def test_missing_status_key_treated_as_missing(self):
        matrix = [{"status": "sufficient"}, {}]
        assert evidence_review._completeness(matrix) == 50.0


# ============================================================
# 8. 三案由清单完整性
# ============================================================

class TestChecklists:
    CAUSES = ["商标侵权", "著作权侵权", "不正当竞争"]
    CATEGORIES = ["权利基础证据", "侵权认定证据", "损害赔偿证据", "取证技术规范"]

    def test_three_causes_present(self):
        for cause in self.CAUSES:
            assert cause in evidence_review.CHECKLISTS, f"缺少案由清单：{cause}"

    def test_four_categories_per_cause(self):
        for cause in self.CAUSES:
            checklist = evidence_review.CHECKLISTS[cause]
            for cat in self.CATEGORIES:
                assert cat in checklist, f"{cause} 缺少类别 {cat}"
                assert len(checklist[cat]) > 0, f"{cause}/{cat} 清单为空"

    def test_every_entry_has_basis_and_element(self):
        for cause in self.CAUSES:
            for cat, entries in evidence_review.CHECKLISTS[cause].items():
                for e in entries:
                    assert e.get("item"), f"{cause}/{cat} 缺 item"
                    assert e.get("basis"), f"{cause}/{cat}/{e.get('item')} 缺法律出处"
                    assert e.get("element"), f"{cause}/{cat}/{e.get('item')} 缺要件"

    def test_unknown_cause_raises_instead_of_falling_back(self):
        """
        未知案由必须显式报错，不许静默回落到商标清单。

        旧行为是 CHECKLISTS.get(cause, CHECKLISTS[商标])：用户选了「专利侵权」却拿到
        一份商标证据清单，且全程无任何提示。评估报告看起来一切正常，实际上整份证据
        盘点是错案由的。宁可让流程失败并报出「不支持的案由」，也不要交付一份看起来
        完整、实际答非所问的结果。
        """
        with pytest.raises(ValueError, match="不支持的案由"):
            evidence_review._build_prompt("专利侵权", "案情", "证据", "观点")

    def test_unknown_cause_never_silently_produces_a_prompt(self):
        """反面钉死：任何未知案由都拿不到 prompt（含空串 / None / 近似拼写）"""
        for bogus in ["专利侵权", "", "商标", "商标权侵权", "TRADEMARK", None]:
            try:
                evidence_review._build_prompt(bogus, "案情", "证据", "观点")
            except (ValueError, KeyError, TypeError):
                continue
            raise AssertionError(f"案由 {bogus!r} 未被告警却产出了 prompt")

    def test_require_supported_cause_passes_through_known_causes(self):
        """已知案由原样返回，不改写、不归一化"""
        for cause in self.CAUSES:
            assert evidence_review.require_supported_cause(cause) == cause


# ============================================================
# 9. 运行时配置
# ============================================================

class TestRuntimeSettings:
    """
    配置键的大小写一致性。

    .env 里写的是 USE_MOCK / DEEPSEEK_API_KEY，而 get_runtime_settings() 原本只返回
    全小写键。照 .env 的写法去访问会静默拿到 None——不抛错，但配置等于没生效。
    写真实 API 探针脚本时正是踩了这一脚：USE_MOCK=True 却判定成真实模式。
    """

    def test_both_cases_are_readable(self):
        settings = config.get_runtime_settings()
        for key in ("use_mock", "deepseek_api_key", "pkulaw_api_token",
                    "qcc_api_token", "siliconflow_api_key", "deepseek_base_url"):
            assert key in settings, f"缺小写键 {key}"
            assert key.upper() in settings, f"缺大写键 {key.upper()}"
            assert settings[key] == settings[key.upper()], (
                f"{key} 与 {key.upper()} 取值不一致")

    def test_use_mock_is_a_boolean(self):
        settings = config.get_runtime_settings()
        assert isinstance(settings["use_mock"], bool)
        assert isinstance(settings["USE_MOCK"], bool)

    def test_missing_keys_default_to_empty_string(self):
        """未配置的 key 应为空串而非 None，调用方才能安全地 .strip()"""
        settings = config.get_runtime_settings()
        for key in ("deepseek_api_key", "qcc_api_token",
                    "pkulaw_api_token", "siliconflow_api_key"):
            assert isinstance(settings[key], str), f"{key} 不是字符串"

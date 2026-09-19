"""
L0 回款能力规则表（基准 78 + 加减分）

此前回款能力是「50 起算 × 乘法」：
  ① 基准 50 让它成了业务预期的固定短板（幂平均 p=-0.5 短板主导），
     「公开记录查不到问题」被当成「回款前景中等」，判赔分再高也拉不动；
  ② 乘法在高基准上会撞顶（78×1.3=101），有利信号失效、惩罚被稀释，
     「有不利往下扣、有利往上加」在实现层面根本不成立。

改为 78 起算 + 加减分后要钉住四件事：
1. 基准值本身（与决策 GO 线一致，不能悄悄退回 50）
2. 是加减而不是乘（同一信号在两种模型下结果不同，乘法会撞顶）
3. 一票否决仍是一票否决（主体不存在 → 0，不参与加减）
4. 「只影响周期、不影响分数」的项被显式记录，界面才不会自相矛盾

顺带钉住此前「采集了却不参与计算」的 P3 轻微项与时间类项——
它们要是又变回只展示，界面上会出现「明细写着命中、下面说没命中」的老毛病。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("USE_MOCK", "True")

from core.config import (
    RECOVERY_BASE_SCORE, RECOVERY_TIER_OK, RECOVERY_TIER_WEAK)
from core.qcc_api import (
    BONUS_FINANCIAL, BONUS_LISTING,
    PENALTY_BUSINESS_EXCEPTION, PENALTY_DISHONEST,
    PENALTY_MINOR_CAP, _stage_h_calc_metrics)

EMPTY_B = {"上市信息": {}, "财务数据": {}}
EMPTY_G = {"被诉历史": {"_count": 0}}


def _d(**kw) -> dict:
    """把 {标签: 条数} 变成阶段 D 的形状；不在 kwargs 里的项等于没查到（0 条）"""
    return {k: {"_count": v} for k, v in kw.items()}


def _calc(stage_d: dict, stage_b: dict = None, stage_g: dict = None) -> dict:
    return _stage_h_calc_metrics(
        stage_b if stage_b is not None else dict(EMPTY_B),
        {}, stage_d, {}, {},
        stage_g if stage_g is not None else dict(EMPTY_G),
    )


# ============================================================
# 1. 基准
# ============================================================

class TestBaseline:
    def test_clean_record_lands_on_the_base_not_fifty(self):
        """
        核心回归点：公开记录干净 = 78，不是 50。

        基准 50 会让每个「查不到问题」的被告都变成业务预期的短板，
        把「没有下调依据」错当成「回款前景中等」。
        """
        m = _calc(_d())
        assert m["recovery_probability"] == RECOVERY_BASE_SCORE == 78.0
        assert m["recovery_tier"] == "ok"
        assert m["red_flags"] == [] and m["green_flags"] == []

    def test_base_is_aligned_with_the_decision_go_line(self):
        """基准与决策 GO 线同源，否则「回款没问题」和「总分不达标」会互相打脸"""
        from core.config import SCORE_THRESHOLD_GO
        assert RECOVERY_BASE_SCORE == float(SCORE_THRESHOLD_GO)


# ============================================================
# 2. 加减分（不是乘法）
# ============================================================

class TestAdditiveNotMultiplicative:
    def test_penalty_subtracts_a_fixed_amount(self):
        """经营异常扣固定 12 分，不是「×0.8」那种随基准缩放的比例"""
        m = _calc(_d(经营异常=1))
        assert m["recovery_probability"] == pytest.approx(
            RECOVERY_BASE_SCORE - PENALTY_BUSINESS_EXCEPTION)

    def test_dishonest_does_not_go_to_zero_but_is_still_weak(self):
        m = _calc(_d(失信信息=2))
        assert m["recovery_probability"] == pytest.approx(
            RECOVERY_BASE_SCORE - PENALTY_DISHONEST)
        assert m["recovery_tier"] == "weak"

    def test_bonuses_add_up_instead_of_hitting_the_ceiling(self):
        """
        有利信号在高基准上必须还有空间。

        乘法下 78×1.3×1.2 = 121.7 直接撞顶 100，上市与财务公开两类信号
        变得毫无区别——这是改加减分的直接动因。
        """
        b = {"上市信息": {"_count": 1}, "财务数据": {"_items": [{"营收": 1}]}}
        m = _calc(_d(), stage_b=b)
        assert m["recovery_probability"] == pytest.approx(
            RECOVERY_BASE_SCORE + BONUS_LISTING + BONUS_FINANCIAL)
        assert m["recovery_probability"] < 100

    def test_minor_issues_are_capped(self):
        """轻微合规瑕疵合计封顶，避免小额欠税 + 小处罚堆成重罚"""
        m = _calc(_d(欠税公告=1, 税收违法=1, 行政处罚=1))
        assert m["recovery_probability"] == pytest.approx(
            RECOVERY_BASE_SCORE - PENALTY_MINOR_CAP)

    def test_delta_is_reported_for_audit(self):
        """界面要能说清「78 是怎么变成 66 的」，所以加减总量必须留档"""
        m = _calc(_d(经营异常=1))
        assert m["recovery_delta"] == pytest.approx(-PENALTY_BUSINESS_EXCEPTION)


# ============================================================
# 3. 一票否决
# ============================================================

class TestVeto:
    def test_deregistered_entity_is_zero_regardless_of_bonuses(self):
        b = {"上市信息": {"_count": 1}, "财务数据": {"_items": [{"营收": 1}]}}
        m = _calc(_d(注销记录=1), stage_b=b)
        assert m["recovery_probability"] == 0.0
        assert "主体存续异常" in m["red_flags"][0]

    def test_veto_wins_over_clean_everything_else(self):
        m = _stage_h_calc_metrics(dict(EMPTY_B), {}, _d(破产重整=1), {}, {}, dict(EMPTY_G))
        assert m["recovery_probability"] == 0.0


# ============================================================
# 4. 只影响周期、不影响分数的项
# ============================================================

class TestTimeOnlyHits:
    def test_court_filing_lengthens_the_cycle_only(self):
        """
        法院立案曾是最刺眼的一例：明细里写着「法院立案 1 条」，
        下面却说「未命中任何迹象」——因为它压根不参与计算。
        现在它进周期预估，并被显式记录，界面才说得通。
        """
        m = _calc(_d(法院立案=1))
        assert m["recovery_probability"] == RECOVERY_BASE_SCORE
        assert m["time_extra_months"] == 1
        assert m["time_only_hits"] == ["法院立案1条"]

    def test_documents_are_capped(self):
        """裁判文书数量可能很大，周期延量必须封顶，否则几十件能加出几十年"""
        m = _calc(_d(裁判文书=50))
        assert m["time_extra_months"] == 6


# ============================================================
# 5. 档位（概率型指标不能用决策分的 62/78）
# ============================================================

class TestTiers:
    def test_tier_bands_are_ordered_and_straddle_the_base(self):
        assert RECOVERY_TIER_WEAK < RECOVERY_TIER_OK <= RECOVERY_BASE_SCORE

    def test_each_band_is_reachable(self):
        ok = _calc(_d())["recovery_tier"]
        neutral = _calc(_d(经营异常=1))["recovery_tier"]
        weak = _calc(_d(失信信息=1))["recovery_tier"]
        assert (ok, neutral, weak) == ("ok", "neutral", "weak"), \
            "三档必须都能取到，否则界面上会有一档永远不出现"


# ============================================================
# 6. mock 与真实同口径
# ============================================================

class TestMockMatchesRealRule:
    def test_mock_recovery_is_the_base_plus_its_own_green_flags(self):
        """
        mock 的回款分必须能由同一张规则表算出来。
        它写着「有公开财务数据」却给一个对不上的数，演示时等于两套规则在打架。
        """
        from core.mock import mock_defendant_profile
        metrics = mock_defendant_profile()["metrics"]
        assert metrics["recovery_probability"] == pytest.approx(
            RECOVERY_BASE_SCORE + BONUS_FINANCIAL)
        assert metrics["recovery_base"] == RECOVERY_BASE_SCORE

    def test_mock_tier_agrees_with_its_score(self):
        from core.mock import mock_defendant_profile
        m = mock_defendant_profile()["metrics"]
        assert m["recovery_tier"] == (
            "ok" if m["recovery_probability"] >= RECOVERY_TIER_OK else "neutral")

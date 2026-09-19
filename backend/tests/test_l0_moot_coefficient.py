"""
L0 对抗修正系数的推导

此前系数由法官 LLM 直接给一个数，10 项子分只展示不参与，既拿不出算式、
也会与法官自己的评分打架。改为推导后要钉住四件事：
1. 公式与降级链（derived / model / fallback）分别走得通
2. 越界与极端值不会漏出去（夹取 + 裁剪标记）
3. 真实 prompt 与 mock 数据的子分键名必须完全对齐——
   键名一旦漂移，推导会静默降级为 1.00，界面上表现为「模拟法庭跑完了但没修正」，
   极难排查（2026-09-19 修系数推导时实测：mock 用的是 3 项旧键名，全部对不上）
4. mock 必须走同一条推导路径，不能读现成的 correction_coefficient
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("USE_MOCK", "True")

from core.config import SUPPORTED_CAUSE_TYPES
from core.moot_court.coefficient import (
    DEFENDANT_KEYS, PLAINTIFF_KEYS, derive_coefficient)


def _scores(keys, value):
    return {k: value for k in keys}


def _judge(p=60, d=60, model=None):
    j = {"plaintiff_scores": _scores(PLAINTIFF_KEYS, p),
         "defendant_scores": _scores(DEFENDANT_KEYS, d)}
    if model is not None:
        j["correction_coefficient"] = model
    return j


# ============================================================
# 1. 公式与取值链
# ============================================================

class TestDerivation:
    def test_equal_strength_means_no_correction(self):
        """双方势均力敌 → 1.00，不修正"""
        assert derive_coefficient(_judge(60, 60))["coefficient"] == 1.0

    def test_stronger_defense_pulls_it_below_one(self):
        """
        被告更强 → 系数 < 1。

        期望值 0.85 来自 (75+10)/(90+10)，不是朴素的 75/90=0.83：
        EPS 对两侧各加 10 起阻尼作用，让低分区的比值不被放大
        （没有 EPS 时「原告 10 / 被告 5」会被算成 2.0，直接撞上上限）。
        选 75/90 这组数是为了让带阻尼的结果正好落在 0.85 这个整数位上，
        于是期望值可以写成字面量——将来 EPS 或 GAMMA 被改动时这里会立刻变红，
        而不是照着实现再算一遍、永远绿。
        """
        detail = derive_coefficient(_judge(75, 90))
        assert detail["source"] == "derived"
        assert detail["coefficient"] == pytest.approx(0.85, abs=0.01)

    def test_weak_defense_pushes_it_above_one(self):
        detail = derive_coefficient(_judge(85, 70))
        assert detail["coefficient"] > 1.0
        assert detail["source"] == "derived"

    def test_model_self_rating_does_not_override_the_derivation(self):
        """
        自评不参与取值：子分说 0.85，自评说 1.20，最终必须按 0.85 走。

        这正是老问题的根源——模型凭直觉给的数与它自己打的分数不一致时，
        以前是直觉赢，现在是分数赢。
        """
        detail = derive_coefficient(_judge(75, 90, model=1.20))
        assert detail["coefficient"] == pytest.approx(0.85, abs=0.01)
        assert detail["model_value"] == 1.20  # 仍留档备查


# ============================================================
# 2. 降级链
# ============================================================

class TestFallbackChain:
    def test_missing_subscores_fall_back_to_model_rating(self):
        j = {"correction_coefficient": 0.88}
        detail = derive_coefficient(j)
        assert detail["source"] == "model"
        assert detail["coefficient"] == 0.88

    def test_model_rating_is_still_clamped(self):
        detail = derive_coefficient({"correction_coefficient": 0.30})
        assert detail["source"] == "model"
        assert detail["coefficient"] == 0.70
        assert detail["clamped"] is True

    def test_nothing_available_means_no_correction(self):
        """既无子分也无自评 → 1.00，绝不静默编造一个修正"""
        detail = derive_coefficient({})
        assert detail["source"] == "fallback"
        assert detail["coefficient"] == 1.0
        assert "不修正" in detail["formula"]

    def test_non_dict_payload_does_not_crash(self):
        assert derive_coefficient(None)["coefficient"] == 1.0


# ============================================================
# 3. 异常输入
# ============================================================

class TestAbnormalInput:
    def test_out_of_range_subscores_are_clipped_not_dropped(self):
        """模型偶尔给 105：夹取到 100，而不是把这一项丢掉"""
        j = _judge(60, 60)
        j["plaintiff_scores"]["rights"] = 105
        detail = derive_coefficient(j)
        assert detail["coefficient"] > 1.0
        assert detail["source"] == "derived"

    def test_zero_defense_does_not_blow_up(self):
        """D=0 时靠 EPS 阻尼，不能除零，也不能直接撞上限"""
        detail = derive_coefficient(_judge(50, 0))
        assert detail["coefficient"] == 1.30
        assert detail["clamped"] is True

    def test_extreme_ratio_is_clamped_and_flagged(self):
        detail = derive_coefficient(_judge(10, 100))
        assert detail["coefficient"] == 0.70
        assert detail["clamped"] is True

    def test_partial_subscores_still_derive(self):
        """只有 3 项有效分也能算——按已给的项加权，不必凑齐 10 项"""
        j = {"plaintiff_scores": {"rights": 70, "evidence": 50},
             "defendant_scores": {"legal_defense": 80}}
        detail = derive_coefficient(j)
        assert detail["source"] == "derived"
        assert detail["coefficient"] < 1.0


# ============================================================
# 4. mock 与真实必须同构（键名漂移的直接防线）
# ============================================================

class TestMockMatchesRealContract:
    def test_mock_subscores_use_the_same_keys_as_the_prompt(self):
        """
        mock 的键名必须与 coefficient.PLAINTIFF_KEYS / DEFENDANT_KEYS 完全一致。
        对不上时推导取不到值 → 静默降级为 1.00，演示时表现为「庭审跑完了但没修正」。
        """
        from core.mock import mock_judge_result
        for cause in SUPPORTED_CAUSE_TYPES:
            j = mock_judge_result(cause)
            assert set(j["plaintiff_scores"]) == set(PLAINTIFF_KEYS), \
                f"{cause} 的原告评分键名与推导模块不一致"
            assert set(j["defendant_scores"]) == set(DEFENDANT_KEYS), \
                f"{cause} 的被告评分键名与推导模块不一致"

    def test_mock_derives_instead_of_reading_a_hardcoded_number(self):
        """
        mock 也要走推导：直接读 correction_coefficient 等于 mock 与真实两套逻辑，
        子分与系数脱节的问题在 mock 下永远不会暴露。
        """
        from core.mock import mock_judge_result
        coeffs = []
        for cause in SUPPORTED_CAUSE_TYPES:
            detail = derive_coefficient(mock_judge_result(cause))
            assert detail["source"] == "derived", \
                f"{cause} 未能从子分推导，说明 mock 数据缺项"
            coeffs.append(detail["coefficient"])
        assert len(set(coeffs)) == 3, f"三案由的推导系数应各不相同，实际：{coeffs}"

    def test_defense_strength_agrees_with_the_subscores(self):
        """
        defense_strength 与被告五项均值不能打架——界面上同时显示这两个数，
        出现「各项都 85、整体强度 60」会直接毁掉可信度。
        """
        from core.mock import mock_judge_result
        for cause in SUPPORTED_CAUSE_TYPES:
            j = mock_judge_result(cause)
            mean = sum(j["defendant_scores"].values()) / len(j["defendant_scores"])
            assert abs(j["defense_strength"] - mean) <= 5, \
                f"{cause}：整体抗辩强度 {j['defense_strength']} 与子分均值 {mean:.0f} 不符"

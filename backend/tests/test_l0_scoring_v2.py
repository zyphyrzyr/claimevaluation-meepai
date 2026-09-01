"""
L0 · v2 评分校准验收基准（分层幂平均 p=-0.5 + 阈值 78/62）

对应文档：`v4-评分模型校准方案-v2-定稿.md` 第四节 + `v4-评分模型校准方案-v2-复核意见.md`

作用：把同伴定稿的验收表固化成回归门禁。改动 POWER_MEAN_P 或阈值时，
本文件的失败会直接告诉你「验收基准不再成立」。

注意：4.4 的期望值比文档低 0.1（如优质案件文档 85.0、实测 84.9），
原因是三个聚合函数各自 round(x, 1)（沿用原有契约，保证界面上
「法律可行性 + 业务预期」能手工复算出最终分）。0.1 的偏差无实际影响。

运行：backend/ 目录下 pytest tests/test_l0_scoring_v2.py -v
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("USE_MOCK", "True")

from core import scoring, report_generator
from core.config import (POWER_MEAN_P, SCORE_THRESHOLD_GO,
                         SCORE_THRESHOLD_PATCH, QUADRANT_AXIS_MID)

GO = SCORE_THRESHOLD_GO
HOLD = SCORE_THRESHOLD_PATCH


def final(r, i, pr, d, rec, c=1.0):
    """走真实代码路径：法律三维 -> 业务两维 -> 总分"""
    legal = scoring.calculate_legal_feasibility(r, i, pr, c)
    biz = scoring.calculate_business_expectation("要钱", damages_scale=d,
                                                 recovery_ability=rec)
    return scoring.calculate_overall_score(legal, biz)


def band(score):
    if score >= GO:
        return "优先启动"
    if score >= HOLD:
        return "补短板"
    return "暂缓"


# ============================================================
# 4.0 配置前提
# ============================================================

class TestCalibrationConfig:
    def test_power_mean_p_is_negative(self):
        """一票否决只在 p <= 0 时成立，p 变正数会静默失去否决语义"""
        assert POWER_MEAN_P <= 0, "p > 0 会破坏一票否决"

    def test_thresholds_are_ordered(self):
        assert GO > HOLD

    def test_quadrant_mid_aligned_with_go(self):
        """
        四象限中线必须与 GO 对齐。
        幂平均恒有 min(x,y) <= M_p(x,y) <= max(x,y)，故两轴均 >= GO 时
        总分必 >= GO —— 这从数学上杜绝了「四象限说强推起诉、决策卡片说补短板」。
        """
        assert QUADRANT_AXIS_MID == GO


# ============================================================
# 4.1 自洽性：各维度同分 -> 最终同分
# ============================================================

class TestSelfConsistency:
    @pytest.mark.parametrize("v", [40, 50, 60, 70, 80, 90, 95])
    def test_all_dims_equal_yields_that_score(self, v):
        """M_p(v,...,v) = v —— 这是幂平均区别于连乘的核心性质"""
        assert final(v, v, v, v, v) == pytest.approx(float(v), abs=0.05)


# ============================================================
# 4.2 一票否决
# ============================================================

class TestVeto:
    @pytest.mark.parametrize("idx,name", [
        (0, "权利基础"), (1, "侵权认定"), (2, "诉讼程序"),
        (3, "判赔规模"), (4, "回款能力"),
    ])
    def test_any_zero_dimension_zeroes_total(self, idx, name):
        dims = [90, 90, 90, 90, 90]
        dims[idx] = 0
        assert final(*dims) == 0.0, f"{name} 归零未触发一票否决"

    def test_empty_shell_company_zeroes_total(self):
        """被告是空壳公司（回款能力 0）时，其余四维再高也应归零"""
        assert final(95, 95, 95, 95, 0) == 0.0

    def test_veto_survives_correction_coefficient(self):
        """修正系数不应救回被否决的分数"""
        assert final(90, 90, 90, 90, 0, c=1.3) == 0.0

    def test_power_mean_short_circuits_on_zero(self):
        """p<0 时 0^p 发散，必须短路，不能返回 inf/nan"""
        result = scoring.power_mean([0.0, 90.0, 90.0])
        assert result == 0.0
        assert result == result, "不得返回 NaN"


# ============================================================
# 4.3 短板敏感度
# ============================================================

class TestShortBoardSensitivity:
    @pytest.mark.parametrize("recovery,expected", [
        (90, 90.0), (70, 84.3), (50, 76.4), (30, 64.3), (10, 40.0), (0, 0.0),
    ])
    def test_recovery_decay_curve(self, recovery, expected):
        assert final(90, 90, 90, 90, recovery) == pytest.approx(expected, abs=0.05)

    def test_monotonically_decreasing(self):
        scores = [final(90, 90, 90, 90, r) for r in range(95, -1, -5)]
        assert all(a >= b for a, b in zip(scores, scores[1:])), "回款降低时总分不得回升"

    def test_below_arithmetic_mean(self):
        """短板惩罚：结果必须低于算术平均"""
        result = final(90, 90, 90, 90, 30)
        assert result < (90 + 90 + 90 + 90 + 30) / 5


# ============================================================
# 4.4 直觉案例
# ============================================================

class TestIntuitiveCases:
    @pytest.mark.parametrize("name,dims,expected_score,expected_band", [
        ("完美案件",     (95, 95, 95, 95, 95), 95.0, "优先启动"),
        ("优质案件",     (88, 85, 90, 80, 85), 84.9, "优先启动"),
        ("平庸案件",     (65, 65, 65, 65, 65), 65.0, "补短板"),
        ("赢了拿不到钱", (90, 88, 90, 85, 15), 47.9, "暂缓"),
        ("时效临期",     (85, 85, 55, 80, 80), 76.2, "补短板"),
        ("证据有短板",   (85, 60, 80, 75, 80), 75.5, "补短板"),
        ("权利有瑕疵",   (35, 85, 85, 80, 85), 70.1, "补短板"),
    ])
    def test_case(self, name, dims, expected_score, expected_band):
        score = final(*dims)
        assert score == pytest.approx(expected_score, abs=0.05), f"{name} 分数不符"
        assert band(score) == expected_band, f"{name} 档位不符"

    def test_medium_short_board_is_not_priority(self):
        """
        定稿的核心诉求：单一维度掉到 55-60、其余 80+ 的案子，
        必须判「补短板」而不是「优先启动」（旧方案 p=0 + 阈值 75 会误判）。
        """
        assert band(final(85, 85, 55, 80, 80)) == "补短板"
        assert band(final(85, 60, 80, 75, 80)) == "补短板"

    def test_quality_case_still_priority(self):
        """提高阈值不能误伤真正的优质案件"""
        assert band(final(88, 85, 90, 80, 85)) == "优先启动"


# ============================================================
# 4.5 四象限一致性（阈值上调后不得同屏矛盾）
# ============================================================

class TestQuadrantConsistency:
    @pytest.mark.parametrize("legal,biz", [
        (78, 78), (80, 80), (85, 90), (78, 95), (95, 78),
    ])
    def test_dual_strong_implies_priority(self, legal, biz):
        """四象限判「双优区（强推起诉）」时，决策建议必须是「优先启动」"""
        q = report_generator.quadrant(legal, biz)
        score = scoring.calculate_overall_score(legal, biz)
        if q == "双优区（强推起诉）":
            assert score >= GO, (
                f"四象限说双优强推，但总分 {score} < {GO}，同屏矛盾")

    def test_no_conflict_across_grid(self):
        """网格扫描：确认不存在任何「双优区」却非「优先启动」的组合"""
        conflicts = []
        for legal in range(40, 101, 5):
            for biz in range(40, 101, 5):
                q = report_generator.quadrant(legal, biz)
                score = scoring.calculate_overall_score(legal, biz)
                if q == "双优区（强推起诉）" and score < GO:
                    conflicts.append((legal, biz, score))
        assert not conflicts, f"存在同屏矛盾组合：{conflicts[:5]}"


# ============================================================
# 4.6 输入边界（复核意见第四节：normalize 不得丢失）
# ============================================================

class TestInputBounds:
    def test_over_100_is_clamped_not_amplified(self):
        """LLM 溢出 120 分必须被夹到 100，不得放大总分"""
        assert scoring.calculate_legal_feasibility(120, 80, 80) <= \
            scoring.calculate_legal_feasibility(100, 80, 80)

    def test_negative_input_zeroes(self):
        assert scoring.calculate_legal_feasibility(-10, 50, 50) == 0.0

    def test_nan_collapses_to_zero_not_propagate(self):
        """NaN 必须归零，不得传播成 NaN 总分（界面会把 NaN 显示成空白）"""
        result = scoring.calculate_legal_feasibility(float("nan"), 80, 80)
        assert result == 0.0
        assert result == result, "不得返回 NaN"

    def test_power_mean_of_empty_is_zero(self):
        assert scoring.power_mean([]) == 0.0

    def test_power_mean_p_zero_is_geometric(self):
        """p=0 时退化为几何平均"""
        assert scoring.power_mean([60.0, 60.0], p=0) == pytest.approx(60.0, abs=1e-9)
        assert scoring.power_mean([40.0, 90.0], p=0) == pytest.approx(60.0, abs=1e-6)

    def test_power_mean_p_one_is_arithmetic(self):
        """p=1 时退化为算术平均（不惩罚短板）"""
        assert scoring.power_mean([40.0, 90.0], p=1) == pytest.approx(65.0, abs=1e-9)


# ============================================================
# 4.7 全分布（粗网格，防止阈值/p 被改坏而无人察觉）
# ============================================================

class TestDistributionSanity:
    def test_distribution_in_reasonable_range(self):
        """
        粗网格（步长 10，6^5 = 7776 组）分布体检。
        全量 248,832 组（步长 5）的基准见复核意见第五节：
            中位 64.3 / P90 75.6 / 优先启动 6.03% / 补短板 55.43% / 暂缓 38.54%
        这里只做宽松断言，防止模型被改坏而无人察觉。
        """
        vals = list(range(40, 101, 10))
        scores = [final(r, i, pr, d, rec)
                  for r in vals for i in vals for pr in vals
                  for d in vals for rec in vals]
        scores.sort()
        n = len(scores)
        median = scores[n // 2]
        go_rate = sum(1 for s in scores if s >= GO) / n
        hold_rate = sum(1 for s in scores if s >= HOLD) / n

        assert 55.0 <= median <= 75.0, f"中位数跑出常识区间：{median}"
        assert 0.02 <= go_rate <= 0.25, f"优先启动占比异常：{go_rate:.2%}"
        assert scores[-1] == pytest.approx(100.0, abs=0.05), "全满分时应为 100"
        assert go_rate < hold_rate, "优先启动必须严于补短板"

    def test_quality_cases_are_priority(self):
        """
        复核意见的关键论据：6% 的整体占比不代表模型过严。
        五维均 >= 75 的案子应有绝大多数被判优先启动。
        """
        vals = [75, 80, 85, 90, 95]
        scores = [final(r, i, pr, d, rec)
                  for r in vals for i in vals for pr in vals
                  for d in vals for rec in vals]
        go_rate = sum(1 for s in scores if s >= GO) / len(scores)
        assert go_rate >= 0.95, f"五维均 >=75 的优质案件优先启动率过低：{go_rate:.1%}"

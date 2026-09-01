"""
L0 三案由区分度（评测报告 P1-4）

曾经的缺陷：mock 数据与模拟法庭 prompt 都只有一套商标内容，
案由参数一路被忽略 →
  - 三个案由跑出完全相同的分数（要钱一律同分、要名一律同分）
  - 选「著作权侵权」做独立演练，庭审却讲「第25类服装上的注册商标」（演示事故）

本文件把「案由必须真正生效」钉成回归门禁，覆盖两条链路：
  A. Mock 数据层：三案由的评估分 / 证据完备度 / 庭审剧本 / 修正系数各不相同
  B. 真实 Prompt 层：三案由的案由画像注入到原、被、审三方的系统提示词与用户提示词
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("USE_MOCK", "True")

from core import moot_service
from core.config import (CAUSE_TRADEMARK, CAUSE_COPYRIGHT,
                         CAUSE_UNFAIR_COMPETITION, SUPPORTED_CAUSE_TYPES)
from core.moot_court import prompts
from core.moot_court.agents import PlaintiffAgent, DefendantAgent, JudgeAgent
from core.moot_court.procedure import MootCourtProcedure

CAUSES = [CAUSE_TRADEMARK, CAUSE_COPYRIGHT, CAUSE_UNFAIR_COMPETITION]

# 各案由的「身份关键词」：出现在自己案由的内容里，不应出现在别的案由里
CAUSE_SIGNATURE = {
    CAUSE_TRADEMARK: ["商标", "《商标法》"],
    CAUSE_COPYRIGHT: ["作品", "著作权"],
    CAUSE_UNFAIR_COMPETITION: ["装潢", "反不正当竞争"],
}

# 各案由的「排他关键词」：出现在别的案由里即为串行污染
CAUSE_FORBIDDEN = {
    CAUSE_TRADEMARK: ["著作权", "美术作品", "反不正当竞争"],
    CAUSE_COPYRIGHT: ["注册商标", "《商标法》", "反不正当竞争"],
    CAUSE_UNFAIR_COMPETITION: ["注册商标", "《商标法》", "美术作品"],
}


# ============================================================
# A. Mock 数据层
# ============================================================

class TestMockDiffersByCause:

    def test_evaluation_scores_differ(self):
        from core.mock import (mock_rights, mock_infringement,
                               mock_damages, mock_precedent)
        sigs = {}
        for c in CAUSES:
            sigs[c] = (mock_rights(c)["score"],
                       mock_infringement(c)["score"],
                       mock_damages(c)["score"],
                       mock_precedent(c)["score"])
        assert len(set(sigs.values())) == 3, (
            f"三案由应有三组不同的评估分，实际：{sigs}")

    def test_evidence_completeness_differs(self):
        from core.mock import mock_evidence_review
        vals = [mock_evidence_review(c)["completeness"] for c in CAUSES]
        assert len(set(vals)) == 3, f"三案由证据完备度应各不相同，实际：{vals}"

    def test_judge_coefficient_and_defense_strength_differ(self):
        from core.mock import mock_judge_result
        sigs = {c: (mock_judge_result(c)["correction_coefficient"],
                    mock_judge_result(c)["defense_strength"]) for c in CAUSES}
        assert len(set(sigs.values())) == 3, (
            f"三案由的修正系数/抗辩强度应各不相同，实际：{sigs}")

    def test_moot_transcript_matches_selected_cause(self):
        """选了哪个案由，庭审内容就必须是那个案由——不得出现其他案由的术语"""
        from core.mock import mock_moot_rounds
        for cause in CAUSES:
            text = "".join(r["content"] for r in mock_moot_rounds(cause))
            for kw in CAUSE_SIGNATURE[cause]:
                assert kw in text, f"{cause} 的庭审内容缺少其标志性术语「{kw}」"
            for kw in CAUSE_FORBIDDEN[cause]:
                assert kw not in text, (
                    f"【案由串行】{cause} 的庭审里出现了「{kw}」，"
                    f"说明 cause_type 没有真正生效")

    def test_unknown_cause_raises_instead_of_falling_back(self):
        """
        未知案由必须显式报错。静默回落商标的危害：用户填「专利侵权」，
        系统拿商标清单和商标剧本输出一份看起来正常、实际完全跑偏的演示。
        """
        from core.mock import mock_rights, mock_moot_rounds, mock_judge_result
        for fn in (mock_rights, mock_judge_result):
            with pytest.raises(ValueError, match="不支持的案由"):
                fn("专利侵权")
        with pytest.raises(ValueError, match="不支持的案由"):
            mock_moot_rounds("商业秘密")


# ============================================================
# B. 真实 Prompt 层：案由画像
# ============================================================

class TestCauseProfiles:

    def test_profile_exists_for_every_supported_cause(self):
        for c in SUPPORTED_CAUSE_TYPES:
            p = prompts.get_cause_profile(c)
            for key in ("label", "plaintiff", "defendant", "judge",
                        "opening_guide", "defense_guide", "evidence_guide"):
                assert p.get(key), f"{c} 的画像缺少字段 {key}"

    def test_profiles_are_distinct_per_role(self):
        for role in ("plaintiff", "defendant", "judge"):
            texts = [prompts.get_cause_profile(c)[role] for c in CAUSES]
            assert len(set(texts)) == 3, f"{role} 的三案由画像完全相同"

    def test_unknown_cause_raises(self):
        with pytest.raises(ValueError, match="不支持的案由"):
            prompts.get_cause_profile("专利侵权")

    def test_base_system_prompts_are_cause_neutral(self):
        """
        角色系统提示词的公共部分不得硬编码任一案由——案由相关内容一律走画像注入。
        否则「著作权案由 + 商标人设」会在同一段提示词里自相矛盾。
        """
        for name, text in (("PLAINTIFF_SYSTEM", prompts.PLAINTIFF_SYSTEM),
                           ("DEFENDANT_SYSTEM", prompts.DEFENDANT_SYSTEM),
                           ("JUDGE_SYSTEM", prompts.JUDGE_SYSTEM)):
            for kw in ("商标", "著作权", "不正当竞争"):
                assert kw not in text, (
                    f"{name} 公共部分硬编码了案由词「{kw}」，应移到 CAUSE_PROFILES")

    def test_built_system_prompt_carries_cause_law(self):
        expected_law = {
            CAUSE_TRADEMARK: "商标法",
            CAUSE_COPYRIGHT: "著作权法",
            CAUSE_UNFAIR_COMPETITION: "反不正当竞争法",
        }
        for cause, law in expected_law.items():
            for role in ("plaintiff", "defendant", "judge"):
                sp = prompts.build_system_prompt(role, cause)
                assert law in sp, f"{cause}/{role} 的系统提示词未包含「{law}」"
                assert "本案案由" in sp

    def test_built_system_prompt_has_no_foreign_law(self):
        """著作权案由的系统提示词里不该出现《商标法》"""
        sp = prompts.build_system_prompt("plaintiff", CAUSE_COPYRIGHT)
        assert "《商标法》" not in sp
        assert "美术作品" in sp or "独创性" in sp

    def test_user_templates_require_cause_guide(self):
        """
        四个用户提示词模板都含 {cause_guide} 占位符。
        少了它，agents.py 的 .format(cause_guide=...) 会 KeyError；
        这条测试是在钉「模板与调用方必须同步改」。
        """
        for name in ("OPENING_STATEMENT_USER", "DEFENSE_RESPONSE_USER",
                     "EVIDENCE_PLAINTIFF_USER", "JUDGE_SUMMARY_USER"):
            assert "{cause_guide}" in getattr(prompts, name), (
                f"{name} 缺少 {{cause_guide}} 占位符")


class TestAgentsAndProcedureCarryCause:

    @pytest.mark.parametrize("cause", CAUSES)
    def test_three_agents_share_the_same_cause(self, cause):
        proc = MootCourtProcedure(case_description="测试案情", cause_type=cause)
        for agent in (proc.plaintiff, proc.defendant, proc.judge):
            assert agent.cause_type == cause
            assert agent.profile is prompts.get_cause_profile(cause)

    @pytest.mark.parametrize("cause", CAUSES)
    def test_agent_system_prompt_is_cause_specific(self, cause):
        assert prompts.get_cause_profile(cause)["label"] in PlaintiffAgent(cause).system_prompt
        assert prompts.get_cause_profile(cause)["label"] in DefendantAgent(cause).system_prompt
        assert prompts.get_cause_profile(cause)["label"] in JudgeAgent(cause).system_prompt

    def test_default_cause_stays_trademark(self):
        """不传案由时仍是商标，保证既有调用方行为不变"""
        assert MootCourtProcedure(case_description="x").cause_type == CAUSE_TRADEMARK

    def test_unknown_cause_rejected_at_construction(self):
        with pytest.raises(ValueError):
            MootCourtProcedure(case_description="x", cause_type="专利侵权")


# ============================================================
# C. 端到端：独立演练按选中案由出内容
# ============================================================

class TestStandaloneMootUsesCause:

    def _run(self, monkeypatch, cause):
        monkeypatch.setattr("time.sleep", lambda *_: None)
        gen = moot_service.run_standalone("测试案情描述", cause_type=cause)
        rounds, final = [], None
        while True:
            try:
                ev = next(gen)
            except StopIteration:
                break
            if ev.get("event") == "round":
                rounds.append(ev)
            if ev.get("event") == "moot_finished":
                final = ev
        return rounds, final

    @pytest.mark.parametrize("cause", CAUSES)
    def test_transcript_matches_cause(self, monkeypatch, cause):
        rounds, final = self._run(monkeypatch, cause)
        assert rounds, "独立演练未产出任何庭审发言"
        text = "".join(r["content"] for r in rounds)
        for kw in CAUSE_SIGNATURE[cause]:
            assert kw in text, f"选了 {cause}，庭审内容却没有「{kw}」"
        for kw in CAUSE_FORBIDDEN[cause]:
            assert kw not in text, f"选了 {cause}，庭审却出现「{kw}」——案由未生效"

    def test_three_causes_produce_three_different_transcripts(self, monkeypatch):
        texts = {"".join(r["content"] for r in self._run(monkeypatch, c)[0])
                 for c in CAUSES}
        assert len(texts) == 3, "三案由跑出了相同的庭审记录"

    def test_correction_coefficient_differs_by_cause(self, monkeypatch):
        coeffs = [self._run(monkeypatch, c)[1]["correction_coefficient"]
                  for c in CAUSES]
        assert len(set(coeffs)) == 3, f"三案由修正系数应各不相同，实际：{coeffs}"

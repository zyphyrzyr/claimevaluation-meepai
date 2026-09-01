"""
L0 备忘录章节渲染

备忘录 markdown 是 Word 导出的**唯一内容源**，所以它漏掉的东西，
用户在界面上看得到、导出成 Word 就凭空消失了——而且不会有任何报错。

本文件盯两件事：
1. 各维度算出来的结论都要进正文（尤其是判例价值，它此前整节缺失）
2. 章节号连续（模拟法庭、判例价值、法宝都是条件渲染，硬编码编号会断号）
"""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("USE_MOCK", "True")

from core import report_generator
from core.case_context import CaseContext
from core.config import CAUSE_COPYRIGHT, CAUSE_TRADEMARK
from core.orchestrator import Orchestrator

_CASES = {}


@pytest.fixture(autouse=True)
def _clear_memo_cache():
    """
    每个用例前清空缓存。

    否则 monkeypatch 了法宝接口之后，取到的还是**改之前**生成的那份备忘录，
    表现为「补丁打上了但章节没出现」——缓存引起的假失败很难查。
    """
    _CASES.clear()


def _memo(cause_type=CAUSE_TRADEMARK, goal_type="要钱", pkulaw=False, **ctx_kwargs):
    """跑完 mock 全链路后产出备忘录；按入参缓存，避免重复跑"""
    key = (cause_type, goal_type, pkulaw, tuple(sorted(ctx_kwargs.items())))
    if key not in _CASES:
        kwargs = dict(
            case_id=f"memo-{len(_CASES)}",
            case_description="原告持有第1234567号注册商标（第25类服装），"
                             "被告在天猫店铺销售近似标识卫衣，已公证取证。",
            cause_type=cause_type,
            goal_type=goal_type,
        )
        kwargs.update(ctx_kwargs)
        ctx = CaseContext(**kwargs)
        Orchestrator(ctx).run_all()
        _CASES[key] = (ctx, report_generator.generate_memo(
            "测试案", ctx, pkulaw=pkulaw))
    return _CASES[key]


def _md(cause_type=CAUSE_TRADEMARK, goal_type="要钱", **kw) -> str:
    return _memo(cause_type, goal_type, **kw)[1]["markdown"]


def _headings(md: str):
    return [l.lstrip("# ").strip() for l in md.split("\n") if l.startswith("## ")]


# ============================================================
# 1. 判例价值章节（要名路径此前整节缺失）
# ============================================================

class TestPrecedentSection:
    def test_fame_path_renders_the_precedent_section(self):
        """
        要名路径的业务预期就是判例价值。这一节此前根本没渲染——
        界面上看得到，导出 Word 就消失，而业务预期是二维模型的一半。

        断言章节标题而非「判例价值」字样：一页纸摘要里有「（判例价值维度）」，
        只搜字样的话，章节整节缺失也照样能通过。
        """
        md = _md(CAUSE_TRADEMARK, "要名")
        assert "### 判例价值" in md, "要名路径的备忘录缺少判例价值章节"

    def test_precedent_section_carries_the_numbers_and_analysis(self):
        ctx, memo = _memo(CAUSE_TRADEMARK, "要名")
        precedent = memo["dimensions"]["precedent"]
        assert precedent, "要名路径没算出判例价值"
        md = memo["markdown"]
        assert f"判例价值（{precedent['score']} 分）" in md
        assert str(precedent["first_case_index"]) in md
        assert precedent["influence_level"] in md
        assert precedent["analysis"] in md, "判例价值的分析文本没进正文"

    def test_money_path_has_no_precedent_section(self):
        """要钱路径不判例价值，不该凭空出现一节"""
        md = _md(CAUSE_TRADEMARK, "要钱")
        assert "### 判例价值" not in md


# ============================================================
# 2. 各维度的分析文本都要进正文
# ============================================================

class TestAnalysisRendering:
    """
    模型的分析文字是结论的**理由**。只渲染分数和列表项、丢掉 analysis，
    等于给用户一堆没有依据的数字——判赔节点专门被要求写明「走的是哪个
    计算顺位」，那段话就写在 analysis 里。
    """

    def test_rights_analysis(self):
        ctx, memo = _memo()
        assert memo["dimensions"]["rights"]["analysis"] in memo["markdown"]

    def test_infringement_analysis_alongside_its_elements(self):
        ctx, memo = _memo()
        infr = memo["dimensions"]["infringement"]
        md = memo["markdown"]
        assert infr["analysis"] in md
        for el in infr.get("elements", []):
            assert el["name"] in md, "侵权要件认定结果没进正文"

    def test_procedure_analysis_alongside_its_risks(self):
        ctx, memo = _memo()
        proc = memo["dimensions"]["procedure"]
        md = memo["markdown"]
        assert proc["analysis"] in md
        for r in proc.get("risks", []):
            assert r["item"] in md, "程序风险项没进正文"

    def test_damages_analysis_is_the_place_where_the_calc_path_is_explained(self):
        ctx, memo = _memo()
        d = memo["dimensions"]["damages"]
        md = memo["markdown"]
        assert d["analysis"] in md
        assert str(d["p50"]) in md
        assert d["scale_support"] in md


# ============================================================
# 3. 章节号连续，不留断号
# ============================================================

class TestSectionNumbering:
    @staticmethod
    def _numbers(md: str):
        """取出各章节的中文序号"""
        out = []
        for h in _headings(md):
            m = re.match(r"^([一二三四五六七八九十])、", h)
            assert m, f"章节标题缺少序号：{h}"
            out.append(m.group(1))
        return out

    def test_no_gap_without_moot_court(self):
        """
        无庭审时「模拟法庭」整节跳过。若编号硬编码，会从三直接跳到五，
        看起来像漏了一章。
        """
        nums = self._numbers(_md(CAUSE_TRADEMARK, "要钱"))
        assert nums == list("一二三四"), f"章节号不连续：{nums}"

    def test_no_gap_on_the_fame_path(self):
        nums = self._numbers(_md(CAUSE_TRADEMARK, "要名"))
        expected = "一二三四五六七八九十"[: len(nums)]
        assert nums == list(expected), f"章节号不连续：{nums}"

    def test_pkulaw_section_number_follows_the_main_flow(self, monkeypatch):
        """
        法宝章节此前写死「六」。它排在条件渲染的章节之后，
        一旦前面的章节增减，就会与主流程撞号或断号。
        """
        from core.pkulaw import pkulaw_api

        monkeypatch.setattr(pkulaw_api, "_pkulaw_configured", lambda: True)
        monkeypatch.setattr(
            pkulaw_api, "search_for_rights_foundation",
            lambda cause: {"status": "ok", "laws": [
                {"title": "中华人民共和国商标法", "content": "第六十三条 …"}]})
        monkeypatch.setattr(
            pkulaw_api, "search_for_infringement",
            lambda cause: {"status": "ok", "laws": [], "cases": [
                {"title": "某某商标侵权案", "court": "某法院", "summary": "摘要"}]})
        monkeypatch.setattr(
            pkulaw_api, "run_verification_phase",
            lambda md: {"status": "ok", "_summary": "核验通过 2 条"})

        md = _md(CAUSE_TRADEMARK, "要钱", pkulaw=True)
        nums = self._numbers(md)
        assert nums == list("一二三四五"), f"法宝章节未顺延：{nums}"
        assert "中华人民共和国商标法" in md

    def test_skipped_pkulaw_does_not_consume_a_number(self, monkeypatch):
        """未配置法宝时整节跳过，也不该占掉一个章节号"""
        from core.pkulaw import pkulaw_api

        monkeypatch.setattr(pkulaw_api, "_pkulaw_configured", lambda: False)
        md = _md(CAUSE_TRADEMARK, "要钱", pkulaw=True)
        assert "法律检索与引用核验" not in md
        assert self._numbers(md) == list("一二三四")

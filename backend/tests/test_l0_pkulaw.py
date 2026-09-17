"""
L0 北大法宝集成评测：案由参数化 / 无 token 降级 / 报告增强不污染评分

该模块长期处于「代码在、主流程零调用」的悬挂状态（评测报告 P2-④）。
接主流程前必须先解决一个前提：检索查询词原本全部硬编码成商标，直接接进去会让
著作权案子检索回一堆商标法条——检索增强反成噪声注入。

本文件用 monkeypatch 拦住 _rpc_call，在没有任何 API key 的前提下验证真实代码
路径传给法宝的查询词，避免「测了个 mock」。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("USE_MOCK", "True")

from core.config import CAUSE_COPYRIGHT, CAUSE_TRADEMARK, CAUSE_UNFAIR_COMPETITION, SUPPORTED_CAUSE_TYPES
from core.pkulaw import pkulaw_api, pkulaw_integration
from core.case_context import CaseContext
from core import orchestrator, report_generator


# ============================================================
# A. 案由检索画像
# ============================================================

class TestCauseSearchProfile:
    PROFILE_FIELDS = [
        "rights_query", "rights_law_title", "rights_law_article",
        "infringement_query", "defense_query", "damages_query", "precedent_query",
    ]

    def test_all_supported_causes_have_a_profile(self):
        for c in SUPPORTED_CAUSE_TYPES:
            assert c in pkulaw_api.CAUSE_SEARCH_PROFILE, f"{c} 缺检索画像"

    def test_profile_fields_complete(self):
        for c, prof in pkulaw_api.CAUSE_SEARCH_PROFILE.items():
            for f in self.PROFILE_FIELDS:
                assert prof.get(f), f"{c} 缺字段 {f}"

    def test_query_terms_differ_across_causes(self):
        """三案由的查询词必须各不相同，否则等于没做区分"""
        for field in ["rights_query", "infringement_query", "defense_query",
                      "damages_query", "precedent_query"]:
            values = [pkulaw_api.CAUSE_SEARCH_PROFILE[c][field]
                      for c in SUPPORTED_CAUSE_TYPES]
            assert len(set(values)) == len(values), f"{field} 在三案由下取值重复：{values}"

    def test_core_law_differs_across_causes(self):
        """请求权基础法条必须随案由切换（商标法/著作权法/反不正当竞争法）"""
        titles = [pkulaw_api.CAUSE_SEARCH_PROFILE[c]["rights_law_title"]
                  for c in SUPPORTED_CAUSE_TYPES]
        assert len(set(titles)) == 3, f"核心法条未随案由切换：{titles}"

    def test_unknown_cause_raises(self):
        for bogus in ["专利侵权", "", "商标", None]:
            with pytest.raises(ValueError, match="不支持的案由"):
                pkulaw_api._profile(bogus)

    def test_no_trademark_terms_leak_into_other_causes(self):
        """反面钉死：非商标案由的检索词里不得出现商标专属术语"""
        for c in (CAUSE_COPYRIGHT, CAUSE_UNFAIR_COMPETITION):
            prof = pkulaw_api.CAUSE_SEARCH_PROFILE[c]
            for field in ["rights_query", "infringement_query", "damages_query"]:
                assert "商标" not in prof[field], (
                    f"{c}/{field} 串入商标术语：{prof[field]}")


# ============================================================
# B. 检索函数：无 token 降级（不得发起网络请求）
# ============================================================

class TestUnconfiguredDegradesGracefully:
    """
    未配置 PKULAW_API_TOKEN 时，各检索函数必须返回 skipped 且不打网络。
    若此处发起真实 HTTP，本地开发与 CI 都会被拖慢甚至超时报错。
    """
    FUNCS = [
        (pkulaw_api.search_for_rights_foundation, ()),
        (pkulaw_api.search_for_infringement, ()),
        (pkulaw_api.search_for_procedure, ()),
        (pkulaw_api.search_for_moot_court, ()),
        (pkulaw_api.search_for_financial, ()),
        (pkulaw_api.search_for_precedent, ("案情",)),
    ]

    def test_all_search_functions_skip_without_token(self, monkeypatch):
        monkeypatch.setattr(pkulaw_api, "_pkulaw_configured", lambda: False)
        for fn, args in self.FUNCS:
            res = fn(*args)
            assert res.get("status") == "skipped", f"{fn.__name__} 无 token 时未跳过"
            assert res.get("laws") == [] and res.get("cases") == []

    def test_no_network_call_when_unconfigured(self, monkeypatch):
        def boom(*a, **kw):
            raise AssertionError("未配置 token 却发起了网络请求")

        monkeypatch.setattr(pkulaw_api, "_pkulaw_configured", lambda: False)
        monkeypatch.setattr(pkulaw_api, "_rpc_call", boom)
        for fn, args in self.FUNCS:
            fn(*args)  # 不应抛出

    def test_verification_phase_skips_without_token(self, monkeypatch):
        monkeypatch.setattr(pkulaw_api, "_pkulaw_configured", lambda: False)
        res = pkulaw_api.run_verification_phase("# 报告\n《商标法》第五十七条")
        assert res.get("status") == "skipped"


# ============================================================
# C. 检索词真的随案由切换（拦住 RPC 看实参）
# ============================================================

class TestQueriesFollowTheSelectedCause:
    """核心用例：无 key 也要能验证「传给法宝的查询词属于所选案由」"""

    @staticmethod
    def _capture(monkeypatch):
        calls = []

        def fake_rpc(tool_name, args):
            calls.append((tool_name, args))
            return {"result": {"content": [{"text": "[]"}]}}

        monkeypatch.setattr(pkulaw_api, "_pkulaw_configured", lambda: True)
        monkeypatch.setattr(pkulaw_api, "_rpc_call", fake_rpc)
        return calls

    def test_rights_search_uses_the_cause_law(self, monkeypatch):
        calls = self._capture(monkeypatch)
        pkulaw_api.search_for_rights_foundation(CAUSE_COPYRIGHT)

        texts = [a.get("text", "") for _, a in calls if "text" in a]
        articles = [a for t, a in calls if t == "get_article"]
        assert any("著作权" in t for t in texts), f"著作权案由却没检索著作权：{texts}"
        assert not any("商标" in t for t in texts), f"串入商标检索词：{texts}"
        assert articles and articles[0]["title"] == "中华人民共和国著作权法"
        assert articles[0]["number"] == "第十条"

    def test_infringement_search_differs_by_cause(self, monkeypatch):
        captured = {}
        for cause in SUPPORTED_CAUSE_TYPES:
            calls = self._capture(monkeypatch)
            pkulaw_api.search_for_infringement(cause)
            captured[cause] = [a.get("text", "") for _, a in calls if "text" in a]

        for cause, texts in captured.items():
            assert texts, f"{cause} 未发出任何检索"
        all_texts = [t for texts in captured.values() for t in texts]
        assert len(set(all_texts)) >= 3, f"三案由检索词未分化：{all_texts}"

    def test_precedent_search_appends_cause_terms(self, monkeypatch):
        calls = self._capture(monkeypatch)
        pkulaw_api.search_for_precedent("某案情描述", CAUSE_UNFAIR_COMPETITION)

        texts = [a.get("text", "") for _, a in calls if "text" in a]
        assert any("不正当竞争" in t for t in texts), f"未带上案由词：{texts}"
        assert not any("商标侵权" in t for t in texts), f"串入商标：{texts}"

    def test_moot_court_defense_query_follows_cause(self, monkeypatch):
        calls = self._capture(monkeypatch)
        pkulaw_api.search_for_moot_court(CAUSE_TRADEMARK)
        texts = [a.get("text", "") for _, a in calls if "text" in a]
        assert any("商标" in t for t in texts)
        # 商标案由下不得出现他案由的抗辩术语
        assert not any("独创性" in t for t in texts), f"商标案由串入著作权抗辩：{texts}"


# ============================================================
# D. 查询计划生成
# ============================================================

class TestQueryPlan:
    def test_plan_follows_cause(self):
        plans = {c: pkulaw_integration.generate_all_queries(
            "pk-1", c, "要钱", "案情") for c in SUPPORTED_CAUSE_TYPES}
        for c, plan in plans.items():
            assert plan["cause_type"] == c
            assert plan["goal_type"] == "要钱"

        core_queries = {
            c: plan["dimensions"]["1.1_权利基础"]["searches"][0]["query"]
            for c, plan in plans.items()
        }
        assert len(set(core_queries.values())) == 3, f"查询计划未按案由分化：{core_queries}"

    def test_plan_branches_on_goal_type(self):
        money = pkulaw_integration.generate_all_queries("pk-2", CAUSE_TRADEMARK, "要钱")
        fame = pkulaw_integration.generate_all_queries("pk-2", CAUSE_TRADEMARK, "要名")
        assert "2.1_判赔规模" in money["dimensions"]
        assert "2.1_判例价值" in fame["dimensions"]
        assert "2.1_判例价值" not in money["dimensions"]
        assert "2.1_判赔规模" not in fame["dimensions"]

    def test_procedure_section_is_cause_neutral(self):
        """程序性检索三案由通用，不应随案由变化"""
        texts = set()
        for c in SUPPORTED_CAUSE_TYPES:
            plan = pkulaw_integration.generate_all_queries("pk-3", c)
            texts.add(plan["dimensions"]["1.3_诉讼程序"]["searches"][0]["query"])
        assert len(texts) == 1, f"程序段不应随案由变化：{texts}"

    def test_every_search_declares_a_known_tool(self):
        """计划里的 tool 必须是 TOOL_ENDPOINTS 里真实存在的，否则执行时静默走默认值"""
        for c in SUPPORTED_CAUSE_TYPES:
            for goal in ("要钱", "要名"):
                plan = pkulaw_integration.generate_all_queries("pk-4", c, goal)
                for dim, body in plan["dimensions"].items():
                    for s in body.get("searches", []):
                        assert s["tool"] in pkulaw_api.TOOL_ENDPOINTS, (
                            f"{c}/{goal}/{dim} 声明了未知工具 {s['tool']}")

    def test_unknown_cause_raises(self):
        with pytest.raises(ValueError, match="不支持的案由"):
            pkulaw_integration.generate_all_queries("pk-5", "专利侵权")

    def test_unknown_goal_raises(self):
        with pytest.raises(ValueError, match="不支持的业务目标"):
            pkulaw_integration.generate_all_queries("pk-6", CAUSE_TRADEMARK, "随便")


# ============================================================
# E. 报告增强：可选、可降级、不污染评分
# ============================================================

def _scored_ctx(**overrides) -> CaseContext:
    kwargs = dict(
        case_id="pk-case",
        case_description="原告持有第1234567号注册商标（第25类服装），被告在天猫店铺销售近似标识卫衣，已公证取证。",
        cause_type=CAUSE_TRADEMARK,
        goal_type="要钱",
    )
    kwargs.update(overrides)
    ctx = CaseContext(**kwargs)
    orchestrator.Orchestrator(ctx).run_all()
    return ctx


class TestMemoEnrichment:
    def test_default_memo_has_no_pkulaw_section(self):
        memo = report_generator.generate_memo("案", _scored_ctx())
        assert "北大法宝" not in memo["markdown"]
        assert "pkulaw" not in memo

    def test_enrich_without_token_leaves_no_empty_section(self, monkeypatch):
        """未配置 token：整段跳过，报告里不该出现空壳章节"""
        monkeypatch.setattr(pkulaw_api, "_pkulaw_configured", lambda: False)
        ctx = _scored_ctx()
        memo = report_generator.generate_memo("案", ctx, pkulaw=True)

        assert memo["pkulaw"]["status"] == "skipped"
        # 断言章节标题而非序号：序号是动态编排的，写死会在加/减章节时误报
        assert "法律检索与引用核验" not in memo["markdown"]
        assert "北大法宝" not in memo["markdown"]

    def test_enrichment_never_alters_the_score(self, monkeypatch):
        """
        硬约束：北大法宝只影响报告文本，绝不参与评分。
        评分链路一旦依赖外部 API，同一份输入就可能跑出不同分——v4 的确定性可复现就没了。
        """
        monkeypatch.setattr(pkulaw_api, "_pkulaw_configured", lambda: False)
        plain = report_generator.generate_memo("案", _scored_ctx())
        rich = report_generator.generate_memo("案", _scored_ctx(), pkulaw=True)
        assert plain["scores"] == rich["scores"]
        assert plain["recommendation"] == rich["recommendation"]

    def test_enrichment_failure_degrades_to_warning(self, monkeypatch):
        """检索或核验抛异常时，降级成 warning，报告照常产出（不阻塞）"""
        def boom(*a, **kw):
            raise RuntimeError("法宝服务 500")

        monkeypatch.setattr(pkulaw_api, "_pkulaw_configured", lambda: True)
        monkeypatch.setattr(pkulaw_api, "_rpc_call", boom)
        monkeypatch.setattr(pkulaw_api, "run_verification_phase", boom)

        memo = report_generator.generate_memo("案", _scored_ctx(), pkulaw=True)
        assert memo["markdown"], "增强失败后报告不应为空"
        assert memo["pkulaw"]["status"] == "error"
        assert any("北大法宝增强失败" in w for w in memo.get("warnings", []))

    def test_section_renders_when_results_exist(self, monkeypatch):
        """有检索结果时才渲染第六章，且内容与案由对应"""
        monkeypatch.setattr(pkulaw_api, "_pkulaw_configured", lambda: True)
        monkeypatch.setattr(
            pkulaw_api, "search_for_rights_foundation",
            lambda cause: {"status": "ok", "laws": [
                {"title": "中华人民共和国著作权法", "content": "第十条 …"}]})
        monkeypatch.setattr(
            pkulaw_api, "search_for_infringement",
            lambda cause: {"status": "ok", "laws": [], "cases": [
                {"title": "某某著作权侵权案", "court": "某法院", "summary": "摘要"}]})
        monkeypatch.setattr(
            pkulaw_api, "run_verification_phase",
            lambda md: {"status": "ok", "_summary": "核验通过 2 条"})

        memo = report_generator.generate_memo(
            "案", _scored_ctx(cause_type=CAUSE_COPYRIGHT), pkulaw=True)
        md = memo["markdown"]
        assert "法律检索与引用核验" in md
        assert "中华人民共和国著作权法" in md
        assert "某某著作权侵权案" in md
        assert "核验通过 2 条" in md

    def test_section_skipped_for_unconfigured_but_surfaces_error(self):
        # 未配置 token：不留空章节
        assert report_generator.render_pkulaw_section({"status": "skipped"}) == []
        assert report_generator.render_pkulaw_section({}) == []
        # 检索/核验失败：必须显式露出原因，不能静默消失（问题3）
        err_lines = report_generator.render_pkulaw_section(
            {"status": "error", "error": "北大法宝鉴权失败(401)"})
        assert err_lines, "error 状态必须渲染提示，而非空章节"
        assert any("北大法宝鉴权失败(401)" in ln for ln in err_lines)


class TestExtractItemsShapes:
    """_extract_items 必须覆盖北大法宝四种返回形态，否则数据被静默丢弃（问题3 根因）。

    早期实现只认 `content[0].text` = JSON 列表，导致：
      - 单对象（get_article / law_recognition）→ 0 结果
      - 类案散文（search_case）→ 0 类案
    """

    def test_none_and_error_return_empty(self):
        assert pkulaw_api._extract_items(None) == []
        assert pkulaw_api._extract_items({"error": "boom"}) == []

    def test_json_list_string(self):
        rpc = {"result": {"content": [
            {"type": "text", "text": '[{"title": "商标法"}, {"title": "著作权法"}]'}]}}
        items = pkulaw_api._extract_items(rpc)
        assert len(items) == 2 and items[0]["title"] == "商标法"

    def test_json_object_wrapped_as_single(self):
        # get_article / law_recognition 常返回单对象而非列表——必须包成单元素列表
        rpc = {"result": {"content": [
            {"type": "text", "text": '{"title": "商标法", "article": "第五十七条…"}'}]}}
        items = pkulaw_api._extract_items(rpc)
        assert len(items) == 1 and items[0]["title"] == "商标法"

    def test_structured_content_list(self):
        rpc = {"result": {"structuredContent": {"result": [
            {"text": "商标法"}, {"text": "著作权法"}]}}}
        items = pkulaw_api._extract_items(rpc)
        assert len(items) == 2

    def test_case_prose_parsed(self):
        prose = ("共返回 3 条案例（语义检索候选）：\n\n"
                 "1. [普通案例] 某某侵害商标权纠纷一审民事判决书 | (2017)苏0412民初6116号\n"
                 "   文书类型：判决书 | 案件类型：民事案件 | 审理法院：常州市某人民法院 | 审结日期：2017-10-25\n"
                 "2. [普通案例] 另一案 | (2018)沪01民终1234号\n"
                 "   审理法院：上海市第一中级人民法院")
        rpc = {"result": {"content": [{"type": "text", "text": prose}]}}
        items = pkulaw_api._extract_items(rpc)
        assert len(items) == 2
        assert items[0]["ahao"] == "(2017)苏0412民初6116号"
        assert "常州市某人民法院" in items[0]["court"]

"""
L0 类案检索链路评测：散文解析 / 参数容错 / 省份与法院推导 / 四顺位级联

为什么专门这份测试：
    这条链路接入前，类案检索有一个「看着能用、实则空转」的状态——
    `_parse_case_prose` 抽不到日期与裁判说理，参数键拼错会被当成 0 命中，
    典型案例汇编条目因为没有案号被整个丢掉。三者都是**静默**的：
    没有报错、用例照绿，但注入模型的类案只有标题。

    所以这里的断言一律指到**结构与不作为**：
      - 解析产物必须有日期、必须有裁判说理；
      - 参数错误必须被认出来（而不是被当成零命中）；
      - 拿不到输入的顺位必须**跳过并留痕**，不许拿空值顶替（空值等于不过滤）。

fixture 取自 2026-09-19 真实接口返回（tests/fixtures/pkulaw_search_case_prose.json），
测试本身不需要任何 API key。
"""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("USE_MOCK", "True")

from core.config import CAUSE_COPYRIGHT, CAUSE_TRADEMARK
from core.pkulaw import court_resolver, pkulaw_api, precedent_ladder

FIXTURE = Path(__file__).parent / "fixtures" / "pkulaw_search_case_prose.json"


def _fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _prose_of(name: str) -> str:
    return _fixture()[name]


def _rpc_of(name: str) -> dict:
    return {"result": {"content": [{"type": "text", "text": _prose_of(name)}]}}


# ============================================================
# A. 散文解析：引入缺失字段同时保持原有约束
# ============================================================

class TestCaseProseParsing:
    def test_guiding_case_exposes_reasoning_and_date(self):
        items = pkulaw_api._parse_case_prose(_prose_of("guiding"))
        assert len(items) == 2, "指导性案例样本应有 2 条"

        first = items[0]
        assert first["ahao"] == "(2012)津高民三终字第0046号"
        assert first["court"] == "天津市高级人民法院"
        assert first["date"] == "2013-02-19"
        # 裁判说理必须真的进来——这是本轮补的核心能力
        assert first["summary"], "类案不能只有标题，裁判说理必须进入 prompt"
        assert "裁判要点" in first["summary"]

    def test_typical_case_without_case_number_is_still_kept(self):
        """无案号的典型案例汇编条目必须保留。

        旧的头部正则要求 `|` 后必须有内容（`\\s*` 又含换行），于是空案号的条目
        会把下一行元数据吃成案号，或直接丢条目。这里钉死：案号为空也要保住标题与正文。
        """
        items = pkulaw_api._parse_case_prose(_prose_of("typical_no_ahao"))
        second = items[1]
        assert second["ahao"] == "", f"案号应为空，不该吞下一行元数据：{second['ahao']!r}"
        assert second["title"], "无案号也必须保住标题"
        assert "文书类型" not in second["title"], "标题里混进了元数据"
        assert second["summary"], "无案号条目同样要抽出裁判说理"

    def test_ordinary_case_exposes_holding(self):
        items = pkulaw_api._parse_case_prose(_prose_of("ordinary"))
        assert items[0]["date"] == "2019-06-26"
        assert items[0]["summary"].strip().startswith("本院认为")

    def test_empty_hit_returns_nothing(self):
        assert pkulaw_api._parse_case_prose(_prose_of("empty")) == []

    def test_parse_never_raises_on_garbage(self):
        assert pkulaw_api._parse_case_prose("这不是类案返回\n随便一行") == []


# ============================================================
# B. 参数校验失败必须被认出来（2026-09-19 实跑确认的头号静默陷阱）
# ============================================================

class TestParameterErrorIsVisible:
    def test_validation_error_is_reported(self):
        fail = pkulaw_api._rpc_failed(_rpc_of("validation_error"))
        assert fail, "拼错参数键返回 HTTP 200 + 正文报错，必须被识别"
        assert "参数校验失败" in fail

    def test_validation_error_is_not_confused_with_zero_hits(self):
        """零命中与参数写错必须可区分——否则两者都显示「检索到 0 个类案」。"""
        assert pkulaw_api._rpc_failed(_rpc_of("empty")) == ""
        assert pkulaw_api._rpc_failed(_rpc_of("validation_error"))

    def test_auth_errors_still_detected_first(self):
        assert "401" in pkulaw_api._rpc_failed(
            {"error": {"code": 401, "message": "Unauthorized"}})
        assert "403" in pkulaw_api._rpc_failed(
            {"error": {"code": 403, "message": "Forbidden"}})

    def test_non_dict_input_is_safe(self):
        assert pkulaw_api._rpc_failed(None) == ""
        assert pkulaw_api._rpc_failed("boom") == ""


# ============================================================
# C. 时间窗口必须动态
# ============================================================

class TestFiveYearsWindow:
    def test_marker_is_five_years_ago(self):
        from datetime import datetime
        marker = pkulaw_api.five_years_ago()
        year = int(marker[:4])
        assert datetime.now().year - year == 5, f"五年窗口漂移了：{marker}"

    def test_financial_search_uses_the_dynamic_window(self, monkeypatch):
        """扫源码会因为注释里的旧日期假红，这里直接拦住 RPC 看实参。"""
        calls = []

        def fake_rpc(tool, args, timeout=25):
            calls.append((tool, args))
            return {"result": {"content": [{"text": "[]"}]}}

        monkeypatch.setattr(pkulaw_api, "_pkulaw_configured", lambda: True)
        monkeypatch.setattr(pkulaw_api, "five_years_ago", lambda: "SENTINEL")
        monkeypatch.setattr(pkulaw_api, "_rpc_call", fake_rpc)

        pkulaw_api.search_for_financial(CAUSE_TRADEMARK)
        dates = [a.get("decision_date_start") for t, a in calls if t == "search_case"]
        assert dates == ["SENTINEL"], f"判赔检索没走动态时间窗口：{dates}"


# ============================================================
# D. 省份与法院推导
# ============================================================

class TestCourtResolver:
    def test_province_from_registration_region(self):
        province, basis = court_resolver.province_from_text("广东省深圳市南山区")
        assert province == "广东省"
        assert basis

    def test_city_fallback_is_marked_as_heuristic(self):
        province, basis = court_resolver.province_from_text("杭州市余杭区")
        assert province == "浙江省"
        assert "兜底" in basis, "城市推导必须自证是兜底，不能冒充精确数据"

    def test_ambiguous_region_abstains(self):
        province, basis = court_resolver.province_from_text("广东省与浙江省均有工厂")
        assert province is None
        assert "不确定" in basis

    def test_unknown_region_abstains(self):
        province, _ = court_resolver.province_from_text("被告经营范围涵盖全国")
        assert province is None

    def test_high_court_name(self):
        assert court_resolver.province_to_high_court("广东省") == "广东省高级人民法院"
        assert court_resolver.province_to_high_court("内蒙古自治区") == "内蒙古自治区高级人民法院"
        assert court_resolver.province_to_high_court("不是省") is None

    def test_upper_court_of_basic_and_intermediate_courts(self):
        assert court_resolver.derive_upper_court("杭州市余杭区人民法院")[0] == "杭州市中级人民法院"
        assert court_resolver.derive_upper_court("深圳市中级人民法院")[0] == "广东省高级人民法院"
        assert court_resolver.derive_upper_court("广东省高级人民法院")[0] == "最高人民法院"

    def test_upper_court_abstains_when_undecidable(self):
        """直辖市基层法院的上诉中院分号无法靠名字推导——宁可不做，不猜。"""
        name, reason = court_resolver.derive_upper_court("北京市海淀区人民法院")
        assert name is None
        assert reason, "放弃推导时必须给出原因"

    def test_hint_prefers_registered_region(self):
        hint = court_resolver.hint_from_case(
            region="广东省深圳市", location_hint="上海", case_description="本案发生在北京")
        assert hint["province"] == "广东省"
        assert hint["court"] is None, "系统不采集管辖法院，不得伪造"

    def test_hint_falls_back_to_case_description(self):
        hint = court_resolver.hint_from_case(
            region="", location_hint="", case_description="被告在浙江省杭州市销售被诉产品")
        assert hint["province"] == "浙江省"


# ============================================================
# E. 去重：案号缺失时的回退主键
# ============================================================

class TestDedup:
    def test_same_case_number_collapses(self):
        cases = [
            {"ahao": "(2019)京01民初1号", "title": "甲", "court": "A法院"},
            {"ahao": "(2019)京01民初1号", "title": "甲", "court": "A法院"},
        ]
        out, _ = precedent_ladder.dedup_cases(cases)
        assert len(out) == 1

    def test_without_case_number_falls_back_to_court_and_title(self):
        """典型案例汇编条目大多没有案号——不能因此把它们全判重丢掉。"""
        cases = [
            {"ahao": "", "title": "某法院发布10起典型案例之八", "court": "A法院"},
            {"ahao": "", "title": "某法院发布10起典型案例之八", "court": "A法院"},
            {"ahao": "", "title": "某法院发布10起典型案例之九", "court": "A法院"},
        ]
        out, _ = precedent_ladder.dedup_cases(cases)
        assert len(out) == 2
        assert all(c["dedup_key"] == "法院+标题" for c in out)
        assert all(c["has_ahao"] is False for c in out)

    def test_dated_come_first(self):
        cases = [
            {"title": "旧", "date": "2015-01-01"},
            {"title": "无日期", "date": ""},
            {"title": "新", "date": "2024-06-01"},
        ]
        ordered = precedent_ladder.sort_by_date_desc(cases)
        assert [c["title"] for c in ordered] == ["新", "旧", "无日期"]


# ============================================================
# F. 四顺位级联
# ============================================================

def _mk_prose(n: int, prefix: str, with_number: bool = True,
              cause: str = "侵害商标权纠纷") -> str:
    lines = [f"共返回 {n} 条案例（语义检索候选）：\n"]
    for i in range(1, n + 1):
        # 案号里带上 prefix，否则不同档的样例会被去重逻辑当成同一批（测试自己的坑）
        ahao = f"（2020）{prefix}{i}民初{i}号" if with_number else ""
        lines.append(f"{i}. [普通案例] {prefix}案例{i} | {ahao}")
        lines.append(f"   文书类型：判决书 | 案件类型：民事案件 | 审理法院：某法院 | 审结日期：2020-0{i}-01")
        lines.append(f"   案由：{cause}")
        lines.append("   本院认为：裁判说理内容。")
    return "\n".join(lines) + "\n"


def _rpc_with(prose: str) -> dict:
    return {"result": {"content": [{"type": "text", "text": prose}]}}


class TestLadderPlan:
    def test_tier3_requires_province(self):
        _, skipped = precedent_ladder.build_tier_plan(province=None, since="2021-01-01")
        codes = [s["code"] for s in skipped]
        assert "③" in codes and "④" in codes

    def test_tier3_built_when_province_known(self):
        tiers, skipped = precedent_ladder.build_tier_plan(
            province="广东省", since="2021-01-01")
        codes = [s["code"] for s in skipped]
        assert "③" not in codes
        third = next(t for t in tiers if t.code == "③")
        names = [c.extra.get("courthouse_name") for c in third.calls]
        assert "广东省高级人民法院" in names
        # ③-A 要同时查「典型案例」与「参考案例」两个标签
        grades = [c.extra.get("case_grade") for c in third.calls]
        assert grades.count("典型案例") == 1 and "参考案例" in grades

    def test_tier1_has_no_time_filter(self):
        tiers, _ = precedent_ladder.build_tier_plan(since="2021-01-01")
        first = tiers[0].calls[0]
        assert "decision_date_start" not in first.extra

    def test_tier4_needs_the_court(self):
        tiers, skipped = precedent_ladder.build_tier_plan(
            province="浙江省", court="杭州市余杭区人民法院", since="2021-01-01")
        assert "④" not in [s["code"] for s in skipped]
        fourth = next(t for t in tiers if t.code == "④")
        names = [c.extra.get("courthouse_name") for c in fourth.calls]
        assert names == ["杭州市中级人民法院", "杭州市余杭区人民法院"]


class TestLadderRun:
    @staticmethod
    def _patch(monkeypatch, hits_per_call, validator=None):
        seen = []

        def fake_rpc(tool, args, timeout=25):
            seen.append(dict(args))
            if validator is not None:
                validator(args)
            return {"result": {"content": [
                {"type": "text", "text": _mk_prose(hits_per_call, "命中")}]}}

        monkeypatch.setattr(pkulaw_api, "_rpc_call", fake_rpc)
        return seen

    def test_first_tier_stops_once_target_reached(self, monkeypatch):
        seen = self._patch(monkeypatch, hits_per_call=6)
        result = precedent_ladder.retrieve_similar_cases(
            "商标侵权", cause_type=CAUSE_TRADEMARK,
            session=precedent_ladder.LadderSession())
        assert result["total"] >= 5
        assert result["executed_calls"] == 1, f"首档凑够就该停，实际调用 {len(seen)} 次"
        assert all("tier_code" in c for c in result["cases"])

    def test_dedups_across_tiers(self, monkeypatch):
        """各顺位都可能返回同一批案例，跨顺位必须去重后才计数。"""
        def fake_rpc(tool, args, timeout=25):
            return {"result": {"content": [
                {"type": "text", "text": _mk_prose(2, "重复")}]}}
        monkeypatch.setattr(pkulaw_api, "_rpc_call", fake_rpc)
        result = precedent_ladder.retrieve_similar_cases(
            "商标侵权", session=precedent_ladder.LadderSession(max_calls=20))
        assert result["total"] == 2, f"同一批案例被重复计数：{result['total']}"
        assert result["executed_calls"] > 1, "应当继续检索后续顺位"

    def test_missing_inputs_are_recorded_not_faked(self, monkeypatch):
        seen = self._patch(monkeypatch, hits_per_call=0)
        result = precedent_ladder.retrieve_similar_cases(
            "商标侵权", session=precedent_ladder.LadderSession())
        reasons = {s["code"]: s["reason"] for s in result["skipped"]}
        assert "③" in reasons and "④" in reasons
        # 没拿到省份就不许出现 province 过滤参数（空值会让接口变成不过滤）
        assert not any(a.get("courthouse_province") for a in seen)
        assert result["insufficient"] is True
        assert "类案数量不足" in result["insufficient_note"]

    def test_province_enables_tier_three(self, monkeypatch):
        self._patch(monkeypatch, hits_per_call=0)
        result = precedent_ladder.retrieve_similar_cases(
            "商标侵权", province="广东省",
            session=precedent_ladder.LadderSession())
        assert "③" not in [s["code"] for s in result["skipped"]]

    def test_call_budget_is_enforced(self, monkeypatch):
        self._patch(monkeypatch, hits_per_call=0)
        session = precedent_ladder.LadderSession(
            max_calls=precedent_ladder.MAX_CALLS_PER_RUN)
        result = precedent_ladder.retrieve_similar_cases(
            "商标侵权", province="广东省", court="杭州市余杭区人民法院",
            session=session, target=99)
        assert result["executed_calls"] <= precedent_ladder.MAX_CALLS_PER_RUN
        assert result["budget_exhausted"] is True

    def test_parameter_error_aborts_and_surfaces(self, monkeypatch):
        def fake_rpc(tool, args, timeout=25):
            return {"result": {"content": [
                {"type": "text", "text": _prose_of("validation_error")}]}}
        monkeypatch.setattr(pkulaw_api, "_rpc_call", fake_rpc)
        result = precedent_ladder.retrieve_similar_cases(
            "商标侵权", session=precedent_ladder.LadderSession())
        assert result["status"] == "error"
        assert result["executed_calls"] == 1, "参数错就是参数错，不该继续用错参数重试"
        assert any("参数校验失败" in e for e in result["errors"])

# ============================================================
# G. 案由相关性门（2026-09-19 实跑暴露：高权威档最容易混入无关案由）
# ============================================================

class TestRelevanceGate:
    def test_irrelevant_hits_do_not_fill_the_quota(self, monkeypatch):
        """顺位①返回的全是案由无关的指导案例时，级联必须继续往下走。

        照规则原文按「原始条数」达标的话，这里会在顺位①就凑满并停止，
        结果拿到的全是环境污染类指导案例——检索增强反而成了噪声注入。
        """
        def fake_rpc(tool, args, timeout=25):
            if args.get("case_grade") == "指导性案例":
                return _rpc_with(_mk_prose(6, "无关", cause="环境污染责任纠纷"))
            return _rpc_with(_mk_prose(6, "对口", cause="侵害商标权纠纷"))

        monkeypatch.setattr(pkulaw_api, "_rpc_call", fake_rpc)
        result = precedent_ladder.retrieve_similar_cases(
            "商标侵权", cause_type=CAUSE_TRADEMARK, province="广东省",
            session=precedent_ladder.LadderSession())
        assert result["relevant_total"] >= 5
        assert result["dropped_irrelevant"] == 6
        assert result["executed_calls"] == 2, "指标记叫相关就不该在首档停住"

    def test_relevant_cases_come_first(self, monkeypatch):
        monkeypatch.setattr(
            pkulaw_api, "_rpc_call",
            lambda tool, args, timeout=25: _rpc_with(
                _mk_prose(6, "无关", cause="环境污染责任纠纷")))
        result = precedent_ladder.retrieve_similar_cases(
            "商标侵权", cause_type=CAUSE_TRADEMARK,
            session=precedent_ladder.LadderSession(max_calls=20))
        # 全不相关时什么都不丢（保留可追溯），但会如实标注
        assert result["relevant_total"] == 0
        assert result["total"] > 0

    def test_relevance_follows_the_cause(self):
        assert precedent_ladder.relevance_score(
            {"cause": "著作权权属纠纷", "title": "某案"}, CAUSE_COPYRIGHT) > 0
        assert precedent_ladder.relevance_score(
            {"cause": "环境污染责任纠纷", "title": "某案"}, CAUSE_TRADEMARK) == 0

    def test_unknown_cause_raises(self, monkeypatch):
        with pytest.raises(ValueError, match="不支持的案由"):
            precedent_ladder.retrieve_similar_cases(
                "某案", cause_type="专利侵权",
                session=precedent_ladder.LadderSession())

    def test_every_call_carries_the_rule_inputs(self, monkeypatch):
        seen = TestLadderRun._patch(monkeypatch, hits_per_call=0)
        precedent_ladder.retrieve_similar_cases(
            "商标侵权", province="广东省",
            session=precedent_ladder.LadderSession(max_calls=20))
        for args in seen:
            assert args["case_type"] == "民事案件", args
            assert args["doc_type"] == "判决书", "漏传 doc_type 会混进非判决文书"
            assert args["text"] == "商标侵权"

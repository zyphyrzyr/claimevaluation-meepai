"""
L1 企查查事实接线评测

背景（评测报告 P1-5 / 用户反馈）：企查查 8 阶段 70+ 次 RPC 全部成功，
但明细既没进 LLM 的判赔推理、也没在界面露出来——用户据此怀疑「根本没调企查查」。
三个阶段修复分别对应下面三组用例：

  ① _build_facts        事实提炼（qcc_api）
  ② _format_qcc_block   事实注入（evaluate_nodes）
  ③ _run_business       节点顺序（orchestrator：企查查必须在判赔之前）

两条纪律贯穿本文件：
  - 正面断言。不能只断言「没报错」：企查查失败时评估照常跑完还没有任何报错，
    「没有 failed 节点」这种判据会在只跑了 1/7 节点时报全绿。
  - 变异可检。每层都留了一条「撤掉实现就必须变红」的用例，见文件末尾说明。
"""
import io
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("USE_MOCK", "True")

from core import config, evaluate_nodes, llm_gateway, orchestrator, qcc_api  # noqa: E402
from core.case_context import CaseContext  # noqa: E402
from core.mock import mock_defendant_profile  # noqa: E402

DEFENDANT = {"name": "深圳市茉莉奶白餐饮管理有限公司", "type": "enterprise"}


def _ctx(goal="要钱") -> CaseContext:
    return CaseContext(
        case_id="t-qcc",
        case_description="被告在全国开设门店约 2268 家，大量使用与原告近似的标识。",
        cause_type="商标侵权",
        goal_type=goal,
        defendant_info=dict(DEFENDANT),
        parties=[{"role": "plaintiff", "name": "原告公司", "party_type": "enterprise"},
                 {"role": "defendant", "name": DEFENDANT["name"], "party_type": "enterprise"}],
    )


def _stage_b(items=None, financial=False):
    return {
        "工商登记": {
            "_count": 1,
            "_summary": "1条记录",
            "_items": [items if items is not None else {
                "企业名称": "深圳市茉莉奶白餐饮管理有限公司",
                "统一社会信用代码": "91440300MA5G6K6D27",
                "法定代表人": "张彪",
                "登记状态": "开业（存续）",
                "成立日期": "2020-05-15",
                "注册资本": "104.167万元",
                "人员规模": "100-199人",
                "参保人数": "165",
                "国标行业": {"门类": "住宿和餐饮业"},
                "所属地区": "广东省深圳市宝安区",
            }],
        },
        "财务数据": {"_count": 1 if financial else 0,
                     "_summary": "1条记录" if financial else "无数据",
                     "_items": [{"年度": 2024}] if financial else []},
        "企业简介": {"_count": 1, "_summary": "1条记录", "_items": []},
    }


def _stage_d(**counts):
    base = {k: {"_count": 0, "_summary": "0条"} for k in (
        "注销记录", "清算信息", "破产重整", "被执行人", "失信信息",
        "终本案件", "限高消费", "严重违法", "经营异常", "法院立案")}
    for k, v in counts.items():
        base[k] = {"_count": v, "_summary": f"{v}条"}
    return base


def _stage_f(**counts):
    base = {k: {"_count": 0, "_summary": "0条"} for k in (
        "商标资产", "线上店铺", "APP信息", "小程序", "微信公众号", "抖音账号",
        "招投标", "融资记录", "荣誉信息")}
    for k, v in counts.items():
        base[k] = {"_count": v, "_summary": f"{v}条"}
    return base


def _closed_stages(items=None, financial=False, **stage_d_counts):
    return {
        "A_主体锁定": {"ok": True, "locked_name": DEFENDANT["name"],
                       "credit_code": "91440300MA5G6K6D27", "match_status": "唯一精确匹配"},
        "B_基本盘": _stage_b(items, financial),
        "C_风险分诊": {"hits": {}, "_summary": "未命中风险维度", "total_hit_dimensions": 0},
        "D_风险下钻": _stage_d(**stage_d_counts),
        "F_经营规模": _stage_f(商标资产=1, 微信公众号=1),
        "G_诉讼时间": {"被诉历史": {"_count": 0, "_summary": "0件"}},
    }


# ============================================================
# 1. 事实提炼：阶段明细 → facts
# ============================================================

class TestFactsStructure:
    """facts 是展示层与 prompt 注入的唯一来源，字段少了两端一起瞎。"""

    @staticmethod
    def _facts(**kw) -> dict:
        st = _closed_stages(**kw)
        return qcc_api._build_facts(
            st["A_主体锁定"], st["B_基本盘"], st["C_风险分诊"], st["D_风险下钻"],
            st["F_经营规模"], st["G_诉讼时间"], [], [], queried_name=DEFENDANT["name"])

    def test_facts_holds_all_four_groups(self):
        f = self._facts()
        for group in ("entity", "risk", "scale"):
            assert f.get(group), f"facts 缺少 {group} 组"
        for key in ("financial_data_available", "signals_hit",
                    "scale_tier", "scale_tier_basis"):
            assert key in f, f"facts 缺少 {key}"

    def test_entity_values_come_from_registration_not_hardcoded(self):
        """务必盯着「值从哪来」：写死常量的假实现也能通过键存在性断言。"""
        e = self._facts()["entity"]
        assert e["name"] == DEFENDANT["name"]
        assert e["credit_code"] == "91440300MA5G6K6D27"
        assert e["legal_rep"] == "张彪"
        assert e["reg_status"] == "开业（存续）"
        assert e["established"] == "2020-05-15"
        assert e["insured_count"] == 165, "参保人数应解析为整数"
        assert e["registered_capital_wan"] == 104.167
        assert e["industry"] == "住宿和餐饮业"
        assert e["region"] == "广东省深圳市宝安区"

    def test_risk_and_scale_counts_flow_from_stages(self):
        f = self._facts(失信信息=2, 被执行人=5, 法院立案=1)
        r, s = f["risk"], f["scale"]
        assert (r["dishonest"], r["executed"], r["court_filed"]) == (2, 5, 1)
        assert r["abnormal"] == 0 and r["bankruptcy"] == 0
        assert (s["trademark_count"], s["wechat_mp"], s["online_shops"]) == (1, 1, 0)

    def test_missing_value_stays_empty_never_backfilled(self):
        """取不到的字段必须留空，不能回落成别处的乐观猜测。

        尤其 name 不能退回阶段 A 的 locked_name：那是模糊搜索挑出来的名字，
        在同名企业里可能就是另一家公司——把它当已核实的事实用，
        正是「查错了人还看不出来」的入口。
        """
        st = _closed_stages()
        st["B_基本盘"]["工商登记"]["_items"] = []
        f = qcc_api._build_facts(st["A_主体锁定"], st["B_基本盘"], st["C_风险分诊"],
                                 st["D_风险下钻"], st["F_经营规模"], st["G_诉讼时间"],
                                 [], [], queried_name=DEFENDANT["name"])
        assert f["entity"]["name"] == ""
        assert f["entity"]["insured_count"] is None
        assert f["entity"]["registered_capital_wan"] is None
        assert f["scale_tier"] == "未分档"
        # 搜索命中的名字单独留痕，与「已核实的登记明细」区分开
        assert f["entity"]["locked_name"] == DEFENDANT["name"]

    def test_financial_flag_reflects_stage_b(self):
        assert self._facts(financial=True)["financial_data_available"] is True
        assert self._facts(financial=False)["financial_data_available"] is False

    def test_signals_hit_counts_red_and_green_flags(self):
        f = qcc_api._build_facts({}, _stage_b(), {}, _stage_d(), _stage_f(), {},
                                 ["失信被执行(2条)", "经营异常"], ["上市公司"],
                                 queried_name="X")
        assert f["signals_hit"] == 3

    def test_name_mismatch_is_flagged(self):
        """查到的是同名另一家公司 —— 后面所有事实都会张冠李戴且毫无报错。"""
        st = _closed_stages()
        st["A_主体锁定"]["locked_name"] = "广州锦云服装有限公司"
        f = qcc_api._build_facts(st["A_主体锁定"], st["B_基本盘"], st["C_风险分诊"],
                                 st["D_风险下钻"], st["F_经营规模"], st["G_诉讼时间"],
                                 [], [], queried_name="广州锦云服饰有限公司")
        assert f["entity"]["name_matches_query"] is False


class TestScaleTier:
    """按工商硬指标分档。这是展示/审计标签，不参与任何金额计算。"""

    @pytest.mark.parametrize("capital,insured,expected", [
        ("3亿元", None, "大型"),
        (None, "1200", "大型"),
        ("5000万元", None, "中型"),
        ("104.167万元", "165", "中型"),
        ("50万元", "8", "小微"),
        ("500万元", "30", "中小型"),
    ])
    def test_tier_by_hard_indicators(self, capital, insured, expected):
        tier, _ = qcc_api._classify_scale(
            qcc_api._num_from_cn(insured), qcc_api._num_from_cn(capital))
        assert tier == expected

    def test_unknown_unit_is_not_guessed(self):
        """100 万美元 ≠ 100 万元人民币。算不出来就 None，不许猜。"""
        assert qcc_api._num_from_cn("100万美元") is None
        assert qcc_api._num_from_cn("104.167万元") == 104.167
        assert qcc_api._num_from_cn("3亿元") == 30000.0

    def test_tier_basis_shows_the_measured_numbers(self):
        _, basis = qcc_api._classify_scale(165.0, 104.167)
        assert "参保 165 人" in basis and "注册资本 104.167 万元" in basis

    def test_no_indicator_yields_unclassified(self):
        tier, basis = qcc_api._classify_scale(None, None)
        assert tier == "未分档"


# ============================================================
# 2. 事实注入：facts → 判赔 prompt
# ============================================================

@pytest.fixture
def capture_prompt(monkeypatch):
    """拦住 HTTP 出口，拿到真正发出去的 prompt —— 看「喂了什么」而不是「返回了什么」。"""
    captured = {}

    def fake_urlopen(req, timeout=None):
        payload = json.loads(req.data.decode("utf-8"))
        captured["prompt"] = payload["messages"][-1]["content"]
        body = json.dumps({"choices": [{"message": {"content": json.dumps({
            "score": 70, "p10": 12, "p50": 35, "p90": 88,
            "return_multiple": 3.2, "scale_support": "medium",
            "external_conflict": "", "analysis": "判赔规模中等偏上。"})}}]})
        return io.BytesIO(body.encode("utf-8"))

    base = config.get_runtime_settings()
    monkeypatch.setattr(llm_gateway, "get_runtime_settings",
                        lambda: {**base, "llm_api_key": "test-key",
                                 "llm_base_url": "https://api.example.invalid"})
    monkeypatch.setattr(llm_gateway.urllib.request, "urlopen", fake_urlopen)
    return captured


class TestFactsInjection:
    def test_facts_reach_the_damages_prompt(self, capture_prompt):
        ctx = _ctx()
        ctx.defendant_profile = {"metrics": {
            "facts": qcc_api._build_facts(*self._stages(), [], [],
                                          queried_name=DEFENDANT["name"])}}
        evaluate_nodes.evaluate_damages(ctx)
        p = capture_prompt["prompt"]
        for expected in ("被告工商登记事实", "91440300MA5G6K6D27", "张彪",
                         "参保 165 人", "开业（存续）", "中型"):
            assert expected in p, f"判赔 prompt 未含工商事实：{expected}"

    def test_name_mismatch_warning_reaches_the_prompt(self, capture_prompt):
        """查错主体时，LLM 必须被明确告知，否则它会把别家公司的事实当成被告的。"""
        st = _closed_stages()
        st["A_主体锁定"]["locked_name"] = "广州锦云服装有限公司"
        ctx = _ctx()
        ctx.defendant_profile = {"metrics": {"facts": qcc_api._build_facts(
            st["A_主体锁定"], st["B_基本盘"], st["C_风险分诊"], st["D_风险下钻"],
            st["F_经营规模"], st["G_诉讼时间"], [], [],
            queried_name="广州锦云服饰有限公司")}}
        evaluate_nodes.evaluate_damages(ctx)
        assert "不一致" in capture_prompt["prompt"]

    def test_prompt_forbids_using_facts_as_a_multiplier(self, capture_prompt):
        """本次改动的红线：工商事实不得变成机械乘数，判赔仍由 LLM 在法定框架内给。"""
        ctx = _ctx()
        ctx.defendant_profile = {"metrics": {
            "facts": qcc_api._build_facts(*self._stages(), [], [],
                                          queried_name=DEFENDANT["name"])}}
        evaluate_nodes.evaluate_damages(ctx)
        assert "不得据此直接加减判赔金额" in capture_prompt["prompt"]
        assert "external_conflict" in capture_prompt["prompt"]

    def test_no_facts_means_no_external_section(self, capture_prompt):
        """无事实时注入空壳，会让 LLM 把「没查到」误读成「被告是家空壳公司」。"""
        evaluate_nodes.evaluate_damages(_ctx())
        assert "外部" not in capture_prompt["prompt"]

    def test_no_facts_means_no_external_conflict_field(self, capture_prompt):
        """「查过且无矛盾」与「压根没查」必须是两种状态。"""
        result = evaluate_nodes.evaluate_damages(_ctx())
        assert "external_conflict" not in result

    @staticmethod
    def _stages():
        st = _closed_stages()
        return (st["A_主体锁定"], st["B_基本盘"], st["C_风险分诊"], st["D_风险下钻"],
                st["F_经营规模"], st["G_诉讼时间"])


# ============================================================
# 3. 节点顺序：企查查必须在判赔之前
# ============================================================

def _real_profile(monkeypatch, profile=None, raises=False):
    """把企查查出口换成可控返回/可控异常，并记录它被调用的时刻。"""
    calls = []

    def fake(info):
        calls.append(info)
        if raises:
            raise RuntimeError("企查查连接超时")
        return profile if profile is not None else QCC_PROFILE

    monkeypatch.setattr(qcc_api, "search_for_financial_qcc_full", fake)
    return calls


QCC_PROFILE = {
    "metrics": {
        "recovery_probability": 50.0,
        "damages_adjustment": "中型",
        "time_extra_months": 0,
        "red_flags": [], "green_flags": [],
        "facts": {"entity": {"name": DEFENDANT["name"], "insured_count": 165},
                  "risk": {}, "scale": {}, "scale_tier": "中型"},
    },
    "stages": _closed_stages(),
    "error": None,
}


class TestNodeOrder:
    """
    顺序这条曾经错得很隐蔽：企查查内联在 damages 之后，damages 跑的时候
    ctx.defendant_profile 还是空 dict，LLM 只能照抄案卷里的自述规模
    （实测材料称 2268 家门店、工商实查参保 165 人，系统无人质疑）。
    """

    @staticmethod
    def _run(monkeypatch, *, damages_spy=None, **kw):
        events = []
        ctx = _ctx()
        orch = orchestrator.Orchestrator(ctx, on_event=events.append)
        orch.mock = False
        _real_profile(monkeypatch, **kw)
        seen = {}

        def spy(c, use_mock=False):
            seen["profile_when_damages_ran"] = dict(c.defendant_profile or {})
            return {} if damages_spy is None else damages_spy

        monkeypatch.setattr(evaluate_nodes, "evaluate_damages", spy)
        monkeypatch.setattr(orchestrator.Orchestrator, "_emit_qcc_stages",
                            lambda self, p: None)
        orch._run_business()
        return ctx, seen, events

    def test_defendant_profile_is_ready_before_damages_runs(self, monkeypatch):
        ctx, seen, _ = self._run(monkeypatch)
        assert seen["profile_when_damages_ran"], \
            "判赔节点执行时被告画像仍为空 —— 企查查又被排到判赔之后了"

    def test_damages_sees_actual_facts(self, monkeypatch):
        _, seen, _ = self._run(monkeypatch)
        metrics = seen["profile_when_damages_ran"].get("metrics") or {}
        assert (metrics.get("facts") or {}).get("entity", {}).get("insured_count") == 165

    def test_recovery_is_set_before_damages(self, monkeypatch):
        ctx, _, _ = self._run(monkeypatch)
        assert ctx.recovery_ability == 50.0

    def test_qcc_failure_does_not_block_damages(self, monkeypatch):
        """外部增强项失败不该拖垮主链路：企查查炸了，判赔仍要照常算出来。"""
        ctx, seen, events = self._run(monkeypatch, raises=True)
        # 异常画像允许带 error，但不能凭空长出 facts 让 LLM 以为查到了东西
        assert not ((seen.get("profile_when_damages_ran") or {}).get("metrics") or {}).get("facts")
        assert ctx.recovery_ability is None
        assert ctx.dimension_results["damages"]["status"] != "failed"
        assert ctx.dimension_results["recovery"]["status"] == "failed"

    def test_conflict_from_llm_is_surfaced(self, monkeypatch):
        damages = {"score": 40, "p50": 20,
                   "external_conflict": "材料称 2268 家门店，工商实查参保 165 人，量级不符"}
        _, _, events = self._run(monkeypatch, damages_spy=damages)
        assert any("矛盾" in str(e.get("text", "")) for e in events), \
            "矛盾必须在流程轨迹里显式出现，不能只躺在 JSON 里"


# ============================================================
# 4. mock 与真实模式要能区分
# ============================================================

class TestMockNotColliding:
    """
    原先 mock 的 recovery_probability=50.0、damages_adjustment="中等规模 → 基准"
    与真实模式的常见输出完全相同，界面上自证不了数据来源。
    """

    def test_recovery_differs_from_real_default(self):
        assert mock_defendant_profile()["metrics"]["recovery_probability"] != 50.0

    def test_simulated_flag_and_marked_name(self):
        p = mock_defendant_profile()
        assert p["metrics"].get("simulated") is True
        assert "模拟" in (p["metrics"]["facts"]["entity"]["name"] or "")

    def test_mock_carries_the_same_facts_shape(self):
        """形状不齐，前端在 mock 下是另一套渲染分支——演示时才会炸。"""
        real = qcc_api._build_facts(*(lambda st: (
            st["A_主体锁定"], st["B_基本盘"], st["C_风险分诊"], st["D_风险下钻"],
            st["F_经营规模"], st["G_诉讼时间"]))(_closed_stages()),
            [], [], queried_name="X")
        mock = mock_defendant_profile()["metrics"]["facts"]
        assert set(mock) == set(real)
        for group in ("entity", "risk", "scale"):
            assert set(mock[group]) == set(real[group]), f"{group} 组字段与真实模式不一致"

    def test_mock_stages_are_renderable(self):
        stages = mock_defendant_profile()["stages"]
        for key in ("A_主体锁定", "B_基本盘", "C_风险分诊", "D_风险下钻",
                    "F_经营规模", "G_诉讼时间"):
            assert key in stages, f"mock 缺少 {key}，8 阶段明细表会渲染成空"

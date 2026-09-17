"""
L2 真实代码路径评测（接真实 key 前的最后一道自检）

Mock 模式下 call_json 一次都不会被调用——五个评估节点走 mock_* 直接返回结构化 dict。
也就是说，prompt 拼装、模型路由、JSON 解析、节点落库这条链路在其余测试里**零覆盖**，
而它恰恰是接上真实 key 后最先炸的地方（评测报告 P0-1 就是这么挖出来的）。

本文件把 HTTP 出口换成按节点分派的假响应，让 use_mock=False 的完整主流程跑一遍，
验证「真实模式的代码路径本身是通的」，与模型实际质量无关。
"""
import io
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("USE_MOCK", "True")

from core import config, llm_gateway, orchestrator, scoring
from core.case_context import CaseContext
from core.config import (CAUSE_COPYRIGHT, CAUSE_TRADEMARK, LLM_FAST_MODEL,
                         LLM_STRONG_MODEL, SCORE_THRESHOLD_GO, SCORE_THRESHOLD_PATCH)


# 按 prompt 特征词分派的假响应。特征词取自 evaluate_nodes 各节点的真实 prompt，
# 万一某个节点改了措辞，_classify 会直接抛错——那正是要发现的问题，
# 不能静默落到某个默认分支，否则改了 prompt 这条测试就失去意义了。
NODE_RESPONSES = {
    # 证据盘点返回的是矩阵而非分数，故意留两个缺口让 gap_list 非空
    "evidence_review": json.dumps({
        "matrix": [
            {"id": "权利基础证据-1", "status": "sufficient", "reason": "已提供商标注册证"},
            {"id": "权利基础证据-2", "status": "sufficient", "reason": "连续三年使用证据齐全"},
            {"id": "权利基础证据-3", "status": "sufficient", "reason": "续展证明在案"},
            {"id": "侵权认定证据-1", "status": "sufficient", "reason": "侵权截图已公证"},
            {"id": "侵权认定证据-2", "status": "sufficient", "reason": "购买取证完整"},
            {"id": "侵权认定证据-3", "status": "partial", "reason": "仅覆盖部分店铺"},
            {"id": "损害赔偿证据-1", "status": "partial", "reason": "销量数据不完整"},
            {"id": "损害赔偿证据-2", "status": "missing", "reason": "未提供获利证据"},
            {"id": "取证技术规范-1", "status": "sufficient", "reason": "可信时间戳齐全"},
        ],
        "extra_evidence": [{"name": "被告招商页面", "value": "可佐证侵权规模"}],
        "note": "",
    }, ensure_ascii=False),
    "rights": '{"score": 82, "analysis": "权利基础稳固，商标在有效期内且连续使用满三年。", '
              '"strengths": ["注册有效", "使用证据充分"], "risks": ["核定范围略窄"]}',
    "infringement": '好的，以下是认定结论：\n```json\n{"score": 76, "elements": '
                    '[{"name": "商标性使用", "status": "满足", "analysis": "用于商品标识"},'
                    ' {"name": "混淆可能性", "status": "存疑", "analysis": "需进一步举证"}], '
                    '"analysis": "侵权构成要件总体成立。"}\n```\n供参考。',
    "procedure": '{"score": 88, "risks": [{"item": "诉讼时效", "level": "low", '
                 '"detail": "未届满"}, {"item": "管辖", "level": "low", "detail": "可选择"}], '
                 '"analysis": "程序上无明显障碍。"}',
    "damages": '{"score": 70, "p10": 12, "p50": 35, "p90": 88, "return_multiple": 3.2, '
               '"scale_support": "medium", "analysis": "判赔规模中等偏上。"}',
    "precedent": '{"score": 64, "first_case_index": 58, "influence_level": "区域级", '
                 '"analysis": "有一定首案潜力。"}',
}

PROMPT_MARKERS = {
    "evidence_review": "逐项核验本案证据准备情况",
    "rights": "评估原告权利基础的稳固程度",
    "infringement": "逐一认定以下构成要件",
    "procedure": "评估诉讼程序可行性",
    "damages": "估算本案判赔规模",
    "precedent": "评估本案的判例价值",
}

QCC_PROFILE = {
    "metrics": {"recovery_probability": 68, "red_flags": [], "green_flags": ["经营正常"]},
}


def _classify(user_prompt: str) -> str:
    """按 prompt 特征词判断在调哪个节点；认不出来要报错，不能静默给个默认值"""
    for node, marker in PROMPT_MARKERS.items():
        if marker in user_prompt:
            return node
    raise AssertionError(f"无法识别的评估节点 prompt：{user_prompt[:120]}")


@pytest.fixture
def real_llm(monkeypatch):
    """伪造 LLM 与企查查出口，记录每次调用，让真实模式链路真正跑起来"""
    calls = []

    # 只覆盖 key 与 base_url，模型名 / json 模式白名单沿用真实配置解析结果。
    # 这样「换供应商只需改 .env」这条路径本身也被测到了，而不是把模型名写死在测试里。
    base_settings = config.get_runtime_settings()
    monkeypatch.setattr(llm_gateway, "get_runtime_settings", lambda: {
        **base_settings,
        "llm_api_key": "test-key",
        "llm_base_url": "https://api.example.invalid",
    })

    def fake_urlopen(req, timeout=None):
        payload = json.loads(req.data.decode("utf-8"))
        user_prompt = payload["messages"][-1]["content"]
        node = _classify(user_prompt)
        calls.append({"node": node, "model": payload["model"],
                      "json_mode": "response_format" in payload,
                      "prompt": user_prompt})
        body = json.dumps({"choices": [{"message": {"content": NODE_RESPONSES[node]}}]})
        return io.BytesIO(body.encode("utf-8"))

    monkeypatch.setattr(llm_gateway.urllib.request, "urlopen", fake_urlopen)

    # 关掉 mock：Orchestrator 内部读 _use_mock()，不接受构造参数注入。
    # 不关的话五个评估节点全走 mock_*，这条链路等于没测。
    monkeypatch.setattr(orchestrator, "_use_mock", lambda: False)

    import core.qcc_api as qcc
    monkeypatch.setattr(qcc, "search_for_financial_qcc_full", lambda info: QCC_PROFILE)

    # 北大法宝出口也换成假响应：保证真实模式链路把「法律可行性接入外部依据」这段
    # 真正跑起来，且不依赖线上 token / 不触发真实网络（否则评测既慢又受环境影响）。
    import core.pkulaw.pkulaw_api as pkulaw_api_mod
    monkeypatch.setattr(pkulaw_api_mod, "_pkulaw_configured", lambda: True)
    monkeypatch.setattr(pkulaw_api_mod, "search_for_rights_foundation",
                        lambda *a, **k: {"laws": [{"title": "保护权利的相关法律规定",
                                                  "content": "相关权利受法律保护，侵权应承担法律责任"}],
                                        "cases": [], "_summary": "测试检索-权利基础"})
    monkeypatch.setattr(pkulaw_api_mod, "search_for_infringement",
                        lambda *a, **k: {"laws": [],
                                        "cases": [{"title": "某知识产权侵权典型案例",
                                                   "court": "高级人民法院",
                                                   "summary": "认定构成侵权并判令停止侵害"}],
                                        "_summary": "测试检索-侵权认定"})
    monkeypatch.setattr(pkulaw_api_mod, "search_for_procedure",
                        lambda *a, **k: {"laws": [{"title": "程序相关法律规定",
                                                  "content": "管辖与诉讼时效依据相关法律规定确定"}],
                                        "cases": [], "_summary": "测试检索-诉讼程序"})

    return calls


# 红线引擎要求 parties 里有原告，否则主体资格检查会把全案 block 掉。
# 早期实现缺这个字段，真实模式下每个案子都被拦（评测报告 P0-1）。
PARTIES = [
    {"role": "plaintiff", "name": "我方权利人公司", "party_type": "法人"},
    {"role": "defendant", "name": "被诉侵权店铺", "party_type": "个体工商户"},
]


def _ctx(**overrides) -> CaseContext:
    kwargs = dict(
        case_id="real-1",
        case_description="原告持有第1234567号注册商标（第25类服装），被告在天猫店铺销售近似标识卫衣，已公证取证。",
        cause_type=CAUSE_TRADEMARK,
        goal_type="要钱",
        parties=list(PARTIES),
    )
    kwargs.update(overrides)
    return CaseContext(**kwargs)


# ============================================================
# 1. 完整主流程（真实代码路径）
# ============================================================

class TestRealModePipeline:
    def test_full_run_produces_a_decision(self, real_llm):
        """
        use_mock=False 跑通全流程：五个 LLM 节点都被真实调用，
        合成出法律分、业务分、决策分，且没有节点处于 failed。
        """
        ctx = _ctx()
        orchestrator.Orchestrator(ctx).run_all()

        assert ctx.scores["final"] is not None, f"未出决策分：{ctx.scores}"
        assert ctx.scores["legal_feasibility"] is not None
        assert ctx.scores["business_expectation"] is not None

        failed = {k: v.get("error") for k, v in ctx.dimension_results.items()
                  if v.get("status") == "failed"}
        assert not failed, f"真实路径下有节点失败：{failed}"

    def test_every_llm_node_was_actually_called(self, real_llm):
        """
        要钱路径会打到五个 LLM 节点。

        证据盘点也在其中——它此前容易被忽略，因为 mock 模式下它和四个评分节点
        一样走 mock_*，看不出自己是 LLM 节点；而真实模式下它一旦解析失败，
        evidence_matrix 为空，整条链路的置信度与缺口清单全塌。
        """
        ctx = _ctx()
        orchestrator.Orchestrator(ctx).run_all()
        nodes = {c["node"] for c in real_llm}
        expected = {"evidence_review", "rights", "infringement", "procedure", "damages"}
        assert nodes == expected, f"要钱路径应调用这五个节点，实际：{nodes}"

    def test_fame_path_calls_precedent_not_damages(self, real_llm):
        """要名路径只调判例价值，不调判赔规模"""
        ctx = _ctx(goal_type="要名")
        orchestrator.Orchestrator(ctx).run_all()
        nodes = {c["node"] for c in real_llm}
        assert "precedent" in nodes and "damages" not in nodes, f"要名路径调用有误：{nodes}"

    def test_evidence_review_output_feeds_the_matrix(self, real_llm):
        """证据盘点结果要真的落到 evidence_matrix 与缺口清单，而不是被丢弃"""
        ctx = _ctx()
        orchestrator.Orchestrator(ctx).run_all()

        assert len(ctx.evidence_matrix) == 9, f"证据矩阵条目数不对：{len(ctx.evidence_matrix)}"
        assert ctx.evidence_completeness == pytest.approx(77.8, abs=0.1)
        assert len(ctx.gap_list) == 3, f"缺口应有 3 项（2 partial + 1 missing）：{ctx.gap_list}"
        assert ctx.extra_evidence, "清单外发现未保留"

    def test_scores_match_the_injected_responses(self, real_llm):
        """端到端核对：注入的分数应原样流到最终评分，中途不得被改写"""
        ctx = _ctx()
        orchestrator.Orchestrator(ctx).run_all()

        assert ctx.dimension_results["rights"]["result"]["score"] == 82
        assert ctx.dimension_results["infringement"]["result"]["score"] == 76
        assert ctx.dimension_results["procedure"]["result"]["score"] == 88
        assert ctx.dimension_results["damages"]["result"]["score"] == 70
        assert ctx.recovery_ability == 68

        # 法律可行性 = M_p(82, 76, 88) × 1.0；业务 = M_p(70, 68)
        legal = scoring.calculate_legal_feasibility(82, 76, 88, 1.0)
        business = scoring.calculate_business_expectation("要钱", 70, 68)
        assert ctx.scores["legal_feasibility"] == pytest.approx(legal, abs=0.1)
        assert ctx.scores["business_expectation"] == pytest.approx(business, abs=0.1)
        assert ctx.scores["final"] == pytest.approx(
            scoring.calculate_overall_score(legal, business), abs=0.1)

    def test_legal_nodes_inject_pkulaw_basis(self, real_llm):
        """法律可行性三节点应接入北大法宝外部依据并随结果返回（问题2 修复）。"""
        ctx = _ctx()
        orchestrator.Orchestrator(ctx).run_all()
        for node in ("rights", "infringement", "procedure"):
            payload = ctx.dimension_results[node]["result"].get("pkulaw")
            assert payload, f"{node} 未注入北大法宝依据"
            assert payload.get("laws") or payload.get("cases"), \
                f"{node} 检索结果为空：{payload}"


# ============================================================
# 2. 模型路由在真实链路上生效
# ============================================================

class TestModelRoutingOnRealPath:
    """
    验的是路由逻辑，不是具体供应商——所以断言对齐运行时解析出的模型名。
    写死 config 常量的话，通过 .env 换供应商时这组会红，但那是切换生效，不是缺陷。
    """

    @staticmethod
    def _model(slot: str) -> str:
        return config.get_runtime_settings()[slot]

    def test_infringement_uses_the_strong_model(self, real_llm):
        ctx = _ctx()
        orchestrator.Orchestrator(ctx).run_all()
        by_node = {c["node"]: c for c in real_llm}
        assert by_node["infringement"]["model"] == self._model("llm_strong_model")
        assert by_node["rights"]["model"] == self._model("llm_fast_model")

    def test_json_mode_only_for_models_that_support_it(self, real_llm):
        """
        json 模式严格跟随 LLM_JSON_MODE_MODELS 白名单：
        白名单外的模型不得下发 response_format（否则真实 API 返回 400），
        白名单内的必须下发（否则退化成文本解析，输出稳定性下降还不报错）。
        """
        ctx = _ctx()
        orchestrator.Orchestrator(ctx).run_all()
        allowed = config.get_runtime_settings()["llm_json_mode_models"]
        for call in real_llm:
            if call["model"] in allowed:
                assert call["json_mode"], (
                    f"{call['node']} 用的 {call['model']} 在白名单内却没开 json 模式")
            else:
                assert not call["json_mode"], (
                    f"{call['node']} 用的 {call['model']} 不在白名单却开了 json 模式")


# ============================================================
# 3. 真实路径下的失败传播
# ============================================================

class TestFailurePropagationOnRealPath:
    def test_unparseable_llm_output_fails_the_node_not_the_score(self, real_llm, monkeypatch):
        """
        某节点返回垃圾：该节点标 failed、决策分不得产出，
        而不是静默补 0 分算出一个「暂不建议起诉」的结论。
        """
        def broken_urlopen(req, timeout=None):
            payload = json.loads(req.data.decode("utf-8"))
            node = _classify(payload["messages"][-1]["content"])
            content = "抱歉，我无法完成此评估。" if node == "infringement" else NODE_RESPONSES[node]
            body = json.dumps({"choices": [{"message": {"content": content}}]})
            return io.BytesIO(body.encode("utf-8"))

        monkeypatch.setattr(llm_gateway.urllib.request, "urlopen", broken_urlopen)
        ctx = _ctx()
        orchestrator.Orchestrator(ctx).run_all()

        infr = ctx.dimension_results["infringement"]
        assert infr["status"] == "failed"
        assert "JSON 解析失败" in (infr.get("error") or "")
        assert ctx.scores["legal_feasibility"] is None, "缺一维却仍算出了法律分"
        assert ctx.scores["final"] is None

    def test_score_out_of_range_fails_the_node(self, real_llm, monkeypatch):
        """模型给出 850 分：应判节点失败，不能让它流进幂平均"""
        def bad_score_urlopen(req, timeout=None):
            payload = json.loads(req.data.decode("utf-8"))
            node = _classify(payload["messages"][-1]["content"])
            content = '{"score": 850, "analysis": "过于乐观"}' if node == "rights" \
                else NODE_RESPONSES[node]
            body = json.dumps({"choices": [{"message": {"content": content}}]})
            return io.BytesIO(body.encode("utf-8"))

        monkeypatch.setattr(llm_gateway.urllib.request, "urlopen", bad_score_urlopen)
        ctx = _ctx()
        orchestrator.Orchestrator(ctx).run_all()

        assert ctx.dimension_results["rights"]["status"] == "failed"
        assert "超出 0-100" in (ctx.dimension_results["rights"].get("error") or "")

    def test_qcc_failure_fails_recovery_dimension(self, real_llm, monkeypatch):
        """企查查挂掉：回款能力标 failed，业务预期因此算不出来"""
        import core.qcc_api as qcc
        monkeypatch.setattr(qcc, "search_for_financial_qcc_full",
                            lambda info: (_ for _ in ()).throw(RuntimeError("企查查 401")))
        ctx = _ctx()
        orchestrator.Orchestrator(ctx).run_all()

        assert ctx.dimension_results["recovery"]["status"] == "failed"
        assert ctx.scores["business_expectation"] is None
        assert any("回款能力" in m for m in
                   ctx.dimension_results["synthesize"]["result"]["missing"])


# ============================================================
# 4. 三案由在真实链路上都能跑
# ============================================================

class TestAllCausesOnRealPath:
    def test_copyright_prompt_carries_the_right_cause(self, real_llm):
        """prompt 里的案由必须随选择变化，否则模型拿到的是错案由"""
        ctx = _ctx(cause_type=CAUSE_COPYRIGHT)
        orchestrator.Orchestrator(ctx).run_all()

        for call in real_llm:
            assert CAUSE_COPYRIGHT in call["prompt"], (
                f"{call['node']} 的 prompt 未带上所选案由")
            assert CAUSE_TRADEMARK not in call["prompt"], (
                f"{call['node']} 的 prompt 串入了商标案由")

    def test_all_three_causes_complete(self, real_llm):
        from core.config import SUPPORTED_CAUSE_TYPES
        for cause in SUPPORTED_CAUSE_TYPES:
            real_llm.clear()
            ctx = _ctx(cause_type=cause)
            orchestrator.Orchestrator(ctx).run_all()
            assert ctx.scores["final"] is not None, f"{cause} 未产出决策分"
            assert ctx.recommendation, f"{cause} 未产出建议"

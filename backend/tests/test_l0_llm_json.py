"""
L0 LLM 输出解析评测（真实模式最大风险点）

Mock 模式下 call_json 根本不会被调用——所有评估节点走 mock_* 直接返回结构化 dict。
也就是说这套 JSON 解析链路在 213 个既有测试里**一次都没被执行过**，
而它恰恰是接真实 key 后最先炸的地方。

本文件用 monkeypatch 伪造 HTTP 响应，覆盖模型常见的各种"不守规矩"输出。
"""
import io
import json
import os
import sys
import urllib.error

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("USE_MOCK", "True")

from core import llm_gateway
from core.config import LLM_FAST_MODEL, LLM_STRONG_MODEL, STRONG_MODEL_NODES


def _fake_response(content: str):
    """伪造 urlopen 返回对象"""
    body = json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8")
    return io.BytesIO(body)


@pytest.fixture
def llm_ready(monkeypatch):
    """让网关认为已配置 key，并把 HTTP 出口换成可注入的假响应"""
    monkeypatch.setattr(llm_gateway, "_settings", lambda: {
        "deepseek_api_key": "test-key",
        "deepseek_base_url": "https://api.example.invalid",
    })
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _fake_response(captured["content"])

    monkeypatch.setattr(llm_gateway.urllib.request, "urlopen", fake_urlopen)

    def respond(content: str):
        captured["content"] = content

    captured["respond"] = respond
    return captured


def _call(captured, content: str, **kw):
    captured["respond"](content)
    return llm_gateway.call_json("sys", "user", **kw)


# ============================================================
# A. 代码块围栏与话术包裹
# ============================================================

class TestJsonExtraction:
    GOOD = '{"score": 80, "analysis": "稳固"}'

    def test_bare_json(self, llm_ready):
        assert _call(llm_ready, self.GOOD)["score"] == 80

    def test_fenced_with_json_tag(self, llm_ready):
        assert _call(llm_ready, f"```json\n{self.GOOD}\n```")["score"] == 80

    def test_fenced_without_lang(self, llm_ready):
        assert _call(llm_ready, f"```\n{self.GOOD}\n```")["score"] == 80

    def test_surrounded_by_preamble_and_postscript(self, llm_ready):
        """
        模型最常见的"不守规矩"：JSON 前后各带一段客套话。
        真实模型几乎必然这么干（"好的，以下是评估结果：…希望对你有帮助"），
        而 _clean_json 只认 startswith("```")，这类输出会被整段判为解析失败。
        """
        content = f"好的，以下是评估结果：\n```json\n{self.GOOD}\n```\n希望对你有帮助！"
        assert _call(llm_ready, content)["score"] == 80

    def test_preamble_without_fence(self, llm_ready):
        """只有前缀话术、没有围栏"""
        assert _call(llm_ready, f"根据案情分析如下：{self.GOOD}")["score"] == 80

    def test_nested_braces_are_not_truncated(self, llm_ready):
        """含嵌套对象的 JSON 不得被截断（朴素的首尾花括号配对会在这里出错）"""
        content = ('```json\n{"score": 80, "elements": '
                   '[{"name": "商标性使用", "status": "满足", "analysis": "成立"}]}\n```')
        result = _call(llm_ready, content)
        assert result["score"] == 80
        assert result["elements"][0]["name"] == "商标性使用"

    def test_unparseable_returns_error_with_raw(self, llm_ready):
        result = _call(llm_ready, "抱歉，我无法完成该评估。")
        assert "error" in result
        assert "raw" in result, "解析失败必须回传原文片段，否则无法定位问题"


# ============================================================
# B. 解析成功但内容不可用（更危险的静默失败）
# ============================================================

class TestStructurallyInvalidPayload:
    """
    JSON 能解析 ≠ 结果可用。模型可能给出合法 JSON 但字段缺失或类型错误，
    此时 "error" not in result 成立、节点被标 ok，而 score 是 None——
    界面显示评估成功，实际这一维根本没分。
    """

    def test_missing_score_is_a_failure(self, llm_ready):
        result = _call(llm_ready, '{"analysis": "分析了一大段"}', node="rights")
        assert "error" in result, "缺 score 字段应判失败，而不是返回 None 分还标 ok"

    def test_string_score_is_a_failure(self, llm_ready):
        result = _call(llm_ready, '{"score": "85分", "analysis": "很好"}', node="rights")
        assert "error" in result, "score 是字符串应判失败"

    def test_out_of_range_score_is_a_failure(self, llm_ready):
        for bad in ('{"score": 850}', '{"score": -20}'):
            assert "error" in _call(llm_ready, bad, node="rights"), f"越界分数未拦下：{bad}"

    def test_score_in_range_passes(self, llm_ready):
        assert _call(llm_ready, '{"score": 0}', node="rights")["score"] == 0
        assert _call(llm_ready, '{"score": 100}', node="rights")["score"] == 100

    def test_numeric_string_that_cannot_coerce(self, llm_ready):
        assert "error" in _call(llm_ready, '{"score": "很高"}', node="rights")

    def test_every_scored_node_is_validated(self):
        """
        凡是 orchestrator 用 score_of() 取分的节点，都必须纳入校验。
        damages / precedent 的 prompt 模板同样要求返回 score，一度漏掉它们——
        业务层的坏分数就能蒙混过关，而业务层杠杆还比法律层高 1.45 倍。
        """
        for n in ("rights", "infringement", "procedure", "damages", "precedent"):
            assert llm_gateway.requires_score(n), f"{n} 产出会直接进评分，必须校验 score"

    def test_nodes_without_score_are_not_validated(self):
        """反向钉死：不经 LLM 或本就不产 score 的节点，不得被校验误伤"""
        for n in ("recovery", "judge", "evidence_review", None):
            assert not llm_gateway.requires_score(n), (
                f"{n} 不经 LLM 产出 score，校验会把它误判为失败")

    def test_damages_payload_with_score_passes(self, llm_ready):
        """damages 既有 score 又有 p10/p50/p90，都应保留"""
        payload = ('{"score": 70, "p10": 10, "p50": 30, "p90": 80, '
                   '"return_multiple": 2.5, "scale_support": "medium"}')
        result = _call(llm_ready, payload, node="damages")
        assert "error" not in result
        assert result["score"] == 70 and result["p50"] == 30

    def test_damages_without_score_is_caught(self, llm_ready):
        """damages 少了 score 同样要判失败（它会被 score_of 取用）"""
        payload = '{"p10": 10, "p50": 30, "p90": 80, "return_multiple": 2.5}'
        assert "error" in _call(llm_ready, payload, node="damages")


# ============================================================
# C. 请求构造与错误可诊断性
# ============================================================

class TestRequestConstruction:
    def test_strong_nodes_get_the_strong_model(self, llm_ready):
        assert STRONG_MODEL_NODES, "强模型节点清单为空，模型路由等于没做"
        _call(llm_ready, '{"score": 80}', node="infringement")
        assert llm_ready["payload"]["model"] == LLM_STRONG_MODEL

    def test_ordinary_nodes_get_the_fast_model(self, llm_ready):
        _call(llm_ready, '{"score": 80}', node="rights")
        assert llm_ready["payload"]["model"] == LLM_FAST_MODEL

    def test_json_mode_is_requested_when_supported(self, llm_ready):
        """能开 json_object 就开，从源头消除格式问题"""
        _call(llm_ready, '{"score": 80}', node="rights")
        assert llm_ready["payload"].get("response_format", {}).get("type") == "json_object"

    def test_json_mode_is_skipped_for_reasoner(self, llm_ready):
        """
        deepseek-reasoner 不接受 response_format，下发会返回 400。
        它正好承担侵权认定与法官归纳两个节点——全局开启会让这两处直接失败。
        """
        assert not llm_gateway.supports_json_mode(LLM_STRONG_MODEL)
        _call(llm_ready, '{"score": 80}', node="infringement")
        assert "response_format" not in llm_ready["payload"], (
            "reasoner 节点不得下发 response_format，否则 API 返回 400")

    def test_reasoner_output_still_parses(self, llm_ready):
        """reasoner 没有 json 模式兜底，更依赖 _clean_json 的提取能力"""
        content = ('让我逐步分析本案…\n\n```json\n'
                   '{"score": 72, "elements": [{"name": "接触", "status": "满足"}]}\n```')
        result = _call(llm_ready, content, node="infringement")
        assert result["score"] == 72

    def test_timeout_is_bounded(self, llm_ready):
        """7 个节点串行，单节点超时过长会让整轮评估卡死"""
        _call(llm_ready, '{"score": 80}')
        assert llm_ready["timeout"] <= 120, f"单节点超时 {llm_ready['timeout']}s 过长"


class TestErrorDiagnosability:
    """401 鉴权失败和 429 限流绝不能长得一样，否则现场排错全靠猜"""

    def test_http_error_carries_status_code(self, monkeypatch, llm_ready):
        monkeypatch.setattr(llm_gateway.urllib.request, "urlopen", _raise_http(401))
        with pytest.raises(llm_gateway.LLMError, match="401"):
            llm_gateway.call_json("sys", "user")

    def test_http_error_carries_response_body(self, monkeypatch, llm_ready):
        """响应体里往往写着真正的原因（余额不足/模型不存在/限流）"""
        monkeypatch.setattr(llm_gateway.urllib.request, "urlopen", _raise_http(429))
        with pytest.raises(llm_gateway.LLMError) as exc:
            llm_gateway.call_json("sys", "user")
        assert "限流" in str(exc.value) or "rate" in str(exc.value).lower()

    def test_missing_key_fails_loudly(self, monkeypatch):
        """未配 key 必须报出缺哪个 key，而不是被底层网络错误盖掉"""
        monkeypatch.setattr(llm_gateway, "get_runtime_settings", lambda: {
            "deepseek_api_key": "", "deepseek_base_url": "https://x"})
        with pytest.raises(llm_gateway.LLMError, match="DEEPSEEK_API_KEY"):
            llm_gateway.call_json("sys", "user")


def _raise_http(code: int, body: str = ""):
    payload = body or {"401": "Invalid API key",
                       "429": "rate limit reached"}.get(str(code), "error")

    def _raise(req, timeout=None):
        raise urllib.error.HTTPError(
            "http://x", code, "err", {}, io.BytesIO(json.dumps(payload).encode()))

    return _raise


# ============================================================
# D. 流式输出
# ============================================================

class TestStreaming:
    SSE = [s.encode("utf-8") for s in [
        'data: {"choices":[{"delta":{"content":"法"}}]}\n\n',
        'data: {"choices":[{"delta":{"content":"官"}}]}\n\n',
        'data: {"choices":[{"delta":{}}]}\n\n',
        'data: [DONE]\n\n',
    ]]

    def test_stream_yields_only_text_deltas(self, monkeypatch, llm_ready):
        monkeypatch.setattr(llm_gateway.urllib.request, "urlopen",
                            lambda req, timeout=None: iter(self.SSE))
        assert "".join(llm_gateway.stream_text("sys", "user")) == "法官"

    def test_stream_tolerates_malformed_lines(self, monkeypatch, llm_ready):
        bad = [s.encode("utf-8") for s in [
            "not a data line\n\n", "data: {broken json\n\n",
            'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n', "data: [DONE]\n\n"]]
        monkeypatch.setattr(llm_gateway.urllib.request, "urlopen",
                            lambda req, timeout=None: iter(bad))
        assert "".join(llm_gateway.stream_text("sys", "user")) == "ok"

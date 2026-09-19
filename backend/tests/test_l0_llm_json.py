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
from core import config
from core.config import LLM_FAST_MODEL, LLM_STRONG_MODEL, STRONG_MODEL_NODES


def _fake_response(content: str, finish_reason: str = "stop"):
    """伪造 urlopen 返回对象。

    finish_reason 必须能指定：它是区分「模型不会写 JSON」与「额度不够没写完」
    的唯一证据，而截断重试这条路径完全靠它驱动。
    """
    body = json.dumps({
        "choices": [{"message": {"content": content},
                     "finish_reason": finish_reason}]
    }).encode("utf-8")
    return io.BytesIO(body)


@pytest.fixture
def llm_ready(monkeypatch):
    """让网关认为已配置 key，并把 HTTP 出口换成可注入的假响应"""
    # 派生自真实配置，只覆盖出口地址。写全量的话，以后每加一个配置项
    # 都要回来改一遍，忘了就是 KeyError——而这类失败跟被测逻辑毫无关系。
    monkeypatch.setattr(llm_gateway, "_settings", lambda: {
        **config.get_runtime_settings(),
        "llm_api_key": "test-key",
        "llm_base_url": "https://api.example.invalid",
    })
    captured = {"requests": []}

    def fake_urlopen(req, timeout=None):
        payload = json.loads(req.data.decode("utf-8"))
        captured["requests"].append(payload)
        captured["payload"] = payload
        captured["timeout"] = timeout
        # 支持按调用次序给出不同响应：截断重试要先给一个断的、再给一个完整的
        sequence = captured.get("sequence")
        if sequence:
            content, reason = sequence.pop(0)
        else:
            content = captured.get("content", "")
            reason = captured.get("finish_reason", "stop")
        return _fake_response(content, reason)

    monkeypatch.setattr(llm_gateway.urllib.request, "urlopen", fake_urlopen)

    def respond(content: str, finish_reason: str = "stop"):
        captured["content"] = content
        captured["finish_reason"] = finish_reason

    def respond_sequence(items):
        """按调用次序给响应：[(内容, finish_reason), ...]"""
        captured["sequence"] = list(items)

    captured["respond"] = respond
    captured["respond_sequence"] = respond_sequence
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
    """
    断言一律对齐「运行时解析出的模型名」而不是 config 里的模块常量。

    写死常量的话，一旦通过 .env 换成别的供应商，这组用例会红——
    但那不是缺陷，是切换生效了。测试要验的是「路由逻辑对不对」，
    不该顺带把供应商钉死。
    """

    @staticmethod
    def _model(slot: str) -> str:
        return config.get_runtime_settings()[slot]

    def test_strong_json_nodes_resolve_to_json_capable_model(self, llm_ready):
        """强模型节点（infringement/judge）是 JSON 节点，必须落到「支持 json 模式」的模型，
        否则推理模型返回散文导致解析失败。强模型若不支持 json 模式，call_json 会兜底退回白名单模型。"""
        assert STRONG_MODEL_NODES, "强模型节点清单为空，模型路由等于没做"
        strong = self._model("llm_strong_model")
        _call(llm_ready, '{"score": 80}', node="infringement")
        model_used = llm_ready["payload"]["model"]
        assert llm_gateway.supports_json_mode(model_used), (
            f"JSON 节点不得裸奔：强模型 {strong} 不支持 json 模式时，"
            f"call_json 必须兜底到支持 json 的模型，实际下发了 {model_used}")
        assert llm_ready["payload"].get("response_format", {}).get("type") == "json_object"

    def test_ordinary_nodes_get_the_fast_model(self, llm_ready):
        _call(llm_ready, '{"score": 80}', node="rights")
        assert llm_ready["payload"]["model"] == self._model("llm_fast_model")

    def test_json_mode_is_requested_when_supported(self, llm_ready):
        """能开 json_object 就开，从源头消除格式问题"""
        _call(llm_ready, '{"score": 80}', node="rights")
        assert llm_ready["payload"].get("response_format", {}).get("type") == "json_object"

    def test_json_node_never_sends_response_format_to_rejecting_strong_model(self, llm_ready):
        """强模型（如 kimi-k3 / deepseek-reasoner）不支持 response_format，下发会 400；
        但 JSON 节点不能因此裸奔——call_json 应兜底改用白名单里的 json 模型：既不开给
        不支持的强模型（避免 400），又保证节点拿到 json 强制（避免返回散文）。"""
        strong = self._model("llm_strong_model")
        if llm_gateway.supports_json_mode(strong):
            pytest.skip(f"当前供应商的强模型 {strong} 支持 json 模式，本例不适用")
        _call(llm_ready, '{"score": 80}', node="infringement")
        assert llm_ready["payload"]["model"] != strong, (
            f"不得把 response_format 下发给不支持 json 的强模型 {strong}")
        assert llm_ready["payload"].get("response_format", {}).get("type") == "json_object", (
            "兜底模型必须带 json 模式，否则等于退回散文裸奔")

    def test_reasoner_output_still_parses(self, llm_ready):
        """即便模型把 JSON 包在围栏/话术里，_clean_json 也要能剥出主体
        （强模型被兜底成白名单 json 模型后仍可能夹带客套话）。"""
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
            **config.get_runtime_settings(),
            "llm_api_key": "", "llm_base_url": "https://x"})
        with pytest.raises(llm_gateway.LLMError, match="LLM_API_KEY"):
            llm_gateway.call_json("sys", "user")

    def test_missing_key_reports_where_requests_would_go(self, monkeypatch):
        """
        换供应商后最常见的失败：填了新 key 却没改 base_url，请求照旧发往上一家，
        拿到 401 还以为是 key 错了。报错必须回显 base_url。
        """
        monkeypatch.setattr(llm_gateway, "get_runtime_settings", lambda: {
            **config.get_runtime_settings(),
            "llm_api_key": "", "llm_base_url": "https://api.moonshot.ai/v1",
            "llm_provider": "kimi"})
        with pytest.raises(llm_gateway.LLMError) as exc:
            llm_gateway.call_json("sys", "user")
        assert "moonshot" in str(exc.value)


class TestProviderSwap:
    """换 LLM 供应商只改 .env，不该动代码"""

    KIMI_ENV = {
        "LLM_PROVIDER": "kimi",
        "LLM_API_KEY": "sk-kimi-test",
        "LLM_BASE_URL": "https://api.moonshot.ai/v1",
        "LLM_STRONG_MODEL": "kimi-k3",
        "LLM_FAST_MODEL": "kimi-k2.6",
        "LLM_JSON_MODE_MODELS": "kimi-k2.6",
        "LLM_MAX_TOKENS_PARAM": "max_completion_tokens",
    }

    def test_length_field_name_is_configurable(self, monkeypatch, llm_ready):
        """
        长度上限的字段名各厂商不统一：Kimi 已把 max_tokens 标为弃用。
        填错的后果是静默的——Kimi 回落到默认 131072，而它的限流按这个值预扣
        额度，低额度账号会莫名其妙 429。所以字段名必须可配，不能硬编码。
        """
        for key, value in self.KIMI_ENV.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setattr(llm_gateway, "_settings", config.get_runtime_settings)

        _call(llm_ready, '{"score": 80}', node="rights")
        payload = llm_ready["payload"]
        assert "max_completion_tokens" in payload
        assert "max_tokens" not in payload

    def test_default_length_field_stays_max_tokens(self, monkeypatch, llm_ready):
        """不配置时用 max_tokens，DeepSeek / OpenAI 认这个，别把默认改坏"""
        monkeypatch.delenv("LLM_MAX_TOKENS_PARAM", raising=False)
        monkeypatch.setattr(llm_gateway, "_settings", config.get_runtime_settings)

        _call(llm_ready, '{"score": 80}', node="rights")
        assert (llm_ready["payload"]["max_tokens"]
                == llm_gateway.DEFAULT_JSON_MAX_TOKENS)

    def test_every_llm_setting_is_env_driven(self, monkeypatch):
        """这五个键少一个可配，换供应商就得改源码——那就不是配置驱动了"""
        for key, value in self.KIMI_ENV.items():
            monkeypatch.setenv(key, value)
        s = config.get_runtime_settings()
        assert s["llm_api_key"] == "sk-kimi-test"
        assert s["llm_base_url"] == "https://api.moonshot.ai/v1"
        assert s["llm_strong_model"] == "kimi-k3"
        assert s["llm_fast_model"] == "kimi-k2.6"
        assert s["llm_json_mode_models"] == {"kimi-k2.6"}
        assert s["llm_provider"] == "kimi"

    def test_routing_and_json_mode_follow_the_new_models(self, monkeypatch):
        """
        最容易漏的一环：模型名换了，但 json 模式白名单还写着旧模型名，
        于是所有节点静默降级成文本解析——不报错，只是输出越来越不稳。
        """
        for key, value in self.KIMI_ENV.items():
            monkeypatch.setenv(key, value)

        assert llm_gateway.pick_model("infringement") == "kimi-k3"
        assert llm_gateway.pick_model("rights") == "kimi-k2.6"
        assert llm_gateway.supports_json_mode("kimi-k2.6")
        assert not llm_gateway.supports_json_mode("kimi-k3")

    def test_requests_go_to_the_new_endpoint_with_the_new_key(self, monkeypatch, llm_ready):
        """换供应商后请求必须打到新地址、带新 key、用新模型名"""
        for key, value in self.KIMI_ENV.items():
            monkeypatch.setenv(key, value)

        captured = {}
        monkeypatch.setattr(llm_gateway, "_settings", config.get_runtime_settings)

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["auth"] = req.get_header("Authorization")
            captured["payload"] = json.loads(req.data.decode("utf-8"))
            return _fake_response('{"score": 80}')

        monkeypatch.setattr(llm_gateway.urllib.request, "urlopen", fake_urlopen)
        llm_gateway.call_json("sys", "user", node="rights")

        assert captured["url"].startswith("https://api.moonshot.ai/v1")
        assert captured["auth"] == "Bearer sk-kimi-test"
        assert captured["payload"]["model"] == "kimi-k2.6"

    def test_legacy_deepseek_env_still_works(self, monkeypatch):
        """已有 .env 只写了 DEEPSEEK_*，升级后不能要求用户改文件"""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-old")
        monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.delenv("LLM_BASE_URL", raising=False)

        s = config.get_runtime_settings()
        assert s["llm_api_key"] == "sk-old"
        assert s["llm_base_url"] == "https://api.deepseek.com"
        # 旧别名仍指向同一份值，既有脚本不会拿到 None
        assert s["deepseek_api_key"] == s["llm_api_key"]

    def test_new_keys_win_over_legacy(self, monkeypatch):
        """两套都填时以 LLM_* 为准，否则用户改了却没生效，最难排查"""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-old")
        monkeypatch.setenv("LLM_API_KEY", "sk-new")
        s = config.get_runtime_settings()
        assert s["llm_api_key"] == "sk-new"


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


# ============================================================
# E. JSON 节点的模型兜底（强模型不支持 json 模式时退回白名单模型）
# ============================================================

class TestJsonModelFallback:
    """修复：node 首选模型不在 json 白名单内时，call_json 必须改用白名单模型，
    否则推理模型返回散文导致 JSON 解析失败（法官归纳 / 侵权认定双双为空）。

    复现场景即对应用户真实配置：强模型（kimi-k3 / deepseek-reasoner）被刻意排除在
    JSON_MODE_MODELS 之外（下发 json 模式会 400），而 judge / infringement 两个
    JSON 节点又走强模型——没有 API 级 json 强制，模型返回中文散文而非 JSON。
    """

    def _nonjson_strong_settings(self, monkeypatch):
        """构造一个「强模型不支持 json 模式」的运行配置。
        直接 monkeypatch get_runtime_settings——pick_model / supports_json_mode /
        call_json 的兜底都读它（而非 _settings）。"""
        settings = {
            **config.get_runtime_settings(),
            "llm_api_key": "test-key",
            "llm_base_url": "https://api.example.invalid",
            "llm_strong_model": "reasoner-x",   # 故意不在白名单内
            "llm_fast_model": "chat-x",
            "llm_json_mode_models": {"chat-x"},
        }
        monkeypatch.setattr(llm_gateway, "get_runtime_settings", lambda: settings)
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["payload"] = json.loads(req.data.decode("utf-8"))
            return _fake_response(captured["content"])

        monkeypatch.setattr(llm_gateway.urllib.request, "urlopen", fake_urlopen)
        return captured

    def test_judge_falls_back_to_json_model(self, monkeypatch):
        cap = self._nonjson_strong_settings(monkeypatch)
        cap["content"] = '{"focus_points": ["争议焦点"], "summary": "归纳"}'
        result = llm_gateway.call_json("sys", "user", node="judge", temperature=0.2)
        assert result.get("focus_points") == ["争议焦点"]
        # 关键断言：实际下发的是白名单内的 json 模型，而非 reasoner-x
        assert cap["payload"]["model"] == "chat-x"
        assert cap["payload"].get("response_format") == {"type": "json_object"}

    def test_infringement_falls_back_to_json_model(self, monkeypatch):
        cap = self._nonjson_strong_settings(monkeypatch)
        cap["content"] = '{"score": 80}'
        result = llm_gateway.call_json("sys", "user", node="infringement")
        assert result.get("score") == 80
        assert cap["payload"]["model"] == "chat-x"
        assert cap["payload"].get("response_format") == {"type": "json_object"}

    def test_plain_node_keeps_json_model(self, monkeypatch):
        """非强模型节点本就用白名单模型，兜底不应改变其行为"""
        cap = self._nonjson_strong_settings(monkeypatch)
        cap["content"] = '{"score": 60}'
        llm_gateway.call_json("sys", "user", node="rights")
        assert cap["payload"]["model"] == "chat-x"
        assert cap["payload"].get("response_format") == {"type": "json_object"}


# ============================================================
# F. 输出预算：被 max_tokens 截断 ≠ 模型不会写 JSON
# ============================================================

class TestJsonOutputBudget:
    """
    法官归纳实测自然长度约 4600 tokens（system prompt 注入「可引用依据」块后更长）。
    预算低于它时输出会在字符串中间断开，json.loads 报 "Unterminated string"，
    而旧实现的报错只有一句「JSON 解析失败」——指向格式，真实原因却是额度。
    库里 4 场真实庭审的法官归纳因此全部失败，且一直被当成格式问题排查。
    """

    def test_default_budget_leaves_room_for_the_judge_summary(self):
        # 4608 是实测值，再留一档余量给更啰嗦的案子
        assert llm_gateway.DEFAULT_JSON_MAX_TOKENS >= 8000

    def test_retry_ceiling_stays_within_deepseek_limit(self):
        assert llm_gateway.JSON_MAX_TOKENS_CEILING <= 8192


class TestTruncationRetry:
    """finish_reason=length 时放大预算重试一次——原样重发只会再断一次"""

    GOOD = '{"score": 80, "analysis": "稳固"}'

    def test_truncated_then_complete_is_retried_and_parsed(self, llm_ready):
        llm_ready["respond_sequence"]([
            (self.GOOD[:-3], "length"),   # 写到一半被掐断
            (self.GOOD, "stop"),
        ])
        result = llm_gateway.call_json("sys", "user", node="rights", max_tokens=4000)
        assert result["score"] == 80
        budgets = [p["max_tokens"] for p in llm_ready["requests"]]
        assert len(budgets) == 2, "截断必须触发重试"
        assert budgets[1] > budgets[0], "重试必须放大预算，否则只是再断一次"

    def test_complete_output_does_not_cost_a_second_request(self, llm_ready):
        llm_ready["respond"](self.GOOD)
        llm_gateway.call_json("sys", "user", node="rights", max_tokens=4000)
        assert len(llm_ready["requests"]) == 1

    def test_still_truncated_reports_length_not_format(self, llm_ready):
        """重试后仍失败：报错必须带 finish_reason=length，否则又会误判成格式问题"""
        llm_ready["respond_sequence"]([
            (self.GOOD[:-3], "length"),
            (self.GOOD[:-3], "length"),
        ])
        result = llm_gateway.call_json("sys", "user", node="rights", max_tokens=4000)
        assert "error" in result
        assert result["finish_reason"] == "length"
        assert result["model"], "报错必须带上实际打的模型，否则无从判断是谁在截断"

    def test_failed_retry_falls_back_to_the_first_response(self, monkeypatch, llm_ready):
        """重试请求本身失败（供应商上限更低 → 400）时，不能把可诊断的失败顶掉"""
        calls = []

        def flaky_urlopen(req, timeout=None):
            payload = json.loads(req.data.decode("utf-8"))
            calls.append(payload)
            if len(calls) == 1:
                return _fake_response(self.GOOD[:-3], "length")
            raise urllib.error.HTTPError(
                "http://x", 400, "err", {},
                io.BytesIO(json.dumps("max_tokens too large").encode()))

        monkeypatch.setattr(llm_gateway.urllib.request, "urlopen", flaky_urlopen)
        result = llm_gateway.call_json("sys", "user", node="rights", max_tokens=4000)
        assert "error" in result
        assert result["finish_reason"] == "length"
        assert len(calls) == 2

    def test_error_keeps_enough_raw_to_locate_the_break(self, llm_ready):
        """raw 只留 500 字时，法官归纳在 7000 字处断开是根本看不见的"""
        broken = '{"score": 80, "analysis": "' + "很长的分析" * 500
        result = _call(llm_ready, broken)
        assert "error" in result
        assert llm_gateway.RAW_SNIPPET_LIMIT > 500
        assert len(result["raw"]) == llm_gateway.RAW_SNIPPET_LIMIT

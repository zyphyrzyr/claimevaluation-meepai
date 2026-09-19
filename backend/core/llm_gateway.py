"""
统一 LLM 网关（合并旧代码 llm_client / moot_court.agents 三处重复实现）
- 模型路由：强模型（法官归纳/侵权认定）vs 普通模型（其余节点），见 config.STRONG_MODEL_NODES
- call_json：强制 JSON 结构化输出 + markdown 清理 + 解析兜底
- call_text：纯文本生成（庭审发言等）
- stream_text：SSE 流式生成（庭审直播、追问逐字输出），P2 起用
- Mock 模式：委托 core.mock 模块，离线演示兜底
"""

import json
import urllib.request
import urllib.error
from typing import Any, Dict, Generator, Optional

from .config import (get_runtime_settings, STRONG_MODEL_NODES,
                     LLM_TIMEOUT_SECONDS)


class LLMError(RuntimeError):
    pass


def _settings() -> dict:
    s = get_runtime_settings()
    if not s["llm_api_key"]:
        # 点名 LLM_API_KEY 并回显当前 base_url：换供应商后最常见的失败就是
        # 填了新 key 却忘了改 base_url，请求照旧发往上一家，报 401 还查不出原因。
        raise LLMError(
            f"未配置 LLM_API_KEY（当前 provider={s['llm_provider']}，"
            f"base_url={s['llm_base_url']}），真实模式无法调用 LLM（可切 USE_MOCK=True）"
        )
    return s


def pick_model(node: Optional[str] = None) -> str:
    """
    模型路由：按节点选择强/普通模型。

    模型名从运行时配置取而不是用模块常量，这样换供应商只改 .env 即可。
    """
    s = get_runtime_settings()
    return s["llm_strong_model"] if node in STRONG_MODEL_NODES else s["llm_fast_model"]


def supports_json_mode(model: str) -> bool:
    """
    该模型是否接受 response_format=json_object。

    这是白名单而不是黑名单：不同厂商、不同型号对 json 模式的支持并不一致
    （deepseek-reasoner、部分推理模型下发该参数会直接返回 400），而它恰好承担
    侵权认定与法官归纳两个节点——全局开启 json 模式会让这两个节点直接失败。
    换供应商时必须同步 LLM_JSON_MODE_MODELS，否则等于静默关掉 json 模式。
    """
    s = get_runtime_settings()
    return model in s["llm_json_mode_models"]


def _post_chat(messages: list, model: str, temperature: float, max_tokens: int,
               stream: bool = False, json_mode: bool = False) -> Dict[str, Any]:
    s = _settings()
    # 供应商预设的 base_url 约定不一致：deepseek 不带 /v1，moonshot/openai 已带 /v1。
    # 这里先归一化掉末尾的 /v1 再统一拼接 /v1/chat/completions，避免拼成 …/v1/v1/… 报 404。
    base = s['llm_base_url'].rstrip('/')
    if base.endswith('/v1'):
        base = base[:-3]
    url = f"{base}/v1/chat/completions"
    # 长度上限的字段名各厂商不统一：OpenAI/DeepSeek 用 max_tokens，Kimi 已把
    # 它标为弃用并要求改用 max_completion_tokens。这个差异不能硬编码——
    # 填错不会报错，只会让 Kimi 回落到默认的 131072，而它的限流是按这个值
    # 预扣额度的，低额度账号会莫名其妙 429。故做成配置项，换供应商时一并改。
    payload: Dict[str, Any] = {
        "model": model,
        s["llm_max_tokens_param"]: max_tokens,
        "temperature": temperature,
        "stream": stream,
        "messages": messages,
    }
    if json_mode and supports_json_mode(model):
        payload["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), method="POST")
    req.add_header("Authorization", f"Bearer {s['llm_api_key']}")
    req.add_header("Content-Type", "application/json")
    try:
        return urllib.request.urlopen(req, timeout=LLM_TIMEOUT_SECONDS)
    except urllib.error.HTTPError as e:
        # 带上响应体：401 鉴权失败、402 余额不足、429 限流、400 参数错误，
        # 光看状态码分不清，而现场排错最需要的就是这一句。
        try:
            body = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            body = ""
        raise LLMError(f"LLM API 错误 ({e.code})：{body or e.reason}")
    except Exception as e:
        raise LLMError(f"LLM 调用失败: {e}")


def ping(max_tokens: int = 16) -> Dict[str, Any]:
    """
    连通性自检：发一个最小请求，确认「key 有效 + 地址可达 + 模型名存在」。

    换供应商后这一步能一次性排掉三类故障：key 拿错（401）、base_url 还指着
    上一家（401/404）、模型名已下线（404）。这三类的表面症状都是「调不通」，
    但根因完全不同，靠猜要很久。

    失败返回 ok=False 而不抛异常：自检是给人看的，抛出去只会在界面上变成一个
    红色 toast，把有用的诊断信息吃掉。
    """
    import time

    started = time.time()
    try:
        model = pick_model(None)
        text = call_text("你是连通性自检助手。", "只回复两个字：正常",
                         temperature=0, max_tokens=max_tokens)
    except LLMError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:                      # 解析失败等，同样不该抛
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {
        "ok": True,
        "model": model,
        "latency_ms": int((time.time() - started) * 1000),
        "reply": (text or "")[:50],
    }


def _clean_json(raw: str) -> str:
    """
    从模型输出里剥出 JSON 主体。

    原实现只认 startswith("```")，而真实模型几乎必然在 JSON 前后加客套话
    （"好的，以下是评估结果：…希望对你有帮助"），这类输出会被整段判为解析失败。
    这里改为：先按围栏截取，失败则用花括号配对扫描出第一个完整 JSON 对象——
    朴素的首尾配对会在含嵌套对象时截断，所以要逐字符计数并跳过字符串内的括号。
    """
    text = (raw or "").strip()

    fenced = _strip_fence(text)
    if fenced is not None:
        text = fenced

    if text.startswith("{"):
        candidate = _balanced_object(text)
        if candidate is not None:
            return candidate

    # 输出前后有话术：从第一个 { 开始做括号配对
    start = text.find("{")
    if start != -1:
        candidate = _balanced_object(text[start:])
        if candidate is not None:
            return candidate

    return text


def _strip_fence(text: str) -> Optional[str]:
    """剥掉 ```json ... ``` 围栏，返回 None 表示没有围栏"""
    if not text.startswith("```"):
        return None
    body = text[3:]
    if body.startswith("json"):
        body = body[4:]
    end = body.rfind("```")
    if end != -1:
        body = body[:end]
    return body.strip() or None


def _balanced_object(text: str) -> Optional[str]:
    """从 text 开头起，扫描出第一个括号配对的 JSON 对象；跳过字符串内的括号与转义"""
    depth, in_string, escaped = 0, False, False
    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[:i + 1]
    return None


def call_text(system_prompt: str, user_prompt: str, *,
              node: Optional[str] = None, temperature: float = 0.3,
              max_tokens: int = 2000) -> str:
    """纯文本生成（庭审发言、分析叙述）"""
    resp = _post_chat(
        [{"role": "system", "content": system_prompt},
         {"role": "user", "content": user_prompt}],
        pick_model(node), temperature, max_tokens,
    )
    result = json.loads(resp.read().decode("utf-8"))
    return result["choices"][0]["message"]["content"].strip()


# 这些节点的产出会被 score_of() 取 result["score"] 后直接喂进评分链路，
# 因此必须有 score 且必须是 0-100 的合法数值。
# damages / precedent 虽属业务层，但它们的 prompt 模板同样要求返回 score，
# 且 orchestrator 就是用 score_of("damages") / score_of("precedent") 取值——
# 一度只把法律三维度列进来，业务两维的坏分数就会蒙混过关。
# recovery 不在其中：它由企查查规则算出来，不经 LLM。judge 输出的是修正系数而非分数。
SCORE_REQUIRED_NODES = {"rights", "infringement", "procedure", "damages", "precedent"}


def requires_score(node: Optional[str]) -> bool:
    """该节点是否必须产出一个 0-100 的 score"""
    return node in SCORE_REQUIRED_NODES


def _validate_payload(payload: Dict[str, Any], node: Optional[str]) -> Optional[str]:
    """
    校验解析成功的 JSON 是否真的可用。

    JSON 能解析 ≠ 结果可用。模型完全可能返回合法 JSON 但 score 缺失、是字符串、
    或超出 0-100。这种情况下 "error" not in result 成立、节点被标 ok，
    而 score_of 拿到 None 走 missing 分支——界面显示评估成功，实际这一维没分。
    这是比解析失败更危险的静默失败，因为连报错都没有。
    """
    if not requires_score(node):
        return None
    if "score" not in payload:
        return "返回结构缺少 score 字段"
    score = payload["score"]
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return f"score 不是数值：{score!r}"
    if not (0 <= score <= 100):
        return f"score 超出 0-100 范围：{score}"
    return None


# ── JSON 节点的输出预算 ────────────────────────────────────
# 法官归纳实测自然长度约 4600 tokens（system prompt 注入「可引用依据」块后更长）。
# 原来的默认 4000 会在输出中途截断：finish_reason=length，JSON 断在字符串中间，
# json.loads 报 "Unterminated string"，而 call_json 只回一句「JSON 解析失败」——
# 报错文案指向格式，真实原因却是额度，这个错位让排查绕了很久（库里 4 场真实
# 庭审的法官归纳因此全部失败）。
# 8000 取 deepseek-chat 的 8192 上限留一点余量。其余节点实际用不到 4000，
# 放宽默认值不会让它们真的多花钱：max_tokens 是上限，不是预扣。
DEFAULT_JSON_MAX_TOKENS = 8000

# 撞上上限时重试的倍数与天花板。天花板是必需的：各供应商单次输出上限不一致
# （deepseek-chat 是 8192，别家可能更低），无上限翻倍会直接 400，
# 把一次「可诊断的截断」变成一个新异常，比不重试还糟。
JSON_TRUNCATION_RETRY_FACTOR = 2
JSON_MAX_TOKENS_CEILING = 8192

# 报错时回显的原始输出长度。原来是 500，而法官归纳的截断位置在 7000 字开外，
# 500 字之后断在哪永远看不到，现场无法自证。
RAW_SNIPPET_LIMIT = 2000


def _json_attempt(system_prompt: str, user_prompt: str, model: str,
                  temperature: float, max_tokens: int):
    """
    发一次 JSON 请求，返回 (finish_reason, 原始输出)。

    单独抽出来是因为截断重试要再发一次，而 HTTPResponse 只能读一次——
    不抽函数就得把请求体的构造复制两遍。
    finish_reason 必须一起带回来：它是区分「模型不会写 JSON」和
    「额度不够没写完」的唯一证据，丢了就只能靠猜（见 DEFAULT_JSON_MAX_TOKENS）。
    """
    resp = _post_chat(
        [{"role": "system", "content": system_prompt},
         {"role": "user", "content": user_prompt}],
        model, temperature, max_tokens, json_mode=True,
    )
    body = json.loads(resp.read().decode("utf-8"))
    choice = body["choices"][0]
    return choice.get("finish_reason"), (choice["message"]["content"] or "")


def _json_error(problem: str, raw: str, finish_reason, model: str) -> Dict[str, Any]:
    """
    统一的失败返回。除 error / raw 外固定带上 finish_reason 与 model：
    没有这两个字段，界面上就只有一句「JSON 解析失败」，既分不清是格式问题
    还是额度问题，也不知道当时打的是哪个模型。
    """
    return {"error": problem,
            "raw": raw[:RAW_SNIPPET_LIMIT],
            "finish_reason": finish_reason,
            "model": model}


def call_json(system_prompt: str, user_prompt: str, *,
              node: Optional[str] = None, temperature: float = 0.2,
              max_tokens: int = DEFAULT_JSON_MAX_TOKENS) -> Dict[str, Any]:
    """
    结构化 JSON 输出（评估节点、法官归纳）。

    失败一律返回含 "error" 的 dict 而不抛异常——编排器据此把节点标 failed 并让
    下游失效，而不是让整轮评估中断。调用方（orchestrator._run_llm_node 等）
    通过 "error" in result 判定，不要改成抛异常。

    输出被 max_tokens 掐断时（finish_reason="length"）会放大预算自动重试一次，
    因为那不是模型不会写 JSON，原样重发只会再断一次。
    """
    model = pick_model(node)
    # JSON 节点必须走「支持 json 模式」的模型，否则推理模型（如 deepseek-reasoner）
    # 在没有 response_format=json_object 强制时，会返回散文而非 JSON，导致解析失败
    # （详见 config.py 对 JSON_MODE_MODELS 的说明）。当节点首选模型不在白名单内时，
    # 退回白名单中的模型再下发 json 模式——一处兜底同时覆盖 judge / infringement 等
    # 所有 JSON 节点，且不波及走 call_text 的纯文本节点（开庭陈述、法庭辩论等）。
    if not supports_json_mode(model):
        whitelist = get_runtime_settings().get("llm_json_mode_models") or set()
        fallback = next(iter(whitelist), None)
        if fallback:
            model = fallback

    finish_reason, content = _json_attempt(
        system_prompt, user_prompt, model, temperature, max_tokens)

    retry_budget = min(max_tokens * JSON_TRUNCATION_RETRY_FACTOR,
                       JSON_MAX_TOKENS_CEILING)
    if finish_reason == "length" and retry_budget > max_tokens:
        # 输出没写完就被掐断。放大预算再要一次；重试本身失败（供应商上限比我们
        # 给的额度更低，会 400）就沿用第一次的结果——兜底动作不能把一次
        # 本来可诊断的失败顶掉，那比不重试更糟。
        first_reason, first_content = finish_reason, content
        try:
            finish_reason, content = _json_attempt(
                system_prompt, user_prompt, model, temperature, retry_budget)
        except LLMError:
            finish_reason, content = first_reason, first_content

    raw = _clean_json(content)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return _json_error("JSON 解析失败", raw, finish_reason, model)

    if not isinstance(payload, dict):
        return _json_error(f"返回结构不是 JSON 对象：{type(payload).__name__}",
                           raw, finish_reason, model)

    problem = _validate_payload(payload, node)
    if problem:
        return _json_error(problem, raw, finish_reason, model)
    return payload


def stream_text(system_prompt: str, user_prompt: str, *,
                node: Optional[str] = None, temperature: float = 0.3,
                max_tokens: int = 2000) -> Generator[str, None, None]:
    """SSE 流式生成，逐段 yield 文本增量（庭审直播/追问）"""
    resp = _post_chat(
        [{"role": "system", "content": system_prompt},
         {"role": "user", "content": user_prompt}],
        pick_model(node), temperature, max_tokens, stream=True,
    )
    for raw_line in resp:
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            chunk = json.loads(data)
            delta = chunk["choices"][0].get("delta", {}).get("content", "")
            if delta:
                yield delta
        except (json.JSONDecodeError, KeyError, IndexError):
            continue

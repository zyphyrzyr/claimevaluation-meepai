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

from .config import (get_runtime_settings, LLM_STRONG_MODEL, LLM_FAST_MODEL,
                     STRONG_MODEL_NODES, LLM_TIMEOUT_SECONDS, JSON_MODE_MODELS)


class LLMError(RuntimeError):
    pass


def _settings() -> dict:
    s = get_runtime_settings()
    if not s["deepseek_api_key"]:
        raise LLMError("未配置 DEEPSEEK_API_KEY，真实模式无法调用 LLM（可切 USE_MOCK=True）")
    return s


def pick_model(node: Optional[str] = None) -> str:
    """模型路由：按节点选择强/普通模型"""
    return LLM_STRONG_MODEL if node in STRONG_MODEL_NODES else LLM_FAST_MODEL


def supports_json_mode(model: str) -> bool:
    """
    该模型是否接受 response_format=json_object。

    deepseek-reasoner 不支持这个参数，下发会返回 400。而它恰好承担侵权认定与
    法官归纳两个节点——全局开启 json 模式会让这两个节点直接失败。
    """
    return model in JSON_MODE_MODELS


def _post_chat(messages: list, model: str, temperature: float, max_tokens: int,
               stream: bool = False, json_mode: bool = False) -> Dict[str, Any]:
    s = _settings()
    url = f"{s['deepseek_base_url'].rstrip('/')}/v1/chat/completions"
    payload: Dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": stream,
        "messages": messages,
    }
    if json_mode and supports_json_mode(model):
        payload["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), method="POST")
    req.add_header("Authorization", f"Bearer {s['deepseek_api_key']}")
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


def call_json(system_prompt: str, user_prompt: str, *,
              node: Optional[str] = None, temperature: float = 0.2,
              max_tokens: int = 4000) -> Dict[str, Any]:
    """
    结构化 JSON 输出（评估节点、法官归纳）。

    失败一律返回含 "error" 的 dict 而不抛异常——编排器据此把节点标 failed 并让
    下游失效，而不是让整轮评估中断。调用方（orchestrator._run_llm_node 等）
    通过 "error" in result 判定，不要改成抛异常。
    """
    resp = _post_chat(
        [{"role": "system", "content": system_prompt},
         {"role": "user", "content": user_prompt}],
        pick_model(node), temperature, max_tokens, json_mode=True,
    )
    result = json.loads(resp.read().decode("utf-8"))
    raw = _clean_json(result["choices"][0]["message"]["content"])
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {"error": "JSON 解析失败", "raw": raw[:500]}

    if not isinstance(payload, dict):
        return {"error": f"返回结构不是 JSON 对象：{type(payload).__name__}", "raw": raw[:500]}

    problem = _validate_payload(payload, node)
    if problem:
        return {"error": problem, "raw": raw[:500]}
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

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

from .config import get_runtime_settings, LLM_STRONG_MODEL, LLM_FAST_MODEL, STRONG_MODEL_NODES


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


def _post_chat(messages: list, model: str, temperature: float, max_tokens: int,
               stream: bool = False) -> Dict[str, Any]:
    s = _settings()
    url = f"{s['deepseek_base_url'].rstrip('/')}/v1/chat/completions"
    payload = json.dumps({
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": stream,
        "messages": messages,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Authorization", f"Bearer {s['deepseek_api_key']}")
    req.add_header("Content-Type", "application/json")
    try:
        resp = urllib.request.urlopen(req, timeout=180)
        return resp
    except urllib.error.HTTPError as e:
        raise LLMError(f"LLM API 错误 ({e.code})")
    except Exception as e:
        raise LLMError(f"LLM 调用失败: {e}")


def _clean_json(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```json"):
        raw = raw.split("\n", 1)[1]
    elif raw.startswith("```"):
        raw = raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3]
    return raw.strip()


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


def call_json(system_prompt: str, user_prompt: str, *,
              node: Optional[str] = None, temperature: float = 0.2,
              max_tokens: int = 4000) -> Dict[str, Any]:
    """结构化 JSON 输出（评估节点、法官归纳），解析失败返回 error dict 而非抛异常"""
    resp = _post_chat(
        [{"role": "system", "content": system_prompt},
         {"role": "user", "content": user_prompt}],
        pick_model(node), temperature, max_tokens,
    )
    result = json.loads(resp.read().decode("utf-8"))
    raw = _clean_json(result["choices"][0]["message"]["content"])
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"error": "JSON 解析失败", "raw": raw[:500]}


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

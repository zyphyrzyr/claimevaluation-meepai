"""
Embedding 提供方（§7 RAG）
- 真实模式：硅基流动 BAAI/bge-m3（1024 维）
- Mock 模式 / 未配置 Key：字符 bigram 哈希向量（256 维，确定性，离线可复现）
  —— 哈希向量按字符二元组统计，天然具备词面相似性，可支撑演示模式的检索演示
"""

import hashlib
import json
import re
import urllib.request
import urllib.error
from typing import List

from ..config import get_runtime_settings

REAL_EMBED_DIM = 1024      # bge-m3
MOCK_EMBED_DIM = 256       # 离线哈希向量维度
SILICONFLOW_EMBED_MODEL = "BAAI/bge-m3"
SILICONFLOW_EMBED_URL = "https://api.siliconflow.cn/v1/embeddings"


class EmbeddingError(RuntimeError):
    pass


def use_mock_embedding() -> bool:
    s = get_runtime_settings()
    return s["use_mock"] or not s["siliconflow_api_key"]


# ---------------------------------------------------------- Mock：字符 bigram 哈希

def _mock_embed(text: str) -> List[float]:
    vec = [0.0] * MOCK_EMBED_DIM
    normalized = re.sub(r"\s+", "", text.lower())
    if not normalized:
        return vec
    grams = [normalized[i:i + 2] for i in range(len(normalized) - 1)] or [normalized]
    for g in grams:
        h = int(hashlib.md5(g.encode("utf-8")).hexdigest(), 16)
        vec[h % MOCK_EMBED_DIM] += 1.0
    norm = sum(v * v for v in vec) ** 0.5
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


# ---------------------------------------------------------- 真实：硅基流动 bge-m3

def _real_embed(texts: List[str]) -> List[List[float]]:
    s = get_runtime_settings()
    payload = json.dumps({"model": SILICONFLOW_EMBED_MODEL, "input": texts}).encode("utf-8")
    req = urllib.request.Request(SILICONFLOW_EMBED_URL, data=payload, method="POST")
    req.add_header("Authorization", f"Bearer {s['siliconflow_api_key']}")
    req.add_header("Content-Type", "application/json")
    try:
        resp = urllib.request.urlopen(req, timeout=60)
        data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise EmbeddingError(f"Embedding API 错误 ({e.code})")
    except Exception as e:
        raise EmbeddingError(f"Embedding 调用失败: {e}")
    # 按 index 排序，保证与输入顺序一致
    items = sorted(data.get("data", []), key=lambda d: d.get("index", 0))
    return [item["embedding"] for item in items]


def embed_texts(texts: List[str]) -> List[List[float]]:
    """批量向量化；失败时自动降级为哈希向量（演示不中断）"""
    if not texts:
        return []
    if use_mock_embedding():
        return [_mock_embed(t) for t in texts]
    try:
        return _real_embed(texts)
    except EmbeddingError:
        return [_mock_embed(t) for t in texts]


def embed_query(query: str) -> List[float]:
    return embed_texts([query])[0]

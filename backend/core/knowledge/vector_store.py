"""
向量存储层：ChromaDB 优先，导入失败时降级为 numpy 内存余弦存储（JSON 持久化）
- 双集合分库（§7）：scope=global（全局经验库，跨案共享）/ scope=case（案件材料库，case_id 隔离）
- 案件材料检索强制带 case_id 过滤，杜绝跨案污染
"""

import json
import math
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import RUNTIME_DIR

COLLECTION_NAME = "soft_ip_kb"
_PERSIST_PATH = RUNTIME_DIR / "vector_store_fallback.json"

_store_lock = threading.Lock()
_store_instance = None


def _chroma_available() -> bool:
    try:
        import chromadb  # noqa: F401
        return True
    except Exception:
        return False


class _BaseStore:
    def upsert(self, ids: List[str], vectors: List[List[float]],
               documents: List[str], metadatas: List[Dict[str, Any]]) -> None:
        raise NotImplementedError

    def delete(self, ids: List[str]) -> None:
        raise NotImplementedError

    def query(self, vector: List[float], where: Optional[Dict[str, Any]],
              top_k: int = 5) -> List[Dict[str, Any]]:
        raise NotImplementedError


# ---------------------------------------------------------- ChromaDB 后端

class _ChromaStore(_BaseStore):
    def __init__(self):
        import chromadb
        self._client = chromadb.PersistentClient(path=str(RUNTIME_DIR / "chroma"))
        self._col = self._client.get_or_create_collection(
            name=COLLECTION_NAME, metadata={"hnsw:space": "cosine"})

    def upsert(self, ids, vectors, documents, metadatas):
        self._col.upsert(ids=ids, embeddings=vectors,
                         documents=documents, metadatas=metadatas)

    def delete(self, ids):
        if ids:
            self._col.delete(ids=ids)

    @staticmethod
    def _to_chroma_where(where: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """扁平条件 dict → chroma where 语法（多条件需 $and）"""
        if not where:
            return None
        if len(where) == 1:
            k, v = next(iter(where.items()))
            return {k: v}
        return {"$and": [{k: v} for k, v in where.items()]}

    def query(self, vector, where, top_k=5):
        res = self._col.query(query_embeddings=[vector],
                              where=self._to_chroma_where(where),
                              n_results=top_k)
        out = []
        for i, cid in enumerate(res.get("ids", [[]])[0]):
            meta = (res.get("metadatas") or [[{}]])[0][i] or {}
            doc = (res.get("documents") or [[""]])[0][i] or ""
            dist = (res.get("distances") or [[0.0]])[0][i]
            out.append({"id": cid, "document": doc, "metadata": meta,
                        "score": 1.0 - float(dist)})   # cosine 距离 → 相似度
        return out


# ---------------------------------------------------------- numpy/内存降级后端

class _FallbackStore(_BaseStore):
    """无 chromadb 环境的兜底：JSON 持久化 + 余弦相似度"""

    def __init__(self):
        self._data: Dict[str, Dict[str, Any]] = {}
        if _PERSIST_PATH.exists():
            try:
                self._data = json.loads(_PERSIST_PATH.read_text(encoding="utf-8"))
            except Exception:
                self._data = {}
        self._dirty = False

    def _persist(self):
        if self._dirty:
            _PERSIST_PATH.write_text(
                json.dumps(self._data, ensure_ascii=False), encoding="utf-8")
            self._dirty = False

    def upsert(self, ids, vectors, documents, metadatas):
        for i, cid in enumerate(ids):
            self._data[cid] = {"vector": vectors[i], "document": documents[i],
                               "metadata": metadatas[i]}
        self._dirty = True
        self._persist()

    def delete(self, ids):
        for cid in ids:
            self._data.pop(cid, None)
        self._dirty = True
        self._persist()

    def query(self, vector, where, top_k=5):
        def _match(meta: Dict[str, Any]) -> bool:
            if not where:
                return True
            return all(meta.get(k) == v for k, v in where.items())

        def _cos(a, b):
            dot = sum(x * y for x, y in zip(a, b))
            na = math.sqrt(sum(x * x for x in a)) or 1.0
            nb = math.sqrt(sum(x * x for x in b)) or 1.0
            return dot / (na * nb)

        hits = [{"id": cid, "document": d["document"], "metadata": d["metadata"],
                 "score": _cos(vector, d["vector"])}
                for cid, d in self._data.items() if _match(d["metadata"])]
        hits.sort(key=lambda h: h["score"], reverse=True)
        return hits[:top_k]


def get_store() -> _BaseStore:
    """单例向量存储（线程安全）"""
    global _store_instance
    with _store_lock:
        if _store_instance is None:
            if _chroma_available():
                try:
                    _store_instance = _ChromaStore()
                except Exception:
                    _store_instance = _FallbackStore()
            else:
                _store_instance = _FallbackStore()
        return _store_instance


def store_backend_name() -> str:
    return "chromadb" if isinstance(get_store(), _ChromaStore) else "fallback-cosine"

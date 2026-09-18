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

    def list_ids(self) -> List[str]:
        """列出全部块 id。给孤儿块清理这类维护动作使用。"""
        raise NotImplementedError

    def total_chunks(self) -> int:
        """块总数。用于把「向量库 vs DB」的不一致做成可观测的自述指标。"""
        raise NotImplementedError

    def backfill_user_id(self, default: str = "") -> int:
        """给缺 user_id 的存量块补默认值，返回补了多少块。

        幂等：已经有该键的块不动。多用户隔离上线前入库的块都没有这个键，
        不补的话「自己的 + 公共的」这条 OR 过滤会把它们全部挡在门外——
        表现是老经验库突然搜不到任何东西，且不报错。
        """
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
        """扁平条件 dict → chroma where 语法（多条件需 $and）

        已经是 chroma 原生语法（含 $and / $or）时原样返回：
        「自己的 + 公共的」是个 OR 条件，压平成一个 dict 根本表达不出来。
        """
        if not where:
            return None
        if "$and" in where or "$or" in where:
            return where
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

    def list_ids(self):
        # include=[] 表示只要 id，不带向量与文档——清理孤儿块时不需要正文，
        # 几百个块每个背着 256 维向量序列化一遍纯属浪费。
        return list(self._col.get(include=[])["ids"])

    def total_chunks(self):
        return self._col.count()

    def backfill_user_id(self, default: str = "") -> int:
        got = self._col.get(include=["metadatas"])
        ids = got.get("ids") or []
        metas = got.get("metadatas") or []
        todo_ids, todo_metas = [], []
        for cid, meta in zip(ids, metas):
            meta = dict(meta or {})
            if "user_id" in meta:
                continue
            meta["user_id"] = default
            todo_ids.append(cid)
            todo_metas.append(meta)
        if todo_ids:
            # chroma 的 update 是整份 metadata 覆盖，所以必须带着原有键一起写回
            self._col.update(ids=todo_ids, metadatas=todo_metas)
        return len(todo_ids)


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
        def _match(meta: Dict[str, Any], cond=None) -> bool:
            """支持 $and / $or 的递归匹配。

            兜底存储必须跟 chroma 后端接受同一套 where：否则「自己的 + 公共的」
            这条 OR 在 chroma 上生效、在降级存储上被当成普通键名去比对，
            两边行为不一致，而降级只在没装 chromadb 的机器上出现，
            出问题时根本想不到是 where 语法的事。
            """
            cond = where if cond is None else cond
            if not cond:
                return True
            if "$and" in cond:
                return all(_match(meta, sub) for sub in cond["$and"])
            if "$or" in cond:
                return any(_match(meta, sub) for sub in cond["$or"])
            return all(meta.get(k) == v for k, v in cond.items())

        def _cos(a, b):
            dot = sum(x * y for x, y in zip(a, b))
            na = math.sqrt(sum(x * x for x in a)) or 1.0
            nb = math.sqrt(sum(x * x for x in b)) or 1.0
            return dot / (na * nb)

        hits = [{"id": cid, "document": d["document"], "metadata": d["metadata"],
                 "score": _cos(vector, d["vector"])}
                for cid, d in self._data.items() if _match(d["metadata"], where)]
        hits.sort(key=lambda h: h["score"], reverse=True)
        return hits[:top_k]

    def list_ids(self):
        return list(self._data)

    def total_chunks(self):
        return len(self._data)

    def backfill_user_id(self, default: str = "") -> int:
        n = 0
        for d in self._data.values():
            meta = d.setdefault("metadata", {}) or {}
            if "user_id" not in meta:
                meta["user_id"] = default
                n += 1
        if n:
            self._dirty = True
            self._persist()
        return n


# 降级原因。None 表示用的是 chromadb（正常）；有值表示当前在兜底存储上跑。
# 「装上但打不开」和「没装」后果完全不同，所以要把原因留下来——
# 否则应用在空库上跑了半个月都没人知道。
_degraded_reason: Optional[str] = None


def get_store(strict: bool = False) -> _BaseStore:
    """
    单例向量存储（线程安全）。

    strict=True 时，若 chromadb 已安装却打不开，**直接抛错而不是降级**。
    维护脚本必须用 strict：否则它的删除会落在一个空的兜底存储上静默变成空操作，
    脚本打印「删除成功」，真实向量库却什么也没变，留下的孤儿块还要花很久才发现。
    """
    global _store_instance, _degraded_reason
    with _store_lock:
        if _store_instance is None:
            if not _chroma_available():
                _degraded_reason = "未安装 chromadb（属正常降级）"
                _store_instance = _FallbackStore()
            else:
                try:
                    _store_instance = _ChromaStore()
                    _degraded_reason = None
                except Exception as e:
                    _degraded_reason = f"{type(e).__name__}: {e}"
                    raise_msg = (
                        "chromadb 已安装但无法打开向量库，已拒绝以降级存储继续。"
                        f"原因：{e}\n"
                        "最常见的是另一个进程正持有该目录——后端 uvicorn 在跑时，"
                        "第二个进程打不开同一个 chroma 目录。维护脚本请先停掉后端再执行。"
                    )
                    if strict:
                        raise RuntimeError(raise_msg) from e
                    # 应用侧不能因为向量库打不开就整个服务起不来，仍降级，
                    # 但必须把这件事喊出来：降级后检索查的是空库，
                    # 接口全部正常返回、只是永远搜不到东西，静默下去极难排查。
                    print("\n" + "!" * 72)
                    print("[vector_store] !! chromadb 打不开，已降级为内存兜底存储")
                    print(f"[vector_store] !! 原因：{e}")
                    print("[vector_store] !! 后果：向量检索将查不到既有内容（接口仍正常返回）")
                    print("!" * 72 + "\n")
                    _store_instance = _FallbackStore()
        elif strict and _degraded_reason and "未安装" not in _degraded_reason:
            # 实例已在本进程里建过一次降级实例，strict 调用同样不能放过
            raise RuntimeError(
                f"向量库处于降级状态，拒绝执行维护动作：{_degraded_reason}")
        return _store_instance


def store_backend_name() -> str:
    return "chromadb" if isinstance(get_store(), _ChromaStore) else "fallback-cosine"


def store_degraded_reason() -> Optional[str]:
    """当前降级原因；None 表示正常跑在 chromadb 上。"""
    get_store()
    return _degraded_reason

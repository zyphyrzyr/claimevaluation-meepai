"""
知识库服务层（§7 RAG 双集合分库）
- 全局经验库（scope=global）：跨案件沉淀，来源 B 手动粘贴 / C 观点沉淀 / D 独立建库
- 案件材料库（scope=case）：每案一个，来源 A 证据文档，case_id 元数据强制隔离
- 检索：案件材料库命中绝不跨案（where 过滤）；全局库全案共享
- 注入：评分链路手动勾选（保复现性）；模拟法庭/追问顾问自动召回
"""

import re
from typing import Any, Dict, List, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..database import KnowledgeEntry, generate_id
from .embeddings import embed_texts, embed_query, use_mock_embedding
from .vector_store import get_store, store_backend_name, store_degraded_reason

CHUNK_SIZE = 400       # 字符
CHUNK_OVERLAP = 60
SNIPPET_LEN = 160

# 向量 metadata 里「公共」的取值。用空串而不是 None：chroma 的 where
# 不接受 None，而存量块压根没有这个键（见 backfill_user_id）。
PUBLIC_USER_ID = ""

SOURCE_TYPE_LABELS = {
    "A": "证据文档",
    "B": "手动录入",
    "C": "观点沉淀",
    "D": "独立建库",
}


# ---------------------------------------------------------- 分块

def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        chunks.append(text[start:start + size])
        start += size - overlap
    return chunks


# ---------------------------------------------------------- 增删查

def add_knowledge(db: Session, scope: str, source_type: str, title: str, content: str,
                  case_id: Optional[str] = None,
                  user_id: Optional[str] = None) -> KnowledgeEntry:
    """写入一条知识条目。

    user_id 只对 **全局经验库** 有意义（案件材料库的归属跟着案件走，由
    case_id + 案件归属校验把关）。不传即公共——存量数据与后台自动入库都是这种。
    """
    if scope not in ("global", "case"):
        raise ValueError("scope 必须为 global 或 case")
    if scope == "case" and not case_id:
        raise ValueError("案件材料必须关联 case_id")

    # 案件材料不记 user_id：它的可见性完全由所属案件决定，
    # 再记一份作者只会让「谁改了它」和「谁能看它」两套口径打架。
    owner = None if scope == "case" else user_id

    entry = KnowledgeEntry(
        id=generate_id(), scope=scope, case_id=case_id if scope == "case" else None,
        source_type=source_type, title=title, content=content, user_id=owner,
    )
    db.add(entry)
    db.flush()

    chunks = chunk_text(content)
    if chunks:
        vectors = embed_texts(chunks)
        ids = [f"{entry.id}:{i}" for i in range(len(chunks))]
        metas = [{"scope": scope, "case_id": case_id or "", "entry_id": entry.id,
                  "source_type": source_type, "title": title, "chunk_index": i,
                  "user_id": owner or PUBLIC_USER_ID}
                 for i in range(len(chunks))]
        get_store().upsert(ids, vectors, chunks, metas)
    db.commit()
    return entry


def delete_knowledge(db: Session, entry_id: str) -> bool:
    entry = db.query(KnowledgeEntry).filter(KnowledgeEntry.id == entry_id).first()
    if not entry:
        return False
    # 删除全部向量块
    chunk_ids = [f"{entry_id}:{i}" for i in range(len(chunk_text(entry.content)))]
    get_store().delete(chunk_ids)
    db.delete(entry)
    db.commit()
    return True


def entry_to_dict(entry: KnowledgeEntry, with_content: bool = False) -> Dict[str, Any]:
    d = {
        "id": entry.id,
        "scope": entry.scope,
        "case_id": entry.case_id,
        "source_type": entry.source_type,
        "source_type_label": SOURCE_TYPE_LABELS.get(entry.source_type, entry.source_type),
        "title": entry.title,
        "created_at": entry.created_at.isoformat() if entry.created_at else "",
        "chunk_count": len(chunk_text(entry.content)),
        # 案件材料的 is_public 跟着案件走，这里只标全局条目的共享状态
        "is_public": entry.scope != "global" or entry.user_id is None,
    }
    if with_content:
        d["content"] = entry.content
        d["snippet"] = (entry.content or "")[:SNIPPET_LEN]
    return d


def _global_visible_sql(user_id: Optional[str]):
    """全局条目的可见范围（SQL 侧）：公共的 + 自己的。

    案件材料（scope != global）一律放行——它的可见性由所属案件的归属校验把关，
    在这里再按 user_id 收窄是错的：案件材料入库时根本不写 user_id。
    """
    if user_id is None:
        return or_(KnowledgeEntry.scope != "global", KnowledgeEntry.user_id.is_(None))
    return or_(KnowledgeEntry.scope != "global",
               KnowledgeEntry.user_id.is_(None),
               KnowledgeEntry.user_id == user_id)


def _global_visible(entry: KnowledgeEntry, user_id: Optional[str]) -> bool:
    """与 _global_visible_sql 同规则，用在已经取出对象之后（无需再查库）。"""
    if entry.scope != "global":
        return True
    if entry.user_id is None:
        return True
    return user_id is not None and entry.user_id == user_id


def _global_where(user_id: Optional[str]) -> Dict[str, Any]:
    """全局库的可见范围（向量侧 where）。

    未登录只看公共（user_id 为空串）；登录后是「自己的 或 公共」——这是 OR，
    所以必须落成 chroma 原生语法，压平成 flat dict 表达不出来。
    """
    if user_id is None:
        return {"scope": "global", "user_id": PUBLIC_USER_ID}
    return {"$and": [{"scope": "global"},
                     {"$or": [{"user_id": user_id}, {"user_id": PUBLIC_USER_ID}]}]}


def list_entries(db: Session, scope: Optional[str] = None,
                 case_id: Optional[str] = None,
                 user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    q = db.query(KnowledgeEntry)
    if scope:
        q = q.filter(KnowledgeEntry.scope == scope)
    if case_id:
        q = q.filter(KnowledgeEntry.case_id == case_id)
    if scope != "case":
        q = q.filter(_global_visible_sql(user_id))
    return [entry_to_dict(e, with_content=True) for e in q.order_by(KnowledgeEntry.created_at.desc()).all()]


# ---------------------------------------------------------- 检索

def _dedupe_join(db: Session, hits: List[Dict[str, Any]],
                 user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """按 entry_id 去重（保留最高分块），并 join 条目元信息。

    这里再按归属筛一遍是**兜底**，不是主要手段：向量 where 已经过滤过了
    （见 _global_where）。但向量库与 DB 是两份数据，任何一侧漏条件都会变成
    「别人的经验被召回进别人的评估报告里」——这种泄漏不报错、界面上只表现为
    「这条材料怎么有点眼生」，极难发现。所以两侧都拦。
    """
    best: Dict[str, Dict[str, Any]] = {}
    for h in hits:
        eid = h["metadata"].get("entry_id")
        if not eid:
            continue
        if eid not in best or h["score"] > best[eid]["score"]:
            best[eid] = h
    if not best:
        return []
    rows = db.query(KnowledgeEntry).filter(
        KnowledgeEntry.id.in_(list(best.keys()))).all()
    row_map = {r.id: r for r in rows if _global_visible(r, user_id)}
    out = []
    for eid, h in best.items():
        row = row_map.get(eid)
        if not row:
            continue
        out.append({
            **entry_to_dict(row, with_content=True),
            "score": round(float(h["score"]), 4),
            "matched_chunk": h["document"],
        })
    out.sort(key=lambda x: x["score"], reverse=True)
    return out


def search_knowledge(db: Session, query: str, *, case_id: Optional[str] = None,
                     scope: Optional[str] = None, top_k: int = 5,
                     user_id: Optional[str] = None,
                     min_score: Optional[float] = None,
                     title_boost: bool = True) -> List[Dict[str, Any]]:
    """
    语义检索。规则（防污染）：
    - scope=case：必须带 case_id，仅检索该案材料库
    - scope=global：仅全局经验库，且只含「自己的 + 公共的」
    - 默认（scope=None）：该案材料库 + 全局经验库（自动召回场景）

    user_id 为 None = 未登录，此时全局库只剩公共部分。

    min_score：最低相似度合格线。为 None 时**不过滤**（历史行为，自动召回
    用它再自行按 auto_recall_min_score 过滤，保持两处互不干扰）；传值时先按
    分数过滤，再做**标题保底**——query 的任一词元（大小写不敏感）命中条目标题
    即重新纳入并把分抬到合格线。标题保底只在可见范围过滤后的集合内进行，
    不改变权限/跨案隔离。
    """
    query = (query or "").strip()
    if not query:
        return []
    vec = embed_query(query)
    store = get_store()
    all_hits: List[Dict[str, Any]] = []

    if scope in (None, "case") and case_id:
        all_hits += store.query(vec, {"scope": "case", "case_id": case_id}, top_k=top_k)
    if scope in (None, "global"):
        all_hits += store.query(vec, _global_where(user_id), top_k=top_k)
    joined = _dedupe_join(db, all_hits, user_id=user_id)

    if min_score is None:
        return joined[:top_k]

    tokens = [t for t in re.split(r"\s+", query.lower()) if t]
    kept: List[Dict[str, Any]] = []
    for h in joined:
        score = float(h.get("score", 0))
        if score >= min_score:
            kept.append(h)
            continue
        # 标题保底：query 词元命中条目标题（只放宽，不收紧，且保底分设成合格线）
        if title_boost and tokens and any(
                t in (h.get("title") or "").lower() for t in tokens):
            boosted = dict(h)
            boosted["score"] = min_score
            kept.append(boosted)
    kept.sort(key=lambda h: float(h.get("score", 0)), reverse=True)
    return kept[:top_k]


# ---------------------------------------------------------- 注入 / 自动召回

def build_injection_text(entries: List[Dict[str, Any]]) -> str:
    """手动勾选注入：拼接为 prompt 片段"""
    if not entries:
        return ""
    lines = []
    for e in entries:
        label = SOURCE_TYPE_LABELS.get(e.get("source_type", ""), "")
        scope_tag = "案件材料" if e.get("scope") == "case" else "经验库"
        content = e.get("content") or e.get("matched_chunk") or ""
        lines.append(f"### [{scope_tag}·{label}] {e.get('title', '未命名')}\n{content[:1200]}")
    return "\n\n".join(lines)


def recall_for_context(db: Session, case_id: Optional[str], query: str,
                       top_k: int = 3, user_id: Optional[str] = None) -> Dict[str, Any]:
    """
    自动召回（模拟法庭 / 追问顾问 / 评估启动）：
    返回 {"context": 拼接文本, "refs": 命中条目列表}

    user_id 必须传：召回的内容会直接拼进 prompt。漏传等于把别人的经验
    当成自己的行业常识喂给模型，而输出看上去完全正常。
    """
    if not query:
        return {"context": "", "refs": []}
    hits = search_knowledge(db, query, case_id=case_id, top_k=top_k, user_id=user_id)
    if not hits:
        return {"context": "", "refs": []}
    blocks = []
    for h in hits:
        blocks.append(f"【{h['source_type_label']}·{h['title']}】{h['matched_chunk']}")
    return {"context": "\n\n".join(blocks), "refs": [
        {"id": h["id"], "title": h["title"], "score": h["score"],
         "scope": h["scope"], "source_type": h["source_type"]} for h in hits]}


def ingest_case_materials(db: Session, case_id: str, evidence_texts: str) -> int:
    """
    来源 A：证据文本自动入库（案件材料库）。
    双换行分段，每段一条目；返回入库条数。
    """
    segments = [s.strip() for s in re.split(r"\n\s*\n", evidence_texts or "") if len(s.strip()) >= 10]
    count = 0
    for i, seg in enumerate(segments):
        title = seg[:24].replace("\n", " ") + ("…" if len(seg) > 24 else "")
        add_knowledge(db, scope="case", source_type="A",
                      title=f"证据材料-{i + 1}：{title}", content=seg, case_id=case_id)
        count += 1
    return count


def info(db: Optional[Session] = None) -> Dict[str, Any]:
    """
    知识库自述。

    传了 db 就顺带算一次孤儿向量块数量——这是「DB 行与向量库不一致」的唯一
    可观测信号。不一致本身不会报错：孤儿块占掉 store.query 的 top_k 名额之后
    被 _dedupe_join 静默丢弃，表现只是「检索结果莫名变少」，不主动算就永远看不见。
    """
    d: Dict[str, Any] = {
        "backend": store_backend_name(),
        "embedding": "bge-m3(硅基流动)" if not use_mock_embedding() else "mock-hash-256d（离线）",
        # 正常为 None；有值说明当前跑在兜底存储上，检索查不到既有内容
        "degraded_reason": store_degraded_reason(),
    }
    if db is not None:
        store = get_store()
        d["total_chunks"] = store.total_chunks()
        d["orphan_chunks"] = len(orphan_chunk_ids(db, store))
        # 期望值按内容现算：两者差得远说明向量库与 DB 已经不一致
        d["expected_chunks"] = sum(
            len(chunk_text(c)) for (c,) in db.query(KnowledgeEntry.content).all())
    return d


def orphan_chunk_ids(db: Session, store=None) -> List[str]:
    """
    找出「向量库里还在、对应条目却已被删掉」的块。

    块 id 的构造是 f"{entry_id}:{i}"（见 add_knowledge），所以 entry_id 直接取
    冒号前缀即可，不必读 metadata——少一次序列化，也顺带兼容兜底存储。

    为什么值得专门找：删条目有两条路径，只有 service.delete_knowledge 会同时
    清 DB 行与向量块。SQL 直删、脚本在降级存储上跑、进程删到一半崩掉，
    都只清了 DB 行——留下的孤儿块不报错、不可见，只是悄悄挤占检索名额。
    """
    store = store or get_store()
    live = {row[0] for row in db.query(KnowledgeEntry.id).all()}
    return [cid for cid in store.list_ids() if cid.split(":", 1)[0] not in live]


def ensure_vector_user_id() -> int:
    """启动自愈：给没有 user_id 的存量向量块补上「公共」。

    多用户隔离上线前入库的块都没有这个键。不补的后果是检索条件
    「自己的 或 公共的」对它们全部不成立——老经验库会一夜之间搜不到，
    而且接口照常返回空列表，没有任何报错。
    """
    try:
        n = get_store().backfill_user_id(PUBLIC_USER_ID)
    except Exception as e:
        # 补不上不能挡住启动，但要把话说出来
        print(f"[init] 向量块 user_id 回填跳过：{type(e).__name__}: {e}")
        return 0
    if n:
        print(f"[init] 已给 {n} 个存量向量块补 user_id=公共")
    return n


def heal_orphan_vectors(db: Session, store=None) -> Dict[str, Any]:
    """删除孤儿向量块。返回 {"removed": n, "chunks": [...]}（供启动自愈与维护脚本调用）"""
    store = store or get_store()
    chunks = orphan_chunk_ids(db, store)
    if chunks:
        store.delete(chunks)
    return {"removed": len(chunks), "chunks": chunks}

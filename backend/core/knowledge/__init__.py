"""
RAG 知识库模块（§7）
- embeddings：向量化（bge-m3 / 离线哈希兜底）
- vector_store：ChromaDB 优先 + 内存余弦降级
- service：双集合分库（全局经验库 / 案件材料库）增删查、注入、自动召回
"""

from .service import (
    add_knowledge,
    delete_knowledge,
    list_entries,
    search_knowledge,
    build_injection_text,
    recall_for_context,
    ingest_case_materials,
    entry_to_dict,
    chunk_text,
    info,
)
from .vector_store import store_backend_name

__all__ = [
    "add_knowledge",
    "delete_knowledge",
    "list_entries",
    "search_knowledge",
    "build_injection_text",
    "recall_for_context",
    "ingest_case_materials",
    "entry_to_dict",
    "chunk_text",
    "info",
    "store_backend_name",
]

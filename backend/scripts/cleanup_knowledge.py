"""
知识库存量清理（一次性维护脚本）：
  1) 全局经验库：删除 E2E 测试残留（标题含「E2E」的条目）
  2) 案件材料库：按 (case_id, title) 去重，保留最早一条，删除其余重复
  3) 全局经验库：删除早期分块器/召回用例的测试样本（见 RESIDUE_TITLES）

用法（backend/ 目录下）：
  python scripts/cleanup_knowledge.py            # 预演：只打印将要删除的条目
  python scripts/cleanup_knowledge.py --apply    # 实际执行删除

注意：不带 SOFT_IP_DATA_DIR 时操作的是真实数据目录（soft-ip-v4/data）。

**跑之前先停掉后端。** 本脚本要求真实向量库可打开（strict），拿不到就直接
报错退出，不会"降级后照常打印成功"。原因见下。

**必须走 service 层的 delete_knowledge，不要图省事写 SQL DELETE。**
它同时清 DB 行与向量块；直接删行会留下孤儿向量块，而 _dedupe_join 遇到
「命中块对应的条目已不存在」会静默 continue——表面看不出任何异常，但
store.query 的 top_k 名额会被孤儿挤占，结果是真实条目检索不到。
这是「删完看着正常、检索悄悄变差」的典型。

另外，一条更隐蔽的踩坑记录：脚本进程拿到的向量库可能和后端进程**不是同一个**。
chromadb 的嵌入式实现同一目录只允许一个进程持有，后端在跑时第二个进程会
构造失败；而 get_store() 原本会把这种失败当成「没装 chromadb」静默降级到
内存兜底存储——于是脚本的向量删除落在一个空库上变成空操作，脚本照样打印
「删除成功」。所以这里用 strict=True：宁可不跑，也不假装跑成功。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

APPLY = "--apply" in sys.argv

from core.config import DB_PATH
from core.database import KnowledgeEntry, SessionLocal, init_db
from core.knowledge.service import delete_knowledge, heal_orphan_vectors, info
from core.knowledge.vector_store import get_store

# 全局库里已知的测试样本标题。
#
# 用精确标题白名单而不是「标题重复就去重」：这批数据的特征是**同名同内容成批出现**
# （各 10 条），而全局库里出现同名条目本身是合法的（比如两条不同的办案心得都可能
# 叫「判赔规模经验」）。按重复去重会留 1 条、删 9 条，既没清干净，又可能误伤真数据。
RESIDUE_TITLES = {
    "L1-共享经验",        # 召回链路的 L1 共享经验用例
    "分块计数校验",        # 分块器用例（1000 字，用于验证 chunk_count）
}

print(f"数据库: {DB_PATH}")
print(f"模式: {'APPLY（实际删除）' if APPLY else 'DRY-RUN（预演，加 --apply 生效）'}")
print("=" * 70)

# 先把向量库拿到手。拿不到就退出——绝不能带着降级存储往下走。
try:
    STORE = get_store(strict=True)
except RuntimeError as e:
    print("\n无法打开真实向量库，已中止（未删除任何数据）：")
    print(f"  {e}\n")
    print("请先停掉后端再执行本脚本，或改用 /api/knowledge/info 在线观察一致性指标：")
    print("  curl -s http://127.0.0.1:8000/api/knowledge/info")
    sys.exit(2)

init_db()
db = SessionLocal()

before = info(db)
print(f"向量库自述: 总块 {before['total_chunks']} · 孤儿块 {before['orphan_chunks']} "
      f"· 按内容应有 {before['expected_chunks']}")

to_delete = []

# 1) 全局库 E2E 残留
global_entries = db.query(KnowledgeEntry).filter(
    KnowledgeEntry.scope == "global").all()
e2e_hits = [e for e in global_entries if "E2E" in (e.title or "")]
for e in e2e_hits:
    to_delete.append(("global/E2E残留", e))

# 2) 案件材料库按 (case_id, title) 去重
case_entries = db.query(KnowledgeEntry).filter(
    KnowledgeEntry.scope == "case").order_by(KnowledgeEntry.created_at.asc()).all()
seen = {}
for e in case_entries:
    key = (e.case_id, e.title)
    if key in seen:
        to_delete.append(("case/重复", e))
    else:
        seen[key] = e

# 3) 全局库已知测试样本
for e in global_entries:
    if (e.title or "") in RESIDUE_TITLES:
        to_delete.append(("global/测试样本", e))

# 4) 孤儿向量块：DB 行已不在、块还留在向量库里。
#    放在这里而不是只挂在启动钩子上——它属于「维护动作」，就该由维护脚本显式执行，
#    顺带把数量打出来（before['orphan_chunks'] 已经算过）。
orphans = before["orphan_chunks"]

by_reason = {}
for reason, e in to_delete:
    by_reason.setdefault(reason, []).append(e)

if to_delete:
    for reason, items in by_reason.items():
        print(f"\n[{reason}] {len(items)} 条")
        for e in items:
            print(f"  {e.id[:8]}  {e.title[:52]}  "
                  f"(scope={e.scope}, case={e.case_id}, created={e.created_at})")
    print("=" * 70)
    print(f"共 {len(to_delete)} 条待删除：" +
          "、".join(f"{r} {len(v)}" for r, v in by_reason.items()))
else:
    print("没有需要清理的条目。")

if orphans:
    print(f"另有 {orphans} 个孤儿向量块待清除。")

if not to_delete and not orphans:
    sys.exit(0)

if not APPLY:
    print("DRY-RUN 结束，未删除任何数据。确认无误后加 --apply 执行。")
    sys.exit(0)

ok = fail = 0
for _, e in to_delete:
    try:
        if delete_knowledge(db, e.id):
            ok += 1
        else:
            fail += 1
    except Exception as exc:
        fail += 1
        print(f"删除失败 {e.id[:8]}: {exc}")

print(f"完成：删除 {ok} 条，失败 {fail} 条")

healed = heal_orphan_vectors(db, STORE)
print(f"孤儿向量块：清除 {healed['removed']} 个")

after = info(db)
print(f"一致性复核：总块 {after['total_chunks']} · 孤儿块 {after['orphan_chunks']} "
      f"· 按内容应有 {after['expected_chunks']}")
if after["orphan_chunks"] or after["total_chunks"] != after["expected_chunks"]:
    print("!! 向量库与 DB 仍不一致，请人工核对（脚本未静默放过）")

remaining = db.query(KnowledgeEntry).filter(KnowledgeEntry.scope == "global").count()
print(f"全局经验库剩余：{remaining} 条")

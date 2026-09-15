"""
知识库存量清理（一次性维护脚本）：
  1) 全局经验库：删除 E2E 测试残留（标题含「E2E」的条目）
  2) 案件材料库：按 (case_id, title) 去重，保留最早一条，删除其余重复

用法（backend/ 目录下）：
  python scripts/cleanup_knowledge.py            # 预演：只打印将要删除的条目
  python scripts/cleanup_knowledge.py --apply    # 实际执行删除

注意：不带 SOFT_IP_DATA_DIR 时操作的是真实数据目录（soft-ip-v4/data）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

APPLY = "--apply" in sys.argv

from core.config import DB_PATH
from core.database import KnowledgeEntry, SessionLocal, init_db
from core.knowledge.service import delete_knowledge

print(f"数据库: {DB_PATH}")
print(f"模式: {'APPLY（实际删除）' if APPLY else 'DRY-RUN（预演，加 --apply 生效）'}")
print("=" * 70)

init_db()
db = SessionLocal()

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

if not to_delete:
    print("没有需要清理的条目。")
    sys.exit(0)

for reason, e in to_delete:
    print(f"[{reason}] {e.id[:8]}  {e.title}  (case_id={e.case_id}, created={e.created_at})")

print("=" * 70)
print(f"共 {len(to_delete)} 条待删除")

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

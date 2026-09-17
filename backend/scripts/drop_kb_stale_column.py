"""
一次性迁移：删除 knowledge_entries.stale 列。

背景：这一列是「知识库健康检查：与权威源冲突标待更新」的设计残留，从未被写出过——
全项目没有任何一处把它置 True，因为系统里并不存在「权威源」这个概念。后端一路把它
返回给前端，前端从不渲染。一个永远亮不起来的灯是死代码，故连同列一并移除。

项目没有迁移框架（core.database.init_db 只做 create_all），create_all 不会删列，
所以这里显式做一次 ALTER TABLE。SQLite 3.35+ 支持 DROP COLUMN，本机是 3.50。

用法（backend/ 目录下）：
  python scripts/drop_kb_stale_column.py            # 预演
  python scripts/drop_kb_stale_column.py --apply    # 执行

幂等：列不存在时直接报告并退出 0，重复跑无副作用。

跑之前建议停掉后端——ALTER TABLE 需要短暂的独占锁，后端正在写时会拿到
「database is locked」而失败（失败是安全的，重试即可，但不如先停干净）。
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

APPLY = "--apply" in sys.argv

from core.config import DB_PATH

TABLE = "knowledge_entries"
COLUMN = "stale"

print(f"数据库: {DB_PATH}")
print(f"模式: {'APPLY（实际执行）' if APPLY else 'DRY-RUN（预演，加 --apply 生效）'}")
print("=" * 70)

conn = sqlite3.connect(str(DB_PATH))
try:
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({TABLE})").fetchall()]
    if COLUMN not in cols:
        print(f"{TABLE} 里已经没有 {COLUMN} 列，无需处理。")
        sys.exit(0)

    print(f"当前列：{', '.join(cols)}")
    print(f"待删除：{COLUMN}")
    n = conn.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
    print(f"受影响行数：{n}（该列的行数据会被一并丢弃，它从未被写过值）")

    if not APPLY:
        print("DRY-RUN 结束，未做任何改动。确认无误后加 --apply 执行。")
        sys.exit(0)

    conn.execute(f"ALTER TABLE {TABLE} DROP COLUMN {COLUMN}")
    conn.commit()

    cols_after = [r[1] for r in conn.execute(f"PRAGMA table_info({TABLE})").fetchall()]
    assert COLUMN not in cols_after, "列仍然存在，DROP 未生效"
    print(f"完成。剩余列：{', '.join(cols_after)}")

    # 完整性自检：DROP COLUMN 在 SQLite 里是重建表，值得确认数据没丢
    assert conn.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0] == n, \
        "行数发生变化，DROP COLUMN 可能损坏了数据"
    print(f"完整性自检通过：行数仍为 {n}")
finally:
    conn.close()

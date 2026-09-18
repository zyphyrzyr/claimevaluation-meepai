"""
给存量库补 user_id 列（多用户归属）。

背景：`init_db()` 只做 `create_all`——它**只建缺失的表，不会给已存在的表加列**。
新增的 users / user_sessions 两张表会自动建出来，但 cases 与 knowledge_entries
上的 user_id 必须手工 ALTER，否则启动后一查就报 no such column。

语义：**列留空 = 公共**。迁移不写任何值，存量 279 个案件与经验库条目
保持 NULL，按「人人只读可见」处理。想改归属只需 UPDATE 这个列，不需要回滚脚本。

幂等：重复执行安全（已存在的列会跳过）。默认 dry-run，加 --apply 才真改。
用法：
    cd backend && python scripts/add_user_id_columns.py            # 只看会做什么
    cd backend && python scripts/add_user_id_columns.py --apply    # 真改（先自动备份）
"""

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import SQLITE_URL  # noqa: E402

# 表名 → 要补的列。加索引是另一个话题：这两列只用于等值过滤，
# 而 SQLite 对 279 行的表根本不在乎索引，故不建，省得多一份要维护的 schema。
TARGETS = ("cases", "knowledge_entries")
COLUMN = "user_id"
COLUMN_DDL = "TEXT REFERENCES users(id)"


def db_path() -> Path:
    """从 SQLAlchemy 的 URL 里取出真实文件路径（sqlite:///xxx → xxx）。"""
    if not SQLITE_URL.startswith("sqlite:///"):
        raise SystemExit(f"本脚本只处理 SQLite，当前 URL 是 {SQLITE_URL}")
    return Path(SQLITE_URL.replace("sqlite:///", "", 1))


def backup(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = path.with_name(f"{path.stem}-backup-{stamp}{path.suffix}")
    shutil.copy2(path, dest)
    return dest


def columns(con: sqlite3.Connection, table: str) -> list:
    return [r[1] for r in con.execute(f"pragma table_info({table})").fetchall()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正执行（否则只预览）")
    args = ap.parse_args()

    path = db_path()
    if not path.exists():
        print(f"数据库不存在：{path}")
        return 1

    print(f"数据库：{path}")
    con = sqlite3.connect(path)
    try:
        todo = []
        for table in TARGETS:
            if not con.execute(
                "select 1 from sqlite_master where type='table' and name=?", (table,)
            ).fetchone():
                print(f"  {table}: 表不存在，跳过")
                continue
            cols = columns(con, table)
            if COLUMN in cols:
                print(f"  {table}: 已有 {COLUMN} 列，跳过")
                continue
            rows = con.execute(f"select count(*) from {table}").fetchone()[0]
            print(f"  {table}: 将新增 {COLUMN} 列（{rows} 行保持 NULL = 公共）")
            todo.append((table, rows))

        if not todo:
            print("\n无需改动。")
            return 0

        if not args.apply:
            print("\n（预览模式，未改动。加 --apply 执行）")
            return 0

        # 改 schema 之前先备份：ALTER 失败留下半截状态的话，能整库换回去
        dest = backup(path)
        print(f"\n已备份到：{dest}")

        for table, rows_before in todo:
            con.execute(f"alter table {table} add column {COLUMN} {COLUMN_DDL}")
            con.commit()
            rows_after = con.execute(f"select count(*) from {table}").fetchone()[0]
            if rows_after != rows_before:
                print(f"  ✗ {table}: 行数从 {rows_before} 变成 {rows_after}，中止")
                return 2
            nulls = con.execute(
                f"select count(*) from {table} where {COLUMN} is null"
            ).fetchone()[0]
            print(f"  ✓ {table}: 已加列，{rows_after} 行，其中 {nulls} 行为 NULL（公共）")

        # 收尾自检：列真的在，且数据没丢
        for table in TARGETS:
            if con.execute(
                "select 1 from sqlite_master where type='table' and name=?", (table,)
            ).fetchone() and COLUMN not in columns(con, table):
                print(f"  ✗ {table}: 自检失败，列没加上")
                return 2
        print("\n完成。")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())

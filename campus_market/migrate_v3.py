"""v2 → v3 数据迁移：新增商品图片表与议价聊天表。

这两张表是纯新增，不动已有表结构，所以不需要像 v2 那样重建表，
直接 CREATE TABLE IF NOT EXISTS 即可，幂等，重复执行安全。

用法：
    python migrate_v3.py [数据库路径]
"""
import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = BASE_DIR / "campus_market.db"

# 与 schema.sql 中两张表的定义保持一致（tests 里有断言比对两边结构）
STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS item_images (
        id         INTEGER PRIMARY KEY,
        item_id    INTEGER NOT NULL REFERENCES items(id) ON DELETE RESTRICT,
        filename   TEXT NOT NULL UNIQUE,
        position   INTEGER NOT NULL DEFAULT 0 CHECK (position BETWEEN 0 AND 4),
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    "CREATE INDEX IF NOT EXISTS idx_item_images_item ON item_images(item_id, position)",
    """CREATE TABLE IF NOT EXISTS messages (
        id          INTEGER PRIMARY KEY,
        item_id     INTEGER NOT NULL REFERENCES items(id) ON DELETE RESTRICT,
        buyer_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        sender_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        kind        TEXT NOT NULL DEFAULT 'text' CHECK (kind IN ('text','offer')),
        body        TEXT NOT NULL CHECK (length(trim(body)) > 0 AND length(body) <= 500),
        offer_price INTEGER CHECK (offer_price IS NULL OR offer_price > 0),
        accepted_at TEXT,
        read_at     TEXT,
        created_at  TEXT NOT NULL DEFAULT (datetime('now')),
        CHECK ((kind = 'offer' AND offer_price IS NOT NULL)
            OR (kind = 'text' AND offer_price IS NULL)),
        CHECK (accepted_at IS NULL OR kind = 'offer')
    )""",
    "CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages(item_id, buyer_id, id)",
    "CREATE INDEX IF NOT EXISTS idx_messages_buyer ON messages(buyer_id)",
]


def migrate(db_path, quiet=False):
    db_path = Path(db_path)
    if not db_path.exists():
        raise SystemExit(f"数据库不存在：{db_path}")

    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        conn.execute("BEGIN IMMEDIATE")
        for stmt in STATEMENTS:  # 每条都是 IF NOT EXISTS，天然幂等
            conn.execute(stmt)
        conn.execute("COMMIT")

        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        missing = {"item_images", "messages"} - tables
        if missing:
            raise SystemExit(f"建表失败：{missing}")
        if not quiet:
            print("迁移完成：item_images、messages 已就绪")
            print("图片数：", conn.execute("SELECT COUNT(*) FROM item_images").fetchone()[0])
            print("消息数：", conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0])
        return True
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    migrate(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DB)

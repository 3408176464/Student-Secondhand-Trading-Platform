"""v1 → v2 数据迁移：订单改为双向确认流程，用户增加微信 openid。

为什么不能直接 ALTER：SQLite 不支持修改 CHECK 约束，而订单状态取值从
「待交易/已完成/已取消」变成了「待接单/待交付/已完成/已取消」。所以按
SQLite 官方推荐做法——建新表、搬数据、删旧表、改名。

用法：
    python migrate_v2.py [数据库路径]

脚本自动备份为 <库>.v1.bak，幂等（已迁移过会直接退出）。
"""
import random
import shutil
import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = BASE_DIR / "campus_market.db"

# 与 schema.sql 中的 orders 定义保持一致（tests 里有断言比对两边的一致性）
ORDERS_DDL = """
CREATE TABLE orders_new (
    id                INTEGER PRIMARY KEY,
    item_id           INTEGER NOT NULL REFERENCES items(id) ON DELETE RESTRICT,
    buyer_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    price             INTEGER NOT NULL CHECK (price > 0),
    status            TEXT NOT NULL DEFAULT '待接单'
                      CHECK (status IN ('待接单','待交付','已完成','已取消')),
    deal_code         TEXT NOT NULL
                      CHECK (deal_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'),
    accepted_at       TEXT,
    seller_confirm_at TEXT,
    buyer_confirm_at  TEXT,
    cancel_by         TEXT CHECK (cancel_by IN ('买家','卖家','系统')),
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK ((status = '待接单' AND accepted_at IS NULL)
        OR (status IN ('待交付','已完成') AND accepted_at IS NOT NULL)
        OR status = '已取消'),
    CHECK (status <> '已完成'
        OR (seller_confirm_at IS NOT NULL AND buyer_confirm_at IS NOT NULL)),
    CHECK ((status = '已取消' AND cancel_by IS NOT NULL)
        OR (status <> '已取消' AND cancel_by IS NULL))
);
"""


def _columns(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def migrate(db_path, quiet=False):
    db_path = Path(db_path)
    if not db_path.exists():
        raise SystemExit(f"数据库不存在：{db_path}")

    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")

    try:
        if "deal_code" in _columns(conn, "orders"):
            if not quiet:
                print("已经是 v2 结构，无需迁移。")
            return False

        backup = db_path.with_suffix(db_path.suffix + ".v1.bak")
        shutil.copy2(db_path, backup)
        if not quiet:
            print(f"已备份到 {backup.name}")

        before = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ("users", "items", "orders")}

        conn.execute("BEGIN IMMEDIATE")
        conn.execute(ORDERS_DDL)

        for row in conn.execute("SELECT * FROM orders ORDER BY id").fetchall():
            old = row["status"]
            code = f"{random.randint(0, 999999):06d}"
            accepted = seller_ok = buyer_ok = cancel_by = None
            if old == "待交易":
                new = "待接单"
            elif old == "已完成":
                # 历史数据没有分步确认的时间，用订单创建时间回填，避免伪造"刚刚完成"
                new = "已完成"
                accepted = seller_ok = buyer_ok = row["created_at"]
            elif old == "已取消":
                new = "已取消"
                cancel_by = "系统"  # v1 未记录取消方
            else:
                raise SystemExit(f"未知的旧状态：{old}（订单 {row['id']}）")

            conn.execute(
                "INSERT INTO orders_new (id, item_id, buyer_id, price, status, "
                "deal_code, accepted_at, seller_confirm_at, buyer_confirm_at, "
                "cancel_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (row["id"], row["item_id"], row["buyer_id"], row["price"], new,
                 code, accepted, seller_ok, buyer_ok, cancel_by, row["created_at"]))

        conn.execute("DROP TABLE orders")
        conn.execute("ALTER TABLE orders_new RENAME TO orders")
        conn.execute("CREATE INDEX idx_orders_buyer ON orders(buyer_id)")
        conn.execute("CREATE INDEX idx_orders_item ON orders(item_id)")
        conn.execute("CREATE UNIQUE INDEX idx_orders_active_item "
                     "ON orders(item_id) WHERE status IN ('待接单','待交付')")

        if "openid" not in _columns(conn, "users"):
            conn.execute("ALTER TABLE users ADD COLUMN openid TEXT")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_openid "
                     "ON users(openid)")
        conn.execute("COMMIT")

        after = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                 for t in ("users", "items", "orders")}
        if not quiet:
            print(f"迁移完成，行数 {before} → {after}")
            print("状态分布：", dict(conn.execute(
                "SELECT status, COUNT(*) FROM orders GROUP BY status").fetchall()))

        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        fk_bad = conn.execute("PRAGMA foreign_key_check").fetchall()
        if not quiet:
            print(f"integrity_check: {integrity}；外键异常行数：{len(fk_bad)}")
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
    target = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DB
    migrate(target)

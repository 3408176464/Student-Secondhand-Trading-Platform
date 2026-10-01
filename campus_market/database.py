"""数据库连接、建表与示例数据。"""
import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def connect(db_path):
    """建立连接：手动事务（isolation_level=None）+ 开启外键约束。"""
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(conn, seed=True):
    """建表（含索引）并写入 7 个固定分类；seed=True 时再写入示例数据（Task 4 实现）。"""
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.executemany(
        "INSERT INTO categories (id, name) VALUES (?, ?)",
        [(1, "教材书籍"), (2, "手机数码"), (3, "数码配件"), (4, "宿舍生活"),
         (5, "交通出行"), (6, "服饰运动"), (7, "其他闲置")],
    )
    if seed:
        seed_demo_data(conn)


def seed_demo_data(conn):
    """示例数据：由 Task 4 填充真实内容。"""
    raise NotImplementedError

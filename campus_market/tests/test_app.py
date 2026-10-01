"""校园二手交易平台测试。"""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database import connect, init_db  # noqa: E402


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "test.db")
        self.conn = connect(self.db_path)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_init_creates_tables(self):
        init_db(self.conn, seed=False)
        names = {r["name"] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"categories", "users", "items", "orders"} <= names)

    def test_categories_always_inserted(self):
        init_db(self.conn, seed=False)
        count = self.conn.execute(
            "SELECT COUNT(*) AS n FROM categories").fetchone()["n"]
        self.assertEqual(count, 7)

    def test_foreign_keys_enforced(self):
        init_db(self.conn, seed=False)
        self.conn.execute(
            "INSERT INTO users (id, username, password) VALUES (1, 'u1', 'p')")
        self.conn.execute(
            "INSERT INTO items (id, title, price, condition, category_id, seller_id) "
            "VALUES (1, 't', 100, '全新', 1, 1)")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("DELETE FROM users WHERE id = 1")

    def test_unique_username(self):
        init_db(self.conn, seed=False)
        self.conn.execute("INSERT INTO users (username, password) VALUES ('dup', 'p')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO users (username, password) VALUES ('dup', 'p2')")

    def test_check_price_positive(self):
        init_db(self.conn, seed=False)
        self.conn.execute("INSERT INTO users (id, username, password) VALUES (1, 'u1', 'p')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO items (title, price, condition, category_id, seller_id) "
                "VALUES ('bad', 0, '全新', 1, 1)")

    def test_check_item_condition(self):
        init_db(self.conn, seed=False)
        self.conn.execute("INSERT INTO users (id, username, password) VALUES (1, 'u1', 'p')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO items (title, price, condition, category_id, seller_id) "
                "VALUES ('bad', 100, '破烂', 1, 1)")

    def test_check_item_status(self):
        init_db(self.conn, seed=False)
        self.conn.execute("INSERT INTO users (id, username, password) VALUES (1, 'u1', 'p')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO items (title, price, condition, status, category_id, seller_id) "
                "VALUES ('bad', 100, '全新', '已退', 1, 1)")

    def test_check_order_status(self):
        init_db(self.conn, seed=False)
        self.conn.execute("INSERT INTO users (id, username, password) VALUES (1, 'u1', 'p')")
        self.conn.execute(
            "INSERT INTO items (id, title, price, condition, category_id, seller_id) "
            "VALUES (1, 't', 100, '全新', 1, 1)")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO orders (item_id, buyer_id, price, status) "
                "VALUES (1, 1, 100, '已发货')")

    def test_partial_unique_index(self):
        init_db(self.conn, seed=False)
        self.conn.execute("INSERT INTO users (id, username, password) VALUES (1, 'u1', 'p')")
        self.conn.execute(
            "INSERT INTO items (id, title, price, condition, category_id, seller_id) "
            "VALUES (1, 't', 100, '全新', 1, 1)")
        self.conn.execute(
            "INSERT INTO orders (item_id, buyer_id, price, status) VALUES (1, 1, 100, '待交易')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO orders (item_id, buyer_id, price, status) VALUES (1, 1, 100, '待交易')")
        # 历史订单（非待交易）不受唯一索引限制
        self.conn.execute(
            "INSERT INTO orders (item_id, buyer_id, price, status) VALUES (1, 1, 100, '已取消')")

    def test_fks_on_every_connection(self):
        init_db(self.conn, seed=False)
        row = self.conn.execute("PRAGMA foreign_keys").fetchone()
        self.assertEqual(row[0], 1)


if __name__ == "__main__":
    unittest.main()

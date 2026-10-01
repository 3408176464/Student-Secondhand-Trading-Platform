"""校园二手交易平台测试。"""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database import connect, init_db  # noqa: E402
from app import create_app, escape_like, format_price, to_local_time, parse_price_yuan


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


class HelperTests(unittest.TestCase):
    def test_format_price(self):
        self.assertEqual(format_price(1), "0.01")
        self.assertEqual(format_price(1999), "19.99")
        self.assertEqual(format_price(10000), "100.00")

    def test_to_local_time(self):
        self.assertEqual(to_local_time("2026-10-01 04:00:00"), "2026-10-01 12:00:00")

    def test_escape_like(self):
        self.assertEqual(escape_like("100%_a\\b"), "100\\%\\_a\\\\b")

    def test_parse_price_yuan(self):
        self.assertEqual(parse_price_yuan("19.99"), 1999)
        self.assertEqual(parse_price_yuan("0.01"), 1)
        with self.assertRaises(ValueError):
            parse_price_yuan("0")
        with self.assertRaises(ValueError):
            parse_price_yuan("0.00")
        with self.assertRaises(ValueError):
            parse_price_yuan("19.999")
        with self.assertRaises(ValueError):
            parse_price_yuan("abc")
        with self.assertRaises(ValueError):
            parse_price_yuan("-5")


class AppTestCase(unittest.TestCase):
    """应用级测试基类：临时数据库 + 测试客户端 + 常用辅助方法。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db_path = str(Path(self.tmp.name) / "test.db")
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test",
            "DATABASE": db_path,
            "SEED_DATA": False,
        })
        self.client = self.app.test_client()

    def tearDown(self):
        if hasattr(self, "_conn"):
            self._conn.close()
        self.tmp.cleanup()

    def register(self, username="buyer1", password="password123", contact="wx123"):
        return self.client.post("/register", data={
            "username": username, "password": password, "contact": contact,
        }, follow_redirects=True)

    def login(self, username="buyer1", password="password123"):
        return self.client.post("/login", data={
            "username": username, "password": password,
        }, follow_redirects=True)

    def logout(self):
        return self.client.post("/logout", follow_redirects=True)

    def sql(self, stmt, params=()):
        # 连接缓存到用例生命周期，调用方才 fetchone()/fetchall() 有效
        if not hasattr(self, "_conn"):
            self._conn = connect(self.app.config["DATABASE"])
        return self._conn.execute(stmt, params)

    def user_id(self, username):
        return self.sql("SELECT id FROM users WHERE username = ?", (username,)).fetchone()["id"]

    def insert_item(self, seller_id, title, price, status="在售", category_id=1,
                    condition="九成新", created_at="2026-10-01 12:00:00"):
        self.sql(
            "INSERT INTO items (title, price, condition, description, status, "
            "category_id, seller_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (title, price, condition, "desc", status, category_id, seller_id, created_at))


class SmokeTests(AppTestCase):
    def test_index_ok(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("商品市场", resp.get_data(as_text=True))

    def test_error_page_404(self):
        resp = self.client.get("/no-such-page")
        self.assertEqual(resp.status_code, 404)
        self.assertIn("资源不存在", resp.get_data(as_text=True))


class AuthTests(AppTestCase):
    def test_register_login_logout(self):
        resp = self.register("alice", "password123", "wx_alice")
        self.assertIn("登录", resp.get_data(as_text=True))
        resp = self.login("alice", "password123")
        self.assertIn("登出", resp.get_data(as_text=True))
        resp = self.logout()
        self.assertIn("登录", resp.get_data(as_text=True))

    def test_register_duplicate_username(self):
        self.register("alice", "password123", "")
        resp = self.register("alice", "password456", "")
        self.assertIn("用户名已存在", resp.get_data(as_text=True))

    def test_register_strips_username(self):
        self.register("  alice  ", "password123", "")
        row = self.sql("SELECT username FROM users WHERE username = 'alice'").fetchone()
        self.assertIsNotNone(row)

    def test_username_case_sensitive(self):
        self.register("Alice", "password123", "")
        self.register("alice", "password123", "")
        count = self.sql("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        self.assertEqual(count, 2)

    def test_register_short_password(self):
        resp = self.register("bob", "12345", "")
        self.assertIn("密码最短 6 位", resp.get_data(as_text=True))
        count = self.sql("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        self.assertEqual(count, 0)

    def test_login_wrong_password_message(self):
        self.register("alice", "password123", "")
        resp = self.login("alice", "wrongpass")
        self.assertIn("用户名或密码错误", resp.get_data(as_text=True))

    def test_logout_post_only(self):
        resp = self.client.get("/logout")
        self.assertEqual(resp.status_code, 405)


if __name__ == "__main__":
    unittest.main()

"""校园二手交易平台测试。"""
import base64
import io
import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database import connect, init_db  # noqa: E402
from app import create_app, escape_like, format_price, to_local_time, parse_price_yuan
import trading  # noqa: E402


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

    def _ready_item(self):
        self.conn.execute("INSERT INTO users (id, username, password) VALUES (1, 'u1', 'p')")
        self.conn.execute(
            "INSERT INTO items (id, title, price, condition, category_id, seller_id) "
            "VALUES (1, 't', 100, '全新', 1, 1)")

    def test_check_order_status(self):
        init_db(self.conn, seed=False)
        self._ready_item()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO orders (item_id, buyer_id, price, status, deal_code) "
                "VALUES (1, 1, 100, '已发货', '123456')")

    def test_deal_code_must_be_six_digits(self):
        init_db(self.conn, seed=False)
        self._ready_item()
        for bad in ("12345", "1234567", "12ab56", ""):
            with self.assertRaises(sqlite3.IntegrityError):
                self.conn.execute(
                    "INSERT INTO orders (item_id, buyer_id, price, deal_code) "
                    "VALUES (1, 1, 100, ?)", (bad,))

    def test_done_requires_both_sides_confirmed(self):
        init_db(self.conn, seed=False)
        self._ready_item()
        # 只有卖家确认，不能算完成——数据库层拦住"一方推到底"
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO orders (item_id, buyer_id, price, status, deal_code, "
                "accepted_at, seller_confirm_at) "
                "VALUES (1, 1, 100, '已完成', '123456', '2026-10-01 10:00:00', "
                "'2026-10-01 10:00:00')")
        self.conn.execute(
            "INSERT INTO orders (item_id, buyer_id, price, status, deal_code, "
            "accepted_at, seller_confirm_at, buyer_confirm_at) "
            "VALUES (1, 1, 100, '已完成', '123456', '2026-10-01 10:00:00', "
            "'2026-10-01 10:00:00', '2026-10-01 10:00:00')")

    def test_delivering_order_needs_accept_time(self):
        init_db(self.conn, seed=False)
        self._ready_item()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO orders (item_id, buyer_id, price, status, deal_code) "
                "VALUES (1, 1, 100, '待交付', '123456')")

    def test_cancelled_order_needs_cancel_by(self):
        init_db(self.conn, seed=False)
        self._ready_item()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO orders (item_id, buyer_id, price, status, deal_code) "
                "VALUES (1, 1, 100, '已取消', '123456')")
        with self.assertRaises(sqlite3.IntegrityError):
            # 未取消的订单不该带取消方
            self.conn.execute(
                "INSERT INTO orders (item_id, buyer_id, price, status, deal_code, cancel_by) "
                "VALUES (1, 1, 100, '待接单', '123456', '买家')")

    def test_partial_unique_index(self):
        init_db(self.conn, seed=False)
        self._ready_item()
        self.conn.execute(
            "INSERT INTO orders (item_id, buyer_id, price, status, deal_code) "
            "VALUES (1, 1, 100, '待接单', '111111')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO orders (item_id, buyer_id, price, status, deal_code) "
                "VALUES (1, 1, 100, '待接单', '222222')")
        # 索引覆盖两种进行中状态：已接单的商品同样不能再被别人下单
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO orders (item_id, buyer_id, price, status, deal_code, "
                "accepted_at) VALUES (1, 1, 100, '待交付', '333333', "
                "'2026-10-01 10:00:00')")
        # 历史订单不受唯一索引限制
        self.conn.execute(
            "INSERT INTO orders (item_id, buyer_id, price, status, deal_code, cancel_by) "
            "VALUES (1, 1, 100, '已取消', '444444', '买家')")
        self.conn.execute(
            "INSERT INTO orders (item_id, buyer_id, price, status, deal_code, "
            "accepted_at, seller_confirm_at, buyer_confirm_at) "
            "VALUES (1, 1, 100, '已完成', '555555', '2026-10-01 10:00:00', "
            "'2026-10-01 10:00:00', '2026-10-01 10:00:00')")

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
        with self.assertRaises(ValueError):
            parse_price_yuan("9" * 12)


class AppTestCase(unittest.TestCase):
    """应用级测试基类：临时数据库 + 测试客户端 + 常用辅助方法。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db_path = str(Path(self.tmp.name) / "test.db")
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test",
            "DATABASE": db_path,
            "UPLOAD_DIR": str(Path(self.tmp.name) / "uploads"),
            "SEED_DATA": False,
            # 绝大多数用例直接 POST 表单，逐个带 token 太啰嗦；
            # CSRF 本身由 CsrfTests 单独开启验证
            "CSRF_ENABLED": False,
            # 超时释放默认关掉：否则用例里手工插入的旧订单会被后台扫描悄悄取消，
            # 需要验证它时由 OrderExpiryTests 自己打开
            "ORDER_TIMEOUT_MINUTES": 0,
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

    def register_user(self, username):
        self.register(username, "password123", "")
        return self.user_id(username)

    def create_item(self, title="高等数学教材", price="25.00", category_id=1,
                    condition="九成新", description="有少量笔记"):
        return self.client.post("/publish", data={
            "title": title, "price": price, "category_id": category_id,
            "condition": condition, "description": description,
        }, follow_redirects=True)


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


class SeedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db_path = str(Path(self.tmp.name) / "seed.db")
        self.app = create_app({
            "TESTING": True, "SECRET_KEY": "test",
            "DATABASE": db_path, "SEED_DATA": True,
        })
        self.conn = connect(db_path)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_seed_counts(self):
        users = self.conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        items = self.conn.execute("SELECT COUNT(*) AS n FROM items").fetchone()["n"]
        orders = self.conn.execute("SELECT COUNT(*) AS n FROM orders").fetchone()["n"]
        self.assertEqual(users, 10)
        self.assertEqual(items, 30)
        self.assertEqual(orders, 12)

    def test_seed_status_consistency(self):
        rows = self.conn.execute(
            "SELECT o.status AS os, i.status AS is_ FROM orders o "
            "JOIN items i ON o.item_id = i.id").fetchall()
        expect = {"待接单": "已预订", "待交付": "已预订",
                  "已完成": "已售", "已取消": "在售"}
        for r in rows:
            self.assertEqual(r["is_"], expect[r["os"]])
        # 反向：每件已预订/已售商品恰有对应状态的订单，成交价快照等于商品价
        for item_status, order_statuses in (("已预订", ("待接单", "待交付")),
                                            ("已售", ("已完成",))):
            marks = ",".join("?" * len(order_statuses))
            orphan = self.conn.execute(
                "SELECT COUNT(*) AS n FROM items i WHERE i.status = ? "
                "AND NOT EXISTS (SELECT 1 FROM orders o WHERE o.item_id = i.id "
                f"AND o.status IN ({marks}))",
                (item_status, *order_statuses)).fetchone()["n"]
            self.assertEqual(orphan, 0)
        bad_price = self.conn.execute(
            "SELECT COUNT(*) AS n FROM orders o JOIN items i ON o.item_id = i.id "
            "WHERE o.price != i.price").fetchone()["n"]
        self.assertEqual(bad_price, 0)

    def test_seed_covers_all_order_states(self):
        states = {r["status"] for r in self.conn.execute(
            "SELECT DISTINCT status FROM orders")}
        self.assertEqual(states, {"待接单", "待交付", "已完成", "已取消"})
        # 演示用的待交付订单：卖家已确认交付、买家还没确认，正好展示双向确认
        row = self.conn.execute(
            "SELECT * FROM orders WHERE status = '待交付'").fetchone()
        self.assertIsNotNone(row["seller_confirm_at"])
        self.assertIsNone(row["buyer_confirm_at"])
        self.assertEqual(len(row["deal_code"]), 6)

    def test_seed_buyer_not_seller(self):
        rows = self.conn.execute(
            "SELECT o.buyer_id, i.seller_id FROM orders o "
            "JOIN items i ON o.item_id = i.id").fetchall()
        for r in rows:
            self.assertNotEqual(r["buyer_id"], r["seller_id"])
        # 每人都既买又卖（设计规格要求）
        buyers = {r["buyer_id"] for r in self.conn.execute(
            "SELECT DISTINCT buyer_id FROM orders")}
        sellers = {r["seller_id"] for r in self.conn.execute(
            "SELECT DISTINCT seller_id FROM items")}
        self.assertEqual(buyers, set(range(1, 11)))
        self.assertEqual(sellers, set(range(1, 11)))


class MarketTests(AppTestCase):
    def test_marks_own_items_only(self):
        sid = self.register_user("seller1")
        other = self.register_user("seller2")
        self.insert_item(sid, "我自己发的", 1000)
        self.insert_item(other, "别人发的", 1000)

        # 匿名：两件都能看到，但都没有标识
        anon = self.client.get("/").get_data(as_text=True)
        self.assertIn("我自己发的", anon)
        self.assertIn("别人发的", anon)
        self.assertNotIn("我发布的", anon)

        # 登录 seller1 后：自己的那件带标识，别人的不带
        self.login("seller1")
        body = self.client.get("/").get_data(as_text=True)
        self.assertIn("我发布的", body)
        self.assertEqual(body.count("我发布的"), 1)
        self.assertIn("别人发的", body)   # 别人的商品仍然展示，不被过滤

    def test_market_shows_only_on_sale(self):
        sid = self.register_user("seller1")
        self.insert_item(sid, "在售商品", 1000, status="在售")
        self.insert_item(sid, "已预订商品", 1000, status="已预订")
        self.insert_item(sid, "已售商品", 1000, status="已售")
        self.insert_item(sid, "下架商品", 1000, status="下架")
        body = self.client.get("/").get_data(as_text=True)
        self.assertIn("在售商品", body)
        self.assertNotIn("已预订商品", body)
        self.assertNotIn("已售商品", body)
        self.assertNotIn("下架商品", body)

    def test_search_by_title(self):
        sid = self.register_user("seller1")
        self.insert_item(sid, "高等数学教材", 1500)
        self.insert_item(sid, "线性代数", 1200)
        body = self.client.get("/?q=数学").get_data(as_text=True)
        self.assertIn("高等数学教材", body)
        self.assertNotIn("线性代数", body)

    def test_search_escapes_percent(self):
        sid = self.register_user("seller1")
        self.insert_item(sid, "期末100%冲刺卷", 1000)
        self.insert_item(sid, "期末冲刺卷", 1000)
        self.insert_item(sid, "100天复习计划", 1000)  # 含 100 但不含 100%
        body = self.client.get("/?q=100%").get_data(as_text=True)
        self.assertIn("期末100%冲刺卷", body)
        self.assertNotIn("期末冲刺卷", body)
        self.assertNotIn("100天复习计划", body)  # 转义失效时 % 成通配符会误中它
        body = self.client.get("/?q=100_").get_data(as_text=True)
        self.assertNotIn("100天复习计划", body)  # 转义失效时 _ 成通配符会误中它

    def test_price_range_filter(self):
        sid = self.register_user("seller1")
        self.insert_item(sid, "便宜货", 500)
        self.insert_item(sid, "中等货", 1500)
        self.insert_item(sid, "贵东西", 5000)
        body = self.client.get("/?min_price=10&max_price=20").get_data(as_text=True)
        self.assertNotIn("便宜货", body)
        self.assertIn("中等货", body)
        self.assertNotIn("贵东西", body)

    def test_price_one_sided(self):
        sid = self.register_user("seller1")
        self.insert_item(sid, "便宜货", 500)
        self.insert_item(sid, "贵东西", 5000)
        body = self.client.get("/?min_price=30").get_data(as_text=True)
        self.assertNotIn("便宜货", body)
        self.assertIn("贵东西", body)
        body = self.client.get("/?max_price=30").get_data(as_text=True)
        self.assertIn("便宜货", body)
        self.assertNotIn("贵东西", body)

    def test_price_min_gt_max_error(self):
        sid = self.register_user("seller1")
        self.insert_item(sid, "某商品", 1000)
        body = self.client.get("/?min_price=50&max_price=10").get_data(as_text=True)
        self.assertIn("最低价不能大于最高价", body)

    def test_price_invalid_error(self):
        body = self.client.get("/?min_price=abc").get_data(as_text=True)
        self.assertIn("价格区间必须为合法数字", body)
        # 超长数字不能 500，同样按非法输入提示
        resp = self.client.get("/?min_price=" + "9" * 25)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("价格区间必须为合法数字", resp.get_data(as_text=True))

    def test_category_filter(self):
        sid = self.register_user("seller1")
        self.insert_item(sid, "教材甲", 1000, category_id=1)
        self.insert_item(sid, "教材乙", 3000, category_id=1)
        self.insert_item(sid, "数码乙", 1000, category_id=2)
        body = self.client.get("/?category_id=1").get_data(as_text=True)
        self.assertIn("教材甲", body)
        self.assertNotIn("数码乙", body)
        # 三条件组合：分类 + 关键词 + 价格
        body = self.client.get("/?category_id=1&q=教材&min_price=20").get_data(as_text=True)
        self.assertNotIn("教材甲", body)  # ¥10 < ¥20
        self.assertIn("教材乙", body)

    def test_invalid_category_ignored(self):
        sid = self.register_user("seller1")
        self.insert_item(sid, "商品甲", 1000, category_id=1)
        resp = self.client.get("/?category_id=999")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("商品甲", resp.get_data(as_text=True))
        # Unicode 数字与超长整数同样按"不存在"忽略，不 500
        for bad in ("²", "①", "9" * 30):
            resp = self.client.get("/?category_id=" + bad)
            self.assertEqual(resp.status_code, 200)
            self.assertIn("商品甲", resp.get_data(as_text=True))

    def test_sort_newest_first(self):
        sid = self.register_user("seller1")
        self.insert_item(sid, "较早商品", 1000, created_at="2026-10-01 12:00:00")
        self.insert_item(sid, "同一秒后发", 1000, created_at="2026-10-01 12:00:00")
        body = self.client.get("/").get_data(as_text=True)
        self.assertLess(body.index("同一秒后发"), body.index("较早商品"))


class DetailTests(AppTestCase):
    def _seller_with_items(self):
        sid = self.register_user("seller1")
        self.insert_item(sid, "在售商品", 2500, status="在售")
        self.insert_item(sid, "已预订商品", 2500, status="已预订")
        self.insert_item(sid, "已售商品", 2500, status="已售")
        self.insert_item(sid, "下架商品", 2500, status="下架")
        return sid

    def test_anon_can_view_on_sale(self):
        self._seller_with_items()
        resp = self.client.get("/items/1")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn("在售商品", body)
        self.assertIn("25.00", body)

    def test_anon_redirected_for_reserved_sold(self):
        self._seller_with_items()
        for path in ("/items/2", "/items/3"):
            resp = self.client.get(path)
            self.assertEqual(resp.status_code, 302)
            self.assertIn("/login", resp.headers["Location"])

    def test_logged_in_can_view_reserved_sold(self):
        sid = self._seller_with_items()
        # 登录的无关用户可看（订单页跳回路径）
        self.register("buyer1", "password123", "")
        self.login("buyer1", "password123")
        for path, title in (("/items/2", "已预订商品"), ("/items/3", "已售商品")):
            resp = self.client.get(path)
            self.assertEqual(resp.status_code, 200)
            self.assertIn(title, resp.get_data(as_text=True))
        # 买家看自己买到的已售商品同样可看
        self.insert_item(sid, "买走的商品", 1000, status="已售")
        self.sql("INSERT INTO orders (item_id, buyer_id, price, status, deal_code, "
                 "accepted_at, seller_confirm_at, buyer_confirm_at) "
                 "VALUES (5, ?, 1000, '已完成', '123456', '2026-10-01 10:00:00', "
                 "'2026-10-01 10:00:00', '2026-10-01 10:00:00')",
                 (self.user_id("buyer1"),))
        resp = self.client.get("/items/5")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("买走的商品", resp.get_data(as_text=True))

    def test_delisted_404_for_anon_and_others(self):
        self._seller_with_items()
        self.assertEqual(self.client.get("/items/4").status_code, 404)
        self.register("buyer1", "password123", "")
        self.login("buyer1", "password123")
        self.assertEqual(self.client.get("/items/4").status_code, 404)
        # 与不存在 id 的响应完全一致：不泄露"曾经存在但已下架"
        self.assertEqual(self.client.get("/items/4").get_data(),
                         self.client.get("/items/999").get_data())

    def test_delisted_visible_to_owner(self):
        sid = self._seller_with_items()
        self.client.post("/login", data={"username": "seller1", "password": "password123"})
        self.assertEqual(self.client.get("/items/4").status_code, 200)

    def test_buy_button_visibility(self):
        self._seller_with_items()
        btn = "下单，等卖家接单"
        # 匿名：无下单入口
        self.assertNotIn(btn, self.client.get("/items/1").get_data(as_text=True))
        # 买家登录：有下单入口
        self.register("buyer1", "password123", "")
        self.login("buyer1", "password123")
        self.assertIn(btn, self.client.get("/items/1").get_data(as_text=True))
        # 卖家看自己的商品：无下单入口
        self.logout()
        self.login("seller1", "password123")
        self.assertNotIn(btn, self.client.get("/items/1").get_data(as_text=True))

    def test_contact_visibility(self):
        self._seller_with_items()
        # 匿名：不含联系方式
        self.assertNotIn("联系方式", self.client.get("/items/1").get_data(as_text=True))
        # 登录：显示联系方式（seller1 注册时 contact 为空）
        self.register("buyer1", "password123", "")
        self.login("buyer1", "password123")
        body = self.client.get("/items/1").get_data(as_text=True)
        self.assertIn("卖家未填写联系方式", body)
        # 有联系方式的卖家：显示真实值
        self.logout()
        self.register("seller2", "password123", "wx_seller2")
        self.login("seller2", "password123")
        sid2 = self.user_id("seller2")
        self.insert_item(sid2, "卖家2的书", 1000)
        self.logout()
        self.login("buyer1", "password123")
        body = self.client.get("/items/5").get_data(as_text=True)
        self.assertIn("wx_seller2", body)


class PublishTests(AppTestCase):
    def test_publish_success(self):
        self.register("seller1", "password123", "")
        self.login("seller1", "password123")
        resp = self.client.post("/publish", data={
            "title": "高等数学教材", "price": "25.00", "category_id": 1,
            "condition": "九成新", "description": "有少量笔记",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers["Location"], "/my-items")  # 发布成功落在我的商品页
        body = self.client.get("/my-items").get_data(as_text=True)
        self.assertIn("高等数学教材", body)
        self.assertIn("25.00", body)
        row = self.sql("SELECT * FROM items").fetchone()
        self.assertEqual(row["price"], 2500)
        self.assertEqual(row["status"], "在售")
        self.assertEqual(row["seller_id"], self.user_id("seller1"))
        self.assertEqual(row["category_id"], 1)

    def test_publish_appears_on_market(self):
        self.register("seller1", "password123", "")
        self.login("seller1", "password123")
        self.create_item()
        self.assertIn("高等数学教材", self.client.get("/").get_data(as_text=True))

    def test_publish_price_zero_rejected(self):
        self.register("seller1", "password123", "")
        self.login("seller1", "password123")
        resp = self.create_item(price="0.00")
        self.assertIn("价格必须大于 0", resp.get_data(as_text=True))
        self.assertEqual(self.sql("SELECT COUNT(*) AS n FROM items").fetchone()["n"], 0)

    def test_publish_price_format_rejected(self):
        self.register("seller1", "password123", "")
        self.login("seller1", "password123")
        for bad in ("19.999", "abc", "-5"):
            resp = self.create_item(price=bad)
            self.assertIn("价格格式不合法", resp.get_data(as_text=True))
        self.assertEqual(self.sql("SELECT COUNT(*) AS n FROM items").fetchone()["n"], 0)

    def test_publish_requires_login(self):
        resp = self.client.get("/publish")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers["Location"])
        count = self.sql("SELECT COUNT(*) AS n FROM items").fetchone()["n"]
        self.assertEqual(count, 0)


class MyItemsTests(AppTestCase):
    def _login_seller(self):
        self.register("seller1", "password123", "")
        self.login("seller1", "password123")

    def test_edit_price_success(self):
        self._login_seller()
        self.create_item()
        resp = self.client.post("/items/1/price", data={"price": "30.00"},
                                follow_redirects=True)
        self.assertIn("30.00", resp.get_data(as_text=True))
        self.assertEqual(self.sql("SELECT price FROM items WHERE id = 1")
                         .fetchone()["price"], 3000)

    def test_edit_price_non_owner_403(self):
        self._login_seller()
        self.create_item()
        self.logout()
        self.register("buyer1", "password123", "")
        self.login("buyer1", "password123")
        resp = self.client.post("/items/1/price", data={"price": "1.00"})
        self.assertEqual(resp.status_code, 403)

    def test_edit_price_wrong_status_rejected(self):
        self._login_seller()
        self.create_item()
        self.sql("UPDATE items SET status = '已预订' WHERE id = 1")
        resp = self.client.post("/items/1/price", data={"price": "30.00"},
                                follow_redirects=True)
        self.assertIn("只有在售商品可以改价", resp.get_data(as_text=True))
        self.assertEqual(self.sql("SELECT price FROM items WHERE id = 1")
                         .fetchone()["price"], 2500)

    def test_delist_success(self):
        self._login_seller()
        self.create_item()
        resp = self.client.post("/items/1/delist", follow_redirects=True)
        self.assertIn("下架", resp.get_data(as_text=True))
        self.assertEqual(self.sql("SELECT status FROM items WHERE id = 1")
                         .fetchone()["status"], "下架")
        self.assertNotIn("高等数学教材", self.client.get("/").get_data(as_text=True))
        # 下架是终态：二次下架被拒绝，状态不变
        resp = self.client.post("/items/1/delist", follow_redirects=True)
        self.assertIn("只有在售商品可以下架", resp.get_data(as_text=True))
        self.assertEqual(self.sql("SELECT status FROM items WHERE id = 1")
                         .fetchone()["status"], "下架")

    def test_delist_wrong_status_rejected(self):
        self._login_seller()
        self.create_item()
        for status in ("已售", "已预订"):
            self.sql("UPDATE items SET status = ? WHERE id = 1", (status,))
            resp = self.client.post("/items/1/delist", follow_redirects=True)
            self.assertIn("只有在售商品可以下架", resp.get_data(as_text=True))
            self.assertEqual(self.sql("SELECT status FROM items WHERE id = 1")
                             .fetchone()["status"], status)


class PlaceOrderTests(AppTestCase):
    def _seller_and_buyer(self):
        self.register("seller1", "password123", "")
        self.login("seller1", "password123")
        self.create_item()          # item 1，价格 25.00 = 2500 分
        self.logout()
        self.register("buyer1", "password123", "")
        self.login("buyer1", "password123")

    def test_buy_success_price_from_db(self):
        self._seller_and_buyer()
        # 伪造客户端价格，验证成交价仍取自数据库
        resp = self.client.post("/items/1/buy", data={"price": "0.01"},
                                follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        item = self.sql("SELECT status FROM items WHERE id = 1").fetchone()
        self.assertEqual(item["status"], "已预订")
        order = self.sql("SELECT * FROM orders").fetchone()
        self.assertEqual(order["price"], 2500)
        self.assertEqual(order["status"], "待接单")
        self.assertEqual(order["buyer_id"], self.user_id("buyer1"))
        self.assertEqual(len(order["deal_code"]), 6)
        self.assertTrue(order["deal_code"].isdigit())
        self.assertIsNone(order["accepted_at"])

    def test_buy_own_item_rejected(self):
        self.register("seller1", "password123", "")
        self.login("seller1", "password123")
        self.create_item()
        resp = self.client.post("/items/1/buy", follow_redirects=True)
        self.assertIn("不能购买自己的商品", resp.get_data(as_text=True))
        self.assertEqual(self.sql("SELECT COUNT(*) AS n FROM orders")
                         .fetchone()["n"], 0)

    def test_buy_non_on_sale_rejected(self):
        self._seller_and_buyer()
        for status in ("已预订", "已售", "下架"):
            self.sql("UPDATE items SET status = ? WHERE id = 1", (status,))
            resp = self.client.post("/items/1/buy", follow_redirects=True)
            self.assertIn("商品已被买走或已下架", resp.get_data(as_text=True))
        self.assertEqual(self.sql("SELECT COUNT(*) AS n FROM orders")
                         .fetchone()["n"], 0)

    def test_double_buy_second_fails(self):
        self._seller_and_buyer()
        self.client.post("/items/1/buy")
        self.logout()
        self.register("buyer2", "password123", "")
        self.login("buyer2", "password123")
        resp = self.client.post("/items/1/buy", follow_redirects=True)
        self.assertIn("商品已被买走或已下架", resp.get_data(as_text=True))
        self.assertEqual(self.sql("SELECT COUNT(*) AS n FROM orders")
                         .fetchone()["n"], 1)
        self.assertEqual(self.sql("SELECT status FROM items WHERE id = 1")
                         .fetchone()["status"], "已预订")

    def test_buy_requires_login(self):
        self.register("seller1", "password123", "")
        self.login("seller1", "password123")
        self.create_item()
        self.logout()
        resp = self.client.post("/items/1/buy")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers["Location"])
        self.assertEqual(self.sql("SELECT COUNT(*) AS n FROM orders")
                         .fetchone()["n"], 0)

    def test_full_lifecycle_rebuy_after_cancel(self):
        # 完整链路：下单→取消→再下单→接单→双方确认完成；唯一索引不误拦历史订单
        self._seller_and_buyer()
        self.client.post("/items/1/buy")
        self.client.post("/orders/1/cancel")
        self.client.post("/items/1/buy")
        self.logout()
        self.login("seller1", "password123")
        self.client.post("/orders/2/accept")
        self.client.post("/orders/2/deliver")
        self.logout()
        self.login("buyer1", "password123")
        self.client.post("/orders/2/receive")
        self.assertEqual(self.sql("SELECT status FROM items WHERE id = 1")
                         .fetchone()["status"], "已售")
        statuses = [r["status"] for r in self.sql(
            "SELECT status FROM orders WHERE item_id = 1 ORDER BY id")]
        self.assertEqual(statuses, ["已取消", "已完成"])

    def test_same_account_buys_and_sells(self):
        # 规格第 7 节：同一账号既买又卖（全员可买可卖模型）
        self.register("alice", "password123", "")
        self.login("alice", "password123")
        self.create_item(title="甲的商品")          # item 1，alice 是卖家
        self.logout()
        self.register("bob", "password123", "")
        self.login("bob", "password123")
        self.create_item(title="乙的商品")          # item 2，bob 是卖家
        self.client.post("/items/1/buy")            # bob 买 alice 的
        self.logout()
        self.login("alice", "password123")
        self.client.post("/items/2/buy")            # alice 买 bob 的
        self.assertIn("乙的商品", self.client.get("/my-orders").get_data(as_text=True))
        self.assertIn("甲的商品", self.client.get("/sales").get_data(as_text=True))
        self.assertEqual(self.sql("SELECT COUNT(*) AS n FROM orders")
                         .fetchone()["n"], 2)


class MyOrdersTests(AppTestCase):
    def _setup_order(self, buyer_contact="wx_buyer", seller_contact="wx_seller"):
        self.register("seller1", "password123", seller_contact)
        self.login("seller1", "password123")
        self.create_item()
        self.logout()
        self.register("buyer1", "password123", buyer_contact)
        self.login("buyer1", "password123")
        self.client.post("/items/1/buy")

    def test_my_orders_shows_seller_contact(self):
        self._setup_order()
        body = self.client.get("/my-orders").get_data(as_text=True)
        self.assertIn("wx_seller", body)
        self.assertIn("seller1", body)
        self.assertIn("待接单", body)

    def test_empty_contact_message(self):
        self._setup_order(seller_contact="")
        body = self.client.get("/my-orders").get_data(as_text=True)
        self.assertIn("对方未填写联系方式", body)

    def test_cancelled_order_visible(self):
        self._setup_order()
        self.client.post("/orders/1/cancel")
        body = self.client.get("/my-orders").get_data(as_text=True)
        self.assertIn("badge st-cancelled", body)  # 断言表格状态单元格，排除 flash 误命中
        self.assertIn("由买家取消", body)

    def test_buyer_cancel_restores_item(self):
        self._setup_order()
        self.client.post("/orders/1/cancel")
        self.assertEqual(self.sql("SELECT status FROM items WHERE id = 1")
                         .fetchone()["status"], "在售")
        self.assertEqual(self.sql("SELECT status FROM orders WHERE id = 1")
                         .fetchone()["status"], "已取消")

    def test_seller_cancel_restores_item(self):
        self._setup_order()
        self.logout()
        self.login("seller1", "password123")
        self.client.post("/orders/1/cancel")
        self.assertEqual(self.sql("SELECT status FROM items WHERE id = 1")
                         .fetchone()["status"], "在售")

    def test_third_party_cancel_403(self):
        self._setup_order()
        self.logout()
        self.register("stranger", "password123", "")
        self.login("stranger", "password123")
        resp = self.client.post("/orders/1/cancel")
        self.assertEqual(resp.status_code, 403)

    def _dealing_order(self):
        """下单后让卖家接单，把订单推进到待交付。"""
        self._setup_order()
        self.logout()
        self.login("seller1", "password123")
        self.client.post("/orders/1/accept")
        self.logout()
        self.login("buyer1", "password123")

    def test_buyer_receive_alone_cannot_finish(self):
        # 卖家没点交付，买家单方确认收货不能把交易推到底
        self._dealing_order()
        resp = self.client.post("/orders/1/receive", follow_redirects=True)
        self.assertIn("等待卖家确认交付", resp.get_data(as_text=True))
        row = self.sql("SELECT status, buyer_confirm_at FROM orders WHERE id = 1").fetchone()
        self.assertEqual(row["status"], "待交付")
        self.assertIsNotNone(row["buyer_confirm_at"])
        self.assertEqual(self.sql("SELECT status FROM items WHERE id = 1")
                         .fetchone()["status"], "已预订")

    def test_both_sides_confirm_finishes_deal(self):
        self._dealing_order()
        self.logout()
        self.login("seller1", "password123")
        self.client.post("/orders/1/deliver")
        self.logout()
        self.login("buyer1", "password123")
        resp = self.client.post("/orders/1/receive", follow_redirects=True)
        self.assertIn("交易完成", resp.get_data(as_text=True))
        order = self.sql("SELECT * FROM orders WHERE id = 1").fetchone()
        self.assertEqual(order["status"], "已完成")
        self.assertIsNotNone(order["seller_confirm_at"])
        self.assertIsNotNone(order["buyer_confirm_at"])
        self.assertEqual(self.sql("SELECT status FROM items WHERE id = 1")
                         .fetchone()["status"], "已售")

    def test_illegal_transition_rejected(self):
        self._setup_order()
        self.client.post("/orders/1/cancel")
        resp = self.client.post("/orders/1/receive", follow_redirects=True)
        self.assertIn("订单还没进入待交付状态", resp.get_data(as_text=True))
        self.assertEqual(self.sql("SELECT status FROM orders WHERE id = 1")
                         .fetchone()["status"], "已取消")

    def test_completed_order_is_read_only(self):
        self._dealing_order()
        self.logout()
        self.login("seller1", "password123")
        self.client.post("/orders/1/deliver")
        self.logout()
        self.login("buyer1", "password123")
        self.client.post("/orders/1/receive")
        resp = self.client.post("/orders/1/cancel", follow_redirects=True)
        self.assertIn("订单状态已变更，无法取消", resp.get_data(as_text=True))
        self.assertEqual(self.sql("SELECT status FROM orders WHERE id = 1")
                         .fetchone()["status"], "已完成")

    def test_data_inconsistency_500(self):
        # 外部把商品改成在售：双方确认时第二个 UPDATE 落空 → 回滚 + 500
        self._dealing_order()
        self.sql("UPDATE items SET status = '在售' WHERE id = 1")
        self.logout()
        self.login("seller1", "password123")
        self.client.post("/orders/1/deliver")
        self.logout()
        self.login("buyer1", "password123")
        resp = self.client.post("/orders/1/receive")
        self.assertEqual(resp.status_code, 500)
        self.assertIn("数据异常", resp.get_data(as_text=True))
        row = self.sql("SELECT status, buyer_confirm_at FROM orders WHERE id = 1").fetchone()
        self.assertEqual(row["status"], "待交付")   # 事务回滚，确认动作没落库
        self.assertIsNone(row["buyer_confirm_at"])

    def test_cancel_data_inconsistency_500(self):
        self._setup_order()
        # 同样的一致性保护走取消路径：商品已被改成已售，回不到在售
        self.sql("UPDATE items SET status = '已售' WHERE id = 1")
        resp = self.client.post("/orders/1/cancel")
        self.assertEqual(resp.status_code, 500)
        self.assertIn("数据异常", resp.get_data(as_text=True))
        self.assertEqual(self.sql("SELECT status FROM orders WHERE id = 1")
                         .fetchone()["status"], "待接单")

    def test_delisted_item_title_plain_text(self):
        self._setup_order()
        self.client.post("/orders/1/cancel")
        self.logout()
        self.login("seller1", "password123")
        self.client.post("/items/1/delist")
        self.logout()
        self.login("buyer1", "password123")
        body = self.client.get("/my-orders").get_data(as_text=True)
        self.assertIn("高等数学教材", body)          # 标题仍显示
        self.assertNotIn('href="/items/1"', body)   # 但不渲染详情链接（下架对买家隐藏）


class SalesTests(AppTestCase):
    def _setup_order(self):
        self.register("seller1", "password123", "")
        self.login("seller1", "password123")
        self.create_item()
        self.logout()
        self.register("buyer1", "password123", "wx_buyer")
        self.login("buyer1", "password123")
        self.client.post("/items/1/buy")
        self.logout()
        self.login("seller1", "password123")

    def test_sales_shows_buyer_contact(self):
        self._setup_order()
        body = self.client.get("/sales").get_data(as_text=True)
        self.assertIn("buyer1", body)
        self.assertIn("wx_buyer", body)

    def test_sales_includes_cancelled(self):
        self._setup_order()
        self.client.post("/orders/1/cancel")
        body = self.client.get("/sales").get_data(as_text=True)
        self.assertIn("badge st-cancelled", body)  # 断言表格状态单元格，排除 flash 文案误命中
        self.assertNotIn("拒绝订单", body)  # 终态只读，无操作按钮
        self.assertNotIn("我已当面交付", body)
        self.assertIn("由卖家取消", body)   # 卖家自己点的取消，记在卖家头上

    def test_seller_accept_from_sales(self):
        self._setup_order()
        resp = self.client.post("/orders/1/accept")
        self.assertEqual(resp.headers["Location"], "/sales")  # 卖家操作后回卖出记录
        row = self.sql("SELECT status, accepted_at FROM orders WHERE id = 1").fetchone()
        self.assertEqual(row["status"], "待交付")
        self.assertIsNotNone(row["accepted_at"])

    def test_buyer_cannot_accept_order(self):
        self._setup_order()
        self.logout()
        self.login("buyer1", "password123")
        resp = self.client.post("/orders/1/accept")
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(self.sql("SELECT status FROM orders WHERE id = 1")
                         .fetchone()["status"], "待接单")

    def test_sales_sorted_newest_first(self):
        self.register("seller1", "password123", "")
        self.login("seller1", "password123")
        self.create_item(title="商品甲")
        self.create_item(title="商品乙")
        self.logout()
        self.register("buyer1", "password123", "")
        self.login("buyer1", "password123")
        self.client.post("/items/1/buy")
        self.client.post("/items/2/buy")
        self.logout()
        self.login("seller1", "password123")
        body = self.client.get("/sales").get_data(as_text=True)
        self.assertLess(body.index("商品乙"), body.index("商品甲"))

    def test_sales_scoped_to_seller(self):
        # 卖出记录只看自己的订单，不得泄露其他卖家的订单与买家联系方式
        self._setup_order()          # seller1 的单（item 1）
        self.logout()
        self.register("seller2", "password123", "")
        self.login("seller2", "password123")
        self.create_item(title="卖家2的商品")   # item 2
        self.logout()
        self.register("buyer2", "password123", "wx_buyer2")
        self.login("buyer2", "password123")
        self.client.post("/items/2/buy")
        self.logout()
        self.login("seller1", "password123")
        body = self.client.get("/sales").get_data(as_text=True)
        self.assertIn("高等数学教材", body)
        self.assertNotIn("卖家2的商品", body)
        self.assertNotIn("buyer2", body)
        self.assertNotIn("wx_buyer2", body)


class OrderFlowTests(AppTestCase):
    """双向确认流程专项：核对码、权限边界、重复操作。"""

    def _order_in_dealing(self):
        self.register("seller1", "password123", "wx_seller")
        self.login("seller1", "password123")
        self.create_item()
        self.logout()
        self.register("buyer1", "password123", "wx_buyer")
        self.login("buyer1", "password123")
        self.client.post("/items/1/buy")
        self.logout()
        self.login("seller1", "password123")
        self.client.post("/orders/1/accept")
        self.logout()
        self.login("buyer1", "password123")

    def test_deal_code_same_on_both_sides(self):
        self._order_in_dealing()
        buyer_view = self.client.get("/my-orders").get_data(as_text=True)
        self.logout()
        self.login("seller1", "password123")
        seller_view = self.client.get("/sales").get_data(as_text=True)
        code = self.sql("SELECT deal_code FROM orders WHERE id = 1").fetchone()["deal_code"]
        self.assertIn("见面核对码", buyer_view)
        self.assertIn(code, buyer_view)
        self.assertIn(code, seller_view)

    def test_buyer_cannot_mark_delivered(self):
        self._order_in_dealing()
        self.assertEqual(self.client.post("/orders/1/deliver").status_code, 403)

    def test_repeat_deliver_rejected(self):
        self._order_in_dealing()
        self.logout()
        self.login("seller1", "password123")
        self.client.post("/orders/1/deliver")
        resp = self.client.post("/orders/1/deliver", follow_redirects=True)
        self.assertIn("已经确认过交付", resp.get_data(as_text=True))
        self.assertEqual(self.sql("SELECT status FROM orders WHERE id = 1")
                         .fetchone()["status"], "待交付")

    def test_cancel_after_accept_restores_item(self):
        self._order_in_dealing()
        self.client.post("/orders/1/cancel")
        self.assertEqual(self.sql("SELECT status FROM items WHERE id = 1")
                         .fetchone()["status"], "在售")
        row = self.sql("SELECT status, cancel_by FROM orders WHERE id = 1").fetchone()
        self.assertEqual(row["status"], "已取消")
        self.assertEqual(row["cancel_by"], "买家")

    def test_third_party_cannot_touch_order(self):
        self._order_in_dealing()
        self.logout()
        self.register("stranger", "password123", "")
        self.login("stranger", "password123")
        for action in ("accept", "deliver", "receive", "cancel"):
            self.assertEqual(self.client.post(f"/orders/1/{action}").status_code, 403)
        row = self.sql("SELECT status FROM orders WHERE id = 1").fetchone()
        self.assertEqual(row["status"], "待交付")

    def test_pending_order_has_no_confirm_buttons(self):
        self.register("seller1", "password123", "")
        self.login("seller1", "password123")
        self.create_item()
        self.logout()
        self.register("buyer1", "password123", "")
        self.login("buyer1", "password123")
        self.client.post("/items/1/buy")
        body = self.client.get("/my-orders").get_data(as_text=True)
        self.assertNotIn("我收到货了", body)   # 还没接单，收货按钮不该出现
        self.assertIn("取消订单", body)


class ImageWebTests(AppTestCase):
    PNG_1PX = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )

    def test_publish_with_image_shows_everywhere(self):
        self.register("seller1", "password123", "")
        self.login("seller1", "password123")
        resp = self.client.post("/publish", data={
            "title": "带图的商品", "price": "12.00", "category_id": "1",
            "condition": "九成新", "description": "",
            "images": (io.BytesIO(self.PNG_1PX), "a.png"),
        }, content_type="multipart/form-data", follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        detail = self.client.get("/items/1").get_data(as_text=True)
        self.assertIn("/uploads/items/", detail)
        market = self.client.get("/").get_data(as_text=True)
        self.assertIn("/uploads/items/", market)

    def test_image_file_is_served(self):
        self.register("seller1", "password123", "")
        self.login("seller1", "password123")
        self.client.post("/publish", data={
            "title": "图", "price": "1.00", "category_id": "1",
            "condition": "全新", "description": "",
            "images": (io.BytesIO(self.PNG_1PX), "a.png"),
        }, content_type="multipart/form-data")
        url = self.sql("SELECT filename FROM item_images").fetchone()["filename"]
        resp = self.client.get(f"/uploads/items/{url}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, self.PNG_1PX)

    def test_rejects_fake_image(self):
        self.register("seller1", "password123", "")
        self.login("seller1", "password123")
        resp = self.client.post("/publish", data={
            "title": "假图", "price": "1.00", "category_id": "1",
            "condition": "全新", "description": "",
            "images": (io.BytesIO(b"MZ\x90\x00fake"), "a.exe"),
        }, content_type="multipart/form-data", follow_redirects=True)
        body = resp.get_data(as_text=True)
        self.assertIn("图片未完全上传", body)
        self.assertEqual(self.sql("SELECT COUNT(*) AS n FROM item_images")
                         .fetchone()["n"], 0)


class ChatWebTests(AppTestCase):
    def _setup(self):
        self.register("seller1", "password123", "")
        self.login("seller1", "password123")
        self.create_item()
        self.logout()
        self.register("buyer1", "password123", "")
        self.login("buyer1", "password123")

    def _post(self, item_id, **data):
        buyer_id = self.user_id("buyer1")
        return self.client.post(f"/messages/{item_id}/{buyer_id}", data=data,
                                follow_redirects=True)

    def test_detail_has_chat_entry(self):
        self._setup()
        body = self.client.get("/items/1").get_data(as_text=True)
        self.assertIn("问问卖家", body)

    def test_buyer_sends_message_and_sees_it(self):
        self._setup()
        self._post(1, body="能便宜点吗")
        page = self.client.get(f"/messages/1/{self.user_id('buyer1')}").get_data(as_text=True)
        self.assertIn("能便宜点吗", page)
        listing = self.client.get("/messages").get_data(as_text=True)
        self.assertIn("seller1", listing)
        self.assertIn("能便宜点吗", listing)

    def test_offer_accepted_changes_price(self):
        self._setup()
        self._post(1, body="", offer_price="18.00")
        msg_id = self.sql("SELECT id FROM messages").fetchone()["id"]
        self.logout()
        self.login("seller1", "password123")
        resp = self.client.post(f"/messages/accept/{msg_id}", follow_redirects=True)
        self.assertIn("已接受报价", resp.get_data(as_text=True))
        self.assertEqual(self.sql("SELECT price FROM items WHERE id = 1")
                         .fetchone()["price"], 1800)

    def test_third_party_gets_403(self):
        self._setup()
        self._post(1, body="hi")
        self.logout()
        self.register("stranger", "password123", "")
        self.login("stranger", "password123")
        resp = self.client.get(f"/messages/1/{self.user_id('buyer1')}")
        self.assertEqual(resp.status_code, 403)

    def test_messages_requires_login(self):
        resp = self.client.get("/messages")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers["Location"])

    def test_empty_message_rejected(self):
        self._setup()
        resp = self._post(1, body="   ")
        self.assertIn("消息不能为空", resp.get_data(as_text=True))


class SettingsTests(AppTestCase):
    def test_settings_update_contact(self):
        self.register("alice", "password123", "old_wx")
        self.login("alice", "password123")
        self.assertIn("old_wx", self.client.get("/settings").get_data(as_text=True))
        resp = self.client.post("/settings", data={"contact": "new_wx"},
                                follow_redirects=True)
        self.assertIn("联系方式已保存", resp.get_data(as_text=True))
        self.assertEqual(self.sql("SELECT contact FROM users WHERE username = 'alice'")
                         .fetchone()["contact"], "new_wx")

    def test_settings_empty_contact_stores_null(self):
        self.register("alice", "password123", "old_wx")
        self.login("alice", "password123")
        self.client.post("/settings", data={"contact": "  "})
        self.assertIsNone(self.sql(
            "SELECT contact FROM users WHERE username = 'alice'")
            .fetchone()["contact"])

    def test_settings_requires_login(self):
        resp = self.client.get("/settings")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers["Location"])

    def test_logout_then_protected_redirects(self):
        self.register("alice", "password123", "")
        self.login("alice", "password123")
        self.logout()
        for path in ("/publish", "/my-items", "/my-orders", "/sales", "/settings"):
            resp = self.client.get(path)
            self.assertEqual(resp.status_code, 302)
            self.assertIn("/login", resp.headers["Location"])

    def test_get_on_post_only_routes_405(self):
        self.register("seller1", "password123", "")
        self.login("seller1", "password123")
        self.create_item()
        self.logout()
        self.register("buyer1", "password123", "")
        self.login("buyer1", "password123")
        self.client.post("/items/1/buy")
        self.assertEqual(self.sql("SELECT COUNT(*) AS n FROM orders").fetchone()["n"], 1)
        for path in ("/items/1/delist", "/items/1/price", "/items/1/buy",
                     "/orders/1/cancel", "/orders/1/accept",
                     "/orders/1/deliver", "/orders/1/receive"):
            self.assertEqual(self.client.get(path).status_code, 405)

    def test_created_at_displayed_local(self):
        self.register("seller1", "password123", "")
        self.login("seller1", "password123")
        self.create_item()
        self.sql("UPDATE items SET created_at = '2026-10-01 04:00:00'")
        body = self.client.get("/my-items").get_data(as_text=True)
        self.assertIn("2026-10-01 12:00:00", body)


class CsrfTests(unittest.TestCase):
    """专门开着 CSRF 校验跑：其余用例为了简洁把它关掉了。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app({
            "TESTING": True, "SECRET_KEY": "test",
            "DATABASE": str(Path(self.tmp.name) / "csrf.db"),
            "UPLOAD_DIR": str(Path(self.tmp.name) / "uploads"),
            "SEED_DATA": False, "CSRF_ENABLED": True,
        })
        self.client = self.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def _token(self, path="/register"):
        body = self.client.get(path).get_data(as_text=True)
        match = re.search(r'name="csrf_token" value="([^"]+)"', body)
        return match.group(1) if match else None

    def test_page_contains_token(self):
        self.assertIsNotNone(self._token())

    def test_post_without_token_rejected(self):
        resp = self.client.post("/register", data={"username": "alice",
                                                   "password": "password123"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("刷新", resp.get_data(as_text=True))
        self.assertEqual(self.client.get("/register").status_code, 200)

    def test_post_with_token_accepted(self):
        token = self._token()
        resp = self.client.post("/register", data={
            "username": "alice", "password": "password123", "csrf_token": token})
        self.assertEqual(resp.status_code, 302)

    def test_forged_token_rejected(self):
        self._token()  # 先让会话拿到合法 token
        resp = self.client.post("/register", data={
            "username": "bob", "password": "password123", "csrf_token": "forged"})
        self.assertEqual(resp.status_code, 400)

    def test_api_uses_bearer_not_cookies(self):
        # 接口不依赖 cookie，天然免疫 CSRF，中间件不该拦
        resp = self.client.post("/api/v1/auth/register",
                                json={"username": "carol", "password": "password123"})
        self.assertEqual(resp.get_json()["code"], 0)


class LoginRateLimitTests(AppTestCase):
    def test_blocks_after_five_failures(self):
        self.register("alice", "password123", "")
        for _ in range(5):
            self.client.post("/login", data={"username": "alice", "password": "wrong"})
        resp = self.client.post("/login", data={"username": "alice",
                                                "password": "password123"},
                                follow_redirects=True)
        self.assertIn("尝试次数过多", resp.get_data(as_text=True))
        # 被限流时即使密码正确也不放行
        self.assertNotIn("登出", resp.get_data(as_text=True))

    def test_success_resets_counter(self):
        self.register("alice", "password123", "")
        for _ in range(3):
            self.client.post("/login", data={"username": "alice", "password": "wrong"})
        ok = self.client.post("/login", data={"username": "alice",
                                              "password": "password123"},
                              follow_redirects=True)
        self.assertIn("登出", ok.get_data(as_text=True))
        # 计数清零后还能继续试错，不会因为累计而被误锁
        resp = self.client.post("/login", data={"username": "alice",
                                                "password": "wrong"},
                                follow_redirects=True)
        self.assertIn("用户名或密码错误", resp.get_data(as_text=True))


class MigrationTests(unittest.TestCase):
    """迁移脚本里的 DDL 必须和 schema.sql 完全一致，否则老库和新库会渐渐走偏。"""

    V1_SCHEMA = """
    CREATE TABLE categories (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
    CREATE TABLE users (
        id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE,
        password TEXT NOT NULL, contact TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')));
    CREATE TABLE items (
        id INTEGER PRIMARY KEY, title TEXT NOT NULL,
        price INTEGER NOT NULL CHECK (price > 0),
        condition TEXT NOT NULL, description TEXT, status TEXT NOT NULL DEFAULT '在售',
        category_id INTEGER NOT NULL, seller_id INTEGER NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now')));
    CREATE TABLE orders (
        id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL, buyer_id INTEGER NOT NULL,
        price INTEGER NOT NULL CHECK (price > 0), status TEXT NOT NULL DEFAULT '待交易'
        CHECK (status IN ('待交易','已完成','已取消')),
        created_at TEXT NOT NULL DEFAULT (datetime('now')));
    CREATE UNIQUE INDEX idx_orders_active_item ON orders(item_id) WHERE status = '待交易';
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _v1_db(self):
        path = self.dir / "v1.db"
        conn = connect(str(path))
        conn.executescript(self.V1_SCHEMA)
        conn.execute("INSERT INTO users (id, username, password) VALUES (1, 'u1', 'p')")
        conn.execute("INSERT INTO users (id, username, password) VALUES (2, 'u2', 'p')")
        conn.execute("INSERT INTO items (id, title, price, condition, category_id, seller_id) "
                     "VALUES (1, 'a', 100, '全新', 1, 1)")
        conn.executemany(
            "INSERT INTO orders (id, item_id, buyer_id, price, status, created_at) "
            "VALUES (?, 1, 2, 100, ?, '2026-10-01 10:00:00')",
            [(1, "待交易"), (2, "已完成"), (3, "已取消")])
        conn.close()
        return path

    def test_migration_maps_old_statuses(self):
        from migrate_v2 import migrate
        path = self._v1_db()
        self.assertTrue(migrate(path, quiet=True))
        conn = connect(str(path))
        rows = {r["id"]: r["status"] for r in conn.execute("SELECT id, status FROM orders")}
        self.assertEqual(rows, {1: "待接单", 2: "已完成", 3: "已取消"})
        done = conn.execute("SELECT * FROM orders WHERE id = 2").fetchone()
        self.assertIsNotNone(done["seller_confirm_at"])
        self.assertIsNotNone(done["buyer_confirm_at"])
        cancelled = conn.execute("SELECT cancel_by FROM orders WHERE id = 3").fetchone()
        self.assertEqual(cancelled["cancel_by"], "系统")  # v1 没记录取消方，如实标注
        conn.close()

    def test_migration_is_idempotent(self):
        from migrate_v2 import migrate
        path = self._v1_db()
        self.assertTrue(migrate(path, quiet=True))
        self.assertFalse(migrate(path, quiet=True))  # 第二次直接跳过，不会二次重建表

    def test_migrated_and_fresh_schema_match(self):
        from migrate_v2 import migrate
        path = self._v1_db()
        migrate(path, quiet=True)
        migrated = connect(str(path))

        fresh_path = self.dir / "fresh.db"
        fresh = connect(str(fresh_path))
        init_db(fresh, seed=False)

        cols = lambda c: [(r[1], r[2], r[3], r[5]) for r in
                          c.execute("PRAGMA table_info(orders)")]
        self.assertEqual(cols(migrated), cols(fresh))

        idx = lambda c: sorted(r[1] for r in c.execute("PRAGMA index_list(orders)"))
        self.assertEqual(idx(migrated), idx(fresh))

        migrated.close()
        fresh.close()


class ItemEditTests(AppTestCase):
    """发布后能改描述/成色/图片，不用下架重发。"""

    def setUp(self):
        super().setUp()
        self.seller = self.register_user("seller1")
        self.login("seller1", "password123")
        self.item_id = None

    def _publish(self, title="高等数学教材"):
        self.create_item(title=title)
        self.item_id = self.sql(
            "SELECT id FROM items ORDER BY id DESC LIMIT 1").fetchone()["id"]
        return self.item_id

    def test_seller_can_edit_all_fields(self):
        self._publish()
        resp = self.client.post(f"/items/{self.item_id}/edit", data={
            "title": "高等数学（改）", "price": "39.90", "category_id": 2,
            "condition": "八成新", "description": "改过的描述",
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        row = self.sql("SELECT * FROM items WHERE id = ?", (self.item_id,)).fetchone()
        self.assertEqual(row["title"], "高等数学（改）")
        self.assertEqual(row["price"], 3990)
        self.assertEqual(row["category_id"], 2)
        self.assertEqual(row["condition"], "八成新")
        self.assertEqual(row["description"], "改过的描述")

    def test_other_user_gets_403(self):
        self._publish()
        self.logout()
        self.register_user("stranger")
        self.login("stranger", "password123")
        resp = self.client.post(f"/items/{self.item_id}/edit", data={
            "title": "偷改", "price": "1.00", "category_id": 1,
            "condition": "全新", "description": "",
        })
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(
            self.sql("SELECT title FROM items WHERE id = ?", (self.item_id,)).fetchone()["title"],
            "高等数学教材")

    def test_reserved_item_cannot_be_edited(self):
        self._publish()
        self.sql("UPDATE items SET status = '已预订' WHERE id = ?", (self.item_id,))
        resp = self.client.get(f"/items/{self.item_id}/edit", follow_redirects=True)
        self.assertIn("只有「在售」商品可以编辑", resp.get_data(as_text=True))

    def test_bad_price_keeps_original(self):
        self._publish()
        resp = self.client.post(f"/items/{self.item_id}/edit", data={
            "title": "新名字", "price": "abc", "category_id": 1,
            "condition": "九成新", "description": "x",
        }, follow_redirects=True)
        self.assertIn("价格", resp.get_data(as_text=True))
        row = self.sql("SELECT title, price FROM items WHERE id = ?",
                       (self.item_id,)).fetchone()
        self.assertEqual(row["title"], "高等数学教材")   # 校验失败一条都不许落库
        self.assertEqual(row["price"], 2500)

    def _add_photos(self, count=2):
        folder = Path(self.app.config["UPLOAD_DIR"]) / "items"
        folder.mkdir(parents=True, exist_ok=True)
        ids = []
        for i in range(count):
            name = f"{self.item_id}_seed{i}.png"
            (folder / name).write_bytes(b"not-a-real-png")
            self.sql("INSERT INTO item_images (item_id, filename, position) VALUES (?, ?, ?)",
                     (self.item_id, name, i))
            ids.append(self.sql("SELECT id FROM item_images ORDER BY id DESC LIMIT 1")
                       .fetchone()["id"])
        return ids

    def test_delete_image_repacks_positions(self):
        self._publish()
        ids = self._add_photos(3)
        resp = self.client.post(
            f"/items/{self.item_id}/images/{ids[0]}/delete", follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        rows = self.sql("SELECT id, filename, position FROM item_images "
                        "WHERE item_id = ? ORDER BY position", (self.item_id,)).fetchall()
        self.assertEqual(len(rows), 2)
        # position 必须重排成 0、1，不能留空洞（0~4 的 CHECK 约束不允许）
        self.assertEqual([r["position"] for r in rows], [0, 1])
        gone = Path(self.app.config["UPLOAD_DIR"]) / "items" / f"{self.item_id}_seed0.png"
        self.assertFalse(gone.exists())   # 文件也真的删掉了，不留孤儿

    def test_only_owner_can_delete_image(self):
        self._publish()
        ids = self._add_photos(1)
        self.logout()
        self.register_user("stranger")
        self.login("stranger", "password123")
        resp = self.client.post(f"/items/{self.item_id}/images/{ids[0]}/delete")
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(self.sql("SELECT COUNT(*) AS n FROM item_images WHERE item_id = ?",
                                  (self.item_id,)).fetchone()["n"], 1)


class ThreadPagingTests(AppTestCase):
    """聊天记录分页 + 新消息轮询。

    老实现是「从最早一条取 100 条」：对话超过 100 句后，新消息永远刷不出来。
    这里固定住「取最近 N 条」的行为。
    """

    def setUp(self):
        super().setUp()
        self.seller = self.register_user("seller1")
        self.buyer = self.register_user("buyer1")
        self.login("seller1", "password123")
        self.create_item(title="护眼台灯")
        self.item_id = self.sql(
            "SELECT id FROM items ORDER BY id DESC LIMIT 1").fetchone()["id"]
        self.login("buyer1", "password123")

    def _seed_messages(self, count):
        for i in range(count):
            sender = self.buyer if i % 2 == 0 else self.seller
            self.sql("INSERT INTO messages (item_id, buyer_id, sender_id, kind, body) "
                     "VALUES (?, ?, ?, 'text', ?)",
                     (self.item_id, self.buyer, sender, f"第{i + 1}句"))

    def _thread(self, url=None):
        return self.client.get(
            url or f"/messages/{self.item_id}/{self.buyer}").get_data(as_text=True)

    def test_shows_latest_page_not_earliest(self):
        self._seed_messages(60)
        body = self._thread()
        self.assertEqual(body.count('class="bubble-row'), 50)
        self.assertIn("第60句", body)      # 最新的一定在
        self.assertNotIn("第10句", body)   # 最早的留给「查看更早」
        self.assertIn("查看更早的消息", body)

    def test_before_id_loads_older_batch(self):
        self._seed_messages(60)
        first_id = self.sql(
            "SELECT id FROM messages ORDER BY id LIMIT 1 OFFSET 10").fetchone()["id"]
        body = self._thread(f"/messages/{self.item_id}/{self.buyer}?before={first_id}")
        self.assertEqual(body.count('class="bubble-row'), 10)
        self.assertIn("第10句", body)
        self.assertNotIn("查看更早的消息", body)   # 到头了就别再给链接

    def test_bad_before_param_does_not_500(self):
        self._seed_messages(3)
        resp = self.client.get(f"/messages/{self.item_id}/{self.buyer}?before=abc")
        self.assertEqual(resp.status_code, 200)

    def test_updates_returns_only_newer_messages(self):
        self._seed_messages(3)
        last = self.sql("SELECT MAX(id) AS m FROM messages").fetchone()["m"]
        self.sql("INSERT INTO messages (item_id, buyer_id, sender_id, kind, body) "
                 "VALUES (?, ?, ?, 'text', ?)", (self.item_id, self.buyer, self.seller, "刚发的"))
        resp = self.client.get(
            f"/messages/{self.item_id}/{self.buyer}/updates?after={last}")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual([m["body"] for m in data["messages"]], ["刚发的"])
        self.assertFalse(data["messages"][0]["mine"])   # 卖家发的，对买家来说不是自己的

    def test_updates_rejects_outsider(self):
        self._seed_messages(2)
        self.logout()
        self.register_user("stranger")
        self.login("stranger", "password123")
        resp = self.client.get(f"/messages/{self.item_id}/{self.buyer}/updates?after=0")
        self.assertEqual(resp.status_code, 403)


class OrderExpiryTests(AppTestCase):
    """待接单超时释放：下单后卖家不理，商品不能一直锁着。

    这类用例会真的改库存状态，所以基类默认把扫描关掉（ORDER_TIMEOUT_MINUTES=0），
    由这里自己打开，免得别的用例插入的旧订单被后台顺手取消。
    """

    def setUp(self):
        super().setUp()
        self.seller = self.register_user("seller1")
        self.buyer = self.register_user("buyer2")
        self.insert_item(self.seller, "闲置自行车", 5000)
        self.item_id = self.sql("SELECT id FROM items ORDER BY id DESC LIMIT 1").fetchone()["id"]

    def _order(self, status="待接单", created_at="-3 hours", accepted=False):
        """直接写库造订单：状态机不允许「两小时前下单」，这里就是在模拟历史数据。"""
        self.sql(
            "INSERT INTO orders (item_id, buyer_id, price, status, deal_code, "
            "accepted_at, created_at) VALUES (?, ?, ?, ?, '123456', ?, datetime('now', ?))",
            (self.item_id, self.buyer, 5000, status,
             "2026-10-01 10:00:00" if accepted else None, created_at))
        order_id = self.sql("SELECT id FROM orders ORDER BY id DESC LIMIT 1").fetchone()["id"]
        if status in ("待接单", "待交付"):
            self.sql("UPDATE items SET status = '已预订' WHERE id = ?", (self.item_id,))
        return order_id

    def _status(self, order_id):
        return self.sql("SELECT status, cancel_by FROM orders WHERE id = ?",
                        (order_id,)).fetchone()

    def test_stale_pending_order_is_released(self):
        order_id = self._order()
        self.assertEqual(trading.expire_stale_orders(self._conn, 120), 1)
        row = self._status(order_id)
        self.assertEqual(row["status"], "已取消")
        self.assertEqual(row["cancel_by"], "系统")   # 取消方记「系统」，不冤枉任何一方
        item = self.sql("SELECT status FROM items WHERE id = ?", (self.item_id,)).fetchone()
        self.assertEqual(item["status"], "在售")

    def test_fresh_pending_order_is_untouched(self):
        order_id = self._order(created_at="-10 minutes")
        self.assertEqual(trading.expire_stale_orders(self._conn, 120), 0)
        self.assertEqual(self._status(order_id)["status"], "待接单")

    def test_accepted_order_is_never_expired(self):
        # 卖家已经接单＝双方进入了交付流程，这时候取消会坑掉买家，无论多久都不动
        order_id = self._order(status="待交付", created_at="-5 days", accepted=True)
        self.assertEqual(trading.expire_stale_orders(self._conn, 120), 0)
        self.assertEqual(self._status(order_id)["status"], "待交付")

    def test_zero_minutes_disables_sweep(self):
        order_id = self._order(created_at="-30 days")
        self.assertEqual(trading.expire_stale_orders(self._conn, 0), 0)
        self.assertEqual(self._status(order_id)["status"], "待接单")

    def test_request_sweeps_without_any_cron(self):
        order_id = self._order()
        self.app.config["ORDER_TIMEOUT_MINUTES"] = 120
        self.app.extensions.pop("last_order_sweep", None)   # 绕过 30 秒节流
        self.login("seller1", "password123")
        resp = self.client.get("/sales")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("已取消", resp.get_data(as_text=True))
        self.assertEqual(self._status(order_id)["cancel_by"], "系统")

    def test_released_item_can_be_bought_again(self):
        self._order()
        trading.expire_stale_orders(self._conn, 120)
        self.login("buyer2", "password123")
        self.client.post(f"/items/{self.item_id}/buy")
        item = self.sql("SELECT status FROM items WHERE id = ?", (self.item_id,)).fetchone()
        self.assertEqual(item["status"], "已预订")   # 商品确实回到了可下单状态


if __name__ == "__main__":
    unittest.main()
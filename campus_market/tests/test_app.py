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
        expect = {"待交易": "已预订", "已完成": "已售", "已取消": "在售"}
        for r in rows:
            self.assertEqual(r["is_"], expect[r["os"]])
        # 反向：每件已预订/已售商品恰有对应状态的订单，成交价快照等于商品价
        for item_status, order_status in (("已预订", "待交易"), ("已售", "已完成")):
            orphan = self.conn.execute(
                "SELECT COUNT(*) AS n FROM items i WHERE i.status = ? "
                "AND NOT EXISTS (SELECT 1 FROM orders o WHERE o.item_id = i.id "
                "AND o.status = ?)", (item_status, order_status)).fetchone()["n"]
            self.assertEqual(orphan, 0)
        bad_price = self.conn.execute(
            "SELECT COUNT(*) AS n FROM orders o JOIN items i ON o.item_id = i.id "
            "WHERE o.price != i.price").fetchone()["n"]
        self.assertEqual(bad_price, 0)

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
        self.sql("INSERT INTO orders (item_id, buyer_id, price, status) "
                 "VALUES (5, ?, 1000, '已完成')", (self.user_id("buyer1"),))
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
        # 匿名：无购买按钮
        self.assertNotIn("购买", self.client.get("/items/1").get_data(as_text=True))
        # 买家登录：有购买按钮
        self.register("buyer1", "password123", "")
        self.login("buyer1", "password123")
        self.assertIn("购买", self.client.get("/items/1").get_data(as_text=True))
        # 卖家看自己的商品：无购买按钮
        self.logout()
        self.login("seller1", "password123")
        self.assertNotIn("购买", self.client.get("/items/1").get_data(as_text=True))

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
        resp = self.create_item()
        body = resp.get_data(as_text=True)
        self.assertIn("高等数学教材", body)   # 落在我的商品页
        self.assertIn("25.00", body)
        row = self.sql("SELECT * FROM items").fetchone()
        self.assertEqual(row["price"], 2500)
        self.assertEqual(row["status"], "在售")

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

    def test_publish_requires_login(self):
        resp = self.client.get("/publish")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers["Location"])
        count = self.sql("SELECT COUNT(*) AS n FROM items").fetchone()["n"]
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()

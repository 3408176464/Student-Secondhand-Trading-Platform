"""接口 /api/v1 的测试。"""
import base64
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app  # noqa: E402
from database import connect  # noqa: E402

# 1x1 的合法 PNG，用来验证图片上传链路（文件头能过魔数校验）
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "api.db")
        self.app = create_app({
            "TESTING": True, "SECRET_KEY": "test",
            "DATABASE": self.db_path, "SEED_DATA": False,
            "UPLOAD_DIR": str(Path(self.tmp.name) / "uploads"),
            "CSRF_ENABLED": False,
            # 后台超时扫描会让手工插入的旧订单被取消，接口用例默认关掉
            "ORDER_TIMEOUT_MINUTES": 0,
        })
        self.client = self.app.test_client()
        self.tokens = {}

    def tearDown(self):
        if hasattr(self, "_conn"):
            self._conn.close()
        self.tmp.cleanup()

    # ---------- 辅助 ----------

    def call(self, method, path, token=None, **kwargs):
        headers = kwargs.pop("headers", {})
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return self.client.open(path, method=method, headers=headers, **kwargs)

    def register(self, username, password="password123", contact=""):
        resp = self.call("POST", "/api/v1/auth/register",
                         json={"username": username, "password": password,
                               "contact": contact})
        data = resp.get_json()
        if data["code"] == 0:
            self.tokens[username] = data["data"]["token"]
        return data

    def token(self, username):
        return self.tokens[username]

    def publish(self, username, title="高等数学教材", price="25.00", **extra):
        payload = {"title": title, "price": price, "category_id": 1,
                   "condition": "九成新", "description": "有少量笔记"}
        payload.update(extra)
        return self.call("POST", "/api/v1/items", token=self.token(username),
                         json=payload).get_json()

    def sql(self, stmt, params=()):
        if not hasattr(self, "_conn"):
            self._conn = connect(self.db_path)
        return self._conn.execute(stmt, params)


class AuthApiTests(ApiTestCase):
    def test_register_returns_token(self):
        data = self.register("alice")
        self.assertEqual(data["code"], 0)
        self.assertTrue(data["data"]["token"])
        self.assertEqual(data["data"]["user"]["username"], "alice")

    def test_register_duplicate_username(self):
        self.register("alice")
        data = self.register("alice")
        self.assertEqual(data["code"], 409)
        self.assertIn("用户名已存在", data["message"])

    def test_register_password_too_short(self):
        data = self.register("bob", password="123")
        self.assertEqual(data["code"], 400)
        self.assertIn("密码最短 6 位", data["message"])

    def test_login_ok_and_bad_password(self):
        self.register("alice")
        ok = self.call("POST", "/api/v1/auth/login",
                       json={"username": "alice", "password": "password123"}).get_json()
        self.assertEqual(ok["code"], 0)
        bad = self.call("POST", "/api/v1/auth/login",
                        json={"username": "alice", "password": "wrong"}).get_json()
        self.assertEqual(bad["code"], 401)

    def test_me_requires_token(self):
        resp = self.call("GET", "/api/v1/me")
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.get_json()["code"], 401)

    def test_me_and_update_contact(self):
        self.register("alice", contact="wx_old")
        me = self.call("GET", "/api/v1/me", token=self.token("alice")).get_json()
        self.assertEqual(me["data"]["contact"], "wx_old")
        updated = self.call("PATCH", "/api/v1/me", token=self.token("alice"),
                            json={"contact": "wx_new"}).get_json()
        self.assertEqual(updated["data"]["contact"], "wx_new")

    def test_invalid_token_treated_as_anonymous(self):
        resp = self.call("GET", "/api/v1/me", token="not-a-real-token")
        self.assertEqual(resp.status_code, 401)

    def test_wx_login_without_appid_configured(self):
        resp = self.call("POST", "/api/v1/auth/wx-login", json={"code": "abc"})
        self.assertEqual(resp.status_code, 501)
        self.assertIn("AppID", resp.get_json()["message"])


class CatalogApiTests(ApiTestCase):
    def test_categories_and_meta(self):
        cats = self.call("GET", "/api/v1/categories").get_json()
        self.assertEqual(len(cats["data"]), 7)
        meta = self.call("GET", "/api/v1/meta").get_json()
        self.assertIn("九成新", meta["data"]["conditions"])

    def test_publish_and_list(self):
        self.register("seller1")
        created = self.publish("seller1")
        self.assertEqual(created["code"], 0)
        items = self.call("GET", "/api/v1/items").get_json()
        self.assertEqual(len(items["data"]), 1)
        self.assertEqual(items["data"][0]["price"], 2500)
        self.assertEqual(items["data"][0]["price_display"], "25.00")

    def test_publish_invalid_price(self):
        self.register("seller1")
        for bad in ("0", "0.00", "19.999", "abc"):
            data = self.publish("seller1", price=bad)
            self.assertEqual(data["code"], 400, bad)

    def test_search_and_filter(self):
        self.register("seller1")
        self.publish("seller1", title="高数教材", price="10.00")
        self.publish("seller1", title="线性代数", price="30.00")
        hit = self.call("GET", "/api/v1/items?q=高数").get_json()["data"]
        self.assertEqual([i["title"] for i in hit], ["高数教材"])
        ranged = self.call("GET", "/api/v1/items?min_price=20&max_price=40").get_json()["data"]
        self.assertEqual([i["title"] for i in ranged], ["线性代数"])

    def test_invalid_price_filter_returns_warning(self):
        self.register("seller1")
        self.publish("seller1")
        data = self.call("GET", "/api/v1/items?min_price=abc").get_json()
        self.assertEqual(data["code"], 0)
        self.assertIn("价格区间", data["warning"])

    def test_detail_hides_delisted_item(self):
        self.register("seller1")
        item_id = self.publish("seller1")["data"]["id"]
        self.call("POST", f"/api/v1/items/{item_id}/delist", token=self.token("seller1"))
        resp = self.call("GET", f"/api/v1/items/{item_id}")
        self.assertEqual(resp.status_code, 404)

    def test_detail_contact_only_when_logged_in(self):
        self.register("seller1", contact="wx_seller")
        self.register("buyer1")
        item_id = self.publish("seller1")["data"]["id"]
        anon = self.call("GET", f"/api/v1/items/{item_id}").get_json()["data"]
        self.assertIsNone(anon["seller_contact"])
        self.assertFalse(anon["can_buy"])
        logged = self.call("GET", f"/api/v1/items/{item_id}",
                           token=self.token("buyer1")).get_json()["data"]
        self.assertEqual(logged["seller_contact"], "wx_seller")
        self.assertTrue(logged["can_buy"])

    def test_detail_reserved_requires_login(self):
        self.register("seller1")
        self.register("buyer1")
        item_id = self.publish("seller1")["data"]["id"]
        self.call("POST", f"/api/v1/items/{item_id}/orders", token=self.token("buyer1"))
        resp = self.call("GET", f"/api/v1/items/{item_id}")
        self.assertEqual(resp.status_code, 401)

    def test_list_marks_own_items(self):
        self.register("seller1")
        self.register("seller2")
        self.publish("seller1", title="甲的")
        self.publish("seller2", title="乙的")
        data = self.call("GET", "/api/v1/items",
                         token=self.token("seller1")).get_json()["data"]
        flags = {i["title"]: i["is_mine"] for i in data}
        self.assertEqual(flags, {"甲的": True, "乙的": False})

    def test_my_items(self):
        self.register("seller1")
        self.publish("seller1", title="甲")
        self.register("seller2")
        self.publish("seller2", title="乙")
        mine = self.call("GET", "/api/v1/my/items", token=self.token("seller1")).get_json()
        self.assertEqual([i["title"] for i in mine["data"]], ["甲"])


class OrderApiTests(ApiTestCase):
    def _setup(self):
        self.register("seller1", contact="wx_seller")
        self.register("buyer1", contact="wx_buyer")
        item_id = self.publish("seller1")["data"]["id"]
        order = self.call("POST", f"/api/v1/items/{item_id}/orders",
                          token=self.token("buyer1")).get_json()
        return item_id, order

    def test_place_order_pending(self):
        item_id, order = self._setup()
        self.assertEqual(order["code"], 0)
        self.assertEqual(order["data"]["status"], "待接单")
        self.assertIsNone(order["data"]["deal_code"])  # 还没接单，核对码先不给
        self.assertEqual(order["data"]["role"], "buyer")
        self.assertEqual(order["data"]["counterparty"]["contact"], "wx_seller")
        self.assertTrue(order["data"]["actions"]["cancel"])
        self.assertFalse(order["data"]["actions"]["receive"])
        item = self.sql("SELECT status FROM items WHERE id = ?", (item_id,)).fetchone()
        self.assertEqual(item["status"], "已预订")
        code = self.sql("SELECT deal_code FROM orders").fetchone()["deal_code"]
        self.assertEqual(len(code), 6)
        self.assertTrue(code.isdigit())

    def test_place_order_without_login(self):
        self.register("seller1")
        item_id = self.publish("seller1")["data"]["id"]
        resp = self.call("POST", f"/api/v1/items/{item_id}/orders")
        self.assertEqual(resp.status_code, 401)

    def test_cannot_buy_own_item(self):
        self.register("seller1")
        item_id = self.publish("seller1")["data"]["id"]
        data = self.call("POST", f"/api/v1/items/{item_id}/orders",
                         token=self.token("seller1")).get_json()
        self.assertEqual(data["code"], 400)
        self.assertIn("不能购买自己的商品", data["message"])

    def test_accept_requires_seller(self):
        _, order = self._setup()
        order_id = order["data"]["id"]
        buyer_try = self.call("POST", f"/api/v1/orders/{order_id}/accept",
                              token=self.token("buyer1")).get_json()
        self.assertEqual(buyer_try["code"], 403)
        seller_ok = self.call("POST", f"/api/v1/orders/{order_id}/accept",
                              token=self.token("seller1")).get_json()
        self.assertEqual(seller_ok["data"]["status"], "待交付")
        self.assertTrue(seller_ok["data"]["actions"]["deliver"])

    def test_full_flow_needs_both_sides(self):
        item_id, order = self._setup()
        order_id = order["data"]["id"]
        self.call("POST", f"/api/v1/orders/{order_id}/accept", token=self.token("seller1"))

        # 卖家交付后，交易仍未完成
        delivered = self.call("POST", f"/api/v1/orders/{order_id}/deliver",
                              token=self.token("seller1")).get_json()
        self.assertEqual(delivered["data"]["status"], "待交付")
        self.assertTrue(delivered["data"]["seller_confirmed"])
        self.assertFalse(delivered["data"]["finished"])

        # 买家收货才收尾
        received = self.call("POST", f"/api/v1/orders/{order_id}/receive",
                             token=self.token("buyer1")).get_json()
        self.assertEqual(received["data"]["status"], "已完成")
        self.assertTrue(received["data"]["finished"])
        self.assertIsNone(received["data"]["deal_code"])  # 完成后不再暴露核对码
        item = self.sql("SELECT status FROM items WHERE id = ?", (item_id,)).fetchone()
        self.assertEqual(item["status"], "已售")

    def test_buyer_receive_alone_does_not_finish(self):
        item_id, order = self._setup()
        order_id = order["data"]["id"]
        self.call("POST", f"/api/v1/orders/{order_id}/accept", token=self.token("seller1"))
        data = self.call("POST", f"/api/v1/orders/{order_id}/receive",
                         token=self.token("buyer1")).get_json()
        self.assertEqual(data["data"]["status"], "待交付")
        self.assertFalse(data["data"]["finished"])
        self.assertIn("等待卖家确认交付", data["message"])

    def test_cancel_restores_item(self):
        item_id, order = self._setup()
        order_id = order["data"]["id"]
        data = self.call("POST", f"/api/v1/orders/{order_id}/cancel",
                         token=self.token("buyer1")).get_json()
        self.assertEqual(data["data"]["status"], "已取消")
        self.assertEqual(data["data"]["cancel_by"], "买家")
        item = self.sql("SELECT status FROM items WHERE id = ?", (item_id,)).fetchone()
        self.assertEqual(item["status"], "在售")

    def test_my_orders_and_sales_scoped(self):
        _, order = self._setup()
        buyer_list = self.call("GET", "/api/v1/my/orders", token=self.token("buyer1")).get_json()
        self.assertEqual(len(buyer_list["data"]), 1)
        self.assertEqual(buyer_list["data"][0]["counterparty"]["username"], "seller1")
        seller_sales = self.call("GET", "/api/v1/my/sales", token=self.token("seller1")).get_json()
        self.assertEqual(len(seller_sales["data"]), 1)
        self.assertEqual(seller_sales["data"][0]["role"], "seller")
        self.assertEqual(seller_sales["data"][0]["counterparty"]["username"], "buyer1")
        self.assertTrue(seller_sales["data"][0]["actions"]["accept"])

    def test_third_party_cannot_operate(self):
        _, order = self._setup()
        order_id = order["data"]["id"]
        self.register("stranger")
        for action in ("accept", "cancel", "deliver", "receive"):
            data = self.call("POST", f"/api/v1/orders/{order_id}/{action}",
                             token=self.token("stranger")).get_json()
            self.assertEqual(data["code"], 403, action)

    def test_unknown_order_404(self):
        self.register("alice")
        data = self.call("POST", "/api/v1/orders/999/accept",
                         token=self.token("alice")).get_json()
        self.assertEqual(data["code"], 404)


class ImageApiTests(ApiTestCase):
    def upload(self, item_id, username, content=PNG_1PX, filename="a.png"):
        return self.call(
            "POST", f"/api/v1/items/{item_id}/images", token=self.token(username),
            data={"file": (io.BytesIO(content), filename)},
            content_type="multipart/form-data").get_json()

    def test_upload_and_show_on_detail(self):
        self.register("seller1")
        item_id = self.publish("seller1")["data"]["id"]
        data = self.upload(item_id, "seller1")
        self.assertEqual(data["code"], 0)
        self.assertEqual(len(data["data"]["images"]), 1)
        self.assertTrue(data["data"]["images"][0].startswith("http://"))

        detail = self.call("GET", f"/api/v1/items/{item_id}").get_json()["data"]
        self.assertEqual(len(detail["images"]), 1)
        self.assertEqual(detail["cover"], detail["images"][0])
        # 列表页也带首图
        listed = self.call("GET", "/api/v1/items").get_json()["data"]
        self.assertEqual(listed[0]["cover"], detail["images"][0])

    def test_reject_non_image(self):
        self.register("seller1")
        item_id = self.publish("seller1")["data"]["id"]
        data = self.upload(item_id, "seller1", content=b"not an image at all",
                           filename="evil.png")
        self.assertEqual(data["code"], 400)
        self.assertIn("只支持", data["message"])

    def test_reject_oversized(self):
        self.register("seller1")
        item_id = self.publish("seller1")["data"]["id"]
        big = PNG_1PX + b"\x00" * (5 * 1024 * 1024)
        data = self.upload(item_id, "seller1", content=big)
        self.assertEqual(data["code"], 400)
        self.assertIn("5MB", data["message"])

    def test_max_five_images(self):
        self.register("seller1")
        item_id = self.publish("seller1")["data"]["id"]
        for i in range(5):
            self.assertEqual(self.upload(item_id, "seller1")["code"], 0, f"第 {i+1} 张")
        sixth = self.upload(item_id, "seller1")
        self.assertEqual(sixth["code"], 400)
        self.assertIn("最多 5 张", sixth["message"])

    def test_only_owner_can_upload(self):
        self.register("seller1")
        self.register("buyer1")
        item_id = self.publish("seller1")["data"]["id"]
        data = self.upload(item_id, "buyer1")
        self.assertEqual(data["code"], 403)


class ChatApiTests(ApiTestCase):
    def _setup(self):
        self.register("seller1", contact="wx_seller")
        self.register("buyer1")
        item_id = self.publish("seller1", price="25.00")["data"]["id"]
        return item_id

    def send(self, item_id, username, body, offer=None, buyer_id=None):
        payload = {"body": body}
        if offer is not None:
            payload["offer_price"] = offer
        if buyer_id is not None:
            payload["buyer_id"] = buyer_id
        return self.call("POST", f"/api/v1/threads/{item_id}/messages",
                         token=self.token(username), json=payload).get_json()

    def test_buyer_opens_thread_and_seller_replies(self):
        item_id = self._setup()
        data = self.send(item_id, "buyer1", "能便宜点吗？")
        self.assertEqual(data["code"], 0)
        self.assertEqual(data["data"]["role"], "buyer")
        self.assertEqual(data["data"]["peer_name"], "seller1")
        self.assertEqual(len(data["data"]["messages"]), 1)
        self.assertTrue(data["data"]["messages"][0]["mine"])

        # 卖家在自己的会话里回复：必须带 buyer_id
        buyer_id = data["data"]["buyer_id"]
        reply = self.send(item_id, "seller1", "最低 22 吧", buyer_id=buyer_id)
        self.assertEqual(reply["data"]["role"], "seller")
        self.assertEqual(len(reply["data"]["messages"]), 2)
        self.assertFalse(reply["data"]["messages"][0]["mine"])   # 买家那条
        self.assertTrue(reply["data"]["messages"][1]["mine"])    # 卖家自己刚发的

    def test_seller_cannot_start_conversation(self):
        item_id = self._setup()
        buyer_id = self.sql("SELECT id FROM users WHERE username='buyer1'").fetchone()["id"]
        data = self.send(item_id, "seller1", "买吗？", buyer_id=buyer_id)
        self.assertEqual(data["code"], 403)

    def test_third_party_cannot_read(self):
        item_id = self._setup()
        self.send(item_id, "buyer1", "你好")
        self.register("stranger")
        data = self.call("GET", f"/api/v1/threads/{item_id}?buyer_id=2",
                         token=self.token("stranger")).get_json()
        self.assertEqual(data["code"], 403)

    def test_offer_accept_updates_price(self):
        item_id = self._setup()
        thread = self.send(item_id, "buyer1", "", offer="20.00")["data"]
        offer = thread["messages"][0]
        self.assertEqual(offer["kind"], "offer")
        self.assertEqual(offer["offer_price"], 2000)
        self.assertEqual(offer["offer_display"], "20.00")

        accepted = self.call("POST", f"/api/v1/messages/{offer['id']}/accept",
                             token=self.token("seller1")).get_json()
        self.assertEqual(accepted["code"], 0)
        self.assertEqual(accepted["data"]["price"], 2000)

        detail = self.call("GET", f"/api/v1/items/{item_id}").get_json()["data"]
        self.assertEqual(detail["price"], 2000)
        self.assertEqual(detail["price_display"], "20.00")

        # 已接受的报价不能再接受一次
        again = self.call("POST", f"/api/v1/messages/{offer['id']}/accept",
                          token=self.token("seller1")).get_json()
        self.assertEqual(again["code"], 400)

    def test_buyer_cannot_accept_offer(self):
        item_id = self._setup()
        thread = self.send(item_id, "buyer1", "", offer="20.00")["data"]
        offer_id = thread["messages"][0]["id"]
        data = self.call("POST", f"/api/v1/messages/{offer_id}/accept",
                         token=self.token("buyer1")).get_json()
        self.assertEqual(data["code"], 403)

    def test_offer_must_be_lower_than_price(self):
        item_id = self._setup()
        data = self.send(item_id, "buyer1", "", offer="30.00")
        self.assertEqual(data["code"], 400)
        self.assertIn("不低于当前标价", data["message"])

    def test_invalid_offer_amount(self):
        item_id = self._setup()
        for bad in ("abc", "0", "-5"):
            data = self.send(item_id, "buyer1", "报价", offer=bad)
            self.assertEqual(data["code"], 400, bad)

    def test_empty_message_rejected(self):
        item_id = self._setup()
        data = self.send(item_id, "buyer1", "   ")
        self.assertEqual(data["code"], 400)

    def test_unread_counts_then_clears(self):
        item_id = self._setup()
        self.send(item_id, "buyer1", "在吗")
        bucket = self.call("GET", "/api/v1/messages",
                           token=self.token("seller1")).get_json()["data"]
        self.assertEqual(bucket["unread"], 1)
        self.assertEqual(len(bucket["conversations"]), 1)
        self.assertEqual(bucket["conversations"][0]["role"], "seller")
        self.assertEqual(bucket["conversations"][0]["unread"], 1)

        buyer_id = bucket["conversations"][0]["buyer_id"]
        self.call("GET", f"/api/v1/threads/{item_id}?buyer_id={buyer_id}",
                  token=self.token("seller1"))
        after = self.call("GET", "/api/v1/messages",
                          token=self.token("seller1")).get_json()["data"]
        self.assertEqual(after["unread"], 0)

    def test_message_requires_login(self):
        item_id = self._setup()
        resp = self.call("POST", f"/api/v1/threads/{item_id}/messages",
                         json={"body": "hi"})
        self.assertEqual(resp.status_code, 401)


if __name__ == "__main__":
    unittest.main()
"""JSON 接口 /api/v1。

网页走服务端渲染，不用这套接口；它留给移动端 / H5 复用。业务规则一律走
trading / market 两个模块，和网页共用同一套事务与校验，不存在两套逻辑。

设计要点：
- 统一信封 {code, message, data}：code 0 表示成功，失败时 code 等于 HTTP 状态码，
  客户端只需要判断 `code === 0`。
- 鉴权用无状态签名 token（itsdangerous），不建会话表；30 天有效。
- 因为不依赖 cookie，天然免疫 CSRF，不需要走网页那套 token 校验。
"""
import json
import random
import sqlite3
import urllib.parse
import urllib.request
from functools import wraps

from flask import Blueprint, current_app, g, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash

import market
import chat
import images
import trading
from common import CONDITIONS, format_price, parse_price_yuan, to_local_time
from database import connect

bp = Blueprint("api", __name__, url_prefix="/api/v1")

TOKEN_MAX_AGE = 60 * 60 * 24 * 30
WX_SESSION_URL = "https://api.weixin.qq.com/sns/jscode2session"


# ---------- 基础设施 ----------

def get_db():
    if "api_db" not in g:
        g.api_db = connect(current_app.config["DATABASE"])
    return g.api_db


def ok(data=None, message="ok", **extra):
    payload = {"code": 0, "message": message, "data": data}
    payload.update(extra)
    return payload


def fail(message, code=400):
    return {"code": code, "message": message, "data": None}, code
    # 注意：Flask 会按元组返回 (body, status)


def body():
    """兼容 JSON 与表单，小程序统一用 JSON。"""
    return request.get_json(silent=True) or request.form.to_dict() or {}


def _serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="cm-api-token")


def make_token(user_id):
    return _serializer().dumps({"uid": user_id})


def _user_from_request():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    try:
        data = _serializer().loads(auth[7:].strip(), max_age=TOKEN_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    uid = data.get("uid") if isinstance(data, dict) else None
    if uid is None:
        return None
    return get_db().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = _user_from_request()
        if user is None:
            return fail("登录已过期，请重新登录", 401)
        g.api_user = user
        return fn(*args, **kwargs)
    return wrapper


def handle_errors(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except trading.BizError as exc:
            return fail(exc.message, exc.status)
        except sqlite3.IntegrityError:
            return fail("数据冲突，请重试", 409)
    return wrapper


@bp.errorhandler(404)
def _not_found(e):
    return fail("接口不存在", 404)


@bp.errorhandler(405)
def _not_allowed(e):
    return fail("请求方法不支持", 405)


@bp.errorhandler(500)
def _server_error(e):
    return fail("服务异常，请稍后重试", 500)


# ---------- 序列化 ----------

def _has(row, key):
    return key in row.keys()


def _abs(paths):
    """相对路径 → 绝对地址。小程序里 <image src> 必须是完整 URL。

    用 request.url_root 拼接，好处是开发者工具（127.0.0.1）和手机（内网 IP）
    访问时都能拿到各自可达的地址，不需要在配置里写死域名。
    """
    root = request.url_root.rstrip("/")
    return [f"{root}{p}" for p in (paths or [])]


def item_json(row, viewer_id=None, image_urls=None):
    data = {
        "id": row["id"],
        "title": row["title"],
        "price": row["price"],
        "price_display": format_price(row["price"]),
        "condition": row["condition"],
        "status": row["status"],
        "description": row["description"] or "",
        "category_id": row["category_id"],
        "seller_id": row["seller_id"],
        "created_at": to_local_time(row["created_at"]),
        "is_mine": viewer_id is not None and row["seller_id"] == viewer_id,
        "images": _abs(image_urls),
        "cover": _abs(image_urls)[0] if image_urls else None,
    }
    if _has(row, "category_name"):
        data["category_name"] = row["category_name"]
    if _has(row, "seller_name"):
        data["seller_name"] = row["seller_name"]
    return data


def user_json(user):
    return {
        "id": user["id"],
        "username": user["username"],
        "contact": user["contact"] or "",
        "has_password": bool(user["password"]),
    }


def order_json(row, viewer_id):
    actions = trading.actions_for(row, viewer_id)
    is_buyer = actions["is_buyer"]
    data = {
        "id": row["id"],
        "item_id": row["item_id"],
        "item_title": row["item_title"],
        "item_status": row["item_status"],
        "price": row["price"],
        "price_display": format_price(row["price"]),
        "status": row["status"],
        "status_hint": trading.STATUS_HINTS.get(row["status"], ""),
        # 核对码只在待交付阶段给出，交易结束后没意义
        "deal_code": row["deal_code"] if row["status"] == trading.DEALING else None,
        "seller_confirmed": row["seller_confirm_at"] is not None,
        "buyer_confirmed": row["buyer_confirm_at"] is not None,
        "cancel_by": row["cancel_by"],
        "created_at": to_local_time(row["created_at"]),
        "role": "buyer" if is_buyer else "seller",
        "counterparty": {
            "username": row["seller_name"] if is_buyer else row["buyer_name"],
            "contact": (row["seller_contact"] if is_buyer else row["buyer_contact"]) or "",
        },
        "actions": {
            "accept": actions["can_accept"],
            "cancel": actions["can_cancel"],
            "deliver": actions["can_deliver"],
            "receive": actions["can_receive"],
        },
    }
    return data


ORDER_SELECT = (
    "SELECT o.*, i.title AS item_title, i.status AS item_status, "
    "i.seller_id AS seller_id, "
    "s.username AS seller_name, s.contact AS seller_contact, "
    "b.username AS buyer_name, b.contact AS buyer_contact "
    "FROM orders o JOIN items i ON o.item_id = i.id "
    "JOIN users s ON i.seller_id = s.id "
    "JOIN users b ON o.buyer_id = b.id "
)


def _load_order(order_id):
    row = get_db().execute(ORDER_SELECT + "WHERE o.id = ?", (order_id,)).fetchone()
    if row is None:
        return None
    return row


# ---------- 登录注册 ----------

@bp.post("/auth/register")
@handle_errors
def register():
    data = body()
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))
    contact = str(data.get("contact", "")).strip()
    if not username:
        return fail("用户名不能为空")
    if len(password) < 6:
        return fail("密码最短 6 位")
    db = get_db()
    try:
        cur = db.execute(
            "INSERT INTO users (username, password, contact) VALUES (?, ?, ?)",
            (username, generate_password_hash(password), contact or None))
    except sqlite3.IntegrityError:
        return fail("用户名已存在", 409)
    user = db.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
    return ok({"token": make_token(user["id"]), "user": user_json(user)})


@bp.post("/auth/login")
@handle_errors
def login():
    data = body()
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))
    user = get_db().execute("SELECT * FROM users WHERE username = ?",
                            (username,)).fetchone()
    # 微信登录用户没设密码，不允许用密码登录（空密码哈希校验必然失败，这里显式拦一道）
    if user is None or not user["password"] or not check_password_hash(user["password"], password):
        return fail("用户名或密码错误", 401)
    return ok({"token": make_token(user["id"]), "user": user_json(user)})


@bp.post("/auth/wx-login")
@handle_errors
def wx_login():
    """用 wx.login 拿到的 code 换 openid，没有 openid 就建账号。"""
    data = body()
    code = str(data.get("code", "")).strip()
    if not code:
        return fail("缺少 code")
    appid = current_app.config.get("WX_APPID")
    secret = current_app.config.get("WX_SECRET")
    if not appid or not secret:
        return fail("服务端还没配置小程序 AppID/AppSecret，请先用账号密码登录", 501)

    query = urllib.parse.urlencode({
        "appid": appid, "secret": secret, "js_code": code,
        "grant_type": "authorization_code",
    })
    try:
        with urllib.request.urlopen(f"{WX_SESSION_URL}?{query}", timeout=5) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return fail("微信登录服务暂时不可用，请稍后重试", 502)
    if payload.get("errcode"):
        return fail(f"微信登录失败：{payload.get('errmsg', '未知错误')}", 401)
    openid = payload.get("openid")
    if not openid:
        return fail("微信登录失败：未获得 openid", 401)

    db = get_db()
    user = db.execute("SELECT * FROM users WHERE openid = ?", (openid,)).fetchone()
    if user is None:
        contact = str(data.get("contact", "")).strip() or None
        user = _create_wx_user(db, openid, contact)
    return ok({"token": make_token(user["id"]), "user": user_json(user)})


def _create_wx_user(db, openid, contact):
    """微信用户没有用户名/密码，用随机昵称占位，冲突就换。"""
    for _ in range(5):
        username = f"同学{random.randint(100000, 999999)}"
        try:
            cur = db.execute(
                "INSERT INTO users (username, password, contact, openid) "
                "VALUES (?, '', ?, ?)", (username, contact, openid))
        except sqlite3.IntegrityError:
            continue
        return db.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
    raise trading.BizError("账号创建失败，请重试", 500)


@bp.get("/me")
@login_required
@handle_errors
def me():
    return ok(user_json(g.api_user))


@bp.patch("/me")
@login_required
@handle_errors
def update_me():
    contact = str(body().get("contact", "")).strip()
    db = get_db()
    db.execute("UPDATE users SET contact = ? WHERE id = ?",
               (contact or None, g.api_user["id"]))
    user = db.execute("SELECT * FROM users WHERE id = ?", (g.api_user["id"],)).fetchone()
    return ok(user_json(user))


# ---------- 商品 ----------

@bp.get("/categories")
def categories():
    rows = get_db().execute("SELECT id, name FROM categories ORDER BY id").fetchall()
    return ok([dict(r) for r in rows])


@bp.get("/meta")
def meta():
    """给小程序下拉框用的静态枚举。"""
    return ok({"conditions": list(CONDITIONS),
               "item_statuses": ["在售", "已预订", "已售", "下架"]})


@bp.get("/items")
@handle_errors
def list_items():
    db = get_db()
    rows, error = market.search_items(
        db,
        q=request.args.get("q", ""),
        min_price=request.args.get("min_price", ""),
        max_price=request.args.get("max_price", ""),
        category_id=request.args.get("category_id", ""),
    )
    # 未登录也能逛，登录了才知道哪几件是自己的
    user = _user_from_request()
    viewer_id = user["id"] if user else None
    covers = images.first_urls(db, [r["id"] for r in rows])
    data = [item_json(r, viewer_id,
                      [covers[r["id"]]] if r["id"] in covers else None)
            for r in rows]
    return ok(data, warning=error)


@bp.get("/items/<int:item_id>")
@handle_errors
def item_detail(item_id):
    user = _user_from_request()
    db = get_db()
    row = db.execute(
        "SELECT i.*, u.username AS seller_name, u.contact AS seller_contact, "
        "c.name AS category_name FROM items i "
        "JOIN users u ON i.seller_id = u.id "
        "JOIN categories c ON i.category_id = c.id WHERE i.id = ?",
        (item_id,)).fetchone()
    if row is None:
        return fail("商品不存在", 404)
    viewer_id = user["id"] if user else None
    if row["status"] == "下架" and viewer_id != row["seller_id"]:
        return fail("商品不存在", 404)   # 下架即对他人隐藏，不泄露存在性
    if row["status"] in ("已预订", "已售") and user is None:
        return fail("登录后才能查看该商品", 401)

    data = item_json(row, viewer_id, images.urls_for(db, item_id))
    data["can_buy"] = bool(user and row["status"] == "在售"
                           and viewer_id != row["seller_id"])
    data["can_chat"] = bool(user and viewer_id != row["seller_id"])
    # 联系方式只在登录后给
    data["seller_contact"] = (row["seller_contact"] or "") if user else None
    return ok(data)


@bp.post("/items")
@login_required
@handle_errors
def create_item():
    data = body()
    title = str(data.get("title", "")).strip()
    condition = str(data.get("condition", ""))
    description = str(data.get("description", "")).strip()
    category_id = str(data.get("category_id", "")).strip()
    if not title:
        return fail("商品名不能为空")
    if condition not in CONDITIONS:
        return fail("成色不合法")
    try:
        price = parse_price_yuan(str(data.get("price", "")))
    except ValueError as exc:
        return fail(str(exc))
    db = get_db()
    cat = db.execute("SELECT id FROM categories WHERE id = ?",
                     (category_id,)).fetchone()
    if cat is None:
        return fail("分类不合法")
    cur = db.execute(
        "INSERT INTO items (title, price, condition, description, status, "
        "category_id, seller_id) VALUES (?, ?, ?, ?, '在售', ?, ?)",
        (title, price, condition, description, cat["id"], g.api_user["id"]))
    item_id = cur.lastrowid
    # 小程序是先建商品再逐张传图，网页版可以一次带上
    saved = []
    for storage in request.files.getlist("images"):
        if not storage or not storage.filename:
            continue
        try:
            saved.append(images.add_image(db, item_id, g.api_user["id"],
                                          storage.read(),
                                          current_app.config["UPLOAD_DIR"]))
        except trading.BizError:
            break
    row = db.execute(
        "SELECT i.*, c.name AS category_name FROM items i "
        "JOIN categories c ON i.category_id = c.id WHERE i.id = ?",
        (item_id,)).fetchone()
    return ok(item_json(row, g.api_user["id"], saved))


@bp.post("/items/<int:item_id>/images")
@login_required
@handle_errors
def upload_image(item_id):
    files = request.files.getlist("file") or request.files.getlist("images")
    files = [f for f in files if f and f.filename]
    if not files:
        return fail("没有收到图片，字段名应为 file")
    saved = []
    for storage in files:
        saved.append(images.add_image(
            get_db(), item_id, g.api_user["id"], storage.read(),
            current_app.config["UPLOAD_DIR"]))
    return ok({"images": _abs(saved), "count": len(saved)})


@bp.get("/my/items")
@login_required
@handle_errors
def my_items():
    db = get_db()
    rows = db.execute(
        "SELECT i.*, c.name AS category_name FROM items i "
        "JOIN categories c ON i.category_id = c.id WHERE i.seller_id = ? "
        "ORDER BY i.created_at DESC, i.id DESC", (g.api_user["id"],)).fetchall()
    covers = images.first_urls(db, [r["id"] for r in rows])
    return ok([item_json(r, g.api_user["id"],
                         [covers[r["id"]]] if r["id"] in covers else None)
               for r in rows])


def _owned_item(item_id):
    item = get_db().execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if item is None:
        raise trading.BizError("商品不存在", 404)
    if item["seller_id"] != g.api_user["id"]:
        raise trading.BizError("只有商品发布者本人可以操作", 403)
    return item


@bp.post("/items/<int:item_id>/price")
@login_required
@handle_errors
def edit_price(item_id):
    item = _owned_item(item_id)
    if item["status"] != "在售":
        return fail("只有在售商品可以改价")
    try:
        price = parse_price_yuan(str(body().get("price", "")))
    except ValueError as exc:
        return fail(str(exc))
    cur = get_db().execute(
        "UPDATE items SET price = ? WHERE id = ? AND status = '在售'", (price, item_id))
    if cur.rowcount == 0:
        return fail("商品状态已变更，改价失败")
    return ok({"id": item_id, "price": price, "price_display": format_price(price)})


@bp.post("/items/<int:item_id>/delist")
@login_required
@handle_errors
def delist(item_id):
    item = _owned_item(item_id)
    if item["status"] != "在售":
        return fail("只有在售商品可以下架")
    cur = get_db().execute(
        "UPDATE items SET status = '下架' WHERE id = ? AND status = '在售'", (item_id,))
    if cur.rowcount == 0:
        return fail("商品状态已变更，下架失败")
    return ok({"id": item_id, "status": "下架"})


# ---------- 交易 ----------

@bp.post("/items/<int:item_id>/orders")
@login_required
@handle_errors
def place_order(item_id):
    order_id = trading.place_order(get_db(), item_id, g.api_user["id"])
    row = _load_order(order_id)
    return ok(order_json(row, g.api_user["id"]), message="下单成功，等待卖家接单")


@bp.get("/my/orders")
@login_required
@handle_errors
def my_orders():
    rows = get_db().execute(
        ORDER_SELECT + "WHERE o.buyer_id = ? ORDER BY o.created_at DESC, o.id DESC",
        (g.api_user["id"],)).fetchall()
    return ok([order_json(r, g.api_user["id"]) for r in rows])


@bp.get("/my/sales")
@login_required
@handle_errors
def my_sales():
    rows = get_db().execute(
        ORDER_SELECT + "WHERE i.seller_id = ? ORDER BY o.created_at DESC, o.id DESC",
        (g.api_user["id"],)).fetchall()
    return ok([order_json(r, g.api_user["id"]) for r in rows])


def _order_action(order_id, action, message):
    """订单动作统一出口：执行 → 回读订单 → 返回最新状态。"""
    row = _load_order(order_id)
    if row is None:
        raise trading.BizError("订单不存在", 404)
    completed = action()
    fresh = _load_order(order_id)
    payload = order_json(fresh, g.api_user["id"])
    payload["finished"] = bool(completed)
    return ok(payload, message=message if not completed else "交易完成")


@bp.post("/orders/<int:order_id>/accept")
@login_required
@handle_errors
def accept_order(order_id):
    return _order_action(
        order_id,
        lambda: trading.accept_order(get_db(), order_id, g.api_user["id"]),
        "已接单，请和买家约定见面时间地点")


@bp.post("/orders/<int:order_id>/cancel")
@login_required
@handle_errors
def cancel_order(order_id):
    return _order_action(
        order_id,
        lambda: trading.cancel_order(get_db(), order_id, g.api_user["id"]),
        "订单已取消，商品重新上架")


@bp.post("/orders/<int:order_id>/deliver")
@login_required
@handle_errors
def deliver_order(order_id):
    return _order_action(
        order_id,
        lambda: trading.deliver_order(get_db(), order_id, g.api_user["id"]),
        "已确认交付，等待买家确认收货")


@bp.post("/orders/<int:order_id>/receive")
@login_required
@handle_errors
def receive_order(order_id):
    return _order_action(
        order_id,
        lambda: trading.receive_order(get_db(), order_id, g.api_user["id"]),
        "已确认收货，等待卖家确认交付")


# ---------- 议价聊天 ----------

def _thread_item(item_id, buyer_id):
    item = get_db().execute(
        "SELECT i.*, s.username AS seller_name, b.username AS buyer_name "
        "FROM items i JOIN users s ON i.seller_id = s.id "
        "JOIN users b ON b.id = ? WHERE i.id = ?", (buyer_id, item_id)).fetchone()
    if item is None:
        raise trading.BizError("商品不存在", 404)
    return item


def _buyer_id(payload=None):
    """买家自己就是会话买家；卖家必须显式指定 buyer_id。"""
    raw = (payload or {}).get("buyer_id") or request.args.get("buyer_id")
    if raw in (None, ""):
        return g.api_user["id"]
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise trading.BizError("buyer_id 不合法")


def _parse_offer(value):
    """报价支持两种写法：整数按分，字符串按元（最多两位小数）。"""
    if value in (None, "", 0):
        return None
    if isinstance(value, int):
        cents = value
    else:
        try:
            cents = parse_price_yuan(str(value))
        except ValueError as exc:
            raise trading.BizError(str(exc))
    if cents <= 0:
        raise trading.BizError("报价必须大于 0")
    return cents


def _thread_payload(item_id, buyer_id):
    db = get_db()
    item = _thread_item(item_id, buyer_id)
    rows = chat.thread(db, item_id, buyer_id, g.api_user["id"])
    is_seller = item["seller_id"] == g.api_user["id"]
    covers = images.first_urls(db, [item_id])
    cover = _abs([covers[item_id]])[0] if item_id in covers else None
    return {
        "item_id": item_id,
        "item_title": item["title"],
        "item_price": item["price"],
        "item_price_display": format_price(item["price"]),
        "item_status": item["status"],
        "item_cover": cover,
        "buyer_id": buyer_id,
        "role": "seller" if is_seller else "buyer",
        "peer_name": item["buyer_name"] if is_seller else item["seller_name"],
        "messages": [chat.message_json(r, g.api_user["id"]) for r in rows],
    }


@bp.get("/messages")
@login_required
@handle_errors
def conversations():
    db = get_db()
    return ok({
        "conversations": chat.conversations(db, g.api_user["id"]),
        "unread": chat.unread_total(db, g.api_user["id"]),
    })


@bp.get("/threads/<int:item_id>")
@login_required
@handle_errors
def get_thread(item_id):
    return ok(_thread_payload(item_id, _buyer_id()))


@bp.post("/threads/<int:item_id>/messages")
@login_required
@handle_errors
def post_message(item_id):
    data = body()
    buyer_id = _buyer_id(data)
    offer = _parse_offer(data.get("offer_price"))
    chat.send(get_db(), item_id, buyer_id, g.api_user["id"],
              str(data.get("body", "")).strip(),
              kind="offer" if offer else "text", offer_price=offer)
    return ok(_thread_payload(item_id, buyer_id))


@bp.post("/messages/<int:message_id>/accept")
@login_required
@handle_errors
def accept_offer(message_id):
    item_id, price = chat.accept_offer(get_db(), message_id, g.api_user["id"])
    row = get_db().execute("SELECT buyer_id FROM messages WHERE id = ?",
                           (message_id,)).fetchone()
    return ok({
        "item_id": item_id,
        "buyer_id": row["buyer_id"],
        "price": price,
        "price_display": format_price(price),
    }, message=f"已接受报价，商品价格改为 ¥{format_price(price)}")

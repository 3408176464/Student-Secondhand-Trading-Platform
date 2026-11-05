"""Flask 应用：工厂、路由与业务逻辑。"""
import hmac
import os
import secrets
import sqlite3
import time
from functools import wraps
from pathlib import Path

from flask import (Flask, abort, flash, g, jsonify, redirect, render_template,
                   request, send_from_directory, session, url_for)
from markupsafe import Markup
from werkzeug.security import check_password_hash, generate_password_hash

import api
import chat
import images
import market
import migrate_v3
import trading
from common import (CONDITIONS, MAX_PRICE_CENTS, PRICE_RE, escape_like,  # noqa: F401
                    format_price, parse_price_filter, parse_price_yuan,
                    to_local_time, with_display)
from database import connect, init_db

BASE_DIR = Path(__file__).resolve().parent

# 登录失败限流：同一 IP + 用户名 10 分钟内失败 5 次就暂时锁住
LOGIN_MAX_FAILS = 5
LOGIN_WINDOW_SECONDS = 600
DEV_SECRET_KEY = "dev-only-change-me"

# 首页每页商品数：4 列网格 3 行，正好一屏多一点
PER_PAGE = 12


def _zh(e, fallback):
    """Werkzeug 默认描述换中文兜底；自定义 description 原样保留。"""
    return fallback if e.description in (None, type(e).description) else e.description


def _env_int(name, default):
    """环境变量取整数，取不到或写成乱码就退回默认值，别在启动时炸。"""
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return int(default)


def _page_window(page, pages, width=5):
    """页码窗口：总页数多时只显示当前页附近的几页，用 None 表示省略号。

    24 件商品只有 2 页，这个函数是为了商品变多以后不用再改模板；
    页码按钮的数量始终有上限，不会把分页条撑成一行数字。
    """
    if pages <= width + 2:
        return list(range(1, pages + 1))
    half = width // 2
    start = max(2, min(page - half, pages - width))
    end = min(pages - 1, start + width - 1)
    window = list(range(start, end + 1))
    if start > 2:
        window = [1, None] + window
    else:
        window = [1] + window
    if end < pages - 1:
        window = window + [None, pages]
    else:
        window = window + [pages]
    return window


# ---------- 应用工厂 ----------

def create_app(test_config=None):
    app = Flask(__name__)
    # 配置优先由环境变量提供，方便同一份代码在本地和服务器上跑
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("CM_SECRET_KEY", DEV_SECRET_KEY),
        DATABASE=os.environ.get("CM_DATABASE", str(BASE_DIR / "campus_market.db")),
        UPLOAD_DIR=os.environ.get("CM_UPLOAD_DIR", str(BASE_DIR / "uploads")),
        SEED_DATA=os.environ.get("CM_SEED", "1") != "0",
        CSRF_ENABLED=os.environ.get("CM_CSRF", "1") != "0",
        # 待接单超过这么多分钟自动释放；设 0 关闭
        ORDER_TIMEOUT_MINUTES=_env_int("CM_ORDER_TIMEOUT_MINUTES",
                                       trading.PENDING_TIMEOUT_MINUTES),
        # 反代到 HTTPS 后，告诉 Flask 原始请求是 https
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        MAX_CONTENT_LENGTH=40 * 1024 * 1024,   # 单次请求上限，防超大表单打满内存
    )
    if test_config:
        app.config.from_mapping(test_config)

    if (not app.config["TESTING"]
            and app.config["SECRET_KEY"] == DEV_SECRET_KEY):
        print("⚠️  正在使用默认 SECRET_KEY，仅适合本地开发。"
              "公网部署请设置环境变量 CM_SECRET_KEY（部署步骤见 HANDOFF.md 附录 A）")

    app.json.ensure_ascii = False  # 接口直接返回中文，便于调试时肉眼核对

    db_path = Path(app.config["DATABASE"])
    if not db_path.exists() or db_path.stat().st_size == 0:
        conn = connect(db_path)
        try:
            init_db(conn, seed=app.config["SEED_DATA"])
        except Exception:
            conn.close()
            db_path.unlink(missing_ok=True)  # 初始化失败不留半成品库，否则会被误判为已初始化
            raise
        conn.close()
    # 老库补齐 v3 新增的图片表、消息表（纯增量、幂等，启动时顺手做掉）
    migrate_v3.migrate(db_path, quiet=True)

    images.image_dir(app.config["UPLOAD_DIR"])

    def get_db():
        if "db" not in g:
            g.db = connect(app.config["DATABASE"])
        return g.db

    app.get_db = get_db

    @app.teardown_appcontext
    def close_db(exc):
        for key in ("db", "api_db"):
            db = g.pop(key, None)
            if db is not None:
                db.close()

    @app.before_request
    def load_logged_in_user():
        user_id = session.get("user_id")
        g.user = None
        g.unread = 0
        if user_id is not None:
            g.user = get_db().execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if g.user is not None:
                g.unread = chat.unread_total(get_db(), g.user["id"])

    # ---------- CSRF ----------
    # 网页用 cookie 会话，浏览器会自动带上 cookie，所以任何第三方站点都能
    # 伪造一个 POST 表单替用户下单/改价。对策：每个会话一个随机 token，
    # 表单里带回来，对不上就拒绝。接口（/api/*）用 Bearer token，天然免疫，不用管。

    def csrf_token():
        token = session.get("_csrf")
        if not token:
            token = secrets.token_urlsafe(32)
            session["_csrf"] = token
        return token

    @app.context_processor
    def inject_csrf():
        def csrf_field():
            return Markup(f'<input type="hidden" name="csrf_token" '
                          f'value="{csrf_token()}">')
        return {"csrf_token": csrf_token, "csrf_field": csrf_field,
                "order_timeout_minutes": app.config.get("ORDER_TIMEOUT_MINUTES", 0)}

    @app.before_request
    def csrf_protect():
        if request.method != "POST" or not app.config["CSRF_ENABLED"]:
            return None
        if request.path.startswith("/api/"):
            return None
        expected = session.get("_csrf")
        got = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
        if not expected or not got or not hmac.compare_digest(expected, got):
            abort(400, description="页面已过期，请刷新后重试")

    # ---------- 订单超时释放 ----------

    @app.before_request
    def sweep_expired_orders():
        """惰性清理：待接单超时的订单自动取消，商品回在售。

        没有定时任务，靠请求顺带扫一眼；30 秒最多一次，避免每次都写库。
        清理失败一律吞掉——它只是兜底，不能把正常请求带崩。
        """
        if request.endpoint in ("static", "uploaded_file"):
            return None
        timeout = app.config.get("ORDER_TIMEOUT_MINUTES", 0)
        if not timeout or timeout <= 0:
            return None
        now = time.time()
        if now - app.extensions.get("last_order_sweep", 0.0) < 30:
            return None
        app.extensions["last_order_sweep"] = now
        try:
            expired = trading.expire_stale_orders(get_db(), timeout)
        except sqlite3.Error:
            return None
        if expired:
            app.logger.info("超时释放 %s 笔待接单订单", expired)
        return None

    # ---------- 登录失败限流 ----------

    def _login_key(username):
        return f"{request.remote_addr}|{username}"

    def _login_blocked(key):
        bucket = app.extensions.setdefault("login_fails", {})
        now = time.time()
        hits = [t for t in bucket.get(key, []) if now - t < LOGIN_WINDOW_SECONDS]
        bucket[key] = hits
        return len(hits) >= LOGIN_MAX_FAILS

    def _record_login_fail(key):
        app.extensions.setdefault("login_fails", {}).setdefault(key, []).append(time.time())

    def _clear_login_fails(key):
        app.extensions.setdefault("login_fails", {}).pop(key, None)

    def login_required(view):
        @wraps(view)
        def wrapped(**kwargs):
            if g.user is None:
                return redirect(url_for("login"))
            return view(**kwargs)
        return wrapped

    app.login_required = login_required

    # 小程序接口：/api/v1/*，与网页共用 trading / market 两层业务逻辑
    app.register_blueprint(api.bp)

    @app.errorhandler(400)
    def bad_request(e):
        return render_template("error.html",
                               message=_zh(e, "请求无效，请刷新页面后重试")), 400

    @app.errorhandler(403)
    def forbidden(e):
        return render_template("error.html", message=_zh(e, "无权访问")), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template("error.html", message=_zh(e, "资源不存在")), 404

    @app.errorhandler(500)
    def internal_error(e):
        return render_template("error.html", message=_zh(e, "数据异常，请稍后重试")), 500

    @app.route("/register", methods=("GET", "POST"))
    def register():
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            contact = request.form.get("contact", "").strip()
            error = None
            if not username:
                error = "用户名不能为空"
            elif len(password) < 6:
                error = "密码最短 6 位"
            if error is None:
                try:
                    get_db().execute(
                        "INSERT INTO users (username, password, contact) VALUES (?, ?, ?)",
                        (username, generate_password_hash(password), contact or None))
                except sqlite3.IntegrityError:
                    error = "用户名已存在"
                else:
                    return redirect(url_for("login"))
            flash(error)
        return render_template("register.html")

    @app.route("/login", methods=("GET", "POST"))
    def login():
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            key = _login_key(username)
            if _login_blocked(key):
                flash("尝试次数过多，请 10 分钟后再试")
                return render_template("login.html")
            user = get_db().execute(
                "SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            if user is None or not check_password_hash(user["password"], password):
                _record_login_fail(key)
                flash("用户名或密码错误")
            else:
                _clear_login_fails(key)
                session.clear()
                session["user_id"] = user["id"]
                session["_csrf"] = secrets.token_urlsafe(32)  # 换会话就换 token
                return redirect(url_for("index"))
        return render_template("login.html")

    @app.route("/logout", methods=("POST",))
    def logout():
        session.clear()
        return redirect(url_for("index"))

    @app.route("/")
    def index():
        db = get_db()
        # 只保留真正填了的筛选条件，分页链接才不会拖一串空参数
        query = {k: v for k, v in {
            "q": request.args.get("q", ""),
            "min_price": request.args.get("min_price", ""),
            "max_price": request.args.get("max_price", ""),
            "category_id": request.args.get("category_id", ""),
            "sort": request.args.get("sort", ""),
        }.items() if v}

        total, error = market.count_items(
            db,
            q=query.get("q", ""),
            min_price=query.get("min_price", ""),
            max_price=query.get("max_price", ""),
            category_id=query.get("category_id", ""),
        )
        if error:
            flash(error)

        pages = max(1, -(-total // PER_PAGE))
        try:
            page = int(request.args.get("page", "1"))
        except (TypeError, ValueError):
            page = 1
        page = min(max(1, page), pages)   # -1、999、abc 之类一律夹回有效范围

        rows, _ = market.search_items(
            db,
            q=query.get("q", ""),
            min_price=query.get("min_price", ""),
            max_price=query.get("max_price", ""),
            category_id=query.get("category_id", ""),
            sort=query.get("sort", ""),
            limit=PER_PAGE,
            offset=(page - 1) * PER_PAGE,
        )
        items = [with_display(r) for r in rows]
        covers = images.first_urls(db, [i["id"] for i in items])
        for i in items:
            i["cover_url"] = covers.get(i["id"])
            # 自己的商品照常展示，但要标出来，免得误点自己的东西
            i["is_mine"] = g.user is not None and i["seller_id"] == g.user["id"]
        categories = db.execute("SELECT * FROM categories ORDER BY id").fetchall()
        return render_template("index.html", items=items, categories=categories,
                               total=total, page=page, pages=pages,
                               page_numbers=_page_window(page, pages),
                               query=query, per_page=PER_PAGE)

    @app.route("/items/<int:item_id>")
    def item_detail(item_id):
        db = get_db()
        item = db.execute(
            "SELECT i.*, u.username AS seller_name, u.contact AS seller_contact, "
            "c.name AS category_name FROM items i "
            "JOIN users u ON i.seller_id = u.id "
            "JOIN categories c ON i.category_id = c.id WHERE i.id = ?",
            (item_id,)).fetchone()
        if item is None:
            abort(404)
        if item["status"] == "下架":
            if g.user is None or g.user["id"] != item["seller_id"]:
                abort(404)  # 不带文案：透露"已下架"会泄露商品存在性，违背下架即隐藏
        elif item["status"] in ("已预订", "已售") and g.user is None:
            return redirect(url_for("login"))
        can_buy = (g.user is not None and item["status"] == "在售"
                   and g.user["id"] != item["seller_id"])
        return render_template("item_detail.html", item=with_display(item),
                               image_urls=images.urls_for(db, item_id),
                               can_buy=can_buy)

    def _item_form_values(db):
        """发布和编辑共用同一套字段校验，避免两处规则跑偏。

        返回 (values, error)；error 非空时 values 里的价格是 0，调用方不得落库。
        """
        title = request.form.get("title", "").strip()
        condition = request.form.get("condition", "")
        description = request.form.get("description", "").strip()
        category_id = request.form.get("category_id", "")
        error = None
        try:
            price = parse_price_yuan(request.form.get("price", ""))
        except ValueError as exc:
            price = 0
            error = str(exc)
        if not title:
            error = error or "商品名不能为空"
        if condition not in CONDITIONS:
            error = error or "成色不合法"
        valid_cat = db.execute("SELECT id FROM categories WHERE id = ?",
                               (category_id,)).fetchone()
        if valid_cat is None:
            error = error or "分类不合法"
        return {"title": title, "price": price, "condition": condition,
                "description": description,
                "category_id": valid_cat["id"] if valid_cat is not None else None}, error

    @app.route("/publish", methods=("GET", "POST"))
    @login_required
    def publish():
        db = get_db()
        if request.method == "POST":
            values, error = _item_form_values(db)
            if error is None:
                cur = db.execute(
                    "INSERT INTO items (title, price, condition, description, "
                    "status, category_id, seller_id) VALUES (?, ?, ?, ?, '在售', ?, ?)",
                    (values["title"], values["price"], values["condition"],
                     values["description"], values["category_id"], g.user["id"]))
                item_id = cur.lastrowid
                problems = []
                for storage in request.files.getlist("images"):
                    if not storage or not storage.filename:
                        continue
                    try:
                        images.add_image(db, item_id, g.user["id"],
                                         storage.read(), app.config["UPLOAD_DIR"])
                    except trading.BizError as exc:
                        problems.append(exc.message)
                        break  # 数量或格式超限，后面的不用试了
                if problems:
                    flash(f"商品已发布，图片未完全上传：{problems[0]}", "warn")
                return redirect(url_for("my_items"))
            flash(error)
        categories = db.execute("SELECT * FROM categories ORDER BY id").fetchall()
        return render_template("publish.html", categories=categories,
                               conditions=CONDITIONS)

    @app.route("/my-items")
    @login_required
    def my_items():
        db = get_db()
        rows = db.execute(
            "SELECT i.*, c.name AS category_name FROM items i "
            "JOIN categories c ON i.category_id = c.id WHERE i.seller_id = ? "
            "ORDER BY i.created_at DESC, i.id DESC",
            (g.user["id"],)).fetchall()
        items = [with_display(r) for r in rows]
        covers = images.first_urls(db, [i["id"] for i in items])
        for i in items:
            i["cover_url"] = covers.get(i["id"])
        return render_template("my_items.html", items=items)

    def _get_owned_item_or_403(item_id):
        item = get_db().execute("SELECT * FROM items WHERE id = ?",
                                (item_id,)).fetchone()
        if item is None:
            abort(404)
        if item["seller_id"] != g.user["id"]:
            abort(403, description="只有商品发布者本人可以操作")
        return item

    @app.route("/items/<int:item_id>/edit", methods=("GET", "POST"))
    @login_required
    def edit_item(item_id):
        db = get_db()
        item = _get_owned_item_or_403(item_id)
        # 已预订＝有订单在跑，改价改名会让买家看到的和谈好的不一致；已售/下架是终态
        if item["status"] != "在售":
            flash("只有「在售」商品可以编辑；有订单在进行时请先处理订单")
            return redirect(url_for("my_items"))

        if request.method == "POST":
            values, error = _item_form_values(db)
            if error is None:
                cur = db.execute(
                    "UPDATE items SET title = ?, price = ?, condition = ?, "
                    "category_id = ?, description = ? WHERE id = ? AND status = '在售'",
                    (values["title"], values["price"], values["condition"],
                     values["category_id"], values["description"], item_id))
                problems = []
                for storage in request.files.getlist("images"):
                    if not storage or not storage.filename:
                        continue
                    try:
                        images.add_image(db, item_id, g.user["id"], storage.read(),
                                         app.config["UPLOAD_DIR"])
                    except trading.BizError as exc:
                        problems.append(exc.message)
                        break
                if cur.rowcount == 0:
                    flash("商品状态已变更，保存失败")
                    return redirect(url_for("my_items"))
                if problems:
                    flash(f"已保存，图片未完全上传：{problems[0]}", "warn")
                else:
                    flash("已保存", "ok")
                return redirect(url_for("edit_item", item_id=item_id))
            flash(error)

        photos = [
            {"id": r["id"], "url": f"/uploads/{images.SUBDIR}/{r['filename']}"}
            for r in db.execute(
                "SELECT id, filename FROM item_images WHERE item_id = ? "
                "ORDER BY position, id", (item_id,)).fetchall()]
        return render_template(
            "item_edit.html", item=with_display(item), photos=photos,
            categories=db.execute("SELECT * FROM categories ORDER BY id").fetchall(),
            conditions=CONDITIONS,
            max_images=images.MAX_IMAGES)

    @app.route("/items/<int:item_id>/images/<int:image_id>/delete", methods=("POST",))
    @login_required
    def delete_item_image(item_id, image_id):
        item = _get_owned_item_or_403(item_id)
        if item["status"] != "在售":
            flash("只有「在售」商品可以改图")
            return redirect(url_for("my_items"))
        try:
            images.remove_image(get_db(), item_id, g.user["id"], image_id,
                                app.config["UPLOAD_DIR"])
        except trading.BizError as exc:
            flash(exc.message)
        else:
            flash("图片已删除", "ok")
        return redirect(url_for("edit_item", item_id=item_id))

    @app.route("/items/<int:item_id>/price", methods=("POST",))
    @login_required
    def edit_item_price(item_id):
        item = _get_owned_item_or_403(item_id)
        if item["status"] != "在售":
            flash("只有在售商品可以改价")
            return redirect(url_for("my_items"))
        try:
            price = parse_price_yuan(request.form.get("price", ""))
        except ValueError as exc:
            flash(str(exc))
            return redirect(url_for("my_items"))
        cur = get_db().execute(
            "UPDATE items SET price = ? WHERE id = ? AND status = '在售'",
            (price, item_id))
        if cur.rowcount == 0:
            flash("商品状态已变更，改价失败")
        return redirect(url_for("my_items"))

    @app.route("/items/<int:item_id>/delist", methods=("POST",))
    @login_required
    def delist_item(item_id):
        item = _get_owned_item_or_403(item_id)
        if item["status"] != "在售":
            flash("只有在售商品可以下架")
            return redirect(url_for("my_items"))
        cur = get_db().execute(
            "UPDATE items SET status = '下架' WHERE id = ? AND status = '在售'",
            (item_id,))
        if cur.rowcount == 0:
            flash("商品状态已变更，下架失败")
        return redirect(url_for("my_items"))

    @app.route("/items/<int:item_id>/buy", methods=("POST",))
    @login_required
    def place_order(item_id):
        db = get_db()
        try:
            trading.place_order(db, item_id, g.user["id"])
        except trading.BizError as exc:
            flash(exc.message)
            # 失败回市场页：买家可能已经无权查看该商品详情（下架/被买走）
            return redirect(url_for("index"))
        return redirect(url_for("my_orders"))

    @app.route("/my-orders")
    @login_required
    def my_orders():
        rows = get_db().execute(
            "SELECT o.*, i.title AS item_title, i.status AS item_status, "
            "i.seller_id AS seller_id, u.username AS seller_name, "
            "u.contact AS seller_contact "
            "FROM orders o JOIN items i ON o.item_id = i.id "
            "JOIN users u ON i.seller_id = u.id "
            "WHERE o.buyer_id = ? ORDER BY o.created_at DESC, o.id DESC",
            (g.user["id"],)).fetchall()
        return render_template("my_orders.html",
                               orders=[_order_view(r) for r in rows])

    @app.route("/sales")
    @login_required
    def sales():
        rows = get_db().execute(
            "SELECT o.*, i.title AS item_title, i.status AS item_status, "
            "i.seller_id AS seller_id, u.username AS buyer_name, "
            "u.contact AS buyer_contact "
            "FROM orders o JOIN items i ON o.item_id = i.id "
            "JOIN users u ON o.buyer_id = u.id "
            "WHERE i.seller_id = ? ORDER BY o.created_at DESC, o.id DESC",
            (g.user["id"],)).fetchall()
        return render_template("sales.html",
                               orders=[_order_view(r) for r in rows])

    def _order_view(row):
        """订单行 + 当前用户可执行的操作，模板只负责展示。"""
        d = with_display(row)
        d.update(trading.actions_for(row, g.user["id"]))
        d["status_hint"] = trading.STATUS_HINTS.get(d["status"], "")
        return d

    def _run_order_action(order_id, action):
        """订单操作统一入口：先查订单决定回跳页，再交给交易层校验权限。"""
        order = get_db().execute(
            "SELECT o.*, i.seller_id FROM orders o "
            "JOIN items i ON o.item_id = i.id WHERE o.id = ?",
            (order_id,)).fetchone()
        if order is None:
            abort(404)
        if g.user["id"] not in (order["buyer_id"], order["seller_id"]):
            abort(403, description="只有订单双方可以操作")
        try:
            done = action(order)
        except trading.BizError as exc:
            if exc.status == 403:
                abort(403, description=exc.message)
            if exc.status == 404:
                abort(404)
            if exc.status >= 500:
                abort(500, description=exc.message)
            flash(exc.message)
        else:
            flash(done, "ok")
        if g.user["id"] == order["buyer_id"]:
            return redirect(url_for("my_orders"))
        return redirect(url_for("sales"))

    @app.route("/orders/<int:order_id>/accept", methods=("POST",))
    @login_required
    def accept_order(order_id):
        def action(order):
            trading.accept_order(get_db(), order_id, g.user["id"])
            return "已接单，请和买家约定见面时间地点"

        return _run_order_action(order_id, action)

    @app.route("/orders/<int:order_id>/cancel", methods=("POST",))
    @login_required
    def cancel_order(order_id):
        def action(order):
            trading.cancel_order(get_db(), order_id, g.user["id"])
            return "订单已取消，商品重新上架"

        return _run_order_action(order_id, action)

    @app.route("/orders/<int:order_id>/deliver", methods=("POST",))
    @login_required
    def deliver_order(order_id):
        def action(order):
            completed = trading.deliver_order(get_db(), order_id, g.user["id"])
            return ("交易完成" if completed
                    else "已确认交付，等待买家确认收货")

        return _run_order_action(order_id, action)

    @app.route("/orders/<int:order_id>/receive", methods=("POST",))
    @login_required
    def receive_order(order_id):
        def action(order):
            completed = trading.receive_order(get_db(), order_id, g.user["id"])
            return ("交易完成" if completed
                    else "已确认收货，等待卖家确认交付")

        return _run_order_action(order_id, action)

    @app.route("/settings", methods=("GET", "POST"))
    @login_required
    def settings():
        if request.method == "POST":
            contact = request.form.get("contact", "").strip()
            get_db().execute("UPDATE users SET contact = ? WHERE id = ?",
                             (contact or None, g.user["id"]))
            flash("联系方式已保存", "ok")
            return redirect(url_for("settings"))
        return render_template("settings.html")

    # ---------- 图片 ----------

    @app.route("/uploads/<path:filename>")
    def uploaded_file(filename):
        # send_from_directory 自带路径穿越防护，文件名又是服务端随机生成的
        return send_from_directory(app.config["UPLOAD_DIR"], filename)

    # ---------- 议价聊天 ----------

    @app.route("/messages")
    @login_required
    def messages():
        return render_template(
            "messages.html",
            conversations=chat.conversations(get_db(), g.user["id"]))

    @app.route("/messages/<int:item_id>/<int:buyer_id>", methods=("GET", "POST"))
    @login_required
    def thread(item_id, buyer_id):
        db = get_db()
        if request.method == "POST":
            body = request.form.get("body", "").strip()
            offer = request.form.get("offer_price", "").strip()
            try:
                if offer:
                    chat.send(db, item_id, buyer_id, g.user["id"], body,
                              kind="offer", offer_price=parse_price_yuan(offer))
                else:
                    chat.send(db, item_id, buyer_id, g.user["id"], body)
            except (trading.BizError, ValueError) as exc:
                flash(getattr(exc, "message", None) or str(exc))
                return redirect(url_for("thread", item_id=item_id, buyer_id=buyer_id))
            return redirect(url_for("thread", item_id=item_id, buyer_id=buyer_id))

        before = request.args.get("before", "")
        try:
            rows = chat.thread(db, item_id, buyer_id, g.user["id"], before_id=before)
        except trading.BizError as exc:
            abort(exc.status, description=exc.message)

        item = db.execute(
            "SELECT i.*, s.username AS seller_name, b.username AS buyer_name "
            "FROM items i JOIN users s ON i.seller_id = s.id "
            "JOIN users b ON b.id = ? WHERE i.id = ?", (buyer_id, item_id)).fetchone()
        message_list = [chat.message_json(r, g.user["id"]) for r in rows]
        return render_template(
            "thread.html", item=with_display(item), messages=message_list,
            buyer_id=buyer_id,
            has_older=chat.has_older(db, item_id, buyer_id,
                                     message_list[0]["id"] if message_list else None),
            is_seller=item["seller_id"] == g.user["id"],
            peer_name=item["buyer_name"] if item["seller_id"] == g.user["id"]
            else item["seller_name"])

    @app.route("/messages/<int:item_id>/<int:buyer_id>/updates")
    @login_required
    def thread_updates(item_id, buyer_id):
        """轮询接口：返回 after 之后的新消息。没有新消息就是空数组。"""
        try:
            rows = chat.new_messages(get_db(), item_id, buyer_id, g.user["id"],
                                     request.args.get("after", "0"))
        except trading.BizError as exc:
            return jsonify({"error": exc.message}), exc.status
        return jsonify({"messages": [chat.message_json(r, g.user["id"]) for r in rows]})

    @app.route("/messages/accept/<int:message_id>", methods=("POST",))
    @login_required
    def accept_offer(message_id):
        db = get_db()
        msg = db.execute("SELECT item_id, buyer_id FROM messages WHERE id = ?",
                         (message_id,)).fetchone()
        if msg is None:
            abort(404)
        try:
            item_id, price = chat.accept_offer(db, message_id, g.user["id"])
        except trading.BizError as exc:
            flash(exc.message)
            return redirect(url_for("thread", item_id=msg["item_id"],
                                    buyer_id=msg["buyer_id"]))
        flash(f"已接受报价，商品价格改为 ¥{format_price(price)}", "ok")
        return redirect(url_for("thread", item_id=item_id, buyer_id=msg["buyer_id"]))

    return app


if __name__ == "__main__":
    # 注意：Flask 默认只监听 127.0.0.1，别的电脑/手机连不上。
    # 这里强制监听所有网卡，并打印局域网地址——这是本地调试最常踩的坑。
    from net import lan_ip

    port = int(os.environ.get("PORT", "5000"))
    print(f"本机   http://127.0.0.1:{port}")
    print(f"局域网 http://{lan_ip()}:{port}")
    create_app().run(host="0.0.0.0", port=port, debug=True)

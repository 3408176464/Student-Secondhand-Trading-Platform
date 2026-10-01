"""Flask 应用：工厂、路由与业务逻辑。"""
import re
import sqlite3
from datetime import datetime, timedelta
from decimal import Decimal
from functools import wraps
from pathlib import Path

from flask import (Flask, abort, flash, g, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from database import connect, init_db

BASE_DIR = Path(__file__).resolve().parent
PRICE_RE = re.compile(r"^\d+(\.\d{1,2})?$")
MAX_PRICE_CENTS = 10 ** 9  # 价格上限 10^9 分 = 1000 万元，防超长数字绑定 SQLite 溢出
CONDITIONS = ("全新", "九成新", "八成新", "有使用痕迹")


# ---------- 工具函数 ----------

def format_price(cents):
    """分 → 元字符串，不经过 float。"""
    yuan, frac = divmod(cents, 100)
    return f"{yuan}.{frac:02d}"


def to_local_time(utc_str):
    """UTC 时间字符串 → UTC+8 展示字符串。"""
    dt = datetime.strptime(utc_str, "%Y-%m-%d %H:%M:%S") + timedelta(hours=8)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def escape_like(s):
    """转义 LIKE 通配符，配合 ESCAPE '\' 使用。"""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def parse_price_yuan(value):
    """商品价格：正则校验 + Decimal 转分，必须 > 0。"""
    value = value.strip()
    if not PRICE_RE.fullmatch(value):
        raise ValueError("价格格式不合法")
    cents = int(Decimal(value) * 100)
    if cents <= 0:
        raise ValueError("价格必须大于 0")
    if cents > MAX_PRICE_CENTS:
        raise ValueError("价格超出合理范围")
    return cents


def parse_price_filter(value):
    """筛选框价格：允许 0（单边过滤用）。"""
    value = value.strip()
    if not PRICE_RE.fullmatch(value):
        raise ValueError("价格区间必须为合法数字")
    cents = int(Decimal(value) * 100)
    if cents > MAX_PRICE_CENTS:
        raise ValueError("价格超出合理范围")
    return cents


def _rollback(db):
    try:
        db.execute("ROLLBACK")
    except sqlite3.Error:
        pass


def with_display(row):
    """数据库行 → 展示用 dict（价格元、时间本地化）。"""
    d = dict(row)
    d["price_display"] = format_price(d["price"])
    d["created_at_local"] = to_local_time(d["created_at"])
    return d


def _zh(e, fallback):
    """Werkzeug 默认描述换中文兜底；自定义 description 原样保留。"""
    return fallback if e.description in (None, type(e).description) else e.description


# ---------- 应用工厂 ----------

def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY="dev",
        DATABASE=str(BASE_DIR / "campus_market.db"),
        SEED_DATA=True,
    )
    if test_config:
        app.config.from_mapping(test_config)

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

    def get_db():
        if "db" not in g:
            g.db = connect(app.config["DATABASE"])
        return g.db

    app.get_db = get_db

    @app.teardown_appcontext
    def close_db(exc):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    @app.before_request
    def load_logged_in_user():
        user_id = session.get("user_id")
        g.user = None
        if user_id is not None:
            g.user = get_db().execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

    def login_required(view):
        @wraps(view)
        def wrapped(**kwargs):
            if g.user is None:
                return redirect(url_for("login"))
            return view(**kwargs)
        return wrapped

    app.login_required = login_required

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
            user = get_db().execute(
                "SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            if user is None or not check_password_hash(user["password"], password):
                flash("用户名或密码错误")
            else:
                session.clear()
                session["user_id"] = user["id"]
                return redirect(url_for("index"))
        return render_template("login.html")

    @app.route("/logout", methods=("POST",))
    def logout():
        session.clear()
        return redirect(url_for("index"))

    @app.route("/")
    def index():
        db = get_db()
        q = request.args.get("q", "").strip()
        min_price = request.args.get("min_price", "").strip()
        max_price = request.args.get("max_price", "").strip()
        category_id = request.args.get("category_id", "").strip()

        sql = ("SELECT i.*, u.username AS seller_name, c.name AS category_name "
               "FROM items i JOIN users u ON i.seller_id = u.id "
               "JOIN categories c ON i.category_id = c.id "
               "WHERE i.status = '在售'")
        params = []
        if q:
            sql += " AND i.title LIKE ? ESCAPE '\\'"
            params.append(f"%{escape_like(q)}%")

        error = None
        min_cents = max_cents = None
        try:
            if min_price:
                min_cents = parse_price_filter(min_price)
            if max_price:
                max_cents = parse_price_filter(max_price)
            if (min_cents is not None and max_cents is not None
                    and min_cents > max_cents):
                error = "最低价不能大于最高价"
        except ValueError:
            error = "价格区间必须为合法数字"

        if error:
            flash(error)
        else:
            if min_cents is not None:
                sql += " AND i.price >= ?"
                params.append(min_cents)
            if max_cents is not None:
                sql += " AND i.price <= ?"
                params.append(max_cents)
            if category_id:
                try:
                    exists = db.execute("SELECT id FROM categories WHERE id = ?",
                                        (int(category_id),)).fetchone()
                except (ValueError, OverflowError):
                    exists = None  # Unicode 数字、超长整数等一律按"分类不存在"忽略
                if exists is not None:
                    sql += " AND i.category_id = ?"
                    params.append(exists["id"])
        sql += " ORDER BY i.created_at DESC, i.id DESC"
        items = [with_display(r) for r in db.execute(sql, params).fetchall()]
        categories = db.execute("SELECT * FROM categories ORDER BY id").fetchall()
        return render_template("index.html", items=items, categories=categories)

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
                               can_buy=can_buy)

    @app.route("/publish", methods=("GET", "POST"))
    @login_required
    def publish():
        db = get_db()
        if request.method == "POST":
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
            if error is None:
                db.execute(
                    "INSERT INTO items (title, price, condition, description, "
                    "status, category_id, seller_id) VALUES (?, ?, ?, ?, '在售', ?, ?)",
                    (title, price, condition, description,
                     valid_cat["id"], g.user["id"]))
                return redirect(url_for("my_items"))
            flash(error)
        categories = db.execute("SELECT * FROM categories ORDER BY id").fetchall()
        return render_template("publish.html", categories=categories,
                               conditions=CONDITIONS)

    @app.route("/my-items")
    @login_required
    def my_items():
        rows = get_db().execute(
            "SELECT i.*, c.name AS category_name FROM items i "
            "JOIN categories c ON i.category_id = c.id WHERE i.seller_id = ? "
            "ORDER BY i.created_at DESC, i.id DESC",
            (g.user["id"],)).fetchall()
        return render_template("my_items.html",
                               items=[with_display(r) for r in rows])

    def _get_owned_item_or_403(item_id):
        item = get_db().execute("SELECT * FROM items WHERE id = ?",
                                (item_id,)).fetchone()
        if item is None:
            abort(404)
        if item["seller_id"] != g.user["id"]:
            abort(403, description="只有商品发布者本人可以操作")
        return item

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
            db.execute("BEGIN IMMEDIATE")
            item = db.execute(
                "SELECT price, seller_id, status FROM items WHERE id = ?",
                (item_id,)).fetchone()
            if item is None:
                _rollback(db)
                return redirect(url_for("index"))
            if item["status"] != "在售":
                _rollback(db)
                flash("商品已被买走或已下架")
                return redirect(url_for("index"))
            if item["seller_id"] == g.user["id"]:
                _rollback(db)
                flash("不能购买自己的商品")
                return redirect(url_for("item_detail", item_id=item_id))
            cur = db.execute(
                "UPDATE items SET status = '已预订' WHERE id = ? AND status = '在售'",
                (item_id,))
            if cur.rowcount == 0:
                _rollback(db)
                flash("商品已被买走或已下架")
                return redirect(url_for("index"))
            db.execute(
                "INSERT INTO orders (item_id, buyer_id, price, status) "
                "VALUES (?, ?, ?, '待交易')",
                (item_id, g.user["id"], item["price"]))
            db.execute("COMMIT")
            return redirect(url_for("index"))
        except sqlite3.IntegrityError:
            _rollback(db)
            flash("商品已被买走或已下架")
            return redirect(url_for("index"))
        except sqlite3.Error:
            _rollback(db)  # 异常路径一律回滚（规格要求），再抛给 500 处理
            raise

    return app


if __name__ == "__main__":
    create_app().run(debug=True)

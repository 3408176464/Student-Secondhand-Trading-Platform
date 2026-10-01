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
    return cents


def parse_price_filter(value):
    """筛选框价格：允许 0（单边过滤用）。"""
    value = value.strip()
    if not PRICE_RE.fullmatch(value):
        raise ValueError("价格区间必须为合法数字")
    return int(Decimal(value) * 100)


def with_display(row):
    """数据库行 → 展示用 dict（价格元、时间本地化）。"""
    d = dict(row)
    d["price_display"] = format_price(d["price"])
    d["created_at_local"] = to_local_time(d["created_at"])
    return d


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
        return render_template("error.html", message=e.description or "无权访问"), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template("error.html", message=e.description or "资源不存在"), 404

    @app.errorhandler(500)
    def internal_error(e):
        return render_template("error.html", message=e.description or "数据异常，请稍后重试"), 500

    @app.route("/")
    def index():
        return render_template("index.html", items=[], categories=[])

    return app


if __name__ == "__main__":
    create_app().run(debug=True)

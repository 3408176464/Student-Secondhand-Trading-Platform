# 校园二手交易平台 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现一个可运行的校园二手交易网站（Flask + SQLite），支持注册登录、发布商品、搜索筛选、下单与订单状态流转。

**Architecture:** Flask 应用工厂 + 原生 SQL（sqlite3）。`database.py` 负责连接（每连接 `PRAGMA foreign_keys=ON`、`isolation_level=None` 手动事务）、建表与示例数据；`app.py` 负责路由与业务（下单/确认/取消均为事务内条件更新）。金额存整数分，时间存 UTC、Python 侧转 UTC+8 展示。设计规格见 `docs/superpowers/specs/2026-10-02-campus-market-design.md`。

**Tech Stack:** Python 3.12、Flask、SQLite、unittest（标准库）、werkzeug.security（密码哈希）。

所有任务的测试命令在 `campus_market/` 目录下执行。项目尚无 git 仓库初始化问题：仓库根目录为 `数据库原理作业/`，已存在。

---

## 文件结构

```
campus_market/
├── app.py            # create_app 工厂 + 路由 + 业务逻辑 + 工具函数
├── database.py       # connect / init_db / seed_demo_data
├── schema.sql        # 建表与索引 SQL
├── requirements.txt  # Flask
├── templates/
│   ├── base.html         # 布局 + 导航 + flash
│   ├── error.html        # 403/404/500 统一错误页
│   ├── index.html        # 商品市场
│   ├── item_detail.html  # 商品详情
│   ├── register.html / login.html
│   ├── publish.html      # 发布商品
│   ├── my_items.html     # 我的商品（改价/下架）
│   ├── my_orders.html    # 我的订单
│   ├── sales.html        # 卖出记录
│   └── settings.html     # 设置
├── static/style.css
└── tests/
    ├── __init__.py
    └── test_app.py       # 全部 unittest 用例
```

约定（全部任务一致）：

- 路由名：`index` `/`、`register` `/register`、`login` `/login`、`logout` `/logout`(POST)、`publish` `/publish`、`item_detail` `/items/<int:item_id>`、`place_order` `/items/<int:item_id>/buy`(POST)、`edit_item_price` `/items/<int:item_id>/price`(POST)、`delist_item` `/items/<int:item_id>/delist`(POST)、`my_items` `/my-items`、`my_orders` `/my-orders`、`sales` `/sales`、`cancel_order` `/orders/<int:order_id>/cancel`(POST)、`confirm_order` `/orders/<int:order_id>/confirm`(POST)、`settings` `/settings`。
- 测试基类 `AppTestCase`（Task 2 定义）：临时库 + `create_app({"TESTING":True,"SECRET_KEY":"test","DATABASE":...,"SEED_DATA":False})`，提供 `register/login/logout/sql/user_id/insert_item` 辅助方法。
- 测试命令模板：`cd campus_market && python -m unittest tests.test_app.类名.方法名 -v`。

---

### Task 1: 项目骨架、schema.sql、database.py

**Files:**
- Create: `campus_market/requirements.txt`
- Create: `campus_market/schema.sql`
- Create: `campus_market/database.py`
- Create: `campus_market/tests/__init__.py`
- Create: `campus_market/tests/test_app.py`

- [ ] **Step 1: 创建目录与依赖文件**

`campus_market/requirements.txt`:

```
Flask>=3.0
```

`campus_market/tests/__init__.py`（空文件）。

安装依赖：`python -m pip install -r campus_market/requirements.txt`，期望输出 `Successfully installed Flask-...`。验证：`python -c "import flask; print(flask.__version__)"` 输出版本号。

- [ ] **Step 2: 写失败测试（数据库约束）**

`campus_market/tests/test_app.py`:

```python
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
```

- [ ] **Step 3: 运行测试确认失败**

Run: `cd campus_market && python -m unittest tests.test_app -v`
Expected: FAIL/ERROR，`ModuleNotFoundError: No module named 'database'`

- [ ] **Step 4: 实现 schema.sql 与 database.py**

`campus_market/schema.sql`:

```sql
-- 校园二手交易平台表结构（外键约束在每个连接上用 PRAGMA foreign_keys=ON 开启）
CREATE TABLE categories (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE users (
    id         INTEGER PRIMARY KEY,
    username   TEXT NOT NULL UNIQUE,
    password   TEXT NOT NULL,
    contact    TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE items (
    id          INTEGER PRIMARY KEY,
    title       TEXT NOT NULL,
    price       INTEGER NOT NULL CHECK (price > 0),
    condition   TEXT NOT NULL CHECK (condition IN ('全新','九成新','八成新','有使用痕迹')),
    description TEXT,
    status      TEXT NOT NULL DEFAULT '在售'
                CHECK (status IN ('在售','已预订','已售','下架')),
    category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE RESTRICT,
    seller_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE orders (
    id         INTEGER PRIMARY KEY,
    item_id    INTEGER NOT NULL REFERENCES items(id) ON DELETE RESTRICT,
    buyer_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    price      INTEGER NOT NULL CHECK (price > 0),
    status     TEXT NOT NULL DEFAULT '待交易'
               CHECK (status IN ('待交易','已完成','已取消')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_items_market ON items(status, category_id, price);
CREATE INDEX idx_orders_buyer ON orders(buyer_id);
CREATE INDEX idx_orders_item ON orders(item_id);
CREATE UNIQUE INDEX idx_orders_active_item ON orders(item_id) WHERE status = '待交易';
```

`campus_market/database.py`:

```python
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
    """建表（含索引）并写入 7 个固定分类；seed=True 时再写入示例数据。"""
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.executemany(
        "INSERT INTO categories (id, name) VALUES (?, ?)",
        [(1, "教材书籍"), (2, "手机数码"), (3, "数码配件"), (4, "宿舍生活"),
         (5, "交通出行"), (6, "服饰运动"), (7, "其他闲置")],
    )
    if seed:
        seed_demo_data(conn)
```

- [ ] **Step 5: 运行测试确认通过**

Run: `cd campus_market && python -m unittest tests.test_app -v`
Expected: `Ran 10 tests ... OK`

- [ ] **Step 6: 提交**

```bash
git add campus_market/requirements.txt campus_market/schema.sql campus_market/database.py campus_market/tests/
git commit -m "feat: 数据库骨架——建表、约束、索引与连接层"
```

---

### Task 2: app 工厂、工具函数、错误页、页面骨架

**Files:**
- Create: `.gitignore`（仓库根）
- Create: `campus_market/app.py`
- Create: `campus_market/templates/base.html`
- Create: `campus_market/templates/error.html`
- Create: `campus_market/templates/index.html`
- Create: `campus_market/static/style.css`
- Modify: `campus_market/tests/test_app.py`（追加工具函数测试与 `AppTestCase` 基类）

- [ ] **Step 1: 写失败测试**

追加到 `campus_market/tests/test_app.py`（imports 增加 `from app import create_app, escape_like, format_price, to_local_time, parse_price_yuan`）：

```python
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
            parse_price_yuan("9" * 12)  # 超长数字按超范围拒绝，不溢出


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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd campus_market && python -m unittest tests.test_app -v`
Expected: ERROR，`ModuleNotFoundError: No module named 'app'`

- [ ] **Step 3: 实现 app.py 与页面骨架**

`campus_market/app.py`:

```python
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
MAX_PRICE_CENTS = 10 ** 9  # 价格上限 10^9 分 = 1000 万元，防超长数字绑定 SQLite 溢出


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

    def _zh(e, fallback):
        """Werkzeug 默认描述换中文兜底；自定义 description 原样保留。"""
        return fallback if e.description in (None, type(e).description) else e.description

    @app.errorhandler(403)
    def forbidden(e):
        return render_template("error.html", message=_zh(e, "无权访问")), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template("error.html", message=_zh(e, "资源不存在")), 404

    @app.errorhandler(500)
    def internal_error(e):
        return render_template("error.html", message=_zh(e, "数据异常，请稍后重试")), 500

    @app.route("/")
    def index():
        return render_template("index.html", items=[], categories=[])

    return app


if __name__ == "__main__":
    create_app().run(debug=True)
```

`campus_market/templates/base.html`:

```html
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>{% block title %}校园二手交易平台{% endblock %}</title>
  <link rel="stylesheet" href="{{ url_for('static', filename='style.css') }}">
</head>
<body>
<nav>
  <a href="{{ url_for('index') }}">商品市场</a>
</nav>
<main>
  {% for message in get_flashed_messages() %}
    <div class="flash">{{ message }}</div>
  {% endfor %}
  {% block content %}{% endblock %}
</main>
</body>
</html>
```

注意：本任务的 nav 只有「商品市场」。登录/注册链接所属路由 Task 3 才实现，提前写 `url_for('login')` 会触发 `BuildError`。Task 3 起 nav 逐步补齐，最终形态见 Task 12。

`campus_market/templates/error.html`:

```html
{% extends "base.html" %}
{% block title %}出错了{% endblock %}
{% block content %}
<h1>出错了</h1>
<p>{{ message }}</p>
<a href="{{ url_for('index') }}">返回商品市场</a>
{% endblock %}
```

`campus_market/templates/index.html`:

```html
{% extends "base.html" %}
{% block title %}商品市场{% endblock %}
{% block content %}
<h1>商品市场</h1>
<form method="get" class="filter-bar">
  <input type="text" name="q" placeholder="搜索商品名">
  <input type="text" name="min_price" placeholder="最低价">
  <input type="text" name="max_price" placeholder="最高价">
  <button type="submit">筛选</button>
</form>
<ul class="item-list">
  {% for item in items %}
  <li>{{ item.title }}</li>
  {% else %}
  <li>暂无商品</li>
  {% endfor %}
</ul>
{% endblock %}
```

仓库根 `.gitignore`（防止把编译产物和数据库文件提交进 git）：

```
__pycache__/
*.pyc
*.db
desktop.ini
```

`campus_market/static/style.css`:

```css
body { font-family: sans-serif; margin: 0; background: #f6f7f9; color: #222; }
nav { background: #2c6eaf; padding: 10px 16px; display: flex; gap: 14px; align-items: center; }
nav a { color: #fff; text-decoration: none; }
nav form.inline { display: inline; margin-left: auto; }
nav button { background: #fff; border: 0; padding: 4px 10px; border-radius: 4px; cursor: pointer; }
main { max-width: 900px; margin: 16px auto; padding: 0 16px; }
.flash { background: #fff3cd; border: 1px solid #ffe08a; padding: 8px 12px; border-radius: 4px; margin-bottom: 12px; }
.filter-bar { display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }
.filter-bar input { padding: 6px 8px; border: 1px solid #ccc; border-radius: 4px; }
.item-list { list-style: none; padding: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 12px; }
.item-card { background: #fff; border: 1px solid #e2e4e8; border-radius: 6px; padding: 12px; }
.item-card h3 { margin: 0 0 6px; font-size: 16px; }
.price { color: #c0392b; font-weight: bold; }
.meta { color: #777; font-size: 13px; }
form.inline { display: inline; }
button { padding: 6px 12px; border: 0; border-radius: 4px; background: #2c6eaf; color: #fff; cursor: pointer; }
button.danger { background: #c0392b; }
table { width: 100%; border-collapse: collapse; background: #fff; }
th, td { border: 1px solid #e2e4e8; padding: 8px 10px; text-align: left; font-size: 14px; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 12px; background: #eee; }
.badge.on-sale { background: #d4edda; }
.badge.reserved { background: #fff3cd; }
.badge.sold { background: #d6eaff; }
.badge.off { background: #f8d7da; }
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd campus_market && python -m unittest tests.test_app -v`
Expected: `Ran 16 tests ... OK`（DatabaseTests 10 + HelperTests 4 + SmokeTests 2）

- [ ] **Step 5: 提交**

```bash
git add .gitignore campus_market/app.py campus_market/templates/ campus_market/static/ campus_market/tests/
git commit -m "feat: 应用工厂、工具函数与页面骨架"
```

---

### Task 3: 注册、登录、登出

**Files:**
- Modify: `campus_market/app.py`（新增 register/login/logout 路由）
- Modify: `campus_market/templates/base.html`（导航补登录/注册/登出入口）
- Create: `campus_market/templates/register.html`、`campus_market/templates/login.html`
- Modify: `campus_market/tests/test_app.py`（追加 `AuthTests`）

- [ ] **Step 1: 写失败测试**

追加 `AuthTests(AppTestCase)`：

```python
class AuthTests(AppTestCase):
    def test_register_login_logout(self):
        resp = self.register("alice", "password123", "wx_alice")
        self.assertIn("登录", resp.get_data(as_text=True))
        resp = self.login("alice", "password123")
        self.assertIn("登出", resp.get_data(as_text=True))  # 「登出」只在登录态渲染，验证 session 真的建立了
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd campus_market && python -m unittest tests.test_app.AuthTests -v`
Expected: FAIL（`/register` 返回 404 或 302 到 404）

- [ ] **Step 3: 实现路由与模板**

`campus_market/app.py` 在 `index` 路由前追加（import 顶部补 `import sqlite3`）：

```python
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
```

`campus_market/templates/register.html`:

```html
{% extends "base.html" %}
{% block title %}注册{% endblock %}
{% block content %}
<h1>注册</h1>
<form method="post">
  <p><label>用户名 <input name="username" required></label></p>
  <p><label>密码（至少 6 位）<input type="password" name="password" required></label></p>
  <p><label>联系方式（可不填）<input name="contact"></label></p>
  <button type="submit">注册</button>
</form>
{% endblock %}
```

`campus_market/templates/login.html`:

```html
{% extends "base.html" %}
{% block title %}登录{% endblock %}
{% block content %}
<h1>登录</h1>
<form method="post">
  <p><label>用户名 <input name="username" required></label></p>
  <p><label>密码 <input type="password" name="password" required></label></p>
  <button type="submit">登录</button>
</form>
{% endblock %}
```

`campus_market/templates/base.html` 的 nav 块整体替换为：

```html
<nav>
  <a href="{{ url_for('index') }}">商品市场</a>
  {% if g.user %}
    <form action="{{ url_for('logout') }}" method="post" class="inline">
      <button type="submit">登出</button>
    </form>
  {% else %}
    <a href="{{ url_for('login') }}">登录</a>
    <a href="{{ url_for('register') }}">注册</a>
  {% endif %}
</nav>
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd campus_market && python -m unittest tests.test_app -v`
Expected: `Ran 23 tests ... OK`

- [ ] **Step 5: 提交**

```bash
git add campus_market/app.py campus_market/templates/ campus_market/tests/
git commit -m "feat: 注册、登录、登出"
```

---

### Task 4: 示例数据

**Files:**
- Modify: `campus_market/database.py`（追加 `seed_demo_data`）
- Modify: `campus_market/tests/test_app.py`（追加 `SeedTests`）

- [ ] **Step 1: 写失败测试**

追加（`AppTestCase` 用 `SEED_DATA: False`，示例数据测试自建 seeded 应用）：

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd campus_market && python -m unittest tests.test_app.SeedTests -v`
Expected: FAIL（`seed_demo_data` 未定义 / 数量为 0）

- [ ] **Step 3: 实现 seed_demo_data**

`campus_market/database.py` 顶部补 `from werkzeug.security import generate_password_hash`，并把 Task 1 留下的 `seed_demo_data` 桩函数（`raise NotImplementedError`）**整体替换**为真实实现（不是追加，避免同名双定义）：

```python
def seed_demo_data(conn):
    """示例数据：10 个用户、30 件商品、12 个订单，状态保持一致，每人都既买又卖。"""
    password = generate_password_hash("password123")
    users = [
        (1, "zhangsan", "wx_zhangsan", "2026-09-20 09:00:00"),
        (2, "lisi", "13800000002", "2026-09-20 09:10:00"),
        (3, "wangwu", None, "2026-09-20 09:20:00"),
        (4, "zhaoliu", "wx_zhaoliu", "2026-09-21 10:00:00"),
        (5, "chenqi", "13800000005", "2026-09-21 10:10:00"),
        (6, "zhouba", None, "2026-09-22 11:00:00"),
        (7, "wujiu", "wx_wujiu", "2026-09-22 11:10:00"),
        (8, "zhengshi", "13800000008", "2026-09-23 12:00:00"),
        (9, "sunshiyi", None, "2026-09-23 12:10:00"),
        (10, "lishier", "wx_lishier", "2026-09-24 13:00:00"),
    ]
    conn.executemany(
        "INSERT INTO users (id, username, password, contact, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        [(i, name, password, contact, created) for i, name, contact, created in users])

    items = [
        (1, "高等数学（同济第七版）", 1500, "九成新", "少量笔记无缺页", "在售", 1, 1, "2026-09-25 08:00:00"),
        (2, "线性代数教材", 1200, "八成新", "有课后习题答案", "在售", 1, 1, "2026-09-25 08:10:00"),
        (3, "考研英语真题集", 2000, "全新", "未拆封", "在售", 1, 2, "2026-09-25 08:20:00"),
        (4, "大学物理实验教程", 900, "有使用痕迹", "封面有折痕", "已预订", 1, 2, "2026-09-25 08:30:00"),
        (5, "C程序设计语言", 1800, "九成新", "经典教材", "已售", 1, 3, "2026-09-25 08:40:00"),
        (6, "数据结构考研辅导", 2200, "八成新", "写过几页笔记", "已售", 1, 1, "2026-09-25 08:50:00"),
        (7, "概率论与数理统计", 1300, "九成新", "课后习题已做", "在售", 1, 1, "2026-09-25 09:00:00"),
        (8, "高等数学辅导讲义", 1100, "八成新", "无笔记", "在售", 1, 4, "2026-09-25 09:10:00"),
        (9, "iPhone 13 128G 星光色", 320000, "九成新", "电池健康89%配件齐", "在售", 2, 3, "2026-09-26 09:00:00"),
        (10, "iPad 2021 64G", 180000, "九成新", "带保护壳", "在售", 2, 5, "2026-09-26 09:10:00"),
        (11, "联想小新Pro14 笔记本", 350000, "八成新", "R5-5600H 16G", "在售", 2, 6, "2026-09-26 09:20:00"),
        (12, "索尼 WH-1000XM4 耳机", 85000, "九成新", "带原装收纳盒", "已预订", 2, 4, "2026-09-26 09:30:00"),
        (13, "小米平板5", 120000, "九成新", "贴膜完好", "已预订", 2, 5, "2026-09-26 09:40:00"),
        (14, "罗技 M330 静音鼠标", 6000, "有使用痕迹", "接收器在", "在售", 3, 7, "2026-09-26 10:00:00"),
        (15, "罗技 K380 键盘", 9000, "九成新", "蓝牙双模", "在售", 3, 7, "2026-09-26 10:10:00"),
        (16, "小米充电宝 10000mAh", 4500, "八成新", "能正常充电", "在售", 3, 8, "2026-09-26 10:20:00"),
        (17, "Anker 65W 充电器", 5500, "全新", "未使用", "在售", 3, 8, "2026-09-26 10:30:00"),
        (18, "机械键盘 Cherry红轴", 18000, "九成新", "无缺键", "已售", 3, 6, "2026-09-26 10:40:00"),
        (19, "护眼 LED 台灯", 2500, "八成新", "三档调光", "在售", 4, 9, "2026-09-27 11:00:00"),
        (20, "宿舍小风扇", 2000, "有使用痕迹", "声音小", "在售", 4, 9, "2026-09-27 11:10:00"),
        (21, "收纳箱两件套", 1500, "九成新", "毕业出", "在售", 4, 10, "2026-09-27 11:20:00"),
        (22, "电水壶 1.5L", 3000, "八成新", "内胆干净", "在售", 4, 8, "2026-09-27 11:30:00"),
        (23, "懒人沙发", 4000, "九成新", "自提", "在售", 4, 10, "2026-09-27 11:40:00"),
        (24, "捷安特通勤自行车", 26000, "八成新", "刹车灵车锁送", "在售", 5, 2, "2026-09-28 12:00:00"),
        (25, "折叠自行车", 18000, "有使用痕迹", "链条需上油", "下架", 5, 4, "2026-09-28 12:10:00"),
        (26, "自行车头盔", 3500, "九成新", "带内衬", "在售", 5, 6, "2026-09-28 12:20:00"),
        (27, "李宁跑步鞋 42 码", 5000, "有使用痕迹", "鞋底磨损", "在售", 6, 9, "2026-09-28 13:00:00"),
        (28, "尤尼克斯羽毛球拍", 7000, "九成新", "带拍套", "在售", 6, 5, "2026-09-28 13:10:00"),
        (29, "落地晾衣架", 1200, "八成新", "毕业甩卖", "在售", 7, 10, "2026-09-28 14:00:00"),
        (30, "站立式全身镜", 1800, "九成新", "自提", "在售", 7, 7, "2026-09-28 14:10:00"),
    ]
    conn.executemany(
        "INSERT INTO items (id, title, price, condition, description, status, "
        "category_id, seller_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        items)

    orders = [
        (1, 4, 5, 900, "待交易", "2026-09-29 10:00:00"),
        (2, 12, 6, 85000, "待交易", "2026-09-29 10:10:00"),
        (3, 5, 7, 1800, "已完成", "2026-09-28 15:00:00"),
        (4, 18, 8, 18000, "已完成", "2026-09-28 15:10:00"),
        (5, 7, 9, 1300, "已取消", "2026-09-28 16:00:00"),
        (6, 22, 10, 3000, "已取消", "2026-09-28 16:10:00"),
        (7, 6, 9, 2200, "已完成", "2026-09-29 09:00:00"),
        (8, 13, 10, 120000, "待交易", "2026-09-29 09:10:00"),
        (9, 3, 1, 2000, "已取消", "2026-09-28 17:00:00"),
        (10, 1, 2, 1500, "已取消", "2026-09-28 17:10:00"),
        (11, 10, 3, 180000, "已取消", "2026-09-28 17:20:00"),
        (12, 24, 4, 26000, "已取消", "2026-09-28 17:30:00"),
    ]
    conn.executemany(
        "INSERT INTO orders (id, item_id, buyer_id, price, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        orders)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd campus_market && python -m unittest tests.test_app -v`
Expected: `Ran 26 tests ... OK`

- [ ] **Step 5: 提交**

```bash
git add campus_market/database.py campus_market/tests/
git commit -m "feat: 示例数据——10 用户、30 商品、12 订单"
```

---

### Task 5: 商品市场（搜索 / 价格区间 / 分类 / 排序）

**Files:**
- Modify: `campus_market/app.py`（`index` 路由完整实现）
- Modify: `campus_market/templates/index.html`（卡片列表 + 分类栏）
- Modify: `campus_market/tests/test_app.py`（追加 `MarketTests`）

- [ ] **Step 1: 写失败测试**

追加（用 `AppTestCase.insert_item` 直接造数据；`created_at` 固定值用于排序断言）：

```python
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
```

`AppTestCase` 需要一个 `register_user` 辅助（只注册拿用户名，不依赖响应内容）——在 `AppTestCase` 中补：

```python
    def register_user(self, username):
        self.register(username, "password123", "")
        return self.user_id(username)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd campus_market && python -m unittest tests.test_app.MarketTests -v`
Expected: FAIL（列表为空、断言找不到商品名）

- [ ] **Step 3: 实现 index 路由与模板**

`campus_market/app.py` 中 `index` 路由整体替换为：

```python
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
```

`campus_market/templates/index.html` 整体替换为：

```html
{% extends "base.html" %}
{% block title %}商品市场{% endblock %}
{% block content %}
<h1>商品市场</h1>
<form method="get" class="filter-bar">
  <input type="text" name="q" value="{{ request.args.get('q', '') }}" placeholder="搜索商品名">
  <input type="text" name="min_price" value="{{ request.args.get('min_price', '') }}" placeholder="最低价">
  <input type="text" name="max_price" value="{{ request.args.get('max_price', '') }}" placeholder="最高价">
  <input type="hidden" name="category_id" value="{{ request.args.get('category_id', '') }}">
  <button type="submit">筛选</button>
</form>
<p class="meta">
  <a href="{{ url_for('index', q=request.args.get('q', ''), min_price=request.args.get('min_price', ''), max_price=request.args.get('max_price', '')) }}">全部</a>
  {% for c in categories %}
    <a href="{{ url_for('index', q=request.args.get('q', ''), min_price=request.args.get('min_price', ''), max_price=request.args.get('max_price', ''), category_id=c.id) }}">{{ c.name }}</a>
  {% endfor %}
</p>
<ul class="item-list">
  {% for item in items %}
  <li class="item-card">
    <h3>{{ item.title }}</h3>
    <div class="price">¥{{ item.price_display }}</div>
    <div class="meta">{{ item.condition }} · {{ item.category_name }} · 卖家 {{ item.seller_name }}</div>
  </li>
  {% else %}
  <li>暂无商品</li>
  {% endfor %}
</ul>
{% endblock %}
```

注意：本任务商品名先渲染为纯文本。`item_detail` 路由 Task 6 才实现，提前写 `url_for('item_detail')` 会触发 `BuildError`；Task 6 把这一行换回链接。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd campus_market && python -m unittest tests.test_app -v`
Expected: `Ran 36 tests ... OK`

- [ ] **Step 5: 提交**

```bash
git add campus_market/app.py campus_market/templates/index.html campus_market/tests/
git commit -m "feat: 商品市场——搜索、价格区间、分类筛选与排序"
```

---

### Task 6: 商品详情（分级访问 + 购买按钮）

**Files:**
- Modify: `campus_market/app.py`（新增 `item_detail` 路由）
- Modify: `campus_market/templates/index.html`（商品名改回详情链接）
- Create: `campus_market/templates/item_detail.html`
- Modify: `campus_market/tests/test_app.py`（追加 `DetailTests`）

- [ ] **Step 1: 写失败测试**

追加：

```python
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
        self.assertIn("买走的商品", self.client.get("/items/5").get_data(as_text=True))

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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd campus_market && python -m unittest tests.test_app.DetailTests -v`
Expected: FAIL（`/items/1` 返回 404）

- [ ] **Step 3: 实现 item_detail 路由与模板**

`campus_market/app.py` 追加：

```python
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
```

`campus_market/templates/item_detail.html`:

```html
{% extends "base.html" %}
{% block title %}{{ item.title }}{% endblock %}
{% block content %}
<h1>{{ item.title }}</h1>
<p><span class="price">¥{{ item.price_display }}</span>
  <span class="badge {{ {'在售': 'on-sale', '已预订': 'reserved', '已售': 'sold', '下架': 'off'}[item.status] }}">{{ item.status }}</span>
</p>
<p>成色：{{ item.condition }}　分类：{{ item.category_name }}</p>
<p>{{ item.description }}</p>
<p>卖家：{{ item.seller_name }}</p>
{% if g.user %}
  <p>联系方式：{{ item.seller_contact or '卖家未填写联系方式' }}</p>
{% endif %}
{% if can_buy %}
<button type="button">购买</button>
{% endif %}
{% endblock %}
```

注意：本任务的「购买」只是占位按钮（`type="button"` 不提交）。`place_order` 路由 Task 9 才实现，提前写 `url_for('place_order')` 会触发 `BuildError`；Task 9 把它换成真实 POST 表单。

`campus_market/templates/index.html` 中商品名一行替换为链接：

```html
    <h3><a href="{{ url_for('item_detail', item_id=item.id) }}">{{ item.title }}</a></h3>
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd campus_market && python -m unittest tests.test_app -v`
Expected: `Ran 43 tests ... OK`

- [ ] **Step 5: 提交**

```bash
git add campus_market/app.py campus_market/templates/ campus_market/tests/
git commit -m "feat: 商品详情——按状态分级访问、联系方式与购买按钮"
```

---

### Task 7: 发布商品

**Files:**
- Modify: `campus_market/app.py`（新增 `publish` 路由；`login_required` 挂到受保护路由）
- Create: `campus_market/templates/publish.html`
- Modify: `campus_market/templates/base.html`（导航补「发布商品」）
- Modify: `campus_market/tests/test_app.py`（追加 `PublishTests`；`AppTestCase` 补 `create_item`）

- [ ] **Step 1: 写失败测试**

`AppTestCase` 追加辅助方法：

```python
    def create_item(self, title="高等数学教材", price="25.00", category_id=1,
                    condition="九成新", description="有少量笔记"):
        return self.client.post("/publish", data={
            "title": title, "price": price, "category_id": category_id,
            "condition": condition, "description": description,
        }, follow_redirects=True)
```

追加测试类：

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd campus_market && python -m unittest tests.test_app.PublishTests -v`
Expected: FAIL（`/publish` 404）

- [ ] **Step 3: 实现 publish 路由与模板**

`campus_market/app.py` 追加：

```python
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
                return redirect(url_for("index"))  # Task 8 实现 my_items 后改回
            flash(error)
        categories = db.execute("SELECT * FROM categories ORDER BY id").fetchall()
        return render_template("publish.html", categories=categories,
                               conditions=CONDITIONS)
```

注意：成功跳转目标是 `index`——`my_items` 端点 Task 8 才建，现在写 `url_for("my_items")` 会 `BuildError`。**Task 8 Step 3 有专门一步把它换回 `my_items`。**

`campus_market/templates/publish.html`:

```html
{% extends "base.html" %}
{% block title %}发布商品{% endblock %}
{% block content %}
<h1>发布商品</h1>
<form method="post">
  <p><label>商品名 <input name="title" required></label></p>
  <p><label>分类
    <select name="category_id">
      {% for c in categories %}<option value="{{ c.id }}">{{ c.name }}</option>{% endfor %}
    </select>
  </label></p>
  <p><label>价格（元）<input name="price" placeholder="25.00" required></label></p>
  <p><label>成色
    <select name="condition">
      {% for c in conditions %}<option value="{{ c }}">{{ c }}</option>{% endfor %}
    </select>
  </label></p>
  <p><label>描述 <textarea name="description"></textarea></label></p>
  <button type="submit">发布</button>
</form>
{% endblock %}
```

`campus_market/templates/base.html` nav 整体替换（此后 Task 8/10/11/12 继续在此基础上加链接）：

```html
<nav>
  <a href="{{ url_for('index') }}">商品市场</a>
  {% if g.user %}
    <a href="{{ url_for('publish') }}">发布商品</a>
    <form action="{{ url_for('logout') }}" method="post" class="inline">
      <button type="submit">登出</button>
    </form>
  {% else %}
    <a href="{{ url_for('login') }}">登录</a>
    <a href="{{ url_for('register') }}">注册</a>
  {% endif %}
</nav>
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd campus_market && python -m unittest tests.test_app -v`
Expected: `Ran 48 tests ... OK`

- [ ] **Step 5: 提交**

```bash
git add campus_market/app.py campus_market/templates/ campus_market/tests/
git commit -m "feat: 发布商品与价格校验"
```

---

### Task 8: 我的商品（改价 / 下架）

**Files:**
- Modify: `campus_market/app.py`（新增 `my_items`、`edit_item_price`、`delist_item`）
- Create: `campus_market/templates/my_items.html`
- Modify: `campus_market/templates/base.html`（导航补「我的商品」）
- Modify: `campus_market/tests/test_app.py`（追加 `MyItemsTests`）

- [ ] **Step 1: 写失败测试**

追加：

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd campus_market && python -m unittest tests.test_app.MyItemsTests -v`
Expected: FAIL（`/my-items` 404）

- [ ] **Step 3: 实现路由与模板**

先把 Task 7 留的跳转换掉：`publish` 路由成功分支的 `return redirect(url_for("index"))  # Task 8 实现 my_items 后改回` 改为 `return redirect(url_for("my_items"))`（删掉注释），发布成功后落到我的商品页。

`campus_market/app.py` 追加：

```python
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
```

`campus_market/templates/my_items.html`:

```html
{% extends "base.html" %}
{% block title %}我的商品{% endblock %}
{% block content %}
<h1>我的商品</h1>
<table>
  <tr><th>商品</th><th>价格</th><th>状态</th><th>发布时间</th><th>操作</th></tr>
  {% for item in items %}
  <tr>
    <td><a href="{{ url_for('item_detail', item_id=item.id) }}">{{ item.title }}</a></td>
    <td>¥{{ item.price_display }}</td>
    <td><span class="badge">{{ item.status }}</span></td>
    <td>{{ item.created_at_local }}</td>
    <td>
      {% if item.status == '在售' %}
      <form action="{{ url_for('edit_item_price', item_id=item.id) }}" method="post" class="inline">
        <input name="price" value="{{ item.price_display }}" size="6">
        <button type="submit">改价</button>
      </form>
      <form action="{{ url_for('delist_item', item_id=item.id) }}" method="post" class="inline">
        <button type="submit" class="danger">下架（不可恢复）</button>
      </form>
      {% else %}—{% endif %}
    </td>
  </tr>
  {% else %}
  <tr><td colspan="5">还没有发布商品</td></tr>
  {% endfor %}
</table>
{% endblock %}
```

`campus_market/templates/base.html` nav 整体替换（在「发布商品」后加「我的商品」）：

```html
<nav>
  <a href="{{ url_for('index') }}">商品市场</a>
  {% if g.user %}
    <a href="{{ url_for('publish') }}">发布商品</a>
    <a href="{{ url_for('my_items') }}">我的商品</a>
    <form action="{{ url_for('logout') }}" method="post" class="inline">
      <button type="submit">登出</button>
    </form>
  {% else %}
    <a href="{{ url_for('login') }}">登录</a>
    <a href="{{ url_for('register') }}">注册</a>
  {% endif %}
</nav>
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd campus_market && python -m unittest tests.test_app -v`
Expected: `Ran 53 tests ... OK`

- [ ] **Step 5: 提交**

```bash
git add campus_market/app.py campus_market/templates/ campus_market/tests/
git commit -m "feat: 我的商品——改价与下架（归属鉴权 + 状态校验）"
```

---

### Task 9: 下单事务（防超卖 + 价格快照）

**Files:**
- Modify: `campus_market/app.py`（新增 `place_order`；补 `_rollback` 辅助）
- Modify: `campus_market/templates/item_detail.html`（占位按钮换成真实购买表单）
- Modify: `campus_market/tests/test_app.py`（追加 `PlaceOrderTests`）

- [ ] **Step 1: 写失败测试**

追加：

```python
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
        self.assertEqual(order["status"], "待交易")
        self.assertEqual(order["buyer_id"], self.user_id("buyer1"))

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
        # 规格第 7 节：下单→取消→再下单→确认完成；唯一索引不误拦历史订单
        self._seller_and_buyer()
        self.client.post("/items/1/buy")
        self.client.post("/orders/1/cancel")
        self.client.post("/items/1/buy")
        self.client.post("/orders/2/confirm")
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
```

（部分唯一索引的第二层防线「直接插两条待交易订单」已在 Task 1 的 `test_partial_unique_index` 覆盖。）

- [ ] **Step 2: 运行测试确认失败**

Run: `cd campus_market && python -m unittest tests.test_app.PlaceOrderTests -v`
Expected: FAIL（`/items/1/buy` 404）

- [ ] **Step 3: 实现 place_order**

`campus_market/app.py` 追加（模块级，路由之前）：

```python
def _rollback(db):
    try:
        db.execute("ROLLBACK")
    except sqlite3.Error:
        pass
```

路由：

```python
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
```

跳转说明：失败路径回 `index`（买家可能被重定向到自己无权看的下架详情页）；「不能买自己的商品」回详情页（自己的商品自己可看）。成功路径本任务先回 `index`——`my_orders` Task 10 才有，提前 `url_for('my_orders')` 会 `BuildError`；Task 10 把成功跳转改到 `my_orders`。

同时把 `item_detail.html` 的占位按钮换成真实表单：

```html
{% if can_buy %}
<form action="{{ url_for('place_order', item_id=item.id) }}" method="post">
  <button type="submit">购买</button>
</form>
{% endif %}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd campus_market && python -m unittest tests.test_app -v`
Expected: `Ran 58 tests ... OK`

- [ ] **Step 5: 提交**

```bash
git add campus_market/app.py campus_market/tests/
git commit -m "feat: 下单事务——价格快照、条件更新防超卖"
```

---

### Task 10: 我的订单（取消 / 确认 + 对方信息）

**Files:**
- Modify: `campus_market/app.py`（新增 `my_orders`、`cancel_order`、`confirm_order`、`_get_writable_order_or_403`、`_order_redirect`；`place_order` 跳转改回 `my_orders`）
- Create: `campus_market/templates/my_orders.html`
- Modify: `campus_market/templates/base.html`（导航补「我的订单」）
- Modify: `campus_market/tests/test_app.py`（追加 `MyOrdersTests`）

- [ ] **Step 1: 写失败测试**

追加：

```python
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
        self.assertIn("待交易", body)

    def test_empty_contact_message(self):
        self._setup_order(seller_contact="")
        body = self.client.get("/my-orders").get_data(as_text=True)
        self.assertIn("对方未填写联系方式", body)

    def test_cancelled_order_visible(self):
        self._setup_order()
        self.client.post("/orders/1/cancel")
        body = self.client.get("/my-orders").get_data(as_text=True)
        self.assertIn('<span class="badge">已取消</span>', body)  # 断言表格状态单元格，排除 flash 误命中

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

    def test_confirm_by_buyer(self):
        self._setup_order()
        self.client.post("/orders/1/confirm")
        self.assertEqual(self.sql("SELECT status FROM orders WHERE id = 1")
                         .fetchone()["status"], "已完成")
        self.assertEqual(self.sql("SELECT status FROM items WHERE id = 1")
                         .fetchone()["status"], "已售")

    def test_illegal_transition_rejected(self):
        self._setup_order()
        self.client.post("/orders/1/cancel")
        resp = self.client.post("/orders/1/confirm", follow_redirects=True)
        self.assertIn("订单状态已变更", resp.get_data(as_text=True))
        self.assertEqual(self.sql("SELECT status FROM orders WHERE id = 1")
                         .fetchone()["status"], "已取消")

    def test_data_inconsistency_500(self):
        self._setup_order()
        # 外部改坏数据：订单待交易但商品被改成在售
        self.sql("UPDATE items SET status = '在售' WHERE id = 1")
        resp = self.client.post("/orders/1/confirm")
        self.assertEqual(resp.status_code, 500)
        self.assertIn("数据异常", resp.get_data(as_text=True))
        # 事务回滚，订单状态不变
        self.assertEqual(self.sql("SELECT status FROM orders WHERE id = 1")
                         .fetchone()["status"], "待交易")

    def test_cancel_data_inconsistency_500(self):
        self._setup_order()
        # 同样的不一致场景走取消路径（规格第 7 节"完成/取消时"）
        self.sql("UPDATE items SET status = '已售' WHERE id = 1")
        resp = self.client.post("/orders/1/cancel")
        self.assertEqual(resp.status_code, 500)
        self.assertIn("数据异常", resp.get_data(as_text=True))
        self.assertEqual(self.sql("SELECT status FROM orders WHERE id = 1")
                         .fetchone()["status"], "待交易")

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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd campus_market && python -m unittest tests.test_app.MyOrdersTests -v`
Expected: FAIL（`/my-orders` 404）

- [ ] **Step 3: 实现路由与模板**

`campus_market/app.py` 追加，并把 `place_order` 成功分支的 `return redirect(url_for("index"))` 改为 `return redirect(url_for("my_orders"))`：

```python
    @app.route("/my-orders")
    @login_required
    def my_orders():
        rows = get_db().execute(
            "SELECT o.*, i.title AS item_title, i.status AS item_status, "
            "u.username AS seller_name, u.contact AS seller_contact "
            "FROM orders o JOIN items i ON o.item_id = i.id "
            "JOIN users u ON i.seller_id = u.id "
            "WHERE o.buyer_id = ? ORDER BY o.created_at DESC, o.id DESC",
            (g.user["id"],)).fetchall()
        return render_template("my_orders.html",
                               orders=[with_display(r) for r in rows])

    def _get_writable_order_or_403(order_id):
        order = get_db().execute(
            "SELECT o.*, i.seller_id FROM orders o "
            "JOIN items i ON o.item_id = i.id WHERE o.id = ?",
            (order_id,)).fetchone()
        if order is None:
            abort(404)
        if g.user["id"] not in (order["buyer_id"], order["seller_id"]):
            abort(403, description="只有订单双方可以操作")
        return order

    def _order_redirect(order):
        """订单操作后按关系跳转：买家回我的订单，卖家回我的商品。"""
        if g.user["id"] == order["buyer_id"]:
            return redirect(url_for("my_orders"))
        return redirect(url_for("my_items"))  # Task 11 把此处换成 url_for("sales")

    @app.route("/orders/<int:order_id>/cancel", methods=("POST",))
    @login_required
    def cancel_order(order_id):
        order = _get_writable_order_or_403(order_id)
        db = get_db()
        try:
            db.execute("BEGIN IMMEDIATE")
            cur = db.execute(
                "UPDATE orders SET status = '已取消' WHERE id = ? AND status = '待交易'",
                (order_id,))
            if cur.rowcount == 0:
                _rollback(db)
                flash("订单状态已变更，无法取消")
                return _order_redirect(order)
            cur2 = db.execute(
                "UPDATE items SET status = '在售' WHERE id = ? AND status = '已预订'",
                (order["item_id"],))
            if cur2.rowcount == 0:
                _rollback(db)
                abort(500, description="数据异常，请稍后重试")
            db.execute("COMMIT")
        except sqlite3.Error:
            _rollback(db)
            abort(500, description="数据异常，请稍后重试")
        flash("订单已取消")
        return _order_redirect(order)

    @app.route("/orders/<int:order_id>/confirm", methods=("POST",))
    @login_required
    def confirm_order(order_id):
        order = _get_writable_order_or_403(order_id)
        db = get_db()
        try:
            db.execute("BEGIN IMMEDIATE")
            cur = db.execute(
                "UPDATE orders SET status = '已完成' WHERE id = ? AND status = '待交易'",
                (order_id,))
            if cur.rowcount == 0:
                _rollback(db)
                flash("订单状态已变更，无法确认")
                return _order_redirect(order)
            cur2 = db.execute(
                "UPDATE items SET status = '已售' WHERE id = ? AND status = '已预订'",
                (order["item_id"],))
            if cur2.rowcount == 0:
                _rollback(db)
                abort(500, description="数据异常，请稍后重试")
            db.execute("COMMIT")
        except sqlite3.Error:
            _rollback(db)
            abort(500, description="数据异常，请稍后重试")
        flash("交易已完成")
        return _order_redirect(order)
```

`campus_market/templates/my_orders.html`:

```html
{% extends "base.html" %}
{% block title %}我的订单{% endblock %}
{% block content %}
<h1>我的订单</h1>
<table>
  <tr><th>商品</th><th>成交价</th><th>状态</th><th>卖家</th><th>联系方式</th><th>下单时间</th><th>操作</th></tr>
  {% for o in orders %}
  <tr>
    <td>
      {% if o.item_status != '下架' %}
        <a href="{{ url_for('item_detail', item_id=o.item_id) }}">{{ o.item_title }}</a>
      {% else %}{{ o.item_title }}{% endif %}
    </td>
    <td>¥{{ o.price_display }}</td>
    <td><span class="badge">{{ o.status }}</span></td>
    <td>{{ o.seller_name }}</td>
    <td>{{ o.seller_contact or '对方未填写联系方式' }}</td>
    <td>{{ o.created_at_local }}</td>
    <td>
      {% if o.status == '待交易' %}
      <form action="{{ url_for('cancel_order', order_id=o.id) }}" method="post" class="inline">
        <button type="submit" class="danger">取消</button>
      </form>
      <form action="{{ url_for('confirm_order', order_id=o.id) }}" method="post" class="inline">
        <button type="submit">确认完成</button>
      </form>
      {% else %}—{% endif %}
    </td>
  </tr>
  {% else %}
  <tr><td colspan="7">还没有订单</td></tr>
  {% endfor %}
</table>
{% endblock %}
```

`campus_market/templates/base.html` nav 整体替换（加「我的订单」）：

```html
<nav>
  <a href="{{ url_for('index') }}">商品市场</a>
  {% if g.user %}
    <a href="{{ url_for('publish') }}">发布商品</a>
    <a href="{{ url_for('my_orders') }}">我的订单</a>
    <a href="{{ url_for('my_items') }}">我的商品</a>
    <form action="{{ url_for('logout') }}" method="post" class="inline">
      <button type="submit">登出</button>
    </form>
  {% else %}
    <a href="{{ url_for('login') }}">登录</a>
    <a href="{{ url_for('register') }}">注册</a>
  {% endif %}
</nav>
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd campus_market && python -m unittest tests.test_app -v`
Expected: `Ran 68 tests ... OK`

- [ ] **Step 5: 提交**

```bash
git add campus_market/app.py campus_market/templates/ campus_market/tests/
git commit -m "feat: 我的订单——取消、确认完成与数据异常回滚"
```

---

### Task 11: 卖出记录（取消 / 确认 + 买家信息）

**Files:**
- Modify: `campus_market/app.py`（新增 `sales` 路由）
- Create: `campus_market/templates/sales.html`
- Modify: `campus_market/templates/base.html`（导航补「卖出记录」）
- Modify: `campus_market/tests/test_app.py`（追加 `SalesTests`）

- [ ] **Step 1: 写失败测试**

追加：

```python
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
        self.assertIn('<span class="badge">已取消</span>', body)  # 断言表格状态单元格，排除 flash 文案误命中
        self.assertNotIn("拒绝订单", body)  # 终态只读，无操作按钮
        self.assertNotIn("确认完成", body)

    def test_seller_confirm_from_sales(self):
        self._setup_order()
        resp = self.client.post("/orders/1/confirm")
        self.assertEqual(resp.headers["Location"], "/sales")  # 锁定 _order_redirect 卖家分支已切到 sales
        self.assertEqual(self.sql("SELECT status FROM orders WHERE id = 1")
                         .fetchone()["status"], "已完成")
        self.assertEqual(self.sql("SELECT status FROM items WHERE id = 1")
                         .fetchone()["status"], "已售")

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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd campus_market && python -m unittest tests.test_app.SalesTests -v`
Expected: FAIL（`/sales` 404）

- [ ] **Step 3: 实现 sales 路由与模板**

先把 Task 10 留的跳转换掉：`_order_redirect` 里卖家分支的 `return redirect(url_for("my_items"))  # Task 11 把此处换成 url_for("sales")` 改为 `return redirect(url_for("sales"))`（删掉注释），卖家操作订单后落到卖出记录页。

`campus_market/app.py` 追加：

```python
    @app.route("/sales")
    @login_required
    def sales():
        rows = get_db().execute(
            "SELECT o.*, i.title AS item_title, i.status AS item_status, "
            "u.username AS buyer_name, u.contact AS buyer_contact "
            "FROM orders o JOIN items i ON o.item_id = i.id "
            "JOIN users u ON o.buyer_id = u.id "
            "WHERE i.seller_id = ? ORDER BY o.created_at DESC, o.id DESC",
            (g.user["id"],)).fetchall()
        return render_template("sales.html",
                               orders=[with_display(r) for r in rows])
```

`campus_market/templates/sales.html`:

```html
{% extends "base.html" %}
{% block title %}卖出记录{% endblock %}
{% block content %}
<h1>卖出记录</h1>
<table>
  <tr><th>商品</th><th>成交价</th><th>状态</th><th>买家</th><th>联系方式</th><th>下单时间</th><th>操作</th></tr>
  {% for o in orders %}
  <tr>
    <td><a href="{{ url_for('item_detail', item_id=o.item_id) }}">{{ o.item_title }}</a></td>
    <td>¥{{ o.price_display }}</td>
    <td><span class="badge">{{ o.status }}</span></td>
    <td>{{ o.buyer_name }}</td>
    <td>{{ o.buyer_contact or '对方未填写联系方式' }}</td>
    <td>{{ o.created_at_local }}</td>
    <td>
      {% if o.status == '待交易' %}
      <form action="{{ url_for('cancel_order', order_id=o.id) }}" method="post" class="inline">
        <button type="submit" class="danger">拒绝订单</button>
      </form>
      <form action="{{ url_for('confirm_order', order_id=o.id) }}" method="post" class="inline">
        <button type="submit">确认完成</button>
      </form>
      {% else %}—{% endif %}
    </td>
  </tr>
  {% else %}
  <tr><td colspan="7">还没有卖出记录</td></tr>
  {% endfor %}
</table>
{% endblock %}
```

`campus_market/templates/base.html` nav 整体替换（加「卖出记录」）：

```html
<nav>
  <a href="{{ url_for('index') }}">商品市场</a>
  {% if g.user %}
    <a href="{{ url_for('publish') }}">发布商品</a>
    <a href="{{ url_for('my_orders') }}">我的订单</a>
    <a href="{{ url_for('sales') }}">卖出记录</a>
    <a href="{{ url_for('my_items') }}">我的商品</a>
    <form action="{{ url_for('logout') }}" method="post" class="inline">
      <button type="submit">登出</button>
    </form>
  {% else %}
    <a href="{{ url_for('login') }}">登录</a>
    <a href="{{ url_for('register') }}">注册</a>
  {% endif %}
</nav>
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd campus_market && python -m unittest tests.test_app -v`
Expected: `Ran 73 tests ... OK`

- [ ] **Step 5: 提交**

```bash
git add campus_market/app.py campus_market/templates/ campus_market/tests/
git commit -m "feat: 卖出记录——JOIN 查询买家信息与订单操作"
```

---

### Task 12: 设置页、POST 约束回归、README

已决策事项（勿再反复）：订单操作对非参与方返回 403（规格明确要求，`test_third_party_cancel_403` 锁定），与"下架商品对非发布者 404 隐藏存在性"哲学上有差异——已知并接受，如要统一为 404 需连改规格+测试，作为独立改动另做，不在本任务范围。

**Files:**
- Modify: `campus_market/app.py`（新增 `settings` 路由）
- Create: `campus_market/templates/settings.html`
- Modify: `campus_market/templates/base.html`（导航补「设置」，形成最终导航）
- Create: `campus_market/README.md`
- Modify: `campus_market/tests/test_app.py`（追加 `SettingsTests` 与回归用例）

- [ ] **Step 1: 写失败测试**

追加：

```python
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
        self.assertEqual(self.sql("SELECT COUNT(*) AS n FROM orders")
                         .fetchone()["n"], 1)  # 下单成功，后续 405 检查才有意义
        for path in ("/items/1/delist", "/items/1/price", "/items/1/buy",
                     "/orders/1/cancel", "/orders/1/confirm"):
            self.assertEqual(self.client.get(path).status_code, 405)

    def test_created_at_displayed_local(self):
        self.register("seller1", "password123", "")
        self.login("seller1", "password123")
        self.create_item()
        self.sql("UPDATE items SET created_at = '2026-10-01 04:00:00'")
        body = self.client.get("/my-items").get_data(as_text=True)
        self.assertIn("2026-10-01 12:00:00", body)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd campus_market && python -m unittest tests.test_app.SettingsTests -v`
Expected: FAIL（`/settings` 404）

- [ ] **Step 3: 实现 settings 路由、模板、README**

`campus_market/app.py` 追加：

```python
    @app.route("/settings", methods=("GET", "POST"))
    @login_required
    def settings():
        if request.method == "POST":
            contact = request.form.get("contact", "").strip()
            get_db().execute("UPDATE users SET contact = ? WHERE id = ?",
                             (contact or None, g.user["id"]))
            flash("联系方式已保存")
            return redirect(url_for("settings"))
        return render_template("settings.html")
```

`campus_market/templates/settings.html`:

```html
{% extends "base.html" %}
{% block title %}设置{% endblock %}
{% block content %}
<h1>设置</h1>
<form method="post">
  <p><label>联系方式 <input name="contact" value="{{ g.user.contact or '' }}"></label></p>
  <button type="submit">保存</button>
</form>
{% endblock %}
```

`campus_market/templates/base.html` nav 整体替换（最终形态）：

```html
<nav>
  <a href="{{ url_for('index') }}">商品市场</a>
  {% if g.user %}
    <a href="{{ url_for('publish') }}">发布商品</a>
    <a href="{{ url_for('my_orders') }}">我的订单</a>
    <a href="{{ url_for('sales') }}">卖出记录</a>
    <a href="{{ url_for('my_items') }}">我的商品</a>
    <a href="{{ url_for('settings') }}">设置</a>
    <form action="{{ url_for('logout') }}" method="post" class="inline">
      <button type="submit">登出</button>
    </form>
  {% else %}
    <a href="{{ url_for('login') }}">登录</a>
    <a href="{{ url_for('register') }}">注册</a>
  {% endif %}
</nav>
```

`campus_market/README.md`:

```markdown
# 校园二手交易平台

Python + Flask + SQLite 的校园二手交易网站（数据库原理课程作业）。

## 运行

    pip install -r requirements.txt
    python app.py

浏览器打开 http://127.0.0.1:5000 。首次启动自动创建 campus_market.db
并写入示例数据（示例账号 zhangsan / lisi / wangwu / zhaoliu 等 10 个，密码统一为 password123）。

## 测试

    python -m unittest tests.test_app -v
```

- [ ] **Step 4: 全量测试确认通过**

Run: `cd campus_market && python -m unittest tests.test_app -v`
Expected: `Ran 82 tests ... OK`（全部用例通过，0 失败）

- [ ] **Step 5: 手工冒烟验证**

Run: `cd campus_market && python app.py`（后台启动），用浏览器或 curl 验证：

1. `http://127.0.0.1:5000/` 出现示例商品（如「高等数学（同济第七版）」）
2. `/?q=数学&min_price=12&max_price=20` 筛选生效（「高等数学（同济第七版）」¥15 保留、「高等数学辅导讲义」¥11 被过滤，与不加价格参数的页面结果不同）
3. 登录 `zhangsan` / `password123`，发布商品成功出现在市场
4. 退出登录，换 `lisi` 登录，下单一件 zhangsan 的商品，「我的订单」出现待交易订单
5. zhangsan 在「卖出记录」确认完成，商品变「已售」

验证完成后停掉进程。

- [ ] **Step 6: 提交**

```bash
git add campus_market/app.py campus_market/templates/ campus_market/README.md campus_market/tests/
git commit -m "feat: 设置页、POST 约束回归与运行说明"
```

---

## 任务依赖

Task 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11 → 12 严格顺序执行。每个任务结束时全量测试必须为绿。

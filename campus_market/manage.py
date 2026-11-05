"""数据库管理工具：查、备份、恢复、导出、体检。

不用装任何东西，有 Python 就能跑。

    python manage.py stats                 # 概况：文件大小、各表行数、最近活动
    python manage.py tables                # 表结构
    python manage.py query "SELECT * FROM items LIMIT 5"
    python manage.py backup                # 备份到 backup/db-20261101-013000.db
    python manage.py restore backup/xxx.db --yes
    python manage.py export items          # 导出 CSV（Excel 可直接打开）
    python manage.py check                 # 图片体检：缺文件 / 孤儿文件
    python manage.py vacuum                # 压缩文件体积
    python manage.py expire [分钟]          # 立刻释放超时未接单的订单（默认 120 分钟）
    python manage.py refresh-pending       # 演示前用：把「待接单」订单挪到最近，免得被超时释放
"""
import csv
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("CM_DATABASE", BASE_DIR / "campus_market.db"))
UPLOAD_DIR = Path(os.environ.get("CM_UPLOAD_DIR", BASE_DIR / "uploads")) / "items"
BACKUP_DIR = BASE_DIR / "backup"


def connect(readonly=False):
    if not DB_PATH.exists():
        sys.exit(f"数据库不存在：{DB_PATH}")
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) if readonly else sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    if not readonly:
        conn.execute("PRAGMA foreign_keys = ON")
    return conn


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def cmd_stats():
    conn = connect(readonly=True)
    print(f"数据库   {DB_PATH}")
    print(f"大小     {human(DB_PATH.stat().st_size)}   最后修改 {datetime.fromtimestamp(DB_PATH.stat().st_mtime):%Y-%m-%d %H:%M}")
    print()
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    print(f"{'表':<16}{'行数':>6}   说明")
    print("-" * 60)
    notes = {
        "users": "账号", "items": "商品", "item_images": "商品图片",
        "orders": "订单", "messages": "议价消息", "categories": "固定分类",
    }
    for t in tables:
        n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"{t:<16}{n:>6}   {notes.get(t, '')}")
    print()
    rows = conn.execute(
        "SELECT id, title, status, created_at FROM items "
        "ORDER BY created_at DESC, id DESC LIMIT 3").fetchall()
    if rows:
        print("最近发布的商品：")
        for r in rows:
            print(f"  #{r['id']:<3} {r['title'][:22]:<24} {r['status']}  {r['created_at']}")
    conn.close()


def cmd_tables():
    conn = connect(readonly=True)
    for (name, sql) in conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name"):
        print(f"\n===== {name} =====")
        for r in conn.execute(f"PRAGMA table_info({name})"):
            flags = []
            if r["pk"]:
                flags.append("主键")
            if r["notnull"]:
                flags.append("非空")
            extra = f"  [{', '.join(flags)}]" if flags else ""
            print(f"  {r['name']:<18}{r['type']:<10}默认={r['dflt_value']}{extra}")
        for idx in conn.execute(f"PRAGMA index_list({name})"):
            print(f"  索引: {idx['name']}{'  (唯一)' if idx['unique'] else ''}")
    conn.close()


def cmd_query(sql):
    if not sql.strip().lower().startswith(("select", "pragma", "explain")):
        sys.exit("只允许查询（SELECT / PRAGMA / EXPLAIN），避免误删数据。改数据请用 GUI 或写脚本。")
    conn = connect(readonly=True)
    try:
        cur = conn.execute(sql)
        rows = cur.fetchall()
    except sqlite3.Error as exc:
        sys.exit(f"SQL 错误：{exc}")
    if not rows:
        print("（没有结果）")
        conn.close()
        return
    cols = rows[0].keys()
    widths = [max(len(str(c)), *(len(str(r[c])) for r in rows)) for c in cols]
    print("  ".join(str(c).ljust(w) for c, w in zip(cols, widths)))
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print("  ".join(str(r[c])[:w].ljust(w) for c, w in zip(cols, widths)))
    print(f"\n{len(rows)} 行")
    conn.close()


def cmd_backup():
    BACKUP_DIR.mkdir(exist_ok=True)
    target = BACKUP_DIR / f"db-{datetime.now():%Y%m%d-%H%M%S}.db"
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(target)
    with dst:
        src.backup(dst)          # 用 SQLite 的在线备份接口，服务运行中也能安全备份
    src.close()
    dst.close()
    print(f"已备份到 {target.relative_to(BASE_DIR)}  ({human(target.stat().st_size)})")
    print("提醒：图片在 uploads/items/ 里，备份要一起拷走。")


def cmd_restore(path, confirmed):
    src = Path(path)
    if not src.exists():
        sys.exit(f"备份文件不存在：{src}")
    if not confirmed:
        sys.exit(f"会用 {src.name} 覆盖当前数据库。确认请加 --yes")
    BACKUP_DIR.mkdir(exist_ok=True)
    safety = BACKUP_DIR / f"before-restore-{datetime.now():%Y%m%d-%H%M%S}.db"
    if DB_PATH.exists():
        db = sqlite3.connect(DB_PATH)
        bak = sqlite3.connect(safety)
        with bak:
            db.backup(bak)
        db.close()
        bak.close()
        print(f"当前库已另存为 {safety.relative_to(BASE_DIR)}")
    shutil.copy2(src, DB_PATH)
    print(f"已用 {src.name} 覆盖数据库。重启服务后生效。")


def cmd_export(table, target=None):
    conn = connect(readonly=True)
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    if not exists:
        sys.exit(f"没有这张表：{table}")
    rows = conn.execute(f"SELECT * FROM {table}").fetchall()
    if not rows:
        sys.exit(f"{table} 里没有数据")
    out = Path(target) if target else BASE_DIR / f"{table}.csv"
    with out.open("w", newline="", encoding="utf-8-sig") as f:   # utf-8-sig 让 Excel 不乱码
        writer = csv.writer(f)
        writer.writerow(rows[0].keys())
        for r in rows:
            writer.writerow([r[c] for c in r.keys()])
    print(f"已导出 {len(rows)} 行到 {out}")
    conn.close()


def cmd_check():
    conn = connect(readonly=True)
    rows = conn.execute("SELECT item_id, filename FROM item_images ORDER BY item_id").fetchall()
    missing = []
    for r in rows:
        if not (UPLOAD_DIR / r["filename"]).exists():
            missing.append((r["item_id"], r["filename"]))
    files = {p.name for p in UPLOAD_DIR.glob("*")} if UPLOAD_DIR.exists() else set()
    recorded = {r["filename"] for r in rows}
    orphan = sorted(files - recorded)

    print(f"数据库记录 {len(rows)} 张图，磁盘上 {len(files)} 个文件")
    if missing:
        print(f"\n⚠️  {len(missing)} 条记录找不到文件（页面会显示空白）：")
        for item_id, name in missing[:10]:
            print(f"   商品 {item_id}: {name}")
    if orphan:
        print(f"\n⚠️  {len(orphan)} 个孤儿文件（没被任何商品引用，可删）：")
        for name in orphan[:10]:
            print(f"   {name}")
    if not missing and not orphan:
        print("✓ 图片记录和文件一一对应")
    conn.close()


def cmd_vacuum():
    before = DB_PATH.stat().st_size
    conn = connect()
    conn.execute("VACUUM")
    conn.close()
    after = DB_PATH.stat().st_size
    print(f"VACUUM 完成：{human(before)} → {human(after)}")


def cmd_expire(minutes=120):
    """手动跑一次超时释放。Web 端也会惰性扫，这里给 cron / 演示兜底用。"""
    import trading   # 延迟导入：manage.py 平时不需要整个应用栈

    minutes = int(minutes)
    conn = connect()
    try:
        n = trading.expire_stale_orders(conn, minutes)
    finally:
        conn.close()
    print(f"超时释放完成：取消 {n} 笔超过 {minutes} 分钟未接单的订单，商品已回「在售」")


def cmd_refresh_pending():
    """演示前用：把还在「待接单」的订单下单时间挪到最近，免得一打开就被超时释放。

    只改 created_at，订单内容和状态一概不动；改完这些订单又能正常演示「卖家接单」。
    """
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT id, item_id, created_at FROM orders WHERE status = '待接单' "
            "ORDER BY id").fetchall()
        if not rows:
            print("没有「待接单」订单，不用处理")
            return
        conn.execute("BEGIN IMMEDIATE")
        for i, r in enumerate(rows):
            # 依次往回排 5、20、35… 分钟，看起来像最近陆续下的单
            conn.execute(
                "UPDATE orders SET created_at = datetime('now', ?) WHERE id = ?",
                (f"-{5 + i * 15} minutes", r["id"]))
            print(f"  订单 {r['id']}（商品 {r['item_id']}）：{r['created_at']} → 约 {5 + i * 15} 分钟前")
        conn.execute("COMMIT")
    finally:
        conn.close()
    print(f"已刷新 {len(rows)} 笔「待接单」订单的下单时间")


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    cmd, rest = args[0], args[1:]

    if cmd == "stats":
        cmd_stats()
    elif cmd == "tables":
        cmd_tables()
    elif cmd == "query":
        if not rest:
            sys.exit('用法：manage.py query "SELECT * FROM items LIMIT 5"')
        cmd_query(rest[0])
    elif cmd == "backup":
        cmd_backup()
    elif cmd == "restore":
        if not rest:
            sys.exit("用法：manage.py restore backup/xxx.db --yes")
        cmd_restore(rest[0], "--yes" in rest)
    elif cmd == "export":
        if not rest:
            sys.exit("用法：manage.py export items [文件名.csv]")
        cmd_export(rest[0], rest[1] if len(rest) > 1 else None)
    elif cmd == "check":
        cmd_check()
    elif cmd == "vacuum":
        cmd_vacuum()
    elif cmd == "expire":
        cmd_expire(rest[0] if rest else 120)
    elif cmd == "refresh-pending":
        cmd_refresh_pending()
    else:
        print(__doc__)


if __name__ == "__main__":
    main()

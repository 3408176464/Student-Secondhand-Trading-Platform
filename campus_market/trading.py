"""交易核心：下单、接单、取消、交付确认、收货确认。

这一层不碰 HTTP，Web 路由和 /api/v1 都调用它，保证两端规则完全一致。
每个写操作自带 `BEGIN IMMEDIATE` 事务，失败一律回滚后抛 BizError。

状态机：
    下单        在售商品 → 订单「待接单」，商品「已预订」
    卖家接单    「待接单」→「待交付」，记录接单时间
    卖家交付    「待交付」+ seller_confirm_at
    买家收货    「待交付」+ buyer_confirm_at
    双方都确认  →「已完成」，商品「已售」（数据库 CHECK 兜底）
    任一方取消  「待接单」/「待交付」→「已取消」，商品回「在售」
"""
import random
import sqlite3

PENDING = "待接单"
DEALING = "待交付"
DONE = "已完成"
CANCELLED = "已取消"
ACTIVE_STATUSES = (PENDING, DEALING)
ALL_STATUSES = (PENDING, DEALING, DONE, CANCELLED)

# 待接单超过这个时长还没人处理就自动取消，商品回在售。
# 不做定时任务，靠请求进来时顺手扫一遍（见 app.py 的 sweep），够用且不会漏。
PENDING_TIMEOUT_MINUTES = 120

STATUS_HINTS = {
    PENDING: "已下单，等待卖家接单",
    DEALING: "卖家已接单，约定时间地点当面交易",
    DONE: "交易完成",
    CANCELLED: "交易已取消",
}


class BizError(Exception):
    """业务异常：带用户可读文案和对应 HTTP 状态码。"""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


class DataInconsistent(BizError):
    """订单与商品状态不自洽，属于数据被外部改坏。"""

    def __init__(self):
        super().__init__("数据异常，请稍后重试", 500)


def new_deal_code():
    """6 位面交核对码：双方线下核对，证明是当面交给对的人。"""
    return f"{random.randint(0, 999999):06d}"


def _rollback(db):
    try:
        db.execute("ROLLBACK")
    except sqlite3.Error:
        pass


def _load(db, order_id):
    order = db.execute(
        "SELECT o.*, i.seller_id, i.title AS item_title, i.status AS item_status "
        "FROM orders o JOIN items i ON o.item_id = i.id WHERE o.id = ?",
        (order_id,)).fetchone()
    if order is None:
        raise BizError("订单不存在", 404)
    return order


def _run(db, action):
    """统一事务壳：成功 COMMIT，BizError/数据库错误一律 ROLLBACK 再抛。"""
    try:
        db.execute("BEGIN IMMEDIATE")
        result = action()
        db.execute("COMMIT")
        return result
    except BizError:
        _rollback(db)
        raise
    except sqlite3.Error:
        _rollback(db)
        raise


def _try_complete(db, order_id):
    """双方都确认过就收尾：订单「已完成」、商品「已售」。"""
    row = db.execute(
        "SELECT status, item_id, seller_confirm_at, buyer_confirm_at "
        "FROM orders WHERE id = ?", (order_id,)).fetchone()
    if row is None or row["status"] != DEALING:
        return False
    if row["seller_confirm_at"] is None or row["buyer_confirm_at"] is None:
        return False
    cur = db.execute("UPDATE orders SET status = ? WHERE id = ? AND status = ?",
                     (DONE, order_id, DEALING))
    if cur.rowcount == 0:
        return False
    cur2 = db.execute("UPDATE items SET status = '已售' WHERE id = ? AND status = '已预订'",
                      (row["item_id"],))
    if cur2.rowcount == 0:
        raise DataInconsistent()
    return True


# ---------- 对外操作 ----------

def place_order(db, item_id, buyer_id):
    """买家下单：商品「在售」→「已预订」，生成「待接单」订单，成交价取自数据库。"""
    def action():
        item = db.execute("SELECT price, seller_id, status FROM items WHERE id = ?",
                          (item_id,)).fetchone()
        if item is None:
            raise BizError("商品不存在", 404)
        if item["status"] != "在售":
            raise BizError("商品已被买走或已下架")
        if item["seller_id"] == buyer_id:
            raise BizError("不能购买自己的商品")
        cur = db.execute(
            "UPDATE items SET status = '已预订' WHERE id = ? AND status = '在售'",
            (item_id,))
        if cur.rowcount == 0:
            raise BizError("商品已被买走或已下架")
        cur = db.execute(
            "INSERT INTO orders (item_id, buyer_id, price, status, deal_code) "
            "VALUES (?, ?, ?, ?, ?)",
            (item_id, buyer_id, item["price"], PENDING, new_deal_code()))
        return cur.lastrowid

    try:
        return _run(db, action)
    except sqlite3.IntegrityError as exc:
        # 部分唯一索引兜底：同一商品并发下单，第二个 INSERT 会被数据库拒绝
        raise BizError("这件商品已经被别人抢先下单了") from exc


def accept_order(db, order_id, actor_id):
    """卖家接单：待接单 → 待交付。"""
    def action():
        order = _load(db, order_id)
        if order["seller_id"] != actor_id:
            raise BizError("只有卖家可以接单", 403)
        if order["status"] != PENDING:
            raise BizError("订单状态已变更，无法接单")
        cur = db.execute(
            "UPDATE orders SET status = ?, accepted_at = datetime('now') "
            "WHERE id = ? AND status = ?", (DEALING, order_id, PENDING))
        if cur.rowcount == 0:
            raise BizError("订单状态已变更，无法接单")

    _run(db, action)


def cancel_order(db, order_id, actor_id):
    """任一方取消：待接单/待交付 → 已取消，商品回在售。"""
    def action():
        order = _load(db, order_id)
        is_buyer = order["buyer_id"] == actor_id
        is_seller = order["seller_id"] == actor_id
        if not (is_buyer or is_seller):
            raise BizError("只有订单双方可以操作", 403)
        if order["status"] not in ACTIVE_STATUSES:
            raise BizError("订单状态已变更，无法取消")
        cur = db.execute(
            "UPDATE orders SET status = ?, cancel_by = ? "
            "WHERE id = ? AND status IN (?, ?)",
            (CANCELLED, "买家" if is_buyer else "卖家", order_id, PENDING, DEALING))
        if cur.rowcount == 0:
            raise BizError("订单状态已变更，无法取消")
        cur2 = db.execute(
            "UPDATE items SET status = '在售' WHERE id = ? AND status = '已预订'",
            (order["item_id"],))
        if cur2.rowcount == 0:
            raise DataInconsistent()

    _run(db, action)


def deliver_order(db, order_id, actor_id):
    """卖家确认已当面交付。返回是否因此完成整笔交易。"""
    def action():
        order = _load(db, order_id)
        if order["seller_id"] != actor_id:
            raise BizError("只有卖家可以确认交付", 403)
        if order["status"] != DEALING:
            raise BizError("订单还没进入待交付状态")
        if order["seller_confirm_at"] is not None:
            raise BizError("你已经确认过交付了")
        cur = db.execute(
            "UPDATE orders SET seller_confirm_at = datetime('now') "
            "WHERE id = ? AND status = ? AND seller_confirm_at IS NULL",
            (order_id, DEALING))
        if cur.rowcount == 0:
            raise BizError("你已经确认过交付了")
        return _try_complete(db, order_id)

    return _run(db, action)


def receive_order(db, order_id, actor_id):
    """买家确认收到货。返回是否因此完成整笔交易。"""
    def action():
        order = _load(db, order_id)
        if order["buyer_id"] != actor_id:
            raise BizError("只有买家可以确认收货", 403)
        if order["status"] != DEALING:
            raise BizError("订单还没进入待交付状态")
        if order["buyer_confirm_at"] is not None:
            raise BizError("你已经确认过收货了")
        cur = db.execute(
            "UPDATE orders SET buyer_confirm_at = datetime('now') "
            "WHERE id = ? AND status = ? AND buyer_confirm_at IS NULL",
            (order_id, DEALING))
        if cur.rowcount == 0:
            raise BizError("你已经确认过收货了")
        return _try_complete(db, order_id)

    return _run(db, action)


def expire_stale_orders(db, minutes=None):
    """把超时未接单的订单自动取消，商品回「在售」，返回取消了几笔。

    minutes <= 0 表示关闭（配置里设 0 就不扫）。
    单笔订单一个事务：一笔坏数据不该把整批扫描带崩；扫描失败也不该影响正常请求，
    调用方可以放心忽略返回值。
    """
    minutes = PENDING_TIMEOUT_MINUTES if minutes is None else int(minutes)
    if minutes <= 0:
        return 0

    try:
        rows = db.execute(
            "SELECT id, item_id FROM orders "
            "WHERE status = ? AND accepted_at IS NULL AND created_at <= datetime('now', ?)",
            (PENDING, f"-{minutes} minutes")).fetchall()
    except sqlite3.Error:
        return 0
    if not rows:
        return 0

    expired = 0
    for row in rows:
        try:
            db.execute("BEGIN IMMEDIATE")
            cur = db.execute(
                "UPDATE orders SET status = ?, cancel_by = '系统' "
                "WHERE id = ? AND status = ? AND accepted_at IS NULL",
                (CANCELLED, row["id"], PENDING))
            if cur.rowcount == 0:
                db.execute("COMMIT")   # 期间被接单/取消了，跳过
                continue
            db.execute(
                "UPDATE items SET status = '在售' WHERE id = ? AND status = '已预订'",
                (row["item_id"],))
            db.execute("COMMIT")
            expired += 1
        except sqlite3.Error:
            _rollback(db)
    return expired


# ---------- 展示辅助 ----------
def actions_for(order, user_id):
    """当前用户在这笔订单上能做什么——模板和 API 共用，避免两端口径不一。"""
    is_buyer = order["buyer_id"] == user_id
    is_seller = order["seller_id"] == user_id
    status = order["status"]
    return {
        "is_buyer": is_buyer,
        "is_seller": is_seller,
        "can_accept": bool(is_seller and status == PENDING),
        "can_cancel": bool((is_buyer or is_seller) and status in ACTIVE_STATUSES),
        "can_deliver": bool(is_seller and status == DEALING
                            and order["seller_confirm_at"] is None),
        "can_receive": bool(is_buyer and status == DEALING
                            and order["buyer_confirm_at"] is None),
    }

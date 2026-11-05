"""议价聊天：会话以 (item_id, buyer_id) 为单位，卖家由 items.seller_id 推导。

规则：
- 只有商品买家和卖家能读这个会话，第三方一律拒绝
- 买家可以发文本或报价；卖家可以回文本，也可以「接受报价」——接受后直接改商品价
- 卖家不能主动开新会话（只能回复已有的），避免卖家群发骚扰
- 打开会话就把对方发的消息标记为已读
"""
import sqlite3

from common import format_price, to_local_time
from trading import BizError

MAX_BODY = 500
PAGE_SIZE = 50


def _rollback(db):
    try:
        db.execute("ROLLBACK")
    except sqlite3.Error:
        pass


def _load_item(db, item_id):
    item = db.execute(
        "SELECT id, title, price, status, seller_id FROM items WHERE id = ?",
        (item_id,)).fetchone()
    if item is None:
        raise BizError("商品不存在", 404)
    return item


def ensure_member(db, item_id, buyer_id, user_id):
    """校验 user 是不是这个会话的当事人，返回 (item, role)。"""
    item = _load_item(db, item_id)
    if item["seller_id"] == user_id:
        role = "seller"
        if buyer_id == user_id:
            raise BizError("不能和自己聊天")
    elif buyer_id == user_id:
        role = "buyer"
    else:
        raise BizError("只有这笔交易的双方可以聊天", 403)
    if buyer_id == item["seller_id"]:
        raise BizError("会话对象不能是卖家本人")
    return item, role


def message_json(row, viewer_id):
    return {
        "id": row["id"],
        "kind": row["kind"],
        "body": row["body"],
        "offer_price": row["offer_price"],
        "offer_display": format_price(row["offer_price"]) if row["offer_price"] else None,
        "accepted": row["accepted_at"] is not None,
        "mine": row["sender_id"] == viewer_id,
        "created_at": to_local_time(row["created_at"]),
    }


def send(db, item_id, buyer_id, sender_id, body, kind="text", offer_price=None):
    body = (body or "").strip()
    if kind not in ("text", "offer"):
        raise BizError("消息类型不合法")
    if kind == "offer":
        if not isinstance(offer_price, int) or offer_price <= 0:
            raise BizError("报价必须是大于 0 的金额")
        if not body:
            body = f"我想出价 {format_price(offer_price)} 元"
    if not body:
        raise BizError("消息不能为空")
    if len(body) > MAX_BODY:
        raise BizError(f"消息最多 {MAX_BODY} 字")

    item, _role = ensure_member(db, item_id, buyer_id, sender_id)
    if kind == "offer" and offer_price >= item["price"]:
        # 报价不低于标价就没必要走议价，直接下单更快
        raise BizError("报价不低于当前标价，直接下单即可")

    try:
        db.execute("BEGIN IMMEDIATE")
        # 卖家回复时，会话必须已经存在（不能凭空给陌生买家发消息）
        if db.execute(
                "SELECT 1 FROM messages WHERE item_id = ? AND buyer_id = ? LIMIT 1",
                (item_id, buyer_id)).fetchone() is None and sender_id != buyer_id:
            raise BizError("还没有人咨询这件商品，无法发起会话", 403)
        cur = db.execute(
            "INSERT INTO messages (item_id, buyer_id, sender_id, kind, body, offer_price) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (item_id, buyer_id, sender_id, kind, body,
             offer_price if kind == "offer" else None))
        message_id = cur.lastrowid
        db.execute("COMMIT")
    except BizError:
        _rollback(db)
        raise
    except sqlite3.Error as exc:
        _rollback(db)
        raise BizError("消息发送失败，请重试") from exc
    return message_id


def thread(db, item_id, buyer_id, viewer_id, mark_read=True, before_id=None,
           limit=PAGE_SIZE):
    """返回会话消息，按时间正序。

    取的是**最近** limit 条（不是最早那批）——聊天窗口要能直接看到新消息；
    往上翻历史用 before_id（传当前最早那条的 id）。原来的实现是从最早一条
    取 100 条，对话超过 100 句后新消息再也刷不出来。
    """
    ensure_member(db, item_id, buyer_id, viewer_id)
    sql = "SELECT * FROM messages WHERE item_id = ? AND buyer_id = ?"
    params = [item_id, buyer_id]
    if before_id:
        try:
            before_id = int(before_id)
        except (TypeError, ValueError):
            before_id = None   # 坏参数当没传，别把整页打成 500
        if before_id is not None:
            sql += " AND id < ?"
            params.append(before_id)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(int(limit))
    rows = db.execute(sql, params).fetchall()
    rows.reverse()   # 先按 id 倒序取最近 N 条，再翻回正序给页面用
    if mark_read:
        try:
            db.execute(
                "UPDATE messages SET read_at = datetime('now') "
                "WHERE item_id = ? AND buyer_id = ? AND sender_id != ? AND read_at IS NULL",
                (item_id, buyer_id, viewer_id))
        except sqlite3.Error:
            pass  # 标记已读失败不影响读消息
    return rows


def has_older(db, item_id, buyer_id, first_id):
    """这条消息之前还有没有更早的（决定要不要显示「查看更早」）。"""
    if not first_id:
        return False
    return db.execute(
        "SELECT 1 FROM messages WHERE item_id = ? AND buyer_id = ? AND id < ? LIMIT 1",
        (item_id, buyer_id, first_id)).fetchone() is not None


def new_messages(db, item_id, buyer_id, viewer_id, after_id=0):
    """after_id 之后的新消息，正序。给页面轮询用。"""
    ensure_member(db, item_id, buyer_id, viewer_id)
    try:
        after_id = int(after_id or 0)
    except (TypeError, ValueError):
        after_id = 0
    rows = db.execute(
        "SELECT * FROM messages WHERE item_id = ? AND buyer_id = ? AND id > ? "
        "ORDER BY id LIMIT ?", (item_id, buyer_id, after_id, PAGE_SIZE)).fetchall()
    if rows:
        try:
            db.execute(
                "UPDATE messages SET read_at = datetime('now') "
                "WHERE item_id = ? AND buyer_id = ? AND sender_id != ? AND read_at IS NULL",
                (item_id, buyer_id, viewer_id))
        except sqlite3.Error:
            pass
    return rows


def accept_offer(db, message_id, actor_id):
    """卖家接受报价：改商品价 + 标记报价已接受。"""
    try:
        db.execute("BEGIN IMMEDIATE")
        msg = db.execute(
            "SELECT m.*, i.seller_id, i.status AS item_status FROM messages m "
            "JOIN items i ON m.item_id = i.id WHERE m.id = ?", (message_id,)).fetchone()
        if msg is None:
            raise BizError("消息不存在", 404)
        if msg["seller_id"] != actor_id:
            raise BizError("只有卖家可以接受报价", 403)
        if msg["kind"] != "offer":
            raise BizError("这条消息不是报价")
        if msg["accepted_at"] is not None:
            raise BizError("这个报价已经接受过了")
        if msg["item_status"] != "在售":
            raise BizError("商品已不在售，无法接受报价")
        if msg["offer_price"] >= db.execute(
                "SELECT price FROM items WHERE id = ?", (msg["item_id"],)
        ).fetchone()["price"]:
            raise BizError("报价不低于当前标价，直接等买家下单即可")

        cur = db.execute(
            "UPDATE items SET price = ? WHERE id = ? AND status = '在售'",
            (msg["offer_price"], msg["item_id"]))
        if cur.rowcount == 0:
            raise BizError("商品状态已变更，接受报价失败")
        db.execute("UPDATE messages SET accepted_at = datetime('now') WHERE id = ?",
                   (message_id,))
        db.execute("COMMIT")
    except BizError:
        _rollback(db)
        raise
    except sqlite3.Error as exc:
        _rollback(db)
        raise BizError("接受报价失败，请重试") from exc
    return msg["item_id"], msg["offer_price"]


def conversations(db, user_id):
    """会话列表：每个 (商品, 买家) 一条，按最新消息倒序。"""
    rows = db.execute(
        "SELECT m.item_id, m.buyer_id, MAX(m.id) AS last_id "
        "FROM messages m JOIN items i ON m.item_id = i.id "
        "WHERE i.seller_id = ? OR m.buyer_id = ? "
        "GROUP BY m.item_id, m.buyer_id ORDER BY last_id DESC LIMIT 50",
        (user_id, user_id)).fetchall()

    result = []
    for r in rows:
        last = db.execute("SELECT * FROM messages WHERE id = ?", (r["last_id"],)).fetchone()
        item = db.execute(
            "SELECT i.id, i.title, i.price, i.status, i.seller_id, "
            "s.username AS seller_name, b.username AS buyer_name "
            "FROM items i JOIN users s ON i.seller_id = s.id "
            "JOIN users b ON b.id = ? WHERE i.id = ?",
            (r["buyer_id"], r["item_id"])).fetchone()
        if item is None:
            continue
        unread = db.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE item_id = ? AND buyer_id = ? "
            "AND sender_id != ? AND read_at IS NULL",
            (r["item_id"], r["buyer_id"], user_id)).fetchone()["n"]
        is_seller = item["seller_id"] == user_id
        result.append({
            "item_id": item["id"],
            "item_title": item["title"],
            "item_price": item["price"],
            "item_price_display": format_price(item["price"]),
            "item_status": item["status"],
            "buyer_id": r["buyer_id"],
            "role": "seller" if is_seller else "buyer",
            "peer_name": item["buyer_name"] if is_seller else item["seller_name"],
            "last_message": last["body"] if last else "",
            "last_kind": last["kind"] if last else "text",
            "last_at": to_local_time(last["created_at"]) if last else "",
            "unread": unread,
        })
    return result


def unread_total(db, user_id):
    return db.execute(
        "SELECT COUNT(*) AS n FROM messages m JOIN items i ON m.item_id = i.id "
        "WHERE m.sender_id != ? AND m.read_at IS NULL "
        "AND (i.seller_id = ? OR m.buyer_id = ?)",
        (user_id, user_id, user_id)).fetchone()["n"]

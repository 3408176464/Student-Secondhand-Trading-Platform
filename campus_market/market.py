"""商品市场的查询逻辑（搜索 / 价格区间 / 分类 / 排序 / 分页）。

Web 首页和 /api/v1/items 共用，保证两个客户端的筛选结果完全一致——
筛选条件写两份，迟早会出现"网页能搜到、小程序搜不到"。
"""
from common import escape_like, parse_price_filter

# 排序白名单：只接受这几个值，拼进 SQL 的永远是这里的常量，不是用户输入
SORT_ORDERS = {
    "new": "i.created_at DESC, i.id DESC",
    "price_asc": "i.price ASC, i.created_at DESC, i.id DESC",
    "price_desc": "i.price DESC, i.created_at DESC, i.id DESC",
}


def _build_filters(db, q="", min_price="", max_price="", category_id=""):
    """把筛选参数翻译成 WHERE 片段。

    返回 (where_sql, params, error)。where_sql 以 " AND ..." 开头，可直接拼在
    "WHERE i.status = '在售'" 之后；error 非空时只保留关键词条件——与 v1 行为一致，
    坏参数不至于把整页打成空。列表查询和计数查询共用它，两边的条数才不会打架。
    """
    q = (q or "").strip()
    min_price = (min_price or "").strip()
    max_price = (max_price or "").strip()
    category_id = str(category_id or "").strip()

    where = ""
    params = []
    if q:
        where += " AND i.title LIKE ? ESCAPE '\\'"
        params.append(f"%{escape_like(q)}%")

    error = None
    min_cents = max_cents = None
    try:
        if min_price:
            min_cents = parse_price_filter(min_price)
        if max_price:
            max_cents = parse_price_filter(max_price)
        if min_cents is not None and max_cents is not None and min_cents > max_cents:
            error = "最低价不能大于最高价"
    except ValueError:
        error = "价格区间必须为合法数字"

    if error is not None:
        return where, params, error

    if min_cents is not None:
        where += " AND i.price >= ?"
        params.append(min_cents)
    if max_cents is not None:
        where += " AND i.price <= ?"
        params.append(max_cents)
    if category_id:
        try:
            exists = db.execute("SELECT id FROM categories WHERE id = ?",
                                (int(category_id),)).fetchone()
        except (ValueError, OverflowError):
            exists = None  # Unicode 数字、超长整数等一律按"分类不存在"忽略
        if exists is not None:
            where += " AND i.category_id = ?"
            params.append(exists["id"])
    return where, params, None


def search_items(db, q="", min_price="", max_price="", category_id="", sort="",
                 limit=None, offset=0):
    """返回 (rows, error)。

    limit 为 None 表示不分页（接口层保持原行为，一次取全）；网页端按页取。
    error 非空表示筛选参数不合法，调用方负责提示。
    """
    where, params, error = _build_filters(db, q, min_price, max_price, category_id)

    sql = ("SELECT i.*, u.username AS seller_name, c.name AS category_name "
           "FROM items i JOIN users u ON i.seller_id = u.id "
           "JOIN categories c ON i.category_id = c.id "
           "WHERE i.status = '在售'")
    sql += where
    sql += " ORDER BY " + SORT_ORDERS.get((sort or "").strip(), SORT_ORDERS["new"])

    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params = params + [int(limit), max(0, int(offset or 0))]

    return db.execute(sql, params).fetchall(), error


def count_items(db, q="", min_price="", max_price="", category_id=""):
    """返回 (total, error)。条件与 search_items 完全同源，不需要 JOIN 用户和分类。"""
    where, params, error = _build_filters(db, q, min_price, max_price, category_id)
    row = db.execute("SELECT COUNT(*) AS n FROM items i "
                     "WHERE i.status = '在售'" + where, params).fetchone()
    return (row["n"] if row else 0), error

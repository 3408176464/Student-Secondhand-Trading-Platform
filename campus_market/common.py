"""Web 和 API 共用的格式化与校验工具。

这些函数原本住在 app.py 里，小程序接口也要用；抽出来避免两边各写一套
价格解析和时区转换，一处改、两处生效。
"""
import re
from datetime import datetime, timedelta
from decimal import Decimal

PRICE_RE = re.compile(r"^\d+(\.\d{1,2})?$")
MAX_PRICE_CENTS = 10 ** 9  # 价格上限 10^9 分 = 1000 万元，防超长数字绑定 SQLite 溢出
CONDITIONS = ("全新", "九成新", "八成新", "有使用痕迹")
ITEM_STATUSES = ("在售", "已预订", "已售", "下架")


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
    value = (value or "").strip()
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
    value = (value or "").strip()
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

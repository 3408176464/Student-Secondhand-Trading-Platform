"""商品图片：校验、落盘、按商品读取。

存储方案：本地磁盘 `uploads/items/<item_id>_<随机串>.jpg`，数据库只存文件名。
作业规模这样最省事；真要上线应换对象存储（OSS/COS），把 `save_image` 换成上传即可，
其余代码不用动。

文件名完全由服务端生成，不含任何用户输入，从根上避免路径穿越。
"""
import random
import sqlite3
import string
from pathlib import Path

from trading import BizError

MAX_BYTES = 5 * 1024 * 1024      # 单张 5MB
MAX_IMAGES = 5                   # 每件商品最多 5 张
SUBDIR = "items"

_ALPHABET = string.ascii_lowercase + string.digits


def sniff_extension(data):
    """按文件头判断真实类型，不信客户端给的扩展名。"""
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def _random_name():
    return "".join(random.choice(_ALPHABET) for _ in range(16))


def image_dir(upload_root):
    path = Path(upload_root) / SUBDIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def urls_for(db, item_id):
    """按 position 排序返回图片的相对 URL 列表。"""
    rows = db.execute(
        "SELECT filename FROM item_images WHERE item_id = ? ORDER BY position, id",
        (item_id,)).fetchall()
    return [f"/uploads/{SUBDIR}/{r['filename']}" for r in rows]


def first_urls(db, item_ids):
    """列表页批量取首图，避免 N+1 查询。"""
    if not item_ids:
        return {}
    marks = ",".join("?" * len(item_ids))
    rows = db.execute(
        f"SELECT item_id, filename FROM item_images WHERE item_id IN ({marks}) "
        "ORDER BY item_id, position, id", list(item_ids)).fetchall()
    covers = {}
    for r in rows:
        covers.setdefault(r["item_id"], f"/uploads/{SUBDIR}/{r['filename']}")
    return covers


def add_image(db, item_id, actor_id, data, upload_root):
    """给商品追加一张图片，返回相对 URL。"""
    if not data:
        raise BizError("没有收到图片内容")
    if len(data) > MAX_BYTES:
        raise BizError("单张图片不能超过 5MB")
    ext = sniff_extension(data)
    if ext is None:
        raise BizError("只支持 JPG / PNG / WebP 格式的图片")

    folder = image_dir(upload_root)
    filename = None
    try:
        db.execute("BEGIN IMMEDIATE")
        item = db.execute("SELECT seller_id FROM items WHERE id = ?",
                          (item_id,)).fetchone()
        if item is None:
            raise BizError("商品不存在", 404)
        if item["seller_id"] != actor_id:
            raise BizError("只有商品发布者可以上传图片", 403)
        count = db.execute("SELECT COUNT(*) AS n FROM item_images WHERE item_id = ?",
                           (item_id,)).fetchone()["n"]
        if count >= MAX_IMAGES:
            raise BizError(f"每件商品最多 {MAX_IMAGES} 张图片")

        filename = f"{item_id}_{_random_name()}{ext}"
        (folder / filename).write_bytes(data)
        db.execute(
            "INSERT INTO item_images (item_id, filename, position) VALUES (?, ?, ?)",
            (item_id, filename, count))
        db.execute("COMMIT")
    except BizError:
        _rollback(db)
        _unlink(folder, filename)
        raise
    except sqlite3.Error as exc:
        _rollback(db)
        _unlink(folder, filename)
        raise BizError("图片保存失败，请重试") from exc
    return f"/uploads/{SUBDIR}/{filename}"


def remove_image(db, item_id, actor_id, image_id, upload_root):
    """删掉商品的一张图，并把剩下的 position 重排成 0、1、2…

    position 有 0~4 的 CHECK 约束，删中间那张如果留空洞，后面再加图会撞号。
    """
    folder = image_dir(upload_root)
    filename = None
    try:
        db.execute("BEGIN IMMEDIATE")
        item = db.execute("SELECT seller_id FROM items WHERE id = ?",
                          (item_id,)).fetchone()
        if item is None:
            raise BizError("商品不存在", 404)
        if item["seller_id"] != actor_id:
            raise BizError("只有商品发布者可以删除图片", 403)
        row = db.execute("SELECT filename FROM item_images WHERE id = ? AND item_id = ?",
                         (image_id, item_id)).fetchone()
        if row is None:
            raise BizError("图片不存在", 404)
        filename = row["filename"]

        db.execute("DELETE FROM item_images WHERE id = ? AND item_id = ?",
                   (image_id, item_id))
        rest = db.execute(
            "SELECT id FROM item_images WHERE item_id = ? ORDER BY position, id",
            (item_id,)).fetchall()
        for pos, r in enumerate(rest):
            db.execute("UPDATE item_images SET position = ? WHERE id = ?", (pos, r["id"]))
        db.execute("COMMIT")
    except BizError:
        _rollback(db)
        raise
    except sqlite3.Error as exc:
        _rollback(db)
        raise BizError("删除图片失败，请重试") from exc
    # 提交成功后才删文件：万一删文件失败，只是留个孤儿，manage.py check 能查出来
    _unlink(folder, filename)


def _unlink(folder, filename):
    if not filename:
        return
    try:
        (Path(folder) / filename).unlink(missing_ok=True)
    except OSError:
        pass


def _rollback(db):
    try:
        db.execute("ROLLBACK")
    except sqlite3.Error:
        pass

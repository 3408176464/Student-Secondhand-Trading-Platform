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

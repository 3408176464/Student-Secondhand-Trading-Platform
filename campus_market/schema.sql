-- 校园二手交易平台表结构（外键约束在每个连接上用 PRAGMA foreign_keys=ON 开启）
-- v2：订单改为双向确认流程，用户增加微信 openid
CREATE TABLE categories (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE users (
    id         INTEGER PRIMARY KEY,
    username   TEXT NOT NULL UNIQUE,
    password   TEXT NOT NULL,
    contact    TEXT,
    openid     TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 微信登录用户允许 password 为空：openid 就是身份凭证，没有可校验的密码
CREATE UNIQUE INDEX idx_users_openid ON users(openid);

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

-- 订单状态机：待接单 → 待交付 → 已完成，待接单/待交付 均可取消
--   accepted_at        卖家接单时间（待交付 起非空）
--   deal_code          当面交易核对码，双方线下见面核对，证明面对面交付
--   seller_confirm_at  卖家点「我已当面交付」的时间
--   buyer_confirm_at   买家点「我已收到货」的时间
--   cancel_by          取消发起方，系统迁移的历史数据记为「系统」
CREATE TABLE orders (
    id                INTEGER PRIMARY KEY,
    item_id           INTEGER NOT NULL REFERENCES items(id) ON DELETE RESTRICT,
    buyer_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    price             INTEGER NOT NULL CHECK (price > 0),
    status            TEXT NOT NULL DEFAULT '待接单'
                      CHECK (status IN ('待接单','待交付','已完成','已取消')),
    deal_code         TEXT NOT NULL
                      CHECK (deal_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'),
    accepted_at       TEXT,
    seller_confirm_at TEXT,
    buyer_confirm_at  TEXT,
    cancel_by         TEXT CHECK (cancel_by IN ('买家','卖家','系统')),
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),

    -- 接单时间与状态一致：待交付/已完成 必有；待接单 必无；
    -- 已取消 允许有（接过单又被取消，是真实历史，不能抹掉）
    CHECK ((status = '待接单' AND accepted_at IS NULL)
        OR (status IN ('待交付','已完成') AND accepted_at IS NOT NULL)
        OR status = '已取消'),
    -- 已完成 必须双方都确认过，杜绝单方推到底
    CHECK (status <> '已完成'
        OR (seller_confirm_at IS NOT NULL AND buyer_confirm_at IS NOT NULL)),
    -- 取消方只在已取消订单上出现
    CHECK ((status = '已取消' AND cancel_by IS NOT NULL)
        OR (status <> '已取消' AND cancel_by IS NULL))
);

CREATE INDEX idx_items_market ON items(status, category_id, price);
CREATE INDEX idx_orders_buyer ON orders(buyer_id);
CREATE INDEX idx_orders_item ON orders(item_id);

-- 部分唯一索引：同一商品至多一条「进行中」订单（待接单或待交付），数据库层兜底
CREATE UNIQUE INDEX idx_orders_active_item
ON orders(item_id) WHERE status IN ('待接单','待交付');

-- 商品图片：一件商品最多 5 张，position 决定轮播顺序（0 为首图）
CREATE TABLE item_images (
    id         INTEGER PRIMARY KEY,
    item_id    INTEGER NOT NULL REFERENCES items(id) ON DELETE RESTRICT,
    filename   TEXT NOT NULL UNIQUE,   -- 存盘文件名，随机生成，不含用户输入
    position   INTEGER NOT NULL DEFAULT 0 CHECK (position BETWEEN 0 AND 4),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_item_images_item ON item_images(item_id, position);

-- 议价聊天：一个会话 = (item_id, buyer_id)，卖家由 items.seller_id 推导
--   kind='offer' 时 offer_price 必填（单位：分），卖家可接受报价并同步改商品价
CREATE TABLE messages (
    id          INTEGER PRIMARY KEY,
    item_id     INTEGER NOT NULL REFERENCES items(id) ON DELETE RESTRICT,
    buyer_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    sender_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    kind        TEXT NOT NULL DEFAULT 'text' CHECK (kind IN ('text','offer')),
    body        TEXT NOT NULL CHECK (length(trim(body)) > 0 AND length(body) <= 500),
    offer_price INTEGER CHECK (offer_price IS NULL OR offer_price > 0),
    accepted_at TEXT,
    read_at     TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),

    -- 报价必须带金额，普通消息不能带
    CHECK ((kind = 'offer' AND offer_price IS NOT NULL)
        OR (kind = 'text' AND offer_price IS NULL)),
    -- 只有报价能被「接受」
    CHECK (accepted_at IS NULL OR kind = 'offer')
);

CREATE INDEX idx_messages_thread ON messages(item_id, buyer_id, id);
CREATE INDEX idx_messages_buyer ON messages(buyer_id);

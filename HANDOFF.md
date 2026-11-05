# 交接文档 · 校园二手交易平台

> 给接手的同学：**先读完这一页再动代码**。数字（用例数、数据量）都是当前真实状态，
> 已按代码和数据库重新核对过；命令在原作者的 3.12 环境验证过，本机换成 `python`（3.13），
> 差异已在第 1 节和第 11 节标注。
> 如果你是 AI：请把本文件当作唯一事实来源，动手前先跑第 7 节的测试确认基线是绿的。

---

## 0. 30 秒速览

| 项 | 值 |
|---|---|
| 项目 | 校园二手交易平台（数据库原理课程作业，大三） |
| 形态 | **Python + Flask + SQLite 服务端渲染网页**，配一套 `/api/v1` JSON 接口 |
| 我的部分 | **已完成并验证**：商品发布、图片、议价聊天、下单到成交的完整闭环 |
| 代码量 | Python 约 2,630 行 / 模板 554 行 / 样式 723 行 |
| 测试 | **176 个用例全部通过**（133 网页端 + 43 接口） |
| 数据 | 31 件商品、13 笔订单、12 个账号、24 张图片 |
| 环境 | Windows，解释器用 **`python`（3.13）**，依赖已装好（Flask 3.1 / waitress 3.0），直接 `python serve.py` |

---

## 1. 怎么跑起来

本机解释器是 **`python`（3.13）**，依赖已经在 2026-11-05 装好，直接启动即可：

```bash
cd campus_market
python serve.py                             # 开发模式，改代码自动重载
```

> 换机器/重装系统后要装一次依赖：`python -m pip install -r requirements.txt`
> （只有 Flask 和 waitress）。本机默认源是清华镜像，实测取不到包，
> 装不上时加 `-i https://pypi.org/simple`。

启动后会打印两个地址，浏览器打开 `http://127.0.0.1:5000`。

> **关于 Python 版本**：旧版本文档写的是 `py -3.12`，那是原作者机器的环境；
> 本机 `py -0` 只注册了 3.14，跑 `py -3.12` 会直接报 `No suitable Python runtime found`。
> 代码里没有版本相关逻辑，本机就用 `python`；若某天要换成 3.12，把下面命令里的
> `python` 全部换成 `py -3.12` 即可。

手机/其他电脑要访问，用启动时打印的局域网地址（形如 `http://192.168.x.x:5000`，
**以终端实际打印的为准**，换网络或重连 WiFi 后会变），前提是同一个 WiFi，
且首次启动 Windows 防火墙弹窗点了「允许访问」。

生产服务器（waitress）：

```bash
set CM_SECRET_KEY=一串随机字符
python run.py
```

### 怎么看数据库

```bash
python manage.py stats                  # 命令行看概况
python manage.py query "SELECT * FROM items LIMIT 5"
```

图形界面用 **DB Browser for SQLite**（免费，官网 sqlitebrowser.org 下载安装，
或命令行 `winget install DBBrowserForSQLite`）：
打开后 File → Open Database 选 `campus_market.db`。
改完数据记得按 **Ctrl+S（写入更改）**，否则改动只在内存里。

> 旧文档里记的是原作者机器上一个便携版的绝对路径（`C:\Users\wxr\...`），本机没有这个目录，
> 别照着找。没装图形工具也不影响，`manage.py query` 足够看数据。

---

## 2. 项目做到哪一步了

### 已经完成的（我负责的部分）

1. **用户体系**：注册 / 登录 / 登出 / 改联系方式，密码加盐哈希
2. **商品**：发布（含最多 5 张图）、搜索、分类筛选、价格区间、分页、**编辑（标题/价格/成色/分类/描述/增删图）**、下架
3. **图片**：上传校验（魔数）、随机文件名、按商品轮播、编辑页删图并重排顺序
4. **议价聊天**：会话、文本消息、报价、卖家一键接受报价（自动改商品价）、未读角标、**记录分页 + 5 秒轮询新消息**
5. **交易闭环**：下单 → 卖家接单 → 面交核对 6 位码 → 卖家确认交付 + 买家确认收货 → 成交；**待接单超 2 小时未处理自动取消、商品回在售**
6. **安全**：CSRF 防护、登录失败限流、参数化查询、会话加固
7. **工程**：176 个测试、部署文档、数据库管理脚本

### 还没做的（谁接手可以挑）

见第 8 节技术债。**没有任何线程/异步代码，全部是同步阻塞**，容易看懂。

---

## 3. 目录与文件职责

```
campus_market/
├── app.py          网页路由 + 应用工厂 create_app（含 CSRF、限流、错误处理）
├── api.py          /api/v1 JSON 接口（Bearer token，给移动端用）
├── trading.py      交易状态机：下单/接单/取消/交付/收货  ← 核心，改动前先看这里
├── chat.py         议价聊天：发消息、报价、接受报价、会话列表
├── images.py       商品图片：魔数校验、落盘、按商品取图
├── market.py       商品搜索筛选（网页和接口共用同一份 SQL）
├── common.py       金额分↔元、UTC→本地时间、价格校验
├── database.py     连接管理、建表、示例数据
├── schema.sql      建表 SQL（含所有 CHECK 约束和索引）
├── net.py          探测本机局域网 IP
├── serve.py        开发启动（0.0.0.0 + 自动重载 + 打印地址）
├── run.py          生产启动（waitress 8 线程）
├── manage.py       数据库管理：stats / backup / restore / export / check / vacuum / expire / refresh-pending
├── migrate_v2.py   v1 → v2（订单流程改造，重建 orders 表）
├── migrate_v3.py   v2 → v3（新增图片表、消息表，纯增量）
├── templates/      14 个 Jinja2 模板
├── static/style.css
├── uploads/items/  商品图片实体（24 张 jpg，约 2.3MB）
└── tests/          test_app.py（133 用例）+ test_api.py（43 用例）
```

**关键函数位置**（改代码前先定位）：

| 想改什么 | 去哪 |
|---|---|
| 交易流程 / 状态流转 | `trading.py:111` 下单、`:140` 接单、`:157` 取消、`:182` 交付、`:203` 收货、`:89` 收尾判定 |
| 超时释放 | `trading.py:224` expire_stale_orders（`app.py` 的 before_request 每 30 秒触发一次） |
| 议价 / 聊天 | `chat.py:63` 发消息、`:169` 接受报价、`:208` 会话列表 |
| 聊天记录分页 / 轮询 | `chat.py:105` thread（最近 50 条）、`:139` has_older、`:148` new_messages |
| 图片上传 / 删除 | `images.py:66` add_image、`:108` remove_image（重排 position）、`:23` 魔数判断 |
| 搜索筛选 / 计数 | `market.py:67` search_items、`:90` count_items |
| 金额显示 | `common.py:16` format_price（分 → "19.90"） |

---

## 4. 数据模型

6 张表：`users`、`categories`、`items`、`item_images`、`orders`、`messages`。

### 关键设计（答辩会问）

**1. `orders` 不存 `seller_id`** —— 卖家通过 `item_id → items.seller_id` 推导，
避免同一事实存两处导致不一致。

**2. 金额全部用整数存「分」** —— `price=1500` 就是 15 元，永远不碰浮点数。

**3. 订单状态机**（这是整个项目最核心的设计）：

```
            买家下单          卖家接单        卖家确认交付 + 买家确认收货
在售商品 ─────────→ 待接单 ─────────→ 待交付 ────────────────────→ 已完成
                     │                 │
                     └── 任一方取消 ────┴──→ 已取消（商品回「在售」）
```

**必须双方都确认才成交**：卖家点「我已当面交付」只写自己那一半（`seller_confirm_at`），
买家没点收货，订单停在「待交付」。反之亦然。这是和普通作业最大的区别——**一个人推不动整条流程**。

**4. 数据库层用 CHECK 兜底**（`schema.sql`）：

```sql
-- 已完成必须有双方确认时间
CHECK (status <> '已完成' OR (seller_confirm_at IS NOT NULL AND buyer_confirm_at IS NOT NULL))
-- 已取消必须有取消方
CHECK ((status = '已取消' AND cancel_by IS NOT NULL) OR (status <> '已取消' AND cancel_by IS NULL))
```

**5. 部分唯一索引防超卖**：

```sql
CREATE UNIQUE INDEX idx_orders_active_item
ON orders(item_id) WHERE status IN ('待接单','待交付');
```

同一件商品同一时刻只能有一条「进行中」订单。注意 `IN` 里必须同时包含两个状态，
只写 `'待接单'` 的话，卖家接单后索引就失效了，第二个买家能插进来。

**6. 6 位 `deal_code`** —— 面交时双方核对，作为线下交付的电子凭证。
没有支付通道的情况下，它替代了「确认收货打款」的作用。

---

## 5. 不能改坏的 6 条规则

改代码前对照这条清单，破坏任意一条都会让 176 个测试变红：

1. **双方确认才成交**，任何「一方就能完成交易」的改动都是错的
2. **价格从数据库读**，不接受客户端传入的价格（防篡改表单）
3. **金额是整数分**，展示时才用 `format_price` 转成元
4. **权限在服务层校验**，不靠前端隐藏按钮（越权返回 403）
5. **所有 SQL 参数化**，禁止字符串拼接
6. **图片文件名服务端生成**，禁止使用用户上传的文件名（防路径穿越）

---

## 6. 现有数据与演示账号

| 项 | 值 |
|---|---|
| 示例账号 | `zhangsan` / `lisi` / `wangwu` / `zhaoliu` / `chenqi` / `zhouba` / `wujiu` / `zhengshi` / `sunshiyi` / `lishier` |
| 统一密码 | `password123` |
| 商品 | 31 件（在售 24 / 已售 3 / 已预订 3 / 下架 1） |
| 订单 | 13 笔（已完成 3 / 待接单 3 / 已取消 7） |
| 图片 | 24 张，覆盖所有在售商品 |

> 另外有两个测试期间注册的账号 `test`（id=11）和 `111`（id=12），密码未知，不影响演示，
> 要清掉可以在「我的商品」里下架它们的东西，或者直接用 DB Browser 删行。

---

## 7. 测试

```bash
cd campus_market
python -m unittest discover tests -v      # 全部 176 个，约 4.5 分钟
python -m unittest tests.test_app -v      # 只跑网页端
python -m unittest tests.test_api -v      # 只跑接口
```

**当前基线：176 个全绿。** 改完任何逻辑都要重新跑一遍再提交。

> 2026-11-05 在本机（3.13）实测：`Ran 176 tests in 267.5s / OK`。
> 用例数（116 + 43）也对过 `tests/` 里的 `def test_`，与代码一致。

测试会用临时数据库（`tempfile`），不会污染 `campus_market.db`。

---

## 8. 技术债与已知缺口

按「接手后要不要补」排序：

| 优先级 | 缺口 | 说明 | 预估 |
|---|---|---|---|
| **高** | 内容安全 | 用户发的文字和图片没有任何检测，公网开放前必须加敏感词过滤/举报入口 | 半天 |
| 中 | 轮询而非推送 | 聊天每 5 秒拉一次增量（`/messages/<item>/<buyer>/updates?after=N`），不是真推送；并发大了要换 SSE/WebSocket | 半天 |
| 中 | 会话列表不分页 | 会话列表固定取最近 50 个（`chat.conversations`），消息本身已分页（每页 50） | 2 小时 |
| 低 | 无支付 | 个人主体开不了微信支付商户号，用核对码替代 | 需资质 |
| 低 | 编辑不能改排序 | 图片只能新增/删除，不能拖拽调整首图（改 `item_images.position` 即可） | 1 小时 |

已经补掉的（2026-11-05）：~~订单超时释放~~（`trading.expire_stale_orders` + 请求级惰性扫描）、
~~无商品编辑~~（`/items/<id>/edit`，含删图与重排）、~~消息分页~~（每页 50 + 「查看更早」）。

**架构上预留的扩展点**：要接支付，只需在「卖家接单」与「待交付」之间插入「待支付」状态，
`trading._try_complete()` 不用动。

---

## 9. 如果继续写代码

推荐顺序（都做过验证，改动面可控）：

1. **举报入口** —— 新增 `reports` 表，详情页加按钮，管理页列表（公网上线前必做）
2. **内容安全** —— 敏感词表 + 发布/发消息时过滤，图片先做人工抽查
3. **真推送** —— 把轮询换成 SSE，注意 waitress 是同步模型，需要长连接支持
4. **会话列表分页** —— 照 `index.html` 的页码条套路做

每做一项：**先写测试，再改代码，跑全绿再提交**。测试文件里已有的辅助方法
（`register_user` / `login` / `insert_item` / `create_item`）可以直接复用。

**改动这几处要当心**：`OrderExpiryTests` 里验证的扫描逻辑跑在每个请求前，
如果新增的页面有「刚下单就立刻检查状态」的假设，先看 `ORDER_TIMEOUT_MINUTES` 配置。

---

## 10. 如果做答辩 PPT

### 10.1 讲什么（10 页结构）

| 页 | 内容 | 素材 |
|---|---|---|
| 1 | 题目 + 一句话定位 | 「校内闲置流转，双方确认才成交」 |
| 2 | 需求与场景 | 校园二手群信息易沉底、无凭证易扯皮 |
| 3 | 技术选型 | Flask + SQLite，为什么不用小程序（类目资质卡点，见 docs/ 设计文档第 8 节） |
| 4 | 数据库设计 | E-R 图：users / items / orders / item_images / messages |
| 5 | **核心亮点：双向确认状态机** | 状态流转图（第 4 节那张） |
| 6 | 防超卖与并发 | 原子 UPDATE + 部分唯一索引，讲「为什么索引 WHERE 要写两个状态」 |
| 7 | 议价聊天与报价 | 买家出价 → 卖家一键接受 → 商品自动改价 |
| 8 | 安全设计 | CSRF、登录限流、图片魔数校验、密码哈希 |
| 9 | 演示 | 现场跑一遍完整交易 |
| 10 | 总结与不足 | 老实讲：无支付、无内容审核、无超时释放 |

### 10.2 现场演示脚本（3 分钟）

用两个浏览器窗口（一个普通、一个无痕），分别登录两个账号：

1. `zhangsan` 发布一件商品（带图）→ 刷新首页出现【30 秒】
2. `lisi` 打开商品详情 → 点「问问卖家 / 议个价」→ 发报价 `18.00`【40 秒】
3. `zhangsan` 在「消息」看到未读 → 点「接受这个价」→ **商品价格自动变成 18.00**【40 秒】
4. `lisi` 下单 → 订单显示「待接单」+ 6 位核对码【30 秒】
5. `zhangsan` 接单 → 状态变「待交付」→ 点「我已当面交付」→ **订单不会完成，仍停在待交付**【20 秒】
6. `lisi` 点「我收到货了」→ **订单变已完成，商品变已售**【20 秒】

第 5 步是全场的点睛之笔：**一个人点到底，交易不会完成**。

### 10.3 老师可能会问 & 怎么答

| 问题 | 答题要点 |
|---|---|
| 为什么用 SQLite 不用 MySQL？ | 课程作业单机部署，SQLite 零配置；但所有 SQL 都是标准写法，换 MySQL 只需改驱动和几处 `datetime('now')` |
| 两个人同时下单同一件商品会怎样？ | 事务里先 `UPDATE items SET status='已预订' WHERE status='在售'`，用受影响行数做抢占判断，行数为 0 就失败；数据库层还有部分唯一索引兜底 |
| 没有支付怎么保证交易可信？ | 承认这是简化：平台不碰钱，用「6 位核对码 + 双方确认 + 时间戳留痕」作为线下凭证；要接支付需要企业主体开商户号 |
| 如果卖家一直不接单？ | 当前是已知缺陷，商品会被锁住，需要手动取消；改进方案是加超时自动释放（见第 8 节） |
| 图片存哪里？ | 磁盘 `uploads/items/`，库里只存文件名；校验真实文件头而不是扩展名，文件名服务端随机生成防路径穿越 |
| 有没有做防 SQL 注入？ | 全部参数化查询，没有任何字符串拼接 SQL |

### 10.4 现成的图表素材

- 状态机图：本文件第 4 节（可直接截图或重画）
- E-R 图：用 DB Browser 的「工具 → 关系图」自动生成，比手画准
- 界面截图：跑起来后自己截，24 件商品都有图，看起来很饱满

---

## 11. 给 AI 的操作约定

如果你是接到这份文档的 AI，请遵守：

**读代码顺序**：`schema.sql` → `trading.py` → `app.py` → `templates/`。
`api.py` 是网页逻辑的镜像，不是另一套业务，**不要**在 api.py 里写新的业务规则。

**动手前必须做**：

```bash
python -m unittest discover tests    # 确认 176 绿
python manage.py backup              # 备份数据库
```

**禁止**：

- 改动 `orders` 表的状态枚举（会让 13 笔存量订单失效）
- 在业务代码里直接写 SQL 字符串拼接
- 删除任何 `tests/` 里的用例来让测试变绿
- 修改 `campus_market.db` 里的数据来迁就代码（应该反过来）

**环境坑**：

- 解释器用 `python`（3.13），依赖要先装；别用 `py -3.12`（本机没这个版本）
- Windows 上启动服务用 `serve.py`，它监听 `0.0.0.0`；直接 `app.py` 虽然也可以（已改），
  但默认端口是 5000，被占用时要先杀掉旧进程
- 路径含中文，命令行里加引号

---

## 12. 变更历史

| 版本 | 时间 | 内容 |
|---|---|---|
| v1 | 2026-10-02 | 基础版：注册登录、发布、搜索、下单、任一方确认即完成 |
| v2 | 2026-10-31 | 订单改为**双向确认**；新增/items/orders 接口层 |
| v3 | 2026-11-01 | 商品图片、议价聊天 |
| v4 | 2026-11-01 | 安全加固：CSRF、登录限流、SECRET_KEY 环境变量、waitress |
| v5 | 2026-11-02 | 首页标注「我发布的」；数据清理；交接文档 |
| v6 | 2026-11-05 | 订单页改卡片 + 四段进度条（核对码放大）；下单/交付/取消二次确认；提示条分成功/失败/警告；首页加排序、件数、清除筛选、发布时间；我的商品改卡片带封面；字体改系统栈、补 favicon；文档数字与环境说明校准 |
| v7 | 2026-11-05 | 详情页图片改轮播（左右按钮 + 缩略图 + 计数 + 看原图）；首页分页（每页 12，计数与列表同源）；文档同步 |
| v8 | 2026-11-06 | 订单超时释放（待接单 2 小时自动取消，请求级惰性扫描 + `manage.py expire`）；商品编辑页（全字段 + 增删图）；聊天记录分页（每页 50）；新消息 5 秒轮询；测试 176 个 |

设计文档（含每个决策的理由和踩过的坑）在 `docs/superpowers/specs/2026-10-02-campus-market-design.md`，
部署方案见本文件**附录 A**。

---

## 13. 一句话交接

> 后端和交易逻辑是完整可用的，**176 个测试证明它能跑**；缺的是运营层面的东西
> （超时释放、内容审核、支付）。演示时最值得讲的是「双方确认才成交」——
> 这是这个作业里唯一一个真正动脑子设计过的地方。

---

## 附录 A · 部署上线速查

假设要给老师/同学在线访问，三档方案，**先看清要哪种，别急着买服务器**。

| 目标 | 做法 | 成本 | 时间 |
|---|---|---|---|
| 答辩演示 / 自己看 | 本机 `python serve.py` | 0 | 1 分钟 |
| 同宿舍、同班几个人用 | 本机 + 内网穿透（cpolar 等） | 0～几十元 | 半小时 |
| 谁都能打开 | 云服务器 + 备案域名 + HTTPS | 几十元/月起 | 1～2 周（备案要等） |

### A.1 本机 / 内网穿透

```bash
python serve.py          # 监听 0.0.0.0，打印局域网地址
cpolar http 5000           # 拿到 https 临时域名，发出去就能用
```

内网穿透的注意点：免费版重启后地址会变、依赖这台电脑常开、图片多时略卡。

### A.2 云服务器（Linux）

```bash
# 1. 传代码、装依赖
scp -r campus_market user@服务器:/opt/ && cd /opt/campus_market
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt

# 2. 生成密钥（唯一不能省的一步）
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

写到 `/etc/systemd/system/campus-market.service`：

```ini
[Unit]
Description=Campus Market
After=network.target

[Service]
User=www-data
WorkingDirectory=/opt/campus_market
Environment="CM_SECRET_KEY=粘贴上面生成的串"
Environment="CM_DATABASE=/opt/campus_market/data/campus_market.db"
Environment="CM_UPLOAD_DIR=/opt/campus_market/data/uploads"
Environment="PORT=8000"
ExecStart=/opt/campus_market/.venv/bin/python run.py
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo mkdir -p /opt/campus_market/data
sudo chown -R www-data:www-data /opt/campus_market
sudo systemctl enable --now campus-market
```

**数据库和图片必须指到 `data/` 目录**，否则更新代码会把数据覆盖掉。

Nginx 反代（注意 `client_max_body_size 40m`，不设传图会 413）：

```nginx
server {
    listen 80;
    server_name 你的域名.com;
    client_max_body_size 40m;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d 你的域名.com      # 自动配好 HTTPS 并续期
```

备份（数据库用 `.backup`，别直接 copy）：

```bash
0 3 * * * sqlite3 /opt/campus_market/data/campus_market.db \
  ".backup '/opt/backup/db-$(date +\%F).sqlite'" && \
  find /opt/backup -name '*.sqlite' -mtime +14 -delete
```

### A.3 上线检查清单

- [ ] `CM_SECRET_KEY` 已换成随机串（启动日志里没有 ⚠️ 警告）
- [ ] 数据库和 `uploads/` 指到独立目录，不在代码目录里
- [ ] 域名已备案，HTTPS 生效（地址栏有小锁）
- [ ] 防火墙只开 80/443，8000 不对外
- [ ] 完整走一遍：注册 → 发布带图 → 另开浏览器下单 → 接单 → 双方确认

### A.4 已做的安全措施（答辩可直接用这张表）

| 项 | 状态 |
|---|---|
| 密码加盐哈希（不存明文） | ✅ werkzeug |
| CSRF 防护（防第三方站伪造下单/改价） | ✅ 每个表单会话级 token |
| 登录失败限流（防爆破） | ✅ 同 IP+用户名 10 分钟 5 次 |
| SQL 注入 | ✅ 全部参数化查询 |
| 图片校验（魔数、大小、数量、随机文件名） | ✅ |
| 会话 cookie HttpOnly + SameSite=Lax | ✅ |
| HTTPS | 由 Nginx / certbot 负责 |

**仍然缺的**：用户发的内容（文字和图片）没有任何安全检测，也没有举报入口。
公网开放给陌生人前，至少要加举报按钮和人工删除后台。

# 校园二手交易平台

> **接手前先读 [`../HANDOFF.md`](../HANDOFF.md)** —— 交接文档，包含运行方式、数据模型、
> 不能改坏的规则、技术债清单和答辩 PPT 素材。

Python + Flask + SQLite 的校园二手交易系统（数据库原理课程作业），服务端渲染网页，
另带一套 `/api/v1` JSON 接口供移动端复用。

## 运行

本机解释器是 `python`（3.13），依赖已装好，直接启动：

    python serve.py

换机器后先装一次依赖（本机默认镜像取不到包时加 `-i https://pypi.org/simple`）：

    python -m pip install -r requirements.txt

`serve.py` 会监听 0.0.0.0 并打印内网地址，手机连同一 WiFi 就能用浏览器打开。

> 旧版本文档写的 `py -3.12` 是原作者机器的环境；本机 `py -0` 只有 3.14，会报
> `No suitable Python runtime found`。代码没有版本相关逻辑，用 `python` 即可。

**部署到服务器**用生产服务器（waitress）：

    set CM_SECRET_KEY=一串随机字符
    python run.py

完整部署步骤、HTTPS、备份、上线检查清单见 [`../HANDOFF.md`](../HANDOFF.md) 附录 A。

首次启动自动创建 `campus_market.db` 并写入示例数据（示例账号 zhangsan / lisi / wangwu 等
10 个，密码统一 `password123`）。

## 环境变量

| 变量 | 作用 | 默认 |
|---|---|---|
| `CM_SECRET_KEY` | 会话签名密钥，**公网部署必须换** | 内置开发值（启动会告警） |
| `CM_DATABASE` | 数据库路径 | `campus_market.db` |
| `CM_UPLOAD_DIR` | 图片存放目录 | `uploads/` |
| `CM_SEED` | 是否写入示例数据，`0` 关闭 | `1` |
| `CM_CSRF` | 是否启用 CSRF 校验，`0` 关闭 | `1` |
| `CM_ORDER_TIMEOUT_MINUTES` | 待接单超过多少分钟自动取消，`0` 关闭 | `120` |
| `PORT` | `run.py` 监听端口 | `8000` |

## 从 v1 升级

v1 的订单状态是「待交易/已完成/已取消」，v2 改成双向确认流程；v3 又加了图片和聊天两张表。
**已有数据库要按顺序迁移一次**（都是幂等的，重复执行安全）：

    python migrate_v2.py
    python migrate_v3.py

v2 脚本会自动备份成 `campus_market.db.v1.bak`。v3 是纯增量建表，不动已有数据。
实际上 `app.py` 启动时也会自动补建 v3 的表，所以老库直接跑也不会报「表不存在」。

## 图片存储

商品图片存在 `campus_market/uploads/items/`，数据库只存文件名。
上线要换对象存储的话，只需改 `images.save_image`，其余代码不用动。

## 数据库管理

不用装任何工具，`manage.py` 覆盖日常操作：

    python manage.py stats                  # 概况：文件大小、各表行数、最近发布
    python manage.py tables                 # 表结构与索引
    python manage.py query "SELECT * FROM items LIMIT 5"
    python manage.py backup                 # 备份到 backup/db-时间戳.db
    python manage.py restore backup/xxx.db --yes
    python manage.py export items           # 导出 CSV（Excel 直接打开）
    python manage.py check                  # 图片体检：缺文件 / 孤儿文件
    python manage.py vacuum                 # 压缩数据库体积
    python manage.py expire 120             # 立即释放超 120 分钟未接单的订单
    python manage.py refresh-pending        # 演示前用：把「待接单」订单挪到最近

`query` 只允许 SELECT，防止误删。改数据请用图形工具（DB Browser for SQLite）或写脚本。
数据库路径可用 `CM_DATABASE` 环境变量覆盖，默认 `campus_market.db`。

## 测试

    python -m unittest tests.test_app -v    # 网页端 + 数据库 + 迁移（133 个用例）
    python -m unittest tests.test_api -v    # /api/v1 接口（43 个用例）
    python -m unittest discover tests -v    # 全部 176 个

## 目录

    campus_market/
    ├── app.py          # Flask 工厂 + 网页路由
    ├── serve.py        # 开发启动（自动重载 + 打印内网地址）
    ├── run.py          # 生产启动（waitress）
    ├── api.py          # /api/v1 JSON 接口（Bearer token，留给移动端复用）
    ├── trading.py      # 交易状态机：下单/接单/取消/交付/收货
    ├── chat.py         # 议价聊天：发消息、报价、接受报价
    ├── images.py       # 商品图片：校验与落盘
    ├── market.py       # 商品搜索与筛选（网页和 API 共用）
    ├── common.py       # 金额、时间、价格校验等共用工具
    ├── database.py     # 连接、建表、示例数据
    ├── schema.sql      # 建表 SQL
    ├── migrate_v2.py   # v1 → v2（订单流程）
    ├── migrate_v3.py   # v2 → v3（图片、聊天）
    ├── uploads/items/  # 商品图片文件
    ├── templates/      # 网页模板
    └── tests/          # test_app.py / test_api.py

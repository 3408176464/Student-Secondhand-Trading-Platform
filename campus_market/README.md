# 校园二手交易平台

Python + Flask + SQLite 的校园二手交易网站（数据库原理课程作业）。

## 运行

    pip install -r requirements.txt
    python app.py

浏览器打开 http://127.0.0.1:5000 。首次启动自动创建 campus_market.db
并写入示例数据（10 个用户，密码统一为 password123）。

## 测试

    python -m unittest tests.test_app -v

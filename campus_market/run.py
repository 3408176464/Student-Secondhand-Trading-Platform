"""生产启动脚本：用 waitress 代替 Flask 自带的开发服务器。

开发服务器（app.run）是单线程、无超时保护、会打印调试信息，只适合自己看。
waitress 是纯 Python 的生产 WSGI 服务器，Windows / Linux 都能跑，一条命令即可。

用法：
    python run.py                # 8000 端口
    set PORT=80 && python run.py # 换端口（Linux 用 export PORT=80）

公网部署前务必设置 CM_SECRET_KEY，否则登录态可被伪造：
    set CM_SECRET_KEY=一串够长的随机字符
    python -c "import secrets; print(secrets.token_urlsafe(48))"
"""
import os

from waitress import serve

from app import DEV_SECRET_KEY, create_app
from net import lan_ip


def main():
    port = int(os.environ.get("PORT", "8000"))
    app = create_app()

    if app.config["SECRET_KEY"] == DEV_SECRET_KEY:
        print("⚠️  CM_SECRET_KEY 未设置，正在用默认值。"
              "仅供本地/内网使用，公网部署必须换掉。\n")

    print(f"校园二手交易平台已启动（waitress，8 线程）")
    print(f"  本机   http://127.0.0.1:{port}")
    print(f"  局域网 http://{lan_ip()}:{port}")
    print("  停止   Ctrl+C\n")

    serve(app, host="0.0.0.0", port=port, threads=8, ident="campus-market")


if __name__ == "__main__":
    main()

"""开发用启动脚本（带自动重载，改代码即时生效）。

比 `python app.py` 多做两件事：
1. 监听 0.0.0.0 —— 默认的 127.0.0.1 只有本机能访问，手机浏览器打不开
2. 自动探测内网 IP，直接打印手机该访问的地址

用手机在同 WiFi 下预览网页版就用这个；真正部署到服务器请用 run.py。

用法：
    python serve.py           # 默认 5000 端口
    python serve.py 8080      # 换端口
"""
import sys

from app import create_app
from net import lan_ip

DEFAULT_PORT = 5000


def banner(ip, port):
    line = "─" * 54
    print(f"""
┌{line}┐
  校园二手 · 开发服务器（改代码自动重载）

  本机          http://127.0.0.1:{port}
  别的设备      http://{ip}:{port}
                （同一局域网下，手机/其他电脑浏览器直接打开）

  对方打不开时按顺序查：
    1. 是不是同一个网络（校园网常开客户端隔离，
       这时用手机开热点让电脑连，最省事）
    2. Windows 防火墙有没有放行 {port}
       （第一次启动弹窗要点「允许访问」）
    3. 换网络后 IP 会变，重新看一眼这里打印的地址
└{line}┘
""")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    banner(lan_ip(), port)
    create_app().run(host="0.0.0.0", port=port, debug=True, use_reloader=True)

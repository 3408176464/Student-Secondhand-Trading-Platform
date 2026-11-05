"""网络小工具：探测本机局域网 IP。

单独一个模块，是为了让 app.py / serve.py / run.py 都能用，
又不产生循环导入（它们都要 import app）。
"""
import socket


def lan_ip():
    """探测本机在局域网里的 IPv4 地址。

    原理：对一个不可达地址做 UDP connect（不会真的发包），
    内核按默认路由选出出口网卡，再从 socket 上取本地地址。
    比遍历网卡列表更准——不会选到 Hyper-V / VPN 的虚拟网卡。
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("10.255.255.255", 1))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()

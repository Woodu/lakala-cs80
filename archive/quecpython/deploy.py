#!/usr/bin/env python3
"""一次性部署：上传设备端文件 → 点屏测试 → 确定 USBNET → 起远程服务。

    py\\python.exe deploy.py            # 完整流程
    py\\python.exe deploy.py --test     # 只做点屏测试
    py\\python.exe deploy.py --boot     # 只起远程服务（不测屏）

跑完这一次之后，后续都可以在 Mac 上通过网络控制设备。
"""
import os
import sys

from device_exec import find_repl, open_port, run_code

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = ["panel_init.py", "cs80.py", "main.py"]


def push(port, names):
    for name in names:
        src = open(os.path.join(HERE, name), encoding="utf-8").read()
        blob = ("f=open('/usr/%s','w')\nf.write(%r)\nf.close()\nprint('ok')\n"
                % (name, src, name))
        print("  %-14s %s" % (name, run_code(port, blob, timeout=25).strip()[:60]))


def main():
    args = sys.argv[1:]
    only_test = "--test" in args
    only_boot = "--boot" in args

    port = find_repl()
    if not port:
        print("✗ 没找到 REPL 口")
        return 1
    print("REPL 口:", port)
    s = open_port(port)
    try:
        if not only_boot:
            print("上传设备端文件 ...")
            push(s, FILES)

        if not only_test:
            print("\n打开 USBNET ...")
            print(run_code(s, "import sys\nsys.path.append('/usr')\nimport main\nmain.net_up()\n", timeout=25))

        if not only_boot:
            print("\n点屏测试（屏幕应在 红/绿/蓝/白 间切换）...")
            out = run_code(s, "import sys\nsys.path.append('/usr')\nimport cs80\ncs80.selftest()\n", timeout=60)
            print(out)

        print("\n启动远程服务 ...")
        print(run_code(s, "import sys\nsys.path.append('/usr')\nimport main\nmain.boot()\n", timeout=30))

        print("\n设备上 /usr 现有文件:")
        print(run_code(s, "import uos\nprint(uos.listdir('/usr'))\n"))
    finally:
        s.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

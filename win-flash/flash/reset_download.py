#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""设备卡在「下载模式但不收数据」时，让它重启回正常工作态（不用拔插）。

现象：设备枚举成 `0525:a4a7`（设备管理器里 `SPRD U2S Diag`），但
`qdisplay_app.py` 一上来就 `PDL connect ... no PDL response (timeout)`。

原因：上一次刷写/校验失败后没发复位，设备**还停在 FDL2 里**，
这时候端口说的是 BSL/HDLC 协议，不是 PDL —— 所以 PDL 握手必然超时。

做法：直接走 BSL 连上去，发 `BSL_CMD_NORMAL_RESET`。
如果连 BSL 都连不上（设备真的卡死了），只能物理断电重上电。

    py\\python.exe reset_download.py            :: 自动找 0525:a4a7 那个口
    py\\python.exe reset_download.py --port COM13
"""
import sys
import time

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

sys.path.insert(0, ".")

import serial.tools.list_ports as lp

from third_party.sprdflash import native, protocol as p


def main():
    port = None
    if "--port" in sys.argv:
        port = sys.argv[sys.argv.index("--port") + 1]
    if not port:
        for it in lp.comports():
            if (it.vid, it.pid) == (0x0525, 0xA4A7):
                port = it.device
                break
    if not port:
        print("没找到 0525:a4a7 下载口 —— 设备现在是正常模式，不用复位。")
        return 1
    print("下载口:", port)
    s = native._open_serial(port)
    try:
        io = p.SpdIO(s, timeout=2.0)
        io.checksum = "sprd"
        print("BSL connect（设备应该还在 FDL2 里）...")
        io.connect()
        io.send(p.BSL_CMD_NORMAL_RESET)
        print("已发 NORMAL_RESET，等它重启 ...")
        time.sleep(1.0)
    except Exception as e:
        print("BSL 也连不上（%s: %s）—— 只能物理断电重上电："
              "拔掉 Type-C 和 DC 12V，等 10 秒，再都插回去。" % (type(e).__name__, e))
        return 2
    finally:
        try:
            s.close()
        except Exception:
            pass
    time.sleep(6)
    for it in lp.comports():
        if (it.vid, it.pid) == (0x2C7C, 0x0901):
            print("设备已回到正常工作态：%s（AT 口）" % it.device)
            return 0
    print("复位已发；如果还没枚举回 2C7C:0901，等 5 秒再看，或断电重上电。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

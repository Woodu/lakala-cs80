#!/usr/bin/env python3
"""软下载刷机：AT+QDOWNLOAD=1 → 0525:a4a7 → PDL → FDL1/FDL2 → 写 fw.pac。

    py\\python.exe flash_at.py --check    # 只读：找 AT 口 + ATI + PDL 握手
    py\\python.exe flash_at.py            # 进下载模式 → 确认后刷写

不用短接 pin55，不用拆机。
"""
import sys
import time

try:  # Windows 控制台是 GBK 时，print 里的 ✗/✓ 会直接抛 UnicodeEncodeError
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

sys.path.insert(0, ".")  # 便携版 (python3xx._pth) 不含脚本目录，必须先插进来

import serial
import serial.tools.list_ports as lp

from third_party.sprdflash import native, pdl

PAC = "fw.pac"


# ---------- 串口打开（断言 DTR/RTS：CDC 下载口需要它才有载波） ----------
def open_serial(com, timeout=0.3):
    last = None
    for _ in range(200):
        try:
            s = serial.Serial(com, 115200, timeout=timeout, write_timeout=2.0)
        except Exception as e:
            last = e
            time.sleep(0.03)
            continue
        try:
            s.dtr = True
            s.rts = True
        except Exception:
            pass
        return s
    raise RuntimeError("打不开 %s: %s" % (com, last))


def pdl_probe(dev, timeout=2.0):
    """只读握手：PDL CONNECT。返回 None 表示成功，否则返回错误描述。"""
    s = open_serial(dev)
    try:
        pdl.PdlIO(s, timeout=timeout).connect()
        return None
    except Exception as e:
        return "%s: %s" % (type(e).__name__, e)
    finally:
        try:
            s.close()
        except Exception:
            pass


# ---------- AT ----------
def quectel_ports():
    return [p for p in lp.comports() if "Quectel" in (p.description or "")]


def at_cmd(port, cmd, wait=0.8, timeout=1.2):
    with serial.Serial(port, 115200, timeout=timeout) as s:
        s.reset_input_buffer()
        s.write(cmd.encode() + b"\r\n")
        time.sleep(wait)
        return s.read(2048).decode("latin-1", "replace")


def find_at_port():
    cand = [p for p in quectel_ports() if "AT" in (p.description or "")]
    for p in cand + quectel_ports():
        try:
            if "OK" in at_cmd(p.device, "AT", wait=0.4):
                return p
        except Exception:
            continue
    return None


def wait_download_port(seconds=30, skip=None):
    end = time.time() + seconds
    while time.time() < end:
        for p in lp.comports():
            if (p.vid, p.pid) == (0x0525, 0xA4A7) and p.device != skip:
                return p.device
        time.sleep(0.3)
    return None


def _progress(file_id, sent, total):
    pct = sent * 100 // total if total else 100
    print("\r  %-14s %3d%%" % (file_id, pct), end="", flush=True)
    if pct >= 100:
        print()


def main():
    check_only = "--check" in sys.argv

    # 先看是不是已经有下载口（上次已经发过 AT+QDOWNLOAD=1 时直接复用）
    dev = wait_download_port(seconds=1)
    if dev is not None:
        print("✓ 设备已处于下载模式: %s" % dev)
    else:
        at = find_at_port()
        if at is None:
            print("✗ 既没有下载口，也找不到应答 AT 的端口。")
            print("  把设备断电再上电（回到正常工作态）后重试。")
            return 1
        print("✓ AT 口: %s (%s)" % (at.device, at.description))
        print("  ATI ->", at_cmd(at.device, "ATI").strip().replace("\r\n", " | "))
        print("  版本 ->", at_cmd(at.device, "AT+QGMR").strip().replace("\r\n", " | "))
        print("\n发送 AT+QDOWNLOAD=1 进入下载模式 ...")
        print("  回应 ->", at_cmd(at.device, "AT+QDOWNLOAD=1", wait=0.5).strip().replace("\r\n", " | "))
        dev = wait_download_port()
        if dev is None:
            print("✗ 30 秒内没等到 0525:a4a7")
            return 1
        print("✓ 下载口: %s" % dev)

    err = pdl_probe(dev)
    if err:
        print("✗ PDL 握手失败: %s" % err)
        print("  排查：换 USB 口（避开 hub）→ 重新 AT+QDOWNLOAD → 再跑")
        return 1
    print("✓ PDL 握手成功")

    if check_only:
        print("\n只读检查通过。运行 py\\python.exe flash_at.py 开始刷写。")
        return 0

    print("\n即将写入 %s：格式化内部文件系统 + 重建 NV，原厂固件不可恢复。" % PAC)
    if input("确认刷写请输入 y : ").strip().lower() != "y":
        print("已取消")
        return 0

    native._open_serial = open_serial          # 让 native_flash 用断言了 DTR/RTS 的打开方式
    native.native_flash(dev, PAC, progress=_progress, format_fs=True, do_reset=True)
    print("完成。设备会自己重启，屏幕应进入 QuecPython。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

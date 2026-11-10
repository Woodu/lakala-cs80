#!/usr/bin/env python3
"""只把 QDisplay APP 写到 0x60260000（不动其他分区 / 不格式化）。

复用本项目验证过的 native 路径（PDL → FDL1 → FDL2 → BSL 写 → 读回校验 → 复位）。

    py\\python.exe qdisplay_app.py <app.bin> --auto      # 自己发 AT+QDOWNLOAD
    py\\python.exe qdisplay_app.py <app.bin> --port COM13
    py\\python.exe qdisplay_app.py <app.bin> --no-verify
"""
import sys
import time

try:  # Windows 控制台是 GBK 时，print 里的 ✗/✓ 会直接抛 UnicodeEncodeError
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

sys.path.insert(0, ".")

import serial
import serial.tools.list_ports as lp

from third_party.sprdflash import native, pdl, protocol as p
from third_party.sprdflash.flasher import DEFAULT_CHUNK, classify, send_stage
from third_party.sprdflash.pac import parse_pac

PAC = "fw.pac"
APP_ADDR = 0x60260000
CRLF = "\r\n"


def find_download_port():
    for it in lp.comports():
        if (it.vid, it.pid) == (0x0525, 0xA4A7):
            return it.device
    return None


def find_at_port():
    for it in lp.comports():
        if it.vid == 0x2C7C and "AT Port" in (it.description or ""):
            return it.device
    return None


def enter_download(port=None):
    """返回下载口：已有就用，没有就发 AT+QDOWNLOAD=1 等它出现。"""
    if port:
        return port
    port = find_download_port()
    if port:
        print("设备已在下载模式:", port)
        return port
    # 设备刚上电时 QPY 要跑 main.py 点屏，AT 口可能暂时不响应：重试等待
    deadline = time.time() + 60
    at = None
    while time.time() < deadline:
        at = find_at_port()
        if at:
            try:
                with serial.Serial(at, 115200, timeout=1.5, write_timeout=3.0) as s:
                    s.dtr = True
                    s.rts = True
                    s.reset_input_buffer()
                    s.write(b"AT" + CRLF.encode())
                    time.sleep(0.6)
                    if b"OK" in s.read(512):
                        print("AT 口就绪:", at)
                        break
            except Exception as e:
                print("  AT 口暂时不通（%s），继续等 ..." % type(e).__name__)
        at = None
        time.sleep(3)
    if not at:
        print("✗ 60 秒内 AT 口没有响应 —— 请把 DC 和 Type-C 都拔掉、等 10 秒再上电后重试。")
        return None
    print("AT 口:", at)
    with serial.Serial(at, 115200, timeout=1.5, write_timeout=3.0) as s:
        s.dtr = True
        s.rts = True
        s.reset_input_buffer()
        s.write(b"ATI" + CRLF.encode())
        time.sleep(0.8)
        ver = s.read(2048).decode("latin-1", "replace")
        print("  ATI:", ver.replace(CRLF, " | ").strip()[:140])
        s.write(b"AT+QDOWNLOAD=1" + CRLF.encode())
        time.sleep(0.8)
        print("  QDOWNLOAD:", s.read(512).decode("latin-1", "replace").replace(CRLF, " | ").strip()[:80])
    end = time.time() + 45
    while time.time() < end:
        port = find_download_port()
        if port:
            print("下载口:", port)
            return port
        time.sleep(0.3)
    print("✗ 45 秒内没等到下载口 0525:a4a7")
    return None


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    app_path = sys.argv[1]
    port = None
    verify = "--no-verify" not in sys.argv
    if "--port" in sys.argv:
        port = sys.argv[sys.argv.index("--port") + 1]

    port = enter_download(port)
    if not port:
        return 1

    app = open(app_path, "rb").read()
    print("APP: %s (%d B) -> 0x%08X" % (app_path, len(app), APP_ADDR))

    info = parse_pac(PAC, verify_payload=True)
    fdl1 = next((e for e in info.entries if classify(e) == "fdl1"), None)
    fdl2 = next((e for e in info.entries if classify(e) == "fdl2"), None)
    if not fdl1:
        raise SystemExit("PAC 里没有 FDL1")
    with open(PAC, "rb") as f:
        fdl1_data = native._read_entry(f, fdl1)
        fdl2_data = native._read_entry(f, fdl2)
    print("FDL1 %d B @ 0x%08X / FDL2 %d B @ 0x%08X"
          % (len(fdl1_data), fdl1.address, len(fdl2_data), fdl2.address))

    s = native._open_serial(port)
    try:
        pio = pdl.PdlIO(s, timeout=2.0)
        print("PDL connect ...")
        pio.connect()
        print("  加载 FDL1 ...")
        pio.send_image(fdl1.address, fdl1_data, checksum=0)
        ver = pio.exec_and_get_ver(timeout=5.0)
        print("  FDL1 已运行")

        io = p.SpdIO(s, timeout=2.0)
        io.checksum = "sprd"
        io.connect()
        print("  加载 FDL2 ...")
        send_stage(io, fdl2.address, fdl2_data, DEFAULT_CHUNK)
        io.command(p.BSL_CMD_EXEC_DATA, timeout=15.0, what="EXEC FDL2")
        io.connect()
        print("  FDL2 已运行")

        print("写入 APP (%d B) ..." % len(app))
        t0 = time.time()
        send_stage(io, APP_ADDR, app, DEFAULT_CHUNK)
        print("  写完 %.1f s" % (time.time() - t0))

        if verify:
            print("读回校验 ...")
            # 1 KB 分块：native._read_flash() 默认 0x3000 的读回不可靠（会误报不一致）
            back = native._read_flash(io, APP_ADDR, len(app), chunk=1024)
            if back[:len(app)] != app:
                diff = sum(1 for x, y in zip(back, app) if x != y)
                print("✗ 读回不一致（%d 字节不同）！保持下载模式，请勿断电。" % diff)
                return 2
            print("✓ 读回一致")

        print("复位 ...")
        io.send(p.BSL_CMD_NORMAL_RESET)
        try:
            s.flush()
        except Exception:
            pass
        time.sleep(1.0)
    finally:
        try:
            s.close()
        except Exception:
            pass
    print("完成：设备正在重启进入 QDisplay。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

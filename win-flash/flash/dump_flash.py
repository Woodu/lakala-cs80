#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""整片 flash 备份 / 回滚（裸读写，走和刷机同一套 PDL→FDL→BSL 路径）。

为什么要它：`qdisplay_app.py` 只动 `0x60260000` 的 APP 分区，`flash_at.py` 会整包覆盖
BOOT/AP/APP/ROOT 并格式化文件系统。降级/换固件之前先把整片 8 MiB 读下来，出问题时原样写回。

    :: 备份（默认 0x60000000 起 8 MiB → win-flash/dumps/）
    py\\python.exe dump_flash.py
    py\\python.exe dump_flash.py --addr 0x60260000 --size 0xCE000      :: 只备份 APP 分区
    py\\python.exe dump_flash.py -o ..\\..\\backup.bin

    :: 回滚（把 dump 裸写回原地址，写完读回校验）
    py\\python.exe dump_flash.py --restore ..\\..\\backup.bin --addr 0x60000000

踩过的坑（已写进代码）：
* `BSL_CMD_READ_FLASH` 的 chunk 超过 0x8000 时，设备会**回一个 0 字节的应答**，
  不报错但数据是空的。以前用它做「读速基准」会得到 55 MB/s 的假速度，最后 dump 出 0 字节文件。
  现在每次读都校验返回长度，chunk 只从验证过能用的档里挑。
* 单条 READ_FLASH 里 offset 字段被 FDL2 忽略（偏移必须加进 addr），见 `native._read_flash`。
"""
import hashlib
import os
import struct
import sys
import time
import zlib

try:  # Windows 控制台是 GBK 时，print 里的 ✗/✓ 会直接抛 UnicodeEncodeError
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

sys.path.insert(0, ".")

import serial
import serial.tools.list_ports as lp

from third_party.sprdflash import native, pdl, protocol as sp
from third_party.sprdflash.flasher import DEFAULT_CHUNK, classify, send_stage
from third_party.sprdflash.pac import parse_pac

PAC = "fw.pac"
DEFAULT_ADDR = 0x60000000
DEFAULT_SIZE = 0x800000          # 8 MiB：BOOT + AP + APP + nv/ROOT 全在里面
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dumps")
DL_VID_PID = (0x0525, 0xA4A7)
AT_VID = 0x2C7C


# ---------- 串口 / 下载模式 ----------
def find_port(vid, pid=None, desc=None):
    for it in lp.comports():
        if it.vid != vid:
            continue
        if pid is not None and it.pid != pid:
            continue
        if desc and desc not in (it.description or ""):
            continue
        return it.device
    return None


def enter_download():
    """返回下载口。已经在下载模式就直接用，否则靠 AT+QDOWNLOAD=1 让设备自己重枚举。"""
    port = find_port(*DL_VID_PID)
    if port:
        print("设备已在下载模式:", port)
        return port

    at = None
    deadline = time.time() + 60
    while time.time() < deadline and not at:
        at = find_port(AT_VID, None, "AT Port")
        if not at:
            time.sleep(0.5)
    if not at:
        print("✗ 找不到 AT 口（2c7c / Quectel USB AT Port）")
        return None

    print("AT 口:", at, "→ 发 AT+QDOWNLOAD=1")
    try:
        s = serial.Serial(at, 115200, timeout=1.0, write_timeout=1.0)
    except Exception as exc:
        print("✗ 打不开 %s：%s" % (at, exc))
        print("  常见原因：桌面壳（cs80-panel）还开着，它的 qdc1 --probe 正占着 COM 口")
        return None
    try:
        s.dtr = True
        s.rts = True
        time.sleep(0.2)
        s.write(b"AT+QDOWNLOAD=1\r\n")
        time.sleep(1.0)
        try:
            print("  AT 回:", s.read(512).decode("utf-8", "replace").strip().replace("\r\n", " | "))
        except Exception:
            pass
    finally:
        s.close()

    deadline = time.time() + 45
    while time.time() < deadline:
        port = find_port(*DL_VID_PID)
        if port:
            print("下载口:", port)
            time.sleep(0.5)
            return port
        time.sleep(0.4)
    print("✗ 45 秒内没等到下载口 0525:a4a7")
    return None


def connect_fdl(port):
    """PDL → FDL1 → FDL2，返回 (serial, SpdIO)。用完要自己关 serial。"""
    info = parse_pac(PAC, verify_payload=True)
    fdl1 = next((e for e in info.entries if classify(e) == "fdl1"), None)
    fdl2 = next((e for e in info.entries if classify(e) == "fdl2"), None)
    if not fdl1 or not fdl2:
        raise SystemExit("PAC 里没有 FDL1/FDL2")
    with open(PAC, "rb") as f:
        fdl1_data = native._read_entry(f, fdl1)
        fdl2_data = native._read_entry(f, fdl2)

    s = native._open_serial(port)
    pio = pdl.PdlIO(s, timeout=2.0)
    pio.connect()
    pio.send_image(fdl1.address, fdl1_data, checksum=0)
    pio.exec_and_get_ver(timeout=5.0)
    io = sp.SpdIO(s, timeout=2.0)
    io.checksum = "sprd"
    io.connect()
    send_stage(io, fdl2.address, fdl2_data, DEFAULT_CHUNK)
    io.command(sp.BSL_CMD_EXEC_DATA, timeout=15.0, what="EXEC FDL2")
    io.connect()
    return s, io


# ---------- 读：每条都校验长度 ----------
def read_checked(io, addr, n, timeout=3.0):
    io.send(sp.BSL_CMD_READ_FLASH, struct.pack(">III", addr, n, 0))
    rtype, rdata = io.recv(timeout=timeout)
    if rtype != sp.BSL_REP_READ_FLASH:
        raise RuntimeError("READ @ %#x: 收到 %s" % (addr, sp.rep_name(rtype)))
    if len(rdata) != n:
        raise RuntimeError("READ @ %#x: 要 %d B 只回 %d B" % (addr, n, len(rdata)))
    return rdata


def read_range(io, addr, total, chunk):
    out = bytearray()
    off = 0
    while off < total:
        n = min(chunk, total - off)
        out += read_checked(io, addr + off, n)
        off += n
    return bytes(out)


def pick_chunk(io, addr, total=0x10000):
    """挑一个真能读出数据的 chunk（>0x8000 会被设备回空包，且不报错）。"""
    best = None
    for chunk in (0x1000, 0x2000, 0x4000, 0x8000):
        try:
            t0 = time.time()
            read_range(io, addr, min(total, 0x10000), chunk)
        except Exception as exc:
            print("  chunk 0x%-5X ✗ %s" % (chunk, exc))
            continue
        dt = time.time() - t0
        kbs = min(total, 0x10000) / 1024.0 / max(dt, 1e-6)
        print("  chunk 0x%-5X %.2f s  %.1f KB/s" % (chunk, dt, kbs))
        if best is None or kbs > best[0]:
            best = (kbs, chunk)
    if best is None:
        raise RuntimeError("所有 chunk 都读不出数据")
    return best[1]


def report(data, base):
    print("  %d B  sha256=%s" % (len(data), hashlib.sha256(data).hexdigest()))
    print("  每 1 MiB crc32:")
    for i in range(0, len(data), 0x100000):
        print("    0x%08X  %08x" % (base + i, zlib.crc32(data[i:i + 0x100000]) & 0xFFFFFFFF))


def do_dump(io, addr, size, out_path):
    print("\n== 读速基准 ==")
    chunk = pick_chunk(io, addr)
    print("  → chunk 0x%X" % chunk)
    print("\n== dump 0x%08X..0x%08X (%.1f MiB) ==" % (addr, addr + size, size / 1048576.0))
    t0 = time.time()
    data = read_range(io, addr, size, chunk)
    dt = time.time() - t0
    print("  读完 %.1f s (%.1f KB/s)" % (dt, len(data) / 1024.0 / max(dt, 1e-6)))

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(data)
    print("✓ 保存:", os.path.abspath(out_path))
    report(data, addr)
    return 0


def do_restore(io, addr, data):
    print("\n== 裸写 0x%08X (%d B) ==" % (addr, len(data)))
    t0 = time.time()
    send_stage(io, addr, data, DEFAULT_CHUNK)
    print("  写完 %.1f s" % (time.time() - t0))
    print("  读回校验 ...")
    back = native._read_flash(io, addr, len(data), chunk=1024)
    if back != data:
        diff = sum(1 for a, b in zip(back, data) if a != b)
        print("✗ 读回不一致：%d 字节不同（保持下载模式，勿断电，可重跑一次）" % diff)
        return 2
    print("✓ 读回一致")
    return 0


def main():
    args = sys.argv[1:]
    restore = None
    addr = DEFAULT_ADDR
    size = DEFAULT_SIZE
    out = None

    if "--restore" in args:
        i = args.index("--restore")
        if i + 1 >= len(args):
            raise SystemExit("--restore 需要文件路径")
        restore = args[i + 1]
    if "--addr" in args:
        addr = int(args[args.index("--addr") + 1], 0)
    if "--size" in args:
        size = int(args[args.index("--size") + 1], 0)
    if "-o" in args:
        out = args[args.index("-o") + 1]
    if restore and out:
        raise SystemExit("--restore 与 -o 不能同时用")
    if restore and "--size" in args:
        size = os.path.getsize(restore)

    port = enter_download()
    if not port:
        return 1

    s = None
    io = None
    try:
        s, io = connect_fdl(port)
        print("FDL2 OK")
        if restore:
            data = open(restore, "rb").read()
            if not size:
                size = len(data)
            return do_restore(io, addr, data)
        if not out:
            if addr == DEFAULT_ADDR and size == DEFAULT_SIZE:
                stamp = time.strftime("%Y%m%d")
                out = os.path.join(OUT_DIR, "full_flash_0x%08X_%dMiB_%s.bin"
                                   % (addr, size // 0x100000, stamp))
            else:
                stamp = time.strftime("%Y%m%d")
                out = os.path.join(OUT_DIR, "flash_0x%08X_%dB_%s.bin" % (addr, size, stamp))
        return do_dump(io, addr, size, out)
    finally:
        if io is not None:
            try:
                print("\nNORMAL_RESET ...")
                io.send(sp.BSL_CMD_NORMAL_RESET)
                time.sleep(0.5)
            except Exception as exc:
                print("  reset 失败:", exc)
        if s is not None:
            try:
                s.close()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())

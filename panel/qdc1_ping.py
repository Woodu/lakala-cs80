#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QDC1 最小探针：发一个 HELLO，确认 QDisplay 固件还活着（不依赖 qdc1.exe / host）。

用来在「刷了试验固件之后」自动判断 APP 有没有起来 —— 起不来就自动回退。
退出码：0 = 正常应答；1 = 打不开口；2 = 打开了但没回应（APP 挂了）。

    py\\python.exe qdc1_ping.py                 :: 默认 COM15
    py\\python.exe qdc1_ping.py --com COM15 --state
"""
import struct
import sys
import time

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

P1, P2, P3, P4, P5 = 2654435761, 2246822519, 3266489917, 668265263, 374761393
M = 0xFFFFFFFF


def _rotl(x, r):
    return ((x << r) | (x >> (32 - r))) & M


def xxh32(data, seed=0):
    n = len(data)
    i = 0
    if n >= 16:
        v = [(seed + P1 + P2) & M, (seed + P2) & M, seed & M, (seed - P1) & M]
        while i <= n - 16:
            for k in range(4):
                lane = int.from_bytes(data[i:i + 4], "little")
                i += 4
                v[k] = (_rotl((v[k] + lane * P2) & M, 13) * P1) & M
        h = (_rotl(v[0], 1) + _rotl(v[1], 7) + _rotl(v[2], 12) + _rotl(v[3], 18)) & M
    else:
        h = (seed + P5) & M
    h = (h + n) & M
    while i <= n - 4:
        h = (h + int.from_bytes(data[i:i + 4], "little") * P3) & M
        h = (_rotl(h, 17) * P4) & M
        i += 4
    while i < n:
        h = (h + data[i] * P5) & M
        h = (_rotl(h, 11) * P1) & M
        i += 1
    h ^= h >> 15
    h = (h * P2) & M
    h ^= h >> 13
    h = (h * P3) & M
    h ^= h >> 16
    return h


class Dev:
    def __init__(self, port):
        import serial
        self.s = serial.Serial(port, 115200, timeout=0.05, write_timeout=1.5)
        self.s.dtr = False
        self.s.rts = False
        self.seq = 0

    def ask(self, kind=1, flags=0, offset=0, raw_length=0, payload=b"", wait=1.5):
        self.seq = (self.seq + 1) & M
        h = struct.pack("<IBBHIIIII", 0x31434451, 1, kind, flags, self.seq, offset,
                        len(payload), raw_length, xxh32(payload))
        h += struct.pack("<I", xxh32(h))
        try:
            self.s.reset_input_buffer()
            self.s.write(h + payload)
            self.s.flush()
        except Exception:
            # 设备端没在读（APP 没起来/忙）→ CDC 发送缓冲满就超时，别把脚本卡死
            return None
        t, buf = time.time(), b""
        while time.time() - t < wait:
            buf += self.s.read(128)
            while len(buf) >= 32 and len(buf) >= 32 + struct.unpack("<I", buf[16:20])[0]:
                need = 32 + struct.unpack("<I", buf[16:20])[0]
                hdr, body = buf[:32], buf[32:need]
                buf = buf[need:]
                if hdr[:4] != b"QDC1" or xxh32(hdr[:28]) != struct.unpack("<I", hdr[28:32])[0]:
                    continue
                st, rx, dec, draw, heap, detail = struct.unpack("<IIIIII", body[:24])
                return dict(status=st, rx=rx, decode=dec, draw=draw, heap=heap, detail=detail)
        return None


def main():
    port = "COM15"
    if "--com" in sys.argv:
        port = sys.argv[sys.argv.index("--com") + 1]
    try:
        d = Dev(port)
    except Exception as e:
        print("打不开 %s：%s（设备没插 / 被官方上位机占着？）" % (port, e))
        return 1
    try:
        r = d.ask(1)
        if not r:
            print("%s 没有回应 —— QDisplay APP 没起来（固件 hang 了或不是 QDisplay）" % port)
            return 2
        print("QDC1 OK：block=%d features=0x%x" % (r["detail"], (d.ask(1, flags=2) or {}).get("detail", 0)))
        if "--state" in sys.argv:
            s = d.ask(1, flags=10)
            if s:
                print("亮度 %d%%  背光PWM %s  edges=%d" %
                      (s["rx"], "运行中" if s["decode"] else "停", s["draw"]))
        return 0
    finally:
        try:
            d.s.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())

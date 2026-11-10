#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把「等待连接」页换成自己做的图（黑底纯白，不闪）—— 只改固件里的一个 LZ4 块，不用重编固件。

原理（0.3.0 实测）：

    controls_poll() 里渲染等待页的代码：
        movw r0, #0x88fc ; movt r0, #0x6027   → waiting_page_lz4 = 0x602788fc
        mov.w r3, #0x1f4000                    → QD_FRAME_BYTES = 2048000
        movw r2, #0xc65f                       → sizeof(waiting_page_lz4) = 50783
        bl   LZ4_decompress_safe
        cmp.w r0, #0x1f4000 ; beq ...

    也就是：页面 = flash 里 0x602788fc 起的一段 **LZ4 块**（raw block，store_size=False），
    解压后正好 2048000 字节（BGR565 小端整帧）。所以换页面只要：

      1. 在同样位置写入新的 LZ4 块（必须 ≤ 原长度，我们那张纯黑白只有 ~22 KB）
      2. 把 `movw r2,#0xc65f` 的立即数改成新长度
      3. 重算 APP2 头部 CRC32（否则 loader 不启动 APP，屏黑）

用法（刷机侧，stdlib 就行）：

    py\\python.exe patch_waiting_page_image.py ..\\..\\win-display\\qdisplay\\qdisplay_native.bin
    py\\python.exe qdisplay_app.py qdisplay_native_wait.bin --auto

页面资源用 `make_waiting_page.py` 生成（默认在 `waiting_page/`），也可以 `--assets` 指定目录。
"""
import hashlib
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # embed python 的 ._pth 不含脚本目录
import appimg

# 渲染等待页那几条指令的字节指纹：movw r0,#imm16 ; movt r0,#0x6027 ; mov.w r3,#0x1f4000
FINGERPRINT = bytes.fromhex("48f6fc00c6f227004ff4fa13")
BASE = 0x60260000


def _imm16(h1, h2):
    i = (h1 >> 10) & 1
    imm4 = h1 & 0xF
    imm3 = (h2 >> 12) & 7
    imm8 = h2 & 0xFF
    return (imm4 << 12) | (i << 11) | (imm3 << 8) | imm8


def _is_movw(h1, h2):
    return (h1 & 0xFB00) == 0xF200 and (h2 & 0x8000) == 0


def _is_movt(h1, h2):
    return (h1 & 0xFB00) == 0xF2C0 and (h2 & 0x8000) == 0


def _enc_movw(value, rd):
    imm4 = (value >> 12) & 0xF
    i = (value >> 11) & 1
    imm3 = (value >> 8) & 7
    imm8 = value & 0xFF
    h1 = 0xF240 | (i << 10) | imm4
    h2 = (imm3 << 12) | (rd << 8) | imm8
    return struct.pack("<HH", h1, h2)


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    src = None
    dst = None
    assets = os.path.join(os.path.dirname(os.path.abspath(__file__)), "waiting_page")
    i = 0
    while i < len(args):
        if args[i] == "--assets" and i + 1 < len(args):
            assets = args[i + 1]
            i += 2
            continue
        if args[i].startswith("--"):
            i += 1
            continue
        if src is None:
            src = args[i]
        elif dst is None:
            dst = args[i]
        i += 1
    if not src:
        print(__doc__)
        return 2
    dst = dst or (os.path.splitext(src)[0] + "_wait.bin")

    lz4_path = os.path.join(assets, "page.bgr565.lz4")
    meta_path = os.path.join(assets, "page.meta")
    for p in (lz4_path, meta_path):
        if not os.path.isfile(p):
            print("缺少资源：%s（先跑 make_waiting_page.py 生成）" % p)
            return 2
    blob = open(lz4_path, "rb").read()
    meta = json.load(open(meta_path, encoding="utf-8"))
    if meta.get("raw_bytes") != 2048000:
        print("meta 里 raw_bytes != 2048000，不是整帧页面")
        return 2

    data = bytearray(open(src, "rb").read())
    if data[:4] not in (b"2PPA", b"APP2"):
        print("不是 APP2 容器：%s" % src)
        return 1
    if int.from_bytes(data[4:8], "little") != len(data):
        print("容器长度字段与文件长度不符，拒绝处理")
        return 1

    pos = data.find(FINGERPRINT)
    if pos < 0:
        print("没找到等待页渲染代码的指纹（固件版本不同？）")
        return 3
    if data.find(FINGERPRINT, pos + 1) >= 0:
        print("指纹命中多处，不敢猜")
        return 1

    src_addr = (_imm16(*struct.unpack_from("<HH", data, pos + 4)) << 16) | \
               _imm16(*struct.unpack_from("<HH", data, pos))
    size_at = pos + 12
    size_h1, size_h2 = struct.unpack_from("<HH", data, size_at)
    if not _is_movw(size_h1, size_h2):
        print("补丁点后面不是 movw（%04x %04x），放弃" % (size_h1, size_h2))
        return 1
    old_size = _imm16(size_h1, size_h2)
    blob_at = src_addr - BASE
    print("输入：%s  %d B  sha256=%s" % (src, len(data), hashlib.sha256(data).hexdigest()))
    print("等待页 LZ4：flash 0x%08x → 文件偏移 0x%x，原 %d B" % (src_addr, blob_at, old_size))

    if not (0 < blob_at < len(data) and blob_at + old_size <= len(data)):
        print("原页面块越界，放弃")
        return 1
    if len(blob) > old_size:
        print("新页面压缩后 %d B > 原 %d B，放不下（简化配色再试）" % (len(blob), old_size))
        return 1
    if len(blob) > 0xFFFF:
        print("新页面 %d B 超出 movw 立即数上限" % len(blob))
        return 1

    data[blob_at:blob_at + len(blob)] = blob
    data[blob_at + len(blob):blob_at + old_size] = b"\0" * (old_size - len(blob))  # 余量清零
    data[size_at:size_at + 4] = _enc_movw(len(blob), (size_h2 >> 8) & 0xF)
    data = appimg.fix(data)
    open(dst, "wb").write(data)

    # 自检
    chk_h1, chk_h2 = struct.unpack_from("<HH", data, size_at)
    ok_size = _imm16(chk_h1, chk_h2) == len(blob)
    ok_crc = appimg.check(data)
    print("新页面：%d B（原 %d B，余量 %d B） 整帧 xxh32=%#x"
          % (len(blob), old_size, old_size - len(blob), meta.get("xxh32", 0)))
    print("立即数补丁：%s   APP2 CRC32：%s" % ("OK" if ok_size else "FAIL", "OK" if ok_crc else "FAIL"))
    if not (ok_size and ok_crc):
        return 1
    print("输出：%s  %d B  sha256=%s" % (dst, len(data), hashlib.sha256(bytes(data)).hexdigest()))
    print("刷写：py\\python.exe qdisplay_app.py %s --auto" % os.path.basename(dst))
    print("刷完屏上会自动出现新等待页（主机 5 s 不发命令就会画）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

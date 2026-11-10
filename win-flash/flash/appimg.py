#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""APP2 容器（写在 0x60260000 的 `qdisplay_native.bin`）的头部 CRC32。

实测（对比官方 0.3.0 / 0.3.1 两个 BIN）：

    头部 u32@8 = crc32(整个文件，计算前把 u32@8 自己当成 0)

    magic        u32@0   "2PPA"（u32 0x41505032）
    total_len    u32@4   = 文件实际长度
    crc32        u32@8   ← 就是这一项
    version?     u32@12  0x00010000
    appimg_enter u32@16  例如 0x60265c49（0.3.0）/ 0x60266045（0.3.1）
    appimg_exit  u32@20  例如 0x60265c71（0.3.0）/ 0x6026606d（0.3.1）
    u32@24..32   0
    u32@32..128  16 字节一条的装载描述符 (kind, offset, size, address)

**所以任何字节改动都必须重算 @8，否则 loader 直接不启动 APP —— 现象是
“刷写/读回校验都成功，但 APP 不跑”：QDC1(COM15) 无应答、屏黑，AT 口正常。**
（我第一次改 panel_init 时就是踩了这个坑：连只改文件末尾填充字节也会不启动。）

本模块给几个 patch_*.py 共用：`fix()` 重算、`check()` 校验。
"""
import struct
import zlib

CRC_OFF = 8


def compute(data) -> int:
    """按“@8 视为 0”计算整文件 CRC32。"""
    z = bytearray(data)
    z[CRC_OFF:CRC_OFF + 4] = b"\0\0\0\0"
    return zlib.crc32(bytes(z)) & 0xFFFFFFFF


def stored(data) -> int:
    return struct.unpack_from("<I", data, CRC_OFF)[0]


def check(data) -> bool:
    return stored(data) == compute(data)


def fix(data) -> bytearray:
    """返回一份把 @8 重算好的 bytearray。"""
    out = bytearray(data)
    out[CRC_OFF:CRC_OFF + 4] = struct.pack("<I", compute(out))
    return out


def describe(path, data) -> str:
    ent = struct.unpack_from("<I", data, 16)[0]
    ext = struct.unpack_from("<I", data, 20)[0]
    return ("%s: %d B  crc32@8=%08x(%s)  enter=0x%08x exit=0x%08x"
            % (path, len(data), stored(data), "ok" if check(data) else "BAD", ent, ext))

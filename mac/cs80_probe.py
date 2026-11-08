#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在 macOS 上探测 CS80（QDisplay 固件）的 QDC1 数据通道。

设备端固件是跨平台的（QDC1 与主机 OS 无关）；官方上位机只有 Windows 版，
所以 Mac 上要自己走 USB。这个脚本先只做一件事：**找出哪个 USB 接口能 claim、
并且能收到 QDC1 HELLO 回复**。通了才有必要写完整客户端。

    sudo pip3 install pyusb        # 或 python3 -m pip install --user pyusb
    sudo python3 mac/cs80_probe.py

为什么可能要 sudo：macOS 上 libusb 抢厂商接口通常需要 root。
"""
import struct
import sys
import time

try:
    import usb.core
    import usb.util
except ImportError:
    sys.exit("缺 pyusb：pip3 install pyusb")

VID, PID = 0x2C7C, 0x0901
MAGIC = 0x31434451


# ---- QDC1 校验：XXH32 变体（与固件/上位机 Core/Protocol.cs Hash32 一致）----
def xxh32(data):
    mask = 0xFFFFFFFF
    p1, p2, p3, p4, p5 = 0x9E3779B1, 0x85EBCA77, 0xC2B2AE3D, 0x27D4EB2F, 0x165667B1

    def rot(value, count):
        return ((value << count) | (value >> (32 - count))) & mask

    offset, size = 0, len(data)
    if size >= 16:
        values = [(p1 + p2) & mask, p2, 0, (-p1) & mask]
        while offset <= size - 16:
            for i in range(4):
                value = struct.unpack_from("<I", data, offset)[0]
                values[i] = rot((values[i] + value * p2) & mask, 13) * p1 & mask
                offset += 4
        value = sum(rot(v, n) for v, n in zip(values, (1, 7, 12, 18))) & mask
    else:
        value = p5
    value = (value + size) & mask
    while offset <= size - 4:
        value = rot((value + struct.unpack_from("<I", data, offset)[0] * p3) & mask, 17) * p4 & mask
        offset += 4
    while offset < size:
        value = rot((value + data[offset] * p5) & mask, 11) * p1 & mask
        offset += 1
    value = (value ^ (value >> 15)) * p2 & mask
    value = (value ^ (value >> 13)) * p3 & mask
    return value ^ (value >> 16)


def hello_packet():
    head = struct.pack("<IBBHIIIII", MAGIC, 1, 1, 0, 1, 0, 0, 0, xxh32(b""))
    return head + struct.pack("<I", xxh32(head))


def parse_hello(buf):
    """返回 (ok, 说明)。固件回复应为 28 字节头 + payload。"""
    if len(buf) < 28:
        return False, "响应太短 (%d B)" % len(buf)
    magic, version, kind, flags, seq, offset, length, raw, check = struct.unpack_from("<IBBHIIIII", buf, 0)
    head = buf[:28]
    if magic != MAGIC:
        return False, "magic 不对: %#x（原始: %s）" % (magic, buf[:16].hex())
    if check != xxh32(head[:24]):
        return False, "头校验不对（可能是别的数据流）"
    payload = buf[28:28 + length]
    if len(payload) < length:
        return False, "payload 不足: 期望 %d 拿到 %d" % (length, len(payload))
    if length >= 24:
        values = struct.unpack("<6I", payload[:24])
        return True, "HELLO ok: 状态=%d 最大块=%d 字节" % (values[0], values[5])
    return True, "HELLO ok（无 payload 细节）"


def interfaces(dev):
    cfg = dev.get_active_configuration()
    out = []
    for intf in cfg:
        eps = [(e.bEndpointAddress, e.bmAttributes & 3, e.wMaxPacketSize) for e in intf]
        out.append((intf.bInterfaceNumber, intf.bInterfaceClass, intf.bInterfaceSubClass,
                    intf.bInterfaceProtocol, eps))
    return out


def main():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        sys.exit("没找到 2c7c:0901 —— 确认设备已插在 Mac 上并且上电、固件是 QDisplay")

    print("设备: %04x:%04x  bus=%s addr=%s" % (VID, PID, dev.bus, dev.address))
    try:
        print("序列号:", usb.util.get_string(dev, dev.iSerialNumber))
    except Exception:
        pass
    print("\n接口列表（class 255 = 厂商自定义；QDC1 在其中一个上）:")
    for num, cls, sub, proto, eps in interfaces(dev):
        print("  if%-3d class=%-3d sub=%-3d proto=%-3d eps=%s" % (num, cls, sub, proto, eps))

    packet = hello_packet()
    print("\n逐个接口尝试 claim + 发 QDC1 HELLO ...")
    hits = []
    for num, cls, sub, proto, eps in interfaces(dev):
        bulks = [e for e in eps if e[1] == 2]
        if not bulks:
            print("  if%-3d 跳过（没有 bulk 端点）" % num)
            continue
        ep_out = next((e[0] for e in bulks if not (e[0] & 0x80)), None)
        ep_in = next((e[0] for e in bulks if (e[0] & 0x80)), None)
        if ep_out is None or ep_in is None:
            print("  if%-3d 跳过（bulk 端点不成对）" % num)
            continue
        try:
            usb.util.claim_interface(dev, num)
        except Exception as exc:
            print("  if%-3d claim 失败: %s" % (num, exc))
            continue
        try:
            dev.write(ep_out, packet, 2000)
            time.sleep(0.6)
            buf = b""
            try:
                buf = bytes(dev.read(ep_in, 4096, 1500))
            except Exception:
                pass
            ok, msg = parse_hello(buf)
            print("  if%-3d claim ok, ep_out=%#04x ep_in=%#04x -> %s" % (num, ep_out, ep_in, msg))
            if ok:
                hits.append(num)
        finally:
            try:
                usb.util.release_interface(dev, num)
            except Exception:
                pass

    print()
    if hits:
        print("✅ 可用接口: %s —— Mac 客户端可行（下一步：帧传输 + 渲染）" % hits)
        return 0
    print("❌ 没有接口回 QDC1 HELLO。")
    print("   常见原因：接口被内核占用（CDC-ECM if0/1）、需要 root、或数据口是 MI_20/if32")
    print("   （本项目 README §2.1 记录过 macOS 对 MI_20 的 claim 会报 EINVAL）")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

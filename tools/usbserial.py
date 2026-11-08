#!/usr/bin/env python3
"""在 macOS 上直接驱动 QuecPython 的厂商自定义串口（class 255）。

macOS 没有这些接口的驱动 → 不生成 /dev/cu.*。
但接口没被内核占用，libusb 可以 claim，然后用 bulk 端点收发。
"""
import time

import usb.core
import usb.util

VID, PID = 0x2C7C, 0x0901


def find_device():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        raise SystemExit("没找到 2c7c:0901，先确认设备插在 Mac 上并且已上电")
    return dev


def list_interfaces(dev):
    cfg = dev.get_active_configuration()
    out = []
    for intf in cfg:
        eps = [(e.bEndpointAddress, e.bmAttributes & 3, e.wMaxPacketSize) for e in intf]
        out.append((intf.bInterfaceNumber, intf.bInterfaceClass,
                    intf.bInterfaceSubClass, intf.bInterfaceProtocol, eps))
    return out


class UsbSerial:
    """把 libusb bulk 端点包装成 pyserial 风格的 read/write。"""

    def __init__(self, dev, ifnum, timeout=0.3):
        self.dev = dev
        self.ifnum = ifnum
        self.timeout_ms = int(timeout * 1000)
        try:
            usb.util.claim_interface(dev, ifnum)
        except usb.core.USBError as e:
            raise SystemExit(f"claim 接口 {ifnum} 失败: {e}")
        cfg = dev.get_active_configuration()
        intf = cfg[(ifnum, 0)]
        self.ep_out = self.ep_in = None
        for ep in intf:
            if (ep.bmAttributes & 3) != 2:      # 只看 bulk
                continue
            if ep.bEndpointAddress & 0x80:
                self.ep_in = ep.bEndpointAddress
            else:
                self.ep_out = ep.bEndpointAddress
        if self.ep_in is None or self.ep_out is None:
            raise SystemExit(f"接口 {ifnum} 没有一对 bulk 端点")

    def write(self, data):
        self.dev.write(self.ep_out, data, self.timeout_ms)
        return len(data)

    def read(self, n=4096, timeout=None):
        try:
            return bytes(self.dev.read(self.ep_in, n, int((timeout or 0.3) * 1000)))
        except usb.core.USBTimeoutError:
            return b""

    @property
    def in_waiting(self):
        return 4096

    def close(self):
        try:
            usb.util.release_interface(self.dev, self.ifnum)
        except Exception:
            pass


def probe():
    dev = find_device()
    print("设备: %04x:%04x" % (dev.idVendor, dev.idProduct))
    print("接口列表:")
    for num, cls, sub, proto, eps in list_interfaces(dev):
        print("  if%-2d class=%-3d sub=%-2d proto=%-2d eps=%s" % (num, cls, sub, proto, eps))

    print("\n逐个接口试探 REPL（发 Ctrl-C Ctrl-C 看有没有 >>>）:")
    for num, cls, sub, proto, eps in list_interfaces(dev):
        if cls != 255:
            continue
        try:
            port = UsbSerial(dev, num, timeout=0.3)
        except SystemExit as e:
            print("  if%-2d %s" % (num, e))
            continue
        try:
            port.write(b"\r\x03\x03")
            time.sleep(0.4)
            got = b""
            for _ in range(6):
                got += port.read(4096, 0.2)
            t = got.decode("latin-1", "replace").strip()
            mark = "  <<< REPL" if ">>>" in t else ""
            print("  if%-2d %-40r%s" % (num, t[:40], mark))
        finally:
            port.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(probe())

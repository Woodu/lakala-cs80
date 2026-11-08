#!/usr/bin/env python3
"""只读探测：对每个 Quectel 串口发 AT / ATI，看谁应答。

用来判断设备当前是"正常工作态"（AT 会回 OK）还是"下载态"（全都不应）。
"""
import time

import serial
import serial.tools.list_ports as lp


def probe(port, cmd, wait=0.6):
    try:
        s = serial.Serial(port, 115200, timeout=0.9)
    except Exception as e:
        return "打不开: %s" % e
    try:
        s.reset_input_buffer()
        s.write(cmd.encode() + b"\r\n")
        time.sleep(wait)
        data = s.read(512)
        return data.decode("latin-1", "replace").strip() or "(无应答)"
    finally:
        try:
            s.close()
        except Exception:
            pass


def main():
    ports = [p for p in lp.comports() if "Quectel" in (p.description or "")]
    print("找到 %d 个 Quectel 串口，逐个发 AT ...\n" % len(ports))
    hit = None
    for p in ports:
        r = probe(p.device, "AT")
        print("%-7s %-30s AT  -> %s" % (p.device, (p.description or "")[:30], r.replace("\n", " | ")))
        if "OK" in r:
            hit = p
    print()
    for p in ports:
        r = probe(p.device, "ATI")
        if "Quectel" in r or "EC600" in r or "Revision" in r:
            print("%-7s %-30s ATI -> %s" % (p.device, (p.description or "")[:30], r.replace("\n", " | ")))
            hit = hit or p

    if hit is None:
        print("没有任何端口应答 AT → 设备大概率在下载模式（BootROM 不说话）")
        return 1
    print("\n可用 AT 口: %s（%s）" % (hit.device, hit.description))
    print("→ 可以走软下载：先发 AT+QDOWNLOAD=1，设备会重新枚举成 0525:a4a7，再刷")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

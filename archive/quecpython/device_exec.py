#!/usr/bin/env python3
"""通过 QuecPython 的 REPL 串口在设备上执行代码。

    py\\python.exe device_exec.py --probe          # 自动找 REPL 口并打印环境信息
    py\\python.exe device_exec.py -p COM11 -f x.py # 执行文件里的代码
    py\\python.exe device_exec.py -p COM11 -c "print(1+1)"

原理：MicroPython 的 raw REPL（Ctrl-A），不需要 QPYcom。
"""
import argparse
import time

import serial
import serial.tools.list_ports as lp

BAUD = 115200


def open_port(dev):
    s = serial.Serial(dev, BAUD, timeout=0.3, write_timeout=2.0)
    try:
        s.dtr = True
        s.rts = True
    except Exception:
        pass
    return s


def drain(s, quiet=0.25):
    buf = b""
    last = time.time()
    while time.time() - last < quiet:
        n = s.in_waiting
        if n:
            buf += s.read(n)
            last = time.time()
        else:
            time.sleep(0.02)
    return buf


def enter_raw(s):
    s.write(b"\r\x03\x03")
    time.sleep(0.4)
    drain(s)
    s.write(b"\x01")          # Ctrl-A: raw REPL
    time.sleep(0.3)
    return drain(s)


def run_code(s, code, timeout=15.0):
    enter_raw(s)
    s.write(code.encode() + b"\x04")   # Ctrl-D: end of input -> execute
    buf = b""
    t0 = time.time()
    while time.time() - t0 < timeout:
        chunk = s.read(s.in_waiting or 1)
        if chunk:
            buf += chunk
        if buf.count(b"\x04>") >= 1:
            break
    s.write(b"\x02")          # Ctrl-B: 退出 raw REPL
    time.sleep(0.1)
    txt = buf.decode("latin-1", "replace")
    # 去掉 raw REPL 的 "OK" 与结尾标记
    if txt.startswith("raw REPL"):
        txt = txt.split(">", 1)[-1]
    txt = txt.replace("\x04>", "").replace("OK", "", 1).strip("\r\n")
    return txt


def find_repl():
    """按优先级找 REPL 口：AP Log > AT > 其它 Quectel。"""
    q = [p for p in lp.comports() if "Quectel" in (p.description or "")]
    def rank(p):
        d = p.description or ""
        return 0 if "AP Log" in d else (1 if "AT" in d else 2)
    ports = sorted(q, key=rank)
    for p in ports:
        try:
            s = open_port(p.device)
        except Exception:
            continue
        try:
            out = enter_raw(s)
            if b">" in out or b"raw REPL" in out:
                r = run_code(s, "print('PROBE_OK')")
                if "PROBE_OK" in r:
                    print("REPL 口: %s (%s)" % (p.device, p.description))
                    return p.device
        except Exception as e:
            print("  %-7s %-32s %s" % (p.device, (p.description or "")[:32], type(e).__name__))
        finally:
            try:
                s.close()
            except Exception:
                pass
    return None


PROBE = """\
import sys
print('VER', sys.version)
try:
    import uos as os
except Exception as e:
    print('uos fail', e)
    os = None
if os:
    print('ROOT', os.listdir('/'))
    try: print('USR', os.listdir('/usr'))
    except Exception as e: print('USR fail', e)
try:
    import lcd
    print('LCD-MOD', [a for a in dir(lcd) if not a.startswith('_')])
except Exception as e:
    print('LCD-MOD fail', e)
try:
    from machine import LCD, Pin
    o = LCD()
    print('LCD-OBJ', [a for a in dir(o) if not a.startswith('_')])
    print('PIN-GPIO', [a for a in dir(Pin) if a.startswith('GPIO')][:12])
    print('PIN-BL', [a for a in dir(Pin) if 'LCD' in a.upper()][:12])
    print('PIN-ALL', [a for a in dir(Pin) if not a.startswith('_')][:40])
except Exception as e:
    print('machine fail', e)
try:
    import utime
    print('UTIME ok')
except Exception as e:
    print('utime fail', e)
try:
    import USBNET
    print('USBNET', [a for a in dir(USBNET) if not a.startswith('_')])
except Exception as e:
    print('USBNET fail', e)
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-p", "--port")
    ap.add_argument("-f", "--file")
    ap.add_argument("-c", "--code")
    ap.add_argument("--probe", action="store_true")
    a = ap.parse_args()

    port = a.port or find_repl()
    if not port:
        print("✗ 没找到 REPL 口")
        return 1
    s = open_port(port)
    try:
        code = PROBE if a.probe else (open(a.file, encoding="utf-8").read()
                                      if a.file else a.code)
        if code is None:
            print("要么 --probe，要么 -f / -c")
            return 1
        print(run_code(s, code, timeout=25.0))
    finally:
        s.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

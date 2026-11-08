#!/usr/bin/env python3
"""生成 CS80 面板初始化数据（社区逆向出来的原厂 R05 序列）。

编码格式（QuecPython mipi_init 的 initbuf）：命令, u8 延时ms, u8 数据长度, 数据...
输出：panel_init.py（被 app_test.py 引用）与 app_test.py（设备端点亮测试）
"""
import textwrap

# 原厂 R05 主屏初始化表： (命令, 数据hex, 延时ms)
SEQ = [
    ("B9", "F1 12 84", 0),
    ("BA", "31 81 05 F9 0E 0E 02 00 00 00 00 00 00 00 44 25 00 91 0A 00 00 00 4F 01 00 00 37", 0),
    ("B8", "A8", 0),
    ("B3", "00 00 00 00 07 0B 1E 1E", 0),
    ("C0", "73 73 50 50 80 00 08 70 00", 0),
    ("CC", "0B", 0),
    ("BF", "02 10 82", 0),
    ("BC", "46", 0),
    ("B4", "80", 0),
    ("B2", "40 08", 0),
    ("E3", "03 03 00 00 03 03 00 00 00 C0", 0),
    ("B1", "43 54 23 1E 1E 22 77 04 DB 4C", 0),
    ("B5", "0A 0A", 0),
    ("B6", "44 44", 0),
    ("E9", "02 00 04 05 0F 80 81 12 31 23 4F 06 80 28 47 08 00 00 0F 00 00 00 00 00 0F 00 00 00 "
           "11 44 FF 88 8B BA A3 31 17 75 58 00 44 FF 88 8B BA A2 20 06 64 48 00 00 00 00 00 00 "
           "00 00 00 00 00 00 00", 0),
    ("EA", "96 12 00 00 00 70 00 00 00 00 00 00 11 44 88 FF 8B BA A4 46 60 02 28 00 44 88 FF 8B BA "
           "A5 57 71 13 38 23 10 00 00 01 40 00 00 00 00 00 00 00 00 00 00 00 00 00 00 40 80 81 00 "
           "00 00 00 00 00", 0),
    ("E0", "01 13 19 27 2F 39 4A 3C 07 0C 0E 13 14 12 13 11 1A 01 13 19 27 2F 39 4A 3C 07 0C 0E 13 "
           "14 12 13 11 1A", 0),
    ("11", "", 250),
    ("29", "", 50),
]

W, H = 800, 1280


def build():
    out = bytearray()
    for cmd, data, delay in SEQ:
        payload = bytes(int(x, 16) for x in data.split()) if data.strip() else b""
        assert len(payload) <= 255, (cmd, len(payload))
        out += bytes([int(cmd, 16), delay, len(payload)]) + payload
    return bytes(out)


def main():
    raw = build()
    print("initbuf 长度: %d 字节，共 %d 条命令" % (len(raw), len(SEQ)))

    with open("panel_init.py", "w") as f:
        f.write("INIT = bytearray(b'%s')\n" % "".join("\\x%02x" % b for b in raw))
    print("已写 panel_init.py")

    app = '''\
"""CS80 点屏测试：背光 → MIPI 初始化 → 三色轮播，验证面板点亮。

部署：把本文件和 panel_init.py 一起送到设备，然后 import app_test
"""
import utime
from machine import LCD, Pin

try:
    from panel_init import INIT
except ImportError:
    raise OSError('缺少 panel_init.py')

WIDTH, HIGHT = 800, 1280

# 背光：社区文档 = 物理 118 = 公开 GPIO8 = 硬件 GPIO7
BACKLIGHT_CANDIDATES = ('GPIO8', 8, 7)


def _pin(name):
    try:
        return getattr(Pin, name) if isinstance(name, str) else name
    except AttributeError:
        return None


def backlight(on=1):
    last = None
    for cand in BACKLIGHT_CANDIDATES:
        n = _pin(cand)
        if n is None:
            continue
        try:
            Pin(n, Pin.OUT, Pin.PULL_DISABLE, on)
            print('backlight on pin', cand, '=', on)
            return True
        except Exception as e:
            last = e
    print('backlight failed:', last)
    return False


def init_panel():
    lcd = LCD()
    # 主屏辅助控制脚（社区：物理 53 = GPIO27，原厂初始化输出低）
    for cand in ('GPIO27', 27):
        n = _pin(cand)
        if n is None:
            continue
        try:
            Pin(n, Pin.OUT, Pin.PULL_DISABLE, 0)
            print('aux pin low:', cand)
            break
        except Exception:
            pass

    r = lcd.mipi_init(INIT,
                      width=WIDTH, hight=HIGHT, bpp=16,
                      DataLane=2, MipiMode=0, PixelFormat=0, DsiFormat=0,
                      TransMode=2, RgbOrder=0, BllpEnable=True,
                      HSync=20, HBP=40, HFP=40,
                      VSync=3, VBP=11, VFP=16,
                      FrameRate=60, TESel=False, RstPolarity=1)
    print('mipi_init ->', r)
    return lcd


def main():
    lcd = init_panel()
    backlight(1)
    utime.sleep_ms(300)
    for name, color in (('red', '0xF800'), ('green', '0x07E0'), ('blue', '0x001F'),
                        ('white', '0xFFFF')):
        print('clear', name)
        try:
            lcd.lcd_clear(color)
        except Exception as e:
            print('clear fail', e)
        utime.sleep_ms(800)
    print('done')


if __name__ == '__main__':
    main()
'''
    with open("app_test.py", "w") as f:
        f.write(app)
    print("已写 app_test.py")


if __name__ == "__main__":
    main()

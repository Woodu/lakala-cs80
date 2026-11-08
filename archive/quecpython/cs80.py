"""CS80 屏幕控制（设备端）。背光 + MIPI 初始化 + 显示。"""

import utime
from machine import LCD, Pin

WIDTH, HIGHT = 800, 1280

try:
    from panel_init import INIT
except ImportError:
    INIT = None

_lcd = None


def _g(name):
    try:
        return getattr(Pin, name)
    except AttributeError:
        return None


def backlight(on=1):
    for cand in ('GPIO8', 8, 7):
        n = _g(cand) if isinstance(cand, str) else cand
        if n is None:
            continue
        try:
            Pin(n, Pin.OUT, Pin.PULL_DISABLE, on)
            print('backlight: pin', cand, '=', on)
            return True
        except Exception as e:
            print('backlight try', cand, 'fail', e)
    return False


def init_panel():
    global _lcd
    if _lcd is not None:
        return _lcd
    if INIT is None:
        raise OSError('missing panel_init.py')

    for cand in ('GPIO27', 27):
        n = _g(cand) if isinstance(cand, str) else cand
        if n is None:
            continue
        try:
            Pin(n, Pin.OUT, Pin.PULL_DISABLE, 0)
            print('aux pin low:', cand)
            break
        except Exception as e:
            print('aux pin', cand, 'fail', e)

    lcd = LCD()
    r = lcd.mipi_init(INIT,
                      width=WIDTH, hight=HIGHT, bpp=16,
                      DataLane=2, MipiMode=0, PixelFormat=0, DsiFormat=0,
                      TransMode=2, RgbOrder=0, BllpEnable=True,
                      HSync=20, HBP=40, HFP=40,
                      VSync=3, VBP=11, VFP=16,
                      FrameRate=60, TESel=False, RstPolarity=1)
    print('mipi_init ->', r)
    _lcd = lcd
    return lcd


def _color(c):
    if isinstance(c, str):
        return int(c, 16)
    return c


def clear(color=0xFFFF):
    return init_panel().lcd_clear(_color(color))


def show(path):
    return init_panel().lcd_show(path, 0, 0, WIDTH, HIGHT)


def selftest():
    init_panel()
    backlight(1)
    utime.sleep_ms(400)
    for name, c in (('red', 0xF800), ('green', 0x07E0),
                    ('blue', 0x001F), ('white', 0xFFFF)):
        print('clear', name)
        try:
            clear(c)
        except Exception as e:
            print('clear fail', e)
        utime.sleep_ms(900)
    print('selftest done')

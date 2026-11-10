#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CS80 状态屏 host：独占 USB 的常驻进程 + 本地 HTTP（画布编辑器 / 数据源）。

    python3 panel/host.py            # 然后浏览器打开 http://127.0.0.1:8787
    python3 panel/host.py --port 9000

职责边界：
  - 唯一持有 panel/qdc1 sidecar（USB 独占），把帧/局部更新/控屏转给它
  - 存布局 ~/.cs80_panel/layout.json
  - 抓数据（系统指标 / agent 状态 / 天气 / 股票），带缓存，组件只读缓存
不负责画像素：像素由浏览器画布算出来再 POST 进来（路线 A：一份渲染代码即编辑器又是设备像素源）。
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "web")
SIDECAR = os.path.join(HERE, "qdc1.exe" if os.name == "nt" else "qdc1")
LAYOUT = os.path.join(os.path.expanduser("~"), ".cs80_panel", "layout.json")
STATE_FILE = os.path.join(os.path.expanduser("~"), "cs80_display", "state.json")
CONF_DIR = os.path.join(os.path.expanduser("~"), ".cs80_panel")  # 跨平台：日志/配置都放这儿
SIDECAR_LOG = os.path.join(CONF_DIR, "sidecar.log")
WEB_LOG = os.path.join(CONF_DIR, "web.log")
# 连上设备时把亮度拉回这个值（0 = 不动）。固件默认 100、PWM 全停；但 +/- 键或
# 上位机设过的低亮度会留在固件 RAM 里，断开主机后固件自己画的等待页仍在 1 kHz 软件
# PWM 下微闪 —— 所以每次连上都重置一次。用户在本进程里手动设过亮度就不再覆盖。
# 亮度策略：**默认不自动设**（以前连上就拉回 100，会把用户手动调的 50% 几十秒后又刷回去）。
# 现在改成“记住用户选的值”：见 Device._bright_state()。
#   CS80_BRIGHTNESS=100  → 强制锁定到 100（老行为，想要 PWM 完全不停时用）
BRIGHTNESS_PIN = int(os.environ.get("CS80_BRIGHTNESS", "0") or 0)

GRID_PAGE = {
    "id": "main",
    "name": "主页",
    "type": "grid",
    "widgets": [
        # 左上：3×2 时钟（右上角待带天气，城市 北京）
        {"id": "clock", "type": "clock_dial", "cell": [0, 0, 3, 2],
         "config": {"city": "北京", "showWeather": True}},
        # 右上：2×2 RunCat
        {"id": "runcat", "type": "runcat", "cell": [3, 0, 2, 2], "config": {}},
        # 中间两行：5×2 自选股（新版多股组件，一行一只）
        {"id": "stock", "type": "stock", "cell": [0, 2, 5, 2],
         "config": {"symbols": "sz002654,sh600641", "title": "自选股"}},
        # 剩下的 5×4 给 Agent
        {"id": "agent", "type": "agent", "cell": [0, 4, 5, 4], "config": {}},
    ],
}

DEFAULT_LAYOUT = {
    "version": 2,
    "theme": {"bg": "#141519", "card": "#1a1c22", "line": "#2a2c34"},
    "activePage": "main",
    "pages": [
        GRID_PAGE,
        {
            "id": "stockpage",
            "name": "股票自选",
            "type": "custom",
            "module": "pages/stock.js",
            "config": {"symbols": "sh000001,sz399001,sh600519", "title": "自选股"},
        },
    ],
}


def migrate_layout(layout):
    """v1（顶层 widgets）→ v2（pages）。idempotent。"""
    if not layout:
        return json.loads(json.dumps(DEFAULT_LAYOUT))
    if layout.get("version") == 2 and isinstance(layout.get("pages"), list) and layout["pages"]:
        pass
    elif isinstance(layout.get("widgets"), list):
        layout = {
            "version": 2,
            "theme": layout.get("theme") or {},
            "activePage": "main",
            "pages": [{"id": "main", "name": "主页", "type": "grid", "widgets": layout["widgets"]}],
        }
    else:
        return json.loads(json.dumps(DEFAULT_LAYOUT))
    pages = []
    for i, page in enumerate(layout["pages"]):
        p = dict(page)
        p.setdefault("id", "page%d" % i)
        p.setdefault("name", p["id"])
        p.setdefault("type", "grid")
        if p["type"] == "grid":
            p["widgets"] = p.get("widgets") or []
        else:
            p.setdefault("module", "pages/stock.js")
            p.setdefault("config", {})
        pages.append(p)
    if not any(p["type"] == "custom" for p in pages):
        pages.append(json.loads(json.dumps(DEFAULT_LAYOUT["pages"][1])))  # 默认带一个自定义股票页
    layout["pages"] = pages
    ids = [p["id"] for p in pages]
    if layout.get("activePage") not in ids:
        layout["activePage"] = ids[0]
    return layout


# ---------------- 屏保 ----------------
SCREENSAVER_CFG = os.path.join(os.path.expanduser("~"), ".cs80_panel", "screensaver.json")
MEDIA_DIRS = [os.path.join(HERE, "web", "media")]
for _d in (("Videos",) if os.name == "nt" else ("Movies",)):
    MEDIA_DIRS.append(os.path.join(os.path.expanduser("~"), _d, "cs80-screensaver"))
MEDIA_DIRS.append(os.path.join(os.path.expanduser("~"), "Movies", "cs80-screensaver"))  # mac 习惯目录也认
SCREEN_STATE = {"locked": False, "asleep": False, "at": 0.0}
SAVER = None


def _windows_locked():
    """Windows：锁屏/UAC 时输入桌面变成 secure desktop → OpenInputDesktop 失败。"""
    try:
        import ctypes

        u32 = ctypes.windll.user32
        u32.OpenInputDesktop.restype = ctypes.c_void_p
        u32.OpenInputDesktop.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        u32.CloseDesktop.argtypes = [ctypes.c_void_p]
        h = u32.OpenInputDesktop(0, False, 0x0100)  # DESKTOP_READOBJECTS
        if not h:
            return True
        u32.CloseDesktop(h)
        return False
    except Exception:
        return False


def screen_locked():
    """锁屏判定：macOS 用 CGSessionCopyCurrentDictionary；Windows 用 OpenInputDesktop。"""
    if os.name == "nt":
        return _windows_locked()
    if sys.platform != "darwin":
        return False
    try:
        import ctypes
        import ctypes.util

        cg = ctypes.CDLL(ctypes.util.find_library("CoreGraphics"))
        cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
        cg.CGSessionCopyCurrentDictionary.restype = ctypes.c_void_p
        ref = cg.CGSessionCopyCurrentDictionary()
        if not ref:
            return False
        cf.CFDictionaryGetValue.restype = ctypes.c_void_p
        cf.CFDictionaryGetValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        cf.CFBooleanGetValue.argtypes = [ctypes.c_void_p]
        key = cf.CFStringCreateWithCString(None, b"CGSSessionScreenIsLocked", 0x08000100)
        val = cf.CFDictionaryGetValue(ref, key)
        locked = bool(cf.CFBooleanGetValue(val)) if val else False
        cf.CFRelease(ctypes.c_void_p(ref))
        cf.CFRelease(ctypes.c_void_p(key))
        return locked
    except Exception:
        return False


def display_asleep():
    """显示器是否休眠（拿不到就当作没睡，宁可不触发屏保）"""
    if os.name == "nt":
        return False  # Windows 没有便宜可靠的接口，锁屏（screen_locked）已经够用了
    try:
        out = subprocess.check_output(["pmset", "-g", "powerstate", "IODisplayWrangler"], text=True, timeout=4)
        return " 1" in out.split("\n")[-2]
    except Exception:
        return False


def _screen_watch():
    while True:
        locked = screen_locked()
        SCREEN_STATE.update(locked=locked, asleep=display_asleep(), at=time.time())
        if SAVER:
            cfg = load_saver_cfg()
            if locked and cfg.get("autoLock", True) and not SAVER.playing and cfg.get("file"):
                SAVER.start(cfg["file"], by_lock=True)
            elif not locked and SAVER.playing and SAVER.by_lock:
                SAVER.stop()  # 只停「锁屏自动起的」，手动播的不动
        time.sleep(2)


class Saver:
    """屏保：host 侧用 ffmpeg 解码成 BGR565 帧，直接写进 sidecar。

    为什么不放在浏览器里播：锁屏时 WebView 被遮挡，页面进入 hidden，
    setInterval 被节流到 ~1s、视频解码也降频 → 屏上就卡成幻灯片。
    ffmpeg 在 host 侧解码完全不受窗口可见性影响。
    """

    def __init__(self, device):
        self.device = device
        self.proc = None
        self.thread = None
        self.file = ""
        self.frames = 0
        self.error = ""
        self.lock = threading.Lock()
        self.last_at = 0.0
        self.by_lock = False  # 是锁屏自动起的还是手动起的

    @property
    def playing(self):
        return self.proc is not None and self.proc.poll() is None

    def _path(self, name):
        for d in MEDIA_DIRS:
            p = os.path.join(d, name)
            if os.path.isfile(p):
                return p
        return ""

    def start(self, name, fps=12, by_lock=False):
        path = self._path(name)
        if not path:
            self.error = "找不到视频：%s" % name
            return False
        if not shutil.which("ffmpeg"):
            self.error = ("没有 ffmpeg（Windows: winget install Gyan.FFmpeg"
                          if os.name == "nt" else "没有 ffmpeg（brew install ffmpeg）")
            return False
        self.stop()
        cmd = [
            "ffmpeg", "-v", "error", "-re", "-stream_loop", "-1", "-i", path,
            "-f", "rawvideo", "-pix_fmt", "bgr565le", "-s", "800x1280", "-r", str(fps), "-",
        ]
        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        except Exception as exc:
            self.error = str(exc)
            return False
        self.file = name
        self.frames = 0
        self.error = ""
        self.by_lock = by_lock
        self.thread = threading.Thread(target=self._pump, args=(self.proc,), daemon=True)
        self.thread.start()
        return True

    def _pump(self, proc):
        frame = 2048000

        def read_exact(n):
            chunks = []
            got = 0
            while got < n:
                chunk = proc.stdout.read(n - got)  # 管道 read 可能只给一部分，必须凑满
                if not chunk:
                    return None
                chunks.append(chunk)
                got += len(chunk)
            return b"".join(chunks)

        while proc.poll() is None:
            buf = read_exact(frame)
            if buf is None:
                break
            if not self.device.send(b"\x04" + buf):
                time.sleep(0.2)  # 设备没连上就慢一点，别空转
                continue
            self.frames += 1
            self.last_at = time.time()
        rc = proc.poll()
        if rc not in (0, None, -9) and not self.error:
            self.error = "ffmpeg 退出码 %s" % rc
        try:
            proc.stdout.close()
        except Exception:
            pass

    def stop(self):
        self.error = ""  # 主动停止不算错误
        proc, self.proc = self.proc, None
        if proc and proc.poll() is None:
            proc.kill()
            try:
                proc.wait(timeout=2)
            except Exception:
                pass
        self.file = ""

    def status(self):
        return {
            "playing": self.playing,
            "file": self.file,
            "frames": self.frames,
            "fps": 12 if self.playing else 0,
            "error": self.error,
        }


def screensaver_files():
    files = []
    for d in MEDIA_DIRS:
        if not os.path.isdir(d):
            continue
        for n in sorted(os.listdir(d)):
            if n.lower().endswith((".mp4", ".webm", ".m4v", ".mov")):
                path = os.path.join(d, n)
                files.append({"name": n, "size": os.path.getsize(path), "source": os.path.basename(d)})
    return files


def load_config():
    """~/.cs80_panel/config.json：目前只存亮度偏好，以后可放其它全局设置。"""
    try:
        with open(os.path.join(CONF_DIR, "config.json"), encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def save_config(cfg):
    try:
        os.makedirs(CONF_DIR, exist_ok=True)
        path = os.path.join(CONF_DIR, "config.json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except Exception:
        pass


def load_saver_cfg():
    try:
        with open(SCREENSAVER_CFG, encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    files = screensaver_files()
    names = [f["name"] for f in files]
    cfg.setdefault("autoLock", True)
    if cfg.get("file") not in names:
        cfg["file"] = names[0] if names else ""
    return cfg


def save_saver_cfg(cfg):
    os.makedirs(os.path.dirname(SCREENSAVER_CFG), exist_ok=True)
    with open(SCREENSAVER_CFG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)

# ---------------- 静态资产版本 ----------------
_WEB_BUILD = {"at": 0.0, "id": ""}


def web_build():
    """web/ 下所有文件的 mtime 汇总成一个版本号：改了前端，页面自己会重载。"""
    now = time.time()
    if now - _WEB_BUILD["at"] < 2 and _WEB_BUILD["id"]:
        return _WEB_BUILD["id"]
    stamp = 0.0
    for dirpath, _dirs, names in os.walk(WEB):
        for n in names:
            try:
                stamp = max(stamp, os.path.getmtime(os.path.join(dirpath, n)))
            except OSError:
                pass
    _WEB_BUILD.update(at=now, id=("%.0f" % (stamp * 1000)))
    return _WEB_BUILD["id"]


# ---------------- sidecar ----------------
# ---------------- sidecar ----------------
def _active_coms():
    """HKLM\\HARDWARE\\DEVICEMAP\\SERIALCOMM：当前真正在场的串口名（COM19 之类）。"""
    try:
        import winreg
    except Exception:
        return set()
    out = set()
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DEVICEMAP\SERIALCOMM") as k:
            for i in range(256):
                try:
                    _name, val, _t = winreg.EnumValue(k, i)
                except OSError:
                    break
                if isinstance(val, str) and val.upper().startswith("COM"):
                    out.add(val.upper())
    except OSError:
        pass
    return out


def _windows_qdc1_com():
    """Windows：QDC1 数据口是复合设备的 **MI_20** 接口（= USB interface 32）。

    注册表 `Enum\\USB\\VID_2C7C&PID_0901&MI_20\\<实例>\\Device Parameters\\PortName` 里有它的口名，
    但**拔插 / 换 USB 口之后会留下旧实例**（明明已经变成 COM22，注册表还写着 COM15）。
    所以只认「同时出现在 SERIALCOMM（当前在场串口）里」的那个；都不匹配就返回空，
    由 `_probe_qdc1()` 用 sidecar 的 `--probe` 真正问一遍。
    """
    try:
        import winreg
    except Exception:
        return ""
    active = _active_coms()
    base = r"SYSTEM\CurrentControlSet\Enum\USB\VID_2C7C&PID_0901&MI_20"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as k:
            for i in range(16):
                try:
                    inst = winreg.EnumKey(k, i)
                except OSError:
                    break
                try:
                    with winreg.OpenKey(k, inst + r"\Device Parameters") as dp:
                        port = str(winreg.QueryValueEx(dp, "PortName")[0] or "")
                except OSError:
                    continue
                if port and (not active or port.upper() in active):
                    return port
    except OSError:
        pass
    return ""


def _probe_qdc1():
    """兜底：让 sidecar 自带探针逐个 COM 发 HELLO，返回真应答的那个口。"""
    try:
        out = subprocess.run([SIDECAR, "--probe"], capture_output=True, text=True, timeout=120)
    except Exception:
        return ""
    for line in (out.stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if ev.get("qdc1"):
            return str(ev.get("com") or "")
    return ""


class Device:
    """独占 USB 的 sidecar 进程 + 自愈：进程死了/链路死了都自动重启并重新枚举设备。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.session = 0
        self.proc = None
        self.status = {"device": "connecting"}
        self.last = None
        self.last_event = 0.0
        self.user_brightness = None  # 本进程里用户手动设过亮度
        self.bright_pref = load_config().get("brightness")  # 用户偏好（持久化到 ~/.cs80_panel/config.json）
        self.bright_expect = None    # 我们刚发出去、还没确认的值
        self.need_restore = False    # 刚(重)连上：第一条 state 用来“恢复偏好”，不拿来学习
        self.com = ""  # Windows：QDC1 数据串口（自动从注册表识别）
        threading.Thread(target=self._supervise, daemon=True).start()
        threading.Thread(target=self._poll_state, daemon=True).start()

    def _spawn(self):
        # 同时只能有一个 sidecar 持有 USB：先确认旧的已经退出，否则 Windows 上两个句柄
        # 会同时打开同一个 CDC 口 → 回包被抢走，看起来就是“时通时不通/卡住”。
        old = self.proc
        if old is not None and old.poll() is None:
            try:
                old.kill()
                old.wait(timeout=5)
            except Exception:
                pass
        self.session += 1
        os.makedirs(CONF_DIR, exist_ok=True)
        err = open(SIDECAR_LOG, "ab")
        cmd = [SIDECAR]
        if os.name == "nt":
            com = os.environ.get("CS80_COM") or self.com or _windows_qdc1_com()
            if not com:
                com = _probe_qdc1()
            if not com:
                err.close()
                self.status = {"device": "down", "reason": "no-qdc1-port"}
                return False
            if com != self.com:
                print("CS80: 自动识别 QDC1 串口 = %s" % com, flush=True)
            self.com = com
            cmd += ["--com", com]
        try:
            self.proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err, bufsize=0
            )
        except FileNotFoundError:
            self.status = {"device": "no-sidecar"}
            return False
        finally:
            err.close()
        self.status = {"device": "connecting"}
        self.last_event = time.time()
        threading.Thread(target=self._reader, args=(self.proc,), daemon=True).start()
        return True

    def _reader(self, proc):
        for line in proc.stdout:
            try:
                ev = json.loads(line)
            except Exception:
                continue
            self.last = ev
            self.last_event = time.time()
            name = ev.get("event")
            if name == "ready":
                self.status = dict(self.status, device="up", ready=ev)
                if BRIGHTNESS_PIN:
                    self.bright_expect = BRIGHTNESS_PIN
                    self.control(1, BRIGHTNESS_PIN)   # 强制模式（CS80_BRIGHTNESS=xxx）
                else:
                    # 不盲设，先读一次状态：拿它决定“要不要把用户偏好恢复回去”
                    self.need_restore = True
                    self.control(4)
            elif name == "features":
                self.status = dict(self.status, features=ev)
            elif name == "lost":
                self.status = dict(self.status, device="down", reason=ev.get("reason"))
            elif name == "exit":
                self.status = dict(self.status, device="down")
            elif name in ("frame", "patch"):
                self.status = dict(
                    self.status, last=name, wait_ms=ev.get("ms"), wire=ev.get("wire"), ok=ev.get("ok"), code=ev.get("code")
                )
            elif name in ("brightness", "power", "state"):
                self.status = dict(self.status, **{name: {k: v for k, v in ev.items() if k != "event"}})
                if name == "state":
                    self._bright_state(ev.get("brightness"))
        proc.wait()
        if self.proc is proc:
            self.proc = None
            self.status = dict(self.status, device="down")

    # ---- 亮度：记住用户选的值，不自己改 ----
    def _set_bright(self, value):
        """主动设亮度（用户操作 / 连上后恢复偏好）。记下期望值供回读确认，并持久化。"""
        cfg = load_config()
        self.bright_pref = value
        self.bright_expect = value
        if cfg.get("brightness") != value:
            cfg["brightness"] = value
            save_config(cfg)
        self.control(1, value)

    def _bright_state(self, cur):
        """收到 HELLO flags=10（亮度/背光/edges）时决定要不要动亮度。

        * 强制模式（`CS80_BRIGHTNESS=N`）：一直拉回 N
        * 刚（重）连上：固件会回到默认 100 → 把用户偏好恢复回去
        * 稳定运行中：设备上的 +/- 键改了亮度 → 以设备为准，学下来并持久化，**不跟用户抢**
        这样“手动调到 50%”不会再几十秒后被刷回 100%，而断电重连后也能回到 50%。
        """
        if not isinstance(cur, int):
            return
        if BRIGHTNESS_PIN:
            if cur != BRIGHTNESS_PIN:
                self.bright_expect = BRIGHTNESS_PIN
                self.control(1, BRIGHTNESS_PIN)
            return
        if self.need_restore:
            self.need_restore = False
            if self.bright_pref is not None and cur != self.bright_pref:
                self._set_bright(self.bright_pref)
            return
        if self.bright_expect is not None:
            self.bright_expect = None
            if cur != self.bright_pref:
                self._set_bright(cur)   # 设的瞬间又被按键改了 → 听用户的
            return
        if self.bright_pref is None or cur != self.bright_pref:
            self._set_bright(cur)       # 首次运行 / 设备上按了 +/-：学下来

    def _supervise(self):
        backoff = 1.0
        while True:
            proc = self.proc
            if proc is None or proc.poll() is not None:
                if not self._spawn():
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 5.0)
                else:
                    # 等它握手成功（ready）或直接退出；没 ready 就退避重试，避免疯狂重枚举 USB
                    deadline = time.time() + 3
                    while time.time() < deadline and self.proc is not None and self.status.get("device") != "up":
                        time.sleep(0.1)
                    if self.status.get("device") == "up":
                        backoff = 1.0
                    else:
                        self.status = dict(self.status, device="down", reason="open-failed")
                        if self.proc is not None:
                            self.proc.kill()
                        time.sleep(backoff)
                        backoff = min(backoff * 2, 5.0)
            elif time.time() - self.last_event > 10:
                # 进程活着但 10 秒没有任何事件（正常保活 1.5s 一条）→ 链路已死，杀掉重启
                self.status = dict(self.status, device="down", reason="stalled")
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except Exception:
                    pass
            time.sleep(0.5)

    def _poll_state(self):
        """定期回读设备亮度/待机状态，顺带给 UI 用（顺带当心跳）。"""
        time.sleep(2)
        while True:
            self.control(4)
            time.sleep(10)

    def reconnect(self):
        proc = self.proc
        if proc and proc.poll() is None:
            proc.kill()
        return True

    def send(self, payload):
        with self.lock:
            proc = self.proc
            if not proc or proc.poll() is not None or not proc.stdin:
                return False
            try:
                proc.stdin.write(payload)
                proc.stdin.flush()
                return True
            except (BrokenPipeError, OSError):
                self.status = dict(self.status, device="down")
                return False

    def control(self, cmd, arg=0):
        return self.send(b"\x03" + bytes([cmd, arg]))


# ---------------- 数据源 ----------------
class Cache:
    def __init__(self):
        self.lock = threading.Lock()
        self.items = {}

    def get(self, key, ttl, fn):
        now = time.time()
        with self.lock:
            hit = self.items.get(key)
            if hit and now - hit[0] < ttl:
                return hit[1]
        try:
            val = fn()
        except Exception as exc:  # 抓不到就退回占位/上次值，不让屏空白
            with self.lock:
                hit = self.items.get(key)
            return (hit[1] if hit else None) or {"error": str(exc), "stale": True}
        with self.lock:
            self.items[key] = (now, val)
        return val


CACHE = Cache()


def http_json(url, headers=None, timeout=6):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "cs80-panel/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _shortcut_weather(city):
    """可选：让用户自己建一个快捷指令（Shortcuts）读系统天气，输出 JSON。
    约定：shortcuts run "$CS80_WEATHER_SHORTCUT" 的文本输出为
    {"temp":16,"low":15,"high":19,"code":3,"city":"上海"}
    """
    name = os.environ.get("CS80_WEATHER_SHORTCUT")
    if not name:
        raise RuntimeError("未配置快捷指令")
    out = subprocess.run(["shortcuts", "run", name], capture_output=True, text=True, timeout=20)
    text = (out.stdout or "").strip()
    if not text:
        raise RuntimeError("快捷指令没有输出")
    data = json.loads(text)
    data.setdefault("city", city)
    data["source"] = "shortcut"
    return data


def weather(city):
    if os.environ.get("CS80_WEATHER_SHORTCUT"):
        try:
            return _shortcut_weather(city)
        except Exception:
            pass  # 快捷指令没配好就回退到 open-meteo
    geo = http_json("https://geocoding-api.open-meteo.com/v1/search?count=1&language=zh&name=" + urllib.parse.quote(city))
    hits = geo.get("results") or []
    if not hits:
        return {"city": city, "error": "找不到城市", "stale": True}
    g = hits[0]
    wx = http_json(
        "https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s&current=temperature_2m,relative_humidity_2m,"
        "weather_code,wind_speed_10m&daily=temperature_2m_max,temperature_2m_min&timezone=auto&forecast_days=1"
        % (g["latitude"], g["longitude"])
    )
    cur, day = wx.get("current", {}), wx.get("daily", {})
    return {
        "city": g.get("name") or city,
        "temp": cur.get("temperature_2m"),
        "humidity": cur.get("relative_humidity_2m"),
        "wind": cur.get("wind_speed_10m"),
        "code": cur.get("weather_code"),
        "high": (day.get("temperature_2m_max") or [None])[0],
        "low": (day.get("temperature_2m_min") or [None])[0],
        "source": "open-meteo",
    }


# ---------- 行情数据源 ----------
# 优先用 efinance / akshare（装了才走，字段更全）；都没装就用东财/新浪的 HTTP 接口
# （efinance 本身也是包这两个接口，所以结果一致，只是没有 pandas 那层便利）。
def _opt(mod):
    try:
        return __import__(mod)
    except Exception:
        return None


def _secid(symbol):
    """sh000001 -> 1.000001（东财 secid 格式）"""
    t = str(symbol).lower().replace(".", "")
    if t.startswith("sh"):
        return "1." + t[2:]
    if t.startswith("sz"):
        return "0." + t[2:]
    if t.startswith("bj"):
        return "0." + t[2:]
    if t.isdigit() and len(t) == 6:
        return ("1." if t[0] in "56" else "0.") + t
    raise RuntimeError("代码格式：sh000001 / sz399001 / 600519")


def _plain(symbol):
    return str(symbol).lower().replace("sh", "").replace("sz", "").replace("bj", "").replace(".", "")


def _sina_quote(symbol):
    url = "https://hq.sinajs.cn/list=" + urllib.parse.quote(symbol)
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}
    )
    with urllib.request.urlopen(req, timeout=6) as resp:
        text = resp.read().decode("gbk", "replace")
    m = re.search(r'="([^"]*)"', text)
    if not m or not m.group(1):
        raise RuntimeError("无数据（需要有效代码，如 sh000001 / sz399001 / sh600519）")
    f = m.group(1).split(",")
    if symbol.startswith(("sh000", "sz399", "sh950")):
        name, price, prev = f[0], float(f[1]), float(f[2])
    else:
        name, price, prev = f[0], float(f[3]), float(f[2])
    return {
        "symbol": symbol,
        "name": name,
        "price": price,
        "prev": prev,
        "change": round(price - prev, 3),
        "pct": round((price - prev) / prev * 100, 2) if prev else 0.0,
        "source": "sina",
    }


def _em_quote(symbol):
    """东财实时快照，顺带拿到 五档 之外的字段（备用）"""
    fields = "f43,f44,f45,f46,f57,f58,f60,f169,f170"
    url = "https://push2.eastmoney.com/api/qt/stock/get?secid=%s&ut=fa5fd1943c7b386f172d6893dbfba10b&fields=%s" % (
        _secid(symbol),
        fields,
    )
    d = http_json(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"})
    data = d.get("data") or {}
    if not data:
        raise RuntimeError("东财无数据")
    price = data.get("f43", 0) / 100.0
    prev = data.get("f60", 0) / 100.0
    return {
        "symbol": symbol,
        "name": data.get("f58") or symbol,
        "price": price,
        "prev": prev,
        "change": round(price - prev, 3),
        "pct": round((price - prev) / prev * 100, 2) if prev else 0.0,
        "source": "eastmoney",
    }


def stock(symbol):
    """现价快照：efinance → akshare → 新浪 → 东财"""
    ef = _opt("efinance")
    if ef is not None:
        try:
            row = ef.stock.get_latest_quote([_plain(symbol)]).iloc[0]
            price, prev = float(row["最新价"]), float(row["昨日收盘"])
            return {
                "symbol": symbol,
                "name": str(row.get("股票名称") or row.get("名称") or symbol),
                "price": price,
                "prev": prev,
                "change": round(price - prev, 3),
                "pct": round((price - prev) / prev * 100, 2) if prev else 0.0,
                "source": "efinance",
            }
        except Exception:
            pass
    ak = _opt("akshare")
    if ak is not None:
        try:
            df = ak.stock_bid_ask_em(symbol=_plain(symbol))
            row = dict(zip(df["item"], df["value"]))
            price, prev = float(row["最新"]), float(row["昨收"])
            return {
                "symbol": symbol,
                "name": symbol,
                "price": price,
                "prev": prev,
                "change": round(price - prev, 3),
                "pct": round((price - prev) / prev * 100, 2) if prev else 0.0,
                "source": "akshare",
            }
        except Exception:
            pass
    try:
        return _sina_quote(symbol)
    except Exception:
        return _em_quote(symbol)


def kline(symbol, limit=60):
    """日 K：efinance → akshare → 东财"""
    ef = _opt("efinance")
    if ef is not None:
        try:
            df = ef.stock.get_quote_history(_plain(symbol), klt=101)
            df = df.tail(limit)
            candles = [
                {"d": str(r["日期"]), "o": float(r["开盘"]), "c": float(r["收盘"]), "h": float(r["最高"]), "l": float(r["最低"])}
                for _, r in df.iterrows()
            ]
            name = str(df.iloc[-1].get("股票名称") or symbol)
            return _kline_payload(symbol, name, candles, "efinance")
        except Exception:
            pass
    ak = _opt("akshare")
    if ak is not None:
        try:
            df = ak.stock_zh_a_hist(symbol=_plain(symbol), period="daily", adjust="qfq").tail(limit)
            candles = [
                {"d": str(r["日期"]), "o": float(r["开盘"]), "c": float(r["收盘"]), "h": float(r["最高"]), "l": float(r["最低"])}
                for _, r in df.iterrows()
            ]
            return _kline_payload(symbol, symbol, candles, "akshare")
        except Exception:
            pass
    url = (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=%s"
        "&ut=fa5fd1943c7b386f172d6893dbfba10b&fields1=f1,f2,f3,f4,f5,f6"
        "&fields2=f51,f52,f53,f54,f55,f56,f57&klt=101&fqt=1&end=20500101&lmt=%d"
    ) % (_secid(symbol), limit)
    d = http_json(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"})
    data = d.get("data") or {}
    rows = data.get("klines") or []
    if not rows:
        raise RuntimeError("没有 K 线数据")
    candles = []
    for row in rows:
        f = row.split(",")
        candles.append({"d": f[0], "o": float(f[1]), "c": float(f[2]), "h": float(f[3]), "l": float(f[4])})
    return _kline_payload(symbol, data.get("name") or symbol, candles, "eastmoney")


def _kline_payload(symbol, name, candles, source):
    last, prev = candles[-1]["c"], (candles[-2]["c"] if len(candles) > 1 else candles[-1]["o"])
    return {
        "symbol": symbol,
        "name": name,
        "candles": candles,
        "price": last,
        "pct": round((last - prev) / prev * 100, 2) if prev else 0.0,
        "source": source,
    }


def minute(symbol):
    """分时（当日 1 分钟）：efinance → akshare → 东财 trends2"""
    ef = _opt("efinance")
    if ef is not None:
        try:
            df = ef.stock.get_quote_history(_plain(symbol), klt=1)
            points = [[str(r["日期"])[-5:], float(r["收盘"])] for _, r in df.iterrows()]
            return {"symbol": symbol, "preClose": float(df.iloc[0]["开盘"]), "points": points, "source": "efinance"}
        except Exception:
            pass
    ak = _opt("akshare")
    if ak is not None:
        try:
            df = ak.stock_zh_a_hist_min_em(symbol=_plain(symbol), period="1")
            points = [[str(r["时间"])[-5:], float(r["收盘"])] for _, r in df.tail(241).iterrows()]
            return {"symbol": symbol, "preClose": points and points[0][1], "points": points, "source": "akshare"}
        except Exception:
            pass
    url = (
        "https://push2his.eastmoney.com/api/qt/stock/trends2/get?secid=%s"
        "&ut=fa5fd1943c7b386f172d6893dbfba10b&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13"
        "&fields2=f51,f53&iscr=0&ndays=1"
    ) % _secid(symbol)
    d = http_json(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"})
    data = d.get("data") or {}
    trends = data.get("trends") or []
    if not trends:
        raise RuntimeError("没有分时数据")
    points = []
    for row in trends:
        ts, price = row.split(",")[:2]
        points.append([ts[-5:], float(price)])
    return {
        "symbol": symbol,
        "name": data.get("name") or symbol,
        "preClose": float(data.get("preClose") or points[0][1]),
        "points": points,
        "source": "eastmoney",
    }


def portfolio(symbols):
    """自选股：一次给「快照 + 分时」，给自定义页面用"""
    items = []
    for sym in [x.strip() for x in str(symbols).split(",") if x.strip()][:8]:
        try:
            q = stock(sym)
            try:
                m = minute(sym)
                q["points"] = m["points"]
                q["preClose"] = m["preClose"]
            except Exception:
                q["points"] = []
            items.append(q)
        except Exception as e:
            items.append({"symbol": sym, "error": str(e)})
    return {"items": items, "source": "mixed"}



# ---------- 本机 Coding Agent 会话扫描 ----------
# 思路：会话都在本地落 jsonl（Pi / Claude Code / Codex），用文件的 mtime 当"最近活动"，
# 用进程是否存活区分「运行中」和「等待输入」，标题取会话里第一条用户消息。
# procs：进程名/命令行 token。macOS 上是可执行文件名；Windows 上 npm 全局装的 CLI
# 实际跑成 node.exe + 一段带包名的命令行，所以把包名也列进来（两端通用、不会误伤）。
AGENT_SPECS = [
    {"id": "pi", "name": "Pi", "short": "Pi", "roots": ["~/.pi/agent/sessions"],
     "procs": ["pi", "pi-coding-agent"]},
    {"id": "claude", "name": "Claude Code", "short": "Claude", "roots": ["~/.claude/projects"],
     "procs": ["claude", "claude-code"]},
    {"id": "codex", "name": "Codex", "short": "Codex", "roots": ["~/.codex/sessions"],
     "procs": ["codex", "codex-cli"]},
    # DeepSeek Harness 是 Electron 应用，会话存在 IndexedDB 里，只能靠进程判断
    {"id": "deepseek", "name": "DeepSeek Harness", "short": "DeepSeek", "roots": [],
     "procs": ["DeepSeek Harness", "deepseek-harness"]},
]

_TITLE_CACHE = {}


def _alive_agents():
    """判断哪些 agent 进程活着（macOS 用 ps，Windows 用 Get-CimInstance）。"""
    if os.name == "nt":
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_Process | ForEach-Object { \"$($_.Name)`t$($_.CommandLine)\" }"],
                capture_output=True, text=True, timeout=12)
        except Exception:
            return set()
        rows = []
        for line in (out.stdout or "").splitlines():
            name, _, cmd = line.partition("\t")
            name, cmd = name.strip().lower(), cmd.strip().lower()
            if name:
                rows.append((name, cmd))
        alive = set()
        for spec in AGENT_SPECS:
            for needle in spec["procs"]:
                n = needle.lower()
                hit = False
                for name, cmd in rows:
                    if " " in n:
                        hit = n in cmd
                    else:
                        # 名字精确匹配（带/不带 .exe），或者命令行的某个 token 就是它
                        hit = (name == n or name == n + ".exe" or
                               any(tok == n for tok in re.split(r"[\s\"'=/\\]+", cmd)))
                    if hit:
                        break
                if hit:
                    alive.add(spec["id"])
                    break
        return alive
    try:
        out = subprocess.check_output(["ps", "-Ao", "command="], text=True, timeout=5)
    except Exception:
        return set()
    cmds = [line.strip() for line in out.splitlines() if line.strip()]
    bases = []
    for line in cmds:
        parts = line.split()
        if parts:
            bases.append(os.path.basename(parts[0]))
            bases.append(line)
    alive = set()
    for spec in AGENT_SPECS:
        for needle in spec["procs"]:
            hit = (needle in bases) if " " not in needle else any(needle in c for c in cmds)
            if hit:
                alive.add(spec["id"])
                break
    return alive


def music():
    """当前正在播放的音乐。macOS 走 osascript；Windows 暂不实现（WinRT 媒体会话接口较重），
    直接返回未在播放，组件显示「未在播放」而不是报错。"""
    if os.name == "nt":
        return {"playing": False}
    script = '''
    tell application "System Events"
      set runningApps to name of every process
    end tell
    if runningApps contains "Music" then
      tell application "Music"
        if player state is playing then
          return (name of current track) & "\\t" & (artist of current track) & "\\t" & "Music"
        end if
      end tell
    end if
    return ""
    '''
    try:
        out = subprocess.check_output(["osascript", "-e", script], text=True, timeout=5).strip()
    except Exception:
        return {"playing": False}
    if not out:
        return {"playing": False}
    parts = out.split("\t")
    return {"playing": True, "title": parts[0], "artist": parts[1] if len(parts) > 1 else "",
            "app": parts[2] if len(parts) > 2 else ""}


def _text_of(content):
    """从 message.content 里取纯文本（list/str 两种写法都兼容）"""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") in ("text", "input_text") and part.get("text"):
                return str(part["text"]).strip()
            if isinstance(part, str) and part.strip():
                return part.strip()
    return ""


def _session_meta(path):
    """取会话标题 + 工作目录，按 (路径, mtime) 缓存，不重复解析"""
    st = os.stat(path)
    key = (path, st.st_mtime, st.st_size)
    if key in _TITLE_CACHE:
        return _TITLE_CACHE[key]
    title, cwd = "", ""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for _ in range(400):
                line = f.readline()
                if not line:
                    break
                if len(line) > 200_000:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if not cwd:
                    cwd = d.get("cwd") or (d.get("payload") or {}).get("cwd") or ""
                if not title:
                    if d.get("type") == "message" and (d.get("message") or {}).get("role") == "user":
                        title = _text_of((d["message"] or {}).get("content"))
                    elif d.get("type") == "user":
                        title = _text_of((d.get("message") or {}).get("content"))
                    elif (d.get("payload") or {}).get("type") in ("user_message", "message"):
                        payload = d["payload"]
                        title = _text_of(payload.get("message") or payload.get("content"))
                if title and cwd:
                    break
    except Exception:
        pass
    # 有些会话的第一条"用户消息"其实是系统注入的上下文/临时路径，换成项目名更好读
    if title.startswith("<") or title.startswith("/") or "/var/folders/" in title:
        title = ""
    if not title:
        title = os.path.basename(cwd) if cwd else os.path.splitext(os.path.basename(path))[0]
    title = " ".join(title.split())[:120]
    _TITLE_CACHE[key] = (title, cwd)
    return title, cwd


ERR_MARKERS = (
    '"isapierrormessage":true',
    '"type":"turn_aborted"',
    '"type":"stream_error"',
    '"type":"error"',
    '"stopreason":"error"',
)


def _session_failed(path, tail_bytes=20000, tail_lines=25):
    """看会话尾部有没有硬错误标记（用户取消/流错误/API 报错）。不解析全文件。"""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - tail_bytes))
            tail = f.read().decode("utf-8", "replace")
    except OSError:
        return False
    for line in tail.splitlines()[-tail_lines:]:
        low = line.lower()
        if any(m in low for m in ERR_MARKERS):
            return True
    return False


def _session_files(roots, limit=4):
    files = []
    for root in roots:
        root = os.path.expanduser(root)
        if not os.path.isdir(root):
            continue
        for dirpath, _dirs, names in os.walk(root):
            for n in names:
                if not n.endswith(".jsonl"):
                    continue
                path = os.path.join(dirpath, n)
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                if st.st_size > 60 * 1024 * 1024:  # 太夸张的会话跳过
                    continue
                files.append((st.st_mtime, path))
    files.sort(reverse=True)
    return files[:limit]


def agents():
    """给 Agent 状态组件：按「运行中 → 等待输入 → 已完成」+ 最近活动倒序"""
    now = time.time()
    alive = _alive_agents()
    items = []
    for spec in AGENT_SPECS:
        is_alive = spec["id"] in alive
        sessions = _session_files(spec["roots"])
        if not sessions:
            if is_alive:  # 没有会话日志的 agent（比如 DeepSeek Harness）
                items.append(
                    {
                        "id": spec["id"] + ":app",
                        "agent": spec["id"],
                        "short": spec["short"],
                        "name": spec["name"],
                        "state": "running",
                        "title": "运行中（会话数据未落盘）",
                        "cwd": "",
                        "age": 0,
                        "updated": now,
                    }
                )
            continue
        for idx, (mtime, path) in enumerate(sessions):
            title, cwd = _session_meta(path)
            age = max(0, now - mtime)
            live = is_alive and idx == 0  # 只有该 agent 最新的那个会话才算"活着"
            if live:
                state = "running" if age < 120 else "waiting"
            elif _session_failed(path):
                state = "error"
            else:
                state = "done" if age < 86400 else "idle"
            if state != "running" and age > 3 * 86400:  # 三天前的旧会话不上屏
                continue
            items.append(
                {
                    "id": hashlib.sha1(path.encode("utf-8")).hexdigest()[:12],
                    "agent": spec["id"],
                    "short": spec["short"],
                    "name": spec["name"],
                    "state": state,
                    "title": title,
                    "cwd": os.path.basename(cwd) if cwd else "",
                    "age": int(age),
                    "updated": mtime,
                }
            )
    order = {"running": 0, "waiting": 1, "error": 2, "done": 3, "idle": 4}
    items.sort(key=lambda it: (order.get(it["state"], 9), it["age"]))
    items = items[:12]  # 屏幕只看最靠前的几条
    running = sum(1 for it in items if it["state"] == "running")
    return {"items": items, "running": running, "alive": sorted(alive)}


_NET_PREV = {"at": 0.0, "rx": 0, "tx": 0}
_NET_WIN = {"at": 0.0, "val": None}
_WIN_CPU = {"ticks": None, "pct": 0.0}


def _net_counters():
    """累计收/发字节数；取不到返回 None。"""
    try:
        if os.name == "nt":
            # 只看默认路由那个网卡（Measure-Object 求和会被虚拟网卡异常计数带飞）
            now = time.time()
            if now - _NET_WIN["at"] < 1.0 and _NET_WIN["val"] is not None:
                return _NET_WIN["val"]
            ps = ("$i=(Get-NetRoute -DestinationPrefix '0.0.0.0/0' | Sort-Object RouteMetric | "
                  "Select-Object -First 1).ifIndex; if(!$i){exit}; "
                  "$n=(Get-NetAdapter -InterfaceIndex $i).Name; "
                  "$s=Get-NetAdapterStatistics -Name $n; \"$($s.ReceivedBytes) $($s.SentBytes)\"")
            out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                                 capture_output=True, text=True, timeout=8)
            parts = (out.stdout or "").split()
            val = (int(parts[0]), int(parts[1])) if len(parts) >= 2 else None
            _NET_WIN.update(at=now, val=val)
            return val
        if os.path.exists("/proc/net/dev"):
            rx = tx = 0
            with open("/proc/net/dev") as f:
                for line in f.readlines()[2:]:
                    name, _, rest = line.partition(":")
                    if name.strip() == "lo":
                        continue
                    cols = rest.split()
                    rx += int(cols[0])
                    tx += int(cols[8])
            return rx, tx
        # macOS：<Link#N> 那行的 Ibytes/Obytes；Address 列可能为空，所以按标记列定位
        out = subprocess.check_output(["netstat", "-ib"], text=True, timeout=5)
        rx = tx = 0
        for line in out.splitlines()[1:]:
            cols = line.split()
            i = next((k for k, v in enumerate(cols) if v.startswith("<Link#")), None)
            if i is None or cols[0].startswith("lo"):
                continue
            if ":" in cols[i + 1]:
                rx += int(cols[i + 4])
                tx += int(cols[i + 7])
            else:
                rx += int(cols[i + 3])
                tx += int(cols[i + 6])
        return rx, tx
    except Exception:
        return None


def net_rates():
    """(上传, 下载) 字节/秒，按上一次采样差分。"""
    now = time.time()
    cur = _net_counters()
    prev = dict(_NET_PREV)
    _NET_PREV.update(at=now, rx=(cur[0] if cur else 0), tx=(cur[1] if cur else 0))
    dt = now - prev["at"]
    if cur is None or dt <= 0.2 or cur[0] < prev["rx"] or cur[1] < prev["tx"]:
        return 0, 0
    return round((cur[1] - prev["tx"]) / dt), round((cur[0] - prev["rx"]) / dt)



# macOS 真实 CPU 占用率（host_statistics64 / HOST_CPU_LOAD_INFO）。
# 不用 loadavg/ncpu —— 那是负载均值，跟活动监视器/RunCat 的口径不一样，会明显偏高。
_CPU_SAMPLE = {"ticks": None, "at": 0.0, "pct": 0.0}


def _mac_cpu_percent():
    import ctypes
    import ctypes.util

    global _CPU_SAMPLE
    now = time.time()
    if now - _CPU_SAMPLE["at"] < 0.5 and _CPU_SAMPLE["ticks"]:
        return _CPU_SAMPLE["pct"]

    class CpuLoad(ctypes.Structure):
        _fields_ = [("cpu_ticks", ctypes.c_uint32 * 4)]  # user, system, idle, nice

    try:
        lib = ctypes.CDLL(ctypes.util.find_library("System"))
        lib.mach_host_self.restype = ctypes.c_uint
        host = lib.mach_host_self()
        info = CpuLoad()
        count = ctypes.c_uint32(4)
        if lib.host_statistics64(host, 3, ctypes.byref(info), ctypes.byref(count)) != 0:
            return _CPU_SAMPLE["pct"]
        ticks = list(info.cpu_ticks)
    except Exception:
        return _CPU_SAMPLE["pct"]

    prev = _CPU_SAMPLE["ticks"]
    _CPU_SAMPLE["ticks"] = ticks
    _CPU_SAMPLE["at"] = now
    if prev:
        delta = [b - a for a, b in zip(prev, ticks)]
        total = sum(delta)
        if total > 0:
            _CPU_SAMPLE["pct"] = max(0.0, min(1.0, 1.0 - delta[2] / total))
    return _CPU_SAMPLE["pct"]



def _windows_metrics():
    """Windows 的 CPU/内存（GetSystemTimes + GlobalMemoryStatusEx，不装第三方包）。"""
    import ctypes
    from ctypes import wintypes

    class FILETIME(ctypes.Structure):
        _fields_ = [("lo", wintypes.DWORD), ("hi", wintypes.DWORD)]

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    k32 = ctypes.windll.kernel32
    idle, kern, user = FILETIME(), FILETIME(), FILETIME()
    now = time.time()
    if not k32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user)):
        raise OSError("GetSystemTimes failed")
    ticks = [(x.hi << 32) | x.lo for x in (idle, kern, user)]
    prev = _WIN_CPU["ticks"]
    _WIN_CPU["ticks"] = ticks
    if prev:
        d_idle, d_kern, d_user = (a - b for a, b in zip(ticks, prev))
        total = d_kern + d_user  # kernel 已含 idle
        if total > 0:
            _WIN_CPU["pct"] = max(0.0, min(1.0, 1.0 - d_idle / total))

    ms = MEMORYSTATUSEX()
    ms.dwLength = ctypes.sizeof(ms)
    k32.GlobalMemoryStatusEx(ctypes.byref(ms))
    total_gb = ms.ullTotalPhys / 2**30
    used_gb = (ms.ullTotalPhys - ms.ullAvailPhys) / 2**30
    return {
        "cpu": round(_WIN_CPU["pct"], 3),
        "mem": round(used_gb, 2),
        "memTotal": round(total_gb, 1),
        "host": os.environ.get("COMPUTERNAME", ""),
        "load": 0,
    }


_IP_CACHE = {"at": 0.0, "ip": ""}


_TUN_HINTS = ("tunnel", "tap", "tun", "vpn", "meta", "wireguard", "zerotier", "tailscale", "clash")


def _rank_ip(ip, has_gw, desc):
    """越小越像“真正的局域网 IP”；None = 不要。"""
    if not ip or ip.startswith("127.") or ip.startswith("169.254.") or ip == "0.0.0.0":
        return None
    if ip.startswith("198.18.") or ip.startswith("198.19."):
        return None  # 代理 TUN / 基准测试保留段（Clash、FlClash 的 Meta Tunnel 就用这个）
    parts = ip.split(".")
    private = (ip.startswith("10.") or ip.startswith("192.168.")
               or (len(parts) == 4 and parts[0] == "172" and parts[1].isdigit() and 16 <= int(parts[1]) <= 31))
    if any(h in (desc or "").lower() for h in _TUN_HINTS):
        return 4
    if private and has_gw:
        return 0
    if private:
        return 1
    if has_gw:
        return 2
    return 3


def _lan_ip_win():
    """Get-NetIPConfiguration → [(alias, ip, 有无默认网关, 网卡描述)]"""
    ps = ("Get-NetIPConfiguration | ForEach-Object { "
          "\"$($_.InterfaceAlias)`t$($_.IPv4Address.IPAddress -join ',')\t"
          "$($_.IPv4DefaultGateway.NextHop)`t$($_.InterfaceDescription)\" }")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                         capture_output=True, text=True, timeout=10)
    rows = []
    for line in (out.stdout or "").splitlines():
        cols = line.split("\t")
        if len(cols) < 4:
            continue
        alias, ips, gw, desc = cols[0].strip(), cols[1].strip(), cols[2].strip(), cols[3].strip()
        for ip in ips.split(","):
            ip = ip.strip()
            if ip:
                rows.append((alias, ip, bool(gw), desc))
    return rows


def _lan_ip_posix():
    """ifconfig + 默认路由 → [(iface, ip, 有无默认网关, iface)]"""
    try:
        conf = subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=6).stdout or ""
    except Exception:
        return []
    default_if = ""
    try:
        rn = subprocess.run(["netstat", "-rn"], capture_output=True, text=True, timeout=6).stdout or ""
        for line in rn.splitlines():
            cols = line.split()
            if len(cols) >= 4 and cols[0] == "default":
                default_if = cols[-1]
                break
    except Exception:
        pass
    rows, cur = [], ""
    for line in conf.splitlines():
        if line and not line.startswith(("\t", " ")):
            cur = line.split(":")[0]
        m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", line)
        if m and cur:
            rows.append((cur, m.group(1), cur == default_if, cur))
    return rows


def lan_ip():
    """本机**局域网** IPv4（拿不到就返回空字符串，绝不抛异常）。

    不能简单地用“默认路由那张网卡”：开了代理（Clash/FlClash 的 Meta Tunnel）时默认路由是指向
    TUN 的，会拿到 198.18.0.1；VMware 的 VMnet 又是 192.168.x.x。所以枚举所有 IPv4 再打分：

    1. 优先「私网段 + 有默认网关」的物理网卡（WLAN / 以太网）
    2. 其次私网但没网关的；再次有网关的公网地址
    3. 直接丢弃：回环、169.254 链路本地、198.18/19（TUN）、以及描述里带 tunnel/vpn/meta/… 的网卡

    10 秒缓存，换 WiFi / 插网线能自动跟上。
    """
    now = time.time()
    if _IP_CACHE["ip"] and now - _IP_CACHE["at"] < 10:
        return _IP_CACHE["ip"]
    rows = []
    try:
        rows = _lan_ip_win() if os.name == "nt" else _lan_ip_posix()
    except Exception:
        rows = []
    ranked = []
    for _alias, ip, has_gw, desc in rows:
        r = _rank_ip(ip, has_gw, desc)
        if r is not None:
            ranked.append((r, ip))
    ip = ""
    if ranked:
        ip = sorted(ranked)[0][1]
    if not ip:  # 全都不合适（例如机器上只有代理 TUN）→ 回退到“默认路由地址”
        try:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.connect(("8.8.8.8", 80))
                cand = s.getsockname()[0]
            finally:
                s.close()
            if _rank_ip(cand, True, "") is not None:
                ip = cand
        except Exception:
            ip = ""
    _IP_CACHE.update(at=now, ip=ip)
    return ip


def system_metrics():
    if os.name == "nt":
        try:
            m = _windows_metrics()
        except Exception:
            m = {"cpu": 0, "mem": 0, "memTotal": 0, "host": os.environ.get("COMPUTERNAME", ""), "load": 0}
        m["netUp"], m["netDown"] = net_rates()
        m["ip"] = lan_ip()
        return m
    if sys.platform == "darwin":
        cpu = _mac_cpu_percent()
    else:
        cpu = 0.0
        try:
            psutil = _opt("psutil")
            if psutil is not None:
                cpu = psutil.cpu_percent(interval=None) / 100.0
            else:
                cpu = min(1.0, os.getloadavg()[0] / max(1, os.cpu_count() or 1))
        except Exception:
            pass
    mem, total = 0.0, 0.0
    try:
        if os.path.exists("/usr/bin/vm_stat"):
            out = subprocess.check_output(["vm_stat"], text=True)
            m = re.search(r"page size of (\d+) bytes", out)
            page = int(m.group(1)) if m else 4096
            used = 0
            for line in out.splitlines():
                key, _, val = line.partition(":")
                if key.strip() in ("Pages active", "Pages wired down", "Pages occupied by compressor"):
                    used += int(val.strip().rstrip(".") or 0)
            total = int(subprocess.check_output(["sysctl", "-n", "hw.memsize"]).strip()) / 2**30
            mem = used * page / 2**30
    except Exception:
        pass
    up, down = net_rates()
    return {
        "cpu": round(cpu, 3),
        "mem": round(mem, 2),
        "memTotal": round(total, 1),
        "netUp": up,
        "netDown": down,
        "ip": lan_ip(),
        "host": os.uname().nodename if hasattr(os, "uname") else os.environ.get("COMPUTERNAME", ""),
        "load": round(os.getloadavg()[0], 2) if hasattr(os, "getloadavg") else 0,
    }


def agent_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            st = json.load(f)
    except Exception:
        return {"state": "idle", "task": "", "since": time.time()}
    st.setdefault("state", "idle")
    st.setdefault("task", "")
    st.setdefault("since", time.time())
    return st


# ---------------- HTTP ----------------
def load_layout():
    try:
        with open(LAYOUT, encoding="utf-8") as f:
            return migrate_layout(json.load(f))
    except Exception:
        return migrate_layout(None)


def save_layout(layout):
    os.makedirs(os.path.dirname(LAYOUT), exist_ok=True)
    tmp = LAYOUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(layout, f, ensure_ascii=False, indent=1)
    os.replace(tmp, LAYOUT)


# ---- 纯色测试图案（判断「屏闪」到底是背光/面板还是我们推的帧）----
PATTERN_COLORS = {
    "white": (255, 255, 255),
    "black": (0, 0, 0),
    "gray": (128, 128, 128),
    "dark": (24, 26, 32),
    "red": (255, 0, 0),
    "green": (0, 255, 0),
    "blue": (0, 0, 255),
}


def parse_color(spec):
    s = str(spec).strip().lower()
    if s in PATTERN_COLORS:
        return PATTERN_COLORS[s]
    if len(s) == 7 and s[0] == "#":
        return tuple(int(s[i:i + 2], 16) for i in (1, 3, 5))
    raise ValueError(spec)


def solid_bgr565(rgb):
    """整屏单色 BGR565 小端整帧（2 MB）：sidecar 的 0x04 消息，不用再过一遍 RGB 转换。"""
    r, g, b = rgb
    value = ((b & 0xF8) << 8) | ((g & 0xFC) << 3) | (r >> 3)
    return value.to_bytes(2, "little") * (800 * 1280)


class Handler(BaseHTTPRequestHandler):
    device = None
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        path, qs = url.path, urllib.parse.parse_qs(url.query)
        if path == "/api/layout":
            return self._send(200, load_layout())
        if path == "/api/status":
            return self._send(200, dict(self.device.status, session=self.device.session, layout=load_layout(),
                                       sidecar=self.device.last, build=web_build(),
                                       screen=dict(SCREEN_STATE),
                                       logs={"sidecar": SIDECAR_LOG, "web": WEB_LOG}))
        if path == "/api/data/weather":
            city = (qs.get("city") or ["北京"])[0]
            return self._send(200, CACHE.get("wx:" + city, 900, lambda: weather(city)))
        if path == "/api/data/stock":
            sym = (qs.get("symbol") or ["sh000001"])[0]
            return self._send(200, CACHE.get("st:" + sym, 15, lambda: stock(sym)))
        if path == "/api/data/kline":
            sym = (qs.get("symbol") or ["sh000001"])[0]
            return self._send(200, CACHE.get("kl:" + sym, 120, lambda: kline(sym)))
        if path == "/api/data/minute":
            sym = (qs.get("symbol") or ["sh000001"])[0]
            return self._send(200, CACHE.get("mi:" + sym, 30, lambda: minute(sym)))
        if path == "/api/data/portfolio":
            syms = (qs.get("symbols") or ["sh000001,sz399001"])[0]
            return self._send(200, CACHE.get("pf:" + syms, 20, lambda: portfolio(syms)))
        if path == "/api/data/crypto":
            ids = (qs.get("ids") or ["bitcoin,ethereum"])[0]
            return self._send(200, CACHE.get("cr:" + ids, 60, lambda: crypto(ids)))
        if path == "/api/data/github":
            repo = (qs.get("repo") or ["tauri-apps/tauri"])[0]
            return self._send(200, CACHE.get("gh:" + repo, 300, lambda: github(repo)))
        if path == "/api/data/music":
            return self._send(200, CACHE.get("mu", 3, music))
        if path == "/api/screensaver":
            return self._send(200, dict(load_saver_cfg(), files=screensaver_files(), screen=dict(SCREEN_STATE),
                                        saver=SAVER.status() if SAVER else None))
        if path == "/api/data/agents":
            return self._send(200, CACHE.get("agents", 3, agents))
        if path == "/api/data/system":
            # 0.9s 缓存：多个组件共用一次采样，网速差分才有固定 dt
            return self._send(200, CACHE.get("sys", 0.9, system_metrics))
        if path == "/api/data/agent":
            return self._send(200, agent_state())
        # 静态文件
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        full = os.path.normpath(os.path.join(WEB, rel))
        if not full.startswith(WEB) or not os.path.isfile(full):
            return self._send(404, {"error": "not found"})
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".svg": "image/svg+xml",
            ".mp4": "video/mp4",
            ".webm": "video/webm",
            ".mov": "video/quicktime",
            ".png": "image/png",
            ".json": "application/json; charset=utf-8",
        }.get(os.path.splitext(full)[1], "application/octet-stream")
        # 视频要支持 Range，否则 <video> 播不了
        size = os.path.getsize(full)
        ranged = False
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        if rng and rng.startswith("bytes="):
            try:
                part = rng[6:].split(",")[0]
                a, _, b = part.partition("-")
                start = int(a) if a else max(0, size - int(b))
                end = min(size - 1, int(b)) if b else size - 1
                ranged = True
            except Exception:
                ranged = False
        with open(full, "rb") as f:
            f.seek(start)
            body = f.read(end - start + 1)
        ext = os.path.splitext(full)[1]
        if ext in (".html", ".js"):
            text = body.decode("utf-8", "replace")
            v = web_build()
            if ext == ".html":
                text = re.sub(r'(src="[^"?]+\.js)(")', r'\1?v=' + v + r'\2', text)
                text = re.sub(r'(href="[^"?]+\.css)(")', r'\1?v=' + v + r'\2', text)
            else:
                text = re.sub(r"(from\s*['\"]\./[^'\"]+?)(['\"])", r"\1?v=" + v + r"\2", text)
            body = text.encode()
        if ranged:
            self.send_response(206)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        else:
            self._send(200, body, ctype)

    def do_PUT(self):
        url = urllib.parse.urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        if url.path == "/api/layout":
            try:
                layout = json.loads(self.rfile.read(n).decode())
            except Exception as exc:
                return self._send(400, {"error": str(exc)})
            if layout.get("reset"):
                layout = json.loads(json.dumps(DEFAULT_LAYOUT))
            save_layout(migrate_layout(layout))
            return self._send(200, {"ok": True})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        if url.path == "/api/screensaver":
            body = json.loads(self.rfile.read(n) or b"{}")
            cfg = load_saver_cfg()
            if "file" in body:
                cfg["file"] = body["file"]
            if "autoLock" in body:
                cfg["autoLock"] = bool(body["autoLock"])
            action = body.get("action")
            if action == "play" and SAVER:
                ok = SAVER.start(cfg["file"])
                return self._send(200 if ok else 500, dict(cfg, ok=ok, error=SAVER.error))
            if action == "stop" and SAVER:
                SAVER.stop()
                return self._send(200, dict(cfg, ok=True))
            save_saver_cfg(cfg)
            return self._send(200, dict(cfg, ok=True))
        if url.path == "/api/frame":
            body = self.rfile.read(n)
            if len(body) != 800 * 1280 * 3:
                return self._send(400, {"error": "frame must be %d bytes, got %d" % (800 * 1280 * 3, len(body))})
            ok = self.device.send(b"\x01" + body)
            return self._send(200, {"ok": ok})
        if url.path == "/api/patch":
            body = self.rfile.read(n)
            ok = self.device.send(b"\x02" + body)
            return self._send(200, {"ok": ok})
        if url.path == "/api/device/pattern":
            # 把整屏刷成一种纯色：闪不闪、闪哪种颜色，就能把固件重绘 / 背光 PWM / 面板分开
            body = json.loads(self.rfile.read(n) or b"{}")
            spec = body.get("color") or "gray"
            try:
                rgb = parse_color(spec)
            except ValueError:
                return self._send(400, {"error": "color 需要 #RRGGBB 或 %s" % "/".join(PATTERN_COLORS)})
            ok = self.device.send(b"\x04" + solid_bgr565(rgb))
            return self._send(200, {"ok": ok, "color": spec})
        if url.path == "/api/pages/active":
            body = json.loads(self.rfile.read(n) or b"{}")
            layout = load_layout()
            if any(p["id"] == body.get("id") for p in layout["pages"]):
                layout["activePage"] = body["id"]
                save_layout(layout)
                return self._send(200, {"ok": True, "activePage": layout["activePage"]})
            return self._send(400, {"error": "no such page"})
        if url.path == "/api/device/reconnect":
            self.device.reconnect()
            return self._send(200, {"ok": True})
        if url.path == "/api/debug":
            text = self.rfile.read(n).decode("utf-8", "replace")
            os.makedirs(CONF_DIR, exist_ok=True)
            with open(WEB_LOG, "a", encoding="utf-8") as f:
                f.write(time.strftime("%H:%M:%S ") + text + "\n")
            return self._send(200, {"ok": True})
        if url.path == "/api/control":
            body = json.loads(self.rfile.read(n) or b"{}")
            cmd = {"brightness": 1, "sleep": 2, "wake": 3, "state": 4, "hello": 5}.get(body.get("cmd"))
            if cmd is None:
                return self._send(400, {"error": "unknown cmd"})
            arg = int(body.get("value") or 0)
            if cmd == 1 and (arg < 5 or arg > 100 or arg % 5):
                return self._send(400, {"error": "brightness 需要 5..100 且为 5 的倍数"})
            if cmd == 1:
                self.device.user_brightness = arg
                if hasattr(self.device, "_set_bright"):
                    self.device._set_bright(arg)   # 记住偏好 + 下发（不再几十秒后又刷回 100）
                else:
                    self.device.control(cmd, arg)
            else:
                self.device.control(cmd, arg)
            time.sleep(0.15)
            return self._send(200, {"ok": True, "status": self.device.status})
        return self._send(404, {"error": "not found"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--no-device", action="store_true", help="只跑编辑器，不连屏（设计布局用）")
    ap.add_argument("--com", help="Windows：指定设备数据串口，如 COM15")
    args = ap.parse_args()
    if args.com:
        os.environ["CS80_COM"] = args.com

    if sys.platform == "darwin":  # 预热 CPU 采样，避免第一次请求报 0%
        _mac_cpu_percent()
        time.sleep(0.12)
        _mac_cpu_percent()
    elif os.name == "nt":
        _windows_metrics()
        time.sleep(0.12)
        _windows_metrics()
    threading.Thread(target=_screen_watch, daemon=True).start()
    device = (
        Device()
        if not args.no_device
        else type("D", (), {"status": {"device": "none"}, "last": None, "session": 0,
                            "user_brightness": None, "status_note": "no-device",
                            "send": lambda *a: False, "control": lambda *a: False})()
    )
    Handler.device = device
    global SAVER
    SAVER = Saver(device)
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print("CS80 面板编辑器: http://127.0.0.1:%d  (layout: %s)" % (args.port, LAYOUT))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""屏闪探针：往屏上推一张整帧纯色，再按指定频率重复提交（1 个 tile 的 PATCH，
但固件每次 PATCH 都会做整帧 D-cache clean + DSI 提交），用来判断：
  - 纯色闪不闪 → 内容相关（面板 dither/反转）还是全局（背光/刷新）
  - 高频重复提交后还闪不闪 → 「DSI 提交频率」是不是原因（静止画面只提交一次）
不动固件、不刷机，跑完 sidecar 退出，设备回到原来的状态。

用法（Windows，先确保官方 QDisplay / QDisplayDevice 没占着口）：
    py\\python.exe flicker_probe.py --com COM15 --color gray --hz 0  --secs 10   :: 静止
    py\\python.exe flicker_probe.py --com COM15 --color gray --hz 60 --secs 10   :: 60 Hz 重提
"""
import argparse
import json
import os
import struct
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SIDECAR = os.path.join(HERE, "qdc1.exe" if os.name == "nt" else "qdc1")
W, H = 800, 1280
COLORS = {"white": (255, 255, 255), "black": (0, 0, 0), "gray": (128, 128, 128),
          "dark": (24, 26, 32), "navy": (15, 32, 51), "red": (255, 0, 0),
          "green": (0, 255, 0), "blue": (0, 0, 255)}


def color(spec):
    s = str(spec).strip().lower()
    if s in COLORS:
        return COLORS[s]
    if s.startswith("#") and len(s) == 7:
        return tuple(int(s[i:i + 2], 16) for i in (1, 3, 5))
    raise SystemExit("未知颜色：%s" % spec)


def bgr565(rgb):
    r, g, b = rgb
    return (((b & 0xF8) << 8) | ((g & 0xFC) << 3) | (r >> 3)).to_bytes(2, "little")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--com", default=os.environ.get("CS80_COM", "COM15"))
    ap.add_argument("--color", default="gray")
    ap.add_argument("--hz", type=float, default=0, help="每秒重复提交次数（0 = 只推一次整帧后静止）")
    ap.add_argument("--secs", type=float, default=10)
    ap.add_argument("--sweep", action="store_true", help="按 白/灰/暗/藏青/黑/红/绿/蓝 每秒切一种，用眼看哪种闪")
    args = ap.parse_args()
    if args.sweep:
        order = ["white", "gray", "dark", "navy", "black", "red", "green", "blue"]
        seq = [(name, bgr565(color(name)) * (W * H)) for name in order]
    else:
        seq = [(args.color, bgr565(color(args.color)) * (W * H))]
    px = bgr565(color(args.color))
    tile = px * (32 * 16)
    patch = b"\x02" + struct.pack("<I", 1) + struct.pack("<HH", 0, 0) + tile

    env = dict(os.environ, CS80_COM=args.com)
    proc = subprocess.Popen([SIDECAR], stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env, bufsize=0)
    stats = {"n": 0, "ms": 0.0}

    def reader():
        for line in proc.stdout:
            line = line.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                print("sidecar:", line)
                continue
            name = ev.get("event")
            if name == "patch":
                stats["n"] += 1
                stats["ms"] += ev.get("ms") or 0
                if not ev.get("ok"):
                    print("patch 失败:", json.dumps(ev, ensure_ascii=False))
            elif name in ("ready", "features", "frame", "exit", "lost"):
                print("sidecar:", json.dumps(ev, ensure_ascii=False))

    threading.Thread(target=reader, daemon=True).start()
    time.sleep(0.6)
    step = 5.0
    end = time.time() + args.secs
    i = 0
    while time.time() < end:
        name, frame = seq[i % len(seq)]
        print("%.0fs: 屏上整帧纯色 %s %s" % (time.time() - (end - args.secs), name, color(name)), flush=True)
        proc.stdin.write(b"\x04" + frame)
        proc.stdin.flush()
        t_end = min(end, time.time() + step)
        period = 1.0 / args.hz if args.hz > 0 else 0
        while time.time() < t_end:
            if period:
                proc.stdin.write(patch)
                proc.stdin.flush()
                time.sleep(period)
            else:
                time.sleep(min(0.2, t_end - time.time()))
        i += 1

    if args.hz > 0:
        print("重复提交频率 %.0f Hz" % args.hz)

    proc.stdin.close()
    time.sleep(0.3)
    if stats["n"]:
        print("其中 PATCH 回包 %d 个，固件侧平均 submit %.1f ms" % (stats["n"], stats["ms"] / stats["n"]))
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()
    print("done")


if __name__ == "__main__":
    main()

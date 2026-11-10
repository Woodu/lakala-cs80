#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 QDisplay 0.3.1+ 固件「等待连接」页的每秒重绘关掉，让等待页真正静态。

为什么要打这个补丁（固件源码 main/demo/firmware/src/main.c）：

    waiting_countdown() 在主机断开 5 s、固件画完等待页之后被 controls_poll()
    每秒调用一次：把两位倒计时数字写进 framebuffer，然后 submit_frame() ——
    整帧 2 MB D-cache clean + 写 0x08807038 触发 DSI 提交。
    framebuffer 是单缓冲、提交也不跟 vblank 同步，所以每秒闪一下/裂一下。

    QDisplay 0.3.0（本仓 win-display/qdisplay/qdisplay_native.bin，154400 B）
    还没有这个倒计时，等待页只提交一次 → 本来就是静态的；
    这个补丁只对 0.3.1 及以后（155456 B 那一版起）有意义。

补丁内容（只改 2 字节）：

    waiting_countdown() 里的 `if(seconds==previous) return;`

        ... 13 68            ldr  r3,[r2]      ; r3 = previous
            b3 42            cmp  r3, r6      ; 和本次 seconds 比
            00 f0 cf 80      beq.w <return>   ; 相等 → 返回（跳过重绘+提交）
            ^^^^^ 改成 9b 42 = cmp r3,r3 → 永远相等 → 永远走 return

    结果：等待页画好一次后再也不重绘，倒计时数字根本不出现（0.3.0 也没有），
    屏上就是一张完全静止的图。90 s 后背光自动关闭（qd_power_idle）等行为不受影响。

    改的是 APP 分区里的代码字节，APP2 容器头（长度/描述符）不动，
    拷到表里只动 0x60260000 起的 APP 区仍然是「只写 APP、不碰 NV / 文件系统」。

用法（Windows 刷机机）：

    py\\python.exe patch_waiting_page.py qdisplay_native.bin
    # → qdisplay_native_static.bin
    py\\python.exe qdisplay_app.py qdisplay_native_static.bin --auto
    # 想回退：拿原来的 qdisplay_native.bin 再刷一次即可

输入 bin 必须是官方发布的 0.3.1/更新版本（155456 B 起）；打不开或找不到
倒计时代码会直接报错退出，不会瞎改。
"""
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # embed python 的 ._pth 不含脚本目录
import appimg

try:  # Windows 控制台编码不是 UTF-8 时不要让中文 print 直接抛异常
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

# waiting_countdown() 里 (previous 比较) 前后的字节指纹：
#   movw r2,#0x1ec | movt r2,#0x80f0 | ldr r3,[r2] | cmp r3,r6
FINGERPRINT = bytes.fromhex("40f2ec12c8f2f0021368b342")
PATCH_OLD = bytes.fromhex("b342")  # cmp r3, r6
PATCH_NEW = bytes.fromhex("9b42")  # cmp r3, r3
KNOWN_SHA256 = "4af5b2b0df8ea504d07ae18bb900aae3fd69381e45e707b2c9ca39ae1c5a5fee"  # 0.3.1-demo


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    src = sys.argv[1]
    dst = sys.argv[2] if len(sys.argv) > 2 else os.path.splitext(src)[0] + "_static.bin"
    data = bytearray(open(src, "rb").read())

    # 容器 magic 是 u32 0x41505032，落在文件里就是 ASCII "2PPA"
    if data[:4] not in (b"2PPA", b"APP2"):
        print("不是 APP2 容器（magic 不是 0x41505032）：%s" % src)
        return 1
    declared = int.from_bytes(data[4:8], "little")
    if declared != len(data):
        print("容器长度字段 %d != 文件长度 %d，拒绝处理" % (declared, len(data)))
        return 1
    sha = hashlib.sha256(data).hexdigest()
    print("输入：%s  %d B  sha256=%s" % (src, len(data), sha))
    if sha != KNOWN_SHA256:
        print("注意：不是官方 0.3.1 发布版（0.3.1 应为 %s）" % KNOWN_SHA256)
        print("      仍按字节指纹继续，但请自己确认版本。")

    hits = []
    pos = data.find(FINGERPRINT)
    while pos >= 0:
        hits.append(pos)
        pos = data.find(FINGERPRINT, pos + 1)
    if not hits:
        print("\n没找到倒计时重绘代码 —— 这个 bin 是 0.3.0 或更早：")
        print("  等待页只 submit_frame() 一次，本来就是静态的，不用打补丁。")
        print("  这种情况下屏上还闪，说明不是固件重绘（去看 docs/troubleshooting.md「屏闪」）。")
        return 3
    if len(hits) != 1:
        print("指纹命中 %d 处（%s），不敢猜，放弃" % (len(hits), [hex(h) for h in hits]))
        return 1

    site = hits[0] + len(FINGERPRINT) - len(PATCH_OLD)
    if data[site:site + 2] != PATCH_OLD:
        print("内部错误：补丁位置字节不符")
        return 1
    # 后面紧跟 beq.w（00 f0 xx 8x / 00 f0 xx ax …）再确认一次
    tail = data[site + 2:site + 4]
    if tail[0] != 0x00 or not (tail[1] & 0xF0) == 0xF0:
        print("补丁点后面不是 beq.w（%s），固件版本不匹配，放弃" % tail.hex())
        return 1

    data[site:site + 2] = PATCH_NEW
    data = appimg.fix(data)  # ★ APP2 头部 u32@8 = 整文件 CRC32，必须重算，否则 APP 不启动
    open(dst, "wb").write(data)
    print("补丁点：文件偏移 0x%x（虚拟地址 0x%x）  cmp r3,r6 → cmp r3,r3" % (site, 0x60260000 + site))
    print("输出：%s  %d B  sha256=%s" % (dst, len(data), hashlib.sha256(data).hexdigest()))
    print("刷写：py\\python.exe qdisplay_app.py %s --auto" % os.path.basename(dst))
    print("效果：等待页只画一次，不再每秒整帧重提（倒计时数字不再出现）；回退就刷原 bin。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

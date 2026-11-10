#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成「等待连接」页（黑底纯白字，无渐变/无中间灰 → 不闪），并压缩成固件用的 LZ4 块。

为什么要这样：固件把等待页当成**一整帧 800×1280 BGR565 的 LZ4 块**存在 flash 里
（0.3.0 在文件偏移 0x188fc，50783 B，解压后正好 2048000 B），由
`LZ4_decompress_safe(waiting_page_lz4, frame, sizeof(...), 2048000)` 渲出来。
所以换页面 = 换这段压缩块（见 `patch_waiting_page_image.py`，不用重编固件）。

原厂页是深蓝渐变 + 大量中间灰；实测这颗屏**纯黑/纯白/纯红/纯蓝不闪，灰/暗蓝这类中间灰会闪**。
所以这里只用「纯黑背景 + 纯白线条/文字」，并且**关掉抗锯齿**（抗锯齿会产生中间灰！）。

依赖（只在这一步需要，刷机侧不需要）：
    pip install pillow lz4

用法：
    python make_waiting_page.py                  # 写到 ./waiting_page/
    python make_waiting_page.py --title "等待连接" --sub "请在电脑上启动推屏程序"
输出：
    waiting_page/page.png          预览（1-bit，可直接看）
    waiting_page/page.bgr565.lz4   LZ4 块（store_size=False，固件就是这么解的）
    waiting_page/page.meta         JSON：解压后长度 / 压缩后长度 / xxh32(整帧)
"""
import argparse
import json
import os

from PIL import Image, ImageDraw, ImageFont

W, H = 800, 1280
HERE = os.path.dirname(os.path.abspath(__file__))
FONT_HEI = "C:/Windows/Fonts/msyhbd.ttc"   # 微软雅黑 Bold
FONT_REG = "C:/Windows/Fonts/msyh.ttc"     # 微软雅黑
FONT_MONO = "C:/Windows/Fonts/consola.ttf"  # Consolas


def bgr565(frame: bytes) -> bytes:
    """整帧 BGR565 小端（灰度 v → 三通道相同）。v=0xFF → 0xFFFF，v=0 → 0x0000。"""
    out = bytearray(W * H * 2)
    for i, v in enumerate(frame):
        val = ((v & 0xF8) << 8) | ((v & 0xFC) << 3) | (v >> 3)
        out[2 * i] = val & 0xFF
        out[2 * i + 1] = val >> 8
    return bytes(out)


def xxh32(data: bytes) -> int:
    p1, p2, p3, p4, p5 = 2654435761, 2246822519, 3266489917, 668265263, 374761393
    m = 0xFFFFFFFF

    def rotl(x, r):
        return ((x << r) | (x >> (32 - r))) & m

    n, i = len(data), 0
    if n >= 16:
        v = [(p1 + p2) & m, p2 & m, 0, (-p1) & m]
        while i <= n - 16:
            for k in range(4):
                lane = int.from_bytes(data[i:i + 4], "little")
                i += 4
                v[k] = (rotl((v[k] + lane * p2) & m, 13) * p1) & m
        h = (rotl(v[0], 1) + rotl(v[1], 7) + rotl(v[2], 12) + rotl(v[3], 18)) & m
    else:
        h = p5 & m
    h = (h + n) & m
    while i <= n - 4:
        h = (h + int.from_bytes(data[i:i + 4], "little") * p3) & m
        h = (rotl(h, 17) * p4) & m
        i += 4
    while i < n:
        h = (h + data[i] * p5) & m
        h = (rotl(h, 11) * p1) & m
        i += 1
    h ^= h >> 15
    h = (h * p2) & m
    h ^= h >> 13
    h = (h * p3) & m
    h ^= h >> 16
    return h



# ---- GitHub 图标（simple-icons 的 glyph，CC0）：按 nonzero winding 扫线填充成 1-bit，不做抗锯齿 ----
OCTOCAT_PATH = "M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12"


def _flatten_svg_path(path, seg=20):
    """M/L/C（含相对）→ 折线；这个 glyph 只用到这些命令。"""
    import re as _re
    tokens = _re.findall(r'[MmLlCcZz]|[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?', path)
    i, cmd, pos = 0, None, (0.0, 0.0)
    contours, cur = [], []
    while i < len(tokens):
        t = tokens[i]
        if _re.fullmatch(r'[A-Za-z]', t):
            cmd = t
            i += 1
            if cmd in 'Zz':
                if cur:
                    contours.append(cur)
                    cur = []
                continue
        def num():
            nonlocal i
            v = float(tokens[i]); i += 1
            return v
        if cmd in 'Mm':
            x, y = num(), num()
            if cur:
                contours.append(cur); cur = []
            if cmd == 'm':
                x, y = pos[0] + x, pos[1] + y
            pos = (x, y); cur = [pos]; cmd = 'L' if cmd == 'M' else 'l'
        elif cmd in 'Ll':
            x, y = num(), num()
            if cmd == 'l':
                x, y = pos[0] + x, pos[1] + y
            pos = (x, y); cur.append(pos)
        elif cmd in 'Cc':
            p1 = (num(), num()); p2 = (num(), num()); p3 = (num(), num())
            if cmd == 'c':
                p1 = (pos[0] + p1[0], pos[1] + p1[1]); p2 = (pos[0] + p2[0], pos[1] + p2[1])
                p3 = (pos[0] + p3[0], pos[1] + p3[1])
            for k in range(1, seg + 1):
                s2 = k / seg; m = 1 - s2
                cur.append((m**3 * pos[0] + 3 * m * m * s2 * p1[0] + 3 * m * s2 * s2 * p2[0] + s2**3 * p3[0],
                            m**3 * pos[1] + 3 * m * m * s2 * p1[1] + 3 * m * s2 * s2 * p2[1] + s2**3 * p3[1]))
            pos = p3
        else:
            raise ValueError("unsupported svg path command: %r" % cmd)
    if cur:
        contours.append(cur)
    return contours


def octocat_mask(size):
    """返回 size×size 的 1-bit 掩码：纯白猫咪剪影（无抗锯齿）。"""
    contours = _flatten_svg_path(OCTOCAT_PATH)
    scale = size / 24.0
    edges = []
    for c in contours:
        pts = [(x * scale, y * scale) for x, y in c]
        for a, b in zip(pts, pts[1:] + pts[:1]):
            if a[1] != b[1]:
                edges.append((a, b))
    mask = Image.new("1", (size, size), 0)
    px = mask.load()
    for y in range(size):
        yc = y + 0.5
        xs = []
        for (x1, y1), (x2, y2) in edges:
            if (y1 <= yc < y2) or (y2 <= yc < y1):
                xs.append((x1 + (yc - y1) * (x2 - x1) / (y2 - y1), 1 if y2 > y1 else -1))
        xs.sort()
        wind = 0
        for k in range(len(xs) - 1):
            wind += xs[k][1]
            if wind == 0 and 0 < k < len(xs) - 2:   # 轮廓里的“洞”就是猫咪本体
                for x in range(max(0, int(round(xs[k][0]))), min(size, int(round(xs[k + 1][0])))):
                    px[x, y] = 1
    return mask


def render(title, sub, tag, outdir, aa=False, levels=0, floor=0, github="", logo=False):
    img = Image.new("L", (W, H), 0)   # 0 = 纯黑
    d = ImageDraw.Draw(img)
    f_title = ImageFont.truetype(FONT_HEI, 76)
    f_sub = ImageFont.truetype(FONT_REG, 30)
    f_tag = ImageFont.truetype(FONT_MONO, 23)

    def center(s, font, y, tracking=0):
        if tracking:
            widths = [d.textlength(c, font=font) for c in s]
            total = sum(widths) + tracking * (len(s) - 1)
            x = (W - total) / 2
            for c, w in zip(s, widths):
                d.text((x, y), c, font=font, fill=255)
                x += w + tracking
        else:
            w = d.textlength(s, font=font)
            d.text(((W - w) / 2, y), s, font=font, fill=255)

    # 显示器线稿：只用纯白描边
    cx, top, ww, hh, st = W // 2, 300, 380, 238, 11
    x0, y0, x1, y1 = cx - ww // 2, top, cx + ww // 2, top + hh
    d.rounded_rectangle([x0, y0, x1, y1], radius=24, outline=255, width=st)
    d.rectangle([cx - 14, y1 + 2, cx + 14, y1 + 58], fill=255)
    d.rounded_rectangle([cx - 92, y1 + 58, cx + 92, y1 + 78], radius=10, fill=255)

    center(title, f_title, 690)
    center(sub, f_sub, 812)
    # 第三行：默认放 GitHub 地址（替换掉原来那句 WAITING FOR HOST）
    if github:
        f_gh = ImageFont.truetype(FONT_MONO, 30)
        if logo:
            icon = octocat_mask(44).convert("L").point(lambda v: 255 if v else 0)
            tw = d.textlength(github, font=f_gh)
            gap = 14
            x0 = (W - (44 + gap + tw)) / 2
            y_icon = 900 - 4
            img.paste(255, (int(round(x0)), y_icon), icon)
            bbox = d.textbbox((0, 0), github, font=f_gh)
            d.text((x0 + 44 + gap, y_icon + (44 - (bbox[3] - bbox[1])) / 2 - bbox[1]),
                   github, font=f_gh, fill=255)
        else:
            center(github, f_gh, 898, tracking=1)
    elif tag:
        center(tag, f_tag, 910, tracking=7)

    # 默认阈值成 1-bit：抗锯齿的边缘是中间灰 → 实测会闪，所以全抹成纯黑/纯白。
    # --aa            保留抗锯齿（边缘会是中间灰，用来对比观感/验证闪的机理）
    # --levels K      AA 后量化成 K 级灰度（K=2 等价阈值；K=4/6 观感更软但仍有中间灰）
    if levels >= 2 and floor > 0:
        # 只保留"浅灰"过渡：<110 直接黑，其余量化到 [floor,255] 的 K-1 级
        # （浅灰比 50% 灰更接近两端，理论上闪得更轻）
        n = max(2, levels - 1)

        def q(v):
            if v < 110:
                return 0
            t = (v - 110) / 145.0
            k = int(round(t * (n - 1)))
            return int(round(floor + (255 - floor) * k / (n - 1)))
        return img.point(q)
    if levels >= 2:
        step = 255.0 / (levels - 1)
        return img.point(lambda v: int(round(v / step)) * step)
    if aa:
        return img
    return img.point(lambda v: 255 if v >= 110 else 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", default="等待连接")
    ap.add_argument("--sub", default="请在电脑上启动推屏程序")
    ap.add_argument("--tag", default="WAITING FOR HOST")
    ap.add_argument("--out", default=os.path.join(HERE, "waiting_page"))
    ap.add_argument("--aa", action="store_true", help="保留抗锯齿（边缘是中间灰，会闪；只用来对比）")
    ap.add_argument("--levels", type=int, default=0, help="AA 后量化成 K 级灰度（2=阈值）")
    ap.add_argument("--floor", type=int, default=0, help="配合 --levels：最低非黑级（如 176 = 只用浅灰过渡）")
    ap.add_argument("--github", default="",
                    help="第三行显示一个地址（顶替 WAITING FOR HOST）；默认空 = 显示 --tag 那句英文")
    ap.add_argument("--logo", action="store_true", help="地址前加 GitHub 猫咪图标（默认不加）")
    args = ap.parse_args()

    img = render(args.title, args.sub, args.tag, args.out, aa=args.aa, levels=args.levels, floor=args.floor, github=args.github, logo=args.logo)
    frame = bgr565(img.tobytes())
    nonzero = sum(1 for b in frame if b)
    colors = set()
    for i in range(0, len(frame), 2):
        colors.add(frame[i] | (frame[i + 1] << 8))
        if len(colors) > 4:
            break

    import lz4.block
    comp = lz4.block.compress(frame, mode="high_compression", store_size=False)
    os.makedirs(args.out, exist_ok=True)
    img.save(os.path.join(args.out, "page.png"))
    with open(os.path.join(args.out, "page.bgr565.lz4"), "wb") as f:
        f.write(comp)
    meta = {"width": W, "height": H, "raw_bytes": len(frame), "lz4_bytes": len(comp),
            "xxh32": xxh32(frame), "title": args.title, "sub": args.sub, "tag": args.tag,
            "aa": bool(args.aa), "levels": args.levels, "floor": args.floor, "github": args.github, "logo": bool(args.logo), "color_count": len(set(
                frame[i] | (frame[i + 1] << 8) for i in range(0, len(frame), 2)))}
    with open(os.path.join(args.out, "page.meta"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    print("整帧 %d B，压缩后 %d B，非零字节 %d，颜色数 %s，xxh32=%#x"
          % (len(frame), len(comp), nonzero, sorted(colors)[:4], meta["xxh32"]))
    print("输出：%s/page.png  page.bgr565.lz4  page.meta" % args.out)


if __name__ == "__main__":
    main()

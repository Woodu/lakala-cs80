#!/bin/sh
# 把任意视频转成 800×1280 / 12fps 的屏保素材，放进 ~/Movies/cs80-screensaver/
#   sh panel/add-screensaver.sh ~/Downloads/xxx.mp4 [名称]
# 为什么要转：4K/30fps 原片在 WebView 里解码 + 我们这 10 MB/s 的 USB 推力都吃不下
set -e
SRC=${1:?用法: sh panel/add-screensaver.sh <视频文件> [输出名]}
NAME=${2:-$(basename "$SRC" | sed 's/\.[^.]*$//')}
OUT_DIR="$HOME/Movies/cs80-screensaver"
mkdir -p "$OUT_DIR"
OUT="$OUT_DIR/$NAME.mp4"

command -v ffmpeg >/dev/null || { echo "需要 ffmpeg：brew install ffmpeg" >&2; exit 1; }

ffmpeg -y -v error -i "$SRC" \
  -vf "scale=800:1280:force_original_aspect_ratio=increase,crop=800:1280,fps=12" \
  -c:v libx264 -crf 26 -preset veryfast -pix_fmt yuv420p -an -movflags +faststart \
  "$OUT"
ls -lh "$OUT"
echo "已加入屏保列表（刷新面板即可看到）：$OUT"

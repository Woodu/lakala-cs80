#!/bin/sh
# 启动 CS80 面板（编辑器 + 运行时）：host 常驻 + Chrome 应用窗口。
# 屏幕内容靠这个窗口里的画布生成，所以窗口一直在（可最小化），关掉窗口屏会回到「等待连接」页。
#
#   sh panel/run.sh            # 启动
#   PORT=9000 sh panel/run.sh  # 换端口
set -e
DIR=$(cd "$(dirname "$0")" && pwd)
PORT=${PORT:-8787}

# 老的单一仪表盘服务会抢 USB，先停掉
launchctl bootout "gui/$UID/com.cs80.panel" 2>/dev/null || true

if ! curl -s -o /dev/null "http://127.0.0.1:$PORT/api/status"; then
  nohup python3 "$DIR/host.py" --port "$PORT" >/tmp/cs80_host.log 2>&1 &
  sleep 2
fi

CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
[ -x "$CHROME" ] || CHROME="/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"
if [ ! -x "$CHROME" ]; then
  echo "没找到 Chrome/Edge，直接用浏览器打开 http://127.0.0.1:$PORT/ 也可以" >&2
  exit 0
fi
# 避免窗口最小化/被遮挡后 Chromium 把页面当后台节流（setInterval 1 s → 1 分钟，
# 屏上就一卡一卡）。屏幕内容靠这个窗口的定时器推，必须让定时器一直正常跑。
exec "$CHROME" --app="http://127.0.0.1:$PORT/" --window-size=1180,1000 \
  --disable-background-timer-throttling --disable-backgrounding-occluded-windows \
  --disable-renderer-backgrounding --disable-features=IntensiveWakeUpThrottling \
  --user-data-dir="$HOME/.cs80_panel/chrome"

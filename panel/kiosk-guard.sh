#!/bin/sh
# CS80 kiosk 守护：跑 Chromium headless，页面心跳停了就重启。
#
# 为什么需要外部看门狗：屏上每一帧都靠这个页面里的 setInterval 推。
# Chromium 会把「看不见 / 被遮挡 / 后台」的页面节流到 1 次/秒，5 分钟后
# 进一步降到 1 次/分钟并可能冻结页面 —— 表现就是屏上「一卡一卡、好几秒不更新」。
# systemd 的 Restart= 只能管进程退出，管不了「进程活着但页面不跑了」，所以：
#   页面每 5 s POST /api/heartbeat → host 记录时间 → 这里查 /api/health 的 beat_age
# 连续两次超过 BEAT_MAX 秒就杀掉 Chromium 重新拉起（换新页面 + 整帧重推）。
set -u
DIR=$(cd "$(dirname "$0")" && pwd)
PORT=${PORT:-8787}
BEAT_MAX=${BEAT_MAX:-30}
# renderer 的 shared memory fd 泄漏到一定量就主动重启，别等 EMFILE 把页面冻住
FD_MAX=${FD_MAX:-8000}
RSS_MAX_KB=${RSS_MAX_KB:-1572864}
BIN=${CS80_CHROME:-/root/.cache/ms-playwright/chromium_headless_shell-1217/chrome-headless-shell-linux64/chrome-headless-shell}
PROFILE=${CS80_KIOSK_PROFILE:-/var/lib/cs80-kiosk}
URL="http://127.0.0.1:${PORT}/?kiosk=1"

log() { echo "cs80-kiosk: $*" >&2; }

# renderer 的 fd / RSS 检查：页面可能「进程活着、心跳还在、推帧已经停了」
# （EMFILE / shm 泄漏）。心跳判断不出来，直接看 renderer 的资源。
check_renderer() {
  G=$(ps -o pgid= -p $$ 2>/dev/null | tr -d ' ')
  [ -z "$G" ] && return 0
  R=$(ps -eo pid=,pgid=,args= | awk -v g="$G" '$2 == g && /--type=renderer/ {print $1; exit}')
  [ -z "$R" ] && return 0
  FDS=$(ls "/proc/$R/fd" 2>/dev/null | wc -l | tr -d ' ')
  RSS=$(ps -o rss= -p "$R" 2>/dev/null | tr -d ' ')
  if [ "${FDS:-0}" -ge "$FD_MAX" ] || [ "${RSS:-0}" -ge "$RSS_MAX_KB" ]; then
    log "renderer pid=$R fds=$FDS rss=${RSS}KB 超过阈值，重启 Chromium"
    return 1
  fi
  return 0
}

# Chromium 的 renderer/gpu/zygote 是子孙进程，只 kill 主进程会留下孤儿（占着 profile
# 还可能继续往 host 推帧）。按 --user-data-dir 精确清理。
kill_chrome() {
  [ -n "${1:-}" ] && kill "$1" 2>/dev/null
  pkill -f "user-data-dir=$PROFILE" 2>/dev/null
  sleep 1
}

if [ ! -x "$BIN" ]; then
  # 找不到 headless shell 就退回完整版 Chrome（会自动加 --headless=new）
  for c in "${HOME}/.cache/ms-playwright/chromium-1217/chrome-linux64/chrome" \
           /usr/bin/chromium /usr/bin/chromium-browser /usr/bin/google-chrome /usr/bin/google-chrome-stable; do
    if [ -x "$c" ]; then BIN="$c"; break; fi
  done
fi
if [ ! -x "$BIN" ]; then
  log "找不到 Chromium（装 Playwright Chromium，或设 CS80_CHROME=/path/to/chrome）"
fi

kill_chrome ""   # 清掉上一轮残留（systemd 重启时主进程可能先被 SIGKILL）
while :; do
  rm -rf "$PROFILE"
  # 完整版 Chrome 需要 --headless=new；chrome-headless-shell 本身就是无头的。
  case "$BIN" in
    *headless-shell*) HEADLESS="" ;;
    *) HEADLESS="--headless=new" ;;
  esac
  "$BIN" $HEADLESS \
    --no-sandbox --disable-dev-shm-usage --disable-gpu --hide-scrollbars \
    --window-size=1280,1000 --no-first-run --no-default-browser-check \
    --user-data-dir="$PROFILE" \
    --disable-background-timer-throttling \
    --disable-backgrounding-occluded-windows \
    --disable-renderer-backgrounding \
    --disable-ipc-flooding-protection \
    --disable-features=IntensiveWakeUpThrottling,CalculateNativeWinOcclusion \
    "$URL" &
  PID=$!
  log "started pid=$PID $BIN"
  WARMUP=8      # 前 8 次（约 40 s）不判心跳：等 host / 页面加载完
  BAD=0
  while kill -0 "$PID" 2>/dev/null; do
    sleep 5
    if [ "$WARMUP" -gt 0 ]; then WARMUP=$((WARMUP - 1)); continue; fi
    AGE=$(curl -s --max-time 3 "http://127.0.0.1:${PORT}/api/health" \
          | sed -n 's/.*"beat_age": *\([0-9][0-9]*\.\?[0-9]*\).*/\1/p')
    [ -z "$AGE" ] && continue    # host 没起来 / 正在重启，不判
    if [ "${AGE%%.*}" -ge "$BEAT_MAX" ]; then
      BAD=$((BAD + 1))
    else
      BAD=0
    fi
    if [ "$BAD" -ge 2 ]; then
      log "页面心跳停了 ${AGE}s（页面被冻结？），重启 Chromium"
      kill_chrome "$PID"
      break
    fi
    if ! check_renderer; then
      kill_chrome "$PID"
      break
    fi
  done
  wait "$PID" 2>/dev/null
  log "Chromium 退出，2 s 后重启"
  sleep 2
done

# 排障：现象 → 原因 → 处理

## 屏上一直是「等待连接」页

固件 `QD_HOST_TIMEOUT_MS = 5000`：**5 秒收不到主机任何命令就自己重画等待页**。按顺序查：

| 检查 | 命令 / 位置 |
|---|---|
| 主机有没有在推帧 | `curl 127.0.0.1:8787/api/status` → `last` 应该是 `patch` / `frame` |
| host 有没有在跑 | 同上，`device` 应为 `up`；`connecting`/`down` 见下一节 |
| 桌面壳窗口是不是被关了 | 关窗只是隐藏到托盘；**退出 App 就没人推帧** |
| 客户端页面是不是还在 | 屏保期间由 host 推；非屏保时必须有一个页面在跑（见「前端没更新」） |

## `device` 不是 `up`

| `device` / `reason` | 含义 | 处理 |
|---|---|---|
| `down` + `open-failed` | 系统里根本没有 `2c7c:0901` | 换 Type-C **数据**线、换口、接 DC 12V，然后点顶栏设备标签重连 |
| `down` + `no-ack-patch` / `no-ack-keepalive` | 设备在，但不应答（固件卡死/重启中） | 等 1–5 秒自动重连；不动就按设备「菜单」键或断电重上 |
| `no-sidecar` | `panel/qdc1` 不存在或没编译 | 按 `docs/windows.md` / README 第 4 步编译 |
| `host-down` | 页面连不上 host | host 没起来或端口不对 |

`/api/status` 的 `session` 每次 sidecar 重连都会 +1；前端看到 `session` 变化会**整帧重推**。

## 前端改了但屏上没变

这是最容易误判的一类。屏上的内容来自**桌面 app 窗口里的页面**，而 app 用的是打包副本：

- 打包版：host 服务的是 `CS80 Panel.app/Contents/Resources/panel/web/` 的**拷贝** → 改完 `panel/web/**` 必须
  `npx tauri build` 重新打包
- 新增 `web/**` 文件还要同步 `panel/shell/src-tauri/tauri.conf.json` 的 `bundle.resources`（显式列举）

现在有兜底：`/api/status` 带 `build`（web 文件 mtime 汇总），页面发现 `build` 变了会**自己 reload**，
静态资源也带 `?v=<build>` 绕过 WebView 缓存。

自查顺序：`curl /api/status | grep build` → 与 `curl /widgets.js | grep <新代码>` 对比。

## 多实例抢 USB

`panel/qdc1` 独占设备。同时起两个（比如手动 host + 桌面壳自己的 host）会出现「一个能用、一个报错」。
`panel/run.sh` 和桌面壳启动时都会先 `launchctl bootout com.cs80.panel`；
手动调试时先 `pkill -f cs80-panel`（壳会拉起自己的 host，也要一起停）。

## 屏闪

两个来源，都来自固件，不是客户端 bug：

1. **背光 1 kHz 软件 PWM**：亮度 <100 时 `bl_running=1`、`bl_edges` 每秒涨 ~1000；
   亮度 100 时 PWM 完全停（`bl_running=0`）→ 把亮度调到 100 最稳
2. **单缓冲 + 无 vblank 同步**：每次 DSI 提交都可能轻微撕裂，等待页的 90 秒倒计时每秒重画一次就是每秒一闪

判断方法：顶栏「暂停推送」（屏上定格、USB 保活不停）——还闪就是屏幕/供电，不闪就是提交导致。

## 锁屏后画面卡成幻灯片

已修。原因：浏览器播视频时，窗口被锁屏盖住 → 页面 `hidden` → WebView 把 `setInterval` 节流到 ~1 s。
现在屏保由 **host 侧 ffmpeg** 解码推帧，不受窗口可见性影响。如果你在别的实现里遇到同类问题，记住这条。

## 中文变方块

canvas 里用的是系统字体栈（`PingFang SC` / `Microsoft YaHei`）。缺中文字体时会回退到方块，
装一个中文字体即可；`panel/web/widgets.js` 的 `FONT` 常量可以改成指定字体。

## 数据源抓不到

组件会保留上次值并显示占位（「暂无数据」/「占位数据」），失败后 15 秒重试（不会等满 TTL）。
常见原因：公司网络挡了 `open-meteo` / 新浪 / 东财 / GitHub；换网络或改用可访问的数据源。

## Windows 特有

见 [`docs/windows.md`](windows.md) 的「故障排查」一节（COM 口不出现、`--probe` 全 false、512 KB 单次写入超时等）。

# panel/ —— 客户端（画布编辑器 + 常驻 runtime）

```
桌面壳（Tauri）→ 窗口加载 http://127.0.0.1:8787/
   浏览器画布 800×1280（编辑器 = 预览 = 设备像素源）
   │ 每次只把脏的 32×16 tile 发出去
   ▼
host.py  ── 独占 USB 的常驻进程（HTTP :8787 + 数据源 + 屏保 + 锁屏监听）
   │ stdin 三条二进制消息：整帧 RGB / 局部 tile / 控屏（+ 屏保整帧 BGR565）
   ▼
qdc1（C sidecar，单进程独占设备；macOS=IOKit，Windows=COM 口）
   ▼
CS80 屏（QDisplay 固件：QDC1 协议 + BGR565 framebuffer + DSI 上屏）
```

协议、硬件、刷机、排障分别见 [`../docs/protocol.md`](../docs/protocol.md)、[`../docs/hardware.md`](../docs/hardware.md)、
[`../docs/firmware.md`](../docs/firmware.md)、[`../docs/troubleshooting.md`](../docs/troubleshooting.md)。

## 跑起来

```bash
cd panel/shell && npm install && npx tauri build     # 产出 .app / .msi（推荐）
open panel/shell/src-tauri/target/release/bundle/macos/"CS80 Panel.app"

# 或者不打包：
python3 panel/host.py        # 然后浏览器打开 http://127.0.0.1:8787
sh panel/run.sh              # host + Chrome 应用窗口（会自动停掉旧的 launchd 服务）
```

**必须常驻**：固件 5 秒收不到主机命令就回「等待连接」页。桌面壳把「关窗」改成「隐藏到托盘」，
并带一个 host 监督线程（host 死了自动拉起，退避 2→10 s）。

## 文件

```
qdc1.c / lz4.c / lz4.h      USB sidecar（QDC1 协议、LZ4、影子 framebuffer、保活、看门狗）
host.py                     常驻 host：HTTP API、数据源、Agent 扫描、屏保(ffmpeg)、锁屏监听、静态资源
web/index.html  styles.css  编辑器骨架 + 设计系统（token 来自 glance 的暗色中性 + 金色 accent）
web/widgets.js              全部组件 + 卡片外壳 card() + 主题 THEME
web/pages/*.js              自定义页面（示例：自选股 + 分时图）
web/core.js                 状态、API、数据调度（按组件 ttl 轮询）
web/editor.js               gridstack 网格、组件库、属性面板、快捷键
web/pusher.js               把格子拼成整帧、tile 差分、整帧/局部推送
web/main.js                 接线 + 顶栏（亮度/待机/屏保/推送统计）
web/lunar.js                农历换算（1900–2100 位表）
web/media/                  屏保视频（内置 wallpaper.mp4）
web/logos/                  Agent logo（Pi / Claude / OpenAI / DeepSeek）
shell/                      Tauri 2 壳（见 shell/README.md）
add-screensaver.sh          把任意视频转成 800×1280 / 12 fps 放进屏保目录
```

## 组件（12 个）

| 组件 | 最小尺寸 | 数据源 |
|---|---|---|
| 时钟 | 2×1 | 本地时间 + 农历 + 右上角天气；`font` 可选 `mono`/`sans`/`lcd`（自绘七段数码管） |
| 时钟 · 表盘 | 3×2 | 刻度圈 + 左右大数字 + 走时指针 + 天气（参考 Watch 表盘风格） |
| Agent | 2×2 | 自动扫描本机 Pi / Claude Code / Codex / DeepSeek Harness 会话 |
| 股票 | 2×1 | 一行一只（高度决定只数），`symbols` 逗号分隔，走 `/api/data/portfolio` |
| K 线 | 2×1 | 东方财富日线 |
| 加密 | 1×1 | coingecko |
| GitHub | 1×1 | GitHub API（未授权 60 次/时） |
| 天气 | 1×1 | open-meteo（可选 macOS 快捷指令，见下） |
| 电脑指标 | 2×1 | `host_statistics64` CPU + `vm_stat` 内存 + 网速 |
| RunCat | 2×2 / 5×2 | CPU/MEM 环 + 奔跑猫，速度随 CPU |
| 倒计时 | 1×1 | 本地计算 |
| 便签 | 1×1 | 纯文本 |

## 编辑器

- 5×8 网格（每格 160 px），画布高度固定为整屏 8 行，空格子也画线
- 添加：点组件库；移动：拖；缩放：拖四角/四边；微调 `←↑↓→`；复制 `⌘D`；删除 `⌫`
- 多画布：顶部 tab 切换（单击切换、双击改名、`＋` 新建、「删除画布」删当前页）
- 布局文件 `~/.cs80_panel/layout.json`，可导出/导入 JSON

## 多画布与自定义页面

```json
{ "version": 2, "activePage": "main",
  "pages": [
    { "id": "main", "name": "主页", "type": "grid", "widgets": [ ... ] },
    { "id": "stockpage", "name": "股票自选", "type": "custom",
      "module": "pages/stock.js", "config": { "symbols": "sh000001,sz399001", "title": "自选股" } }
  ] }
```

自定义页面就是一个 ES 模块：

```js
export default {
  needs: { kind: 'portfolio', ttl: 20000, query: (cfg) => ({ symbols: cfg.symbols }) },
  draw(ctx, size, data, cfg, theme) { /* 800×1280 设备像素，随便画 */ },
};
```

## Agent 组件

扫描各 Agent 自己落在本地的会话日志（无需埋点）：

| Agent | 会话位置 | 运行判定 |
|---|---|---|
| Pi | `~/.pi/agent/sessions/**/*.jsonl` | 进程 `pi` 存活 |
| Claude Code | `~/.claude/projects/**/*.jsonl` | 进程 `claude` 存活 |
| Codex | `~/.codex/sessions/**/*.jsonl` | 进程 `codex` 存活 |
| DeepSeek Harness | 会话在 IndexedDB 里读不到，只判进程 | 进程存在 |

- 状态色：运行中（绿，呼吸）/ 等待（琥珀，呼吸）/ 异常退出（红）/ 1 小时内结束（蓝）/ 更早（灰）
- 排序：运行中 → 等待 → 异常 → 已结束，组内按最近活动倒序；无滚动条，按高度取行数
- 配置项是两级勾选树：整组隐藏某个 Agent，或只隐藏某一条会话（`config.hide` / `config.hideTasks`）

## 屏保

- 顶栏：下拉选视频、`屏保` 按钮、`锁屏播放` 勾选
- **画面由 host 侧 ffmpeg 解码**（`bgr565le` → sidecar 的 `0x04` 消息），锁屏后不受 WebView 节流影响，稳定 12 fps
- 素材：`panel/web/media/*.mp4` + `~/Movies/cs80-screensaver/*.mp4`
- 加自己的视频：`sh panel/add-screensaver.sh ~/Downloads/xxx.mp4 名字`（转成 800×1280 / 12 fps）

## 天气数据

| 来源 | 说明 |
|---|---|
| open-meteo（默认） | 免费无 key，`geocoding` + `forecast` |
| macOS 系统天气（可选） | 系统没有公开接口；自建一个 Shortcuts 快捷指令输出 `{"temp":16,"low":15,"high":19,"code":3}`，然后 `CS80_WEATHER_SHORTCUT="天气"` 起 host |

## 已知限制

- Windows 传输后端代码已写好但**未实机验证**（见 [`../docs/windows.md`](../docs/windows.md)）
- Agent 的「等待输入」是「进程活着但 120 秒没写入」的近似，不是真状态
- DeepSeek Harness 读不到会话内容，只能显示「运行中」
- 默认每 200 ms 一次推送循环（时钟秒级）；屏保期间浏览器暂停推帧，由 host 独占

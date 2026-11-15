# 走过的死路（别重复踩）

按「现象 / 原因 / 结论」记，都是实测过的，不是猜测。

---

## 1. 用 QuecPython 显示任意画面 → 不行

**现象**：`lcd_clear(纯色)` 正常（2 MB / 21 ms），但 `lcd_write` / `lcd_show` 全部无效，
要么返回 0 看着成功、要么屏幕变黑。

**原因**：面板跑 video/burst 模式，DSI 每帧必须完整 2 048 000 字节；提交前必须先
**D-cache clean**（`0x60137213`）再**写 DSI 触发寄存器**（`0x08807038 |= 1`）。
这两步只在 C 层能做：Python 写 framebuffer 走的是 D-cache，DSI 从 DRAM 读 → 屏不变；
`lcd_write` 内部能 clean 但不支持整帧/部分写。

**结论**：换成 QDisplay native 固件（本仓库当前方案）。QuecPython 时代的工具留在 `archive/quecpython/`。

## 2. macOS 上用 libusb 打接口 32 → 不行

**现象**：`usb.util.claim_interface(dev, 32)` 恒返回 `EINVAL`；`if8/20/31` 是 `NOT_FOUND`、`if33` 也是 `EINVAL`。

**原因**：不是权限（免 root 也一样），是 libusb darwin 后端这条路径的限制；`if32` 明明存在于描述符里。

**结论**：走 IOKit（IOUSBLib）的 `CreateInterfaceIterator` + `USBInterfaceOpen`，**不需要 root**。
实现见 `panel/qdc1.c` 的 `open_link()`（`#ifndef _WIN32` 分支）。

## 3. 32×32 的猫放大到 100+ px → 糊

**现象**：RunCat 组件里的猫在屏上明显模糊 + 锯齿。

**原因**：原素材只有 32×32，放大 4 倍又关了插值。

**结论**：换 RunCat365 的 **120×100** 奔跑帧（Apache-2.0），`source-in` 染色后按目标尺寸绘制。
中途还试过自绘矢量猫，姿势像「走路」不像「奔跑」，也弃用了。

## 4. 在浏览器里播屏保视频 → 锁屏后卡成幻灯片

**现象**：手动播很流畅，锁屏后掉到 ~1 fps。

**原因**：窗口被锁屏盖住 → 页面 `hidden` → WebView 把 `setInterval` 节流到 ~1 s、视频解码降频。

**结论**：屏保改由 **host 侧 ffmpeg** 解码成 BGR565 整帧直推设备（sidecar 的 `0x04` 消息），
和窗口可见性无关，稳定 12 fps。

## 5. 改造官方 Windows 上位机（QDisplay.exe）当客户端

**做法**：替换 `QDisplay.Windows.dll`（`Graphics.cs` 里 GDI+ 画自定义仪表盘），跑 `QDisplayDevice` 服务。

**为什么放弃**：只能 Windows；C# 5 + .NET Framework 4.8 的编译器限制（不能用 `$""` 插值）；DLL 替换脆弱；
组件化/多画布这些需求根本没地方放。相关源码与安装包还在 `win-display/qdisplay/`，作为对照保留。

## 6. 用 loadavg / ncpu 当 CPU 占用率 → 数值偏高

**现象**：屏幕上 CPU 52%，活动监视器 / RunCat 只有 26%。

**原因**：`loadavg` 是「运行队列长度」，不是占用率。

**结论**：用 `host_statistics64(HOST_CPU_LOAD_INFO)` 的 tick 差值算真实占用率
（`1 - idle_delta / total_delta`），和系统监控一致。实现见 `panel/host.py` 的 `_mac_cpu_percent()`。

## 7. 用 `sleep` / 拔插后靠「进程还活着」判断设备在线

**现象**：客户端显示「设备在线」，屏上却是等待页。

**原因**：sidecar 攥着休眠前的 IOKit 句柄，`WritePipeTO` 照样返回成功，但数据进不了设备 —— 没人发现。

**结论**：三层看门狗，缺一不可：
1. sidecar：2 秒收不到 ACK 就 `exit(3)`
2. host：进程死了/6 秒没有事件就重启并重新枚举（1→2→4→5 s 退避）
3. 前端：`session` 变化或 `ok:0` 回执就整帧重推；另外每 60 秒无条件整帧兜底

## 8. 用 `read(n)` 读 ffmpeg 管道 → 丢帧

**现象**：屏保每次启动几帧就退出，报 `ffmpeg 退出码 -9`。

**原因**：管道 `read(n)` 可能只返回一部分，按「不足一帧就退出」处理会立刻中断。

**结论**：必须循环读到 2 048 000 字节整帧；另外「手动播 vs 锁屏播」要分开标记，
否则锁屏监视线程会在解锁时把手动启动的屏保也停掉。

---

## 值得记一笔的小坑

- `.cs` / `.js` 里中文字面量：**UTF-8 without BOM** 才是跨工具安全的选择（Windows 的 csc 时代要求带 BOM）
- `payload_hash` 空 payload = `0x02cc5d05`，不是 0
- `COMMIT` 必须 `flags=1` 才允许后续 `PATCH`
- 设备 `PATCH` 的 tile 固定 32×16，布局/脏矩形都要对齐它
- **一次写的长度（32 B 头 + payload）正好是 512 的整数倍 → HCD 补 ZLP → 固件接收超时 `status=8`**。
  看起来像“LZ4 解码坏 / 设备忙 / 自动重连”，实际 ACK 里 `rx≈3 s`、`dec=0`、`draw=0`。
  拆成 `len-1` + `1` 两次写即可（`qdc1.c` 的 `raw_write_all()`），QDC1 是字节流，设备无感。
- Tauri 打包资源是**显式列举**的：新增 `web/**` 文件忘了加，打包版就会白屏

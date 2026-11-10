# 固件：刷 QDisplay / 恢复 QuecPython

设备上有两个可选固件。**当前设备是 QDisplay native**（能显示任意画面）。

| 固件 | 状态 | 用途 |
|---|---|---|
| QDisplay native APP **0.3.0**（`win-display/qdisplay/qdisplay_native.bin`，154400 B） | ✅ 本仓默认 | `features=0x23f`；无待机/唤醒，等待页只画一次 |
| QDisplay native APP **0.3.1**（`win-display/qdisplay/qdisplay_native_031.bin`，155456 B） | ⏸ 可选（上游最新，**本机实测不稳，未采用**） | `features=0x27f`，多 bit `0x40`；增加 HELLO 18 待机/唤醒、90 s 断连关背光，**等待页带 90 s 倒计时（每秒重绘 → 每秒闪，可用 `patch_waiting_page.py` 关掉）**。2026-11 实测：CRC 正确的官方 0.3.1 能刷入并启动（读到过 `features=0x27f`），但 QDC1 **间歇性不应答**（12 次探测 5 次无响应，同一会话第 2–3 条命令也常超时），像 APP 崩溃重启循环；同机 0.3.0 稳定 → 已回退 0.3.0 |
| QuecPython（`fw.pac` 整包） | ⏸ 可回退 | 原来的 Python 环境（上不了任意画面，见 `docs/hardware.md` §6） |

> **刷机只能在 Windows 上做**：进入下载模式后设备枚举成 `0525:a4a7`（CDC 形态），
> Windows 会自动给它绑 `usbser.sys` 生成 COM 口，脚本走**串口**跑 PDL/FDL 协议；
> macOS 不给这个下载口建 `/dev/cu.*`（BootROM 口 `1782:4d00` 则直接 `Access denied`）。
> 已经刷好的设备不受影响，Mac/Windows 都能正常推帧。

---

## 0. 要不要拆机？（买第二台时先看这里）

| 设备状态 | 拆机 | 怎么刷 |
|---|---|---|
| **新机 / 固件完好**（能开机、AT 口有响应） | **不用** | 只插 **Type-C 数据线 + DC 12V**，Windows 上跑 `qdisplay_app.py`（软下载：发 `AT+QDOWNLOAD=1` → 设备自己重枚举成 `0525:a4a7`） |
| 刷坏了 / 不启动 / AT 口 60 秒没响应 | **要拆** | 拆壳露出模组，把 **Pin 55（NET_STATUS / USB_BOOT）短接 GND 后上电** → 进 BootROM `1782:4d00` → 再跑同一个脚本（`--no-verify` 或 `flash_at.py`） |

也就是说：**正常新设备全程无损、免拆机、纯 Type-C 软件刷写**；只有「刷死救砖」才需要开壳碰板子。

### 首次刷机准备清单

| 项 | 说明 |
|---|---|
| Windows 10/11 x64 | 刷机唯一可行平台（macOS 上 BootROM/下载口 I/O 被拒） |
| Type-C **数据**线 + DC 12V/2A | 线只供电不传数据是第一次失败最常见的原因 |
| `win-flash/flash/quectel-usb-driver.zip` | 装完能在设备管理器看到 `Quectel USB AT Port (COMx)` |
| `win-flash/flash/py/` | 便携 Python：解压 `python-embed.zip` + 装 `pyserial.whl`（**只需要 pyserial**） |
| ~~pyusb / libusb / Zadig~~ | **不需要**。`third_party/sprdflash/usb_transport.py` 是另一条未使用的 libusb 路径，我们的脚本全部走串口 |

### 刷机需要的文件（全部在仓库里）

| 文件 | 位置 | 校验 |
|---|---|---|
| QDisplay APP 固件 | `win-display/qdisplay/qdisplay_native.bin` | **154 400 B**（写 `0x60260000`） |
| 官方 PAC（FDL1/FDL2 来源，也是恢复包） | `win-flash/flash/fw.pac` | 6.5 MB，18 条目，含 `fdl1@0x8000c0`(14 208 B) / `fdl2@0x810000`(72 384 B) |
| 写 APP 的脚本 | `win-flash/flash/qdisplay_app.py` | `--auto` 自己发 `AT+QDOWNLOAD=1` |
| 刷回 QuecPython 的脚本 | `win-flash/flash/flash_at.py` | 会格式化文件系统 |
| 刷写器 | `win-flash/flash/third_party/sprdflash/` | 纯 Python，走串口（无需厂商工具） |
| 便携 Python | `win-flash/flash/python-embed.zip` | 解压成 `py/`（gitignore，不随仓库分发） |
| pyserial | `win-flash/flash/pyserial.whl` | 唯一需要的第三方包 |
| Quectel USB 驱动 | `win-flash/flash/quectel-usb-driver.zip` | 出 AT 口必需 |
| QuecPython 官方固件（备份） | `win-flash/QPY_OCPU_V0004_EC600U_CNLB_FW.zip` | 6.7 MB |

也就是说：**除了 Windows 系统和一根能传数据的 Type-C 线，其余全在仓库里**。
唯一不随仓库走的是 `py/`（解压出来才有，`.gitignore` 里）。

### 「无损」到底无损在哪

- `qdisplay_app.py` **只写 `0x60260000` 的 APP 分区**：不格式化文件系统、不动 NV / IMEI / 射频校准
- 但 APP 分区里原来的固件会被覆盖 —— 想恢复原厂/QuecPython 要走 `flash_at.py` 刷 `fw.pac`
  （这一步**会**格式化文件系统 + 重建 NV）
- 刷完不需要重新做任何校准，设备还是同一台设备的同一套参数

---

## 1. 刷 QDisplay（只写 APP 分区，不格式化、不动 NV）

前置：
- 设备核心是 `EC600UCNLBR03A04M08_OCPU_QPY`（R03）
- `win-flash/flash/` 在仓库里；需要 `py\python.exe`（解压 `python-embed.zip` + 装 `pyserial.whl`）
- 固件 `win-display/qdisplay/qdisplay_native.bin`

```bat
cd win-flash\flash
py\python.exe qdisplay_app.py ..\..\win-display\qdisplay\qdisplay_native.bin --auto
```

流程：`AT+QDOWNLOAD=1` → USB 重新枚举成 `0525:a4a7` → PDL → FDL1 → FDL2 →
写 `0x60260000`（154 400 B）→ 读回校验 → 复位。

**踩过的坑**：社区官方 `qflash.py` 走串口 BSL，在本机驱动上会卡死在 FDL2。
本项目改用 `third_party/sprdflash` 的 native（libusb）路径，也就是上面的 `qdisplay_app.py`，稳定可用。

## 1.5 改 BIN 必须重算头部 CRC32（重要）

`qdisplay_native.bin` 是 **APP2 容器**，头部 32 字节里有一个**整文件 CRC32**（对比官方 0.3.0 / 0.3.1 两个 BIN 实测得出）：

| 偏移 | 内容 |
|---|---|
| 0 | `2PPA`（u32 `0x41505032`） |
| 4 | 文件长度（必须 = 实际字节数） |
| **8** | **`crc32(整个文件，计算前 @8 视为 0)`** |
| 12 | `0x00010000` |
| 16 / 20 | `appimg_enter` / `appimg_exit` 地址（0.3.0: `0x60265c49`/`0x60265c71`） |
| 24..32 | 0 |
| 32..128 | 16 字节一条的装载描述符 `(kind, offset, size, address)` |

**只改 1 个字节也必须重算 @8**，否则 loader 直接不启动 APP，表现为：

- 刷写成功、读回校验也一致（字节确实写进去了）
- 但 **APP 不跑**：屏黑、`COM15` 一个字节都不回（QDC1 探活失败）
- **AT 口（COM7）正常**（`ATI` 能回应）——这是区分「APP 不启动」和「设备变砖」的关键

连只改文件**末尾填充区**（0x25B10，不属于任何描述符）也会不启动，说明校验的是整个文件。
`win-flash/flash/appimg.py` 就是干这个的：`fix()` 重算、`check()` 校验；
`patch_waiting_page.py` / `patch_waiting_page_image.py` 都已接上；自己写新补丁脚本时 `import appimg` 后调用
`appimg.fix(data)` 即可。

> 踩坑记录：2026-11 第一次改 `panel_init`（`B4 80->60`）和改 MIPI 消隐时都“黑屏、APP 起不来”，
> 当时归因于“寄存器改坏了/时序锁不住”，**实际都是没重算 CRC**。重算后这两类变体都能正常启动。

## 1.6 换「等待连接」页图片（黑底纯白 → 不闪）

等待页不是画出来的，而是固件里的一段 **LZ4 块**：渲染代码是

```
movw r0,#0x88fc ; movt r0,#0x6027   → waiting_page_lz4 = 0x602788fc
mov.w r3,#0x1f4000                   → QD_FRAME_BYTES = 2048000
movw r2,#0xc65f                      → sizeof(waiting_page_lz4) = 50783
bl LZ4_decompress_safe               → 解压成 800×1280 BGR565 整帧
```

所以换页面 = ①在同样的 flash 偏移写一段新 LZ4 块（必须 ≤ 原长度）②改 `movw r2,#…` 的
立即数 ③重算 APP2 的 CRC32（见 1.5）。**不用重编固件。**

```bat
cd win-flash\flash
:: 生成页面（需要 pillow + lz4；只在这一步需要）
::   --github "github.com/xxx/yyy" 想在第三行显示一个地址时用（默认空 = 显示 --tag 那句 WAITING FOR HOST）
::   --logo 在地址前加 GitHub 猫咪图标（默认不加）
::   --title/--sub/--tag 改文案；--aa 保留抗锯齿（会闪）；--levels K / --floor V 折中
python make_waiting_page.py            :: → waiting_page/page.png + page.bgr565.lz4 + page.meta
:: 刷机侧只依赖 stdlib：
py\python.exe patch_waiting_page_image.py ..\..\win-display\qdisplay\qdisplay_native.bin qdisplay_native_wait.bin
py\python.exe qdisplay_app.py qdisplay_native_wait.bin --auto
:: 回退原厂页面：revert-firmware.cmd
```

设计原则：**只用纯黑（0x0000）/纯白（0xFFFF），并且关掉抗锯齿**——实测这颗屏白/黑/纯色不闪，
中间灰会闪；抗锯齿的过渡像素就是中间灰。`make_waiting_page.py` 最后会阈值成 1-bit，
产出的整帧只有 2 种颜色（`page.meta` 里可查）。

实测（2026-11，本机）：原厂页 50783 B / 415 种颜色 → 新页 **11658 B / 2 种颜色**，
刷入后 `VERIFY` 回读的 framebuffer 哈希 == 新页面 xxh32，屏上显示
![新等待页](waiting-page-design.png)。

> 注意：`HELLO flags=8`（`QD_WAITING_HASH`）是**编进固件的旧页面哈希**，换页面后它不会跟着变，
> 别拿它当“当前屏上是不是等待页”的判据；要看就用 `VERIFY` 和 `page.meta` 里的 `xxh32` 比。

## 2. 恢复成 QuecPython

```bat
cd win-flash\flash
py\python.exe flash_at.py          :: 按 fw.pac 整包刷 + 格式化文件系统 + 重建 NV
```

`win-flash/QPY_OCPU_V0004_EC600U_CNLB_FW.zip` 是同版本的官方 QuecPython 包（备份用）。

## 2.5 关掉等待页每秒重绘（0.3.1+，可选）

0.3.1 固件的「等待连接」页带 90 s 倒计时：`waiting_countdown()` 每秒把数字写进 framebuffer 后
`submit_frame()`（2 MB cache clean + DSI 整帧提交），单缓冲下就是**每秒闪一下**。
`patch_waiting_page.py` 只改 1 字节，让这个函数每次都提前 return：等待页只画一次，从此真静态
（倒计时数字不再出现），90 s 适时关背光 / 按键调亮度都不受影响。

```bat
cd win-flash\flash
py\python.exe patch_waiting_page.py qdisplay_native.bin    :: 官方 0.3.1 = 155456 B / sha256 4af5b2…
py\python.exe qdisplay_app.py qdisplay_native_static.bin --auto
:: 回退：把原来的 qdisplay_native.bin 再刷一次
```

脚本会先核 APP2 容器长度与字节指纹：本仓 0.3.0 的 154400 B bin **没有**倒计时那段代码，会直接报「不用打补丁」。
如果屏上还闪，按 [`troubleshooting.md`](troubleshooting.md#屏闪) 的四步区分背光 PWM / 面板 / 供电。

## 3. 刷完怎么算成功

1. 屏上出现固件内置的「等待连接」页（几秒没有主机会显示）
2. 客户端握手成功：`/api/status` 里 `ready.block = 520192`（508 KiB 接收块）、特性位 `0x23f`
3. 推一帧后 `VERIFY` 返回的设备哈希与本机整帧哈希一致

## 4. 目录说明

```
win-flash/
├── QPY_OCPU_V0004_EC600U_CNLB_FW.zip  官方 QuecPython 固件（恢复用）
└── flash/
    ├── qdisplay_app.py                刷 QDisplay APP（写 0x60260000）
    ├── flash_at.py                    刷回 QuecPython（整包 + 格式化）
    ├── fw.pac                         官方 PAC
    ├── third_party/sprdflash/         纯 Python 刷写器（PDL/FDL over libusb）
    ├── quectel-usb-driver.zip         Windows USB 驱动
    ├── python-embed.zip               便携 Python（解压到 py/）
    └── pyserial.whl
```

## 5. 硬刷兜底（设备彻底不枚举时）

EC600U-CN 的 **Pin 55（NET_STATUS / USB_BOOT）短接 GND 后上电**，会直接进 BootROM 下载模式，
再跑一次 `qdisplay_app.py` 或 `flash_at.py`。这是唯一能从「刷坏/不启动」里救回来的办法。

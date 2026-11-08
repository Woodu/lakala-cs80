# 固件：刷 QDisplay / 恢复 QuecPython

设备上有两个可选固件。**当前设备是 QDisplay native**（能显示任意画面）。

| 固件 | 状态 | 用途 |
|---|---|---|
| QDisplay native APP（写在 `0x60260000`，154 KB） | ✅ 日常使用 | 接受 QDC1 帧，LZ4 → framebuffer → DSI 上屏 |
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

## 2. 恢复成 QuecPython

```bat
cd win-flash\flash
py\python.exe flash_at.py          :: 按 fw.pac 整包刷 + 格式化文件系统 + 重建 NV
```

`win-flash/QPY_OCPU_V0004_EC600U_CNLB_FW.zip` 是同版本的官方 QuecPython 包（备份用）。

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

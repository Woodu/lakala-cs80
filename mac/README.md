# Mac 上驱动这块屏（可行性 + 步骤）

## 先给结论

| 层 | 跨平台？ | 说明 |
|---|---|---|
| **设备固件（QDisplay）** | ✅ 是 | 只认 USB 上的 QDC1 帧协议，跟主机是 Windows/Mac/Linux 无关 |
| **协议层（`Core/Protocol.cs`）** | ✅ 是 | 源码里明写 `// This file has no Windows dependency; it is shared by future OS transports.` |
| **官方上位机（`QDisplay.exe`/`QDisplay.Service.exe`）** | ❌ 只有 Windows | C#/.NET Framework 4.8 + 大量 Win32：GDI+ 渲染、`GetSystemTimes`/`GlobalMemoryStatusEx`、DXGI 截屏、虚拟屏驱动、托盘 UI |

所以 **Mac 能做，但要自己写一个客户端**，复用协议、重写传输和渲染。

## Mac 端要写什么

1. **传输**：`pyusb`（libusb）claim 厂商接口，bulk 端点收发
   - 本仓库 `tools/usbserial.py` 已在 macOS 上验证 **if2~if7 可 claim 并收发**
   - 但 README §2.1 也记录过 **MI_20/if32 claim 报 EINVAL**（macOS 不放给用户态）→ **数据口到底是哪个接口必须实测**
2. **协议**：QDC1
   - 28 字节小端头：`magic=0x31434451("QDC1") / version / kind / flags / seq / offset / length / raw / check`
   - 校验：XXH32 变体（`Core/Protocol.cs::Hash32`，本目录 `cs80_probe.py` 里已实现并**与已验证实现比对通过**）
   - 帧：BGR565，`800*1280*2 = 2048000` 字节，按 128 KB 或 508 KB 分块，块内 **LZ4** 压缩（`codec=1` 表示 LZ4）
   - 命令：HELLO(1) / BEGIN(2) / FRAME(3) / … / COMMIT / VERIFY，参考 `desktop/src/Core/Protocol.cs`
3. **渲染**：把 Windows 那边的 `Dashboard.Draw()`（GDI+）换成 **Pillow** 画同样布局，再转 BGR565
   - Windows 的转换是 `Pictures.Pixels()`：`((B&248)<<8)|((G&252)<<3)|(R>>3)`，小端

## 第一步：先探通再动手

```bash
sudo pip3 install pyusb
sudo python3 mac/cs80_probe.py
```

它会：枚举 `2c7c:0901` 的所有接口 → 逐个尝试 claim → 发 QDC1 HELLO → 校验回复。

- **打印 `✅ 可用接口: [...]`** → Mac 客户端可行，接着写帧传输 + 渲染
- **打印 `❌ 没有接口回 QDC1 HELLO`** → macOS 没把数据口放给用户态，看下面的替代方案

## 替代方案（不想写客户端时）

1. **虚拟机 USB 直通**：Parallels / VMware Fusion 里跑 Windows，USB 直通给 VM，直接用现成上位机（最省事）
2. **一台 Windows 小主机/迷你机**常开，Mac 通过 SSH/VNC 控制它
3. **等我写 Mac 客户端**：协议已经摸清，Pillow 版 `Draw()` 也能照搬，主要风险是上面那个「接口能否 claim」

## 已知的 macOS 限制（来自本项目实测）

- CDC-ECM（if0/1）→ 有网卡但无载波（不是 QDC1 通道）
- 厂商串口 **没有 `/dev/cu.*`**，必须走 libusb
- MI_20 / if32 → `claim` 报 `EINVAL`
- BootROM 下载口（`1782:4d12`）→ `Access denied`（所以**刷机仍然只能用 Windows**，本机已经刷好了，不受影响）

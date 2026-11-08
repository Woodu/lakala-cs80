# 在 Windows 上验证 / 运行（面板版）

macOS 侧已经全部实测通过（数字见 [`protocol.md`](protocol.md) §5）。Windows 侧**代码写好了但没有实机验证**，
下面是从零到验收的完整步骤，以及每一步失败该怎么查。整个过程约 20 分钟。

---

## 0. 前置

| 需要 | 用途 | 检查 |
|---|---|---|
| Python 3.10+ | 跑 `host.py` | `python --version` |
| VS Build Tools（C++ 工作负载）或 MSYS2 | 编译 sidecar `qdc1.exe` | `cl` 或 `gcc --version` |
| Node 20+ | 编 Tauri 桌面壳（可选，只想先跑网页就不用） | `node --version` |
| Rust 1.77+（MSVC toolchain） | 同上 | `rustc --version` |
| WebView2 Runtime | 桌面壳用（Win11 自带，Win10 大概率已有） | 控制面板→程序 |
| Quectel / Unisoc 串口驱动 | 设备出 COM 口 | 设备管理器→端口 |

---

## 1. 编译 sidecar

`qdc1.c` 里 `_WIN32` 分支走厂商虚拟串口（不依赖 libusb）。

```bat
:: VS 开发者命令提示符（x64）
cd panel
cl /O2 /Fe:qdc1.exe qdc1.c lz4.c
```

MSYS2 / MinGW：

```bash
gcc -O2 -o qdc1.exe qdc1.c lz4.c -lws2_32
```

编译产物必须放在 `panel\qdc1.exe`（`host.py` 会按这个名字找）。

---

## 2. 找到 QDC1 的数据串口（关键一步）

设备插上后应该出现若干个 COM 口。QDC1 只挂在**其中一个**上，用探针模式直接问：

```bat
set PATH=%PATH%;C:\path\to\panel
qdc1.exe --probe
```

输出形如：

```json
{"com":"COM5","qdc1":false,"block":0}
{"com":"COM6","qdc1":false,"block":0}
{"com":"COM15","qdc1":true,"block":520192}
```

看到 `"qdc1":true` 的那个口就是：

```bat
set CS80_COM=COM15
:: 或者起 host 时带参数
python panel\host.py --com COM15
```

判断依据：只有数据口会回一个 56 字节的 QDC1 ACK（`block=520192` = 508 KiB 接收块）。
**别靠端口号猜**：官方上位机是硬编码读 `HKLM\...\VID_2C7C&PID_0901&MI_20` 的 `PortName` 的，
所以社区才会去伪造那个键；我们的 sidecar 直接按 `CS80_COM` 打开，不需要动注册表。

排查：

| 现象 | 处理 |
|---|---|
| 没有 VID_2C7C 的设备 | 供电/数据线问题；接 DC 12V、换 Type-C 数据线 |
| 有设备但没有 COM 口 | 装 Quectel USB 驱动；或设备管理器里看有没有黄色感叹号 |
| `--probe` 全部 false | 固件是不是 QDisplay（屏上应显示「等待连接」页），不是就先刷固件 |
| 某个口被其他程序占用 | 关掉官方 `QDisplay.Service`（`sc stop QDisplayDevice`）再试 |

---

## 3. 冒烟测试（不上 UI，先确认链路）

```bat
:: 终端 A
set CS80_COM=COM15
python panel\host.py --port 8787
```

```bat
:: 终端 B —— 每一条都应该有 JSON 回来
curl http://127.0.0.1:8787/api/status
curl http://127.0.0.1:8787/api/data/system
curl -X POST -H "Content-Type: application/json" -d "{\"cmd\":\"state\"}" http://127.0.0.1:8787/api/control
curl -X POST -H "Content-Type: application/json" -d "{\"cmd\":\"brightness\",\"value\":50}" http://127.0.0.1:8787/api/control
curl -X POST -H "Content-Type: application/json" -d "{\"cmd\":\"wake\"}" http://127.0.0.1:8787/api/control
```

期望：`/api/status` 里 `device` = `up`，`ready.block` = 520192；调亮度后屏会短暂出现 OSD 亮度条。

然后浏览器打开 <http://127.0.0.1:8787/> —— 画布一加载就会推整帧，屏上应立刻出现组件画面，
终端里 `/api/status` 的 `last` 会变成 `patch`，`wire` 在 1–10 KB 量级。

> 数据源在 Windows 上的差异：`system` 组件用 ctypes 读 `GlobalMemoryStatusEx`，CPU 优先用
> `pip install psutil`（没装就显示 0）；`music` 组件在 Windows 上返回「需要自己接 SMTC」，属预期。

---

## 4. 桌面壳（可选）

```bat
cd panel\shell
npm install
npx tauri dev            :: 开发模式，改 web/ 里的文件刷新即生效
npx tauri build          :: 出 MSI/NSIS 安装包
```

壳只做四件事：拉起 `host.py` → 等端口就绪 → 开窗口（加载 `http://127.0.0.1:8787/`）→ 托盘 + 关窗不退出。
`host.py` 找不到时会读 `CS80_PANEL_DIR`；打包后的资源在安装目录 `Resources\panel\`。

开机自启：把桌面壳的快捷方式丢进 `shell:startup`；或者用 `nssm` 把 `python host.py` 注册成服务
（注意服务是 Session 0，画布窗口仍需用户登录后运行 → 推荐前者）。

---

## 5. 验收清单

| # | 检查 | 期望 |
|---|---|---|
| 1 | `qdc1.exe --probe` | 恰好一个口 `qdc1:true` |
| 2 | `/api/status` | `device=up`，`ready.block=520192`，`features.bits=0x23f` |
| 3 | 打开画布 | 屏上出现 5×8 网格排布的组件，不是「等待连接」页 |
| 4 | 等 1 分钟 | `last` 一直是 `patch`，`code=0`（patch 的 base 哈希必须与设备一致，否则回 4） |
| 5 | 拖一个组件 | 松手后整帧重推一次，屏上布局跟着变 |
| 6 | 改亮度滑杆 | OSD 亮度条出现、亮度变化 |
| 7 | 点「待机 / 唤醒」 | 背光灭 / 亮 |
| 8 | 关掉窗口 | 5 秒后屏回「等待连接」页（预期行为，因为没人推帧了） |
| 9 | 重开窗口 | 自动整帧重推，画面立刻回来 |

第 4 项是**内容正确性的硬证据**：局部更新的 `offset` 必须是设备上一帧的哈希，
对不上固件直接 `status=4`。持续 `code=0` 就说明屏上像素与本机渲染逐字节一致。

---

## 6. 故障排查

| 现象 | 原因 / 处理 |
|---|---|
| `host.py` 起来但 `device=no-sidecar` | `panel\qdc1.exe` 不存在或没编译 → 回到第 1 步 |
| `device=down` 反复重启 | sidecar 打印的错误被吞了：直接跑 `panel\qdc1.exe < nul`（需要 `CS80_COM`），看 stderr |
| 屏上一直是「等待连接」页 | ① 没打开画布页面（窗口关了就没人推帧）② `CS80_COM` 指错口 ③ 固件不是 QDisplay |
| `patch` 一直 `code=4` | 设备侧 committed 哈希与本机不一致（换过设备/固件重启）→ 点「整帧重推」 |
| `patch` 报 `too-many-tiles` | 改了布局又同时大量变化 → 客户端会自动改推整帧，不用管 |
| 整帧很慢（>1 s） | 串口波特率/DTR 设置被驱动改过；确认 115200 8N1、DTR/RTS 关闭（sidecar 已在打开时设置） |
| 组件里有中文方框 | 画布字体回退到了无中文字体 → 装「微软雅黑」或改 `app.js` 里的 `F()` 字体栈 |
| 数据源拿不到 | 公司网络/代理挡了新浪、东财、open-meteo、GitHub；组件会保留上次值并显示占位 |

---

## 7. 已知未验证点（Mac 上验证过，Windows 上需要你确认）

1. **COM 口映射**：驱动是否把 USB 接口 32 枚举成 COM 口（`--probe` 会直接告诉你）。
   如果所有口都 `qdc1:false`，说明驱动没给这个接口建口 —— 那时才需要备选方案（用 libusb + WinUSB
   直接 claim 接口 32，Windows 上 libusb 没有 macOS 的限制，但要替换驱动，体验变差）。
2. **大块写入**：单次 512 KB 的 `WriteFile` 在该驱动上是否稳定（Mac 侧 20 ms/帧，Windows 可能更慢）。
   出现超时就把它当「整帧慢」处理，功能不受影响。
3. **待机/唤醒字段位**：不同固件版本语义有差异，UI 用回读兜底（`/api/control {cmd:"state"}`）。
4. **桌面壳打包**：`npx tauri build` 在 Windows 上会出 MSI/NSIS，代码路径与 macOS 共用，
   但图标/签名没做过（自用够了）。

把上面 1、2 的实测结果补进本节，Windows 侧就算闭环了。

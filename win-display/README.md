# win-display —— Windows 侧工具（当前方案）

这块屏现在是 **QDisplay native 固件**，Windows 上由 `QDisplayDevice` 服务经 USB（QDC1 协议）
把整帧画面推给设备，实测 30 fps。本目录放**日常要用的东西**和**调试工具**。

> 想了解整机方案（为什么换固件、屏幕能力边界、刷机与恢复），看上一级 [`../README.md`](../README.md)。

---

## 1. 日常唯一要用的：`agent_state.py`

```bat
set PY=..\win-flash\flash\py\python.exe

%PY% agent_state.py running "重构 auth 中间件"
%PY% agent_state.py waiting
%PY% agent_state.py done
%PY% agent_state.py error "测试挂了"
%PY% agent_state.py idle
```

它写 `%USERPROFILE%\cs80_display\state.json`：

```json
{"state": "running", "task": "重构 auth 中间件", "since": 1762530000.0}
```

- `state`：`idle` / `running` / `waiting` / `done` / `error`
- `since`：epoch 秒，屏上「已持续 HH:MM:SS」用它算
- **同一状态 + 同一任务重复设置不会重置计时**

**服务每秒重读这个文件**，所以任何程序（agent 收尾钩子、CI、shell 脚本）写它都行。

---

## 2. 自定义屏上画面

屏上的页面 = PC 端画的位图（`Dashboard.Draw()`，GDI+）。改它就能改屏：

```
qdisplay/build/Graphics.cs     ← 布局都在这（当前：日期/大时钟/agent 状态/任务/CPU/内存/网络）
```

```bat
:: 1) 编译（用系统自带的 .NET Framework 4.8 编译器）
cd qdisplay\build
build.bat

:: 2) 部署（管理员）：停服务 → 备份 .orig → 覆盖 DLL → 起服务
..\install_dll.bat
```

改完看一眼屏，或跑 `camera_shot.ps1` 拍照确认。

**注意**
- `.cs` 必须 **UTF-8 with BOM**（否则中文编译后乱码）
- `csc.exe` 只支持 **C# 5**：不要用 `$""` 插值、`?.`
- `qdisplay/build/` 里的其它 `.cs`（`Backend.cs`、`MainWindow.cs` 等）是原项目文件，
  只有当你要改行为时才需要动；**只改画面就只编 `QDisplay.Windows.dll`**
- 回滚：`C:\Program Files\QDisplay\QDisplay.Windows.dll.orig`

---

## 3. 一次性环境修复：`qdisplay/make_mi20.ps1`

上位机找数据口是**硬编码**的：

```
HKLM\SYSTEM\CurrentControlSet\Enum\USB\VID_2C7C&PID_0901&MI_20\<实例>\Device Parameters\PortName
```

而本机 Quectel 驱动把这台设备的数据口枚举成 **`MI_08`（描述含 `REPL Port`，如 COM15）**，
注册表里没有 `MI_20` → GUI 永远停在「正在连接设备」。

跑一次（管理员）：

```powershell
powershell -ExecutionPolicy Bypass -File qdisplay\make_mi20.ps1
Restart-Service QDisplayDevice -Force
```

之后 `C:\ProgramData\QDisplay\service.log` 应出现 `USB connected COM15`。

> 换 USB 口后 COM 号会变，改脚本里的 `COM15` 再跑一次。

---

## 4. 诊断工具

| 脚本 | 用途 | 典型用法 |
|---|---|---|
| `camera_shot.ps1` | 用本机摄像头拍设备屏幕（MediaCapture，无窗口） | `-Out out.jpg` |
| `cam_stats.ps1` | 对截图按纵向分带输出平均 RGB + 色相 | `-Img out.jpg` |
| `window_grab.ps1` | 截取指定标题的窗口 | `-Title QDisplay -Out gui.png` |
| `list_windows.ps1` | 枚举当前可见窗口（找弹窗/确认窗口类） | 无参 |
| `click_button.ps1` | 按控件名点 WinForms 按钮（`BM_CLICK`） | `-List` 先看控件 |
| `display_modes.ps1` | 列出每块显示器的全部可用模式 | 无参 |

**为什么需要摄像头**：屏幕很亮，手机/摄像头自动曝光会把画面拍成一片白，
所以判断「屏上到底是什么颜色/有没有内容」时，用 `camera_shot.ps1` + `cam_stats.ps1` 看数值比肉眼可靠。

`click_button.ps1` 的背景：QDisplay GUI 是 WinForms 自绘控件（UIA 全是 `Pane`，`Invoke` 无效），
而坐标点击在 150% DPI 缩放下会偏，所以最终方案是**枚举子窗口句柄 + 发 `BM_CLICK`**：

```
powershell -ExecutionPolicy Bypass -File win-display\click_button.ps1
powershell -ExecutionPolicy Bypass -File win-display\click_button.ps1 -List   :: 先列出控件
```

> 脚本必须存成 **UTF-8 with BOM**，否则 `-Want '副屏'` 里的中文会乱码匹配不到。

---

## 5. 常用排查

```powershell
Get-Service QDisplayDevice                      # 应为 Running
Get-Content C:\ProgramData\QDisplay\service.log -Tail 10   # 找 "USB connected COMxx"
Restart-Service QDisplayDevice -Force           # 设备重插后
```

屏显示「等待连接」= 没收到帧，检查服务 + 上面的数据口映射。

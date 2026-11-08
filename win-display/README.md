# win-display/ —— 官方 Windows 上位机（备份）与屏上内容验证

我们的客户端在 `panel/`，跟这里无关。本目录只留两样东西：

| 文件 | 用途 |
|---|---|
| `qdisplay/qdisplay_native.bin` | **QDisplay 固件 APP**（刷到 `0x60260000`，刷机流程见 [`../docs/firmware.md`](../docs/firmware.md)） |
| `qdisplay/QDisplay-0.3.0-x64.msi` | 官方 Windows 上位机安装包（装完注册 `QDisplayDevice` 服务）。只在对照/临时排障时用 |
| `camera_shot.ps1` | 用摄像头拍屏幕（无窗口），用来客观确认屏上到底显示什么 |
| `cam_stats.ps1` | 对拍到的截图做分带颜色统计（不用肉眼判断） |
| `agent_state.py` | 写 `%USERPROFILE%\cs80_display\state.json` 的小工具（跨平台，Mac 也能用） |

## 摄像头验证怎么用（Windows）

```powershell
powershell -ExecutionPolicy Bypass -File camera_shot.ps1 -Out status.jpg
powershell -ExecutionPolicy Bypass -File cam_stats.ps1 -Path status.jpg
```

`cam_stats.ps1` 会把画面按行分带统计主色 —— 改了配色/布局后，用它判断「屏上到底变了没有」比截图肉眼可靠。

> 早期还在这套官方上位机上改过 C# 仪表盘（换 DLL、伪造 MI_20 注册表键），那条路已废弃，
> 原因见 [`../docs/dead-ends.md`](../docs/dead-ends.md) §5。

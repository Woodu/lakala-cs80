# archive/ —— QuecPython 时代（历史留档，不要再用）

这些工具属于「换固件之前」的方案。**结论已经证伪**：QuecPython 下 `lcd_write`/`lcd_show` 都无法上任意画面
（根因见 [`../docs/dead-ends.md`](../docs/dead-ends.md) §1）。保留它们只是为了记录当时的做法和接口细节。

| 文件 | 当时的作用 |
|---|---|
| `cs80.py` / `main.py` | 设备端的点屏 + 帧解码脚本（跑在设备上的 Python） |
| `deploy.py` / `device_exec.py` | 通过 raw REPL 部署/执行设备侧脚本 |
| `panel_init.py` / `gen_panel.py` | 原厂面板初始化表（SC7705 800×1280） |
| `at_test.py` / `刷机_AT.bat` | AT 命令探测与刷机入口 |

现在要显示内容，走 `panel/`（QDisplay 固件 + QDC1 协议）。

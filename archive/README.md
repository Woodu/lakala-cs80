# archive —— 历史留档（当前流程不用）

这里的代码**不参与现在的部署流程**，保留只是为了「以后想回 QuecPython 侧折腾时不用从零再来」。

## quecpython/

这台设备最早刷的是官方 QuecPython（`EC600UCNLBR03A04M08_OCPY`）。当时的工具链：

| 文件 | 作用 |
|---|---|
| `cs80.py` | 设备端屏控：`mipi_init` + 背光 + `lcd_clear` + `lcd_show` |
| `main.py` | 设备端开机脚本（点屏 + 清底） |
| `panel_init.py` | 原厂 R05 逆出来的面板初始化表（SC7705，19 条命令 / 297 字节） |
| `gen_panel.py` | 从 R05 序列表生成上面的 `panel_init.py` |
| `deploy.py` | 上传设备端脚本 + 点屏测试 |
| `device_exec.py` | 通过 raw REPL 在设备上执行代码（不需要 QPYcom） |
| `at_test.py` | 逐个 Quectel 串口发 `AT`/`ATI`，判断设备在正常态还是下载态 |
| `刷机_AT.bat` | 早期刷机入口（软下载） |

### 当时的结论（已验证，但最终没用）

- `mipi_init()` 返回 0、背光（GPIO8）可控、`lcd_clear(颜色)` 能显示**任意纯色**
- 但 `lcd_write()` / `lcd_show()` 全部无法把画面送上去（详见主 README §5）
- USB 数据通道：ECM 网卡无 SIM 起不来；AT 文件命令不支持；最后靠 **raw REPL（~26 KB/s）** 通了
- 推帧链路（PC 渲染 RGB565 → zlib → REPL → 设备解压 → `lcd_write`）在传输层面跑通
  （整帧 1.7 s / 增量 0.2 s），**但 `lcd_write` 只会把屏写黑**

根因：面板是 video/burst 模式，每帧必须完整 2 048 000 字节，且提交前要做 **cache-clean + 写寄存器**，
这两件事只有 C 层能做。所以最终换成了 QDisplay native 固件（主 README §3）。

**想回去**：`win-flash/flash/flash_at.py` 重刷 `fw.pac`，然后把这里的 `cs80.py`/`main.py`/`panel_init.py`
用 `device_exec.py` 推回设备 `/usr` 即可。

## 其余（experiments/）

留空。以后临时验证性的脚本放这里，别污染主目录。

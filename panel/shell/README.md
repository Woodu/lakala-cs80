# panel/shell —— 桌面壳（Tauri 2）

只做四件事，像素/协议全在 `../host.py` + `../web/` 里：

1. 启动时拉起 `../host.py`（8787 已在跑就复用，避免两个进程抢 USB）
2. 等端口就绪再显示窗口（窗口加载 `http://127.0.0.1:8787/`，即画布编辑器）
3. 托盘：显示窗口 / 退出；**关窗只是隐藏**（窗口一关就没人推帧，屏 5 秒后回「等待连接」页）
4. 退出时把 host 子进程一起带走

另外带一个 **host 监督线程**：每 1.5 秒检查端口，host 死了就重新拉起（退避 2→10s）。
今天踩过一次：host 被外部 kill 后壳子不知道，面板就一直是空的。

## 开发

```bash
cd panel/shell
npm install
npx tauri dev            # dev 模式：host 读仓库里的 ../web/，改完刷新窗口即可，不用重编 Rust
```

⚠️ **打包版**（.app/.msi）里 host 服务的是 `Resources/panel/web/` 的副本，
所以改完 `panel/web/*.js` 必须重新 `npx tauri build`，否则窗口里还是旧页面
（踩过：改了组件却在打包版里看不到，因为 host 跑的是 bundle 内的旧 app.js）。

## 打包

```bash
npx tauri build                          # macOS: .app + .dmg；Windows: .msi + NSIS
npx tauri build --no-bundle              # 只要可执行文件（快，验证用）
```

产物：
- `src-tauri/target/release/bundle/macos/CS80 Panel.app`（约 6.7 MB）
- `src-tauri/target/release/bundle/dmg/CS80 Panel_0.1.0_<arch>.dmg`（约 2.2 MB）
- Windows 对应 `bundle/msi/*.msi`、`bundle/nsis/*.exe`

`.app` 内已包含 `Resources/panel/{host.py,qdc1,web/,README.md}`，所以是自包含的
（`qdc1` 是当前机器架构的 sidecar，换架构要重编）。

⚠️ 资源清单是**显式列举**的（`tauri.conf.json` 的 `bundle.resources`）：新增 `panel/web/*.js`、
换图标、加文档都要同步加进去，否则打包版里 host 找不到文件 → 窗口白屏/只出静态骨架。
（今天踩的坑：删了 `web/app.js` 却没删资源清单，`tauri build` 直接报 `resource path ... doesn't exist`。）

## 环境变量

| 变量 | 作用 |
|---|---|
| `CS80_PANEL_DIR` | 指向 `panel/` 目录（`host.py` 找不到时用；打包版会自动找 `Resources/panel`） |
| `CS80_PYTHON` | 指定 python 解释器（默认 `python3` / Windows `python`） |
| `CS80_COM` | Windows：设备数据串口，如 `COM15`（见 [`../../docs/windows.md`](../../docs/windows.md)） |

## 改窗口/托盘行为

- 窗口尺寸、是否显示：`src-tauri/tauri.conf.json` 的 `app.windows[0]`
- 托盘菜单项：`src-tauri/src/main.rs` 里 `MenuItem::with_id(...)`
- 开机自启：**没做插件**。macOS 丢进「系统设置 → 通用 → 登录项」；Windows 把快捷方式丢进 `shell:startup`。

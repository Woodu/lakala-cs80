// CS80 状态屏桌面壳
//
// 只做四件事（像素和协议都在 host.py / 画布 JS 里）：
//   1. 启动前拉起 panel/host.py（若 8787 已在跑就复用，不再起第二个去抢 USB）
//   2. 等 host 起来，再显示窗口（窗口加载的是 http://127.0.0.1:8787/ 的画布编辑器）
//   3. 托盘：显示窗口 / 退出；关窗不退出（关掉窗口就没人推帧，5 秒后屏会回「等待连接」页）
//   4. 退出时把 host 子进程带走
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::net::TcpStream;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{Manager, WindowEvent};

const PORT: u16 = 8787;

struct Host(Arc<Mutex<Option<Child>>>);

fn port_ready(port: u16) -> bool {
    TcpStream::connect(("127.0.0.1", port)).is_ok()
}

/// panel/ 目录：环境变量 > 打包资源 > 可执行文件旁 > 编译期源码路径
fn panel_dir() -> PathBuf {
    if let Ok(p) = std::env::var("CS80_PANEL_DIR") {
        return PathBuf::from(p);
    }
    if let Ok(exe) = std::env::current_exe() {
        let mut dir = exe.clone();
        dir.pop();
        for cand in [dir.clone(), dir.join("panel")] {
            if cand.join("host.py").exists() {
                return cand;
            }
        }
        // macOS .app: Contents/MacOS/<exe> -> Contents/Resources
        if let Some(contents) = exe.parent().and_then(|p| p.parent()) {
            for cand in [contents.join("Resources"), contents.join("Resources").join("panel")] {
                if cand.join("host.py").exists() {
                    return cand;
                }
            }
        }
    }
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..")
}

fn spawn_host() -> Option<Child> {
    if port_ready(PORT) {
        return None; // 已经有 host 在跑（比如手动起的），复用它
    }
    let dir = panel_dir();
    let script = dir.join("host.py");
    if !script.exists() {
        eprintln!("[cs80] 找不到 {}（设 CS80_PANEL_DIR 指向 panel/ 目录）", script.display());
        return None;
    }
    let python = std::env::var("CS80_PYTHON").unwrap_or_else(|_| {
        if cfg!(windows) {
            "python".to_string()
        } else {
            "python3".to_string()
        }
    });
    let log = std::fs::File::create(std::env::temp_dir().join("cs80_host.log")).ok();
    let mut cmd = Command::new(python);
    cmd.arg(script)
        .arg("--port")
        .arg(PORT.to_string())
        .current_dir(&dir)
        .stdin(Stdio::null());
    match log {
        Some(f) => {
            cmd.stdout(f.try_clone().ok()?).stderr(f);
        }
        None => {
            cmd.stdout(Stdio::null()).stderr(Stdio::null());
        }
    }
    match cmd.spawn() {
        Ok(c) => {
            eprintln!("[cs80] host 已启动 pid={}", c.id());
            Some(c)
        }
        Err(e) => {
            eprintln!("[cs80] 启动 host 失败: {e}");
            None
        }
    }
}

/// host 死了要能自己起来（端口占用竞争、python 崩了、被手动 kill 都属于这类）
fn supervise(host: Arc<Mutex<Option<Child>>>) {
    let mut backoff = 2u64;
    loop {
        std::thread::sleep(Duration::from_millis(1500));
        if port_ready(PORT) {
            continue; // 有人（可能是手动起的）在监听，别插手
        }
        let mut guard = host.lock().unwrap();
        let dead = match guard.as_mut() {
            Some(child) => matches!(child.try_wait(), Ok(Some(_))),
            None => true,
        };
        if dead {
            eprintln!("[cs80] host 不在了，重试启动（退避 {backoff}s）");
            *guard = spawn_host();
            backoff = if guard.is_some() { 2 } else { (backoff * 2).min(10) };
        }
    }
}

fn main() {
    let host = Arc::new(Mutex::new(spawn_host()));
    {
        let h = host.clone();
        std::thread::spawn(move || supervise(h));
    }

    tauri::Builder::default()
        .manage(Host(host))
        .setup(|app| {
            // 等 host 就绪再显示窗口，避免白屏
            let start = Instant::now();
            while !port_ready(PORT) && start.elapsed() < Duration::from_secs(20) {
                std::thread::sleep(Duration::from_millis(200));
            }
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.show();
                let _ = w.set_focus();
            }

            let show = MenuItem::with_id(app, "show", "显示窗口", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "退出（屏会回等待页）", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show, &quit])?;
            let mut tray = TrayIconBuilder::with_id("cs80")
                .menu(&menu)
                .show_menu_on_left_click(true)
                .on_menu_event(|app, ev| match ev.id.as_ref() {
                    "show" => {
                        if let Some(w) = app.get_webview_window("main") {
                            let _ = w.show();
                            let _ = w.set_focus();
                        }
                    }
                    "quit" => app.exit(0),
                    _ => {}
                });
            if let Some(icon) = app.default_window_icon() {
                tray = tray.icon(icon.clone());
            }
            tray.build(app)?;
            Ok(())
        })
        // 关窗只是隐藏：窗口一关就没人推帧了
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .build(tauri::generate_context!())
        .expect("Tauri 启动失败")
        .run(|app, event| {
            if let tauri::RunEvent::Exit = event {
                if let Some(host) = app.state::<Host>().0.lock().unwrap().as_mut() {
                    let _ = host.kill();
                }
            }
        });
}

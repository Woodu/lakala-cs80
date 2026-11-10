// 入口：把编辑器、数据调度、设备推送接起来
import { H, W, api, postJson, saveLayout, state, startDataLoop, startStatusLoop } from './core.js';
import { Pusher } from './pusher.js';
import { applyZoom, deleteCurrentPage, initEditor, renderAll, renderTile, tiles, toast } from './editor.js';

const el = (sel) => document.querySelector(sel);
const pusher = new Pusher();

function paintStats(st) {
  const pill = el('#push-pill');
  if (st.kind === 'error') {
    pill.textContent = '推送失败';
    pill.className = 'pill mono off';
    return;
  }
  const what = st.kind === 'full' ? '整帧' : st.kind === 'patch' ? `${st.tiles} tile` : '无变化';
  pill.textContent = `推送 ${what} · ${st.ms}ms`;
  pill.className = 'pill mono' + (st.kind === 'none' ? '' : '');
}

let saver = { files: [], file: '', autoLock: true, video: null, active: false, byLock: false };
let tickMs = 200;
let tickTimer = null;
let setRateFn = null;
let wasUp = null;
let lastFullPush = 0;
let loadedBuild = null;
let reloadedFor = null;

function paintDevice(st) {
  // 后端 web/ 变了（重新 build）就让页面自己重载，避免一直跑旧代码
  if (st.build) {
    if (loadedBuild === null) loadedBuild = st.build;
    else if (st.build !== loadedBuild && reloadedFor !== st.build) {
      reloadedFor = st.build;
      toast('检测到前端更新，正在重载…');
      setTimeout(() => location.reload(), 400);
      return;
    }
  }
  const up = st.device === 'up';
  const pill = el('#device-pill');
  pill.className = 'pill ' + (up ? 'on' : st.device === 'connecting' ? '' : 'off');
  el('#device-text').textContent =
    up ? '设备在线' : st.device === 'connecting' ? '连接设备…' : st.device === 'host-down' ? 'host 未运行' : '设备离线';
  // 设备重新上线（拔插/休眠唤醒后 sidecar 重启）→ 必须整帧重推，设备端 framebuffer 状态已丢
  if (up && wasUp === false) {
    pusher.markFull();
    toast('设备已重连，重推整帧');
  }
  wasUp = up;
  // 局部更新失败（设备换了/固件重启导致 base 哈希不匹配）→ 退回整帧
  const side = st.sidecar || {};
  if ((side.event === 'patch' || side.event === 'frame') && side.ok === 0) pusher.markFull();
  const b = st.state && st.state.brightness;
  if (typeof b === 'number') {
    el('#brightness').value = b;
    el('#brightness-val').textContent = b;
    el('#brightness').style.setProperty('--pct', b + '%');
  }
  const edges = st.state && st.state.edges;
  const bits = st.features && Number(st.features.bits);
  // 0x40 = QD_FEATURE_SCREEN_POWER：0.3.1 起才有（等待页也开始每秒重画倒计时）
  const fw = typeof bits === 'number' ? (bits & 0x40 ? '0.3.1+' : '0.3.0') : '—';
  const pwm = typeof edges === 'number' ? (edges ? `运行 ${edges}` : '停（100%）') : '—';
  el('#device-detail').innerHTML = up
    ? `块大小 <code>${(st.ready && st.ready.block) || '—'}</code><br>
       特性位 <code>${typeof bits === 'number' ? '0x' + bits.toString(16) : '—'}</code> · 固件 <code>${fw}</code><br>
       背光 <code>${typeof b === 'number' ? b + '%' : '—'}</code> · PWM <code>${pwm}</code><br>
       session <code>${st.session ?? '—'}</code> · 最近事件 <code>${side.event || '—'}</code>
       ${side.wire ? ` · ${side.wire} B` : ''}`
    : `未连上设备${st.reason ? '（' + st.reason + '）' : ''}<br>检查 Type-C 数据线 / 供电 / 官方上位机是否占着串口。<br>
       日志 <code>${(st.logs && st.logs.sidecar) || '~/.cs80_panel/sidecar.log'}</code>`;

  // sidecar 重连（device 重启/拔插）后设备丢了帧状态 → 整帧重推
  if (pusher.session !== null && st.session !== pusher.session) {
    pusher.markFull();
    toast('设备重连，已整帧重推');
  }
  pusher.session = st.session;
}

async function boot() {
  pusher.onStats = paintStats;

  const dataLoop = startDataLoop((w) => renderTile(w));
  await initEditor({
    onLayoutChange: () => pusher.markFull(),
    invalidateData: (id) => dataLoop.invalidate(id),
    invalidatePage: (id) => dataLoop.invalidatePage(id),
  });
  el('#btn-delpage').onclick = () => deleteCurrentPage();

  document.addEventListener('layout:invalid', () => pusher.markFull());

  // 顶栏：亮度
  const slider = el('#brightness');
  slider.addEventListener('input', () => {
    el('#brightness-val').textContent = slider.value;
    slider.style.setProperty('--pct', slider.value + '%');
  });
  slider.addEventListener('change', async () => {
    try {
      await postJson('/api/control', { cmd: 'brightness', value: Number(slider.value) });
      toast(`亮度 ${slider.value}`);
    } catch (e) {
      toast('亮度设置失败：' + e.message, 'warn');
    }
  });
  let paused = false;
  el('#btn-pause').onclick = () => {
    paused = !paused;
    pusher.paused = paused;
    el('#btn-pause').classList.toggle('primary', paused);
    el('#btn-pause').textContent = paused ? '继续推送' : '暂停推送';
    if (!paused) {
      pusher.markFull();
      lastFullPush = Date.now();
    }
    toast(paused ? '已暂停推帧（屏上定格）' : '已恢复推送');
  };
  // 纯色自检：整屏刷一种颜色，用来看屏自身的闪（背光 PWM / 面板 / 固件重绘）
  // 先把推送暂停，否则下一次 tick 会立刻把纯色盖回去
  for (const btn of document.querySelectorAll('#diag-colors button')) {
    btn.onclick = async () => {
      const color = btn.dataset.color;
      if (!pusher.paused) el('#btn-pause').click();
      try {
        await postJson('/api/device/pattern', { color });
        pusher.markFull();
        toast(`屏上已刷纯色：${btn.textContent}（看完点「继续推送」恢复画面）`);
      } catch (e) {
        toast('纯色自检失败：' + e.message, 'warn');
      }
    };
  }
  el('#device-pill').onclick = async () => {
    await postJson('/api/device/reconnect', {});
    toast('正在重连设备…');
  };
  el('#btn-sleep').onclick = () => postJson('/api/control', { cmd: 'sleep' }).then(() => toast('已待机'));
  el('#btn-wake').onclick = () => postJson('/api/control', { cmd: 'wake' }).then(() => toast('已唤醒'));
  el('#btn-full').onclick = () => {
    pusher.markFull();
    toast('整帧重推');
  };

  // 缩放
  el('#zoom-in').onclick = () => applyZoom(state.zoom + 0.06);
  el('#zoom-out').onclick = () => applyZoom(state.zoom - 0.06);

  // 布局导入导出
  el('#btn-export').onclick = () => {
    const blob = new Blob([JSON.stringify(state.layout, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'cs80-layout.json';
    a.click();
    URL.revokeObjectURL(a.href);
  };
  el('#btn-import').onclick = () => el('#file-import').click();
  el('#file-import').addEventListener('change', async (ev) => {
    const file = ev.target.files[0];
    if (!file) return;
    try {
      const layout = JSON.parse(await file.text());
      await api('/api/layout', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(layout),
      });
      location.reload();
    } catch (e) {
      toast('导入失败：' + e.message, 'warn');
    }
  });
  el('#btn-default').onclick = async () => {
    if (!confirm('恢复默认布局？当前布局会被覆盖')) return;
    await api('/api/layout', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reset: true }),
    });
    location.reload();
  };

  startStatusLoop(paintDevice);

  // 渲染 + 推送：时钟秒级；屏保时提速到 ~11fps
  const tick = () => {
    if (!saver.active) renderAll();
    if (Date.now() - lastFullPush > 60000 && !saver.active) {
      pusher.markFull();
      lastFullPush = Date.now();
    }
    pusher.tick(tiles);
  };
  const setRate = (ms) => {
    if (tickMs === ms && tickTimer) return;
    tickMs = ms;
    if (tickTimer) clearInterval(tickTimer);
    tickTimer = setInterval(tick, ms);
  };
  setRate(200);
  setRateFn = setRate;

  await initScreensaver();

  // 调试/自动化用的最小句柄
  window.__cs80 = { pusher, state, renderAll, saver, editor: await import('./editor.js'), setRate: () => setRateFn };
}

// ---------------- 屏保 ----------------
async function loadSaverCfg() {
  try {
    const cfg = await (await fetch('/api/screensaver')).json();
    saver.files = cfg.files || [];
    saver.file = cfg.file || (saver.files[0] && saver.files[0].name) || '';
    saver.autoLock = cfg.autoLock !== false;
    renderSaverUI();
  } catch (e) {
    console.warn('screensaver cfg failed', e);
  }
}

async function startScreensaver() {
  if (saver.active) return;
  const res = await postJson('/api/screensaver', { action: 'play' });
  if (!res.ok) {
    toast('屏保启动失败：' + (res.error || '未知错误'), 'warn');
    return;
  }
  saver.active = true;
  pusher.paused = true; // 屏保由 host 直接推帧，浏览器别再抢
  renderSaverUI();
  toast('屏保已开始');
}

async function stopScreensaver() {
  if (!saver.active) return;
  await postJson('/api/screensaver', { action: 'stop' });
  saver.active = false;
  pusher.paused = false;
  pusher.markFull();
  renderSaverUI();
}

function renderSaverUI() {
  const sel = el('#saver-file');
  const btn = el('#btn-saver');
  if (!sel || !btn) return;
  if (sel.dataset.stamp !== String(saver.files.length)) {
    sel.innerHTML = saver.files.map((f) => `<option value="${f.name}">${f.name}</option>`).join('');
    sel.dataset.stamp = String(saver.files.length);
  }
  sel.value = saver.file;
  sel.onchange = async () => {
    saver.file = sel.value;
    if (saver.active) {
      stopScreensaver();
      startScreensaver();
    }
    await postJson('/api/screensaver', { file: saver.file });
  };
  btn.textContent = saver.active ? '退出屏保' : '屏保';
  btn.classList.toggle('primary', saver.active);
  const auto = el('#saver-auto');
  if (auto) {
    auto.checked = saver.autoLock;
    auto.onchange = () => {
      saver.autoLock = auto.checked;
      postJson('/api/screensaver', { autoLock: auto.checked });
    };
  }
}

async function initScreensaver() {
  await loadSaverCfg();
  el('#btn-saver').onclick = () => (saver.active ? stopScreensaver() : startScreensaver());
  // 锁屏 → 自动播放；解锁 → 恢复画布
  setInterval(async () => {
    try {
      const st = await (await fetch('/api/screensaver')).json();
      if (st.files && st.files.length && st.files.length !== saver.files.length) {
        saver.files = st.files;
        saver.file = st.file || saver.files[0].name;
      }
      saver.autoLock = st.autoLock !== false;
      const playing = !!(st.saver && st.saver.playing);
      if (playing !== saver.active) {
        saver.active = playing;
        pusher.paused = playing;      // 屏保期间浏览器不推帧
        if (!playing) pusher.markFull();
        renderSaverUI();
      }
    } catch (e) {
      /* host 抖动忽略 */
    }
  }, 2500);
}

boot().catch((e) => {
  console.error(e);
  document.body.innerHTML = `<pre style="color:#ef6f6f;padding:24px;font:12px/1.6 monospace">启动失败：${e.stack || e}</pre>`;
});

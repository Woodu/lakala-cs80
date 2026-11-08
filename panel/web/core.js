// 常量 + 状态 + API + 数据调度
import { THEME, WIDGETS } from './widgets.js';

export const COLS = 5;
export const ROWS = 8;
export const CELL = 160;
export const W = COLS * CELL;
export const H = ROWS * CELL;

export const state = {
  layout: null,
  data: {},          // widgetId -> 数据
  selected: null,    // widgetId
  zoom: 0.52,
  device: {},        // /api/status 的镜像
  session: null,
};

export async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error(`${path} ${res.status}`);
  const type = res.headers.get('content-type') || '';
  return type.includes('json') ? res.json() : res;
}

export async function postBinary(path, body) {
  return api(path, { method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body });
}

export async function postJson(path, obj) {
  return api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(obj) });
}

export async function loadLayout() {
  const layout = await api('/api/layout');
  if (!layout.pages) layout.pages = [];
  // 设计 token 只来自 widgets.js（老布局里残留的 theme 字段直接忽略，避免旧配色回流）
  layout.theme = { ...THEME };
  state.layout = layout;
  return state.layout;
}

export async function saveLayout() {
  await api('/api/layout', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(state.layout),
  });
}

export const pages = () => (state.layout ? state.layout.pages : []);
export const activePage = () => {
  if (!state.layout) return null;
  const find = (id) => state.layout.pages.find((p) => p.id === id);
  return find(state.layout.activePage) || state.layout.pages[0] || null;
};
export const gridPage = () => {
  const p = activePage();
  return p && p.type === 'grid' ? p : null;
};
export const widgetById = (id) => {
  const page = gridPage();
  return page ? page.widgets.find((w) => w.id === id) : null;
};

export async function setActivePage(id) {
  await postJson('/api/pages/active', { id });
  state.layout.activePage = id;
}

const moduleCache = new Map();
/** 自定义页面模块（pages/xxx.js），带缓存；加载失败返回 null */
export async function loadPageModule(page) {
  if (!page || page.type !== 'custom') return null;
  const key = page.module || 'pages/stock.js';
  if (moduleCache.has(key)) return moduleCache.get(key);
  try {
    const mod = await import('./' + key.replace(/^\.?\//, ''));
    moduleCache.set(key, mod.default || mod);
  } catch (e) {
    console.error('[page module]', key, e);
    moduleCache.set(key, null);
  }
  return moduleCache.get(key);
}

export function touchWidget(w) {
  if (!w) return;
  const def = WIDGETS[w.type] || {};
  w.config = w.config || {};
  for (const [k, v] of Object.entries(def.config || {})) if (w.config[k] === undefined) w.config[k] = v;
}

/** 数据调度：每个组件按自己的 needs.ttl 拉数据，只通知变化过的组件 */
export function startDataLoop(onChange) {
  const last = {};
  async function tick() {
    // 自定义页面：模块自己声明 needs，用页面 id 作为数据 key
    for (const page of pages()) {
      if (page.type !== 'custom') continue;
      const mod = await loadPageModule(page);
      const need = mod && mod.needs;
      if (!need) continue;
      const ttl = need.ttl || 60000;
      const key = 'page:' + page.id;
      if (last[key] && Date.now() - last[key] < ttl) continue;
      last[key] = Date.now();
      const q = need.query ? need.query(page.config || {}) : {};
      const qs = new URLSearchParams(q).toString();
      try {
        const data = await api(`/api/data/${need.kind}${qs ? '?' + qs : ''}`);
        if (JSON.stringify(data) !== JSON.stringify(state.data[page.id])) {
          state.data[page.id] = data;
          onChange(page);
        }
      } catch (e) {
        state.data[page.id] = { error: String(e) };
        last[key] = Date.now() - ttl + 15000;
      }
    }
    const page = gridPage();
    const widgets = page ? page.widgets : [];
    for (const w of widgets) {
      const def = WIDGETS[w.type];
      if (!def || !def.needs) continue;
      const ttl = def.needs.ttl || 60000;
      const due = ttl === 0 || !last[w.id] || Date.now() - last[w.id] > ttl;
      if (!due) continue;
      last[w.id] = Date.now();
      const q = def.needs.query ? def.needs.query(w.config || {}) : {};
      const qs = new URLSearchParams(q).toString();
      try {
        const data = await api(`/api/data/${def.needs.kind}${qs ? '?' + qs : ''}`);
        const changed = JSON.stringify(data) !== JSON.stringify(state.data[w.id]);
        state.data[w.id] = data;
        if (changed) onChange(w);
      } catch (e) {
        state.data[w.id] = { error: String(e) };
        last[w.id] = Date.now() - ttl + 15000; // 失败别等满 TTL，15 秒后重试
        onChange(w);
      }
    }
  }
  tick();
  setInterval(tick, 500);
  return {
    invalidate: (id) => delete last[id],
    invalidatePage: (id) => delete last['page:' + id],
  };
}

/** 设备镜像状态轮询；session 变化（sidecar 重连）时回调，用于整帧重推 */
export function startStatusLoop(onStatus) {
  async function tick() {
    try {
      const st = await api('/api/status');
      state.device = st;
      onStatus(st);
    } catch (e) {
      state.device = { device: 'host-down', error: String(e) };
      onStatus(state.device);
    }
  }
  tick();
  setInterval(tick, 2000);
}

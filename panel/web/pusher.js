// 设备像素推送：把每个格子的 canvas 拼成整帧 800×1280，只发脏的 32×16 tile。
import { CELL, H, ROWS, W, COLS, activePage, loadPageModule, state, postBinary } from './core.js';

const TW = 32;
const TH = 16;

export class Pusher {
  constructor() {
    this.canvas = document.createElement('canvas');
    this.canvas.width = W;
    this.canvas.height = H;
    this.ctx = this.canvas.getContext('2d', { willReadFrequently: true });
    this.prev = null;
    this.fullNeeded = true;
    this.busy = false;
    this.paused = false; /* 暂停提交：屏上定格，USB 保活照常 → 用来判断「屏幕自身的闪」 */
    this.session = null;
    this.stats = { wire: 0, ms: 0, tiles: 0, kind: '-' };
    this.onStats = null;
  }

  markFull() {
    this.fullNeeded = true;
  }

  /** tiles: Map<widgetId, HTMLCanvasElement>（各自是设备像素尺寸） */
  async compose(tiles) {
    const ctx = this.ctx;
    const page = activePage();
    const theme = state.layout.theme || {};
    ctx.fillStyle = theme.bg || '#141519';
    ctx.fillRect(0, 0, W, H);
    if (!page) return;
    if (page.type === 'custom') {
      const mod = await loadPageModule(page);
      if (!mod || !mod.draw) {
        ctx.fillStyle = '#5a1d1d';
        ctx.fillRect(0, 0, W, H);
        ctx.fillStyle = '#ffd7d7';
        ctx.font = '400 24px -apple-system, sans-serif';
        ctx.fillText('自定义页面加载失败：' + (page.module || ''), 40, 60);
        return;
      }
      ctx.save();
      try {
        mod.draw(ctx, { w: W, h: H }, state.data[page.id], page.config || {}, theme);
      } catch (e) {
        console.error('[page draw]', page.module, e);
        ctx.fillStyle = '#5a1d1d';
        ctx.fillRect(0, 0, W, H);
        ctx.fillStyle = '#ffd7d7';
        ctx.font = '400 20px -apple-system, sans-serif';
        ctx.fillText('页面绘制报错：' + String(e).slice(0, 80), 40, 60);
      }
      ctx.restore();
      return;
    }
    for (const w of page.widgets) {
      const el = tiles.get(w.id);
      if (!el) continue;
      ctx.drawImage(el, w.cell[0] * CELL, w.cell[1] * CELL);
    }
  }

  toBgr565(rgba) {
    const out = new Uint16Array(W * H);
    for (let i = 0, j = 0; i < out.length; i++, j += 4) {
      out[i] = ((rgba[j + 2] & 0xf8) << 8) | ((rgba[j + 1] & 0xfc) << 3) | (rgba[j] >> 3);
    }
    return out;
  }

  diff(a, b) {
    const tiles = [];
    for (let ty = 0; ty * TH < H; ty++) {
      for (let tx = 0; tx * TW < W; tx++) {
        let dirty = false;
        const x0 = tx * TW;
        const y0 = ty * TH;
        for (let row = 0; row < TH && !dirty; row++) {
          let idx = (y0 + row) * W + x0;
          for (let k = 0; k < TW; k++, idx++) {
            if (a[idx] !== b[idx]) {
              dirty = true;
              break;
            }
          }
        }
        if (dirty) tiles.push([x0, y0]);
      }
    }
    return tiles;
  }

  async pushFull(rgba) {
    const rgb = new Uint8Array(W * H * 3);
    for (let i = 0, j = 0, k = 0; i < W * H; i++, j += 4, k += 3) {
      rgb[k] = rgba[j];
      rgb[k + 1] = rgba[j + 1];
      rgb[k + 2] = rgba[j + 2];
    }
    await postBinary('/api/frame', rgb);
  }

  async pushPatch(tiles, buf) {
    const tileBytes = TW * TH * 2;
    const ab = new ArrayBuffer(4 + tiles.length * (4 + tileBytes));
    const dv = new DataView(ab);
    dv.setUint32(0, tiles.length, true);
    let off = 4;
    for (const [x, y] of tiles) {
      dv.setUint16(off, x, true);
      dv.setUint16(off + 2, y, true);
      off += 4;
      for (let row = 0; row < TH; row++) {
        const idx = (y + row) * W + x;
        for (let k = 0; k < TW; k++, off += 2) dv.setUint16(off, buf[idx + k], true);
      }
    }
    await postBinary('/api/patch', new Uint8Array(ab));
  }

  async tick(tiles) {
    if (this.busy || this.paused || !state.layout) return;
    this.busy = true;
    try {
      await this.compose(tiles);
      const img = this.ctx.getImageData(0, 0, W, H).data;
      const buf = this.toBgr565(img);
      const t0 = performance.now();
      if (this.fullNeeded || !this.prev) {
        await this.pushFull(img);
        this.fullNeeded = false;
        this.stats = { wire: 0, ms: Math.round(performance.now() - t0), tiles: 0, kind: 'full' };
      } else {
        const dirty = this.diff(this.prev, buf);
        if (dirty.length && dirty.length <= 120) {
          await this.pushPatch(dirty, buf);
          this.stats = { wire: 0, ms: Math.round(performance.now() - t0), tiles: dirty.length, kind: 'patch' };
        } else if (dirty.length) {
          await this.pushFull(img);
          this.stats = { wire: 0, ms: Math.round(performance.now() - t0), tiles: 0, kind: 'full' };
        } else {
          this.stats = { ...this.stats, ms: Math.round(performance.now() - t0) };
        }
      }
      this.prev = buf;
      if (this.onStats) this.onStats(this.stats);
    } catch (e) {
      if (this.onStats) this.onStats({ ...this.stats, kind: 'error', error: String(e) });
    } finally {
      this.busy = false;
    }
  }
}

/* Scope: spectrum + scrolling waterfall + channel/filter overlay.
   Mirrors the IC-9700 bandscope look. */
(function (global) {
  "use strict";

  const AMP_MAX = 160;            // documented 27 00 data range
  const FLOOR_DB = 22;            // approximate noise floor amplitude
  const STRIP_H = 16;             // band-plan strip height (px) along the spectrum bottom

  function buildLUT() {
    // Icom-style waterfall colormap: dark navy -> blue -> cyan -> green -> yellow -> red -> white
    const stops = [
      [0.00, [2, 6, 18]],
      [0.18, [12, 28, 110]],
      [0.36, [10, 110, 200]],
      [0.52, [10, 200, 200]],
      [0.66, [40, 210, 90]],
      [0.80, [240, 220, 40]],
      [0.92, [240, 90, 30]],
      [1.00, [255, 245, 230]],
    ];
    const lut = new Uint8Array(256 * 3);
    for (let i = 0; i < 256; i++) {
      const t = i / 255;
      let a = stops[0], b = stops[stops.length - 1];
      for (let s = 0; s < stops.length - 1; s++) {
        if (t >= stops[s][0] && t <= stops[s + 1][0]) { a = stops[s]; b = stops[s + 1]; break; }
      }
      const f = (t - a[0]) / Math.max(1e-6, b[0] - a[0]);
      lut[i * 3] = a[1][0] + (b[1][0] - a[1][0]) * f;
      lut[i * 3 + 1] = a[1][1] + (b[1][1] - a[1][1]) * f;
      lut[i * 3 + 2] = a[1][2] + (b[1][2] - a[1][2]) * f;
    }
    return lut;
  }

  class Scope {
    constructor(spectrum, waterfall, overlay, wrap) {
      this.spec = spectrum;
      this.wf = waterfall;
      this.ov = overlay;
      this.wrap = wrap;
      this.sctx = spectrum.getContext("2d");
      this.wctx = waterfall.getContext("2d", { willReadFrequently: false });
      this.octx = overlay.getContext("2d");
      this.lut = buildLUT();
      this.opMode = "USB";
      this.meta = { mode: 0, center: 0, span: 50000, lower: 0, upper: 0, tuned: 0, filterBw: 2400 };
      this.lastData = null;
      this.maxHold = null;
      this.bandplan = null;        // [{lo,hi (Hz), label, desc, kind}]
      this.showBandplan = false;
      this.resize();
      window.addEventListener("resize", () => this.resize());
    }

    resize() {
      const w = Math.max(320, Math.floor(this.wrap.clientWidth));
      const dpr = window.devicePixelRatio || 1;
      // read the laid-out heights so the waterfall fills its pane (falls back to fixed)
      const specH = Math.max(80, Math.floor(this.spec.clientHeight) || 150);
      const wfH = Math.max(120, Math.floor(this.wf.clientHeight) || 240);
      // overlay spans the FULL scope incl. the split gap so canvas-Y == display-Y;
      // otherwise the band-plan strip (drawn at the spectrum bottom) bleeds onto the
      // split handle that sits between the spectrum and waterfall.
      const scopeH = Math.max(specH + wfH, Math.floor(this.wrap.clientHeight) || 0);
      // Mobile browsers fire window 'resize' when the address bar shows/hides while
      // scrolling. Rebuilding here wipes the waterfall, so bail when nothing changed.
      if (this._sized && w === this.W && specH === this.specH && wfH === this.wfH &&
          scopeH === this.scopeH && dpr === this._dpr) return;
      this._sized = true; this._dpr = dpr;
      this.W = w; this.specH = specH; this.wfH = wfH; this.scopeH = scopeH;
      for (const [c, h] of [[this.spec, this.specH], [this.ov, this.scopeH]]) {
        c.width = w * dpr; c.height = h * dpr;
        c.getContext("2d").setTransform(dpr, 0, 0, dpr, 0, 0);
      }
      // waterfall stays 1:1 so putImageData rows line up crisply
      this.wf.width = w; this.wf.height = this.wfH;
      this.wctx.fillStyle = "#020c14"; this.wctx.fillRect(0, 0, w, this.wfH);
      this.rowBuf = this.wctx.createImageData(w, 1);
      this.maxHold = null;
      if (this.lastData) this.drawSpectrum(this.lastData);
      this.drawOverlay();
    }

    setOpMode(m) { if (m) { this.opMode = m; this.drawOverlay(); } }

    clear() {
      this.lastData = null;
      this.maxHold = null;
      this.meta = { mode: 0, center: 0, span: this.meta.span || 50000, lower: 0, upper: 0, tuned: 0, filterBw: 0 };
      this.wctx.fillStyle = "#020c14"; this.wctx.fillRect(0, 0, this.W, this.wfH);
      this.sctx.clearRect(0, 0, this.W, this.specH);
      this.octx.clearRect(0, 0, this.W, this.scopeH || (this.specH + this.wfH));
    }

    // No-scope radios (e.g. Yaesu FT-991A CAT): no sweeps, but keep the band-plan
    // strip + tuned marker + freq labels aligned to the current frequency.
    showStatic(center, span) {
      this.lastData = null; this.maxHold = null;
      this.meta = { mode: 0, center: center || 0, span: span || 200000, lower: 0, upper: 0, tuned: center || 0, filterBw: 0 };
      this.sctx.fillStyle = "#020c14"; this.sctx.fillRect(0, 0, this.W, this.specH);
      this.wctx.fillStyle = "#020c14"; this.wctx.fillRect(0, 0, this.W, this.wfH);
      this.drawOverlay();
    }

    freqToX(f) {
      const m = this.meta;
      let lo, hi;
      if (m.mode === 1 && m.lower && m.upper) { lo = m.lower; hi = m.upper; }
      else { const span = m.span || 50000; lo = (m.center || f) - span / 2; hi = (m.center || f) + span / 2; }
      if (hi <= lo) return this.W / 2;
      return ((f - lo) / (hi - lo)) * this.W;
    }

    visibleRange() {
      const m = this.meta;
      if (m.mode === 1 && m.lower && m.upper) return [m.lower, m.upper];
      const span = m.span || 50000, c = m.center || m.tuned || 0;
      return [c - span / 2, c + span / 2];
    }

    // distinct band-plan kinds currently visible (for the color-key legend), in display order
    visibleKinds() {
      if (!this.bandplan) return [];
      const [lo, hi] = this.visibleRange();
      const seen = [];
      for (const seg of this.bandplan) {
        if (seg.hi <= lo || seg.lo >= hi) continue;
        if (seen.indexOf(seg.kind) < 0) seen.push(seg.kind);
      }
      return seen;
    }

    // band-plan segment under a canvas-space point (for hover tooltips), else null
    bandplanSegAt(px, py) {
      if (!this.showBandplan || !this.bandplan) return null;
      const sy = this.specH - STRIP_H - 3;
      if (py < sy - 2 || py > sy + STRIP_H + 2) return null;
      const [lo, hi] = this.visibleRange();
      for (const seg of this.bandplan) {
        if (seg.hi <= lo || seg.lo >= hi) continue;
        if (px >= this.freqToX(seg.lo) && px <= this.freqToX(seg.hi)) return seg;
      }
      return null;
    }

    pushSweep(meta, data) {
      this.meta = meta;
      this.lastData = data;
      this.scrollWaterfall(data);
      this.drawSpectrum(data);
      this.drawOverlay();
    }

    scrollWaterfall(data) {
      const w = this.W, ctx = this.wctx;
      ctx.drawImage(this.wf, 0, 1);              // shift history down 1px
      const row = this.rowBuf.data, n = data.length || 1;
      for (let x = 0; x < w; x++) {
        const v = data[Math.min(n - 1, (x * n / w) | 0)];
        const idx = Math.min(255, Math.round((v / AMP_MAX) * 255)) * 3;
        const o = x * 4;
        row[o] = this.lut[idx]; row[o + 1] = this.lut[idx + 1];
        row[o + 2] = this.lut[idx + 2]; row[o + 3] = 255;
      }
      ctx.putImageData(this.rowBuf, 0, 0);
    }

    drawSpectrum(data) {
      const ctx = this.sctx, w = this.W, h = this.specH, n = data.length || 1;
      ctx.clearRect(0, 0, w, h);
      ctx.fillStyle = "#020c14"; ctx.fillRect(0, 0, w, h);
      // grid
      ctx.strokeStyle = "rgba(20,90,130,.45)"; ctx.lineWidth = 1;
      ctx.beginPath();
      for (let i = 1; i < 10; i++) { const x = (i / 10) * w; ctx.moveTo(x, 0); ctx.lineTo(x, h); }
      for (let i = 1; i < 4; i++) { const y = (i / 4) * h; ctx.moveTo(0, y); ctx.lineTo(w, y); }
      ctx.stroke();

      if (!this.maxHold || this.maxHold.length !== w) this.maxHold = new Float32Array(w);
      const yOf = v => h - (Math.min(AMP_MAX, v) / AMP_MAX) * (h - 4) - 2;

      // filled spectrum
      ctx.beginPath(); ctx.moveTo(0, h);
      for (let x = 0; x < w; x++) {
        const v = data[Math.min(n - 1, (x * n / w) | 0)];
        ctx.lineTo(x, yOf(v));
        this.maxHold[x] = Math.max(v, this.maxHold[x] * 0.985);
      }
      ctx.lineTo(w, h); ctx.closePath();
      const grad = ctx.createLinearGradient(0, 0, 0, h);
      grad.addColorStop(0, "rgba(60,210,170,.55)");
      grad.addColorStop(1, "rgba(20,90,120,.10)");
      ctx.fillStyle = grad; ctx.fill();
      // line
      ctx.beginPath();
      for (let x = 0; x < w; x++) { const v = data[Math.min(n - 1, (x * n / w) | 0)]; x ? ctx.lineTo(x, yOf(v)) : ctx.moveTo(x, yOf(v)); }
      ctx.strokeStyle = "#5ff0c8"; ctx.lineWidth = 1.2; ctx.stroke();
      // max hold
      ctx.beginPath();
      for (let x = 0; x < w; x++) { const y = yOf(this.maxHold[x]); x ? ctx.lineTo(x, y) : ctx.moveTo(x, y); }
      ctx.strokeStyle = "rgba(255,200,120,.55)"; ctx.lineWidth = 1; ctx.stroke();
    }

    drawOverlay() {
      const ctx = this.octx, w = this.W, h = this.scopeH || (this.specH + this.wfH), m = this.meta;
      ctx.clearRect(0, 0, w, h);
      // filter passband band (offset by mode)
      const bw = m.filterBw || 0, tuned = m.tuned || 0;   // explicit 0 (AF scope) -> no marker
      if (bw > 0 && tuned) {
        let loF, hiF;
        if (this.opMode === "USB" || this.opMode === "DV" || this.opMode === "RTTY-R") { loF = tuned; hiF = tuned + bw; }
        else if (this.opMode === "LSB" || this.opMode === "RTTY") { loF = tuned - bw; hiF = tuned; }
        else { loF = tuned - bw / 2; hiF = tuned + bw / 2; }
        const x0 = this.freqToX(loF), x1 = this.freqToX(hiF);
        ctx.fillStyle = "rgba(90,200,255,.16)";
        ctx.fillRect(Math.min(x0, x1), 0, Math.abs(x1 - x0), h);
        ctx.strokeStyle = "rgba(120,220,255,.5)"; ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(x0, 0); ctx.lineTo(x0, h); ctx.moveTo(x1, 0); ctx.lineTo(x1, h); ctx.stroke();
      }
      // tuned channel marker
      if (tuned) {
        const xc = this.freqToX(tuned);
        ctx.strokeStyle = "#ff9a2e"; ctx.lineWidth = 1.4;
        ctx.beginPath(); ctx.moveTo(xc, 0); ctx.lineTo(xc, h); ctx.stroke();
        // little triangle at top
        ctx.fillStyle = "#ff9a2e";
        ctx.beginPath(); ctx.moveTo(xc - 5, 0); ctx.lineTo(xc + 5, 0); ctx.lineTo(xc, 8); ctx.closePath(); ctx.fill();
      }
      if (m.out) {
        ctx.fillStyle = "rgba(255,80,80,.9)"; ctx.font = "11px monospace";
        ctx.fillText("OUT OF RANGE", w / 2 - 40, this.specH / 2);
      }
      this.drawBandplan();
    }

    drawBandplan() {
      if (!this.showBandplan || !this.bandplan || !this.bandplan.length) return;
      const ctx = this.octx, w = this.W;
      const [lo, hi] = this.visibleRange();
      const sy = this.specH - STRIP_H - 3;     // 3px clear of the spectrum/waterfall split below
      const colors = global.BANDPLAN_COLORS || {};
      ctx.font = "10px Consolas, monospace";
      ctx.textBaseline = "middle";
      for (const seg of this.bandplan) {
        if (seg.hi <= lo || seg.lo >= hi) continue;
        const x0 = Math.max(0, this.freqToX(seg.lo));
        const x1 = Math.min(w, this.freqToX(seg.hi));
        const bw = x1 - x0;
        if (bw <= 0.5) continue;
        const base = colors[seg.kind] || "rgba(150,160,180,";
        ctx.fillStyle = base + "0.30)";
        ctx.fillRect(x0, sy, bw, STRIP_H);
        ctx.strokeStyle = base + "0.85)"; ctx.lineWidth = 1;
        ctx.strokeRect(x0 + 0.5, sy + 0.5, Math.max(0, bw - 1), STRIP_H - 1);
        if (bw > 24) {
          ctx.save();
          ctx.beginPath(); ctx.rect(x0 + 1, sy, bw - 2, STRIP_H); ctx.clip();
          ctx.fillStyle = "rgba(255,255,255,.95)";
          ctx.textAlign = "center";
          ctx.fillText(seg.label, (x0 + x1) / 2, sy + STRIP_H / 2 + 0.5);
          ctx.restore();
        }
      }
      ctx.textBaseline = "alphabetic"; ctx.textAlign = "left";
    }
  }

  global.Scope = Scope;
  global.fmtHz = function (hz) {
    if (!hz) return "—";
    const mhz = hz / 1e6;
    return mhz.toFixed(mhz >= 1000 ? 4 : 3);
  };
})(window);

/* Scope: spectrum + scrolling waterfall + channel/filter overlay.
   Mirrors the IC-9700 bandscope look. */
(function (global) {
  "use strict";

  const AMP_MAX = 160;            // documented 27 00 data range
  const FLOOR_DB = 22;            // approximate noise floor amplitude

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
      this.resize();
      window.addEventListener("resize", () => this.resize());
    }

    resize() {
      const w = Math.max(320, Math.floor(this.wrap.clientWidth));
      const dpr = window.devicePixelRatio || 1;
      this.W = w;
      // read the laid-out heights so the waterfall fills its pane (falls back to fixed)
      this.specH = Math.max(80, Math.floor(this.spec.clientHeight) || 150);
      this.wfH = Math.max(120, Math.floor(this.wf.clientHeight) || 240);
      for (const [c, h] of [[this.spec, this.specH], [this.ov, this.specH + this.wfH]]) {
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

    freqToX(f) {
      const m = this.meta;
      let lo, hi;
      if (m.mode === 1 && m.lower && m.upper) { lo = m.lower; hi = m.upper; }
      else { const span = m.span || 50000; lo = (m.center || f) - span / 2; hi = (m.center || f) + span / 2; }
      if (hi <= lo) return this.W / 2;
      return ((f - lo) / (hi - lo)) * this.W;
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
      const ctx = this.octx, w = this.W, h = this.specH + this.wfH, m = this.meta;
      ctx.clearRect(0, 0, w, h);
      // filter passband band (offset by mode)
      const bw = m.filterBw || 0, tuned = m.tuned || m.center || 0;
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
    }
  }

  global.Scope = Scope;
  global.fmtHz = function (hz) {
    if (!hz) return "—";
    const mhz = hz / 1e6;
    return mhz.toFixed(mhz >= 1000 ? 4 : 3);
  };
})(window);

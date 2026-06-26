/* Radio WebOp — UI controller */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);

  const scope = new Scope($("spectrum"), $("waterfall"), $("overlay"), $("scopeWrap"));

  let ws = null, state = {}, step = 25000;
  let blanked = false;                            // after a radio change: VFO zeroed + waterfall cleared until reconnect
  let radios = [], currentRadio = null;          // radio profiles from /api/radios
  let pttIntended = false, pttKeyedAt = 0;       // PTT toggle state + time keyed
  const LEVELS = ["af", "rf", "sql", "rfpwr", "nb_level", "nr_level", "pbt1", "pbt2", "mnotch_pos",
                  "mic", "comp_level", "vox_gain", "mon_level"];

  // ---- frequency formatting (Icom dotted readout) ----
  function formatFreq(hz) {
    hz = Math.max(0, Math.round(hz));
    const mhz = Math.floor(hz / 1e6);
    const rem = hz % 1e6;
    const khz = String(Math.floor(rem / 1000)).padStart(3, "0");
    const h = String(rem % 1000).padStart(3, "0");
    return `${mhz}.${khz}.${h}`;
  }

  // ---- websocket ----
  function connectWS() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.binaryType = "arraybuffer";
    ws.onmessage = onMessage;
    ws.onclose = () => setTimeout(connectWS, 1200);
  }

  function send(obj) {
    if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj));
  }

  function onMessage(ev) {
    if (typeof ev.data === "string") {
      const msg = JSON.parse(ev.data);
      if (msg.type === "state") updateState(msg);
      return;
    }
    const tag = new Uint8Array(ev.data, 0, 1)[0];
    if (tag === 0x53) parseScope(ev.data);        // 'S' scope sweep
    else if (tag === 0x41) playAudio(ev.data);    // 'A' RX audio
  }

  function parseScope(buf) {
    if (blanked) return;                          // radio changed: ignore sweeps so the waterfall stays cleared
    const dv = new DataView(buf);
    if (dv.getUint8(0) !== 0x53) return;
    const npoints = dv.getUint16(3, true);
    const meta = {
      mode: dv.getUint8(1),
      out: dv.getUint8(2) === 1,
      center: dv.getUint32(5, true),
      span: dv.getUint32(9, true),
      lower: dv.getUint32(13, true),
      upper: dv.getUint32(17, true),
      tuned: dv.getUint32(21, true),
      filterBw: dv.getUint32(25, true),
    };
    const data = new Uint8Array(buf, 29, npoints);
    scope.pushSweep(meta, data);
    updateScopeLabels(meta);
  }

  function updateScopeLabels(m) {
    let lo, hi, c;
    if (m.mode === 1) { lo = m.lower; hi = m.upper; c = (lo + hi) / 2; }
    else { lo = m.center - m.span / 2; hi = m.center + m.span / 2; c = m.center; }
    $("lblLeft").textContent = formatFreq(lo);
    $("lblRight").textContent = formatFreq(hi);
    $("lblCenter").textContent = formatFreq(m.tuned || c);
  }

  // ---- state -> UI ----
  function updateState(s) {
    state = s;
    // dual-watch band readout (MAIN/SUB); single-rx radios show MAIN only
    $("rowSub").style.display = s.dual_watch ? "" : "none";
    if (blanked) {                                  // radio just changed: keep VFOs zeroed until a fresh connect
      renderFreq($("mainFreq"), 0, false);
      renderFreq($("subFreq"), 0, false);
    } else {
      fillBand("main", s.main, s.active_band !== "sub");
      fillBand("sub", s.sub, s.active_band === "sub");
    }
    $("rowMain").classList.toggle("active", s.active_band !== "sub");
    $("rowSub").classList.toggle("active", s.active_band === "sub");
    setInd("mainInd", s, s.active_band !== "sub");
    setInd("subInd", s, s.active_band === "sub");

    // multi-meter (S live; TX meters wired for M3)
    const isS = (s.meter || "S") === "S";
    const mmax = isS ? 240 : (s.meter_max || 255);
    $("meterFill").style.width = Math.min(100, (s.meter_val || 0) / mmax * 100) + "%";
    $("meterVal").textContent = isS ? (s.smeter_s || "S0") : (s.meter + " " + (s.meter_val || 0));
    setActive(".m-btn", b => b.dataset.meter === (s.meter || "S"));

    // RX toggles
    $("preampBtn").classList.toggle("on", (s.preamp || 0) > 0);
    $("attBtn").classList.toggle("on", (s.att || 0) > 0);
    $("lockBtn").classList.toggle("on", !!s.lock);

    // connection
    const on = !!s.connected;
    $("led").classList.toggle("on", on);
    $("connlabel").textContent = on ? (s.transport || "Connected") : "Disconnected";
    $("audioAvail").textContent = s.audio ? "• available" : "• LAN only";
    $("modelLabel").textContent = on ? (s.radio_name || "") : (currentRadio ? currentRadio.name : "");

    // PTT / TX tag (button label + countdown handled by tickPtt)
    $("txTag").textContent = s.ptt ? "TX" : "RX";
    $("txTag").classList.toggle("on", !!s.ptt);
    $("pttBtn").classList.toggle("on", !!s.ptt);
    if (!!s.ptt !== pttIntended) {             // reconcile with the radio's real TX state
      pttIntended = !!s.ptt;
      if (pttIntended) pttKeyedAt = Date.now();
    }
    tickPtt();

    // span + scope mode
    $("spanVal").textContent = s.span_label || "";
    $("btnCenter").classList.toggle("active", !!s.scope_center);
    $("btnFixed").classList.toggle("active", !s.scope_center);

    // active buttons
    setActive(".band", b => b.dataset.band === String(bandOf(s.freq)));
    setActive(".mode", b => b.dataset.mode === s.mode_name);
    setActive(".filt", b => b.dataset.filter === String(s.filter));

    // RX DSP toggles + AGC
    for (const f of ["nb", "nr", "anotch", "mnotch"]) {
      const el = $(f + "Btn");
      if (el) el.classList.toggle("on", (s[f] || 0) > 0);
    }
    setActive(".agc", b => +b.dataset.mode === (s.agc || 2));
    setActive(".mnw", b => +b.dataset.width === (s.mnotch_w || 0));

    // TX toggles + TBW + SPLIT/RIT (M3)
    for (const f of ["comp", "vox", "mon"]) {
      const el = $(f + "Btn");
      if (el) el.classList.toggle("on", (s[f] || 0) > 0);
    }
    setActive(".tbw", b => +b.dataset.w === (s.tbw || 0));
    setActive(".dup", b => +b.dataset.mode === (s.duplex || 0));
    if ($("splitBtn")) $("splitBtn").classList.toggle("on", (s.split || 0) > 0);
    if ($("ritBtn")) $("ritBtn").classList.toggle("on", (s.rit || 0) > 0);
    if ($("ritVal")) $("ritVal").textContent = (s.rit_freq > 0 ? "+" : "") + (s.rit_freq || 0) + " Hz";

    // level sliders (don't fight an active drag) + value readouts
    for (const t of LEVELS) {
      const el = $(t);
      if (el && document.activeElement !== el && s[t] != null) { el.value = s[t]; setFill(el); }
      if (s[t] != null) $(t + "Val").textContent = fmtLevel(t, s[t]);
    }

    // keep overlay tracking between sweeps
    scope.setOpMode(s.mode_name);
    scope.meta.tuned = s.freq;
    scope.meta.filterBw = s.filter_bw;
    scope.drawOverlay();
  }

  const BAND_LABEL = { "144": "2 m", "430": "70 cm", "1200": "23 cm" };
  function renderFreq(el, hz, interactive) {
    if (!el) return;
    if (el._hz === hz && el._int === interactive) return;   // skip when unchanged (avoids hover flicker)
    el._hz = hz; el._int = interactive;
    el.classList.toggle("tunable", !!interactive);
    const s = formatFreq(hz), ndig = (s.match(/\d/g) || []).length;
    let html = "", di = 0;
    for (const ch of s) {
      if (ch >= "0" && ch <= "9") {
        html += '<span class="fd" data-place="' + Math.pow(10, ndig - 1 - di) + '">' + ch + "</span>";
        di++;
      } else {
        html += '<span class="fdsep">' + ch + "</span>";
      }
    }
    el.innerHTML = html;
  }
  function fillBand(name, b, active) {
    if (!b) return;
    renderFreq($(name + "Freq"), b.freq, !!active);
    $(name + "Mode").textContent = b.mode_name || "";
    $(name + "Fil").textContent = b.filter_name || "";
    const bn = bandOf(b.freq);
    const lbl = $(name + "Band");
    if (lbl) lbl.textContent = BAND_LABEL[bn] || (bn ? bn + " m" : "");
  }

  // function-indicator strip (lit, like the TFT) — only the operating band's DSP is tracked
  function setInd(elId, s, isActive) {
    const el = $(elId);
    if (!el) return;
    if (!isActive) { el.innerHTML = ""; return; }
    const inds = [["AGC-" + ({ 1: "F", 2: "M", 3: "S" }[s.agc || 2]), "amber"]];
    if ((s.preamp || 0) > 0) inds.push(["P.AMP", ""]);
    if ((s.att || 0) > 0) inds.push(["ATT", ""]);
    if (s.nb) inds.push(["NB", ""]);
    if (s.nr) inds.push(["NR", ""]);
    if (s.anotch) inds.push(["AN", ""]);
    if (s.mnotch) inds.push(["MN", ""]);
    el.innerHTML = inds.map(([t, c]) => '<span class="ind ' + c + '">' + t + "</span>").join("");
  }

  function bandOf(hz) {
    if (!currentRadio) return "";
    for (const b of currentRadio.bands) if (hz >= b.lo && hz <= b.hi) return b.name;
    return "";
  }
  function setActive(sel, pred) {
    document.querySelectorAll(sel).forEach(b => b.classList.toggle("active", pred(b)));
  }

  // hover tooltips: just what each abbreviation stands for (no descriptions)
  const TITLES = {
    "NB": "Noise Blanker", "NR": "Noise Reduction", "A-NOTCH": "Auto Notch", "M-NOTCH": "Manual Notch",
    "NOTCH": "Manual Notch", "AGC": "Automatic Gain Control",
    "PBT1": "Passband Tuning 1", "PBT2": "Passband Tuning 2",
    "P.AMP": "Preamplifier", "ATT": "Attenuator", "LOCK": "Dial Lock",
    "COMP": "Speech Compressor", "VOX": "Voice-Operated Transmit", "MON": "Monitor",
    "TBW": "Transmit Bandwidth", "RIT": "Receiver Incremental Tuning",
    "SPLIT": "Split", "DUP": "Duplex", "SIMP": "Simplex", "PTT": "Push To Talk",
    "AF": "Audio Gain", "RF": "RF Gain", "SQL": "Squelch", "PWR": "RF Power",
    "MIC": "Microphone Gain", "VOL": "Volume",
    "FIL1": "Filter 1", "FIL2": "Filter 2", "FIL3": "Filter 3",
    "LSB": "Lower Sideband", "USB": "Upper Sideband", "CW": "Continuous Wave", "CW-R": "CW Reverse",
    "AM": "Amplitude Modulation", "FM": "Frequency Modulation",
    "RTTY": "Radio Teletype", "RTTY-R": "RTTY Reverse", "DV": "Digital Voice", "DD": "Digital Data",
    "S": "S-meter", "PO": "Power Output", "SWR": "Standing Wave Ratio", "ALC": "Auto Level Control",
    "Vd": "Drain Voltage", "Id": "Drain Current", "CENT": "Center", "FIX": "Fixed",
    "STEP": "Tuning Step",
  };
  function applyTitles(root) {
    const r = root || document;
    r.querySelectorAll(".key,.m-btn,.sc-btn,.step-label").forEach(el => {
      const t = (el.textContent || "").trim();
      if (TITLES[t] && !el.title) el.title = TITLES[t];
    });
    r.querySelectorAll(".sl").forEach(row => {
      const lab = row.querySelector("span");
      const t = lab && (lab.textContent || "").trim();
      if (t && TITLES[t] && !row.title) row.title = TITLES[t];
    });
  }

  // ---- button delegation ----
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-act]");
    if (!b) return;
    const act = b.dataset.act;
    if (act === "band") send({ action: "band", band: b.dataset.band });
    else if (act === "select_band") send({ action: "select_band", band: b.dataset.band });
    else if (act === "meter") send({ action: "set_meter", meter: b.dataset.meter });
    else if (act === "toggle") {
      const fn = b.dataset.fn;
      const on = fn === "att" ? !((state.att || 0) > 0)
               : fn === "preamp" ? !((state.preamp || 0) > 0)
               : !state.lock;
      send({ action: fn, on });
    }
    else if (act === "rxfunc") send({ action: "rx_func", name: b.dataset.fn, on: !((state[b.dataset.fn] || 0) > 0) });
    else if (act === "agc") send({ action: "agc", mode: +b.dataset.mode });
    else if (act === "mnotch_w") send({ action: "mnotch_w", width: +b.dataset.width });
    else if (act === "tbw") send({ action: "tbw", w: +b.dataset.w });
    else if (act === "duplex") send({ action: "duplex", mode: +b.dataset.mode });
    else if (act === "splittog") send({ action: "split", on: !((state.split || 0) > 0) });
    else if (act === "rittog") send({ action: "rit", on: !((state.rit || 0) > 0) });
    else if (act === "rit_d") { const v = Math.max(-9999, Math.min(9999, (state.rit_freq || 0) + (+b.dataset.d))); state.rit_freq = v; send({ action: "rit_freq", hz: v }); }
    else if (act === "rit_z") { state.rit_freq = 0; send({ action: "rit_freq", hz: 0 }); }
    else if (act === "mode") send({ action: "set_mode", mode: b.dataset.mode });
    else if (act === "filter") send({ action: "set_filter", filter: +b.dataset.filter });
    else if (act === "vfo") send({ action: "vfo", code: +b.dataset.code });
    else if (act === "scope_mode") send({ action: "scope_mode", center: b.dataset.center === "1" });
  });

  // span +/- cycle through documented spans
  const SPANS = [2500, 5000, 10000, 25000, 50000, 100000, 250000, 500000];
  $("spanUp").onclick = () => stepSpan(+1);
  $("spanDn").onclick = () => stepSpan(-1);
  function stepSpan(dir) {
    let i = SPANS.indexOf(state.span || 50000);
    if (i < 0) i = 3;
    i = Math.max(0, Math.min(SPANS.length - 1, i + dir));
    send({ action: "set_span", span: SPANS[i] });
  }

  // frequency entry
  $("freqSet").onclick = setFreqFromEntry;
  $("freqEntry").addEventListener("keydown", e => { if (e.key === "Enter") setFreqFromEntry(); });
  function setFreqFromEntry() {
    const v = parseFloat($("freqEntry").value);
    if (!isNaN(v)) send({ action: "set_freq", hz: Math.round(v * 1e6) });
  }

  // step select
  $("step").onchange = () => { step = +$("step").value; };

  // level sliders (power shown as %, others 0–255)
  function fmtLevel(t, v) { return t === "rfpwr" ? Math.round(v / 255 * 100) + "%" : "" + v; }
  function setFill(el) {
    const mn = +el.min || 0, mx = +el.max || 100;
    el.style.setProperty("--p", Math.round((el.value - mn) / (mx - mn) * 100) + "%");
  }
  for (const t of LEVELS) {
    $(t).addEventListener("input", e => {
      send({ action: "set_level", target: t, value: +e.target.value });
      $(t + "Val").textContent = fmtLevel(t, +e.target.value);
    });
  }
  // drive the visual fill (--p) for every slider, incl. VOL
  document.querySelectorAll(".sl input[type=range]").forEach(el => {
    setFill(el);
    el.addEventListener("input", () => setFill(el));
    el.addEventListener("wheel", e => {                 // only the clicked (focused) slider takes the wheel;
      if (document.activeElement !== el) return;        // otherwise let the wheel scroll the settings panel
      e.preventDefault();
      const span = (+el.max || 100) - (+el.min || 0);
      const d = Math.max(1, Math.round(span / 64)) * (e.deltaY < 0 ? 1 : -1);
      el.value = Math.max(+el.min, Math.min(+el.max, (+el.value) + d));
      el.dispatchEvent(new Event("input"));
    }, { passive: false });
  });

  // PTT — tap to toggle (works on touch + mouse). Latched TX auto-releases on
  // screen-lock / app-switch, page unload, and the failsafe time-out (which the
  // server independently enforces too). The button shows the countdown.
  const ptt = $("pttBtn");
  function setPtt(tx) {
    pttIntended = !!tx;
    if (pttIntended) pttKeyedAt = Date.now();
    send({ action: "ptt", tx: pttIntended });
    tickPtt();
  }
  function tickPtt() {
    if (!pttIntended) { ptt.textContent = "PTT"; return; }
    const left = (state.ptt_tot || 120) - Math.floor((Date.now() - pttKeyedAt) / 1000);
    if (left <= 0) { setPtt(false); return; }       // client failsafe (server enforces too)
    ptt.textContent = "ON AIR " + left + "s — STOP";
  }
  setInterval(tickPtt, 400);
  ptt.addEventListener("click", () => setPtt(!pttIntended));
  document.addEventListener("visibilitychange", () => { if (document.hidden && pttIntended) setPtt(false); });
  window.addEventListener("pagehide", () => { if (pttIntended) setPtt(false); });

  // ---- tuning on the scope: tap to select a frequency, drag/slide to spin ----
  const wrap = $("scopeWrap");
  let scDown = false, scStartX = 0, scLastX = 0, scMoved = 0, scAccum = 0;
  function scBounds() {
    const m = scope.meta;
    if (m.mode === 1 && m.lower && m.upper) return [m.lower, m.upper];
    return [m.center - m.span / 2, m.center + m.span / 2];
  }
  wrap.addEventListener("pointerdown", (e) => {
    scDown = true; scMoved = 0; scAccum = 0;
    const r = wrap.getBoundingClientRect();
    scStartX = scLastX = e.clientX - r.left;
    try { wrap.setPointerCapture(e.pointerId); } catch (_) {}
  });
  wrap.addEventListener("pointermove", (e) => {
    if (!scDown) return;
    const r = wrap.getBoundingClientRect();
    const x = e.clientX - r.left, dx = x - scLastX; scLastX = x; scMoved += Math.abs(dx);
    if (scMoved > 6) {                                // a drag -> pan the frequency
      const b = scBounds();
      scAccum += -(dx / scope.W) * (b[1] - b[0]);     // drag right -> tune down (content follows finger)
      const n = (scAccum / step) | 0;
      if (n !== 0) { send({ action: "tune", delta: n * step }); scAccum -= n * step; }
    }
  });
  function scEnd(e) {
    if (!scDown) return; scDown = false;
    try { wrap.releasePointerCapture(e.pointerId); } catch (_) {}
    if (scMoved <= 6) {                               // a tap -> tune to where you clicked
      const b = scBounds();
      const freq = b[0] + (scStartX / scope.W) * (b[1] - b[0]);
      const SNAP = 100;                               // fine snap so it lands at the click, not the coarse step
      send({ action: "set_freq", hz: Math.round(freq / SNAP) * SNAP });
    }
  }
  wrap.addEventListener("pointerup", scEnd);
  wrap.addEventListener("pointercancel", () => { scDown = false; });
  wrap.addEventListener("wheel", (e) => {
    e.preventDefault();
    send({ action: "tune", delta: (e.deltaY < 0 ? 1 : -1) * step });
  }, { passive: false });

  // ---- per-digit VFO tuning (click the readout digits up/down, or scroll them) ----
  const bandsEl = $("bands");
  function digitDelta(d, up) {
    const place = +d.dataset.place || 0;
    if (!place) return;
    const row = d.closest(".bandrow");
    if (row && !row.classList.contains("active")) {   // clicking the non-operating band selects it first
      send({ action: "select_band", band: row.dataset.band });
      return;
    }
    const hz = Math.max(0, (state.freq || 0) + (up ? place : -place));
    state.freq = hz;                                  // optimistic so rapid clicks/scrolls accumulate
    send({ action: "set_freq", hz });
  }
  if (bandsEl) {
    bandsEl.addEventListener("click", (e) => {
      const d = e.target.closest(".fd"); if (!d) return;
      e.stopPropagation();                            // don't also fire the row's band-select
      digitDelta(d, e.offsetY < d.clientHeight / 2);
    });
    bandsEl.addEventListener("wheel", (e) => {
      const d = e.target.closest(".fd"); if (!d) return;
      e.preventDefault();
      digitDelta(d, e.deltaY < 0);
    }, { passive: false });
  }

  // ---- draggable spectrum / waterfall split ----
  (function () {
    const split = $("scopeSplit"), spec = $("spectrum"), wrap = $("scopeWrap");
    if (!split || !spec || !wrap) return;
    let down = false;
    split.addEventListener("pointerdown", (e) => {
      down = true; split.classList.add("drag");
      try { split.setPointerCapture(e.pointerId); } catch (_) {}
      e.preventDefault();
    });
    split.addEventListener("pointermove", (e) => {
      if (!down) return;
      const r = wrap.getBoundingClientRect();
      spec.style.height = Math.max(60, Math.min(r.height - 110, e.clientY - r.top)) + "px";
      scope.resize();
    });
    const end = (e) => {
      if (!down) return; down = false; split.classList.remove("drag");
      try { split.releasePointerCapture(e.pointerId); } catch (_) {}
      scope.resize();
    };
    split.addEventListener("pointerup", end);
    split.addEventListener("pointercancel", end);
  })();

  // ---- day / night theme ----
  const themeBtn = $("themeBtn");
  function applyTheme(day) {
    document.body.classList.toggle("day", day);
    if (themeBtn) themeBtn.textContent = day ? "☀️" : "🌙";
    try { localStorage.setItem("radiowebop.theme", day ? "day" : "night"); } catch (_) {}
  }
  if (themeBtn) themeBtn.addEventListener("click", () => applyTheme(!document.body.classList.contains("day")));
  (function () {
    let t = "night";
    try { t = localStorage.getItem("radiowebop.theme") || "night"; } catch (_) {}
    applyTheme(t === "day");
  })();

  // ---- band-plan overlay (ARRL band plan on VHF/UHF, FCC license-class on HF) ----
  (function () {
    const btn = $("bandPlanBtn"), tip = $("bandTip"), ov = $("overlay"), wrap = $("scopeWrap");
    if (!btn || !ov || !wrap) return;
    scope.bandplan = (window.BANDPLAN || []).map((s) => ({ ...s, lo: s.lo * 1e6, hi: s.hi * 1e6 }));
    const colors = window.BANDPLAN_COLORS || {}, klabel = window.BANDPLAN_KIND_LABEL || {};

    function apply(on) {
      scope.showBandplan = on;
      btn.classList.toggle("active", on);
      if (!on && tip) tip.hidden = true;
      scope.drawOverlay();
      try { localStorage.setItem("radiowebop.bandplan", on ? "1" : "0"); } catch (_) {}
    }
    btn.addEventListener("click", () => apply(!scope.showBandplan));
    let saved = "0";
    try { saved = localStorage.getItem("radiowebop.bandplan") || "0"; } catch (_) {}
    apply(saved === "1");

    wrap.addEventListener("mousemove", (e) => {
      if (!scope.showBandplan || !tip) return;
      const r = ov.getBoundingClientRect();
      const px = (e.clientX - r.left) * (scope.W / r.width);
      const py = (e.clientY - r.top) * ((scope.scopeH || (scope.specH + scope.wfH)) / r.height);
      const seg = scope.bandplanSegAt(px, py);
      if (!seg) { tip.hidden = true; return; }
      const base = colors[seg.kind] || "rgba(150,160,180,";
      tip.innerHTML =
        '<div class="bt-h"><span class="bt-sw" style="background:' + base + '0.9)"></span>' + seg.label + "</div>" +
        '<div class="bt-rng">' + (seg.lo / 1e6).toFixed(3) + " – " + (seg.hi / 1e6).toFixed(3) +
        " MHz · " + (klabel[seg.kind] || seg.kind) + "</div>" +
        '<div class="bt-d">' + seg.desc + "</div>";
      tip.hidden = false;
      const tw = tip.offsetWidth, th = tip.offsetHeight;
      let x = e.clientX + 14, y = e.clientY + 14;
      if (x + tw > window.innerWidth - 8) x = e.clientX - tw - 14;
      if (y + th > window.innerHeight - 8) y = e.clientY - th - 14;
      tip.style.left = x + "px"; tip.style.top = y + "px";
    });
    wrap.addEventListener("mouseleave", () => { if (tip) tip.hidden = true; });
  })();

  // ---- radio profiles (bands/modes/steps render from the selected radio) ----
  function selectedRadio() { return radios.find(p => p.id === $("radioSel").value) || radios[0] || null; }
  async function loadRadios() {
    try {
      const j = await (await fetch("/api/radios")).json();
      radios = j.radios || [];
      const sel = $("radioSel"); sel.innerHTML = "";
      for (const p of radios) {
        const o = document.createElement("option"); o.value = p.id; o.textContent = p.name; sel.appendChild(o);
      }
    } catch (_) { radios = []; }
    return radios;
  }
  function renderRadio(p) {
    if (!p) return;
    currentRadio = p;
    $("modelLabel").textContent = state.connected ? (state.radio_name || p.name) : p.name;
    const br = $("bandRow"); br.innerHTML = ""; br.classList.toggle("band-grid", p.bands.length > 4);
    for (const b of p.bands) {
      const btn = document.createElement("button");
      btn.className = "key band"; btn.dataset.act = "band"; btn.dataset.band = b.name;
      btn.textContent = b.name; br.appendChild(btn);
    }
    const mr = $("modeRow"); mr.innerHTML = "";
    for (const name of p.modes) {
      const btn = document.createElement("button");
      btn.className = "key mode"; btn.dataset.act = "mode"; btn.dataset.mode = name;
      btn.textContent = name; mr.appendChild(btn);
    }
    const st = $("step"); st.innerHTML = "";
    for (const s of p.steps) {
      const o = document.createElement("option"); o.value = s.v; o.textContent = s.label;
      if (s.v === p.default_step) o.selected = true; st.appendChild(o);
    }
    step = p.default_step;
    // dual-watch + RX-control availability per radio
    const sub = $("rowSub"); if (sub) sub.style.display = p.dual_watch ? "" : "none";
    const pa = $("preampBtn"); if (pa) pa.style.display = p.has_preamp ? "" : "none";
    const at = $("attBtn"); if (at) at.style.display = p.has_att ? "" : "none";
    applyTitles();
  }
  $("radioSel").addEventListener("change", () => {
    renderRadio(selectedRadio());
    saveConn();
    blanked = true;                                 // new radio: blank the readout + waterfall until reconnect
    scope.clear();
    renderFreq($("mainFreq"), 0, false);
    renderFreq($("subFreq"), 0, false);
    $("lblLeft").textContent = $("lblCenter").textContent = $("lblRight").textContent = "";
    if (state.connected) {                          // switching radios -> drop the current connection
      fetch("/api/disconnect", { method: "POST" });
      $("conn").classList.add("open");              // reopen the connect controls so they can reconnect
    }
  });

  // ---- connection controls ----
  async function loadPorts() {
    try {
      const r = await fetch("/api/ports");
      const j = await r.json();
      const sel = $("transport");
      sel.querySelectorAll("option:not([value='sim']):not([value='lan'])").forEach(o => o.remove());
      const lanOpt = sel.querySelector("option[value='lan']");
      for (const p of j.ports) {
        const o = document.createElement("option");
        o.value = p.device; o.dataset.kind = "serial";
        o.textContent = `${p.device} — ${p.description}`.slice(0, 48);
        sel.insertBefore(o, lanOpt);
      }
      return j;
    } catch (_) { return null; }
  }
  function updateConnFields() {
    const sel = $("transport");
    const opt = sel.options[sel.selectedIndex];
    const isSerial = opt && opt.dataset.kind === "serial";
    $("baud").hidden = !isSerial;
    $("lanFields").hidden = sel.value !== "lan";
  }
  $("transport").addEventListener("change", updateConnFields);

  const CONN_KEY = "radiowebop.conn";
  function buildConnectBody() {
    const radio = $("radioSel").value;
    const sel = $("transport"), opt = sel.options[sel.selectedIndex];
    if (!opt || opt.value === "sim") return { transport: "sim", radio };
    if (opt.value === "lan") return {
      transport: "lan", radio, host: $("lanHost").value.trim(), port: 50001,
      user: $("lanUser").value, password: $("lanPass").value,
    };
    return { transport: "serial", radio, port: opt.value, baud: +$("baud").value || 115200 };
  }
  function saveConn() {
    try {
      localStorage.setItem(CONN_KEY, JSON.stringify({
        radio: $("radioSel").value,
        transport: $("transport").value, host: $("lanHost").value,
        user: $("lanUser").value, pass: $("lanPass").value, baud: $("baud").value,
      }));
    } catch (_) {}
  }
  function restoreConnFields() {
    let s = null;
    try { s = JSON.parse(localStorage.getItem(CONN_KEY) || localStorage.getItem("icomwebop.conn") || "null"); } catch (_) {}
    if (!s) return null;
    if (s.host) $("lanHost").value = s.host;
    if (s.user) $("lanUser").value = s.user;
    if (s.pass) $("lanPass").value = s.pass;
    if (s.baud) $("baud").value = s.baud;
    return s;
  }
  async function doConnect(body, silent) {
    const btn = $("connectBtn"); btn.textContent = "Connecting…"; btn.disabled = true;
    try {
      const r = await fetch("/api/connect", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const j = await r.json();
      if (j.ok) { $("conn").classList.remove("open"); blanked = false; }   // fresh connection -> live data again
      else if (!silent) alert("Connect failed: " + (j.error || "unknown"));
    } catch (e) { if (!silent) alert("Connect error: " + e); }
    finally { btn.textContent = "Connect"; btn.disabled = false; }
  }
  $("connToggle").onclick = () => $("conn").classList.toggle("open");
  $("connectBtn").onclick = () => {
    const body = buildConnectBody();
    if (body.transport === "lan" && !body.host) { alert("Enter the radio's IP address."); return; }
    saveConn();
    doConnect(body);                       // collapses the settings on success (mobile)
  };
  $("disconnectBtn").onclick = () => { $("conn").classList.add("open"); fetch("/api/disconnect", { method: "POST" }); };

  // ---- RX audio (Web Audio playback of 16-bit LE mono PCM) ----
  let audioCtx = null, audioGain = null, playTime = 0, audioOn = false;
  function startAudio() {
    if (!audioCtx) {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      audioGain = audioCtx.createGain();
      audioGain.gain.value = (+$("vol").value || 80) / 100;
      audioGain.connect(audioCtx.destination);
    }
    audioCtx.resume(); playTime = 0; audioOn = true;
  }
  function playAudio(buf) {
    if (!audioOn || !audioCtx) return;
    const rate = new DataView(buf).getUint16(2, true) || 16000;
    const pcm = new Int16Array(buf, 4);            // header is 4 bytes
    const n = pcm.length; if (!n) return;
    const ab = audioCtx.createBuffer(1, n, rate);
    const ch = ab.getChannelData(0);
    for (let i = 0; i < n; i++) ch[i] = pcm[i] / 32768;
    const src = audioCtx.createBufferSource(); src.buffer = ab; src.connect(audioGain);
    const now = audioCtx.currentTime;
    if (playTime < now + 0.05) playTime = now + 0.15;   // (re)build jitter buffer on underrun
    src.start(playTime); playTime += ab.duration;
  }

  // ---- TX mic (capture -> 16 kHz mono s16le -> server -> radio) ----
  // NOTE: this only streams audio to the radio's modulator; the radio transmits
  // ONLY when PTT is engaged AND its MOD Input is set to LAN.
  let micStream = null, micSrc = null, micProc = null, micSink = null, micOn = false;
  async function startMic() {
    if (!window.isSecureContext || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      alert("Microphone (TX) needs a secure HTTPS connection. You're on " + location.protocol +
        " — browsers only allow the mic over HTTPS (or localhost), not plain HTTP. " +
        "Serve the app over HTTPS (e.g. `tailscale serve`) and open the https:// address. " +
        "RX audio still works over HTTP.");
      return false;
    }
    try {
      micStream = await navigator.mediaDevices.getUserMedia(
        { audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
    } catch (e) { alert("Microphone access denied: " + e); return false; }
    if (!audioCtx) startAudio();
    micSrc = audioCtx.createMediaStreamSource(micStream);
    micProc = audioCtx.createScriptProcessor(4096, 1, 1);
    micSink = audioCtx.createGain(); micSink.gain.value = 0;   // silent sink (no mic monitor)
    const inRate = audioCtx.sampleRate, outRate = 16000, ratio = inRate / outRate;
    micProc.onaudioprocess = (e) => {
      if (!micOn) return;
      const input = e.inputBuffer.getChannelData(0);
      const outLen = Math.floor(input.length / ratio);
      const out = new Int16Array(outLen);
      let peak = 0;
      for (let i = 0; i < outLen; i++) {
        const s = input[(i * ratio) | 0];
        const a = s < 0 ? -s : s; if (a > peak) peak = a;
        out[i] = s < -1 ? -32768 : s > 1 ? 32767 : (s * 32767) | 0;
      }
      const ml = $("micLevel");
      if (ml) ml.style.width = Math.min(100, Math.round(Math.sqrt(peak) * 100)) + "%";   // perceptual level
      if (ws && ws.readyState === 1) ws.send(out.buffer);
    };
    micSrc.connect(micProc); micProc.connect(micSink); micSink.connect(audioCtx.destination);
    micOn = true; return true;
  }
  function stopMic() {
    micOn = false;
    for (const n of [micProc, micSrc, micSink]) { try { n && n.disconnect(); } catch (_) {} }
    if (micStream) micStream.getTracks().forEach(t => t.stop());
    micProc = micSrc = micSink = micStream = null;
    const ml = $("micLevel"); if (ml) ml.style.width = "0%";
  }

  $("audioBtn").onclick = () => {
    if (audioOn) { audioOn = false; $("audioBtn").classList.remove("active"); }
    else { startAudio(); $("audioBtn").classList.add("active"); }
  };
  $("micBtn").onclick = async () => {
    if (micOn) { stopMic(); $("micBtn").classList.remove("on"); }
    else if (await startMic()) $("micBtn").classList.add("on");
  };
  $("vol").oninput = (e) => { if (audioGain) audioGain.gain.value = (+e.target.value) / 100; };

  // ---- boot ----
  requestAnimationFrame(() => scope.resize());          // re-measure once the console layout settles
  window.addEventListener("load", () => scope.resize());
  const saved = restoreConnFields();
  updateConnFields();
  connectWS();
  loadRadios().then(() => {
    if (saved && saved.radio && radios.some(p => p.id === saved.radio)) $("radioSel").value = saved.radio;
    renderRadio(selectedRadio());
    return loadPorts();
  }).then((status) => {
    const sel = $("transport");
    if (saved && [...sel.options].some(o => o.value === saved.transport)) sel.value = saved.transport;
    updateConnFields();
    if (status && status.connected) {              // already connected — reflect its radio
      if (status.radio && radios.some(p => p.id === status.radio)) { $("radioSel").value = status.radio; renderRadio(selectedRadio()); }
      $("conn").classList.remove("open");          // collapse settings (mobile)
      return;
    }
    const body = buildConnectBody();               // else connect to the remembered method (silent on failure)
    if (body.transport === "lan" && !body.host) doConnect({ transport: "sim", radio: $("radioSel").value }, true);
    else doConnect(body, true);
  });
})();

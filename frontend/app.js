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
                  "mic", "comp_level", "vox_gain", "mon_level", "cw_pitch", "keyer_speed"];

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
    ws.onclose = (e) => {
      if (e.code === 1008) { location.replace("/login"); return; }   // auth required/expired -> sign in
      setTimeout(connectWS, 1200);
    };
  }

  function send(obj) {
    if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj));
  }

  function onMessage(ev) {
    if (typeof ev.data === "string") {
      const msg = JSON.parse(ev.data);
      if (msg.type === "state") updateState(msg);
      else if (msg.type === "meter") updateMeter(msg);
      else if (msg.type === "menu") updateMenu(msg.values || {});
      else if (msg.type === "host_audio") { if (msg.error) alert("Host audio — " + msg.error); }
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
    let lo, hi;
    if (m.mode === 1) { lo = m.lower; hi = m.upper; }
    else { const vc = m.tuned || m.center; lo = vc - m.span / 2; hi = vc + m.span / 2; }
    // center freq label removed — the tuned-channel marker sits there and overlapped it
    $("lblLeft").textContent = formatFreq(lo);
    $("lblRight").textContent = formatFreq(hi);
  }

  // ---- state -> UI ----
  // Meter render reads the live `state`; driven both by full-state frames and the fast
  // 'meter' channel (which carries only meter/meter_val/smeter/smeter_s between full frames).
  function renderMeter() {
    const s = state || {};
    const isS = (s.meter || "S") === "S";
    const mmax = isS ? 240 : (s.meter_max || 255);
    const mf = $("meterFill"), mv = $("meterVal");
    if (mf) mf.style.width = Math.min(100, (s.meter_val || 0) / mmax * 100) + "%";
    if (mv) mv.textContent = isS ? (s.smeter_s || "S0") : (s.meter + " " + (s.meter_val || 0));
  }
  function updateMeter(m) {
    state.meter = m.meter; state.meter_val = m.meter_val;
    state.smeter = m.smeter; state.smeter_s = m.smeter_s;
    renderMeter();
  }
  function updateState(s) {
    const justConnected = !state.connected && s.connected;
    state = s;
    if (justConnected) onMenuReconnect();           // drop stale menu cache + refresh open groups
    // dual-watch band readout (MAIN/SUB); single-rx radios show MAIN only
    $("rowSub").style.display = s.dual_watch ? "" : "none";
    if (blanked) {                                  // radio just changed: keep VFOs zeroed until a fresh connect
      renderFreq($("mainFreq"), 0, false);
      renderFreq($("subFreq"), 0, false);
    } else {
      fillBand("main", s.main, s.active_band !== "sub");
      fillBand("sub", s.sub, s.active_band === "sub");
    }
    // no-scope radios (Yaesu CAT): no sweeps. While RX audio runs we draw an AF
    // spectrum (afTimer); otherwise drive the band plan + freq labels from the freq.
    if (!blanked && currentRadio && currentRadio.has_scope === false && !afTimer) {
      scope.showStatic(s.freq, 200000);
      updateScopeLabels(scope.meta);
    }
    $("rowMain").classList.toggle("active", s.active_band !== "sub");
    $("rowSub").classList.toggle("active", s.active_band === "sub");
    setInd("mainInd", s, s.active_band !== "sub");
    setInd("subInd", s, s.active_band === "sub");

    // multi-meter (S live; TX meters wired for M3). The value also arrives on the fast
    // lightweight 'meter' channel between full-state frames — render via the shared helper.
    renderMeter();
    setActive(".m-btn", b => b.dataset.meter === (s.meter || "S"));

    // RX toggles
    { const pb = $("preampBtn"); if (pb) {
        const labels = (currentRadio && currentRadio.preamp_labels) || ["OFF", "P.AMP"];
        pb.textContent = labels[s.preamp || 0] || labels[0];
        pb.classList.toggle("on", (s.preamp || 0) > 0);
      } }
    $("attBtn").classList.toggle("on", (s.att || 0) > 0);
    $("lockBtn").classList.toggle("on", !!s.lock);
    { const tb = $("tunerBtn"); if (tb) tb.classList.toggle("on", (s.tuner || 0) > 0); }

    // connection
    const on = !!s.connected;
    $("led").classList.toggle("on", on);
    $("connlabel").textContent = on ? (s.transport || "Connected") : "Disconnected";
    $("audioAvail").textContent = s.audio ? "• available" : (currentTransportKind() === "serial" ? "• USB device" : "• LAN only");
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

    // NAR/WIDE toggle + FM Tone/DCS/shift + the extra operating controls (Yaesu)
    if ($("narBtn")) $("narBtn").classList.toggle("on", (s.narrow || 0) > 0);
    syncFmPanel(s);
    syncExtPanel(s);
    syncIcomCw(s);

    // level sliders (don't fight an active drag) + value readouts
    for (const t of LEVELS) {
      const el = $(t);
      if (el && document.activeElement !== el && s[t] != null) { el.value = s[t]; setFill(el); }
      if (s[t] != null) $(t + "Val").textContent = fmtLevel(t, s[t]);
    }

    // Keep the marker + filter passband tracking the tuned freq between real CI-V
    // scope sweeps. The AF audio scope (no-scope radios with RX audio on) owns the
    // overlay and intentionally draws no marker (the dial sits at an edge), so skip
    // this while it runs — otherwise every state update stamps a marker the next AF
    // frame wipes, i.e. it flickers.
    if (!afTimer) {
      scope.setOpMode(s.mode_name);
      scope.meta.tuned = s.freq;
      scope.meta.filterBw = s.filter_bw;
      scope.drawOverlay();
    }
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
    "CW-USB": "CW (USB side)", "CW-LSB": "CW (LSB side)",
    "AM": "Amplitude Modulation", "FM": "Frequency Modulation", "FM-N": "FM Narrow", "AM-N": "AM Narrow",
    "RTTY": "Radio Teletype", "RTTY-R": "RTTY Reverse",
    "RTTY-LSB": "RTTY (LSB)", "RTTY-USB": "RTTY (USB)",
    "DATA-LSB": "Data (LSB)", "DATA-USB": "Data (USB)", "DATA-FM": "Data (FM)", "C4FM": "C4FM Digital",
    "DV": "Digital Voice", "DD": "Digital Data",
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

  // ---- FM Tone / DCS (CTCSS + DCS + repeater shift), Yaesu operating controls ----
  // index = the Yaesu CN "number" for that tone/code (must match the backend tables).
  const CTCSS_TONES = ["67.0", "69.3", "71.9", "74.4", "77.0", "79.7", "82.5", "85.4", "88.5", "91.5",
    "94.8", "97.4", "100.0", "103.5", "107.2", "110.9", "114.8", "118.8", "123.0", "127.3", "131.8",
    "136.5", "141.3", "146.2", "151.4", "156.7", "159.8", "162.2", "165.5", "167.9", "171.3", "173.8",
    "177.3", "179.9", "183.5", "186.2", "189.9", "192.8", "196.6", "199.5", "203.5", "206.5", "210.7",
    "218.1", "225.7", "229.1", "233.6", "241.8", "250.3", "254.1"];
  const DCS_CODES = ["023", "025", "026", "031", "032", "036", "043", "047", "051", "053", "054", "065",
    "071", "072", "073", "074", "114", "115", "116", "122", "125", "131", "132", "134", "143", "145",
    "152", "155", "156", "162", "165", "172", "174", "205", "212", "223", "225", "226", "243", "244",
    "245", "246", "251", "252", "255", "261", "263", "265", "266", "271", "274", "306", "311", "315",
    "325", "331", "332", "343", "346", "351", "356", "364", "365", "371", "411", "412", "413", "423",
    "431", "432", "445", "446", "452", "454", "455", "462", "464", "465", "466", "503", "506", "516",
    "523", "526", "532", "546", "565", "606", "612", "624", "627", "631", "632", "654", "662", "664",
    "703", "712", "723", "731", "732", "734", "743", "754"];
  const TONE_MODES = [[0, "OFF"], [2, "TONE"], [1, "TSQL"], [3, "DCS"], [4, "DCS-ENC"]]; // [wire value, label]
  const RPT_SHIFTS = [[0, "SIMPLEX"], [1, "+ SHIFT"], [2, "- SHIFT"]];
  const FM_MODES = ["FM", "FM-N", "DATA-FM", "C4FM"];
  let fmWired = false;

  function setupFmControls() {
    if (fmWired) return; fmWired = true;
    const opt = (v, t) => { const o = document.createElement("option"); o.value = v; o.textContent = t; return o; };
    const tm = $("toneMode"), tf = $("toneFreq"), dc = $("dcsCode"), rs = $("rptShift");
    if (!tm) return;
    TONE_MODES.forEach(([v, l]) => tm.appendChild(opt(v, l)));
    RPT_SHIFTS.forEach(([v, l]) => rs.appendChild(opt(v, l)));
    CTCSS_TONES.forEach((hz, i) => tf.appendChild(opt(i, hz + " Hz")));
    DCS_CODES.forEach((c, i) => dc.appendChild(opt(i, c)));
    tm.addEventListener("change", () => send({ action: "tone_mode", value: +tm.value }));
    tf.addEventListener("change", () => send({ action: "tone_freq", idx: +tf.value }));
    dc.addEventListener("change", () => send({ action: "dcs_code", idx: +dc.value }));
    rs.addEventListener("change", () => send({ action: "rpt_shift", value: +rs.value }));
  }

  const FM_FAMILY = ["FM", "FM-N", "DATA-FM", "C4FM"];
  function syncFmPanel(s) {
    const cap = (currentRadio && currentRadio.capabilities) || {};
    const show = !!cap.fm_tone && FM_FAMILY.includes(s.mode_name);
    const g = $("fmGrp"); if (g) g.style.display = show ? "block" : "none";   // beat the #fmGrp{display:none} default
    if (!show) return;
    // DCS + repeater-shift only where the radio has them (Yaesu); Icom does tone/TSQL only,
    // and uses the SPLIT·RIT duplex control for shift.
    const dcs = cap.fm_dcs !== false;
    [...$("toneMode").options].forEach(o => { if (o.value === "3" || o.value === "4") o.hidden = !dcs; });
    const hideRow = (id, hide) => { const e = $(id); if (e && e.parentElement) e.parentElement.style.display = hide ? "none" : ""; };
    hideRow("dcsCode", !dcs); hideRow("rptShift", !dcs);
    const set = (id, v) => { const el = $(id); if (el && document.activeElement !== el) el.value = String(v); };
    set("toneMode", s.tone_mode || 0); set("toneFreq", s.tone_freq || 0);
    set("dcsCode", s.dcs_code || 0); set("rptShift", s.rpt_shift || 0);
  }

  // ---- Icom CW / filter group: APF, break-in, CW pitch/speed, filter shape ----
  const APF_LABELS = ["OFF", "WIDE", "MID", "NAR"], BKIN_LABELS = ["OFF", "SEMI", "FULL"];
  function syncIcomCw(s) {
    const cap = (currentRadio && currentRadio.capabilities) || {};
    const g = $("icomCwGrp"); if (!g) return;
    const cw = ["CW", "CW-R"].includes(s.mode_name);       // Icom CW mode names
    g.style.display = (cap.icom_cw && cw) ? "" : "none";
    if (!(cap.icom_cw && cw)) return;
    const apf = s.apf || 0, bkin = s.bkin || 0;
    const ab = $("apfCycBtn"); if (ab) { ab.textContent = "APF: " + APF_LABELS[apf]; ab.classList.toggle("on", apf > 0); }
    const bb = $("bkinCycBtn"); if (bb) { bb.textContent = "BK-IN: " + BKIN_LABELS[bkin]; bb.classList.toggle("on", bkin > 0); }
    const fs = $("fshapeBtn"); if (fs) { fs.textContent = (s.filter_shape ? "SOFT" : "SHARP"); fs.classList.toggle("on", !!s.filter_shape); }
  }

  // ---- extra operating controls: WIDTH / CONTOUR / APF / CW / TXW / scan (Yaesu) ----
  const SSB_MODES = ["LSB", "USB"], CW_MODES = ["CW-USB", "CW-LSB"];
  const RTTYDATA_MODES = ["RTTY-LSB", "RTTY-USB", "DATA-LSB", "DATA-USB"];
  const WIDTH_MODES = [...SSB_MODES, ...CW_MODES, ...RTTYDATA_MODES];
  const CONTOUR_MODES = [...WIDTH_MODES, "AM"];
  // SH DSP bandwidth per context — [code, Hz]; keyed by mode class then narrow(1)/wide(0).
  // From the FT-991A CAT manual WIDTH table. NAR on => narrow column, NAR off => wide column.
  // lists are sorted ascending by Hz so the -/+ stepper moves narrower/wider monotonically.
  const SH_VALID = {
    ssb: { 1: [[1, 200], [2, 400], [3, 600], [4, 850], [5, 1100], [6, 1350], [0, 1500], [7, 1500], [8, 1650], [9, 1800]],
           0: [[9, 1800], [10, 1950], [11, 2100], [12, 2200], [13, 2300], [0, 2400], [14, 2400], [15, 2500], [16, 2600], [17, 2700], [18, 2800], [19, 2900], [20, 3000], [21, 3200]] },
    cw:  { 1: [[1, 50], [2, 100], [3, 150], [4, 200], [5, 250], [6, 300], [7, 350], [8, 400], [9, 450], [0, 500], [10, 500]],
           0: [[10, 500], [11, 800], [12, 1200], [13, 1400], [14, 1700], [15, 2000], [0, 2400], [16, 2400], [17, 3000]] },
    data:{ 1: [[1, 50], [2, 100], [3, 150], [4, 200], [5, 250], [0, 300], [6, 300], [7, 350], [8, 400], [9, 450], [10, 500]],
           0: [[0, 500], [10, 500], [11, 800], [12, 1200], [13, 1400], [14, 1700], [15, 2000], [16, 2400], [17, 3000]] },
  };
  function widthClass(m) { return CW_MODES.includes(m) ? "cw" : RTTYDATA_MODES.includes(m) ? "data" : "ssb"; }
  function widthList() { return (SH_VALID[widthClass(state.mode_name)] || {})[(state.narrow || 0) ? 1 : 0] || []; }
  function stepWidth(delta) {
    const list = widthList(); if (!list.length) return;
    let i = list.findIndex(([c]) => c === (state.width || 0)); if (i < 0) i = 0;
    i = Math.max(0, Math.min(list.length - 1, i + delta));
    send({ action: "width", code: list[i][0] });
  }

  let extWired = false;
  function setupExtControls() {
    if (extWired) return; extWired = true;
    // sliders with their own actions: update readout live (input), send on release (change)
    const wire = (id, act, key, fmt) => {
      const el = $(id); if (!el) return;
      const vv = $(id + "Val");
      el.addEventListener("input", () => { if (vv) vv.textContent = fmt(+el.value); if (typeof setFill === "function") setFill(el); });
      el.addEventListener("change", () => send({ action: act, [key]: +el.value }));
    };
    wire("contour_freq", "contour_freq", "hz", v => v + " Hz");
    wire("apf_freq", "apf_freq", "v", v => ((v - 25) * 10) + " Hz");
    wire("key_speed", "key_speed", "wpm", v => v + " wpm");
    wire("key_pitch", "key_pitch", "code", v => (300 + v * 10) + " Hz");
  }

  function syncExtPanel(s) {
    const cap = (currentRadio && currentRadio.capabilities) || {};
    const ext = !!cap.ext_ops, mode = s.mode_name || "";
    const disp = (id, on) => { const e = $(id); if (e) e.style.display = on ? "" : "none"; };
    disp("widthGrp", ext && WIDTH_MODES.includes(mode));
    disp("contourGrp", ext && CONTOUR_MODES.includes(mode));
    disp("apfGrp", ext && CW_MODES.includes(mode));
    disp("cwGrp", ext && CW_MODES.includes(mode));
    disp("opsGrp", ext);
    disp("paramEqBtn", ext); disp("txwBtn", ext); disp("qsplitBtn", ext);
    if (!ext) return;
    const tog = (id, v) => { const e = $(id); if (e) e.classList.toggle("on", (v || 0) > 0); };
    tog("contourBtn", s.contour); tog("apfBtn", s.apf); tog("bkinBtn", s.bkin); tog("keyerBtn", s.keyer);
    tog("spotBtn", s.spot); tog("paramEqBtn", s.param_eq); tog("txwBtn", s.txw); tog("fastBtn", s.fast);
    const sl = (id, v, fmt) => {
      const e = $(id); if (e && document.activeElement !== e) { e.value = v; if (typeof setFill === "function") setFill(e); }
      const vv = $(id + "Val"); if (vv) vv.textContent = fmt(v);
    };
    sl("contour_freq", s.contour_freq || 10, v => v + " Hz");
    sl("apf_freq", s.apf_freq == null ? 25 : s.apf_freq, v => ((v - 25) * 10) + " Hz");
    sl("key_speed", s.key_speed || 20, v => v + " wpm");
    sl("key_pitch", s.key_pitch == null ? 40 : s.key_pitch, v => (300 + v * 10) + " Hz");
    const wv = $("widthVal"); if (wv) { const e = widthList().find(([c]) => c === (s.width || 0)); wv.textContent = e ? e[1] + " Hz" : "—"; }
    setActive(".scan", b => +b.dataset.dir === (s.scan || 0));
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
      if (fn === "preamp") {                              // cycle through the radio's preamp states
        const labels = (currentRadio && currentRadio.preamp_labels) || ["OFF", "P.AMP"];
        send({ action: "preamp", level: ((state.preamp || 0) + 1) % labels.length });
      } else {
        // generic on/off toggle: att/tuner/narrow/contour/apf/bkin/keyer/spot/param_eq/txw/fast
        const on = fn === "lock" ? !state.lock : !((state[fn] || 0) > 0);
        send({ action: fn, on });
      }
    }
    else if (act === "tune_atu") send({ action: "tune_atu" });
    else if (act === "width_d") stepWidth(+b.dataset.d);
    else if (act === "scan") send({ action: "scan", dir: +b.dataset.dir });
    else if (act === "zero_in") send({ action: "zero_in" });
    else if (act === "quick_split") send({ action: "quick_split" });
    else if (act === "apf_cyc") send({ action: "apf_lvl", v: (((state.apf || 0) + 1) % 4) });   // Icom APF OFF/WIDE/MID/NAR
    else if (act === "bkin_cyc") send({ action: "bkin_lvl", v: (((state.bkin || 0) + 1) % 3) });  // Icom BK-IN OFF/SEMI/FULL
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
  function fmtLevel(t, v) {
    if (t === "rfpwr") return Math.round(v / 255 * 100) + "%";
    if (t === "cw_pitch") return (300 + Math.round(v / 255 * 600)) + " Hz";   // Icom 14 09: 300-900 Hz
    if (t === "keyer_speed") return (6 + Math.round(v / 255 * 42)) + " wpm";    // Icom 14 0C: 6-48 WPM
    return "" + v;
  }
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
      // keep this gesture on the split: don't let the wrap's tuning handler also
      // start (it would steal the pointer capture, so our pointerup never fires).
      e.preventDefault(); e.stopPropagation();
    });
    split.addEventListener("pointermove", (e) => {
      if (!down || !(e.buttons & 1)) return;   // ignore plain hover moves after release
      e.stopPropagation();
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
    split.addEventListener("lostpointercapture", end);   // safety: capture lost -> stop dragging
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
    const legend = $("bandLegend"), hint = $("scopeHint");

    function updateLegend() {
      if (!legend) return;
      if (!scope.showBandplan) { legend.hidden = true; legend._sig = null; if (hint) hint.hidden = false; return; }
      const kinds = scope.visibleKinds(), sig = kinds.join(",");
      if (sig === legend._sig) return;            // unchanged -> skip re-render
      legend._sig = sig;
      legend.innerHTML = kinds.map((k) =>
        '<span class="lg"><span class="lg-sw" style="background:' + ((colors[k] || "rgba(150,160,180,") + "0.85)") +
        '"></span>' + (klabel[k] || k) + "</span>").join("");
      legend.hidden = kinds.length === 0;
      if (hint) hint.hidden = kinds.length > 0;   // swap the hint out for the legend when we have one
    }

    function apply(on) {
      scope.showBandplan = on;
      btn.classList.toggle("active", on);
      if (!on && tip) tip.hidden = true;
      scope.drawOverlay();
      updateLegend();
      try { localStorage.setItem("radiowebop.bandplan", on ? "1" : "0"); } catch (_) {}
    }
    btn.addEventListener("click", () => apply(!scope.showBandplan));
    let saved = "1";                                // band plan ON by default
    try { saved = localStorage.getItem("radiowebop.bandplan") || "1"; } catch (_) {}
    apply(saved === "1");
    setInterval(updateLegend, 600);               // refresh the color key as you tune / change span

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
  // ---- SET menu (Setup tab): data-driven accordion from currentRadio.menu ----
  let menuItems = {}, menuCache = {};
  function renderMenu(p) {
    const root = $("menuRoot"); if (!root) return;
    menuItems = {}; menuCache = {}; root.innerHTML = "";
    const items = (p && p.menu) || [];
    if (!items.length) { root.hidden = true; return; }
    root.hidden = false;
    for (const it of items) menuItems[it.num] = it;
    const order = [], byGroup = {};
    for (const it of items) {
      if (!byGroup[it.group]) { byGroup[it.group] = []; order.push(it.group); }
      byGroup[it.group].push(it);
    }
    const title = document.createElement("div");
    title.className = "grp-title";
    title.innerHTML = 'SET MENU <span class="menu-hint">tap a section to read it from the radio</span>';
    root.appendChild(title);
    for (const g of order) {
      const sec = document.createElement("div"); sec.className = "menu-grp"; sec.dataset.group = g;
      const head = document.createElement("button"); head.className = "menu-ghead"; head.type = "button";
      head.innerHTML = `<span>${rhEsc(g)}</span><span class="menu-count">${byGroup[g].length}</span>`;
      const body = document.createElement("div"); body.className = "menu-gbody hide";
      head.addEventListener("click", () => toggleMenuGroup(g, sec, body, byGroup[g]));
      sec.appendChild(head); sec.appendChild(body); root.appendChild(sec);
    }
  }
  function toggleMenuGroup(g, sec, body, items) {
    const open = body.classList.toggle("hide") === false;
    sec.classList.toggle("open", open);
    if (!open) return;
    if (!body._built) { for (const it of items) body.appendChild(buildMenuItem(it)); body._built = true; }
    if (state.connected) send({ action: "menu_read_group", group: g });   // lazy: read this group only
  }
  function buildMenuItem(it) {
    const row = document.createElement("div");
    row.className = "menu-item" + (it.critical ? " critical" : "");
    row.dataset.num = it.num;
    const name = document.createElement("div"); name.className = "mi-name";
    name.innerHTML = `<span>${rhEsc(it.name)}${it.critical ? ' <span class="mi-warn" title="connection / transmit-sensitive">&#9888;</span>' : ''}</span>` +
                     `<span class="mi-num">${String(it.num).padStart(3, "0")}</span>`;
    if (it.note) name.title = it.note;
    row.appendChild(name);
    if (it.readonly) {
      const ro = document.createElement("span"); ro.className = "mi-ro"; ro.dataset.role = "val"; ro.textContent = "—";
      row.appendChild(ro);
    } else if (it.kind === "enum") {
      const ctl = document.createElement("div"); ctl.className = "mi-ctl";
      const sel = document.createElement("select"); sel.dataset.role = "input";
      for (let i = 0; i < it.options.length; i++) {
        const o = document.createElement("option"); o.value = i; o.textContent = it.options[i];
        if (/reserved/i.test(it.options[i])) o.disabled = true;   // documented index gap — not selectable
        sel.appendChild(o);
      }
      sel.addEventListener("change", () => writeMenu(it, +sel.value));
      ctl.appendChild(sel); row.appendChild(ctl);
    } else {
      const ctl = document.createElement("div"); ctl.className = "mi-ctl";
      const r = document.createElement("input"); r.type = "range"; r.dataset.role = "input";
      r.min = it.min; r.max = it.max; r.step = it.step || 1;
      r.value = (it.kind === "signed-int") ? Math.max(it.min, Math.min(it.max, 0)) : it.min;
      const val = document.createElement("span"); val.className = "mi-val"; val.dataset.role = "val"; val.textContent = "—";
      const show = () => { val.textContent = r.value + (it.unit ? " " + it.unit : ""); };
      r.addEventListener("input", show);
      r.addEventListener("change", () => writeMenu(it, +r.value));
      ctl.appendChild(r); ctl.appendChild(val); row.appendChild(ctl);
    }
    if (it.num in menuCache) applyMenuValue(it, row, menuCache[it.num]);
    return row;
  }
  function writeMenu(it, value) {
    if (!state.connected || (it.critical && !confirm(`Change "${it.name}" (menu ${String(it.num).padStart(3, "0")})?\n` +
        (it.note || "This is a connection / transmit-sensitive setting.")))) {
      revertMenuItem(it); return;          // disconnected or cancelled -> restore the real value
    }
    send({ action: "menu_write", num: it.num, value: value });
  }
  function revertMenuItem(it) {
    if (it.num in menuCache) refreshMenuItem(it.num);                  // repaint from last known value
    else if (state.connected) send({ action: "menu_read", num: it.num });  // none cached -> ask the radio
  }
  function onMenuReconnect() {
    menuCache = {};                                                   // stale values from the old session
    document.querySelectorAll(".menu-grp.open").forEach(sec =>
      send({ action: "menu_read_group", group: sec.dataset.group }));   // refresh whatever is open
  }
  function updateMenu(values) {
    for (const k in values) { menuCache[+k] = values[k]; refreshMenuItem(+k); }
  }
  function refreshMenuItem(num) {
    const it = menuItems[num]; if (!it || !(num in menuCache)) return;
    const row = document.querySelector(`.menu-item[data-num="${num}"]`);
    if (row) applyMenuValue(it, row, menuCache[num]);
  }
  function applyMenuValue(it, row, value) {
    const inp = row.querySelector('[data-role="input"]'), valEl = row.querySelector('[data-role="val"]');
    if (it.readonly) { if (valEl) valEl.textContent = value; return; }
    if (it.kind === "enum") {
      if (inp && document.activeElement !== inp) {
        const idx = (typeof value === "number") ? value : it.options.indexOf(value);
        if (idx >= 0) inp.value = idx;
      }
    } else {
      if (inp && document.activeElement !== inp) inp.value = value;
      if (valEl) valEl.textContent = value + (it.unit ? " " + it.unit : "");
    }
  }

  async function loadRadios() {
    try {
      const j = await (await fetch("/api/radios")).json();
      radios = j.radios || [];
      const sel = $("radioSel"); sel.innerHTML = "";
      for (const p of radios) {
        const o = document.createElement("option"); o.value = p.id;
        o.textContent = (p.make ? p.make + " " : "") + p.name; sel.appendChild(o);
      }
    } catch (_) { radios = []; }
    return radios;
  }
  function renderRadio(p) {
    if (!p) return;
    currentRadio = p;
    const label = (p.make ? p.make + " " : "") + p.name;
    $("modelLabel").textContent = state.connected ? (state.radio_name || label) : label;
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
    // capability-driven gating: show only what the radio supports (falls back to flat flags)
    const cap = p.capabilities || {};
    const capHas = (k, flat) => (k in cap ? cap[k] : flat);
    const sub = $("rowSub"); if (sub) sub.style.display = capHas("dual_watch", p.dual_watch) ? "" : "none";
    const pa = $("preampBtn"); if (pa) {
      pa.style.display = capHas("preamp", p.has_preamp) ? "" : "none";
      const pl = p.preamp_labels || ["OFF", "P.AMP"];
      pa.textContent = pl[state.preamp || 0] || pl[0];
    }
    const at = $("attBtn"); if (at) at.style.display = capHas("att", p.has_att) ? "" : "none";
    const nb = $("narBtn"); if (nb) nb.style.display = capHas("narrow", false) ? "" : "none";
    // FM Tone/DCS group: base-hide unless the rig supports it (updateState shows it in FM modes)
    const fg = $("fmGrp"); if (fg && !capHas("fm_tone", false)) fg.style.display = "none";
    const tu = $("tunerBtn"); if (tu) tu.style.display = capHas("tuner", p.has_tuner) ? "" : "none";
    const tn = $("tuneBtn"); if (tn) tn.style.display = capHas("tuner", p.has_tuner) ? "" : "none";
    // hide the whole ANTENNA TUNER group (now in the TX panel) when the rig has no internal ATU
    const tg = $("tunerGrp"); if (tg) tg.style.display = capHas("tuner", p.has_tuner) ? "" : "none";
    // VFO A/B select: hidden when the rig has no CAT active-VFO selector (FT-991A); A=B/SWAP stay
    const vfoSel = capHas("vfo_select", true);
    document.querySelectorAll('[data-act="vfo"][data-code="0"], [data-act="vfo"][data-code="1"]')
      .forEach(b => { b.style.display = vfoSel ? "" : "none"; });
    // RX-DSP + TX-function buttons: only those the rig reports
    document.querySelectorAll('[data-act="rxfunc"][data-fn]').forEach(b => {
      const fn = b.dataset.fn;
      const list = ["nb", "nr", "anotch", "mnotch"].includes(fn) ? cap.rx_dsp : cap.tx_funcs;
      if (Array.isArray(list)) b.style.display = list.includes(fn) ? "" : "none";
    });
    // meter buttons: only the meters the rig supports
    if (Array.isArray(cap.meters)) document.querySelectorAll('[data-act="meter"][data-meter]')
      .forEach(b => { b.style.display = cap.meters.includes(b.dataset.meter) ? "" : "none"; });
    // no-scope radios (Yaesu CAT): hide the scope-only controls, show the notice
    const noScope = p.has_scope === false;
    const ns = $("noScope"); if (ns) ns.hidden = !noScope;
    const soc = $("scopeOnlyCtl"); if (soc) soc.hidden = noScope;
    if (p.default_baud) $("baud").value = p.default_baud;
    // COM-only radios (FT-991A, IC-7300 family): no RS-BA1 LAN — hide that option
    const lanOpt = $("transport").querySelector('option[value="lan"]');
    if (lanOpt) lanOpt.hidden = p.has_network === false;
    if (p.has_network === false && $("transport").value === "lan") {
      $("transport").value = "sim";
    }
    updateConnFields();
    renderMenu(p);
    syncExtPanel(state);         // show/hide the WIDTH/CONTOUR/APF/CW/ops groups for this radio+mode
    syncIcomCw(state);           // Icom CW/filter group visibility for this radio+mode
    applyTitles();
  }
  $("radioSel").addEventListener("change", () => {
    renderRadio(selectedRadio());
    renderRadioHelp();
    applyConnForRadio($("radioSel").value);         // restore THIS radio's last connection + settings + audio
    CONNS.last = $("radioSel").value; persistConns();
    stopAllAudio();                                 // drop any audio/AF-scope from the old radio
    blanked = true;                                 // new radio: blank the readout + waterfall until reconnect
    scope.clear();
    renderFreq($("mainFreq"), 0, false);
    renderFreq($("subFreq"), 0, false);
    $("lblLeft").textContent = $("lblRight").textContent = "";
    if (state.connected) {                          // switching radios -> drop the current connection
      fetch("/api/disconnect", { method: "POST" });
      $("conn").classList.add("open");              // reopen the connect controls so they can reconnect
    }
  });

  // ---- connection help popover ("?" beside the radio picker) ----
  function rhEsc(s) { return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }
  function renderRadioHelp() {
    const help = $("radioHelp"), p = selectedRadio();
    if (!help || !p) return;
    const secs = p.connect_help || [];
    let html = '<div class="rh-title">' + rhEsc((p.make ? p.make + " " : "") + p.name) +
      ' <small>— set these on the radio</small></div>';
    if (!secs.length) {
      html += '<div class="rh-sec">No special radio settings needed — just connect.</div>';
    } else {
      for (const s of secs) {
        html += '<div class="rh-sec"><div class="rh-h">' + rhEsc(s.title) + '</div><ul>' +
          (s.items || []).map((it) => "<li>" + rhEsc(it) + "</li>").join("") + "</ul></div>";
      }
    }
    help.innerHTML = html;
  }
  function toggleRadioHelp(force) {
    const help = $("radioHelp"), btn = $("radioHelpBtn");
    if (!help || !btn) return;
    const show = force === undefined ? help.hidden : force;
    if (show) renderRadioHelp();
    help.hidden = !show;
    btn.classList.toggle("on", show);
  }
  $("radioHelpBtn").addEventListener("click", (e) => { e.stopPropagation(); toggleRadioHelp(); });
  document.addEventListener("click", (e) => {
    const help = $("radioHelp");
    if (help && !help.hidden && !e.target.closest(".radio-pick")) toggleRadioHelp(false);
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") toggleRadioHelp(false); });

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
        // keep the DISTINGUISHING part of the description (e.g. "Enhanced COM Port") —
        // the chip name is the same on both Yaesu ports, and CAT is only on Enhanced.
        let desc = p.description || "";
        const colon = desc.indexOf(":");
        if (colon >= 0) desc = desc.slice(colon + 1).trim();
        o.textContent = (`${p.device} — ${desc}` || p.device).slice(0, 64);
        o.title = `${p.device} — ${p.description}`;
        if (/enhanced/i.test(p.description || "")) o.dataset.enhanced = "1";
        sel.insertBefore(o, lanOpt);
      }
      return j;
    } catch (_) { return null; }
  }
  function updateConnFields() {
    const sel = $("transport");
    const opt = sel.options[sel.selectedIndex];
    const isSerial = opt && opt.dataset.kind === "serial";
    const isLan = sel.value === "lan";
    $("baud").hidden = !isSerial;
    $("lanFields").hidden = !isLan;
    const devs = $("audioDevs");
    if (devs) {
      devs.hidden = !(isSerial || isLan);            // COM: Radio RX/TX + Mic In; LAN: Mic In only; sim: none
      devs.querySelectorAll(".usb-only").forEach((el) => { el.hidden = !isSerial; });  // radio channels are USB-only
    }
    if (isSerial || isLan) loadAudioDevices();
    const note = $("audioAvail");
    if (note) note.textContent = isSerial ? "• USB device" : (isLan ? "• network" : "");
  }
  $("transport").addEventListener("change", () => {
    stopAllAudio();              // audio belongs to a transport; don't let a stream outlive a switch
    updateConnFields(); saveConn();
  });

  // ---- per-radio connection memory (transport + settings, and COM audio devices) ----
  const CONNS_KEY = "radiowebop.conns";
  let CONNS = { last: null, radios: {} };
  let wantRxDev = null, wantMicDev = null, wantMicInDev = null;   // device IDs to restore once the lists populate
  (function loadConns() {
    try { const v = JSON.parse(localStorage.getItem(CONNS_KEY)); if (v && v.radios) CONNS = v; } catch (_) {}
    if (!CONNS.radios) CONNS.radios = {};
    if (!Object.keys(CONNS.radios).length) {        // migrate the old single-config key
      try {
        const old = JSON.parse(localStorage.getItem("radiowebop.conn") || localStorage.getItem("icomwebop.conn") || "null");
        if (old && old.radio) { CONNS.radios[old.radio] = { transport: old.transport, host: old.host, user: old.user, pass: old.pass, baud: old.baud }; CONNS.last = old.radio; }
      } catch (_) {}
    }
  })();
  function persistConns() { try { localStorage.setItem(CONNS_KEY, JSON.stringify(CONNS)); } catch (_) {} }

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
  function currentConf() {
    const sel = $("transport"), opt = sel.options[sel.selectedIndex];
    const serial = !!(opt && opt.dataset.kind === "serial");
    return {
      transport: serial ? "serial" : (opt ? opt.value : "sim"),
      port: serial ? opt.value : "",
      baud: $("baud").value,
      host: $("lanHost").value, user: $("lanUser").value, pass: $("lanPass").value,
      rxDev: $("rxDev") ? $("rxDev").value : "", micDev: $("micDev") ? $("micDev").value : "",
      micInDev: $("micInDev") ? $("micInDev").value : "",
    };
  }
  function saveConn() {
    const id = $("radioSel").value; if (!id) return;
    CONNS.radios[id] = currentConf(); CONNS.last = id; persistConns();
  }
  // restore a radio's last-used connection type + settings (+ COM audio devices)
  function applyConnForRadio(id) {
    const c = (CONNS.radios && CONNS.radios[id]) || {};
    if (c.host != null) $("lanHost").value = c.host;
    if (c.user != null) $("lanUser").value = c.user;
    if (c.pass != null) $("lanPass").value = c.pass;
    if (c.baud) $("baud").value = c.baud;
    const sel = $("transport");
    let v = "sim";
    if (c.transport === "serial" && c.port && [...sel.options].some((o) => o.dataset.kind === "serial" && o.value === c.port)) v = c.port;
    else if (c.transport === "lan") { const lo = sel.querySelector('option[value="lan"]'); if (lo && !lo.hidden) v = "lan"; }
    sel.value = v;
    if (v === "sim" && !c.transport) {            // no saved choice: a COM-only radio prefers its Enhanced (CAT) port
      const p = radios.find((r) => r.id === id);
      if (p && p.has_network === false) {
        const enh = [...sel.options].find((o) => o.dataset.kind === "serial" && o.dataset.enhanced);
        if (enh) sel.value = enh.value;
      }
    }
    wantRxDev = c.rxDev || null; wantMicDev = c.micDev || null; wantMicInDev = c.micInDev || null;
    updateConnFields();                             // shows the right fields + (serial) loads audio devices
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
  $("disconnectBtn").onclick = () => { stopAllAudio(); $("conn").classList.add("open"); fetch("/api/disconnect", { method: "POST" }); };
  // Enter in any connection field starts the connect; edits persist per-radio
  ["lanHost", "lanUser", "lanPass", "baud"].forEach((id) => {
    const el = $(id);
    if (!el) return;
    el.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); $("connectBtn").click(); } });
    el.addEventListener("change", saveConn);
  });

  // ---- RX audio (Web Audio playback of 16-bit LE mono PCM) ----
  let audioCtx = null, audioGain = null, rxBus = null, playTime = 0, audioOn = false;
  let rsFifo = [], rsPos = 0;                       // continuous-resampler state (input samples + fractional read pos)
  function ensureAudioCtx() {
    if (!audioCtx) {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      audioGain = audioCtx.createGain();
      audioGain.gain.value = (+$("vol").value || 80) / 100;
      audioGain.connect(audioCtx.destination);
      rxBus = audioCtx.createGain();               // pre-volume RX bus: tools (CW, AF) tap here
      rxBus.connect(audioGain);
    }
    if (audioCtx.state === "suspended") audioCtx.resume();
  }
  function startAudio() {                 // LAN / WS RX (jitter-buffered PCM)
    ensureAudioCtx(); playTime = 0; rsFifo = []; rsPos = 0; audioOn = true;
  }
  // minimal audio API for overlay tools (e.g. the CW decoder/coder in cwtool.js)
  // on(): is RX audio actually running? (the 🔊 RX button is active for both serial + LAN paths).
  // Tools use this for status instead of guessing from the instantaneous audio level.
  window.RadioAudio = { ensure: ensureAudioCtx, ctx: () => audioCtx, bus: () => rxBus, state: () => state,
    on: () => !!($("audioBtn") && $("audioBtn").classList.contains("active")) };
  // control channel for overlay tools (CW TX): send a WS command + read live state
  window.RadioControl = { send: (o) => send(o), state: () => state };
  function playAudio(buf) {
    if (!audioOn || !audioCtx) return;
    const rate = new DataView(buf).getUint16(2, true) || 16000;
    const pcm = new Int16Array(buf, 4);            // header is 4 bytes
    const n = pcm.length; if (!n) return;
    for (let i = 0; i < n; i++) rsFifo.push(pcm[i] / 32768);   // queue this chunk's samples
    // Resample source-rate -> context-rate with a fractional read position that carries
    // ACROSS chunks, so consecutive scheduled buffers join seamlessly (no per-chunk
    // resampler edge clicks, which were the main source of the artifacting).
    const ctxRate = audioCtx.sampleRate, ratio = rate / ctxRate;
    const out = [];
    while (rsPos + 1 < rsFifo.length) {
      const i0 = rsPos | 0, f = rsPos - i0;
      out.push(rsFifo[i0] * (1 - f) + rsFifo[i0 + 1] * f);
      rsPos += ratio;
    }
    const drop = rsPos | 0;
    if (drop > 0) { rsFifo.splice(0, drop); rsPos -= drop; }   // keep only the fractional remainder
    if (!out.length) return;
    const ab = audioCtx.createBuffer(1, out.length, ctxRate);
    ab.getChannelData(0).set(out);
    const src = audioCtx.createBufferSource(); src.buffer = ab; src.connect(rxBus);
    const now = audioCtx.currentTime;
    if (playTime < now + 0.06) playTime = now + 0.18;        // prime / rebuild the jitter buffer on underrun
    else if (playTime > now + 0.5) playTime = now + 0.2;     // bound runaway latency without a hard gap
    src.start(playTime); playTime += ab.duration;
  }

  // ---- USB audio (serial/COM radios) ----------------------------------------
  // Over a COM/CAT connection the control link carries no audio — the radio's
  // audio is a separate USB sound device. So pick the radio's USB-CODEC input to
  // hear RX, and an output device to feed your mic to for TX. Needs a secure
  // context (HTTPS or localhost), like any getUserMedia.
  function currentTransportKind() {
    const sel = $("transport"), opt = sel.options[sel.selectedIndex];
    if (!opt) return "sim";
    return opt.dataset.kind === "serial" ? "serial" : opt.value;
  }
  function fillDevs(sel, list, kind, withDefault) {
    if (!sel) return;
    const prev = sel.value; sel.innerHTML = "";
    if (withDefault) {                             // leading "system default" entry (value "" -> getUserMedia default).
      const o = document.createElement("option");  // Mic In uses this so it never auto-picks the radio's RX CODEC.
      o.value = ""; o.textContent = "Default " + kind.toLowerCase(); sel.appendChild(o);
    }
    if (!list.length && !withDefault) {
      const o = document.createElement("option"); o.value = ""; o.textContent = "(no " + kind.toLowerCase() + " devices)";
      sel.appendChild(o); return;
    }
    list.forEach((d, i) => {
      const o = document.createElement("option");
      o.value = d.deviceId;
      o.textContent = d.label || (kind + " " + (i + 1));
      sel.appendChild(o);
    });
    if (prev) sel.value = prev;
  }
  // Radio RX / Radio TX are sound cards on the HOST (the PC with the radio), so on a serial/USB
  // radio they're enumerated SERVER-side — a remote browser can't see the host's devices. Mic In
  // stays the client's microphone. fillHostDevs defaults to the radio's USB CODEC when unset.
  let hostAudioAvail = true;
  function fillHostDevs(sel, list, kind) {
    if (!sel) return;
    const prev = sel.value; sel.innerHTML = "";
    if (!list.length) {
      const o = document.createElement("option"); o.value = "";
      o.textContent = hostAudioAvail ? "(no " + kind + ")" : "install sounddevice on host";
      sel.appendChild(o); return;
    }
    list.forEach((d) => { const o = document.createElement("option"); o.value = String(d.id); o.textContent = d.name; sel.appendChild(o); });
    if (prev) sel.value = prev;
    else { const codec = [...sel.options].find((o) => /codec/i.test(o.textContent)); if (codec) sel.value = codec.value; }
  }
  async function loadHostDevices() {
    try {
      const j = await (await fetch("/api/audio_devices")).json();
      hostAudioAvail = !!j.available;
      fillHostDevs($("rxDev"), j.inputs || [], "host input");
      fillHostDevs($("micDev"), j.outputs || [], "host output");
    } catch (_) { hostAudioAvail = false; fillHostDevs($("rxDev"), [], "host input"); fillHostDevs($("micDev"), [], "host output"); }
  }
  async function loadAudioDevices() {
    if (currentTransportKind() === "serial") await loadHostDevices();    // Radio RX/TX = host sound cards
    if (navigator.mediaDevices && navigator.mediaDevices.enumerateDevices) {
      let devs = [];
      try { devs = await navigator.mediaDevices.enumerateDevices(); } catch (_) { devs = []; }
      fillDevs($("micInDev"), devs.filter((d) => d.kind === "audioinput"), "Mic", true);   // Mic In = this client's mic
    }
    applyWantedDevs();                              // restore the remembered Radio RX / Radio TX / Mic In for this radio
  }
  function applyWantedDevs() {
    const r = $("rxDev"), m = $("micDev"), mi = $("micInDev");
    if (r && wantRxDev && [...r.options].some((o) => o.value === wantRxDev)) r.value = wantRxDev;
    if (m && wantMicDev && [...m.options].some((o) => o.value === wantMicDev)) m.value = wantMicDev;
    if (mi && wantMicInDev && [...mi.options].some((o) => o.value === wantMicInDev)) mi.value = wantMicInDev;
  }
  if (navigator.mediaDevices && navigator.mediaDevices.addEventListener) {
    navigator.mediaDevices.addEventListener("devicechange", loadAudioDevices);
  }

  // Clearer message for the failure modes the Mic In deviceId constraint can raise.
  function micErr(e) {
    const n = e && e.name;
    if (n === "OverconstrainedError" || n === "NotFoundError")
      return "The selected Mic In device isn't available — pick another under Mic In.\n(" + e + ")";
    if (n === "NotReadableError")
      return "The microphone is in use by another app and can't be opened.\n(" + e + ")";
    return "Microphone access denied: " + e;
  }
  // ---- audio (AF) spectrum from RX, for radios with no CAT band scope (FT-991A) ----
  // FFT the RX audio and feed the existing spectrum + waterfall. Audio Hz is mapped
  // to RF by the mode's sideband (USB: dial+af, LSB: dial-af) so the labels, tuned
  // marker and band plan all read real frequencies — a mini panadapter of the passband.
  let afTimer = 0, afAnalyser = null;
  const AF_MAX = 3600;                          // Hz of the RX audio passband to show (SSB/CW/data occupied width)
  const AF_RANGE_DB = 30;                       // dB above the LOCAL noise floor mapped to full brightness
  const AF_MARGIN_DB = 3;                       // headroom that keeps the flattened noise floor dark
  function afEligible() { return !!(currentRadio && currentRadio.has_scope === false); }
  function startAfScope(srcNode) {
    stopAfScope();
    ensureAudioCtx();
    const sr = audioCtx.sampleRate;
    afAnalyser = audioCtx.createAnalyser();
    // Size the FFT so the passband spans ~1 bin per output pixel — maximum useful detail without
    // over/under-sampling (fftSize ~= W*sr/AF_MAX; e.g. 8192 @ 48 kHz over a ~700 px scope).
    const W = (scope && scope.W) || 700;
    const fft = 1 << Math.round(Math.log2(Math.max(2048, W * sr / AF_MAX)));
    afAnalyser.fftSize = Math.max(4096, Math.min(16384, fft));
    afAnalyser.smoothingTimeConstant = 0;        // no temporal smoothing — crisp, like WSJT
    try { srcNode.connect(afAnalyser); } catch (_) { afAnalyser = null; return; }
    const bins = afAnalyser.frequencyBinCount, buf = new Float32Array(bins);
    const nyq = sr / 2;
    const n = Math.max(64, Math.min(bins, Math.round(AF_MAX / nyq * bins)));   // bins inside the passband
    const data = new Uint8Array(n), b = new Float32Array(n), bl = new Float32Array(n);
    const binHz = sr / afAnalyser.fftSize;
    const H = Math.max(6, Math.round(250 / binHz));   // baseline half-window (~250 Hz of bins)
    const ns = $("noScope"); if (ns) ns.hidden = true;
    const badge = $("afBadge"); if (badge) badge.hidden = false;
    afTimer = setInterval(() => {
      afAnalyser.getFloatFrequencyData(buf);     // raw dB (full dynamic range), not the lossy 0-255 byte path
      const mode = state.mode_name || "USB";
      const lsb = ["LSB", "CW-LSB", "RTTY-LSB", "DATA-LSB", "CW-R", "RTTY", "DATA-L"].includes(mode);
      for (let i = 0; i < n; i++) { const d = buf[lsb ? (n - 1 - i) : i]; b[i] = d > -200 ? d : -160; }
      // Per-frequency noise baseline via a wide boxcar mean (O(n) sliding window). Subtracting it
      // flattens the radio's shaped passband so the floor goes dark and only energy ABOVE the local
      // noise shows — the clean WSJT look (vs a single global floor that leaves the hump bright).
      let sum = 0, cnt = 0;
      for (let j = 0; j <= H && j < n; j++) { sum += b[j]; cnt++; }
      for (let i = 0; i < n; i++) {
        bl[i] = sum / cnt;
        const rem = i - H; if (rem >= 0) { sum -= b[rem]; cnt--; }
        const add = i + 1 + H; if (add < n) { sum += b[add]; cnt++; }
      }
      const gain = 160 / AF_RANGE_DB;
      for (let i = 0; i < n; i++) {
        const r = (b[i] - bl[i] - AF_MARGIN_DB) * gain;        // dB above the local noise floor -> 0..160
        data[i] = r <= 0 ? 0 : r >= 160 ? 160 : r | 0;
      }
      const dial = state.freq || 0;
      const lower = lsb ? dial - AF_MAX : dial;
      const upper = lsb ? dial : dial + AF_MAX;
      // tuned:0 -> no RF marker (the dial sits at an edge here); labels read lower/center/upper
      scope.pushSweep({ mode: 0, center: (lower + upper) / 2, span: AF_MAX, lower, upper, tuned: 0, filterBw: 0 }, data);
      updateScopeLabels(scope.meta);
    }, 40);                                       // ~25 fps
  }
  function stopAfScope() {
    if (afTimer) { clearInterval(afTimer); afTimer = 0; }
    if (afAnalyser) { try { afAnalyser.disconnect(); } catch (_) {} afAnalyser = null; }
    const badge = $("afBadge"); if (badge) badge.hidden = true;
    if (afEligible()) { const ns = $("noScope"); if (ns) ns.hidden = false; }
  }
  // stop every audio path + reset the buttons (on radio change / disconnect)
  function stopAllAudio() {
    audioOn = false;
    if (currentTransportKind() === "serial") { send({ action: "host_rx", on: false }); send({ action: "host_tx", on: false }); }
    stopMic(); stopAfScope();
    $("audioBtn").classList.remove("active");
    $("micBtn").classList.remove("on");
  }

  // re-route live if a picker changes while running. Sync the want* cache first so the restart's
  // applyWantedDevs() keeps the NEW pick. Radio RX/TX re-point the HOST capture/playback over WS.
  if ($("rxDev")) $("rxDev").addEventListener("change", () => {
    wantRxDev = $("rxDev").value || null; saveConn();
    if (currentTransportKind() === "serial" && $("audioBtn").classList.contains("active") && $("rxDev").value)
      send({ action: "host_rx", on: true, device: +$("rxDev").value });
  });
  if ($("micDev")) $("micDev").addEventListener("change", () => {
    wantMicDev = $("micDev").value || null; saveConn();
    if (currentTransportKind() === "serial" && $("micBtn").classList.contains("on") && $("micDev").value)
      send({ action: "host_tx", on: true, device: +$("micDev").value });
  });
  if ($("micInDev")) $("micInDev").addEventListener("change", () => {
    wantMicInDev = $("micInDev").value || null; saveConn();
    if (micOn) { stopMic(); startMic(); }                         // re-open the client mic on the new input
  });

  // ---- TX mic (capture -> 16 kHz mono s16le -> server -> radio) ----
  // NOTE: this only streams audio to the radio's modulator; the radio transmits
  // ONLY when PTT is engaged AND its MOD Input is set to LAN.
  let micStream = null, micSrc = null, micProc = null, micSink = null, micOn = false;
  async function startMic() {
    if (!window.isSecureContext || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      alert("Microphone (TX) needs a secure HTTPS connection. You're on " + location.protocol +
        " — browsers only allow the mic over HTTPS (or localhost), not plain HTTP. " +
        "Serve the app over HTTPS (e.g. behind a reverse proxy / TLS tunnel) and open the https:// address. " +
        "RX audio still works over HTTP.");
      return false;
    }
    try {
      const micId = $("micInDev") ? $("micInDev").value : "";
      micStream = await navigator.mediaDevices.getUserMedia(
        { audio: { deviceId: micId ? { exact: micId } : undefined, channelCount: 1, echoCancellation: true, noiseSuppression: true } });
    } catch (e) { alert(micErr(e)); return false; }
    loadAudioDevices();                  // mic labels are available now permission is granted
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
    const serial = currentTransportKind() === "serial";
    if ($("audioBtn").classList.contains("active")) {   // RX off
      if (serial) send({ action: "host_rx", on: false });
      audioOn = false; stopAfScope();
      $("audioBtn").classList.remove("active");
    } else {                                            // RX on
      if (serial) {
        const dev = $("rxDev") ? $("rxDev").value : "";
        if (!dev) { alert("Pick the radio's RX sound card on the host computer (Radio RX)."); return; }
        send({ action: "host_rx", on: true, device: +dev });
      }
      startAudio();                                     // play the WS PCM (host capture, or LAN radio)
      if (afEligible()) startAfScope(rxBus);            // FT-991A etc.: AF spectrum tapped off the RX bus
      $("audioBtn").classList.add("active");
    }
  };
  $("micBtn").onclick = async () => {
    const serial = currentTransportKind() === "serial";
    if ($("micBtn").classList.contains("on")) {         // mic off
      if (serial) send({ action: "host_tx", on: false });
      stopMic();
      $("micBtn").classList.remove("on");
    } else {                                            // mic on
      if (serial) {
        const dev = $("micDev") ? $("micDev").value : "";
        if (!dev) { alert("Pick the radio's TX sound card on the host computer (Radio TX)."); return; }
        send({ action: "host_tx", on: true, device: +dev });
      }
      const ok = await startMic();                      // capture the CLIENT mic -> WS -> server -> host TX card
      if (ok) $("micBtn").classList.add("on");
      else if (serial) send({ action: "host_tx", on: false });
    }
  };
  $("vol").oninput = (e) => { if (audioGain) audioGain.gain.value = (+e.target.value) / 100; };

  // ---- remote access setup (Tailscale) ----
  (function () {
    const modal = $("remoteModal"), body = $("remoteBody");
    if (!modal || !body) return;
    const E = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    async function render() {
      body.innerHTML = '<p class="rm-hint">Checking…</p>';
      let s;
      try { s = await (await fetch("/api/remote_status")).json(); }
      catch (_) { body.innerHTML = '<p class="rm-bad">Could not read remote status.</p>'; return; }
      const ts = s.tailscale || {}, local = s.local, port = s.port || 8700;
      let h = '<section class="rm-why"><b>Why HTTPS?</b> Browsers only allow microphone access over a '
        + 'secure (HTTPS) connection. Listening and full radio control work over plain HTTP, but to '
        + '<b>transmit phone/SSB with your mic remotely you need HTTPS</b>. Tailscale provides it '
        + 'automatically and keeps access private to your own devices — no port&#8209;forwarding.</section>';
      h += '<section class="rm-status">';
      if (!ts.installed) {
        h += '<p class="rm-bad">Tailscale isn’t installed on this PC.</p><p>'
          + '<a href="https://tailscale.com/download" target="_blank" rel="noopener">Download Tailscale</a>, '
          + 'install it, sign in, then reopen this dialog.</p>';
      } else if (!ts.running) {
        h += '<p class="rm-bad">Tailscale is installed but not signed in.</p>'
          + '<p>Open the Tailscale app on this PC and sign in, then reopen this dialog.</p>';
      } else if (ts.serve_running && ts.url) {
        h += '<p class="rm-good">✓ Remote access is ON — tailnet&#8209;only, HTTPS.</p>'
          + '<div class="rm-url"><code id="rmUrl">' + E(ts.url) + '</code>'
          + '<button class="ctl-btn" id="rmCopy" type="button">Copy</button></div>'
          + '<p class="rm-hint">Open that address from any device signed into your tailnet.</p>'
          + (local ? '<button class="ctl-btn" id="rmOff" type="button">Turn off remote access</button>' : '');
      } else if (ts.url) {
        h += '<p class="rm-good">Tailscale is ready (<code>' + E(ts.url) + '</code>).</p>'
          + (local ? '<button class="ctl-btn primary" id="rmOn" type="button">Turn on secure remote access</button>'
                   + '<p class="rm-hint">Runs <code>tailscale serve</code> so your address serves HTTPS to the radio.</p>'
                   : '<p class="rm-hint">Open this on the host PC to turn remote access on.</p>');
      }
      h += '</section>';
      h += '<section class="rm-steps"><h3>How it works</h3><ol>'
        + '<li>Install Tailscale on this PC <b>and</b> the device you operate from; sign both into the same account.</li>'
        + '<li>In the Tailscale admin console → <b>DNS</b>, enable <b>MagicDNS</b> and <b>HTTPS Certificates</b>.</li>'
        + '<li>Turn on secure remote access above (or run <code>tailscale serve --bg ' + port + '</code>).</li>'
        + '<li>Open your <code>*.ts.net</code> address from any of your devices — HTTPS, mic and all.</li></ol>'
        + '<p class="rm-hint">Tip: launch the server with <code>--host 127.0.0.1</code> so only Tailscale can reach it.</p></section>';
      h += '<section class="rm-pw"><h3>Password ' + (s.auth_enabled ? '<span class="rm-on">on</span>' : '<span class="rm-off">optional</span>') + '</h3>';
      if (!local) {
        h += '<p class="rm-hint">Change the password on the host PC (open <code>http://localhost:' + port + '</code> there).</p>';
      } else if (s.auth_enabled) {
        h += '<p>A login password is required for everyone reaching the server (including this host).</p>'
          + '<button class="ctl-btn" id="rmPwClear" type="button">Remove password</button>';
      } else {
        h += '<p>Your tailnet already limits access to your own devices. Add a password too if you share your tailnet with others.</p>'
          + '<div class="rm-pwset"><input id="rmPw" type="password" placeholder="new password (min 6 chars)" autocomplete="new-password">'
          + '<button class="ctl-btn" id="rmPwSet" type="button">Set password</button></div>';
      }
      h += '<div class="rm-msg" id="rmMsg"></div></section>';
      body.innerHTML = h;
      const on = (id, fn) => { const e = $(id); if (e) e.onclick = fn; };
      on("rmCopy", () => { const u = $("rmUrl"); if (u && navigator.clipboard) navigator.clipboard.writeText(u.textContent); });
      on("rmOn", () => postJson("/api/tailscale_serve", {}));
      on("rmOff", () => postJson("/api/tailscale_serve_off", {}));
      on("rmPwClear", () => postJson("/api/set_password", { clear: true }));
      on("rmPwSet", () => postJson("/api/set_password",
        { password: ($("rmPw") || {}).value || "", allowed_hosts: ts.magicdns ? [ts.magicdns] : [] }));
    }
    async function postJson(url, obj) {
      const msg = $("rmMsg");
      try {
        const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(obj) });
        const j = await r.json().catch(() => ({}));
        if (j && j.ok === false && j.error) { if (msg) msg.textContent = j.error; return; }
      } catch (_) { if (msg) msg.textContent = "Request failed."; return; }
      render();
    }
    if ($("remoteBtn")) $("remoteBtn").onclick = () => { modal.hidden = false; render(); };
    if ($("remoteClose")) $("remoteClose").onclick = () => { modal.hidden = true; };
    modal.addEventListener("click", (e) => { if (e.target === modal) modal.hidden = true; });
  })();

  // ---- update check (latest GitHub release) ----
  (function () {
    const link = $("verUpdate");
    if (!link) return;
    function check(retry) {
      fetch("/api/version").then((r) => r.json()).then((v) => {
        if (v.update_available && v.url) {
          link.href = v.url;
          link.textContent = "↑ " + (v.latest || "update");
          link.title = "A newer release (" + v.latest + ") is available — click for the release notes";
          link.hidden = false;
        } else if (!v.latest && retry) {
          setTimeout(() => check(false), 3500);   // backend hadn't finished its GitHub check yet
        }
      }).catch(() => {});
    }
    check(true);
  })();

  // ---- boot ----
  requestAnimationFrame(() => scope.resize());          // re-measure once the console layout settles
  window.addEventListener("load", () => scope.resize());
  setupFmControls();                                     // populate + wire the FM Tone/DCS selects once
  setupExtControls();                                    // wire the WIDTH/CONTOUR/APF/CW sliders once
  connectWS();
  loadRadios().then(() => {
    if (CONNS.last && radios.some(p => p.id === CONNS.last)) $("radioSel").value = CONNS.last;   // last-used radio
    renderRadio(selectedRadio());
    return loadPorts();                            // serial COM options now exist
  }).then((status) => {
    if (status && status.connected) {              // already connected — reflect its radio
      if (status.radio && radios.some(p => p.id === status.radio)) { $("radioSel").value = status.radio; renderRadio(selectedRadio()); }
      applyConnForRadio($("radioSel").value);
      $("conn").classList.remove("open");          // collapse settings (mobile)
      return;
    }
    applyConnForRadio($("radioSel").value);        // restore this radio's last connection type + settings
    const body = buildConnectBody();               // and connect to it (silent on failure)
    if (body.transport === "lan" && !body.host) doConnect({ transport: "sim", radio: $("radioSel").value }, true);
    else doConnect(body, true);
  });
})();

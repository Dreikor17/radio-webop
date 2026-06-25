/* Icom WebOp — UI controller */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);

  const scope = new Scope($("spectrum"), $("waterfall"), $("overlay"), $("scopeWrap"));

  let ws = null, state = {}, step = 25000;

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
    $("freq").textContent = formatFreq(s.freq);
    $("modeReadout").textContent = s.mode_name || "";
    $("filtReadout").textContent = s.filter_name || "";
    $("vfoLetter").textContent = "A";

    const pct = Math.min(100, (s.smeter || 0) / 240 * 100);
    $("meterFill").style.width = pct + "%";
    $("meterVal").textContent = s.smeter_s || "S0";

    // connection
    const on = !!s.connected;
    $("led").classList.toggle("on", on);
    $("connlabel").textContent = on ? (s.transport || "Connected") : "Disconnected";
    $("audioAvail").textContent = s.audio ? "• available" : "• LAN only";

    // PTT / TX tag
    $("txTag").textContent = s.ptt ? "TX" : "RX";
    $("txTag").classList.toggle("on", !!s.ptt);
    $("pttBtn").classList.toggle("on", !!s.ptt);

    // span + scope mode
    $("spanVal").textContent = s.span_label || "";
    $("btnCenter").classList.toggle("active", !!s.scope_center);
    $("btnFixed").classList.toggle("active", !s.scope_center);

    // active buttons
    setActive(".band", b => b.dataset.band === String(bandOf(s.freq)));
    setActive(".mode", b => b.dataset.mode === s.mode_name);
    setActive(".filt", b => b.dataset.filter === String(s.filter));

    // level sliders (don't fight an active drag) + value readouts
    for (const t of ["af", "rf", "sql", "rfpwr"]) {
      const el = $(t);
      if (el && document.activeElement !== el && s[t] != null) el.value = s[t];
      if (s[t] != null) $(t + "Val").textContent = fmtLevel(t, s[t]);
    }

    // keep overlay tracking between sweeps
    scope.setOpMode(s.mode_name);
    scope.meta.tuned = s.freq;
    scope.meta.filterBw = s.filter_bw;
    scope.drawOverlay();
  }

  function bandOf(hz) {
    if (hz >= 1_240_000_000) return 1200;
    if (hz >= 430_000_000) return 430;
    return 144;
  }
  function setActive(sel, pred) {
    document.querySelectorAll(sel).forEach(b => b.classList.toggle("active", pred(b)));
  }

  // ---- button delegation ----
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-act]");
    if (!b) return;
    const act = b.dataset.act;
    if (act === "band") send({ action: "band", band: b.dataset.band });
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
  for (const t of ["af", "rf", "sql", "rfpwr"]) {
    $(t).addEventListener("input", e => {
      send({ action: "set_level", target: t, value: +e.target.value });
      $(t + "Val").textContent = fmtLevel(t, +e.target.value);
    });
  }

  // PTT momentary
  const ptt = $("pttBtn");
  const pttDown = () => send({ action: "ptt", tx: true });
  const pttUp = () => send({ action: "ptt", tx: false });
  ptt.addEventListener("mousedown", pttDown);
  ptt.addEventListener("mouseup", pttUp);
  ptt.addEventListener("mouseleave", () => { if (state.ptt) pttUp(); });

  // ---- tuning: click-to-tune + wheel on the scope ----
  const wrap = $("scopeWrap");
  wrap.addEventListener("click", (e) => {
    const r = wrap.getBoundingClientRect();
    const x = e.clientX - r.left;
    const m = scope.meta; let lo, hi;
    if (m.mode === 1 && m.lower && m.upper) { lo = m.lower; hi = m.upper; }
    else { lo = m.center - m.span / 2; hi = m.center + m.span / 2; }
    const freq = lo + (x / scope.W) * (hi - lo);
    send({ action: "set_freq", hz: Math.round(freq / step) * step });  // snap to step
  });
  wrap.addEventListener("wheel", (e) => {
    e.preventDefault();
    send({ action: "tune", delta: (e.deltaY < 0 ? 1 : -1) * step });
  }, { passive: false });

  // ---- main dial drag ----
  const dial = $("dial"), knob = $("dialKnob");
  let dragging = false, lastAngle = 0, accum = 0, rot = 0;
  function angleAt(e, rect) {
    const cx = rect.left + rect.width / 2, cy = rect.top + rect.height / 2;
    return Math.atan2(e.clientY - cy, e.clientX - cx) * 180 / Math.PI;
  }
  dial.addEventListener("pointerdown", (e) => {
    dragging = true; dial.setPointerCapture(e.pointerId);
    lastAngle = angleAt(e, dial.getBoundingClientRect());
  });
  dial.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    const a = angleAt(e, dial.getBoundingClientRect());
    let d = a - lastAngle;
    if (d > 180) d -= 360; if (d < -180) d += 360;
    lastAngle = a; accum += d; rot += d;
    knob.style.transform = `rotate(${rot}deg)`;
    const PER = 6; // degrees per step
    while (Math.abs(accum) >= PER) {
      send({ action: "tune", delta: (accum > 0 ? 1 : -1) * step });
      accum -= (accum > 0 ? 1 : -1) * PER;
    }
  });
  const endDrag = () => { dragging = false; };
  dial.addEventListener("pointerup", endDrag);
  dial.addEventListener("pointercancel", endDrag);
  // wheel on dial too
  dial.addEventListener("wheel", (e) => {
    e.preventDefault();
    send({ action: "tune", delta: (e.deltaY < 0 ? 1 : -1) * step });
  }, { passive: false });

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

  const CONN_KEY = "icomwebop.conn";
  function buildConnectBody() {
    const sel = $("transport"), opt = sel.options[sel.selectedIndex];
    if (!opt || opt.value === "sim") return { transport: "sim" };
    if (opt.value === "lan") return {
      transport: "lan", host: $("lanHost").value.trim(), port: 50001,
      user: $("lanUser").value, password: $("lanPass").value,
    };
    return { transport: "serial", port: opt.value, baud: +$("baud").value || 115200 };
  }
  function saveConn() {
    try {
      localStorage.setItem(CONN_KEY, JSON.stringify({
        transport: $("transport").value, host: $("lanHost").value,
        user: $("lanUser").value, pass: $("lanPass").value, baud: $("baud").value,
      }));
    } catch (_) {}
  }
  function restoreConnFields() {
    let s = null; try { s = JSON.parse(localStorage.getItem(CONN_KEY) || "null"); } catch (_) {}
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
      if (!j.ok && !silent) alert("Connect failed: " + (j.error || "unknown"));
    } catch (e) { if (!silent) alert("Connect error: " + e); }
    finally { btn.textContent = "Connect"; btn.disabled = false; }
  }
  $("connectBtn").onclick = () => {
    const body = buildConnectBody();
    if (body.transport === "lan" && !body.host) { alert("Enter the radio's IP address."); return; }
    saveConn();
    doConnect(body);
  };
  $("disconnectBtn").onclick = () => fetch("/api/disconnect", { method: "POST" });

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
      if (!micOn || !ws || ws.readyState !== 1) return;
      const input = e.inputBuffer.getChannelData(0);
      const outLen = Math.floor(input.length / ratio);
      const out = new Int16Array(outLen);
      for (let i = 0; i < outLen; i++) {
        const s = input[(i * ratio) | 0];
        out[i] = s < -1 ? -32768 : s > 1 ? 32767 : (s * 32767) | 0;
      }
      ws.send(out.buffer);
    };
    micSrc.connect(micProc); micProc.connect(micSink); micSink.connect(audioCtx.destination);
    micOn = true; return true;
  }
  function stopMic() {
    micOn = false;
    for (const n of [micProc, micSrc, micSink]) { try { n && n.disconnect(); } catch (_) {} }
    if (micStream) micStream.getTracks().forEach(t => t.stop());
    micProc = micSrc = micSink = micStream = null;
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
  step = +$("step").value;
  const saved = restoreConnFields();
  updateConnFields();
  connectWS();
  loadPorts().then((status) => {
    const sel = $("transport");
    if (saved && [...sel.options].some(o => o.value === saved.transport)) sel.value = saved.transport;
    updateConnFields();
    if (status && status.connected) return;       // server already connected — leave it
    const body = buildConnectBody();               // else connect to the remembered method
    // silent: a failed auto-connect (e.g. remembered radio is off) shouldn't alert
    if (body.transport === "lan" && !body.host) doConnect({ transport: "sim" }, true);
    else doConnect(body, true);
  });
})();

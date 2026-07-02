/* CW decoder / coder — an overlay tool that pops up over the waterfall.
 *
 * Decoder: a NEURAL decoder (DeepCW). It taps the pre-volume RX bus
 * (RadioAudio.bus()), resamples to 3200 Hz, and re-runs the ONNX model in
 * cw-worker.js over a growing window, committing text at pauses. Far more
 * robust than a timing decoder under QSB/QRM/weak signals.
 *
 * Coder: types -> Morse -> a soft-keyed sidetone (NO transmit; it never keys the
 * radio). The sidetone feeds the same rxBus, so playing it self-tests the decoder.
 *
 * SPDX-License-Identifier: AGPL-3.0-only
 * The decode path links the AGPL-3.0 DeepCW model (github.com/e04/deepcw-engine),
 * so the CW feature is AGPL-3.0. See cw-worker.js. onnxruntime-web is MIT.
 */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const RA = () => window.RadioAudio || null;
  const RC = () => window.RadioControl || null;   // WS command channel + live radio state
  const CW_WORKER_V = "2";                       // bump when cw-worker.js changes (cache-bust)

  const MORSE = {
    A: ".-", B: "-...", C: "-.-.", D: "-..", E: ".", F: "..-.", G: "--.", H: "....",
    I: "..", J: ".---", K: "-.-", L: ".-..", M: "--", N: "-.", O: "---", P: ".--.",
    Q: "--.-", R: ".-.", S: "...", T: "-", U: "..-", V: "...-", W: ".--", X: "-..-",
    Y: "-.--", Z: "--..",
    "0": "-----", "1": ".----", "2": "..---", "3": "...--", "4": "....-", "5": ".....",
    "6": "-....", "7": "--...", "8": "---..", "9": "----.",
    ".": ".-.-.-", ",": "--..--", "?": "..--..", "/": "-..-.", "=": "-...-", "+": ".-.-.",
    "-": "-....-", ":": "---...", "(": "-.--.", ")": "-.--.-", '"': ".-..-.", "@": ".--.-.",
    "'": ".----.",
  };

  const panel = $("cwTool"), btn = $("cwToolBtn"), head = $("cwHead");
  if (!panel || !btn) return;
  let open = false;

  // ---- show / hide ----
  function setOpen(v) {
    open = v;
    panel.hidden = !v;
    btn.classList.toggle("active", v);
    if (v) startDecoder(); else stopDecoder();
  }
  btn.addEventListener("click", () => setOpen(!open));
  $("cwClose").addEventListener("click", () => setOpen(false));

  // ---- drag the panel by its header ----
  (function () {
    let down = false, sx = 0, sy = 0, baseL = 0, baseT = 0;
    const prect = () => (panel.offsetParent || panel.parentElement).getBoundingClientRect();
    head.addEventListener("pointerdown", (e) => {
      if (e.target.closest(".tp-close")) return;
      const p = prect(), r = panel.getBoundingClientRect();
      baseL = r.left - p.left; baseT = r.top - p.top;
      panel.style.left = baseL + "px"; panel.style.top = baseT + "px";
      panel.style.right = "auto"; panel.style.bottom = "auto";
      sx = e.clientX; sy = e.clientY; down = true;
      try { head.setPointerCapture(e.pointerId); } catch (_) {}
      e.preventDefault();
    });
    head.addEventListener("pointermove", (e) => {
      if (!down || !(e.buttons & 1)) return;
      const p = prect();
      let x = baseL + (e.clientX - sx), y = baseT + (e.clientY - sy);
      x = Math.max(0, Math.min(p.width - panel.offsetWidth, x));
      y = Math.max(0, Math.min(p.height - 30, y));
      panel.style.left = x + "px"; panel.style.top = y + "px";
    });
    const end = () => { down = false; };
    head.addEventListener("pointerup", end);
    head.addEventListener("pointercancel", end);
    head.addEventListener("lostpointercapture", end);
  })();

  // ---- resize the panel by dragging any edge / corner ----
  (function () {
    const MINW = 240, MINH = 160;
    const prect = () => (panel.offsetParent || panel.parentElement).getBoundingClientRect();
    for (const dir of ["n", "s", "e", "w", "ne", "nw", "se", "sw"]) {
      const h = document.createElement("div");
      h.className = "tp-resize tp-resize-" + dir;
      h.dataset.dir = dir;
      panel.appendChild(h);                          // absolute -> stays out of the flex flow
    }
    let dir = null, sx = 0, sy = 0, sw = 0, sh = 0, sl = 0, st = 0;
    panel.addEventListener("pointerdown", (e) => {
      const t = e.target.closest(".tp-resize"); if (!t) return;
      dir = t.dataset.dir;
      const p = prect(), r = panel.getBoundingClientRect();
      sx = e.clientX; sy = e.clientY; sw = r.width; sh = r.height;
      sl = r.left - p.left; st = r.top - p.top;
      panel.style.left = sl + "px"; panel.style.top = st + "px";   // pin to left/top so resize is predictable
      panel.style.right = "auto"; panel.style.bottom = "auto";
      panel.style.width = sw + "px"; panel.style.height = sh + "px";
      try { t.setPointerCapture(e.pointerId); } catch (_) {}
      e.preventDefault(); e.stopPropagation();
    });
    panel.addEventListener("pointermove", (e) => {
      if (!dir || !(e.buttons & 1)) return;
      const p = prect();
      const dx = e.clientX - sx, dy = e.clientY - sy;
      let w = sw, h = sh, l = sl, t = st;
      if (dir.includes("e")) w = sw + dx;
      if (dir.includes("s")) h = sh + dy;
      if (dir.includes("w")) { w = sw - dx; l = sl + dx; }
      if (dir.includes("n")) { h = sh - dy; t = st + dy; }
      if (w < MINW) { if (dir.includes("w")) l -= (MINW - w); w = MINW; }   // keep the anchored edge fixed
      if (h < MINH) { if (dir.includes("n")) t -= (MINH - h); h = MINH; }
      w = Math.min(w, p.width); h = Math.min(h, p.height);                  // never exceed the scope pane
      l = Math.max(0, Math.min(l, p.width - w));
      t = Math.max(0, Math.min(t, p.height - h));
      panel.style.width = w + "px"; panel.style.height = h + "px";
      panel.style.left = l + "px"; panel.style.top = t + "px";
    });
    const end = () => { dir = null; };
    panel.addEventListener("pointerup", end);
    panel.addEventListener("pointercancel", end);
    panel.addEventListener("lostpointercapture", end);
  })();

  // ---- neural decoder: live streaming over a growing buffer, committing at word gaps ----
  // Ported from e04/web-deep-cw-decoder (src/hooks/useStreamingDecode.ts): keep a buffer of
  // UNcommitted audio; each tick decode it and show the running decode as a live preview; when a
  // settled word gap is found (a word-space span at least TAIL_GUARD before the end), commit the
  // text up to that gap and slide the buffer PAST it. Because the cut lands in a word gap and the
  // in-progress word's audio is retained, the live stream decodes with no mid-word seam.
  const SR = 3200;                               // model sample rate
  const INFER_MS = 1000;                         // re-decode cadence
  const MIN_PAD_S = 5;                           // pad short analyses to the model's min length
  const MIN_PENDING = 2 * SR;                    // need this much audio before decoding
  const MIN_CONFIRMED = 2 * SR;                  // earliest sample we'll commit up to
  const TAIL_GUARD = (1.25 * SR) | 0;            // never commit text inside the freshest tail (may still change)
  const MAX_SEGMENT = 18 * SR;                   // force a commit before the model's input grows too long
  const SILENCE = 1.5e-3;                        // |sample| below this across the buffer = no RX signal

  let worker = null, workerReady = false, HOP = 48;      // HOP (frame -> sample) comes from the worker
  let capNode = null, ticker = 0, workletReady = false;  // realtime capture worklet
  let buf = new Float32Array(21 * SR), pendingLen = 0;   // UNcommitted audio @ 3200 Hz (room above MAX_SEGMENT)
  let pendingText = "", lastPeak = 0;
  let messages = [], rxOpen = false;                     // texting chat log: {dir:"rx"|"tx", text, t}
  let inflight = false, analysisLen = 0, reqId = 0;
  const out = $("cwOut"), hint = $("cwHint"), statusEl = $("cwStatus");

  // ---- chat log helpers: decoded RX = grey bubbles left, transmitted = blue bubbles right ----
  function fmtT() { const d = new Date(); return String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0"); }
  function trimMsgs() { if (messages.length > 80) messages.splice(0, messages.length - 80); }
  function openRx() { if (!rxOpen) { messages.push({ dir: "rx", text: "", t: fmtT() }); rxOpen = true; trimMsgs(); } }
  function closeRx() {                                    // finalize the current RX bubble (on silence / before a TX)
    if (!rxOpen) return;
    const m = messages[messages.length - 1];
    if (m && m.dir === "rx" && !m.text) messages.pop();  // drop an RX bubble that never confirmed any text
    rxOpen = false;
  }
  function addRxText(seg) {                               // append a committed segment to the open RX bubble
    if (!seg) return;
    openRx();
    const m = messages[messages.length - 1];
    m.text += (m.text ? " " : "") + seg;
    if (m.text.length > 500) m.text = m.text.slice(-480);
  }
  function addTx(text) {                                  // a transmitted message = its own blue bubble
    const t = String(text || "").toUpperCase().trim();
    if (!t) return;
    closeRx();
    messages.push({ dir: "tx", text: t, t: fmtT() });
    trimMsgs(); render();
  }

  function setStatus(s) { if (statusEl) statusEl.textContent = s; }

  function ensureWorker() {
    if (worker) return;
    setStatus("loading model…");
    worker = new Worker("/static/cw-worker.js?v=" + CW_WORKER_V);
    worker.onmessage = (e) => {
      const m = e.data;
      if (m.type === "ready") { workerReady = true; if (m.hop) HOP = m.hop; setStatus("ready"); render(); }
      else if (m.type === "error") { setStatus("model failed"); }
      else if (m.type === "result") onResult(m);
    };
    worker.onerror = () => setStatus("model error");
  }

  async function startDecoder() {
    ensureWorker();
    const ra = RA(); if (!ra) return;
    ra.ensure();
    const ctx = ra.ctx(), bus = ra.bus();
    if (!ctx || !bus) return;
    stopCapture();
    pendingLen = 0; pendingText = ""; lastPeak = 0; messages = []; rxOpen = false;
    inflight = false; analysisLen = 0;
    render();
    try {
      if (!workletReady) { await ctx.audioWorklet.addModule("/static/cw-capture-worklet.js?v=" + CW_WORKER_V); workletReady = true; }
      if (!open) return;                                  // panel was closed while the module loaded
      capNode = new AudioWorkletNode(ctx, "cw-capture", { processorOptions: { targetSR: SR } });
      capNode.port.onmessage = (e) => appendSamples(e.data);
      bus.connect(capNode); capNode.connect(ctx.destination);   // worklet emits no output -> silent
    } catch (err) {
      setStatus("audio capture unavailable");
      return;
    }
    if (!ticker) ticker = setInterval(tick, INFER_MS);
  }
  function stopDecoder() { stopCapture(); if (ticker) { clearInterval(ticker); ticker = 0; } }
  function stopCapture() {
    if (capNode) { try { capNode.disconnect(); capNode.port.onmessage = null; } catch (_) {} capNode = null; }
  }

  // 3200 Hz chunks from the realtime capture worklet -> append to the pending (uncommitted) buffer
  function appendSamples(arr) {
    for (let i = 0; i < arr.length; i++) {
      if (pendingLen >= buf.length) break;       // bounded; tick force-commits well before this
      buf[pendingLen++] = arr[i];
    }
  }

  function tick() {
    if (!workerReady) { render(); return; }
    if (inflight) return;
    if (pendingLen < MIN_PENDING) { render(); return; }
    let pk = 0;                                   // RX-present gate: peak across the whole pending buffer
    for (let i = 0; i < pendingLen; i++) { const a = buf[i] < 0 ? -buf[i] : buf[i]; if (a > pk) pk = a; }
    lastPeak = pk;
    if (pk < SILENCE) {                           // no signal: keep a short lead, never decode pure noise
      const keep = Math.min(pendingLen, (0.5 * SR) | 0);
      if (pendingLen > keep) { buf.copyWithin(0, pendingLen - keep, pendingLen); pendingLen = keep; }
      pendingText = ""; closeRx(); render(); return;     // RX went quiet -> finalize the current bubble
    }
    openRx();                                     // RX signal present -> ensure a bubble to receive it
    analysisLen = Math.min(pendingLen, MAX_SEGMENT);
    const L = Math.max(analysisLen, MIN_PAD_S * SR);     // pad short analyses with trailing silence
    const a = new Float32Array(L);
    a.set(buf.subarray(0, analysisLen));
    const g = Math.max(0.1, Math.min(100, 0.5 / pk));    // level-normalize (the model has no input AGC)
    if (g !== 1) for (let i = 0; i < analysisLen; i++) a[i] *= g;
    inflight = true;
    worker.postMessage({ type: "decode", id: ++reqId, audio: a }, [a.buffer]);
  }

  // The latest word-space gap whose mid-sample is settled: >= MIN_CONFIRMED in, and (unless we're
  // forcing because the buffer is too long) at least TAIL_GUARD before the analyzed end.
  function findSplit(spans, allowNearEnd) {
    const maxCommit = allowNearEnd ? analysisLen : Math.max(MIN_CONFIRMED, analysisLen - TAIL_GUARD);
    for (let i = spans.length - 1; i >= 0; i--) {
      const sample = Math.round(((spans[i].startFrame + spans[i].endFrame) / 2) * HOP);
      if (sample >= MIN_CONFIRMED && sample <= maxCommit) return { sample, endFrame: spans[i].endFrame };
    }
    return null;
  }
  function trimToFrame(spans, endFrame) {        // text of the characters that end at/before a frame
    let s = "";
    for (let i = 0; i < spans.length; i++) if (spans[i].endFrame <= endFrame) s += spans[i].char;
    return s;
  }
  function norm(s) { return s.replace(/\s+/g, " ").trim(); }

  function onResult(m) {
    inflight = false;
    if (m.err) { return; }
    pendingText = norm(m.text || "");            // running decode of the pending buffer = the live preview
    const ws = m.wordSpaceSpans || [], cs = m.characterSpans || [];
    let split = findSplit(ws, false);
    if (!split && pendingLen >= MAX_SEGMENT) split = findSplit(ws, true);
    const confirmedLen = split ? split.sample : (pendingLen >= MAX_SEGMENT ? analysisLen : 0);
    if (confirmedLen >= MIN_CONFIRMED) {
      const segText = split ? norm(trimToFrame(cs, split.endFrame)) : pendingText;   // forced: whole analysis
      if (segText) addRxText(segText);            // append to the current grey RX bubble
      buf.copyWithin(0, confirmedLen, pendingLen); pendingLen -= confirmedLen;       // slide past committed audio
      pendingText = "";                          // cleared so committed text never appears twice in one tick
    }
    render();
  }

  function render() {
    if (out) {
      let html = "";
      for (let i = 0; i < messages.length; i++) {
        const m = messages[i];
        let inner = esc(m.text);
        if (m.dir === "rx" && rxOpen && i === messages.length - 1 && pendingText)   // live decode tail
          inner += (m.text ? " " : "") + '<span class="cw-live">' + esc(pendingText) + "</span>";
        if (!inner) continue;                     // skip an empty (just-opened) RX bubble
        html += '<div class="cw-msg-wrap ' + m.dir + '"><div class="cw-msg-lbl">' + m.t +
          '</div><div class="cw-msg ' + m.dir + '">' + inner + "</div></div>";
      }
      out.innerHTML = html;
      out.scrollTop = out.scrollHeight;
    }
    if (hint) {
      // Drive the prompt off whether RX audio is actually ON — not the audio level, so it never
      // flashes "turn on RX" mid-decode just because of a momentary gap or a commit.
      const ra = RA(), rxOn = ra && ra.on ? ra.on() : (lastPeak >= SILENCE);
      hint.textContent = !workerReady ? "loading neural model…"
        : (rxOn ? "decoding…" : "turn on 🔊 RX to decode the receiver audio");
    }
  }
  function esc(s) { return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }

  $("cwClear").addEventListener("click", () => { messages = []; rxOpen = false; pendingText = ""; render(); });

  // ---- coder (text -> Morse sidetone; never transmits) ----
  $("cwPlay").addEventListener("click", () => playMorse($("cwSend").value));
  $("cwSend").addEventListener("keydown", (e) => { if (e.key === "Enter") playMorse($("cwSend").value); });
  function playMorse(text) {
    const ra = RA(); if (!ra) return;
    ra.ensure();
    const ctx = ra.ctx(), bus = ra.bus();
    if (!ctx || !bus || !text) return;
    const wpm = Math.max(5, Math.min(40, +$("cwWpmSet").value || 18));
    const dot = 1.2 / wpm;
    const tone = +$("cwToneSet").value || 600;
    const osc = ctx.createOscillator(); osc.type = "sine"; osc.frequency.value = tone;
    const g = ctx.createGain(); g.gain.value = 0.0001;
    osc.connect(g); g.connect(bus);              // -> rxBus -> speakers AND the decoder's tap
    let t = ctx.currentTime + 0.05;
    for (const chRaw of text.toUpperCase()) {
      if (chRaw === " ") { t += dot * 7; continue; }
      const m = MORSE[chRaw]; if (!m) continue;
      for (const el of m) {
        const dur = el === "-" ? dot * 3 : dot;
        g.gain.setValueAtTime(0, t);                        // raised-edge keying to true 0/1
        g.gain.linearRampToValueAtTime(0.3, t + 0.005);
        g.gain.setValueAtTime(0.3, t + dur - 0.005);
        g.gain.linearRampToValueAtTime(0, t + dur);
        t += dur + dot;
      }
      t += dot * 2;
    }
    osc.start(); osc.stop(t + 0.1);
  }

  // ---- transmit (operator-triggered): key the message on the radio as CW ----
  // Same act as pressing PTT — one bounded message, fully operator-controlled. The
  // backend hands the text to the rig's own keyer, which generates clean CW at the
  // WPM and drops back to RX. Press again to stop. Bound by the 120 s failsafe + TOT.
  const txBtn = $("cwTx"), txRow = $("cwTxRow"), txHint = $("cwTxHint");
  function setTxHint(s) {
    if (!txRow || !txHint) return;
    txHint.textContent = s || "";
    txRow.hidden = !s;
  }
  if (txBtn) {
    txBtn.addEventListener("click", () => {
      const rc = RC(); if (!rc) return;
      const st = rc.state() || {};
      if (st.cw_tx) { rc.send({ action: "cw_stop" }); return; }   // already sending -> stop
      if (!st.connected) { setTxHint("connect a radio to transmit"); return; }
      if (!st.cw_tx_ready) { setTxHint("CW key port not found — check the radio's 2nd USB (Standard) COM port"); return; }
      // any CW mode: Icom CW / CW-R, Yaesu CW-USB / CW-LSB
      const m = st.mode_name || "";
      if (!m.startsWith("CW")) { setTxHint("set the radio to a CW mode to transmit"); return; }
      const text = $("cwSend").value;
      if (!text.trim()) { setTxHint("type a message first"); return; }
      const wpm = Math.max(5, Math.min(40, +$("cwWpmSet").value || 18));
      setTxHint("");
      addTx(text);                                  // show our sent message as a blue bubble
      rc.send({ action: "cw_tx", text, wpm });
    });
  }
  // keep the TX button in step with the live radio state (shown only when the
  // connected radio supports CW message TX; reflects the transmitting state)
  function syncTx() {
    if (!txBtn) return;
    const st = (RC() && RC().state()) || {};
    const supported = !!st.has_cw_tx;
    txBtn.hidden = !supported;
    if (!supported) { setTxHint(""); return; }
    const tx = !!st.cw_tx;
    const ready = !!st.cw_tx_ready;
    txBtn.textContent = tx ? "■" : "TX";
    txBtn.classList.toggle("on", tx);
    txBtn.disabled = !tx && (!st.connected || !ready);
    txBtn.title = tx ? "Stop transmitting"
      : !st.connected ? "Connect a radio to transmit"
      : !ready ? "CW key port not found"
      : "Transmit this message as CW on the radio";
    if (tx) setTxHint("on air — sending CW");
    else if (st.connected && !ready) setTxHint("CW key port not found — check the radio's 2nd USB (Standard) COM port");
    else if (txHint && (txHint.textContent === "on air — sending CW" || txHint.textContent.indexOf("CW key port") === 0)) setTxHint("");
  }
  setInterval(syncTx, 400);
  syncTx();

  // diagnostic: window.__cwdiag() -> live decode internals (peak / dominant Hz / raw text)
  window.__cwdiag = function () {
    let domF = 0, domE = -1;
    const N = Math.min(pendingLen, SR);             // last ~1 s of the pending buffer
    if (N > 128) {
      const start = pendingLen - N;
      for (let f = 250; f <= 1500; f += 25) {       // coarse Goertzel scan for the dominant tone
        const coeff = 2 * Math.cos(2 * Math.PI * f / SR);
        let s1 = 0, s2 = 0;
        for (let i = 0; i < N; i++) { const s0 = buf[start + i] + coeff * s1 - s2; s2 = s1; s1 = s0; }
        const e = s1 * s1 + s2 * s2 - coeff * s1 * s2;
        if (e > domE) { domE = e; domF = f; }
      }
    }
    return { peak: +lastPeak.toFixed(4), pendingLen, sec: +(pendingLen / SR).toFixed(2),
             domHz: domF, inBand: domF >= 400 && domF <= 1200,
             pending: pendingText, committed: committed.slice(-48), ready: workerReady };
  };
})();

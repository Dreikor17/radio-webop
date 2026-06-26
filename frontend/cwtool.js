/* CW decoder / coder — an overlay tool that pops up over the waterfall.
   Decoder: taps the pre-volume RX bus (RadioAudio.bus()), tracks the CW tone
   envelope, adapts the threshold + dot length, and turns the on/off timing into
   text. Coder: types -> Morse -> a soft-keyed sidetone (NO transmit; it never
   keys the radio — it just makes sound you can also feed back to the decoder).

   A classic (DSP) decoder, not the deep-learning one — see the note in the app
   about the AGPL ONNX model from e04/deep-cw-decoder. */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const RA = () => window.RadioAudio || null;

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
  const TEXT_OF = {};
  for (const k in MORSE) TEXT_OF[MORSE[k]] = k;

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
    let down = false, ox = 0, oy = 0;
    head.addEventListener("pointerdown", (e) => {
      if (e.target.closest(".tp-close")) return;
      down = true; ox = e.clientX; oy = e.clientY;
      const r = panel.getBoundingClientRect();
      panel.style.left = r.left + "px"; panel.style.top = r.top + "px";
      panel.style.right = "auto"; panel.style.bottom = "auto";
      try { head.setPointerCapture(e.pointerId); } catch (_) {}
      e.preventDefault();
    });
    head.addEventListener("pointermove", (e) => {
      if (!down || !(e.buttons & 1)) return;
      const pane = panel.parentElement.getBoundingClientRect();
      let x = parseFloat(panel.style.left) + (e.clientX - ox);
      let y = parseFloat(panel.style.top) + (e.clientY - oy);
      x = Math.max(pane.left, Math.min(pane.right - panel.offsetWidth, x));
      y = Math.max(pane.top, Math.min(pane.bottom - 40, y));
      panel.style.left = x + "px"; panel.style.top = y + "px";
      ox = e.clientX; oy = e.clientY;
    });
    const end = () => { down = false; };
    head.addEventListener("pointerup", end);
    head.addEventListener("pointercancel", end);
    head.addEventListener("lostpointercapture", end);
  })();

  // ---- decoder ----
  let analyser = null, timer = 0, raf = 0;
  let outText = "", curMorse = "", dotMs = 60, lastFlush = true;
  let lvlPeak = 0, lvlNoise = 0, keyOn = false, edgeT = 0, wordPending = false;
  const out = $("cwOut"), hint = $("cwHint");

  function startDecoder() {
    const ra = RA(); if (!ra) return;
    ra.ensure();
    const ctx = ra.ctx(), bus = ra.bus();
    if (!ctx || !bus) return;
    stopAnalyser();
    analyser = ctx.createAnalyser();
    analyser.fftSize = 1024;                 // ~21 ms window: responsive enough to ~25 wpm
    analyser.smoothingTimeConstant = 0;
    bus.connect(analyser);
    edgeT = performance.now(); keyOn = false; lvlPeak = 0; lvlNoise = 0; wordPending = false;
    const bins = analyser.frequencyBinCount, buf = new Uint8Array(bins);
    timer = setInterval(() => sample(ctx, buf), 5);   // ~200 Hz envelope sampling
    drawLoop();
  }
  function stopDecoder() { stopAnalyser(); }
  function stopAnalyser() {
    if (timer) { clearInterval(timer); timer = 0; }
    if (raf) { cancelAnimationFrame(raf); raf = 0; }
    if (analyser) { try { analyser.disconnect(); } catch (_) {} analyser = null; }
  }

  function toneBin(ctx) {
    const tone = +$("cwToneSet").value || 600;
    return Math.round(tone / (ctx.sampleRate / 2) * analyser.frequencyBinCount);
  }
  function sample(ctx, buf) {
    analyser.getByteFrequencyData(buf);
    const b = toneBin(ctx);
    let mag = 0;
    for (let i = Math.max(0, b - 1); i <= Math.min(buf.length - 1, b + 1); i++) mag = Math.max(mag, buf[i]);
    // adaptive peak (fast attack / slow decay) + noise floor (fast down / slow up)
    lvlPeak += (mag > lvlPeak ? 0.5 : 0.008) * (mag - lvlPeak);
    lvlNoise += (mag < lvlNoise ? 0.5 : 0.004) * (mag - lvlNoise);
    const span = lvlPeak - lvlNoise;
    const now = performance.now();
    if (span < 18) {                         // no real signal: just let any pending char/word flush
      flushIdle(now); return;
    }
    const thHi = lvlNoise + span * 0.55, thLo = lvlNoise + span * 0.40;
    if (!keyOn && mag > thHi) { onEdge(true, now); }
    else if (keyOn && mag < thLo) { onEdge(false, now); }
    else { flushIdle(now); }
  }
  function onEdge(down, now) {
    const dur = now - edgeT; edgeT = now; keyOn = down;
    if (down) {                              // key just went DOWN -> the gap that ended was OFF
      classifyGap(dur);
    } else {                                 // key just went UP -> the mark that ended was ON
      if (dur < dotMs * 2) { curMorse += "."; dotMs += 0.25 * (dur - dotMs); }       // dot
      else { curMorse += "-"; dotMs += 0.12 * (dur / 3 - dotMs); }                   // dash
      dotMs = Math.max(20, Math.min(200, dotMs));
    }
  }
  function classifyGap(gap) {
    if (gap < dotMs * 2) return;             // intra-character gap
    flushChar();                             // >= 2 dots -> character boundary
    if (gap > dotMs * 5) wordPending = true; // >= 5 dots -> also a word boundary
  }
  function flushChar() {
    if (!curMorse) return;
    if (wordPending && outText && !outText.endsWith(" ")) outText += " ";
    wordPending = false;
    outText += (TEXT_OF[curMorse] || "");
    curMorse = "";
    trimOut();
  }
  function flushIdle(now) {                   // flush a pending char/word once the key has been quiet
    if (keyOn) return;
    const gap = now - edgeT;
    if (curMorse && gap > dotMs * 2.5) flushChar();
    if (gap > dotMs * 6 && outText && !outText.endsWith(" ")) { outText += " "; }
  }
  function trimOut() { if (outText.length > 600) outText = outText.slice(-500); }

  function drawLoop() {
    if (out) {
      out.innerHTML = esc(outText) + (curMorse ? '<span class="cw-live">' + esc(curMorse) + "</span>" : "");
      out.scrollTop = out.scrollHeight;
    }
    $("cwWpm").textContent = Math.round(1200 / dotMs);
    $("cwTone").textContent = (+$("cwToneSet").value || 600);
    if (hint) {
      const audible = lvlPeak > 25;
      hint.textContent = audible ? "listening…" : "turn on 🔊 RX (or play below) to decode";
    }
    raf = requestAnimationFrame(drawLoop);
  }
  function esc(s) { return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }

  $("cwClear").addEventListener("click", () => { outText = ""; curMorse = ""; });
  $("cwAuto").addEventListener("click", () => autoTone());

  // pick the strongest bin in the 300-1000 Hz CW range over a short look
  function autoTone() {
    const ra = RA(); if (!ra || !ra.ctx()) return;
    const ctx = ra.ctx();
    if (!analyser) startDecoder();
    const buf = new Uint8Array(analyser.frequencyBinCount);
    let best = 0, bestMag = 0, frames = 0;
    const acc = new Float32Array(analyser.frequencyBinCount);
    const scan = setInterval(() => {
      analyser.getByteFrequencyData(buf);
      for (let i = 0; i < buf.length; i++) acc[i] += buf[i];
      if (++frames >= 30) {
        clearInterval(scan);
        const lo = Math.round(300 / (ctx.sampleRate / 2) * buf.length);
        const hi = Math.round(1000 / (ctx.sampleRate / 2) * buf.length);
        for (let i = lo; i <= hi; i++) if (acc[i] > bestMag) { bestMag = acc[i]; best = i; }
        const hz = Math.round(best * (ctx.sampleRate / 2) / buf.length / 10) * 10;
        if (hz) $("cwToneSet").value = Math.max(300, Math.min(1000, hz));
      }
    }, 10);
  }

  // ---- coder (text -> Morse sidetone; never transmits) ----
  const SEND_WPM = 18;
  $("cwPlay").addEventListener("click", () => playMorse($("cwSend").value));
  $("cwSend").addEventListener("keydown", (e) => { if (e.key === "Enter") playMorse($("cwSend").value); });
  function playMorse(text) {
    const ra = RA(); if (!ra) return;
    ra.ensure();
    const ctx = ra.ctx(), bus = ra.bus();
    if (!ctx || !bus || !text) return;
    const dot = 1.2 / SEND_WPM;               // seconds per dot
    const tone = +$("cwToneSet").value || 600;
    const osc = ctx.createOscillator(); osc.type = "sine"; osc.frequency.value = tone;
    const g = ctx.createGain(); g.gain.value = 0.0001;
    osc.connect(g); g.connect(bus);           // -> rxBus -> speakers AND the decoder's tap
    let t = ctx.currentTime + 0.05;
    for (const chRaw of text.toUpperCase()) {
      if (chRaw === " ") { t += dot * 7; continue; }
      const m = MORSE[chRaw]; if (!m) continue;
      for (const el of m) {
        const dur = el === "-" ? dot * 3 : dot;
        g.gain.setValueAtTime(0.0001, t);
        g.gain.exponentialRampToValueAtTime(0.3, t + 0.006);     // soft keying (no clicks)
        g.gain.setValueAtTime(0.3, t + dur - 0.006);
        g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
        t += dur + dot;                       // element + 1-dot intra-character gap
      }
      t += dot * 2;                           // -> 3-dot character gap
    }
    osc.start(); osc.stop(t + 0.1);
  }
})();

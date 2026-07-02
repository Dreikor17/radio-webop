# Roadmap

Radio WebOp is a browser control panel for ham transceivers (Icom CI-V + Yaesu CAT).
Each radio is a **declarative `RadioProfile`** in `backend/profiles.py` — transports
(CAT/serial, RS-BA1 LAN, audio, scope), a **capability map** the UI adapts to, and a
**SET-menu table** — so adding a radio is mostly data (see `docs/ADDING-A-RADIO.md`).

## Done

### v0.2.23 — CW chat as a texting UI
- The decoded‑CW window now reads like a messaging thread: **received in grey bubbles (left),
  transmitted in blue bubbles (right)**, time‑stamped, with the live in‑progress decode shown in
  amber inside the current receive bubble. Also: the **header version is now driven by
  `/api/version`** so it can't drift out of sync with the build.

### v0.2.22 — scope frequency scale + cursor readout, auto RX audio, CW-TX mode fix
- Scope shows the **centre (tuned) + edge frequencies** and a **cursor frequency readout** on hover
  (CI-V + AF scopes). **RX audio auto-starts on COM/USB connect**. **CW encoder TX** now works in any
  CW mode (fixed the FT-991A CW-USB/CW-LSB rejection).

### v0.2.21 — IC-7300MK2 operating controls
- Filled the Icom operating-control gaps: **internal ATU** (`1C 01`, reuses TUNER/TUNE),
  **FM tone/TSQL + CTCSS freq** (`16 42/43`, `1B 00`), an **Icom CW/filter group** (APF `16 32`,
  break-in `16 47`, CW pitch `14 09`, keyer speed `14 0C`, filter shape `16 56`), and **3-state
  preamp**. FM panel made adaptive (`fm_dcs` cap hides DCS on Icom). New caps `fm_dcs` / `icom_cw`.
  Remaining tracked in [docs/IC-7300MK2-SETTINGS-AUDIT.md](docs/IC-7300MK2-SETTINGS-AUDIT.md);
  most commands are shared with the IC-9700.

### v0.2.20 — FT-991A operating controls, rounded out
- **WIDTH** (`SH`, per-mode stepper), **CONTOUR** + **APF** (`CO`), the **CW cluster**
  (break-in/keyer/speed/pitch/spot/zero-in), **TXW**/**Quick Split**/**Parametric EQ**, **SCAN**
  and **FAST** — all adaptive by mode. **MONITOR** (`ML`) is now real (was a no-op). New
  `ext_ops` capability. Remaining (memory management, DVS, keyer-message play) tracked in
  [docs/FT-991A-SETTINGS-AUDIT.md](docs/FT-991A-SETTINGS-AUDIT.md).

### v0.2.19 — Yaesu operating controls (modes, Tone/DCS, NAR/WIDE)
- **Radio-accurate mode labels** on the Yaesu radios (CW-LSB/CW-USB, RTTY-LSB/RTTY-USB,
  DATA-LSB/DATA-USB, correct order) — the UI mirrors the rig's own MODE list.
- **FM Tone/DCS panel** — tone mode (`CT`), 50 CTCSS tones + 104 DCS codes (`CN`), repeater shift
  (`OS`); FM-family modes only. **NAR/WIDE** toggle (`NA`). New caps `narrow` / `fm_tone`.
- Kicks off the **"expose every setting"** effort: [docs/ADDING-A-RADIO.md](docs/ADDING-A-RADIO.md)
  now requires it, and [docs/FT-991A-SETTINGS-AUDIT.md](docs/FT-991A-SETTINGS-AUDIT.md) tracks the
  remaining operating controls (WIDTH/CONTOUR/TXW/MONITOR/CW-cluster/…).

### v0.2.18 — one-click Windows install
- **`install.bat`** (one-click install/update): checks for Python first (installs 3.12 via
  `winget` if missing), builds an isolated `.venv`, installs deps, verifies the import, and
  makes a desktop shortcut. Idempotent — re-run to update (git pull on a clean checkout).
  `run.bat` now uses the `.venv`. See [README](README.md#install--run-windows).

### v0.2.17 — Yaesu FT‑891
- **FT‑891 profile** (HF/50 MHz mobile, typically over a Digirig) on the shared Yaesu CAT driver:
  160–6 m, 10 modes, IPO/AMP preamp, RF ATT, 5–100 W, S/PO/SWR/ALC/COMP/ID meters; COM‑only, AF
  scope, **no internal ATU** (external/ATAS only). Host sound‑card audio (Digirig CODEC as Radio
  RX/TX).
- **Full 159‑item SET menu** in the Settings tab, compiled from the CAT reference and cross‑checked
  against a deterministic parse; connection/transmit‑sensitive items confirm‑gated, versions
  read‑only. The menu engine now carries a per‑item **`ex_width`** so it speaks the FT‑891's 4‑digit
  `EXGGNN` menu numbers as well as the FT‑991A's 3‑digit `EXNNN`.
- **Safety:** TX TOT (05‑14) set to 120 s and PC KEYING (07‑12) forced OFF on connect; the driver
  no longer emits an FT‑991A menu number for a radio that doesn't line‑key. CW *transmit* is not
  wired yet (FT‑891 `KY` replays stored memories only).

### v0.2.16 — remote operation: host audio + fast meter + secure remote access
- **Host sound‑card audio** for serial/USB radios operated remotely — the server captures the host's
  RX card → browser and plays the browser mic → the host's TX card (16 kHz PCM over the WS, like the
  LAN path). Radio RX/TX pickers list host devices; optional `sounddevice` dep.
- **Faster meter** — S/TX meters on a dedicated lightweight WS channel (~25 Hz Icom / ~12 Hz Yaesu),
  hi‑res‑timer pacing, and full‑state de‑dup so it doesn't flood. Failsafes untouched.
- **Secure remote access (Tailscale).** In‑app **🌐 Remote** setup: detects Tailscale, runs
  `tailscale serve` (tailnet‑only HTTPS, no port‑forward, CGNAT‑friendly), explains why HTTPS is
  needed. App‑layer shared‑password auth (loopback exempt; not‑trusted behind a proxy) + an
  Origin/Host allowlist closing cross‑site‑WS / DNS‑rebinding.

### v0.2.15 — audio routing + sharper waterfall + live CW decode
- **Three-input audio routing** — COM radios expose **Radio RX / Radio TX / Mic In**; IP radios just
  **Mic In** (defaults to the OS mic, never the radio CODEC). Remembered per radio.
- **Higher-resolution, WSJT-style audio scope** — adaptive FFT (~1 bin/pixel) + float dB data + a
  per-frequency noise-baseline subtraction that flattens the shaped passband to a dark floor (no
  smoothing, crisp pixels).
- **Live-streaming CW decode (no seam)** — a growing-buffer decoder that commits at word gaps and
  slides past them (after [e04/web-deep-cw-decoder](https://github.com/e04/web-deep-cw-decoder)); the
  worker returns per-character / word-space frame spans. Plus the "turn on RX" false-prompt fix.

### v0.2.14 — Icom Setup menus + preamp/tuner
- **Icom CI-V Setup menus** — the data-driven Setup tab generalized to Icom (`1A 05`): the
  **IC-9700** (54 items) and the full **IC-7300MK2** (176 items), on the same engine as the Yaesu
  `EX` menus; per-model tables in `backend/menus/<id>_menu.py`.
- **FT-991A preamp IPO / AMP1 / AMP2** (preamp states declared per radio via `preamp_labels`).
- **Antenna-tuner TUNE** action (operator-triggered ATU tuning cycle) beside the tuner in/out
  toggle, plus a **waterfall "click or drag to tune"** hint.
- **RF power read, not forced, on connect** — the panel mirrors the rig's actual power (and
  AF/RF/SQL) instead of zeroing it; the Icom per-band connect power-zero is removed.

### v0.2.13 — declarative profiles + full Setup menu
- **Declarative `RadioProfile`** is the single source of truth: structured `transports`
  (serial/network/audio/scope) + a `capabilities` map (synthesized from the flat flags when
  omitted). The **UI is adaptive** — it shows only the controls a rig reports (and fixed the
  FT-991A's dead VFO A/B buttons).
- **Data-driven Setup tab** — the **FT-991A's full 154-item SET menu** (001–154) as a grouped
  accordion: each section reads live from the radio on open (lazy), and writes back with a
  confirm-read. Connection/transmit-sensitive items (CAT rate/RTS, PC keying, PTT/port routing,
  per-band max power) are flagged and confirmed; CAT-RTS / PC-KEYING are protected so the menu
  can't break CW/PTT keying. Generic codec in `backend/menu_engine.py`; per-model table in
  `backend/menus/<id>_menu.py` (Icom CI-V `1A 05` menus are a stubbed seam for later).

### v0.2.2 — multi-manufacturer + overlay tools
- **Yaesu FT-991A** — first non-Icom radio (Yaesu CAT over USB, COM-only); profiles
  carry make/protocol/capabilities. Serial ports show Enhanced/Standard + auto-pick.
- **Band-plan overlay** (ARRL VHF/UHF + FCC license-class HF) with color key + tooltips.
- **CW decoder/coder** overlay tool (adaptive squelch/tone/WPM; Morse sidetone, no TX);
  **audio (AF) spectrum scope** from RX audio for radios with no CAT scope.
- **USB audio** RX-in/Mic-out pickers for COM radios; **per-radio connection memory**
  (+ "?" help) and **live panel re-sync** so the UI mirrors the radio's real state.

### v0.2.0 — unified multi-radio base
- **Multi-radio app** with a model selector (IC-9700, IC-7300MK2); bands/modes/
  steps render from the selected profile.
- Transports behind one interface: **USB CI-V**, **LAN** (RS-BA1 UDP), simulator.
- **Scope + waterfall** with the tuned marker + filter passband; tap-to-select and
  drag/slide tuning, step, direct entry, wheel.
- **RX audio** + **mic TX** over LAN (auto MOD-Input → LAN); **PTT** tap-to-toggle
  with a failsafe time-out; mobile-friendly; remembers radio + connection.

### v0.2.1 — IC-9700 build-out (M1–M3)
- **Dual-watch** MAIN/SUB Radio view + **multi-meter** + core RX (preamp/att/lock).
- **RX DSP** — NB, NR, auto/manual notch (+ width/position), AGC, twin-PBT.
- **TX** — mic / COMP / VOX / monitor / TBW, **RIT**, **split/duplex** (VOX bound by
  the same failsafe as PTT).
- **Skeuomorphic TFT Radio view** from the IC-9700 manual (mode/filter box, lit
  function-indicator strip, VFO + wavelength band labels); controls below the
  waterfall; full-travel sliders; abbreviation tooltips.
- Reference: full per-control map in `docs/CONTROL-MAP.md`, screen/menu blueprint
  in `docs/UI-SPEC.md`.

### Band-plan overlay
- **ARRL band plan / FCC license-class overlay** on the scope — a toggleable strip
  along the spectrum showing the segments for the band in view, aligned to the
  frequency axis, with a hover tooltip giving the range, type, modes and notes.
  VHF/UHF (2 m / 70 cm / 23 cm) is colored by **use** (CW / weak-signal / SSB / FM /
  repeater / simplex / satellite / beacon / digital, ARRL voluntary band plan); HF +
  6 m is colored by **license class** (Technician / General / Extra sub-bands, FCC
  Part 97). Data in `frontend/bandplan.js`, compiled + adversarially verified.

## Planned

### Operating
- **Band-plan overlay enhancements** (the overlay itself shipped): let the user pick
  their license class to highlight just their privileges and soft-flag tuning outside
  them; make the data region-aware so non-US band plans can drop in.
- **CW encoder / decoder / transmit** — *shipped*. The "CW" button pops a draggable panel
  over the waterfall with a **neural decoder** (the DeepCW ONNX model from e04/deepcw-engine,
  run in-browser via onnxruntime-web), a type-to-Morse soft-keyed off-air sidetone, and a
  **TX button** that sends the typed message as CW — operator-triggered, one bounded message,
  failsafe- and TOT-bound. Icom keys via CI-V `17` (semi break-in) so the rig generates the CW;
  the FT-991A is keyed by host-timed **DTR on its Standard USB port** (PC KEYING=DTR, CAT RTS
  disabled), since it has no arbitrary-text CW CAT command. The decoder is what relicensed the app to **AGPL-3.0** (a
  deliberate opt-in for the accuracy). **Live streaming decode shipped (v0.2.15)** — a growing-buffer
  decoder that commits at settled word gaps and slides past them, so the live stream decodes with no
  fixed-window seam (after e04/web-deep-cw-decoder). *Follow-ups:* optional WebGPU backend.
- **Audio recorder** — capture the RX audio (and optionally TX) to a file from the browser:
  start/stop with an elapsed-time + level readout and download. `MediaRecorder` on the
  pre-volume `rxBus` → WebM/Opus (or WAV), so it works for every transport (LAN PCM, USB
  audio, AF). Foundation for QSO timestamping / a recording log later.
- **More overlay tools** over the waterfall (the framework is in place) — e.g. RTTY/FT8
  decode, a tuning/zero-beat aid, memory keyer.
- **Tone / DTCS** (CTCSS encode/decode) + an editable duplex offset (M3.5).
- **Memory channels**, band-stacking registers, scan control.
- **CW** keyer/memories, **RTTY** decode; **DV / D-STAR** (DR, call signs, GPS);
  **satellite** dual-scope.
- Read **true IF filter widths** (`1A 03`); scope **reference-level / sweep-speed /
  VBW** controls.

### UI
- The **FUNCTION-screen panel** — a clean modern panel of the IC-9700 FUNCTION-screen
  toggles grouped by category. *(The SET-mode menu tree shipped — see Done.)*

### Platform
- **More radios** — IC-705, IC-7610, IC-905, IC-7300, etc. (each is just a profile).
- **Authentication** for safe remote use (today there is no login — restrict by
  interface / firewall / VPN). *Built-in HTTPS shipped* — serve TLS directly with
  `run.py --ssl-certfile/--ssl-keyfile` (or `RADIO_WEBOP_SSL_CERT/_KEY`).
- **Remote power-on** from networked standby where the radio supports it.
- Audio: selectable **codec / sample-rate**, lower-latency options.

## Non-goals (for now)
- Non-Icom rigs / Hamlib backends — possible later via the transport+profile split,
  but not a near-term focus.

# Changelog

All notable changes to **Radio WebOp** are documented here. This project adheres
to [Semantic Versioning](https://semver.org).

## [0.2.19] — 2026-07-01

Yaesu operating controls to match the radio: correct mode labels, FM Tone/DCS, and NAR/WIDE —
part of an ongoing push to surface **every** setting the rig has (see the audit doc).

### Added
- **FM Tone / DCS panel** (FT-991A + FT-891) — CTCSS/DCS **tone mode** (OFF / TONE / TSQL / DCS /
  DCS-ENC, `CT`), **CTCSS tone** (all 50, `CN`), **DCS code** (all 104, `CN`), and **repeater
  shift** direction (Simplex / + / −, `OS`). Shown only in FM-family modes. This fills the missing
  2 m / 70 cm tone/DCS controls.
- **NAR/WIDE** IF-filter toggle (`NA`) as an explicit control.
- **`docs/FT-991A-SETTINGS-AUDIT.md`** — a checklist of every FT-991A operating control vs. the UI,
  and a **"Completeness"** section in [docs/ADDING-A-RADIO.md](docs/ADDING-A-RADIO.md) making
  "expose every setting the radio has" an explicit requirement for each radio.

### Changed
- **Mode buttons now match the radio's own labels + order** on the Yaesu radios: **LSB, USB, AM,
  CW-LSB, CW-USB, FM, RTTY-LSB, RTTY-USB, C4FM, DATA-LSB, DATA-USB, DATA-FM** (was CW/CW-R/RTTY/
  RTTY-R/DATA-L/DATA-U). Icom labels are unchanged. New `Capabilities` flags `narrow` / `fm_tone`
  gate the new controls; the server passes radio-specific mode labels straight through.

## [0.2.18] — 2026-07-01

One-click install/update on Windows — no Python or command line needed.

### Added
- **`install.bat` — one-click install / update.** Checks for **Python first** (installs
  Python 3.12 via `winget` if none is found), then sets up an isolated local environment
  (`.venv`), installs/upgrades all dependencies, verifies the app imports, and creates a
  **"Radio WebOp" desktop shortcut**. Safe to re-run: on a git checkout it `git pull`s the
  latest code (only when your working tree is clean) before refreshing dependencies. The
  optional `sounddevice` audio package is best-effort — if it can't install, the core app
  still installs and runs.
- **`setup.ps1`** — the PowerShell engine behind `install.bat` (finds/ranks Python, prefers
  versions with reliable prebuilt wheels, skips Microsoft Store stubs).

### Changed
- **`run.bat` now uses the local `.venv`** created by the installer (falling back to system
  Python if you never ran it), so the one-click run is fully self-contained.

## [0.2.17] — 2026-06-30

Adds the **Yaesu FT‑891** (HF/50 MHz mobile), typically reached over a Digirig (USB‑serial CAT
plus a separate USB sound card for RX/TX audio — so it uses the host sound‑card audio path).

### Added
- **Yaesu FT‑891 radio profile.** All‑mode HF + 6 m (160–6 m), 10 modes (no DATA‑FM/C4FM),
  IPO/AMP preamp, a single RF ATT, 5–100 W, S/PO/SWR/ALC/COMP/ID meters. COM‑only, no CAT band
  scope (AF scope), **no internal ATU** (menu 16‑15 TUNER SELECT is external/ATAS only, so the
  Tuner/Tune controls are hidden). Reuses the shared Yaesu CAT driver.
- **Full FT‑891 SET menu in the Settings tab** — all **159 items** across 18 groups, compiled
  from the FT‑891 CAT reference (each with its exact on‑the‑wire value width) and cross‑checked
  against a deterministic parse of the manual. Connection/transmit‑sensitive items (CAT link,
  PC keying, PTT/port routing, TX time‑out, per‑band max power, tuner select, factory reset)
  are confirm‑gated; version items are read‑only.

### Changed
- **Menu engine handles variable EX menu‑number widths.** The FT‑891 addresses SET‑menu items
  by a 4‑digit group/item code (`EXGGNN`, e.g. 05‑14 → `EX0514`) rather than the FT‑991A's flat
  3‑digit `EXNNN`; `MenuItem.ex_width` carries the width through read/write/decode (and the sim).
- **Tuner controls moved to the TX panel; "Setup" tab renamed "Settings".** The **TUNER**/**TUNE**
  buttons are transmit functions, so they now live in the TX tab (in their own ANTENNA TUNER group,
  gated on the rig having an internal ATU) instead of RX.

### Safety
- **TX time‑out backstop on connect** — the FT‑891's TX TOT (menu 05‑14) is set to 2 min (120 s)
  on connect, matching the app's PTT failsafe.
- **PC KEYING forced OFF on connect** (menu 07‑12) so no control line can key the rig; the Yaesu
  driver no longer sends an FT‑991A‑specific menu number when a radio doesn't line‑key.
- CW **transmit** is not wired for the FT‑891 yet (its CAT `KY` only replays stored keyer
  memories, not arbitrary text) — key CW with a paddle for now.

## [0.2.16] — 2026-06-30

Remote operation: host‑side sound‑card audio for serial radios, a much faster meter, and a
secure remote‑access setup built around Tailscale (with password + anti‑hijacking defenses).

### Added
- **Host sound‑card audio for serial/USB radios over the network.** When a serial radio (e.g.
  FT‑991A) is operated remotely, its RX/TX audio lives on the **host** PC's sound card, which a
  remote browser can't see. The server now captures the chosen host **Radio RX** card and streams
  it to the browser, and plays the browser mic to the host **Radio TX** card — the same 16 kHz PCM
  path the IC‑9700 LAN audio uses. Radio RX/TX pickers list the host's devices (auto‑select the USB
  CODEC); **Mic In** stays the client mic. Optional `sounddevice` dependency.
- **Secure remote‑access setup (Tailscale).** A **🌐 Remote** dialog explains *why* HTTPS is needed
  (the mic needs a secure context), detects Tailscale, turns on `tailscale serve` for you (tailnet‑
  only HTTPS, no port‑forward, CGNAT‑friendly), shows your `*.ts.net` address, and walks through the
  setup. See [docs/REMOTE‑ACCESS.md](docs/REMOTE-ACCESS.md).
- **App‑layer auth.** An optional shared password gates remote sessions (the host on loopback is
  exempt); scrypt‑hashed, stdlib HMAC session cookie. A login page; the app bounces there if a remote
  session expires. Behind a reverse proxy, loopback is *not* treated as trusted (so the password
  actually gates tailnet peers).
- **Cross‑site/DNS‑rebinding defense.** An Origin/Host allowlist on the WebSocket and state‑changing
  routes blocks a page you merely visit from keying your transmitter — enforced even with no password.

### Changed
- **Meter polls much faster + lighter.** The S‑meter / TX meters now ride a dedicated lightweight WS
  channel at ~25 Hz on Icom serial (~12 Hz on the FT‑991A, CAT‑bound) instead of ~6.7 Hz, with a
  Windows hi‑res‑timer for a stable cadence. Full‑state frames are de‑duplicated, so the higher meter
  rate doesn't flood the connection (panel re‑reads no longer re‑broadcast unchanged state). The
  transmit failsafes are unchanged (absolute monotonic deadlines, checked every loop).

## [0.2.15] — 2026-06-29

Three-input audio device routing, a far sharper WSJT-style waterfall, and a CW decoder that
decodes the live stream continuously with no window seam.

### Added
- **Audio device routing, named per direction.** On a COM/USB radio the AUDIO panel now has three
  pickers — **Radio RX** (the rig's USB-CODEC receive audio you hear), **Radio TX** (the CODEC
  output your mic is routed to), and **Mic In** (your computer's microphone). On an IP/LAN radio only
  **Mic In** shows (RX/TX ride the network). Each is remembered per radio. **Mic In defaults to the
  system default microphone**, never the radio's RX CODEC — so the mic can't accidentally loop RX
  audio back into TX.

### Changed
- **Much higher-resolution spectrum + waterfall** for radios with no CAT scope (e.g. FT-991A). The
  audio scope now uses an **adaptive FFT** (~1 frequency bin per pixel — up to ~4× the old detail),
  reads **float dB** data instead of the lossy 8-bit path, and subtracts a **per-frequency noise
  baseline** so the receiver's shaped passband flattens to a dark floor and signals pop — much closer
  to WSJT. Temporal smoothing is **off** and the waterfall renders with crisp (non-interpolated) pixels.
- **CW decoder now decodes the live stream continuously — no window seam.** Reworked to a
  growing-buffer streaming decoder (after [e04/web-deep-cw-decoder](https://github.com/e04/web-deep-cw-decoder)):
  it commits each word at the **settled word gap** and slides the audio buffer past it, so a word is
  never cut mid-character the way the old fixed ~10 s window did. Committed text accumulates with a
  live preview trailing. The worker now returns per-character / word-space frame spans to drive it.

### Fixed
- **CW prompt no longer flashes "turn on RX" mid-decode** — it reflects whether RX audio is actually
  on, not a momentary audio level.
- **Mic In** no longer snaps back to a stale saved device when changed mid-stream; clearer message
  when a selected microphone is unavailable; audio stops cleanly when the transport is switched; the
  device pickers carry screen-reader labels.

## [0.2.14] — 2026-06-29

Icom CI-V Setup menus (IC-9700 + IC-7300MK2), the FT-991A preamp's third state, an
antenna-tuner TUNE action, and a waterfall tune hint.

### Added
- **Icom CI-V Setup menus.** The data-driven Setup tab now works for Icom radios over CI-V
  `1A 05` (the menu engine generalized from Yaesu CAT `EX`): the **IC-9700** (54 items) and the
  full **IC-7300MK2** (176 items) get grouped, lazily-read, write-through menus with the same
  critical-item confirms. Per-model tables live in `backend/menus/<id>_menu.py`.
- **FT-991A preamp now cycles IPO / AMP1 / AMP2** (was only IPO ↔ AMP1). Preamp states are
  declared per radio (`preamp_labels`) and the button shows the current state.
- **Antenna-tuner TUNE.** Alongside the tuner in/out toggle, a **TUNE** button starts an ATU
  tuning cycle (FT-991A `AC002`) — operator-triggered, the same transmit boundary as PTT, bounded
  by the radio's own cycle and the hardware TX time-out. Shown only on radios with an internal ATU.
- **Waterfall "click or drag to tune" hint** along the bottom of the scope.

### Changed
- **RF power is read, not forced, on connect.** The panel now **reads and displays** each radio's
  actual RF power (plus AF/RF/SQL) on connect, instead of forcing power to 0 %. The Icom path used
  to zero power (per-band on the IC-9700) on connect — that's removed; it now reads `14 0A` like the
  FT-991A already reads `PC`. Transmit stays guarded by operator-only keying, the 120 s PTT failsafe,
  the hardware TOT, and unkey-on-disconnect.

### Notes
- The IC-7300MK2 menu was compiled from its CI-V Reference Guide; verify it against the radio
  (RX-safe reads) — a few level items had an ambiguous range in the source and were set to max 255.

## [0.2.13] — 2026-06-29

Declarative, capability-driven radio profiles and a full data-driven Setup menu — the
FT-991A now exposes its entire SET menu (001–154) from the web UI.

### Added
- **Declarative profiles.** Each `RadioProfile` is now the single source of truth for a radio:
  structured **transports** (CAT/serial bits-parity-stop, RS-BA1 network + ports, audio, scope),
  a **capability map**, and a declarative **SET-menu table**. When omitted, transports/capabilities
  are synthesized from the existing flags, so the Icom profiles are unchanged.
- **Adaptive UI.** The interface shows only the controls a radio reports it supports
  (capability-driven), and the FT-991A's dead VFO A/B buttons are hidden (it has no CAT VFO
  selector; A=B / SWAP stay).
- **FT-991A Setup tab — the full SET menu (001–154).** A grouped accordion of every menu item;
  each section reads live from the radio when you open it (lazy, so connect stays fast) and writes
  back with a confirm-read. Connection/transmit-sensitive items (CAT rate, PTT/port routing,
  per-band max power) are flagged ⚠ and confirmed before writing; **CAT RTS** and **PC KEYING** are
  shown read-only because the app manages them for CW keying — so the menu can't break PTT/CW.
- **Menu engine.** A generic `EX` read/write/decode codec (`backend/menu_engine.py`) driven by a
  per-model table (`backend/menus/<id>_menu.py`), so a radio's menu is data; the Icom CI-V `1A 05`
  menu path is a stubbed seam for later.

### Notes
- A few FT-991A menu items the manual rendered ambiguously (RTTY shift/mark frequencies, the reserved
  index gaps, time zone) are encoded best-effort and flagged for confirmation against real hardware.

## [0.2.12] — 2026-06-26

CW transmit (both makes), live FT-991A transmit meters, a resizable CW window, and
transmit-safety hardening.

### Added
- **CW transmit.** A **TX** button next to Play sends the typed message as CW on the radio —
  operator-triggered, one bounded message per press, just like keying PTT, at the WPM you set.
  Per radio: **Icom** uses CI-V `17` (Send-CW-message) with semi break-in + `14 0C` keyer speed,
  so the rig generates the CW; **FT-991A** has no arbitrary-text CW CAT command, so it's keyed by
  host-timed **DTR on its second (Standard) USB port** (PC KEYING = DTR, CAT RTS disabled — the
  N1MM / fldigi / cwdaemon method); the app auto-detects the sibling port and sets the menus on
  connect. Only in CW/CW-R mode. Bounded by an auto-stop, the 120 s failsafe, and the hardware TOT;
  press again to stop; the key line is forced up on connect / stop / disconnect / client-drop and
  PC KEYING is disarmed on disconnect. Play stays the off-air sidetone so you never key by accident.
- **Resizable CW tool window** — drag any edge or corner to resize the CW panel; the decoded-
  text area grows with it. Applies to any overlay tool panel.
- **Transmit-safety contract** ([docs/ADDING-A-RADIO.md](docs/ADDING-A-RADIO.md)) — a gate
  for every radio that can key TX: the 120 s PTT failsafe, hardware TOT on connect, high-SWR
  cutoff + warning, RF power 0 % on connect, unkey-on-disconnect, bounded/stoppable CW send,
  and operator-triggered only (no unattended/auto-CQ transmission).
- **Hardware TX time-out timer set on connect** — a backstop if the control link drops
  mid-transmit. FT-991A = 2 min (120 s exactly); IC-9700 / IC-7300MK2 = 3 min (their coarsest
  non-OFF step — the app's 120 s PTT failsafe stays the precise limit). Verified on hardware.
- **CW coder WPM control** — the type-to-Morse sidetone speed is adjustable (5–40 WPM).

### Fixed
- **FT-991A transmit meters** — PO / SWR / ALC / COMP / Vd / Id now read live from the radio
  (Yaesu `RM` command); before, only the S-meter was polled so the others sat dead like the
  simulator. The TX meters read during transmit; the S-meter works on receive.

### Changed
- **Scope: removed the center frequency label** — the tuned-channel marker sat right on top
  of it; the edge (left/right) frequency labels still show.

## [0.2.10] — 2026-06-26

Big FT-991A + tooling release. **Note: this version relicenses Radio WebOp to
AGPL-3.0-only** (see below).

### Added
- **Neural CW decoder.** The CW tool's decoder is now the deep-learning **DeepCW** model
  (ONNX) running in-browser via onnxruntime-web — vastly more accurate than the old
  timing decoder under noise/QSB/QRM (≈0 % error down to ~0 dB SNR in testing). It taps
  the RX bus through a realtime AudioWorklet, resamples to 3200 Hz, and re-decodes a
  rolling window. The type-to-sidetone coder is unchanged (still never transmits).
- **FT-991A: full CAT control.** Power, AF/RF/squelch, AGC, NB, DNR, manual notch,
  **DNF auto-notch** (the A-NOTCH button), preamp (IPO/AMP1), 12 dB attenuator, dial
  lock, split, RIT/clarifier, IF shift, filter narrow/wide — all over Yaesu CAT, with
  the panel reading the radio's **real** settings on connect and polling them. Before,
  only frequency and mode worked; everything else was a silent no-op.
- **FT-991A: data/digital modes** — DATA-LSB, DATA-USB, DATA-FM and C4FM mode buttons.
- **FT-991A: antenna tuner** — a TUNER toggle that switches the internal ATU in/out of
  line (the transmit-to-tune cycle stays on the radio's own button).
- **FT-991A: PTT** now keys/unkeys (`TX1;`/`TX0;`) like the Icom path, with the same
  120 s stuck-TX failsafe.
- **Update check** — the version badge links to a newer GitHub release when one exists.
- **Built-in HTTPS** — `run.py --ssl-certfile/--ssl-keyfile` (or `RADIO_WEBOP_SSL_CERT/
  _KEY`) serves TLS directly on the port, so HTTPS works without a separate proxy.

### Fixed
- **Center-mode scope** no longer snaps when you change frequency — the whole view is
  anchored to the tuned freq and the content slides under a fixed center marker.
- **AF audio scope** (no-scope radios): the tuned-marker overlay no longer flickers while
  RX audio is on.
- Generic remote-access docs (no longer name a specific VPN); `.mjs` ES modules are
  served with a JavaScript MIME type.

### Changed
- **License: now AGPL-3.0-only** ([LICENSE](LICENSE), [NOTICE](NOTICE)). The neural CW
  decoder bundles the AGPL-3.0 DeepCW model, so the project is AGPL — if you host a
  modified copy, you must offer users the corresponding source. onnxruntime-web is MIT.

## [0.2.02] — 2026-06-26

Hotfix re-release of 0.2.2, which crashed at startup on a fresh install.

### Fixed
- **Startup crash on current Starlette.** A clean `pip install` pulls the latest
  Starlette, which removed the `on_startup=` / `on_shutdown=` constructor arguments
  (deprecated since 0.26). 0.2.2 failed at import with `Starlette.__init__() got an
  unexpected keyword argument 'on_startup'`. The server now uses a `lifespan` handler,
  which works on old and new Starlette. (A dev box's older Starlette had masked it.)
- **Scope marker jitter in center mode.** The tuned-channel marker and filter-width
  overlay are now anchored to the scope center while tuning, instead of bouncing
  off-center (the optimistic tuned freq raced ahead of the sweep's reported center)
  and snapping back. Fixed mode still tracks the tuned freq across the window.

## [0.2.2] — 2026-06-26

Goes **multi-manufacturer** (first non-Icom radio), adds overlay tools over the
waterfall, and makes the radio's real state — and your connection choices — stick.

### Added
- **Yaesu FT-991A** — the first non-Icom radio. Yaesu **CAT** over USB (COM-only,
  8N2); frequency / mode / S-meter readout and control. Verified on real hardware.
  A profile now carries its make, protocol and capabilities, so adding a brand is
  still just a profile.
- **Band-plan overlay** on the scope (toggle) — the **ARRL voluntary band plan** on
  VHF/UHF and **FCC license-class sub-bands** on HF, aligned to the frequency axis,
  with a color-key legend and a hover tooltip per segment. Data is compiled and
  verified against the ARRL plan + FCC Part 97.
- **CW decoder / coder** — the first overlay tool: a draggable panel that decodes
  received CW from the RX audio (adaptive tone, **squelch**, WPM) and encodes typed
  text to a soft-keyed Morse sidetone. It never transmits.
- **Audio (AF) spectrum scope** — for radios with no CAT band scope (FT-991A), an
  FFT of the RX audio drives the spectrum + waterfall as a mini-panadapter, mapped
  to RF by the mode's sideband.
- **USB audio for COM radios** — RX-in / Mic-out device pickers for the radio's USB
  sound device (the control link carries no audio over a serial/CAT connection).
- **Per-radio connection "?" help** — a popover listing the radio-side settings to
  set before connecting (CI-V baud/address, the FT-991A's CAT rate + Enhanced port).

### Changed
- **Connection memory is now per-radio** — each radio remembers its last transport
  (Simulator / LAN / COM port), that transport's settings, and for COM the RX-in /
  Mic-out devices; startup reopens the last-used radio.
- **Settings now track the radio live** — the Icom poll re-reads the whole panel
  (preamp/att/lock, RX-DSP, levels, RIT, split/duplex) periodically, so front-panel
  changes show in the app, not just the values read at connect.
- The scope opens at the **widest span (±250 kHz)**; the settings column is a touch
  wider; **RX audio is smoother** (continuous resampling + a firmer jitter buffer).
- Serial ports now show **Enhanced / Standard** (the FT-991A's CAT is on Enhanced),
  and a COM-only radio auto-selects its Enhanced port.
- Static assets are versioned so a reload never serves stale JS/CSS.

### Fixed
- **Mobile:** scrolling no longer wipes the waterfall (the address-bar show/hide
  fired a resize that rebuilt it).
- The spectrum/waterfall **split** no longer gets stuck "dragging" after release —
  and dragging it no longer secretly re-tuned the radio.
- The **CW window** no longer jumps when you grab it to move it.
- COM / Simulator now **hide the LAN IP/user/password** fields; the FT-991A (no
  network) hides the LAN option entirely.
- Changing the selected radio clears the previous waterfall and zeros the VFOs until
  you reconnect; **Enter** in a connection field starts Connect.

## [0.2.1] — 2026-06-25

Start of the full **IC-9700** control build-out (see [ROADMAP.md](ROADMAP.md) and
`docs/CONTROL-MAP.md`). The other radios still use the shared base.

### Added
- **Dual-watch (MAIN/SUB) Radio view** for the IC-9700 — both receivers shown; tap
  a band to make it the operating band. The active band is live; the other shows
  its last-known values and goes live when selected (the radio reports only the
  operating band over CI-V).
- **Multi-meter** with a selector (S / PO / SWR / ALC / COMP / Vd / Id) — the
  S-meter is live; the TX meters come alive while transmitting.
- **Core RX controls** — preamp, attenuator, dial lock.
- **RX DSP** — Noise Blanker, Noise Reduction, auto + manual notch (with width and
  position), AGC (FAST / MID / SLOW), and twin PBT.
- `docs/CONTROL-MAP.md` — the full map of the IC-9700's CI-V control surface with
  the milestone build order.

### Fixed
- The connection bar now wraps at laptop / tablet widths instead of overflowing.

## [0.2.0] — 2026-06-25

Merged the former **Icom WebOp (IC-9700)** and **IC-7300MK2 WebOp** apps into one
multi-radio program: **Radio WebOp**.

### Added
- **Multi-radio support with a radio-model selector.** Bands, modes, steps and
  the CI-V address come from a per-model profile (`backend/profiles.py`); ships
  the IC-9700 and IC-7300MK2. Adding a radio is just a new profile.
- **Tap-to-select and drag/slide tuning** directly on the waterfall (alongside
  the dial, wheel, step select and direct entry).
- **PTT failsafe time-out** — the radio auto-unkeys after a fixed interval both
  client-side (a visible countdown on the PTT button) and server-side (an
  independent auto-unkey that fires even if the browser is gone).
- A **collapsible connection bar on mobile** (status + ⚙ toggle) so the scope
  gets the screen; it auto-collapses once connected.
- `ROADMAP.md`.
- Over LAN, **MOD Input is set to LAN automatically on connect** (and restored on
  disconnect), so the browser mic transmits without changing the radio's menu.

### Changed
- Renamed the app to **Radio WebOp**; responsive/mobile layout pass.
- **PTT is tap-to-toggle** (was press-and-hold) so it works on touchscreens.
- Remembers the selected radio alongside the connection method.
- Microphone (TX) shows a clear "needs HTTPS" message on insecure connections —
  browsers only expose `getUserMedia` in a secure context (HTTPS or localhost).

### Fixed
- **Stuck-TX risk with multiple clients:** the radio now unkeys when *any* keyed
  client disconnects, not only the last one (the client-side unkey can be lost
  when the socket is already closing). The server failsafe remains the backstop.
- **MOD Input could be left on LAN** if the read of the original value was lost;
  the app now only takes over the MOD source once it has captured the original,
  so it can always restore it (otherwise it leaves the radio's MOD untouched).

## [0.1.0] — 2026-06-25

First release. Browser-based CI-V control panel for the Icom IC-9700 with a
live spectrum scope + waterfall.

### Added
- **Live spectrum scope + waterfall** decoded from CI-V `27 00` (475 points,
  0–160), with the tuned-channel marker and the receive filter passband shaded
  over it, like the radio's own display.
- **Transports** behind one interface:
  - **USB** serial CI-V (a COM port).
  - **LAN** — Icom RS-BA1 UDP protocol (control/serial/audio on 50001/50002/
    50003), reverse-engineered; control + scope verified on real hardware.
  - **Simulator** that speaks CI-V back (incl. USB-format scope sweeps) and
    auto-connects on load, so the whole UI runs with no radio.
- **Control** — band (144 / 430 / 1200), mode (LSB/USB/CW/CW-R/AM/FM/RTTY/DV),
  filter (FIL1–3), VFO A/B/A=B/swap.
- **Tuning** — draggable main dial, mouse-wheel, click-to-tune on the scope,
  selectable step, and direct frequency entry.
- **Levels** — AF / RF / SQL / RF power with value readouts (power shown as %);
  RF power defaults to 0% on connect (across all bands, as a safety default).
- **Audio over LAN** — RX audio plays in the browser (verified on hardware);
  mic streams to the radio for TX (16 kHz / 16-bit mono PCM).
- Real-time **S-meter** and frequency / mode / filter readouts.
- **Remembers** the chosen connection (and LAN IP / user / password) in the
  browser.
- **Mobile-friendly** responsive layout with touch-sized controls.
- Binds to `0.0.0.0` by default (reachable across your network).

### Notes
- The LAN protocol is a clean-room implementation informed by the open-source
  wfview and kappanhang projects; there is no official Icom wire-format spec.
- No authentication yet — anyone who can reach the port can control the radio
  (including TX). Restrict the bind interface and/or use a firewall / VPN.
- Mic capture (TX) needs a secure context (HTTPS or localhost), so it won't run
  over plain-HTTP remote access; RX audio playback works over HTTP.

[0.2.10]: https://github.com/Dreikor17/radio-webop/releases/tag/v0.2.10
[0.2.02]: https://github.com/Dreikor17/radio-webop/releases/tag/v0.2.02
[0.2.2]: https://github.com/Dreikor17/radio-webop/releases/tag/v0.2.2
[0.2.1]: https://github.com/Dreikor17/radio-webop/releases/tag/v0.2.1
[0.2.0]: https://github.com/Dreikor17/radio-webop/releases/tag/v0.2.0
[0.1.0]: https://github.com/Dreikor17/radio-webop/releases/tag/v0.1.0

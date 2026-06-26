# Changelog

All notable changes to **Radio WebOp** are documented here. This project adheres
to [Semantic Versioning](https://semver.org).

## [Unreleased]

### Added
- **FT-991A: full CAT control.** Power, AF/RF/squelch, AGC, NB, DNR, manual notch,
  preamp (IPO/AMP1), 12 dB attenuator, dial lock, split, RIT/clarifier, IF shift and
  filter narrow/wide now work over Yaesu CAT, and the panel reads the radio's **real**
  settings on connect (and keeps polling them). Previously only frequency and mode were
  wired up — every other control was a silent no-op. Command formats verified against
  the Yaesu CAT reference + Hamlib and confirmed on real hardware (RX-only, no TX).
- **Update check** — the version badge links to a newer GitHub release when one exists.
- **Built-in HTTPS** — `run.py --ssl-certfile/--ssl-keyfile` serves TLS directly on the
  port (or `RADIO_WEBOP_SSL_CERT/_KEY`), so HTTPS works without a separate proxy.

### Fixed
- **Center-mode scope** no longer snaps when you change frequency — the whole view is
  anchored to the tuned freq and the content slides under a fixed center marker.

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

[0.2.02]: https://github.com/Dreikor17/radio-webop/releases/tag/v0.2.02
[0.2.2]: https://github.com/Dreikor17/radio-webop/releases/tag/v0.2.2
[0.2.1]: https://github.com/Dreikor17/radio-webop/releases/tag/v0.2.1
[0.2.0]: https://github.com/Dreikor17/radio-webop/releases/tag/v0.2.0
[0.1.0]: https://github.com/Dreikor17/radio-webop/releases/tag/v0.1.0

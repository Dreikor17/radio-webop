# Changelog

All notable changes to **Radio WebOp** are documented here. This project adheres
to [Semantic Versioning](https://semver.org).

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
- Binds to `0.0.0.0` by default (reachable over LAN / Tailscale).

### Notes
- The LAN protocol is a clean-room implementation informed by the open-source
  wfview and kappanhang projects; there is no official Icom wire-format spec.
- No authentication yet — anyone who can reach the port can control the radio
  (including TX). Restrict the bind interface and/or use Tailscale ACLs.
- Mic capture (TX) needs a secure context (HTTPS or localhost), so it won't run
  over plain-HTTP remote access; RX audio playback works over HTTP.

[0.2.1]: https://github.com/Dreikor17/radio-webop/releases/tag/v0.2.1
[0.2.0]: https://github.com/Dreikor17/radio-webop/releases/tag/v0.2.0
[0.1.0]: https://github.com/Dreikor17/radio-webop/releases/tag/v0.1.0

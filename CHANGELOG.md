# Changelog

All notable changes to Icom WebOp (IC-9700) are documented here. This project
adheres to [Semantic Versioning](https://semver.org).

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

[0.1.0]: https://github.com/Dreikor17/icom-webop/releases/tag/v0.1.0

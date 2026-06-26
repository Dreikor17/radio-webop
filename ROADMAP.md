# Roadmap

Radio WebOp is a browser control panel for Icom CI-V transceivers. The protocol
(CI-V framing, the `27 00` scope, the RS-BA1 LAN transport, audio) is shared;
each supported radio is a **profile** in `backend/profiles.py` (address, bands,
modes, filter widths, MOD-Input numbers, power behaviour), so adding a radio is
just adding a profile.

## Done

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
- **CW encoder / decoder** — *shipped* as the first overlay tool (the "CW" button pops
  a draggable panel over the waterfall): a classic DSP decoder on the RX audio (adaptive
  tone/threshold/WPM) + a type-to-Morse soft-keyed sidetone (no transmit). *Follow-ups:*
  optionally integrate the deep-learning ONNX decoder from e04/deep-cw-decoder (note: it
  is **AGPL-3.0**, so it would relicense the app — a deliberate opt-in); and actual CW
  keying to TX via the CI-V keyer, bound by the same PTT failsafe.
- **More overlay tools** over the waterfall (the framework is in place) — e.g. RTTY/FT8
  decode, a tuning/zero-beat aid, memory keyer.
- **Tone / DTCS** (CTCSS encode/decode) + an editable duplex offset (M3.5).
- **Memory channels**, band-stacking registers, scan control.
- **CW** keyer/memories, **RTTY** decode; **DV / D-STAR** (DR, call signs, GPS);
  **satellite** dual-scope.
- Read **true IF filter widths** (`1A 03`); scope **reference-level / sweep-speed /
  VBW** controls.

### UI
- The **FUNCTION-screen panel** + the **SET-mode menu tree** (M4) — clean modern
  lists generated from the control map, grouped by the radio's MENU categories.

### Platform
- **More radios** — IC-705, IC-7610, IC-905, IC-7300, etc. (each is just a profile).
- **Authentication + built-in HTTPS** for safe remote use (today there is no login —
  restrict by interface / firewall / VPN).
- **Remote power-on** from networked standby where the radio supports it.
- Audio: selectable **codec / sample-rate**, recording, lower-latency options.

## Non-goals (for now)
- Non-Icom rigs / Hamlib backends — possible later via the transport+profile split,
  but not a near-term focus.

# IC-7300MK2 operating-settings audit

Goal: expose **every** operating setting the IC-7300MK2 has in the web UI (see the "Completeness"
section in [ADDING-A-RADIO.md](ADDING-A-RADIO.md)). The SET/MENU items already ship in full on the
**Settings** tab, and the Icom handler (`radio.py`) already covered most operating controls
(freq/mode/filter, preamp/att/lock, NB/NR/notch, AGC, twin-PBT, COMP/VOX/MONITOR, RIT, split/duplex,
meters, scope). This tracks the remaining operating (FUNCTION-menu / front-panel) controls, audited
from the CI-V reference + Basic manual.

## Done
- [x] **Internal antenna tuner** — TUNER in/out + TUNE cycle (`1C 01`); reuses the TUNER/TUNE buttons
      (`has_tuner=True` for the IC-7300MK2 only; the IC-9700 has no ATU).
- [x] **Preamp 3-state** — OFF / P.AMP1 / P.AMP2 (`16 02`).
- [x] **FM Tone / TSQL** — repeater tone (`16 42`) + tone squelch (`16 43`) on/off, driven from the
      FM Tone panel; **CTCSS tone frequency** (`1B 00`/`1B 01`, all 50 tones). (No DTCS over CI-V, so
      the panel's DCS options are hidden on Icom; repeater shift uses the existing SPLIT·RIT duplex.)
- [x] **APF** OFF / WIDE / MID / NAR (`16 32`), CW.
- [x] **CW break-in** OFF / SEMI / FULL (`16 47`), CW.
- [x] **CW pitch** 300–900 Hz (`14 09`) and **keyer speed** 6–48 WPM (`14 0C`) sliders, CW.
- [x] **IF filter shape** SHARP / SOFT (`16 56`).

## To do (deferred)

### Would fit the current model (next batch)
- [ ] **APF peak position** (`14 05`, ±250 Hz) beside the APF cycle.
- [ ] **Break-in delay** (`14 0F`) beside break-in.
- [ ] **TSQL frequency** as a separate control (`1B 01`; currently kept equal to the tone freq).
- [ ] **Twin Peak Filter** (`16 4F`, RTTY), **ANTI-VOX** (`14 17`), **IP+** (`16 65`),
      **RX-ANT** (`12 00`), **XFC** TX-freq monitor (`1C 02`), **ΔTX/XIT** (`21 02`).
- [ ] **Scan** start/stop (`0E`) — Icom's scan model (programmed/memory/ΔF/fine) differs from the
      Yaesu up/down buttons, so it needs its own small control.
- [ ] **Scope** reference level / sweep speed / hold / edge (`27 19/1A/17/16`).
- [ ] **Tuning-step select** (`10`).

### Needs a subsystem
- [ ] **Memory-channel** operations — VFO⇄MEM, write, M-CH select, clear (`07`/`08`/`09`/`0A`/`0B`,
      `1A 00`), **band-stacking registers** (`1A 01`).
- [ ] **CW keyer memories** M1–M8 (`1A 02` + `17` to send — keys TX, failsafe-bound).
- [ ] **Voice-TX memories** T1–T8 (`28 00`) and **voice announce** (`13`).

### N/A
- **IF filter width edit** (`1A 03`) — the FIL1/2/3 + twin-PBT controls already cover this.

_Audited from `docs/IC-7300MK2_ENG_CI-V_0.pdf` + `_Basic_1.pdf`. **Verify on the real radio** (read →
echo), especially the **CTCSS tone BCD** (`1B 00`) format, before trusting writes. Most of these
CI-V commands are shared with the IC-9700 — enabling them there later is a per-profile capability
flip. TX paths honour the transmit-safety contract._

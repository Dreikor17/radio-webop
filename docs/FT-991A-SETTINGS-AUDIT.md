# FT-991A operating-settings audit

Goal: expose **every** setting the FT-991A has, as if standing at the radio (see the
"Completeness" section in [ADDING-A-RADIO.md](ADDING-A-RADIO.md)). This is the working
checklist — the SET/MENU items already ship in full on the **Settings** tab; this tracks the
**operating (FUNCTION-menu / front-panel) controls**, audited from the CAT reference + OM.

## Done
- [x] **Mode list** matches the radio's labels + order — LSB, USB, AM, CW-LSB, CW-USB, FM,
      RTTY-LSB, RTTY-USB, C4FM, DATA-LSB, DATA-USB, DATA-FM (`MD`).
- [x] **NAR/WIDE** IF-filter toggle (`NA`).
- [x] **FM Tone/DCS** — tone mode OFF/TONE/TSQL/DCS/DCS-ENC (`CT`), CTCSS tone (50, `CN0`),
      DCS code (104, `CN1`); shown only in FM-family modes.
- [x] **Repeater shift** direction — Simplex / + / − (`OS`), FM only.
- [x] **IF WIDTH** — DSP passband width stepper (`SH`), per-mode Hz from the manual table;
      SSB/CW/RTTY/DATA, follows NAR/WIDE. (DSP tab.)
- [x] **CONTOUR** on/off + centre freq (`CO0`/`CO1`, 10–3200 Hz). SSB/CW/RTTY/DATA/AM.
- [x] **APF** on/off + peak freq (`CO2`/`CO3`, −250..+250 Hz). CW only.
- [x] **MONITOR** on/off + level (`ML0`/`ML1`) — the `MON` button + `MON` slider are now real
      (were no-ops).
- [x] **CW cluster** — Break-In (`BI`), Keyer (`KR`), Keyer speed (`KS`, 4–60 WPM), CW pitch
      (`KP`, 300–1050 Hz), SPOT (`CS`), Zero-In (`ZI`). CW modes only.
- [x] **TXW** (`TS`), **Quick Split** (`QS`), **Parametric mic EQ** (`PR1`). (TX tab.)
- [x] **SCAN** stop/up/down (`SC`) + **FAST** step (`FS`). (Radio tab, OPERATING.)

## To do (deferred — need a bigger subsystem, or niche / redundant)
- [ ] **Memory channels** — channel select (`MC`), VFO⇄MEM (`VM`), store/copy (`MW`/`MT`/`MA`),
      QMB (`QI`/`QR`), band select (`BS`). Needs a memory-management UI (a distinct feature).
- [ ] **CW keyer messages** — edit (`KM`) + playback (`KY`, keys TX → must bind the PTT failsafe).
      Pairs with the memory work; the app's own CW-TX (host-timed keying) is the primary path.
- [ ] **DVS** voice-memory record/playback (`LM`/`PB`) — niche.
- [ ] **MIC UP/DN** (`UP`/`DN`); **Date/Time/TZ** (`DT`).
- [ ] N/A: **MOX** (`MX`) — redundant with the PTT control (both key TX); **Dimmer** (`DA`) —
      covered by the SET-menu DIMMER items; **BK-IN delay** (`SD`) — dup of SET menu 057; **AI**
      auto-info — internal housekeeping, not a user control; **IF-SHIFT** (`IS`) — already shipped
      as the `PBT1` slider.

_Audit generated from `docs/CAT_CONTROL_ysu-ft-991a_us.pdf` + `docs/FT-991A_OM_*.pdf`. Verify new
commands on the real radio (read → echo) before trusting them — esp. the `SH` per-mode WIDTH
table; TX paths must honor the transmit-safety contract._

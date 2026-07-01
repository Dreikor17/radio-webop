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

## To do (operating controls)

### High
- [ ] **IF WIDTH** — DSP passband width (`SH`, per-mode bandwidth code table). SSB/CW/RTTY/DATA.
- [ ] **CONTOUR** on/off + center freq (`CO0 0` / `CO0 1`, 10–3200 Hz). SSB/CW/RTTY/DATA/AM.
- [ ] **TXW** — listen on the TX frequency during split (`TS`, hold). All (split).

### Medium
- [ ] **MONITOR** on/off + level (`ML0` / `ML1`) — the existing `#monBtn` / `#mon_level` are
      currently no-ops; make them real. (Also the CW sidetone level.)
- [ ] **APF** on/off + peak freq (`CO0 2` / `CO0 3`, −250..+250 Hz). CW only.
- [ ] **Break-In** on/off (`BI`), **Keyer** on/off (`KR`), **keyer speed** (`KS`, 4–60 WPM),
      **CW pitch** (`KP`, 300–1050 Hz). CW only.
- [ ] **Quick Split** (`QS`); **Scan** (`SC`); **Memory channel** select (`MC`); **VFO/MEM**
      toggle (`VM`); **FAST** step (`FS`).
- [ ] **Parametric mic EQ** enable via `PR1` (SSB/AM).

### Low
- [ ] CW **SPOT** (`CS`) / **Zero-In** (`ZI`); keyer-memory **edit** (`KM`) + **playback**
      (`KY`, keys TX — bind to the PTT failsafe); **BK-IN delay** quick set (`SD`).
- [ ] Memory **store/copy** (`AM`/`MA`/`MW`/`MT`), **QMB** (`QI`/`QR`), **band select** (`BS`).
- [ ] **MIC UP/DN** (`UP`/`DN`), **MOX** (`MX`, keys TX — failsafe), **AI** auto-info (`AI`).
- [ ] **DVS** voice-memory record/playback (`LM`/`PB`); **Dimmer** (`DA`); **Date/Time/TZ** (`DT`).
- [ ] Verify **IF-SHIFT** (`IS`) ↔ the current `pbt1` mapping; confirm `SH` per-mode width tables.

_Audit generated from `docs/CAT_CONTROL_ysu-ft-991a_us.pdf` + `docs/FT-991A_OM_*.pdf`. Verify new
commands on the real radio (read → echo) before trusting them; TX paths must honor the
transmit-safety contract._

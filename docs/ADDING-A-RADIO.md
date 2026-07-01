# Adding a radio — and the transmit‑safety contract

Radio WebOp is **profile‑driven**: each model is a `RadioProfile` in
`backend/profiles.py`, driven by a protocol handler (`Radio` = Icom CI‑V,
`YaesuRadio` = Yaesu CAT). Adding a model of an existing make is usually just a new
profile; a new make/protocol is a new handler class exposing the same method surface the
server dispatches (`set_freq` / `set_mode` / `set_level` / `set_ptt` / …).

## ⚠️ Transmit‑safety contract — MANDATORY for any radio that can key TX

Remote operation puts a real transmitter on the air over a network, so **every keyed path
needs a backstop**. A radio is **not "done"** until every applicable item below is wired —
or explicitly marked N/A with a reason. When in doubt, **fail safe** (don't transmit / unkey).

1. **Operator‑triggered only — no *unattended* transmission.** Every keyed path is the
   direct result of an operator action *now*: **PTT** (press = key, release = unkey) and the
   **CW message send** (one bounded message per TX press — the rig's own keyer generates it,
   the operator can stop it). What is **not** shipped is transmission the app *originates on
   its own*: auto‑CQ on a timer, beacons, scheduled/unattended keying — those would need
   explicit local‑control safeguards and are never an implicit feature. (Hard rule for the
   assistant maintaining this repo: it wires operator‑triggered transmit, but never
   triggers or tests a transmission itself — on‑air testing is the operator's.)
2. **PTT stuck‑TX failsafe (120 s).** Keying arms `PTT_TIMEOUT`; the poll loop auto‑unkeys
   past the deadline, the client shows a countdown and unkeys too, **disconnect unkeys**,
   and **ANY client drop while keyed unkeys**. Mirror `Radio.set_ptt`, the `_poll`
   failsafe, and the `disconnect` unkey.
3. **Auto‑keying paths bound to the same failsafe.** Any mode that can key TX off audio
   (VOX) arms the same deadline and is dropped on disconnect.
4. **Hardware TOT set on connect.** Set the radio's own time‑out timer to ≈120 s (closest
   the radio supports) as a backstop for **control‑link loss** — if the network drops
   mid‑transmit the app failsafe can't fire, but the rig's own timer will. The profile
   carries the command; `N/A` if the radio doesn't expose it over the control link (then
   the app‑level failsafe is the only backstop — note it).
5. **CW message send is bounded + stoppable** (if `cw_send` is set). The rig's keyer
   generates the CW at the WPM the app sets — never host‑timed PTT keying. Cap the message
   length, only fire in CW/CW‑R mode, arm an auto‑stop deadline (`_cw_deadline`, the poll
   stops it — Icom `17 FF`), expose a stop, and stop on disconnect / client‑drop. The
   hardware TOT (item 4) is the hard backstop where a clean CAT abort isn't available
   (FT‑991A keyer playback).
6. **High‑SWR cutoff + warning.** While keyed, read the SWR meter; warn in the UI and
   auto‑unkey above the threshold to protect the PA. Reads + a protective un‑key only.
7. **Power read — not forced — on connect.** The app **reads and displays** the radio's
   current RF power (and AF/RF/SQL) on connect so the panel mirrors the rig; it does **not**
   zero power. (Earlier builds forced 0 % on connect — removed by request so the operator's
   real settings show. Transmit stays guarded by operator‑only keying, the 120 s PTT
   failsafe, the hardware TOT, and unkey‑on‑disconnect.)
8. **Unkey + restore on disconnect.** Never leave the radio keyed; restore any borrowed
   state (e.g. the MOD source on the Icom LAN path).

Items 1–5, 8 ship today; 6 (high‑SWR cutoff) is being added per‑radio; 7 is read‑and‑display
(power is no longer forced to 0). The profile fields make every safety feature
inherit‑by‑filling for future radios.

## Completeness — expose *every* setting the radio has

**Goal: the web UI should let the operator do everything they could do standing at the radio.**
Not just frequency/mode/levels — every operating control and every menu item. Treat the manuals
as the checklist, in two layers:

1. **SET / MENU items** → the data‑driven **Settings** tab. Compile the *complete* menu table
   into `backend/menus/<id>_menu.py` (one `MenuItem` per entry, every group), not a subset.
2. **Operating (front‑panel / FUNCTION‑menu) controls** → real controls in the main tabs, each
   backed by a CAT/CI‑V command. These are the ones most often missed because they are *not* in
   the SET menu. Go through the manual's FUNCTION/operating sections and the CAT reference's full
   command list and wire **all** of them, gating each to the modes it applies to. For the Yaesu
   radios this includes, e.g.: **NAR/WIDE** (`NA`), **WIDTH** (`SH`), **CONTOUR/APF** (`CO`),
   **IF‑SHIFT** (`IS`), **CTCSS/DCS tone mode** (`CT`), **tone/DCS number** (`CN`), **repeater
   shift** (`OS`), **MONITOR** (`ML`), **break‑in/keyer/speed/pitch/spot** (`BI`/`KR`/`KS`/`KP`/`CS`),
   **TXW** (`TS`), **scan/memory/VFO‑MEM** (`SC`/`MC`/`VM`), etc.

How to be exhaustive and not miss things:

- **Audit the manual against the code.** Extract the CAT/CI‑V reference and the operating manual to
  text (`pymupdf`/`fitz` — the Read tool can't rasterize PDFs here), list *every* command and
  operating setting, and diff against what the handler + UI already expose. Anything reachable over
  the control link but not in the UI is a gap to close. (This repo keeps such an audit as a
  workflow; see the FT‑991A gap list in the changelog history.)
- **Gate by capability + mode.** Add a `Capabilities` flag for each optional control family (e.g.
  `narrow`, `fm_tone`) so the UI shows it only on radios that have it, and hide mode‑specific
  controls when out of mode (Tone/DCS only in FM, WIDTH/CONTOUR only in SSB/CW/RTTY/DATA, APF/keyer
  only in CW). Adaptive UI beats a wall of dead buttons.
- **Never ship a dead control.** If a button/slider exists in the HTML, its handler must be real
  (not a `_noop`). A stub that silently does nothing is worse than an absent control.
- **Verify a sample on the real radio** (read → echo) before trusting new commands, and remember
  the transmit‑safety contract above for anything that can key TX (`KY` keyer playback, `MX` MOX).

## The profile is the declarative source of truth

A `RadioProfile` describes *everything* about reaching a radio and what it can do, and the UI
renders/gates itself from it:

- **`transports`** (`Transports`) — serial bits/parity/stopbits, RS‑BA1 network + UDP ports,
  audio kind (`usb-codec`/`lan`/`none`), scope kind (`native`/`audio`/`none`).
- **`capabilities`** (`Capabilities`) — `preamp`/`att`/`tuner`/`dual_watch`/`vfo_select`/
  `rx_dsp`/`tx_funcs`/`meters`/… The UI **shows only what `capabilities` reports.**
- **`menu`** — a declarative `MenuItem` list driving the data‑driven **Setup tab**.

When `transports`/`capabilities` are omitted they are **synthesized from the flat `has_*`
flags in `__post_init__`**, so you only spell them out where the radio differs (e.g. the
FT‑991A has no CAT active‑VFO selector → `vfo_select=False`).

## Steps to add a radio
1. Add a `RadioProfile` (id, name, make, protocol, address/baud, bands, modes, filters, steps,
   `connect_help`, the **safety/feature fields** — `tot_civ`/`tot_cat`, SWR meter source,
   `cw_send`/`cw_line`). Add `transports`/`capabilities` only where they differ from the
   synthesized defaults.
2. **SET‑menu table (recommended)** — to expose the radio's full menu in the Setup tab, add
   `backend/menus/<id>_menu.py`: one `MenuItem` per entry (num, name, group,
   `kind`=enum|int|signed‑int, **`digits` = the manual's exact wire width** — for signed it
   INCLUDES the sign char, options *or* min/max/step/unit), marking `readonly` and `critical`
   (connection/transmit‑sensitive) items. Reference it from the profile (`menu=<ID>_MENU`).
   Compile it from the manufacturer's CAT/menu reference (a research pass over the PDF).
   **Verify a sample of `digits` on the real radio (read → echo) before trusting the table** —
   a wrong width is silently ignored by the radio. The shared `backend/menu_engine.py` handles
   encode/decode (Yaesu `EX` today; the Icom CI‑V `1A 05` encoder is implemented too — see ic9700_menu.py / ic7300mk2_menu.py).
3. **Same protocol** as an existing radio → done; the handler + menu engine are shared.
4. **New protocol/make** → a new handler class exposing the server's method surface (incl.
   `get_menu`/`set_menu`/`read_menu_group`) **and the full safety contract above**, plus a
   menu‑engine encoder for that protocol.
5. Register it in `PROFILES`; add any new **frontend** asset to `server.py`'s versioned‑asset
   list (or it 404s / serves stale). Menu tables are pure backend data — no asset entry needed.
6. Verify on real hardware **RX‑side only** (the maintainer never tests TX by transmitting).
   The operator verifies any transmit path — PTT, CW send — and any **critical menu writes**
   (CAT rate, PTT/port routing, max power) themselves.

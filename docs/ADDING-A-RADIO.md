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

1. **No autonomous transmission.** The app never *generates* transmissions on the
   operator's behalf — no CW / voice / auto‑CQ keyers that key the rig. **PTT relays the
   operator's direct control only** (press = key, release = unkey). On‑air keyers are a
   deliberate operator add‑on, never a shipped feature. (This is also a hard rule for the
   assistant maintaining this repo: it does not author transmit‑*generating* code.)
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
5. **High‑SWR cutoff + warning.** While keyed, read the SWR meter; warn in the UI and
   auto‑unkey above the threshold to protect the PA. Reads + a protective un‑key only.
6. **Safe power on connect.** RF power defaults to 0 % on connect.
7. **Unkey + restore on disconnect.** Never leave the radio keyed; restore any borrowed
   state (e.g. the MOD source on the Icom LAN path).

Items 2, 3, 6, 7 ship today; 4 and 5 are being added per‑radio and the profile fields make
them inherit-by-filling for future radios.

## Steps to add a radio
1. Add a `RadioProfile` (id, name, make, protocol, address/baud, bands, modes, filters,
   steps, the `has_*` capability flags, `connect_help`, and the **safety fields** — TOT
   command/value, SWR meter source).
2. **Same protocol** as an existing radio → done; the handler is shared.
3. **New protocol** → a new handler class exposing the server's method surface **and the
   full safety contract above**.
4. Register it in `PROFILES`; add any new frontend asset to `server.py`'s versioned‑asset
   list (or it 404s / serves stale).
5. Verify on real hardware **RX‑side only**; never test TX by transmitting.

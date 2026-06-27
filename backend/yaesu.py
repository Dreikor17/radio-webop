"""
Yaesu CAT (serial) radio — a COM-only sibling of the Icom CI-V ``Radio`` used
for the FT-991A. Same public surface the server calls, but speaks Yaesu CAT:
2-letter ASCII commands + fixed-width params, each terminated by ';'.

The FT-991A exposes NO band scope / spectrum over CAT (its scope is display
only), so there is no waterfall — the app shows an audio (AF) scope instead.

Command formats below are verified against BOTH the Yaesu FT-991A CAT Operation
Reference and the Hamlib newcat/ft991 sources (digit counts + ranges matter — a
wrong width makes the radio silently ignore the command, which is why the level
sliders, power and DSP toggles did nothing before).

TX SAFETY (same as the Icom path): PTT is operator-driven from the UI (TX1;/TX0;)
and bound by a stuck-TX failsafe — auto-unkey after PTT_TIMEOUT, on disconnect, and
on any client drop while keyed. VOX is never armed over CAT (it can auto-key off mic
audio with no explicit PTT). The TX; poll keeps the keyed state in sync.
"""
from __future__ import annotations

import threading
import time
from typing import Optional

from . import civ, profiles
from .radio import PTT_TIMEOUT, fresh_state, smeter_label

# our mode-button name -> Yaesu MD command code (P2 char). Only the subset the
# FT-991A profile exposes (all of which also exist in civ.MODE_CODES).
YAESU_MD = {
    "LSB": "1", "USB": "2", "CW": "3", "FM": "4", "AM": "5",
    "RTTY": "6", "CW-R": "7", "RTTY-R": "9",
    "DATA-L": "8", "DATA-FM": "A", "DATA-U": "C", "C4FM": "E",
}
# MD reply code -> readable name (includes codes we don't expose as buttons)
MD_DECODE = {
    "1": "LSB", "2": "USB", "3": "CW", "4": "FM", "5": "AM",
    "6": "RTTY", "7": "CW-R", "8": "DATA-L", "9": "RTTY-R",
    "A": "DATA-FM", "B": "FM-N", "C": "DATA-U", "D": "AM-N", "E": "C4FM",
}
# GT answer P3 (0-6) -> our AGC button (1 FAST / 2 MID / 3 SLOW). The radio
# reports AUTO as 4/5/6 (auto-fast/mid/slow); fold those onto the base speed.
AGC_READ = {"0": 0, "1": 1, "2": 2, "3": 3, "4": 1, "5": 2, "6": 3}

# the FT-991A's level sliders all arrive 0-255 (CI-V scale, mapped server-side);
# each Yaesu level has its own native range, so we scale on the way out and back.
HF_MAX_W = 100          # 160-6 m (incl. 50 MHz)
VU_MAX_W = 50           # 144 / 430 MHz


def _scale(value: int, hi: int) -> int:
    """0-255 slider -> 0..hi (native Yaesu range)."""
    return round(max(0, min(255, int(value))) / 255 * hi)


def _unscale(value: int, hi: int) -> int:
    """native 0..hi -> 0-255 slider (for read-back)."""
    if hi <= 0:
        return 0
    return min(255, round(max(0, min(hi, int(value))) / hi * 255))


class YaesuRadio:
    def __init__(self, profile=None) -> None:
        self.profile = profile or profiles.PROFILES.get("ft991a")
        self._tp = None
        self._lock = threading.Lock()
        self._poll_thread: Optional[threading.Thread] = None
        self._poll_stop = threading.Event()
        self._ptt_deadline: Optional[float] = None   # stuck-TX failsafe deadline (monotonic)
        self._rxbuf = ""
        self.on_state = None
        self.on_scope = None          # never used (no scope over CAT)
        self.on_audio = None
        self.state = fresh_state(self.profile)
        self.state["dual_watch"] = False

    # -- lifecycle -----------------------------------------------------------
    def connect(self, transport, profile=None) -> None:
        self.disconnect()
        if profile is not None:
            self.profile = profile
            self.state = fresh_state(profile)
            self.state["dual_watch"] = False
        self._tp = transport
        self._rxbuf = ""
        transport.start(self._on_bytes)
        self.state["connected"] = True
        self.state["transport"] = transport.name
        self.state["audio"] = getattr(transport, "supports_audio", False)
        # prime the readout + the whole settings panel so the UI shows the
        # radio's REAL state on connect, not defaults.
        for cmd in ("ID;", "IF;", "FA;", "MD0;", "SM0;") + self._SETTINGS:
            self._send(cmd)
        if getattr(self.profile, "tot_cat", ""):            # safety: hardware TX time-out backstop
            self._send(self.profile.tot_cat)
        self._poll_stop.clear()
        self._poll_thread = threading.Thread(target=self._poll, daemon=True, name="yaesu-poll")
        self._poll_thread.start()
        self._emit_state()

    def disconnect(self) -> None:
        self._poll_stop.set()
        if self._poll_thread:
            self._poll_thread.join(timeout=1.0)
            self._poll_thread = None
        if self._tp:
            try:
                if self.state.get("ptt"):
                    self._tp.write(b"TX0;")          # never leave the radio keyed
            except Exception:
                pass
            try:
                self._tp.stop()
            except Exception:
                pass
            self._tp = None
        self._ptt_deadline = None
        self.state["ptt"] = False
        if self.state.get("connected"):
            self.state["connected"] = False
            self.state["transport"] = None
            self._emit_state()

    # -- wire ----------------------------------------------------------------
    def _send(self, cmd: str) -> None:
        tp = self._tp
        if tp:
            try:
                tp.write(cmd.encode("ascii"))
            except Exception:
                pass

    def _on_bytes(self, data: bytes) -> None:
        try:
            self._rxbuf += data.decode("ascii", "ignore")
        except Exception:
            return
        while ";" in self._rxbuf:
            part, self._rxbuf = self._rxbuf.split(";", 1)
            part = part.strip()
            if part:
                self._parse(part)
        if len(self._rxbuf) > 512:        # never let a stray un-terminated reply grow
            self._rxbuf = ""

    def _parse(self, msg: str) -> None:  # noqa: C901 - flat prefix dispatch, kept readable
        m = msg
        changed = True
        if m.startswith("FA") and len(m) >= 11 and m[2:11].isdigit():
            self._set_freq_state(int(m[2:11]))
        elif m.startswith("MD0") and len(m) >= 4:
            name = MD_DECODE.get(m[3].upper())
            if name:
                self.state["mode_name"] = name
                self.state["main"]["mode_name"] = name
            else:
                changed = False
        elif m.startswith("SM0") and len(m) >= 6 and m[3:6].isdigit():
            self._set_smeter(int(m[3:6]))
        elif m.startswith("PC") and len(m) >= 5 and m[2:5].isdigit():
            self.state["rfpwr"] = _unscale(int(m[2:5]), self._max_watts())
        elif m.startswith("AG0") and len(m) >= 6 and m[3:6].isdigit():
            self.state["af"] = int(m[3:6])
        elif m.startswith("RG0") and len(m) >= 6 and m[3:6].isdigit():
            self.state["rf"] = int(m[3:6])
        elif m.startswith("SQ0") and len(m) >= 6 and m[3:6].isdigit():
            self.state["sql"] = _unscale(int(m[3:6]), 100)
        elif m.startswith("MG") and len(m) >= 5 and m[2:5].isdigit():
            self.state["mic"] = _unscale(int(m[2:5]), 100)
        elif m.startswith("GT0") and len(m) >= 4:
            self.state["agc"] = AGC_READ.get(m[3], self.state.get("agc", 2))
        elif m.startswith("NB0") and len(m) >= 4:
            self.state["nb"] = 1 if m[3] == "1" else 0
        elif m.startswith("NL0") and len(m) >= 6 and m[3:6].isdigit():
            self.state["nb_level"] = _unscale(int(m[3:6]), 10)
        elif m.startswith("NR0") and len(m) >= 4:
            self.state["nr"] = 1 if m[3] == "1" else 0
        elif m.startswith("RL0") and len(m) >= 5 and m[3:5].isdigit():
            self.state["nr_level"] = _unscale(int(m[3:5]), 15)
        elif m.startswith("BC0") and len(m) >= 4:
            self.state["anotch"] = 1 if m[3] == "1" else 0       # DNF (auto notch)
        elif m.startswith("BP00") and len(m) >= 7:
            self.state["mnotch"] = 1 if m[4:7] == "001" else 0
        elif m.startswith("BP01") and len(m) >= 7 and m[4:7].isdigit():
            self.state["mnotch_pos"] = _unscale(int(m[4:7]), 320)
        elif m.startswith("PA0") and len(m) >= 4:
            self.state["preamp"] = 1 if m[3] in ("1", "2") else 0
        elif m.startswith("RA0") and len(m) >= 4:
            self.state["att"] = 1 if m[3] == "1" else 0
        elif m.startswith("LK") and len(m) >= 3 and m[2] in ("0", "1"):
            self.state["lock"] = (m[2] == "1")
        elif m.startswith("NA0") and len(m) >= 4:                  # narrow/wide -> FIL1/FIL2
            self.state["filter"] = 2 if m[3] == "1" else 1
            self.state["filter_name"] = "FIL2" if m[3] == "1" else "FIL1"
        elif m.startswith("FT") and len(m) >= 3 and m[2] in ("0", "1"):
            self.state["split"] = 1 if m[2] == "1" else 0
        elif m.startswith("RT") and len(m) >= 3 and m[2] in ("0", "1"):
            self.state["rit"] = 1 if m[2] == "1" else 0
        elif m.startswith("AC") and len(m) >= 5 and m[4] in ("0", "1", "2"):
            self.state["tuner"] = 1 if m[4] in ("1", "2") else 0  # AC00X: 0 off, 1 on, 2 tuning
        elif m.startswith("IF") and len(m) >= 14 and m[5:14].isdigit():
            self._set_freq_state(int(m[5:14]))                    # freq only; mode via MD0;
        elif m.startswith("TX") and len(m) >= 3:
            self.state["ptt"] = m[2] in ("1", "2")                # status only; never keyed here
        else:
            changed = False
        if changed:
            self._emit_state()

    def _set_freq_state(self, hz: int) -> None:
        self.state["freq"] = hz
        self.state["main"]["freq"] = hz

    def _max_watts(self) -> int:
        return HF_MAX_W if int(self.state.get("freq", 0)) < 54_000_000 else VU_MAX_W

    def _set_smeter(self, level: int) -> None:
        level = max(0, min(255, level))
        scaled = round(level / 255 * 241)            # 0..255 -> the 0..241 S-scale
        s = smeter_label(scaled)
        self.state["smeter"] = scaled
        self.state["smeter_s"] = s
        self.state["main"]["smeter"] = scaled
        self.state["main"]["smeter_s"] = s
        if self.state.get("meter", "S") == "S":
            self.state["meter_val"] = min(240, round(level / 255 * 240))

    # settings read each panel refresh (also primed on connect) — keep the
    # fast-changing freq/mode/smeter/tx out of here; those poll every cycle.
    _SETTINGS = ("PC;", "AG0;", "RG0;", "SQ0;", "MG;", "GT0;",
                 "NB0;", "NL0;", "NR0;", "RL0;", "BC0;", "BP00;",
                 "PA0;", "RA0;", "LK;", "NA0;", "FT;", "RT;", "AC;")

    def _poll(self) -> None:
        cyc = 0
        while not self._poll_stop.is_set():
            # stuck-TX failsafe: never leave the radio keyed past the time-out.
            if (self._ptt_deadline and time.monotonic() >= self._ptt_deadline
                    and self.state.get("ptt")):
                self.set_ptt(False)
            for cmd in ("FA;", "MD0;", "SM0;", "TX;"):
                if self._poll_stop.is_set():
                    break
                self._send(cmd)
                time.sleep(0.03)
            if cyc % 6 == 0:                         # refresh the full panel ~every 2 s
                c4fm = self.state.get("mode_name") == "C4FM"
                for cmd in self._SETTINGS:
                    if self._poll_stop.is_set():
                        break
                    if cmd == "NA0;" and c4fm:       # FT-991A hangs on NA0; while in C4FM
                        continue
                    self._send(cmd)
                    time.sleep(0.03)
            cyc += 1
            for _ in range(3):                       # ~0.3 s between fast cycles
                if self._poll_stop.is_set():
                    break
                time.sleep(0.1)

    # -- commands the server dispatches --------------------------------------
    def set_freq(self, hz: int) -> None:
        hz = max(30_000, min(470_000_000, int(hz)))
        self._set_freq_state(hz)
        self._send(f"FA{hz:09d};")
        self._emit_state()

    def tune(self, delta_hz: int) -> None:
        self.set_freq(int(self.state.get("freq", 0)) + int(delta_hz))

    def set_mode(self, mode, filt: Optional[int] = None) -> None:
        # the server hands us the Icom CI-V code; map code -> name -> Yaesu MD
        name = mode if isinstance(mode, str) else civ.MODES.get(mode)
        md = YAESU_MD.get(name)
        if not md:
            return
        self.state["mode_name"] = name
        self.state["main"]["mode_name"] = name
        self._send(f"MD0{md};")
        if filt is not None:
            self.set_filter(filt)
        self._emit_state()

    def set_filter(self, filt: int) -> None:
        # FT-991A has narrow/wide (NA0), not 3 discrete filters: FIL1 = wide,
        # FIL2/FIL3 = narrow. Reflect the choice and tell the radio.
        narrow = 0 if int(filt) <= 1 else 1
        self.state["filter"] = int(filt)
        self.state["filter_name"] = f"FIL{int(filt)}"
        if self.state.get("mode_name") != "C4FM":     # FT-991A hangs on NA0; in C4FM
            self._send(f"NA0{narrow};")
        self._emit_state()

    def set_band(self, band: str) -> None:
        d = self.profile.band_default(band)
        if d:
            self.set_freq(d)

    select_band = set_band

    def set_meter(self, key: str) -> None:
        self.state["meter"] = key
        self._emit_state()

    def select_vfo(self, code: int) -> None:
        code = int(code)
        if code == 0xA0:                              # A=B (copy VFO-A -> VFO-B)
            self._send("AB;")
        elif code == 0xB0:                            # swap A/B
            self._send("SV;")
        # 0x00/0x01 (select A/B): the FT-991A has no active-VFO selector — no-op.

    def set_level(self, sub: int, value: int) -> None:
        value = max(0, min(255, int(value)))
        key = None
        cmd = None
        if sub == 0x01:                               # AF gain   AG0 000-255
            key, cmd = "af", f"AG0{value:03d};"
        elif sub == 0x02:                             # RF gain   RG0 000-255
            key, cmd = "rf", f"RG0{value:03d};"
        elif sub == 0x03:                             # squelch   SQ0 000-100
            key, cmd = "sql", f"SQ0{_scale(value, 100):03d};"
        elif sub == 0x0A:                             # RF power  PC 005-100 W (band-capped)
            key, cmd = "rfpwr", f"PC{max(5, _scale(value, self._max_watts())):03d};"
        elif sub == 0x0B:                             # mic gain  MG 000-100
            key, cmd = "mic", f"MG{_scale(value, 100):03d};"
        elif sub == 0x06:                             # DNR level RL0 01-15
            key, cmd = "nr_level", f"RL0{max(1, _scale(value, 15)):02d};"
        elif sub == 0x12:                             # NB level  NL0 000-010
            key, cmd = "nb_level", f"NL0{_scale(value, 10):03d};"
        elif sub == 0x0E:                             # processor PL 000-100
            key, cmd = "comp_level", f"PL{_scale(value, 100):03d};"
        elif sub == 0x0D:                             # manual notch freq BP01 001-320 (x10 Hz)
            key, cmd = "mnotch_pos", f"BP01{max(1, _scale(value, 320)):03d};"
        elif sub == 0x07:                             # twin-PBT inner -> single IF shift
            off = max(-1200, min(1200, round((value - 128) / 127 * 1200 / 20) * 20))
            key, cmd = "pbt1", f"IS0{'+' if off >= 0 else '-'}{abs(off):04d};"
        # mon/vox/pbt2 have no safe FT-991A mapping (vox arms TX) -> ignored.
        if cmd is None:
            return
        if key:
            self.state[key] = value
        self._send(cmd)
        self._emit_state()

    def set_rx_func(self, name: str, on: bool) -> None:
        v = 1 if on else 0
        cmd = None
        if name == "nb":
            cmd = f"NB0{v};"
        elif name == "nr":
            cmd = f"NR0{v};"
        elif name == "anotch":
            cmd = f"BC0{v};"                          # DNF (digital/auto notch) on/off
        elif name == "mnotch":
            cmd = f"BP00{v:03d};"                     # IF (manual) notch BP00000; / BP00001;
        elif name == "comp":
            cmd = f"PR0{2 if on else 1};"             # speech processor on/off
        # "vox"/"mon": vox arms TX -> never toggled here (safety)
        if cmd is None:
            return
        self.state[name] = v
        self._send(cmd)
        self._emit_state()

    def set_agc(self, mode: int) -> None:
        mode = max(1, min(3, int(mode)))             # 1 FAST / 2 MID / 3 SLOW
        self.state["agc"] = mode
        self._send(f"GT0{mode};")
        self._emit_state()

    def set_preamp(self, on: bool) -> None:
        self.state["preamp"] = 1 if on else 0
        self._send(f"PA0{1 if on else 0};")          # 0 = IPO, 1 = AMP1
        self._emit_state()

    def set_att(self, on: bool) -> None:
        self.state["att"] = 1 if on else 0           # FT-991A ATT is a single 12 dB step
        self._send(f"RA0{1 if on else 0};")
        self._emit_state()

    def set_lock(self, on: bool) -> None:
        self.state["lock"] = bool(on)
        self._send(f"LK{1 if on else 0};")
        self._emit_state()

    def set_split(self, on: bool) -> None:
        self.state["split"] = 1 if on else 0
        self._send("FT3;" if on else "FT2;")         # FT3 = TX on VFO-B (split on)
        self._emit_state()

    def set_rit(self, on: bool) -> None:
        self.state["rit"] = 1 if on else 0
        self._send(f"RT{1 if on else 0};")           # clarifier on/off
        self._emit_state()

    def set_rit_freq(self, hz: int) -> None:
        hz = max(-9999, min(9999, int(hz)))
        self.state["rit_freq"] = hz
        # clear then step to the absolute offset (Hamlib idiom); magnitude is 4 digits.
        self._send(f"RC;RU{hz:04d};" if hz >= 0 else f"RC;RD{-hz:04d};")
        self._emit_state()

    def set_tuner(self, on: bool) -> None:
        # Switch the internal ATU in (AC001) or out of line (AC000). This does NOT
        # transmit. Starting an actual tuning cycle is AC002;, which keys a carrier —
        # that is a TX action and is deliberately not issued here.
        self.state["tuner"] = 1 if on else 0
        self._send("AC001;" if on else "AC000;")
        self._emit_state()

    def set_ptt(self, tx: bool) -> None:
        # Operator-driven from the UI (same as the Icom set_ptt). TX1; keys, TX0;
        # unkeys, bound by the stuck-TX failsafe (auto-unkey in _poll, on disconnect,
        # and on client drop). The TX; poll keeps state["ptt"] in sync with the radio.
        tx = bool(tx)
        self.state["ptt"] = tx
        self._ptt_deadline = (time.monotonic() + PTT_TIMEOUT) if tx else None
        self._send("TX1;" if tx else "TX0;")
        self._emit_state()

    def write_audio(self, pcm: bytes) -> None:
        return                                        # no USB-audio path here

    # -- not applicable over FT-991A CAT -------------------------------------
    def _noop(self, *a, **k) -> None:
        return

    set_mnotch_w = set_tbw = set_duplex = _noop
    set_span = set_scope_mode = _noop

    def _emit_state(self) -> None:
        if self.on_state:
            try:
                self.on_state(self.state)
            except Exception:
                pass

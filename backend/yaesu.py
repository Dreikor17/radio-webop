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

import json
import threading
import time
from typing import Optional

from . import civ, menu_engine, profiles
from .radio import (CW_YAESU_CHARS, PTT_TIMEOUT, cw_duration, cw_elements,
                    fresh_state, smeter_label)
from .transport import CwKeyPort, find_sibling_port

# Windows quantises time.sleep to ~15 ms unless the multimedia timer resolution is
# raised; CW element timing needs ~1 ms, so bracket the keying loop with timeBeginPeriod.
try:
    import ctypes
    _WINMM = ctypes.WinDLL("winmm")
except Exception:
    _WINMM = None


def _sleep_until(deadline: float, abort) -> None:
    """Sleep until perf_counter() >= deadline, staying responsive to an abort Event."""
    while True:
        rem = deadline - time.perf_counter()
        if rem <= 0 or abort.is_set():
            return
        time.sleep(0.0008 if rem > 0.0008 else rem)

# our mode-button name -> Yaesu MD command code (P2 char). Names match the radio's
# own labels (CW-USB/CW-LSB, RTTY-LSB/RTTY-USB, DATA-LSB/DATA-USB) so the UI mirrors
# the rig. LSB/USB/AM/FM arrive as CI-V codes (via civ.MODES); the rest as the name.
YAESU_MD = {
    "LSB": "1", "USB": "2", "CW-USB": "3", "FM": "4", "AM": "5",
    "RTTY-LSB": "6", "CW-LSB": "7", "DATA-LSB": "8", "RTTY-USB": "9",
    "DATA-FM": "A", "DATA-USB": "C", "C4FM": "E",
}
# MD reply code -> readable name (matches the radio's front-panel mode labels;
# includes FM-N / AM-N, which the rig can report but we don't expose as buttons)
MD_DECODE = {
    "1": "LSB", "2": "USB", "3": "CW-USB", "4": "FM", "5": "AM",
    "6": "RTTY-LSB", "7": "CW-LSB", "8": "DATA-LSB", "9": "RTTY-USB",
    "A": "DATA-FM", "B": "FM-N", "C": "DATA-USB", "D": "AM-N", "E": "C4FM",
}
# GT answer P3 (0-6) -> our AGC button (1 FAST / 2 MID / 3 SLOW). The radio
# reports AUTO as 4/5/6 (auto-fast/mid/slow); fold those onto the base speed.
AGC_READ = {"0": 0, "1": 1, "2": 2, "3": 3, "4": 1, "5": 2, "6": 3}

# our meter-button key -> Yaesu RM (READ METER) P1 selector. S comes from SM0; the rest
# are read via RM (value 0-255). FT-991A RM P1: 3=COMP 4=ALC 5=PO 6=SWR 7=ID 8=VDD.
YAESU_METER = {"PO": "5", "SWR": "6", "ALC": "4", "COMP": "3", "Vd": "8", "Id": "7"}
YAESU_METER_BY_P1 = {p1: key for key, p1 in YAESU_METER.items()}

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
        self._cw_deadline: Optional[float] = None    # CW-TX auto-stop deadline (monotonic)
        self._cw_abort: Optional[threading.Event] = None   # stops an in-progress CW message
        self._cw_thread: Optional[threading.Thread] = None  # the live keying thread (join + re-entrancy)
        self._key_port: Optional[CwKeyPort] = None   # 2nd (Standard) USB port for DTR CW keying
        self._rxbuf = ""
        self.on_state = None
        self.on_scope = None          # never used (no scope over CAT)
        self.on_meter = None          # fast meter channel (separate from full state)
        self._last_sig = None         # last non-meter state sig (dedup full frames)
        self.on_audio = None
        self.on_menu = None
        self._menu_index = {}     # num -> MenuItem, built on connect
        self._menu_vals = {}      # num -> last decoded menu value (cache)
        self._ex_width = 3        # EX menu-number width (FT-991A NNN=3, FT-891 GGNN=4)
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
        _menu = getattr(self.profile, "menu", None) or []
        self._menu_index = {it.num: it for it in _menu}
        self._ex_width = max((getattr(it, "ex_width", 3) for it in _menu), default=3)
        self._menu_vals = {}
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
        # CW keying setup. The FT-991A keys CW from the DTR line of its SECOND (Standard)
        # USB port; CAT runs here on the Enhanced port. So: set menu 033 CAT RTS=DISABLE
        # (RTS stays free for CAT, host RTS high), menu 060 PC KEYING=DTR, and open the
        # sibling port DTR-low. When CW is NOT line-keyed, force PC KEYING=OFF so the
        # control lines can never auto-key the rig.
        self.state["cw_tx_ready"] = False
        if getattr(self.profile, "cw_send", "") == "line":
            self._send("EX0330;")                           # menu 033 CAT RTS = DISABLE
            # Open the key port DTR-low FIRST so we own the line low, THEN arm PC KEYING.
            # If the key port can't open, disarm (EX0600) rather than leave the rig armed
            # to key on a line we don't control.
            if self._open_key_port(transport):
                line = getattr(self.profile, "cw_line", "dtr")
                self._send("EX0603;" if line == "dtr" else "EX0602;")   # menu 060 PC KEYING = DTR / RTS
            else:
                self._send("EX0600;")                       # no key port -> PC KEYING = OFF
        else:
            # Not line-keying (e.g. FT-891 over a Digirig): force PC KEYING = OFF as a safety
            # default so no control line can key the rig. The menu number differs per model
            # (FT-991A 060, FT-891 07-12), so the full EX string comes from the profile.
            off = getattr(self.profile, "pc_keying_off_cat", "")
            if off:
                self._send(off)
        self._poll_stop.clear()
        self._poll_thread = threading.Thread(target=self._poll, daemon=True, name="yaesu-poll")
        self._poll_thread.start()
        self._emit_state()

    def _open_key_port(self, transport) -> bool:
        """Open the sibling (Standard) USB port for DTR CW keying — CAT stays on the
        Enhanced port. Best-effort: if the sibling can't be found/opened, CW TX simply
        stays unavailable (cw_tx_ready False) rather than failing the connection.
        Returns True iff the key port is open and ready."""
        dev = getattr(transport, "port", None)
        if not dev:
            return False                                    # sim / LAN: no real serial port
        sib = find_sibling_port(dev)
        if not sib:
            return False                                    # no sibling port -> CW TX unavailable (UI shows it)
        try:
            kp = CwKeyPort(sib)
            kp.open()                                       # opens DTR low (key up)
            self._key_port = kp
            self.state["cw_tx_ready"] = True
            return True
        except Exception:
            self._key_port = None
            return False

    def disconnect(self) -> None:
        self._poll_stop.set()
        if self._poll_thread:
            self._poll_thread.join(timeout=1.0)
            self._poll_thread = None
        if self._cw_abort:
            self._cw_abort.set()                     # stop any in-progress CW keying
        t = self._cw_thread                          # wait for the keyer to fully exit BEFORE
        if t and t.is_alive():                       # closing the port, so no key() races close()
            t.join(timeout=2.0)
        self._cw_thread = None
        if self._key_port:
            self._key_port.close()                   # DTR low + close the 2nd (keying) port
            self._key_port = None
        if self._tp:
            try:
                if getattr(self.profile, "cw_send", "") == "line":
                    self._send("EX0600;")            # disarm: menu 060 PC KEYING = OFF
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
        self._cw_deadline = None
        self.state["ptt"] = False
        self.state["cw_tx"] = False
        self.state["cw_tx_ready"] = False
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
        if m.startswith("EX") and len(m) >= 6 and m[2:5].isdigit():
            self._handle_ex_reply(m)     # SET-menu reply -> separate menu channel, not state
            return
        changed = True
        meter_changed = False            # SM0 / RM ride the lightweight meter channel
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
            self._set_smeter(int(m[3:6])); meter_changed = True; changed = False
        elif m.startswith("RM") and len(m) >= 6 and m[2:6].isdigit():   # READ METER (TX meters)
            key = YAESU_METER_BY_P1.get(m[2])
            if key and self.state.get("meter") == key:
                self.state["meter_val"] = min(255, int(m[3:6])); meter_changed = True
            changed = False
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
        elif m.startswith("PA0") and len(m) >= 4 and m[3].isdigit():
            self.state["preamp"] = int(m[3])             # 0 IPO / 1 AMP1 / 2 AMP2
        elif m.startswith("RA0") and len(m) >= 4:
            self.state["att"] = 1 if m[3] == "1" else 0
        elif m.startswith("LK") and len(m) >= 3 and m[2] in ("0", "1"):
            self.state["lock"] = (m[2] == "1")
        elif m.startswith("NA0") and len(m) >= 4:                  # narrow/wide -> FIL1/FIL2
            self.state["narrow"] = 1 if m[3] == "1" else 0
            self.state["filter"] = 2 if m[3] == "1" else 1
            self.state["filter_name"] = "FIL2" if m[3] == "1" else "FIL1"
        elif m.startswith("CT0") and len(m) >= 4 and m[3].isdigit():   # CTCSS/DCS mode
            self.state["tone_mode"] = int(m[3])
        elif m.startswith("CN0") and len(m) >= 7 and m[4:7].isdigit():  # CN0<p2><nnn> tone/DCS number
            if m[3] == "0":
                self.state["tone_freq"] = int(m[4:7])
            elif m[3] == "1":
                self.state["dcs_code"] = int(m[4:7])
        elif m.startswith("OS0") and len(m) >= 4 and m[3] in ("0", "1", "2"):  # repeater shift
            self.state["rpt_shift"] = int(m[3])
        elif m.startswith("SH0") and len(m) >= 5 and m[3:5].isdigit():   # DSP width code (0-21)
            self.state["width"] = int(m[3:5])
        elif m.startswith("CO0") and len(m) >= 8 and m[4:8].isdigit():   # CONTOUR/APF: CO0<p2><nnnn>
            p2, nnnn = m[3], int(m[4:8])
            if p2 == "0":   self.state["contour"] = 1 if nnnn else 0
            elif p2 == "1": self.state["contour_freq"] = nnnn
            elif p2 == "2": self.state["apf"] = 1 if nnnn else 0
            elif p2 == "3": self.state["apf_freq"] = nnnn
        elif m.startswith("ML0") and len(m) >= 6 and m[3:6].isdigit():   # monitor on/off
            self.state["mon"] = 1 if int(m[3:6]) else 0
        elif m.startswith("ML1") and len(m) >= 6 and m[3:6].isdigit():   # monitor level 0-100
            self.state["mon_level"] = _unscale(int(m[3:6]), 100)
        elif m.startswith("PR0") and len(m) >= 4 and m[3].isdigit():     # speech processor on/off
            self.state["comp"] = 1 if m[3] == "2" else 0
        elif m.startswith("PR1") and len(m) >= 4 and m[3].isdigit():     # parametric mic EQ on/off
            self.state["param_eq"] = 1 if m[3] == "2" else 0
        elif m.startswith("TS") and len(m) >= 3 and m[2] in ("0", "1"):  # TXW
            self.state["txw"] = int(m[2])
        elif m.startswith("BI") and len(m) >= 3 and m[2] in ("0", "1"):  # CW break-in
            self.state["bkin"] = int(m[2])
        elif m.startswith("KR") and len(m) >= 3 and m[2] in ("0", "1"):  # CW keyer on/off
            self.state["keyer"] = int(m[2])
        elif m.startswith("KS") and len(m) >= 5 and m[2:5].isdigit():    # keyer speed (WPM)
            self.state["key_speed"] = int(m[2:5])
        elif m.startswith("KP") and len(m) >= 4 and m[2:4].isdigit():    # CW pitch code (0-75)
            self.state["key_pitch"] = int(m[2:4])
        elif m.startswith("CS") and len(m) >= 3 and m[2] in ("0", "1"):  # CW spot
            self.state["spot"] = int(m[2])
        elif m.startswith("SC") and len(m) >= 3 and m[2] in ("0", "1", "2"):  # scan
            self.state["scan"] = int(m[2])
        elif m.startswith("FS") and len(m) >= 3 and m[2] in ("0", "1"):  # fast tuning step
            self.state["fast"] = int(m[2])
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
        elif meter_changed:
            self._emit_meter()           # meter-only: tiny frame, no full-state re-serialize

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
                 "PA0;", "RA0;", "LK;", "NA0;", "FT;", "RT;", "AC;",
                 "CT0;", "CN00;", "CN01;", "OS0;",     # FM Tone/DCS mode + tone/DCS number + shift
                 "SH0;", "CO00;", "CO01;", "CO02;", "CO03;",   # width + contour/APF
                 "ML0;", "ML1;", "PR0;", "PR1;", "TS;",        # monitor + processor/param-EQ + TXW
                 "BI;", "KR;", "KS;", "KP;", "CS;",            # CW break-in/keyer/speed/pitch/spot
                 "SC;", "FS;")                                  # scan + fast step

    _CMD_DT = 0.02                               # inter-command pacing; 0.02 is the FT-991A CAT floor

    def _poll(self) -> None:
        # The FT-991A is CAT-bound: every read is a query/reply round-trip with a small
        # inter-command gap. So poll the METER (SM0 + the selected TX meter) every cycle, send
        # freq/mode/PTT a few times a second, and round-robin ONE panel setting per cycle so a
        # full-panel refresh never blocks the meter for a long burst. ~12 Hz S-meter.
        cyc = 0
        while not self._poll_stop.is_set():
            # stuck-TX failsafe (every cycle): never leave the radio keyed past the time-out.
            if (self._ptt_deadline and time.monotonic() >= self._ptt_deadline
                    and self.state.get("ptt")):
                self.set_ptt(False)
            # CW-TX indicator auto-clear: the rig's keyer plays a bounded message and returns
            # to RX on its own; clear the 'transmitting' flag once it's done.
            if self._cw_deadline and time.monotonic() >= self._cw_deadline:
                self.stop_cw()
            self._send("SM0;")                       # S-meter — every cycle (the fast needle)
            time.sleep(self._CMD_DT)
            mkey = self.state.get("meter", "S")      # selected TX meter (S comes from SM0)
            if mkey in YAESU_METER and not self._poll_stop.is_set():
                self._send(f"RM{YAESU_METER[mkey]};")
                time.sleep(self._CMD_DT)
            if cyc % 4 == 0 and not self._poll_stop.is_set():      # freq / mode / PTT ~3x/s
                for cmd in ("FA;", "MD0;", "TX;"):
                    if self._poll_stop.is_set():
                        break
                    self._send(cmd)
                    time.sleep(self._CMD_DT)
            if not self._poll_stop.is_set():
                self._poll_panel_rr()                # one panel setting per cycle (~2 s full refresh)
            cyc += 1
            self._poll_stop.wait(self._CMD_DT)       # short gap between cycles

    def _poll_panel_rr(self) -> None:
        """Send ONE panel setting per call, round-robin through _SETTINGS, so the full panel
        re-reads gradually without a burst that would stall the meter."""
        n = len(self._SETTINGS)
        i = getattr(self, "_panel_i", 0) % n
        self._panel_i = i + 1
        cmd = self._SETTINGS[i]
        if cmd == "NA0;" and self.state.get("mode_name") == "C4FM":
            return                                   # FT-991A hangs on NA0; while in C4FM
        self._send(cmd)
        time.sleep(self._CMD_DT)

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
        # S rides the S-meter (SM0); the others are TX meters read via RM. Reset the bar
        # and kick an immediate read so it doesn't show a stale value from the last meter.
        self.state["meter_val"] = self.state.get("smeter", 0) if key == "S" else 0
        if key in YAESU_METER:
            self._send(f"RM{YAESU_METER[key]};")
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
        elif sub == 0x15:                             # monitor level ML1 000-100
            key, cmd = "mon_level", f"ML1{_scale(value, 100):03d};"
        # vox/pbt2 have no safe FT-991A mapping (vox arms TX) -> ignored.
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
        elif name == "mon":
            cmd = f"ML0{1 if on else 0:03d};"         # monitor on/off (ML0 000/001)
        # "vox": arms TX off mic audio -> never toggled here (safety)
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

    def set_preamp(self, level) -> None:
        level = max(0, min(2, int(level)))           # 0 = IPO, 1 = AMP1, 2 = AMP2
        self.state["preamp"] = level
        self._send(f"PA0{level};")
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

    def set_narrow(self, on: bool) -> None:
        # NAR/WIDE — the IF filter narrow toggle (NA), i.e. the front-panel NAR key.
        self.state["narrow"] = 1 if on else 0
        self.state["filter"] = 2 if on else 1
        self.state["filter_name"] = "FIL2" if on else "FIL1"
        if self.state.get("mode_name") != "C4FM":          # NA0 hangs the rig in C4FM
            self._send(f"NA0{1 if on else 0};")
        self._emit_state()

    # -- FM Tone / DCS + repeater shift (FM-family modes) --------------------
    def set_tone_mode(self, v: int) -> None:
        v = max(0, min(4, int(v)))                          # CT P2: 0 OFF/1 TSQL/2 TONE/3 DCS/4 DCS-ENC
        self.state["tone_mode"] = v
        self._send(f"CT0{v};")
        self._emit_state()

    def set_tone_freq(self, idx: int) -> None:
        idx = max(0, min(49, int(idx)))                     # CTCSS tone number (Table 1)
        self.state["tone_freq"] = idx
        self._send(f"CN00{idx:03d};")
        self._emit_state()

    def set_dcs_code(self, idx: int) -> None:
        idx = max(0, min(103, int(idx)))                    # DCS code number (Table 2)
        self.state["dcs_code"] = idx
        self._send(f"CN01{idx:03d};")
        self._emit_state()

    def set_rpt_shift(self, v: int) -> None:
        v = max(0, min(2, int(v)))                          # OS P2: 0 simplex/1 +shift/2 -shift (FM only)
        self.state["rpt_shift"] = v
        if self.state.get("mode_name") in ("FM", "FM-N", "DATA-FM", "C4FM"):
            self._send(f"OS0{v};")
        self._emit_state()

    # -- DSP filter: WIDTH / CONTOUR / APF ------------------------------------
    def set_width(self, code: int) -> None:
        code = max(0, min(21, int(code)))                   # SH bandwidth code (per-mode table)
        self.state["width"] = code
        self._send(f"SH0{code:02d};")
        self._emit_state()

    def set_contour(self, on: bool) -> None:
        self.state["contour"] = 1 if on else 0
        self._send(f"CO000001;" if on else "CO000000;")     # CO P2=0 on/off
        self._emit_state()

    def set_contour_freq(self, hz: int) -> None:
        hz = max(10, min(3200, int(hz)))
        self.state["contour_freq"] = hz
        self._send(f"CO01{hz:04d};")                        # CO P2=1 freq 0010-3200
        self._emit_state()

    def set_apf(self, on: bool) -> None:
        self.state["apf"] = 1 if on else 0
        self._send(f"CO020001;" if on else "CO020000;")     # CO P2=2 APF on/off
        self._emit_state()

    def set_apf_freq(self, v: int) -> None:
        v = max(0, min(50, int(v)))                         # CO P2=3: 0-50 = -250..+250 Hz
        self.state["apf_freq"] = v
        self._send(f"CO03{v:04d};")
        self._emit_state()

    # -- TX ops: TXW / quick-split / parametric mic EQ -----------------------
    def set_txw(self, on: bool) -> None:
        self.state["txw"] = 1 if on else 0                  # listen on the TX freq during split
        self._send(f"TS{1 if on else 0};")
        self._emit_state()

    def set_param_eq(self, on: bool) -> None:
        self.state["param_eq"] = 1 if on else 0
        self._send(f"PR1{2 if on else 1};")                 # PR P1=1 parametric mic EQ (1 off / 2 on)
        self._emit_state()

    def set_quick_split(self) -> None:
        if self.state.get("connected"):
            self._send("QS;")                               # one-touch split (VFO-B = VFO-A + offset)

    # -- CW operating controls (CW modes) ------------------------------------
    def set_bkin(self, on: bool) -> None:
        self.state["bkin"] = 1 if on else 0
        self._send(f"BI{1 if on else 0};")
        self._emit_state()

    def set_keyer(self, on: bool) -> None:
        self.state["keyer"] = 1 if on else 0
        self._send(f"KR{1 if on else 0};")
        self._emit_state()

    def set_key_speed(self, wpm: int) -> None:
        wpm = max(4, min(60, int(wpm)))
        self.state["key_speed"] = wpm
        self._send(f"KS{wpm:03d};")
        self._emit_state()

    def set_key_pitch(self, code: int) -> None:
        code = max(0, min(75, int(code)))                   # KP 00-75 = 300-1050 Hz (10 Hz)
        self.state["key_pitch"] = code
        self._send(f"KP{code:02d};")
        self._emit_state()

    def set_spot(self, on: bool) -> None:
        self.state["spot"] = 1 if on else 0
        self._send(f"CS{1 if on else 0};")                  # CW spot tone (RX zero-beat aid)
        self._emit_state()

    def set_zero_in(self) -> None:
        if self.state.get("mode_name") in ("CW-USB", "CW-LSB"):
            self._send("ZI;")                               # auto zero-in on the received CW signal

    # -- operating: scan / fast tuning step ----------------------------------
    def set_scan(self, direction: int) -> None:
        direction = max(0, min(2, int(direction)))          # 0 off / 1 up / 2 down
        self.state["scan"] = direction
        self._send(f"SC{direction};")
        self._emit_state()

    def set_fast(self, on: bool) -> None:
        self.state["fast"] = 1 if on else 0
        self._send(f"FS{1 if on else 0};")
        self._emit_state()

    def set_tuner(self, on: bool) -> None:
        # Switch the internal ATU in (AC001) or out of line (AC000). This does NOT transmit.
        self.state["tuner"] = 1 if on else 0
        self._send("AC001;" if on else "AC000;")
        self._emit_state()

    def tune_atu(self) -> None:
        """Start an antenna-tuner cycle (AC002). Operator-triggered — this keys a brief carrier
        to tune (the same TX boundary as PTT). The radio self-limits the cycle, and the hardware
        TX time-out set on connect is the backstop if it ever sticks. Tuning puts the ATU in line."""
        if not self.state.get("connected"):
            return
        self.state["tuner"] = 1
        self._send("AC002;")
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

    # -- CW message transmit (operator-triggered) ----------------------------
    def send_cw(self, text: str, wpm: int = 18) -> None:
        """Transmit a typed CW message by host-timed keying of the DTR line on the
        sibling (Standard) USB port — the proven FT-991A method (N1MM/fldigi/cwdaemon).
        Operator-triggered, one bounded message per call; the rig shapes the CW envelope."""
        if getattr(self.profile, "cw_send", "") != "line" or not self.state.get("connected"):
            return
        if not (self._key_port and self._key_port.is_open):
            return                                       # CW key port unavailable
        if self.state.get("mode_name") not in ("CW-USB", "CW-LSB"):
            return                                       # only meaningful in CW (the UI guards too)
        if self.state.get("cw_tx") or (self._cw_thread and self._cw_thread.is_alive()):
            return                                       # re-entrancy guard: one message keys at a time
        msg = "".join(c for c in str(text).upper() if c in CW_YAESU_CHARS)[:80]
        if not msg.strip():
            return
        wpm = max(4, min(60, int(wpm)))
        abort = threading.Event()
        self._cw_abort = abort
        self._cw_deadline = time.monotonic() + cw_duration(msg, wpm) + 3.0   # failsafe backstop
        self.state["cw_tx"] = True
        self._emit_state()
        # Key on a thread: timing is host-driven and must not block the asyncio loop.
        t = threading.Thread(target=self._cw_key_seq, args=(msg, wpm, abort),
                             daemon=True, name="yaesu-cw")
        self._cw_thread = t
        t.start()

    def _cw_key_seq(self, msg: str, wpm: int, abort: threading.Event) -> None:
        kp = self._key_port
        seq = cw_elements(msg, wpm)
        if _WINMM:
            try: _WINMM.timeBeginPeriod(1)
            except Exception: pass
        try:
            for down, dur in seq:
                if abort.is_set() or self._poll_stop.is_set() or not self.state.get("connected"):
                    break
                if kp:
                    kp.key(down)                         # DTR: True = key down (element)
                _sleep_until(time.perf_counter() + dur, abort)
        finally:
            if kp:
                kp.key(False)                            # ALWAYS end key-up
            if _WINMM:
                try: _WINMM.timeEndPeriod(1)
                except Exception: pass
            self._cw_deadline = None
            if self.state.get("cw_tx"):
                self.state["cw_tx"] = False
                self._emit_state()

    def _key_up(self) -> None:
        if self._key_port:
            self._key_port.key(False)

    def stop_cw(self) -> None:
        """Stop an in-progress CW message and force the key line up immediately."""
        if self._cw_abort:
            self._cw_abort.set()
        self._key_up()                                   # DTR low (key up)
        self._cw_deadline = None
        if self.state.get("cw_tx"):
            self.state["cw_tx"] = False
            self._emit_state()

    # -- SET menus (Yaesu EX) ------------------------------------------------
    def get_menu(self, num) -> None:
        it = self._menu_index.get(int(num))
        if it is not None:
            self._send(menu_engine.yaesu_read_cmd(it))

    def read_menu_group(self, group) -> None:
        """Lazily read one menu category (sent spaced on a thread so it never blocks the
        WS loop or floods the fast freq/mode/S-meter poll)."""
        items = [it for it in (getattr(self.profile, "menu", None) or []) if it.group == group]
        if not items:
            return

        def _run():
            for it in items:
                if self._poll_stop.is_set() or self._tp is None:   # bail if disconnected mid-read
                    break
                self._send(menu_engine.yaesu_read_cmd(it))
                time.sleep(0.03)

        threading.Thread(target=_run, daemon=True, name="yaesu-menu-read").start()

    def set_menu(self, num, value) -> None:
        it = self._menu_index.get(int(num))
        if it is None or it.readonly or not self._menu_write_allowed(it):
            return
        try:
            cmd = menu_engine.yaesu_encode(it, value)
        except menu_engine.MenuError:
            return
        self._send(cmd)
        self._send(menu_engine.yaesu_read_cmd(it))    # confirm-read -> UI reflects the radio

    def _menu_write_allowed(self, item) -> bool:
        # The app owns CAT RTS (033) + PC KEYING (060) for CW line-keying; a menu write
        # could leave the rig armed to key on a line we don't control, so block those while
        # we manage keying. They stay readable; the UI marks them app-managed.
        if getattr(self.profile, "cw_send", "") == "line" and item.num in (33, 60):
            return False
        return True

    def _handle_ex_reply(self, m: str) -> None:
        w = self._ex_width
        if len(m) < 2 + w or not m[2:2 + w].isdigit():
            return
        it = self._menu_index.get(int(m[2:2 + w]))
        if it is None:
            return
        val = menu_engine.yaesu_decode(it, m)
        if val is None:
            return
        self._menu_vals[it.num] = val
        self._emit_menu({it.num: val})

    def _emit_menu(self, values: dict) -> None:
        if self.on_menu:
            try:
                self.on_menu(values)
            except Exception:
                pass

    def write_audio(self, pcm: bytes) -> None:
        return                                        # no USB-audio path here

    # -- not applicable over FT-991A CAT -------------------------------------
    def _noop(self, *a, **k) -> None:
        return

    set_mnotch_w = set_tbw = set_duplex = _noop
    set_span = set_scope_mode = _noop

    _METER_FIELDS = ("smeter", "smeter_s", "meter_val")

    def _state_sig(self) -> str:
        out = {}
        for k, v in self.state.items():
            if k in self._METER_FIELDS:
                continue
            if isinstance(v, dict):
                v = {kk: vv for kk, vv in v.items() if kk not in self._METER_FIELDS}
            out[k] = v
        return json.dumps(out, sort_keys=True, default=str)

    def _emit_state(self) -> None:
        if not self.on_state:
            return
        sig = self._state_sig()                  # skip redundant full frames (panel re-reads)
        if sig == self._last_sig:
            return
        self._last_sig = sig
        try:
            self.on_state(self.state)
        except Exception:
            pass

    def _emit_meter(self) -> None:
        if self.on_meter:
            s = self.state
            try:
                self.on_meter(s.get("meter", "S"), s.get("meter_val", 0),
                              s.get("smeter", 0), s.get("smeter_s", "S0"))
            except Exception:
                pass

"""
Yaesu CAT (serial) radio — a COM-only sibling of the Icom CI-V ``Radio`` used
for the FT-991A. Same public surface the server calls, but speaks Yaesu CAT:
2-letter ASCII commands + fixed-width params, each terminated by ';'.

The FT-991A exposes NO band scope / spectrum over CAT (its scope is display
only), so there is no waterfall — only frequency / mode / S-meter readout and
frequency/mode/band control.

HARD SAFETY RULE (same as the Icom path): never key the transmitter. We never
send TX1; / MX1;. The TX; read is used for status display only. set_ptt() is a
deliberate no-op.
"""
from __future__ import annotations

import threading
import time
from typing import Optional

from . import civ, profiles
from .radio import fresh_state, smeter_label

# our mode-button name -> Yaesu MD command code (P2 char). Only the subset the
# FT-991A profile exposes (all of which also exist in civ.MODE_CODES).
YAESU_MD = {
    "LSB": "1", "USB": "2", "CW": "3", "FM": "4", "AM": "5",
    "RTTY": "6", "CW-R": "7", "RTTY-R": "9",
}
# MD reply code -> readable name (includes codes we don't expose as buttons)
MD_DECODE = {
    "1": "LSB", "2": "USB", "3": "CW", "4": "FM", "5": "AM",
    "6": "RTTY", "7": "CW-R", "8": "DATA-L", "9": "RTTY-R",
    "A": "DATA-FM", "B": "FM-N", "C": "DATA-U", "D": "AM-N", "E": "C4FM",
}


class YaesuRadio:
    def __init__(self, profile=None) -> None:
        self.profile = profile or profiles.PROFILES.get("ft991a")
        self._tp = None
        self._lock = threading.Lock()
        self._poll_thread: Optional[threading.Thread] = None
        self._poll_stop = threading.Event()
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
        # prime the readout
        for cmd in ("ID;", "IF;", "FA;", "MD0;", "SM0;"):
            self._send(cmd)
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
                self._tp.stop()
            except Exception:
                pass
            self._tp = None
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

    def _parse(self, msg: str) -> None:
        changed = False
        if msg.startswith("FA") and len(msg) >= 11:
            try:
                hz = int(msg[2:11]); self._set_freq_state(hz); changed = True
            except ValueError:
                pass
        elif msg.startswith("MD0") and len(msg) >= 4:
            name = MD_DECODE.get(msg[3].upper())
            if name:
                self.state["mode_name"] = name
                self.state["main"]["mode_name"] = name
                changed = True
        elif msg.startswith("SM0") and len(msg) >= 6:
            try:
                self._set_smeter(int(msg[3:6])); changed = True
            except ValueError:
                pass
        elif msg.startswith("IF") and len(msg) >= 14:
            # info string: P2 (VFO-A freq) is the 9 chars at index 5..13 (mode comes
            # from MD0; so we only take the frequency here)
            try:
                hz = int(msg[5:14]); self._set_freq_state(hz); changed = True
            except ValueError:
                pass
        elif msg.startswith("TX") and len(msg) >= 3:
            # status only; we never key from the app. 1=CAT-keyed, 2=radio/PTT-keyed.
            self.state["ptt"] = msg[2] in ("1", "2"); changed = True
        if changed:
            self._emit_state()

    def _set_freq_state(self, hz: int) -> None:
        self.state["freq"] = hz
        self.state["main"]["freq"] = hz

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

    def _poll(self) -> None:
        while not self._poll_stop.is_set():
            for cmd in ("FA;", "MD0;", "SM0;", "TX;"):
                if self._poll_stop.is_set():
                    break
                self._send(cmd)
                time.sleep(0.03)
            for _ in range(3):                       # ~0.3 s between full cycles
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
        self._emit_state()

    def set_band(self, band: str) -> None:
        d = self.profile.band_default(band)
        if d:
            self.set_freq(d)

    select_band = set_band

    def set_meter(self, key: str) -> None:
        self.state["meter"] = key
        self._emit_state()

    def set_ptt(self, tx: bool) -> None:
        # HARD RULE: never key TX over CAT. Status comes from the TX; poll only.
        return

    def write_audio(self, pcm: bytes) -> None:
        return                                        # no USB-audio path here

    # -- everything else the server may dispatch is N/A over FT-991A CAT -----
    def _noop(self, *a, **k) -> None:
        return

    set_filter = set_preamp = set_att = set_lock = set_rx_func = set_agc = _noop
    set_mnotch_w = set_tbw = set_rit = set_rit_freq = set_split = set_duplex = _noop
    select_vfo = set_level = set_span = set_scope_mode = _noop

    def _emit_state(self) -> None:
        if self.on_state:
            try:
                self.on_state(self.state)
            except Exception:
                pass

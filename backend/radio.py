"""
High-level radio controller.

Owns a Transport, decodes the inbound CI-V stream into live state + scope
sweeps, and exposes button-level actions used by the web UI. Knows nothing
about asyncio; the server bridges thread -> websocket via callbacks.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from . import civ
from .transport import Transport

# Per-mode IF filter bandwidths (Hz) for the FIL1/2/3 slots. Approximate the
# IC-9700 defaults; only used to draw the passband shade over the scope.
FILTER_BW = {
    "LSB":  {1: 3000, 2: 2400, 3: 1800},
    "USB":  {1: 3000, 2: 2400, 3: 1800},
    "CW":   {1: 1200, 2: 500,  3: 250},
    "CW-R": {1: 1200, 2: 500,  3: 250},
    "RTTY": {1: 2400, 2: 500,  3: 250},
    "RTTY-R": {1: 2400, 2: 500, 3: 250},
    "AM":   {1: 9000, 2: 6000, 3: 3000},
    "FM":   {1: 15000, 2: 7000, 3: 7000},
    "DV":   {1: 6250, 2: 6250, 3: 6250},
    "DD":   {1: 130000, 2: 130000, 3: 130000},
}

# A sensible default frequency per band button (Hz)
BAND_FREQ = {"144": 144_200_000, "430": 432_100_000, "1200": 1_296_100_000}

# CI-V 1A 05 data numbers for MOD Input (model-specific). On LAN connect we route
# TX modulation to LAN so the browser mic actually transmits; restored on disconnect.
MOD_DATAOFF = (0x01, 0x15)     # DATA OFF MOD setting (modulation source for voice modes)
LAN_MOD_LEVEL = (0x01, 0x14)   # LAN MOD Level
MOD_LAN = 0x05                 # value that selects the LAN modulation source

ScopeCb = Callable[[civ.ScopeSweep], None]
StateCb = Callable[[dict], None]


def smeter_label(level: int) -> str:
    if level <= 120:
        s = round(level * 9 / 120)
        return f"S{s}"
    db = round((level - 120) / (241 - 120) * 60)
    db = (db // 10) * 10
    return f"S9+{db}"


class Radio:
    def __init__(self) -> None:
        self._tp: Optional[Transport] = None
        self._reader = civ.FrameReader()
        self._scope = civ.ScopeAssembler()
        self._lock = threading.Lock()
        self._poll_thread: Optional[threading.Thread] = None
        self._poll_stop = threading.Event()

        self.on_scope: Optional[ScopeCb] = None
        self.on_state: Optional[StateCb] = None
        self.on_audio: Optional[Callable[[bytes], None]] = None
        self._modsrc_orig: Optional[int] = None     # original DATA OFF MOD (for restore)
        self._lanmod_orig: Optional[int] = None      # original LAN MOD Level (for restore)
        self._mod_managed = False

        self.state = {
            "connected": False,
            "transport": None,
            "freq": 144_200_000,
            "mode": 0x01,
            "mode_name": "USB",
            "filter": 1,
            "filter_name": "FIL1",
            "filter_bw": 3000,
            "span": 50_000,
            "span_label": civ.SPAN_LABELS.get(50_000, ""),
            "scope_center": True,
            "smeter": 0,
            "smeter_s": "S0",
            "af": 128,
            "rf": 200,
            "sql": 0,
            "rfpwr": 0,
            "ptt": False,
            "audio": False,
        }

    # -- audio passthrough (LAN only) ---------------------------------------
    def _dispatch_audio(self, pcm: bytes) -> None:
        if self.on_audio:
            self.on_audio(pcm)

    def write_audio(self, pcm: bytes) -> None:
        if self._tp is not None and getattr(self._tp, "supports_audio", False):
            self._tp.write_audio(pcm)

    # -- connection ----------------------------------------------------------
    def connect(self, transport: Transport) -> None:
        self.disconnect()
        self._tp = transport
        self._reader = civ.FrameReader()
        self._scope = civ.ScopeAssembler()
        if getattr(transport, "supports_audio", False):
            transport.on_audio = self._dispatch_audio
        try:
            transport.start(self._on_bytes)
        except Exception:
            self._tp = None
            raise
        self.state["connected"] = True
        self.state["transport"] = transport.name
        self.state["audio"] = getattr(transport, "supports_audio", False)

        # enable the scope output (needs BOTH on/off and data-output on)
        self.set_scope_mode(self.state["scope_center"])
        self.set_span(self.state["span"])
        self._write(civ.build(0x27, 0x10, b"\x01"))     # scope ON
        self._write(civ.build(0x27, 0x11, b"\x01"))     # data output ON

        # initial reads
        self._write(civ.build(0x03))                     # freq
        self._write(civ.build(0x04))                     # mode

        self._poll_stop.clear()
        self._poll_thread = threading.Thread(target=self._poll, daemon=True,
                                             name="civ-poll")
        self._poll_thread.start()
        self._emit_state()

        # safety default: start every band at 0% TX power
        threading.Thread(target=self._zero_power_all_bands, daemon=True,
                         name="civ-pwr0").start()

        # over LAN, route TX modulation to the LAN input so the browser mic works
        if getattr(transport, "supports_audio", False):
            threading.Thread(target=self._setup_lan_mod, daemon=True,
                             name="civ-lanmod").start()

    def _setup_lan_mod(self) -> None:
        """Route TX modulation to LAN so the browser mic actually transmits.
        Reads the current DATA OFF MOD + LAN MOD Level first, so disconnect can
        restore them and leave local mic operation untouched."""
        self._write(civ.build(0x1A, 0x05, bytes(MOD_DATAOFF)))     # read DATA OFF MOD
        self._write(civ.build(0x1A, 0x05, bytes(LAN_MOD_LEVEL)))   # read LAN MOD Level
        time.sleep(0.6)
        if not self.state["connected"]:
            return
        self._write(civ.build(0x1A, 0x05, bytes(MOD_DATAOFF) + bytes([MOD_LAN])))   # source -> LAN
        if self._lanmod_orig == 0:        # if the LAN MOD level was 0 there'd be no audio
            self._write(civ.build(0x1A, 0x05, bytes(LAN_MOD_LEVEL) + civ.level_to_bcd(128)))
        self._mod_managed = True

    def _zero_power_all_bands(self) -> None:
        """Set RF power to 0 on 144/430/1200, then restore the original freq.
        RF power is per-band and CI-V only addresses the current band, so we
        briefly visit each band. Receive-only (no PTT)."""
        self._write(civ.build(0x03))          # ask for the real current freq
        time.sleep(0.5)
        if not self.state["connected"]:
            return
        orig = self.state["freq"]
        for f in (145_000_000, 435_000_000, 1_295_000_000):
            if not self.state["connected"]:
                return
            self._write(civ.build(0x05, None, civ.freq_to_bcd(f)))
            time.sleep(0.1)
            self._write(civ.build(0x14, 0x0A, civ.level_to_bcd(0)))
            time.sleep(0.1)
        self._write(civ.build(0x05, None, civ.freq_to_bcd(orig)))   # restore
        self.state["freq"] = orig
        self.state["rfpwr"] = 0
        self._emit_state()

    def disconnect(self) -> None:
        self._poll_stop.set()
        if self._poll_thread:
            self._poll_thread.join(timeout=1.0)
            self._poll_thread = None
        if self._tp:
            try:
                if self._mod_managed and self._modsrc_orig is not None:   # restore MOD Input
                    self._write(civ.build(0x1A, 0x05, bytes(MOD_DATAOFF) + bytes([self._modsrc_orig])))
                    if self._lanmod_orig == 0:
                        self._write(civ.build(0x1A, 0x05, bytes(LAN_MOD_LEVEL) + civ.level_to_bcd(0)))
                self._write(civ.build(0x27, 0x11, b"\x00"))  # stop scope data
                time.sleep(0.15)
            except Exception:
                pass
            self._tp.stop()
            self._tp = None
        self._mod_managed = False
        self._modsrc_orig = None
        self._lanmod_orig = None
        self.state["connected"] = False
        self.state["transport"] = None
        self.state["audio"] = False
        self._emit_state()

    def _write(self, frame: bytes) -> None:
        if self._tp:
            self._tp.write(frame)

    # -- inbound stream ------------------------------------------------------
    def _on_bytes(self, data: bytes) -> None:
        for fr in self._reader.feed(data):
            self._dispatch(fr)

    def _dispatch(self, fr: civ.Frame) -> None:
        c, s, d = fr.cmd, fr.sub, fr.data
        changed = False
        if c == 0x27 and s == 0x00:
            sweep = self._scope.feed(d)
            if sweep and self.on_scope:
                # NOTE: do NOT set state["freq"] from sweep.center_hz. The scope
                # center is the *display* center (filter-center mode offsets it
                # from the carrier by ~1 kHz), so using it as the tuned frequency
                # makes the readout flicker against the 0x03 poll. The tuned freq
                # comes only from 0x03 / 0x00 / our own set_freq; the scope center
                # is carried separately (for the X-axis) in the scope frame.
                if sweep.span_hz:
                    self.state["span"] = sweep.span_hz
                    self.state["span_label"] = civ.SPAN_LABELS.get(sweep.span_hz, "")
                self.on_scope(sweep)
            return
        if c in (0x03, 0x00):                            # freq read / transceive
            f = civ.bcd_to_freq(self._payload(s, d))
            if f and f != self.state["freq"]:
                self.state["freq"] = f
                changed = True
        elif c in (0x04, 0x01):                          # mode read / transceive
            p = self._payload(s, d)
            if p:
                self.state["mode"] = p[0]
                self.state["mode_name"] = civ.MODES.get(p[0], "?")
                if len(p) > 1 and p[1] in civ.FILTERS:
                    self.state["filter"] = p[1]
                    self.state["filter_name"] = civ.FILTERS[p[1]]
                changed = True
        elif c == 0x14:                                  # level read
            val = civ.bcd_to_level(d)
            key = {0x01: "af", 0x02: "rf", 0x03: "sql", 0x0A: "rfpwr"}.get(s)
            if key:
                self.state[key] = val
                changed = True
        elif c == 0x15 and s == 0x02:                    # S-meter
            lvl = civ.bcd_to_level(d)
            self.state["smeter"] = lvl
            self.state["smeter_s"] = smeter_label(lvl)
            changed = True
        elif c == 0x1A and s == 0x05 and len(d) >= 3:    # MOD Input read response
            if d[0] == MOD_DATAOFF[0] and d[1] == MOD_DATAOFF[1] and self._modsrc_orig is None:
                self._modsrc_orig = d[2]
            elif (d[0] == LAN_MOD_LEVEL[0] and d[1] == LAN_MOD_LEVEL[1]
                  and len(d) >= 4 and self._lanmod_orig is None):
                self._lanmod_orig = civ.bcd_to_level(d[2:4])
        if changed:
            self._recalc_filter_bw()
            self._emit_state()

    @staticmethod
    def _payload(sub: Optional[int], data: bytes) -> bytes:
        # for commands without a real sub-command, the parser stuffed the first
        # data byte into `sub`; reconstruct the true payload.
        if sub is None:
            return data
        return bytes([sub]) + data

    def _recalc_filter_bw(self) -> None:
        bw = FILTER_BW.get(self.state["mode_name"], {}).get(self.state["filter"], 2400)
        self.state["filter_bw"] = bw

    # -- polling -------------------------------------------------------------
    def _poll(self) -> None:
        tick = 0
        while not self._poll_stop.is_set():
            self._write(civ.build(0x15, 0x02))           # S-meter
            if tick % 8 == 0:                            # ~1.2s: catch panel changes
                self._write(civ.build(0x03))
                self._write(civ.build(0x04))
            tick += 1
            time.sleep(0.15)

    # -- button-level actions ------------------------------------------------
    def set_freq(self, hz: int) -> None:
        hz = max(0, int(hz))
        self.state["freq"] = hz
        self._write(civ.build(0x05, None, civ.freq_to_bcd(hz)))
        self._emit_state()

    def tune(self, delta_hz: int) -> None:
        self.set_freq(self.state["freq"] + int(delta_hz))

    def set_mode(self, mode_code: int, filt: Optional[int] = None) -> None:
        filt = filt or self.state["filter"]
        self.state["mode"] = mode_code
        self.state["mode_name"] = civ.MODES.get(mode_code, "?")
        self.state["filter"] = filt
        self.state["filter_name"] = civ.FILTERS.get(filt, "FIL1")
        self._recalc_filter_bw()
        self._write(civ.build(0x06, None, bytes([mode_code, filt])))
        self._emit_state()

    def set_filter(self, filt: int) -> None:
        self.set_mode(self.state["mode"], filt)

    def select_vfo(self, code: int) -> None:
        self._write(civ.build(0x07, code))

    def set_level(self, sub: int, value: int) -> None:
        key = {0x01: "af", 0x02: "rf", 0x03: "sql", 0x0A: "rfpwr"}.get(sub)
        if key:
            self.state[key] = max(0, min(255, int(value)))
        self._write(civ.build(0x14, sub, civ.level_to_bcd(value)))
        self._emit_state()

    def set_band(self, band: str) -> None:
        if band in BAND_FREQ:
            self.set_freq(BAND_FREQ[band])

    def set_span(self, span_hz: int) -> None:
        self.state["span"] = span_hz
        self.state["span_label"] = civ.SPAN_LABELS.get(span_hz, "")
        data = bytes([0x00]) + civ.freq_to_bcd(span_hz)
        self._write(civ.build(0x27, 0x15, data))
        self._emit_state()

    def set_scope_mode(self, center: bool) -> None:
        self.state["scope_center"] = center
        self._write(civ.build(0x27, 0x14, bytes([0x00, 0x00 if center else 0x01])))
        self._emit_state()

    def set_ptt(self, tx: bool) -> None:
        self.state["ptt"] = tx
        self._write(civ.build(0x1C, 0x00, bytes([1 if tx else 0])))
        self._emit_state()

    # -- state notify --------------------------------------------------------
    def _emit_state(self) -> None:
        if self.on_state:
            self.on_state(dict(self.state))

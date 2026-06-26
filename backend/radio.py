"""
High-level radio controller.

Owns a Transport, decodes the inbound CI-V stream into live state + scope
sweeps, and exposes button-level actions used by the web UI. Knows nothing
about asyncio; the server bridges thread -> websocket via callbacks.

Model-specific details (CI-V address, bands, modes, filter widths, MOD-Input
numbers, power behaviour) come from a RadioProfile (see profiles.py).
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from . import civ, profiles
from .profiles import RadioProfile
from .transport import Transport

MOD_LAN = 0x05                 # CI-V value selecting the LAN modulation source (all models)
PTT_TIMEOUT = 120              # PTT failsafe: auto-unkey after this many seconds keyed

# CI-V 0x14 level sub-command -> state key
LEVEL_KEYS = {0x01: "af", 0x02: "rf", 0x03: "sql", 0x0A: "rfpwr",
              0x06: "nr_level", 0x12: "nb_level", 0x07: "pbt1", 0x08: "pbt2", 0x0D: "mnotch_pos",
              0x0B: "mic", 0x0E: "comp_level", 0x15: "mon_level", 0x16: "vox_gain"}  # M3 TX
# function on/off toggles: state key -> CI-V 0x16 sub-command
RX_FUNCS = {"nb": 0x22, "nr": 0x40, "anotch": 0x41, "mnotch": 0x48,
            "comp": 0x44, "vox": 0x46, "mon": 0x45}                  # M3 TX adds COMP/VOX/MON
# CI-V 0x16 read sub -> state key (preamp + RX DSP + TX toggles + TBW)
FUNC_KEYS = {0x02: "preamp", 0x22: "nb", 0x40: "nr", 0x41: "anotch",
             0x48: "mnotch", 0x12: "agc", 0x57: "mnotch_w",
             0x44: "comp", 0x46: "vox", 0x45: "mon", 0x58: "tbw"}

ScopeCb = Callable[[civ.ScopeSweep], None]
StateCb = Callable[[dict], None]


def smeter_label(level: int) -> str:
    if level <= 120:
        s = round(level * 9 / 120)
        return f"S{s}"
    db = round((level - 120) / (241 - 120) * 60)
    db = (db // 10) * 10
    return f"S9+{db}"


def fresh_state(p) -> dict:
    """Initial UI/state dict for a profile — shared by the CI-V Radio and the
    Yaesu CAT radio so both speak the same shape to the frontend."""
    sub_freq = p.bands[1].default if (p.dual_watch and len(p.bands) > 1) else p.default_freq
    return {
        "connected": False,
        "transport": None,
        "radio": p.id,
        "radio_name": p.name,
        "freq": p.default_freq,
        "mode": 0x01,
        "mode_name": "USB",
        "filter": 1,
        "filter_name": "FIL1",
        "filter_bw": 2400,
        "span": 500_000,
        "span_label": civ.SPAN_LABELS.get(500_000, ""),
        "scope_center": True,
        "smeter": 0,
        "smeter_s": "S0",
        "af": 128, "rf": 200, "sql": 0, "rfpwr": 0,
        "ptt": False, "ptt_tot": PTT_TIMEOUT,
        "audio": False,
        "dual_watch": p.dual_watch,
        "active_band": "main",
        "main": {"freq": p.default_freq, "mode_name": "USB", "filter_name": "FIL1", "smeter": 0, "smeter_s": "S0"},
        "sub":  {"freq": sub_freq, "mode_name": "FM", "filter_name": "FIL1", "smeter": 0, "smeter_s": "S0"},
        "meter": "S",
        "meter_val": 0,
        "meter_max": civ.METER_MAX,
        "meter_keys": civ.METER_KEYS,
        "preamp": 0, "att": 0, "lock": False,
        "has_preamp": p.has_preamp, "has_att": p.has_att,
        "nb": 0, "nb_level": 0,
        "nr": 0, "nr_level": 0,
        "anotch": 0, "mnotch": 0, "mnotch_w": 0, "mnotch_pos": 128,
        "agc": 2,
        "pbt1": 128, "pbt2": 128,
        "mic": 128, "comp": 0, "comp_level": 128,
        "vox": 0, "vox_gain": 128, "mon": 0, "mon_level": 128,
        "tbw": 0,
        "rit": 0, "rit_freq": 0,
        "split": 0, "duplex": 0,
        "offset": 600000,
        "has_scope": getattr(p, "has_scope", True),
    }


class Radio:
    def __init__(self, profile: Optional[RadioProfile] = None) -> None:
        self.profile = profile or profiles.PROFILES[profiles.DEFAULT_PROFILE_ID]
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
        self._ptt_deadline: Optional[float] = None   # PTT failsafe deadline (monotonic)
        self._suppress_poll = False                  # mute panel reads during the connect band-cycle
        self._switch_at = 0.0                         # monotonic time of the last MAIN/SUB switch

        self.state = self._fresh_state()

    def _fresh_state(self) -> dict:
        return fresh_state(self.profile)

    def _b(self, cmd: int, sub: Optional[int] = None, data: bytes = b"") -> bytes:
        """Build a CI-V frame addressed to this radio's CI-V address."""
        return civ.build(cmd, sub, data, radio_addr=self.profile.civ_addr)

    # -- audio passthrough (LAN only) ---------------------------------------
    def _dispatch_audio(self, pcm: bytes) -> None:
        if self.on_audio:
            self.on_audio(pcm)

    def write_audio(self, pcm: bytes) -> None:
        if self._tp is not None and getattr(self._tp, "supports_audio", False):
            self._tp.write_audio(pcm)

    # -- connection ----------------------------------------------------------
    def connect(self, transport: Transport, profile: Optional[RadioProfile] = None) -> None:
        self.disconnect()
        if profile is not None:
            self.profile = profile
            self.state = self._fresh_state()
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
        self._write(self._b(0x27, 0x10, b"\x01"))       # scope ON
        self._write(self._b(0x27, 0x11, b"\x01"))       # data output ON

        self._write(self._b(0x03))                       # read freq
        self._write(self._b(0x04))                       # read mode
        for sub in (0x02, 0x22, 0x40, 0x41, 0x48, 0x12, 0x57,
                    0x44, 0x45, 0x46, 0x58):             # preamp + RX-DSP + (M3) COMP/MON/VOX/TBW
            self._write(self._b(0x16, sub))
        for sub in (0x06, 0x12, 0x07, 0x08, 0x0D,
                    0x0B, 0x0E, 0x15, 0x16):             # RX levels + (M3) MIC/COMP/MON/VOX
            self._write(self._b(0x14, sub))
        self._write(self._b(0x21, 0x00))                 # RIT frequency
        self._write(self._b(0x21, 0x01))                 # RIT on/off
        self._write(self._b(0x0F))                       # split / duplex
        self._write(self._b(0x0C))                       # duplex offset

        self._poll_stop.clear()
        self._poll_thread = threading.Thread(target=self._poll, daemon=True, name="civ-poll")
        self._poll_thread.start()
        self._emit_state()

        threading.Thread(target=self._zero_power, daemon=True, name="civ-pwr0").start()
        if getattr(transport, "supports_audio", False):     # LAN: route TX modulation to LAN
            threading.Thread(target=self._setup_lan_mod, daemon=True, name="civ-lanmod").start()

    def _setup_lan_mod(self) -> None:
        """Route TX modulation to LAN so the browser mic actually transmits.
        Reads the current DATA OFF MOD + LAN MOD Level first so disconnect can
        restore them, leaving local mic operation untouched."""
        dataoff, level = self.profile.mod_dataoff, self.profile.lan_mod_level
        self._write(self._b(0x1A, 0x05, bytes(dataoff)))      # read DATA OFF MOD
        self._write(self._b(0x1A, 0x05, bytes(level)))        # read LAN MOD Level
        # Wait (bounded) for the read replies. Only take over the MOD source once
        # we've captured the original value, so disconnect can always restore it
        # exactly — if the read is lost we leave the radio's MOD untouched rather
        # than forcing LAN and being unable to put it back.
        for _ in range(15):
            if self._modsrc_orig is not None or not self.state["connected"]:
                break
            time.sleep(0.1)
        if not self.state["connected"] or self._modsrc_orig is None:
            return
        self._write(self._b(0x1A, 0x05, bytes(dataoff) + bytes([MOD_LAN])))   # source -> LAN
        if self._lanmod_orig == 0:        # otherwise there'd be no audio
            self._write(self._b(0x1A, 0x05, bytes(level) + civ.level_to_bcd(128)))
        self._mod_managed = True

    def _zero_power(self) -> None:
        """Safety default: start at 0% TX power. RF power is per-band on multi-band
        radios (e.g. IC-9700), so visit each band; otherwise a single set."""
        time.sleep(0.5)
        if not self.state["connected"]:
            return
        bands = self.profile.power_zero_bands
        if not bands:
            self._write(self._b(0x14, 0x0A, civ.level_to_bcd(0)))
        else:
            orig = self.state["freq"]                     # already read at connect
            self._suppress_poll = True                    # mute panel reads during the band-cycle
            try:
                for f in bands:
                    if not self.state["connected"]:
                        return
                    self._write(self._b(0x05, None, civ.freq_to_bcd(f)))
                    time.sleep(0.1)
                    self._write(self._b(0x14, 0x0A, civ.level_to_bcd(0)))
                    time.sleep(0.1)
                self._write(self._b(0x05, None, civ.freq_to_bcd(orig)))   # restore freq
                self.state["freq"] = orig
                self.state[self.state["active_band"]]["freq"] = orig
            finally:
                self._suppress_poll = False
        self.state["rfpwr"] = 0
        self._emit_state()

    def disconnect(self) -> None:
        self._poll_stop.set()
        if self._poll_thread:
            self._poll_thread.join(timeout=1.0)
            self._poll_thread = None
        if self._tp:
            try:
                if self.state.get("ptt"):                    # never leave keyed
                    self._write(self._b(0x1C, 0x00, b"\x00"))
                if self.state.get("vox"):                    # never leave VOX able to auto-key
                    self._write(self._b(0x16, 0x46, b"\x00"))
                if self._mod_managed and self._modsrc_orig is not None:   # restore MOD Input
                    self._write(self._b(0x1A, 0x05, bytes(self.profile.mod_dataoff) + bytes([self._modsrc_orig])))
                    if self._lanmod_orig == 0:
                        self._write(self._b(0x1A, 0x05, bytes(self.profile.lan_mod_level) + civ.level_to_bcd(0)))
                self._write(self._b(0x27, 0x11, b"\x00"))    # stop scope data
                time.sleep(0.15)
            except Exception:
                pass
            self._tp.stop()
            self._tp = None
        self._mod_managed = False
        self._modsrc_orig = None
        self._lanmod_orig = None
        self._ptt_deadline = None
        self.state["connected"] = False
        self.state["transport"] = None
        self.state["audio"] = False
        self.state["ptt"] = False
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
                # Do NOT set state["freq"] from sweep.center_hz: the scope center is
                # the *display* center (filter-center offsets it from the carrier),
                # so it would flicker against the 0x03 poll. Tuned freq comes only
                # from 0x03 / 0x00 / set_freq; scope center rides in the scope frame.
                if sweep.span_hz:
                    self.state["span"] = sweep.span_hz
                    self.state["span_label"] = civ.SPAN_LABELS.get(sweep.span_hz, "")
                self.on_scope(sweep)
            return
        if c in (0x03, 0x00):                            # freq read / transceive
            if time.monotonic() - self._switch_at < 0.3:
                return                                   # ignore stale reads right after a band switch
            f = civ.bcd_to_freq(self._payload(s, d))
            if f and f != self.state["freq"]:
                self.state["freq"] = f
                changed = True
        elif c in (0x04, 0x01):                          # mode read / transceive
            if time.monotonic() - self._switch_at < 0.3:
                return
            p = self._payload(s, d)
            if p:
                self.state["mode"] = p[0]
                self.state["mode_name"] = civ.MODES.get(p[0], "?")
                if len(p) > 1 and p[1] in civ.FILTERS:
                    self.state["filter"] = p[1]
                    self.state["filter_name"] = civ.FILTERS[p[1]]
                changed = True
        elif c == 0x14:                                  # level read
            key = LEVEL_KEYS.get(s)
            if key:
                self.state[key] = civ.bcd_to_level(d)
                changed = True
        elif c == 0x15:                                  # multi-meter
            lvl = civ.bcd_to_level(d)
            if s == 0x02:                                # S-meter (active band)
                ab = self.state["active_band"]
                self.state["smeter"] = lvl
                self.state["smeter_s"] = smeter_label(lvl)
                self.state[ab]["smeter"] = lvl
                self.state[ab]["smeter_s"] = smeter_label(lvl)
                if self.state["meter"] == "S":
                    self.state["meter_val"] = lvl
                changed = True
            elif civ.METER_SUBS.get(self.state["meter"]) == s:
                self.state["meter_val"] = lvl
                changed = True
        elif c == 0x07 and s == 0xD2:                    # main-band selection state (01 = MAIN operating)
            if d:
                self.state["active_band"] = "main" if d[-1] == 1 else "sub"
                changed = True
        elif c == 0x11:                                  # attenuator (value rides in the sub byte)
            if s is not None:
                self.state["att"] = s
                changed = True
        elif c == 0x16 and d:                            # functions (preamp / lock / RX DSP)
            if s == 0x50:                                # dial lock (bool)
                self.state["lock"] = bool(d[0]); changed = True
            else:
                key = FUNC_KEYS.get(s)
                if key:
                    self.state[key] = d[0]; changed = True
        elif c == 0x21:                                  # RIT
            if s == 0x00 and len(d) >= 3:                # RIT frequency (signed BCD)
                self.state["rit_freq"] = civ.rit_from_bcd(d); changed = True
            elif s == 0x01 and d:                        # RIT on/off
                self.state["rit"] = d[0]; changed = True
        elif c == 0x0F:                                  # split / duplex status (read reply)
            v = s if s is not None else (d[0] if d else 0)
            if v == 0x00:                                # simplex / split off
                self.state["split"] = 0; self.state["duplex"] = 0; changed = True
            elif v == 0x01:                              # split on
                self.state["split"] = 1; changed = True
            elif v in (0x11, 0x12):                      # DUP- / DUP+
                self.state["duplex"] = v - 0x10; self.state["split"] = 0; changed = True
        elif c == 0x0C:                                  # duplex offset read (3-byte BCD, 100 Hz LSB)
            raw = self._payload(s, d)
            if raw:
                self.state["offset"] = civ.offset_from_bcd(raw); changed = True
        elif c == 0x1A and s == 0x05 and len(d) >= 3:    # MOD Input read response
            dataoff, level = self.profile.mod_dataoff, self.profile.lan_mod_level
            if d[0] == dataoff[0] and d[1] == dataoff[1] and self._modsrc_orig is None:
                self._modsrc_orig = d[2]
            elif (d[0] == level[0] and d[1] == level[1]
                  and len(d) >= 4 and self._lanmod_orig is None):
                self._lanmod_orig = civ.bcd_to_level(d[2:4])
        if changed:
            self._recalc_filter_bw()
            self._emit_state()

    @staticmethod
    def _payload(sub: Optional[int], data: bytes) -> bytes:
        if sub is None:
            return data
        return bytes([sub]) + data

    def _recalc_filter_bw(self) -> None:
        bw = self.profile.filter_bw.get(self.state["mode_name"], {}).get(self.state["filter"], 2400)
        self.state["filter_bw"] = bw

    # -- polling -------------------------------------------------------------
    def _poll(self) -> None:
        tick = 0
        while not self._poll_stop.is_set():
            # TX failsafe: never leave the radio able to transmit past the time-out —
            # covers both latched PTT and VOX (which auto-keys off the modulator audio).
            if self._ptt_deadline and time.monotonic() >= self._ptt_deadline:
                if self.state["ptt"]:
                    self.set_ptt(False)
                if self.state.get("vox"):
                    self.set_rx_func("vox", False)
            self._write(self._b(0x15, 0x02))             # S-meter (active band)
            if self.state["meter"] != "S":               # selected TX meter for the big bar
                self._write(self._b(0x15, civ.METER_SUBS[self.state["meter"]]))
            if tick % 8 == 0 and not self._suppress_poll:   # ~1.2s: catch panel changes
                self._write(self._b(0x03))
                self._write(self._b(0x04))
                if self.state["dual_watch"]:             # is MAIN the operating band?
                    self._write(self._b(0x07, 0xD2, b"\x00"))
            tick += 1
            time.sleep(0.15)

    # -- button-level actions ------------------------------------------------
    def set_freq(self, hz: int) -> None:
        hz = max(0, int(hz))
        self.state["freq"] = hz
        self._write(self._b(0x05, None, civ.freq_to_bcd(hz)))
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
        self._write(self._b(0x06, None, bytes([mode_code, filt])))
        self._emit_state()

    def set_filter(self, filt: int) -> None:
        self.set_mode(self.state["mode"], filt)

    def select_vfo(self, code: int) -> None:
        self._write(self._b(0x07, code))

    def select_band(self, band: str) -> None:
        """Select MAIN or SUB as the operating band (IC-9700 dual watch). The radio
        only reports the operating band over CI-V, so the other band keeps its
        last-known values until it is selected."""
        if band not in ("main", "sub"):
            return
        self._write(self._b(0x07, 0xD0 if band == "main" else 0xD1))
        self.state["active_band"] = band
        self._switch_at = time.monotonic()                # ignore stale freq/mode reads briefly
        self._write(self._b(0x07, 0xD2, b"\x00"))         # confirm the new operating band
        b = self.state[band]                              # reflect target band immediately
        self.state["freq"] = b["freq"]
        self.state["mode_name"] = b["mode_name"]
        self.state["filter_name"] = b["filter_name"]
        self.state["smeter"] = b["smeter"]
        self.state["smeter_s"] = b["smeter_s"]
        self._emit_state()

    def set_meter(self, key: str) -> None:
        if key in civ.METER_SUBS:
            self.state["meter"] = key
            self.state["meter_val"] = self.state["smeter"] if key == "S" else 0
            self._write(self._b(0x15, civ.METER_SUBS[key]))
            self._emit_state()

    def set_preamp(self, on: bool) -> None:
        v = 1 if on else 0
        self.state["preamp"] = v
        self._write(self._b(0x16, 0x02, bytes([v])))
        self._emit_state()

    def set_att(self, on: bool) -> None:
        v = 0x10 if on else 0x00                          # IC-9700 ATT: on (10 dB) / off
        self.state["att"] = v
        self._write(self._b(0x11, v))
        self._emit_state()

    def set_lock(self, on: bool) -> None:
        self.state["lock"] = bool(on)
        self._write(self._b(0x16, 0x50, bytes([1 if on else 0])))
        self._emit_state()

    def set_level(self, sub: int, value: int) -> None:
        key = LEVEL_KEYS.get(sub)
        if key:
            self.state[key] = max(0, min(255, int(value)))
        self._write(self._b(0x14, sub, civ.level_to_bcd(value)))
        self._emit_state()

    def set_rx_func(self, name: str, on: bool) -> None:
        sub = RX_FUNCS.get(name)
        if sub is None:
            return
        self.state[name] = 1 if on else 0
        if name == "vox":                                 # VOX can auto-key TX -> bound it like PTT
            if on:
                self._ptt_deadline = time.monotonic() + PTT_TIMEOUT
            elif not self.state["ptt"]:
                self._ptt_deadline = None
        self._write(self._b(0x16, sub, bytes([1 if on else 0])))
        self._emit_state()

    def set_agc(self, mode: int) -> None:
        mode = max(1, min(3, int(mode)))
        self.state["agc"] = mode
        self._write(self._b(0x16, 0x12, bytes([mode])))
        self._emit_state()

    def set_mnotch_w(self, width: int) -> None:
        width = max(0, min(2, int(width)))
        self.state["mnotch_w"] = width
        self._write(self._b(0x16, 0x57, bytes([width])))
        self._emit_state()

    # -- M3 TX actions -------------------------------------------------------
    def set_tbw(self, w: int) -> None:
        w = max(0, min(2, int(w)))                        # SSB TX bandwidth WIDE/MID/NAR
        self.state["tbw"] = w
        self._write(self._b(0x16, 0x58, bytes([w])))
        self._emit_state()

    def set_rit(self, on: bool) -> None:
        self.state["rit"] = 1 if on else 0
        self._write(self._b(0x21, 0x01, bytes([1 if on else 0])))
        self._emit_state()

    def set_rit_freq(self, hz: int) -> None:
        hz = max(-9999, min(9999, int(hz)))
        self.state["rit_freq"] = hz
        self._write(self._b(0x21, 0x00, civ.rit_to_bcd(hz)))
        self._emit_state()

    def set_split(self, on: bool) -> None:
        self.state["split"] = 1 if on else 0
        if on:
            self.state["duplex"] = 0                       # split and duplex are exclusive modes
        self._write(self._b(0x0F, 0x01 if on else 0x00))
        self._emit_state()

    def set_duplex(self, mode: int) -> None:
        mode = max(0, min(2, int(mode)))                  # 0 SIMP, 1 DUP-, 2 DUP+
        self.state["duplex"] = mode
        self.state["split"] = 0
        self._write(self._b(0x0F, 0x10 + mode))
        self._emit_state()

    def set_band(self, band: str) -> None:
        f = self.profile.band_default(band)
        if f is not None:
            self.set_freq(f)

    def set_span(self, span_hz: int) -> None:
        self.state["span"] = span_hz
        self.state["span_label"] = civ.SPAN_LABELS.get(span_hz, "")
        self._write(self._b(0x27, 0x15, bytes([0x00]) + civ.freq_to_bcd(span_hz)))
        self._emit_state()

    def set_scope_mode(self, center: bool) -> None:
        self.state["scope_center"] = center
        self._write(self._b(0x27, 0x14, bytes([0x00, 0x00 if center else 0x01])))
        self._emit_state()

    def set_ptt(self, tx: bool) -> None:
        tx = bool(tx)
        self.state["ptt"] = tx
        if tx:
            self._ptt_deadline = time.monotonic() + PTT_TIMEOUT
        elif not self.state.get("vox"):
            self._ptt_deadline = None                     # keep armed while VOX is still active
        self._write(self._b(0x1C, 0x00, bytes([1 if tx else 0])))
        self._emit_state()

    # -- state notify --------------------------------------------------------
    def _mirror_active(self) -> None:
        """Keep state[active_band] in step with the live top-level (active) fields,
        so MAIN/SUB both stay current regardless of which band is operating."""
        b = self.state.get(self.state.get("active_band", "main"))
        if isinstance(b, dict):
            b["freq"] = self.state["freq"]
            b["mode_name"] = self.state["mode_name"]
            b["filter_name"] = self.state["filter_name"]

    def _emit_state(self) -> None:
        self._mirror_active()
        if self.on_state:
            s = self.state
            self.on_state({**s, "main": dict(s["main"]), "sub": dict(s["sub"])})

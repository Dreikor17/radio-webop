"""
High-level radio controller.

Owns a Transport, decodes the inbound CI-V stream into live state + scope
sweeps, and exposes button-level actions used by the web UI. Knows nothing
about asyncio; the server bridges thread -> websocket via callbacks.

Model-specific details (CI-V address, bands, modes, filter widths, MOD-Input
numbers, power behaviour) come from a RadioProfile (see profiles.py).
"""
from __future__ import annotations

import json
import threading
import time
from typing import Callable, Optional

from . import civ, menu_engine, profiles
from .profiles import RadioProfile
from .transport import Transport

# Windows quantises time.sleep to ~15 ms unless the multimedia timer resolution is raised;
# the fast meter poll wants a stable ~40 ms cadence, so bracket the poll loop with it.
try:
    import ctypes
    _WINMM = ctypes.WinDLL("winmm")
except Exception:
    _WINMM = None

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


# --- CW keying (operator-triggered message send) ---------------------------
# Allowed characters for the Icom "Send CW message" command (CI-V 17), per the
# CI-V reference. Anything outside this set is dropped before transmit.
CW_CIV_CHARS = set("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
                   "/?.-,:'()=+\"@ ")
# Charset the FT-991A CW path (host-timed DTR line keying) can send: only glyphs that
# exist in _MORSE are keyable, so anything else is dropped.
CW_YAESU_CHARS = set("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ/?.,=+-: ")

_MORSE = {
    "A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".", "F": "..-.", "G": "--.",
    "H": "....", "I": "..", "J": ".---", "K": "-.-", "L": ".-..", "M": "--", "N": "-.",
    "O": "---", "P": ".--.", "Q": "--.-", "R": ".-.", "S": "...", "T": "-", "U": "..-",
    "V": "...-", "W": ".--", "X": "-..-", "Y": "-.--", "Z": "--..",
    "0": "-----", "1": ".----", "2": "..---", "3": "...--", "4": "....-", "5": ".....",
    "6": "-....", "7": "--...", "8": "---..", "9": "----.",
    ".": ".-.-.-", ",": "--..--", "?": "..--..", "/": "-..-.", "=": "-...-", "+": ".-.-.",
    "-": "-....-", ":": "---...", "(": "-.--.", ")": "-.--.-", '"': ".-..-.", "@": ".--.-.",
    "'": ".----.",
}


def cw_duration(text: str, wpm: int) -> float:
    """Estimated on-air seconds for ``text`` at ``wpm`` (PARIS timing). Used to size
    the CW-TX safety auto-stop and the UI 'transmitting' indicator."""
    dot = 1.2 / max(1, int(wpm))
    units = 0
    for ch in str(text).upper():
        if ch == " ":
            units += 7                       # word gap
            continue
        m = _MORSE.get(ch)
        if not m:
            continue
        for el in m:
            units += (3 if el == "-" else 1) + 1   # element + intra-element gap
        units += 2                            # -> 3-dot inter-character gap total
    return units * dot


def cw_elements(text: str, wpm: int) -> list:
    """(key_down, seconds) segments to key ``text`` as Morse at ``wpm`` (PARIS
    timing) for line keying (RTS/DTR): element = key-down, gap = key-up."""
    dot = 1.2 / max(1, int(wpm))
    seq: list = []
    for ch in str(text).upper():
        if ch == " ":
            seq.append((False, dot * 7))                       # word gap
            continue
        m = _MORSE.get(ch)
        if not m:
            continue
        for el in m:
            seq.append((True, dot * (3 if el == "-" else 1)))  # dit / dah (key down)
            seq.append((False, dot))                           # intra-element gap
        if seq and not seq[-1][0]:
            seq[-1] = (False, dot * 3)                         # last gap -> inter-character (3 dots)
    return seq


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
        "preamp": 0, "att": 0, "lock": False, "tuner": 0,
        "has_preamp": p.has_preamp, "has_att": p.has_att,
        "has_tuner": getattr(p, "has_tuner", False),
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
        # FM operating settings (Tone/DCS + repeater shift) and the NAR/WIDE toggle
        "narrow": 0,
        "tone_mode": 0,      # 0 OFF / 1 TSQL / 2 TONE / 3 DCS / 4 DCS-ENC (Yaesu CT P2)
        "tone_freq": 0,      # CTCSS tone index 0-49
        "dcs_code": 0,       # DCS code index 0-103
        "rpt_shift": 0,      # 0 simplex / 1 +shift / 2 -shift
        # DSP filter + CW + operating controls (Yaesu SH/CO/ML/TS/PR/BI/KR/KS/KP/CS/SC/FS)
        "width": 0,          # SH DSP bandwidth code 0-21 (Hz depends on mode + narrow)
        "contour": 0, "contour_freq": 10,     # CONTOUR on/off + centre freq (10-3200 Hz)
        "apf": 0, "apf_freq": 25,             # APF on/off + freq 0-50 (25 = 0 Hz, -250..+250)
        "txw": 0,            # TXW (listen on TX freq during split)
        "param_eq": 0,       # parametric mic EQ on/off
        "bkin": 0, "keyer": 0, "spot": 0,     # CW break-in / keyer / spot
        "key_speed": 20,     # keyer WPM 4-60
        "key_pitch": 40,     # CW pitch code 0-75 (300-1050 Hz; 40 = 700 Hz)
        "scan": 0, "fast": 0,
        "has_scope": getattr(p, "has_scope", True),
        "cw_tx": False,                                  # CW message currently transmitting
        "has_cw_tx": bool(getattr(p, "cw_send", "")),    # this radio can send a typed CW message
        "cw_tx_ready": False,                            # CW TX usable now (Icom: connected; FT-991A: key port open)
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
        self.on_menu: Optional[Callable] = None
        self.on_meter: Optional[Callable] = None      # fast meter channel (separate from full state)
        self._poll_dt = 0.04                          # poll interval (s); set per-transport on connect
        self._last_sig = None                         # last non-meter state sig (dedup full frames)
        self._menu_index = {}     # num (1A 05 data number) -> MenuItem, built on connect
        self._menu_vals = {}      # num -> last decoded menu value (cache)
        self._modsrc_orig: Optional[int] = None     # original DATA OFF MOD (for restore)
        self._lanmod_orig: Optional[int] = None      # original LAN MOD Level (for restore)
        self._mod_managed = False
        self._ptt_deadline: Optional[float] = None   # PTT failsafe deadline (monotonic)
        self._cw_deadline: Optional[float] = None    # CW-TX auto-stop deadline (monotonic)
        self._switch_at = 0.0                         # monotonic time of the last MAIN/SUB switch

        self.state = self._fresh_state()

    def _fresh_state(self) -> dict:
        return fresh_state(self.profile)

    def _b(self, cmd: int, sub: Optional[int] = None, data: bytes = b"") -> bytes:
        """Build a CI-V frame addressed to this radio's CI-V address."""
        return civ.build(cmd, sub, data, radio_addr=self.profile.civ_addr)

    # -- SET menus (Icom CI-V 1A 05 <data-number>) ---------------------------
    def get_menu(self, num) -> None:
        it = self._menu_index.get(int(num))
        if it is not None:
            self._write(self._b(0x1A, 0x05, menu_engine.civ_read_data(it)))

    def set_menu(self, num, value) -> None:
        it = self._menu_index.get(int(num))
        if it is None or it.readonly or not self._menu_write_allowed(it):
            return
        try:
            data = menu_engine.civ_write_data(it, value)
        except menu_engine.MenuError:
            return
        self._write(self._b(0x1A, 0x05, data))
        self.get_menu(num)                                # confirm-read -> UI reflects the radio

    def _menu_write_allowed(self, item) -> bool:
        # While the app owns the MOD source on LAN, block menu writes to the MOD data numbers
        # (DATA OFF MOD / LAN MOD Level) so the disconnect-restore can't fight the user's change.
        if self._mod_managed:
            def _num(dn):
                return ((dn[0] >> 4) * 1000 + (dn[0] & 0xF) * 100
                        + (dn[1] >> 4) * 10 + (dn[1] & 0xF)) if dn else -1
            if item.num in (_num(self.profile.mod_dataoff), _num(self.profile.lan_mod_level)):
                return False
        return True

    def read_menu_group(self, group) -> None:
        """Lazily read one menu category (spaced on a thread; bails on disconnect)."""
        items = [it for it in (getattr(self.profile, "menu", None) or []) if it.group == group]
        if not items:
            return

        def _run():
            for it in items:
                if self._poll_stop.is_set() or self._tp is None:
                    break
                self._write(self._b(0x1A, 0x05, menu_engine.civ_read_data(it)))
                time.sleep(0.03)

        threading.Thread(target=_run, daemon=True, name="civ-menu-read").start()

    def _handle_menu_reply(self, d: bytes) -> None:
        num = (d[0] >> 4) * 1000 + (d[0] & 0xF) * 100 + (d[1] >> 4) * 10 + (d[1] & 0xF)
        it = self._menu_index.get(num)
        if it is None:
            return
        val = menu_engine.civ_decode(it, d)
        if val is None:
            return
        self._menu_vals[num] = val
        self._emit_menu({num: val})

    def _emit_menu(self, values: dict) -> None:
        if self.on_menu:
            try:
                self.on_menu(values)
            except Exception:
                pass

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
        self._menu_index = {it.num: it for it in (getattr(self.profile, "menu", None) or [])}
        self._menu_vals = {}
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
        # Icom CW TX (CI-V 17) needs no extra port — ready as soon as we're connected.
        self.state["cw_tx_ready"] = bool(getattr(self.profile, "cw_send", ""))

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
        for sub in (0x01, 0x02, 0x03, 0x0A, 0x06, 0x12, 0x07, 0x08, 0x0D,
                    0x0B, 0x0E, 0x15, 0x16):             # AF/RF/SQL/PWR + RX-DSP + (M3) MIC/COMP/MON/VOX
            self._write(self._b(0x14, sub))
        self._write(self._b(0x21, 0x00))                 # RIT frequency
        self._write(self._b(0x21, 0x01))                 # RIT on/off
        self._write(self._b(0x0F))                       # split / duplex
        self._write(self._b(0x0C))                       # duplex offset

        # Meter cadence: fast on direct serial (USB CI-V); easier on LAN, where each read is a
        # UDP round-trip to the radio, so don't outrun the reply rate.
        self._poll_dt = 0.08 if getattr(transport, "supports_audio", False) else 0.04
        self._poll_stop.clear()
        self._poll_thread = threading.Thread(target=self._poll, daemon=True, name="civ-poll")
        self._poll_thread.start()
        self._emit_state()

        if getattr(self.profile, "tot_civ", ()):            # safety: hardware TX time-out backstop
            self._write(self._b(0x1A, 0x05, bytes(self.profile.tot_civ)))
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

    def disconnect(self) -> None:
        self._poll_stop.set()
        if self._poll_thread:
            self._poll_thread.join(timeout=1.0)
            self._poll_thread = None
        if self._tp:
            try:
                if self.state.get("cw_tx"):                  # stop an in-progress CW message
                    self._write(self._b(0x17, None, b"\xff"))
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
        self._cw_deadline = None
        self.state["connected"] = False
        self.state["transport"] = None
        self.state["audio"] = False
        self.state["ptt"] = False
        self.state["cw_tx"] = False
        self.state["cw_tx_ready"] = False
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
        meter_changed = False         # meter-only updates ride the lightweight meter channel
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
        elif c == 0x15:                                  # multi-meter (fast channel, not full state)
            lvl = civ.bcd_to_level(d)
            if s == 0x02:                                # S-meter (active band)
                ab = self.state["active_band"]
                self.state["smeter"] = lvl
                self.state["smeter_s"] = smeter_label(lvl)
                self.state[ab]["smeter"] = lvl
                self.state[ab]["smeter_s"] = smeter_label(lvl)
                if self.state["meter"] == "S":
                    self.state["meter_val"] = lvl
                meter_changed = True
            elif civ.METER_SUBS.get(self.state["meter"]) == s:
                self.state["meter_val"] = lvl
                meter_changed = True
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
        elif c == 0x1A and s == 0x05 and len(d) >= 2:    # 1A 05 <data-number> read response
            dataoff, level = self.profile.mod_dataoff, self.profile.lan_mod_level
            if len(d) >= 3 and d[0] == dataoff[0] and d[1] == dataoff[1] and self._modsrc_orig is None:
                self._modsrc_orig = d[2]                  # MOD Input source (for restore)
            elif (len(d) >= 4 and d[0] == level[0] and d[1] == level[1]
                  and self._lanmod_orig is None):
                self._lanmod_orig = civ.bcd_to_level(d[2:4])
            self._handle_menu_reply(d)                    # SET-menu items -> separate menu channel
        if changed:
            self._recalc_filter_bw()
            self._emit_state()
        elif meter_changed:
            self._emit_meter()        # meter-only: tiny frame, no full-state re-serialize

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
        dt = self._poll_dt                               # ~25 Hz serial / ~12 Hz LAN
        panel_n = max(1, round(1.2 / dt))                # freq/mode/active-band cadence (~1.2 s)
        full_n = max(1, round(6.0 / dt))                 # full-panel re-sync (~6 s)
        if _WINMM:
            try: _WINMM.timeBeginPeriod(1)               # accurate short sleeps on Windows
            except Exception: pass
        try:
            next_t = time.perf_counter()
            while not self._poll_stop.is_set():
                # TX failsafe (EVERY iteration, never gated behind tick%N): never leave the radio
                # able to transmit past the time-out — covers latched PTT and VOX (audio-keyed).
                if self._ptt_deadline and time.monotonic() >= self._ptt_deadline:
                    if self.state["ptt"]:
                        self.set_ptt(False)
                    if self.state.get("vox"):
                        self.set_rx_func("vox", False)
                # CW-TX bounded auto-stop: the rig self-keys the message via BK-IN, so the
                # PTT failsafe doesn't cover it — stop it once the message should be done.
                if self._cw_deadline and time.monotonic() >= self._cw_deadline:
                    self.stop_cw()
                self._write(self._b(0x15, 0x02))         # S-meter (active band) — every tick
                if self.state["meter"] != "S":           # selected TX meter for the big bar
                    self._write(self._b(0x15, civ.METER_SUBS[self.state["meter"]]))
                if tick % panel_n == 0:                  # ~1.2s: catch panel changes
                    self._write(self._b(0x03))
                    self._write(self._b(0x04))
                    if self.state["dual_watch"]:         # is MAIN the operating band?
                        self._write(self._b(0x07, 0xD2, b"\x00"))
                if tick % full_n == 0:                   # ~6s: re-sync the whole panel from the radio
                    self._read_panel()
                tick += 1
                next_t += dt                             # absolute-deadline pacing: stable cadence,
                rem = next_t - time.perf_counter()       # no drift accumulation
                if rem > 0:
                    self._poll_stop.wait(rem)            # responsive: returns at once on disconnect
                else:
                    next_t = time.perf_counter()         # fell behind -> resync, don't spiral
        finally:
            if _WINMM:
                try: _WINMM.timeEndPeriod(1)
                except Exception: pass

    def _read_panel(self) -> None:
        """Re-read the panel settings so the UI mirrors the radio even when knobs
        are turned at the front panel (connect reads these once; this keeps them live)."""
        self._write(self._b(0x11))                       # attenuator
        for sub in (0x02, 0x50, 0x22, 0x40, 0x41, 0x48,  # preamp, lock, NB, NR, A/M-notch,
                    0x12, 0x57, 0x44, 0x45, 0x46, 0x58):  # AGC, notch-W, COMP, MON, VOX, TBW
            self._write(self._b(0x16, sub))
        for sub in (0x01, 0x02, 0x03, 0x0A, 0x06, 0x12, 0x07, 0x08, 0x0D,  # AF/RF/SQL/PWR + NR/NB/PBT/notch,
                    0x0B, 0x0E, 0x15, 0x16):              # MIC, COMP, MON, VOX level
            self._write(self._b(0x14, sub))
        self._write(self._b(0x21, 0x00))                 # RIT frequency
        self._write(self._b(0x21, 0x01))                 # RIT on/off
        self._write(self._b(0x0F))                       # split / duplex
        self._write(self._b(0x0C))                       # duplex offset

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

    def set_preamp(self, level) -> None:
        v = max(0, min(3, int(level)))               # 16 02: 0 off / 1 P.AMP / 2 EXT / 3 P.AMP+EXT
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

    def set_tuner(self, on: bool) -> None:
        # Icom internal ATU isn't wired yet; has_tuner is False for the Icom profiles
        # so the button is hidden. No-op keeps the shared action dispatch safe.
        return

    def tune_atu(self) -> None:
        return                                            # Icom ATU not wired (has_tuner False)

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

    # FM Tone/DCS + repeater-shift + NAR/WIDE + the Yaesu DSP/CW/operating controls —
    # not yet implemented for Icom CI-V (the Yaesu handler implements them); no-op so
    # the shared WS dispatch is safe.
    def set_tone_mode(self, v: int) -> None: pass
    def set_tone_freq(self, idx: int) -> None: pass
    def set_dcs_code(self, idx: int) -> None: pass
    def set_rpt_shift(self, v: int) -> None: pass
    def set_narrow(self, on: bool) -> None: pass
    def set_width(self, code: int) -> None: pass
    def set_contour(self, on: bool) -> None: pass
    def set_contour_freq(self, hz: int) -> None: pass
    def set_apf(self, on: bool) -> None: pass
    def set_apf_freq(self, v: int) -> None: pass
    def set_txw(self, on: bool) -> None: pass
    def set_param_eq(self, on: bool) -> None: pass
    def set_bkin(self, on: bool) -> None: pass
    def set_keyer(self, on: bool) -> None: pass
    def set_key_speed(self, wpm: int) -> None: pass
    def set_key_pitch(self, code: int) -> None: pass
    def set_spot(self, on: bool) -> None: pass
    def set_zero_in(self) -> None: pass
    def set_quick_split(self) -> None: pass
    def set_scan(self, direction: int) -> None: pass
    def set_fast(self, on: bool) -> None: pass

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

    # -- CW message transmit (operator-triggered) ----------------------------
    def send_cw(self, text: str, wpm: int = 18) -> None:
        """Transmit a typed CW message. Operator-triggered, one bounded message per
        call: set the keyer speed from WPM, enable semi break-in (so CI-V 17 keys the
        TX), then hand the text to the rig's keyer, which generates clean CW and drops
        back to RX on its own. Bounded by an auto-stop (17 FF) as a safety backstop."""
        if getattr(self.profile, "cw_send", "") != "civ17" or not self.state.get("connected"):
            return
        if self.state.get("mode_name") not in ("CW", "CW-R"):
            return                                       # only meaningful in CW (the UI guards too)
        msg = "".join(c for c in str(text) if c in CW_CIV_CHARS)[:30]   # cmd 17 max 30 chars
        if not msg.strip():
            return
        wpm = max(6, min(48, int(wpm)))                  # IC keyer range 6-48 WPM
        speed = round((wpm - 6) / 42 * 255)
        self._write(self._b(0x14, 0x0C, civ.level_to_bcd(speed)))   # keying speed
        self._write(self._b(0x16, 0x47, b"\x01"))                    # semi BK-IN: cmd 17 keys the TX
        self._write(self._b(0x17, None, msg.encode("ascii")))       # send the CW message
        self._cw_deadline = time.monotonic() + cw_duration(msg, wpm) + 2.0
        self.state["cw_tx"] = True
        self._emit_state()

    def stop_cw(self) -> None:
        """Stop an in-progress CW message (and clear the indicator). 17 FF aborts the
        rig's keyer; harmless if the message already finished."""
        was = self.state.get("cw_tx")
        self._cw_deadline = None
        if self._tp and getattr(self.profile, "cw_send", "") == "civ17":
            self._write(self._b(0x17, None, b"\xff"))    # FF stops sending CW
        if was:
            self.state["cw_tx"] = False
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

    _METER_FIELDS = ("smeter", "smeter_s", "meter_val")

    def _state_sig(self) -> str:
        """Signature of the state the FULL frame renders, EXCLUDING the fast meter fields
        (those ride the meter channel). Lets _emit_state skip redundant frames from panel
        re-reads / scope ticks that didn't actually change anything."""
        out = {}
        for k, v in self.state.items():
            if k in self._METER_FIELDS:
                continue
            if isinstance(v, dict):
                v = {kk: vv for kk, vv in v.items() if kk not in self._METER_FIELDS}
            out[k] = v
        return json.dumps(out, sort_keys=True, default=str)

    def _emit_state(self) -> None:
        self._mirror_active()
        if not self.on_state:
            return
        sig = self._state_sig()
        if sig == self._last_sig:                 # nothing the full frame shows changed -> skip
            return
        self._last_sig = sig
        s = self.state
        self.on_state({**s, "main": dict(s["main"]), "sub": dict(s["sub"])})

    def _emit_meter(self) -> None:
        if self.on_meter:
            s = self.state
            self.on_meter(s["meter"], s["meter_val"], s["smeter"], s["smeter_s"])

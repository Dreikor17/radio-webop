"""
Transports carry raw CI-V bytes to/from "the radio".

Two implementations share one interface:
  * SerialTransport - a real IC-9700 on a Windows COM port (USB CI-V).
  * SimTransport    - a built-in simulator that speaks CI-V back, including
                      USB-style 11-frame 27 00 scope sweeps, so the whole UI
                      and waterfall can be built and verified with no radio.

Reads happen on a background thread that calls on_bytes(data).
"""
from __future__ import annotations

import math
import threading
import time
from typing import Callable, Optional

import serial
from serial.tools import list_ports

from . import civ


def available_ports() -> list[dict]:
    out = []
    for p in list_ports.comports():
        out.append({"device": p.device, "description": p.description or "",
                    "hwid": p.hwid or ""})
    return out


def _hwid_serial(hwid: str) -> Optional[str]:
    """Pull the USB serial-number token (SER=...) out of a pyserial hwid string."""
    for tok in (hwid or "").split():
        if tok.upper().startswith("SER="):
            return tok[4:]
    return None


def find_sibling_port(device: str) -> Optional[str]:
    """Find the OTHER COM port of the same physical USB device — e.g. the FT-991A's
    Standard port that is the sibling of its Enhanced/CAT port. The FT-991A's CP2105
    enumerates two ports sharing one USB serial number; CW is keyed on the Standard
    one's control line. Returns the sibling device name, or None if not found."""
    ports = list(list_ports.comports())
    target = None
    for p in ports:
        if p.device == device:
            target = _hwid_serial(p.hwid)
            break
    if not target:
        return None
    for p in ports:
        if p.device != device and _hwid_serial(p.hwid) == target:
            return p.device
    return None


class Transport:
    # Audio support (overridden by LanTransport). on_audio is called with raw
    # RX PCM (16-bit LE mono); write_audio takes TX (mic) PCM in the same format.
    supports_audio: bool = False
    on_audio: Optional[Callable[[bytes], None]] = None

    def start(self, on_bytes: Callable[[bytes], None]) -> None: ...
    def write(self, data: bytes) -> None: ...
    def write_audio(self, pcm: bytes) -> None: ...
    def stop(self) -> None: ...
    @property
    def name(self) -> str: return "transport"


class SerialTransport(Transport):
    def __init__(self, port: str, baud: int = 115200, stopbits: int = 1) -> None:
        self.port = port
        self.baud = baud
        self.stopbits = stopbits          # Icom CI-V = 1; Yaesu CAT (FT-991A) = 2
        self._ser: Optional[serial.Serial] = None
        self._on_bytes: Optional[Callable[[bytes], None]] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    @property
    def name(self) -> str:
        return f"{self.port}@{self.baud}"

    def start(self, on_bytes: Callable[[bytes], None]) -> None:
        self._on_bytes = on_bytes
        sb = serial.STOPBITS_TWO if self.stopbits == 2 else serial.STOPBITS_ONE
        self._ser = serial.Serial(self.port, self.baud, timeout=0.05, stopbits=sb)
        self._stop.clear()
        self._thread = threading.Thread(target=self._reader, name="civ-serial",
                                        daemon=True)
        self._thread.start()

    def _reader(self) -> None:
        assert self._ser is not None
        while not self._stop.is_set():
            try:
                data = self._ser.read(4096)
                if data and self._on_bytes:
                    self._on_bytes(data)
            except Exception:
                time.sleep(0.1)

    def write(self, data: bytes) -> None:
        if self._ser and self._ser.is_open:
            try:
                self._ser.write(data)
            except Exception:
                pass

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        if self._ser:
            try:
                self._ser.close()
            except Exception:
                pass


class CwKeyPort:
    """A SECOND serial port opened solely to key CW via its DTR line (the FT-991A's
    Standard USB port, sibling of the Enhanced/CAT port). No CAT data flows here — only
    the DTR control line is toggled. Always opens key-UP (DTR de-asserted) and forces it
    up again on close, so it can never leave the rig keyed."""

    def __init__(self, device: str) -> None:
        self.device = device
        self._ser: Optional[serial.Serial] = None
        # Serializes key()/close() so a key-down can never land between close()'s
        # CLRDTR and the handle close (which would strand DTR HIGH = stuck carrier).
        self._lock = threading.Lock()

    def open(self) -> None:
        ser = serial.Serial()
        ser.port = self.device
        ser.baudrate = 9600           # irrelevant: we only drive DTR, no data
        ser.timeout = 0.1
        ser.rts = False               # set before open so the lines come up LOW (key up)
        ser.dtr = False
        ser.open()
        with self._lock:
            self._ser = ser

    def key(self, down: bool) -> None:
        with self._lock:              # mutually exclusive with close()
            s = self._ser
            if not s or not s.is_open:
                return
            try:
                s.dtr = bool(down)    # DTR asserted = key down = CW element
            except Exception:
                pass

    def close(self) -> None:
        with self._lock:
            s = self._ser
            self._ser = None
            if s:
                try:
                    s.dtr = False     # never leave keyed
                    s.rts = False
                except Exception:
                    pass
                try:
                    s.close()
                except Exception:
                    pass

    @property
    def is_open(self) -> bool:
        s = self._ser
        return bool(s and s.is_open)


class SimTransport(Transport):
    """A synthetic IC-9700. Responds to CI-V commands and streams a scope."""

    def __init__(self, profile=None, fps: float = 20.0) -> None:
        self._on_bytes: Optional[Callable[[bytes], None]] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._reader = civ.FrameReader()
        self.fps = fps
        self._t0 = time.time()
        self._civ_addr = profile.civ_addr if profile is not None else civ.DEFAULT_RADIO_ADDR

        # simulated radio state — dual-band (MAIN/SUB) for dual-watch radios
        main_freq = profile.default_freq if profile is not None else 144_200_000
        self.dual = bool(profile is not None and getattr(profile, "dual_watch", False))
        sub_freq = profile.bands[1].default if (self.dual and len(profile.bands) > 1) else main_freq
        self.active = "main"
        self.b = {
            "main": {"freq": main_freq, "mode": 0x01, "filt": 0x01},   # USB
            "sub":  {"freq": sub_freq, "mode": 0x05, "filt": 0x01},    # FM
        }
        self.att = 0x00
        self.span = 500_000        # full span Hz (±250k, widest)
        self.scope_center = True   # center vs fixed
        self.scope_on = False
        self.scope_out = False
        self.levels = {0x01: 128, 0x02: 200, 0x03: 0, 0x0A: 0,   # AF/RF/SQL/RFpwr (power 0%)
                       0x06: 0, 0x12: 0, 0x07: 128, 0x08: 128,    # NR/NB level, twin PBT (center)
                       0x0B: 128, 0x0E: 128, 0x15: 128, 0x16: 128}  # MIC/COMP/MON/VOX level (M3)
        self.funcs = {0x02: 0, 0x50: 0, 0x22: 0, 0x40: 0,        # preamp, dial-lock, NB, NR
                      0x41: 0, 0x48: 0, 0x12: 2, 0x57: 0,          # A-notch, M-notch, AGC=MID, notch-W
                      0x44: 0, 0x45: 0, 0x46: 0, 0x58: 0}          # COMP, MON, VOX, TBW (M3)
        self.rit_on = 0; self.rit_freq = 0
        self.split = 0; self.duplex = 0; self.offset = 600000
        from . import menu_engine
        self._menu = {}            # (dn_hi, dn_lo) -> stored value bytes
        self._menu_index = {tuple(menu_engine.civ_datanum(it.num)): it
                            for it in (getattr(profile, "menu", None) or [])}

    @property
    def name(self) -> str:
        return "Simulator"

    def start(self, on_bytes: Callable[[bytes], None]) -> None:
        self._on_bytes = on_bytes
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="civ-sim",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)

    # -- incoming commands ---------------------------------------------------
    def write(self, data: bytes) -> None:
        for fr in self._reader.feed(data):
            self._handle(fr)

    def _emit(self, cmd: int, sub: Optional[int], data: bytes = b"") -> None:
        # reply travels radio -> controller (to=E0, from=A2)
        frame = civ.build(cmd, sub, data,
                          radio_addr=civ.DEFAULT_CTRL_ADDR,
                          ctrl_addr=self._civ_addr)
        if self._on_bytes:
            self._on_bytes(frame)

    def _ok(self) -> None:
        self._emit(civ.OK, None)

    def _handle(self, fr: civ.Frame) -> None:
        c, s, d = fr.cmd, fr.sub, fr.data
        with self._lock:
            act = self.b[self.active]
            if c == 0x03:                                   # read freq (active band)
                self._emit(0x03, None, civ.freq_to_bcd(act["freq"]))
            elif c == 0x04:                                 # read mode (active band)
                self._emit(0x04, None, bytes([act["mode"], act["filt"]]))
            elif c == 0x05:                                 # set freq (active band)
                act["freq"] = civ.bcd_to_freq(bytes([s]) + d if s is not None else d)
                self._ok()
            elif c == 0x00:                                 # set freq (transceive)
                act["freq"] = civ.bcd_to_freq(bytes([s]) + d if s is not None else d)
            elif c == 0x06:                                 # set mode (active band)
                if s is not None:
                    act["mode"] = s
                    act["filt"] = d[0] if d else 0x01
                self._ok()
            elif c == 0x07:                                 # VFO / MAIN-SUB band select
                if s == 0xD0:
                    self.active = "main"; self._ok()
                elif s == 0xD1:
                    self.active = "sub"; self._ok()
                elif s == 0xD2:                             # read main/sub selection state
                    if not d:
                        self._emit(civ.NG, None)            # malformed: needs a 00/01 selector
                    else:
                        sel = d[0]                          # 00 = query main, 01 = query sub
                        queried = "main" if sel == 0x00 else "sub"
                        status = 0x01 if self.active == queried else 0x00
                        self._emit(0x07, 0xD2, bytes([sel, status]))
                else:
                    self._ok()
            elif c == 0x11:                                 # attenuator
                if s is not None:                           # set (value in sub)
                    self.att = s; self._ok()
                else:                                       # read
                    self._emit(0x11, self.att)
            elif c == 0x14:                                 # levels
                if d:                                       # set
                    self.levels[s] = civ.bcd_to_level(d)
                    self._ok()
                else:                                       # read
                    self._emit(0x14, s, civ.level_to_bcd(self.levels.get(s, 0)))
            elif c == 0x15:                                 # meters (read)
                self._emit(0x15, s, civ.level_to_bcd(self._meter(s)))
            elif c == 0x16:                                 # functions
                if d:
                    self.funcs[s] = d[0]
                    self._ok()
                else:
                    self._emit(0x16, s, bytes([self.funcs.get(s, 0)]))
            elif c == 0x21:                                 # RIT
                if s == 0x00:
                    if d:
                        self.rit_freq = civ.rit_from_bcd(d); self._ok()
                    else:
                        self._emit(0x21, 0x00, civ.rit_to_bcd(self.rit_freq))
                elif s == 0x01:
                    if d:
                        self.rit_on = d[0]; self._ok()
                    else:
                        self._emit(0x21, 0x01, bytes([self.rit_on]))
                else:
                    self._ok()
            elif c == 0x0F:                                 # split / duplex (mutually exclusive)
                if s is None:                               # read -> one documented status byte
                    if self.split:
                        self._emit(0x0F, 0x01)
                    elif self.duplex == 1:
                        self._emit(0x0F, 0x11)
                    elif self.duplex == 2:
                        self._emit(0x0F, 0x12)
                    else:
                        self._emit(0x0F, 0x00)
                elif s == 0x00:
                    self.split = 0; self._ok()
                elif s == 0x01:
                    self.split = 1; self.duplex = 0; self._ok()
                elif s in (0x10, 0x11, 0x12, 0x13):
                    self.duplex = min(2, s - 0x10); self.split = 0; self._ok()
                else:
                    self._ok()
            elif c == 0x0C:                                 # read duplex offset (3-byte BCD)
                self._emit(0x0C, None, civ.offset_to_bcd(self.offset))
            elif c == 0x0D:                                 # set duplex offset
                raw = (bytes([s]) if s is not None else b"") + d
                if raw:
                    self.offset = civ.offset_from_bcd(raw)
                self._ok()
            elif c == 0x19:                                 # read radio id
                self._emit(0x19, 0x00, bytes([self._civ_addr]))
            elif c == 0x18:                                 # power on/off
                self._ok()
            elif c == 0x27:                                 # scope control
                self._scope_cmd(s, d)
            elif c == 0x1A and s == 0x05:                   # SET-menu (1A 05 <data-number>)
                self._menu_cmd(d)
            else:
                self._ok()

    def _scope_cmd(self, sub: Optional[int], d: bytes) -> None:
        if sub == 0x10:        # scope on/off
            if d:
                self.scope_on = d[0] == 1
            self._ok()
        elif sub == 0x11:      # data output on/off
            if d:
                self.scope_out = d[0] == 1
            self._ok()
        elif sub == 0x14:      # center/fixed
            if len(d) >= 2:
                self.scope_center = d[1] == 0
            self._ok()
        elif sub == 0x15:      # span (main/sub + 5 BCD)
            if len(d) >= 6:
                self.span = civ.bcd_to_freq(d[1:6]) or self.span
            self._ok()
        else:
            self._ok()

    def _meter(self, sub: Optional[int]) -> int:
        t = time.time() - self._t0
        if sub == 0x02:        # S-meter, breathe around S5-S7
            return int(70 + 35 * (0.5 + 0.5 * math.sin(t * 0.7)))
        return 0

    def _menu_cmd(self, d: bytes) -> None:
        """SET-menu 1A 05: d = data-number(2) [+ value for a write]."""
        if len(d) < 2:
            self._ok(); return
        key = (d[0], d[1])
        if len(d) > 2:                                  # write: store the value bytes
            self._menu[key] = bytes(d[2:]); self._ok()
        else:                                           # read: echo stored value or a default
            val = self._menu.get(key) or self._menu_default(d)
            self._emit(0x1A, 0x05, bytes([d[0], d[1]]) + val)

    def _menu_default(self, d: bytes) -> bytes:
        it = self._menu_index.get((d[0], d[1]))
        if it is None:
            return b"\x00"
        try:
            from . import menu_engine
            val = it.min if it.kind == "int" else 0
            return menu_engine.civ_write_data(it, val)[2:]   # strip the data number
        except Exception:
            return b"\x00"

    # -- scope generation ----------------------------------------------------
    def _loop(self) -> None:
        period = 1.0 / self.fps
        while not self._stop.is_set():
            with self._lock:
                run = self.scope_on and self.scope_out
                center = self.b[self.active]["freq"]
                span = self.span
            if run:
                self._emit_scope(center, span)
            time.sleep(period)

    def _emit_scope(self, center: int, span: int) -> None:
        wf = self._make_waveform(span)

        # header frame (div 1 of 11): main/sub, 1, 11, center/fixed=0,
        #   center freq (5), span (5), out-of-range
        header = bytearray([0x00, 0x01, 0x0B, 0x00])
        header += civ.freq_to_bcd(center)
        header += civ.freq_to_bcd(span)
        header += bytes([0x00])
        self._emit(0x27, 0x00, bytes(header))

        # 10 waveform frames (div 2..11)
        chunks = _split_even(wf, 10)
        for i, ch in enumerate(chunks):
            body = bytes([0x00, 0x02 + i, 0x0B]) + ch
            self._emit(0x27, 0x00, body)

    def _make_waveform(self, span: int) -> bytes:
        n = civ.SCOPE_POINTS
        t = time.time() - self._t0
        out = bytearray(n)
        # noise floor
        floor = 22.0
        for x in range(n):
            # cheap deterministic "noise"
            nse = (math.sin(x * 12.9898 + t * 7.0) * 43758.5453)
            nse = (nse - math.floor(nse))  # 0..1
            out[x] = int(floor + nse * 10)
        # signals: (relative position 0..1, base amplitude, drift, width)
        sigs = [
            (0.50, 132, 14, 5),    # tuned station near center, breathing
            (0.30, 95, 8, 4),
            (0.72, 110, 22, 3),
            (0.18, 70, 30, 2),
        ]
        for pos, amp, drift, width in sigs:
            cx = int(pos * n)
            a = amp + drift * math.sin(t * (0.5 + pos))
            for x in range(max(0, cx - width * 6), min(n, cx + width * 6)):
                g = math.exp(-((x - cx) ** 2) / (2 * width * width))
                v = out[x] + a * g
                out[x] = int(min(civ.SCOPE_MAX, v))
        # occasional transient
        if int(t * 3) % 7 == 0:
            cx = int((0.4 + 0.2 * math.sin(t)) * n)
            for x in range(max(0, cx - 3), min(n, cx + 3)):
                out[x] = min(civ.SCOPE_MAX, out[x] + 40)
        return bytes(out)


def _split_even(data: bytes, parts: int) -> list[bytes]:
    n = len(data)
    base = n // parts
    rem = n % parts
    chunks = []
    i = 0
    for p in range(parts):
        size = base + (1 if p < rem else 0)
        chunks.append(data[i:i + size])
        i += size
    return chunks


class YaesuSimTransport(Transport):
    """A synthetic Yaesu CAT radio (FT-991A): answers FA/FB/MD/SM/TX/ID/IF reads
    and applies sets, so the FT-991A profile is demoable with no radio. There is
    no scope (the real FT-991A exposes none over CAT)."""

    def __init__(self, profile=None) -> None:
        self._on_bytes: Optional[Callable[[bytes], None]] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._buf = ""
        self._t0 = time.time()
        self.freq = profile.default_freq if profile is not None else 14_074_000
        self.mode = "2"            # USB
        self._menu_index = {it.num: it for it in (getattr(profile, "menu", None) or [])}
        self._menu_vals = {}       # num -> wire value field (as written), for EX read-back
        self._levels = {"PC": "025", "AG0": "140", "RG0": "200",   # power(W)/AF/RF: so the
                        "SQ0": "015", "MG": "050", "GT0": "2"}      # sim reports real levels
        # operating toggles/enums echoed on read (key = command prefix, value = param field)
        self._ops = {"NA0": "0", "CT0": "0", "CN00": "012", "CN01": "000", "OS0": "0",
                     "SH0": "00", "CO00": "0000", "CO01": "0300", "CO02": "0000", "CO03": "0025",
                     "ML0": "000", "ML1": "030", "PR0": "1", "PR1": "1", "TS": "0",
                     "BI": "0", "KR": "0", "KS": "020", "KP": "40", "CS": "0", "SC": "0", "FS": "0"}

    @property
    def name(self) -> str:
        return "Simulator (CAT)"

    def start(self, on_bytes: Callable[[bytes], None]) -> None:
        self._on_bytes = on_bytes
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="yaesu-sim")
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():    # idle; replies are produced in write()
            time.sleep(0.2)

    def write(self, data: bytes) -> None:
        try:
            s = data.decode("ascii", "ignore")
        except Exception:
            return
        with self._lock:
            self._buf += s
            while ";" in self._buf:
                cmd, self._buf = self._buf.split(";", 1)
                self._handle(cmd.strip())

    def _handle(self, cmd: str) -> None:
        out = None
        if cmd == "FA":
            out = f"FA{self.freq:09d};"
        elif cmd.startswith("FA") and len(cmd) >= 11:
            try:
                self.freq = int(cmd[2:11])
            except ValueError:
                pass
        elif cmd == "FB":
            out = f"FB{self.freq:09d};"
        elif cmd == "MD0":
            out = f"MD0{self.mode};"
        elif cmd.startswith("MD0") and len(cmd) >= 4:
            self.mode = cmd[3]
        elif cmd == "SM0":
            lvl = int(90 + 70 * abs(math.sin((time.time() - self._t0) * 0.7)))   # wandering S-meter
            out = f"SM0{min(255, lvl):03d};"
        elif cmd.startswith("RM") and len(cmd) >= 3 and cmd[2] in "012345678":   # READ METER
            p1 = cmd[2]
            v = int(70 + 80 * abs(math.sin((time.time() - self._t0) * (0.6 + int(p1) * 0.13))))
            out = f"RM{p1}{min(255, max(0, v)):03d};"
        elif cmd == "TX":
            out = "TX0;"
        elif cmd == "ID":
            out = "ID0670;"
        elif cmd == "IF":
            out = f"IF001{self.freq:09d}+000000{self.mode}0000000;"
        elif cmd in self._levels:                  # level read: PC/AG0/RG0/SQ0/MG/GT0
            out = f"{cmd}{self._levels[cmd]};"
        elif any(cmd.startswith(k) and cmd[len(k):].isdigit() for k in self._levels):
            for k in self._levels:                 # level set -> remember (sim)
                if cmd.startswith(k) and cmd[len(k):].isdigit():
                    self._levels[k] = cmd[len(k):]; break
        elif any(cmd == p or (cmd.startswith(p) and cmd[len(p):].isdigit()) for p in self._ops):
            for pfx in self._ops:                  # NA0/CT0/CN00/CN01/OS0 read + write
                if cmd == pfx:
                    out = f"{pfx}{self._ops[pfx]};"; break
                if cmd.startswith(pfx) and cmd[len(pfx):].isdigit():
                    self._ops[pfx] = cmd[len(pfx):]; break
        elif cmd.startswith("EX") and len(cmd) >= 5 and cmd[2:5].isdigit():     # SET menu
            # menu-number width varies (FT-991A NNN=3, FT-891 GGNN=4); match a known item.
            body = cmd[2:]
            num = it = None
            for w in (4, 3):
                if len(body) >= w and body[:w].isdigit() and int(body[:w]) in self._menu_index:
                    num, it = int(body[:w]), self._menu_index[int(body[:w])]
                    field = body[w:]
                    break
            if it is not None:
                if field:                              # write -> remember it
                    self._menu_vals[num] = field
                else:                                  # read -> echo stored value or a default
                    w = getattr(it, "ex_width", 3)
                    out = f"EX{num:0{w}d}{self._menu_vals.get(num) or self._ex_default(num)};"
        if out and self._on_bytes:
            self._on_bytes(out.encode("ascii"))

    def _ex_default(self, num: int) -> str:
        """A plausible default value field for an unwritten EX read (sim only)."""
        from . import menu_engine
        if num == 87:
            return "0670"                              # RADIO ID (read-only)
        it = self._menu_index.get(num)
        if it is None:
            return "0"
        try:
            val = it.min if it.kind == "int" else 0    # enum -> index 0, signed -> 0
            w = getattr(it, "ex_width", 3)
            return menu_engine.yaesu_encode(it, val)[2 + w:-1]   # strip EX<num> and ';'
        except Exception:
            return "0"

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)

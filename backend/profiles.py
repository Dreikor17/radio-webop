"""
Radio profiles — everything that differs between supported radios lives here, so
adding a radio is mostly just adding a RadioProfile to PROFILES (a new make/protocol
also needs a handler class).

Within a make the protocol is shared (Icom: CI-V framing, the 27 00 scope, RS-BA1 LAN,
audio; Yaesu: CAT) — only address/baud, band plan, mode set, filter widths and a couple
of menu numbers change per model.

>>> ADDING A RADIO? Follow the transmit-safety contract in docs/ADDING-A-RADIO.md.
Every radio that can key TX MUST have: the 120 s PTT stuck-TX failsafe, TOT set on
connect, the high-SWR cutoff + warning, RF power 0% on connect, unkey-on-disconnect, and
NO autonomous transmission (PTT relays the operator only). Not optional. <<<
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class Band:
    name: str          # button label, e.g. "20" or "144"
    lo: int            # band low edge (Hz)
    hi: int            # band high edge (Hz)
    default: int       # frequency the band button tunes to (Hz)


@dataclass
class SerialCfg:
    baud: int = 115200
    bits: int = 8
    parity: str = "N"          # N | E | O
    stopbits: int = 1          # Icom CI-V = 1, Yaesu CAT = 2


@dataclass
class NetworkCfg:
    enabled: bool = False
    control_port: int = 50001  # RS-BA1 UDP: control / serial / audio
    serial_port: int = 50002
    audio_port: int = 50003


@dataclass
class AudioCfg:
    kind: str = "none"         # usb-codec | lan | none


@dataclass
class ScopeCfg:
    kind: str = "native"       # native (CI-V 27 00) | audio (AF FFT) | none


@dataclass
class Transports:
    serial: SerialCfg = field(default_factory=SerialCfg)
    network: NetworkCfg = field(default_factory=NetworkCfg)
    audio: AudioCfg = field(default_factory=AudioCfg)
    scope: ScopeCfg = field(default_factory=ScopeCfg)


@dataclass
class Capabilities:
    """What the radio supports — drives which controls the adaptive UI renders."""
    preamp: bool = True
    att: bool = True
    tuner: bool = False
    dual_watch: bool = False
    vfo_select: bool = False   # a CAT active-VFO (A/B) selector. FT-991A: no.
    vfo_swap: bool = True
    split: bool = True
    rit: bool = True
    duplex: bool = True
    rx_dsp: list = field(default_factory=list)    # ["nb","nr","anotch","mnotch"]
    tx_funcs: list = field(default_factory=list)  # ["comp","vox","mon"]
    tbw: bool = True
    meters: list = field(default_factory=list)    # ["S","PO","SWR","ALC","COMP","Vd","Id"]
    cw_tx: bool = False
    menus: bool = False
    narrow: bool = False        # NAR/WIDE IF-filter toggle (Yaesu NA)
    fm_tone: bool = False       # FM Tone/DCS + repeater-shift panel (Yaesu CT/CN/OS), FM modes only


@dataclass
class MenuItem:
    """One radio SET-menu item, fully declarative (see backend/menu_engine.py).
    `digits` is the fixed value-field width on the wire — a wrong width makes the radio
    silently ignore the command, so it is the #1 correctness factor."""
    num: int
    name: str
    group: str
    kind: str = "enum"         # enum | int | signed-int
    digits: int = 1
    ex_width: int = 3          # Yaesu EX menu-number width: FT-991A flat EXNNN=3; FT-891 GGNN=4
    options: list = field(default_factory=list)   # enum option labels (index = wire value)
    min: int = 0
    max: int = 0
    step: int = 1
    unit: str = ""
    readonly: bool = False
    critical: bool = False     # connection/transmit-sensitive -> UI confirms before write
    note: str = ""


@dataclass
class RadioProfile:
    id: str                       # "ic9700"
    name: str                     # "IC-9700"
    civ_addr: int                 # default CI-V address
    modes: list[str]              # mode buttons, in order
    bands: list[Band]
    filter_bw: dict               # mode_name -> {1: hz, 2: hz, 3: hz}
    mod_dataoff: tuple            # CI-V 1A 05 data number for DATA OFF MOD
    lan_mod_level: tuple          # CI-V 1A 05 data number for LAN MOD Level
    default_freq: int             # simulator / initial frequency
    steps: list                   # [(value_hz, label), ...]
    default_step: int
    dual_watch: bool = False      # True for dual-receiver radios (IC-9700 MAIN/SUB)
    has_preamp: bool = True        # P.AMP available
    has_att: bool = True           # attenuator available
    has_tuner: bool = False        # internal antenna tuner (in/out toggle) available
    # Safety: hardware TX time-out timer set on connect (backstop if the control link drops
    # mid-transmit). Icom = CI-V 1A 05 (datanum_hi, datanum_lo, value); Yaesu = a CAT EX
    # string. ~120 s where the radio allows — Icom's coarsest non-OFF step is 3 min, so the
    # app's 120 s PTT failsafe stays the precise limit there. See docs/ADDING-A-RADIO.md.
    tot_civ: tuple = ()            # Icom: (0x00, datanum_lo, value); () = none
    tot_cat: str = ""              # Yaesu: full CAT EX string; "" = none
    # CW transmit: how this radio sends a typed CW message. Operator-triggered — one
    # bounded message per TX press, fully under the operator's control, and the RIG
    # generates the CW at the keyer speed we set (cleaner than host-timed keying; the
    # Icom carrier can't be keyed by PTT in CW mode anyway). "" = unsupported;
    # "civ17" = Icom Send-CW-message (CI-V 17 + semi BK-IN + 14 0C keyer speed);
    # "line"  = host-timed keying of a serial control line (Yaesu FT-991A: PC KEYING =
    #           RTS/DTR; the FT-991A has NO arbitrary-text CW CAT command, so this is the
    #           same mechanism N1MM / fldigi / cwdaemon use). See docs/ADDING-A-RADIO.md.
    cw_send: str = ""
    cw_line: str = ""             # for cw_send="line": control line to key ("rts" | "dtr")
    # Yaesu safety: force PC KEYING = OFF on connect when we are NOT managing line keying, so a
    # stray control line can never key the rig. Full CAT EX string incl. the radio's own
    # PC-KEYING menu number/width (FT-991A menu 060 -> "EX0600;"; FT-891 menu 07-12 -> "EX07120;").
    # "" = the radio has no such menu / nothing to disarm.
    pc_keying_off_cat: str = ""
    make: str = "Icom"            # manufacturer, shown before the model in the picker
    protocol: str = "civ"         # "civ" (Icom CI-V) | "yaesu" (Yaesu CAT)
    has_scope: bool = True        # False = no spectrum/waterfall over the control link
    has_network: bool = True      # False = COM-only (no RS-BA1/LAN) -> hide the LAN option
    default_baud: int = 115200    # default serial baud for the connection bar
    # connect_help: radio-side settings to set before connecting; rendered in the
    # "?" popover. [{"title": str, "items": [str, ...]}, ...]
    connect_help: list = field(default_factory=list)
    preamp_labels: list = field(default_factory=lambda: ["OFF", "P.AMP"])  # preamp states (index = code)
    # v2 declarative blocks — synthesized from the flat flags above when not given,
    # so the existing profiles keep working unchanged (see __post_init__).
    transports: Optional["Transports"] = None
    capabilities: Optional["Capabilities"] = None
    menu: list = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.capabilities is None:
            self.capabilities = Capabilities(
                preamp=self.has_preamp, att=self.has_att, tuner=self.has_tuner,
                dual_watch=self.dual_watch,
                vfo_select=self.protocol != "yaesu",   # FT-991A has no CAT active-VFO selector
                vfo_swap=True,
                split=True, rit=True, duplex=True,
                rx_dsp=["nb", "nr", "anotch", "mnotch"],
                tx_funcs=["comp", "vox", "mon"], tbw=True,
                meters=["S", "PO", "SWR", "ALC", "COMP", "Vd", "Id"],
                cw_tx=bool(self.cw_send), menus=bool(self.menu),
                narrow=self.protocol == "yaesu",
                fm_tone=self.protocol == "yaesu",
            )
        elif self.menu and not self.capabilities.menus:
            self.capabilities.menus = True
        if self.transports is None:
            self.transports = Transports(
                serial=SerialCfg(baud=self.default_baud,
                                 stopbits=2 if self.protocol == "yaesu" else 1),
                network=NetworkCfg(enabled=self.has_network),
                audio=AudioCfg(kind="lan" if self.has_network else "usb-codec"),
                scope=ScopeCfg(kind="native" if self.has_scope else "audio"),
            )

    def band_default(self, name: str) -> Optional[int]:
        for b in self.bands:
            if b.name == name:
                return b.default
        return None

    def to_json(self) -> dict:
        return {
            "id": self.id, "name": self.name,
            "make": self.make, "protocol": self.protocol, "has_scope": self.has_scope,
            "has_network": self.has_network,
            "default_baud": self.default_baud, "connect_help": self.connect_help,
            "modes": self.modes,
            "bands": [{"name": b.name, "lo": b.lo, "hi": b.hi, "def": b.default} for b in self.bands],
            "steps": [{"v": v, "label": lbl} for v, lbl in self.steps],
            "default_step": self.default_step,
            "default_freq": self.default_freq,
            "dual_watch": self.dual_watch,
            "has_preamp": self.has_preamp,
            "has_att": self.has_att,
            "has_tuner": self.has_tuner,
            "preamp_labels": self.preamp_labels,
            "has_cw_tx": bool(self.cw_send),
            "transports": asdict(self.transports) if self.transports else None,
            "capabilities": asdict(self.capabilities) if self.capabilities else None,
            "menu": [asdict(m) for m in self.menu],
            "has_menu": bool(self.menu),
        }


_SSB = {1: 3000, 2: 2400, 3: 1800}
_CW = {1: 1200, 2: 500, 3: 250}
_RTTY = {1: 2400, 2: 500, 3: 250}

from .menus.ic9700_menu import IC9700_MENU  # noqa: E402  (after MenuItem is defined above)

IC9700 = RadioProfile(
    id="ic9700", name="IC-9700", civ_addr=0xA2,
    modes=["LSB", "USB", "CW", "CW-R", "AM", "FM", "RTTY", "DV"],
    bands=[
        Band("144", 144_000_000, 148_000_000, 144_200_000),
        Band("430", 430_000_000, 450_000_000, 432_100_000),
        Band("1200", 1_240_000_000, 1_300_000_000, 1_296_100_000),
    ],
    filter_bw={
        "LSB": _SSB, "USB": _SSB, "CW": _CW, "CW-R": _CW,
        "RTTY": _RTTY, "RTTY-R": _RTTY,
        "AM": {1: 9000, 2: 6000, 3: 3000}, "FM": {1: 15000, 2: 7000, 3: 7000},
        "DV": {1: 6250, 2: 6250, 3: 6250}, "DD": {1: 130000, 2: 130000, 3: 130000},
    },
    mod_dataoff=(0x01, 0x15), lan_mod_level=(0x01, 0x14),
    tot_civ=(0x00, 0x41, 0x01),     # TX TOT 0041 = 3 min (no 2-min step; app's 120 s failsafe is the precise limit)
    cw_send="civ17",                # CW message TX via CI-V 17 (semi BK-IN keys it)
    default_freq=144_200_000,
    steps=[(10, "10 Hz"), (100, "100 Hz"), (1000, "1 kHz"), (5000, "5 kHz"),
           (10000, "10 kHz"), (12500, "12.5 kHz"), (25000, "25 kHz")],
    default_step=25000,
    dual_watch=True,                # MAIN + SUB receivers
    menu=IC9700_MENU,
    connect_help=[
        {"title": "USB (CI-V)", "items": [
            "Install Icom's USB driver, then connect [USB] to the PC and pick its COM port above.",
            "MENU > SET > Connectors > CI-V:",
            "CI-V USB Baud Rate = Auto (default) — or 115200 to match the Baud box",
            "CI-V Address = A2h (default)",
            "CI-V Transceive = ON",
            "CI-V USB Echo Back = OFF",
        ]},
        {"title": "Network (LAN / RS-BA1)", "items": [
            "Connect [LAN] to your network. MENU > SET > Network > Network Control = ON.",
            "Set a Network User1 ID + Password (8-16 chars, not all the same); note the radio's IP.",
            "Control port (UDP) = 50001 (default). Restart the radio after network changes.",
            "Enter the IP, port 50001 and the user/password above.",
        ]},
    ],
)

from .menus.ic7300mk2_menu import IC7300MK2_MENU  # noqa: E402  (after MenuItem is defined above)

IC7300MK2 = RadioProfile(
    id="ic7300mk2", name="IC-7300MK2", civ_addr=0xB6,
    modes=["LSB", "USB", "CW", "CW-R", "RTTY", "RTTY-R", "AM", "FM"],
    bands=[
        Band("160", 1_800_000, 2_000_000, 1_840_000),
        Band("80", 3_500_000, 4_000_000, 3_700_000),
        Band("60", 5_300_000, 5_410_000, 5_357_000),
        Band("40", 7_000_000, 7_300_000, 7_150_000),
        Band("30", 10_100_000, 10_150_000, 10_130_000),
        Band("20", 14_000_000, 14_350_000, 14_200_000),
        Band("17", 18_068_000, 18_168_000, 18_130_000),
        Band("15", 21_000_000, 21_450_000, 21_300_000),
        Band("12", 24_890_000, 24_990_000, 24_950_000),
        Band("10", 28_000_000, 29_700_000, 28_400_000),
        Band("6", 50_000_000, 54_000_000, 50_150_000),
    ],
    filter_bw={
        "LSB": _SSB, "USB": _SSB, "CW": _CW, "CW-R": _CW,
        "RTTY": _RTTY, "RTTY-R": _RTTY,
        "AM": {1: 9000, 2: 6000, 3: 3000}, "FM": {1: 15000, 2: 10000, 3: 7000},
    },
    mod_dataoff=(0x00, 0x84), lan_mod_level=(0x00, 0x83),
    tot_civ=(0x00, 0x32, 0x01),     # TX TOT 0032 = 3 min (closest to 120 s)
    cw_send="civ17",                # CW message TX via CI-V 17 (semi BK-IN keys it)
    default_freq=14_074_000,
    steps=[(1, "1 Hz"), (10, "10 Hz"), (100, "100 Hz"), (1000, "1 kHz"),
           (5000, "5 kHz"), (9000, "9 kHz"), (10000, "10 kHz")],
    default_step=100,
    menu=IC7300MK2_MENU,
    connect_help=[
        {"title": "USB (CI-V)", "items": [
            "Install Icom's USB driver, then connect [USB] to the PC and pick its COM port above.",
            "MENU > SET > Connectors > CI-V:",
            "Set CI-V USB Port to 'Unlink from [REMOTE]' first — while linked, USB baud is capped at 19200.",
            "CI-V USB Baud Rate = 115200 (match the Baud box)",
            "CI-V Address = B6h (default)",
            "CI-V Transceive = ON",
            "CI-V USB Echo Back = OFF",
        ]},
        {"title": "Network (LAN / RS-BA1)", "items": [
            "Connect [LAN] to your network. MENU > SET > Network > Network Control = ON.",
            "Set a Network User1 ID + Password (8-16 chars, not all the same); note the radio's IP.",
            "Control port (UDP) = 50001 (default). Restart the radio after network changes.",
            "Enter the IP, port 50001 and the user/password above.",
        ]},
    ],
)

from .menus.ft991a_menu import FT991A_MENU  # noqa: E402  (after MenuItem is defined above)

# Yaesu FT-991A — all-mode HF/50/144/430 MHz. Yaesu CAT (serial), COM-only: no
# Ethernet, and NO band scope over CAT (display-only scope), so no waterfall.
# civ_addr / mod_* are unused by the Yaesu path but the dataclass requires them.
FT991A = RadioProfile(
    id="ft991a", name="FT-991A", civ_addr=0x00,
    make="Yaesu", protocol="yaesu", has_scope=False, default_baud=38400,
    # mode buttons match the radio's own labels + order (CW-USB=MD3, CW-LSB=MD7,
    # RTTY-LSB=MD6, RTTY-USB=MD9, DATA-LSB=MD8, DATA-USB=MDC, DATA-FM=MDA, C4FM=MDE).
    modes=["LSB", "USB", "AM", "CW-LSB", "CW-USB", "FM", "RTTY-LSB", "RTTY-USB",
           "C4FM", "DATA-LSB", "DATA-USB", "DATA-FM"],
    bands=[
        Band("160", 1_800_000, 2_000_000, 1_840_000),
        Band("80", 3_500_000, 4_000_000, 3_700_000),
        Band("60", 5_330_500, 5_403_500, 5_330_500),
        Band("40", 7_000_000, 7_300_000, 7_074_000),
        Band("30", 10_100_000, 10_150_000, 10_136_000),
        Band("20", 14_000_000, 14_350_000, 14_074_000),
        Band("17", 18_068_000, 18_168_000, 18_100_000),
        Band("15", 21_000_000, 21_450_000, 21_074_000),
        Band("12", 24_890_000, 24_990_000, 24_915_000),
        Band("10", 28_000_000, 29_700_000, 28_074_000),
        Band("6", 50_000_000, 54_000_000, 50_313_000),
        Band("2", 144_000_000, 148_000_000, 144_200_000),
        Band("70", 430_000_000, 450_000_000, 432_100_000),
    ],
    filter_bw={
        "LSB": _SSB, "USB": _SSB, "CW-USB": _CW, "CW-LSB": _CW,
        "RTTY-LSB": _RTTY, "RTTY-USB": _RTTY, "DATA-LSB": _SSB, "DATA-USB": _SSB,
        "AM": {1: 9000, 2: 6000, 3: 3000}, "FM": {1: 16000, 2: 9000, 3: 9000},
        "DATA-FM": {1: 16000, 2: 9000, 3: 9000}, "C4FM": {1: 16000, 2: 16000, 3: 16000},
    },
    mod_dataoff=(0x00, 0x00), lan_mod_level=(0x00, 0x00),
    tot_cat="EX03602;",             # menu 036 TX TOT = 2 min = 120 s (exact)
    # CW TX: host-timed DTR keying on the SECOND (Standard) USB port — the proven method
    # (N1MM/fldigi/cwdaemon). CAT runs on the Enhanced port; on connect the app sets
    # menu 033 CAT RTS=DISABLE + menu 060 PC KEYING=DTR and opens the sibling port DTR-low.
    # RTS keying on the CAT port is forbidden (collides with CAT RTS -> breaks PTT).
    cw_send="line", cw_line="dtr",
    default_freq=14_074_000,
    steps=[(10, "10 Hz"), (100, "100 Hz"), (1000, "1 kHz"), (2500, "2.5 kHz"),
           (5000, "5 kHz"), (10000, "10 kHz"), (25000, "25 kHz")],
    default_step=100,
    # FT-991A has IPO/AMP1 (PA0), a 12 dB RF ATT (RA0), and an internal auto ATU (AC).
    has_preamp=True, has_att=True, has_tuner=True, has_network=False,
    preamp_labels=["IPO", "AMP1", "AMP2"],
    menu=FT991A_MENU,
    connect_help=[
        {"title": "USB CAT (COM only)", "items": [
            "Install the Yaesu USB driver first. The radio shows TWO COM ports — pick the Enhanced (CAT) port above, not Standard.",
            "MENU EX-031 (CAT RATE) = 38400 to match the Baud box (the factory default is lower — change it, or set the Baud box to your radio's rate).",
            "Serial format is 8 data / no parity / 2 stop bits (8N2) — handled for you.",
            "No network: the FT-991A is COM-only.",
        ]},
    ],
)

from .menus.ft891_menu import FT891_MENU  # noqa: E402  (after MenuItem is defined above)

# Yaesu FT-891 — HF/50 MHz all-mode mobile. Yaesu CAT (serial), COM-only: no Ethernet and
# NO band scope over CAT, so the app shows an audio (AF) scope. Typically reached over a
# Digirig (one USB serial port for CAT + a separate USB sound card for RX/TX audio), so the
# radio audio rides the host sound-card path (v0.2.16). No internal ATU (menu 16-15 TUNER
# SELECT is external/ATAS only), a 2-state preamp (IPO/AMP), a single 12 dB ATT, 5-100 W. The
# SET menu is grouped (GG-NN) and 4-digit over CAT (EXGGNN) -> the table's ex_width=4.
# civ_addr / mod_* are unused by the Yaesu path but the dataclass requires them.
FT891 = RadioProfile(
    id="ft891", name="FT-891", civ_addr=0x00,
    make="Yaesu", protocol="yaesu", has_scope=False, has_network=False, default_baud=38400,
    # radio-accurate mode labels (no C4FM / DATA-FM on the FT-891)
    modes=["LSB", "USB", "AM", "CW-LSB", "CW-USB", "FM", "RTTY-LSB", "RTTY-USB",
           "DATA-LSB", "DATA-USB"],
    bands=[
        Band("160", 1_800_000, 2_000_000, 1_840_000),
        Band("80", 3_500_000, 4_000_000, 3_700_000),
        Band("60", 5_330_500, 5_403_500, 5_330_500),
        Band("40", 7_000_000, 7_300_000, 7_074_000),
        Band("30", 10_100_000, 10_150_000, 10_136_000),
        Band("20", 14_000_000, 14_350_000, 14_074_000),
        Band("17", 18_068_000, 18_168_000, 18_100_000),
        Band("15", 21_000_000, 21_450_000, 21_074_000),
        Band("12", 24_890_000, 24_990_000, 24_915_000),
        Band("10", 28_000_000, 29_700_000, 28_074_000),
        Band("6", 50_000_000, 54_000_000, 50_313_000),
    ],
    filter_bw={
        "LSB": _SSB, "USB": _SSB, "CW-USB": _CW, "CW-LSB": _CW,
        "RTTY-LSB": _RTTY, "RTTY-USB": _RTTY, "DATA-LSB": _SSB, "DATA-USB": _SSB,
        "AM": {1: 9000, 2: 6000, 3: 3000}, "FM": {1: 16000, 2: 9000, 3: 9000},
    },
    mod_dataoff=(0x00, 0x00), lan_mod_level=(0x00, 0x00),
    tot_cat="EX051402;",            # menu 05-14 TX TOT = 02 min = 120 s (exact backstop)
    pc_keying_off_cat="EX07120;",   # menu 07-12 PC KEYING = OFF on connect (safety default)
    cw_send="",                     # no CAT text-CW yet: FT-891 KY only plays stored KM memories
    default_freq=14_074_000,
    steps=[(10, "10 Hz"), (100, "100 Hz"), (1000, "1 kHz"), (2500, "2.5 kHz"),
           (5000, "5 kHz"), (10000, "10 kHz"), (25000, "25 kHz")],
    default_step=100,
    # FT-891: IPO/AMP preamp (PA0 0/1), single 12 dB RF ATT (RA0), no internal ATU.
    has_preamp=True, has_att=True, has_tuner=False,
    preamp_labels=["IPO", "AMP"],
    capabilities=Capabilities(
        preamp=True, att=True, tuner=False, dual_watch=False,
        vfo_select=False,          # Yaesu CAT has no active-VFO (A/B) selector
        vfo_swap=True, split=True, rit=True, duplex=True,
        rx_dsp=["nb", "nr", "anotch", "mnotch"],
        tx_funcs=["comp", "vox", "mon"], tbw=True,
        meters=["S", "PO", "SWR", "ALC", "COMP", "Id"],   # FT-891 RM: 7=ID is the top; no VDD (8)
        cw_tx=False, menus=True,
        narrow=True, fm_tone=True,
    ),
    menu=FT891_MENU,
    connect_help=[
        {"title": "USB CAT via Digirig (COM only)", "items": [
            "Install the Digirig / CH340 USB-serial driver, then pick the Digirig's COM port above.",
            "MENU 05-06 [CAT RATE] = 38400 bps to match the Baud box (factory default is 4800 — raise it, or set the Baud box to match your radio).",
            "MENU 05-08 [CAT RTS] to suit your Digirig cable (DISABLE if RTS on that port keys PTT).",
            "Serial format is 8 data / no parity / 2 stop bits (8N2) — handled for you.",
            "Audio: the radio's RX/TX audio is on the Digirig's USB sound card — pick it as Radio RX / Radio TX in the audio selector (Mic In stays your computer mic).",
            "No network: the FT-891 is COM-only.",
        ]},
    ],
)

PROFILES = {p.id: p for p in (IC9700, IC7300MK2, FT991A, FT891)}
DEFAULT_PROFILE_ID = "ic9700"

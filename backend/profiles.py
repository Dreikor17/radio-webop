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

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Band:
    name: str          # button label, e.g. "20" or "144"
    lo: int            # band low edge (Hz)
    hi: int            # band high edge (Hz)
    default: int       # frequency the band button tunes to (Hz)


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
    power_zero_bands: list        # freqs to visit to zero per-band power; [] = single set
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
    make: str = "Icom"            # manufacturer, shown before the model in the picker
    protocol: str = "civ"         # "civ" (Icom CI-V) | "yaesu" (Yaesu CAT)
    has_scope: bool = True        # False = no spectrum/waterfall over the control link
    has_network: bool = True      # False = COM-only (no RS-BA1/LAN) -> hide the LAN option
    default_baud: int = 115200    # default serial baud for the connection bar
    # connect_help: radio-side settings to set before connecting; rendered in the
    # "?" popover. [{"title": str, "items": [str, ...]}, ...]
    connect_help: list = field(default_factory=list)

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
        }


_SSB = {1: 3000, 2: 2400, 3: 1800}
_CW = {1: 1200, 2: 500, 3: 250}
_RTTY = {1: 2400, 2: 500, 3: 250}

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
    power_zero_bands=[145_000_000, 435_000_000, 1_295_000_000],
    default_freq=144_200_000,
    steps=[(10, "10 Hz"), (100, "100 Hz"), (1000, "1 kHz"), (5000, "5 kHz"),
           (10000, "10 kHz"), (12500, "12.5 kHz"), (25000, "25 kHz")],
    default_step=25000,
    dual_watch=True,                # MAIN + SUB receivers
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
    power_zero_bands=[],            # HF RF power is a single setting
    default_freq=14_074_000,
    steps=[(1, "1 Hz"), (10, "10 Hz"), (100, "100 Hz"), (1000, "1 kHz"),
           (5000, "5 kHz"), (9000, "9 kHz"), (10000, "10 kHz")],
    default_step=100,
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

# Yaesu FT-991A — all-mode HF/50/144/430 MHz. Yaesu CAT (serial), COM-only: no
# Ethernet, and NO band scope over CAT (display-only scope), so no waterfall.
# civ_addr / mod_* are unused by the Yaesu path but the dataclass requires them.
FT991A = RadioProfile(
    id="ft991a", name="FT-991A", civ_addr=0x00,
    make="Yaesu", protocol="yaesu", has_scope=False, default_baud=38400,
    modes=["LSB", "USB", "CW", "CW-R", "AM", "FM", "RTTY", "RTTY-R",
           "DATA-L", "DATA-U", "DATA-FM", "C4FM"],
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
        "LSB": _SSB, "USB": _SSB, "CW": _CW, "CW-R": _CW,
        "RTTY": _RTTY, "RTTY-R": _RTTY, "DATA-L": _SSB, "DATA-U": _SSB,
        "AM": {1: 9000, 2: 6000, 3: 3000}, "FM": {1: 16000, 2: 9000, 3: 9000},
        "DATA-FM": {1: 16000, 2: 9000, 3: 9000}, "C4FM": {1: 16000, 2: 16000, 3: 16000},
    },
    mod_dataoff=(0x00, 0x00), lan_mod_level=(0x00, 0x00),
    tot_cat="EX03602;",             # menu 036 TX TOT = 2 min = 120 s (exact)
    power_zero_bands=[],
    default_freq=14_074_000,
    steps=[(10, "10 Hz"), (100, "100 Hz"), (1000, "1 kHz"), (2500, "2.5 kHz"),
           (5000, "5 kHz"), (10000, "10 kHz"), (25000, "25 kHz")],
    default_step=100,
    # FT-991A has IPO/AMP1 (PA0), a 12 dB RF ATT (RA0), and an internal auto ATU (AC).
    has_preamp=True, has_att=True, has_tuner=True, has_network=False,
    connect_help=[
        {"title": "USB CAT (COM only)", "items": [
            "Install the Yaesu USB driver first. The radio shows TWO COM ports — pick the Enhanced (CAT) port above, not Standard.",
            "MENU EX-031 (CAT RATE) = 38400 to match the Baud box (the factory default is lower — change it, or set the Baud box to your radio's rate).",
            "Serial format is 8 data / no parity / 2 stop bits (8N2) — handled for you.",
            "No network: the FT-991A is COM-only.",
        ]},
    ],
)

PROFILES = {p.id: p for p in (IC9700, IC7300MK2, FT991A)}
DEFAULT_PROFILE_ID = "ic9700"

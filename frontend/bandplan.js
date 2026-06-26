/* US amateur band-plan overlay data.
   VHF/UHF = ARRL voluntary band plan (segment by use); HF/6m = FCC Part 97
   license-class privileges (segment by lowest class + mode).
   Frequencies in MHz. Compiled + adversarially verified (workflow arrl-bandplan-data,
   against the ARRL band plan + FCC Part 97.301/97.305). */
(function (global) {
  "use strict";

  // kind -> base rgba() prefix (alpha appended by the renderer)
  global.BANDPLAN_COLORS = {
    // --- VHF/UHF use ---
    cw:         "rgba(120,200,255,",
    weaksignal: "rgba(90,180,255,",
    ssb:        "rgba(80,220,150,",
    fm:         "rgba(255,180,80,",
    repeater:   "rgba(255,140,90,",
    simplex:    "rgba(255,210,120,",
    digital:    "rgba(200,140,255,",
    satellite:  "rgba(255,120,200,",
    beacon:     "rgba(160,160,170,",
    calling:    "rgba(255,90,90,",
    mixed:      "rgba(130,150,175,",
    // --- HF license class ---
    tech:       "rgba(90,210,140,",
    gen:        "rgba(110,175,255,",
    extra:      "rgba(255,165,90,",
    all:        "rgba(150,200,170,",
  };

  // Short human label per kind (for the legend / tooltip header).
  global.BANDPLAN_KIND_LABEL = {
    cw: "CW", weaksignal: "Weak-signal", ssb: "SSB/phone", fm: "FM",
    repeater: "Repeater", simplex: "Simplex", digital: "Digital", satellite: "Satellite",
    beacon: "Beacon", calling: "Calling freq", mixed: "Mixed/other",
    tech: "Technician+", gen: "General+", extra: "Extra (sub-band)", all: "All classes",
  };

  // lo/hi in MHz. Contiguous within each band (60m is channelized, so it has gaps).
  global.BANDPLAN = [
    // ===== 2 m (ARRL voluntary band plan) =====
    { lo: 144.000, hi: 144.050, label: "EME (CW)", kind: "weaksignal", desc: "Earth-Moon-Earth (moonbounce) CW. CW-only by FCC rule below 144.1 MHz. Technician class and above (Novices have no 2 m privileges)." },
    { lo: 144.050, hi: 144.100, label: "General CW", kind: "cw", desc: "General CW and weak-signal work. CW-only segment by FCC rule (144.0-144.1)." },
    { lo: 144.100, hi: 144.200, label: "EME/wk-sig SSB", kind: "weaksignal", desc: "EME and weak-signal SSB. Phone permitted above 144.1 MHz; CW also used." },
    { lo: 144.200, hi: 144.275, label: "SSB call 144.200", kind: "calling", desc: "General SSB. 144.200 MHz is the 2 m national SSB calling frequency; move off to a working freq after contact." },
    { lo: 144.275, hi: 144.300, label: "Beacons", kind: "beacon", desc: "Propagation beacons — automated CW/digital beacons for monitoring band openings." },
    { lo: 144.300, hi: 144.500, label: "OSCAR subband", kind: "satellite", desc: "New OSCAR (satellite) subband. CW/SSB/FM satellite uplinks/downlinks." },
    { lo: 144.500, hi: 144.600, label: "Lin xlatr in", kind: "repeater", desc: "Linear translator (transponder) inputs — uplink side of analog linear translators." },
    { lo: 144.600, hi: 144.900, label: "FM rptr inputs", kind: "repeater", desc: "FM repeater inputs (receive frequencies)." },
    { lo: 144.900, hi: 145.100, label: "Wk sig/simplex", kind: "digital", desc: "Weak-signal and FM simplex; packet/digital on 145.01/.03/.05/.07/.09 MHz." },
    { lo: 145.100, hi: 145.200, label: "Lin xlatr out", kind: "repeater", desc: "Linear translator (transponder) outputs — downlink side." },
    { lo: 145.200, hi: 145.500, label: "FM rptr outputs", kind: "repeater", desc: "FM repeater outputs (transmit frequencies)." },
    { lo: 145.500, hi: 145.800, label: "Misc/experiment", kind: "mixed", desc: "Miscellaneous and experimental modes; digital and special-use." },
    { lo: 145.800, hi: 146.000, label: "OSCAR satellite", kind: "satellite", desc: "OSCAR satellite subband — protected for amateur-satellite uplinks/downlinks (CW/SSB/FM)." },
    { lo: 146.000, hi: 146.400, label: "Rptr inputs", kind: "repeater", desc: "FM repeater inputs (146.01-146.37, 20 kHz spacing). 0.6 MHz pairs: input here, output +0.6 (146.61-146.97)." },
    { lo: 146.400, hi: 146.600, label: "Simplex 146.52", kind: "simplex", desc: "FM simplex (146.40-146.58). 146.52 MHz is the national FM simplex calling frequency." },
    { lo: 146.600, hi: 147.000, label: "Rptr outputs", kind: "repeater", desc: "FM repeater outputs (146.61-146.97). Paired with 146.01-146.37 inputs (-0.6 MHz)." },
    { lo: 147.000, hi: 147.400, label: "Rptr outputs", kind: "repeater", desc: "FM repeater outputs (147.00-147.39). Paired with 147.60-147.99 inputs (+0.6 MHz)." },
    { lo: 147.400, hi: 147.600, label: "Simplex", kind: "simplex", desc: "FM simplex (147.42-147.57)." },
    { lo: 147.600, hi: 148.000, label: "Rptr inputs", kind: "repeater", desc: "FM repeater inputs (147.60-147.99). Paired with 147.00-147.39 outputs (+0.6 MHz)." },

    // ===== 70 cm (ARRL voluntary band plan) =====
    { lo: 420.000, hi: 426.000, label: "ATV/links", kind: "mixed", desc: "ATV repeater/simplex (421.25 MHz video carrier), control links, experimental. Geographic/power limits per FCC US270." },
    { lo: 426.000, hi: 432.000, label: "ATV simplex", kind: "mixed", desc: "ATV simplex (427.250 MHz video carrier). Amateur television." },
    { lo: 432.000, hi: 432.070, label: "EME", kind: "weaksignal", desc: "Earth-Moon-Earth (moonbounce) weak-signal CW/digital." },
    { lo: 432.070, hi: 432.100, label: "Weak-signal CW", kind: "cw", desc: "Weak-signal CW only, leading up to the 432.100 calling frequency." },
    { lo: 432.100, hi: 432.300, label: "SSB/CW call", kind: "calling", desc: "432.100 = 70 cm SSB/CW weak-signal calling frequency; mixed-mode weak-signal above it." },
    { lo: 432.300, hi: 432.400, label: "Beacons", kind: "beacon", desc: "Propagation beacons (continuous CW/digital)." },
    { lo: 432.400, hi: 433.000, label: "Weak-signal", kind: "weaksignal", desc: "Mixed-mode and weak-signal work (SSB/CW/digital)." },
    { lo: 433.000, hi: 435.000, label: "Aux/links", kind: "repeater", desc: "Auxiliary and repeater links (control/linking)." },
    { lo: 435.000, hi: 438.000, label: "Satellite", kind: "satellite", desc: "Satellite-only (internationally). Amateur-satellite uplinks/downlinks; keep clear for sat use." },
    { lo: 438.000, hi: 442.000, label: "ATV in/links", kind: "mixed", desc: "ATV repeater inputs (439.250 MHz video carrier) and links. ARRL ATV/links block is 438-444 and overlaps the repeater subband." },
    { lo: 442.000, hi: 445.000, label: "Repeaters", kind: "repeater", desc: "Repeater inputs/outputs (local coordination); 5 MHz standard offset. Overlaps ATV 438-444." },
    { lo: 445.000, hi: 447.000, label: "Rptr/simplex", kind: "simplex", desc: "Aux/control links, repeaters and simplex (local option). 446.000 = national FM simplex calling frequency." },
    { lo: 447.000, hi: 450.000, label: "Repeaters", kind: "repeater", desc: "Repeater inputs/outputs (local option); 5 MHz standard offset." },

    // ===== 23 cm (ARRL voluntary band plan) =====
    { lo: 1240.000, hi: 1246.000, label: "ATV ch 1", kind: "mixed", desc: "Amateur Television channel 1 (wideband FM/analog & digital ATV)." },
    { lo: 1246.000, hi: 1248.000, label: "P2P link in", kind: "digital", desc: "Point-to-point links (FM & digital). Paired with 1258-1260 MHz; 25 kHz spacing." },
    { lo: 1248.000, hi: 1252.000, label: "Mixed/digital", kind: "mixed", desc: "General digital and experimental use; data/links per local coordination." },
    { lo: 1252.000, hi: 1258.000, label: "ATV ch 2", kind: "mixed", desc: "Amateur Television channel 2 (wideband FM/analog & digital ATV)." },
    { lo: 1258.000, hi: 1260.000, label: "P2P link out", kind: "digital", desc: "Point-to-point links (FM & digital). Paired with 1246-1248 MHz; 25 kHz spacing." },
    { lo: 1260.000, hi: 1270.000, label: "Sat uplink", kind: "satellite", desc: "Amateur-satellite uplinks (secondary, non-interference). Also experimental/simplex ATV." },
    { lo: 1270.000, hi: 1276.000, label: "Rptr inputs", kind: "repeater", desc: "FM & digital repeater inputs (25 kHz). Paired with outputs at 1282-1288 MHz." },
    { lo: 1276.000, hi: 1282.000, label: "ATV ch 3", kind: "mixed", desc: "Amateur Television channel 3 (wideband FM/analog & digital ATV)." },
    { lo: 1282.000, hi: 1288.000, label: "Rptr outputs", kind: "repeater", desc: "FM & digital repeater outputs (25 kHz). Paired with inputs at 1270-1276 MHz." },
    { lo: 1288.000, hi: 1290.000, label: "Mixed/wideband", kind: "mixed", desc: "Broadband experimental & simplex ATV (part of the ARRL 1288-1294 broadband segment)." },
    { lo: 1290.000, hi: 1294.000, label: "Rptr out (reg)", kind: "repeater", desc: "FM & digital repeater outputs, regional option (25 kHz). Paired with regional inputs at 1270-1274 MHz." },
    { lo: 1294.000, hi: 1295.000, label: "FM simplex", kind: "simplex", desc: "FM simplex (25 kHz). 1294.5 MHz = national FM simplex calling frequency." },
    { lo: 1295.000, hi: 1295.800, label: "NB image/exp", kind: "mixed", desc: "Narrow-band image (SSTV/FAX/ACSSB) and experimental; no FM." },
    { lo: 1295.800, hi: 1296.080, label: "EME CW/SSB", kind: "weaksignal", desc: "Weak-signal EME (moonbounce): CW/SSB/digital. JT65c/Q65 near 1296.000 MHz." },
    { lo: 1296.080, hi: 1296.200, label: "SSB call 1296.1", kind: "calling", desc: "Weak-signal terrestrial CW/SSB. 1296.100 MHz = national CW/SSB calling frequency." },
    { lo: 1296.200, hi: 1296.400, label: "CW beacons", kind: "beacon", desc: "Propagation beacons and weak-signal digital." },
    { lo: 1296.400, hi: 1297.000, label: "Gen narrowband", kind: "weaksignal", desc: "General narrow-band weak-signal: CW/SSB/digital." },
    { lo: 1297.000, hi: 1300.000, label: "Digital", kind: "digital", desc: "Digital modes / data — packet and high-speed data." },

    // ===== HF + 6 m (FCC Part 97 license-class privileges) =====
    // 160 m
    { lo: 1.800, hi: 2.000, label: "Gen CW/phone", kind: "gen", desc: "General/Advanced/Extra: CW, RTTY/data, phone (SSB), image across the band. No Tech. 1.843-2.000 SSB; AM ~1.885." },
    // 80 m
    { lo: 3.500, hi: 3.525, label: "Extra CW", kind: "extra", desc: "Extra only: CW (also RTTY/data). Extra-exclusive 3.500-3.525." },
    { lo: 3.525, hi: 3.600, label: "CW/data", kind: "gen", desc: "Tech/Gen/Adv/Extra: CW & RTTY/data (no phone). Tech is CW-only here, 200 W. Data ~3.570-3.600." },
    { lo: 3.600, hi: 3.700, label: "Extra phone", kind: "extra", desc: "Extra only: CW, RTTY/data, phone (SSB), image. 3.690 DX window." },
    { lo: 3.700, hi: 3.800, label: "Adv phone", kind: "extra", desc: "Advanced/Extra: CW, RTTY/data, phone, image. No General phone below 3.800. 3.790-3.800 DX." },
    { lo: 3.800, hi: 4.000, label: "Gen phone", kind: "gen", desc: "General/Advanced/Extra: CW, RTTY/data, phone, image. 3.845 SSTV; 3.885 AM calling." },
    // 60 m (channelized, 2.8 kHz channels, 100 W PEP ERP, USB)
    { lo: 5.3291, hi: 5.3319, label: "Ch1 5330.5", kind: "gen", desc: "Channel 1, center 5330.5 kHz (USB dial 5332.0). Gen/Adv/Extra. 2.8 kHz; USB/CW/data; 100 W PEP ERP." },
    { lo: 5.3451, hi: 5.3479, label: "Ch2 5346.5", kind: "gen", desc: "Channel 2, center 5346.5 kHz (USB dial 5348.0). Gen/Adv/Extra. 2.8 kHz; USB/CW/data; 100 W PEP ERP." },
    { lo: 5.3556, hi: 5.3584, label: "Ch3 5357.0", kind: "gen", desc: "Channel 3, center 5357.0 kHz (USB dial 5358.5). Gen/Adv/Extra. 2.8 kHz; USB/CW/data; 100 W PEP ERP." },
    { lo: 5.3701, hi: 5.3729, label: "Ch4 5371.5", kind: "gen", desc: "Channel 4, center 5371.5 kHz (USB dial 5373.0). Gen/Adv/Extra. 2.8 kHz; USB/CW/data; 100 W PEP ERP." },
    { lo: 5.4021, hi: 5.4049, label: "Ch5 5403.5", kind: "gen", desc: "Channel 5, center 5403.5 kHz (USB dial 5405.0). Gen/Adv/Extra. 2.8 kHz; USB/CW/data; 100 W PEP ERP." },
    // 40 m
    { lo: 7.000, hi: 7.025, label: "Extra CW", kind: "extra", desc: "Extra only: CW (also RTTY/data). Extra-exclusive 7.000-7.025." },
    { lo: 7.025, hi: 7.125, label: "CW/data", kind: "gen", desc: "Tech/Gen/Adv/Extra: CW & RTTY/data (no phone). Tech is CW-only, 200 W. 7.040 RTTY/data DX." },
    { lo: 7.125, hi: 7.150, label: "Extra phone", kind: "extra", desc: "Extra only: CW, RTTY/data, phone (SSB), image." },
    { lo: 7.150, hi: 7.175, label: "Adv phone", kind: "extra", desc: "Advanced/Extra: CW, RTTY/data, phone, image. No General phone below 7.175." },
    { lo: 7.175, hi: 7.300, label: "Gen phone", kind: "gen", desc: "General/Advanced/Extra: CW, RTTY/data, phone, image. 7.171 SSTV; 7.290 AM calling." },
    // 30 m
    { lo: 10.100, hi: 10.150, label: "Gen CW/data", kind: "gen", desc: "General/Advanced/Extra: CW & RTTY/data only; NO phone/image. Secondary, 200 W PEP max. No Tech." },
    // 20 m
    { lo: 14.000, hi: 14.025, label: "Extra CW/data", kind: "extra", desc: "Extra only. CW, RTTY, data." },
    { lo: 14.025, hi: 14.150, label: "Gen CW/data", kind: "gen", desc: "General/Advanced/Extra. CW, RTTY, data." },
    { lo: 14.150, hi: 14.175, label: "Extra phone", kind: "extra", desc: "Extra only. CW, phone, image." },
    { lo: 14.175, hi: 14.225, label: "Adv+ phone", kind: "extra", desc: "Advanced/Extra. CW, phone, image. General phone starts 14.225." },
    { lo: 14.225, hi: 14.350, label: "Gen phone", kind: "gen", desc: "General/Advanced/Extra. CW, phone, image. 14.230 SSTV; 14.300 maritime net." },
    // 17 m
    { lo: 18.068, hi: 18.110, label: "Gen CW/data", kind: "gen", desc: "General/Advanced/Extra (no Tech). CW, RTTY, data." },
    { lo: 18.110, hi: 18.168, label: "Gen phone", kind: "gen", desc: "General/Advanced/Extra. CW, phone, image." },
    // 15 m
    { lo: 21.000, hi: 21.025, label: "Extra CW/data", kind: "extra", desc: "Extra only. CW, RTTY, data." },
    { lo: 21.025, hi: 21.200, label: "Tech/Gen CW", kind: "tech", desc: "Technician/Novice CW only (200 W); General/Advanced/Extra CW, RTTY, data." },
    { lo: 21.200, hi: 21.225, label: "Extra phone", kind: "extra", desc: "Extra only. CW, phone, image." },
    { lo: 21.225, hi: 21.275, label: "Adv+ phone", kind: "extra", desc: "Advanced/Extra. CW, phone, image. General phone starts 21.275." },
    { lo: 21.275, hi: 21.450, label: "Gen phone", kind: "gen", desc: "General/Advanced/Extra. CW, phone, image. (Expanded from 21.300 in 2006.)" },
    // 12 m
    { lo: 24.890, hi: 24.930, label: "Gen CW/data", kind: "gen", desc: "General/Advanced/Extra (no Tech). CW, RTTY, data." },
    { lo: 24.930, hi: 24.990, label: "Gen phone", kind: "gen", desc: "General/Advanced/Extra. CW, phone, image." },
    // 10 m
    { lo: 28.000, hi: 28.300, label: "Tech CW/data", kind: "tech", desc: "Technician/Novice (200 W) + General/Advanced/Extra. CW, RTTY, data." },
    { lo: 28.300, hi: 28.500, label: "Tech phone", kind: "tech", desc: "Technician/Novice (200 W, SSB) + General/Advanced/Extra. CW, phone, image. 28.400 SSB calling." },
    { lo: 28.500, hi: 29.700, label: "Gen phone", kind: "gen", desc: "General/Advanced/Extra. CW, phone, image. FM ~29.0-29.7; 29.600 FM calling; 29.6+ repeaters." },
    // 6 m
    { lo: 50.000, hi: 50.100, label: "CW/weak-signal", kind: "all", desc: "All classes (Tech+). CW only. 50.060-50.080 beacons." },
    { lo: 50.100, hi: 51.000, label: "SSB/weak-signal", kind: "all", desc: "All classes. CW/phone/image/MCW/RTTY/data. 50.125 SSB calling; 50.100-50.125 DX window." },
    { lo: 51.000, hi: 54.000, label: "FM/repeaters", kind: "all", desc: "All classes. All modes incl FM. 52.525 FM simplex calling; 53.000+ repeaters." },
  ];
})(window);

"""Host-side sound-card audio for serial/USB radios operated remotely.

A web browser can only enumerate the CLIENT's audio devices, but a remotely-operated
serial radio's RX/TX audio sits on the HOST's sound card (its USB CODEC). This module
captures the chosen host INPUT device and streams 16 kHz mono PCM to the browser (via the
hub's broadcast_audio), and plays browser-mic PCM (16 kHz) to the chosen host OUTPUT device
— the exact wire contract the IC-9700 LAN audio already uses, so the browser side is shared.

Optional dependency: `sounddevice` (PortAudio). If it isn't installed, host audio reports
unavailable and the UI falls back to a hint; nothing else breaks.
"""
from __future__ import annotations

import threading
from typing import Callable, List, Optional

try:
    import sounddevice as _sd
except Exception:                      # pragma: no cover - absence is a supported state
    _sd = None

RATE = 16000                           # 16 kHz mono int16 — matches server.AUDIO_RATE / the LAN path
_BLOCK = 800                           # 50 ms @ 16 kHz


def available() -> bool:
    return _sd is not None


def list_devices() -> dict:
    """Host input/output devices on the default host API, deduped by name.

    Windows lists every device once per host API (MME / WASAPI / DirectSound); we keep the
    default host API only so the picker shows one clean entry per physical sound card.
    """
    if _sd is None:
        return {"available": False, "inputs": [], "outputs": []}
    inputs: List[dict] = []
    outputs: List[dict] = []
    try:
        default_api = _sd.default.hostapi
        seen_in: set = set()
        seen_out: set = set()
        for i, d in enumerate(_sd.query_devices()):
            if d["hostapi"] != default_api:
                continue
            name = (d["name"] or "").strip()
            if d["max_input_channels"] > 0 and name not in seen_in:
                seen_in.add(name)
                inputs.append({"id": i, "name": name})
            if d["max_output_channels"] > 0 and name not in seen_out:
                seen_out.add(name)
                outputs.append({"id": i, "name": name})
    except Exception:
        pass
    return {"available": True, "inputs": inputs, "outputs": outputs}


class HostAudio:
    """One RX capture stream + one TX playback stream over host sound cards.

    RX: the chosen input device -> `on_pcm(bytes)` (16 kHz mono int16) -> hub.broadcast_audio.
    TX: `write(pcm)` queues browser-mic PCM -> the chosen output device. Thread-safe.
    """

    def __init__(self, on_pcm: Callable[[bytes], None]) -> None:
        self._on_pcm = on_pcm
        self._in = None
        self._out = None
        self._buf = bytearray()                 # pending playback PCM (16 kHz mono int16)
        self._lock = threading.Lock()
        self.rx_device: Optional[int] = None
        self.tx_device: Optional[int] = None

    # -- RX: host input device -> browser ------------------------------------
    def start_rx(self, device) -> None:
        self.stop_rx()
        if _sd is None:
            raise RuntimeError("sounddevice not installed")
        dev = int(device)

        def cb(indata, frames, t, status):
            try:
                self._on_pcm(bytes(indata))     # raw int16 mono PCM
            except Exception:
                pass

        self._in = _sd.RawInputStream(device=dev, samplerate=RATE, channels=1,
                                      dtype="int16", blocksize=_BLOCK, callback=cb)
        self._in.start()
        self.rx_device = dev

    def stop_rx(self) -> None:
        s, self._in = self._in, None
        self.rx_device = None
        if s:
            try:
                s.stop(); s.close()
            except Exception:
                pass

    # -- TX: browser mic PCM -> host output device ---------------------------
    def start_tx(self, device) -> None:
        self.stop_tx()
        if _sd is None:
            raise RuntimeError("sounddevice not installed")
        dev = int(device)

        def cb(outdata, frames, t, status):
            need = frames * 2                   # int16 mono
            with self._lock:
                n = min(need, len(self._buf))
                chunk = bytes(self._buf[:n])
                del self._buf[:n]
            if n < need:
                chunk += b"\x00" * (need - n)   # underrun -> silence (no glitch crash)
            outdata[:] = chunk

        self._out = _sd.RawOutputStream(device=dev, samplerate=RATE, channels=1,
                                        dtype="int16", blocksize=_BLOCK, callback=cb)
        self._out.start()
        self.tx_device = dev

    def write(self, pcm: bytes) -> None:
        """Queue browser-mic PCM (16 kHz mono int16) for playback to the host output."""
        if self._out is None or not pcm:
            return
        with self._lock:
            if len(self._buf) > RATE * 2:       # cap ~1 s backlog so latency can't run away
                del self._buf[: len(self._buf) - RATE]
            self._buf.extend(pcm)

    def stop_tx(self) -> None:
        s, self._out = self._out, None
        self.tx_device = None
        with self._lock:
            self._buf.clear()
        if s:
            try:
                s.stop(); s.close()
            except Exception:
                pass

    def stop(self) -> None:
        self.stop_rx()
        self.stop_tx()

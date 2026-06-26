"""
Radio WebOp server.

ASGI app (Starlette on uvicorn):
  GET  /              -> the radio UI
  GET  /api/ports     -> available COM ports (+ "sim")
  POST /api/connect   -> {transport:"sim"} or {transport:"serial",port,baud}
  POST /api/disconnect
  WS   /ws            -> JSON state in/out + binary scope sweeps out

Scope sweeps are pushed as a single self-contained binary frame:
  <BBBH IIIIII> header then N amplitude bytes (see _pack_scope).
"""
from __future__ import annotations

import asyncio
import json
import struct
import threading
import time
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional, Set

from starlette.applications import Starlette
from starlette.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from . import __version__, civ, profiles
from .lan import LanTransport
from .radio import Radio
from .transport import SerialTransport, SimTransport, YaesuSimTransport, available_ports
from .yaesu import YaesuRadio

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"

LEVEL_TARGETS = {"af": 0x01, "rf": 0x02, "sql": 0x03, "rfpwr": 0x0A,
                 "nr_level": 0x06, "nb_level": 0x12, "pbt1": 0x07, "pbt2": 0x08,
                 "mnotch_pos": 0x0D,
                 "mic": 0x0B, "comp_level": 0x0E, "mon_level": 0x15, "vox_gain": 0x16}

radio = Radio()


# -- update check (latest GitHub release) ------------------------------------
_REPO = "Dreikor17/radio-webop"
_VER_TTL = 3600.0                          # re-check at most hourly
_latest = {"tag": None, "url": None, "ts": 0.0}
_latest_lock = threading.Lock()


def _ver_tuple(s: str) -> tuple:
    """'v0.2.02' -> (0, 2, 2); leading-digit per dotted part, so 0.2.02 == 0.2.2."""
    out = []
    for part in (s or "").lstrip("vV").split("."):
        digits = ""
        for ch in part:
            if ch.isdigit():
                digits += ch
            else:
                break
        out.append(int(digits) if digits else 0)
    return tuple(out) or (0,)


def _fetch_latest_release() -> None:
    tag = url = None
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{_REPO}/releases/latest",
            headers={"Accept": "application/vnd.github+json", "User-Agent": "radio-webop"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        tag, url = data.get("tag_name"), data.get("html_url")
    except Exception:
        pass                               # offline / rate-limited / no release -> skip quietly
    with _latest_lock:
        if tag:
            _latest["tag"], _latest["url"] = tag, url
        _latest["ts"] = time.monotonic()


def _maybe_check_update() -> None:
    """Kick a background release check if the cache is empty or stale."""
    with _latest_lock:
        stale = _latest["ts"] == 0.0 or (time.monotonic() - _latest["ts"]) >= _VER_TTL
        if stale:
            _latest["ts"] = time.monotonic()   # claim the slot so callers don't fan out
    if stale:
        threading.Thread(target=_fetch_latest_release, daemon=True, name="ver-check").start()


class Hub:
    """Bridges radio threads -> websocket clients via the asyncio loop."""

    def __init__(self) -> None:
        self.clients: Set[asyncio.Queue] = set()
        self.loop: Optional[asyncio.AbstractEventLoop] = None

    def add(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=64)   # room for scope + audio
        self.clients.add(q)
        return q

    def remove(self, q: asyncio.Queue) -> None:
        self.clients.discard(q)

    def _push(self, item) -> None:
        if not self.loop:
            return
        def deliver():
            for q in list(self.clients):
                if q.full():
                    try:
                        q.get_nowait()       # drop oldest, keep stream live
                    except asyncio.QueueEmpty:
                        pass
                try:
                    q.put_nowait(item)
                except asyncio.QueueFull:
                    pass
        self.loop.call_soon_threadsafe(deliver)

    def broadcast_state(self, state: dict) -> None:
        self._push(("text", json.dumps({"type": "state", **state})))

    def broadcast_scope(self, sweep: civ.ScopeSweep) -> None:
        self._push(("bytes", _pack_scope(sweep, radio.state)))

    def broadcast_audio(self, pcm: bytes) -> None:
        # 'A' tag + channels + sample-rate header, then 16-bit LE mono PCM
        self._push(("bytes", struct.pack("<BBH", 0x41, 1, AUDIO_RATE) + pcm))


AUDIO_RATE = 16000

hub = Hub()

# One control stack per protocol; the active one is bound to `radio` and is
# swapped on connect based on the chosen profile's protocol.
_icom = radio                 # Icom CI-V radio (created above)
_yaesu = YaesuRadio()         # Yaesu CAT radio (FT-991A) — COM-only


def _bind_radio(r) -> None:
    r.on_state = hub.broadcast_state
    r.on_scope = hub.broadcast_scope
    r.on_audio = hub.broadcast_audio


_bind_radio(radio)


def _pack_scope(sweep: civ.ScopeSweep, state: dict) -> bytes:
    data = sweep.data[: civ.SCOPE_POINTS]
    header = struct.pack(
        "<BBBH IIIIII",
        0x53,                          # 'S' magic
        1 if sweep.mode == 1 else 0,   # 0 center / 1 fixed
        1 if sweep.out_of_range else 0,
        len(data),
        sweep.center_hz or 0,
        sweep.span_hz or state.get("span", 0),
        sweep.lower_hz or 0,
        sweep.upper_hz or 0,
        state.get("freq", 0),          # tuned freq (channel marker)
        state.get("filter_bw", 0),
    )
    return header + data


# -- HTTP routes -------------------------------------------------------------
def _asset_version():
    """Token from frontend file mtimes so the browser refetches updated assets."""
    try:
        return str(int(max(
            (FRONTEND / f).stat().st_mtime
            for f in ("index.html", "app.js", "waterfall.js", "bandplan.js", "cwtool.js", "style.css")
        )))
    except OSError:
        return "0"


async def index(request):
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    v = _asset_version()
    for asset in ("app.js", "waterfall.js", "bandplan.js", "cwtool.js", "style.css"):
        html = html.replace(f"/static/{asset}", f"/static/{asset}?v={v}")
    return HTMLResponse(html)


async def api_radios(request):
    return JSONResponse({"radios": [p.to_json() for p in profiles.PROFILES.values()]})


async def api_version(request):
    _maybe_check_update()
    with _latest_lock:
        latest, url = _latest["tag"], _latest["url"]
    available = bool(latest) and _ver_tuple(latest) > _ver_tuple(__version__)
    return JSONResponse({
        "current": __version__,
        "latest": latest,
        "update_available": available,
        "url": url,
    })


async def api_ports(request):
    return JSONResponse({
        "ports": available_ports(),
        "connected": radio.state["connected"],
        "transport": radio.state["transport"],
        "radio": radio.state["radio"],
    })


async def api_connect(request):
    global radio
    body = await request.json()
    kind = body.get("transport", "sim")
    try:
        profile = profiles.PROFILES.get(body.get("radio"), radio.profile)
        is_yaesu = getattr(profile, "protocol", "civ") == "yaesu"
        # route to the matching protocol stack (Icom CI-V vs Yaesu CAT)
        target = _yaesu if is_yaesu else _icom
        if target is not radio:
            radio.disconnect()
            radio = target
            _bind_radio(radio)
        if kind == "serial":
            baud = int(body.get("baud") or getattr(profile, "default_baud", 115200))
            tp = SerialTransport(body["port"], baud, stopbits=2 if is_yaesu else 1)
        elif kind == "lan":
            if is_yaesu:
                raise ValueError("The FT-991A is COM-only (no network CAT)")
            host = (body.get("host") or "").strip()
            if not host:
                raise ValueError("LAN host/IP is required")
            tp = LanTransport(host, int(body.get("port", 50001)),
                              body.get("user", ""), body.get("password", ""))
        else:
            tp = YaesuSimTransport(profile) if is_yaesu else SimTransport(profile)
        radio.connect(tp, profile)
        return JSONResponse({"ok": True, "transport": radio.state["transport"], "radio": profile.id})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


async def api_disconnect(request):
    radio.disconnect()
    return JSONResponse({"ok": True})


# -- WebSocket ---------------------------------------------------------------
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    q = hub.add()
    await ws.send_text(json.dumps({"type": "state", **radio.state}))

    async def sender():
        try:
            while True:
                kind, payload = await q.get()
                if kind == "text":
                    await ws.send_text(payload)
                else:
                    await ws.send_bytes(payload)
        except (WebSocketDisconnect, RuntimeError):
            pass

    send_task = asyncio.create_task(sender())
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("text") is not None:
                _handle_cmd(json.loads(msg["text"]))
            elif msg.get("bytes") is not None:
                radio.write_audio(msg["bytes"])      # mic PCM (16-bit LE mono)
    except WebSocketDisconnect:
        pass
    finally:
        send_task.cancel()
        hub.remove(q)
        # safety: PTT is a single shared radio state with one keyer, so the
        # disconnecting client may be the one transmitting. Unkey on ANY client
        # drop while keyed — not just the last one — since the client-side unkey
        # can be lost when the socket is already closing.
        if radio.state.get("ptt"):
            radio.set_ptt(False)


def _handle_cmd(cmd: dict) -> None:
    action = cmd.get("action")
    try:
        if action == "set_freq":
            radio.set_freq(int(cmd["hz"]))
        elif action == "tune":
            radio.tune(int(cmd["delta"]))
        elif action == "set_mode":
            code = civ.MODE_CODES.get(cmd["mode"])
            if code is not None:
                radio.set_mode(code, cmd.get("filter"))
        elif action == "set_filter":
            radio.set_filter(int(cmd["filter"]))
        elif action == "vfo":
            radio.select_vfo(int(cmd["code"]))
        elif action == "set_level":
            sub = LEVEL_TARGETS.get(cmd["target"])
            if sub is not None:
                radio.set_level(sub, int(cmd["value"]))
        elif action == "band":
            radio.set_band(str(cmd["band"]))
        elif action == "set_span":
            radio.set_span(int(cmd["span"]))
        elif action == "scope_mode":
            radio.set_scope_mode(bool(cmd["center"]))
        elif action == "select_band":
            radio.select_band(str(cmd["band"]))
        elif action == "set_meter":
            radio.set_meter(str(cmd["meter"]))
        elif action == "preamp":
            radio.set_preamp(bool(cmd["on"]))
        elif action == "att":
            radio.set_att(bool(cmd["on"]))
        elif action == "lock":
            radio.set_lock(bool(cmd["on"]))
        elif action == "rx_func":
            radio.set_rx_func(str(cmd["name"]), bool(cmd["on"]))
        elif action == "agc":
            radio.set_agc(int(cmd["mode"]))
        elif action == "mnotch_w":
            radio.set_mnotch_w(int(cmd["width"]))
        elif action == "tbw":
            radio.set_tbw(int(cmd["w"]))
        elif action == "rit":
            radio.set_rit(bool(cmd["on"]))
        elif action == "rit_freq":
            radio.set_rit_freq(int(cmd["hz"]))
        elif action == "split":
            radio.set_split(bool(cmd["on"]))
        elif action == "duplex":
            radio.set_duplex(int(cmd["mode"]))
        elif action == "ptt":
            radio.set_ptt(bool(cmd["tx"]))
    except (KeyError, ValueError, TypeError):
        pass


@asynccontextmanager
async def _lifespan(app):
    # capture the serving loop so radio threads can schedule WS sends onto it.
    # (lifespan, not on_startup= — Starlette removed the on_startup/on_shutdown kwargs.)
    hub.loop = asyncio.get_running_loop()
    _maybe_check_update()                  # warm the update-check cache at startup
    yield


routes = [
    Route("/", index),
    Route("/api/radios", api_radios),
    Route("/api/version", api_version),
    Route("/api/ports", api_ports),
    Route("/api/connect", api_connect, methods=["POST"]),
    Route("/api/disconnect", api_disconnect, methods=["POST"]),
    WebSocketRoute("/ws", ws_endpoint),
    Mount("/static", StaticFiles(directory=str(FRONTEND)), name="static"),
]

app = Starlette(debug=False, routes=routes, lifespan=_lifespan)

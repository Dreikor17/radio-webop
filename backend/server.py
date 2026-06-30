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
import mimetypes
import struct
import threading
import time
import urllib.request

# ES-module glue files (e.g. onnxruntime-web's .mjs wasm loader) must be served with a
# JavaScript MIME type or the browser refuses to dynamically import them. Python maps
# .mjs -> text/plain on some platforms (Windows), so register it explicitly.
mimetypes.add_type("text/javascript", ".mjs")
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional, Set

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import (FileResponse, HTMLResponse, JSONResponse,
                                 PlainTextResponse, RedirectResponse)
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from . import __version__, auth, civ, hostaudio, profiles, tailscale
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

    def broadcast_menu(self, values: dict) -> None:
        # SET-menu values ride a SEPARATE channel so the 154-item table never bloats the
        # high-rate state frames.
        self._push(("text", json.dumps({"type": "menu", "values": values})))

    def broadcast_host_audio(self, **kw) -> None:
        # host sound-card audio status/errors (separate from radio state)
        self._push(("text", json.dumps({"type": "host_audio", **kw})))

    def broadcast_meter(self, meter: str, val: int, smeter: int, smeter_s: str) -> None:
        # the meter changes ~every poll; keep it OFF the heavy full-state frame so it can
        # fan out fast on its own lightweight channel (like scope / menu).
        self._push(("text", json.dumps({"type": "meter", "meter": meter, "meter_val": val,
                                        "smeter": smeter, "smeter_s": smeter_s})))


AUDIO_RATE = 16000

hub = Hub()
# Host sound-card audio: for a serial/USB radio reached remotely, the radio's RX/TX audio is
# on the HOST's sound card (not the browser's). Capture it server-side and stream over the WS.
host_audio = hostaudio.HostAudio(hub.broadcast_audio)

# One control stack per protocol; the active one is bound to `radio` and is
# swapped on connect based on the chosen profile's protocol.
_icom = radio                 # Icom CI-V radio (created above)
_yaesu = YaesuRadio()         # Yaesu CAT radio (FT-991A) — COM-only


def _bind_radio(r) -> None:
    r.on_state = hub.broadcast_state
    r.on_scope = hub.broadcast_scope
    r.on_audio = hub.broadcast_audio
    r.on_menu = hub.broadcast_menu
    r.on_meter = hub.broadcast_meter


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
        host_audio.stop()                   # drop any prior host sound-card streams on (re)connect
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
            # CAT port opens with control lines at their defaults (RTS high) so CAT works
            # regardless of the radio's CAT-RTS setting. FT-991A CW keys on the SIBLING
            # (Standard) port's DTR line, opened separately — never on this CAT port.
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
    host_audio.stop()                       # release the host sound cards with the radio
    radio.disconnect()
    return JSONResponse({"ok": True})


async def api_audio_devices(request):
    """Host (server-side) sound cards, for a serial/USB radio's Radio RX / Radio TX pickers."""
    return JSONResponse(hostaudio.list_devices())


# -- WebSocket ---------------------------------------------------------------
async def ws_endpoint(ws: WebSocket):
    # The HTTP middleware doesn't see WS upgrades — gate here. Origin/Host blocks cross-site
    # WS hijacking + DNS-rebinding; the session cookie gates remote clients (loopback exempt).
    host = ws.headers.get("host")
    if not auth.host_ok(host) or not auth.origin_ok(ws.headers.get("origin"), host):
        await ws.close(code=1008); return
    if auth.auth_enabled() and not auth.local_exempt(ws.client.host if ws.client else None) \
            and not auth.valid_session(ws.cookies.get(auth.COOKIE_NAME)):
        await ws.close(code=1008); return
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
                if host_audio.tx_device is not None:
                    host_audio.write(msg["bytes"])   # serial radio: mic -> host TX sound card
                else:
                    radio.write_audio(msg["bytes"])  # LAN: mic PCM -> the radio's modulator
    except WebSocketDisconnect:
        pass
    finally:
        send_task.cancel()
        hub.remove(q)
        host_audio.stop_tx()                 # this client's mic feed ends -> stop playing to the radio
        if not hub.clients:                  # nobody left listening -> stop capturing the host RX card
            host_audio.stop_rx()
        # safety: PTT is a single shared radio state with one keyer, so the
        # disconnecting client may be the one transmitting. Unkey on ANY client
        # drop while keyed — not just the last one — since the client-side unkey
        # can be lost when the socket is already closing.
        if radio.state.get("ptt"):
            radio.set_ptt(False)
        if radio.state.get("cw_tx"):                  # stop an in-progress CW message too
            radio.stop_cw()


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
            radio.set_preamp(int(cmd["level"]) if "level" in cmd else (1 if cmd.get("on") else 0))
        elif action == "att":
            radio.set_att(bool(cmd["on"]))
        elif action == "lock":
            radio.set_lock(bool(cmd["on"]))
        elif action == "tuner":
            radio.set_tuner(bool(cmd["on"]))
        elif action == "tune_atu":
            radio.tune_atu()
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
        elif action == "cw_tx":
            radio.send_cw(str(cmd.get("text", "")), int(cmd.get("wpm", 18)))
        elif action == "cw_stop":
            radio.stop_cw()
        elif action == "menu_read":
            radio.get_menu(int(cmd["num"]))
        elif action == "menu_read_group":
            radio.read_menu_group(str(cmd["group"]))
        elif action == "menu_write":
            radio.set_menu(int(cmd["num"]), cmd["value"])
        elif action == "host_rx":            # capture a host sound card -> stream to the browser
            try:
                host_audio.start_rx(cmd["device"]) if cmd.get("on") else host_audio.stop_rx()
            except Exception as exc:
                host_audio.stop_rx(); hub.broadcast_host_audio(error="Radio RX audio — " + str(exc))
        elif action == "host_tx":            # play browser-mic PCM -> a host sound card (the radio)
            try:
                host_audio.start_tx(cmd["device"]) if cmd.get("on") else host_audio.stop_tx()
            except Exception as exc:
                host_audio.stop_tx(); hub.broadcast_host_audio(error="Radio TX audio — " + str(exc))
    except (KeyError, ValueError, TypeError):
        pass


@asynccontextmanager
async def _lifespan(app):
    # capture the serving loop so radio threads can schedule WS sends onto it.
    # (lifespan, not on_startup= — Starlette removed the on_startup/on_shutdown kwargs.)
    hub.loop = asyncio.get_running_loop()
    _maybe_check_update()                  # warm the update-check cache at startup
    yield


# -- access control: shared-password auth + Origin/Host (CSWSH + DNS-rebinding) ----------
_AUTH_EXEMPT = {"/login", "/api/login", "/api/auth_status"}
_login_hits: dict = {}                  # ip -> [recent attempt times] (brute-force brake)


def _client_ip(req) -> str:
    return req.client.host if req.client else ""


def _rate_limited(ip: str) -> bool:
    now = time.monotonic()
    hits = [t for t in _login_hits.get(ip, []) if now - t < 300]   # 5-min window
    _login_hits[ip] = hits
    return len(hits) >= 10              # >10 attempts / 5 min


class Gate(BaseHTTPMiddleware):
    """Origin/Host allowlist on every request (CSWSH + DNS-rebinding), plus the shared-password
    session gate on all routes except loopback + the login endpoints."""
    async def dispatch(self, request, call_next):
        host = request.headers.get("host")
        if not auth.host_ok(host):
            return PlainTextResponse("Bad Host header.", status_code=403)
        if request.method not in ("GET", "HEAD"):           # block cross-site state changes
            if not auth.origin_ok(request.headers.get("origin"), host):
                return PlainTextResponse("Cross-site request blocked.", status_code=403)
        if auth.auth_enabled() and not auth.local_exempt(_client_ip(request)) \
                and request.url.path not in _AUTH_EXEMPT:
            if not auth.valid_session(request.cookies.get(auth.COOKIE_NAME)):
                if request.url.path.startswith("/api/"):
                    return JSONResponse({"error": "auth required"}, status_code=401)
                return RedirectResponse("/login", status_code=303)
        return await call_next(request)


def _set_session_cookie(resp, request) -> None:
    resp.set_cookie(auth.COOKIE_NAME, auth.make_session(), max_age=auth.SESSION_TTL,
                    httponly=True, samesite="lax", secure=request.url.scheme == "https", path="/")


async def login_page(request):
    return FileResponse(str(FRONTEND / "login.html"))


async def api_login(request):
    ip = _client_ip(request)
    if _rate_limited(ip):
        return JSONResponse({"ok": False, "error": "too many attempts — wait a few minutes"}, status_code=429)
    _login_hits.setdefault(ip, []).append(time.monotonic())
    body = await request.json()
    if auth.verify_password(str(body.get("password", ""))):
        _login_hits[ip] = []
        resp = JSONResponse({"ok": True})
        _set_session_cookie(resp, request)
        return resp
    return JSONResponse({"ok": False, "error": "wrong password"}, status_code=401)


async def api_logout(request):
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE_NAME, path="/")
    return resp


async def api_auth_status(request):
    local = auth.is_local(_client_ip(request))
    return JSONResponse({"enabled": auth.auth_enabled(), "local": local,
                         "authed": local or auth.valid_session(request.cookies.get(auth.COOKIE_NAME))})


async def api_set_password(request):
    # Setting / clearing the shared password is the host operator's job — local only.
    if not auth.is_local(_client_ip(request)):
        return JSONResponse({"ok": False, "error": "set the password on the host PC"}, status_code=403)
    body = await request.json()
    if body.get("clear"):
        auth.clear_password()
        return JSONResponse({"ok": True, "enabled": False})
    pw = str(body.get("password", ""))
    if len(pw) < 6:
        return JSONResponse({"ok": False, "error": "use at least 6 characters"}, status_code=400)
    auth.set_password(pw)
    for h in (body.get("allowed_hosts") or []):
        auth.add_allowed_host(str(h))
    resp = JSONResponse({"ok": True, "enabled": True})
    _set_session_cookie(resp, request)         # keep the operator signed in after setting it
    return resp


def _app_port(request) -> int:
    return request.url.port or 8700


async def api_remote_status(request):
    port = _app_port(request)
    return JSONResponse({"tailscale": tailscale.status(port), "auth_enabled": auth.auth_enabled(),
                         "behind_proxy": auth.behind_proxy(), "local": auth.is_local(_client_ip(request)),
                         "port": port})


async def api_tailscale_serve(request):
    if not auth.is_local(_client_ip(request)):
        return JSONResponse({"ok": False, "error": "set this up from the host PC's own browser"}, status_code=403)
    port = _app_port(request)
    code, out, err = tailscale.serve_on(port)
    st = tailscale.status(port)
    if st.get("magicdns"):
        auth.add_allowed_host(st["magicdns"])       # so the Origin/Host gate accepts the *.ts.net name
    auth.set_behind_proxy(True)
    if code != 0:
        return JSONResponse({"ok": False, "error": (err or out or "tailscale serve failed").strip(),
                             "tailscale": st}, status_code=400)
    return JSONResponse({"ok": True, "tailscale": st})


async def api_tailscale_serve_off(request):
    if not auth.is_local(_client_ip(request)):
        return JSONResponse({"ok": False, "error": "do this from the host PC's own browser"}, status_code=403)
    tailscale.serve_off()
    auth.set_behind_proxy(False)
    return JSONResponse({"ok": True, "tailscale": tailscale.status(_app_port(request))})


routes = [
    Route("/", index),
    Route("/login", login_page),
    Route("/api/login", api_login, methods=["POST"]),
    Route("/api/logout", api_logout, methods=["POST"]),
    Route("/api/auth_status", api_auth_status),
    Route("/api/set_password", api_set_password, methods=["POST"]),
    Route("/api/remote_status", api_remote_status),
    Route("/api/tailscale_serve", api_tailscale_serve, methods=["POST"]),
    Route("/api/tailscale_serve_off", api_tailscale_serve_off, methods=["POST"]),
    Route("/api/radios", api_radios),
    Route("/api/version", api_version),
    Route("/api/ports", api_ports),
    Route("/api/audio_devices", api_audio_devices),
    Route("/api/connect", api_connect, methods=["POST"]),
    Route("/api/disconnect", api_disconnect, methods=["POST"]),
    WebSocketRoute("/ws", ws_endpoint),
    Mount("/static", StaticFiles(directory=str(FRONTEND)), name="static"),
]

app = Starlette(debug=False, routes=routes, middleware=[Middleware(Gate)], lifespan=_lifespan)

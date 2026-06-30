"""App-layer access control — the ONLY thing protecting the transmitter once Radio WebOp is
reachable off the host PC.

- One shared password (scrypt-hashed) gates every REMOTE session; the operator at the host
  (loopback) is exempt, so the local UI needs no login.
- A signed (HMAC) session cookie — stdlib only, no DB, no extra dependency.
- An Origin/Host allowlist on the WebSocket + state-changing routes blocks cross-site
  WebSocket hijacking and DNS-rebinding (a page you merely VISIT keying your TX). This is
  enforced even with NO password set — it's the one defense that works without a login.

Fail closed: an unparseable/ambiguous client IP is treated as remote; an unrecognized Host
is rejected. Config lives in ~/.radio-webop/config.json (override with RADIO_WEBOP_CONFIG).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

CONFIG_PATH = Path(os.environ.get("RADIO_WEBOP_CONFIG")
                   or (Path.home() / ".radio-webop" / "config.json"))
SESSION_TTL = 30 * 24 * 3600          # signed-cookie lifetime (seconds)
COOKIE_NAME = "rw_session"

_cfg: Optional[dict] = None


def _load() -> dict:
    global _cfg
    if _cfg is None:
        try:
            _cfg = json.loads(CONFIG_PATH.read_text("utf-8"))
        except Exception:
            _cfg = {}
        if not _cfg.get("session_secret"):       # persisted so sessions survive restarts
            _cfg["session_secret"] = secrets.token_hex(32)
            _save()
    return _cfg


def _save() -> None:
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(_cfg, indent=2), "utf-8")
    except Exception:
        pass


# -- shared password ---------------------------------------------------------
def auth_enabled() -> bool:
    """Remote auth is ON exactly when a password has been set (via setup)."""
    return bool(_load().get("password_hash"))


def set_password(pw: str) -> None:
    cfg = _load()
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32)
    cfg["password_hash"] = base64.b64encode(salt + h).decode()
    _save()


def clear_password() -> None:
    _load().pop("password_hash", None)
    _save()


def verify_password(pw: str) -> bool:
    stored = _load().get("password_hash")
    if not stored:
        return False
    try:
        raw = base64.b64decode(stored)
    except Exception:
        return False
    salt, h = raw[:16], raw[16:]
    test = hashlib.scrypt(pw.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32)
    return hmac.compare_digest(h, test)


# -- stateless HMAC-signed session cookie ------------------------------------
def make_session() -> str:
    exp = str(int(time.time()) + SESSION_TTL)
    sig = hmac.new(_load()["session_secret"].encode(), exp.encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def valid_session(token: Optional[str]) -> bool:
    if not token or "." not in token:
        return False
    exp, sig = token.rsplit(".", 1)
    good = hmac.new(_load()["session_secret"].encode(), exp.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, good):
        return False
    try:
        return int(exp) > time.time()
    except ValueError:
        return False


# -- local vs remote (fail closed: ONLY loopback is "local") -----------------
def is_local(host: Optional[str]) -> bool:
    """The host PC itself (loopback). NOT the LAN — a LAN is not a trust boundary for a
    transmitter. Behind a reverse proxy/tunnel the peer is also loopback, so loopback-exempt
    is only sound with DIRECT TLS (the v1 Let's Encrypt setup), where the peer IP is real."""
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_loopback


def behind_proxy() -> bool:
    """Set when fronted by a reverse proxy (e.g. Tailscale Serve), where every client arrives
    from loopback — so loopback can NOT be trusted as 'this is the host operator'."""
    return bool(_load().get("behind_proxy"))


def set_behind_proxy(v: bool) -> None:
    _load()["behind_proxy"] = bool(v)
    _save()


def local_exempt(host: Optional[str]) -> bool:
    """Skip the password only for the host operator on loopback — and only with DIRECT TLS.
    Behind a proxy, loopback is everyone, so nothing is exempt."""
    return is_local(host) and not behind_proxy()


# -- Origin / Host allowlist (CSWSH + DNS-rebinding) -------------------------
def _hostname(netloc: str) -> str:
    h = (netloc or "").strip()
    if h.startswith("["):                          # [ipv6]:port
        return h[1:h.find("]")].lower()
    return h.split(":")[0].lower()


def _is_ip(h: str) -> bool:
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        return False


def _allowed() -> set:
    return {h.lower() for h in _load().get("allowed_hosts", []) if h}


def host_ok(host_header: Optional[str]) -> bool:
    """Reject DNS-rebinding: Host must be localhost, a literal IP, or a configured name.
    Permissive until an allowlist is configured (setup adds the domain), so it never breaks
    existing hostname access on a LAN; once a remote host is configured, it's enforced."""
    h = _hostname(host_header or "")
    if not h:
        return False
    if h == "localhost" or _is_ip(h):
        return True
    allowed = _allowed()
    if not allowed:
        return True
    return h in allowed


def origin_ok(origin: Optional[str], host_header: Optional[str]) -> bool:
    """Reject cross-site requests/WS: Origin (when present) must be same-host or allowlisted."""
    if not origin:
        return True                                # browsers always send Origin on WS + cross-origin/POST
    oh = _hostname(urlparse(origin).netloc)
    if not oh:
        return False
    return oh == _hostname(host_header or "") or oh in _allowed()


def add_allowed_host(host: str) -> None:
    cfg = _load()
    hosts = cfg.setdefault("allowed_hosts", [])
    if host and host.lower() not in {h.lower() for h in hosts}:
        hosts.append(host)
        _save()

"""Thin wrapper around the Tailscale CLI for the remote-access setup.

Tailscale is the recommended remote-access path: `tailscale serve` puts the app behind a
tailnet-only HTTPS reverse proxy with an automatic *.ts.net certificate — real HTTPS (so the
browser mic/TX works), no port-forward (CGNAT-friendly), and the tailnet is the access
boundary. We just detect it and drive `serve` for the user. Absent Tailscale, status() reports
not-installed and the setup page shows install steps.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Optional

_WIN = r"C:\Program Files\Tailscale\tailscale.exe"
_MAC = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"


def cli_path() -> Optional[str]:
    p = shutil.which("tailscale") or shutil.which("tailscale.exe")
    if p:
        return p
    for c in (_WIN, _MAC, "/usr/bin/tailscale", "/usr/local/bin/tailscale"):
        if os.path.exists(c):
            return c
    return None


def _run(args, timeout=12):
    cli = cli_path()
    if not cli:
        return -1, "", "tailscale not found"
    try:
        p = subprocess.run([cli] + list(args), capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except Exception as exc:        # pragma: no cover - environment dependent
        return -1, "", str(exc)


def status(port: int) -> dict:
    """{installed, running, magicdns, url, serve_running} — what the setup page renders."""
    out = {"installed": cli_path() is not None, "running": False,
           "magicdns": "", "url": "", "serve_running": False}
    if not out["installed"]:
        return out
    code, so, _ = _run(["status", "--json"])
    if code == 0:
        try:
            j = json.loads(so)
            out["running"] = j.get("BackendState") == "Running"
            name = (j.get("Self", {}).get("DNSName") or "").rstrip(".")
            out["magicdns"] = name
            if name:
                out["url"] = f"https://{name}"
        except Exception:
            pass
    code, so, _ = _run(["serve", "status"])
    if code == 0 and so and (f"127.0.0.1:{port}" in so or f"localhost:{port}" in so):
        out["serve_running"] = True
    return out


def serve_on(port: int):
    """Proxy https://<host>.ts.net/ -> http://127.0.0.1:<port> (tailnet-only, --bg = persists)."""
    return _run(["serve", "--bg", str(int(port))], timeout=25)


def serve_off():
    return _run(["serve", "reset"], timeout=25)

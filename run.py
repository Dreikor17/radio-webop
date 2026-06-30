#!/usr/bin/env python3
"""Launch the Radio WebOp server. Usage: python run.py [--host H] [--port P]"""
import argparse
import os
import sys
import webbrowser

# make the app importable and paths stable regardless of where we're launched
_HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(_HERE)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import uvicorn


def main() -> None:
    ap = argparse.ArgumentParser(description="Radio WebOp server")
    ap.add_argument("--host", default="0.0.0.0",
                    help="bind address (default 0.0.0.0 = all interfaces: LAN, VPN, etc.)")
    ap.add_argument("--port", type=int, default=8700)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--reload", action="store_true")
    ap.add_argument("--ssl-certfile", default=os.environ.get("RADIO_WEBOP_SSL_CERT"),
                    help="TLS certificate (PEM) — serve HTTPS directly on --port "
                         "(also reads RADIO_WEBOP_SSL_CERT). Pair with --ssl-keyfile.")
    ap.add_argument("--ssl-keyfile", default=os.environ.get("RADIO_WEBOP_SSL_KEY"),
                    help="TLS private key (PEM) for --ssl-certfile (also reads RADIO_WEBOP_SSL_KEY).")
    args = ap.parse_args()

    # Serve HTTPS directly only if BOTH cert and key are given; otherwise plain HTTP.
    ssl_kw = {}
    if args.ssl_certfile and args.ssl_keyfile:
        ssl_kw = {"ssl_certfile": args.ssl_certfile, "ssl_keyfile": args.ssl_keyfile}
    elif args.ssl_certfile or args.ssl_keyfile:
        print("  NOTE: --ssl-certfile and --ssl-keyfile must be given together; serving plain HTTP.")

    scheme = "https" if ssl_kw else "http"
    local = f"{scheme}://localhost:{args.port}"
    if not args.no_browser and not args.reload:
        try:
            webbrowser.open(local)
        except Exception:
            pass
    print(f"Radio WebOp -> {local}")
    if args.host in ("0.0.0.0", "::"):
        print(f"  Also reachable on all interfaces (LAN / VPN / port-forward) at port {args.port}"
              f" over {scheme}.")
        print("  WARNING: no login — anyone who can reach this port can control the radio (incl. TX).")
        print("  Secure remote tip: bind --host 127.0.0.1 and front it with 'tailscale serve --bg "
              f"{args.port}' for tailnet-only HTTPS (mic/TX needs HTTPS). See docs/REMOTE-ACCESS.md.")
    uvicorn.run("backend.server:app", host=args.host, port=args.port,
                reload=args.reload, log_level="info", **ssl_kw)


if __name__ == "__main__":
    main()

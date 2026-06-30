# Remote access (secure + easy)

> **Easiest:** click **🌐 Remote** in the connection bar. It detects Tailscale, turns on
> `tailscale serve` for you, shows your `https://<host>.ts.net` address, and explains why HTTPS
> is needed for the mic. The steps below are the manual equivalent + the security details.


Radio WebOp has **no login**. Anyone who can reach the port can control the radio,
including TX. So the security boundary must be the *network*, not the app. The
recommended path is **Tailscale Serve**: it puts the app behind a tailnet-only
HTTPS reverse proxy with an automatic `*.ts.net` certificate, which also satisfies
the browser's secure-context requirement for the **microphone / TX audio**
(`getUserMedia` only works over HTTPS or `localhost`).

## TL;DR

1. Install Tailscale on the host PC (the one with the radio) and on each client
   device. Sign both into the **same tailnet**.
2. In the Tailscale admin console, enable **MagicDNS** and **HTTPS Certificates**
   (DNS page → "Enable HTTPS"). One-time, per tailnet.
3. Run the app bound to localhost only (no firewall holes needed):

   ```
   python run.py --host 127.0.0.1 --no-browser
   ```

4. Put it behind Tailscale's HTTPS proxy (run once; persists across reboots):

   ```
   tailscale serve --bg 8700
   ```

5. From any device on the tailnet, open the URL `tailscale serve status` prints,
   e.g. `https://radio-pc.tailNNNN.ts.net/`. You get HTTPS (so the mic/TX works),
   tailnet-only access, and no Windows Firewall prompts.

To stop proxying: `tailscale serve reset` (clears all) or the targeted `tailscale serve --https=443 8700 off`.

## Why this over the alternatives

- **No app changes, no self-signed cert.** Tailscale terminates TLS and reverse-
  proxies to `http://127.0.0.1:8700`. The frontend already picks `wss://` when the
  page is `https://` (see `frontend/app.js` `connectWS`), so the WebSocket, scope,
  and audio streams ride the proxy with no edits.
- **Secure context for free.** The mic / host-audio TX path is hard-gated on
  `window.isSecureContext` (`app.js` `startMic`). Over plain HTTP it refuses with a
  popup. A `*.ts.net` HTTPS origin clears that gate.
- **Tailnet boundary = access control.** Because there's no login, only devices in
  your tailnet can reach it. Tighten further with Tailscale ACLs if you share the
  tailnet with others.

## Do NOT use Tailscale Funnel here

`tailscale funnel` exposes the service to the **public internet**. With **no
authentication**, that means anyone on the internet who finds the hostname can key
your transmitter. Never funnel this app. Serve (tailnet-only) is the correct tool.

## Connection model

```
browser (https, wss)  ->  tailscale serve (TLS, *.ts.net cert)  ->  http://127.0.0.1:8700  (run.py)
```

⚠️ **Bind the app to `127.0.0.1` when fronting it with Serve.** If you leave the
default `0.0.0.0`, the app *also* listens in the clear on the LAN and the raw Tailscale
IP (`http://<ip>:8700`) — a no-login, plaintext, TX-capable port that bypasses the
proxy's TLS entirely. Serve only needs loopback.

## Troubleshooting "can't connect over MagicDNS"

The default `python run.py` binds `0.0.0.0` (all interfaces), so a refused/blocked
MagicDNS connection — when you're *not* using Serve — is almost always one of these:

1. **Browser forced `https://` against a plain-HTTP server.** If you point a browser
   at `https://radio-pc...:8700` while the app is running plain HTTP, uvicorn logs
   "Invalid HTTP request received" and the page fails. Either use Tailscale Serve
   (HTTPS at the proxy, HTTP to the app) or run the app with `--ssl-certfile/
   --ssl-keyfile`. Match the scheme to how the server actually runs.
2. **Windows Firewall blocking inbound on the port / python.exe.** If you skip Serve
   and hit `http://radio-pc...:8700` directly, the first run usually triggers a
   Windows Defender Firewall prompt for Python — if it was dismissed/denied, inbound
   8700 is blocked on the Tailscale interface. Serve avoids this entirely (the app
   listens on loopback; the proxy is the Tailscale daemon). If you insist on direct
   access, allow it:

   ```
   netsh advfirewall firewall add rule name="Radio WebOp 8700" ^
     dir=in action=allow protocol=TCP localport=8700
   ```

3. **HTTPS-cert expectation on the MagicDNS name.** `https://host` (no port) works
   only after you enable HTTPS Certificates + MagicDNS and use Serve (or have a real
   cert). `https://host:8700` does not magically get a cert; it just hits plain HTTP.
4. **Hitting the wrong thing on loopback.** A `127.0.0.1`-only dev/preview process is
   not reachable over MagicDNS by design. With Serve, that's exactly what you want —
   the app stays on loopback and the proxy bridges the tailnet.

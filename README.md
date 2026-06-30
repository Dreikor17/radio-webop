# Radio WebOp

A browser-based control panel for **Icom CI-V transceivers**, with a live
spectrum scope + waterfall. One app, multiple radios — pick your model from a
dropdown and the bands, modes, steps and CI-V details all follow. Currently
ships profiles for the **IC-9700** and **IC-7300MK2**; adding a radio is just a
new profile.

It includes a built-in **simulator**, so the whole interface and waterfall run
with no radio attached. Connect over **USB** or **LAN** when you're ready.

> This repo was previously two apps (`icom-webop` for the IC-9700 and
> `ic7300mk2-webop`). They're now merged here as **Radio WebOp**.

## Features

- **Multi-radio** — choose the model; band/mode/step buttons and the CI-V
  address come from that radio's profile (`backend/profiles.py`).
- **Live bandscope + waterfall** decoded from CI-V `27 00` (475 points, 0–160),
  with the tuned-frequency marker and the receive passband shaded over it.
- **Tuning** — main dial, **tap-to-select and drag/slide on the waterfall**,
  mouse-wheel, selectable step, and direct frequency entry.
- **Control** — band, mode, filter (FIL1–3), VFO A/B/A=B/swap.
- **Audio over LAN** — RX plays in the browser; the mic transmits (16 kHz / 16-bit
  mono). MOD Input is auto-routed to LAN on connect and restored on disconnect.
- **PTT** tap-to-toggle with a **failsafe time-out** — a client countdown and an
  independent server-side auto-unkey, plus release on screen-lock / disconnect.
- **Levels** — AF, RF, SQL, RF power (shown as %, defaults to 0% on connect);
  real-time S-meter.
- **Mobile-friendly** responsive layout; remembers the radio + connection.

## Transports

- **USB CI-V** — a COM port.
- **LAN (network)** — Icom's RS-BA1 UDP protocol (control 50001 / CI-V 50002 /
  audio 50003), for radios with an Ethernet port (IC-9700, IC-7300MK2, …).
- **Simulator** — auto-connects on load.

## Run it (Windows)

```
pip install -r requirements.txt
run.bat
```

Then open <http://localhost:8700>. Pick your **radio model**, choose a transport
(Simulator / a COM port / LAN), and Connect.

- **USB:** set the radio's CI-V on USB, pick the COM port + baud, Connect.
- **LAN:** on the radio set Network Function ON + a network user/password, leave
  it on (or in networked standby), then choose LAN, enter the IP / user /
  password, Connect.
- **Remote/mobile:** binds `0.0.0.0` by default, so it's reachable over your LAN, a
  VPN, or a port-forward. RX and control work over plain HTTP; the mic (TX) and USB
  audio need a secure context (HTTPS). For HTTPS, either put it behind a reverse proxy
  / TLS tunnel, or serve TLS **directly on its own port**:

  ```
  python run.py --ssl-certfile cert.pem --ssl-keyfile key.pem
  ```

  (or set `RADIO_WEBOP_SSL_CERT` / `RADIO_WEBOP_SSL_KEY`). Then open the `https://`
  address. The WebSocket follows the page scheme automatically (`ws://` / `wss://`).
  Note: HTTPS sent to a plain-HTTP instance is rejected as an invalid request — match
  the scheme to how the server is running.

  **Recommended for remote use: Tailscale Serve.** Because there is **no login**,
  the safest easy path is to keep the app on loopback (`--host 127.0.0.1`) and front
  it with a tailnet-only HTTPS reverse proxy that auto-provisions a `*.ts.net`
  certificate (which also satisfies the mic/TX secure-context requirement):

  ```
  python run.py --host 127.0.0.1 --no-browser
  tailscale serve --bg 8700
  ```

  Then open the printed `https://<host>.ts.net/` URL from any tailnet device — no
  firewall holes, no self-signed cert. Do **not** use `tailscale funnel` (public
  internet) with this no-auth app. See [docs/REMOTE-ACCESS.md](docs/REMOTE-ACCESS.md).

## Architecture

```
backend/
  profiles.py   RadioProfile registry — per-model address, bands, modes,
                filters, MOD-Input numbers, power behaviour (add a radio here)
  civ.py        CI-V protocol: framing, BCD freq/levels, modes, 27 00 scope
  transport.py  SerialTransport (USB) + SimTransport (profile-driven simulator)
  lan.py        LanTransport — Icom RS-BA1 UDP (control/serial/audio + login)
  radio.py      profile-driven controller: live state, scope, audio, actions
  server.py     Starlette/uvicorn — /api/radios, connect/ports, WebSocket
frontend/       Icom-themed panel; bands/modes/steps render from the profile
```

See [ROADMAP.md](ROADMAP.md) for what's next and [CHANGELOG.md](CHANGELOG.md).

## Notes

Independent project, not affiliated with Icom. The LAN protocol is a clean-room
implementation informed by the open-source
[wfview](https://gitlab.com/eliggett/wfview) and
[kappanhang](https://github.com/nonoo/kappanhang). There is **no authentication**
yet — restrict access by interface and/or a firewall / VPN. Icom's CI-V reference
PDFs are not redistributed (see `docs/README.md`).

## License

**AGPL-3.0-only** — see [LICENSE](LICENSE) and [NOTICE](NOTICE). The project is AGPL
because its **neural CW decoder** bundles the **DeepCW** model from
[e04/deepcw-engine](https://github.com/e04/deepcw-engine), which is AGPL-3.0. If you
host a modified copy, AGPL section 13 requires offering users the corresponding source.
The in-browser ONNX runtime ([onnxruntime-web](https://github.com/microsoft/onnxruntime))
is MIT.

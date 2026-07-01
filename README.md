# Radio WebOp

A browser-based control panel for **Icom CI-V** and **Yaesu CAT** transceivers,
with a live spectrum scope + waterfall. One app, multiple radios — pick your model
from a dropdown and the bands, modes, steps and protocol details all follow.
Currently ships profiles for the **IC-9700**, **IC-7300MK2**, **Yaesu FT-991A**
and **FT-891**; adding a radio is just a new profile.

It includes a built-in **simulator**, so the whole interface and waterfall run
with no radio attached. Connect over **USB** (CI-V or CAT) or **LAN** (Icom
RS-BA1) when you're ready.

> This repo was previously two apps (`icom-webop` for the IC-9700 and
> `ic7300mk2-webop`). They're now merged here as **Radio WebOp**.

## Features

- **Multi-radio** — choose the model; bands, modes, steps and the control details
  come from that radio's profile (`backend/profiles.py`). The UI adapts, showing
  only the controls a rig actually supports.
- **Live bandscope + waterfall** — decoded from the Icom CI-V `27 00` scope (475
  points), with the tuned-frequency marker and the receive passband shaded over it.
  Radios with no scope over the control link (the Yaesu CATs) get an **audio (AF)
  spectrum** scope built from their RX audio instead.
- **Tuning** — main dial, **tap-to-select and drag/slide on the waterfall**,
  mouse-wheel, selectable step, and direct frequency entry.
- **Control** — band, mode, filter, VFO A/B / A=B / swap, RX DSP (NB / NR / notch)
  and TX (mic / COMP / VOX / monitor / RIT / split), where the radio supports them.
- **Full SET-menu tab** — the radio's own menu, read and written live over the
  control link (Icom `1A 05`, Yaesu `EX`); connection/transmit-sensitive items are
  confirm-gated before writing.
- **Audio** — RX in the browser and mic TX (16 kHz mono), either over **LAN**
  (RS-BA1, for networked Icoms — MOD Input auto-routed on connect and restored on
  disconnect) or via the **host PC's sound card** for USB/serial radios (e.g. an
  FT-891 on a Digirig).
- **PTT** tap-to-toggle with a **failsafe time-out** — a client countdown and an
  independent server-side auto-unkey, plus release on screen-lock / disconnect. A
  hardware TX time-out is set on connect as a backstop.
- **Levels** — AF, RF, SQL, RF power and mic gain; a real-time S-meter and TX
  meters. Power and levels are **read from the rig on connect**, so the panel
  mirrors the radio rather than overriding it.
- **Overlay tools** — an ARRL/FCC **band-plan overlay** and a neural **CW
  decoder/encoder** over the waterfall.
- **Mobile-friendly** responsive layout; remembers the radio + connection per
  radio (transport, ports, and audio devices).

## Transports

- **USB** — a COM port, speaking Icom **CI-V** or Yaesu **CAT**.
- **LAN (network)** — Icom's RS-BA1 UDP protocol (control 50001 / CI-V 50002 /
  audio 50003), for radios with an Ethernet port (IC-9700, IC-7300MK2). The Yaesu
  radios are COM-only.
- **Simulator** — auto-connects on load (until you pick a real radio + transport).

## Install & run (Windows)

**One-click:**

1. Double-click **`install.bat`** — it checks for **Python** (installs 3.12 via `winget`
   if you don't have it), creates a local environment, installs everything, and adds a
   **"Radio WebOp" desktop shortcut**. Re-run it anytime to **update**.
2. Double-click the **desktop shortcut** (or **`run.bat`**) to start it — your browser
   opens at <http://localhost:8700>.

No Python or command line needed. `install.bat` is safe to run again; on a git checkout it
also pulls the latest code before updating dependencies.

<details><summary>Prefer the command line?</summary>

```
pip install -r requirements.txt
python run.py
```
</details>

Pick your **radio model**, choose a transport (Simulator / a COM port / LAN), and Connect.

- **USB:** pick the COM port + baud and Connect (use the **?** next to the model
  for that radio's exact radio-side settings — CI-V address / CAT rate, etc.).
- **LAN:** on the radio set Network Function ON + a network user/password, leave
  it on (or in networked standby), then choose LAN, enter the IP / user /
  password, Connect.
- **Remote/mobile:** binds `0.0.0.0` by default, so it's reachable over your LAN, a
  VPN, or a port-forward. RX and control work over plain HTTP; only the **browser
  mic** (for TX / "Mic In") needs a secure context (HTTPS). For HTTPS, either put it
  behind a reverse proxy / TLS tunnel, or serve TLS **directly on its own port**:

  ```
  python run.py --ssl-certfile cert.pem --ssl-keyfile key.pem
  ```

  (or set `RADIO_WEBOP_SSL_CERT` / `RADIO_WEBOP_SSL_KEY`). Then open the `https://`
  address. The WebSocket follows the page scheme automatically (`ws://` / `wss://`).
  Note: HTTPS sent to a plain-HTTP instance is rejected as an invalid request — match
  the scheme to how the server is running.

  **Recommended for remote use: Tailscale Serve.** The app has a built-in **🌐 Remote**
  setup that detects Tailscale and turns this on for you (and lets you enable an
  optional shared password). The safest easy path is to keep the app on loopback
  (`--host 127.0.0.1`) and front it with a tailnet-only HTTPS reverse proxy that
  auto-provisions a `*.ts.net` certificate (which also satisfies the mic/TX
  secure-context requirement):

  ```
  python run.py --host 127.0.0.1 --no-browser
  tailscale serve --bg 8700
  ```

  Then open the printed `https://<host>.ts.net/` URL from any tailnet device — no
  firewall holes, no self-signed cert. Do **not** use `tailscale funnel` (which
  exposes it to the public internet). See [docs/REMOTE-ACCESS.md](docs/REMOTE-ACCESS.md).

## Architecture

```
backend/
  profiles.py     RadioProfile registry — per-model bands, modes, filters,
                  capabilities + SET-menu table (add a radio here)
  civ.py          Icom CI-V protocol: framing, BCD freq/levels, modes, 27 00 scope
  radio.py        Icom controller: live state, scope, audio, actions
  yaesu.py        Yaesu CAT controller (same surface; COM-only, AF scope)
  transport.py    SerialTransport (USB) + profile-driven simulators
  lan.py          LanTransport — Icom RS-BA1 UDP (control/serial/audio + login)
  hostaudio.py    host sound-card capture/playback for USB/serial radios
  menu_engine.py  SET-menu codec (Icom 1A 05 / Yaesu EX); menus/<id>_menu.py tables
  auth.py         optional shared-password login for remote sessions
  tailscale.py    Tailscale Serve helper for the in-app Remote setup
  server.py       Starlette/uvicorn — /api/radios, connect/ports, WebSocket
frontend/         adaptive multi-radio panel; controls render from the profile
```

See [ROADMAP.md](ROADMAP.md) for what's next and [CHANGELOG.md](CHANGELOG.md).

## Notes

Independent project, not affiliated with Icom. The LAN protocol is a clean-room
implementation informed by the open-source
[wfview](https://gitlab.com/eliggett/wfview) and
[kappanhang](https://github.com/nonoo/kappanhang). Remote access has an **optional
shared-password login** (off by default; the local host is exempt) plus an always-on
cross-site / DNS-rebinding guard — but with no password set, anyone who can reach the
port can control the radio (including TX), so restrict access by interface / firewall
/ VPN, or use the in-app **🌐 Remote** (Tailscale) setup. See
[docs/REMOTE-ACCESS.md](docs/REMOTE-ACCESS.md). Manufacturer CI-V / CAT reference PDFs
are not redistributed (see `docs/README.md`).

## License

**AGPL-3.0-only** — see [LICENSE](LICENSE) and [NOTICE](NOTICE). The project is AGPL
because its **neural CW decoder** bundles the **DeepCW** model from
[e04/deepcw-engine](https://github.com/e04/deepcw-engine), which is AGPL-3.0. If you
host a modified copy, AGPL section 13 requires offering users the corresponding source.
The in-browser ONNX runtime ([onnxruntime-web](https://github.com/microsoft/onnxruntime))
is MIT.

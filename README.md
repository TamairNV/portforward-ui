# portforward-ui

A tiny, dependency-free web UI to manage your router's port forwards over
**UPnP IGD** — no router admin password required. Pure Python 3 standard
library, runs on macOS or Linux.

Handy when the router's web admin is locked down or ISP-managed (e.g. a
carrier Hitron) but UPnP is still enabled on the LAN.

## Run

```bash
python3 portforward_ui.py            # binds 0.0.0.0:8765
python3 portforward_ui.py 9000       # custom port
HOST=127.0.0.1 PORT=9000 python3 portforward_ui.py
```

Then open the URL it prints. Anyone on the same LAN can reach
`http://<server-ip>:8765`.

## Features

- Lists current forwards with one-click delete
- Add TCP / UDP / both, with description
- **"This device"** button autofills the IP of *whoever is viewing the page*
  (detected from the request), so each person forwards to their own machine
- Shows your public IP and highlights forwards pointing at your device
- Auto-refreshes every 15s; dark/light aware; works on phone

## How it works

On start it SSDP-discovers the Internet Gateway Device, reads its
`WANIPConnection`/`WANPPPConnection` control URL, and issues SOAP
`AddPortMapping` / `DeletePortMapping` / `GetGenericPortMappingEntry` calls.

## Security

- The UI has **no authentication** and exposes port-forward control to anyone
  who can reach it. Keep it **LAN-only** — never forward its own port to the
  internet.
- UPnP must be enabled on the router. If `add` is rejected for a *different*
  device's IP, open the page *from that device* (some UPnP stacks only allow a
  host to forward to itself).

## Run as a service (Linux)

See `portforward-ui.service`. Edit `User=` and the path, then:

```bash
sudo cp portforward-ui.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now portforward-ui
```

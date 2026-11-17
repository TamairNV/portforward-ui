# portforward-ui

A tiny, dependency-free web UI to manage your router's port forwards over
**UPnP IGD** — no router admin password required. Pure Python 3 standard
library, runs on macOS or Linux.

Handy when the router's web admin is locked down or ISP-managed (e.g. a
carrier Hitron) but UPnP is still enabled on the LAN.

## How this came to exist

I was at a friend's house trying to set up a port forward, and the router's
admin page at `192.168.0.1` simply wouldn't load. The usual story would be
"wrong gateway IP" — but it wasn't that simple. Here's the trail:

1. **The gateway was correct, but only half-alive.** `192.168.0.1` *pinged*
   fine (~8ms, 0% loss) and was genuinely the default gateway. So routing was
   not the problem — it was specifically the *web page* that wouldn't load.

2. **Ruled out the usual suspects.** The machine had ~10 `utun` interfaces and
   Tailscale installed, so a VPN hijacking LAN traffic looked likely — but
   Tailscale was stopped and no exit node was active. The macOS firewall was
   off, no Little Snitch / LuLu. Nothing local was blocking anything.

3. **The gateway was dropping its own web ports on purpose.** A clean TCP probe
   was the giveaway: ports 8080/8443 returned an *instant* "connection refused"
   while ports 80 and 443 *timed out* (silently dropped). Same host, different
   behaviour per port — that's a deliberate filter, not a dead box. Its web
   admin was firewalled off from the LAN.

4. **So where *was* the admin page?** A quick parallel sweep of the whole
   `/24` across common web ports turned up three live pages — none on `.1`:
   `.13` and `.20` redirected to `btwhole.home` (BT Whole Home mesh discs), and
   `.37` served a `/2.0/gui/` login whose HTML carried a `(C) Sagemcom`
   copyright + a Sagemcom MAC — a **BT Smart Hub**. The login didn't work, and
   it turned out `.37` wasn't even the box doing the NAT.

5. **A traceroute settled the topology.** Hop 1 was `192.168.0.1`, hop 3 was a
   *public* IP — a single private hop, so **no double-NAT**: `.1` really is the
   edge router (a Hitron, model `CHITA`, ISP-managed). The BT kit was just a
   secondary Wi-Fi layer behind it. Meaning any port forward *had* to be set on
   `.1` — the one box with no reachable admin UI and a password nobody had.

6. **The way in: UPnP.** A wider port scan of `.1` showed one unexpected open
   port — `49153` — the classic **UPnP IGD** control port. SSDP discovery
   returned the device description (`IGDdevicedesc_brlan0.xml`, RDK-B/Hitron
   firmware), which exposed the `WANIPConnection` control URL. From there,
   plain SOAP calls could read the public IP, list existing forwards, and
   **add new ones with no login at all** — exactly what the locked admin UI
   was refusing to let anyone do.

This tool is that last step, wrapped in a friendly UI so it never has to be
rediscovered from scratch.

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

# portforward-ui

[![Python](https://img.shields.io/badge/python-3-3776ab?logo=python&logoColor=white)](https://www.python.org) [![Nix Flake](https://img.shields.io/badge/nix-flake-5277c3?logo=nixos&logoColor=white)](https://nixos.org) [![UPnP](https://img.shields.io/badge/UPnP-IGD-green)](https://en.wikipedia.org/wiki/Internet_Gateway_Device_Protocol)

## What is this?

A tiny, dependency-free web UI to manage a router's port forwards over
**UPnP IGD** — no router admin password required. The whole app is one
Python-3-standard-library file in [portforward_ui.py](portforward_ui.py), and a
Nix flake pins the runtime so it behaves the same on a laptop or a headless
NixOS box.

It exists because some routers — especially ISP-managed ones — lock their web
admin away but leave UPnP open on the LAN. This reaches the router the way a
game or torrent client does, and wraps it in a friendly page: list the current
forwards, add TCP/UDP forwards, and point them at the right device. The
**"This device"** button fills in the IP of *whoever is viewing the page*
(detected from the request), so each person on the network forwards to their
own machine.

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

This tool is that last step, wrapped in a UI so it never has to be
rediscovered from scratch.

## Getting started

With [Nix](https://nixos.org) and flakes enabled, run it straight from the
repo — no clone, no install:

```sh
nix run
```

Or enter a dev shell that provides Python (and `ip` for device discovery):

```sh
nix develop
python3 portforward_ui.py            # binds 0.0.0.0:8765
python3 portforward_ui.py 9000       # custom port
```

Without Nix, it is still just one stdlib file:

```sh
python3 portforward_ui.py
```

It prints a `http://127.0.0.1:8765` URL and a shareable `http://<lan-ip>:8765`
one. Open either; anyone on the same LAN can reach the LAN URL.

## Run on NixOS

The flake ships a NixOS module, so the mini PC runs it as a hardened,
auto-restarting service. Add the input and enable it:

```nix
{
  inputs.portforward-ui.url = "github:youruser/portforward-ui";

  # in your configuration.nix / host module:
  imports = [ inputs.portforward-ui.nixosModules.default ];

  services.portforward-ui = {
    enable = true;
    openFirewall = true;   # LAN only — never expose this to the internet
    # port = 8765;
    # host = "0.0.0.0";
  };
}
```

`nixos-rebuild switch` and it is up on boot. The service runs as a
`DynamicUser` with `ProtectSystem=strict` and writes nothing to disk.

On a non-NixOS Linux box, [portforward-ui.service](portforward-ui.service) is a
plain systemd unit you can copy to `/etc/systemd/system/` instead.

## Features

- Lists current forwards with one-click delete
- Add TCP / UDP / both, with a description
- **"This device"** autofills the viewer's IP, and forwards pointing at it are
  highlighted in the table
- Shows your public IP; auto-refreshes every 15s; dark/light aware; mobile-friendly

## How it works

[portforward_ui.py](portforward_ui.py) SSDP-discovers the Internet Gateway
Device on startup, reads its `WANIPConnection`/`WANPPPConnection` control URL
from the device description, and drives it with plain SOAP:
`GetExternalIPAddress`, `GetGenericPortMappingEntry`, `AddPortMapping`,
`DeletePortMapping`. The web layer is `http.server` from the standard library —
no third-party dependencies at all. LAN IP is found via the default-route
trick, and the neighbour table (`ip neigh`, falling back to `arp`) feeds the
device-suggestion dropdown.

[flake.nix](flake.nix) exposes `packages.default` (a wrapped `python3`),
`apps.default` (for `nix run`), `devShells.default` (for `nix develop`), and
`nixosModules.default` (the service above).

## Security

- The UI has **no authentication** and hands port-forward control to anyone who
  can reach it. Keep it **LAN-only** — never forward its own port outward.
- UPnP must be enabled on the router. If an `add` is rejected for a *different*
  device's IP, open the page *from that device* (some UPnP stacks only let a
  host forward to itself).

## Licence

MIT — do what you like.

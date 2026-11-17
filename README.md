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

## Keeping the target address put

A forward is `external port → <LAN IP>:port`, and that LAN IP comes from DHCP on
the same router whose admin page is firewalled off. Nothing holds it there: the
lease is 7 days, and a box that comes back on a different address leaves the
forward quietly pointing at whatever answers on the old one. The usual fix is a
DHCP reservation — which needs the admin UI — and UPnP IGD has no action for it
either, only port mappings. So the *target* has to hold its own address.

On the mini PC (`mini-nixos`, NixOS, NetworkManager) that is two pieces:

```nix
# The profile. Written to /run/NetworkManager/system-connections/lan.nmconnection
# at boot and on every rebuild.
networking.networkmanager.ensureProfiles.profiles.lan = {
  connection = {
    id = "lan";
    type = "ethernet";
    interface-name = "enp2s0";
    autoconnect = true;
    autoconnect-priority = 10;  # beats an autogenerated wired profile, which is 0
  };

  ipv4 = {
    method = "manual";
    address1 = "192.168.0.69/24,192.168.0.1";  # <address>/<prefix>,<gateway>
    dns = "192.168.0.1;1.1.1.1;";              # the router resolves; public fallback
    may-fail = false;
  };

  ipv6.method = "auto";
};

# `ensureProfiles` only writes the file and runs `nmcli connection reload`, and
# NetworkManager keeps whatever is already active — so without this, a rebuild
# that happens while a DHCP profile is up quietly stays on DHCP. No-ops when
# `lan` is already active, so an unrelated rebuild doesn't flap the link.
systemd.services.NetworkManager-activate-lan = {
  wantedBy = [ "multi-user.target" ];
  wants = [ "NetworkManager-ensure-profiles.service" ];
  after = [ "NetworkManager.service" "NetworkManager-ensure-profiles.service" ];

  path = [ pkgs.networkmanager pkgs.gnugrep ];
  serviceConfig.Type = "oneshot";

  script = ''
    if ! nmcli -t -f NAME connection show --active | grep -qFx "lan"; then
      nmcli connection up "lan"
    fi
  '';
};
```

Check it took — the route flips from `proto dhcp` to `proto static`:

```bash
nmcli -t -f NAME,DEVICE,STATE connection show --active   # lan:enp2s0:activated
ip route | head -1                                       # ... proto static ...
```

Two things to know before copying this:

- **The address is inside the router's DHCP pool**, and the pool can't be
  narrowed for the same reason a reservation can't be made. It mostly doesn't
  matter: while the box is up it answers the ARP probe any other client sends
  before claiming an address, so they pick something else. The gap is a
  power-off longer than the lease, after which the address is back in the pool
  and something else can take it first. Nothing host-side closes that.

- **Don't `nmcli connection modify lan` on a declarative profile.** It doesn't
  edit the generated keyfile — NetworkManager *migrates* the profile to
  `/etc/NetworkManager/system-connections/`, where it keeps the same UUID,
  outranks the copy in `/run`, and survives every later rebuild. The config
  then looks right in git while the machine ignores it. To undo:

  ```bash
  sudo rm /etc/NetworkManager/system-connections/lan.nmconnection
  sudo systemctl restart NetworkManager-ensure-profiles
  sudo nmcli connection reload && sudo nmcli connection up lan
  ```

  To try new values, add a throwaway profile (`con-name lan-test`,
  `autoconnect no`) and `nmcli connection up` that instead. Arm an undo first if
  you are over SSH — a wrong gateway locks you out of the box:

  ```bash
  sudo systemd-run --on-active=180 --unit=net-revert --collect \
    /run/current-system/sw/bin/bash -c \
    "nmcli connection delete lan-test; nmcli connection up lan"
  ```

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

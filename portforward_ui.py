#!/usr/bin/env python3
"""Local web UI to manage router port forwards via UPnP IGD — no router login needed.

Runs anywhere with Python 3 (macOS / Linux). Talks to the LAN gateway's UPnP
Internet Gateway Device over SSDP + SOAP. Bind host/port configurable:

    python3 portforward_ui.py                 # 0.0.0.0:8765
    python3 portforward_ui.py 9000            # custom port
    HOST=127.0.0.1 PORT=9000 python3 portforward_ui.py
"""
import os, re, sys, json, socket, subprocess
import urllib.request as u
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SVC = "urn:schemas-upnp-org:service:WANIPConnection:1"
SVC_PPP = "urn:schemas-upnp-org:service:WANPPPConnection:1"

# ---------------------------------------------------------------- UPnP discovery
def discover():
    """SSDP-discover the IGD; return (control_url, service_type) or (None, None)."""
    try:
        msg = "\r\n".join(['M-SEARCH * HTTP/1.1', 'HOST:239.255.255.250:1900',
            'MAN:"ssdp:discover"', 'MX:2',
            'ST:urn:schemas-upnp-org:device:InternetGatewayDevice:1', '', '']).encode()
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(3)
        s.sendto(msg, ('239.255.255.250', 1900))
        loc = None
        while True:
            d, _ = s.recvfrom(2048)
            for line in d.decode('latin1', 'replace').splitlines():
                if line.lower().startswith('location:'):
                    loc = line.split(':', 1)[1].strip(); break
            if loc:
                break
    except Exception:
        return None, None
    try:
        base = loc.rsplit('/', 1)[0]
        xml = u.urlopen(loc, timeout=5).read().decode('latin1', 'replace')
        for blk in re.findall(r'<service>(.*?)</service>', xml, re.S):
            st = re.search(r'<serviceType>(.*?)</serviceType>', blk)
            cu = re.search(r'<controlURL>(.*?)</controlURL>', blk)
            if st and cu and ('WANIPConnection' in st.group(1) or 'WANPPPConnection' in st.group(1)):
                c = cu.group(1)
                url = c if c.startswith('http') else base + (c if c.startswith('/') else '/' + c)
                return url, st.group(1)
    except Exception:
        pass
    return None, None

CTRL, CTRL_SVC = discover()

def soap(action, body=""):
    if not CTRL:
        raise RuntimeError("No UPnP gateway found on the network")
    env = (f'<?xml version="1.0"?><s:Envelope '
           f'xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
           f's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
           f'<s:Body><u:{action} xmlns:u="{CTRL_SVC}">{body}</u:{action}></s:Body></s:Envelope>')
    req = u.Request(CTRL, env.encode(),
        {"Content-Type": 'text/xml; charset="utf-8"', "SOAPAction": f'"{CTRL_SVC}#{action}"'})
    return u.urlopen(req, timeout=6).read().decode('latin1', 'replace')

def v(x, t):
    m = re.search(rf'<{t}>(.*?)</{t}>', x, re.S); return m.group(1) if m else ''

# ---------------------------------------------------------------- operations
def get_state():
    out = {"ctrl": CTRL or "", "wan": "", "mappings": [], "server_ip": server_ip(), "error": ""}
    if not CTRL:
        out["error"] = "No UPnP Internet Gateway Device found on this network."
        return out
    try:
        out["wan"] = v(soap("GetExternalIPAddress"), "NewExternalIPAddress")
    except Exception as e:
        out["error"] = f"WAN: {e}"
    i = 0
    while i < 80:
        try:
            r = soap("GetGenericPortMappingEntry", f"<NewPortMappingIndex>{i}</NewPortMappingIndex>")
        except Exception:
            break
        ext = v(r, "NewExternalPort")
        if not ext:
            break
        out["mappings"].append({"ext": ext, "proto": v(r, "NewProtocol"),
            "client": v(r, "NewInternalClient"), "iport": v(r, "NewInternalPort"),
            "enabled": v(r, "NewEnabled"), "desc": v(r, "NewPortMappingDescription")})
        i += 1
    return out

def add_map(ext, ip, intp, proto, desc):
    body = (f"<NewRemoteHost></NewRemoteHost><NewExternalPort>{ext}</NewExternalPort>"
            f"<NewProtocol>{proto}</NewProtocol><NewInternalPort>{intp}</NewInternalPort>"
            f"<NewInternalClient>{ip}</NewInternalClient><NewEnabled>1</NewEnabled>"
            f"<NewPortMappingDescription>{desc}</NewPortMappingDescription>"
            f"<NewLeaseDuration>0</NewLeaseDuration>")
    soap("AddPortMapping", body)

def del_map(ext, proto):
    soap("DeletePortMapping",
         f"<NewRemoteHost></NewRemoteHost><NewExternalPort>{ext}</NewExternalPort>"
         f"<NewProtocol>{proto}</NewProtocol>")

# ---------------------------------------------------------------- helpers
def server_ip():
    """This machine's LAN IP (the one on the default route)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))  # no packets sent for UDP connect; just picks the iface
        ip = s.getsockname()[0]; s.close(); return ip
    except Exception:
        return "127.0.0.1"

def normalize_client(addr):
    """Turn a raw request peer address into a usable LAN IPv4, or '' if loopback."""
    ip = addr.split('%')[0]
    if ip.startswith('::ffff:'):
        ip = ip[7:]
    if ip in ('127.0.0.1', '::1', 'localhost'):
        return ''
    return ip

def lan_hosts():
    """Known neighbours on the server's /24, for the datalist. Linux + macOS."""
    prefix = server_ip().rsplit('.', 1)[0] + '.'
    found = set()
    for cmd in (["ip", "neigh"], ["arp", "-an"]):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=4).stdout
        except Exception:
            continue
        for ip in re.findall(r'(\d+\.\d+\.\d+\.\d+)', out):
            if ip.startswith(prefix) and not ip.endswith(('.0', '.255')):
                found.add(ip)
        if found:
            break
    return sorted(found, key=lambda x: int(x.split('.')[-1]))

# ---------------------------------------------------------------- web
HTML = r"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Port Forwarding</title><style>
:root{--bg:#0d1117;--card:#161b22;--line:#30363d;--txt:#e6edf3;--mut:#8b949e;
--acc:#2f81f7;--ok:#3fb950;--bad:#f85149;}
@media(prefers-color-scheme:light){:root{--bg:#f6f8fa;--card:#fff;--line:#d0d7de;--txt:#1f2328;--mut:#656d76;--acc:#0969da;}}
*{box-sizing:border-box}body{margin:0;font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
background:var(--bg);color:var(--txt);-webkit-font-smoothing:antialiased}
.wrap{max-width:860px;margin:0 auto;padding:28px 20px 80px}
h1{font-size:22px;margin:0 0 2px;letter-spacing:-.3px}
.sub{color:var(--mut);font-size:13px;margin-bottom:22px}
.hero{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:24px}
.stat{flex:1;min-width:170px;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.stat .k{color:var(--mut);font-size:11px;text-transform:uppercase;letter-spacing:.06em}
.stat .val{font-size:19px;font-weight:600;margin-top:4px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;word-break:break-all}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px 18px 8px;margin-bottom:22px}
.card h2{font-size:14px;margin:0 0 14px;color:var(--mut);text-transform:uppercase;letter-spacing:.06em}
table{width:100%;border-collapse:collapse;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px}
th{text-align:left;color:var(--mut);font-weight:500;padding:6px 8px;border-bottom:1px solid var(--line);font-size:11px;text-transform:uppercase}
td{padding:9px 8px;border-bottom:1px solid var(--line)}tr:last-child td{border-bottom:0}
.proto{display:inline-block;padding:1px 7px;border-radius:5px;font-size:11px;font-weight:600}
.proto.TCP{background:rgba(47,129,247,.15);color:#79c0ff}.proto.UDP{background:rgba(63,185,80,.15);color:#56d364}
.me{outline:1px solid var(--acc);outline-offset:-1px}
.del{background:transparent;border:1px solid var(--line);color:var(--bad);border-radius:7px;padding:4px 10px;cursor:pointer;font-size:12px}
.del:hover{border-color:var(--bad);background:rgba(248,81,73,.08)}
.empty{color:var(--mut);padding:14px 8px;font-style:italic}
form{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}
label{display:block;font-size:12px;color:var(--mut);margin-bottom:5px}
input,select{width:100%;background:var(--bg);border:1px solid var(--line);color:var(--txt);border-radius:8px;padding:9px 11px;font-size:14px;font-family:inherit}
input:focus,select:focus{outline:none;border-color:var(--acc)}
.row2{grid-column:span 2}.ipwrap{display:flex;gap:8px}.ipwrap input{flex:1}
.mini{white-space:nowrap;background:var(--bg);border:1px solid var(--line);color:var(--acc);border-radius:8px;padding:0 12px;cursor:pointer;font-size:12px}
.mini:hover{border-color:var(--acc)}.mini:disabled{opacity:.4;cursor:default}
.btn{grid-column:span 2;background:var(--acc);border:0;color:#fff;border-radius:8px;padding:12px;font-size:15px;font-weight:600;cursor:pointer;margin-top:4px}
.btn:hover{filter:brightness(1.1)}.btn:disabled{opacity:.5;cursor:default}
.bar{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px}
.refresh{background:transparent;border:1px solid var(--line);color:var(--txt);border-radius:8px;padding:6px 12px;cursor:pointer;font-size:13px}
.refresh:hover{border-color:var(--acc)}
#toast{position:fixed;left:50%;bottom:26px;transform:translateX(-50%) translateY(20px);opacity:0;
background:var(--card);border:1px solid var(--line);padding:11px 18px;border-radius:10px;font-size:14px;
transition:.25s;pointer-events:none;max-width:90%;box-shadow:0 8px 30px rgba(0,0,0,.4)}
#toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
#toast.ok{border-color:var(--ok)}#toast.bad{border-color:var(--bad)}
@media(max-width:560px){form{grid-template-columns:1fr}.row2,.btn{grid-column:span 1}}
</style></head><body><div class=wrap>
<h1>Port Forwarding</h1>
<div class=sub id=sub>via UPnP · no router login needed</div>
<div class=hero>
  <div class=stat><div class=k>Public IP</div><div class=val id=wan>…</div></div>
  <div class=stat><div class=k>This device (you)</div><div class=val id=me>…</div></div>
  <div class=stat><div class=k>Active forwards</div><div class=val id=count>…</div></div>
</div>
<div class=card>
  <div class=bar><h2 style=margin:0>Current forwards</h2><button class=refresh onclick=load()>↻ Refresh</button></div>
  <table><thead><tr><th>Proto</th><th>Ext port</th><th>→ Internal</th><th>Description</th><th></th></tr></thead>
  <tbody id=rows></tbody></table>
</div>
<div class=card>
  <h2>Add a forward</h2>
  <form id=f onsubmit="return addMap(event)">
    <div><label>External port</label><input id=ext type=number min=1 max=65535 required placeholder=25565></div>
    <div><label>Protocol</label><select id=proto><option>TCP</option><option>UDP</option><option value=BOTH>TCP + UDP</option></select></div>
    <div class=row2><label>Internal device IP</label><div class=ipwrap>
      <input id=ip list=hosts required placeholder=192.168.0.50>
      <button type=button class=mini id=thisbtn onclick="fillMe()">This device</button>
    </div><datalist id=hosts></datalist></div>
    <div><label>Internal port</label><input id=iport type=number min=1 max=65535 placeholder="(same as ext)"></div>
    <div><label>Description</label><input id=desc placeholder="e.g. minecraft"></div>
    <button class=btn id=sb type=submit>Add forward</button>
  </form>
</div>
</div><div id=toast></div>
<script>
let MYIP='';let T;
function toast(m,k){let t=document.getElementById('toast');t.textContent=m;t.className='show '+(k||'');
clearTimeout(T);T=setTimeout(()=>t.className='',3600);}
function fillMe(){if(MYIP)document.getElementById('ip').value=MYIP;}
async function load(){
  let s;try{s=await (await fetch('/api/state')).json();}catch(e){toast('Cannot reach server','bad');return;}
  MYIP=s.client_ip||'';
  document.getElementById('wan').textContent=s.wan||'?';
  document.getElementById('me').textContent=MYIP||('server '+(s.server_ip||'?'));
  document.getElementById('count').textContent=s.mappings.length;
  let btn=document.getElementById('thisbtn');btn.disabled=!MYIP;
  if(!MYIP)btn.title='Open this page from the device you want to forward to';
  let tb=document.getElementById('rows');tb.innerHTML='';
  if(!s.mappings.length){tb.innerHTML='<tr><td colspan=5 class=empty>No port forwards yet</td></tr>';}
  for(let m of s.mappings){let tr=document.createElement('tr');
    let mine=(m.client===MYIP)?' class=me':'';
    tr.innerHTML=`<td><span class="proto ${m.proto}">${m.proto}</span></td><td>${m.ext}</td>
      <td${mine}>${m.client}:${m.iport}</td><td>${m.desc||''}</td>
      <td style=text-align:right><button class=del>Delete</button></td>`;
    tr.querySelector('.del').onclick=()=>delMap(m.ext,m.proto);tb.appendChild(tr);}
  let dl=document.getElementById('hosts');dl.innerHTML='';
  for(let h of (s.hosts||[])){let o=document.createElement('option');o.value=h;dl.appendChild(o);}
  if(s.error)toast(s.error,'bad');
}
async function addMap(e){e.preventDefault();
  let ext=document.getElementById('ext').value,ip=document.getElementById('ip').value,
      iport=document.getElementById('iport').value||ext,proto=document.getElementById('proto').value,
      desc=document.getElementById('desc').value||('fwd '+ext);
  document.getElementById('sb').disabled=true;
  let j;try{j=await (await fetch('/api/add',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({ext,ip,iport,proto,desc})})).json();}catch(e){j={ok:false,error:'network'};}
  document.getElementById('sb').disabled=false;
  if(j.ok){toast('Added '+proto+' '+ext+' → '+ip+':'+iport,'ok');document.getElementById('f').reset();load();}
  else toast('Failed: '+j.error,'bad');
  return false;
}
async function delMap(ext,proto){
  if(!confirm('Delete '+proto+' forward on port '+ext+'?'))return;
  let j;try{j=await (await fetch('/api/del',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({ext,proto})})).json();}catch(e){j={ok:false,error:'network'};}
  if(j.ok){toast('Removed '+proto+' '+ext,'ok');load();}else toast('Failed: '+j.error,'bad');
}
load();setInterval(load,15000);
</script></body></html>"""

class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def _send(self, code, body, ctype="application/json"):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        if self.path == "/":
            return self._send(200, HTML, "text/html; charset=utf-8")
        if self.path == "/api/state":
            st = get_state()
            st["hosts"] = lan_hosts()
            st["client_ip"] = normalize_client(self.client_address[0])
            return self._send(200, json.dumps(st))
        self._send(404, "{}")
    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            d = json.loads(self.rfile.read(n) or b"{}")
            if self.path == "/api/add":
                if not CTRL:
                    return self._send(200, json.dumps({"ok": False, "error": "no UPnP gateway"}))
                protos = ["TCP", "UDP"] if d.get("proto") == "BOTH" else [d["proto"]]
                for p in protos:
                    add_map(d["ext"], d["ip"], d["iport"], p, d.get("desc", "fwd"))
                return self._send(200, json.dumps({"ok": True}))
            if self.path == "/api/del":
                del_map(d["ext"], d["proto"])
                return self._send(200, json.dumps({"ok": True}))
            return self._send(404, "{}")
        except u.HTTPError as e:
            msg = e.read().decode('latin1', 'replace')
            em = re.search(r'<errorDescription>(.*?)</errorDescription>', msg)
            return self._send(200, json.dumps({"ok": False, "error": em.group(1) if em else f"HTTP {e.code}"}))
        except Exception as e:
            return self._send(200, json.dumps({"ok": False, "error": str(e)}))

def main():
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("PORT", "8765"))
    while True:
        try:
            srv = ThreadingHTTPServer((host, port), H); break
        except OSError:
            port += 1
    print(f"UPnP gateway: {CTRL or 'NOT FOUND — is UPnP enabled on the router?'}")
    print(f"On this host:       http://127.0.0.1:{port}")
    print(f"Share on the LAN:   http://{server_ip()}:{port}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Read-only LAN landing page for the HP NAS and Dell development server."""

from __future__ import annotations

import html
import ipaddress
import json
import os
import socket
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def ipv4_setting(name: str) -> str:
    value = os.environ[name]
    address = ipaddress.IPv4Address(value)
    if not address.is_private:
        raise ValueError(f"{name} must be a private IPv4 address")
    return str(address)


HP_IP = ipv4_setting("HOMELAB_HP_IP")
DELL_IP = ipv4_setting("HOMELAB_DELL_IP")
PORT = 8088


def service_state(name: str) -> str:
    try:
        result = subprocess.run(
            ["systemctl", "is-active", name],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        return "active" if result.returncode == 0 else "inactive"
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


def disk_state(path: str) -> dict[str, str]:
    if not Path(path).is_mount():
        return {"state": "not mounted", "space": "—"}
    try:
        stat = os.statvfs(path)
        total = stat.f_blocks * stat.f_frsize
        free = stat.f_bavail * stat.f_frsize
        used = total - free
        return {
            "state": "mounted",
            "space": f"{used / 2**30:.0f} / {total / 2**30:.0f} GiB used",
        }
    except OSError:
        return {"state": "unavailable", "space": "—"}


def memory_state() -> str:
    try:
        values = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, value = line.split(":", 1)
            values[key] = int(value.strip().split()[0])
        used = values["MemTotal"] - values["MemAvailable"]
        return f"{used / 2**20:.1f} / {values['MemTotal'] / 2**20:.1f} GiB used"
    except (OSError, KeyError, ValueError):
        return "unavailable"


def load_state() -> str:
    try:
        return f"{os.getloadavg()[0]:.2f}"
    except (OSError, AttributeError):
        return "unavailable"


def reachable(ip: str, port: int) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=0.5):
            return True
    except OSError:
        return False


def snapshot() -> dict:
    dell_ssh = reachable(DELL_IP, 22)
    dell_cockpit = reachable(DELL_IP, 9090)
    dell_portainer = reachable(DELL_IP, 9443)
    return {
        "hp": {
            "memory": memory_state(),
            "load": load_state(),
            "ssd": disk_state("/srv/homelab"),
            "bulk": disk_state("/srv/bulk"),
            "samba": service_state("smbd.service"),
            "ssh": service_state("ssh.service"),
        },
        "dell": {
            "online": any((dell_ssh, dell_cockpit, dell_portainer)),
            "ssh": dell_ssh,
            "cockpit": dell_cockpit,
            "portainer": dell_portainer,
        },
    }


CSS = """
:root{color-scheme:dark;font-family:system-ui,sans-serif;background:#101522;color:#e6edf7}
*{box-sizing:border-box}body{margin:0 auto;max-width:1040px;padding:36px 20px 64px}
header{display:flex;justify-content:space-between;align-items:baseline;gap:20px;margin-bottom:28px}
h1{font-size:2rem;margin:0}h2{font-size:1.15rem;margin:0 0 16px}p{color:#aebbd0;line-height:1.5}
.eyebrow{font-size:.75rem;letter-spacing:.14em;text-transform:uppercase;color:#75d4c5;margin:0 0 8px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px}
.card{background:#1b2333;border:1px solid #36425a;border-radius:14px;padding:22px}
.sub{color:#aebbd0;margin-top:-7px}dl{display:grid;grid-template-columns:1fr auto;gap:10px 14px}
dt{color:#aebbd0}dd{margin:0;text-align:right;font-variant-numeric:tabular-nums}
.ok{color:#77dfae}.bad{color:#ffb38f}.muted{color:#aebbd0}
a{color:#9cc7ff;text-decoration:none}a:hover{text-decoration:underline}
.links{display:flex;flex-wrap:wrap;gap:10px;margin-top:21px}
.links a{padding:9px 12px;background:#263751;border-radius:8px}
.note{font-size:.9rem;margin-top:28px}
"""


def badge(value: str, good: bool) -> str:
    css_class = "ok" if good else "bad"
    return f'<span class="{css_class}">{html.escape(value)}</span>'


def render_page(data: dict) -> bytes:
    hp = data["hp"]
    dell = data["dell"]
    dell_state = "Online" if dell["online"] else "Offline"
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="20"><title>Homelab control</title><link rel="stylesheet" href="/style.css"></head>
<body><header><div><p class="eyebrow">Network control</p><h1>Homelab</h1></div><p>Refreshes every 20 seconds</p></header>
<main class="grid">
<section class="card"><h2>HP · storage</h2><p class="sub">Always on · {html.escape(HP_IP)}</p>
<dl><dt>Memory</dt><dd>{html.escape(hp['memory'])}</dd><dt>1-minute load</dt><dd>{html.escape(hp['load'])}</dd>
<dt>SSD /srv/homelab</dt><dd>{badge(hp['ssd']['state'], hp['ssd']['state'] == 'mounted')}</dd>
<dt>SSD space</dt><dd>{html.escape(hp['ssd']['space'])}</dd>
<dt>HDD /srv/bulk</dt><dd>{badge(hp['bulk']['state'], hp['bulk']['state'] == 'mounted')}</dd>
<dt>HDD space</dt><dd>{html.escape(hp['bulk']['space'])}</dd>
<dt>Samba</dt><dd>{badge(hp['samba'], hp['samba'] == 'active')}</dd>
<dt>SSH</dt><dd>{badge(hp['ssh'], hp['ssh'] == 'active')}</dd></dl>
<div class="links"><a href="https://{HP_IP}:9090/">Manage HP in Cockpit ↗</a></div></section>
<section class="card"><h2>Dell · development</h2><p class="sub">On demand · {html.escape(DELL_IP)}</p>
<dl><dt>Server</dt><dd>{badge(dell_state, dell['online'])}</dd>
<dt>SSH</dt><dd>{badge('Available' if dell['ssh'] else 'Unavailable', dell['ssh'])}</dd>
<dt>Cockpit</dt><dd>{badge('Available' if dell['cockpit'] else 'Unavailable', dell['cockpit'])}</dd>
<dt>Portainer</dt><dd>{badge('Available' if dell['portainer'] else 'Unavailable', dell['portainer'])}</dd></dl>
<div class="links"><a href="https://{DELL_IP}:9090/">Manage Dell in Cockpit ↗</a>
<a href="https://{DELL_IP}:9443/">Manage containers in Portainer ↗</a></div></section>
</main><p class="note">This page shows status only. Cockpit and Portainer handle sign-in and changes. The Dell must be powered on for its tools and containers.</p>
</body></html>"""
    return page.encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        route = self.path.split("?", 1)[0]
        if route == "/":
            body, content_type = render_page(snapshot()), "text/html; charset=utf-8"
        elif route == "/style.css":
            body, content_type = CSS.encode(), "text/css; charset=utf-8"
        elif route == "/healthz":
            body, content_type = json.dumps({"status": "ok"}).encode(), "application/json"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'self'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    server = ThreadingHTTPServer((HP_IP, PORT), Handler)
    print(f"Homelab landing page listening on http://{HP_IP}:{PORT}", flush=True)
    server.serve_forever()

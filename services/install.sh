#!/usr/bin/env bash
# Install the owned application and its services. Never partition or format disks.
set -Eeuo pipefail
[[ $EUID -eq 0 && $# -eq 3 ]] || { echo 'Usage: sudo bash services/install.sh hp|dell THIS_LAN_IP OTHER_SERVER_IP'; exit 2; }
role=$1 own_ip=$2 peer_ip=$3
[[ $role == hp || $role == dell ]] || { echo 'Role must be hp or dell'; exit 2; }
source /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 24.04 ]] || { echo 'Ubuntu 24.04 is required'; exit 2; }
python3 - "$own_ip" "$peer_ip" <<'PY'
import ipaddress,sys
for value in sys.argv[1:]:
    ip=ipaddress.IPv4Address(value)
    if not ip.is_private or ip.is_loopback or ip.is_link_local: raise SystemExit('Use real private LAN IPv4 addresses')
PY
ip -o -4 address | awk '{split($4,a,"/");print a[1]}' | grep -Fxq "$own_ip" || { echo "$own_ip is not assigned to this server"; exit 2; }
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
[[ -f $source_dir/requirements.lock && -f $source_dir/homelab/app.py ]] || { echo 'Run from the complete repo clone'; exit 2; }
[[ -f "$source_dir/../docs/guide.html" ]] || { echo 'The rendered install guide is missing; use a complete release clone.'; exit 2; }
if [[ $role == hp ]]; then
  mountpoint -q /srv/bulk && mountpoint -q /srv/homelab || { echo 'Finish HP storage and NAS setup first'; exit 2; }
  [[ -d /srv/bulk/models ]] || { echo 'The Models share is missing'; exit 2; }
  hp_ip=$own_ip dell_ip=$peer_ip port=8088
else
  hp_ip=$peer_ip dell_ip=$own_ip port=8090
fi
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3-venv python3-pip restic curl openssh-client sudo
if ! id homelab >/dev/null 2>&1; then useradd --system --user-group --home-dir /var/lib/homelab-app --shell /usr/sbin/nologin homelab; fi
[[ $(id -u homelab) -ne 0 ]] || { echo 'homelab must be an unprivileged account'; exit 2; }
if [[ $role == hp ]]; then usermod -a -G homelabshare homelab; fi
install -d -m 0750 -o homelab -g homelab /var/lib/homelab-app /srv/homelab-projects
install -d -m 0750 -o root -g homelab /etc/homelab /etc/homelab/app-config /etc/homelab/app-config/backups
install -d -m 0755 /opt/homelab/apps
if [[ ! -f /etc/homelab/app-token ]]; then
  python3 -c 'import secrets; print(secrets.token_urlsafe(40))' > /etc/homelab/app-token
fi
chown root:homelab /etc/homelab/app-token
chmod 0640 /etc/homelab/app-token
python3 - "$role" "$own_ip" "$hp_ip" "$dell_ip" "$port" <<'PY'
import json,pathlib,sys
role,own,hp,dell,port=sys.argv[1:]
p=pathlib.Path('/etc/homelab/apps.json')
desired=dict(role=role,listen_host=own,port=int(port),state_dir='/var/lib/homelab-app',
             models_dir='/srv/bulk/models',projects_dir='/srv/homelab-projects',config_dir='/etc/homelab/app-config',
             hp_url=f'http://{hp}:8088',dell_url=f'http://{dell}:8090',token_file='/etc/homelab/app-token',allow_network_downloads=True)
if p.exists():
    old=json.loads(p.read_text())
    for key in ('role','listen_host','port','state_dir','models_dir','projects_dir','config_dir','hp_url','dell_url','token_file'):
        if old.get(key)!=desired[key]: raise SystemExit(f'Existing apps.json differs at {key}; inspect it before changing addresses or paths')
else: p.write_text(json.dumps(desired,indent=2)+'\n')
PY
chown root:homelab /etc/homelab/apps.json
chmod 0640 /etc/homelab/apps.json
python3 -m venv /opt/homelab/apps/venv
/opt/homelab/apps/venv/bin/python -m pip install --require-hashes -r "$source_dir/requirements.lock"
# The old landing page and ping demo are replaced only after dependencies install.
systemctl stop homelab-app.service homelab-mcp.service 2>/dev/null || true
if [[ $role == hp ]]; then systemctl disable --now homelab-dashboard.service 2>/dev/null || true; fi
if [[ $role == dell && -f /opt/homelab/mcp/dell-demo/compose.yaml ]]; then
  (cd /opt/homelab/mcp/dell-demo && docker compose down)
fi
if [[ -d /opt/homelab/apps/homelab ]]; then
  cp -a /opt/homelab/apps/homelab "/opt/homelab/apps/homelab.previous.$(date +%Y%m%d%H%M%S)"
fi
install -d -m 0755 /opt/homelab/apps/homelab /opt/homelab/apps/homelab/static
for file in "$source_dir"/homelab/*.py; do install -m 0644 "$file" /opt/homelab/apps/homelab/; done
for file in "$source_dir"/homelab/static/*; do install -m 0644 "$file" /opt/homelab/apps/homelab/static/; done
install -m 0644 "$source_dir/requirements.lock" /opt/homelab/apps/requirements.lock
install -d -m 0755 /opt/homelab/apps/guide
cp -a "$source_dir/../docs/." /opt/homelab/apps/guide/
cat > /etc/systemd/system/homelab-app.service <<EOF
[Unit]
Description=Homelab application ($role)
After=network-online.target
Wants=network-online.target
RequiresMountsFor=/var/lib/homelab-app /srv/homelab-projects

[Service]
Type=simple
User=homelab
Group=homelab
WorkingDirectory=/opt/homelab/apps
ExecStart=/opt/homelab/apps/venv/bin/python -m homelab.app --config /etc/homelab/apps.json
Restart=on-failure
RestartSec=5
KillMode=control-group
TimeoutStopSec=30
UMask=0027
PrivateTmp=true
ProtectSystem=full
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
TasksMax=256
MemoryMax=2G

[Install]
WantedBy=multi-user.target
EOF
if [[ $role == hp ]]; then
  install -d /etc/systemd/system/homelab-app.service.d
  printf '[Unit]\nRequiresMountsFor=/srv/bulk/models /srv/homelab\n' > /etc/systemd/system/homelab-app.service.d/storage.conf
  printf 'homelab ALL=(root) NOPASSWD: /usr/bin/python3 /usr/local/sbin/game-lun-controller.py status\n' > /etc/sudoers.d/homelab-steam-status
  chmod 0440 /etc/sudoers.d/homelab-steam-status
  visudo -cf /etc/sudoers.d/homelab-steam-status
else
  cat > /etc/systemd/system/homelab-mcp.service <<'EOF'
[Unit]
Description=Homelab developer MCP tools
After=homelab-app.service
Requires=homelab-app.service
[Service]
User=homelab
Group=homelab
WorkingDirectory=/opt/homelab/apps
ExecStart=/opt/homelab/apps/venv/bin/python -m homelab.mcp_server --config /etc/homelab/apps.json
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
UMask=0027
[Install]
WantedBy=multi-user.target
EOF
fi
systemctl daemon-reload
systemctl enable --now homelab-app.service
[[ $role != dell ]] || systemctl enable --now homelab-mcp.service
ready=0
for attempt in {1..20}; do
  if curl --fail --silent --max-time 2 "http://$own_ip:$port/healthz"; then ready=1; break; fi
  sleep 1
done
[[ $ready == 1 ]] || { journalctl -u homelab-app -n 40 --no-pager; exit 1; }
printf '\nOpen http://%s:%s/\nLogin token (save privately):\n' "$own_ip" "$port"
cat /etc/homelab/app-token
printf '\nThe app is installed. Follow the guide to configure the Windows control app and a backup destination.\n'

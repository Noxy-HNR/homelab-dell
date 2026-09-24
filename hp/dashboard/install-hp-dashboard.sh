#!/usr/bin/env bash
# Install the read-only landing page and Cockpit on the always-on HP NAS.
set -euo pipefail

usage() {
  echo 'Usage: sudo bash install-hp-dashboard.sh HP_LAN_IP DELL_LAN_IP'
}
if [[ $# -ne 2 ]]; then usage; exit 2; fi
[[ $(id -u) -eq 0 ]] || { echo 'Run with sudo.' >&2; exit 2; }
source /etc/os-release
[[ ${ID:-} == ubuntu && ${VERSION_ID:-} == 24.04 ]] || { echo 'Ubuntu Server 24.04 is required.' >&2; exit 2; }
HP_IP=$1
DELL_IP=$2
python3 - "$HP_IP" "$DELL_IP" <<'PY'
import ipaddress, sys
for value in sys.argv[1:]:
    ip = ipaddress.IPv4Address(value)
    if not ip.is_private or ip.is_loopback or ip.is_link_local:
        raise SystemExit(f'Expected a private LAN IPv4 address: {value}')
PY
ip -4 -o address show scope global | awk '{split($4,a,"/");print a[1]}' | grep -Fxq "$HP_IP" || {
  echo "HP address $HP_IP is not assigned to this server." >&2; exit 2;
}

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
DEST=/opt/homelab/dashboard
CONFIG=/etc/homelab-dashboard.conf
UNIT=/etc/systemd/system/homelab-dashboard.service
install -d -m 0755 "$DEST"
if [[ -e $DEST/dashboard.py ]]; then
  cmp -s "$SCRIPT_DIR/dashboard.py" "$DEST/dashboard.py" || {
    echo "Existing $DEST/dashboard.py differs; review it before updating." >&2; exit 2;
  }
else
  install -m 0644 "$SCRIPT_DIR/dashboard.py" "$DEST/dashboard.py"
fi

EXPECTED_CONFIG=$(printf 'HOMELAB_HP_IP=%s\nHOMELAB_DELL_IP=%s\n' "$HP_IP" "$DELL_IP")
if [[ -e $CONFIG ]]; then
  [[ $(cat "$CONFIG") == "$EXPECTED_CONFIG" ]] || {
    echo "Existing $CONFIG differs; review it before changing addresses." >&2; exit 2;
  }
else
  printf '%s\n' "$EXPECTED_CONFIG" > "$CONFIG"
  chmod 0644 "$CONFIG"
fi

if [[ -e $UNIT ]]; then
  cmp -s "$SCRIPT_DIR/homelab-dashboard.service" "$UNIT" || {
    echo "Existing $UNIT differs; review it before updating." >&2; exit 2;
  }
else
  install -m 0644 "$SCRIPT_DIR/homelab-dashboard.service" "$UNIT"
fi

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  cockpit cockpit-storaged cockpit-packagekit
systemctl enable --now cockpit.socket
systemctl daemon-reload
systemctl enable --now homelab-dashboard.service
systemctl is-active --quiet cockpit.socket
systemctl is-active --quiet homelab-dashboard.service
ready=0
for attempt in {1..15}; do
  if curl --fail --silent --max-time 2 "http://$HP_IP:8088/healthz"; then
    ready=1
    break
  fi
  sleep 1
done
[[ $ready -eq 1 ]] || {
  journalctl -u homelab-dashboard.service -n 50 --no-pager >&2
  echo 'Dashboard did not answer /healthz.' >&2
  exit 1
}
printf '\nOpen http://%s:8088/ from your LAN. Cockpit: https://%s:9090/\n' "$HP_IP" "$HP_IP"

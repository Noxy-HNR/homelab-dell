#!/usr/bin/env bash
# Install Cockpit and Portainer CE on the Dell Ubuntu development server.
set -euo pipefail

if [[ $# -ne 1 ]]; then echo 'Usage: sudo bash install-dell-dashboard.sh DELL_LAN_IP' >&2; exit 2; fi
[[ $(id -u) -eq 0 ]] || { echo 'Run with sudo.' >&2; exit 2; }
source /etc/os-release
[[ ${ID:-} == ubuntu && ${VERSION_ID:-} == 24.04 ]] || { echo 'Ubuntu Server 24.04 is required.' >&2; exit 2; }
DELL_IP=$1
python3 - "$DELL_IP" <<'PY'
import ipaddress, sys
ip = ipaddress.IPv4Address(sys.argv[1])
if not ip.is_private or ip.is_loopback or ip.is_link_local:
    raise SystemExit('Expected a private Dell LAN IPv4 address')
PY
ip -4 -o address show scope global | awk '{split($4,a,"/");print a[1]}' | grep -Fxq "$DELL_IP" || {
  echo "Dell address $DELL_IP is not assigned to this server." >&2; exit 2;
}
command -v docker >/dev/null || { echo 'Install Docker with ubuntu-server.sh first.' >&2; exit 2; }
docker compose version >/dev/null

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
DEST=/opt/homelab/dashboard/portainer
install -d -m 0755 "$DEST"
if [[ -e $DEST/compose.yaml ]]; then
  cmp -s "$SCRIPT_DIR/portainer-compose.yaml" "$DEST/compose.yaml" || {
    echo "Existing $DEST/compose.yaml differs; review it before updating." >&2; exit 2;
  }
else
  install -m 0644 "$SCRIPT_DIR/portainer-compose.yaml" "$DEST/compose.yaml"
fi
if [[ -e $DEST/.env ]]; then
  [[ $(cat "$DEST/.env") == "PORTAINER_BIND_IP=$DELL_IP" ]] || {
    echo "Existing $DEST/.env has a different address; review it before updating." >&2; exit 2;
  }
else
  printf 'PORTAINER_BIND_IP=%s\n' "$DELL_IP" > "$DEST/.env"
  chmod 0600 "$DEST/.env"
fi

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  cockpit cockpit-storaged cockpit-packagekit
systemctl enable --now cockpit.socket
(cd "$DEST" && docker compose config --quiet && docker compose up -d)
systemctl is-active --quiet cockpit.socket
(cd "$DEST" && docker compose ps)
printf 'Open https://%s:9090/ for Dell Cockpit and https://%s:9443/ for Portainer.\n' "$DELL_IP" "$DELL_IP"
echo 'Complete Portainer first-time admin setup in your browser.'
echo "Get its setup token with: cd $DEST && sudo docker compose logs portainer"

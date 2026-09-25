#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID -eq 0 && $# -eq 2 ]] || { echo 'Usage: sudo bash hp/dashboard/install-hp-dashboard.sh HP_IP DELL_IP'; exit 2; }
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends cockpit cockpit-storaged cockpit-packagekit
systemctl enable --now cockpit.socket
bash "$source_dir/services/install.sh" hp "$1" "$2"

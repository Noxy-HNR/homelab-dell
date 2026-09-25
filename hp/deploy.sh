#!/usr/bin/env bash
# Run only after Ubuntu and the two HP logical drives/mounts exist.
# This script never creates arrays, partitions, filesystems, or Windows volumes.
set -Eeuo pipefail

usage() {
  echo 'Usage: sudo bash hp/deploy.sh plan|install|verify HP_10G_IP DELL_LAN_IP'
}
[[ $# -eq 3 ]] || { usage; exit 2; }
action=$1 hp_ip=$2 dell_ip=$3
[[ $action == plan || $action == install || $action == verify ]] || { usage; exit 2; }
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

iface_for_ip() {
  ip -o -4 address show scope global | awk -v wanted="$hp_ip" '{split($4,a,"/"); if (a[1] == wanted) print $2}'
}
check_link() {
  local iface
  iface=$(iface_for_ip)
  [[ -n $iface && $(printf '%s\n' "$iface" | wc -l) -eq 1 ]] || {
    echo "HP address $hp_ip must belong to exactly one local interface." >&2; return 1;
  }
  if ! command -v ethtool >/dev/null; then
    echo "NIC $iface has $hp_ip; install ethtool to confirm 10 Gb/s before provisioning."
    return 2
  fi
  ethtool "$iface" | grep -Eq '^[[:space:]]*Speed:[[:space:]]*10000Mb/s' || {
    echo "NIC $iface is not reporting a 10 Gb/s link. Check the HP NIC, optic, fiber and Cisco port." >&2
    return 1
  }
  echo "PASS: HP $iface at 10 Gb/s ($hp_ip)"
}

nas_args=(--data-mount /srv/homelab --share-mount /srv/bulk --portal-ip "$hp_ip")
case $action in
  plan)
    bash "$script_dir/setup-nas.sh" --action plan "${nas_args[@]}"
    check_link || true
    echo 'Plan complete. Check both array health screens and the disk serials before install.'
    ;;
  install)
    [[ $EUID -eq 0 ]] || { echo 'Run install with sudo.' >&2; exit 2; }
    bash "$script_dir/setup-nas.sh" --action plan "${nas_args[@]}"
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ethtool curl
    check_link
    bash "$script_dir/setup-nas.sh" --action apply "${nas_args[@]}"
    if ! pdbedit -L -u homelabshare >/dev/null 2>&1; then
      echo 'Set the SMB password for homelabshare (you will enter it twice):'
      smbpasswd -a homelabshare
    fi
    bash "$script_dir/dashboard/install-hp-dashboard.sh" "$hp_ip" "$dell_ip"
    bash "$script_dir/setup-nas.sh" --action verify "${nas_args[@]}"
    ;;
  verify)
    [[ $EUID -eq 0 ]] || { echo 'Run verify with sudo.' >&2; exit 2; }
    check_link
    bash "$script_dir/setup-nas.sh" --action verify "${nas_args[@]}"
    systemctl is-active --quiet homelab-app.service
    curl --fail --silent --show-error --max-time 5 "http://$hp_ip:8088/healthz"
    echo 'PASS: HP NAS, 10G link and Homelab app'
    ;;
esac

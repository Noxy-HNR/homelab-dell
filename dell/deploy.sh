#!/usr/bin/env bash
# Run after installing Ubuntu and configuring the four-port copper bond.
set -Eeuo pipefail

usage() { echo 'Usage: sudo bash dell/deploy.sh plan|install|verify DELL_BOND_IP HP_IP'; }
[[ $# -eq 3 ]] || { usage; exit 2; }
action=$1 dell_ip=$2 hp_ip=$3
[[ $action == plan || $action == install || $action == verify ]] || { usage; exit 2; }
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

check_bond() {
  local status=/proc/net/bonding/bond0 count speed_count
  [[ -r $status ]] || { echo 'bond0 is missing; follow docs/DELL-NETWORK.md from local consoles.' >&2; return 1; }
  grep -Fq 'Bonding Mode: IEEE 802.3ad Dynamic link aggregation' "$status" || {
    echo 'bond0 is not in 802.3ad LACP mode.' >&2; return 1;
  }
  count=$(grep -c '^Slave Interface:' "$status" || true)
  speed_count=$(grep -c '^Speed: 1000 Mbps' "$status" || true)
  [[ $count -eq 4 && $speed_count -eq 4 ]] || {
    echo "Expected four 1 Gb/s bond members; found $count members and $speed_count at 1 Gb/s." >&2; return 1;
  }
  ip -4 -o address show dev bond0 | awk '{split($4,a,"/");print a[1]}' | grep -Fxq "$dell_ip" || {
    echo "Dell address $dell_ip is not assigned to bond0." >&2; return 1;
  }
  echo "PASS: Dell 4 x 1 Gb/s LACP bond at $dell_ip"
}

case $action in
  plan)
    bash "$script_dir/ubuntu-server.sh" preflight
    check_bond || true
    echo 'Plan complete. Confirm Cisco port-channel before install.'
    ;;
  install)
    [[ $EUID -eq 0 ]] || { echo 'Run install with sudo.' >&2; exit 2; }
    check_bond
    bash "$script_dir/ubuntu-server.sh" install-all "$dell_ip" "$hp_ip"
    ;;
  verify)
    [[ $EUID -eq 0 ]] || { echo 'Run verify with sudo.' >&2; exit 2; }
    check_bond
    bash "$script_dir/ubuntu-server.sh" verify-all
    ;;
esac

#!/usr/bin/env bash
# Post-install setup for the Dell Ubuntu development server. UNTESTED on target hardware.
# No disks or network addresses are selected by this script.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: sudo bash ubuntu-server.sh ACTION [arguments]

Actions (run in order after installing Ubuntu Server):
  install-all DELL_LAN_IP   Install base, Docker, dev tools, MCP demo and dashboard
  verify-all               Check those components without changing them
  preflight
  install-base
  install-docker
  install-dev
  install-mcp-demo
  verify-mcp-demo
EOF
}

action="${1:-}"
if [[ -z "$action" ]]; then usage; exit 2; fi
if [[ "$action" == install-all ]]; then
  [[ $# -eq 2 ]] || { usage; exit 2; }
else
  [[ $# -eq 1 ]] || { usage; exit 2; }
fi

if [[ ! -r /etc/os-release ]]; then echo 'Ubuntu os-release missing' >&2; exit 2; fi
. /etc/os-release
if [[ "${ID:-}" != ubuntu || "${VERSION_ID:-}" != 24.04 ]]; then
  echo "Expected Ubuntu 24.04 LTS; found ${PRETTY_NAME:-unknown}" >&2
  exit 2
fi
if [[ "$(uname -m)" != x86_64 ]]; then echo 'Expected x86_64 server' >&2; exit 2; fi

require_root() {
  if [[ "$(id -u)" -ne 0 ]]; then echo 'Run this action with sudo.' >&2; exit 2; fi
}

preflight() {
  printf 'OS: %s\nKernel: %s\n' "${PRETTY_NAME}" "$(uname -r)"
  printf 'CPU:\n'; lscpu | grep -E '^(Model name|CPU\(s\)|Virtualization):' || true
  printf 'Memory:\n'; free -h
  printf 'Disks (read only):\n'; lsblk -e7 -o NAME,TYPE,SIZE,TRAN,MODEL,FSTYPE,MOUNTPOINTS
  printf 'NICs:\n'; ip -brief -4 address || true
  printf 'PCI GPU/NICs:\n'
  if command -v lspci >/dev/null 2>&1; then
    lspci | grep -Ei 'vga|3d controller|ethernet|network controller' || true
  else
    echo 'pciutils not installed yet'
  fi
  printf 'NVIDIA driver:\n'; if command -v nvidia-smi >/dev/null 2>&1; then nvidia-smi; else echo 'not installed'; fi
  printf 'Docker:\n'; if command -v docker >/dev/null 2>&1; then docker --version; else echo 'not installed'; fi
}

install_base() {
  require_root
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ca-certificates curl gnupg git jq openssh-server python3 python3-venv \
    python3-pip pciutils iproute2 openssl
  systemctl enable --now ssh
  echo 'Base tools installed. Review SSH access and your router before exposing this host.'
}

install_docker() {
  require_root
  if dpkg-query -W -f='${Status}' docker.io 2>/dev/null | grep -q 'install ok installed'; then
    echo 'Ubuntu docker.io is installed; review migration before using the Docker CE repository.' >&2
    exit 2
  fi
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates curl
  install -m 0755 -d /etc/apt/keyrings
  if [[ ! -e /etc/apt/keyrings/docker.asc ]]; then
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  fi
  chmod a+r /etc/apt/keyrings/docker.asc
  if [[ ! -e /etc/apt/sources.list.d/docker.sources ]]; then
    cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${UBUNTU_CODENAME:-$VERSION_CODENAME}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
  fi
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y docker-ce docker-ce-cli containerd.io \
    docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
  docker version --format '{{.Server.Version}}'
  echo 'Docker installed. Use sudo for Docker; no user was added to its root-equivalent group.'
}

install_dev() {
  require_root
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    build-essential cmake ninja-build pkg-config git-lfs ripgrep \
    python3-venv pipx htop smartmontools
  git lfs install --system
  echo 'Development tools installed. Clone projects into the intended user home directory.'
}

mcp_demo_dir='/opt/homelab/mcp/dell-demo'

install_mcp_demo() {
  require_root
  command -v docker >/dev/null 2>&1 || { echo 'Install Docker first.' >&2; exit 2; }
  docker compose version >/dev/null
  local source_dir
  source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/mcp-host"
  [[ -d "$source_dir" ]] || { echo 'Copy the whole setup directory to this server.' >&2; exit 2; }
  install -d -m 0755 "$mcp_demo_dir"
  local name
  for name in Dockerfile compose.yaml server.py smoke_client.py; do
    [[ -f "$source_dir/$name" ]] || { echo "Missing $source_dir/$name" >&2; exit 2; }
    if [[ -e "$mcp_demo_dir/$name" ]]; then
      cmp -s "$source_dir/$name" "$mcp_demo_dir/$name" || {
        echo "Existing $mcp_demo_dir/$name differs; review it before an update." >&2
        exit 2
      }
    else
      install -m 0644 "$source_dir/$name" "$mcp_demo_dir/$name"
    fi
  done
  (cd "$mcp_demo_dir" && docker compose config --quiet && docker compose up -d --build)
  echo "MCP demo installed at $mcp_demo_dir"
  echo 'Run verify-mcp-demo, then connect the laptop through the SSH tunnel in mcp-host/README.md.'
}

verify_mcp_demo() {
  require_root
  [[ -f "$mcp_demo_dir/compose.yaml" ]] || { echo 'MCP demo not installed.' >&2; exit 2; }
  (cd "$mcp_demo_dir" && docker compose ps)
  local attempt output
  for attempt in {1..30}; do
    if output=$(cd "$mcp_demo_dir" && docker compose exec -T dell-mcp-demo python smoke_client.py 2>&1); then
      printf '%s\n' "$output"
      return 0
    fi
    sleep 1
  done
  printf '%s\n' "$output" >&2
  (cd "$mcp_demo_dir" && docker compose logs --tail=80) >&2
  echo 'MCP demo did not answer the smoke test.' >&2
  return 1
}

verify_all() {
  require_root
  local binary
  for binary in git python3 gcc cmake ninja docker; do
    command -v "$binary" >/dev/null || { echo "Missing $binary" >&2; return 1; }
  done
  systemctl is-active --quiet ssh.service
  systemctl is-active --quiet docker.service
  systemctl is-active --quiet cockpit.socket
  docker compose version
  verify_mcp_demo
  local portainer_dir=/opt/homelab/dashboard/portainer
  [[ -f "$portainer_dir/compose.yaml" ]] || { echo 'Portainer is not installed.' >&2; return 1; }
  (cd "$portainer_dir" && docker compose ps --status running --services) | grep -Fxq portainer || {
    echo 'Portainer container is not running.' >&2; return 1;
  }
  echo 'PASS: Dell base, Docker, dev tools, MCP demo, Cockpit and Portainer'
}

install_all() {
  require_root
  local dell_ip=$1 source_dir
  python3 - "$dell_ip" <<'PY'
import ipaddress, sys
address = ipaddress.IPv4Address(sys.argv[1])
if not address.is_private or address.is_loopback or address.is_link_local:
    raise SystemExit('Expected the Dell private LAN IPv4 address')
PY
  ip -4 -o address show scope global | awk '{split($4,a,"/");print a[1]}' | grep -Fxq "$dell_ip" || {
    echo "Dell address $dell_ip is not assigned to this server." >&2; return 2;
  }
  preflight
  install_base
  install_docker
  install_dev
  install_mcp_demo
  source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
  [[ -f "$source_dir/dashboard/install-dell-dashboard.sh" ]] || {
    echo 'Copy the whole setup directory to the Dell.' >&2; return 2;
  }
  bash "$source_dir/dashboard/install-dell-dashboard.sh" "$dell_ip"
  verify_all
}

case "$action" in
  install-all) install_all "$2" ;;
  verify-all) verify_all ;;
  preflight) preflight ;;
  install-base) install_base ;;
  install-docker) install_docker ;;
  install-dev) install_dev ;;
  install-mcp-demo) install_mcp_demo ;;
  verify-mcp-demo) verify_mcp_demo ;;
  *) usage; exit 2 ;;
esac

#!/usr/bin/env bash
# Installs only controller/account configuration. Does not format or attach disks.
# Each PC gets its own account (gamehandoff-desktop, gamehandoff-laptop) whose key and
# sudoers entries only allow status, claim and release for that PC's own --host.
set -Eeuo pipefail
CONTROLLER=/usr/local/sbin/game-lun-controller.py
HANDOFF_HOSTS=(desktop laptop)
account_for() {
  case $1 in desktop|laptop) printf 'gamehandoff-%s\n' "$1" ;; *) echo "Unknown host: $1" >&2; return 1 ;; esac
}
render_keys() {
  printf 'restrict %s\n' "$(tr -d '\r\n' < "$1")"
}
render_sudoers() {
  local host=$1 user
  user=$(account_for "$host") || return 1
  printf '%s ALL=(root) NOPASSWD: /usr/bin/python3 %s %s\n' \
    "$user" "$CONTROLLER" 'status' \
    "$user" "$CONTROLLER" "claim --host $host" \
    "$user" "$CONTROLLER" "release --host $host --clean-client-offline"
}
# Sourcing (offline tests) stops here, before any root check or system change.
[[ ${BASH_SOURCE[0]} == "$0" ]] || return 0

[[ $EUID -eq 0 ]] || { echo 'Run with sudo.' >&2; exit 1; }
[[ $# -eq 3 ]] || { echo 'Usage: sudo bash install-controller.sh controller.json desktop.pub laptop.pub' >&2; exit 1; }
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
CONFIG=$(realpath -e -- "$1")
declare -A KEY_FILE
KEY_FILE[desktop]=$(realpath -e -- "$2")
KEY_FILE[laptop]=$(realpath -e -- "$3")
python3 - "$SCRIPT_DIR" "$CONFIG" <<'PY'
import json, sys
sys.path.insert(0, sys.argv[1])
import game_lun_controller as controller
with open(sys.argv[2], encoding='utf-8') as source:
    controller.validate_config(json.load(source))
PY
for host in "${HANDOFF_HOSTS[@]}"; do
  key=${KEY_FILE[$host]}
  ssh-keygen -lf "$key" >/dev/null
  [[ $(awk 'NF {n++} END {print n}' "$key") == 1 ]] || { echo 'Each public-key file must contain exactly one key.' >&2; exit 1; }
  grep -Eq '^(ssh-ed25519|ssh-rsa|ecdsa-sha2-[^ ]+) ' "$key" || { echo 'Supply an OpenSSH public key without authorized_keys options.' >&2; exit 1; }
done
[[ $(ssh-keygen -lf "${KEY_FILE[desktop]}" | awk '{print $2}') != "$(ssh-keygen -lf "${KEY_FILE[laptop]}" | awk '{print $2}')" ]] ||
  { echo 'Desktop and laptop must use different SSH keys.' >&2; exit 1; }
# The former shared account let either key claim either host; refuse to install alongside it.
[[ ! -e /etc/sudoers.d/gamehandoff && ! -L /etc/sudoers.d/gamehandoff ]] ||
  { echo 'Legacy /etc/sudoers.d/gamehandoff grants both hosts; remove it (and retire the gamehandoff account) first.' >&2; exit 1; }
if id gamehandoff >/dev/null 2>&1; then
  echo 'Warning: legacy gamehandoff account still exists; lock it or remove its authorized_keys.' >&2
fi

check_existing() {
  local source=$1 dest=$2
  if [[ -e $dest || -L $dest ]]; then
    [[ ! -L $dest ]] && cmp -s -- "$source" "$dest" || { echo "Existing $dest differs; inspect it before replacing it." >&2; exit 1; }
  fi
}
safe_install() {
  local source=$1 dest=$2 mode=$3 owner=${4:-root}
  check_existing "$source" "$dest"
  install -o "$owner" -g "$owner" -m "$mode" -- "$source" "$dest"
}

TEMP_DIR=$(mktemp -d)
trap 'rm -rf -- "$TEMP_DIR"' EXIT
# Render and preflight every file before changing anything.
check_existing "$SCRIPT_DIR/game_lun_controller.py" "$CONTROLLER"
check_existing "$CONFIG" /etc/homelab/game-lun-controller.json
for host in "${HANDOFF_HOSTS[@]}"; do
  user=$(account_for "$host")
  render_keys "${KEY_FILE[$host]}" > "$TEMP_DIR/$host.keys"
  render_sudoers "$host" > "$TEMP_DIR/$host.sudoers"
  visudo -cf "$TEMP_DIR/$host.sudoers"
  check_existing "$TEMP_DIR/$host.keys" "/home/$user/.ssh/authorized_keys"
  check_existing "$TEMP_DIR/$host.sudoers" "/etc/sudoers.d/$user"
done

install -d -o root -g root -m 0700 /etc/homelab /var/lib/homelab
safe_install "$SCRIPT_DIR/game_lun_controller.py" "$CONTROLLER" 0755
safe_install "$CONFIG" /etc/homelab/game-lun-controller.json 0600
for host in "${HANDOFF_HOSTS[@]}"; do
  user=$(account_for "$host")
  if ! id "$user" >/dev/null 2>&1; then
    useradd --create-home --user-group --shell /bin/bash "$user"
  fi
  [[ $(id -u "$user") -ne 0 ]] || { echo "$user must not be UID 0." >&2; exit 1; }
  [[ $(getent passwd "$user" | cut -d: -f6) == "/home/$user" ]] || { echo "Unexpected $user home." >&2; exit 1; }
  [[ ! -L /home/$user/.ssh ]] || { echo "/home/$user/.ssh must not be a symlink." >&2; exit 1; }
  install -d -o "$user" -g "$user" -m 0700 "/home/$user/.ssh"
  safe_install "$TEMP_DIR/$host.keys" "/home/$user/.ssh/authorized_keys" 0600 "$user"
  safe_install "$TEMP_DIR/$host.sudoers" "/etc/sudoers.d/$user" 0440
done
if [[ -e /var/lib/homelab/game-lun-state.json ]]; then
  /usr/bin/python3 "$CONTROLLER" status
else
  /usr/bin/python3 "$CONTROLLER" bootstrap
fi
echo 'Controller installed. Set ControllerSshUser to gamehandoff-desktop / gamehandoff-laptop in each PC config.'
echo 'Pin the HP SSH host key on each PC before using GameDrive.ps1.'

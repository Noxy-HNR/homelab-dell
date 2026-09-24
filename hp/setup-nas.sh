#!/usr/bin/env bash
# Ubuntu 24.04 DL360p Gen8 NAS setup. Never partitions, formats, or selects a disk.
set -Eeuo pipefail

ACTION=plan
DATA_MOUNT=/srv/homelab
SHARE_MOUNT=
PORTAL_IP=
TARGET_IQN=iqn.2026-09.local.homelab:steam-games
SIZE_BYTES=$((256 * 1024 * 1024 * 1024))
RESERVE_BYTES=$((100 * 1024 * 1024 * 1024))
STATE_FILE=/etc/homelab-budget-nas/install-state
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

usage() {
  cat <<'EOF'
Usage: sudo bash ./setup-nas.sh --action plan|apply|verify --data-mount /srv/homelab --portal-ip 192.168.1.10 [--share-mount /srv/bulk] [--target-iqn iqn.2026-09.local.homelab:steam-games]

Run plan first. The data mount must already be an ext4 filesystem listed in
/etc/fstab. Apply creates shares and a fully allocated 256 GiB iSCSI backing
file, but does not partition, format, or mount any disk. It leaves LIO's target
portal group disabled with zero initiator ACLs.
EOF
}

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
note() { printf '%s\n' "$*"; }

while (($#)); do
  case "$1" in
    --action) ACTION=${2:?}; shift 2 ;;
    --data-mount) DATA_MOUNT=${2:?}; shift 2 ;;
    --share-mount) SHARE_MOUNT=${2:?}; shift 2 ;;
    --portal-ip) PORTAL_IP=${2:?}; shift 2 ;;
    --target-iqn) TARGET_IQN=${2:?}; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown argument: $1" ;;
  esac
done
[[ $ACTION == plan || $ACTION == apply || $ACTION == verify ]] || die 'Action must be plan, apply, or verify'
command -v python3 >/dev/null || die 'Install python3 before running this script'
[[ $DATA_MOUNT == /* && $DATA_MOUNT != / && $DATA_MOUNT != *[[:space:]]* ]] || die 'Data mount must be an absolute path without spaces and cannot be /'
[[ $TARGET_IQN =~ ^iqn\.[0-9]{4}-[0-9]{2}\.[A-Za-z0-9.-]+:[A-Za-z0-9._-]+$ ]] || die 'Invalid target IQN'
[[ -n $PORTAL_IP ]] || die 'Supply --portal-ip with this server’s 10 GbE IPv4 address'
python3 - "$PORTAL_IP" <<'PY' || die 'Invalid portal IPv4'
import ipaddress, sys
ip = ipaddress.IPv4Address(sys.argv[1])
assert ip.is_private and not ip.is_loopback and not ip.is_link_local
PY

[[ -r /etc/os-release ]] || die 'This script requires Ubuntu Server 24.04'
# shellcheck source=/etc/os-release
source /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 24.04 ]] || die 'This script supports Ubuntu Server 24.04 only'
[[ -d $DATA_MOUNT && ! -L $DATA_MOUNT ]] || die 'Data mount must be an existing non-symlink directory'
[[ $(realpath -e -- "$DATA_MOUNT") == "$DATA_MOUNT" ]] || die 'Use the canonical data mount path'
mountpoint -q -- "$DATA_MOUNT" || die 'Data directory is not an active mount point'
FSTYPE=$(findmnt --noheadings --output FSTYPE --mountpoint "$DATA_MOUNT")
[[ $FSTYPE == ext4 ]] || die "Expected ext4 data mount, found ${FSTYPE:-none}"
findmnt --fstab --mountpoint "$DATA_MOUNT" >/dev/null || die 'Add the ext4 data mount to /etc/fstab before continuing'
SHARE_MOUNT=${SHARE_MOUNT:-$DATA_MOUNT}
[[ $SHARE_MOUNT == /* && $SHARE_MOUNT != / && $SHARE_MOUNT != *[[:space:]]* ]] || die 'Share mount must be absolute, without spaces, and cannot be /'
[[ -d $SHARE_MOUNT && ! -L $SHARE_MOUNT && $(realpath -e -- "$SHARE_MOUNT") == "$SHARE_MOUNT" ]] || die 'Share mount must be an existing canonical non-symlink directory'
mountpoint -q -- "$SHARE_MOUNT" || die 'Share directory is not an active mount point'
[[ $(findmnt --noheadings --output FSTYPE --mountpoint "$SHARE_MOUNT") == ext4 ]] || die 'Share mount must be ext4'
findmnt --fstab --mountpoint "$SHARE_MOUNT" >/dev/null || die 'Add the share mount to /etc/fstab first'
if [[ $SHARE_MOUNT != "$DATA_MOUNT" ]]; then
  DATA_SOURCE=$(findmnt --noheadings --output SOURCE --mountpoint "$DATA_MOUNT")
  SHARE_SOURCE=$(findmnt --noheadings --output SOURCE --mountpoint "$SHARE_MOUNT")
  [[ $DATA_SOURCE != "$SHARE_SOURCE" ]] || die 'Separate SSD and HDD mount paths resolve to the same source; review the arrays and /etc/fstab'
fi
ip -4 -o address show scope global | awk '{split($4,a,"/");print a[1]}' | grep -Fxq "$PORTAL_IP" || die 'Portal IPv4 is not assigned to this server'

IMAGE="$DATA_MOUNT/iscsi/steam-games.img"
MOUNT_SOURCE=$(findmnt --noheadings --output SOURCE --mountpoint "$DATA_MOUNT")
if [[ -e $STATE_FILE ]]; then
  [[ -f $STATE_FILE && ! -L $STATE_FILE ]] || die 'Unexpected installer state file type'
  mapfile -t STATE_LINES < "$STATE_FILE"
  [[ ${STATE_LINES[0]:-} == "$DATA_MOUNT" && ${STATE_LINES[1]:-} == "$PORTAL_IP" && ${STATE_LINES[2]:-} == "$TARGET_IQN" ]] || die 'Existing install state has different mount, portal, or IQN; review manually'
  [[ ${STATE_LINES[4]:-$DATA_MOUNT} == "$SHARE_MOUNT" ]] || die 'Existing install uses a different share mount; migrate data manually before changing it'
  INSTALL_STATUS=${STATE_LINES[3]:-in-progress}
  [[ $INSTALL_STATUS == in-progress || $INSTALL_STATUS == complete ]] || die 'Invalid install state'
else
  INSTALL_STATUS=new
fi

if [[ -e $IMAGE ]]; then
  [[ $INSTALL_STATUS != new ]] || die 'Steam image exists without this installer’s state; refusing to adopt it'
  [[ -f $IMAGE && ! -L $IMAGE ]] || die 'Steam image must be a regular non-symlink file'
  [[ $(stat -c %s -- "$IMAGE") == "$SIZE_BYTES" ]] || die 'Existing Steam image is not exactly 256 GiB'
  [[ $(( $(stat -c %b -- "$IMAGE") * 512 )) -ge $SIZE_BYTES ]] || die 'Existing Steam image is sparse or incompletely allocated'
else
  [[ $INSTALL_STATUS != complete ]] || die 'Completed install is missing its Steam image'
  AVAILABLE=$(df -B1 --output=avail "$DATA_MOUNT" | awk 'NR==2 {print $1}')
  [[ $AVAILABLE =~ ^[0-9]+$ && $AVAILABLE -ge $((SIZE_BYTES + RESERVE_BYTES)) ]] || die 'Need at least 356 GiB free for a fully allocated image plus 100 GiB headroom'
fi

note "Data mount: $DATA_MOUNT ($MOUNT_SOURCE, ext4)"
note "SMB shares mount: $SHARE_MOUNT"
note "Portal: $PORTAL_IP:3260   Target: $TARGET_IQN"
note "Image: $IMAGE   State: $INSTALL_STATUS"
note "Free bytes: $(df -B1 --output=avail "$DATA_MOUNT" | awk 'NR==2 {print $1}')"
note 'Storage topology (read only):'
lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS,MODEL
if [[ -r /proc/mdstat ]]; then
  note 'Linux RAID status (read only):'
  cat /proc/mdstat
  if grep -Eq '\[[U_]*_[U_]*\]' /proc/mdstat; then
    die 'A Linux mdraid array appears degraded; resolve it before provisioning'
  fi
fi
if command -v smartctl >/dev/null && [[ $MOUNT_SOURCE == /dev/* ]]; then
  note 'SMART/Smart Array-visible health (advisory; a controller logical drive may hide individual drives):'
  SMART_OUTPUT=$(smartctl -H "$MOUNT_SOURCE" 2>&1 || true)
  printf '%s\n' "$SMART_OUTPUT"
  [[ $SMART_OUTPUT != *'FAILED!'* ]] || die 'SMART reports a failed drive'
else
  note 'Check all member disks and Smart Array logical drives in HPE SSA/iLO before apply.'
fi

if [[ $ACTION == plan ]]; then
  note 'PLAN: install samba, targetcli-fb and smartmontools; configure Projects/Backups/Models shares; allocate 256 GiB; create disabled LIO LUN with zero ACLs.'
  note 'PLAN: review HPE iLO/Smart Array health and confirm the mounted data volume before apply.'
  exit 0
fi

[[ $EUID -eq 0 ]] || die 'Apply and verify require root'
if [[ $ACTION == verify || $INSTALL_STATUS == complete ]]; then
  [[ -s /etc/samba/smb.conf ]] || die 'Samba configuration missing'
  testparm -s /etc/samba/smb.conf >/dev/null
  for share in Projects Backups Models; do
    expected="$SHARE_MOUNT/${share,,}"
    actual=$(testparm -s --section-name="$share" --parameter-name=path /etc/samba/smb.conf 2>/dev/null)
    [[ $actual == "$expected" ]] || die "$share share path missing or wrong: expected $expected, found ${actual:-none}"
  done
  systemctl is-enabled smbd.service >/dev/null
  systemctl is-active smbd.service >/dev/null || die 'Samba is not running'
  systemctl is-enabled rtslib-fb-targetctl.service >/dev/null
  python3 "$SCRIPT_DIR/lio_check.py" --image "$IMAGE" --iqn "$TARGET_IQN" --ip "$PORTAL_IP" --complete --allow-controller-state >/dev/null || die 'Saved LIO storage, portal or LUN configuration differs from the completed install'
  if [[ -e /var/lib/homelab/game-lun-state.json ]]; then
    [[ -x /usr/local/sbin/game-lun-controller.py ]] || die 'Handoff state exists but the controller script is missing'
    python3 /usr/local/sbin/game-lun-controller.py verify || die 'Live handoff controller state is invalid'
  else
    python3 "$SCRIPT_DIR/lio_check.py" --image "$IMAGE" --iqn "$TARGET_IQN" --ip "$PORTAL_IP" --complete >/dev/null || die 'Target is not neutral before handoff controller installation'
  fi
  note 'Service and saved LIO configuration verified. Live target state follows:'
  targetcli "/iscsi/$TARGET_IQN/tpg1" ls
  [[ $ACTION == verify ]] || note 'Already applied. No configuration changed.'
  exit 0
fi

note 'Installing Ubuntu packages...'
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends samba targetcli-fb smartmontools openssh-server
systemctl enable --now ssh.service

install -d -m 0700 /etc/homelab-budget-nas
if [[ ! -e $STATE_FILE ]]; then
  printf '%s\n%s\n%s\nin-progress\n%s\n' "$DATA_MOUNT" "$PORTAL_IP" "$TARGET_IQN" "$SHARE_MOUNT" > "$STATE_FILE"
  chmod 0600 "$STATE_FILE"
fi

if ! getent group homelabshare >/dev/null; then
  groupadd --system homelabshare
fi
if ! getent passwd homelabshare >/dev/null; then
  useradd --system --gid homelabshare --no-create-home --shell /usr/sbin/nologin homelabshare
fi
[[ $(id -gn homelabshare) == homelabshare ]] || die 'Existing homelabshare user has a different primary group'
for folder in projects backups models; do
  install -d -m 2770 -o root -g homelabshare "$SHARE_MOUNT/$folder"
done
install -d -m 0700 -o root -g root "$DATA_MOUNT/iscsi"

SMB_CONF=/etc/samba/smb.conf
SMB_TEMP=$(mktemp)
trap 'rm -f -- "$SMB_TEMP"' EXIT
python3 - "$SMB_CONF" "$SMB_TEMP" "$SHARE_MOUNT" <<'PY'
from pathlib import Path
import re, sys
source, output, mount = map(Path, sys.argv[1:])
original = source.read_text(encoding='utf-8')
begin = '# BEGIN homelab-budget-nas managed shares'
end = '# END homelab-budget-nas managed shares'
block = f'''{begin}
[Projects]
   path = {mount}/projects
   browseable = yes
   read only = no
   valid users = homelabshare
   force group = homelabshare
   create mask = 0660
   directory mask = 2770

[Backups]
   path = {mount}/backups
   browseable = yes
   read only = no
   valid users = homelabshare
   force group = homelabshare
   create mask = 0660
   directory mask = 2770

[Models]
   path = {mount}/models
   browseable = yes
   read only = no
   valid users = homelabshare
   force group = homelabshare
   create mask = 0660
   directory mask = 2770
{end}
'''
if original.count(begin) != original.count(end) or original.count(begin) > 1:
    raise SystemExit('Ambiguous existing managed Samba block')
if begin in original:
    left = original.index(begin)
    right = original.index(end) + len(end)
    updated = original[:left] + block.rstrip() + original[right:]
else:
    if re.search(r'^\s*\[(?:Projects|Backups|Models)\]\s*$', original, re.I | re.M):
        raise SystemExit('Projects, Backups or Models share already exists outside managed block')
    updated = original.rstrip() + '\n\n' + block
output.write_text(updated, encoding='utf-8')
PY
testparm -s "$SMB_TEMP" >/dev/null || die 'Generated Samba configuration failed testparm'
for share in Projects Backups Models; do
  expected="$SHARE_MOUNT/${share,,}"
  actual=$(testparm -s --section-name="$share" --parameter-name=path "$SMB_TEMP" 2>/dev/null)
  [[ $actual == "$expected" ]] || die "Generated $share share path is wrong: expected $expected, found ${actual:-none}"
done
if ! cmp -s "$SMB_TEMP" "$SMB_CONF"; then
  [[ -e "$SMB_CONF.homelab-before" ]] || cp -a "$SMB_CONF" "$SMB_CONF.homelab-before"
  install -m 0644 "$SMB_TEMP" "$SMB_CONF"
fi
install -d -m 0755 /etc/systemd/system/smbd.service.d
printf '[Unit]\nRequiresMountsFor=%s\n' "$SHARE_MOUNT" > /etc/systemd/system/smbd.service.d/homelab-shares.conf
systemctl daemon-reload
systemctl enable --now smbd.service
systemctl restart smbd.service

if [[ ! -e $IMAGE ]]; then
  note 'Allocating 256 GiB Steam image (this can take time)...'
  install -m 0600 /dev/null "$IMAGE"
  fallocate -l "$SIZE_BYTES" "$IMAGE"
  chmod 0600 "$IMAGE"
fi
[[ $(stat -c %s -- "$IMAGE") == "$SIZE_BYTES" ]] || die 'Backing file size differs from 256 GiB'
ALLOCATED=$(( $(stat -c %b -- "$IMAGE") * 512 ))
[[ $ALLOCATED -ge $SIZE_BYTES ]] || die 'Backing file is sparse or incompletely allocated'

# Make target creation fail-closed: no wildcard portal, no auto-enabled TPG,
# and no automatic mapping if the handoff controller later creates one ACL.
targetcli set global auto_add_default_portal=false
targetcli set global auto_enable_tpgt=false
targetcli set global auto_add_mapped_luns=false
targetcli saveconfig >/dev/null

lio_state() {
  targetcli saveconfig >/dev/null || return 1
  python3 "$SCRIPT_DIR/lio_check.py" --image "$IMAGE" --iqn "$TARGET_IQN" --ip "$PORTAL_IP"
}
read_lio_state() {
  local checked_state
  checked_state=$(lio_state) || die 'LIO state check failed; no further target changes made'
  read -r HAS_STORE HAS_TARGET HAS_PORTAL HAS_LUN <<< "$checked_state"
}
read_lio_state
if [[ $HAS_STORE == 0 ]]; then
  targetcli /backstores/fileio create steam_games "$IMAGE" 256G write_back=false
fi
read_lio_state
if [[ $HAS_TARGET == 0 ]]; then
  targetcli /iscsi create "$TARGET_IQN"
fi
targetcli "/iscsi/$TARGET_IQN/tpg1" disable
targetcli "/iscsi/$TARGET_IQN/tpg1" set attribute generate_node_acls=0
targetcli "/iscsi/$TARGET_IQN/tpg1" set attribute authentication=0
read_lio_state
if [[ $HAS_PORTAL == 0 ]]; then
  targetcli "/iscsi/$TARGET_IQN/tpg1/portals" create "$PORTAL_IP" 3260
fi
read_lio_state
if [[ $HAS_LUN == 0 ]]; then
  targetcli "/iscsi/$TARGET_IQN/tpg1/luns" create /backstores/fileio/steam_games 0
fi
targetcli "/iscsi/$TARGET_IQN/tpg1" disable
targetcli saveconfig >/dev/null
python3 "$SCRIPT_DIR/lio_check.py" --image "$IMAGE" --iqn "$TARGET_IQN" --ip "$PORTAL_IP" --complete

# Boot restore must wait for the separately configured ext4 mount.
install -d -m 0755 /etc/systemd/system/rtslib-fb-targetctl.service.d
printf '[Unit]\nRequiresMountsFor=%s\n' "$DATA_MOUNT" > /etc/systemd/system/rtslib-fb-targetctl.service.d/homelab-data.conf
systemctl daemon-reload
systemctl enable rtslib-fb-targetctl.service

printf '%s\n%s\n%s\ncomplete\n%s\n' "$DATA_MOUNT" "$PORTAL_IP" "$TARGET_IQN" "$SHARE_MOUNT" > "$STATE_FILE.tmp"
chmod 0600 "$STATE_FILE.tmp"
mv -f -- "$STATE_FILE.tmp" "$STATE_FILE"
note 'NAS setup complete. The iSCSI target is DISABLED with zero ACLs.'
note 'Set a Samba password with: sudo smbpasswd -a homelabshare'
note 'Install and bootstrap the separate handoff controller before any Windows iSCSI login.'

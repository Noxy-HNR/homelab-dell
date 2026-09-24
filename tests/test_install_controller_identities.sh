#!/usr/bin/env bash
# Offline check that each handoff account can only act on its own host.
# Sources the installer's render functions only; never runs the installer body.
set -Eeuo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=../hp/iscsi/install-controller.sh
source "$ROOT/hp/iscsi/install-controller.sh"
set +e
FAILED=0
fail() { echo "FAIL: $*" >&2; FAILED=1; }

prefix="ALL=(root) NOPASSWD: /usr/bin/python3 /usr/local/sbin/game-lun-controller.py"
for host in desktop laptop; do
  other=$([[ $host == desktop ]] && echo laptop || echo desktop)
  expected="gamehandoff-$host $prefix status
gamehandoff-$host $prefix claim --host $host
gamehandoff-$host $prefix release --host $host --clean-client-offline"
  actual=$(render_sudoers "$host")
  [[ $actual == "$expected" ]] || fail "$host sudoers mismatch:"$'\n'"$actual"
  ! grep -q -- "$other" <<<"$actual" || fail "$host sudoers mentions $other"
  [[ $(account_for "$host") == "gamehandoff-$host" ]] || fail "$host account name"
  if command -v visudo >/dev/null 2>&1; then
    visudo -cqf <(printf '%s\n' "$actual") >/dev/null || fail "$host sudoers rejected by visudo"
  fi
done

render_sudoers other >/dev/null 2>&1 && fail 'unknown host accepted'
account_for 'desktop x' >/dev/null 2>&1 && fail 'host with space accepted'

KEY=$(mktemp)
trap 'rm -f -- "$KEY"' EXIT
printf 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExample desktop\r\n' > "$KEY"
[[ $(render_keys "$KEY") == 'restrict ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExample desktop' ]] || fail 'authorized_keys line'

! grep -Eq '^gamehandoff ' "$ROOT/hp/iscsi/install-controller.sh" || fail 'shared gamehandoff sudoers rule still present'

[[ $FAILED -eq 0 ]] && echo 'PASS: per-host handoff identities' || exit 1

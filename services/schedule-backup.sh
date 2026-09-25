#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID -eq 0 && $# -ge 1 && $# -le 2 ]] || { echo 'Usage: sudo bash services/schedule-backup.sh PLAN_ID [daily|weekly]'; exit 2; }
plan=$1 cadence=${2:-daily}
[[ $plan =~ ^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$ ]] || { echo 'Invalid plan ID'; exit 2; }
[[ $cadence == daily || $cadence == weekly ]] || { echo 'Cadence must be daily or weekly'; exit 2; }
[[ -f /etc/homelab/app-config/backups/$plan.json ]] || { echo 'Configure and test this backup plan first'; exit 2; }
[[ -f /opt/homelab/apps/homelab/backup_cli.py ]] || { echo 'Update the application before scheduling'; exit 2; }
unit="homelab-backup-$plan"
cat > "/etc/systemd/system/$unit.service" <<EOF
[Unit]
Description=Scheduled homelab backup ($plan)
After=homelab-app.service network-online.target
Requires=homelab-app.service
[Service]
Type=oneshot
User=homelab
Group=homelab
WorkingDirectory=/opt/homelab/apps
ExecStart=/opt/homelab/apps/venv/bin/python -m homelab.backup_cli --plan $plan
TimeoutStartSec=25h
NoNewPrivileges=true
ProtectSystem=full
EOF
cat > "/etc/systemd/system/$unit.timer" <<EOF
[Unit]
Description=Run $plan backup $cadence
[Timer]
OnCalendar=$cadence
Persistent=true
RandomizedDelaySec=15m
[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now "$unit.timer"
systemctl list-timers "$unit.timer" --no-pager
printf 'Schedule installed. Disable with: sudo systemctl disable --now %s.timer\n' "$unit"

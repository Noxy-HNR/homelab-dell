# Server measurements and backups

The dashboard shows real measurements from each server: CPU use, memory, disk space, network byte counters, link speed when the operating system reports it, and disk activity. It keeps up to one hour of five-second samples in the server state directory. A service restart keeps that history. This is host information; it does not claim to show Cisco switch counters.

The backup page can run backups, list saved snapshots, check all stored data, and restore a selected snapshot into a new folder. Jobs have a time limit, a Cancel button, and a saved log. A restart marks an unfinished task **interrupted**. A green result means restic completed that operation; a server with no plan says **no backup plan**.

## Pick a backup destination

- Use an existing separate mounted filesystem, such as a USB backup disk, **or** an SFTP directory on another server.
- Choose source folders containing things you cannot download again: projects, configuration exports, notes, and saves.
- The `homelab` account needs read permission on those source folders. Do not give it permission to all of `/root` or your whole home directory merely to bypass a failed backup.
- The script does not erase, partition, format, or mount a drive.
- A copy on the same RAID0 array does not protect you if that array fails. The local helper refuses a destination on the source filesystem. For SFTP, choose storage on the other server or an independent backup system.

## Easiest local setup

This example assumes an existing separate backup filesystem is mounted at `/mnt/backup`. Dell project sources live in `/srv/homelab-projects`. Change the mount only if your disk is mounted elsewhere.

```bash
cd ~/homelab-stack
sudo bash services/configure-backup.sh \
  --id projects \
  --source /srv/homelab-projects \
  --mount /mnt/backup \
  --repository /mnt/backup/dell-projects
```

The helper generates a password, initializes the encrypted repository as the `homelab` service account, and creates a protected plan. It refuses to replace an existing plan or repository. It prints the password file location without printing the password. Store a protected copy of that file away from this server; losing it prevents restores.

Open the dashboard, choose **Backups**, select **projects**, and click **Back up now**. When it finishes, click **Check**. Then select a snapshot and click **Restore a copy**. Restore writes to a newly created folder under `/var/lib/homelab-app/restores/`; it never writes over the source files. Open the task to find its exact restore folder and inspect the restored files before treating the backup as proven.

To connect an existing repository, add `--existing --password-file /absolute/path/to/its-password`. That password file must be root-owned, readable by group `homelab`, and mode `0640`. The repository must be readable and writable by `homelab`.

## Back up to the other server over SSH

This needs one manual SSH setup because only you can confirm the destination and its host identity. The example backs up Dell projects to the HP. Replace `HP_IP` with its address.

On the **HP**, create a separate destination account and directory:

```bash
sudo adduser --disabled-password --gecos '' backup
sudo install -d -m 700 -o backup -g backup /home/backup/.ssh
sudo install -d -m 700 -o backup -g backup /srv/bulk/backups/dell
```

On the **Dell**, make a key used only for backups:

```bash
sudo install -d -m 750 -o root -g homelab /etc/homelab/app-config/secrets
sudo ssh-keygen -t ed25519 -N '' -f /etc/homelab/app-config/secrets/backup_ed25519
sudo chown root:homelab /etc/homelab/app-config/secrets/backup_ed25519
sudo chmod 640 /etc/homelab/app-config/secrets/backup_ed25519
sudo cat /etc/homelab/app-config/secrets/backup_ed25519.pub
```

Copy that one public-key line into `/home/backup/.ssh/authorized_keys` on the HP, using `sudo nano`. Prefix it with `restrict,command="internal-sftp" ` (including the trailing space) so the key can use SFTP without a normal shell. Then set its owner and permissions:

```bash
sudo chown backup:backup /home/backup/.ssh/authorized_keys
sudo chmod 600 /home/backup/.ssh/authorized_keys
sudo ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

The last command prints the HP host fingerprint. Keep it visible. On the Dell, fetch the public host key and compare the fingerprint before trusting it:

```bash
ssh-keyscan -t ed25519 HP_IP > /tmp/hp-backup-known-hosts
ssh-keygen -lf /tmp/hp-backup-known-hosts
```

Only after the fingerprints match, install the host key and test the connection:

```bash
sudo install -m 640 -o root -g homelab /tmp/hp-backup-known-hosts /etc/homelab/app-config/secrets/known_hosts
sudo -u homelab sftp \
  -i /etc/homelab/app-config/secrets/backup_ed25519 \
  -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile=/etc/homelab/app-config/secrets/known_hosts \
  backup@HP_IP
```

At the `sftp>` prompt, run `ls /srv/bulk/backups/dell`, then `exit`. Create the plan on the Dell:

```bash
cd ~/homelab-stack
sudo bash services/configure-backup.sh \
  --id projects-to-hp \
  --source /srv/homelab-projects \
  --repository sftp:backup@HP_IP:/srv/bulk/backups/dell \
  --ssh-key /etc/homelab/app-config/secrets/backup_ed25519 \
  --known-hosts /etc/homelab/app-config/secrets/known_hosts
```

The default task limit is six hours. Use `--timeout` to choose 60–86400 seconds when creating a plan. Only a local administrator can edit plans under `/etc/homelab/app-config/backups`; the web API cannot change the backup destination or submit commands. Run backups while source applications are quiet when their files need a consistent point-in-time copy. For active databases, export a database dump first and include the dump directory.

## Schedules and retention

No schedule is enabled automatically. After a successful backup, Check, and test restore, install a daily or weekly timer with the included helper:

```bash
sudo bash services/schedule-backup.sh projects daily
sudo systemctl list-timers 'homelab-backup-*'
```

Substitute your actual plan ID. The timer calls the authenticated local app as the `homelab` account and records failures in `journalctl -u homelab-backup-projects.service`. It starts a missed run after boot. Disable it with `sudo systemctl disable --now homelab-backup-projects.timer`. Retention and pruning remain explicit administrator tasks so the web app cannot erase recovery history.

## API reference

All routes are under `/api` and require the normal login. Both servers expose them.

| Route | Result |
| --- | --- |
| `GET /metrics` | `{host, current, history}`. `current` has `cpu_percent`, `memory`, `load_average`, `disks`, `network` (cumulative counters, `is_up`, `speed_mbps` or null), `disk_io`. `history` holds up to 720 samples. |
| `GET /backups` | `{available, plans, tasks}`. `available` is false when restic is missing. An empty `plans` list means **no backup plan**. Each plan has `configured`, `error`, and `last_backup` (`null` until a backup task finishes, then its `id`, `state`, `finished_at`). Task summaries omit logs. |
| `GET /backups/{plan_id}/snapshots` | `{items: [{id, short_id, time, hostname, paths}]}` |
| `POST /backups/run`, `POST /backups/check` | Body `{plan_id}`. Returns the new task with status 202. |
| `POST /backups/restore` | Body `{plan_id, snapshot_id}`. `snapshot_id` must be 8–64 hex characters; `latest` is not accepted. `restore_path` is a new folder under `state_dir/restores`. |
| `GET /backups/tasks/{id}` | Task with `state` (`queued`, `running`, `succeeded`, `failed`, `cancelled`, `interrupted`), `exit_code`, `error`, `restore_path`, and the last 64 KiB of `log`. |
| `POST /backups/tasks/{id}/cancel` | Requests cancellation and returns the task. |

Errors: 400 for an invalid or unconfigured plan, 404 for an unknown task, 409 when another backup operation is running (one at a time per server), 504 when listing snapshots takes over 30 seconds. The password is passed to restic only as `RESTIC_PASSWORD_FILE`; it never appears in commands, logs, or responses. Inherited `RESTIC_*` variables are removed.

A task still running when the service stops is marked **interrupted**, never cancelled or succeeded. After an interrupted or cancelled job, restic may leave a stale lock. Check that no job is running, then run `sudo -u homelab restic --repo <repository> --password-file <password file> unlock` once.

SFTP plans require the user and host to start with a letter or digit, and key and known-hosts paths may contain only letters, digits, and `_ . / @ + = , -` (no spaces or quotes).

Tests: `PYTHONPATH=services .venv/Scripts/python.exe -m pytest services/tests/test_operations.py` (on Linux use `.venv/bin/python`). They use temporary folders and a fake restic script. They never touch real backups.

## Optional Cisco port counters

Host metrics work immediately after installation. Cisco port counters require a separately configured SNMP exporter and read-only SNMP credentials on the real switch. We do not enable guessed SNMP settings. The supported path is [Prometheus SNMP exporter](https://github.com/prometheus/snmp_exporter) using SNMPv3 authentication and privacy, limited to the metrics host. Until configured, the app has no Cisco traffic figures. Verify port IDs against the actual switch interface list before labeling charts.

Restic's [repository setup guide](https://restic.readthedocs.io/en/stable/030_preparing_a_new_repo.html) describes local and SFTP repositories. Its [restore guide](https://restic.readthedocs.io/en/stable/050_restore.html) explains restoring selected snapshots.

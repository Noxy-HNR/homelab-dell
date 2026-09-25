#!/usr/bin/env bash
# Create a protected restic plan. This script never formats or mounts a disk.
set -euo pipefail
if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo 'Run this helper with sudo after installing the homelab app.' >&2
  exit 1
fi
exec python3 - "$@" <<'PY'
import argparse
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import shlex
import shutil
import stat
import subprocess
import sys

p = argparse.ArgumentParser(description="Create an encrypted restic repository and a homelab backup plan. Never formats disks.")
p.add_argument('--id', required=True, help='Short name such as dell-projects')
p.add_argument('--name', default='')
p.add_argument('--repository', required=True, help='/mnt/backup/dell or sftp:backup@hp:/srv/backups/dell')
p.add_argument('--mount', help='Mount point for a separate local backup filesystem')
p.add_argument('--source', action='append', required=True, help='Absolute source directory; repeat to add more')
p.add_argument('--exclude', action='append', default=[])
p.add_argument('--ssh-key', help='Existing private key for SFTP, readable only by root and the app account')
p.add_argument('--known-hosts', help='Existing verified SSH host keys file')
p.add_argument('--password-file', help='Existing repository password file; omit to generate a new password')
p.add_argument('--existing', action='store_true', help='Verify an existing repository instead of initializing a new one')
p.add_argument('--config', default='/etc/homelab/apps.json', help='Application configuration file')
p.add_argument('--timeout', type=int, default=21600, help='Maximum job seconds (default six hours)')
a = p.parse_args()

def fail(message):
    raise SystemExit(message)

def private_file(path, *, root_owned=False):
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        fail(f'Required absolute regular file is missing: {path}')
    info = path.stat()
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH | stat.S_IROTH):
        fail(f'Use protected file permissions (0600 or 0640): {path}')
    if root_owned and info.st_uid != 0:
        fail(f'File must be owned by root: {path}')

if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}', a.id):
    fail('Use letters, numbers, underscores, and hyphens for --id.')
if not 60 <= a.timeout <= 86400:
    fail('--timeout must be between 60 seconds and one day.')
restic = shutil.which('restic')
if not restic:
    fail('restic is missing. Run the server application installer first.')
try:
    account = pwd.getpwnam('homelab')
except KeyError:
    fail('The homelab service account is missing. Run the server application installer first.')
config = json.loads(Path(a.config).read_text())
config_dir = Path(config['config_dir'])
state_dir = Path(config['state_dir'])
if not config_dir.is_absolute() or not state_dir.is_absolute():
    fail('Application config_dir and state_dir must be absolute.')
plan_dir = config_dir / 'backups'
secrets_dir = config_dir / 'secrets'
for directory in (config_dir, plan_dir, secrets_dir):
    if directory.is_symlink():
        fail(f'Refusing a symlink configuration directory: {directory}')
    directory.mkdir(parents=True, exist_ok=True)
    os.chown(directory, 0, account.pw_gid)
    directory.chmod(0o750)
plan_path = plan_dir / f'{a.id}.json'
if plan_path.exists() or plan_path.is_symlink():
    fail(f'Plan already exists: {plan_path}. Edit it deliberately with sudo, or choose a new --id.')
sources = []
for item in a.source:
    path = Path(item)
    if not path.is_absolute() or not path.is_dir():
        fail(f'Source must be an existing absolute directory: {item}')
    sources.append(str(path.resolve()))

plan = dict(name=a.name or a.id, repository=a.repository, sources=sources,
            excludes=a.exclude, timeout_seconds=a.timeout)
command = [restic, '--repo', a.repository, '--no-cache']
if a.repository.startswith('sftp:'):
    # Must match homelab/operations.py: user and host cannot start with '-', no quoting characters.
    if not re.fullmatch(r'''sftp:[A-Za-z0-9][A-Za-z0-9_.-]*@[A-Za-z0-9][A-Za-z0-9_.-]*:/[^\s\x00'"\\]+''', a.repository):
        fail('Use sftp:backup@server:/absolute/directory for the SFTP repository.')
    if not a.ssh_key or not a.known_hosts:
        fail('SFTP requires --ssh-key and --known-hosts. See docs/features/OPERATIONS.md for the one-time SSH setup.')
    for item in (a.ssh_key, a.known_hosts):
        if not re.fullmatch(r'/[A-Za-z0-9_./@+=,-]+', item):
            fail(f'Use an absolute path without spaces or quotes: {item}')
    key = Path(a.ssh_key)
    known_hosts = Path(a.known_hosts)
    private_file(key)
    private_file(known_hosts)
    plan.update(ssh_key=str(key), known_hosts=str(known_hosts))
    destination = a.repository[5:].split(':', 1)[0]
    ssh = ['ssh', '-i', str(key), '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
           '-o', 'StrictHostKeyChecking=yes', '-o', f'UserKnownHostsFile={known_hosts}', '-s', destination, 'sftp']
    command += ['-o', 'sftp.command=' + shlex.join(ssh)]
    created_repository = None
else:
    if not a.mount:
        fail('Local backups require --mount for a separate mounted backup filesystem.')
    mount, repository = Path(a.mount), Path(a.repository)
    if not mount.is_absolute() or not os.path.ismount(mount):
        fail('The backup mount is not mounted. Mount your existing backup filesystem first.')
    if not repository.is_absolute() or repository.is_symlink():
        fail('Repository must be an absolute directory without a symlink.')
    if not repository.resolve().is_relative_to(mount.resolve()) or repository.resolve() == mount.resolve():
        fail('Repository must be a subdirectory of --mount.')
    if any(Path(source).stat().st_dev == mount.stat().st_dev for source in sources):
        fail('Backup destination must be on a separate filesystem from every source.')
    if any(repository.resolve().is_relative_to(Path(source)) for source in sources):
        fail('The backup repository cannot be inside a source directory.')
    if a.existing and not a.password_file:
        fail('--existing requires --password-file containing the existing repository password.')
    if a.existing and not repository.is_dir():
        fail('--existing was given but the repository directory does not exist.')
    if repository.exists() and not a.existing:
        fail('Repository directory already exists. For an existing restic repository use --existing and --password-file.')
    created_repository = None if repository.exists() else repository
    repository.mkdir(parents=True, exist_ok=True)
    os.chown(repository, account.pw_uid, account.pw_gid)
    repository.chmod(0o700)
    plan['repository_mount'] = str(mount.resolve())

if a.existing and not a.password_file:
    fail('--existing requires --password-file containing the existing repository password.')
if a.password_file:
    password = Path(a.password_file)
    private_file(password, root_owned=True)
else:
    password = secrets_dir / f'{a.id}.password'
    try:
        descriptor = os.open(password, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    except FileExistsError:
        fail(f'Password already exists: {password}. Reuse it explicitly with --password-file, or choose a new --id.')
    with os.fdopen(descriptor, 'w') as stream:
        stream.write(secrets.token_urlsafe(48) + '\n')
    os.chown(password, 0, account.pw_gid)
    password.chmod(0o640)
plan['password_file'] = str(password)
environment = {key: value for key, value in os.environ.items() if not key.startswith('RESTIC_')}
environment['RESTIC_PASSWORD_FILE'] = str(password)
# runuser drops privilege: the app must actually be able to read credentials and write the repository.
operation = ['snapshots', '--json'] if a.existing else ['init']
try:
    result = subprocess.run(['runuser', '-u', 'homelab', '--', *command, *operation], env=environment,
                            capture_output=True, text=True, timeout=120)
    returncode, stderr = result.returncode, result.stderr
except subprocess.TimeoutExpired:
    returncode, stderr = 1, 'restic did not finish within 120 seconds (check the destination and SSH host key).'
if returncode:
    print(stderr[-4000:], file=sys.stderr)
    if created_repository is not None:
        try:
            created_repository.rmdir()  # Only removes the directory this run created if restic left it empty.
        except OSError:
            print(f'Left {created_repository} in place because it is not empty; inspect it before retrying.', file=sys.stderr)
    fail(f'Repository verification failed; no plan was activated. Password retained at {password}. Check permissions and SSH, then retry with --password-file {password}.')
descriptor = os.open(plan_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
with os.fdopen(descriptor, 'w') as stream:
    json.dump(plan, stream, indent=2)
    stream.write('\n')
os.chown(plan_path, 0, account.pw_gid)
plan_path.chmod(0o640)
print(f'Backup plan ready: {a.id}')
print(f'Plan: {plan_path}')
print(f'Password file: {password}')
print('Keep a protected copy of that password outside this server; the repository cannot be restored without it.')
print('Open the dashboard Backups page. Run a backup, then Check, then restore a snapshot to test it.')
PY

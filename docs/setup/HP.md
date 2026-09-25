# HP setup

The HP is your always-on storage server. This page installs its file shares, the Steam drive target, and the web app on port **8088**.

**Before you start** you must have finished Parts 1 and 2 of the [start guide](../START-HERE.md):

- Ubuntu Server 24.04 is installed on the HP.
- `/srv/homelab` and `/srv/bulk` are mounted (`findmnt /srv/homelab` and `findmnt /srv/bulk` each print a line).
- The HP's 10G address is set on its network card.
- This repo is cloned on the HP (`cd homelab-stack`).

Below, **HP_IP** is the HP's 10G address. **DELL_IP** is the Dell's address. Use your real numbers, for example `192.168.1.10` and `192.168.1.20`.

![Initial HP and Dell drive layouts](../assets/drives.svg)

## Steps

### 1. Look before you install

```bash
sudo bash hp/deploy.sh plan HP_IP DELL_IP
```

This changes nothing. It shows your storage and what the install will do. Check that the drive sizes match what you built.

**You should see:** a list of the planned changes and `PASS: HP <name> at 10 Gb/s (HP_IP)`, then `Plan complete.`

If it does not say `10 Gb/s`, stop. Check the network card, the optic, the fiber and the switch port.

### 2. Install

```bash
sudo bash hp/deploy.sh install HP_IP DELL_IP
```

It takes several minutes. It will:

1. Check the 10G link.
2. Install the file sharing and the Steam drive target.
3. Ask you to set the password for the `homelabshare` user. **Type it twice.** You will use it on Windows to open `\\HP_IP\Models`.
4. Install Cockpit (system controls) and the HP web app.
5. Run its own checks.

The web app is installed with `services/install.sh`, which the script calls for you.

**You should see, near the end:**

```text
{"ok":true,"role":"hp"}

Open http://HP_IP:8088/
Login token (save privately):
<a long string of letters and numbers>

The app is installed. Follow the guide to configure the Windows control app and a backup destination.
```

The token line scrolls up as the last checks run. If you missed it, get it again in step 4.

### 3. Verify

```bash
sudo bash hp/deploy.sh verify HP_IP DELL_IP
```

**You should see:** `PASS: HP NAS, 10G link and dashboard`.

### 4. Get your login token

```bash
sudo cat /etc/homelab/app-token
```

Copy it into a password manager or a private note. Do not paste it into chat, email or Git.

### 5. Open the web app

On any PC on the network, open `http://HP_IP:8088/`.

**You should see:** a page titled **Sign in to this server** with one box, *Server login token*. Paste the token and click **Open workspace**.

**Then you should see:** a menu on the left with **Overview**, **Model library** and **Backups**, and a badge that says `HP`. The overview shows CPU, memory, storage and network links. Each network card should show its link speed, for example `10 Gb/s` for the HP's 10G card.

The login lasts eight hours. Click **Sign out** when you are done on a shared PC.

### 6. Check the other pages

- **Cockpit:** `https://HP_IP:9090/`. Your browser will warn about the certificate. That is normal for a home server. Sign in with your Ubuntu user.
- **Model share:** on the laptop, open `\\HP_IP\Models` in Explorer. Sign in as `homelabshare`.

Leave the Steam drive for [Part 6](../START-HERE.md#part-6-set-up-the-steam-drive-then-check-everything).

## What the installer did and did not do

- It **did** create a `homelab` service account with no login shell, a login token at `/etc/homelab/app-token`, and a settings file at `/etc/homelab/apps.json`.
- It **did** start the app as a service named `homelab-app`. It restarts on failure and after a reboot.
- It **did not** create RAID arrays, partition or format any disk, or set any IP address.
- The old landing page service, `homelab-dashboard`, is turned off. The new app replaces it.

## Update later

```bash
cd ~/homelab-stack
git pull --ff-only
sudo bash services/install.sh hp HP_IP DELL_IP
```

Your token and settings are kept. The old app is copied to `/opt/homelab/apps/homelab.previous.<date>` first.

## If something goes wrong

| You see | What it means | What to do |
| --- | --- | --- |
| `HP address ... must belong to exactly one local interface` | The address you typed is not on this server, or is on two cards. | Run `ip -4 address`. Use the address on the 10G card. |
| `NIC ... is not reporting a 10 Gb/s link` | The link is slower than 10G. | Check the card, the optic, the fiber and the switch port. |
| `Finish HP storage and NAS setup first` | `/srv/bulk` or `/srv/homelab` is not mounted. | Fix the mounts in [INSTALL.md step 4](../INSTALL.md#4-install-ubuntu-on-both-servers). |
| `The Models share is missing` | The folder `/srv/bulk/models` does not exist. | Run `sudo bash hp/deploy.sh install HP_IP DELL_IP` again. It creates the shares. |
| `Ubuntu 24.04 is required` | Wrong Ubuntu version. | Install Ubuntu Server 24.04. |
| `... is not assigned to this server` | You typed an address the HP does not have. | Check the address with `ip -4 address`. |
| `Existing apps.json differs at ...` | The saved settings use a different address or path. | Read `/etc/homelab/apps.json`. Use the same addresses as before, or ask for help before you change it. |
| The final health check fails | The app did not start. | The installer prints the last log lines. Also run `sudo journalctl -u homelab-app -n 60 --no-pager`. |
| The page loads but the token is refused | You used the Dell's token. | Each server has its own. Run `sudo cat /etc/homelab/app-token` **on the HP**. |

Back to the [start guide](../START-HERE.md).

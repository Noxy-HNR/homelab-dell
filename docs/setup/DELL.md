# Dell setup

The Dell is your development workbench. This page installs Docker, developer tools, Cockpit, Portainer, the workbench web app on port **8090**, and the MCP tools for your AI assistant.

**Before you start** you must have finished Parts 1 and 2 of the [start guide](../START-HERE.md):

- Ubuntu Server 24.04 is installed on the Dell's two-drive virtual disk.
- The four onboard network ports are joined in one bond called `bond0`. Follow the [Dell network guide](../DELL-NETWORK.md). The Cisco switch must have the matching port-channel.
- `bond0` has the Dell's fixed address.
- This repo is cloned on the Dell (`cd homelab-stack`).
- The HP is set up first, so you know its address.

Below, **DELL_IP** is the address on `bond0`. **HP_IP** is the HP's address. Use your real numbers, for example `192.168.1.20` and `192.168.1.10`.

## Steps

### 1. Look before you install

```bash
sudo bash dell/deploy.sh plan DELL_IP HP_IP
```

This changes nothing. It checks the system and the bond.

**You should see:** `PASS: Dell 4 x 1 Gb/s LACP bond at DELL_IP`, then `Plan complete.`

If the bond check fails, stop. Fix the bond first. The installer will not go on without it.

### 2. Install

```bash
sudo bash dell/deploy.sh install DELL_IP HP_IP
```

It takes several minutes. It will:

1. Check the bond again.
2. Install base tools, Docker and developer tools.
3. Install Cockpit and Portainer.
4. Install the workbench web app and the MCP tools. The old MCP demo is turned off and replaced.
5. Run its own checks.

**You should see, near the end:**

```text
{"ok":true,"role":"dell"}

Open http://DELL_IP:8090/
Login token (save privately):
<a long string of letters and numbers>

The app is installed. Follow the guide to configure the Windows control app and a backup destination.
```

The last line of the whole run should be:

```text
PASS: Dell base, Docker, dev tools, workbench, MCP tools, Cockpit and Portainer
```

### 3. Verify

```bash
sudo bash dell/deploy.sh verify DELL_IP HP_IP
```

**You should see:** `PASS: Dell 4 x 1 Gb/s LACP bond at DELL_IP` and then the same final `PASS` line as above.

### 4. Get your login token

```bash
sudo cat /etc/homelab/app-token
```

This is the **Dell** token. It is different from the HP token. Keep it private. You will use it for the web app and for the MCP connection.

### 5. Open the workbench

On any PC on the network, open `http://DELL_IP:8090/`.

**You should see:** a page titled **Sign in to this server**. Paste the Dell token and click **Open workspace**.

**Then you should see:** a menu with **Overview**, **Projects & jobs**, **Search files** and **Backups**, and a badge that says `DELL`. The overview shows live CPU, memory, disks and network links. The `bond0` members should each show `1 Gb/s`.

### 6. Check the other pages

- **Cockpit:** `https://DELL_IP:9090/`. Sign in with your Ubuntu user.
- **Portainer:** `https://DELL_IP:9443/`. It asks you to create an admin account on first use. Your browser will warn about the certificate. That is normal on a home network.

## Connect an AI assistant with MCP

The MCP server runs on the Dell at `127.0.0.1:8765`. That address is **only inside the Dell**. It is never opened to the network. You reach it from your PC through an SSH tunnel. It also needs the Dell token every time.

1. On your PC, open PowerShell and start the tunnel. Replace the user name and address:

   ```powershell
   ssh -N -L 8765:127.0.0.1:8765 YOUR_DELL_USER@DELL_IP
   ```

   Enter your Ubuntu password. The window then looks stuck. **That is correct.** Keep it open while you use MCP.

2. In a second PowerShell window, check the door is locked without a token:

   ```powershell
   curl.exe -i http://127.0.0.1:8765/mcp
   ```

   **You should see:** `HTTP/1.1 401` and `A valid server bearer token is required.`

3. In your MCP client, add a server with the URL `http://127.0.0.1:8765/mcp` and this header, using the **Dell** token:

   ```text
   Authorization: Bearer YOUR_DELL_TOKEN
   ```

**Tools it offers:** list projects, search project text, read a project file, list jobs, start a job, run a project's tests, compile a project, get a job, cancel a job, and rebuild the search index.

**What it cannot do:** it has no shell, and it cannot read outside your registered projects. Jobs run only the named presets (`test` and `compile`). Job output and project text are data, not instructions. Run only projects you trust, because a test job runs that project's code.

## What the installer did and did not do

- It **did** create a `homelab` service account with no login shell. The app runs as that account, outside Docker, so it cannot touch the Docker socket.
- It **did** put your projects in `/srv/homelab-projects` and app data in `/var/lib/homelab-app`.
- It **did** start two services, `homelab-app` and `homelab-mcp`. They restart after a reboot.
- It **did not** partition or format any disk, or change the bond or any IP address.

## Update later

```bash
cd ~/homelab-stack
git pull --ff-only
sudo bash services/install.sh dell DELL_IP HP_IP
```

Your token, settings and projects are kept. The old app is copied to `/opt/homelab/apps/homelab.previous.<date>` first.

## If something goes wrong

| You see | What it means | What to do |
| --- | --- | --- |
| `bond0 is missing` | The bond does not exist. | Follow the [Dell network guide](../DELL-NETWORK.md) from the local console. |
| `bond0 is not in 802.3ad LACP mode` | The bond is set up the wrong way. | Fix it as in the network guide. |
| `Expected four 1 Gb/s bond members` | A cable is loose, a port is down, or a port runs at the wrong speed. | Check all four cables and the Cisco port-channel. |
| `Dell address ... is not assigned to bond0` | You typed the wrong address. | Run `ip -4 address show bond0`. |
| `Copy the whole setup directory to the Dell.` | The clone is incomplete. | Clone the full repo again, or run `git status`. |
| `Existing apps.json differs at ...` | Saved settings use a different address or path. | Read `/etc/homelab/apps.json`. Use the same addresses as before. |
| The final health check fails | The app did not start. | Run `sudo journalctl -u homelab-app -n 60 --no-pager`. |
| The token is refused | You used the HP token. | Run `sudo cat /etc/homelab/app-token` **on the Dell**. |
| MCP says `401` | Wrong or missing token. | Use the Dell token in the `Authorization: Bearer` header. |
| The tunnel command says connection refused | The MCP service is not running. | On the Dell run `sudo systemctl status homelab-mcp`. |

Back to the [start guide](../START-HERE.md).

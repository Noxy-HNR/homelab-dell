# Windows setup

This page sets up the **desktop** and the **laptop**. Do each step on the PC named in it.

> **The Windows control app is new and has only been tested offline.** It has not been tried on your real PCs yet. If `client/Install-Control-Center.ps1` is missing from your clone, run `git pull --ff-only` and look again.

**Before you start** you must have finished Parts 3 and 4 of the [start guide](../START-HERE.md). The HP and Dell must be running. You need both addresses and both login tokens.

Below, **HP_IP** and **DELL_IP** are the server addresses. **LAPTOP_IP** and **DESKTOP_IP** are the 10G addresses of the PCs.

## Steps

### 1. Check the 10G link (both PCs)

Plug the adapter in. Open PowerShell and run:

```powershell
Get-NetAdapter | Format-Table Name, Status, LinkSpeed
```

**You should see:** one adapter with Status `Up` and LinkSpeed `10 Gbps`.

If it says `1 Gbps`, stop. Check the cable and the switch port. If the adapter is missing, install its driver. The [Intel Windows 11 driver page](https://www.intel.com/content/www/us/en/download/727998/intel-network-adapter-driver-for-microsoft-windows-11.html) is a starting point for a genuine Intel card.

### 2. Get this repo (both PCs)

Skip this if you already did [INSTALL.md step 6](../INSTALL.md#6-clone-this-repo-on-each-machine). Otherwise:

```powershell
winget install --id Git.Git --exact
winget install --id GitHub.cli --exact
gh auth login
gh repo clone Noxy-HNR/homelab-desktop homelab-stack
Set-Location .\homelab-stack
```

On the laptop replace `homelab-desktop` with `homelab-laptop`. Close and reopen PowerShell if it cannot find `git` or `gh`.

### 3. Install the PC tools

**Desktop.** Open **PowerShell as administrator** in the repo folder:

```powershell
powershell -ExecutionPolicy Bypass -File .\desktop\install.ps1 -Action Plan
powershell -ExecutionPolicy Bypass -File .\desktop\install.ps1 -Action Install -HpIp HP_IP -DellIp DELL_IP
```

Restart or finish the first WSL, Docker Desktop and Ollama launch if it asks. Then:

```powershell
powershell -ExecutionPolicy Bypass -File .\desktop\install.ps1 -Action PullModel
powershell -ExecutionPolicy Bypass -File .\desktop\install.ps1 -Action Verify
```

**Laptop.** Open PowerShell in the repo folder:

```powershell
powershell -ExecutionPolicy Bypass -File .\laptop\install.ps1 -Action Plan
powershell -ExecutionPolicy Bypass -File .\laptop\install.ps1 -Action InstallTools -HpIp HP_IP -DellIp DELL_IP
powershell -ExecutionPolicy Bypass -File .\laptop\install.ps1 -Action Verify
```

**You should see:** each `Verify` finishes without an error, and a **Homelab Control Center** shortcut appears on the desktop. `Plan` changes nothing. Your existing laptop AI setup is kept.

### 4. Open the model share (laptop)

In File Explorer, go to `\\HP_IP\Models`. Sign in as `homelabshare` with the password you set on the HP.

**You should see:** the Models folder. It may be empty until you download a model in [Daily use](../DAILY-USE.md#download-a-model).

### 5. Prepare the Steam settings (both PCs)

The control app's Steam buttons use a small local settings file. You make that file in [INSTALL.md step 10](../INSTALL.md#10-set-up-the-exclusive-steam-drive) with `Prepare-Client.ps1`. It is saved as `client\game-drive\game-client.json` and is never sent to Git.

Finish that before you use the control app's Steam buttons. The [game drive guide](../GAME-DRIVE.md) explains the whole handoff.

### 6. Install the control app (both PCs)

If you passed `-HpIp` and `-DellIp` to the machine installer in step 3, it already created the shortcut. Otherwise run this **once on each PC**. Use `desktop` or `laptop` as the role.

```powershell
powershell -ExecutionPolicy Bypass -File ./client/Install-Control-Center.ps1 -Role laptop -HpIp HP_IP -DellIp DELL_IP
```

On the desktop, use `-Role desktop`.

**You should see:**

```text
Installed the Homelab Control Center desktop shortcut.
Settings: C:\Users\YOU\AppData\Local\Homelab\client-settings.json
Keep this repository folder in place. The app asks for the HP token when you refresh models; it does not save that token.
```

A **Homelab Control Center** shortcut is now on your desktop. Keep the repo folder where it is. The shortcut points into it.

The installer stores only the server addresses, not any token. Running it again keeps a dated copy of the old settings file.

### 7. Open the control app and check it

1. Double-click **Homelab Control Center** on the desktop. A window titled `Homelab - laptop` (or `desktop`) opens.
2. Click **Refresh model list**.
3. A box titled **HP login token** asks you to paste the HP token. Paste it and click **Connect**.

**You should see:** your GGUF models listed with their sizes. If the list is empty, you have not downloaded a model yet. See [Daily use](../DAILY-USE.md#download-a-model).

The app keeps the token in memory only until you close it. Use **Change HP token** to enter a different one. To show the token again, run `sudo cat /etc/homelab/app-token` on the HP.

### What the control app does

| Button | What it does |
| --- | --- |
| **HP model library** | Opens the HP web page. |
| **Dell projects and jobs** | Opens the Dell web page. |
| **Open Models share** | Opens `\\HP_IP\Models` in Explorer. |
| **Setup guide** | Opens this guide on GitHub. |
| **Refresh model list** | Lists the GGUF files on the HP. Asks for the HP token. |
| **Link selected model from NAS** | *Laptop only.* Makes a link to the chosen model in `C:\AI\models`. The model stays on the HP. |
| **Load selected model** | *Laptop only.* Asks your existing local Switchboard to load the linked model. It first asks you to confirm, because it may replace the model that is loaded now. Switchboard keeps its own safety checks. |
| **Steam drive: status** | Shows who owns the Steam drive. |
| **Attach game drive here** | Attaches the Steam drive to this PC. |
| **Release game drive** | Releases the Steam drive so the other PC can attach it. |

Link and the three Steam buttons open a command window and ask Windows for administrator rights. Read the result in that window. The **desktop** has the model buttons turned off. It uses its own local Ollama setup.

The Steam buttons use the local `client\game-drive\game-client.json` from step 5. If it is missing they say to finish the one-time Steam setup first. The app does not do anything beyond the list above.

## Update later

```powershell
Set-Location .\homelab-stack
git pull --ff-only
```

Then run the step 6 install command again on that PC. It refreshes the shortcut and keeps a dated copy of the old settings. Do not commit `game-client.json` or any SSH key.

## If something goes wrong

| You see | What it means | What to do |
| --- | --- | --- |
| LinkSpeed says `1 Gbps` | Slow cable, port or adapter. | Test a short Cat6A cable. Check the switch port. |
| `\\HP_IP\Models` asks again and again for a password | Wrong user or password. | Use user `homelabshare`. Reset the password on the HP with `sudo smbpasswd homelabshare`. |
| `Install-Control-Center.ps1` is not found | Old clone, or the file is not finished. | Run `git pull --ff-only`. Check you are in the repo folder. |
| The app says the token is wrong | You pasted the Dell token. | The app asks for the **HP** token. Use **Change HP token**. |
| Link or Load is greyed out | You are on the desktop. | Those two buttons work on the laptop only. |
| Load fails | Switchboard is not running on the laptop, or it refused (busy, memory, recording). | Start your local Switchboard. Read the message in the command window. Switchboard is expected at `http://127.0.0.1:8002`. |
| The app cannot reach a server | The server is off, or the address is wrong. | Open `http://HP_IP:8088/` in a browser. A Dell that is off is normal. |
| Steam buttons fail | The Steam settings are missing or wrong. | Finish [INSTALL.md step 10](../INSTALL.md#10-set-up-the-exclusive-steam-drive). See the [game drive guide](../GAME-DRIVE.md). |
| PowerShell blocks the script | Execution policy. | Keep `-ExecutionPolicy Bypass` in the command as shown. |

Back to the [start guide](../START-HERE.md).

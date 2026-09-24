# What each machine runs

The goal is local AI on the laptop and desktop, a shared model library, a direct server-hosted Steam game drive, and a Linux development/MCP machine. The NAS and Dell do not need CPU or RAM upgrades for the first build. The photographed ImageStream **PCIE-ETH-GIGX6** has six SFP cages and is not counted as a 10 GbE SFP+ NIC; its GIGX6 label suggests gigabit ports, pending a live PCI ID check.

## HP DL360p Gen8 — always on

- **OS:** Ubuntu Server 24.04 LTS amd64 on the 512 GB SATA SSD. A 64 GiB ext4 root partition and the remaining SSD space at `/srv/homelab` hold the OS, dashboard and 256 GiB Steam iSCSI image.
- **Bulk storage:** four 300 GB 2.5-inch HDDs in one Smart Array RAID0 logical drive, ext4 at `/srv/bulk`. `Projects`, `Backups` and `Models` Samba shares live here. This is about 1.09 TiB before formatting. The `Backups` share is on the same array, so it is not a separate failure copy.
- **Network:** an HPE 560FLR-SFP+ 10 GbE port, matched owned optical modules and owned short fiber to the Cisco. The HP's chosen 10G IP is the iSCSI portal and dashboard address.
- **Software from `hp/deploy.sh`:** OpenSSH, Samba, targetcli-fb/Linux LIO, smartmontools, ethtool, Cockpit, a read-only LAN dashboard, and the prepared game LUN. The game handoff controller is installed in a later step after both Windows initiator IQNs and SSH public keys are known.
- **LAN services:** SMB 445, iSCSI 3260, dashboard HTTP 8088, Cockpit HTTPS 9090, SSH 22. No router port forwarding.
- **Daily use:** browse `\\HP-IP\Models` from the laptop; leave the HP running so models and games stay available.

## Dell R710 — turn on for development

- **OS/storage:** Ubuntu Server 24.04 LTS amd64 on one PERC RAID0 virtual disk made from two 300 GB 2.5-inch HDDs in owned 3.5-inch bay adapters. This is about 559 GiB before formatting.
- **Network:** four built-in 1 GbE RJ45 ports over owned Cat5e patch leads to four 1 GbE copper SFP modules in the Cisco. Netplan `bond0` and a matching Cisco LACP EtherChannel provide up to 4 Gb/s across multiple flows; an individual transfer uses one 1 Gb/s member.
- **Software from `dell/deploy.sh`:** OpenSSH, Git/Git LFS, Python/venv/pipx, C/C++ toolchain, CMake, Ninja, ripgrep, htop, smartmontools, Docker Engine from Docker's Ubuntu repository, Compose, Buildx, Cockpit and Portainer CE.
- **MCP:** a small Streamable HTTP demo container binds to Dell loopback port 8765. The laptop reaches it through an SSH tunnel; additional MCP services can be placed in separate Docker Compose directories under `/opt/homelab/mcp`.
- **LAN services:** SSH 22, Cockpit HTTPS 9090, Portainer HTTPS 9443. The MCP demo is reachable only through the local SSH tunnel. The Dell's containers restart when it boots, but the server must be on to use them.
- **Daily use:** VS Code Remote-SSH or terminal SSH for Linux projects; keep active source trees and container data on Dell storage.

## Windows desktop — local tools and 10G gaming client

- **OS/hardware:** keep Windows 11, GTX 1050 Ti, 700 W PSU and local SSD. Add one genuine used Intel X540-T1 10GBASE-T RJ45 PCIe x8 card in the free x16 slot with the proper bracket and airflow. One pure-copper Cat6A cable goes to one Cisco 10GBASE-T SFP+ module.
- **Software from `desktop/install.ps1`:** Git, VS Code, PowerShell 7, uv, Steam, Windows Subsystem for Linux with Ubuntu, Docker Desktop, and a small local Ollama/Qwen3 assistant. The local model directory stays on a local fixed drive.
- **Steam:** use the shared iSCSI volume as `S:` only while the desktop owns it. `GameDrive.ps1` handles attach/release. It does not format the volume.

## Windows laptop — main AI client

- **OS/hardware:** keep Windows 11 and the existing native llama.cpp, Switchboard, model, recorder and GPU settings. Add a USB4/Thunderbolt 10GBASE-T RJ45 adapter with Windows driver. Its own Cat6A cable goes to a separate copper module in the Cisco.
- **Software from `laptop/install.ps1`:** add Git, VS Code, PowerShell 7, uv and Steam if missing. The installer does not replace the laptop's current `C:\AI` model stack.
- **Models:** store larger GGUF files in `\\HP-IP\Models` and register selected files as links in `C:\AI\models` with `laptop/NetworkModels.ps1`. The laptop loads the selected file over SMB and runs inference locally. Keep the lightweight fallback model on the laptop for offline use.
- **MCP:** Switchboard stays local on the laptop. Dell MCP services are optional additions through SSH forwarding.
- **Steam:** attach the same `S:` volume only after a clean release from the desktop.

## Cisco 3850-24XS — LAN switch

- Keep its current IOS XE and uplink. Use one SFP+ port for HP fiber, one 10GBASE-T module for each PC, and four 1 GbE copper SFP ports for Dell LACP. The example port numbers and VLAN in `switch/candidate.txt` are **placeholders** to adapt at the console.
- A four-port LACP group does not need QSFP. No firmware upgrade is included in the first deployment.

## Limits and recovery

The HP and Dell HDD arrays are RAID0: one member failure loses the entire corresponding array. GitHub backs up only code you commit and push. Keep an independent copy of irreplaceable files, credentials, game saves, and configuration exports. The scripts are safe to re-run when their existing managed files match, and stop on a conflicting configuration instead of overwriting it.

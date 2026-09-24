# Homelab deployment

One Git repository for the **HP NAS**, **Dell development server**, **Windows desktop**, **Windows laptop**, **Cisco 3850-24XS**, and the shared Steam game drive. The HP stays on and holds the model library and game volume. The Dell runs Linux development tools, Docker, MCP services and Portainer when powered on. Both PCs use separate Cat6A 10 GbE links. The Dell uses its four onboard copper 1 GbE ports in one LACP bond.

**Start with [the illustrated install guide](docs/INSTALL.md).** It shows the cables and drive bays, the steps that must be done on the hardware, and the commands to run on each machine. Read the [machine-by-machine stack](docs/STACK.md) when you want to see exactly what each installer adds. The [Dell network guide](docs/DELL-NETWORK.md) covers the four-port bond.

| Machine | Run from a clone of this repo, after the manual hardware and OS steps |
| --- | --- |
| HP Ubuntu NAS | `sudo bash hp/deploy.sh plan HP_10G_IP DELL_IP`, then `install`, then `verify` |
| Dell Ubuntu dev server | `sudo bash dell/deploy.sh plan DELL_BOND_IP`, then `install`, then `verify` |
| Windows desktop | `powershell -ExecutionPolicy Bypass -File .\desktop\install.ps1 -Action Plan`, then `Install`, `PullModel`, `Verify` |
| Windows laptop | `powershell -ExecutionPolicy Bypass -File .\laptop\install.ps1 -Action Plan`, then `InstallTools`, `Verify` |
| Steam game drive on both PCs | `client\game-drive\Prepare-Client.ps1` once, then `GameDrive.ps1 -Action Probe/Attach/Release` as the guide directs |
| Cisco 3850 | Inspect and adapt [candidate switch commands](switch/candidate.txt) from its console |

The installers **do not create RAID arrays, partition disks, initialize the Windows iSCSI disk, flash firmware, or guess LAN addresses**. They refuse several mismatched layouts. Keep the per-machine JSON files and SSH keys local; `.gitignore` excludes them from Git.

This repository has been checked offline on Windows. The server scripts, switch commands, drive controllers, NIC negotiation and iSCSI handoff still need their first live validation on the target hardware. See [validation and first-run checks](docs/VALIDATION.md).

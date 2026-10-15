# Homelab deployment: Dell development server

This public repository is the deployment snapshot for the **Dell development server**. It is part of a five-repository homelab guide; the repositories share the same release snapshot, installation steps, and scripts. Use the setup link below for this host.

- The **HP** stays on. It holds your AI model library and the game drive.
- The **Dell** is your development workbench. It runs Python projects, jobs, file search, Docker and MCP tools when it is on.
- The **Windows PCs** get a control app to pick models and hand the Steam drive back and forth.

## START HERE

### **[Open the beginner install guide: docs/START-HERE.md](docs/START-HERE.md)**

It takes you from parts and cables to a working system. Every step is numbered and says what you should see when it works.

After cloning, open **[the illustrated guide](docs/guide.html)** in a browser. It has a page for each machine, diagrams, the photo of your network card, and copyable commands. The Markdown guide above reads directly on GitHub.

![The planned cabling between the rack and the two PCs](docs/assets/cabling-final.svg)

### Clone the repository for your machine

| Machine | Repository | First setup page |
| --- | --- | --- |
| HP | [homelab-hp](https://github.com/Noxy-HNR/homelab-hp) | [HP setup](docs/setup/HP.md) |
| Dell | [homelab-dell](https://github.com/Noxy-HNR/homelab-dell) | [Dell setup](docs/setup/DELL.md) |
| Desktop | [homelab-desktop](https://github.com/Noxy-HNR/homelab-desktop) | [Windows setup](docs/setup/WINDOWS.md) |
| Laptop | [homelab-laptop](https://github.com/Noxy-HNR/homelab-laptop) | [Windows setup](docs/setup/WINDOWS.md) |

Clone the public repository listed in the table above into a directory named `homelab-stack`. [Full clone instructions](docs/INSTALL.md#6-clone-this-repo-on-each-machine) cover GitHub CLI setup.

## After it is installed

| I want to... | Read |
| --- | --- |
| Use the system every day | [Daily use](docs/DAILY-USE.md) |
| Set up or fix the HP | [HP page](docs/setup/HP.md) |
| Set up or fix the Dell | [Dell page](docs/setup/DELL.md) |
| Set up or fix a Windows PC | [Windows page](docs/setup/WINDOWS.md) |
| Switch the Steam drive between PCs | [Game drive guide](docs/GAME-DRIVE.md) |
| Back up and restore | [Backups](docs/features/OPERATIONS.md) |
| See what each installer adds | [Stack](docs/STACK.md) |

## Upgrade

On each server, from your clone of this repo:

```bash
git pull --ff-only
sudo bash services/install.sh hp HP_IP DELL_IP     # on the HP
sudo bash services/install.sh dell DELL_IP HP_IP   # on the Dell
```

Run only the line for that machine. Your login token and settings are kept.

## What has been tested

The scripts and docs have passed **offline and local functional tests**. **Physical hardware verification is still pending.** No one has yet run this on the real HP, Dell, Cisco switch, network cards and Windows PCs. Treat your first install as the first real test. Details are in [validation status](docs/VALIDATION.md).

The installers never make RAID arrays, partition or format disks, or guess your IP addresses.

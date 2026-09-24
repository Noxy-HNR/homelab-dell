# Validation status

**Prepared and checked offline; the physical HP, Dell, Cisco ports, purchased NICs and Windows iSCSI handoff have not yet been configured.** A parser or mocked controller test does not prove that a Smart Array, PERC, optic, cable or Windows driver will work on the target machine.

From a Windows clone with Python and Git for Windows installed, run:

```powershell
powershell -ExecutionPolicy Bypass -File .\tests\verify-source.ps1
```

The offline suite checks Bash and PowerShell syntax, compiles Python scripts, runs the LIO and Steam controller unit tests, checks that each Steam SSH account can act only for its own host, validates local guide links and diagrams, and confirms local JSON files are Git-ignored. It does **not** install packages, create arrays, format a disk, open iSCSI sessions or edit the current laptop's local AI stack.

| Machine | First live checks needed during installation |
| --- | --- |
| HP | Smart Array shows SSD and four-HDD volumes; both ext4 mounts survive a reboot; purchased 10G NIC and owned optic load without errors and negotiate 10 Gb/s; Samba, LIO and dashboard work after reboot. |
| Dell | PERC shows the correct two-HDD volume; four onboard 1G ports are bundled in Cisco and `/proc/net/bonding/bond0`; Docker, MCP smoke test, Cockpit and Portainer work after reboot. |
| Desktop | Genuine X540-T1 driver loads, NIC fits and stays cool, Cat6A link negotiates 10 Gb/s; Windows tools install and a game launches from `S:`. |
| Laptop | USB4/Thunderbolt adapter loads and negotiates 10 Gb/s; existing `C:\AI` stack remains intact; selected GGUF loads from SMB; game launches after exclusive handoff. |
| Switch | Actual VLAN and unused ports match the candidate, 10G copper modules are accepted, optical HP port is up, Dell EtherChannel has four bundled members. |

Record failures before changing any other layer. The [main install guide](INSTALL.md) lists the order so a broken link or mount is caught before services depend on it.

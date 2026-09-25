# Validation status

**Prepared and checked offline; the physical HP, Dell, Cisco ports, purchased NICs and Windows iSCSI handoff have not yet been configured.** A parser or mocked controller test does not prove that a Smart Array, PERC, optic, cable or Windows driver will work on the target machine.

From a Windows clone with Python and Git for Windows installed, run:

```powershell
powershell -ExecutionPolicy Bypass -File .\tests\verify-source.ps1 -Applications
```

On 2026-09-24 the source verification passed: Bash and PowerShell syntax, Python compilation, the LIO and Steam controller tests, the handoff SSH identity checks, local guide links and diagrams, ignored client secrets, Windows control app guards, and **190 application tests passed, one Linux-only test was skipped**. `node --check` passed for the browser app. A local HTTP preview also signed in to the HP and Dell pages; it listed a sample GGUF, created a Dell example project, ran its test job, and found indexed text. The screenshots in `docs/assets` show that local preview, not the physical servers.

The suite does **not** install packages on the HP or Dell, create arrays, format a disk, open iSCSI sessions, configure the switch, or edit the current laptop's local AI stack. The first real installation remains the hardware acceptance test.

| Machine | First live checks needed during installation |
| --- | --- |
| HP | Smart Array shows SSD and four-HDD volumes; both ext4 mounts survive a reboot; purchased 10G NIC and owned optic load without errors and negotiate 10 Gb/s; Samba, LIO and dashboard work after reboot. |
| Dell | PERC shows the correct two-HDD volume; four onboard 1G ports are bundled in Cisco and `/proc/net/bonding/bond0`; Docker, workbench project/test/search, MCP tool connection, Cockpit and Portainer work after reboot. |
| Desktop | Genuine X540-T1 driver loads, NIC fits and stays cool, Cat6A link negotiates 10 Gb/s; Windows tools install and a game launches from `S:`. |
| Laptop | USB4/Thunderbolt adapter loads and negotiates 10 Gb/s; existing `C:\AI` stack remains intact; selected GGUF loads from SMB; game launches after exclusive handoff. |
| Switch | Actual VLAN and unused ports match the candidate, 10G copper modules are accepted, optical HP port is up, Dell EtherChannel has four bundled members. |

Record failures before changing any other layer. The [main install guide](INSTALL.md) lists the order so a broken link or mount is caught before services depend on it.

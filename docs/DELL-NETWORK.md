# Dell R710: four onboard copper ports

The Dell uses its four built-in **1 GbE RJ45** ports. Run four short copper
patch leads to four separate Cisco 3850-24XS ports containing compatible
**1 GbE RJ45 SFP** modules. All four Cisco ports must be on the same switch
or switch stack, in the same LAN VLAN and in one **LACP EtherChannel**. The
Dell uses a matching Ubuntu **802.3ad bond**. No Dell PCIe network card is
needed.

This is a one-time **local-console** setup: changing the Dell's live network
connection will interrupt SSH. First install Ubuntu on the Dell with one
working onboard port, then work at the Dell keyboard/monitor or iDRAC console
and the Cisco console. Record the current Dell address and the network details:

```bash
ip -br link
ip -br address
ip route
sudo netplan get
ls -l /etc/netplan/
```

Identify the four **actual** interface names and their RJ45 labels. The
example `eno1` through `eno4` below is only a pattern; replace it if Ubuntu
reports different names. If the current Dell address is a router DHCP
reservation, plan to reserve the **bond0 MAC** after the bond is up because
it may differ from the address your router sees now.

## Cisco side

Use the [Cisco command template](../switch/candidate.txt). It shows
example ports `TenGigabitEthernet1/0/4` through `1/0/7` and port-channel 10.
Confirm all four ports and channel 10 are unused; substitute the real switch
member, four port IDs, LAN VLAN and free channel number. Configure the four
member ports as access ports in that same VLAN with `channel-group ... mode
active`. Keep the Cisco console available while configuring Ubuntu.

The 3850's 24 front sockets are SFP+ form factor and support 1 GbE with
compatible **1 GbE** SFP transceivers. Do not count on a 10GBASE-T SFP+
module running at 1 GbE. Confirm each copper module's compatibility with
the switch and its installed IOS XE version before wiring.

## Ubuntu side

Back up the actual Netplan files that currently configure the Dell LAN:

```bash
sudo mkdir -p /root/homelab-netplan-before
sudo cp -a /etc/netplan/. /root/homelab-netplan-before/
```

Edit that file so the **four physical NICs have no IP configuration** and
the address belongs to `bond0`. Here is a DHCP example; replace the four
interface names with the names you recorded:

```yaml
network:
  version: 2
  renderer: networkd
  ethernets:
    eno1: {dhcp4: false, dhcp6: false}
    eno2: {dhcp4: false, dhcp6: false}
    eno3: {dhcp4: false, dhcp6: false}
    eno4: {dhcp4: false, dhcp6: false}
  bonds:
    bond0:
      interfaces: [eno1, eno2, eno3, eno4]
      dhcp4: true
      dhcp6: false
      parameters:
        mode: 802.3ad
        lacp-rate: fast
        mii-monitor-interval: 100
        transmit-hash-policy: layer3+4
```

If the Dell uses a **static address**, put its real `addresses`, `routes`
and `nameservers` under `bond0` instead of `dhcp4: true`. Remove those
settings from the physical NIC entries. Preserve any other required
network settings from the installed configuration. Do not leave a second
Netplan YAML file that assigns the same address to a member NIC.

Check syntax and apply from the **Dell local console**:

```bash
sudo netplan generate
sudo netplan apply
```

Do not rely on `netplan try` as an automatic rollback for this bond; its
rollback behavior for virtual devices and custom bond settings is not a safe
recovery plan. Confirm at the local console that `bond0` has a LAN address
and reaches the router and HP. If it does not, restore the backed-up YAML
from the Dell console, then apply it:

```bash
sudo cp -a /root/homelab-netplan-before/. /etc/netplan/
sudo netplan generate
sudo netplan apply
```

If you created a new Netplan file instead of editing the existing one, remove
that new file before applying the backup. From the Cisco console, remove the
four ports from the new channel-group and restore their original settings
using the interface configurations you recorded before this change.
Then inspect both ends:

```bash
ip -br address show bond0
cat /proc/net/bonding/bond0
ip route
```

On Cisco, run `show etherchannel summary`, `show lacp neighbor`, and
`show interfaces status`. The port-channel should be in use and all four
members should be bundled; the members should each negotiate **1 Gb/s**.
Use the Dell's final LAN IP for `dell/deploy.sh install` and for the
HP dashboard.

This gives up to **4 Gb/s total across different conversations** when the
traffic hashes across members. One ordinary file transfer stays on one
1 Gb/s member. Cisco's load-balance method is switch-wide, so inspect
`show etherchannel load-balance` before changing it for this bond.

[Dell R710 onboard NIC specifications](https://i.dell.com/sites/doccontent/business/solutions/engineering-docs/en/Documents/server-poweredge-r710-tech-guidebook.pdf),
[Cisco 3850 EtherChannel configuration](https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst3850/software/release/16-6/configuration_guide/b_166_lyr2_lyr3_3850_cg/m-165-layer2-etherchanl-cg.html),
[Netplan bond settings](https://netplan.readthedocs.io/en/stable/netplan-yaml/).

# Parts to buy for this version

The hardware budget is **under $300 total delivered**. These are spending caps, not an order or guaranteed checkout prices. Measure the two room cable paths and check what the HP already has before buying.

| Needed part | Target delivered cap | Why |
| --- | ---: | --- |
| Used HPE 560FLR-SFP+ 10G dual-port FlexibleLOM NIC, if HP lacks suitable 10G | $20 | HP to Cisco over owned short fiber |
| Used genuine Intel X540-T1 single-port 10GBASE-T RJ45 PCIe x8, correct desktop bracket | $40 | Replaces the mistaken assumption about the ImageStream card |
| Windows-compatible USB4/Thunderbolt 10GBASE-T RJ45 adapter | $130 | Laptop 10G port |
| Two pure-copper Cat6A room runs of measured lengths | $100 | One separate rack-to-room run per PC |
| **Total target cap** | **$290** | Leaves $10 for price variation; checkout must stay below $300 |

The [Sabrent NT-P10G](https://sabrent.com/products/nt-p10g) is a documented laptop adapter (manufacturer price was $129.99 before tax); this [USB4 retailer example](https://www.centralcomputer.com/usb4-to-10g-ethernet-network-adapter-black.html) was listed at $99.95 before checkout. A [used X540-T1 example](https://www.ebay.com/itm/127787955314) was listed at $39.99 with free shipping before tax. A [used HPE 560FLR-SFP+ example](https://www.ebay.com/itm/188280947795) was near $20. A [75 ft pure-copper Cat6A example](https://www.monoprice.com/Product?p_id=45436) was $20.99 each before shipping and tax. These listings can change or sell out, and 75 ft works only if it actually reaches with slack.

**Check before checkout:** the HP's FlexibleLOM socket and current adapter, desktop full-height or low-profile bracket, Windows drivers, both actual cable routes, the switch's copper module compatibility, and the final tax/shipping total. Cisco's published SFP-10G-T-X 10G reach is [30 m over Cat6A/Cat7](https://www.cisco.com/c/en/us/products/collateral/interfaces-modules/transceiver-modules/data_sheet_c78-455693.html); do not assume a 100 ft cable meets a 30 m rating. The X540-T1 end supports [up to 100 m Cat6A](https://www.intel.com/content/www/us/en/support/articles/000007404/ethernet-products.html), so the switch module limits that example link.

**Already owned and reused:** Cisco 3850-24XS, matched optical modules and short rack fiber, two compatible 10G copper SFP+ switch modules, four compatible 1G copper SFP switch modules, four Cat5e Dell patch cables, six 300 GB 2.5-inch HDDs, one 512 GB SSD, 64 GB USB stick and drive trays/adapters (assumed owned). The photographed ImageStream PCIE-ETH-GIGX6 is set aside until its live PCI ID and link speed are confirmed. No Dell PCIe NIC, CPU upgrade, RAM upgrade, 40G module, or Raspberry Pi part is in this cart.

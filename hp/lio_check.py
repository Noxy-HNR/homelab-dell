#!/usr/bin/env python3
"""Inspect Ubuntu targetcli's saved LIO configuration during NAS install.

This never changes target configuration. It is deliberately strict about the
partial neutral target so rerunning setup cannot adopt an unrelated LUN.
"""

import argparse
import json
from pathlib import Path
import sys

SAVE = Path("/etc/rtslib-fb-target/saveconfig.json")
SIZE = 256 * 1024**3


def fail(message):
    raise ValueError(message)


def inspect(config, image, iqn, ip, *, allow_controller_state=False):
    if not isinstance(config, dict):
        fail("Saved LIO configuration must be a JSON object")
    objects = config.get("storage_objects", [])
    if not isinstance(objects, list) or any(not isinstance(s, dict) for s in objects):
        fail("Invalid storage_objects list")
    stores = [s for s in objects if s.get("plugin") == "fileio" and s.get("name") == "steam_games"]
    if len(stores) > 1:
        fail("Multiple steam_games fileio objects")
    store = stores[0] if stores else None
    if store:
        if store.get("dev") != image or store.get("size", SIZE) != SIZE or store.get("write_back") is not False:
            fail("Existing steam_games backstore differs from the planned file, size, or write_back=false")

    targets_list = config.get("targets", [])
    if not isinstance(targets_list, list) or any(not isinstance(t, dict) for t in targets_list):
        fail("Invalid targets list")
    targets = [t for t in targets_list if t.get("fabric") == "iscsi" and t.get("wwn") == iqn]
    if len(targets) > 1:
        fail("Multiple matching iSCSI targets")
    target = targets[0] if targets else None
    portal = lun = False
    if target:
        tpgs = target.get("tpgs", [])
        if not isinstance(tpgs, list) or len(tpgs) != 1 or not isinstance(tpgs[0], dict) or tpgs[0].get("tag") != 1:
            fail("Target must have exactly TPG 1")
        tpg = tpgs[0]
        if tpg.get("enable", True) and not allow_controller_state:
            fail("Target TPG is enabled during neutral installation")
        if tpg.get("node_acls", []) and not allow_controller_state:
            fail("Target has an initiator ACL during neutral installation")
        if not isinstance(tpg.get("node_acls", []), list):
            fail("Target has an invalid initiator ACL list")
        attrs = tpg.get("attributes", {})
        if not isinstance(attrs, dict):
            fail("Target has invalid attributes")
        for key in ("generate_node_acls", "authentication"):
            if key in attrs and int(attrs[key]) != 0:
                fail(f"Target {key} is not zero")
        portals = tpg.get("portals", [])
        if not isinstance(portals, list) or any(not isinstance(p, dict) for p in portals):
            fail("Target has an invalid portal list")
        if len(portals) > 1 or (portals and (portals[0].get("ip_address") != ip or portals[0].get("port") != 3260)):
            fail("Target has an unexpected portal")
        portal = bool(portals)
        luns = tpg.get("luns", [])
        if not isinstance(luns, list) or any(not isinstance(l, dict) for l in luns):
            fail("Target has an invalid LUN list")
        if len(luns) > 1 or (luns and (luns[0].get("index") != 0 or luns[0].get("storage_object") != "/backstores/fileio/steam_games")):
            fail("Target has an unexpected LUN")
        lun = bool(luns)
    return bool(store), bool(target), portal, lun


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--save", type=Path, default=SAVE)
    parser.add_argument("--image", required=True)
    parser.add_argument("--iqn", required=True)
    parser.add_argument("--ip", required=True)
    parser.add_argument("--complete", action="store_true")
    parser.add_argument("--allow-controller-state", action="store_true",
                        help="Check fixed storage/portal/LUN topology after controller installation")
    args = parser.parse_args()
    data = json.loads(args.save.read_text(encoding="utf-8"))
    states = inspect(data, args.image, args.iqn, args.ip,
                     allow_controller_state=args.allow_controller_state)
    if args.complete and not all(states):
        fail("Neutral LIO target is incomplete")
    print(" ".join("1" if state else "0" for state in states))


if __name__ == "__main__":
    try:
        main()
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"LIO configuration check failed: {exc}", file=sys.stderr)
        sys.exit(1)

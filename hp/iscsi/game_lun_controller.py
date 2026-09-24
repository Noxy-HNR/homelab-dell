#!/usr/bin/env python3
"""Exclusive handoff for the Ubuntu LIO Steam LUN. Install with mode 0755.

The target starts disabled with no ACLs. This controller is the only supported
writer of that target's ACL and TPG enable state after bootstrap. It never
creates, formats, or deletes a storage image.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile


CONFIG_PATH = Path("/etc/homelab/game-lun-controller.json")
STATE_DIR = Path("/var/lib/homelab")
STATE_PATH = STATE_DIR / "game-lun-state.json"
LOCK_PATH = STATE_DIR / "game-lun.lock"
CONFIGFS_ROOT = Path("/sys/kernel/config/target/iscsi")
IQN_RE = re.compile(r"^iqn\.[0-9]{4}-[0-9]{2}\.[a-z0-9.-]+:[a-z0-9.:-]+$")


class HandoffError(Exception):
    pass


def refuse(message: str) -> None:
    raise HandoffError(message)


def read_json(path: Path) -> dict:
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        refuse(f"Cannot read {path}: {exc}")
    if not isinstance(result, dict):
        refuse(f"{path} must contain an object")
    return result


def validate_config(config: dict) -> None:
    if config.get("schema_version") != 1:
        refuse("Config schema_version must be 1")
    if set(config) != {"schema_version", "target_iqn", "desktop_iqn", "laptop_iqn", "portal_ip", "backstore_name"}:
        refuse("Config contains missing or unexpected keys")
    iqns = [config[key] for key in ("target_iqn", "desktop_iqn", "laptop_iqn")]
    if any(not isinstance(value, str) or not IQN_RE.fullmatch(value) or "replace" in value.lower() for value in iqns):
        refuse("Target and client IQNs must be actual valid IQNs")
    if len(set(iqns)) != 3:
        refuse("Target, desktop, and laptop IQNs must differ")
    from ipaddress import IPv4Address

    try:
        ip = IPv4Address(config["portal_ip"])
    except (ValueError, TypeError) as exc:
        refuse(f"portal_ip must be a concrete IPv4 address: {exc}")
    if ip.is_unspecified or ip.is_multicast or ip.is_loopback:
        refuse("portal_ip must be a local non-wildcard host address")
    if config["backstore_name"] != "steam_games":
        refuse("This controller is scoped to the steam_games fileio backstore")


def assert_root_only(path: Path, *, is_dir: bool = False) -> None:
    try:
        info = path.stat()
    except OSError as exc:
        refuse(f"Cannot stat {path}: {exc}")
    if info.st_uid != 0 or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        refuse(f"{path} must be root-owned and not group/world writable")
    if is_dir and not stat.S_ISDIR(info.st_mode):
        refuse(f"{path} must be a directory")
    if not is_dir and not stat.S_ISREG(info.st_mode):
        refuse(f"{path} must be a regular file")


def read_int(path: Path) -> int:
    try:
        value = int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError) as exc:
        refuse(f"Cannot read kernel target value {path}: {exc}")
    if value < 0:
        refuse(f"Invalid negative kernel target value at {path}")
    return value


class LioTarget:
    def __init__(self, config: dict, root: Path = CONFIGFS_ROOT):
        self.config = config
        self.target_dir = root / config["target_iqn"]
        self.tpg_dir = self.target_dir / "tpgt_1"
        self.acls_dir = self.tpg_dir / "acls"

    def inspect(self) -> dict:
        if not self.tpg_dir.is_dir():
            refuse(f"Expected LIO target is absent: {self.tpg_dir}")
        tpgs = sorted(path.name for path in self.target_dir.glob("tpgt_*") if path.is_dir())
        if tpgs != ["tpgt_1"]:
            refuse(f"Expected only tpgt_1, found {tpgs}")
        if read_int(self.tpg_dir / "attrib" / "generate_node_acls") != 0:
            refuse("generate_node_acls must be 0; permissive target is unsafe")
        if read_int(self.tpg_dir / "attrib" / "authentication") != 0:
            refuse("Unexpected TPG authentication setting")
        portals = sorted(path.name for path in (self.tpg_dir / "np").iterdir())
        expected_portal = f"{self.config['portal_ip']}:3260"
        if portals != [expected_portal]:
            refuse(f"Expected only portal {expected_portal}, found {portals}")
        luns = sorted(path.name for path in (self.tpg_dir / "lun").iterdir() if path.is_dir())
        if luns != ["lun_0"]:
            refuse(f"Expected exactly LUN 0, found {luns}")
        lun_links = [entry for entry in (self.tpg_dir / "lun" / "lun_0").iterdir() if entry.is_symlink()]
        if len(lun_links) != 1 or lun_links[0].resolve().name != self.config["backstore_name"]:
            refuse("LUN 0 no longer points to the configured steam_games backstore")
        if not self.acls_dir.is_dir():
            refuse("LIO ACL directory is absent")
        acl_names = sorted(path.name for path in self.acls_dir.iterdir())
        if any(name not in (self.config["desktop_iqn"], self.config["laptop_iqn"]) for name in acl_names):
            refuse(f"Unknown ACL present: {acl_names}")
        sessions = read_int(self.target_dir / "fabric_statistics" / "iscsi_instance" / "sessions")
        # If the dynamic-session pseudo-file reports anything, fail closed even
        # though generate_node_acls=0 should prevent such sessions.
        dynamic_path = self.tpg_dir / "dynamic_sessions"
        try:
            dynamic = dynamic_path.read_text(encoding="utf-8").strip() if dynamic_path.exists() else ""
        except OSError as exc:
            refuse(f"Cannot inspect dynamic sessions: {exc}")
        if dynamic:
            refuse("Dynamic iSCSI sessions exist; inspect target manually")
        return {"enabled": read_int(self.tpg_dir / "enable"), "acls": acl_names, "sessions": sessions}

    def mapped_owner(self, iqn: str) -> bool:
        acl = self.acls_dir / iqn
        mapping = acl / "lun_0"
        if not mapping.is_dir():
            return False
        mappings = sorted(path.name for path in acl.glob("lun_*") if path.is_dir())
        links = [entry for entry in mapping.iterdir() if entry.is_symlink()]
        return mappings == ["lun_0"] and len(links) == 1 and links[0].resolve() == (self.tpg_dir / "lun" / "lun_0").resolve()

    def cli(self, *arguments: str) -> None:
        command = ["/usr/bin/targetcli", *arguments]
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as exc:
            refuse(f"targetcli could not complete {arguments[-1]}: {exc}")
        if result.returncode != 0:
            refuse(f"targetcli refused {arguments[-1]}: {result.stderr.strip() or result.stdout.strip()}")

    @property
    def tpg_cli(self) -> str:
        return f"/iscsi/{self.config['target_iqn']}/tpg1"

    def create_owner_acl(self, iqn: str) -> None:
        self.cli(f"{self.tpg_cli}/acls", "create", iqn)
        self.cli(f"{self.tpg_cli}/acls/{iqn}", "create", "0", "0")

    def delete_owner_acl(self, iqn: str) -> None:
        self.cli(f"{self.tpg_cli}/acls", "delete", iqn)

    def set_enabled(self, enabled: bool) -> None:
        self.cli(self.tpg_cli, "enable" if enabled else "disable")

    def save(self) -> None:
        self.cli("/", "saveconfig")


def atomic_state(path: Path, value: dict) -> None:
    fd, tmp_name = tempfile.mkstemp(prefix=".game-lun-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(value, output, indent=2, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(tmp_name, path)
        if os.name != "nt":
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def load_state(path: Path) -> dict:
    state = read_json(path)
    if set(state) != {"phase", "owner", "generation"}:
        refuse("State has missing or unexpected keys")
    if state["phase"] not in ("released", "claiming", "active", "releasing"):
        refuse("Unknown handoff phase")
    if state["owner"] not in (None, "desktop", "laptop"):
        refuse("Unknown owner")
    if type(state["generation"]) is not int or state["generation"] < 0:
        refuse("Invalid generation")
    if (state["phase"] == "released") != (state["owner"] is None):
        refuse("State phase and owner disagree")
    return state


def show(state: dict | None, snapshot: dict) -> None:
    print(json.dumps({"state": state, "target": snapshot}, indent=2, sort_keys=True))


def operate(target: LioTarget, state_path: Path, action: str, host: str | None,
            clean_offline: bool, manual_offline: bool = False) -> None:
    snapshot = target.inspect()
    if action == "status":
        show(load_state(state_path) if state_path.exists() else None, snapshot)
        return
    if action == "verify":
        state = load_state(state_path)
        if state["phase"] == "released":
            if snapshot != {"enabled": 0, "acls": [], "sessions": 0}:
                refuse("Released state and live target disagree")
        elif state["phase"] == "active":
            owner_iqn = target.config[f"{state['owner']}_iqn"]
            if snapshot["enabled"] != 1 or snapshot["acls"] != [owner_iqn] or not target.mapped_owner(owner_iqn):
                refuse("Active owner and live target disagree")
        else:
            refuse("Handoff transition is incomplete; inspect status and recover manually")
        show(state, snapshot)
        return
    if action == "bootstrap":
        if state_path.exists():
            refuse("State already exists; bootstrap never overwrites it")
        if snapshot != {"enabled": 0, "acls": [], "sessions": 0}:
            refuse("Bootstrap requires a disabled TPG, no ACLs, and zero sessions")
        atomic_state(state_path, {"phase": "released", "owner": None, "generation": 0})
        show(load_state(state_path), target.inspect())
        return
    if action == "recover-neutral":
        if not manual_offline:
            refuse("Recovery requires --both-clients-offline after manual inspection")
        if os.environ.get("SSH_CONNECTION"):
            refuse("Recovery must run from the NAS console, not an SSH session")
        state = load_state(state_path)
        if state["phase"] == "released":
            refuse("State is already released; inspect unexpected target configuration manually")
        if snapshot["sessions"] != 0:
            refuse("Recovery refused: target still has an iSCSI session")
        if len(snapshot["acls"]) > 1:
            refuse("Recovery refused: more than one ACL is present")
        state["phase"] = "releasing"
        atomic_state(state_path, state)
        if target.inspect()["sessions"] != 0:
            refuse("A session appeared during recovery; state remains releasing")
        if snapshot["enabled"]:
            target.set_enabled(False)
        if target.inspect()["sessions"] != 0:
            refuse("A session survived fencing; state remains releasing")
        for acl_iqn in target.inspect()["acls"]:
            target.delete_owner_acl(acl_iqn)
        target.save()
        neutral = target.inspect()
        if neutral != {"enabled": 0, "acls": [], "sessions": 0}:
            refuse("Recovery did not reach neutral; state remains releasing")
        state["owner"] = None
        state["phase"] = "released"
        state["generation"] += 1
        atomic_state(state_path, state)
        show(state, neutral)
        return
    if not host:
        refuse("claim and release require --host desktop or laptop")
    state = load_state(state_path)
    owner_iqn = target.config[f"{host}_iqn"]
    if action == "claim":
        if state["phase"] == "active" and state["owner"] == host:
            if snapshot["enabled"] != 1 or snapshot["acls"] != [owner_iqn] or not target.mapped_owner(owner_iqn):
                refuse("State/target disagree; manual inspection required")
            show(state, snapshot)
            return
        if state["phase"] != "released":
            refuse("Another host owns the LUN or a transition is incomplete")
        if snapshot != {"enabled": 0, "acls": [], "sessions": 0}:
            refuse("Target is not neutral; refusing claim")
        state = {"phase": "claiming", "owner": host, "generation": state["generation"] + 1}
        atomic_state(state_path, state)
        target.create_owner_acl(owner_iqn)
        before_enable = target.inspect()
        if before_enable != {"enabled": 0, "acls": [owner_iqn], "sessions": 0} or not target.mapped_owner(owner_iqn):
            refuse("ACL mapping did not match owner; state remains claiming")
        target.set_enabled(True)
        target.save()
        after_enable = target.inspect()
        if after_enable["enabled"] != 1 or after_enable["acls"] != [owner_iqn] or not target.mapped_owner(owner_iqn):
            refuse("Target did not retain exclusive owner; state remains claiming")
        state["phase"] = "active"
        atomic_state(state_path, state)
        show(state, after_enable)
        return
    if not clean_offline:
        refuse("Release requires --clean-client-offline after Windows has offlined and disconnected the exact disk")
    if state["owner"] != host or state["phase"] != "active":
        refuse("Only the active owner may release; incomplete transitions need manual inspection")
    if snapshot["enabled"] != 1 or snapshot["acls"] != [owner_iqn] or not target.mapped_owner(owner_iqn):
        refuse("State/target disagree; manual inspection required")
    if snapshot["sessions"] != 0:
        refuse("Target still has an iSCSI session; owner remains held")
    state["phase"] = "releasing"
    atomic_state(state_path, state)
    # A second check catches a reconnect in the gap before the TPG is fenced.
    if target.inspect()["sessions"] != 0:
        refuse("A session reappeared; state remains releasing")
    target.set_enabled(False)
    fenced = target.inspect()
    if fenced["enabled"] != 0 or fenced["sessions"] != 0:
        refuse("TPG did not fence cleanly; state remains releasing")
    target.delete_owner_acl(owner_iqn)
    target.save()
    neutral = target.inspect()
    if neutral != {"enabled": 0, "acls": [], "sessions": 0}:
        refuse("Target did not return to neutral; state remains releasing")
    state["owner"] = None
    state["phase"] = "released"
    atomic_state(state_path, state)
    show(state, neutral)


def main() -> None:
    import fcntl

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "verify", "bootstrap", "claim", "release", "recover-neutral"))
    parser.add_argument("--host", choices=("desktop", "laptop"))
    parser.add_argument("--clean-client-offline", action="store_true")
    parser.add_argument("--both-clients-offline", action="store_true")
    args = parser.parse_args()
    if os.geteuid() != 0:
        refuse("Run as root on the Ubuntu NAS")
    assert_root_only(CONFIG_PATH)
    assert_root_only(STATE_DIR, is_dir=True)
    config = read_json(CONFIG_PATH)
    validate_config(config)
    target = LioTarget(config)
    with LOCK_PATH.open("a+") as lock:
        os.fchmod(lock.fileno(), 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        operate(target, STATE_PATH, args.action, args.host, args.clean_client_offline,
                args.both_clients_offline)


if __name__ == "__main__":
    try:
        main()
    except HandoffError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        raise SystemExit(2)

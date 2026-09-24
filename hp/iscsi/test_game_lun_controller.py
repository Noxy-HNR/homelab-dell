"""Offline transaction tests; no LIO or real disks are changed."""

import contextlib
import io
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import game_lun_controller as controller


CONFIG = {
    "schema_version": 1,
    "target_iqn": "iqn.2026-09.local.homelab:steam-games",
    "desktop_iqn": "iqn.1991-05.com.microsoft:desktop",
    "laptop_iqn": "iqn.1991-05.com.microsoft:laptop",
    "portal_ip": "192.168.1.20",
    "backstore_name": "steam_games",
}


class FakeTarget:
    def __init__(self):
        self.config = CONFIG
        self.enabled = 0
        self.acls = []
        self.sessions = 0
        self.mapped = set()
        self.saves = 0
        self.fail_enable = False

    def inspect(self):
        return {"enabled": self.enabled, "acls": sorted(self.acls), "sessions": self.sessions}

    def mapped_owner(self, iqn):
        return iqn in self.mapped

    def create_owner_acl(self, iqn):
        self.acls.append(iqn)
        self.mapped.add(iqn)

    def delete_owner_acl(self, iqn):
        self.acls.remove(iqn)
        self.mapped.remove(iqn)

    def set_enabled(self, enabled):
        if enabled and self.fail_enable:
            raise controller.HandoffError("simulated LIO failure")
        self.enabled = int(enabled)

    def save(self):
        self.saves += 1


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / "state.json"
        self.target = FakeTarget()

    def run_action(self, action, host=None, clean=False, manual=False):
        with contextlib.redirect_stdout(io.StringIO()):
            controller.operate(self.target, self.state, action, host, clean, manual)

    def test_bootstrap_only_neutral_once(self):
        self.target.enabled = 1
        with self.assertRaises(controller.HandoffError):
            self.run_action("bootstrap")
        self.target.enabled = 0
        self.run_action("bootstrap")
        with self.assertRaises(controller.HandoffError):
            self.run_action("bootstrap")

    def test_single_acl_then_orderly_handoff(self):
        self.run_action("bootstrap")
        self.run_action("verify")
        self.run_action("claim", "desktop")
        self.run_action("verify")
        self.assertEqual(self.target.acls, [CONFIG["desktop_iqn"]])
        self.assertEqual(self.target.enabled, 1)
        self.run_action("claim", "desktop")  # idempotent same-owner retry
        with self.assertRaises(controller.HandoffError):
            self.run_action("claim", "laptop")
        with self.assertRaises(controller.HandoffError):
            self.run_action("release", "desktop")
        self.assertEqual(self.target.enabled, 1)
        self.run_action("release", "desktop", clean=True)
        self.run_action("verify")
        self.assertEqual(self.target.inspect(), {"enabled": 0, "acls": [], "sessions": 0})
        self.run_action("claim", "laptop")
        self.assertEqual(self.target.acls, [CONFIG["laptop_iqn"]])

    def test_verify_fails_on_state_target_disagreement(self):
        self.run_action("bootstrap")
        self.target.enabled = 1
        with self.assertRaises(controller.HandoffError):
            self.run_action("verify")
        self.target.enabled = 0
        self.run_action("claim", "desktop")
        self.target.mapped.clear()
        with self.assertRaises(controller.HandoffError):
            self.run_action("verify")

    def test_verify_refuses_incomplete_handoff(self):
        self.run_action("bootstrap")
        self.target.fail_enable = True
        with self.assertRaises(controller.HandoffError):
            self.run_action("claim", "desktop")
        with self.assertRaises(controller.HandoffError):
            self.run_action("verify")

    def test_release_refuses_live_session(self):
        self.run_action("bootstrap")
        self.run_action("claim", "desktop")
        self.target.sessions = 1
        with self.assertRaises(controller.HandoffError):
            self.run_action("release", "desktop", clean=True)
        self.assertEqual(controller.load_state(self.state)["phase"], "active")
        self.assertEqual(self.target.enabled, 1)
        self.assertEqual(self.target.acls, [CONFIG["desktop_iqn"]])

    def test_incomplete_claim_blocks_takeover(self):
        self.run_action("bootstrap")
        self.target.fail_enable = True
        with self.assertRaises(controller.HandoffError):
            self.run_action("claim", "desktop")
        self.assertEqual(controller.load_state(self.state)["phase"], "claiming")
        with self.assertRaises(controller.HandoffError):
            self.run_action("claim", "laptop")

    def test_manual_recovery_requires_no_sessions_and_explicit_ack(self):
        self.run_action("bootstrap")
        self.run_action("claim", "desktop")
        with self.assertRaises(controller.HandoffError):
            self.run_action("recover-neutral")
        self.target.sessions = 1
        with self.assertRaises(controller.HandoffError):
            self.run_action("recover-neutral", manual=True)
        self.target.sessions = 0
        with patch.dict("os.environ", {"SSH_CONNECTION": "192.0.2.1"}):
            with self.assertRaises(controller.HandoffError):
                self.run_action("recover-neutral", manual=True)
        self.run_action("recover-neutral", manual=True)
        self.assertEqual(self.target.inspect(), {"enabled": 0, "acls": [], "sessions": 0})
        self.assertEqual(controller.load_state(self.state)["phase"], "released")

    def test_config_requires_real_distinct_iqns(self):
        controller.validate_config(CONFIG)
        bad = dict(CONFIG, laptop_iqn=CONFIG["desktop_iqn"])
        with self.assertRaises(controller.HandoffError):
            controller.validate_config(bad)
        bad = dict(CONFIG, portal_ip="0.0.0.0")
        with self.assertRaises(controller.HandoffError):
            controller.validate_config(bad)


class ConfigfsInspectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = dict(CONFIG, target_iqn="test-target")
        self.tpg = self.root / "test-target" / "tpgt_1"
        for directory in (self.tpg / "attrib", self.tpg / "np", self.tpg / "lun" / "lun_0",
                          self.tpg / "acls", self.tpg.parent / "fabric_statistics" / "iscsi_instance"):
            directory.mkdir(parents=True)
        (self.tpg / "attrib" / "generate_node_acls").write_text("0\n")
        (self.tpg / "attrib" / "authentication").write_text("0\n")
        (self.tpg / "enable").write_text("0\n")
        (self.tpg.parent / "fabric_statistics" / "iscsi_instance" / "sessions").write_text("0\n")
        self.portals = ["192.168.1.20:3260"]
        self.target = controller.LioTarget(self.config, root=self.root)

    def inspect(self):
        real_iterdir = Path.iterdir
        portals_path = self.tpg / "np"

        def fake_iterdir(path):
            if path == portals_path:
                return iter(SimpleNamespace(name=name) for name in self.portals)
            if path == self.tpg / "lun" / "lun_0":
                return iter([SimpleNamespace(is_symlink=lambda: True,
                                             resolve=lambda: Path("steam_games"))])
            return real_iterdir(path)

        with patch.object(Path, "iterdir", fake_iterdir):
            return self.target.inspect()

    def test_inspect_reads_live_topology_and_sessions(self):
        self.assertEqual(self.inspect(), {"enabled": 0, "acls": [], "sessions": 0})
        (self.tpg.parent / "fabric_statistics" / "iscsi_instance" / "sessions").write_text("1\n")
        self.assertEqual(self.inspect()["sessions"], 1)

    def test_inspect_rejects_unexpected_portal_acl_and_permissive_mode(self):
        self.portals.append("0.0.0.0:3260")
        with self.assertRaises(controller.HandoffError):
            self.inspect()
        self.portals.pop()
        (self.tpg / "acls" / "unknown").mkdir()
        with self.assertRaises(controller.HandoffError):
            self.inspect()
        (self.tpg / "acls" / "unknown").rmdir()
        (self.tpg / "attrib" / "generate_node_acls").write_text("1\n")
        with self.assertRaises(controller.HandoffError):
            self.inspect()


if __name__ == "__main__":
    unittest.main()

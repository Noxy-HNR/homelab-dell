"""Offline checks for refusing an unsafe or unrelated LIO configuration."""
import copy
import unittest
from lio_check import inspect, SIZE

IMAGE = '/srv/homelab/iscsi/steam-games.img'
IQN = 'iqn.2026-09.local.homelab:steam-games'
IP = '192.168.1.10'
CONFIG = {
    'storage_objects': [{'plugin': 'fileio', 'name': 'steam_games',
                         'dev': IMAGE, 'size': SIZE, 'write_back': False}],
    'targets': [{'fabric': 'iscsi', 'wwn': IQN, 'tpgs': [{
        'tag': 1, 'enable': False, 'node_acls': [],
        'attributes': {'generate_node_acls': 0, 'authentication': 0},
        'portals': [{'ip_address': IP, 'port': 3260}],
        'luns': [{'index': 0, 'storage_object': '/backstores/fileio/steam_games'}]
    }]}]
}

class ProvisionChecks(unittest.TestCase):
    def test_neutral_and_empty_states(self):
        self.assertEqual(inspect(CONFIG, IMAGE, IQN, IP), (True, True, True, True))
        self.assertEqual(inspect({}, IMAGE, IQN, IP), (False, False, False, False))

    def test_foreign_image_or_write_cache_refused(self):
        for key, value in [('dev', '/wrong.img'), ('write_back', True), ('size', 1)]:
            config = copy.deepcopy(CONFIG)
            config['storage_objects'][0][key] = value
            with self.assertRaises(ValueError):
                inspect(config, IMAGE, IQN, IP)

    def test_permissive_or_active_target_refused(self):
        for field, value in [('enable', True), ('node_acls', [{'node_wwn': 'client'}]),
                             ('portals', [{'ip_address': '0.0.0.0', 'port': 3260}]),
                             ('attributes', {'generate_node_acls': 1})]:
            config = copy.deepcopy(CONFIG)
            config['targets'][0]['tpgs'][0][field] = value
            with self.assertRaises(ValueError):
                inspect(config, IMAGE, IQN, IP)

    def test_completed_install_can_check_fixed_topology_with_active_controller(self):
        config = copy.deepcopy(CONFIG)
        config['targets'][0]['tpgs'][0]['enable'] = True
        config['targets'][0]['tpgs'][0]['node_acls'] = [{'node_wwn': 'iqn.1991-05.com.microsoft:desktop'}]
        self.assertEqual(inspect(config, IMAGE, IQN, IP, allow_controller_state=True),
                         (True, True, True, True))
        config['targets'][0]['tpgs'][0]['luns'][0]['storage_object'] = '/backstores/fileio/unrelated'
        with self.assertRaises(ValueError):
            inspect(config, IMAGE, IQN, IP, allow_controller_state=True)

    def test_malformed_saved_configuration_fails_closed(self):
        for config in ([], {'storage_objects': None}, {'targets': [None]}):
            with self.assertRaises(ValueError):
                inspect(config, IMAGE, IQN, IP)
        for field in ('attributes', 'node_acls', 'portals', 'luns'):
            config = copy.deepcopy(CONFIG)
            config['targets'][0]['tpgs'][0][field] = None
            with self.assertRaises(ValueError):
                inspect(config, IMAGE, IQN, IP)

if __name__ == '__main__':
    unittest.main()

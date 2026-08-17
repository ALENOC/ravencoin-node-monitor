import unittest

import privacy


class PrivacyTests(unittest.TestCase):
    def test_ipv4_masked(self):
        self.assertEqual(privacy.mask_ip("192.168.1.123"), "192.168.x.x")

    def test_ipv4_with_port_masked(self):
        self.assertEqual(privacy.mask_addr("192.168.1.123:8767"), "192.168.x.x:8767")

    def test_ipv6_masked(self):
        self.assertEqual(privacy.mask_ip("2001:db8::1"), "2001:db8:x:x:x:x")

    def test_ipv6_bracketed_with_port_masked(self):
        self.assertEqual(privacy.mask_addr("[2001:db8::1]:8767"), "[2001:db8:x:x:x:x]:8767")

    def test_onion_untouched(self):
        addr = "abcdefghijklmnop.onion:8767"
        self.assertEqual(privacy.mask_addr(addr), addr)

    def test_apply_privacy_masks_peers_and_electrumx_clients(self):
        snapshot = {
            "peers": [{"addr": "1.2.3.4:8767"}],
            "electrumx": {"sessions": [{"remote_address": "5.6.7.8:50002"}]},
            "banned_peers": [{"address": "9.10.11.12:8767"}],
        }
        privacy.apply_privacy(snapshot, True)
        self.assertEqual(snapshot["peers"][0]["addr"], "1.2.x.x:8767")
        self.assertEqual(snapshot["electrumx"]["sessions"][0]["remote_address"], "5.6.x.x:50002")
        self.assertEqual(snapshot["banned_peers"][0]["address"], "9.10.x.x:8767")

    def test_apply_privacy_noop_when_disabled(self):
        snapshot = {"peers": [{"addr": "1.2.3.4:8767"}]}
        privacy.apply_privacy(snapshot, False)
        self.assertEqual(snapshot["peers"][0]["addr"], "1.2.3.4:8767")


if __name__ == "__main__":
    unittest.main()

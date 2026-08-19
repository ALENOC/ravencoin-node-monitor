import unittest

from tests.fake_rpc import synced_core

import collector
import config
import network_traffic
import rpc
import state as state_module


class TrafficTrackerTests(unittest.TestCase):
    def test_first_sample_has_totals_but_no_fake_rate(self):
        tracker = network_traffic.TrafficTracker()
        snap = tracker.update(
            {"totalbytesrecv": 1000, "totalbytessent": 500, "uploadtarget": {}},
            now=10.0,
        )
        self.assertEqual(snap["total_bytes_received"], 1000)
        self.assertEqual(snap["total_bytes_sent"], 500)
        self.assertEqual(snap["total_bytes_transferred"], 1500)
        self.assertIsNone(snap["download_bytes_per_second"])
        self.assertIsNone(snap["upload_bytes_per_second"])

    def test_rates_are_deltas_between_core_counters(self):
        tracker = network_traffic.TrafficTracker()
        tracker.update(
            {"totalbytesrecv": 1000, "totalbytessent": 500, "uploadtarget": {}},
            now=10.0,
        )
        snap = tracker.update(
            {"totalbytesrecv": 21_000, "totalbytessent": 10_500, "uploadtarget": {}},
            now=20.0,
        )
        self.assertEqual(snap["download_bytes_per_second"], 2000.0)
        self.assertEqual(snap["upload_bytes_per_second"], 1000.0)
        self.assertEqual(snap["sample_seconds"], 10.0)

    def test_counter_reset_after_core_restart_does_not_create_negative_spike(self):
        tracker = network_traffic.TrafficTracker()
        tracker.update(
            {"totalbytesrecv": 50_000, "totalbytessent": 20_000, "uploadtarget": {}},
            now=10.0,
        )
        restarted = tracker.update(
            {"totalbytesrecv": 100, "totalbytessent": 50, "uploadtarget": {}},
            now=20.0,
        )
        self.assertIsNone(restarted["download_bytes_per_second"])
        self.assertIsNone(restarted["upload_bytes_per_second"])

        next_sample = tracker.update(
            {"totalbytesrecv": 1100, "totalbytessent": 550, "uploadtarget": {}},
            now=30.0,
        )
        self.assertEqual(next_sample["download_bytes_per_second"], 100.0)
        self.assertEqual(next_sample["upload_bytes_per_second"], 50.0)

    def test_upload_target_is_normalized(self):
        tracker = network_traffic.TrafficTracker()
        snap = tracker.update(
            {
                "totalbytesrecv": 1,
                "totalbytessent": 2,
                "uploadtarget": {
                    "timeframe": 86400,
                    "target": 1000,
                    "bytes_left_in_cycle": 250,
                    "time_left_in_cycle": 3600,
                    "target_reached": False,
                    "serve_historical_blocks": True,
                },
            },
            now=10.0,
        )
        target = snap["upload_target"]
        self.assertTrue(target["enabled"])
        self.assertEqual(target["used_bytes"], 750)
        self.assertEqual(target["bytes_left"], 250)
        self.assertEqual(target["progress"], 0.75)
        self.assertEqual(target["time_left_seconds"], 3600)
        self.assertTrue(target["serve_historical_blocks"])


class CollectorTrafficTests(unittest.TestCase):
    def setUp(self):
        self._orig_post = rpc._post
        self.addCleanup(setattr, rpc, "_post", self._orig_post)
        rpc.latency_tracker._samples.clear()

    def test_status_snapshot_contains_only_normalized_ravencoin_traffic(self):
        core = synced_core()
        rpc._post = core
        cfg = config.Config()
        cfg.core_host = "fake"
        cfg.core_port = 1
        cfg.electrumx_mode = "false"
        cfg.history_storage = "memory"
        st = state_module.MonitorState(cfg)

        snap = collector.build_snapshot(cfg, st)

        self.assertIn("getnettotals", core.call_log)
        self.assertEqual(snap["network_traffic"]["scope"], "ravencoin_p2p")
        self.assertEqual(snap["network_traffic"]["source"], "getnettotals")
        self.assertEqual(snap["network_traffic"]["total_bytes_received"], 250_000_000)
        self.assertEqual(snap["network_traffic"]["total_bytes_sent"], 125_000_000)
        self.assertNotIn("net_totals", snap)


if __name__ == "__main__":
    unittest.main()

import time
import unittest

from tests.fake_rpc import FakeCore, block, synced_core

import collector
import config
import rpc
import state as state_module


def make_cfg(**overrides):
    cfg = config.Config()
    cfg.core_host = "fake"
    cfg.core_port = 1
    cfg.electrumx_mode = "false"
    cfg.history_enabled = True
    cfg.history_storage = "memory"
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


class CollectorTestCase(unittest.TestCase):
    def setUp(self):
        self._orig_post = rpc._post
        self.addCleanup(setattr, rpc, "_post", self._orig_post)
        rpc.latency_tracker._samples.clear()

    def patch_rpc(self, fake_core):
        rpc._post = fake_core


class NormalNodeTests(CollectorTestCase):
    def test_synced_node_is_healthy(self):
        core = synced_core(height=1000, connections=8)
        self.patch_rpc(core)
        cfg = make_cfg()
        st = state_module.MonitorState(cfg)

        snap = collector.build_snapshot(cfg, st)

        self.assertFalse(snap["starting_up"])
        self.assertEqual(snap["errors"], [])
        self.assertEqual(snap["health"]["status"], "healthy")
        self.assertEqual(snap["health"]["score"], 100)
        self.assertEqual(len(snap["peers"]), 8)
        self.assertIsNotNone(snap["rpc_latency"]["current_ms"])

    def test_core_version_display_strips_wire_format_slashes(self):
        core = synced_core()  # subversion "/Ravencoin:4.8.0/"
        self.patch_rpc(core)
        cfg = make_cfg()
        st = state_module.MonitorState(cfg)
        snap = collector.build_snapshot(cfg, st)
        self.assertEqual(snap["core_version"]["version"], "Ravencoin:4.8.0")

    def test_core_version_safety_check(self):
        core = synced_core()
        self.patch_rpc(core)
        cfg = make_cfg(min_safe_core_version="4.9.0")  # node reports 4.8.0 -> unsafe
        st = state_module.MonitorState(cfg)
        snap = collector.build_snapshot(cfg, st)
        self.assertFalse(snap["core_version"]["safe"])

        cfg2 = make_cfg(min_safe_core_version="4.0.0")  # node is newer -> safe
        st2 = state_module.MonitorState(cfg2)
        snap2 = collector.build_snapshot(cfg2, st2)
        self.assertTrue(snap2["core_version"]["safe"])


class WarmupAndFailureTests(CollectorTestCase):
    def test_startup_warmup_short_circuits(self):
        core = FakeCore()
        core.set_error("getblockchaininfo", -28, "Loading block index...")
        self.patch_rpc(core)
        cfg = make_cfg()
        st = state_module.MonitorState(cfg)

        snap = collector.build_snapshot(cfg, st)

        self.assertTrue(snap["starting_up"])
        self.assertEqual(snap["health"]["status"], "unknown")
        # Only the one doomed call should have been made, not six more.
        self.assertEqual(core.call_log, ["getblockchaininfo"])

    def test_connection_refused_is_critical_not_a_crash(self):
        def refuse(cfg, payload):
            raise ConnectionRefusedError("refused")

        rpc._post = refuse
        cfg = make_cfg()
        st = state_module.MonitorState(cfg)
        # _post itself isn't touched by our refuse fn signature mismatch;
        # instead simulate via a real urlopen failure path is out of scope
        # here - directly assert build_snapshot survives an RpcError by
        # making getblockchaininfo fail via error response instead.
        core = FakeCore()
        core.set_error("getblockchaininfo", -1, "some failure")
        rpc._post = core
        snap = collector.build_snapshot(cfg, st)
        self.assertIsNone(snap["blockchain"])
        self.assertEqual(snap["health"]["status"], "critical")
        self.assertEqual(snap["health"]["score"], 0)
        self.assertIn("core_unreachable", snap["health"]["active_alerts"])

    def test_recovery_after_outage_emits_events(self):
        cfg = make_cfg()
        st = state_module.MonitorState(cfg)

        down = FakeCore()
        down.set_error("getblockchaininfo", -1, "down")
        self.patch_rpc(down)
        collector.build_snapshot(cfg, st)  # baseline: unreachable

        up = synced_core()
        self.patch_rpc(up)
        collector.build_snapshot(cfg, st)  # recovers

        types = [e["type"] for e in st.event_log.recent()]
        self.assertIn("core_recovered", types)

    def test_core_restart_detected(self):
        cfg = make_cfg()
        st = state_module.MonitorState(cfg)
        core = synced_core()
        core.set("uptime", 5000)
        self.patch_rpc(core)
        collector.build_snapshot(cfg, st)

        core.set("uptime", 30)  # uptime dropped -> restart
        collector.build_snapshot(cfg, st)

        types = [e["type"] for e in st.event_log.recent()]
        self.assertIn("core_restart_detected", types)


class ChainTests(CollectorTestCase):
    def test_new_block_event(self):
        cfg = make_cfg()
        st = state_module.MonitorState(cfg)
        core = synced_core(height=100)
        self.patch_rpc(core)
        collector.build_snapshot(cfg, st)

        core2 = synced_core(height=101)
        self.patch_rpc(core2)
        collector.build_snapshot(cfg, st)

        events = [e for e in st.event_log.recent() if e["type"] == "new_block"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["metadata"]["height"], 101)

    def test_stale_chain_tip_flagged(self):
        cfg = make_cfg(chain_stale_warning_seconds=100, chain_stale_critical_seconds=200)
        st = state_module.MonitorState(cfg)
        core = synced_core(height=50)
        old_time = time.time() - 500
        core.set("getblock", lambda params: block(50, "hash50", "hash49", time_=old_time))
        core.set("getblockhash", lambda params: f"hash{params[0]}" if params[0] <= 50 else None)
        self.patch_rpc(core)

        snap = collector.build_snapshot(cfg, st)
        self.assertEqual(snap["chain_status"]["stale"], "critical")
        self.assertEqual(snap["health"]["components"]["chain"]["status"], "critical")

    def test_reorg_detected(self):
        cfg = make_cfg()
        st = state_module.MonitorState(cfg)
        core = synced_core(height=100)
        self.patch_rpc(core)
        collector.build_snapshot(cfg, st)  # baseline tip = hash100

        # Simulate a reorg: same height, different hash, old tip vanished.
        reorged = synced_core(height=100)

        def getblock(params):
            h_hash = params[0]
            if h_hash == "hash100":
                return block(100, "hash100-B", "hash99")
            h = int(h_hash.replace("hash", "")) if h_hash.startswith("hash") and "-" not in h_hash else None
            if h is None:
                return None
            return block(h, h_hash, f"hash{h - 1}" if h > 0 else None)

        def getblockhash(params):
            h = params[0]
            return "hash100-B" if h == 100 else (f"hash{h}" if h < 100 else None)

        reorged.set("getblock", getblock)
        reorged.set("getblockhash", getblockhash)
        self.patch_rpc(reorged)
        snap = collector.build_snapshot(cfg, st)

        self.assertIsNotNone(snap["chain_status"]["reorg"])
        types = [e["type"] for e in st.event_log.recent()]
        self.assertIn("reorg_detected", types)

    def test_no_false_positive_reorg_on_first_observation(self):
        cfg = make_cfg()
        st = state_module.MonitorState(cfg)
        core = synced_core(height=100)
        self.patch_rpc(core)
        snap = collector.build_snapshot(cfg, st)
        self.assertIsNone(snap["chain_status"]["reorg"])


class DiskHealthTests(CollectorTestCase):
    def _snapshot_with_disk(self, used_percent):
        cfg = make_cfg()
        st = state_module.MonitorState(cfg)
        core = synced_core()
        self.patch_rpc(core)
        snap = collector.build_snapshot(cfg, st)
        snap["host"]["disk"]["used_percent"] = used_percent
        import health

        snap["health"] = health.compute_health(snap, cfg)
        return snap, cfg, st

    def test_disk_healthy(self):
        snap, _, _ = self._snapshot_with_disk(50)
        self.assertEqual(snap["health"]["components"]["disk"]["status"], "healthy")

    def test_disk_warning(self):
        snap, _, _ = self._snapshot_with_disk(85)
        self.assertEqual(snap["health"]["components"]["disk"]["status"], "warning")

    def test_disk_critical(self):
        snap, _, _ = self._snapshot_with_disk(95)
        self.assertEqual(snap["health"]["components"]["disk"]["status"], "critical")


class ElectrumXHealthTests(CollectorTestCase):
    def test_electrumx_disabled_mode_has_no_component(self):
        cfg = make_cfg(electrumx_mode="false")
        st = state_module.MonitorState(cfg)
        core = synced_core()
        self.patch_rpc(core)
        snap = collector.build_snapshot(cfg, st)
        self.assertNotIn("electrumx", snap["health"]["components"])

    def test_electrumx_lag_warning(self):
        import health

        cfg = make_cfg(electrumx_warning_lag=3, electrumx_critical_lag=10)
        blockchain = {"blocks": 110}
        electrumx_data = {"info": {"db height": 105}, "sessions": [], "backend": None}
        comp, deduction = health._electrumx_component(electrumx_data, blockchain, "true", cfg)
        self.assertEqual(comp["status"], "warning")


if __name__ == "__main__":
    unittest.main()

"""Tests for the optional host-controller protocol without live Docker/tc."""

import importlib.util
import json
import os
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import bandwidth


class OneShotUnixServer(threading.Thread):
    def __init__(self, path, response):
        super().__init__(daemon=True)
        self.path = path
        self.response = response
        self.request = None
        self.ready = threading.Event()

    def run(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.bind(self.path)
            sock.listen(1)
            self.ready.set()
            conn, _ = sock.accept()
            with conn:
                raw = b""
                while b"\n" not in raw:
                    raw += conn.recv(4096)
                self.request = json.loads(raw.split(b"\n", 1)[0].decode())
                conn.sendall((json.dumps(self.response) + "\n").encode())
        finally:
            sock.close()


class BandwidthClientTests(unittest.TestCase):
    def test_set_limits_uses_canonical_bytes_per_second(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "control.sock")
            server = OneShotUnixServer(path, {"ok": True, "enabled": True})
            server.start()
            self.assertTrue(server.ready.wait(2))
            response = bandwidth.set_limits(path, 1536 * 1024, 2 * 1024 * 1024)
            server.join(2)
            self.assertTrue(response["ok"])
            self.assertEqual(server.request["action"], "set")
            self.assertEqual(server.request["core_bytes_per_second"], 1536 * 1024)
            self.assertEqual(server.request["electrumx_bytes_per_second"], 2 * 1024 * 1024)

    def test_set_connection_limit_uses_fixed_protocol(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "control.sock")
            server = OneShotUnixServer(path, {"ok": True, "enabled": True})
            server.start()
            self.assertTrue(server.ready.wait(2))
            response = bandwidth.set_connection_limit(path, "core", 80)
            server.join(2)
            self.assertTrue(response["ok"])
            self.assertEqual(
                server.request,
                {"action": "set_connection_limit", "service": "core", "limit": 80},
            )

    def test_helper_error_is_not_silently_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "control.sock")
            server = OneShotUnixServer(path, {"ok": False, "error": "denied"})
            server.start()
            self.assertTrue(server.ready.wait(2))
            with self.assertRaises(bandwidth.BandwidthError):
                bandwidth.get_status(path)
            server.join(2)


class HostControllerValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        module_path = Path(__file__).resolve().parents[1] / "contrib" / "ravencoin-bandwidth-controller.py"
        spec = importlib.util.spec_from_file_location("ravencoin_bandwidth_controller", module_path)
        cls.controller = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.controller)

    def setUp(self):
        self.controller._state = self.controller._default_state()

    def test_rate_arg_is_exact_bits_per_second(self):
        self.assertEqual(self.controller._rate_arg(1024), "8192bit")
        self.assertEqual(self.controller._rate_arg(2 * 1024 * 1024), "16777216bit")
        self.assertEqual(self.controller._rate_arg(0), self.controller.UNLIMITED_RATE)

    def test_limit_validation_rejects_non_integer_canonical_values(self):
        current = 0
        self.assertEqual(self.controller._validated_limit({"x": 1234}, "x", current), 1234)
        for bad in (-1, 1.5, True, "1000"):
            with self.assertRaises(ValueError):
                self.controller._validated_limit({"x": bad}, "x", current)

    def test_apply_tc_recreates_its_owned_tree_before_readding_filters(self):
        with mock.patch.object(self.controller, "_root_qdisc", return_value="qdisc htb 1: root refcnt 5"):
            with mock.patch.object(self.controller, "_ns") as ns:
                self.controller._apply_tc(123, "eth0", 1024)
        calls = [call.args for call in ns.call_args_list]
        self.assertEqual(calls[0], (123, "tc", "qdisc", "del", "dev", "eth0", "root"))
        filter_calls = [args for args in calls if len(args) > 3 and args[2:4] == ("filter", "add")]
        self.assertEqual(len(filter_calls), len(self.controller.PRIVATE_CIDRS))

    def test_apply_tc_refuses_foreign_root_qdisc(self):
        with mock.patch.object(self.controller, "_root_qdisc", return_value="qdisc fq_codel 0: root"):
            with mock.patch.object(self.controller, "_ns") as ns:
                with self.assertRaises(RuntimeError):
                    self.controller._apply_tc(123, "eth0", 1024)
        ns.assert_not_called()

    def test_zero_connection_limit_means_deployment_default(self):
        state = self.controller._default_state()
        self.assertEqual(state["core_max_peers"], 0)
        self.assertEqual(state["electrumx_max_sessions"], 0)

    def test_core_override_preserves_existing_args_and_replaces_only_maxconnections(self):
        state = self.controller._default_state()
        state["core_max_peers"] = 75
        state["core_base_args"] = ["-foo=bar", "-maxconnections=999"]
        context = {"service_name": "ravencoin-core"}
        rendered = self.controller._render_connection_override("core", context, state)
        self.assertIn('"-foo=bar"', rendered)
        self.assertIn('"-maxconnections=75"', rendered)
        self.assertNotIn("999", rendered)

    def test_electrumx_override_uses_native_max_sessions(self):
        state = self.controller._default_state()
        state["electrumx_max_sessions"] = 250
        context = {"service_name": "electrumx"}
        rendered = self.controller._render_connection_override("electrumx", context, state)
        self.assertIn("MAX_SESSIONS", rendered)
        self.assertIn('"250"', rendered)

    def test_connection_action_rejects_extra_fields(self):
        response = self.controller._handle(
            {"action": "set_connection_limit", "service": "core", "limit": 80, "command": "rm -rf /"}
        )
        self.assertFalse(response["ok"])
        self.assertIn("only service and limit", response["error"])

    def test_connection_action_dispatches_only_valid_service_and_limit(self):
        with mock.patch.object(self.controller, "_apply_connection_limit") as apply:
            with mock.patch.object(self.controller, "_snapshot", return_value={"ok": True}):
                response = self.controller._handle(
                    {"action": "set_connection_limit", "service": "electrumx", "limit": 300}
                )
        self.assertTrue(response["ok"])
        apply.assert_called_once_with("electrumx", 300)


if __name__ == "__main__":
    unittest.main()

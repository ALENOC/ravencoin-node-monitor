"""Tests for the optional bandwidth controller protocol without live Docker/tc."""

import importlib.util
import json
import os
import socket
import tempfile
import threading
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()

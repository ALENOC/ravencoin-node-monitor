"""HTTP tests for the write-capable bandwidth control endpoint."""

import base64
import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

os.environ.setdefault("ELECTRUMX_ENABLED", "false")
os.environ.setdefault("HISTORY_STORAGE", "memory")

import server  # noqa: E402


def start_server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, httpd.server_address[1]


def request(port, method="GET", payload=None, headers=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/bandwidth",
        data=data,
        method=method,
        headers=headers or {},
    )
    try:
        response = urllib.request.urlopen(req, timeout=5)
        return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


class BandwidthServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd, cls.port = start_server()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def setUp(self):
        self.old_enabled = server.cfg.bandwidth_control_enabled
        self.old_user = server.cfg.monitor_user
        self.old_password = server.cfg.monitor_password
        server.cfg.bandwidth_control_enabled = True
        server.cfg.monitor_user = "monitor"
        server.cfg.monitor_password = "secret"
        self.auth = "Basic " + base64.b64encode(b"monitor:secret").decode()
        self.helper_status = {
            "ok": True,
            "enabled": True,
            "max_bytes_per_second": 10 * 1024 * 1024 * 1024,
            "services": {
                "core": {"status": "active", "limit_bytes_per_second": 0},
                "electrumx": {"status": "active", "limit_bytes_per_second": 0},
            },
        }

    def tearDown(self):
        server.cfg.bandwidth_control_enabled = self.old_enabled
        server.cfg.monitor_user = self.old_user
        server.cfg.monitor_password = self.old_password

    def test_get_status_is_readable_when_enabled(self):
        with mock.patch.object(server.bandwidth, "get_status", return_value=dict(self.helper_status)):
            status, payload = request(self.port, headers={"Authorization": self.auth})
        self.assertEqual(status, 200)
        self.assertTrue(payload["write_enabled"])

    def test_write_requires_monitor_password_to_be_configured(self):
        server.cfg.monitor_password = None
        status, payload = request(
            self.port,
            method="POST",
            payload={"core_bytes_per_second": 1024},
            headers={"Content-Type": "application/json", server.CONTROL_HEADER: "1"},
        )
        self.assertEqual(status, 403)
        self.assertIn("MONITOR_PASSWORD", payload["error"])

    def test_write_requires_csrf_resistant_control_header(self):
        status, payload = request(
            self.port,
            method="POST",
            payload={"core_bytes_per_second": 1024},
            headers={"Content-Type": "application/json", "Authorization": self.auth},
        )
        self.assertEqual(status, 403)
        self.assertIn(server.CONTROL_HEADER, payload["error"])

    def test_decimal_ui_can_send_converted_integer_byte_rate(self):
        desired = round(1.5 * 1024 * 1024)
        result = dict(self.helper_status)
        result["services"] = {
            "core": {"status": "active", "limit_bytes_per_second": desired},
            "electrumx": {"status": "active", "limit_bytes_per_second": 0},
        }
        with (
            mock.patch.object(server.bandwidth, "get_status", return_value=dict(self.helper_status)),
            mock.patch.object(server.bandwidth, "set_limits", return_value=result) as setter,
        ):
            status, payload = request(
                self.port,
                method="POST",
                payload={"core_bytes_per_second": desired},
                headers={
                    "Content-Type": "application/json",
                    "Authorization": self.auth,
                    server.CONTROL_HEADER: "1",
                },
            )
        self.assertEqual(status, 200)
        setter.assert_called_once()
        args = setter.call_args.args
        self.assertEqual(args[1], desired)
        self.assertEqual(args[2], 0)
        self.assertEqual(payload["services"]["core"]["limit_bytes_per_second"], desired)

    def test_unknown_fields_are_rejected(self):
        status, _ = request(
            self.port,
            method="POST",
            payload={"command": "rm -rf /"},
            headers={
                "Content-Type": "application/json",
                "Authorization": self.auth,
                server.CONTROL_HEADER: "1",
            },
        )
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()

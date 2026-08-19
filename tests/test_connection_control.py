import base64
import json
import re
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

import control_server
import server


def start_server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), control_server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, httpd.server_address[1]


def request(port, path, method="GET", payload=None, headers=None):
    data = None if payload is None else json.dumps(payload).encode()
    request_headers = dict(headers or {})
    if payload is not None:
        request_headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        headers=request_headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, response.read().decode(), dict(response.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(), dict(exc.headers)


def auth_header(user="monitor", password="secret"):
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def helper_snapshot(core_limit=0, ex_limit=0):
    return {
        "ok": True,
        "connections": {
            "max_limit": 10000,
            "zero_means": "deployment_default",
            "services": {
                "core": {
                    "configured_limit": core_limit,
                    "managed": core_limit > 0,
                    "running_limit": core_limit or None,
                    "applied": True,
                    "compose_managed": True,
                    "restart_required": True,
                    "status": "active",
                    "error": None,
                },
                "electrumx": {
                    "configured_limit": ex_limit,
                    "managed": ex_limit > 0,
                    "running_limit": ex_limit or None,
                    "applied": True,
                    "compose_managed": True,
                    "restart_required": True,
                    "status": "active",
                    "error": None,
                },
            },
        },
    }


class ConnectionControlHttpTests(unittest.TestCase):
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
        server.cfg.monitor_password = None
        with server._state_lock:
            server._state["network"] = {"connections": 47}
            server._state["electrumx"] = {"sessions": [{}, {}, {}]}

    def tearDown(self):
        server.cfg.bandwidth_control_enabled = self.old_enabled
        server.cfg.monitor_user = self.old_user
        server.cfg.monitor_password = self.old_password

    def test_dashboard_loads_connection_script_with_same_nonce(self):
        status, body, headers = request(self.port, "/")
        self.assertEqual(status, 200)
        self.assertIn('src="/static/network-traffic.js?v=0.4.0"', body)
        self.assertIn('src="/static/connection-control.js?v=0.4.0"', body)
        tags = re.findall(r"<script\b[^>]*>", body)
        nonces = [re.search(r'nonce="([^"]+)"', tag).group(1) for tag in tags]
        self.assertEqual(len(set(nonces)), 1)
        self.assertIn(f"script-src 'nonce-{nonces[0]}'", headers.get("Content-Security-Policy", ""))

    def test_get_connection_status_includes_live_counts(self):
        with mock.patch.object(control_server.bandwidth, "get_status", return_value=helper_snapshot(80, 250)):
            status, body, _ = request(self.port, "/api/connections")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["services"]["core"]["current_connections"], 47)
        self.assertEqual(data["services"]["electrumx"]["current_connections"], 3)
        self.assertEqual(data["services"]["core"]["configured_limit"], 80)
        self.assertFalse(data["write_enabled"])

    def test_write_requires_monitor_password(self):
        headers = {"X-Ravencoin-Monitor-Control": "1"}
        status, body, _ = request(
            self.port,
            "/api/connections",
            method="POST",
            payload={"service": "core", "limit": 80, "confirm_restart": True},
            headers=headers,
        )
        self.assertEqual(status, 403)
        self.assertIn("MONITOR_PASSWORD", body)

    def test_write_requires_explicit_restart_confirmation(self):
        server.cfg.monitor_password = "secret"
        headers = auth_header()
        headers["X-Ravencoin-Monitor-Control"] = "1"
        status, body, _ = request(
            self.port,
            "/api/connections",
            method="POST",
            payload={"service": "core", "limit": 80, "confirm_restart": False},
            headers=headers,
        )
        self.assertEqual(status, 400)
        self.assertIn("confirm_restart", body)

    def test_write_rejects_arbitrary_fields(self):
        server.cfg.monitor_password = "secret"
        headers = auth_header()
        headers["X-Ravencoin-Monitor-Control"] = "1"
        status, body, _ = request(
            self.port,
            "/api/connections",
            method="POST",
            payload={"service": "core", "limit": 80, "confirm_restart": True, "command": "whoami"},
            headers=headers,
        )
        self.assertEqual(status, 400)
        self.assertIn("only service", body)

    def test_valid_write_dispatches_fixed_helper_action(self):
        server.cfg.monitor_password = "secret"
        headers = auth_header()
        headers["X-Ravencoin-Monitor-Control"] = "1"
        with mock.patch.object(
            control_server.bandwidth,
            "set_connection_limit",
            return_value=helper_snapshot(90, 0),
        ) as setter:
            status, body, _ = request(
                self.port,
                "/api/connections",
                method="POST",
                payload={"service": "core", "limit": 90, "confirm_restart": True},
                headers=headers,
            )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["services"]["core"]["configured_limit"], 90)
        setter.assert_called_once_with(
            server.cfg.bandwidth_control_socket,
            "core",
            90,
            timeout=control_server.CONNECTION_REQUEST_TIMEOUT,
        )


if __name__ == "__main__":
    unittest.main()

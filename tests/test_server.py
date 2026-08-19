"""End-to-end tests against a real ThreadingHTTPServer instance (the actual
Handler class, not a mock) - the only way to genuinely verify routing,
path-traversal defenses, Host-header policy, authentication, and status
codes match what a browser would see.
"""

import base64
import json
import os
import re
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

os.environ.setdefault("ELECTRUMX_ENABLED", "false")
os.environ.setdefault("HISTORY_STORAGE", "memory")

from tests.fake_rpc import synced_core  # noqa: E402

import rpc  # noqa: E402
import server  # noqa: E402


def start_server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, port


def request(port, path, headers=None):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        headers=headers or {},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=5)
        raw = resp.read()
        return resp.status, raw.decode("utf-8", errors="replace"), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        return (
            exc.code,
            exc.read().decode("utf-8", errors="replace"),
            dict(exc.headers),
        )


def get(port, path):
    status, body, _ = request(port, path)
    return status, body


class ServerTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd, cls.port = start_server()
        rpc._post = synced_core(height=500)
        with server._state_lock:
            server._state.update({"blockchain": {"blocks": 500}, "starting_up": False, "health": {"score": 100, "status": "healthy"}})
        # A long-lived stand-in for the real poll thread, just so
        # /healthz's liveness check (which just checks is_alive()) sees a
        # running thread without this test actually driving real polling.
        server._poll_thread = threading.Thread(target=threading.Event().wait, daemon=True)
        server._poll_thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()


class RoutingSecurityTests(ServerTestCase):
    def test_index_served(self):
        status, body = get(self.port, "/")
        self.assertEqual(status, 200)
        self.assertIn("<html", body.lower())

    def test_security_headers_and_nonce_csp(self):
        status, body, headers = request(self.port, "/")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(headers.get("Referrer-Policy"), "no-referrer")
        csp = headers.get("Content-Security-Policy", "")
        match = re.search(r'<script nonce="([^"]+)">', body)
        self.assertIsNotNone(match)
        self.assertIn(f"script-src 'nonce-{match.group(1)}'", csp)
        script_directive = next(part for part in csp.split(";") if "script-src" in part)
        self.assertNotIn("'unsafe-inline'", script_directive)

    def test_untrusted_hostname_rejected_to_block_dns_rebinding(self):
        status, body, _ = request(self.port, "/api/status", {"Host": "attacker.example"})
        self.assertEqual(status, 421)
        self.assertIn("untrusted Host", body)

    def test_explicit_allowed_hostname_accepted(self):
        old = server.cfg.monitor_allowed_hosts
        server.cfg.monitor_allowed_hosts = "node.example"
        try:
            status, _, _ = request(self.port, "/api/status", {"Host": "node.example"})
            self.assertEqual(status, 200)
        finally:
            server.cfg.monitor_allowed_hosts = old

    def test_optional_basic_auth_protects_data_endpoints(self):
        old_user = server.cfg.monitor_user
        old_password = server.cfg.monitor_password
        server.cfg.monitor_user = "alice"
        server.cfg.monitor_password = "correct horse battery staple"
        try:
            status, _, headers = request(self.port, "/api/status")
            self.assertEqual(status, 401)
            self.assertIn("Basic", headers.get("WWW-Authenticate", ""))

            token = base64.b64encode(b"alice:correct horse battery staple").decode()
            status, _, _ = request(
                self.port,
                "/api/status",
                {"Authorization": f"Basic {token}"},
            )
            self.assertEqual(status, 200)

            # Liveness/readiness remain usable by Docker/orchestrators.
            status, _, _ = request(self.port, "/healthz")
            self.assertEqual(status, 200)
        finally:
            server.cfg.monitor_user = old_user
            server.cfg.monitor_password = old_password

    def test_wrong_basic_auth_rejected(self):
        old_user = server.cfg.monitor_user
        old_password = server.cfg.monitor_password
        server.cfg.monitor_user = "monitor"
        server.cfg.monitor_password = "secret"
        try:
            token = base64.b64encode(b"monitor:wrong").decode()
            status, _, _ = request(
                self.port,
                "/metrics",
                {"Authorization": f"Basic {token}"},
            )
            self.assertEqual(status, 401)
        finally:
            server.cfg.monitor_user = old_user
            server.cfg.monitor_password = old_password

    def test_unknown_path_is_plain_404_no_traceback_leak(self):
        status, body = get(self.port, "/nope-does-not-exist")
        self.assertEqual(status, 404)
        self.assertNotIn("Traceback", body)

    def test_static_path_traversal_blocked(self):
        status, _ = get(self.port, "/static/..%2f..%2fserver.py")
        self.assertEqual(status, 404)

    def test_static_path_traversal_blocked_double_dot(self):
        status, _ = get(self.port, "/static/....//....//app/server.py")
        self.assertIn(status, (404, 400))

    def test_static_asset_serves_real_file(self):
        status, _ = get(self.port, "/static/raven-icon.png")
        self.assertEqual(status, 200)

    def test_malformed_txid_rejected(self):
        status, body = get(self.port, "/api/tx/not-a-real-txid")
        self.assertEqual(status, 404)

    def test_txid_wrong_length_rejected(self):
        status, _ = get(self.port, "/api/tx/" + "a" * 63)
        self.assertEqual(status, 404)

    def test_txid_with_injection_attempt_rejected(self):
        payload = urllib_quote("a" * 64 + "'; DROP TABLE samples; --")
        status, _ = get(self.port, "/api/tx/" + payload)
        self.assertEqual(status, 404)

    def test_history_unknown_metric_rejected(self):
        status, body = get(self.port, "/api/history?metric=" + urllib_quote("../../etc/passwd"))
        self.assertEqual(status, 400)

    def test_history_valid_metric_returns_json(self):
        status, body = get(self.port, "/api/history?metric=block_height&range=1h")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertIn("points", data)

    def test_events_endpoint_bounded(self):
        status, body = get(self.port, "/api/events?limit=999999")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertLessEqual(len(data["events"]), server.MAX_EVENTS_RESPONSE)

    def test_health_endpoint(self):
        status, body = get(self.port, "/api/health")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertIn("status", data)

    def test_healthz(self):
        status, body = get(self.port, "/healthz")
        self.assertEqual(status, 200)

    def test_metrics_endpoint_prometheus_format(self):
        status, body = get(self.port, "/metrics")
        self.assertEqual(status, 200)
        self.assertIn("# HELP", body)

    def test_diagnostics_never_contains_credentials(self):
        status, body = get(self.port, "/api/diagnostics")
        self.assertEqual(status, 200)
        lowered = body.lower()
        self.assertNotIn("rpcpassword", lowered)
        self.assertNotIn("core_password", lowered)
        self.assertNotIn("core_user", lowered)
        self.assertNotIn("monitor_password", lowered)
        self.assertNotIn("webhook_url", lowered.replace("alert_webhook_configured", ""))


def urllib_quote(s):
    import urllib.parse

    return urllib.parse.quote(s, safe="")


if __name__ == "__main__":
    unittest.main()

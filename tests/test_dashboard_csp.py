import re
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

import server


class DashboardCspTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        server._poll_thread = threading.Thread(target=threading.Event().wait, daemon=True)
        server._poll_thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def request(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=5) as response:
            return response.status, response.read().decode("utf-8", errors="replace"), dict(response.headers)

    def test_every_dashboard_script_has_same_csp_nonce(self):
        status, body, headers = self.request("/")
        self.assertEqual(status, 200)

        script_tags = re.findall(r"<script\b[^>]*>", body)
        self.assertGreaterEqual(len(script_tags), 3)
        nonces = []
        for tag in script_tags:
            match = re.search(r'nonce="([^"]+)"', tag)
            self.assertIsNotNone(match, f"script missing CSP nonce: {tag}")
            nonces.append(match.group(1))

        self.assertEqual(len(set(nonces)), 1)
        csp = headers.get("Content-Security-Policy", "")
        self.assertIn(f"script-src 'nonce-{nonces[0]}'", csp)
        script_directive = next(part for part in csp.split(";") if "script-src" in part)
        self.assertNotIn("'unsafe-inline'", script_directive)
        self.assertIn('src="/static/network-traffic.js"', body)

    def test_network_traffic_script_is_served(self):
        status, body, headers = self.request("/static/network-traffic.js")
        self.assertEqual(status, 200)
        self.assertIn("Ravencoin network traffic", body)
        self.assertIn("javascript", headers.get("Content-Type", ""))


if __name__ == "__main__":
    unittest.main()

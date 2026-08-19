"""Security-focused tests for the ElectrumX transport boundary."""

import os
import sys
import unittest
from unittest import mock

APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import electrumx  # noqa: E402


class FakeSocket:
    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.timeout = None
        self.sent = b""

    def settimeout(self, timeout):
        self.timeout = timeout

    def sendall(self, payload):
        self.sent += payload

    def recv(self, _size):
        return self.chunks.pop(0) if self.chunks else b""


class ElectrumxTransportSecurityTests(unittest.TestCase):
    def test_response_size_is_bounded(self):
        sock = FakeSocket([b"x" * 20, b"y" * 20])
        with self.assertRaisesRegex(RuntimeError, "response exceeds"):
            electrumx._request(sock, "server.version", max_response_bytes=32)

    def test_incomplete_response_is_rejected(self):
        sock = FakeSocket([b'{"id":1,"result":{} }'])
        with self.assertRaisesRegex(RuntimeError, "complete response"):
            electrumx._request(sock, "server.version", max_response_bytes=1024)

    def test_local_targets_may_use_unverified_tls(self):
        self.assertTrue(electrumx._is_localish_host("127.0.0.1"))
        self.assertTrue(electrumx._is_localish_host("192.168.1.50"))
        self.assertTrue(electrumx._is_localish_host("electrumx"))
        self.assertTrue(electrumx._is_localish_host("node.local"))

    def test_remote_fqdn_is_not_local(self):
        self.assertFalse(electrumx._is_localish_host("electrum.example.com"))
        self.assertFalse(electrumx._is_localish_host("8.8.8.8"))

    def test_unverified_remote_tls_is_rejected_before_connect(self):
        with mock.patch.object(electrumx.socket, "create_connection") as create_connection:
            with self.assertRaisesRegex(ValueError, "refusing unverified TLS"):
                electrumx.backend_info(
                    "electrum.example.com",
                    50002,
                    "electrum.example.com",
                    verify=False,
                )
            create_connection.assert_not_called()


if __name__ == "__main__":
    unittest.main()

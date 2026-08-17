#!/usr/bin/env python3
"""ravencoin-node-monitor entrypoint.

LAN-only monitoring dashboard for a Ravencoin Core node, optionally paired
with ElectrumX. Stdlib only - no third-party dependencies, so it runs
anywhere Python 3 does. Access control (keeping this off the public
internet) is the operator's responsibility: bind it to a LAN interface,
or front it with your own reverse proxy / firewall rule. See README.md.
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import collector
import config
import price as price_client

cfg = config.load()

_state_lock = threading.Lock()
_state = {"error": "starting up", "price": None}


def poll_loop():
    while True:
        started = time.time()
        try:
            snapshot = collector.build_snapshot(cfg)
        except Exception as exc:  # keep the poller alive no matter what
            snapshot = {"timestamp": time.time(), "errors": [f"collector: {exc}"]}
        with _state_lock:
            _state.update(snapshot)
        elapsed = time.time() - started
        time.sleep(max(1.0, cfg.poll_interval - elapsed))


def price_loop():
    if not cfg.price_feed_enabled:
        return
    while True:
        try:
            price = price_client.fetch_price(cfg.price_feed_symbol)
            with _state_lock:
                _state["price"] = price
        except Exception as exc:  # noqa: BLE001 - never let this kill the thread
            with _state_lock:
                _state["price_error"] = str(exc)
        time.sleep(cfg.price_poll_interval)


class Handler(BaseHTTPRequestHandler):
    server_version = "ravencoin-node-monitor/1.0"

    def log_message(self, fmt, *args):
        pass

    def _send_json(self, payload, code=200):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/api/status", "/api/status/"):
            with _state_lock:
                payload = dict(_state)
            self._send_json(payload)
            return
        if self.path in ("/", "/index.html"):
            try:
                with open(config.INDEX_HTML_PATH, "rb") as f:
                    body = f.read()
            except FileNotFoundError:
                self._send_json({"error": "dashboard UI not found"}, code=500)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()


def main():
    threading.Thread(target=poll_loop, daemon=True, name="poll").start()
    threading.Thread(target=price_loop, daemon=True, name="price").start()
    server = ThreadingHTTPServer((cfg.bind_host, cfg.bind_port), Handler)
    print(f"ravencoin-node-monitor listening on {cfg.bind_host}:{cfg.bind_port}")
    server.serve_forever()


if __name__ == "__main__":
    main()

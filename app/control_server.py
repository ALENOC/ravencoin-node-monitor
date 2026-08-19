#!/usr/bin/env python3
"""Extended HTTP entrypoint for optional host controls.

The original server remains the read-mostly monitor implementation.  This
entrypoint subclasses it only to add the authenticated connection-limit API
and one extra dashboard script.  The privileged Docker/traffic-control work
still happens exclusively in the root-owned host helper.
"""

import json
import os
import secrets
from urllib.parse import urlsplit

import bandwidth
import config
import server

APP_VERSION = "0.4.0"
MAX_CONTROL_BODY_BYTES = 4096
MAX_CONNECTION_LIMIT = 10_000
CONTROL_HEADER = "X-Ravencoin-Monitor-Control"
CONNECTION_REQUEST_TIMEOUT = max(30, int(os.environ.get("CONNECTION_CONTROL_TIMEOUT", "120")))


def _current_connection_counts():
    with server._state_lock:
        snap = dict(server._state)
    network = snap.get("network") or {}
    electrumx = snap.get("electrumx") or {}
    sessions = electrumx.get("sessions") or []
    return {
        "core": network.get("connections"),
        "electrumx": len(sessions) if snap.get("electrumx") is not None else None,
    }


def _decorate_connection_payload(helper_payload):
    connections = dict((helper_payload or {}).get("connections") or {})
    services = connections.get("services") or {}
    counts = _current_connection_counts()
    decorated = {}
    for service in ("core", "electrumx"):
        item = dict(services.get(service) or {})
        item["current_connections"] = counts.get(service)
        decorated[service] = item
    return {
        "ok": True,
        "enabled": True,
        "write_enabled": server._auth_enabled(),
        "max_limit": connections.get("max_limit", MAX_CONNECTION_LIMIT),
        "zero_means": connections.get("zero_means", "deployment_default"),
        "services": decorated,
        **({"write_disabled_reason": "MONITOR_PASSWORD is required for connection-limit changes"}
           if not server._auth_enabled() else {}),
    }


def _connection_status():
    if not server.cfg.bandwidth_control_enabled:
        return {
            "ok": True,
            "enabled": False,
            "write_enabled": False,
            "reason": "host control is disabled",
            "services": {},
        }
    try:
        payload = bandwidth.get_status(
            server.cfg.bandwidth_control_socket,
            timeout=server.cfg.bandwidth_control_timeout,
        )
    except bandwidth.BandwidthError as exc:
        return {
            "ok": False,
            "enabled": True,
            "write_enabled": server._auth_enabled(),
            "error": str(exc),
            "services": {},
        }
    return _decorate_connection_payload(payload)


class Handler(server.Handler):
    server_version = f"ravencoin-node-monitor/{APP_VERSION}"

    def _serve_extended_index(self):
        self._csp_nonce = None
        if not server._host_is_allowed(self.headers.get("Host")):
            self._send_json({"error": "untrusted Host header"}, code=421)
            return
        if not server._authorization_ok(self.headers.get("Authorization")):
            self._send_unauthorized()
            return

        try:
            with open(config.INDEX_HTML_PATH, "rb") as handle:
                body = handle.read()
        except FileNotFoundError:
            self._send_json({"error": "dashboard UI not found"}, code=500)
            return

        nonce = secrets.token_urlsafe(18)
        marker = b"<script>"
        if marker not in body:
            self._send_json({"error": "dashboard UI script marker not found"}, code=500)
            return
        body = body.replace(marker, f'<script nonce="{nonce}">'.encode())

        closing_body = b"</body>"
        if closing_body not in body:
            self._send_json({"error": "dashboard UI body marker not found"}, code=500)
            return
        scripts = (
            f'<script nonce="{nonce}" src="/static/network-traffic.js?v={APP_VERSION}"></script>\n'
            f'<script nonce="{nonce}" src="/static/connection-control.js?v={APP_VERSION}"></script>\n'
        ).encode()
        body = body.replace(closing_body, scripts + closing_body, 1)

        self._csp_nonce = nonce
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path in ("/", "/index.html"):
            self._serve_extended_index()
            return
        if path in ("/api/connections", "/api/connections/"):
            self._csp_nonce = None
            if not server._host_is_allowed(self.headers.get("Host")):
                self._send_json({"error": "untrusted Host header"}, code=421)
                return
            if not server._authorization_ok(self.headers.get("Authorization")):
                self._send_unauthorized()
                return
            self._send_json(_connection_status())
            return
        super().do_GET()

    def do_POST(self):
        path = urlsplit(self.path).path
        if path not in ("/api/connections", "/api/connections/"):
            super().do_POST()
            return

        self._csp_nonce = None
        if not server._host_is_allowed(self.headers.get("Host")):
            self._send_json({"error": "untrusted Host header"}, code=421)
            return
        if not server.cfg.bandwidth_control_enabled:
            self._send_json({"error": "host control is disabled"}, code=404)
            return
        if not server._auth_enabled():
            self._send_json({"error": "MONITOR_PASSWORD is required for connection-limit changes"}, code=403)
            return
        if not server._authorization_ok(self.headers.get("Authorization")):
            self._send_unauthorized()
            return
        if self.headers.get(CONTROL_HEADER) != "1":
            self._send_json({"error": f"missing required {CONTROL_HEADER} header"}, code=403)
            return

        content_type = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self._send_json({"error": "Content-Type must be application/json"}, code=415)
            return
        if self.headers.get("Transfer-Encoding"):
            self._send_json({"error": "chunked request bodies are not supported"}, code=400)
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._send_json({"error": "invalid Content-Length"}, code=400)
            return
        if length <= 0 or length > MAX_CONTROL_BODY_BYTES:
            self._send_json({"error": "request body is empty or too large"}, code=413)
            return

        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json({"error": "invalid JSON"}, code=400)
            return
        if not isinstance(payload, dict):
            self._send_json({"error": "request must be a JSON object"}, code=400)
            return
        if set(payload) != {"service", "limit", "confirm_restart"}:
            self._send_json({"error": "only service, limit and confirm_restart are accepted"}, code=400)
            return

        service = payload.get("service")
        limit = payload.get("limit")
        if service not in ("core", "electrumx"):
            self._send_json({"error": "service must be core or electrumx"}, code=400)
            return
        if isinstance(limit, bool) or not isinstance(limit, int) or not 0 <= limit <= MAX_CONNECTION_LIMIT:
            self._send_json({"error": f"limit must be an integer from 0 to {MAX_CONNECTION_LIMIT}"}, code=400)
            return
        if payload.get("confirm_restart") is not True:
            self._send_json({"error": "confirm_restart must be true"}, code=400)
            return

        try:
            result = bandwidth.set_connection_limit(
                server.cfg.bandwidth_control_socket,
                service,
                limit,
                timeout=CONNECTION_REQUEST_TIMEOUT,
            )
        except bandwidth.BandwidthError as exc:
            self._send_json({"error": str(exc)}, code=503)
            return
        response = _decorate_connection_payload(result)
        response["write_enabled"] = True
        self._send_json(response)


def main():
    server.APP_VERSION = APP_VERSION
    server.Handler = Handler
    server.main()


if __name__ == "__main__":
    main()

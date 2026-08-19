#!/usr/bin/env python3
"""ravencoin-node-monitor entrypoint.

LAN-only monitoring dashboard for a Ravencoin Core node, optionally paired
with ElectrumX. Stdlib only - no third-party dependencies, so it runs
anywhere Python 3 does. Access control (keeping this off the public
internet) is the operator's responsibility: bind it to a LAN interface,
or front it with your own reverse proxy / firewall rule. See README.md.
"""

import base64
import binascii
import hmac
import ipaddress
import json
import mimetypes
import os
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import collector
import config
import history as history_module
import metrics as metrics_module
import price as price_client
import privacy
import rpc
import state as state_module

APP_VERSION = "0.2.0"

TXID_RE = re.compile(r"^/api/tx/([0-9a-fA-F]{64})$")
BLOCK_RE = re.compile(r"^/api/block/([0-9a-fA-F]{64})$")
HEX64_RE = re.compile(r"^[0-9a-fA-F]{64}$")
STATIC_ASSET_RE = re.compile(r"^/static/([A-Za-z0-9_.-]+)$")

MAX_EVENTS_RESPONSE = 500

cfg = config.load()
mon_state = state_module.MonitorState(cfg)

_state_lock = threading.Lock()
_state = {"error": "starting up", "price": None}
_poll_thread = None


def poll_loop():
    while True:
        started = time.time()
        try:
            snapshot = collector.build_snapshot(cfg, mon_state)
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
            # This loop already runs at its own sensible cadence
            # (PRICE_POLL_INTERVAL), so each successful fetch is sampled
            # directly - no need to also gate it behind the main
            # collector's history-sampling interval.
            if mon_state.history is not None and price.get("last_price") is not None:
                mon_state.history.insert_sample({"price_rvn_usdt": price["last_price"]})
        except Exception as exc:  # noqa: BLE001 - never let this kill the thread
            with _state_lock:
                _state["price_error"] = str(exc)
        time.sleep(cfg.price_poll_interval)


def _diagnostics_snapshot():
    """Sanitized bundle suitable for bug reports. Never includes RPC
    credentials, webhook URLs, or any other secret - built from an
    explicit whitelist rather than dumping config/state, so a future new
    config field can never leak here by accident.
    """
    with _state_lock:
        snap = dict(_state)

    core_host = cfg.core_host
    if cfg.privacy_mode:
        core_host = privacy.mask_ip(core_host)

    return {
        "app_version": APP_VERSION,
        "generated_at": time.time(),
        "core_version": snap.get("core_version"),
        "chain_status": snap.get("chain_status"),
        "blockchain": {
            k: (snap.get("blockchain") or {}).get(k)
            for k in ("chain", "blocks", "headers", "verificationprogress", "difficulty")
        } if snap.get("blockchain") else None,
        "health": snap.get("health"),
        "config_summary": {
            "node_name": cfg.node_name,
            "core_host": core_host,
            "core_port": cfg.core_port,
            "electrumx_mode": cfg.electrumx_mode,
            "history_enabled": cfg.history_enabled,
            "history_storage": cfg.history_storage,
            "privacy_mode": cfg.privacy_mode,
            "prometheus_enabled": cfg.prometheus_enabled,
            "min_safe_core_version": cfg.min_safe_core_version or None,
            "disk_warning_percent": cfg.disk_warning_percent,
            "disk_critical_percent": cfg.disk_critical_percent,
            "electrumx_warning_lag": cfg.electrumx_warning_lag,
            "electrumx_critical_lag": cfg.electrumx_critical_lag,
            "chain_stale_warning_seconds": cfg.chain_stale_warning_seconds,
            "chain_stale_critical_seconds": cfg.chain_stale_critical_seconds,
            "alert_webhook_configured": bool(cfg.alert_webhook_url),
            "monitor_auth_configured": bool(cfg.monitor_password),
        },
        "peer_stats": snap.get("peer_stats"),
        "host": snap.get("host"),
        "electrumx": {
            "present": snap.get("electrumx") is not None,
            "info_version": ((snap.get("electrumx") or {}).get("info") or {}).get("version"),
            "client_count": len((snap.get("electrumx") or {}).get("sessions") or []) if snap.get("electrumx") else None,
        } if cfg.electrumx_mode != "false" else None,
        "recent_events": mon_state.event_log.recent(limit=50),
        "errors": snap.get("errors"),
    }


def _normalize_host(value):
    """Return a normalized hostname from an HTTP Host-style value.

    Reject userinfo, paths and malformed ports. This deliberately parses
    syntactically only; no DNS lookup is performed as part of the trust
    decision, so DNS rebinding cannot influence what is considered local.
    """
    value = (value or "").strip()
    if not value or any(ch in value for ch in "\r\n"):
        return None
    try:
        parsed = urlsplit("//" + value)
        _ = parsed.port  # force validation of a present port
    except ValueError:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    if parsed.path or parsed.query or parsed.fragment or not parsed.hostname:
        return None
    return parsed.hostname.lower().rstrip(".")


def _host_is_allowed(host_header):
    host = _normalize_host(host_header)
    if host is None:
        return False

    explicitly_allowed = {
        normalized
        for item in (cfg.monitor_allowed_hosts or "").split(",")
        if (normalized := _normalize_host(item.strip())) is not None
    }
    if host in explicitly_allowed:
        return True
    if host == "localhost" or host.endswith(".localhost"):
        return True

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # DNS names are denied unless explicitly configured. That blocks
        # attacker-controlled origins from using DNS rebinding to read the
        # local dashboard/API through a victim's browser.
        return False
    return ip.is_loopback or ip.is_private or ip.is_link_local


def _auth_enabled():
    return cfg.monitor_password not in (None, "")


def _authorization_ok(header):
    if not _auth_enabled():
        return True
    if not header or not header.startswith("Basic "):
        return False
    token = header[6:].strip()
    try:
        decoded = base64.b64decode(token, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return False
    user, sep, password = decoded.partition(":")
    if not sep:
        return False
    expected_user = cfg.monitor_user or "monitor"
    return hmac.compare_digest(user.encode(), expected_user.encode()) and hmac.compare_digest(
        password.encode(), cfg.monitor_password.encode()
    )


class Handler(BaseHTTPRequestHandler):
    server_version = f"ravencoin-node-monitor/{APP_VERSION}"
    sys_version = ""

    def log_message(self, fmt, *args):
        pass

    def end_headers(self):
        # These headers are applied to every response, including errors and
        # static assets. The dashboard itself receives a per-response CSP
        # nonce, so future accidental HTML injection cannot execute script.
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        nonce = getattr(self, "_csp_nonce", None)
        if nonce:
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
                "object-src 'none'; form-action 'none'; img-src 'self' data:; "
                "manifest-src 'self'; connect-src 'self'; font-src 'none'; "
                f"script-src 'nonce-{nonce}'; style-src 'self' 'unsafe-inline'",
            )
        else:
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; object-src 'none'",
            )
        super().end_headers()

    def _send_json(self, payload, code=200):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text, code=200, content_type="text/plain; charset=utf-8"):
        body = text.encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_unauthorized(self):
        body = json.dumps({"error": "authentication required"}).encode()
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Ravencoin Node Monitor", charset="UTF-8"')
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._csp_nonce = None
        parsed = urlsplit(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if not _host_is_allowed(self.headers.get("Host")):
            self._send_json({"error": "untrusted Host header"}, code=421)
            return

        # Keep liveness/readiness probes credential-free. Everything that
        # exposes node, peer, transaction, host or metric data is protected
        # when MONITOR_PASSWORD is configured.
        if path not in ("/healthz", "/readyz") and not _authorization_ok(
            self.headers.get("Authorization")
        ):
            self._send_unauthorized()
            return

        if path in ("/api/status", "/api/status/"):
            with _state_lock:
                payload = dict(_state)
            self._send_json(payload)
            return

        if path == "/healthz":
            alive = _poll_thread is not None and _poll_thread.is_alive()
            self._send_json({"status": "ok" if alive else "down"}, code=200 if alive else 503)
            return

        if path == "/readyz":
            with _state_lock:
                ready = bool(_state.get("blockchain")) and not _state.get("starting_up")
            self._send_json({"ready": ready}, code=200 if ready else 503)
            return

        if path == "/metrics":
            if not cfg.prometheus_enabled:
                self.send_response(404)
                self.end_headers()
                return
            with _state_lock:
                snap = dict(_state)
            self._send_text(metrics_module.render(snap), content_type="text/plain; version=0.0.4; charset=utf-8")
            return

        if path in ("/api/health", "/api/health/"):
            with _state_lock:
                health = _state.get("health", {"score": None, "status": "unknown", "components": {}, "active_alerts": []})
            self._send_json(health)
            return

        if path in ("/api/events", "/api/events/"):
            severity = query.get("severity", [None])[0]
            if severity not in (None, "info", "warning", "critical"):
                self._send_json({"error": "severity must be info, warning, or critical"}, code=400)
                return
            try:
                limit = min(MAX_EVENTS_RESPONSE, max(1, int(query.get("limit", ["100"])[0])))
            except ValueError:
                limit = 100
            self._send_json({"events": mon_state.event_log.recent(limit=limit, min_severity=severity)})
            return

        if path in ("/api/history", "/api/history/"):
            if mon_state.history is None:
                self._send_json({"error": "history is disabled or unavailable", "points": []}, code=200)
                return
            metric = query.get("metric", [None])[0]
            range_key = query.get("range", ["24h"])[0]
            if metric not in history_module.METRICS:
                self._send_json({"error": f"unknown metric, must be one of: {', '.join(history_module.METRICS)}"}, code=400)
                return
            if range_key not in history_module.RANGE_SECONDS:
                self._send_json({"error": f"unknown range, must be one of: {', '.join(history_module.RANGE_SECONDS)}"}, code=400)
                return
            points = mon_state.history.query_metric(metric, range_key)
            self._send_json({"metric": metric, "range": range_key, "points": points})
            return

        if path in ("/api/diagnostics", "/api/diagnostics/"):
            self._send_json(_diagnostics_snapshot())
            return

        tx_match = TXID_RE.match(path)
        if tx_match:
            txid = tx_match.group(1)
            blockhash = query.get("blockhash", [None])[0]
            if blockhash and not HEX64_RE.match(blockhash):
                blockhash = None
            try:
                detail = collector.get_tx_detail(cfg, txid, blockhash)
            except rpc.RpcError as exc:
                self._send_json({"error": f"transaction not found: {exc}"}, code=404)
                return
            self._send_json(detail)
            return

        block_match = BLOCK_RE.match(path)
        if block_match:
            try:
                detail = collector.get_block_detail(cfg, block_match.group(1))
            except rpc.RpcError as exc:
                self._send_json({"error": f"block not found: {exc}"}, code=404)
                return
            self._send_json(detail)
            return

        if path == "/favicon.ico":
            path = "/static/raven-icon.png"

        asset_match = STATIC_ASSET_RE.match(path)
        if asset_match:
            file_path = os.path.join(config.STATIC_DIR, asset_match.group(1))
            # the regex already forbids "/" and "..", but confirm containment too
            if not os.path.abspath(file_path).startswith(os.path.abspath(config.STATIC_DIR) + os.sep):
                self.send_response(404)
                self.end_headers()
                return
            try:
                with open(file_path, "rb") as f:
                    body = f.read()
            except FileNotFoundError:
                self.send_response(404)
                self.end_headers()
                return
            content_type, _ = mimetypes.guess_type(file_path)
            self.send_response(200)
            self.send_header("Content-Type", content_type or "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(body)
            return

        if path in ("/", "/index.html"):
            try:
                with open(config.INDEX_HTML_PATH, "rb") as f:
                    body = f.read()
            except FileNotFoundError:
                self._send_json({"error": "dashboard UI not found"}, code=500)
                return
            nonce = secrets.token_urlsafe(18)
            marker = b"<script>"
            if marker not in body:
                self._send_json({"error": "dashboard UI script marker not found"}, code=500)
                return
            body = body.replace(marker, f'<script nonce="{nonce}">'.encode(), 1)
            self._csp_nonce = nonce
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
    global _poll_thread
    _poll_thread = threading.Thread(target=poll_loop, daemon=True, name="poll")
    _poll_thread.start()
    threading.Thread(target=price_loop, daemon=True, name="price").start()
    server = ThreadingHTTPServer((cfg.bind_host, cfg.bind_port), Handler)
    print(f"ravencoin-node-monitor {APP_VERSION} listening on {cfg.bind_host}:{cfg.bind_port}")
    if cfg.history_enabled:
        print(f"history: storage={cfg.history_storage} sample_interval={cfg.history_sample_interval}s retention={cfg.history_retention_hours}h")
    if _auth_enabled():
        print(f"dashboard authentication: enabled (user={cfg.monitor_user or 'monitor'})")
    server.serve_forever()


if __name__ == "__main__":
    main()

"""Minimal Ravencoin Core JSON-RPC client, stdlib only.

Talks plain HTTP JSON-RPC (the same protocol Bitcoin-derived daemons use).
Supports single calls and batched calls in one HTTP round trip.
"""

import base64
import json
import threading
import time
import urllib.error
import urllib.request
from collections import deque


class RpcError(Exception):
    pass


class RpcWarmupError(RpcError):
    """Core's RPC_IN_WARMUP (-28): still loading the block index, verifying
    blocks, rescanning, etc right after startup. Expected and transient,
    not a real failure - callers treat it differently from other errors.
    """


class LatencyTracker:
    """Rolling window of RPC round-trip times. Every call to Core already
    measures its own latency here for free - no extra "ping" RPC is ever
    issued just to measure latency.
    """

    def __init__(self, maxlen=50):
        self._lock = threading.Lock()
        self._samples = deque(maxlen=maxlen)

    def record(self, seconds):
        with self._lock:
            self._samples.append(seconds)

    def snapshot(self):
        with self._lock:
            samples = list(self._samples)
        if not samples:
            return {"current_ms": None, "avg_ms": None, "p95_ms": None, "samples": 0}
        sorted_samples = sorted(samples)
        p95_index = min(len(sorted_samples) - 1, int(len(sorted_samples) * 0.95))
        return {
            "current_ms": round(samples[-1] * 1000, 1),
            "avg_ms": round(sum(samples) / len(samples) * 1000, 1),
            "p95_ms": round(sorted_samples[p95_index] * 1000, 1),
            "samples": len(samples),
        }


latency_tracker = LatencyTracker()


def _post(cfg, payload):
    url = f"http://{cfg.core_host}:{cfg.core_port}/"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "text/plain")
    # Explicit, not just relying on urllib's default: without this, a
    # keep-alive connection can be left half-open on Core's side (visible
    # as CLOSE-WAIT there) if this process ever exits abruptly between
    # requests - a crash, an OOM-kill, a container restart. Over many such
    # events those connections can pile up and exhaust Core's HTTP work
    # queue for everyone, not just this monitor.
    req.add_header("Connection", "close")
    if cfg.core_user is not None:
        token = base64.b64encode(f"{cfg.core_user}:{cfg.core_password}".encode()).decode()
        req.add_header("Authorization", f"Basic {token}")
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=cfg.core_timeout) as resp:
            result = json.loads(resp.read())
        latency_tracker.record(time.monotonic() - started)
        return result
    except urllib.error.HTTPError as exc:
        body = exc.read()
        try:
            result = json.loads(body)
            # Core answered (even if the JSON-RPC call itself errored, e.g.
            # invalid params) - that's still a real, measurable round trip.
            latency_tracker.record(time.monotonic() - started)
            return result
        except (ValueError, TypeError):
            raise RpcError(f"HTTP {exc.code}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RpcError(str(exc.reason)) from exc
    except OSError as exc:
        # Catches bare socket timeouts/connection errors that urlopen can
        # raise without wrapping in URLError, depending on Python version.
        raise RpcError(str(exc)) from exc


def call(cfg, method, params=None):
    payload = {"jsonrpc": "1.0", "id": "rncm", "method": method, "params": params or []}
    result = _post(cfg, payload)
    error = result.get("error")
    if error:
        if isinstance(error, dict) and error.get("code") == -28:
            raise RpcWarmupError(error.get("message") or "node is starting up")
        raise RpcError(str(error))
    return result.get("result")


def call_batch(cfg, calls):
    """calls: list of (method, params) tuples.

    Returns a list of (result, error) tuples in the same order as `calls`.
    """
    payload = [
        {"jsonrpc": "1.0", "id": str(i), "method": method, "params": params or []}
        for i, (method, params) in enumerate(calls)
    ]
    if not payload:
        return []
    raw = _post(cfg, payload)
    if not isinstance(raw, list):
        raise RpcError(f"expected a batch response, got: {raw!r}")
    by_id = {str(item.get("id")): item for item in raw}
    out = []
    for i in range(len(calls)):
        item = by_id.get(str(i))
        if item is None:
            out.append((None, "missing response"))
        elif item.get("error"):
            out.append((None, str(item["error"])))
        else:
            out.append((item.get("result"), None))
    return out

"""Minimal Ravencoin Core JSON-RPC client, stdlib only.

Talks plain HTTP JSON-RPC (the same protocol Bitcoin-derived daemons use).
Supports single calls and batched calls in one HTTP round trip.
"""

import base64
import json
import urllib.error
import urllib.request


class RpcError(Exception):
    pass


class RpcWarmupError(RpcError):
    """Core's RPC_IN_WARMUP (-28): still loading the block index, verifying
    blocks, rescanning, etc right after startup. Expected and transient,
    not a real failure - callers treat it differently from other errors.
    """


def _post(cfg, payload):
    url = f"http://{cfg.core_host}:{cfg.core_port}/"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "text/plain")
    if cfg.core_user is not None:
        token = base64.b64encode(f"{cfg.core_user}:{cfg.core_password}".encode()).decode()
        req.add_header("Authorization", f"Basic {token}")
    try:
        with urllib.request.urlopen(req, timeout=cfg.core_timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read()
        try:
            return json.loads(body)
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

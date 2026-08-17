"""ElectrumX client, stdlib only.

Two distinct interfaces:
- admin RPC (default port 8000): plain newline-delimited JSON-RPC over a
  bare TCP socket, used for `getinfo` / `sessions`. Only reachable on
  ElectrumX's internal Docker network, never exposed publicly.
- the public `ssl://` protocol port (default 50002): the real Electrum
  wire protocol, wrapped in TLS. `server.ravencoin_backend` is a
  Ravencoin-specific extension that reports the backend Core node's sync
  state without needing any Core credentials at all.
"""

import json
import socket
import ssl
import time


def _request(sock, method, params=None, timeout=6):
    payload = json.dumps({"id": 1, "method": method, "params": params or []}) + "\n"
    sock.settimeout(timeout)
    sock.sendall(payload.encode())
    buf = b""
    while not buf.endswith(b"\n"):
        chunk = sock.recv(65536)
        if not chunk:
            break
        buf += chunk
    data = json.loads(buf.decode())
    if data.get("error"):
        raise RuntimeError(str(data["error"]))
    return data.get("result")


def admin_call(host, port, method, params=None, timeout=6):
    with socket.create_connection((host, port), timeout=timeout) as sock:
        return _request(sock, method, params, timeout=timeout)


def backend_info(host, port, sni, verify=False, timeout=6):
    ctx = ssl.create_default_context() if verify else ssl._create_unverified_context()
    with socket.create_connection((host, port), timeout=timeout) as raw:
        with ctx.wrap_socket(raw, server_hostname=sni) as sock:
            return _request(sock, "server.ravencoin_backend", timeout=timeout)


# Positional order of electrumx/server/session.py Session._session_data().
SESSION_FIELDS = (
    "session_id",
    "flags",
    "remote_address",
    "client",
    "protocol_version",
    "cost",
    "extra_cost",
    "unanswered_requests",
    "txs_sent",
    "sub_count",
    "recv_count",
    "recv_size",
    "send_count",
    "send_size",
    "duration",
)


def read_admin_snapshot(path, max_age):
    """Read the {"info": ..., "sessions": ..., "generated_at": ...} snapshot
    written by contrib/electrumx-admin-poller.py. Raises if the file is
    missing, malformed, or older than `max_age` seconds (a stalled poller
    should surface as "unreachable", not stale-but-silent data).
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    generated_at = data.get("generated_at")
    if generated_at is None or (time.time() - generated_at) > max_age:
        raise RuntimeError(f"admin snapshot at {path} is stale or missing a timestamp")
    return data.get("info"), parse_sessions(data.get("sessions"))


def parse_sessions(raw_sessions):
    """Turn `sessions` RPC rows into dicts, dropping the admin/RPC connection
    itself (protocol_version == "RPC" is this poller's own call, not a real
    wallet).
    """
    sessions = []
    for row in raw_sessions or []:
        item = dict(zip(SESSION_FIELDS, row))
        if item.get("protocol_version") == "RPC":
            continue
        sessions.append(item)
    return sessions

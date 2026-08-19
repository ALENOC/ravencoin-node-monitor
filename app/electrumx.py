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

import ipaddress
import json
import socket
import ssl
import time

MAX_RESPONSE_BYTES = 16 * 1024 * 1024


def _request(sock, method, params=None, timeout=6, max_response_bytes=MAX_RESPONSE_BYTES):
    payload = json.dumps({"id": 1, "method": method, "params": params or []}) + "\n"
    sock.settimeout(timeout)
    sock.sendall(payload.encode())
    buf = b""
    while not buf.endswith(b"\n"):
        chunk = sock.recv(65536)
        if not chunk:
            break
        if len(buf) + len(chunk) > max_response_bytes:
            raise RuntimeError(
                f"ElectrumX response exceeds {max_response_bytes} bytes"
            )
        buf += chunk
    if not buf.endswith(b"\n"):
        raise RuntimeError("ElectrumX connection closed before a complete response")
    data = json.loads(buf.decode())
    if data.get("error"):
        raise RuntimeError(str(data["error"]))
    return data.get("result")


def admin_call(host, port, method, params=None, timeout=6):
    with socket.create_connection((host, port), timeout=timeout) as sock:
        return _request(sock, method, params, timeout=timeout)


def _is_localish_host(host):
    """Return True for targets where intentionally unverified TLS can be
    reasonable: loopback/private/link-local IP literals, localhost, mDNS
    names, and single-label Docker/LAN service names.

    Public/FQDN targets are deliberately not treated as local. This is a
    policy decision, not DNS resolution: resolving a public name before
    deciding whether certificate verification is required would itself put
    the decision behind an untrusted DNS answer.
    """
    normalized = (host or "").strip().lower().rstrip(".")
    if not normalized:
        return False
    if normalized == "localhost" or normalized.endswith(".localhost"):
        return True
    if normalized.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(normalized)
    except ValueError:
        return "." not in normalized
    return ip.is_loopback or ip.is_private or ip.is_link_local


def backend_info(host, port, sni, verify=False, timeout=6):
    if not verify and not _is_localish_host(host):
        raise ValueError(
            "refusing unverified TLS to a non-local ElectrumX host; "
            "set ELECTRUMX_SSL_VERIFY=true and use a certificate valid for "
            "ELECTRUMX_SSL_SNI"
        )

    if verify:
        ctx = ssl.create_default_context()
    else:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

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

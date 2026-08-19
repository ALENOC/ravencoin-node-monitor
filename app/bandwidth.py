"""Client for the optional host-side bandwidth controller.

The monitor container never receives CAP_NET_ADMIN or Docker socket access.
Instead it talks to a tiny root-owned host helper over a Unix-domain socket.
The helper exposes only a fixed JSON protocol for reading/applying Core and
ElectrumX upload limits.
"""

import json
import socket

MAX_RESPONSE_BYTES = 64 * 1024


class BandwidthError(RuntimeError):
    pass


def _request(socket_path, payload, timeout=2.0):
    raw = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
    chunks = []
    total = 0

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(float(timeout))
    try:
        sock.connect(socket_path)
        sock.sendall(raw)
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_RESPONSE_BYTES:
                raise BandwidthError("bandwidth helper response too large")
            chunks.append(chunk)
            if b"\n" in chunk:
                break
    except (OSError, socket.timeout) as exc:
        raise BandwidthError(f"bandwidth helper unavailable: {exc}") from exc
    finally:
        sock.close()

    data = b"".join(chunks).split(b"\n", 1)[0]
    if not data:
        raise BandwidthError("bandwidth helper returned an empty response")
    try:
        response = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BandwidthError("bandwidth helper returned invalid JSON") from exc
    if not isinstance(response, dict):
        raise BandwidthError("bandwidth helper returned an invalid response")
    if response.get("ok") is False:
        raise BandwidthError(str(response.get("error") or "bandwidth helper rejected request"))
    return response


def get_status(socket_path, timeout=2.0):
    return _request(socket_path, {"action": "status"}, timeout=timeout)


def set_limits(socket_path, core_kbps, electrumx_kbps, timeout=2.0):
    return _request(
        socket_path,
        {
            "action": "set",
            "core_kbps": core_kbps,
            "electrumx_kbps": electrumx_kbps,
        },
        timeout=timeout,
    )

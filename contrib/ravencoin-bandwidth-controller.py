#!/usr/bin/env python3
"""Minimal host-side upload shaper for ravencoin-node-monitor.

This helper is intentionally separate from the monitor container. It runs on
the Docker host, owns CAP_NET_ADMIN/root privileges there, and exposes only a
small Unix-socket JSON API. The dashboard never receives the Docker socket,
CAP_NET_ADMIN, or an arbitrary command-execution primitive.

Limits are expressed in KB/s (1 KB = 1024 bytes). A value of 0 means
unlimited. Shaping is applied to the containers' default network interfaces
with Linux HTB. RFC1918 destinations are put in an effectively-unlimited
class so Core <-> ElectrumX / Docker-LAN traffic is not throttled; public
outbound traffic uses the configured class.
"""

import json
import os
import re
import socketserver
import subprocess
import threading
import time

CORE_KEY = "core"
ELECTRUMX_KEY = "electrumx"
SERVICE_KEYS = (CORE_KEY, ELECTRUMX_KEY)
MAX_REQUEST_BYTES = 16 * 1024
UNLIMITED_RATE = "10gbit"
PRIVATE_CIDRS = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
CONTAINER_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
IFACE_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,32}$")

SOCKET_PATH = os.environ.get("BANDWIDTH_SOCKET_PATH", "/run/ravencoin-bandwidth/control.sock")
STATE_FILE = os.environ.get("BANDWIDTH_STATE_FILE", "/var/lib/ravencoin-bandwidth/limits.json")
SOCKET_GID = int(os.environ.get("BANDWIDTH_SOCKET_GID", "1000"))
APPLY_INTERVAL = max(2, int(os.environ.get("BANDWIDTH_APPLY_INTERVAL", "5")))
MAX_LIMIT_KBPS = max(1, int(os.environ.get("BANDWIDTH_MAX_KBPS", "1000000")))
CONTAINERS = {
    CORE_KEY: os.environ.get("RAVENCOIN_CORE_CONTAINER", "electrumx-ravencoin-core-1"),
    ELECTRUMX_KEY: os.environ.get("ELECTRUMX_CONTAINER", "electrumx-ravencoin-electrumx-1"),
}

for _name in CONTAINERS.values():
    if not CONTAINER_RE.fullmatch(_name):
        raise SystemExit(f"invalid configured container name: {_name!r}")

_lock = threading.RLock()
_applied = {}
_samples = {}


def _run(argv, check=True, timeout=5):
    try:
        proc = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"command failed: {argv[0]}: {exc}") from exc
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "unknown error").strip()
        raise RuntimeError(f"{argv[0]} failed ({proc.returncode}): {detail}")
    return proc


def _ns(pid, *argv, check=True):
    return _run(["nsenter", "-t", str(pid), "-n", "--", *argv], check=check)


def _container_pid(container):
    proc = _run(["docker", "inspect", "--format", "{{.State.Pid}}", container])
    try:
        pid = int(proc.stdout.strip())
    except ValueError as exc:
        raise RuntimeError(f"invalid PID returned for {container}") from exc
    if pid <= 1:
        raise RuntimeError(f"container {container} is not running")
    return pid


def _default_iface(pid):
    proc = _ns(pid, "ip", "-4", "route", "show", "default")
    match = re.search(r"\bdev\s+([^\s]+)", proc.stdout)
    if not match:
        raise RuntimeError("container has no IPv4 default route")
    iface = match.group(1).split("@", 1)[0]
    if not IFACE_RE.fullmatch(iface) or iface == "lo":
        raise RuntimeError(f"invalid default interface: {iface!r}")
    return iface


def _rate_arg(limit_kbps):
    if limit_kbps == 0:
        return UNLIMITED_RATE
    # User-facing KB/s uses 1024-byte kilobytes. tc accepts bit/s exactly.
    return f"{limit_kbps * 1024 * 8}bit"


def _apply_tc(pid, iface, limit_kbps):
    rate = _rate_arg(limit_kbps)

    # Rebuild only our root qdisc when the requested setting or container PID
    # changes. This makes the configuration deterministic and avoids stale
    # filters after container recreation.
    _ns(pid, "tc", "qdisc", "del", "dev", iface, "root", check=False)
    _ns(pid, "tc", "qdisc", "add", "dev", iface, "root", "handle", "1:", "htb", "default", "20")
    _ns(pid, "tc", "class", "add", "dev", iface, "parent", "1:", "classid", "1:1", "htb", "rate", UNLIMITED_RATE, "ceil", UNLIMITED_RATE)
    _ns(pid, "tc", "class", "add", "dev", iface, "parent", "1:1", "classid", "1:10", "htb", "rate", UNLIMITED_RATE, "ceil", UNLIMITED_RATE)
    _ns(pid, "tc", "class", "add", "dev", iface, "parent", "1:1", "classid", "1:20", "htb", "rate", rate, "ceil", rate, "burst", "64kb", "cburst", "64kb")

    # Keep Docker/LAN traffic out of the public-rate class. Unclassified
    # packets (including normal public IPv4/IPv6 traffic) use class 1:20.
    for priority, cidr in enumerate(PRIVATE_CIDRS, start=10):
        _ns(
            pid,
            "tc", "filter", "add", "dev", iface,
            "protocol", "ip", "parent", "1:", "prio", str(priority),
            "u32", "match", "ip", "dst", cidr, "flowid", "1:10",
        )


def _qdisc_present(pid, iface):
    proc = _ns(pid, "tc", "qdisc", "show", "dev", iface)
    return "htb 1:" in proc.stdout


def _class_bytes(pid, iface):
    proc = _ns(pid, "tc", "-s", "class", "show", "dev", iface)
    match = re.search(
        r"class\s+htb\s+1:20\b(?P<body>.*?)(?=\nclass\s+htb\s+|\Z)",
        proc.stdout,
        re.DOTALL,
    )
    if not match:
        return None
    sent = re.search(r"\bSent\s+(\d+)\s+bytes\b", match.group("body"))
    return int(sent.group(1)) if sent else None


def _load_state():
    default = {"core_kbps": 0, "electrumx_kbps": 0}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except FileNotFoundError:
        return default
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        print(f"warning: ignoring invalid state file: {exc}", flush=True)
        return default

    result = dict(default)
    for key in result:
        value = raw.get(key, 0) if isinstance(raw, dict) else 0
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_LIMIT_KBPS:
            print(f"warning: ignoring invalid {key} in state file", flush=True)
            value = 0
        result[key] = value
    return result


def _save_state():
    directory = os.path.dirname(STATE_FILE)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    temp_path = STATE_FILE + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(_state, handle, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temp_path, 0o600)
    os.replace(temp_path, STATE_FILE)


def _limit_for(service):
    return _state[f"{service}_kbps"]


def _ensure_service(service):
    container = CONTAINERS[service]
    limit = _limit_for(service)
    pid = _container_pid(container)
    iface = _default_iface(pid)
    desired = (pid, iface, limit)
    current = _applied.get(service)

    if current != desired or not _qdisc_present(pid, iface):
        _apply_tc(pid, iface, limit)
        _applied[service] = desired
        _samples.pop(service, None)
    return pid, iface


def _service_status(service):
    container = CONTAINERS[service]
    limit = _limit_for(service)
    status = {
        "container": container,
        "limit_kbps": limit,
        "limited": limit > 0,
        "unit": "KB/s",
        "status": "unavailable",
        "upload_bytes_per_second": None,
        "error": None,
    }
    try:
        pid, iface = _ensure_service(service)
        sent_bytes = _class_bytes(pid, iface)
        now = time.monotonic()
        previous = _samples.get(service)
        rate = None
        if sent_bytes is not None and previous is not None:
            prev_bytes, prev_time, prev_pid = previous
            elapsed = now - prev_time
            if prev_pid == pid and elapsed > 0 and sent_bytes >= prev_bytes:
                rate = (sent_bytes - prev_bytes) / elapsed
        if sent_bytes is not None:
            _samples[service] = (sent_bytes, now, pid)
        status.update({
            "status": "active",
            "pid": pid,
            "interface": iface,
            "upload_bytes_per_second": rate,
        })
    except Exception as exc:  # service state is reported, not fatal to helper
        _applied.pop(service, None)
        _samples.pop(service, None)
        status["error"] = str(exc)
    return status


def _snapshot():
    return {
        "ok": True,
        "enabled": True,
        "unit": "KB/s",
        "max_kbps": MAX_LIMIT_KBPS,
        "services": {
            CORE_KEY: _service_status(CORE_KEY),
            ELECTRUMX_KEY: _service_status(ELECTRUMX_KEY),
        },
    }


def _validated_limit(payload, key, current):
    if key not in payload:
        return current
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer KB/s value")
    if not 0 <= value <= MAX_LIMIT_KBPS:
        raise ValueError(f"{key} must be between 0 and {MAX_LIMIT_KBPS} KB/s")
    return value


def _handle(payload):
    if not isinstance(payload, dict):
        return {"ok": False, "error": "request must be a JSON object"}
    action = payload.get("action")
    with _lock:
        if action == "status":
            return _snapshot()
        if action == "set":
            try:
                core = _validated_limit(payload, "core_kbps", _state["core_kbps"])
                electrumx = _validated_limit(payload, "electrumx_kbps", _state["electrumx_kbps"])
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            _state["core_kbps"] = core
            _state["electrumx_kbps"] = electrumx
            _save_state()
            # Apply immediately; unavailable containers remain persisted and the
            # background reconciler will apply the setting when they return.
            return _snapshot()
    return {"ok": False, "error": "unknown action"}


class RequestHandler(socketserver.StreamRequestHandler):
    def handle(self):
        raw = self.rfile.readline(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            response = {"ok": False, "error": "request too large"}
        else:
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                response = {"ok": False, "error": "invalid JSON"}
            else:
                response = _handle(payload)
        self.wfile.write((json.dumps(response, separators=(",", ":")) + "\n").encode("utf-8"))


class ThreadingUnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def _prepare_socket_parent():
    parent = os.path.dirname(SOCKET_PATH)
    os.makedirs(parent, mode=0o750, exist_ok=True)
    os.chown(parent, 0, SOCKET_GID)
    os.chmod(parent, 0o750)
    if os.path.isdir(SOCKET_PATH):
        raise RuntimeError(f"socket path is a directory: {SOCKET_PATH}")
    try:
        os.unlink(SOCKET_PATH)
    except FileNotFoundError:
        pass


def _reconcile_loop():
    while True:
        with _lock:
            for service in SERVICE_KEYS:
                try:
                    _ensure_service(service)
                except Exception:
                    _applied.pop(service, None)
        time.sleep(APPLY_INTERVAL)


def main():
    global _state
    _state = _load_state()
    _prepare_socket_parent()

    # Fail early with a useful error rather than silently running without the
    # host tools required to enforce limits.
    for binary in ("docker", "nsenter", "ip", "tc"):
        _run([binary, "--help"], check=False, timeout=3)

    server = ThreadingUnixServer(SOCKET_PATH, RequestHandler)
    os.chown(SOCKET_PATH, 0, SOCKET_GID)
    os.chmod(SOCKET_PATH, 0o660)

    threading.Thread(target=_reconcile_loop, daemon=True, name="bandwidth-reconcile").start()
    print(
        f"ravencoin bandwidth controller listening on {SOCKET_PATH}; "
        f"Core={CONTAINERS[CORE_KEY]} ElectrumX={CONTAINERS[ELECTRUMX_KEY]}",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        try:
            os.unlink(SOCKET_PATH)
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    main()

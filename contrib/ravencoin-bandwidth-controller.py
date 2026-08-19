#!/usr/bin/env python3
"""Minimal host-side controller for ravencoin-node-monitor.

The helper runs on the Docker host and owns the privileges required for Linux
traffic control and narrowly-scoped Docker Compose service recreation.  The
monitor container stays unprivileged: it receives neither CAP_NET_ADMIN nor
the Docker socket and can only send a small fixed JSON protocol over a Unix
socket.

Upload limits are stored canonically as bytes/second.  Connection limits are
stored as integers; 0 means "deployment default / unmanaged", never zero
connections.  Core's max peers are applied as ``-maxconnections=N`` and
ElectrumX's client limit as ``MAX_SESSIONS=N``.  Both are native settings of
the existing applications; no Core or ElectrumX source modification is made.
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
UNLIMITED_RATE = "100gbit"
PRIVATE_CIDRS = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16")
CONTAINER_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
COMPOSE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
IFACE_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,32}$")
MAX_CONNECTION_LIMIT = 10_000

SOCKET_PATH = os.environ.get("BANDWIDTH_SOCKET_PATH", "/run/ravencoin-bandwidth/control.sock")
STATE_FILE = os.environ.get("BANDWIDTH_STATE_FILE", "/var/lib/ravencoin-bandwidth/limits.json")
SOCKET_GID = int(os.environ.get("BANDWIDTH_SOCKET_GID", "10001"))
APPLY_INTERVAL = max(2, int(os.environ.get("BANDWIDTH_APPLY_INTERVAL", "5")))
CONNECTION_RECONCILE_INTERVAL = max(15, int(os.environ.get("CONNECTION_RECONCILE_INTERVAL", "30")))
COMPOSE_TIMEOUT = max(30, int(os.environ.get("CONNECTION_COMPOSE_TIMEOUT", "120")))
MAX_LIMIT_BYTES_PER_SECOND = max(
    1,
    int(os.environ.get("BANDWIDTH_MAX_BYTES_PER_SECOND", str(10 * 1024 * 1024 * 1024))),
)
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
_last_connection_attempt = {}


def _run(argv, check=True, timeout=5, cwd=None):
    try:
        proc = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"command failed: {argv[0]}: {exc}") from exc
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "unknown error").strip()
        raise RuntimeError(f"{argv[0]} failed ({proc.returncode}): {detail}")
    return proc


def _inspect_container(container):
    proc = _run(["docker", "inspect", container])
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"docker inspect returned invalid JSON for {container}") from exc
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
        raise RuntimeError(f"docker inspect returned an unexpected result for {container}")
    return payload[0]


def _ns(pid, *argv, check=True):
    return _run(["nsenter", "-t", str(pid), "-n", "--", *argv], check=check)


def _container_pid(container):
    info = _inspect_container(container)
    try:
        pid = int((info.get("State") or {}).get("Pid", 0))
    except (TypeError, ValueError) as exc:
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


def _rate_arg(limit_bytes_per_second):
    if limit_bytes_per_second == 0:
        return UNLIMITED_RATE
    return f"{limit_bytes_per_second * 8}bit"


def _root_qdisc(pid, iface):
    return _ns(pid, "tc", "qdisc", "show", "dev", iface).stdout.strip()


def _our_qdisc_present(pid, iface):
    return "qdisc htb 1:" in _root_qdisc(pid, iface)


def _apply_tc(pid, iface, limit_bytes_per_second):
    existing = _root_qdisc(pid, iface)
    if existing and "qdisc htb 1:" not in existing and "qdisc noqueue" not in existing:
        raise RuntimeError(f"refusing to replace an existing non-default root qdisc on {iface}: {existing}")

    rate = _rate_arg(limit_bytes_per_second)
    _ns(pid, "tc", "qdisc", "replace", "dev", iface, "root", "handle", "1:", "htb", "default", "20")
    _ns(pid, "tc", "class", "replace", "dev", iface, "parent", "1:", "classid", "1:1", "htb", "rate", UNLIMITED_RATE, "ceil", UNLIMITED_RATE)
    _ns(pid, "tc", "class", "replace", "dev", iface, "parent", "1:1", "classid", "1:10", "htb", "rate", UNLIMITED_RATE, "ceil", UNLIMITED_RATE)
    _ns(pid, "tc", "class", "replace", "dev", iface, "parent", "1:1", "classid", "1:20", "htb", "rate", rate, "ceil", rate, "burst", "64kb", "cburst", "64kb")

    for priority, cidr in enumerate(PRIVATE_CIDRS, start=10):
        _ns(
            pid,
            "tc", "filter", "replace", "dev", iface,
            "protocol", "ip", "parent", "1:", "prio", str(priority),
            "u32", "match", "ip", "dst", cidr, "flowid", "1:10",
        )


def _public_class_bytes(pid, iface):
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


def _default_state():
    return {
        "core_bytes_per_second": 0,
        "electrumx_bytes_per_second": 0,
        "core_max_peers": 0,
        "electrumx_max_sessions": 0,
        "core_base_args": [],
    }


def _load_state():
    result = _default_state()
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except FileNotFoundError:
        return result
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        print(f"warning: ignoring invalid state file: {exc}", flush=True)
        return result

    if not isinstance(raw, dict):
        return result
    for service in SERVICE_KEYS:
        key = f"{service}_bytes_per_second"
        value = raw.get(key)
        if value is None and f"{service}_kbps" in raw:
            legacy = raw.get(f"{service}_kbps")
            value = legacy * 1024 if isinstance(legacy, int) and not isinstance(legacy, bool) else 0
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_LIMIT_BYTES_PER_SECOND:
            value = 0
        result[key] = value

    for key in ("core_max_peers", "electrumx_max_sessions"):
        value = raw.get(key, 0)
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_CONNECTION_LIMIT:
            value = 0
        result[key] = value

    base_args = raw.get("core_base_args", [])
    if isinstance(base_args, list) and all(isinstance(item, str) and len(item) <= 4096 for item in base_args):
        result["core_base_args"] = base_args[:128]
    return result


def _save_state(state=None):
    state = _state if state is None else state
    directory = os.path.dirname(STATE_FILE)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    temp_path = STATE_FILE + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(state, handle, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temp_path, 0o600)
    os.replace(temp_path, STATE_FILE)


def _limit_for(service):
    return _state[f"{service}_bytes_per_second"]


def _ensure_service(service):
    container = CONTAINERS[service]
    limit = _limit_for(service)
    pid = _container_pid(container)
    iface = _default_iface(pid)
    desired = (pid, iface, limit)
    if _applied.get(service) != desired or not _our_qdisc_present(pid, iface):
        _apply_tc(pid, iface, limit)
        _applied[service] = desired
        _samples.pop(service, None)
    return pid, iface


def _service_status(service):
    container = CONTAINERS[service]
    limit = _limit_for(service)
    status = {
        "container": container,
        "limit_bytes_per_second": limit,
        "limited": limit > 0,
        "status": "unavailable",
        "upload_bytes_per_second": None,
        "error": None,
    }
    try:
        pid, iface = _ensure_service(service)
        sent_bytes = _public_class_bytes(pid, iface)
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
        status.update({"status": "active", "pid": pid, "interface": iface, "upload_bytes_per_second": rate})
    except Exception as exc:
        _applied.pop(service, None)
        _samples.pop(service, None)
        status["error"] = str(exc)
    return status


def _compose_context(service):
    info = _inspect_container(CONTAINERS[service])
    labels = ((info.get("Config") or {}).get("Labels") or {})
    service_name = labels.get("com.docker.compose.service")
    project_name = labels.get("com.docker.compose.project")
    project_dir = labels.get("com.docker.compose.project.working_dir")
    config_files_raw = labels.get("com.docker.compose.project.config_files")
    if not all(isinstance(value, str) and value for value in (service_name, project_name, project_dir, config_files_raw)):
        raise RuntimeError("container is not a Docker Compose service with discoverable project metadata")
    if not COMPOSE_NAME_RE.fullmatch(service_name) or not COMPOSE_NAME_RE.fullmatch(project_name):
        raise RuntimeError("invalid Docker Compose project/service metadata")
    if any(ch in project_dir for ch in "\r\n\x00") or not os.path.isdir(project_dir):
        raise RuntimeError("Docker Compose project directory is unavailable to the controller")

    config_files = []
    for value in config_files_raw.split(","):
        value = value.strip()
        if not value or any(ch in value for ch in "\r\n\x00"):
            raise RuntimeError("invalid Docker Compose config-file metadata")
        path = value if os.path.isabs(value) else os.path.join(project_dir, value)
        path = os.path.realpath(path)
        if not os.path.isfile(path):
            raise RuntimeError(f"Docker Compose config file is unavailable: {path}")
        if path not in config_files:
            config_files.append(path)
    if not config_files:
        raise RuntimeError("Docker Compose config-file list is empty")
    return {
        "service_name": service_name,
        "project_name": project_name,
        "project_dir": project_dir,
        "config_files": config_files,
        "info": info,
    }


def _strip_core_maxconnections(args):
    result = []
    for arg in args or []:
        if isinstance(arg, str) and not re.fullmatch(r"-maxconnections(?:=.*)?", arg):
            result.append(arg)
    return result


def _running_connection_limit(service):
    info = _inspect_container(CONTAINERS[service])
    if service == CORE_KEY:
        for arg in info.get("Args") or []:
            if not isinstance(arg, str):
                continue
            match = re.fullmatch(r"-maxconnections=(\d+)", arg)
            if match:
                return int(match.group(1))
        return None

    for item in ((info.get("Config") or {}).get("Env") or []):
        if isinstance(item, str) and item.startswith("MAX_SESSIONS="):
            raw = item.split("=", 1)[1]
            return int(raw) if raw.isdigit() else None
    return None


def _connection_state_key(service):
    return "core_max_peers" if service == CORE_KEY else "electrumx_max_sessions"


def _connection_override_path(service):
    directory = os.path.dirname(STATE_FILE)
    return os.path.join(directory, f"connection-{service}.override.yml")


def _render_connection_override(service, context, state):
    service_name = context["service_name"]
    limit = state[_connection_state_key(service)]
    lines = ["services:", f"  {service_name}:"]
    if limit == 0:
        lines[-1] += " {}"
    elif service == CORE_KEY:
        args = list(state.get("core_base_args") or [])
        args = _strip_core_maxconnections(args)
        args.append(f"-maxconnections={limit}")
        lines.append("    command:")
        for arg in args:
            lines.append(f"      - {json.dumps(arg)}")
    else:
        lines.extend([
            "    environment:",
            f"      MAX_SESSIONS: {json.dumps(str(limit))}",
        ])
    return "\n".join(lines) + "\n"


def _write_private_file(path, content):
    directory = os.path.dirname(path)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(path, 0o600)


def _compose_up(context, override_path):
    argv = [
        "docker", "compose",
        "--project-name", context["project_name"],
        "--project-directory", context["project_dir"],
    ]
    files = list(context["config_files"])
    override_real = os.path.realpath(override_path)
    if override_real not in files:
        files.append(override_real)
    for path in files:
        argv.extend(["-f", path])
    argv.extend([
        "up", "-d", "--no-deps", context["service_name"],
    ])
    _run(argv, timeout=COMPOSE_TIMEOUT, cwd=context["project_dir"])


def _apply_connection_limit(service, limit, persist=True):
    if service not in SERVICE_KEYS:
        raise ValueError("unknown service")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 0 <= limit <= MAX_CONNECTION_LIMIT:
        raise ValueError(f"connection limit must be between 0 and {MAX_CONNECTION_LIMIT}")

    context = _compose_context(service)
    proposed = dict(_state)
    if service == CORE_KEY and _state.get("core_max_peers", 0) == 0 and limit > 0:
        proposed["core_base_args"] = _strip_core_maxconnections(context["info"].get("Args") or [])
    proposed[_connection_state_key(service)] = limit

    stable_path = _connection_override_path(service)
    previous = None
    previous_exists = os.path.isfile(stable_path)
    if previous_exists:
        with open(stable_path, "rb") as handle:
            previous = handle.read()
    _write_private_file(stable_path, _render_connection_override(service, context, proposed))
    try:
        _compose_up(context, stable_path)
    except Exception:
        if previous_exists:
            temp_restore = stable_path + ".restore"
            with open(temp_restore, "wb") as handle:
                handle.write(previous)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_restore, 0o600)
            os.replace(temp_restore, stable_path)
        else:
            try:
                os.unlink(stable_path)
            except FileNotFoundError:
                pass
        raise

    _state.clear()
    _state.update(proposed)
    if persist:
        _save_state()
    _applied.pop(service, None)
    _samples.pop(service, None)
    return _connection_status(service)


def _connection_status(service):
    desired = _state[_connection_state_key(service)]
    result = {
        "container": CONTAINERS[service],
        "configured_limit": desired,
        "managed": desired > 0,
        "running_limit": None,
        "applied": desired == 0,
        "compose_managed": False,
        "restart_required": True,
        "status": "unavailable",
        "error": None,
    }
    try:
        _compose_context(service)
        result["compose_managed"] = True
        running = _running_connection_limit(service)
        result["running_limit"] = running
        result["applied"] = True if desired == 0 else running == desired
        result["status"] = "active"
    except Exception as exc:
        result["error"] = str(exc)
    return result


def _connections_snapshot():
    return {
        "max_limit": MAX_CONNECTION_LIMIT,
        "zero_means": "deployment_default",
        "services": {service: _connection_status(service) for service in SERVICE_KEYS},
    }


def _snapshot():
    return {
        "ok": True,
        "enabled": True,
        "canonical_unit": "B/s",
        "max_bytes_per_second": MAX_LIMIT_BYTES_PER_SECOND,
        "services": {service: _service_status(service) for service in SERVICE_KEYS},
        "connections": _connections_snapshot(),
    }


def _validated_limit(payload, key, current):
    if key not in payload:
        return current
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer number of bytes/second")
    if not 0 <= value <= MAX_LIMIT_BYTES_PER_SECOND:
        raise ValueError(f"{key} must be between 0 and {MAX_LIMIT_BYTES_PER_SECOND} bytes/second")
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
                core = _validated_limit(payload, "core_bytes_per_second", _state["core_bytes_per_second"])
                electrumx = _validated_limit(payload, "electrumx_bytes_per_second", _state["electrumx_bytes_per_second"])
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            _state["core_bytes_per_second"] = core
            _state["electrumx_bytes_per_second"] = electrumx
            _save_state()
            return _snapshot()
        if action == "set_connection_limit":
            if set(payload) != {"action", "service", "limit"}:
                return {"ok": False, "error": "set_connection_limit accepts only service and limit"}
            service = payload.get("service")
            limit = payload.get("limit")
            if service not in SERVICE_KEYS:
                return {"ok": False, "error": "service must be core or electrumx"}
            if isinstance(limit, bool) or not isinstance(limit, int) or not 0 <= limit <= MAX_CONNECTION_LIMIT:
                return {"ok": False, "error": f"limit must be an integer from 0 to {MAX_CONNECTION_LIMIT}"}
            try:
                _apply_connection_limit(service, limit)
            except (RuntimeError, ValueError, OSError) as exc:
                return {"ok": False, "error": str(exc)}
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


def _reconcile_bandwidth_loop():
    while True:
        with _lock:
            for service in SERVICE_KEYS:
                try:
                    _ensure_service(service)
                except Exception:
                    _applied.pop(service, None)
        time.sleep(APPLY_INTERVAL)


def _reconcile_connections_loop():
    while True:
        with _lock:
            now = time.monotonic()
            for service in SERVICE_KEYS:
                desired = _state[_connection_state_key(service)]
                if desired <= 0:
                    continue
                last = _last_connection_attempt.get(service, 0)
                if now - last < CONNECTION_RECONCILE_INTERVAL:
                    continue
                try:
                    running = _running_connection_limit(service)
                except Exception:
                    continue
                if running == desired:
                    continue
                _last_connection_attempt[service] = now
                try:
                    _apply_connection_limit(service, desired)
                    print(f"reapplied {service} connection limit {desired} after container recreation", flush=True)
                except Exception as exc:
                    print(f"warning: could not reapply {service} connection limit: {exc}", flush=True)
        time.sleep(CONNECTION_RECONCILE_INTERVAL)


def main():
    global _state
    _state = _load_state()
    _prepare_socket_parent()
    for binary in ("docker", "nsenter", "ip", "tc"):
        _run([binary, "--help"], check=False, timeout=3)
    _run(["docker", "compose", "version"], timeout=10)

    server = ThreadingUnixServer(SOCKET_PATH, RequestHandler)
    os.chown(SOCKET_PATH, 0, SOCKET_GID)
    os.chmod(SOCKET_PATH, 0o660)
    threading.Thread(target=_reconcile_bandwidth_loop, daemon=True, name="bandwidth-reconcile").start()
    threading.Thread(target=_reconcile_connections_loop, daemon=True, name="connections-reconcile").start()
    print(
        f"ravencoin host controller listening on {SOCKET_PATH}; "
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

#!/usr/bin/env python3
"""Verify that the Node Monitor port is really published by Docker on the host.

This script is intentionally host-side.  A container healthcheck can prove that
127.0.0.1:8899 works *inside* the container, but it cannot prove that Docker
actually installed the host-side port mapping after a reboot.  This verifier
checks both Docker's published-port state and a real HTTP request through the
host bind.  With --repair it performs at most one controlled force-recreate of
the monitor service, then verifies again and fails closed if publication is
still broken.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Sequence


class PublishError(RuntimeError):
    pass


def _run(argv: Sequence[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(argv), cwd=cwd, check=False, capture_output=True, text=True
    )


def _inspect_ports(container: str) -> dict:
    result = _run([
        "docker", "inspect", "--format", "{{json .NetworkSettings.Ports}}", container
    ])
    if result.returncode != 0:
        raise PublishError(
            f"cannot inspect {container}: {(result.stderr or result.stdout).strip()}"
        )
    try:
        payload = json.loads(result.stdout.strip() or "{}")
    except json.JSONDecodeError as exc:
        raise PublishError("Docker returned malformed port metadata") from exc
    return payload if isinstance(payload, dict) else {}


def bindings_for_port(ports: dict, port: int) -> list[dict]:
    value = ports.get(f"{port}/tcp")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict) and item.get("HostPort")]


def _probe_health(host: str, port: int, timeout: float) -> None:
    url = f"http://{host}:{port}/healthz"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            if response.status != 200:
                raise PublishError(f"{url} returned HTTP {response.status}")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise PublishError(f"host-published health probe failed for {url}: {exc}") from exc


def verify_once(container: str, host: str, port: int, timeout: float) -> list[dict]:
    ports = _inspect_ports(container)
    bindings = bindings_for_port(ports, port)
    if not bindings:
        raise PublishError(
            f"container {container} has no host publication for {port}/tcp"
        )
    _probe_health(host, port, timeout)
    return bindings


def _compose_command(files: Sequence[str], service: str) -> list[str]:
    command = ["docker", "compose"]
    for filename in files:
        command += ["-f", filename]
    command += ["up", "-d", "--no-deps", "--force-recreate", service]
    return command


def repair_once(compose_dir: Path, compose_files: Sequence[str], service: str) -> None:
    result = _run(_compose_command(compose_files, service), cwd=compose_dir)
    if result.returncode != 0:
        raise PublishError(
            "monitor force-recreate failed: "
            + (result.stderr or result.stdout).strip()
        )


def verify_with_optional_repair(
    *,
    container: str,
    host: str,
    port: int,
    timeout: float,
    repair: bool,
    compose_dir: Path,
    compose_files: Sequence[str],
    service: str,
    wait_seconds: int,
) -> list[dict]:
    try:
        return verify_once(container, host, port, timeout)
    except PublishError:
        if not repair:
            raise

    # Exactly one repair attempt.  Never loop recreate operations indefinitely.
    repair_once(compose_dir, compose_files, service)
    deadline = time.monotonic() + max(1, wait_seconds)
    last_error: PublishError | None = None
    while time.monotonic() < deadline:
        try:
            return verify_once(container, host, port, timeout)
        except PublishError as exc:
            last_error = exc
            time.sleep(1)
    raise PublishError(
        "monitor host publication is still broken after one force-recreate: "
        f"{last_error}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", default="ravencoin-node-monitor")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8899)
    parser.add_argument("--timeout", type=float, default=4.0)
    parser.add_argument("--repair", action="store_true")
    parser.add_argument("--compose-dir", type=Path, default=Path.cwd())
    parser.add_argument("--compose-file", action="append", default=[])
    parser.add_argument("--service", default="monitor")
    parser.add_argument("--wait-seconds", type=int, default=30)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.repair and not args.compose_file:
        raise PublishError("--repair requires at least one --compose-file")
    bindings = verify_with_optional_repair(
        container=args.container,
        host=args.host,
        port=args.port,
        timeout=args.timeout,
        repair=args.repair,
        compose_dir=args.compose_dir,
        compose_files=args.compose_file,
        service=args.service,
        wait_seconds=args.wait_seconds,
    )
    rendered = ", ".join(
        f"{item.get('HostIp') or '*'}:{item.get('HostPort')}" for item in bindings
    )
    print(f"Node Monitor host publication verified: {rendered}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PublishError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)

#!/usr/bin/env python3
"""Host-side companion for locked-down ElectrumX admin RPC deployments.

ElectrumX's admin RPC (rpc://) is commonly bound to 127.0.0.1 inside its
own container - that's what lets `electrumx_rpc` work passwordless from
inside the container, and it means no sibling container can reach it,
even on the same Docker network.

If you don't want to change that binding, and don't want to hand the
monitor container access to the Docker socket, run this script on the
Docker HOST instead (outside any container - it already has legitimate
`docker exec` access to your own containers). It polls ElectrumX's admin
RPC via `docker exec` and writes a small JSON snapshot to disk. Bind-mount
that file read-only into the monitor container and point it at the file:

    ELECTRUMX_ADMIN_SOURCE=file
    ELECTRUMX_ADMIN_FILE=/data/electrumx-admin.json

Usage:
    python3 electrumx-admin-poller.py --container electrumx-1 \\
        --output /var/lib/ravencoin-node-monitor/electrumx-admin.json

See electrumx-admin-poller.service for a systemd unit example.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

# Runs inside the ElectrumX container via `docker exec`. Talks to the
# admin RPC the same way electrumx_rpc does: plain newline-delimited
# JSON-RPC over a bare TCP socket to 127.0.0.1:8000.
INLINE_SCRIPT = """
import socket, json

def call(method):
    s = socket.create_connection(("127.0.0.1", 8000), timeout=6)
    s.sendall((json.dumps({"id": 1, "method": method, "params": []}) + "\\n").encode())
    buf = b""
    while not buf.endswith(b"\\n"):
        chunk = s.recv(65536)
        if not chunk:
            break
        buf += chunk
    s.close()
    data = json.loads(buf.decode())
    if data.get("error"):
        raise RuntimeError(str(data["error"]))
    return data.get("result")

print(json.dumps({"info": call("getinfo"), "sessions": call("sessions")}))
"""


def poll_once(container):
    result = subprocess.run(
        ["docker", "exec", container, "python3", "-c", INLINE_SCRIPT],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    data = json.loads(result.stdout)
    data["generated_at"] = time.time()
    return data


def write_atomic(path, data):
    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp_path = tempfile.mkstemp(dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.chmod(tmp_path, 0o644)  # readable by the monitor container's UID; no secrets in this file
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--container", required=True, help="ElectrumX container name")
    parser.add_argument("--output", required=True, help="Path to write the JSON snapshot to")
    parser.add_argument("--interval", type=int, default=10, help="Seconds between polls")
    parser.add_argument("--once", action="store_true", help="Poll once and exit instead of looping")
    args = parser.parse_args()

    while True:
        try:
            data = poll_once(args.container)
            write_atomic(args.output, data)
        except Exception as exc:  # keep the loop alive across transient failures
            print(f"electrumx-admin-poller: {exc}", file=sys.stderr)
        if args.once:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()

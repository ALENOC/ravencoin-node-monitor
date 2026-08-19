"""Manual Prometheus text-exposition-format rendering - no client library.
No labels are ever attached to any metric here, which trivially satisfies
"never put peer IPs (or any other identifying string) into a label".
"""


def _gauge(lines, name, value, help_text):
    if value is None:
        return
    lines.append(f"# HELP {name} {help_text}")
    lines.append(f"# TYPE {name} gauge")
    lines.append(f"{name} {value}")


def render(snapshot):
    lines = []
    blockchain = snapshot.get("blockchain") or {}
    _gauge(lines, "ravencoin_block_height", blockchain.get("blocks"), "Current validated block height")

    peers = snapshot.get("peers")
    _gauge(lines, "ravencoin_peer_count", len(peers) if peers is not None else None, "Connected P2P peers")

    traffic = snapshot.get("network_traffic") or {}
    _gauge(
        lines,
        "ravencoin_p2p_download_bytes_per_second",
        traffic.get("download_bytes_per_second"),
        "Current Ravencoin Core P2P receive rate in bytes per second",
    )
    _gauge(
        lines,
        "ravencoin_p2p_upload_bytes_per_second",
        traffic.get("upload_bytes_per_second"),
        "Current Ravencoin Core P2P send rate in bytes per second",
    )
    _gauge(
        lines,
        "ravencoin_p2p_bytes_received_total",
        traffic.get("total_bytes_received"),
        "Ravencoin Core P2P bytes received since Core start",
    )
    _gauge(
        lines,
        "ravencoin_p2p_bytes_sent_total",
        traffic.get("total_bytes_sent"),
        "Ravencoin Core P2P bytes sent since Core start",
    )

    mempool = snapshot.get("mempool") or {}
    _gauge(lines, "ravencoin_mempool_transactions", mempool.get("size"), "Mempool transaction count")

    latency = snapshot.get("rpc_latency") or {}
    avg_ms = latency.get("avg_ms")
    _gauge(
        lines,
        "ravencoin_rpc_latency_seconds",
        (avg_ms / 1000) if avg_ms is not None else None,
        "Average Ravencoin Core RPC round-trip time",
    )

    host = snapshot.get("host") or {}
    disk_pct = (host.get("disk") or {}).get("used_percent")
    _gauge(
        lines,
        "ravencoin_disk_usage_ratio",
        (disk_pct / 100) if disk_pct is not None else None,
        "Root filesystem usage ratio (0-1)",
    )

    ex = snapshot.get("electrumx")
    lag = None
    if ex:
        ex_height = (ex.get("info") or {}).get("db height")
        core_height = blockchain.get("blocks")
        if ex_height is not None and core_height is not None:
            lag = max(0, core_height - ex_height)
    _gauge(lines, "ravencoin_electrumx_lag_blocks", lag, "Blocks ElectrumX is behind Core")

    health = snapshot.get("health") or {}
    _gauge(lines, "ravencoin_node_health_score", health.get("score"), "Overall node health score (0-100)")

    return "\n".join(lines) + "\n"

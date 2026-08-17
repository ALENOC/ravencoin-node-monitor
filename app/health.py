"""Deterministic node health scoring. Pure functions operating on an
already-built snapshot dict plus configured thresholds - no RPC calls here,
so this is trivially unit-testable with plain dicts.

Score starts at 100 and loses fixed, documented amounts per issue found.
Status is derived from the worst component severity, not the score alone,
so one critical component can't be hidden by an otherwise-good average.
"""

STATUS_UNKNOWN = "unknown"
STATUS_HEALTHY = "healthy"
STATUS_WARNING = "warning"
STATUS_CRITICAL = "critical"

_SEVERITY_RANK = {STATUS_HEALTHY: 0, STATUS_WARNING: 1, STATUS_CRITICAL: 2, STATUS_UNKNOWN: 0}


def _worst(*statuses):
    real = [s for s in statuses if s and s != STATUS_UNKNOWN]
    if not real:
        return STATUS_UNKNOWN
    return max(real, key=lambda s: _SEVERITY_RANK[s])


def _component(status, detail, deduction=0):
    return {"status": status, "detail": detail, "score_impact": deduction}


def _chain_component(chain_status, blockchain):
    if not blockchain or not chain_status:
        return _component(STATUS_UNKNOWN, "no chain data"), 0
    stale = chain_status.get("stale")
    if stale == "critical":
        return _component(STATUS_CRITICAL, "chain tip is very stale", 30), 30
    if stale == "warning":
        return _component(STATUS_WARNING, "chain tip is stale", 10), 10
    if chain_status.get("reorg"):
        return _component(STATUS_WARNING, "recent reorganization observed", 15), 15
    headers_ahead = chain_status.get("headers_ahead") or 0
    if headers_ahead > 2:
        return _component(STATUS_WARNING, f"{headers_ahead} headers ahead of validated blocks", 10), 10
    progress = blockchain.get("verificationprogress")
    if progress is not None and progress < 0.9999:
        return _component(STATUS_WARNING, f"syncing ({progress * 100:.2f}%)", 5), 5
    return _component(STATUS_HEALTHY, "synced, tip fresh"), 0


def _rpc_component(rpc_latency, cfg):
    if not rpc_latency or rpc_latency.get("current_ms") is None:
        return _component(STATUS_UNKNOWN, "no latency samples yet"), 0
    avg = rpc_latency.get("avg_ms") or 0
    if avg >= cfg.rpc_latency_warning_ms:
        return _component(STATUS_WARNING, f"average RPC latency {avg:.0f}ms", 10), 10
    return _component(STATUS_HEALTHY, f"average RPC latency {avg:.0f}ms"), 0


def _peers_component(peers, cfg):
    if peers is None:
        return _component(STATUS_UNKNOWN, "no peer data"), 0
    count = len(peers)
    if count == 0:
        return _component(STATUS_CRITICAL, "no peers connected", 40), 40
    if count < cfg.health_min_peers:
        return _component(STATUS_WARNING, f"only {count} peers connected", 15), 15
    return _component(STATUS_HEALTHY, f"{count} peers connected"), 0


def _disk_component(host, cfg):
    if not host:
        return _component(STATUS_UNKNOWN, "no host data"), 0
    disks = [host.get("disk")] + (host.get("extra_disks") or [])
    worst_pct = None
    for d in disks:
        if d and d.get("used_percent") is not None:
            worst_pct = d["used_percent"] if worst_pct is None else max(worst_pct, d["used_percent"])
    if worst_pct is None:
        return _component(STATUS_UNKNOWN, "no disk data"), 0
    if worst_pct >= cfg.disk_critical_percent:
        return _component(STATUS_CRITICAL, f"disk at {worst_pct:.1f}%", 30), 30
    if worst_pct >= cfg.disk_warning_percent:
        return _component(STATUS_WARNING, f"disk at {worst_pct:.1f}%", 10), 10

    swap = host.get("swap") or {}
    if swap.get("total_gb") and (swap.get("used_percent") or 0) >= 80:
        return _component(STATUS_WARNING, f"swap at {swap.get('used_percent')}%", 10), 10

    return _component(STATUS_HEALTHY, f"disk at {worst_pct:.1f}%"), 0


def _electrumx_component(electrumx_data, blockchain, electrumx_mode, cfg):
    if electrumx_mode == "false":
        return None  # not applicable in standalone-Core mode
    if not electrumx_data:
        if electrumx_mode == "true":
            return _component(STATUS_WARNING, "ElectrumX unreachable", 20), 20
        return None  # "auto" mode and not present: not applicable, not an error
    info = electrumx_data.get("info") or {}
    ex_height = info.get("db height")
    core_height = blockchain.get("blocks") if blockchain else None
    if ex_height is None or core_height is None:
        return _component(STATUS_UNKNOWN, "lag unknown"), 0
    lag = max(0, core_height - ex_height)
    if lag >= cfg.electrumx_critical_lag:
        return _component(STATUS_CRITICAL, f"{lag} blocks behind Core", 15), 15
    if lag >= cfg.electrumx_warning_lag:
        return _component(STATUS_WARNING, f"{lag} blocks behind Core", 5), 5
    return _component(STATUS_HEALTHY, f"{lag} blocks behind Core"), 0


def _mempool_component(mempool, errors):
    if mempool is None:
        return _component(STATUS_UNKNOWN, "no mempool data"), 0
    if any("mempool" in e or "getrawmempool" in e for e in (errors or [])):
        return _component(STATUS_WARNING, "mempool data partially unavailable", 5), 5
    return _component(STATUS_HEALTHY, f"{mempool.get('size', 0)} transactions"), 0


def compute_health(snapshot, cfg):
    """Returns the health dict to attach to the snapshot as `snapshot["health"]`."""
    if snapshot.get("starting_up"):
        return {
            "score": None,
            "status": STATUS_UNKNOWN,
            "components": {},
            "active_alerts": [],
            "reason": "node is starting up",
        }

    blockchain = snapshot.get("blockchain")
    if blockchain is None:
        # Core RPC is unreachable outright (not just warming up) - nothing
        # else we collected is trustworthy either.
        return {
            "score": 0,
            "status": STATUS_CRITICAL,
            "components": {"core_rpc": _component(STATUS_CRITICAL, "Ravencoin Core RPC unreachable", 100)},
            "active_alerts": ["core_unreachable"],
        }

    score = 100
    components = {}
    alerts = []

    for name, (comp, deduction) in {
        "chain": _chain_component(snapshot.get("chain_status"), blockchain),
        "rpc": _rpc_component(snapshot.get("rpc_latency"), cfg),
        "peers": _peers_component(snapshot.get("peers"), cfg),
        "disk": _disk_component(snapshot.get("host"), cfg),
        "mempool": _mempool_component(snapshot.get("mempool"), snapshot.get("errors")),
    }.items():
        components[name] = comp
        score -= deduction
        if comp["status"] == STATUS_CRITICAL:
            alerts.append(f"{name}_critical")
        elif comp["status"] == STATUS_WARNING:
            alerts.append(f"{name}_warning")

    ex_result = _electrumx_component(
        snapshot.get("electrumx"), blockchain, cfg.electrumx_mode, cfg
    )
    if ex_result is not None:
        comp, deduction = ex_result
        components["electrumx"] = comp
        score -= deduction
        if comp["status"] == STATUS_CRITICAL:
            alerts.append("electrumx_critical")
        elif comp["status"] == STATUS_WARNING:
            alerts.append("electrumx_warning")

    core_version = snapshot.get("core_version")
    if core_version is not None:
        version_label = core_version.get("version") or "unknown"
        if core_version.get("safe") is False:
            detail = f"{version_label} (below configured minimum {core_version.get('minimum_safe_version')})"
        else:
            detail = version_label
        components["core_version"] = _component(
            STATUS_HEALTHY if core_version.get("safe") is not False else STATUS_WARNING,
            detail,
        )

    score = max(0, min(100, score))
    worst = _worst(*(c["status"] for c in components.values()))
    if worst == STATUS_CRITICAL:
        status = STATUS_CRITICAL
    elif worst == STATUS_WARNING:
        status = STATUS_WARNING
    elif worst == STATUS_UNKNOWN and all(c["status"] == STATUS_UNKNOWN for c in components.values()):
        status = STATUS_UNKNOWN
    else:
        status = STATUS_HEALTHY

    return {"score": score, "status": status, "components": components, "active_alerts": alerts}

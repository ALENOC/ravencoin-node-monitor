"""Builds one status snapshot per poll cycle. The ElectrumX section is
included, hidden, or surfaced as an error depending on `electrumx_mode`
("auto" / "true" / "false") - this is what makes the dashboard adaptive
without needing two separate codebases for standalone-Core vs
Core+ElectrumX deployments.

`build_snapshot(cfg, state)` is the entry point the poll loop calls each
cycle. `state` (state.MonitorState) carries everything that needs to
survive between cycles - chain baseline, mempool classification cache,
P2P traffic baseline, event log, RAM history, alert cooldowns - and is only
ever mutated from the single poll-loop thread, so nothing in here needs a
lock of its own.
"""

import time

import assets
import chain
import electrumx as electrumx_client
import health as health_engine
import privacy
import rpc
from hoststats import get_host_stats

RECENT_BLOCKS_COUNT = 8


def _get_recent_blocks(cfg, tip_height, errors):
    if tip_height is None:
        return None
    heights = list(range(tip_height, max(tip_height - RECENT_BLOCKS_COUNT, -1), -1))
    try:
        hash_results = rpc.call_batch(cfg, [("getblockhash", [h]) for h in heights])
    except rpc.RpcError as exc:
        errors.append(f"getblockhash (batch): {exc}")
        return None
    hashes = [h for h, err in hash_results if not err and h]
    if not hashes:
        return None
    try:
        block_results = rpc.call_batch(cfg, [("getblock", [h, 1]) for h in hashes])
    except rpc.RpcError as exc:
        errors.append(f"getblock (batch): {exc}")
        return None
    blocks = []
    for block, err in block_results:
        if err or block is None:
            continue
        blocks.append(
            {
                "height": block.get("height"),
                "hash": block.get("hash"),
                "time": block.get("time"),
                "size": block.get("size"),
                "tx_count": len(block.get("tx") or []),
                "difficulty": block.get("difficulty"),
            }
        )
    return blocks


def _parse_subver(subver):
    """Split a raw P2P subversion string into (version, comment).

    Core reports this as "/Ravencoin:4.8.0/" or, with an operator-set
    uacomment (e.g. a donation address), "/Ravencoin:4.8.0(comment)/" -
    the slashes are wire-format framing, and the parenthesized part is a
    free-form comment, not part of the version. Any peer can set this to
    arbitrary text, so callers must still escape it before display.
    """
    stripped = (subver or "").strip("/")
    if not stripped:
        return None, None
    if "(" in stripped and stripped.endswith(")"):
        version, _, comment = stripped.partition("(")
        return version.strip() or None, comment[:-1].strip() or None
    return stripped, None


def _core_version_status(network, cfg):
    if not network:
        return None
    minimum = (cfg.min_safe_core_version or "").strip()
    version_display, _ = _parse_subver(network.get("subversion"))
    result = {
        "version": version_display,
        "protocol_version": network.get("protocolversion"),
        "minimum_safe_version": minimum or None,
        "safe": True,
    }
    if not minimum:
        return result
    try:
        parts = [int(p) for p in minimum.split(".")[:3]]
        while len(parts) < 3:
            parts.append(0)
        min_encoded = parts[0] * 1_000_000 + parts[1] * 10_000 + parts[2] * 100
    except ValueError:
        return result  # malformed MIN_SAFE_CORE_VERSION - don't guess, just skip the check
    running = network.get("version")
    if isinstance(running, int):
        result["safe"] = running >= min_encoded
    return result


def _collect_core(cfg, errors):
    blockchain = None
    try:
        blockchain = rpc.call(cfg, "getblockchaininfo")
    except rpc.RpcWarmupError as exc:
        # Core is still loading the block index / verifying blocks after a
        # (re)start. Every other RPC call would fail the same way right
        # now, so stop here instead of making more doomed calls.
        return {
            "starting_up": True,
            "startup_message": str(exc),
            "blockchain": None,
            "network": None,
            "net_totals": None,
            "network_hashrate": None,
            "mempool": None,
            "mempool_txs": None,
            "peers": None,
            "banned_peers": None,
            "recent_blocks": None,
            "chain_tips": None,
            "core_version": None,
            "uptime_seconds": None,
        }
    except rpc.RpcError as exc:
        errors.append(f"getblockchaininfo: {exc}")

    network = None
    try:
        network = rpc.call(cfg, "getnetworkinfo")
    except rpc.RpcError as exc:
        errors.append(f"getnetworkinfo: {exc}")

    net_totals = None
    try:
        net_totals = rpc.call(cfg, "getnettotals")
        if not isinstance(net_totals, dict):
            raise rpc.RpcError(f"malformed getnettotals result: {net_totals!r}")
    except rpc.RpcError as exc:
        errors.append(f"getnettotals: {exc}")

    mempool = None
    try:
        mempool = rpc.call(cfg, "getmempoolinfo")
    except rpc.RpcError as exc:
        errors.append(f"getmempoolinfo: {exc}")

    mempool_txs = None
    try:
        raw_mempool = rpc.call(cfg, "getrawmempool", [True])
        if not isinstance(raw_mempool, dict):
            raise rpc.RpcError(f"malformed getrawmempool result: {raw_mempool!r}")
        items = [
            {
                "txid": txid,
                "size": info.get("size"),
                "fee": info.get("fee"),
                "time": info.get("time"),
                "depends": len(info.get("depends") or []),
            }
            for txid, info in raw_mempool.items()
        ]
        items.sort(key=lambda x: x["time"] or 0, reverse=True)
        mempool_txs = items[: cfg.mempool_tx_limit]
        # Classification (kind/asset_name/...) happens in build_snapshot,
        # via the incremental cache - _collect_core stays cache-agnostic.
    except rpc.RpcError as exc:
        errors.append(f"getrawmempool: {exc}")

    peers = None
    try:
        raw_peers = rpc.call(cfg, "getpeerinfo")
        if not isinstance(raw_peers, list):
            raise rpc.RpcError(f"malformed getpeerinfo result: {raw_peers!r}")
        peers = []
        for p in raw_peers:
            subver_version, subver_comment = _parse_subver(p.get("subver"))
            peers.append({
                "addr": p.get("addr"),
                "subver": p.get("subver"),
                "subver_version": subver_version,
                "subver_comment": subver_comment,
                "inbound": p.get("inbound"),
                "conntime": p.get("conntime"),
                "pingtime": p.get("pingtime"),
                "synced_blocks": p.get("synced_blocks"),
                "bytessent": p.get("bytessent"),
                "bytesrecv": p.get("bytesrecv"),
            })
    except rpc.RpcError as exc:
        errors.append(f"getpeerinfo: {exc}")

    banned_peers = None
    try:
        raw_banned = rpc.call(cfg, "listbanned")
        if not isinstance(raw_banned, list):
            raise rpc.RpcError(f"malformed listbanned result: {raw_banned!r}")
        banned_peers = [
            {
                "address": b.get("address"),
                "banned_until": b.get("banned_until"),
                "ban_created": b.get("ban_created"),
                "ban_reason": b.get("ban_reason"),
            }
            for b in raw_banned
        ]
    except rpc.RpcError as exc:
        errors.append(f"listbanned: {exc}")

    uptime_seconds = None
    try:
        uptime_seconds = rpc.call(cfg, "uptime")
    except rpc.RpcError as exc:
        errors.append(f"uptime: {exc}")

    network_hashrate = None
    try:
        network_hashrate = rpc.call(cfg, "getnetworkhashps")
    except rpc.RpcError as exc:
        errors.append(f"getnetworkhashps: {exc}")

    recent_blocks = _get_recent_blocks(cfg, blockchain.get("blocks") if blockchain else None, errors)
    chain_tips = chain.get_chain_tips(cfg, rpc)
    core_version = _core_version_status(network, cfg)

    return {
        "starting_up": False,
        "startup_message": None,
        "blockchain": blockchain,
        "network": network,
        "net_totals": net_totals,
        "network_hashrate": network_hashrate,
        "mempool": mempool,
        "mempool_txs": mempool_txs,
        "peers": peers,
        "banned_peers": banned_peers,
        "recent_blocks": recent_blocks,
        "chain_tips": chain_tips,
        "core_version": core_version,
        "uptime_seconds": uptime_seconds,
    }


def get_tx_detail(cfg, txid, blockhash=None):
    """Full structured detail for one transaction, fetched on demand (not
    part of the periodic snapshot - keeps /api/status small even with a
    large mempool). Input amounts aren't resolved (that needs one RPC call
    per input to look up the spent output) - only what's directly on the
    raw transaction is shown.

    `blockhash`, when known (e.g. clicking a tx inside a confirmed block),
    is passed to `getrawtransaction` on the chance the node's RPC version
    supports the 3-argument form (only in Bitcoin-derived Cores newer than
    the ~0.17 lineage; some Ravencoin Core builds don't). When it's not
    accepted, or omitted, or the node doesn't run `txindex=1`, a spent
    confirmed transaction simply won't be found - that's a real limitation
    of the node, not something this falls back further from.
    """
    if blockhash:
        try:
            tx = rpc.call(cfg, "getrawtransaction", [txid, True, blockhash])
        except rpc.RpcError:
            tx = rpc.call(cfg, "getrawtransaction", [txid, True])
    else:
        tx = rpc.call(cfg, "getrawtransaction", [txid, True])

    if not isinstance(tx, dict):
        raise rpc.RpcError(f"malformed getrawtransaction result: {tx!r}")

    outputs = []
    total_rvn = 0.0
    for vout in tx.get("vout", []):
        script = vout.get("scriptPubKey") or {}
        asset = script.get("asset")
        value = vout.get("value")
        if value is not None and asset is None:
            total_rvn += value
        classification = assets.classify_vout(vout)
        outputs.append(
            {
                "n": vout.get("n"),
                "value": value,
                "addresses": script.get("addresses") or ([script["address"]] if script.get("address") else []),
                "type": script.get("type"),
                "asset": {"name": asset.get("name"), "amount": asset.get("amount")} if asset else None,
                "asset_operation": classification["asset_operation"],
                "asset_subtype": classification["asset_subtype"],
            }
        )

    inputs = [
        {"txid": vin.get("txid"), "vout": vin.get("vout"), "coinbase": "coinbase" in vin}
        for vin in tx.get("vin", [])
    ]

    return {
        "txid": tx.get("txid"),
        "size": tx.get("size"),
        "vsize": tx.get("vsize"),
        "version": tx.get("version"),
        "locktime": tx.get("locktime"),
        "confirmations": tx.get("confirmations"),
        "blockhash": tx.get("blockhash"),
        "time": tx.get("time"),
        "total_output_rvn": round(total_rvn, 8),
        "inputs": inputs,
        "outputs": outputs,
    }


def get_block_detail(cfg, blockhash):
    """Block header fields plus the list of TXIDs it contains, fetched on
    demand when a recent-blocks row is expanded. Doesn't fetch full detail
    for every transaction in the block (could be thousands) - the UI fetches
    a single transaction's detail separately (via get_tx_detail, passing
    this blockhash) only when the user drills into one.
    """
    block = rpc.call(cfg, "getblock", [blockhash, 1])
    if not isinstance(block, dict):
        raise rpc.RpcError(f"malformed getblock result: {block!r}")
    return {
        "hash": block.get("hash"),
        "height": block.get("height"),
        "time": block.get("time"),
        "size": block.get("size"),
        "difficulty": block.get("difficulty"),
        "merkleroot": block.get("merkleroot"),
        "confirmations": block.get("confirmations"),
        "txids": block.get("tx") or [],
    }


def _collect_electrumx(cfg):
    if cfg.ex_admin_source == "file":
        info, sessions = electrumx_client.read_admin_snapshot(cfg.ex_admin_file, cfg.ex_admin_max_age)
    else:
        info = electrumx_client.admin_call(cfg.ex_rpc_host, cfg.ex_rpc_port, "getinfo")
        raw_sessions = electrumx_client.admin_call(cfg.ex_rpc_host, cfg.ex_rpc_port, "sessions")
        sessions = electrumx_client.parse_sessions(raw_sessions)
    try:
        backend = electrumx_client.backend_info(
            cfg.ex_ssl_host, cfg.ex_ssl_port, cfg.ex_ssl_sni, cfg.ex_ssl_verify
        )
    except (OSError, RuntimeError, ValueError):
        backend = None
    return {"info": info, "sessions": sessions, "backend": backend}


def _peer_aggregates(peers):
    if not peers:
        return None
    inbound = sum(1 for p in peers if p.get("inbound"))
    ipv4 = ipv6 = tor = 0
    versions = {}
    for p in peers:
        addr = p.get("addr") or ""
        host = addr.rsplit(":", 1)[0] if addr.count(":") <= 1 else addr
        if host.endswith(".onion"):
            tor += 1
        elif ":" in host.strip("[]"):
            ipv6 += 1
        else:
            ipv4 += 1
        subver = p.get("subver") or "unknown"
        versions[subver] = versions.get(subver, 0) + 1
    return {
        "total": len(peers),
        "inbound": inbound,
        "outbound": len(peers) - inbound,
        "ipv4": ipv4,
        "ipv6": ipv6,
        "tor": tor,
        "version_distribution": versions,
    }


def _derive_events(state, cfg, snapshot):
    """Turns this cycle's health/status view into events, but only on
    genuine state transitions - never once per poll cycle. Returns the
    list of newly generated events (also already added to state.event_log
    and dispatched to the webhook, if configured).
    """
    new_events = []

    def emit(event_type, severity, message, metadata=None):
        event = {
            "timestamp": time.time(),
            "type": event_type,
            "severity": severity,
            "message": message,
            "metadata": metadata or {},
        }
        new_events.append(event)
        state.event_log.add(event)
        state.alert_dispatcher.maybe_send(event, cfg)

    health = snapshot.get("health") or {}
    components = health.get("components") or {}

    # Core reachability: the clearest possible transition (blockchain is
    # None only when the RPC call itself failed, not during warmup).
    core_reachable = snapshot.get("blockchain") is not None or snapshot.get("starting_up")
    t = state.transitions.check("core_reachable", core_reachable)
    if t and t["old"] is not None:
        if core_reachable:
            emit("core_recovered", "info", "Ravencoin Core RPC is reachable again")
        else:
            emit("core_unreachable", "critical", "Ravencoin Core RPC is unreachable")

    if not snapshot.get("starting_up") and snapshot.get("blockchain"):
        height = snapshot["blockchain"].get("blocks")
        if height is not None and state.last_block_height is not None and height > state.last_block_height:
            emit("new_block", "info", f"New block #{height}", {"height": height})
        state.last_block_height = height if height is not None else state.last_block_height

        uptime = snapshot.get("uptime_seconds")
        if uptime is not None:
            if state.last_uptime_seconds is not None and uptime < state.last_uptime_seconds - 5:
                emit("core_restart_detected", "warning", "Ravencoin Core restart detected")
            state.last_uptime_seconds = uptime

    peers_status = (components.get("peers") or {}).get("status")
    if peers_status:
        t = state.transitions.check("peers_status", peers_status)
        if t and t["old"] is not None:
            if peers_status in (health_engine.STATUS_WARNING, health_engine.STATUS_CRITICAL):
                emit("peer_count_low", "warning" if peers_status == "warning" else "critical", components["peers"]["detail"])
            elif peers_status == health_engine.STATUS_HEALTHY:
                emit("peer_count_recovered", "info", "Peer count back to normal")

    disk_status = (components.get("disk") or {}).get("status")
    if disk_status:
        t = state.transitions.check("disk_status", disk_status)
        if t and t["old"] is not None:
            if disk_status == health_engine.STATUS_CRITICAL:
                emit("disk_critical", "critical", components["disk"]["detail"])
            elif disk_status == health_engine.STATUS_WARNING:
                emit("disk_warning", "warning", components["disk"]["detail"])
            elif disk_status == health_engine.STATUS_HEALTHY:
                emit("disk_recovered", "info", "Disk usage back to normal")

    chain_stale = (snapshot.get("chain_status") or {}).get("stale")
    if chain_stale and chain_stale != "unknown":
        t = state.transitions.check("chain_stale", chain_stale in ("warning", "critical"))
        if t and t["old"] is not None:
            if chain_stale in ("warning", "critical"):
                emit("chain_stale", "warning" if chain_stale == "warning" else "critical", "Chain tip has not advanced recently")
            else:
                emit("chain_recovered", "info", "Chain tip is advancing normally again")

    ex_present = snapshot.get("electrumx") is not None
    if cfg.electrumx_mode != "false":
        t = state.transitions.check("electrumx_reachable", ex_present)
        if t and t["old"] is not None:
            emit("electrumx_recovered", "info", "ElectrumX is reachable again") if ex_present else emit(
                "electrumx_unreachable", "warning", "ElectrumX is unreachable"
            )
        ex_status = (components.get("electrumx") or {}).get("status")
        if ex_status:
            t = state.transitions.check("electrumx_lag_status", ex_status)
            if t and t["old"] is not None:
                if ex_status in (health_engine.STATUS_WARNING, health_engine.STATUS_CRITICAL):
                    emit("electrumx_behind", "warning", components["electrumx"]["detail"])
                elif ex_status == health_engine.STATUS_HEALTHY:
                    emit("electrumx_caught_up", "info", "ElectrumX has caught up with Core")

    return new_events


def _history_metrics(snapshot):
    blockchain = snapshot.get("blockchain") or {}
    peers = snapshot.get("peers") or []
    mempool = snapshot.get("mempool") or {}
    host = snapshot.get("host") or {}
    disk = host.get("disk") or {}
    mem = host.get("mem") or {}
    swap = host.get("swap") or {}
    load = host.get("load") or {}
    traffic = snapshot.get("network_traffic") or {}
    rpc_latency = snapshot.get("rpc_latency") or {}
    ex = snapshot.get("electrumx")
    ex_info = (ex or {}).get("info") or {}
    health = snapshot.get("health") or {}
    return {
        "block_height": blockchain.get("blocks"),
        "peer_count": len(peers) if peers else None,
        "inbound_peers": sum(1 for p in peers if p.get("inbound")) if peers else None,
        "outbound_peers": sum(1 for p in peers if not p.get("inbound")) if peers else None,
        "mempool_tx_count": mempool.get("size"),
        "mempool_size_bytes": mempool.get("bytes"),
        "network_hashrate": snapshot.get("network_hashrate"),
        "network_download_bps": traffic.get("download_bytes_per_second"),
        "network_upload_bps": traffic.get("upload_bytes_per_second"),
        "rpc_latency_ms": rpc_latency.get("current_ms"),
        "electrumx_height": ex_info.get("db height"),
        "electrumx_clients": len(ex.get("sessions") or []) if ex else None,
        "load1": load.get("1m"),
        "memory_used_percent": mem.get("used_percent"),
        "swap_used_percent": swap.get("used_percent"),
        "disk_used_percent": disk.get("used_percent"),
        "disk_free_gb": disk.get("free_gb"),
        "temperature_c": host.get("cpu_temp_c"),
        "health_score": health.get("score"),
    }


def build_snapshot(cfg, state):
    errors = []
    core = _collect_core(cfg, errors)
    starting_up = core.get("starting_up", False)
    network_traffic = state.network_traffic.update(core.pop("net_totals", None))

    if core.get("mempool_txs") is not None:
        state.mempool_cache.classify(cfg, core["mempool_txs"], errors)

    chain_status, chain_events = state.chain_monitor.observe(core.get("blockchain"), core.get("recent_blocks"))
    for ev in chain_events:
        full_event = {"timestamp": time.time(), **ev}
        state.event_log.add(full_event)
        state.alert_dispatcher.maybe_send(full_event, cfg)

    electrumx_data = None
    if cfg.electrumx_mode != "false" and not starting_up:
        try:
            electrumx_data = _collect_electrumx(cfg)
        except (OSError, RuntimeError, ValueError) as exc:
            if cfg.electrumx_mode == "true":
                errors.append(f"electrumx: {exc}")
            # "auto" mode: stay silent, just leave the section absent so the
            # UI hides it instead of showing a permanent error banner.

    snapshot = {
        "timestamp": time.time(),
        "node_name": cfg.node_name,
        "mode": "electrumx" if electrumx_data else "core",
        "host": get_host_stats(cfg.extra_disk_paths),
        "network_traffic": network_traffic,
        "electrumx": electrumx_data,
        "errors": errors,
        "chain_status": chain_status,
        "rpc_latency": rpc.latency_tracker.snapshot(),
        "peer_stats": _peer_aggregates(core.get("peers")),
        "asset_activity": assets.detect_mempool_anomalies(core.get("mempool_txs")),
        **core,
    }

    snapshot["health"] = health_engine.compute_health(snapshot, cfg)

    _derive_events(state, cfg, snapshot)

    state.maybe_sample_history(cfg, _history_metrics(snapshot))

    privacy.apply_privacy(snapshot, cfg.privacy_mode)

    return snapshot

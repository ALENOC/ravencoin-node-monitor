"""Builds one status snapshot per poll cycle. The ElectrumX section is
included, hidden, or surfaced as an error depending on `electrumx_mode`
("auto" / "true" / "false") - this is what makes the dashboard adaptive
without needing two separate codebases for standalone-Core vs
Core+ElectrumX deployments.
"""

import time

import electrumx as electrumx_client
import rpc
from hoststats import get_host_stats


def _classify_mempool_txs(cfg, items, errors):
    subset = items[: cfg.mempool_classify_limit]
    if not subset:
        return
    calls = [("getrawtransaction", [it["txid"], True]) for it in subset]
    try:
        results = rpc.call_batch(cfg, calls)
    except rpc.RpcError as exc:
        errors.append(f"getrawtransaction (batch): {exc}")
        return
    for item, (tx, err) in zip(subset, results):
        if err or tx is None:
            item["kind"] = None
            item["asset_name"] = None
            continue
        asset_name = None
        for vout in tx.get("vout", []):
            asset = (vout.get("scriptPubKey") or {}).get("asset")
            if asset:
                asset_name = asset.get("name")
                break
        item["kind"] = "ASSET" if asset_name else "RVN"
        item["asset_name"] = asset_name


def _collect_core(cfg, errors):
    blockchain = None
    try:
        blockchain = rpc.call(cfg, "getblockchaininfo")
    except rpc.RpcError as exc:
        errors.append(f"getblockchaininfo: {exc}")

    network = None
    try:
        network = rpc.call(cfg, "getnetworkinfo")
    except rpc.RpcError as exc:
        errors.append(f"getnetworkinfo: {exc}")

    mempool = None
    try:
        mempool = rpc.call(cfg, "getmempoolinfo")
    except rpc.RpcError as exc:
        errors.append(f"getmempoolinfo: {exc}")

    mempool_txs = None
    try:
        raw_mempool = rpc.call(cfg, "getrawmempool", [True])
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
        if cfg.mempool_classify:
            _classify_mempool_txs(cfg, mempool_txs, errors)
    except rpc.RpcError as exc:
        errors.append(f"getrawmempool: {exc}")

    peers = None
    try:
        raw_peers = rpc.call(cfg, "getpeerinfo")
        peers = [
            {
                "addr": p.get("addr"),
                "subver": p.get("subver"),
                "inbound": p.get("inbound"),
                "conntime": p.get("conntime"),
                "pingtime": p.get("pingtime"),
                "synced_blocks": p.get("synced_blocks"),
            }
            for p in raw_peers
        ]
    except rpc.RpcError as exc:
        errors.append(f"getpeerinfo: {exc}")

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

    return {
        "blockchain": blockchain,
        "network": network,
        "network_hashrate": network_hashrate,
        "mempool": mempool,
        "mempool_txs": mempool_txs,
        "peers": peers,
        "uptime_seconds": uptime_seconds,
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


def build_snapshot(cfg):
    errors = []
    core = _collect_core(cfg, errors)

    electrumx_data = None
    if cfg.electrumx_mode != "false":
        try:
            electrumx_data = _collect_electrumx(cfg)
        except (OSError, RuntimeError, ValueError) as exc:
            if cfg.electrumx_mode == "true":
                errors.append(f"electrumx: {exc}")
            # "auto" mode: stay silent, just leave the section absent so the
            # UI hides it instead of showing a permanent error banner.

    return {
        "timestamp": time.time(),
        "node_name": cfg.node_name,
        "mode": "electrumx" if electrumx_data else "core",
        "host": get_host_stats(),
        "electrumx": electrumx_data,
        "errors": errors,
        **core,
    }

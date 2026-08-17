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


def _collect_core(cfg, errors):
    blockchain = None
    try:
        blockchain = rpc.call(cfg, "getblockchaininfo")
    except rpc.RpcWarmupError as exc:
        # Core is still loading the block index / verifying blocks after a
        # (re)start. Every other RPC call would fail the same way right
        # now, so stop here instead of making six more doomed calls.
        return {
            "starting_up": True,
            "startup_message": str(exc),
            "blockchain": None,
            "network": None,
            "network_hashrate": None,
            "mempool": None,
            "mempool_txs": None,
            "peers": None,
            "banned_peers": None,
            "recent_blocks": None,
            "uptime_seconds": None,
        }
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

    banned_peers = None
    try:
        raw_banned = rpc.call(cfg, "listbanned")
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

    return {
        "starting_up": False,
        "startup_message": None,
        "blockchain": blockchain,
        "network": network,
        "network_hashrate": network_hashrate,
        "mempool": mempool,
        "mempool_txs": mempool_txs,
        "peers": peers,
        "banned_peers": banned_peers,
        "recent_blocks": recent_blocks,
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

    outputs = []
    total_rvn = 0.0
    for vout in tx.get("vout", []):
        script = vout.get("scriptPubKey") or {}
        asset = script.get("asset")
        value = vout.get("value")
        if value is not None and asset is None:
            total_rvn += value
        outputs.append(
            {
                "n": vout.get("n"),
                "value": value,
                "addresses": script.get("addresses") or ([script["address"]] if script.get("address") else []),
                "type": script.get("type"),
                "asset": {"name": asset.get("name"), "amount": asset.get("amount")} if asset else None,
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


def build_snapshot(cfg):
    errors = []
    core = _collect_core(cfg, errors)
    starting_up = core.get("starting_up", False)

    electrumx_data = None
    if cfg.electrumx_mode != "false" and not starting_up:
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
        "host": get_host_stats(cfg.extra_disk_paths),
        "electrumx": electrumx_data,
        "errors": errors,
        **core,
    }

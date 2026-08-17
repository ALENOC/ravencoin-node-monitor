"""Chain integrity tracking: stall detection, reorg detection, and
headers-vs-blocks lag. Reuses the block data collector.py already fetches
for the "recent blocks" card - no extra RPC calls are made here.

Single-writer design: only the poll loop thread ever calls `observe()`, so
no locking is needed inside ChainMonitor itself.
"""

import time


class ChainMonitor:
    def __init__(self, cfg):
        self.cfg = cfg
        self._last_hash = None
        self._last_height = None
        self._have_baseline = False

    def observe(self, blockchain, recent_blocks):
        """blockchain: getblockchaininfo result (or None).
        recent_blocks: the same list collector.py builds for the UI, newest
        first, each with height/hash/time/previousblockhash (or None).

        Returns (status: dict, events: list[dict]).
        """
        events = []
        now = time.time()

        if not blockchain or not recent_blocks:
            return {
                "tip_age_seconds": None,
                "stale": "unknown",
                "reorg": None,
                "headers_ahead": None,
            }, events

        tip = recent_blocks[0]
        tip_hash = tip.get("hash")
        tip_height = tip.get("height")
        tip_time = tip.get("time")
        tip_age = (now - tip_time) if tip_time else None

        # --- reorg detection ---
        reorg = None
        if self._have_baseline and self._last_hash and tip_hash != self._last_hash:
            # Did our previously observed tip stay on the chain, just get
            # buried under new blocks (normal), or did it fall off (reorg)?
            chain_hashes = {b.get("hash") for b in recent_blocks}
            if self._last_hash not in chain_hashes and (
                self._last_height is None or tip_height is None or tip_height <= self._last_height
            ):
                depth = max(1, (self._last_height or 0) - (tip_height or 0) + 1)
                reorg = {"old_tip": self._last_hash, "new_tip": tip_hash, "approx_depth": depth}
                events.append(
                    {
                        "type": "reorg_detected",
                        "severity": "warning",
                        "message": f"Chain reorganization detected (approx depth {depth})",
                        "metadata": reorg,
                    }
                )

        self._last_hash = tip_hash
        self._last_height = tip_height
        self._have_baseline = True

        # --- stale tip ---
        stale = "unknown"
        if tip_age is not None:
            if tip_age >= self.cfg.chain_stale_critical_seconds:
                stale = "critical"
            elif tip_age >= self.cfg.chain_stale_warning_seconds:
                stale = "warning"
            else:
                stale = "ok"

        # --- headers ahead of validated blocks ---
        headers = blockchain.get("headers")
        blocks = blockchain.get("blocks")
        headers_ahead = None
        if headers is not None and blocks is not None:
            headers_ahead = max(0, headers - blocks)

        return {
            "tip_age_seconds": round(tip_age) if tip_age is not None else None,
            "stale": stale,
            "reorg": reorg,
            "headers_ahead": headers_ahead,
        }, events


def get_chain_tips(cfg, rpc_module, limit=5):
    """Best-effort `getchaintips` - returns None if the RPC is unsupported
    or fails, never raises. Only the most relevant tips (active + the
    highest alternate forks) are kept to bound the response size.
    """
    try:
        tips = rpc_module.call(cfg, "getchaintips")
    except rpc_module.RpcError:
        return None
    if not isinstance(tips, list):
        return None
    active = [t for t in tips if t.get("status") == "active"]
    others = sorted(
        (t for t in tips if t.get("status") != "active"),
        key=lambda t: t.get("height", 0),
        reverse=True,
    )
    trimmed = active + others[: max(0, limit - len(active))]
    return [
        {
            "height": t.get("height"),
            "hash": t.get("hash"),
            "branch_length": t.get("branchlen"),
            "status": t.get("status"),
        }
        for t in trimmed
    ]

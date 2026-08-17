"""Incremental mempool transaction classification. Previously every poll
cycle re-fetched and re-classified every transaction currently in the
mempool via a batched `getrawtransaction`; most of those transactions are
the *same* ones already classified last cycle. This caches classification
by txid and only resolves genuinely new ones, evicting anything that has
left the mempool.

Single-writer: only the poll loop thread ever calls `classify()`.
"""

import assets
import rpc


class MempoolTxCache:
    def __init__(self, max_size=2000):
        self._entries = {}  # txid -> classification dict
        self._max_size = max_size
        self.last_resolved_count = 0  # exposed for tests / measuring RPC savings

    def classify(self, cfg, items, errors):
        """Mutates each item in `items` in place, adding kind/asset_name/
        asset_operation/asset_subtype. `items` must already have a "txid".
        """
        current_txids = {it["txid"] for it in items}
        new_items = [it for it in items if it["txid"] not in self._entries]
        subset = new_items[: cfg.mempool_classify_limit]
        self.last_resolved_count = len(subset)

        if subset:
            calls = [("getrawtransaction", [it["txid"], True]) for it in subset]
            try:
                results = rpc.call_batch(cfg, calls)
            except rpc.RpcError as exc:
                errors.append(f"getrawtransaction (batch): {exc}")
                results = [(None, str(exc))] * len(subset)
            for item, (tx, err) in zip(subset, results):
                if err or tx is None:
                    classification = {"kind": None, "asset_name": None, "asset_operation": None, "asset_subtype": None}
                else:
                    classification = assets.classify_tx(tx)
                self._entries[item["txid"]] = classification

        unresolved = {"kind": None, "asset_name": None, "asset_operation": None, "asset_subtype": None}
        for item in items:
            item.update(self._entries.get(item["txid"], unresolved))

        # Evict anything that's no longer in the mempool - a transaction
        # that leaves (mined or replaced) will never be looked up again by
        # this cache, so keeping it around is pure waste.
        for txid in list(self._entries.keys()):
            if txid not in current_txids:
                del self._entries[txid]

        # Hard size backstop in case the mempool itself is huge.
        if len(self._entries) > self._max_size:
            excess = len(self._entries) - self._max_size
            for txid in list(self._entries.keys())[:excess]:
                del self._entries[txid]

    def size(self):
        return len(self._entries)

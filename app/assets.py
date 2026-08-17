"""Ravencoin asset transaction classification, built on data Core's own
`getrawtransaction` already returns - no extra RPC calls.

Two signals are combined:
- `scriptPubKey.type`, which Core sets to one of a known, stable set of
  strings for asset-carrying outputs (issuance/reissue/transfer). If a
  node returns something outside that known set, the operation is
  reported "unknown" rather than guessed.
- the asset name's prefix/suffix, which Ravencoin's consensus rules fix
  permanently (a name ending in "!" is always an ownership token, a name
  starting with "#" is always a qualifier, etc) - safe to rely on
  unconditionally since it's a protocol rule, not an RPC implementation
  detail that could vary between Core builds.
"""

_KNOWN_OPERATIONS = {
    "new_asset": "issuance",
    "reissue_asset": "reissue",
    "transfer_asset": "transfer",
}


def _name_subtype(name):
    if not name:
        return "unknown"
    if name.endswith("!"):
        return "ownership"
    if name.startswith("#"):
        return "sub_qualifier" if "/" in name else "qualifier"
    if name.startswith("$"):
        return "restricted"
    if "#" in name:  # mid-string (leading "#" already handled above)
        return "unique"
    return "regular"


def classify_vout(vout):
    script = vout.get("scriptPubKey") or {}
    asset = script.get("asset")
    if not asset:
        return {"kind": "RVN", "asset_name": None, "asset_operation": None, "asset_subtype": None}
    name = asset.get("name")
    operation = _KNOWN_OPERATIONS.get(script.get("type"), "unknown")
    return {
        "kind": "ASSET",
        "asset_name": name,
        "asset_operation": operation,
        "asset_subtype": _name_subtype(name),
    }


def classify_tx(tx):
    """Transaction-level summary: the first asset-carrying output decides
    `kind`/`asset_name` (matches the classification shape already used
    throughout the dashboard), plus the full set of distinct operations
    seen across all outputs for transactions that touch more than one.
    """
    outputs = [classify_vout(v) for v in tx.get("vout", [])]
    asset_outputs = [o for o in outputs if o["kind"] == "ASSET"]
    if not asset_outputs:
        return {"kind": "RVN", "asset_name": None, "asset_operation": None, "asset_subtype": None, "operations": []}
    primary = asset_outputs[0]
    operations = sorted({o["asset_operation"] for o in asset_outputs if o["asset_operation"]})
    return {
        "kind": "ASSET",
        "asset_name": primary["asset_name"],
        "asset_operation": primary["asset_operation"],
        "asset_subtype": primary["asset_subtype"],
        "operations": operations,
    }


def detect_mempool_anomalies(mempool_txs, burst_threshold=20):
    """Conservative, single-signal anomaly layer: flags only a burst of
    simultaneous asset activity in the current mempool. Deliberately does
    NOT attempt "unusually large amount" detection - without a historical
    per-asset baseline that would be little more than an arbitrary
    threshold dressed up as an anomaly signal, and asset amounts vary
    enormously by design (some assets are issued in units of 1, others in
    the billions), so a single global threshold would be more noise than
    signal.
    """
    if not mempool_txs:
        return {"asset_operation_burst": False, "asset_tx_count": 0}
    asset_count = sum(1 for tx in mempool_txs if tx.get("kind") == "ASSET")
    return {
        "asset_operation_burst": asset_count >= burst_threshold,
        "asset_tx_count": asset_count,
    }

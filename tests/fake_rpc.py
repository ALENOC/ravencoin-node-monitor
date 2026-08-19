"""Programmable fake Ravencoin Core JSON-RPC responder for tests. Patches
into `rpc._post` so every layer above it (rpc.call, rpc.call_batch, and
everything collector.py/chain.py/mempool_cache.py build on top) is
exercised for real - only the actual HTTP round trip is replaced.
"""

import time

import rpc


class FakeCore:
    def __init__(self):
        self.responses = {}  # method -> value, or callable(params) -> value
        self.errors = {}  # method -> (code, message)
        self.call_log = []

    def set(self, method, value):
        self.responses[method] = value

    def set_error(self, method, code, message):
        self.errors[method] = (code, message)

    def __call__(self, cfg, payload):
        rpc.latency_tracker.record(0.001)  # stand in for the real timed round trip
        if isinstance(payload, list):
            return [self._one(item) for item in payload]
        return self._one(payload)

    def _one(self, item):
        method = item["method"]
        self.call_log.append(method)
        if method in self.errors:
            code, message = self.errors[method]
            return {"jsonrpc": "1.0", "id": item["id"], "result": None, "error": {"code": code, "message": message}}
        value = self.responses.get(method)
        if callable(value):
            value = value(item.get("params"))
        return {"jsonrpc": "1.0", "id": item["id"], "result": value, "error": None}


def block(height, block_hash, prev_hash=None, time_=None, size=300, ntx=1, difficulty=100.0):
    return {
        "height": height,
        "hash": block_hash,
        "previousblockhash": prev_hash,
        "time": time_ if time_ is not None else time.time(),
        "size": size,
        "tx": [f"tx{height}-{i}" for i in range(ntx)],
        "difficulty": difficulty,
    }


def synced_core(height=1000, connections=8):
    """A FakeCore preloaded with a normal, synced, healthy node."""
    core = FakeCore()
    core.set("getblockchaininfo", {
        "chain": "main", "blocks": height, "headers": height,
        "verificationprogress": 0.9999999, "difficulty": 100.0,
        "bestblockhash": f"hash{height}", "size_on_disk": 10_000_000_000,
    })
    core.set("getnetworkinfo", {
        "version": 4080000, "subversion": "/Ravencoin:4.8.0/", "protocolversion": 70028,
        "connections": connections,
    })
    core.set("getnettotals", {
        "totalbytesrecv": 250_000_000,
        "totalbytessent": 125_000_000,
        "timemillis": int(time.time() * 1000),
        "uploadtarget": {
            "timeframe": 86400,
            "target": 0,
            "target_reached": False,
            "serve_historical_blocks": True,
            "bytes_left_in_cycle": 0,
            "time_left_in_cycle": 0,
        },
    })
    core.set("getmempoolinfo", {"size": 0, "bytes": 0, "mempoolminfee": 0.0})
    core.set("getrawmempool", lambda params: {})
    core.set("getpeerinfo", lambda params: [
        {"addr": f"10.0.0.{i}:8767", "subver": "/Ravencoin:4.8.0/", "inbound": i % 2 == 0, "pingtime": 0.05}
        for i in range(connections)
    ])
    core.set("listbanned", [])
    core.set("uptime", 3600)
    core.set("getnetworkhashps", 1_000_000.0)

    def getblockhash(params):
        h = params[0]
        return f"hash{h}" if 0 <= h <= height else None

    def getblock(params):
        block_hash = params[0]
        h = int(block_hash.replace("hash", "")) if block_hash and block_hash.startswith("hash") else None
        if h is None:
            return None
        prev = f"hash{h - 1}" if h > 0 else None
        return block(h, block_hash, prev)

    core.set("getblockhash", getblockhash)
    core.set("getblock", getblock)
    core.set("getchaintips", [{"height": height, "hash": f"hash{height}", "branchlen": 0, "status": "active"}])
    return core

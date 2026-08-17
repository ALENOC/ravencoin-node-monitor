import unittest

from tests.fake_rpc import FakeCore

import config
import rpc
from mempool_cache import MempoolTxCache


def make_cfg():
    cfg = config.Config()
    cfg.core_host = "fake"
    cfg.core_port = 1
    cfg.mempool_classify_limit = 300
    return cfg


class MempoolCacheTests(unittest.TestCase):
    def setUp(self):
        self._orig = rpc._post
        self.addCleanup(setattr, rpc, "_post", self._orig)

    def test_only_new_txids_are_resolved(self):
        core = FakeCore()
        core.set("getrawtransaction", lambda params: {"vout": []})
        rpc._post = core
        cfg = make_cfg()
        cache = MempoolTxCache()
        errors = []

        items = [{"txid": "a"}, {"txid": "b"}]
        cache.classify(cfg, items, errors)
        self.assertEqual(cache.last_resolved_count, 2)
        first_round_calls = len(core.call_log)

        # Second cycle: same two txids still in mempool, plus one new one.
        items2 = [{"txid": "a"}, {"txid": "b"}, {"txid": "c"}]
        cache.classify(cfg, items2, errors)
        self.assertEqual(cache.last_resolved_count, 1)  # only "c" is new
        self.assertEqual(len(core.call_log) - first_round_calls, 1)

    def test_evicts_txids_no_longer_in_mempool(self):
        core = FakeCore()
        core.set("getrawtransaction", lambda params: {"vout": []})
        rpc._post = core
        cfg = make_cfg()
        cache = MempoolTxCache()
        errors = []

        cache.classify(cfg, [{"txid": "a"}, {"txid": "b"}], errors)
        self.assertEqual(cache.size(), 2)
        cache.classify(cfg, [{"txid": "a"}], errors)  # "b" left the mempool
        self.assertEqual(cache.size(), 1)

    def test_classification_applied_to_all_items(self):
        core = FakeCore()
        core.set("getrawtransaction", lambda params: {
            "vout": [{"scriptPubKey": {"type": "transfer_asset", "asset": {"name": "MYASSET", "amount": 5}}}]
        })
        rpc._post = core
        cfg = make_cfg()
        cache = MempoolTxCache()
        errors = []
        items = [{"txid": "a"}]
        cache.classify(cfg, items, errors)
        self.assertEqual(items[0]["kind"], "ASSET")
        self.assertEqual(items[0]["asset_name"], "MYASSET")
        self.assertEqual(items[0]["asset_operation"], "transfer")

    def test_size_bound_enforced(self):
        core = FakeCore()
        core.set("getrawtransaction", lambda params: {"vout": []})
        rpc._post = core
        cfg = make_cfg()
        cache = MempoolTxCache(max_size=3)
        errors = []
        items = [{"txid": str(i)} for i in range(10)]
        cache.classify(cfg, items, errors)
        self.assertLessEqual(cache.size(), 3)


if __name__ == "__main__":
    unittest.main()

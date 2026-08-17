import unittest

import assets


class NameSubtypeTests(unittest.TestCase):
    def test_ownership_token(self):
        self.assertEqual(assets._name_subtype("MYASSET!"), "ownership")

    def test_qualifier(self):
        self.assertEqual(assets._name_subtype("#KYC"), "qualifier")

    def test_sub_qualifier(self):
        self.assertEqual(assets._name_subtype("#KYC/#TIER1"), "sub_qualifier")

    def test_restricted(self):
        self.assertEqual(assets._name_subtype("$SECURITY"), "restricted")

    def test_unique(self):
        self.assertEqual(assets._name_subtype("PARENT#UniqueTag"), "unique")

    def test_regular(self):
        self.assertEqual(assets._name_subtype("PLAINASSET"), "regular")

    def test_none_name_is_unknown(self):
        self.assertEqual(assets._name_subtype(None), "unknown")


class ClassifyVoutTests(unittest.TestCase):
    def test_plain_rvn_output(self):
        result = assets.classify_vout({"scriptPubKey": {"type": "pubkeyhash"}})
        self.assertEqual(result["kind"], "RVN")

    def test_known_operation(self):
        vout = {"scriptPubKey": {"type": "new_asset", "asset": {"name": "FOO", "amount": 1}}}
        result = assets.classify_vout(vout)
        self.assertEqual(result["kind"], "ASSET")
        self.assertEqual(result["asset_operation"], "issuance")

    def test_unrecognized_operation_string_is_unknown_not_guessed(self):
        vout = {"scriptPubKey": {"type": "some_future_script_type", "asset": {"name": "FOO"}}}
        result = assets.classify_vout(vout)
        self.assertEqual(result["asset_operation"], "unknown")


class ClassifyTxTests(unittest.TestCase):
    def test_pure_rvn_tx(self):
        tx = {"vout": [{"scriptPubKey": {"type": "pubkeyhash"}}]}
        result = assets.classify_tx(tx)
        self.assertEqual(result["kind"], "RVN")
        self.assertEqual(result["asset_name"], None)

    def test_asset_tx_backward_compatible_fields(self):
        tx = {"vout": [{"scriptPubKey": {"type": "transfer_asset", "asset": {"name": "X", "amount": 1}}}]}
        result = assets.classify_tx(tx)
        self.assertEqual(result["kind"], "ASSET")
        self.assertEqual(result["asset_name"], "X")


class AnomalyTests(unittest.TestCase):
    def test_no_burst_below_threshold(self):
        txs = [{"kind": "ASSET"} for _ in range(5)]
        result = assets.detect_mempool_anomalies(txs, burst_threshold=20)
        self.assertFalse(result["asset_operation_burst"])

    def test_burst_detected_above_threshold(self):
        txs = [{"kind": "ASSET"} for _ in range(25)]
        result = assets.detect_mempool_anomalies(txs, burst_threshold=20)
        self.assertTrue(result["asset_operation_burst"])

    def test_empty_mempool(self):
        result = assets.detect_mempool_anomalies([])
        self.assertFalse(result["asset_operation_burst"])
        self.assertEqual(result["asset_tx_count"], 0)


if __name__ == "__main__":
    unittest.main()

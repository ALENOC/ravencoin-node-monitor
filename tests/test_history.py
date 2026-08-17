import glob
import os
import tempfile
import time
import unittest

import history


class MemoryHistoryTests(unittest.TestCase):
    def test_default_is_memory_no_files(self):
        cwd_before = set(glob.glob("*.db*"))
        store = history.HistoryStore("memory")
        store.insert_sample({"block_height": 100, "peer_count": 5})
        cwd_after = set(glob.glob("*.db*"))
        self.assertEqual(cwd_before, cwd_after)  # no history.db / -wal / -shm appeared

    def test_insert_and_query(self):
        store = history.HistoryStore("memory")
        now = time.time()
        store.insert_sample({"block_height": 100}, ts=now)
        store.insert_sample({"block_height": 101}, ts=now + 30)
        points = store.query_metric("block_height", "1h")
        self.assertGreaterEqual(len(points), 1)
        self.assertAlmostEqual(points[-1]["value"], 101, delta=1)

    def test_unknown_metric_rejected(self):
        store = history.HistoryStore("memory")
        with self.assertRaises(ValueError):
            store.query_metric("drop table samples", "1h")

    def test_unknown_range_rejected(self):
        store = history.HistoryStore("memory")
        with self.assertRaises(ValueError):
            store.query_metric("block_height", "999y")

    def test_unknown_metric_names_are_dropped_silently_on_insert(self):
        store = history.HistoryStore("memory")
        store.insert_sample({"not_a_real_metric": 1, "block_height": 5})
        points = store.query_metric("block_height", "1h")
        self.assertEqual(len(points), 1)

    def test_retention_cleanup_removes_old_samples(self):
        store = history.HistoryStore("memory", retention_hours=1)
        old_ts = time.time() - 7200  # 2h ago, older than 1h retention
        store.insert_sample({"block_height": 1}, ts=old_ts)
        store._last_cleanup = 0  # force cleanup to run on next insert
        store.insert_sample({"block_height": 2}, ts=time.time())
        points = store.query_metric("block_height", "30d")
        values = [p["value"] for p in points]
        self.assertNotIn(1, values)

    def test_max_samples_backstop(self):
        store = history.HistoryStore("memory", retention_hours=999999, max_samples_per_metric=5)
        base = time.time() - 1000
        for i in range(20):
            store.insert_sample({"block_height": i}, ts=base + i)
        store._last_cleanup = 0
        store.insert_sample({"block_height": 999}, ts=time.time())
        with store._lock:
            count = store._conn.execute(
                "SELECT COUNT(*) FROM samples WHERE metric='block_height'"
            ).fetchone()[0]
        self.assertLessEqual(count, 5)

    def test_restart_produces_empty_history(self):
        store1 = history.HistoryStore("memory")
        store1.insert_sample({"block_height": 42})
        store1.close()
        store2 = history.HistoryStore("memory")  # simulates a fresh process
        self.assertEqual(store2.query_metric("block_height", "1h"), [])


class SqliteOptInHistoryTests(unittest.TestCase):
    def test_explicit_sqlite_storage_creates_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "history.db")
            store = history.HistoryStore("sqlite", db_path=path)
            store.insert_sample({"block_height": 1})
            self.assertTrue(os.path.exists(path))
            store.close()

    def test_sqlite_without_path_raises(self):
        with self.assertRaises(ValueError):
            history.HistoryStore("sqlite", db_path=None)

    def test_unknown_storage_mode_raises(self):
        with self.assertRaises(ValueError):
            history.HistoryStore("postgres")


if __name__ == "__main__":
    unittest.main()

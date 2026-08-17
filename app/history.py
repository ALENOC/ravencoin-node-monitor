"""Bounded, RAM-only time-series history using stdlib `sqlite3` in
`:memory:` mode by default - SQL makes the range-bucketing query in
`query_metric` simple, but nothing is ever written to flash unless the
operator explicitly opts into `HISTORY_STORAGE=sqlite` (a real file, for
users who accept the write wear in exchange for surviving a restart).

Design invariant: under default configuration this module creates zero
files. `:memory:` SQLite lives entirely in the process's own RAM and is
gone the instant the process exits - exactly like a restart wiping a
deque, just with SQL query ergonomics.

Two independent bounds keep memory usage predictable regardless of how
long the process runs: a retention window (age-based) and a hard
max-samples-per-metric cap (count-based, the backstop if retention cleanup
ever falls behind).
"""

import json
import os
import sqlite3
import threading
import time

# Whitelisted metric names - the only column values `insert_sample` writes
# and the only names `/api/history` will ever accept. Keeps the API from
# turning into an arbitrary-query surface.
METRICS = (
    "block_height",
    "peer_count",
    "inbound_peers",
    "outbound_peers",
    "mempool_tx_count",
    "mempool_size_bytes",
    "network_hashrate",
    "rpc_latency_ms",
    "electrumx_height",
    "electrumx_clients",
    "load1",
    "memory_used_percent",
    "swap_used_percent",
    "disk_used_percent",
    "disk_free_gb",
    "temperature_c",
    "health_score",
)

RANGE_SECONDS = {"1h": 3600, "6h": 21600, "24h": 86400, "7d": 604800, "30d": 2592000}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    ts INTEGER NOT NULL,
    metric TEXT NOT NULL,
    value REAL
);
CREATE INDEX IF NOT EXISTS idx_samples_metric_ts ON samples(metric, ts);

CREATE TABLE IF NOT EXISTS events (
    ts INTEGER NOT NULL,
    type TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    metadata TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
"""

CLEANUP_INTERVAL_SECONDS = 900


class HistoryStore:
    def __init__(self, storage="memory", db_path=None, retention_hours=168, max_samples_per_metric=20160):
        """storage: "memory" (default, RAM-only, no files ever) or
        "sqlite" (explicit opt-in, writes `db_path`). Never silently
        falls back from one to the other - an operator who asks for
        "sqlite" and gets a bad path should see an error, not a silent
        reversion to memory that quietly stops surviving restarts as
        expected, and the reverse (falling back to disk when memory was
        requested) would violate the whole point of this module.
        """
        if storage not in ("memory", "sqlite"):
            raise ValueError(f"unknown HISTORY_STORAGE: {storage}")
        self.storage = storage
        self.retention_hours = retention_hours
        self.max_samples_per_metric = max_samples_per_metric
        self._lock = threading.Lock()
        self._last_cleanup = 0.0

        if storage == "memory":
            target = ":memory:"
        else:
            if not db_path:
                raise ValueError("HISTORY_STORAGE=sqlite requires a db_path")
            os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
            target = db_path

        self._conn = sqlite3.connect(target, check_same_thread=False)
        if storage == "sqlite":
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def insert_sample(self, metrics: dict, ts=None):
        """`metrics`: {name: value}. One transaction per call regardless of
        how many metric names are included - this is meant to be called
        once per history-sampling interval, not once per metric.
        """
        ts = int(ts if ts is not None else time.time())
        rows = [(ts, name, float(value)) for name, value in metrics.items() if name in METRICS and value is not None]
        if not rows:
            return
        with self._lock:
            self._conn.executemany("INSERT INTO samples (ts, metric, value) VALUES (?, ?, ?)", rows)
            self._conn.commit()
        self._maybe_cleanup()

    def insert_event(self, event: dict):
        with self._lock:
            self._conn.execute(
                "INSERT INTO events (ts, type, severity, message, metadata) VALUES (?, ?, ?, ?, ?)",
                (
                    int(event["timestamp"]),
                    event["type"],
                    event["severity"],
                    event["message"],
                    json.dumps(event.get("metadata") or {}),
                ),
            )
            self._conn.commit()

    def query_metric(self, metric, range_key, max_points=500):
        if metric not in METRICS:
            raise ValueError(f"unknown metric: {metric}")
        range_seconds = RANGE_SECONDS.get(range_key)
        if range_seconds is None:
            raise ValueError(f"unknown range: {range_key}")
        since = time.time() - range_seconds
        bucket = max(30, int(range_seconds / max_points))
        with self._lock:
            cur = self._conn.execute(
                "SELECT (CAST(ts AS INTEGER)/?)*? AS bucket, AVG(value) FROM samples "
                "WHERE metric = ? AND ts >= ? GROUP BY bucket ORDER BY bucket",
                (bucket, bucket, metric, since),
            )
            rows = cur.fetchall()
        return [{"ts": r[0], "value": round(r[1], 4)} for r in rows]

    def sample_span_seconds(self, metric):
        """How much wall-clock time the oldest-to-newest retained sample
        for `metric` actually spans - used to gate the disk-runway
        estimate so a handful of samples never produces a wild forecast.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT MIN(ts), MAX(ts), COUNT(*) FROM samples WHERE metric = ?", (metric,)
            ).fetchone()
        if not row or row[2] < 2 or row[0] is None:
            return 0.0, 0
        return float(row[1] - row[0]), row[2]

    def _maybe_cleanup(self):
        now = time.time()
        if now - self._last_cleanup < CLEANUP_INTERVAL_SECONDS:
            return
        self._last_cleanup = now
        cutoff = int(now - self.retention_hours * 3600)
        with self._lock:
            self._conn.execute("DELETE FROM samples WHERE ts < ?", (cutoff,))
            self._conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
            # Hard backstop: even if retention math is somehow wrong, no
            # single metric's series can grow without bound.
            for metric in METRICS:
                self._conn.execute(
                    "DELETE FROM samples WHERE metric = ? AND rowid NOT IN "
                    "(SELECT rowid FROM samples WHERE metric = ? ORDER BY ts DESC LIMIT ?)",
                    (metric, metric, self.max_samples_per_metric),
                )
            self._conn.commit()

    def close(self):
        with self._lock:
            self._conn.close()

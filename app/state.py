"""Bundles every piece of state that must survive across poll cycles
(chain baseline, mempool classification cache, P2P traffic baseline, event
log, RAM history, alert cooldowns). Constructed once in server.py and
mutated only by the poll loop thread - the single-writer discipline that
keeps this safe without needing locks scattered across every module; each
module that does have its own internal concurrency need (history's SQLite
connection, the event log, rpc.py's latency tracker) locks itself.
"""

import time

import alerts
import chain
import events
import history
import mempool_cache
import network_traffic


class MonitorState:
    def __init__(self, cfg):
        self.chain_monitor = chain.ChainMonitor(cfg)
        self.mempool_cache = mempool_cache.MempoolTxCache()
        self.network_traffic = network_traffic.TrafficTracker()
        self.alert_dispatcher = alerts.AlertDispatcher()
        self.transitions = events.TransitionTracker()

        self.history = None
        self.history_init_error = None
        if cfg.history_enabled:
            try:
                if cfg.history_storage == "sqlite":
                    self.history = history.HistoryStore(
                        "sqlite",
                        db_path=cfg.history_db_path,
                        retention_hours=cfg.history_retention_hours,
                        max_samples_per_metric=cfg.history_max_samples,
                    )
                else:
                    self.history = history.HistoryStore(
                        "memory",
                        retention_hours=cfg.history_retention_hours,
                        max_samples_per_metric=cfg.history_max_samples,
                    )
            except Exception as exc:  # noqa: BLE001 - history is optional, never fatal
                self.history = None
                self.history_init_error = str(exc)
                # Deliberately does NOT fall back to a different storage
                # mode - if "sqlite" was requested and fails, history
                # becomes unavailable rather than silently writing
                # somewhere else (or the reverse: silently persisting to
                # disk when memory-only was requested).

        self.event_log = events.EventLog(history=self.history)
        self._last_history_sample_ts = 0.0

        # Simple monotonic-value transition tracking (a counter going up or
        # down, not an enum/status) - plain attributes rather than
        # TransitionTracker, which is for status-like values.
        self.last_block_height = None
        self.last_uptime_seconds = None

    def maybe_sample_history(self, cfg, metrics: dict):
        if self.history is None:
            return
        now = time.time()
        if now - self._last_history_sample_ts < cfg.history_sample_interval:
            return
        self._last_history_sample_ts = now
        self.history.insert_sample(metrics, ts=now)

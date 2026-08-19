"""Ravencoin Core P2P traffic accounting.

The source is Core's ``getnettotals`` RPC, not the host network interface,
so every counter and rate exposed here belongs to Ravencoin's own P2P
traffic. RPC traffic, Docker traffic, SSH, web browsing, ElectrumX and other
host applications are intentionally excluded.
"""

import time


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if value >= 0 else None


def _normalize_upload_target(raw):
    raw = raw if isinstance(raw, dict) else {}
    target = _number(raw.get("target"))
    bytes_left = _number(raw.get("bytes_left_in_cycle"))
    timeframe = _number(raw.get("timeframe"))
    time_left = _number(raw.get("time_left_in_cycle"))
    limited = target is not None and target > 0

    used = None
    progress = None
    if limited and bytes_left is not None:
        used = min(target, max(0, target - bytes_left))
        progress = used / target

    return {
        "enabled": limited,
        "target_bytes": target if limited else None,
        "used_bytes": used,
        "bytes_left": bytes_left if limited else None,
        "progress": progress,
        "timeframe_seconds": timeframe if limited else None,
        "time_left_seconds": time_left if limited else None,
        "target_reached": bool(raw.get("target_reached")) if limited else False,
        "serve_historical_blocks": bool(raw.get("serve_historical_blocks", True)),
    }


class TrafficTracker:
    """Convert cumulative Core counters into per-second rates.

    Core exposes cumulative received/sent byte counters. Rates therefore need
    one previous sample. The first observation (and the first observation
    after a Core restart/counter reset) deliberately returns ``None`` rates
    rather than a misleading spike.
    """

    def __init__(self):
        self._last_received = None
        self._last_sent = None
        self._last_monotonic = None

    def update(self, totals, now=None):
        if not isinstance(totals, dict):
            return None

        received = _number(totals.get("totalbytesrecv"))
        sent = _number(totals.get("totalbytessent"))
        if received is None or sent is None:
            return None

        now = time.monotonic() if now is None else now
        download_bps = None
        upload_bps = None
        sample_seconds = None

        if self._last_monotonic is not None:
            elapsed = now - self._last_monotonic
            counters_monotonic = (
                received >= self._last_received and sent >= self._last_sent
            )
            if elapsed > 0 and counters_monotonic:
                sample_seconds = elapsed
                download_bps = (received - self._last_received) / elapsed
                upload_bps = (sent - self._last_sent) / elapsed

        self._last_received = received
        self._last_sent = sent
        self._last_monotonic = now

        return {
            "scope": "ravencoin_p2p",
            "source": "getnettotals",
            "download_bytes_per_second": download_bps,
            "upload_bytes_per_second": upload_bps,
            "total_bytes_received": received,
            "total_bytes_sent": sent,
            "total_bytes_transferred": received + sent,
            "sample_seconds": sample_seconds,
            "core_time_millis": _number(totals.get("timemillis")),
            "upload_target": _normalize_upload_target(totals.get("uploadtarget")),
        }

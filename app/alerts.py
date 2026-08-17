"""Generic webhook alerting, stdlib only. Fires on event state transitions
(never on every poll cycle - that's the caller's job via events.py), with
a per-event-type cooldown so a flapping condition can't spam a webhook.

Delivery runs in a short-lived daemon thread so a slow or unreachable
webhook endpoint can never stall the poll loop; failures are swallowed
without ever logging the URL (which may contain a secret token).
"""

import json
import threading
import time
import urllib.error
import urllib.request

_SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}


class AlertDispatcher:
    def __init__(self):
        self._lock = threading.Lock()
        self._last_sent = {}

    def maybe_send(self, event, cfg):
        if not cfg.alert_webhook_url:
            return
        if _SEVERITY_RANK.get(event["severity"], 0) < _SEVERITY_RANK.get(cfg.alert_min_severity, 1):
            return
        key = event["type"]
        now = time.time()
        with self._lock:
            last = self._last_sent.get(key, 0)
            if now - last < cfg.alert_cooldown_seconds:
                return
            self._last_sent[key] = now
        threading.Thread(target=self._send, args=(event, cfg.alert_webhook_url), daemon=True).start()

    @staticmethod
    def _send(event, url):
        payload = {
            "service": "ravencoin-node-monitor",
            "severity": event["severity"],
            "event": event["type"],
            "message": event["message"],
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(event["timestamp"])),
        }
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=5).read()
        except (urllib.error.URLError, OSError, ValueError):
            pass  # alert delivery must never affect monitoring, and the
            # URL itself may carry a secret token - nothing about this
            # failure is logged

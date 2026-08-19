"""Environment-driven configuration. No secrets or topology hardcoded here -
everything comes from the environment (or *_FILE-pointed secret files) so the
same image works for any deployment.
"""

import os
from dataclasses import dataclass

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
INDEX_HTML_PATH = os.path.join(STATIC_DIR, "index.html")


def _env(name, default=None):
    return os.environ.get(name, default)


def _env_int(name, default):
    val = os.environ.get(name)
    return int(val) if val is not None and val != "" else default


def _env_bool(name, default):
    val = os.environ.get(name)
    if val is None or val == "":
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _secret(name, default=None):
    """Read NAME, or the contents of the file pointed at by NAME_FILE if set.

    NAME_FILE takes precedence so credentials can be mounted from Docker
    secrets / bind-mounted files instead of living in plain environment
    variables.
    """
    file_path = os.environ.get(f"{name}_FILE")
    if file_path:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    return os.environ.get(name, default)


@dataclass
class Config:
    node_name: str = "Ravencoin Node"
    bind_host: str = "0.0.0.0"
    bind_port: int = 8899
    poll_interval: int = 10

    # HTTP access hardening. Private/loopback IP Host headers and localhost
    # are accepted automatically; DNS/reverse-proxy names must be listed in
    # monitor_allowed_hosts. Setting monitor_password enables HTTP Basic auth
    # for the dashboard and data endpoints (/healthz and /readyz stay public
    # so orchestrator probes do not need credentials).
    monitor_allowed_hosts: str = ""
    monitor_user: str = "monitor"
    monitor_password: str = None

    core_host: str = "127.0.0.1"
    core_port: int = 8766
    core_user: str = None
    core_password: str = None
    core_timeout: int = 8

    mempool_tx_limit: int = 200
    mempool_classify: bool = True
    mempool_classify_limit: int = 300

    # "auto": probe silently, hide the ElectrumX section if unreachable.
    # "true": always show it, surface connection failures as errors.
    # "false": never probe, standalone-Core mode only.
    electrumx_mode: str = "auto"
    ex_rpc_host: str = "127.0.0.1"
    ex_rpc_port: int = 8000
    ex_ssl_host: str = "127.0.0.1"
    ex_ssl_port: int = 50002
    ex_ssl_sni: str = None
    ex_ssl_verify: bool = False
    # "rpc": call the admin RPC directly (needs ELECTRUMX_RPC_HOST reachable).
    # "file": read a JSON snapshot written by contrib/electrumx-admin-poller.py
    # - use this when the admin RPC is locked to 127.0.0.1 inside its own
    # container and you don't want to change that or share its netns.
    ex_admin_source: str = "rpc"
    ex_admin_file: str = None
    ex_admin_max_age: int = 60

    price_feed_enabled: bool = False
    price_feed_symbol: str = "RVNUSDT"
    price_poll_interval: int = 300

    # "Label=/path,Label2=/path2" - extra mounted volumes to report disk
    # usage for (e.g. blockchain data on a separate drive from the OS).
    extra_disk_paths: str = ""

    # --- history (RAM-only by default - see history.py's module docstring) ---
    history_enabled: bool = True
    history_storage: str = "memory"  # "memory" (default) or "sqlite" (opt-in, writes a file)
    history_db_path: str = "/data/history.db"  # only used when history_storage="sqlite"
    history_sample_interval: int = 60  # seconds between history samples (independent of poll_interval)
    history_retention_hours: int = 168
    history_max_samples: int = 20160  # hard per-metric cap, backstop under retention_hours
    event_history_max: int = 2000

    privacy_mode: bool = False
    prometheus_enabled: bool = True

    min_safe_core_version: str = ""  # e.g. "4.8.0" - empty disables the check

    health_min_peers: int = 4
    disk_warning_percent: int = 80
    disk_critical_percent: int = 90
    electrumx_warning_lag: int = 3
    electrumx_critical_lag: int = 10
    chain_stale_warning_seconds: int = 900
    chain_stale_critical_seconds: int = 1800
    rpc_latency_warning_ms: int = 2000

    alert_webhook_url: str = ""
    alert_min_severity: str = "warning"
    alert_cooldown_seconds: int = 900


def load() -> Config:
    ex_ssl_host = _env("ELECTRUMX_SSL_HOST", _env("ELECTRUMX_RPC_HOST", "127.0.0.1"))
    return Config(
        node_name=_env("NODE_NAME", "Ravencoin Node"),
        bind_host=_env("BIND_HOST", "0.0.0.0"),
        bind_port=_env_int("BIND_PORT", 8899),
        poll_interval=_env_int("POLL_INTERVAL", 10),
        monitor_allowed_hosts=_env("MONITOR_ALLOWED_HOSTS", ""),
        monitor_user=_env("MONITOR_USER", "monitor"),
        monitor_password=_secret("MONITOR_PASSWORD"),
        core_host=_env("CORE_RPC_HOST", "127.0.0.1"),
        core_port=_env_int("CORE_RPC_PORT", 8766),
        core_user=_secret("CORE_RPC_USER"),
        core_password=_secret("CORE_RPC_PASSWORD"),
        core_timeout=_env_int("CORE_RPC_TIMEOUT", 8),
        mempool_tx_limit=_env_int("MEMPOOL_TX_LIMIT", 200),
        mempool_classify=_env_bool("MEMPOOL_CLASSIFY", True),
        mempool_classify_limit=_env_int("MEMPOOL_CLASSIFY_LIMIT", 300),
        electrumx_mode=_env("ELECTRUMX_ENABLED", "auto").strip().lower(),
        ex_rpc_host=_env("ELECTRUMX_RPC_HOST", "127.0.0.1"),
        ex_rpc_port=_env_int("ELECTRUMX_RPC_PORT", 8000),
        ex_ssl_host=ex_ssl_host,
        ex_ssl_port=_env_int("ELECTRUMX_SSL_PORT", 50002),
        ex_ssl_sni=_env("ELECTRUMX_SSL_SNI", ex_ssl_host),
        ex_ssl_verify=_env_bool("ELECTRUMX_SSL_VERIFY", False),
        ex_admin_source=_env("ELECTRUMX_ADMIN_SOURCE", "rpc").strip().lower(),
        ex_admin_file=_env("ELECTRUMX_ADMIN_FILE"),
        ex_admin_max_age=_env_int("ELECTRUMX_ADMIN_MAX_AGE", 60),
        price_feed_enabled=_env_bool("PRICE_FEED_ENABLED", False),
        price_feed_symbol=_env("PRICE_FEED_SYMBOL", "RVNUSDT"),
        price_poll_interval=_env_int("PRICE_POLL_INTERVAL", 300),
        extra_disk_paths=_env("EXTRA_DISK_PATHS", ""),
        history_enabled=_env_bool("HISTORY_ENABLED", True),
        history_storage=_env("HISTORY_STORAGE", "memory").strip().lower(),
        history_db_path=_env("HISTORY_DB_PATH", "/data/history.db"),
        history_sample_interval=_env_int("HISTORY_SAMPLE_INTERVAL", 60),
        history_retention_hours=_env_int("HISTORY_RETENTION_HOURS", 168),
        history_max_samples=_env_int("HISTORY_MAX_SAMPLES", 20160),
        event_history_max=_env_int("EVENT_HISTORY_MAX", 2000),
        privacy_mode=_env_bool("PRIVACY_MODE", False),
        prometheus_enabled=_env_bool("PROMETHEUS_ENABLED", True),
        min_safe_core_version=_env("MIN_SAFE_CORE_VERSION", ""),
        health_min_peers=_env_int("HEALTH_MIN_PEERS", 4),
        disk_warning_percent=_env_int("DISK_WARNING_PERCENT", 80),
        disk_critical_percent=_env_int("DISK_CRITICAL_PERCENT", 90),
        electrumx_warning_lag=_env_int("ELECTRUMX_WARNING_LAG", 3),
        electrumx_critical_lag=_env_int("ELECTRUMX_CRITICAL_LAG", 10),
        chain_stale_warning_seconds=_env_int("CHAIN_STALE_WARNING_SECONDS", 900),
        chain_stale_critical_seconds=_env_int("CHAIN_STALE_CRITICAL_SECONDS", 1800),
        rpc_latency_warning_ms=_env_int("RPC_LATENCY_WARNING_MS", 2000),
        alert_webhook_url=_env("ALERT_WEBHOOK_URL", ""),
        alert_min_severity=_env("ALERT_MIN_SEVERITY", "warning").strip().lower(),
        alert_cooldown_seconds=_env_int("ALERT_COOLDOWN_SECONDS", 900),
    )

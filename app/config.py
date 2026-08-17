"""Environment-driven configuration. No secrets or topology hardcoded here -
everything comes from the environment (or *_FILE-pointed secret files) so the
same image works for any deployment.
"""

import os
from dataclasses import dataclass, field

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


def load() -> Config:
    ex_ssl_host = _env("ELECTRUMX_SSL_HOST", _env("ELECTRUMX_RPC_HOST", "127.0.0.1"))
    return Config(
        node_name=_env("NODE_NAME", "Ravencoin Node"),
        bind_host=_env("BIND_HOST", "0.0.0.0"),
        bind_port=_env_int("BIND_PORT", 8899),
        poll_interval=_env_int("POLL_INTERVAL", 10),
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
    )

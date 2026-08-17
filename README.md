# ravencoin-node-monitor

A lightweight, LAN-only monitoring dashboard for a Ravencoin Core node,
optionally paired with ElectrumX. One adaptive UI: it shows Core-only
metrics for a standalone node, and automatically adds ElectrumX server /
connected-client cards when ElectrumX is reachable.

Stdlib-only Python (no pip dependencies), a single static HTML page, and a
Docker image that runs on anything from a Raspberry Pi to a VPS.

## Features

- Sync status, network hashrate, P2P peers (with addresses), mempool stats
  and transaction list (with RVN vs. asset-transfer classification), host
  resource usage (load, memory, swap, disk, CPU temperature)
- Click a mempool transaction for full detail: inputs, and every output's
  address, type, and RVN or asset amount
- Sortable and filterable peer/mempool tables, copy-to-clipboard on
  addresses and TXIDs, manual light/dark/auto theme toggle
- Reports disk usage for extra mounted volumes (e.g. blockchain data on a
  separate drive from the OS), not just the root filesystem
- A dedicated "node is starting up" state (instead of a wall of RPC
  errors) while Core is loading its block index after a restart
- Optional RVN/USDT price ticker (Binance public API)
- Optional ElectrumX section: server info and a live list of connected
  Electrum clients with their addresses
- Zero third-party Python packages, single JSON `/api/status` endpoint,
  auto-refreshing page

## Quick start

```bash
cp .env.example .env
# edit .env: set CORE_RPC_USER / CORE_RPC_PASSWORD (or the _FILE variants)
# to match your Ravencoin Core node's rpcauth
docker compose up -d --build
```

Open `http://<host>:8899` from your LAN. The container binds
`127.0.0.1:8899` by default in `docker-compose.yml` - it is meant to be
reached over the LAN via your own reverse proxy, SSH tunnel, or a firewall
rule you control. **It has no authentication of its own; do not expose it
to the public internet.**

## Configuration

All configuration is via environment variables - see `.env.example` for the
full list with defaults. Highlights:

| Variable | Purpose |
|---|---|
| `CORE_RPC_HOST` / `CORE_RPC_PORT` | Where to reach Ravencoin Core's JSON-RPC |
| `CORE_RPC_USER` / `CORE_RPC_PASSWORD` | RPC credentials (or the `_FILE` variants below) |
| `CORE_RPC_USER_FILE` / `CORE_RPC_PASSWORD_FILE` | Read credentials from a file instead (Docker secrets, mounted files) |
| `ELECTRUMX_ENABLED` | `auto` (default, silent probe), `true`, or `false` |
| `ELECTRUMX_RPC_HOST` / `_PORT` | ElectrumX's admin RPC (`rpc://`), default port 8000 |
| `ELECTRUMX_SSL_HOST` / `_PORT` / `_SNI` | ElectrumX's public `ssl://` port, used read-only for backend sync info |
| `PRICE_FEED_ENABLED` | Off by default; set `true` to poll Binance for RVN/USDT |
| `EXTRA_DISK_PATHS` | `Label=/path,Label2=/path2` - report disk usage for extra mounted volumes beyond `/` |

Credentials can be supplied either as plain environment variables or as
file paths (`*_FILE`), so you can mount Docker/Compose secrets instead of
putting passwords in `.env`.

## Deployment modes

### Standalone Ravencoin Core

The common case: point `CORE_RPC_HOST`/`CORE_RPC_PORT`/credentials at your
node. `ELECTRUMX_ENABLED=auto` (the default) means the ElectrumX section
just won't appear if nothing answers on the ElectrumX ports - no
configuration needed to hide it.

### Core + ElectrumX bundle

ElectrumX's admin RPC port (`rpc://`) is meant to be reachable from
*localhost only* by default - that's what makes `electrumx_rpc getinfo`
work without a password, so most ElectrumX deployments (including the
official `docker-compose` examples) bind it to `127.0.0.1` inside the
ElectrumX container itself. A sibling container on the same Docker network
cannot reach it, even by container name - and if your ElectrumX stack is
someone else's project (or already security-reviewed and you don't want
to touch its config), that binding should stay exactly as it is.

Three ways to get the connected-client list anyway, in order of
preference:

**1. Host-side poller (recommended - no ElectrumX config changes, no
Docker socket in the monitor container).** `contrib/electrumx-admin-poller.py`
runs on the Docker host itself (which already has legitimate `docker exec`
access to your own containers), polls the admin RPC via `docker exec`, and
writes a small JSON snapshot to disk. The monitor container just reads
that file read-only:

```bash
python3 contrib/electrumx-admin-poller.py \
  --container <your-electrumx-container-name> \
  --output /var/lib/ravencoin-node-monitor/electrumx-admin.json
```

(see `contrib/electrumx-admin-poller.service.example` for a systemd unit),
then in `.env`:

```
ELECTRUMX_ADMIN_SOURCE=file
ELECTRUMX_ADMIN_FILE=/data/electrumx-admin.json
```

and bind-mount the snapshot file read-only into the monitor container at
that path.

**2. Shared network namespace.** If you're fine editing your own compose
file, run the monitor with `network_mode: "container:<electrumx-container-name>"`
(see `docker-compose.electrumx.example.yml`) - `127.0.0.1:8000` inside the
monitor then reaches ElectrumX's admin RPC directly. Note this also means
the monitor's own HTTP port can only be published by adding it to the
ElectrumX service's own `ports:` list, since a container sharing another's
netns can't publish ports independently.

**3. Loosen the bind on purpose.** If you control the ElectrumX deployment
and are fine with it, changing `rpc://127.0.0.1:8000` to `rpc://0.0.0.0:8000`
in its `SERVICES` setting lets any sibling container on the same Docker
network reach it directly via `ELECTRUMX_RPC_HOST=<service-name>` - the
port still isn't published to the LAN unless you also add it to `ports:`.

If your ElectrumX deployment already binds its admin RPC to a routable
address, none of this is needed - just point `ELECTRUMX_RPC_HOST` at the
container/service name directly.

## Mempool transaction classification

Each mempool transaction is looked up via `getrawtransaction` (batched into
a single JSON-RPC call) and marked `RVN` or `ASSET: <name>` based on
whether any output carries an asset transfer. This only sees transactions
currently in Core's own mempool - it is not a general asset explorer, and
it does not require `txindex=1`.

## Transaction detail

`GET /api/tx/<txid>` fetches full detail for one transaction on demand
(size, version, locktime, inputs, and every output's address/type/amount)
- it is not part of the periodic `/api/status` snapshot, so a large mempool
doesn't bloat every poll cycle. The UI calls it when you click a mempool
transaction row. Input amounts aren't resolved (that would need one extra
RPC call per input to look up the spent output), only what's directly on
the raw transaction is shown.

## Updating a running deployment

If you're running via `docker compose` and only copy new source files into
the container's filesystem (`docker cp`) without rebuilding the image, the
change is temporary: the next time the container is recreated for *any*
reason - a reboot, `docker compose up -d` after touching `.env` or a
compose file, a Docker upgrade - Compose recreates it from the last-built
**image**, silently reverting to the old code. Always rebuild before
recreating:

```bash
docker compose build
docker compose up -d
```

The source tree on disk is the source of truth; the image should always be
rebuilt from it, never hand-patched and left that way.

## Running without Docker

```bash
cd app
python3 server.py
```

No dependencies beyond Python 3.9+.

## License

MIT - see `LICENSE`.

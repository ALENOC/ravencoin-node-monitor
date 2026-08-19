# Live bandwidth control

`ravencoin-node-monitor` can optionally change upload limits for the Ravencoin Core and ElectrumX containers directly from the dashboard without modifying either project's source code.

The feature is **off by default**. The monitor container remains unprivileged and does not receive the Docker socket or `CAP_NET_ADMIN`. A small root-owned host helper applies Linux `tc` rules inside the two containers' network namespaces and exposes only a fixed Unix-socket protocol.

## Dashboard units

The two limits are independent. Each field accepts a decimal number and one of:

- `B/s`
- `KB/s`
- `MB/s`
- `GB/s`

`KB`, `MB`, and `GB` use powers of 1024, matching the rest of the dashboard. `0` or the **Unlimited** button removes the practical rate cap. Internally the dashboard converts the selected value to an integer number of bytes/second before sending it to the helper.

Examples:

- `250 KB/s` = 256000 B/s
- `1.5 MB/s` = 1572864 B/s
- `10 MB/s` = 10485760 B/s

Private RFC1918/link-local destinations are placed in an effectively-unlimited class, so normal Docker/LAN traffic such as Core ↔ ElectrumX is not throttled. The configured cap applies to normal public egress.

## Security model

Bandwidth writes are refused unless `MONITOR_PASSWORD` is configured. The write endpoint accepts only JSON containing the two numeric rate fields and also requires a same-origin custom header. There is no shell command field and no arbitrary command execution path.

The host helper's Unix socket is owned by `root:10001` with mode `0660`. The monitor image uses uid/gid `10001`, allowing it to connect to that one socket while remaining otherwise unprivileged.

## Host prerequisites

The Docker host needs:

- Docker CLI / daemon
- Python 3
- `nsenter` (`util-linux` package)
- `ip` and `tc` (`iproute2` package)

On Debian / Ubuntu / Raspberry Pi OS:

```bash
sudo apt-get update
sudo apt-get install -y python3 iproute2 util-linux
```

## Install the controller as a systemd service

This example assumes the repository is installed at `/opt/ravencoin-node-monitor`, as in the Raspberry Pi deployment used by this project.

```bash
cd /opt/ravencoin-node-monitor
sudo cp contrib/ravencoin-bandwidth-controller.service.example \
  /etc/systemd/system/ravencoin-bandwidth-controller.service
sudo systemctl daemon-reload
sudo systemctl enable --now ravencoin-bandwidth-controller.service
sudo systemctl status --no-pager ravencoin-bandwidth-controller.service
```

The example unit targets these container names by default:

```text
electrumx-ravencoin-ravencoin-core-1
electrumx-ravencoin-electrumx-1
```

If your names differ, edit the two `Environment=` lines in the unit before starting it.

Persistent limits are stored in:

```text
/var/lib/ravencoin-bandwidth/limits.json
```

The helper reconciles the configuration periodically, so if Docker recreates a Core or ElectrumX container and its PID/network namespace changes, the stored limit is reapplied automatically.

## Connect the monitor container

Set a monitor password in `.env` and enable the feature:

```ini
MONITOR_USER=monitor
MONITOR_PASSWORD=choose-a-strong-password
BANDWIDTH_CONTROL_ENABLED=true
BANDWIDTH_CONTROL_SOCKET=/run/ravencoin-bandwidth/control.sock
```

Then mount the helper socket directory read-only into the monitor. `docker-compose.bandwidth.yml` contains the optional overlay:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.override.yml \
  -f docker-compose.bandwidth.yml \
  up -d --build --no-deps monitor
```

If your deployment relies on Compose's automatic `docker-compose.override.yml` loading and you do not want to specify three `-f` arguments each time, copy the `environment:` entries and `/run/ravencoin-bandwidth` volume from `docker-compose.bandwidth.yml` into your local override file instead.

## API

Read current state:

```text
GET /api/bandwidth
```

Apply limits (authenticated dashboard use only):

```json
{
  "core_bytes_per_second": 524288,
  "electrumx_bytes_per_second": 262144
}
```

A value of `0` means unlimited. The API intentionally uses canonical integer bytes/second; the browser UI handles unit conversion.

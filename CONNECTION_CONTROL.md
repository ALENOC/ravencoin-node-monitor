# Connection limits from the dashboard

`ravencoin-node-monitor` can optionally manage the maximum Ravencoin Core P2P peer count and the maximum ElectrumX client-session count without modifying either project's source code.

This feature uses the same optional root-owned host controller as live bandwidth control. The monitor container stays unprivileged and never receives the Docker socket.

## What the dashboard controls

| Dashboard field | Native setting | Apply behavior |
|---|---|---|
| Ravencoin Core maximum peers | `-maxconnections=N` | recreate/restart Core only |
| ElectrumX maximum clients | `MAX_SESSIONS=N` | recreate/restart ElectrumX only |

Both fields accept whole numbers from `0` to `10000`.

**`0` means deployment default / no controller override. It never means zero connections.**

The dashboard shows the current connected peer/client count separately from the configured maximum.

## Why a restart is required

Neither setting is a live runtime RPC setting. Core reads `maxconnections` at startup and ElectrumX reads `MAX_SESSIONS` when its process starts. The dashboard therefore asks for explicit confirmation before applying a connection-limit change.

The selected service is recreated with Docker Compose using its native configuration setting. Current peers/clients disconnect briefly and reconnect normally. The other service is not deliberately recreated (`docker compose up -d --no-deps <service>`).

Bandwidth limits are different: those continue to use Linux `tc` and are applied live without restarting either service.

## Compose discovery and persistence

The controller does not hard-code the ElectrumX project directory. It reads Docker Compose's own labels from the running Core/ElectrumX containers:

- `com.docker.compose.project`
- `com.docker.compose.service`
- `com.docker.compose.project.working_dir`
- `com.docker.compose.project.config_files`

It then creates a small controller-owned override under:

```text
/var/lib/ravencoin-bandwidth/
```

For Core the override adds only the `-maxconnections=N` argument while preserving the container's other existing command arguments. For ElectrumX it adds only `MAX_SESSIONS=N` to the environment.

The original Compose files are never edited.

Configured limits are persisted in the controller state file. If Docker later recreates a managed container without the controller override, the controller notices that the running native setting no longer matches and reapplies it. Setting a dashboard limit back to `0` removes the controller override and restores the deployment's own default/configuration.

## Security model

Connection-limit writes use the same protections as bandwidth writes:

- `BANDWIDTH_CONTROL_ENABLED=true` must be explicitly enabled;
- `MONITOR_PASSWORD` must be configured or writes are refused;
- the browser must send the same-origin `X-Ravencoin-Monitor-Control: 1` header;
- only JSON with the exact `service`, `limit`, and `confirm_restart` fields is accepted;
- `confirm_restart` must be literal `true`;
- the host helper accepts only the fixed `core` / `electrumx` service names and an integer limit;
- there is no arbitrary shell-command field;
- the monitor container still has no Docker socket and no `CAP_NET_ADMIN`.

The host controller itself is root because Linux traffic shaping and Docker Compose service recreation require host privileges. Its systemd unit allows only read-only access to `/home` so it can read Compose projects stored there; controller state remains under `/var/lib/ravencoin-bandwidth`.

## API

Read state:

```text
GET /api/connections
```

Apply a Core limit:

```json
{
  "service": "core",
  "limit": 80,
  "confirm_restart": true
}
```

Apply an ElectrumX limit:

```json
{
  "service": "electrumx",
  "limit": 250,
  "confirm_restart": true
}
```

Restore deployment defaults by sending `limit: 0` for the chosen service.

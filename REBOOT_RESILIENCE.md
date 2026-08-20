# Reboot and host-port resilience

The container healthcheck proves that the monitor HTTP server is alive inside
its own network namespace.  It cannot prove that Docker successfully recreated
the host-side published port after a full host reboot.

A real incident on 2026-08-21 showed exactly that split-brain condition:
`ravencoin-node-monitor` was reported healthy, while `docker port`,
`.NetworkSettings.Ports` and the host socket table showed no active publication
for port 8899.  Recreating only the monitor container restored the mapping.

Future deployments should therefore use `contrib/verify-published-port.py` as a
host-side post-start gate.  It requires both:

1. a Docker `8899/tcp` host binding in the container metadata; and
2. an HTTP 200 response from `http://<host>:8899/healthz` through the host bind.

Example for a Compose deployment:

```sh
python3 contrib/verify-published-port.py \
  --compose-dir "$PWD" \
  --compose-file docker-compose.yml \
  --repair
```

`--repair` is intentionally bounded: it performs **one**
`docker compose up -d --no-deps --force-recreate monitor` and verifies again.
It never loops recreation indefinitely and never touches Ravencoin Core or
ElectrumX dependencies.

For the bundled ElectrumX-Ravencoin deployment this check is invoked by the
verified installer after activation.  That deployment also avoids the original
cross-Compose external-network race by placing the Monitor in the same Compose
project and keeping it independent of ElectrumX health/network namespaces.

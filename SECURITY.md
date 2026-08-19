# Security

## Deployment boundary

Ravencoin Node Monitor is designed for a trusted LAN, localhost, an SSH tunnel, or a reverse proxy you control. It exposes operational information about the node, including peer addresses, host-resource data, transaction details, and (when enabled) ElectrumX client information. Do not publish the Python HTTP server directly to the public internet.

The default Compose file publishes the monitor only on `127.0.0.1:8899`. Keep that default unless you have intentionally designed the surrounding network controls.

## HTTP Host validation and DNS-rebinding protection

The server rejects untrusted HTTP `Host` values. Localhost and private/loopback/link-local IP literals are allowed automatically. DNS names are denied unless explicitly configured with `MONITOR_ALLOWED_HOSTS`:

```text
MONITOR_ALLOWED_HOSTS=node.local,monitor.example.internal
```

This prevents an attacker-controlled website/DNS name from using a victim browser as a bridge to read the local monitor through DNS rebinding. Host validation is defense in depth, not a replacement for a firewall or reverse proxy.

## Optional dashboard authentication

Set a password to enable HTTP Basic authentication for the dashboard, `/api/*`, static assets, and `/metrics`:

```text
MONITOR_USER=monitor
MONITOR_PASSWORD=use-a-long-random-password
```

or mount a secret file:

```text
MONITOR_PASSWORD_FILE=/run/secrets/monitor_password
```

`/healthz` and `/readyz` intentionally remain unauthenticated so Docker and orchestrator probes continue to work.

Basic authentication does **not** encrypt HTTP. If traffic can leave a trusted host/LAN, terminate HTTPS at a reverse proxy or use an SSH/VPN tunnel; otherwise credentials and node data can be observed in transit.

## Ravencoin Core RPC credentials

Use a dedicated RPC account with only the network reachability required by the monitor. Prefer `CORE_RPC_USER_FILE` and `CORE_RPC_PASSWORD_FILE` over plaintext environment variables where practical. Never expose Ravencoin Core's RPC port to the public internet.

The diagnostics endpoint is built from an explicit allowlist and does not include Core credentials, monitor passwords, or webhook URLs.

## ElectrumX trust boundary

The ElectrumX admin RPC is passwordless in common deployments and should remain loopback/internal-network only.

The public ElectrumX TLS connection may run without certificate verification only when the configured target is clearly local: loopback/private/link-local IP literals, localhost, `.local`, or a single-label LAN/Docker service name. A remote FQDN or public IP is refused unless `ELECTRUMX_SSL_VERIFY=true`; its certificate must then validate for `ELECTRUMX_SSL_SNI`.

ElectrumX JSON responses are size-bounded and incomplete newline-delimited responses are rejected, preventing an endpoint from growing monitor memory without limit.

## Browser hardening

All responses receive restrictive security headers. The dashboard uses a per-response Content-Security-Policy nonce for JavaScript, denies framing, disables MIME sniffing, suppresses referrers, restricts browser permissions, and keeps cross-origin resource policy same-origin.

Network- and blockchain-derived strings rendered by the dashboard must be treated as untrusted. Keep using the existing escaping helpers or DOM `textContent` for any new peer, asset, RPC, ElectrumX, or transaction fields.

## CI / supply-chain controls

GitHub Actions runs with `contents: read`, checkout credentials are not persisted, action references are pinned to exact commit SHAs, and the CI-only Ruff dependency is version-pinned. Any future third-party Actions should likewise be pinned to immutable commit SHAs and granted only the permissions they require.

## Reporting a vulnerability

Do not include RPC passwords, monitor passwords, webhook secrets, private keys, or unredacted sensitive deployment data in a public issue. Provide a minimal reproducer and describe the affected commit/version and deployment assumptions.

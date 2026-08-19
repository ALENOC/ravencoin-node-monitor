# Changelog

All notable changes to Ravencoin Node Monitor are documented here.

## v1.0.0 — 2026-08-19

First stable release of **Ravencoin Node Monitor**.

### Monitoring

- Real-time Ravencoin Core health and synchronization status.
- Block height, headers, verification progress, network difficulty and estimated hashrate.
- Connected Ravencoin P2P peers with inbound/outbound, IPv4/IPv6/Tor and ping information.
- Mempool summary and transaction views.
- Recent block information.
- Host CPU load, temperature, RAM and swap monitoring.
- Storage usage, additional mounted disks and storage-growth estimates.
- Ravencoin P2P upload/download rates and cumulative traffic using Core `getnettotals`.
- RAM-backed historical charts by default to avoid unnecessary microSD/SSD writes.
- Event timeline for important node state changes.
- Optional RVN/USDT market data.
- Prometheus-compatible metrics, health/readiness probes and sanitized diagnostics.

### ElectrumX

- Optional ElectrumX monitoring.
- Version, uptime, DB height, peer-server state and connected Electrum client information.
- Safe host-side admin snapshot integration for deployments where ElectrumX admin RPC is loopback-only.

### Bandwidth control

- Optional host-side Linux `tc` controller.
- Independent public upload limits for Ravencoin Core and ElectrumX.
- Manual limits in B/s, KB/s, MB/s and GB/s.
- `0` / Unlimited removes the monitor-imposed bandwidth cap.
- Private Docker/LAN destinations are exempt so Core ↔ ElectrumX local traffic is not intentionally throttled.
- Core current P2P upload uses the same `getnettotals` sample shown in the main traffic card.

### Connection limits

- Ravencoin Core maximum P2P peers through native `-maxconnections=N`.
- ElectrumX maximum client sessions through native `MAX_SESSIONS=N`.
- Current connection count and current effective/default limit shown clearly in the dashboard.
- Native defaults displayed as 125 Core peers and 1,000 ElectrumX client sessions when no override is active.
- `0` means remove the monitor override and return to deployment/native defaults; it never means zero connections.
- Only the selected Core/ElectrumX service is recreated when applying a connection-limit change.

### Security

- Unprivileged dashboard container with `cap_drop: ALL`, read-only root filesystem and `no-new-privileges`.
- No Docker socket and no `CAP_NET_ADMIN` inside the web dashboard container.
- Privileged host operations isolated in a separate root-owned Unix-socket controller.
- Optional HTTP Basic authentication; authentication required for write-capable controls.
- Host-header validation to reduce DNS-rebinding exposure.
- Security headers and nonce-based Content Security Policy.
- RPC credentials can be provided through mounted secret files.
- Sanitized diagnostics endpoint based on an explicit safe-field allowlist.
- TLS certificate verification required for remote/FQDN ElectrumX TLS targets.
- Fixed request protocol and validation for host-controller write operations.
- GitHub Actions dependencies pinned to immutable commit SHAs and CI configured with minimal permissions.

### Deployment and compatibility

- Docker / Docker Compose deployment.
- Suitable for Raspberry Pi, Orange Pi, ARM SBCs, mini PCs, Linux servers and VPS hosts.
- Responsive desktop/tablet/mobile dashboard.
- Public Vercel demo isolated from private node credentials and clearly marking simulated/node-required values.
- Documented update, troubleshooting, privacy and optional-controller procedures.
- Tested against Ravencoin Core 4.8.0 and ElectrumX-RVN deployment patterns used by this project.

### Documentation

- Beginner-oriented README with installation and configuration walkthrough.
- Current dashboard screenshots.
- Dedicated bandwidth-control and connection-control documentation.
- Security policy and public-demo security boundary.

### Known limitations

- Historical data is RAM-only by default and is lost when the monitor restarts unless persistent SQLite storage is explicitly enabled.
- Public demo node-local values are simulated or unavailable by design and do not represent a private production node.
- Applying Core/ElectrumX connection limits requires restarting only the selected service because those native settings are read at process startup.
- The optional bandwidth/connection controller is Linux/Docker specific.

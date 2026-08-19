# Public Vercel Demo

This repository contains a deliberately separated public demonstration of the Ravencoin Node Monitor UI.

## What is live

The Vercel serverless endpoint `api/demo-data.js` fetches only public, credential-free data:

- `RVN/USDT` 24-hour ticker data from Binance's public market-data API.
- Ravencoin mainnet chain/node state from the public RavencoinExplorer.com API.
- Recent Ravencoin mainnet blocks from the public RavencoinExplorer.com API.

The demo refreshes these public feeds periodically and identifies their provenance in the UI.

## What is not live

The public demo is **not connected to the operator's Ravencoin Core or ElectrumX infrastructure**. The following require a real local deployment and therefore are either labelled `SIMULATED`, `NODE REQUIRED`, or `ELECTRUMX REQUIRED` in the demo:

- host CPU/load/memory/temperature;
- local disk/storage use and growth;
- this node's P2P upload/download rates and cumulative bytes;
- connected Ravencoin peer addresses;
- connected ElectrumX client addresses;
- backend build identity / compatibility evidence;
- local node health derived from those private/local inputs.

The simulated network-traffic card exists only to demonstrate how the real monitor renders `getnettotals` data. It never claims those sample numbers are public mainnet traffic.

## Vercel deployment

`vercel.json` rewrites `/` and `/demo` to `demo/index.html`. Vercel automatically exposes `api/demo-data.js` as `/api/demo-data`.

No environment variables or API keys are required. If the GitHub repository is already connected to a Vercel project, pushes and pull requests can produce preview deployments automatically. Production deployment should point the Vercel project at this repository and keep the repository root as the project root.

The `.vercelignore` file excludes the real Python/Docker monitor implementation from the public deployment. The Vercel site contains only the demo assets, public API aggregator, and deployment configuration.

## Security boundary

The public serverless function has no generic proxy functionality and accepts no upstream URL from the browser. Its outbound destinations are fixed in source code, avoiding an SSRF-style proxy surface. The response contains only sanitized fields from public APIs and no local RPC credentials, webhook URLs, private IP addresses, or ElectrumX admin data.

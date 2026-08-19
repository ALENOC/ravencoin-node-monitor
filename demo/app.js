"use strict";

const $ = (id) => document.getElementById(id);
const nf = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 });

function setText(id, value) {
  const el = $(id);
  if (el) el.textContent = value;
}

function fmtInt(value) {
  return Number.isFinite(value) ? Math.round(value).toLocaleString() : "—";
}

function fmtBytes(value) {
  if (!Number.isFinite(value)) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = value;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
  return `${v.toFixed(v < 10 && i > 0 ? 2 : 0)} ${units[i]}`;
}

function fmtAge(ts) {
  if (!Number.isFinite(ts)) return "—";
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
  return `${Math.floor(seconds / 86400)}d ${Math.floor((seconds % 86400) / 3600)}h`;
}

function setSource(id, ok) {
  const el = $(id);
  if (!el) return;
  el.classList.toggle("ok", !!ok);
  el.classList.toggle("fail", !ok);
}

function renderPrice(price) {
  if (!price) return;
  if (Number.isFinite(price.last_price)) setText("price", `$${price.last_price.toFixed(6)}`);
  if (Number.isFinite(price.high_24h)) setText("price-high", `$${price.high_24h.toFixed(6)}`);
  if (Number.isFinite(price.low_24h)) setText("price-low", `$${price.low_24h.toFixed(6)}`);
  if (Number.isFinite(price.volume_rvn_24h)) setText("price-volume", nf.format(price.volume_rvn_24h));
  const change = $("price-change");
  if (change && Number.isFinite(price.price_change_percent)) {
    change.textContent = `${price.price_change_percent >= 0 ? "+" : ""}${price.price_change_percent.toFixed(2)}%`;
    change.className = `change ${price.price_change_percent >= 0 ? "up" : "down"}`;
  }
}

function renderNetwork(network) {
  if (!network) return;
  setText("blocks", fmtInt(network.blocks));
  setText("headers", fmtInt(network.headers));
  setText("chain", network.chain || "—");
  setText("difficulty", Number.isFinite(network.difficulty) ? nf.format(network.difficulty) : "—");
  setText("source-peers", fmtInt(network.source_node_connections));
  setText("source-mempool", fmtInt(network.source_node_mempool_tx));
  setText("source-version", network.subversion ? network.subversion.replaceAll("/", "") : "—");
  if (Number.isFinite(network.verificationprogress)) {
    setText("verify", `${(network.verificationprogress * 100).toFixed(4)}%`);
  }
}

function renderBlocks(blocks) {
  const tbody = $("recent-blocks");
  if (!tbody) return;
  tbody.textContent = "";
  if (!Array.isArray(blocks) || blocks.length === 0) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 4;
    td.className = "placeholder";
    td.textContent = "Public recent-block data is temporarily unavailable.";
    tr.appendChild(td);
    tbody.appendChild(tr);
    return;
  }
  for (const block of blocks) {
    const tr = document.createElement("tr");
    const values = [fmtInt(block.height), fmtAge(block.time), fmtInt(block.tx_count), fmtBytes(block.size)];
    for (const value of values) {
      const td = document.createElement("td");
      td.textContent = value;
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
}

function animateSimulatedTraffic() {
  const t = Date.now() / 1000;
  const down = 38 + Math.sin(t / 5) * 8 + Math.sin(t / 2.7) * 3;
  const up = 17 + Math.cos(t / 6) * 5 + Math.sin(t / 3.4) * 2;
  setText("demo-down", `${Math.max(1, down).toFixed(1)} KB/s`);
  setText("demo-up", `${Math.max(1, up).toFixed(1)} KB/s`);
}

async function refresh() {
  const error = $("error");
  try {
    const response = await fetch("/api/demo-data", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (!data || data.demo !== true) throw new Error("unexpected demo response");

    renderPrice(data.live_public_data && data.live_public_data.price);
    renderNetwork(data.live_public_data && data.live_public_data.network);
    renderBlocks((data.live_public_data && data.live_public_data.recent_blocks) || []);

    const status = (data.sources && data.sources.status) || {};
    setSource("source-binance", status.binance_rvnusdt);
    setSource("source-chain", status.ravencoin_explorer_nodeinfo);
    setSource("source-blocks", status.ravencoin_explorer_blocks);

    const allLive = Object.values(status).every(Boolean);
    $("live-pill").classList.toggle("warn", !allLive);
    setText("live-text", allLive ? "Live public data" : "Partial public data");
    setText("health-score", allLive ? "LIVE" : "PARTIAL");
    setText("updated", new Date((data.generated_at || Date.now() / 1000) * 1000).toLocaleString());
    error.classList.remove("show");
  } catch (err) {
    setText("live-text", "Public feeds unavailable");
    $("live-pill").classList.add("warn");
    error.textContent = `The demo page is online, but its public data feeds could not be refreshed: ${String(err)}`;
    error.classList.add("show");
  }
}

animateSimulatedTraffic();
setInterval(animateSimulatedTraffic, 1000);
refresh();
setInterval(refresh, 15000);

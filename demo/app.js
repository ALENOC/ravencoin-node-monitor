"use strict";

const $ = (id) => document.getElementById(id);
const nf = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 });
let lastHistoryPoints = [];

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

function clearPrice() {
  setText("price", "—");
  setText("price-high", "—");
  setText("price-low", "—");
  setText("price-volume", "—");
  const change = $("price-change");
  if (change) {
    change.textContent = "";
    change.className = "change";
  }
}

function renderPrice(price) {
  if (!price || !Number.isFinite(price.last_price)) {
    clearPrice();
    return;
  }
  setText("price", `$${price.last_price.toFixed(6)}`);
  setText("price-high", Number.isFinite(price.high_24h) ? `$${price.high_24h.toFixed(6)}` : "—");
  setText("price-low", Number.isFinite(price.low_24h) ? `$${price.low_24h.toFixed(6)}` : "—");
  setText("price-volume", Number.isFinite(price.volume_rvn_24h) ? nf.format(price.volume_rvn_24h) : "—");
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
  if (Number.isFinite(network.verificationprogress)) setText("verify", `${(network.verificationprogress * 100).toFixed(4)}%`);
}

function renderMempool(network) {
  const count = network && Number.isFinite(network.source_node_mempool_tx)
    ? network.source_node_mempool_tx
    : null;
  setText("mempool-count", count === null ? "—" : fmtInt(count));
  setText("mempool-count-row", count === null ? "—" : fmtInt(count));
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

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function drawHistory(points) {
  const canvas = $("history-canvas");
  const empty = $("history-empty");
  if (!canvas || !empty) return;
  if (!Array.isArray(points) || points.length < 2) {
    canvas.classList.add("hidden");
    empty.classList.remove("hidden");
    return;
  }

  canvas.classList.remove("hidden");
  empty.classList.add("hidden");
  const rect = canvas.getBoundingClientRect();
  if (rect.width < 10 || rect.height < 10) return;
  const dpr = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
  canvas.width = Math.round(rect.width * dpr);
  canvas.height = Math.round(rect.height * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  const w = rect.width;
  const h = rect.height;
  const pad = { left: 58, right: 14, top: 16, bottom: 28 };
  const plotW = Math.max(1, w - pad.left - pad.right);
  const plotH = Math.max(1, h - pad.top - pad.bottom);
  const values = points.map((p) => p.close).filter(Number.isFinite);
  let min = Math.min(...values);
  let max = Math.max(...values);
  if (min === max) { min *= 0.995; max *= 1.005; }
  const margin = (max - min) * 0.08;
  min -= margin;
  max += margin;

  ctx.clearRect(0, 0, w, h);
  ctx.font = "11px -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif";
  ctx.lineWidth = 1;
  ctx.strokeStyle = cssVar("--border");
  ctx.fillStyle = cssVar("--muted");

  for (let i = 0; i <= 4; i += 1) {
    const y = pad.top + (plotH * i) / 4;
    ctx.beginPath();
    ctx.moveTo(pad.left, y);
    ctx.lineTo(w - pad.right, y);
    ctx.stroke();
    const value = max - ((max - min) * i) / 4;
    ctx.fillText(`$${value.toFixed(5)}`, 5, y + 4);
  }

  const firstTs = points[0].timestamp;
  const lastTs = points[points.length - 1].timestamp;
  const rangeSeconds = Math.max(1, lastTs - firstTs);
  const xFor = (ts) => pad.left + ((ts - firstTs) / rangeSeconds) * plotW;
  const yFor = (value) => pad.top + ((max - value) / (max - min)) * plotH;

  ctx.strokeStyle = cssVar("--accent");
  ctx.lineWidth = 2;
  ctx.beginPath();
  points.forEach((point, index) => {
    const x = xFor(point.timestamp);
    const y = yFor(point.close);
    if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.stroke();

  ctx.fillStyle = cssVar("--muted");
  ctx.textAlign = "left";
  ctx.fillText(new Date(firstTs * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }), pad.left, h - 8);
  ctx.textAlign = "right";
  ctx.fillText(new Date(lastTs * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }), w - pad.right, h - 8);
  ctx.textAlign = "left";
}

async function refreshHistory() {
  const rangeEl = $("history-range");
  const range = rangeEl ? rangeEl.value : "24h";
  setText("history-status", "Refreshing public history…");
  try {
    const response = await fetch(`/api/demo-history?range=${encodeURIComponent(range)}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (!data || data.demo !== true || !Array.isArray(data.points)) throw new Error("unexpected history response");
    lastHistoryPoints = data.points;
    drawHistory(lastHistoryPoints);
    setSource("source-history", true);
    setText("history-status", `${data.points.length} public samples · ${data.interval} · updated ${new Date((data.generated_at || Date.now() / 1000) * 1000).toLocaleTimeString()}`);
  } catch (err) {
    lastHistoryPoints = [];
    drawHistory([]);
    setSource("source-history", false);
    setText("history-status", `History unavailable · ${String(err)}`);
  }
}

async function refresh() {
  const error = $("error");
  try {
    const response = await fetch("/api/demo-data", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (!data || data.demo !== true) throw new Error("unexpected demo response");

    const live = data.live_public_data || {};
    renderPrice(live.price);
    renderNetwork(live.network);
    renderMempool(live.network);
    renderBlocks(live.recent_blocks || []);

    const status = (data.sources && data.sources.status) || {};
    setSource("source-binance", status.binance_rvnusdt);
    setSource("source-chain", status.ravencoin_explorer_nodeinfo);
    setSource("source-blocks", status.ravencoin_explorer_blocks);
    setText("price-feed", data.sources && data.sources.price ? data.sources.price : "Unavailable");
    const priceSource = $("source-binance");
    if (priceSource && data.sources && data.sources.price) priceSource.textContent = data.sources.price;

    const allLive = Object.values(status).every(Boolean);
    $("live-pill").classList.toggle("warn", !allLive);
    setText("live-text", allLive ? "Live public data" : "Partial public data");
    setText("health-score", allLive ? "LIVE" : "PARTIAL");
    setText("updated", new Date((data.generated_at || Date.now() / 1000) * 1000).toLocaleString());
    error.classList.remove("show");
  } catch (err) {
    clearPrice();
    setText("live-text", "Public feeds unavailable");
    $("live-pill").classList.add("warn");
    error.textContent = `The demo page is online, but its public data feeds could not be refreshed: ${String(err)}`;
    error.classList.add("show");
  }
}

animateSimulatedTraffic();
setInterval(animateSimulatedTraffic, 1000);
refresh();
refreshHistory();
setInterval(refresh, 10000);
setInterval(refreshHistory, 60000);

const rangeSelect = $("history-range");
if (rangeSelect) rangeSelect.addEventListener("change", refreshHistory);
let resizeTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => drawHistory(lastHistoryPoints), 120);
});

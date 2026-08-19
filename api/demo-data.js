const BINANCE_TICKER = "https://api.binance.com/api/v3/ticker/24hr?symbol=RVNUSDT";
const RVN_EXPLORER = "https://api.ravencoinexplorer.com";

async function fetchJson(url, timeoutMs = 7000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, {
      headers: { "User-Agent": "ravencoin-node-monitor-demo/1.0" },
      signal: controller.signal,
      cache: "no-store",
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return await response.json();
  } finally {
    clearTimeout(timer);
  }
}

function finiteNumber(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function sanitizePrice(raw) {
  if (!raw || typeof raw !== "object") return null;
  return {
    symbol: raw.symbol === "RVNUSDT" ? raw.symbol : "RVNUSDT",
    last_price: finiteNumber(raw.lastPrice),
    price_change_percent: finiteNumber(raw.priceChangePercent),
    high_24h: finiteNumber(raw.highPrice),
    low_24h: finiteNumber(raw.lowPrice),
    volume_rvn_24h: finiteNumber(raw.volume),
    quote_volume_usdt_24h: finiteNumber(raw.quoteVolume),
  };
}

function sanitizeNode(raw) {
  if (!raw || typeof raw !== "object" || raw.ok === false) return null;
  return {
    chain: typeof raw.chain === "string" ? raw.chain : null,
    blocks: finiteNumber(raw.blocks),
    headers: finiteNumber(raw.headers),
    difficulty: finiteNumber(raw.difficulty),
    verificationprogress: finiteNumber(raw.verificationprogress),
    source_node_connections: finiteNumber(raw.connections),
    version: finiteNumber(raw.version),
    subversion: typeof raw.subversion === "string" ? raw.subversion : null,
    source_node_mempool_tx: finiteNumber(raw.mempool_tx),
  };
}

function sanitizeBlocks(raw) {
  const items = raw && raw.data && Array.isArray(raw.data.items) ? raw.data.items : [];
  return items.slice(0, 8).map((item) => ({
    height: finiteNumber(item.height),
    hash: typeof item.hash === "string" ? item.hash : null,
    time: finiteNumber(item.time),
    time_iso: typeof item.time_iso === "string" ? item.time_iso : null,
    size: finiteNumber(item.size),
    difficulty: finiteNumber(item.difficulty),
    tx_count: finiteNumber(item.tx_count),
  }));
}

module.exports = async function handler(req, res) {
  if (req.method !== "GET") {
    res.setHeader("Allow", "GET");
    return res.status(405).json({ error: "method not allowed" });
  }

  res.setHeader("Cache-Control", "public, s-maxage=10, stale-while-revalidate=30");
  res.setHeader("X-Content-Type-Options", "nosniff");
  res.setHeader("Referrer-Policy", "no-referrer");

  const requests = await Promise.allSettled([
    fetchJson(BINANCE_TICKER),
    fetchJson(`${RVN_EXPLORER}/nodeinfo`),
    fetchJson(`${RVN_EXPLORER}/api/v1/blocks/latest?limit=8`),
  ]);

  const price = requests[0].status === "fulfilled" ? sanitizePrice(requests[0].value) : null;
  const network = requests[1].status === "fulfilled" ? sanitizeNode(requests[1].value) : null;
  const recentBlocks = requests[2].status === "fulfilled" ? sanitizeBlocks(requests[2].value) : [];

  const sourceStatus = {
    binance_rvnusdt: requests[0].status === "fulfilled" && price !== null,
    ravencoin_explorer_nodeinfo: requests[1].status === "fulfilled" && network !== null,
    ravencoin_explorer_blocks: requests[2].status === "fulfilled" && recentBlocks.length > 0,
  };

  return res.status(200).json({
    demo: true,
    generated_at: Date.now() / 1000,
    live_public_data: {
      price,
      network,
      recent_blocks: recentBlocks,
    },
    sources: {
      status: sourceStatus,
      price: "Binance public RVN/USDT ticker",
      chain: "RavencoinExplorer.com public API",
    },
    node_specific_data: {
      available: false,
      reason: "This public demo is not connected to a private Ravencoin Core or ElectrumX instance.",
      fields: [
        "local host resources",
        "local storage",
        "this node's P2P upload/download rates and cumulative traffic",
        "connected Ravencoin peers and their addresses",
        "connected ElectrumX clients",
        "backend identity/compatibility checks"
      ],
    },
  });
};

const BINANCE_BASES = [
  "https://data-api.binance.vision",
  "https://api.binance.com",
];

const RANGE_CONFIG = {
  "24h": { interval: "15m", limit: 96 },
  "7d": { interval: "1h", limit: 168 },
  "30d": { interval: "4h", limit: 180 },
};

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

async function fetchKlines(config) {
  const failures = [];
  for (const base of BINANCE_BASES) {
    const url = `${base}/api/v3/klines?symbol=RVNUSDT&interval=${config.interval}&limit=${config.limit}`;
    try {
      const raw = await fetchJson(url);
      if (!Array.isArray(raw)) throw new Error("unexpected response shape");
      return { raw, source: base, failures };
    } catch (error) {
      failures.push(`${base}: ${String(error)}`);
    }
  }
  throw new Error(failures.join("; "));
}

function finiteNumber(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function sanitizeKlines(raw) {
  return raw
    .map((item) => {
      if (!Array.isArray(item) || item.length < 6) return null;
      const tsMs = finiteNumber(item[0]);
      const open = finiteNumber(item[1]);
      const high = finiteNumber(item[2]);
      const low = finiteNumber(item[3]);
      const close = finiteNumber(item[4]);
      const volume = finiteNumber(item[5]);
      if ([tsMs, open, high, low, close, volume].some((value) => value === null)) return null;
      return {
        timestamp: tsMs / 1000,
        open,
        high,
        low,
        close,
        volume_rvn: volume,
      };
    })
    .filter(Boolean);
}

module.exports = async function handler(req, res) {
  if (req.method !== "GET") {
    res.setHeader("Allow", "GET");
    return res.status(405).json({ error: "method not allowed" });
  }

  const range = Object.prototype.hasOwnProperty.call(RANGE_CONFIG, req.query && req.query.range)
    ? req.query.range
    : "24h";
  const config = RANGE_CONFIG[range];

  res.setHeader("Cache-Control", "public, s-maxage=30, stale-while-revalidate=60");
  res.setHeader("X-Content-Type-Options", "nosniff");
  res.setHeader("Referrer-Policy", "no-referrer");

  try {
    const result = await fetchKlines(config);
    const points = sanitizeKlines(result.raw);
    if (points.length < 2) throw new Error("not enough valid history points");

    return res.status(200).json({
      demo: true,
      metric: "price_rvn_usdt",
      range,
      interval: config.interval,
      generated_at: Date.now() / 1000,
      source: result.source,
      fallback_failures: result.failures,
      points,
    });
  } catch (error) {
    return res.status(502).json({
      demo: true,
      metric: "price_rvn_usdt",
      range,
      points: [],
      error: String(error),
    });
  }
};

"""RVN/USDT ticker, stdlib only. Binance's public market-data endpoint is
used because it returns a genuine USDT pair (CoinGecko's simple/price only
carries a USD quote for this asset)."""

import json
import urllib.request

BINANCE_TICKER_URL = "https://api.binance.com/api/v3/ticker/24hr?symbol={symbol}"


def fetch_price(symbol, timeout=8):
    url = BINANCE_TICKER_URL.format(symbol=symbol)
    req = urllib.request.Request(url, headers={"User-Agent": "ravencoin-node-monitor"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())

    def _f(key):
        val = data.get(key)
        return float(val) if val is not None else None

    return {
        "symbol": data.get("symbol"),
        "last_price": _f("lastPrice"),
        "price_change_percent": _f("priceChangePercent"),
        "high_24h": _f("highPrice"),
        "low_24h": _f("lowPrice"),
        "volume_24h": _f("volume"),
    }

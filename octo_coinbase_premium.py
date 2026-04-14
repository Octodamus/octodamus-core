"""Coinbase premium signal: Coinbase BTC/ETH price vs Binance."""

import time

try:
    import httpx
except ImportError:
    httpx = None

_CACHE: dict = {}
_CACHE_TTL = 300  # 5 minutes

_BULL_THRESHOLD = 0.3   # % premium → bullish
_BEAR_THRESHOLD = -0.3  # % discount → bearish

_BINANCE_URL = "https://api.binance.com/api/v3/ticker/price"
_COINBASE_URL = "https://api.coinbase.com/v2/prices/{pair}/spot"

_ASSET_CONFIG = {
    "BTC": {"binance_symbol": "BTCUSDT", "coinbase_pair": "BTC-USD"},
    "ETH": {"binance_symbol": "ETHUSDT", "coinbase_pair": "ETH-USD"},
}


def _cached(key: str):
    entry = _CACHE.get(key)
    if entry and (time.time() - entry["ts"]) < _CACHE_TTL:
        return entry["data"]
    return None


def _store(key: str, data: dict):
    _CACHE[key] = {"ts": time.time(), "data": data}


def _neutral_result(note: str) -> dict:
    return {
        "coinbase_price": 0.0,
        "binance_price": 0.0,
        "premium_pct": 0.0,
        "signal": "neutral",
        "note": note,
    }


def _fetch_binance_price(symbol: str) -> float | None:
    if httpx is None:
        return None
    try:
        with httpx.Client(timeout=8) as client:
            resp = client.get(_BINANCE_URL, params={"symbol": symbol})
            resp.raise_for_status()
            return float(resp.json()["price"])
    except Exception:
        return None


def _fetch_coinbase_price(pair: str) -> float | None:
    if httpx is None:
        return None
    url = _COINBASE_URL.format(pair=pair)
    try:
        with httpx.Client(timeout=8) as client:
            resp = client.get(url)
            resp.raise_for_status()
            return float(resp.json()["data"]["amount"])
    except Exception:
        return None


def get_coinbase_premium(asset: str = "BTC") -> dict:
    """Return Coinbase premium signal dict for BTC or ETH."""
    asset = asset.upper()
    if asset not in _ASSET_CONFIG:
        return _neutral_result(f"Unsupported asset: {asset}. Use BTC or ETH.")

    key = f"cb_premium_{asset}"
    cached = _cached(key)
    if cached:
        return cached

    config = _ASSET_CONFIG[asset]

    binance_price = _fetch_binance_price(config["binance_symbol"])
    coinbase_price = _fetch_coinbase_price(config["coinbase_pair"])

    if binance_price is None or coinbase_price is None:
        result = _neutral_result(
            f"Could not fetch prices — Binance: {binance_price}, Coinbase: {coinbase_price}"
        )
        _store(key, result)
        return result

    premium_pct = ((coinbase_price - binance_price) / binance_price) * 100

    if premium_pct > _BULL_THRESHOLD:
        signal = "bull"
        note = (
            f"Coinbase trades at +{premium_pct:.3f}% vs Binance — "
            f"US institutional/retail demand dominant (bullish)"
        )
    elif premium_pct < _BEAR_THRESHOLD:
        signal = "bear"
        note = (
            f"Coinbase trades at {premium_pct:.3f}% vs Binance — "
            f"Asia-led selling or US deleveraging (bearish)"
        )
    else:
        signal = "neutral"
        note = (
            f"Coinbase premium of {premium_pct:.3f}% — within neutral band "
            f"(±{_BULL_THRESHOLD}%)"
        )

    result = {
        "coinbase_price": round(coinbase_price, 4),
        "binance_price": round(binance_price, 4),
        "premium_pct": round(premium_pct, 4),
        "signal": signal,
        "note": note,
    }
    _store(key, result)
    return result


def coinbase_premium_context_str(asset: str = "BTC") -> str:
    """Return a formatted string for prompt injection."""
    try:
        p = get_coinbase_premium(asset)
        return (
            f"[COINBASE PREMIUM | {asset.upper()}]\n"
            f"  Coinbase Price : ${p['coinbase_price']:,.2f}\n"
            f"  Binance Price  : ${p['binance_price']:,.2f}\n"
            f"  Premium        : {p['premium_pct']:+.3f}%\n"
            f"  Signal         : {p['signal'].upper()}\n"
            f"  Note           : {p['note']}\n"
        )
    except Exception:
        return ""

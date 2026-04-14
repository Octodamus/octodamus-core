"""Stablecoin flow monitor using CoinGecko free API."""

import time

try:
    import httpx
except ImportError:
    httpx = None

_CACHE: dict = {}
_CACHE_TTL = 14400  # 4 hours

_MINT_THRESHOLD_BN = 0.5   # $500M
_BURN_THRESHOLD_BN = -0.5  # -$500M


def _cached(key: str):
    entry = _CACHE.get(key)
    if entry and (time.time() - entry["ts"]) < _CACHE_TTL:
        return entry["data"]
    return None


def _store(key: str, data: dict):
    _CACHE[key] = {"ts": time.time(), "data": data}


def _neutral_result(note: str) -> dict:
    return {
        "usdt_supply_bn": 0.0,
        "usdc_supply_bn": 0.0,
        "usdt_7d_change_bn": 0.0,
        "usdc_7d_change_bn": 0.0,
        "total_7d_change_bn": 0.0,
        "signal": "neutral",
        "note": note,
    }


def _fetch_coin_data(coin_id: str) -> dict:
    """Fetch market_data for a stablecoin from CoinGecko."""
    if httpx is None:
        return {}
    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}"
    params = {
        "localization": "false",
        "tickers": "false",
        "market_data": "true",
        "community_data": "false",
        "developer_data": "false",
        "sparkline": "false",
    }
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
    except Exception:
        return {}


def _extract_supply(data: dict) -> tuple[float, float]:
    """Return (current_supply_bn, 7d_change_bn)."""
    try:
        market = data.get("market_data", {})
        current = market.get("market_cap", {}).get("usd", 0) or 0
        change_7d_pct = market.get("market_cap_change_percentage_24h", 0) or 0  # fallback
        # Use 7d price change as proxy for market-cap change direction
        # (stablecoins hold $1, so mc change ≈ supply change)
        change_7d_mc = market.get("market_cap_change_24h", 0) or 0
        # Prefer dedicated 7d field if available
        current_bn = current / 1e9
        # CoinGecko doesn't expose 7d mcap delta directly; use percentage_7d on price
        # For stablecoins price ~$1 so we approximate: 7d supply delta via sparkline or pct
        pct_7d = market.get("market_cap_change_percentage_7d_in_currency", {})
        if isinstance(pct_7d, dict):
            pct_7d = pct_7d.get("usd", 0) or 0
        else:
            pct_7d = float(pct_7d) if pct_7d else 0.0
        change_7d_bn = current_bn * (pct_7d / 100)
        return current_bn, change_7d_bn
    except Exception:
        return 0.0, 0.0


def get_stablecoin_signal() -> dict:
    """Return stablecoin flow signal dict."""
    key = "stablecoin_signal"
    cached = _cached(key)
    if cached:
        return cached

    try:
        usdt_data = _fetch_coin_data("tether")
        usdc_data = _fetch_coin_data("usd-coin")

        if not usdt_data and not usdc_data:
            result = _neutral_result("Could not fetch stablecoin data from CoinGecko")
            _store(key, result)
            return result

        usdt_bn, usdt_7d = _extract_supply(usdt_data)
        usdc_bn, usdc_7d = _extract_supply(usdc_data)
        total_7d = usdt_7d + usdc_7d

        if total_7d >= _MINT_THRESHOLD_BN:
            signal = "bull"
            note = f"Net mint of ${total_7d:.2f}B in 7d — new capital entering crypto"
        elif total_7d <= _BURN_THRESHOLD_BN:
            signal = "bear"
            note = f"Net burn of ${abs(total_7d):.2f}B in 7d — capital exiting crypto"
        else:
            signal = "neutral"
            note = f"Stablecoin supply change of ${total_7d:.2f}B in 7d — no strong flow signal"

        result = {
            "usdt_supply_bn": round(usdt_bn, 2),
            "usdc_supply_bn": round(usdc_bn, 2),
            "usdt_7d_change_bn": round(usdt_7d, 2),
            "usdc_7d_change_bn": round(usdc_7d, 2),
            "total_7d_change_bn": round(total_7d, 2),
            "signal": signal,
            "note": note,
        }
    except Exception as e:
        result = _neutral_result(f"Error computing stablecoin signal: {e}")

    _store(key, result)
    return result


def stablecoin_context_str() -> str:
    """Return a formatted string for prompt injection."""
    try:
        sig = get_stablecoin_signal()
        return (
            f"[STABLECOIN FLOWS]\n"
            f"  USDT Supply : ${sig['usdt_supply_bn']:.1f}B  (7d Δ: ${sig['usdt_7d_change_bn']:+.2f}B)\n"
            f"  USDC Supply : ${sig['usdc_supply_bn']:.1f}B  (7d Δ: ${sig['usdc_7d_change_bn']:+.2f}B)\n"
            f"  Net 7d Flow : ${sig['total_7d_change_bn']:+.2f}B\n"
            f"  Signal      : {sig['signal'].upper()}\n"
            f"  Note        : {sig['note']}\n"
        )
    except Exception:
        return ""

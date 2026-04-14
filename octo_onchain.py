"""On-chain signal module using free public APIs (no API keys required)."""

import time
from datetime import datetime, timezone

try:
    import httpx
except ImportError:
    httpx = None

_CACHE: dict = {}
_CACHE_TTL = 1800  # 30 minutes


def _cached(key: str):
    entry = _CACHE.get(key)
    if entry and (time.time() - entry["ts"]) < _CACHE_TTL:
        return entry["data"]
    return None


def _store(key: str, data: dict):
    _CACHE[key] = {"ts": time.time(), "data": data}


def _neutral(note: str) -> dict:
    return {
        "exchange_flow": "NEUTRAL",
        "active_addr_trend": "flat",
        "signal": "neutral",
        "note": note,
    }


def _get_blockchain_stats() -> dict:
    """Fetch BTC stats from blockchain.com public API."""
    if httpx is None:
        return {}
    base = "https://blockchain.info/q"
    try:
        with httpx.Client(timeout=10) as client:
            tx_24h = float(client.get(f"{base}/24hrtransactioncount").text.strip())
            tx_month = float(client.get(f"{base}/n-transactions-this-month").text.strip())
        return {"tx_24h": tx_24h, "tx_month": tx_month}
    except Exception:
        return {}


def _get_coingecko_coin(coin_id: str) -> dict:
    """Fetch coin data from CoinGecko free API."""
    if httpx is None:
        return {}
    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}"
    params = {
        "localization": "false",
        "tickers": "false",
        "market_data": "true",
        "community_data": "false",
        "developer_data": "true",
        "sparkline": "false",
    }
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
    except Exception:
        return {}


def _btc_signal() -> dict:
    stats = _get_blockchain_stats()
    if not stats:
        return _neutral("Could not fetch BTC blockchain data")

    tx_24h = stats.get("tx_24h", 0)
    tx_month = stats.get("tx_month", 0)

    # Daily average from monthly count
    daily_avg = tx_month / 30 if tx_month else 0

    # Active address trend: 24h vs daily average
    if daily_avg > 0:
        ratio = tx_24h / daily_avg
        if ratio > 1.05:
            active_trend = "rising"
        elif ratio < 0.95:
            active_trend = "falling"
        else:
            active_trend = "flat"
    else:
        active_trend = "flat"

    # Exchange flow proxy: very high tx volume suggests exchange activity (inflow pressure)
    # Threshold: >400k tx/day historically correlates with heavy exchange inflows
    if tx_24h > 400_000:
        exchange_flow = "INFLOW"
    elif tx_24h < 280_000:
        exchange_flow = "OUTFLOW"
    else:
        exchange_flow = "NEUTRAL"

    # Combine signals
    bull_points = (1 if exchange_flow == "OUTFLOW" else 0) + (1 if active_trend == "rising" else 0)
    bear_points = (1 if exchange_flow == "INFLOW" else 0) + (1 if active_trend == "falling" else 0)

    if bull_points > bear_points:
        signal = "bull"
    elif bear_points > bull_points:
        signal = "bear"
    else:
        signal = "neutral"

    note = (
        f"BTC 24h txs: {tx_24h:,.0f} | monthly avg/day: {daily_avg:,.0f} | "
        f"flow proxy: {exchange_flow} | addr trend: {active_trend}"
    )
    return {
        "exchange_flow": exchange_flow,
        "active_addr_trend": active_trend,
        "signal": signal,
        "note": note,
    }


def _eth_signal() -> dict:
    data = _get_coingecko_coin("ethereum")
    if not data:
        return _neutral("Could not fetch ETH CoinGecko data")

    try:
        dev_data = data.get("developer_data", {})
        tx_30d = dev_data.get("commit_count_4_weeks", 0) or 0  # fallback metric
        # Use market_data volume as activity proxy
        market = data.get("market_data", {})
        vol_24h = market.get("total_volume", {}).get("usd", 0) or 0
        price_change_7d = market.get("price_change_percentage_7d", 0) or 0

        # Activity proxy: volume relative to market cap
        mcap = market.get("market_cap", {}).get("usd", 1) or 1
        vol_ratio = vol_24h / mcap

        if vol_ratio > 0.06:
            active_trend = "rising"
            exchange_flow = "INFLOW" if price_change_7d < 0 else "OUTFLOW"
        elif vol_ratio < 0.03:
            active_trend = "falling"
            exchange_flow = "NEUTRAL"
        else:
            active_trend = "flat"
            exchange_flow = "NEUTRAL"

        bull = (1 if exchange_flow == "OUTFLOW" else 0) + (1 if active_trend == "rising" else 0)
        bear = (1 if exchange_flow == "INFLOW" else 0) + (1 if active_trend == "falling" else 0)
        signal = "bull" if bull > bear else ("bear" if bear > bull else "neutral")

        note = (
            f"ETH vol/mcap ratio: {vol_ratio:.3f} | 7d price chg: {price_change_7d:.1f}% | "
            f"flow: {exchange_flow} | activity: {active_trend}"
        )
        return {
            "exchange_flow": exchange_flow,
            "active_addr_trend": active_trend,
            "signal": signal,
            "note": note,
        }
    except Exception as e:
        return _neutral(f"ETH signal parse error: {e}")


def _sol_signal() -> dict:
    data = _get_coingecko_coin("solana")
    if not data:
        return _neutral("Could not fetch SOL CoinGecko data")

    try:
        market = data.get("market_data", {})
        vol_24h = market.get("total_volume", {}).get("usd", 0) or 0
        mcap = market.get("market_cap", {}).get("usd", 1) or 1
        price_change_7d = market.get("price_change_percentage_7d", 0) or 0
        price_change_24h = market.get("price_change_percentage_24h", 0) or 0

        vol_ratio = vol_24h / mcap

        if vol_ratio > 0.08:
            active_trend = "rising"
            exchange_flow = "INFLOW" if price_change_24h < -1 else "OUTFLOW"
        elif vol_ratio < 0.03:
            active_trend = "falling"
            exchange_flow = "NEUTRAL"
        else:
            active_trend = "flat"
            exchange_flow = "NEUTRAL"

        bull = (1 if exchange_flow == "OUTFLOW" else 0) + (1 if active_trend == "rising" else 0)
        bear = (1 if exchange_flow == "INFLOW" else 0) + (1 if active_trend == "falling" else 0)
        signal = "bull" if bull > bear else ("bear" if bear > bull else "neutral")

        note = (
            f"SOL vol/mcap: {vol_ratio:.3f} | 7d: {price_change_7d:.1f}% | 24h: {price_change_24h:.1f}% | "
            f"flow: {exchange_flow} | activity: {active_trend}"
        )
        return {
            "exchange_flow": exchange_flow,
            "active_addr_trend": active_trend,
            "signal": signal,
            "note": note,
        }
    except Exception as e:
        return _neutral(f"SOL signal parse error: {e}")


def get_onchain_signal(asset: str) -> dict:
    """Return on-chain signal dict for the given asset (BTC, ETH, SOL)."""
    asset = asset.upper()
    key = f"onchain_{asset}"
    cached = _cached(key)
    if cached:
        return cached

    try:
        if asset == "BTC":
            result = _btc_signal()
        elif asset == "ETH":
            result = _eth_signal()
        elif asset == "SOL":
            result = _sol_signal()
        else:
            result = _neutral(f"Unsupported asset: {asset}")
    except Exception as e:
        result = _neutral(f"Unexpected error for {asset}: {e}")

    _store(key, result)
    return result


def onchain_context_str(asset: str) -> str:
    """Return a formatted string for prompt injection."""
    try:
        sig = get_onchain_signal(asset)
        return (
            f"[ON-CHAIN | {asset.upper()}]\n"
            f"  Exchange Flow : {sig['exchange_flow']}\n"
            f"  Active Addr   : {sig['active_addr_trend']}\n"
            f"  Signal        : {sig['signal'].upper()}\n"
            f"  Note          : {sig['note']}\n"
        )
    except Exception:
        return ""

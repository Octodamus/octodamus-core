"""
octo_gecko.py — OctoGecko Extended Crypto Intelligence Mind
CoinGecko free tier API. No API key required.

v2 fixes:
- run_gecko_scan() always returns a dict (never None or partial)
- _get_global() failure returns {} not None — stored as {} in result
- All internal helpers return [] or {} on failure, never None
- Added btc_dominance at top level for easy access
"""

import time
import requests
from datetime import datetime

GECKO_BASE = "https://api.coingecko.com/api/v3"
HEADERS    = {"User-Agent": "octodamus-oracle/1.0 (@octodamusai)"}
_DELAY     = 1.2

TRACK_IDS = [
    "bitcoin", "ethereum", "solana", "binancecoin", "ripple",
    "cardano", "avalanche-2", "polkadot", "chainlink", "uniswap",
    "dogecoin", "shiba-inu", "pepe", "sui", "aptos",
]


def _get_global() -> dict:
    """Fetch global crypto market data. Always returns dict."""
    try:
        r = requests.get(f"{GECKO_BASE}/global", headers=HEADERS, timeout=12)
        r.raise_for_status()
        data = r.json().get("data") or {}
        btc_dom = round(float((data.get("market_cap_percentage") or {}).get("btc", 0) or 0), 1)
        eth_dom = round(float((data.get("market_cap_percentage") or {}).get("eth", 0) or 0), 1)
        return {
            "total_market_cap_usd": (data.get("total_market_cap") or {}).get("usd"),
            "total_volume_24h":     (data.get("total_volume") or {}).get("usd"),
            "btc_dominance":        btc_dom,
            "eth_dominance":        eth_dom,
            "market_cap_change_24h": data.get("market_cap_change_percentage_24h_usd"),
            "active_coins":          data.get("active_cryptocurrencies"),
        }
    except Exception as e:
        print(f"[OctoGecko] Global data failed: {e}")
        return {}


def _get_trending() -> list:
    """Fetch trending coins. Always returns list."""
    try:
        r = requests.get(f"{GECKO_BASE}/search/trending", headers=HEADERS, timeout=12)
        r.raise_for_status()
        coins = r.json().get("coins") or []
        return [
            {
                "name":      c["item"]["name"],
                "symbol":    c["item"]["symbol"].upper(),
                "rank":      c["item"].get("market_cap_rank"),
                "price_btc": c["item"].get("price_btc"),
            }
            for c in coins[:7]
        ]
    except Exception as e:
        print(f"[OctoGecko] Trending fetch failed: {e}")
        return []


def _get_prices(ids: list) -> list:
    """Fetch price/volume/change data. Always returns list."""
    try:
        r = requests.get(
            f"{GECKO_BASE}/coins/markets",
            params={
                "vs_currency": "usd",
                "ids": ",".join(ids),
                "order": "market_cap_desc",
                "per_page": len(ids),
                "page": 1,
                "sparkline": "false",
                "price_change_percentage": "24h,7d",
            },
            headers=HEADERS,
            timeout=15,
        )
        r.raise_for_status()
        results = []
        for c in (r.json() or []):
            results.append({
                "id":         c.get("id", ""),
                "symbol":     str(c.get("symbol", "")).upper(),
                "name":       c.get("name", ""),
                "price":      c.get("current_price"),
                "market_cap": c.get("market_cap"),
                "volume_24h": c.get("total_volume"),
                "chg_24h":    c.get("price_change_percentage_24h"),
                "chg_7d":     c.get("price_change_percentage_7d_in_currency"),
                "rank":       c.get("market_cap_rank"),
            })
        return results
    except Exception as e:
        print(f"[OctoGecko] Price fetch failed: {e}")
        return []


def run_gecko_scan() -> dict:
    """
    Full CoinGecko scan. Always returns a complete dict — never None.
    Keys: timestamp, global, btc_dominance, trending, prices, gainers, losers, dom_signal
    """
    print("[OctoGecko] Scanning CoinGecko...")

    global_data = _get_global()
    btc_dom = float(global_data.get("btc_dominance", 50) or 50)

    if global_data:
        print(f"  BTC dominance: {btc_dom}%")
        mcap = global_data.get("total_market_cap_usd")
        if mcap:
            print(f"  Total market cap: ${mcap/1e12:.2f}T")
    time.sleep(_DELAY)

    trending = _get_trending()
    print(f"  Trending: {', '.join(c['symbol'] for c in trending[:5])}")
    time.sleep(_DELAY)

    prices = _get_prices(TRACK_IDS)

    gainers = sorted(
        [p for p in prices if p.get("chg_24h") is not None],
        key=lambda x: x["chg_24h"], reverse=True
    )[:3]
    losers = sorted(
        [p for p in prices if p.get("chg_24h") is not None],
        key=lambda x: x["chg_24h"]
    )[:3]

    for p in gainers:
        print(f"  GAINER {p['symbol']:6s} {p['chg_24h']:+.1f}%")
    for p in losers:
        print(f"  LOSER  {p['symbol']:6s} {p['chg_24h']:+.1f}%")

    if btc_dom > 55:
        dom_signal = "BTC dominance HIGH — risk-off, alts underperforming"
    elif btc_dom < 45:
        dom_signal = "BTC dominance LOW — alt season conditions"
    else:
        dom_signal = "BTC dominance NEUTRAL — balanced market"

    return {
        "timestamp":      datetime.utcnow().isoformat(),
        "global":         global_data,            # dict (may be empty on failure)
        "btc_dominance":  btc_dom,                # top-level for easy access
        "trending":       trending,
        "prices":         prices,
        "gainers":        gainers,
        "losers":         losers,
        "dom_signal":     dom_signal,
    }


def format_gecko_for_prompt(result: dict) -> str:
    result = result or {}
    lines = ["Extended crypto (OctoGecko/CoinGecko):"]
    g = result.get("global") or {}
    if g:
        mcap = g.get("total_market_cap_usd")
        mcap_str = f"${mcap/1e12:.2f}T" if mcap else "--"
        lines.append(f"  Total market cap: {mcap_str} | BTC dominance: {g.get('btc_dominance','?')}%")
        chg = g.get("market_cap_change_24h")
        if chg is not None:
            lines.append(f"  Market cap 24h: {chg:+.1f}%")
    lines.append(f"  {result.get('dom_signal','')}")
    gainers = result.get("gainers") or []
    losers  = result.get("losers") or []
    if gainers:
        lines.append("  Top gainers 24h: " + ", ".join(f"{c['symbol']} {c['chg_24h']:+.1f}%" for c in gainers))
    if losers:
        lines.append("  Top losers 24h:  " + ", ".join(f"{c['symbol']} {c['chg_24h']:+.1f}%" for c in losers))
    trending = result.get("trending") or []
    if trending:
        lines.append("  Trending: " + ", ".join(c["symbol"] for c in trending[:5]))
    return "\n".join(lines)


if __name__ == "__main__":
    result = run_gecko_scan()
    print(f"\n── OctoGecko Report ──────────────────────")
    g = result.get("global") or {}
    mcap = g.get("total_market_cap_usd")
    if mcap:
        print(f"Market cap: ${mcap/1e12:.2f}T | BTC dom: {g.get('btc_dominance')}%")
    print(f"Trending: {[c['symbol'] for c in result.get('trending', [])]}")
    gainers_str = [(c['symbol'], str(round(c['chg_24h'], 1)) + "%") for c in result.get('gainers', [])]
    losers_str  = [(c['symbol'], str(round(c['chg_24h'], 1)) + "%") for c in result.get('losers', [])]
    print(f"Gainers: {gainers_str}")
    print(f"Losers:  {losers_str}")

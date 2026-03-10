"""
octo_gecko.py
OctoGecko — Extended Crypto Intelligence Mind

CoinGecko free tier API.
No API key required for basic endpoints (rate limit ~30 req/min).

Covers:
  - Top 50 coins by market cap (volume, dominance, 24h change)
  - BTC dominance
  - Total crypto market cap
  - Trending coins (what's gaining attention)
  - Gainers and losers

Usage:
    from octo_gecko import run_gecko_scan, format_gecko_for_prompt
    gecko = run_gecko_scan()
"""

import time
import requests
from datetime import datetime

GECKO_BASE = "https://api.coingecko.com/api/v3"
HEADERS    = {"User-Agent": "octodamus-oracle/1.0 (@octodamusai)"}
_DELAY     = 1.2  # CoinGecko is strict on free tier rate limits

TRACK_IDS  = [
    "bitcoin", "ethereum", "solana", "binancecoin", "ripple",
    "cardano", "avalanche-2", "polkadot", "chainlink", "uniswap",
    "dogecoin", "shiba-inu", "pepe", "sui", "aptos",
]


def _get_global() -> dict | None:
    """Fetch global crypto market data."""
    try:
        r = requests.get(f"{GECKO_BASE}/global", headers=HEADERS, timeout=10)
        r.raise_for_status()
        data = r.json().get("data", {})
        return {
            "total_market_cap_usd": data.get("total_market_cap", {}).get("usd"),
            "total_volume_24h":     data.get("total_volume", {}).get("usd"),
            "btc_dominance":        round(data.get("market_cap_percentage", {}).get("btc", 0), 1),
            "eth_dominance":        round(data.get("market_cap_percentage", {}).get("eth", 0), 1),
            "market_cap_change_24h":data.get("market_cap_change_percentage_24h_usd"),
            "active_coins":         data.get("active_cryptocurrencies"),
        }
    except Exception as e:
        print(f"[OctoGecko] Global data failed: {e}")
        return None


def _get_trending() -> list[dict]:
    """Fetch trending coins on CoinGecko."""
    try:
        r = requests.get(f"{GECKO_BASE}/search/trending", headers=HEADERS, timeout=10)
        r.raise_for_status()
        coins = r.json().get("coins", [])
        return [
            {
                "name":   c["item"]["name"],
                "symbol": c["item"]["symbol"].upper(),
                "rank":   c["item"].get("market_cap_rank"),
                "price_btc": c["item"].get("price_btc"),
            }
            for c in coins[:7]
        ]
    except Exception as e:
        print(f"[OctoGecko] Trending fetch failed: {e}")
        return []


def _get_prices(ids: list) -> list[dict]:
    """Fetch price/volume/change data for a list of coin IDs."""
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
        for c in r.json():
            results.append({
                "id":        c["id"],
                "symbol":    c["symbol"].upper(),
                "name":      c["name"],
                "price":     c.get("current_price"),
                "market_cap":c.get("market_cap"),
                "volume_24h":c.get("total_volume"),
                "chg_24h":   c.get("price_change_percentage_24h"),
                "chg_7d":    c.get("price_change_percentage_7d_in_currency"),
                "rank":      c.get("market_cap_rank"),
            })
        return results
    except Exception as e:
        print(f"[OctoGecko] Price fetch failed: {e}")
        return []


def run_gecko_scan() -> dict:
    """Full CoinGecko scan — global market + trending + tracked coins."""
    print("[OctoGecko] Scanning CoinGecko...")

    global_data = _get_global()
    if global_data:
        print(f"  BTC dominance: {global_data['btc_dominance']}%")
        mcap = global_data["total_market_cap_usd"]
        if mcap:
            print(f"  Total market cap: ${mcap/1e12:.2f}T")
    time.sleep(_DELAY)

    trending = _get_trending()
    print(f"  Trending: {', '.join(c['symbol'] for c in trending[:5])}")
    time.sleep(_DELAY)

    prices = _get_prices(TRACK_IDS)

    # Identify movers
    gainers = sorted([p for p in prices if p["chg_24h"] is not None], key=lambda x: x["chg_24h"], reverse=True)[:3]
    losers  = sorted([p for p in prices if p["chg_24h"] is not None], key=lambda x: x["chg_24h"])[:3]

    for p in gainers:
        print(f"  GAINER {p['symbol']:6s} {p['chg_24h']:+.1f}%")
    for p in losers:
        print(f"  LOSER  {p['symbol']:6s} {p['chg_24h']:+.1f}%")

    # Market sentiment from dominance
    btc_dom = global_data["btc_dominance"] if global_data else 50
    if btc_dom > 55:
        dom_signal = "BTC dominance HIGH — risk-off, alts underperforming"
    elif btc_dom < 45:
        dom_signal = "BTC dominance LOW — alt season conditions"
    else:
        dom_signal = "BTC dominance NEUTRAL — balanced market"

    return {
        "timestamp":    datetime.utcnow().isoformat(),
        "global":       global_data,
        "trending":     trending,
        "prices":       prices,
        "gainers":      gainers,
        "losers":       losers,
        "dom_signal":   dom_signal,
    }


def format_gecko_for_prompt(result: dict) -> str:
    lines = ["Extended crypto (OctoGecko/CoinGecko):"]
    g = result.get("global")
    if g:
        mcap = g["total_market_cap_usd"]
        mcap_str = f"${mcap/1e12:.2f}T" if mcap else "--"
        lines.append(f"  Total market cap: {mcap_str} | BTC dominance: {g['btc_dominance']}%")
        if g.get("market_cap_change_24h") is not None:
            lines.append(f"  Market cap 24h: {g['market_cap_change_24h']:+.1f}%")
    lines.append(f"  {result.get('dom_signal','')}")
    gainers = result.get("gainers", [])
    losers  = result.get("losers", [])
    if gainers:
        lines.append("  Top gainers 24h: " + ", ".join(f"{c['symbol']} {c['chg_24h']:+.1f}%" for c in gainers))
    if losers:
        lines.append("  Top losers 24h:  " + ", ".join(f"{c['symbol']} {c['chg_24h']:+.1f}%" for c in losers))
    trending = result.get("trending", [])
    if trending:
        lines.append("  Trending: " + ", ".join(c["symbol"] for c in trending[:5]))
    return "\n".join(lines)


if __name__ == "__main__":
    result = run_gecko_scan()
    print(f"\n── OctoGecko Report ──────────────────────")
    g = result.get("global")
    if g:
        print(f"Market cap: ${g['total_market_cap_usd']/1e12:.2f}T | BTC dom: {g['btc_dominance']}%")
    print(f"\nTrending: {[c['symbol'] for c in result['trending']]}")
    gainers_str = [(c['symbol'], f"{c['chg_24h']:+.1f}%") for c in result['gainers']]
    print(f"Gainers:  {gainers_str}")
    losers_str = [(c['symbol'], f"{c['chg_24h']:+.1f}%") for c in result['losers']]
    print(f"Losers:  {losers_str}")

"""
octo_gecko.py — OctoGecko Extended Crypto Intelligence Mind
CoinGecko free tier API. No API key required.

v2 fixes:
- run_gecko_scan() always returns a dict (never None or partial)
- _get_global() failure returns {} not None — stored as {} in result
- All internal helpers return [] or {} on failure, never None
- Added btc_dominance at top level for easy access
"""

import os
import time
import json
import requests
from datetime import datetime
from pathlib import Path

GECKO_BASE = "https://api.coingecko.com/api/v3"
_DELAY     = 1.2


def _headers() -> dict:
    """Request headers, including a CoinGecko Demo API key when one is configured.

    The keyless free tier rate-limits hard (HTTP 429) and the IP is shared across ~20
    Octodamus modules. A free Demo key (COINGECKO_API_KEY in .octo_secrets, loaded to env
    by bitwarden.load_all_secrets) lifts the limit to ~30 calls/min and largely ends 429s.
    Falls back to keyless behaviour when no key is set.
    """
    h = {"User-Agent": "octodamus-oracle/1.0 (@octodamusai)"}
    key = os.environ.get("COINGECKO_API_KEY", "")
    if key:
        h["x-cg-demo-api-key"] = key
    return h


# CoinGecko free tier rate-limits hard (HTTP 429). The API server, runner, and Telegram bot
# each hit these fetchers from separate processes that share one outbound IP, so an in-process
# cache alone can't stop the 429 spam. Back the cache with a small disk file so a fresh process
# starts warm and serves the last good value on failure instead of re-hammering the free tier.
_CACHE_TTL   = 300  # seconds — 5-min market granularity is fine for oracle prompts
_CACHE_FILE  = Path(__file__).parent / "data" / "gecko_cache.json"


def _read_disk_cache() -> dict:
    try:
        return json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_disk_cache(cache: dict) -> None:
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _CACHE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache), encoding="utf-8")
        tmp.replace(_CACHE_FILE)          # atomic on Windows + POSIX
    except Exception:
        pass


_CACHE: dict = _read_disk_cache()          # {ck: [ts, val]} — shared across processes via disk


def _ttl_cache(key: str, ttl: int = _CACHE_TTL):
    def deco(fn):
        def wrap(*args, **kwargs):
            ck = f"{key}:{args!r}"
            hit = _CACHE.get(ck)
            if hit and (time.time() - hit[0]) < ttl:
                return hit[1]
            val = fn(*args, **kwargs)
            if val:                       # only cache real data
                _CACHE[ck] = (time.time(), val)
                _write_disk_cache(_CACHE)
                return val
            if hit:
                return hit[1]             # serve stale on 429/empty instead of nothing
            fresh = _read_disk_cache().get(ck)  # another process may have refreshed since import
            return fresh[1] if fresh else val
        return wrap
    return deco

TRACK_IDS = [
    "bitcoin", "ethereum", "solana", "binancecoin", "ripple",
    "cardano", "avalanche-2", "polkadot", "chainlink", "uniswap",
    "dogecoin", "shiba-inu", "pepe", "sui", "aptos",
]


@_ttl_cache("global")
def _get_global() -> dict:
    """Fetch global crypto market data. Always returns dict."""
    try:
        r = requests.get(f"{GECKO_BASE}/global", headers=_headers(), timeout=12)
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


@_ttl_cache("trending")
def _get_trending() -> list:
    """Fetch trending coins. Always returns list."""
    try:
        r = requests.get(f"{GECKO_BASE}/search/trending", headers=_headers(), timeout=12)
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


@_ttl_cache("prices")
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
            headers=_headers(),
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


# ID -> symbol for the degraded fallback below (/simple/price omits symbols).
_ID_SYMBOL = {
    "bitcoin": "BTC", "ethereum": "ETH", "solana": "SOL", "binancecoin": "BNB",
    "ripple": "XRP", "cardano": "ADA", "avalanche-2": "AVAX", "polkadot": "DOT",
    "chainlink": "LINK", "uniswap": "UNI", "dogecoin": "DOGE", "shiba-inu": "SHIB",
    "pepe": "PEPE", "sui": "SUI", "aptos": "APT",
}


def _get_prices_simple(ids: list) -> list:
    """Bare spot-price fallback for a cold 429: /coins/markets failed and no cache exists.

    Only `price` is populated — change %, market cap, and rank stay null. /simple/price is a
    lighter endpoint but shares the same per-IP limit, so this is a best-effort degrade, not
    a fix. Fetched outside the ttl cache so partial rows never overwrite full cached data.
    """
    try:
        r = requests.get(
            f"{GECKO_BASE}/simple/price",
            params={"ids": ",".join(ids), "vs_currencies": "usd"},
            headers=_headers(),
            timeout=12,
        )
        r.raise_for_status()
        data = r.json() or {}
        results = []
        for cid in ids:
            row = data.get(cid)
            if not row:
                continue
            results.append({
                "id":         cid,
                "symbol":     _ID_SYMBOL.get(cid, cid.upper()),
                "name":       cid,
                "price":      row.get("usd"),
                "market_cap": None,
                "volume_24h": None,
                "chg_24h":    None,
                "chg_7d":     None,
                "rank":       None,
            })
        return results
    except Exception as e:
        print(f"[OctoGecko] Simple-price fallback failed: {e}")
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
    if not prices:
        # Cold 429: /coins/markets failed and the ttl cache had nothing to serve.
        # Degrade to bare spot prices so `price` is at least populated this scan.
        prices = _get_prices_simple(TRACK_IDS)

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

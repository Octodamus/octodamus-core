"""
octo_predexon.py -- Predexon + BlockRun partner APIs via ClawRouter

ClawRouter exposes these as direct REST endpoints (x402 auto-payment):

  GET /v1/pm/polymarket/events                          $0.001/req  live odds + volume
  GET /v1/pm/polymarket/markets?q=...                   $0.001/req  search markets -> conditionId
  GET /v1/pm/polymarket/leaderboard                     $0.001/req  top traders by profit
  GET /v1/pm/polymarket/market/:cid/smart-money         $0.005/req  winning wallets on a market
  GET /v1/pm/polymarket/markets/smart-activity          $0.005/req  markets smart money flows into
  GET /v1/pm/polymarket/wallet/:wallet                  $0.005/req  full wallet profile
  GET /v1/pm/polymarket/wallet/pnl/:wallet              $0.005/req  wallet P&L history
  GET /v1/pm/matching-markets                           $0.005/req  Polymarket vs Kalshi arb
  GET /v1/crypto/price/:symbol                          FREE
  GET /v1/fx/price/:symbol                              FREE
  GET /v1/commodity/price/:symbol                       FREE
  GET /v1/stocks/:market/price/:symbol                  $0.001/req

ClawRouter must be running on port 8402.
"""

import json
import os
import time
import threading
from typing import Optional

import httpx

# ── Config ────────────────────────────────────────────────────────────────────

CLAWROUTER_BASE = "http://localhost:8402"
CLAWROUTER_KEY  = os.environ.get("OPENAI_API_KEY", "x402")

_HEADERS = {"Authorization": f"Bearer {CLAWROUTER_KEY}"}

# ── Cache ─────────────────────────────────────────────────────────────────────

_cache: dict = {}
_cache_lock  = threading.Lock()

CACHE_TTL_SMART   = 10 * 60   # 10 min -- $0.005/req
CACHE_TTL_EVENTS  =  5 * 60   #  5 min -- $0.001/req, odds move faster
CACHE_TTL_FREE    =  2 * 60   #  2 min -- free endpoints, refresh more often


def _cache_get(key: str) -> Optional[object]:
    with _cache_lock:
        entry = _cache.get(key)
        if entry and (time.time() - entry[1]) < entry[2]:
            return entry[0]
    return None


def _cache_set(key: str, value, ttl: int):
    with _cache_lock:
        _cache[key] = (value, time.time(), ttl)


# ── HTTP helper ───────────────────────────────────────────────────────────────

def _get(path: str, params: dict = None) -> Optional[dict]:
    try:
        r = httpx.get(
            f"{CLAWROUTER_BASE}{path}",
            headers=_HEADERS,
            params=params or {},
            timeout=15,
        )
        if r.status_code == 200:
            return r.json()
        # 402 = endpoint alive but wallet needs funds
        if r.status_code == 402:
            print(f"[predexon] 402 Payment Required on {path} -- fund ClawRouter wallet")
    except Exception as e:
        print(f"[predexon] {path} error: {e}")
    return None


# ── Polymarket Events ─────────────────────────────────────────────────────────

def get_events(limit: int = 10) -> Optional[dict]:
    """Live Polymarket events with odds + volume. $0.001/req."""
    cached = _cache_get("events")
    if cached is not None:
        return cached
    data = _get("/v1/pm/polymarket/events", {"limit": limit})
    if data:
        _cache_set("events", data, CACHE_TTL_EVENTS)
    return data


def get_markets(query: str) -> Optional[dict]:
    """Search Polymarket markets by keyword, returns conditionId. $0.001/req."""
    cache_key = f"markets:{query}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    data = _get("/v1/pm/polymarket/markets", {"q": query})
    if data:
        _cache_set(cache_key, data, CACHE_TTL_EVENTS)
    return data


def get_leaderboard(limit: int = 20) -> Optional[dict]:
    """Top Polymarket traders by profit. $0.001/req."""
    cached = _cache_get("leaderboard")
    if cached is not None:
        return cached
    data = _get("/v1/pm/polymarket/leaderboard", {"limit": limit})
    if data:
        _cache_set("leaderboard", data, CACHE_TTL_SMART)
    return data


# ── Smart Money ───────────────────────────────────────────────────────────────

_SMART_FILTER = {"min_realized_pnl": 5000}   # Predexon minimum threshold


def get_smart_money(condition_id: str) -> Optional[dict]:
    """Winning wallet positions on a specific market. $0.005/req."""
    cache_key = f"smart_money:{condition_id}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    data = _get(f"/v1/pm/polymarket/market/{condition_id}/smart-money", _SMART_FILTER)
    if data:
        _cache_set(cache_key, data, CACHE_TTL_SMART)
    return data


def get_smart_activity(limit: int = 10) -> Optional[dict]:
    """All-time smart activity, sorted by smart volume. Includes resolved markets. $0.005/req.
    Use get_active_smart_activity() for open markets only."""
    cached = _cache_get("smart_activity")
    if cached is not None:
        return cached
    data = _get("/v1/pm/polymarket/markets/smart-activity",
                {**_SMART_FILTER, "limit": limit})
    if data:
        _cache_set("smart_activity", data, CACHE_TTL_SMART)
    return data


def get_active_smart_activity(want: int = 10) -> list[dict]:
    """
    Smart activity for currently OPEN markets only.

    Uses Predexon's status=open filter directly — no Gamma cross-reference needed.
    Predexon without status filter defaults to all-time volume (mostly resolved markets).

    Cost: one $0.005 Predexon call (cached 10 min).
    """
    cache_key = f"active_smart_activity:{want}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    data = _get("/v1/pm/polymarket/markets/smart-activity",
                {**_SMART_FILTER, "status": "open", "limit": want})
    if not data:
        return []

    result = data.get("markets", [])[:want]
    _cache_set(cache_key, result, CACHE_TTL_SMART)
    return result


def get_wallet(wallet_address: str) -> Optional[dict]:
    """Full Polymarket wallet profile: profit, win rate, open positions. $0.005/req."""
    cache_key = f"wallet:{wallet_address}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    data = _get(f"/v1/pm/polymarket/wallet/{wallet_address}")
    if data:
        _cache_set(cache_key, data, CACHE_TTL_SMART)
    return data


def get_matching_markets() -> Optional[dict]:
    """Polymarket vs Kalshi matching markets for arb comparison. $0.005/req."""
    cached = _cache_get("matching_markets")
    if cached is not None:
        return cached
    data = _get("/v1/pm/matching-markets")
    if data:
        _cache_set("matching_markets", data, CACHE_TTL_SMART)
    return data


# ── Free Price Feeds ──────────────────────────────────────────────────────────

def get_crypto_price(symbol: str) -> Optional[float]:
    """Real-time crypto price. FREE."""
    cache_key = f"crypto:{symbol}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    data = _get(f"/v1/crypto/price/{symbol.upper()}")
    if data:
        price = data.get("price") or data.get("value")
        if price:
            _cache_set(cache_key, float(price), CACHE_TTL_FREE)
            return float(price)
    return None


def get_fx_price(pair: str) -> Optional[float]:
    """Real-time FX rate (e.g. EUR/USD). FREE."""
    cache_key = f"fx:{pair}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    data = _get(f"/v1/fx/price/{pair.upper()}")
    if data:
        price = data.get("price") or data.get("value")
        if price:
            _cache_set(cache_key, float(price), CACHE_TTL_FREE)
            return float(price)
    return None


def get_commodity_price(symbol: str) -> Optional[float]:
    """Real-time commodity price (GOLD, SILVER, OIL). FREE."""
    cache_key = f"commodity:{symbol}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    data = _get(f"/v1/commodity/price/{symbol.upper()}")
    if data:
        price = data.get("price") or data.get("value")
        if price:
            _cache_set(cache_key, float(price), CACHE_TTL_FREE)
            return float(price)
    return None


# ── Context Formatters (for octo_boto_ai injection) ───────────────────────────

def get_smart_money_context(condition_id: str, question: str = "") -> str:
    """
    Formatted smart money context for prompt injection.
    Response fields (from Predexon API):
      positioning.smart_wallet_count, net_buyers, net_sellers, net_buyers_pct,
      total_smart_volume, avg_smart_buy_price, avg_smart_sell_price,
      avg_smart_roi, avg_smart_win_rate
    Returns empty string if ClawRouter unavailable or no data.
    """
    data = get_smart_money(condition_id)
    if not data:
        return ""

    p = data.get("positioning", {})
    if not p:
        return ""

    count      = p.get("smart_wallet_count", 0)
    buyers     = p.get("net_buyers", 0)
    sellers    = p.get("net_sellers", 0)
    buyers_pct = p.get("net_buyers_pct", 0)   # fraction 0-1
    sellers_pct = 1.0 - buyers_pct
    bias       = "YES" if buyers_pct > 0.5 else "NO"
    volume     = p.get("total_smart_volume", 0)
    avg_roi    = p.get("avg_smart_roi", 0)
    avg_wr     = p.get("avg_smart_win_rate", 0)
    buy_price  = p.get("avg_smart_buy_price")
    sell_price = p.get("avg_smart_sell_price")

    lines = [
        "SMART MONEY SIGNAL (Predexon -- wallets with $5k+ realized P&L):",
        f"  {count} smart wallets: {buyers_pct:.0%} YES ({buyers}) / {sellers_pct:.0%} NO ({sellers}) -- bias {bias}",
        f"  Total smart volume: ${volume:,.0f}",
    ]
    if buy_price and sell_price:
        lines.append(f"  Avg entry: YES @ {buy_price:.3f} | NO @ {sell_price:.3f}")
    if avg_roi:
        lines.append(f"  Avg smart wallet ROI: {avg_roi:.1%}")
    if avg_wr:
        lines.append(f"  Avg smart wallet win rate: {avg_wr:.1%}")
    lines.append("  NOTE: Secondary signal. Use to confirm, not override, primary estimate.")

    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    arg = sys.argv[2] if len(sys.argv) > 2 else ""

    if cmd == "smart_money" and arg:
        data = get_smart_money(arg)
        print(json.dumps(data, indent=2) if data else "No data returned")
    elif cmd == "activity":
        data = get_smart_activity()
        print(json.dumps(data, indent=2) if data else "No data returned")
    elif cmd == "active_activity":
        results = get_active_smart_activity(want=int(arg) if arg else 10)
        if results:
            for m in results:
                print(f"{m['condition_id']} | {m['title'][:60]} | {m.get('net_buyers_pct', 0):.0%} YES smart")
        else:
            print("No active smart activity markets found")
    elif cmd == "events":
        data = get_events()
        print(json.dumps(data, indent=2) if data else "No data returned")
    elif cmd == "markets" and arg:
        data = get_markets(arg)
        print(json.dumps(data, indent=2) if data else "No data returned")
    elif cmd == "leaderboard":
        data = get_leaderboard()
        print(json.dumps(data, indent=2) if data else "No data returned")
    elif cmd == "wallet" and arg:
        data = get_wallet(arg)
        print(json.dumps(data, indent=2) if data else "No data returned")
    elif cmd == "prices":
        for sym in ("BTC", "ETH", "SOL"):
            p = get_crypto_price(sym)
            print(f"{sym}: ${p:,.2f}" if p else f"{sym}: no data")
        print(f"GOLD: ${get_commodity_price('GOLD'):,.2f}" if get_commodity_price("GOLD") else "GOLD: no data")
    elif cmd == "arb":
        data = get_matching_markets()
        print(json.dumps(data, indent=2) if data else "No data returned")
    else:
        print("Usage:")
        print("  python octo_predexon.py activity")
        print("  python octo_predexon.py events")
        print("  python octo_predexon.py markets <query>")
        print("  python octo_predexon.py smart_money <condition_id>")
        print("  python octo_predexon.py leaderboard")
        print("  python octo_predexon.py wallet <address>")
        print("  python octo_predexon.py prices")
        print("  python octo_predexon.py arb")

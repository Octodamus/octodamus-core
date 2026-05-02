"""
octo_polymarket_clob.py
Polymarket CLOB order book depth — live bid/ask spread, depth, and pressure.

Free, no auth required for public order book data.
API: https://clob.polymarket.com/book?token_id=<yes_token_id>

Usage:
    from octo_polymarket_clob import get_clob_depth, clob_context_str, enrich_with_clob_depth

Cache: 90s (order books change fast but we don't need tick-level freshness)
"""

import json
import time
from pathlib import Path
from typing import Optional

import httpx

CLOB_BASE   = "https://clob.polymarket.com"
CACHE_FILE  = Path(__file__).parent / "data" / "polymarket_clob_cache.json"
CACHE_TTL_S = 90


# ── Cache ─────────────────────────────────────────────────────────────────────

def _load_cache() -> dict:
    try:
        if CACHE_FILE.exists():
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_cache(cache: dict):
    CACHE_FILE.parent.mkdir(exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache), encoding="utf-8")


# ── Core fetch ────────────────────────────────────────────────────────────────

def get_clob_depth(token_id: str) -> Optional[dict]:
    """
    Fetch live L2 order book for a YES token.
    Returns parsed depth summary or None on failure.

    Fields returned:
      best_bid      float   highest bid price (0-1)
      best_ask      float   lowest ask price (0-1)
      spread        float   ask - bid (cents on dollar)
      spread_pct    float   spread as % of mid
      mid_price     float   (bid + ask) / 2
      bid_depth_5   float   total size of bids within 5 cents of best bid
      ask_depth_5   float   total size of asks within 5 cents of best ask
      bid_ask_ratio float   bid_depth_5 / ask_depth_5 (>1 = buy pressure)
      pressure      str     BUYERS / SELLERS / BALANCED
      quality       str     TIGHT / NORMAL / WIDE (spread quality)
      n_bids        int
      n_asks        int
      fetched_at    float
    """
    cache = _load_cache()
    cached = cache.get(token_id)
    if cached and (time.time() - cached.get("fetched_at", 0)) < CACHE_TTL_S:
        return cached

    try:
        r = httpx.get(f"{CLOB_BASE}/book", params={"token_id": token_id}, timeout=8)
        if r.status_code != 200:
            return None
        data = r.json()
    except Exception:
        return None

    try:
        bids_raw = data.get("bids") or []
        asks_raw = data.get("asks") or []

        # Parse and sort
        bids = sorted(
            [{"price": float(b["price"]), "size": float(b["size"])} for b in bids_raw],
            key=lambda x: -x["price"]
        )
        asks = sorted(
            [{"price": float(a["price"]), "size": float(a["size"])} for a in asks_raw],
            key=lambda x: x["price"]
        )

        if not bids or not asks:
            return None

        best_bid = bids[0]["price"]
        best_ask = asks[0]["price"]
        spread   = round(best_ask - best_bid, 4)
        mid      = round((best_bid + best_ask) / 2, 4)
        spread_pct = round(spread / mid * 100, 2) if mid > 0 else 0

        # Depth within 5 cents of best
        bid_depth_5 = round(sum(b["size"] for b in bids if b["price"] >= best_bid - 0.05), 2)
        ask_depth_5 = round(sum(a["size"] for a in asks if a["price"] <= best_ask + 0.05), 2)
        ba_ratio    = round(bid_depth_5 / ask_depth_5, 3) if ask_depth_5 > 0 else 0

        # Pressure
        if ba_ratio > 1.3:
            pressure = "BUYERS"
        elif ba_ratio < 0.7:
            pressure = "SELLERS"
        else:
            pressure = "BALANCED"

        # Spread quality
        if spread <= 0.02:
            quality = "TIGHT"
        elif spread <= 0.05:
            quality = "NORMAL"
        else:
            quality = "WIDE"

        result = {
            "token_id":    token_id,
            "best_bid":    best_bid,
            "best_ask":    best_ask,
            "spread":      spread,
            "spread_pct":  spread_pct,
            "mid_price":   mid,
            "bid_depth_5": bid_depth_5,
            "ask_depth_5": ask_depth_5,
            "bid_ask_ratio": ba_ratio,
            "pressure":    pressure,
            "quality":     quality,
            "n_bids":      len(bids),
            "n_asks":      len(asks),
            "fetched_at":  time.time(),
        }
        cache[token_id] = result
        _save_cache(cache)
        return result

    except Exception:
        return None


def clob_context_str(depth: dict) -> str:
    """Format CLOB depth as a compact context string for LLM prompts."""
    if not depth:
        return ""
    lines = ["\nPOLYMARKET ORDER BOOK (live CLOB):"]
    lines.append(
        f"  Bid: {depth['best_bid']:.3f} | Ask: {depth['best_ask']:.3f} | "
        f"Spread: {depth['spread']:.3f} ({depth['quality']})"
    )
    lines.append(
        f"  Depth (5c): Bids ${depth['bid_depth_5']:,.0f} | Asks ${depth['ask_depth_5']:,.0f} | "
        f"B/A ratio: {depth['bid_ask_ratio']:.2f}"
    )
    lines.append(f"  Pressure: {depth['pressure']} | Mid: {depth['mid_price']:.3f}")
    if depth["quality"] == "WIDE":
        lines.append("  WARNING: Wide spread — high entry cost, thin market. Consider skipping.")
    elif depth["pressure"] == "BUYERS" and depth["quality"] == "TIGHT":
        lines.append("  Strong buy-side depth with tight spread — favorable entry conditions.")
    elif depth["pressure"] == "SELLERS":
        lines.append("  Sell pressure dominant — market may drift lower before resolution.")
    return "\n".join(lines)


def enrich_with_clob_depth(summary: dict, raw_market: dict) -> dict:
    """
    Given a market summary and the raw Gamma API dict, fetch CLOB depth for
    the YES token and attach it. Returns the summary dict with clob_depth added.
    Skips silently if token ID unavailable or CLOB fetch fails.
    """
    try:
        tokens = raw_market.get("tokens") or []
        yes_token_id = None
        for t in tokens:
            outcome = str(t.get("outcome", "")).strip().upper()
            if outcome in ("YES", "TRUE", "1"):
                yes_token_id = t.get("token_id") or t.get("tokenId") or t.get("id")
                break

        if not yes_token_id:
            # Try clobTokenIds field
            clob_ids = raw_market.get("clobTokenIds") or []
            if isinstance(clob_ids, str):
                try:
                    clob_ids = json.loads(clob_ids)
                except Exception:
                    clob_ids = []
            if clob_ids:
                yes_token_id = clob_ids[0]  # first token = YES

        if yes_token_id:
            depth = get_clob_depth(str(yes_token_id))
            summary["clob_depth"]    = depth
            summary["clob_spread"]   = depth.get("spread") if depth else None
            summary["clob_pressure"] = depth.get("pressure") if depth else None
            summary["clob_quality"]  = depth.get("quality") if depth else None
    except Exception:
        pass
    return summary


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    token_id = sys.argv[1] if len(sys.argv) > 1 else None
    if not token_id:
        # Test with a known Polymarket YES token (WTI $110 April market)
        token_id = "52114319501245915516055106046884209969926127482827954674443846427813813222426"
        print(f"No token ID given, using test token: {token_id[:20]}...")

    depth = get_clob_depth(token_id)
    if depth:
        print(clob_context_str(depth))
        print(f"\nRaw: {json.dumps(depth, indent=2)}")
    else:
        print("Failed to fetch order book.")

"""
octo_boto_mm.py — Market maker mode for OctoBoto (#6)

Posts limit orders on both sides of liquid markets to earn the spread.
Only activates on markets with sufficient liquidity and tight spreads.

Strategy:
  - Buy YES at midpoint - HALF_SPREAD
  - Buy NO  at midpoint - HALF_SPREAD  (= Sell YES at midpoint + HALF_SPREAD)
  - If both sides fill → flat position, profit = spread captured
  - Max exposure per market: MM_MAX_SIZE_USDC

Safety:
  - Only runs in LIVE mode (paper MM is pointless)
  - Only on markets with >$2000 liquidity
  - Cancels all MM orders on /gopaper or kill switch
  - Never MM a market we already hold a directional position in
"""

import logging
import os
from typing import Optional

log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

MM_HALF_SPREAD    = 0.02    # Post 2¢ away from midpoint each side
MM_MAX_SIZE_USDC  = 25.0    # Max $25 per side per market
MM_MIN_LIQUIDITY  = 2000.0  # Only MM markets with >$2k liquidity
MM_MAX_SPREAD     = 0.06    # Only MM if current spread <6¢ (tight enough to earn)
MM_MAX_MARKETS    = 3       # Max simultaneous MM positions

# Track active MM orders: market_id → {yes_order_id, no_order_id}
_mm_orders: dict = {}


# ── Eligibility ───────────────────────────────────────────────────────────────

def is_mm_eligible(market: dict, open_position_ids: set) -> bool:
    """Check if a market is eligible for market making."""
    if market.get("id") in open_position_ids:
        return False  # Never MM a directional position
    if market.get("liquidity", 0) < MM_MIN_LIQUIDITY:
        return False
    spread = market.get("spread", 1.0)
    if spread and spread > MM_MAX_SPREAD:
        return False
    yes_price = market.get("yes_price", 0.5)
    if yes_price < 0.10 or yes_price > 0.90:
        return False  # Too close to resolution — no spread to earn
    return True


# ── Place MM pair ─────────────────────────────────────────────────────────────

def place_mm_pair(market: dict, size_usdc: float = MM_MAX_SIZE_USDC) -> Optional[dict]:
    """
    Place a YES buy and NO buy around the midpoint.
    Returns dict with both order IDs, or None on failure.
    Requires LIVE mode — MM in paper makes no sense.
    """
    try:
        from octo_boto_clob import place_order, get_token_ids, is_live
    except ImportError:
        log.error("[MM] octo_boto_clob not available")
        return None

    if not is_live():
        log.warning("[MM] Market maker only runs in LIVE mode")
        return None

    if len(_mm_orders) >= MM_MAX_MARKETS:
        log.info(f"[MM] Max markets reached ({MM_MAX_MARKETS})")
        return None

    mid_id = market["id"]
    yes_price = market.get("yes_price", 0.5)
    question  = market.get("question", "")[:60]

    yes_bid = round(yes_price - MM_HALF_SPREAD, 4)
    no_bid  = round((1 - yes_price) - MM_HALF_SPREAD, 4)

    # Safety bounds
    if yes_bid < 0.03 or no_bid < 0.03:
        log.info(f"[MM] Skipping {question} — bid too low")
        return None

    tokens = get_token_ids(market.get("conditionId", mid_id))
    if not tokens["yes"] or not tokens["no"]:
        log.warning(f"[MM] Could not get token IDs for {mid_id}")
        return None

    yes_order = place_order(tokens["yes"], "BUY", yes_bid, size_usdc, f"MM-YES {question}")
    no_order  = place_order(tokens["no"],  "BUY", no_bid,  size_usdc, f"MM-NO  {question}")

    if yes_order.get("status") == "error" or no_order.get("status") == "error":
        log.error(f"[MM] Order failed for {question}")
        return None

    result = {
        "market_id":    mid_id,
        "question":     question,
        "yes_order_id": yes_order.get("order_id"),
        "no_order_id":  no_order.get("order_id"),
        "yes_bid":      yes_bid,
        "no_bid":       no_bid,
        "size_usdc":    size_usdc,
    }
    _mm_orders[mid_id] = result
    log.info(f"[MM] Placed pair on {question} | YES@{yes_bid} NO@{no_bid}")
    return result


# ── Cancel MM orders ──────────────────────────────────────────────────────────

def cancel_mm_market(market_id: str) -> bool:
    """Cancel both MM orders for a specific market."""
    try:
        from octo_boto_clob import cancel_order
    except ImportError:
        return False
    order = _mm_orders.pop(market_id, None)
    if not order:
        return False
    ok = True
    for oid in (order.get("yes_order_id"), order.get("no_order_id")):
        if oid:
            ok = ok and cancel_order(oid)
    return ok


def cancel_all_mm() -> int:
    """Cancel all active MM orders. Returns count cancelled."""
    market_ids = list(_mm_orders.keys())
    for mid in market_ids:
        cancel_mm_market(mid)
    return len(market_ids)


# ── Status ────────────────────────────────────────────────────────────────────

def mm_status_str() -> str:
    if not _mm_orders:
        return "📊 Market maker: no active pairs"
    lines = [f"📊 *Market maker — {len(_mm_orders)} active pair(s):*"]
    for mid, o in _mm_orders.items():
        lines.append(
            f"• `{o['question'][:45]}` | YES@{o['yes_bid']} NO@{o['no_bid']} | ${o['size_usdc']:.0f}/side"
        )
    return "\n".join(lines)


def active_mm_count() -> int:
    return len(_mm_orders)

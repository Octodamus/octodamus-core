"""
octo_boto_math.py — OctoBoto Math Engine v2
Fixes:
  - Added composite_score() — ranks by EV × volume × liquidity, not just EV
  - Added liquidity_adjusted_ev() — accounts for price impact of large positions
  - Added days_to_resolution factor — closer deadline = harder to move price = better signal
  - is_valid_market() now checks end_date and volume activity
  - MAX_POSITION_PCT lowered to 4% ($20 on $500 bankroll — right size for paper)
  - Tightened price band to 3%–97% (was 2%–98%)
"""

import math
from datetime import datetime, timezone
from typing import Optional

import numpy as np

# ─── Constants ────────────────────────────────────────────────────────────────
MIN_EV_THRESHOLD   = 0.12   # 6% edge required (raised from 5%)
MIN_LIQUIDITY      = 10_000  # $3k min liquidity (lowered to find more markets)
MIN_VOLUME_24H     = 2_000    # $500+ daily volume (screens out dead markets)
KELLY_FRACTION     = 0.25   # Quarter-Kelly — proven safer for volatile markets
MAX_POSITION_PCT   = 0.06   # 4% of bankroll max = $20 on $500 starting
MIN_MARKET_PRICE   = 0.03   # Tighter band
MAX_MARKET_PRICE   = 0.97
MAX_POSITION_DAYS  = 90     # Don't enter markets resolving > 90 days out


# ─── EV Functions ─────────────────────────────────────────────────────────────

def ev_yes(market_price: float, true_p: float) -> float:
    """
    EV per dollar on YES.
    Mathematical identity: EV_YES = true_p - market_price
    (Buy YES at P: win (1-P) with prob true_p, lose P with prob (1-true_p))
    """
    return round(true_p - market_price, 4)


def ev_no(market_price: float, true_p: float) -> float:
    """
    EV per dollar on NO.
    Buy NO at (1-P): win P with prob (1-true_p), lose (1-P) with prob true_p.
    """
    return round((1.0 - true_p) - (1.0 - market_price), 4)


def liquidity_adjusted_ev(raw_ev: float, size: float, liquidity: float) -> float:
    """
    Adjust EV downward for price impact.
    Buying into a $5k liquidity pool with $20 moves price ~0.4%.
    Impact = size / liquidity. Deduct half of impact from EV.
    """
    if liquidity <= 0:
        return raw_ev
    impact = (size / liquidity) * 0.5
    return round(raw_ev - impact, 4)


# ─── Kelly Criterion ──────────────────────────────────────────────────────────

def kelly(market_price: float, true_p: float, side: str = "YES") -> float:
    """
    Fractional Kelly (quarter-Kelly) for binary prediction markets.

    YES: f* = (true_p - P) / (1 - P)
    NO:  f* = ((1 - true_p) - (1 - P)) / P  =  (P - true_p) / P

    Both derived from standard f* = (pb - q) / b formula.
    Returns quarter-Kelly, hard-capped at MAX_POSITION_PCT.
    """
    try:
        if side == "YES":
            denom = 1.0 - market_price
            if denom <= 0:
                return 0.0
            raw = (true_p - market_price) / denom
        else:
            denom = market_price
            if denom <= 0:
                return 0.0
            raw = ((1.0 - true_p) - (1.0 - market_price)) / denom

        raw = max(0.0, raw)
        fractional = raw * KELLY_FRACTION
        return round(min(fractional, MAX_POSITION_PCT), 4)

    except (ZeroDivisionError, ValueError):
        return 0.0


def position_size(bankroll: float, kelly_frac: float) -> float:
    """Dollar size from bankroll and Kelly fraction. $2 minimum."""
    size = bankroll * min(kelly_frac, MAX_POSITION_PCT)
    return round(max(size, 2.0), 2)


# ─── Best Trade ───────────────────────────────────────────────────────────────

def best_trade(market_price: float, true_p: float) -> dict:
    """
    Return the best tradeable side and key metrics.
    Returns NONE if no side clears MIN_EV_THRESHOLD.
    """
    if not (MIN_MARKET_PRICE < market_price < MAX_MARKET_PRICE):
        return {"side": "NONE", "ev": 0.0, "kelly": 0.0, "price": 0.0}

    ey = ev_yes(market_price, true_p)
    en = ev_no(market_price, true_p)

    if ey >= en and ey >= MIN_EV_THRESHOLD:
        return {
            "side":  "YES",
            "ev":    ey,
            "kelly": kelly(market_price, true_p, "YES"),
            "price": market_price
        }
    elif en >= MIN_EV_THRESHOLD:
        return {
            "side":  "NO",
            "ev":    en,
            "kelly": kelly(market_price, true_p, "NO"),
            "price": round(1.0 - market_price, 4)
        }
    else:
        return {"side": "NONE", "ev": max(ey, en), "kelly": 0.0, "price": 0.0}


# ─── Composite Opportunity Score ──────────────────────────────────────────────

def composite_score(ev: float, liquidity: float, volume24h: float,
                    confidence: str, days_to_close: Optional[int]) -> float:
    """
    Rank opportunities by more than just EV.
    Score = EV × log(liquidity) × volume_bonus × conf_multiplier × time_factor

    Rationale:
    - High liquidity = less price impact, easier execution
    - High 24h volume = active market, price is live/accurate
    - Confidence bonus: AI high > medium > low
    - Time factor: markets resolving in 1–14 days > further out
    """
    if ev <= 0:
        return 0.0

    # Liquidity factor: log scale, $3k = 1.0, $100k = ~1.7
    liq_factor = math.log10(max(liquidity, 1_000)) / math.log10(3_000)

    # Volume factor: 24h volume signals active price discovery
    vol_factor = 1.0 + min(math.log10(max(volume24h, 1)) / 4, 0.5)

    # Confidence multiplier
    conf_mult = {"high": 1.3, "medium": 1.0, "low": 0.6}.get(confidence, 0.8)

    # Time factor: sweet spot is 1–14 days to resolution
    time_factor = 1.0
    if days_to_close is not None:
        if 1 <= days_to_close <= 7:
            time_factor = 1.4   # Imminent: strong signal
        elif 8 <= days_to_close <= 14:
            time_factor = 1.2   # Good window
        elif 15 <= days_to_close <= 30:
            time_factor = 1.0
        elif days_to_close > 60:
            time_factor = 0.7   # Too far out, uncertainty compounds

    score = ev * liq_factor * vol_factor * conf_mult * time_factor
    return round(score, 4)


# ─── Market Validation ────────────────────────────────────────────────────────

def is_valid_market(market: dict) -> bool:
    """
    Gate keeper before spending AI tokens.
    All conditions must pass.
    """
    # Price must exist and be in tradeable range
    price = market.get("yes_price")
    if price is None:
        return False
    if not (MIN_MARKET_PRICE < float(price) < MAX_MARKET_PRICE):
        return False

    # Must be binary YES/NO structure
    if not market.get("is_binary", False):
        return False

    # Liquidity gate
    liq = float(market.get("liquidity", 0) or 0)
    if liq < MIN_LIQUIDITY:
        return False

    # Activity gate — dead markets have no price discovery
    vol24 = float(market.get("volume24h", 0) or 0)
    if vol24 < MIN_VOLUME_24H:
        return False

    # Already resolved — skip
    if market.get("resolved"):
        return False

    # Days-to-close gate — don't enter markets expiring in < 1 day or > 90 days
    dtc = market.get("days_to_close")
    if dtc is not None:
        if dtc < 1 or dtc > MAX_POSITION_DAYS:
            return False

    return True


def days_until(end_date: str) -> Optional[int]:
    """Parse ISO end date and return days remaining. Returns None on error."""
    if not end_date:
        return None
    try:
        # Handle various ISO formats
        clean = end_date.replace("Z", "+00:00")
        dt    = datetime.fromisoformat(clean)
        now   = datetime.now(timezone.utc)
        return max(0, (dt - now).days)
    except Exception:
        return None


# ─── Portfolio Stats ──────────────────────────────────────────────────────────

def compute_sharpe(pnl_pcts: list) -> float:
    """Sharpe from per-trade % returns. Not annualised — relative ranking only."""
    if len(pnl_pcts) < 3:
        return 0.0
    arr = np.array(pnl_pcts, dtype=float)
    std = arr.std()
    if std == 0:
        return 0.0
    return round((arr.mean() / std) * math.sqrt(len(arr)), 2)


def compute_max_drawdown(balance_series: list) -> float:
    """Peak-to-trough drawdown %. Returns negative number."""
    if len(balance_series) < 2:
        return 0.0
    arr  = np.array(balance_series, dtype=float)
    peak = np.maximum.accumulate(arr)
    dd   = (arr - peak) / peak
    return round(float(dd.min()) * 100, 2)

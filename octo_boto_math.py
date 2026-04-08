"""
octo_boto_math.py â€" OctoBoto Math Engine v2
Fixes:
  - Added composite_score() â€" ranks by EV Ã— volume Ã— liquidity, not just EV
  - Added liquidity_adjusted_ev() â€" accounts for price impact of large positions
  - Added days_to_resolution factor â€" closer deadline = harder to move price = better signal
  - is_valid_market() now checks end_date and volume activity
  - MAX_POSITION_PCT lowered to 4% ($20 on $500 bankroll â€" right size for paper)
  - Tightened price band to 3%â€"97% (was 2%â€"98%)
"""

import math
from datetime import datetime, timezone
from typing import Optional

import numpy as np

# â"€â"€â"€ Constants â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
MIN_EV_THRESHOLD   = 0.12   # 6% edge required (raised from 5%)
MIN_LIQUIDITY      = 10_000  # $3k min liquidity (lowered to find more markets)
MIN_VOLUME_24H     = 2_000    # $500+ daily volume (screens out dead markets)
KELLY_FRACTION     = 0.25   # Quarter-Kelly â€" proven safer for volatile markets
MAX_POSITION_PCT   = 0.06   # 4% of bankroll max = $20 on $500 starting
MIN_MARKET_PRICE   = 0.03   # Tighter band
MAX_MARKET_PRICE   = 0.97
MAX_POSITION_DAYS  = 90     # Don't enter markets resolving > 90 days out


# â"€â"€â"€ EV Functions â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

# Longshot Bias Calibration
# Source: Becker 2026, 72.1M trades / $18.26B volume on Kalshi/Polymarket.
# Cheap YES contracts are systematically overpriced. Correct model probability
# downward before computing EV. Correction only applied below 15c.

_LONGSHOT_TABLE = [
    (0.01, 0.43),   # 1c market: 0.43% actual win rate  (correction 0.43x)
    (0.02, 0.60),
    (0.03, 0.72),
    (0.05, 0.836),  # 5c market: 4.18% actual win rate  (correction 0.836x)
    (0.08, 0.90),
    (0.10, 0.92),
    (0.15, 0.97),   # above 15c bias is negligible
]

def longshot_calibrate(true_p: float, market_price: float) -> float:
    """
    Apply longshot bias correction to model probability when market price < 15c.
    Interpolates correction factor from empirical table.
    Returns corrected probability (always <= true_p for longshots).
    """
    if market_price >= 0.15:
        return true_p
    for i in range(len(_LONGSHOT_TABLE) - 1):
        p0, c0 = _LONGSHOT_TABLE[i]
        p1, c1 = _LONGSHOT_TABLE[i + 1]
        if p0 <= market_price <= p1:
            t = (market_price - p0) / (p1 - p0)
            return round(true_p * (c0 + t * (c1 - c0)), 4)
    return round(true_p * _LONGSHOT_TABLE[0][1], 4)


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


# â"€â"€â"€ Kelly Criterion â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

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


# â"€â"€â"€ Best Trade â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

def best_trade(market_price: float, true_p: float) -> dict:
    """
    Return the best tradeable side and key metrics.
    Returns NONE if no side clears MIN_EV_THRESHOLD.

    Applies two empirical biases from Becker 2026 (72.1M trades):
    1. Longshot calibration: corrects YES probability downward when market < 15c
    2. NO preference: below 30c, NO outperforms YES at 69/99 price levels —
       require YES to beat NO by >2pp before taking YES side in this range.
    """
    if not (MIN_MARKET_PRICE < market_price < MAX_MARKET_PRICE):
        return {"side": "NONE", "ev": 0.0, "kelly": 0.0, "price": 0.0}

    # Apply longshot bias correction to model probability
    calibrated_p = longshot_calibrate(true_p, market_price)

    ey = ev_yes(market_price, calibrated_p)
    en = ev_no(market_price, calibrated_p)

    # Below 30c: Optimism Tax means takers systematically overpay for YES.
    # Require YES to clearly beat NO before choosing it over NO.
    yes_bias_penalty = 0.02 if market_price < 0.30 else 0.0

    if ey >= (en + yes_bias_penalty) and ey >= MIN_EV_THRESHOLD:
        return {
            "side":  "YES",
            "ev":    ey,
            "kelly": kelly(market_price, calibrated_p, "YES"),
            "price": market_price
        }
    elif en >= MIN_EV_THRESHOLD:
        return {
            "side":  "NO",
            "ev":    en,
            "kelly": kelly(market_price, calibrated_p, "NO"),
            "price": round(1.0 - market_price, 4)
        }
    else:
        return {"side": "NONE", "ev": max(ey, en), "kelly": 0.0, "price": 0.0}


# â"€â"€â"€ Composite Opportunity Score â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

def resolution_risk_score(question: str, description: str = "") -> float:
    """
    Score market resolution ambiguity. Returns 0.0 (clear) to 1.0 (very risky).

    High-risk signals: vague modifiers, discretionary resolvers, multi-condition criteria.
    Low-risk signals: specific source cited, binary binary date-based, official stat.
    """
    text = (question + " " + description).lower()

    risk = 0.0

    # Vague quantifiers — hard to objectively resolve
    vague_terms = [
        "substantially", "significantly", "roughly", "approximately", "around",
        "most", "majority", "many", "few", "largely", "generally", "primarily",
        "widespread", "major", "notable", "considerable",
    ]
    risk += sum(0.08 for t in vague_terms if t in text)

    # Multi-condition AND/OR logic — resolution gets complicated
    if " and " in text and " or " in text:
        risk += 0.15
    if text.count(" and ") >= 3:
        risk += 0.10

    # Discretionary resolver signals
    discretionary = ["at the discretion", "as determined by", "in the opinion",
                     "polymarket reserves", "admin", "moderator"]
    risk += sum(0.20 for t in discretionary if t in text)

    # Good signals: specific sources (lower risk)
    clear_sources = ["according to", "as reported by", "per the official",
                     "bureau of labor", "federal reserve", "cdc", "fda", "sec filing",
                     "election results", "official count", "final score"]
    risk -= sum(0.10 for t in clear_sources if t in text)

    return round(max(0.0, min(1.0, risk)), 2)


def composite_score(ev: float, liquidity: float, volume24h: float,
                    confidence: str, days_to_close: Optional[int],
                    market_age_hours: Optional[float] = None,
                    resolution_risk: float = 0.0) -> float:
    """
    Rank opportunities by more than just EV.
    Score = EV x log(liquidity) x volume_bonus x conf_multiplier x time_factor
            x freshness_bonus x resolution_risk_penalty

    Rationale:
    - High liquidity = less price impact, easier execution
    - High 24h volume = active market, price is live/accurate
    - Confidence bonus: AI high > medium > low
    - Time factor: markets resolving in 1-14 days > further out
    - Freshness bonus: new markets (<24h) often have stale creator prices
    - Resolution risk penalty: ambiguous criteria -> discount score
    """
    if ev <= 0:
        return 0.0

    # Liquidity factor: log scale, $3k = 1.0, $100k = ~1.7
    liq_factor = math.log10(max(liquidity, 1_000)) / math.log10(3_000)

    # Volume factor: 24h volume signals active price discovery
    vol_factor = 1.0 + min(math.log10(max(volume24h, 1)) / 4, 0.5)

    # Confidence multiplier
    conf_mult = {"high": 1.3, "medium": 1.0, "low": 0.6}.get(confidence, 0.8)

    # Time factor: sweet spot is 1-14 days to resolution
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

    # Freshness bonus: new markets not yet corrected by arb traders
    freshness = 1.0
    if market_age_hours is not None:
        if market_age_hours < 6:
            freshness = 1.35   # Very fresh — creator price, almost no arb
        elif market_age_hours < 24:
            freshness = 1.20   # Early price discovery phase
        elif market_age_hours < 72:
            freshness = 1.05   # Recent, partially corrected

    # Resolution risk penalty: max 50% reduction at risk=1.0
    risk_penalty = 1.0 - (resolution_risk * 0.5)

    score = ev * liq_factor * vol_factor * conf_mult * time_factor * freshness * risk_penalty
    return round(score, 4)


# â"€â"€â"€ Market Validation â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

SPORTS_KEYWORDS = [
    # Tennis
    "vs.", " vs ", "open:", "open —", "atp", "wta", "wimbledon", "roland garros",
    "us open tennis", "australian open", "french open", "grand slam",
    # Team sports
    "yankees", "dodgers", "cubs", "mets", "red sox", "astros", "braves",
    "royals", "twins", "cardinals", "giants mlb", "padres",
    "lakers", "celtics", "warriors", "bucks", "heat", "nets", "knicks",
    "chiefs", "eagles", "cowboys", "patriots", "49ers", "ravens",
    "manchester", "arsenal", "chelsea", "liverpool", "real madrid", "barcelona",
    # Generic sports signals
    "win the match", "win the game", "win the series", "win the set",
    "score more", "championship game", "playoff", "super bowl",
    "world series", "nba finals", "stanley cup",
    # Sport types
    " nfl ", " nba ", " mlb ", " nhl ", " mls ", " ufc ",
    "formula 1", "f1 race", "grand prix",
    # Cricket
    "indian premier league", " ipl ", "ipl:", "super kings", "punjab kings",
    "rajasthan royals", "mumbai indians", "kolkata knight", "sunrisers",
    "delhi capitals", "lucknow super", "gujarat titans", "royal challengers",
    "cricket", " odi ", " t20 ", "test match", "innings",
    # More tennis tournaments
    "open:", "masters 1000", "copa ", "bucharest", "colsanitas",
    "challenger", "davis cup", "billie jean", "fed cup",
    # Horse racing / other
    "horse racing", "kentucky derby", "preakness", "belmont",
    "golf open", "pga tour", "masters golf", "ryder cup",
]

WAR_KEYWORDS = [
    # Active conflicts
    "ukraine", "russia", "nato", "putin", "zelensky",
    "israel", "gaza", "hamas", "hezbollah", "west bank", "idf",
    "iran", "tehran", "irgc", "nuclear deal",
    "north korea", "kim jong", "dprk",
    "taiwan", "pla ", "strait of taiwan", "china invade",
    "yemen", "houthi", "red sea attack",
    "sudan", "syria", "lebanon war",
    # Conflict event types
    "ceasefire", "peace deal", "peace talks", "invasion", "offensive",
    "airstrike", "missile strike", "ground invasion", "troop withdrawal",
    "war crimes", "sanctions", "arms deal", "military aid",
    "coup", "regime change", "assassination",
    # Nuclear / WMD
    "nuclear weapon", "nuclear test", "dirty bomb",
]

# Markets where Octodamus has live data feed advantage.
# OctoBoto should ONLY trade these categories.
DATA_EDGE_KEYWORDS = [
    # Crypto price / on-chain
    "bitcoin", "btc", "ethereum", "eth", "solana", "sol",
    "crypto", "altcoin", "defi", "stablecoin", "usdt", "usdc",
    "binance", "coinbase", "coinbase ipo", "kraken", "bybit",
    "bnb", "xrp", "doge", "dogecoin", "ada", "avax", "link", "matic",
    "100k", "200k", "50k", "80k", "price", "ath", "all-time high",
    "market cap", "dominance", "halving", "etf", "spot etf",
    "liquidat", "funding rate", "open interest",
    # Macro economic data — Octodamus pulls these live
    "cpi", "inflation", "fed rate", "federal reserve", "fomc",
    "interest rate", "rate cut", "rate hike", "basis point",
    "gdp", "recession", "unemployment", "jobs report", "nonfarm",
    "treasury", "yield", "10-year", "2-year", "bond",
    "s&p", "sp500", "nasdaq", "dow jones", "vix",
    "oil price", "crude", "gold price", "silver",
    # Prediction market / sentiment
    "polymarket", "kalshi", "fear and greed", "fear & greed",
    "prediction market", "crowd probability",
    # Trump / US political — proven edge per backtest
    "trump", "tariff", "trade war", "sec ", "gensler",
    "election", "approval rating", "congress", "senate vote",
    "debt ceiling", "government shutdown",
]


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

    question_lower    = (market.get("question", "") or "").lower()
    description_lower = (market.get("description", "") or "").lower()
    text = question_lower + " " + description_lower

    # Sports filter -- no Octodamus data edge, proven losing category
    if any(kw in text for kw in SPORTS_KEYWORDS):
        return False

    # War/conflict/geopolitical filter -- no live data feed, pure guessing,
    # resolution criteria vague, news-driven flips. Responsible for most losses.
    if any(kw in text for kw in WAR_KEYWORDS):
        return False

    # Data-edge filter -- only trade markets where Octodamus has a live feed.
    # If the market doesn't touch crypto, macro data, or prediction markets,
    # OctoBoto is just guessing with no information advantage — skip it.
    if not any(kw in text for kw in DATA_EDGE_KEYWORDS):
        return False

    # Liquidity gate
    liq = float(market.get("liquidity", 0) or 0)
    if liq < MIN_LIQUIDITY:
        return False

    # Activity gate â€" dead markets have no price discovery
    vol24 = float(market.get("volume24h", 0) or 0)
    if vol24 < MIN_VOLUME_24H:
        return False

    # Already resolved â€" skip
    if market.get("resolved"):
        return False

    # Days-to-close gate â€" don't enter markets expiring in < 1 day or > 90 days
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


# â"€â"€â"€ Portfolio Stats â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

def compute_sharpe(pnl_pcts: list) -> float:
    """Sharpe from per-trade % returns. Not annualised â€" relative ranking only."""
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

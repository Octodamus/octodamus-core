"""
octo_boto_calibration.py  (#10)
Track AI estimate accuracy vs resolved market outcomes.
Computes per-confidence-tier calibration bias and injects correction into system prompt.

Usage:
  from octo_boto_calibration import record_estimate, record_outcome, get_calibration_context
"""

import json
from datetime import datetime, timezone
from pathlib import Path

CALIB_FILE    = Path(r"C:\Users\walli\octodamus\octo_boto_calibration.json")
THRESHOLD_FILE = Path(__file__).parent / "data" / "octo_ev_threshold.json"
MIN_RECORDS_FOR_CALIBRATION = 5    # don't claim bias until we have enough data
MIN_RECORDS_FOR_THRESHOLD   = 20   # need more trades before auto-adjusting threshold
DEFAULT_THRESHOLD           = 0.12
MIN_THRESHOLD               = 0.10
MAX_THRESHOLD               = 0.25

# Market category detection — mirrors DATA_EDGE_KEYWORDS groups in octo_boto_math.py
_CATEGORY_MAP = {
    "crypto":    ["bitcoin", "btc", "ethereum", "eth", "solana", "crypto", "altcoin",
                  "defi", "halving", "etf", "liquidat", "funding rate", "dominance"],
    "macro":     ["cpi", "inflation", "fed rate", "fomc", "interest rate", "rate cut",
                  "rate hike", "gdp", "recession", "unemployment", "jobs report",
                  "treasury", "yield", "s&p", "sp500", "nasdaq", "vix"],
    "political": ["trump", "tariff", "trade war", "election", "congress", "senate",
                  "approval rating", "sec ", "gensler", "debt ceiling"],
    "polymarket":["polymarket", "kalshi", "prediction market", "fear and greed"],
}


# ── Persistence ───────────────────────────────────────────────────────────────

def _load() -> dict:
    try:
        with open(CALIB_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"estimates": []}


def _save(data: dict):
    CALIB_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CALIB_FILE, "w") as f:
        json.dump(data, f, indent=2)


# ── Record events ─────────────────────────────────────────────────────────────

def _detect_category(question: str) -> str:
    """Detect market category from question text."""
    q = question.lower()
    for cat, keywords in _CATEGORY_MAP.items():
        if any(kw in q for kw in keywords):
            return cat
    return "other"


def record_estimate(
    market_id:    str,
    question:     str,
    claude_p:     float,
    market_price: float,
    confidence:   str,
    side:         str,
):
    """
    Log an AI estimate at the moment a position is opened.
    Call from octo_boto.py right after TRACKER.open_position() succeeds.
    """
    data = _load()
    if any(e["market_id"] == market_id for e in data["estimates"]):
        return  # already logged
    cat = _detect_category(question)
    data["estimates"].append({
        "market_id":    market_id,
        "question":     question[:120],
        "claude_p":     round(claude_p, 4),
        "market_price": round(market_price, 4),
        "confidence":   confidence,
        "side":         side,
        "category":     cat,
        "recorded_at":  datetime.now(timezone.utc).isoformat(),
        "outcome":      None,
    })
    _save(data)
    try:
        from octo_memory_db import db_record_estimate
        db_record_estimate(market_id, question, round(claude_p, 4), round(market_price, 4),
                           confidence, side, cat)
    except Exception:
        pass


def record_outcome(market_id: str, resolved_yes: bool):
    """
    Fill in actual outcome after a market resolves.
    Call from octo_boto.py right after TRACKER.close_position() succeeds.
    """
    data = _load()
    updated = False
    for e in data["estimates"]:
        if e["market_id"] == market_id and e["outcome"] is None:
            e["outcome"]     = "YES" if resolved_yes else "NO"
            e["resolved_at"] = datetime.now(timezone.utc).isoformat()
            updated = True
    if updated:
        _save(data)
        try:
            from octo_memory_db import db_record_outcome
            db_record_outcome(market_id, resolved_yes)
        except Exception:
            pass
        auto_adjust_threshold()


# ── Compute calibration ───────────────────────────────────────────────────────

def compute_calibration() -> dict:
    """
    Returns per-confidence-tier calibration stats.

    Calibration bias: positive = overconfident (predicted higher than actual).
    E.g. if high-confidence estimates average 78% but only 55% resolved YES → bias +23%.
    """
    data     = _load()
    resolved = [e for e in data["estimates"] if e.get("outcome") is not None]

    if len(resolved) < MIN_RECORDS_FOR_CALIBRATION:
        return {"n": len(resolved), "tiers": {}, "bias": 0.0, "ready": False}

    tiers: dict = {"high": [], "medium": [], "low": []}
    for e in resolved:
        conf       = e.get("confidence", "low")
        actual_yes = e["outcome"] == "YES"
        # Convert to YES probability regardless of trade side
        if e.get("side") == "NO":
            our_yes_p = 1.0 - e["claude_p"]
        else:
            our_yes_p = e["claude_p"]
        tiers.get(conf, tiers["low"]).append({"our_p": our_yes_p, "actual": actual_yes})

    tier_stats = {}
    all_biases = []
    for tier, records in tiers.items():
        if not records:
            continue
        avg_pred    = sum(r["our_p"] for r in records) / len(records)
        actual_rate = sum(1 for r in records if r["actual"]) / len(records)
        bias        = avg_pred - actual_rate   # +ve = overconfident
        tier_stats[tier] = {
            "n":             len(records),
            "avg_predicted": round(avg_pred, 3),
            "actual_rate":   round(actual_rate, 3),
            "bias":          round(bias, 3),
        }
        all_biases.append(bias)

    overall_bias = round(sum(all_biases) / len(all_biases), 3) if all_biases else 0.0

    return {
        "n":      len(resolved),
        "tiers":  tier_stats,
        "bias":   overall_bias,
        "ready":  True,
    }


# ── Category win rate tracking ────────────────────────────────────────────────

def compute_category_stats() -> dict:
    """
    Compute win rate per market category from resolved trades.
    A win = the side we bet on resolved correctly.
    """
    data     = _load()
    resolved = [e for e in data["estimates"] if e.get("outcome") is not None]
    cats: dict = {}
    for e in resolved:
        cat    = e.get("category", "other")
        side   = e.get("side", "YES")
        outcome = e["outcome"]
        won    = (side == outcome)
        if cat not in cats:
            cats[cat] = {"wins": 0, "losses": 0}
        if won:
            cats[cat]["wins"] += 1
        else:
            cats[cat]["losses"] += 1
    # Compute win rates
    result = {}
    for cat, stats in cats.items():
        total = stats["wins"] + stats["losses"]
        result[cat] = {
            "wins":   stats["wins"],
            "losses": stats["losses"],
            "total":  total,
            "win_rate": round(stats["wins"] / total, 3) if total else 0.0,
        }
    return result


# ── Threshold auto-calibration ────────────────────────────────────────────────

def get_dynamic_threshold() -> float:
    """
    Return the current recommended MIN_EV_THRESHOLD based on rolling performance.
    Reads from THRESHOLD_FILE (updated by auto_adjust_threshold).
    Falls back to DEFAULT_THRESHOLD if no data yet.
    """
    try:
        if THRESHOLD_FILE.exists():
            data = json.loads(THRESHOLD_FILE.read_text())
            return float(data.get("threshold", DEFAULT_THRESHOLD))
    except Exception:
        pass
    return DEFAULT_THRESHOLD


def auto_adjust_threshold() -> float:
    """
    Compute and save recommended MIN_EV_THRESHOLD based on rolling win rate.

    Logic:
    - Rolling win rate > 85%: loosen by 1pp (more trades, still profitable)
    - Rolling win rate 70-85%: hold current threshold
    - Rolling win rate 55-70%: tighten by 2pp (be more selective)
    - Rolling win rate < 55%: tighten by 4pp (something is wrong, get conservative)

    Never goes below MIN_THRESHOLD or above MAX_THRESHOLD.
    Requires MIN_RECORDS_FOR_THRESHOLD resolved trades.
    """
    data     = _load()
    resolved = [e for e in data["estimates"] if e.get("outcome") is not None]

    if len(resolved) < MIN_RECORDS_FOR_THRESHOLD:
        return DEFAULT_THRESHOLD

    # Use last 30 trades for rolling window
    recent  = resolved[-30:]
    wins    = sum(1 for e in recent if e.get("side") == e.get("outcome"))
    win_rate = wins / len(recent)

    current = get_dynamic_threshold()

    if win_rate > 0.85:
        new_threshold = max(MIN_THRESHOLD, current - 0.01)
        reason = f"win rate {win_rate:.0%} > 85% — loosening slightly"
    elif win_rate >= 0.70:
        new_threshold = current
        reason = f"win rate {win_rate:.0%} in target range — holding"
    elif win_rate >= 0.55:
        new_threshold = min(MAX_THRESHOLD, current + 0.02)
        reason = f"win rate {win_rate:.0%} below 70% — tightening"
    else:
        new_threshold = min(MAX_THRESHOLD, current + 0.04)
        reason = f"win rate {win_rate:.0%} below 55% — tightening hard"

    new_threshold = round(new_threshold, 3)
    THRESHOLD_FILE.parent.mkdir(parents=True, exist_ok=True)
    THRESHOLD_FILE.write_text(json.dumps({
        "threshold":  new_threshold,
        "win_rate":   round(win_rate, 3),
        "n_trades":   len(recent),
        "reason":     reason,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }, indent=2))

    if new_threshold != current:
        print(f"[Calibration] Threshold adjusted: {current:.2f} → {new_threshold:.2f} ({reason})")

    return new_threshold


# ── System prompt injection ───────────────────────────────────────────────────

def get_calibration_context() -> str:
    """
    Returns a calibration correction note for appending to the AI system prompt.
    Empty string until MIN_RECORDS_FOR_CALIBRATION resolved trades exist.
    """
    cal = compute_calibration()
    if not cal["ready"]:
        return ""

    lines = [f"\n[SELF-CALIBRATION — {cal['n']} resolved trades]"]
    actionable = False

    for tier in ("high", "medium", "low"):
        stats = cal["tiers"].get(tier)
        if not stats or stats["n"] < 3:
            continue
        bias = stats["bias"]
        if abs(bias) < 0.04:
            continue   # within noise
        direction   = "overconfident" if bias > 0 else "underconfident"
        adj         = "down" if bias > 0 else "up"
        lines.append(
            f"  {tier.upper()}: predicted {stats['avg_predicted']:.0%} avg, "
            f"resolved YES {stats['actual_rate']:.0%} — {direction} by {abs(bias):.0%}. "
            f"Adjust {tier}-confidence estimates {adj} ~{abs(bias):.0%}."
        )
        actionable = True

    overall = cal["bias"]
    if abs(overall) >= 0.06:
        direction = "overconfident" if overall > 0 else "underconfident"
        adj       = "downward" if overall > 0 else "upward"
        lines.append(
            f"  OVERALL: {direction} by {abs(overall):.0%}. "
            f"Apply a {abs(overall):.0%} {adj} correction to all probability estimates."
        )
        actionable = True

    # Category win rates
    cat_stats = compute_category_stats()
    if cat_stats:
        cat_lines = []
        for cat, s in cat_stats.items():
            if s["total"] >= 3:
                cat_lines.append(f"  {cat.upper()}: {s['win_rate']:.0%} win rate ({s['wins']}W/{s['losses']}L)")
        if cat_lines:
            lines.append("\n[CATEGORY WIN RATES — focus on high-win categories]")
            lines.extend(cat_lines)
            actionable = True

    return "\n".join(lines) if actionable else ""


# ── Summary for /stats command ────────────────────────────────────────────────

def calibration_summary_str() -> str:
    """Human-readable calibration report for the /stats Telegram command."""
    cal = compute_calibration()
    if not cal["ready"]:
        n = cal["n"]
        needed = MIN_RECORDS_FOR_CALIBRATION - n
        return f"Calibration: {n}/{MIN_RECORDS_FOR_CALIBRATION} resolved trades — need {needed} more."

    lines = [f"Calibration ({cal['n']} resolved trades):"]
    for tier in ("high", "medium", "low"):
        stats = cal["tiers"].get(tier)
        if not stats:
            continue
        bias_str = f"{stats['bias']:+.0%}"
        lines.append(
            f"  {tier.capitalize()} ({stats['n']}): "
            f"pred {stats['avg_predicted']:.0%} → actual {stats['actual_rate']:.0%} "
            f"[{bias_str}]"
        )
    overall = cal["bias"]
    lines.append(f"  Overall bias: {overall:+.0%} ({'over' if overall > 0 else 'under'}confident)")
    return "\n".join(lines)

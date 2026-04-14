"""
octo_boto_exit.py — Position exit logic for OctoBoto (#3)

Two exit strategies applied to open positions:

1. Trailing stop — lock in gains if price moves in our favour then reverses
   - Activates after position gains TRAIL_ACTIVATE_PCT (default 15%)
   - Stops out if price pulls back TRAIL_STOP_PCT (default 8%) from peak

2. Time-based decay — exit if position is losing and resolution is near
   - If Days_to_close <= TIME_EXIT_DAYS and PnL% < TIME_EXIT_MIN_PNL, exit
   - Avoids holding a loser into forced resolution at 0

Usage:
    from octo_boto_exit import check_exit_signals
    exits = check_exit_signals(open_positions, current_prices)
    # exits: list of {market_id, reason, action}
"""

import logging
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────

TRAIL_ACTIVATE_PCT  = 0.15   # Start trailing after +15% gain
TRAIL_STOP_PCT      = 0.08   # Stop out if price drops 8% from trail peak
TIME_EXIT_DAYS      = 2      # Days-to-close threshold for time-based exit
TIME_EXIT_MIN_PNL   = -0.10  # Exit if losing >10% and resolution <2 days away
MIN_HOLD_HOURS      = 12     # Never exit a position held less than 12 hours


# ── Trail peak tracking (in-memory, persists for session) ─────────────────────

_trail_peaks: dict = {}   # market_id → peak_price seen since position opened


def update_trail_peak(market_id: str, current_price: float) -> float:
    """Update and return the trailing peak for this position."""
    peak = _trail_peaks.get(market_id, current_price)
    if current_price > peak:
        _trail_peaks[market_id] = current_price
        return current_price
    return peak


def clear_trail(market_id: str):
    """Call when a position is closed."""
    _trail_peaks.pop(market_id, None)


# ── Days to close helper ──────────────────────────────────────────────────────

def _days_to_close(pos: dict) -> Optional[float]:
    end = pos.get("end_date") or pos.get("close_time") or pos.get("endDate")
    if not end:
        return None
    try:
        if isinstance(end, str):
            dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
        else:
            dt = end
        delta = (dt - datetime.now(timezone.utc)).total_seconds() / 86400
        return max(0.0, delta)
    except Exception:
        return None


def _hours_held(pos: dict) -> float:
    opened = pos.get("opened_at") or pos.get("entry_time")
    if not opened:
        return 999.0
    try:
        if isinstance(opened, str):
            dt = datetime.fromisoformat(opened.replace("Z", "+00:00"))
        else:
            dt = opened
        return (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    except Exception:
        return 999.0


# ── Main check ────────────────────────────────────────────────────────────────

def check_exit_signals(open_positions: list, current_prices: dict) -> list:
    """
    Check all open positions for exit signals.

    Args:
        open_positions: list of position dicts from TRACKER.open_positions()
        current_prices: dict of {market_id: current_yes_price}

    Returns:
        List of {market_id, question, reason, current_price, entry_price, pnl_pct}
        for positions that should be exited.
    """
    exits = []

    for pos in open_positions:
        mid = pos["market_id"]
        side = pos.get("side", "YES")
        entry = pos.get("entry_price", 0.5)

        current_yes = current_prices.get(mid)
        if current_yes is None:
            continue

        current = current_yes if side == "YES" else (1.0 - current_yes)
        pnl_pct = (current - entry) / entry if entry > 0 else 0.0

        # Minimum hold — never exit too early
        if _hours_held(pos) < MIN_HOLD_HOURS:
            continue

        # ── Trailing stop ─────────────────────────────────────────────────────
        peak = update_trail_peak(mid, current)
        if pnl_pct >= TRAIL_ACTIVATE_PCT:
            drawdown_from_peak = (peak - current) / peak if peak > 0 else 0.0
            if drawdown_from_peak >= TRAIL_STOP_PCT:
                exits.append({
                    "market_id":   mid,
                    "question":    pos.get("question", ""),
                    "reason":      f"trailing stop — peak {peak:.3f} → now {current:.3f} (−{drawdown_from_peak:.1%})",
                    "current_price": current,
                    "entry_price": entry,
                    "pnl_pct":     pnl_pct,
                })
                continue

        # ── Time-based decay exit ─────────────────────────────────────────────
        days_left = _days_to_close(pos)
        if days_left is not None and days_left <= TIME_EXIT_DAYS and pnl_pct < TIME_EXIT_MIN_PNL:
            exits.append({
                "market_id":   mid,
                "question":    pos.get("question", ""),
                "reason":      f"time exit — {days_left:.1f}d to close, PnL {pnl_pct:+.1%}",
                "current_price": current,
                "entry_price": entry,
                "pnl_pct":     pnl_pct,
            })

    return exits


def exit_summary_str(exits: list) -> str:
    if not exits:
        return ""
    lines = [f"⚠️ *{len(exits)} position(s) flagged for exit:*"]
    for e in exits:
        pnl = f"{e['pnl_pct']:+.1%}"
        lines.append(f"• `{e['question'][:50]}` | {pnl} | {e['reason']}")
    return "\n".join(lines)

"""Historical signal pattern lookup from resolved oracle calls."""

import json
from pathlib import Path

_CALLS_PATH = Path(r"C:\Users\walli\octodamus\data\octo_calls.json")


def _load_calls() -> list:
    """Load and return all calls from octo_calls.json."""
    try:
        if not _CALLS_PATH.exists():
            return []
        with open(_CALLS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        return []
    except Exception:
        return []


def _count_signals(signals: dict) -> tuple[int, int]:
    """Return (bull_count, bear_count) from a signals dict."""
    bull = 0
    bear = 0
    for val in signals.values():
        if isinstance(val, str):
            v = val.lower()
            if v == "bull":
                bull += 1
            elif v == "bear":
                bear += 1
        elif isinstance(val, dict):
            v = val.get("signal", "").lower()
            if v == "bull":
                bull += 1
            elif v == "bear":
                bear += 1
    return bull, bear


def get_pattern_win_rate(
    bull_count: int,
    bear_count: int,
    asset: str = None,
) -> dict:
    """Return historical win rate for setups similar to the given bull/bear counts.

    Matches resolved oracle calls where bull_count and bear_count are within ±1
    of the provided values.
    """
    calls = _load_calls()

    similar_calls = 0
    wins = 0

    for call in calls:
        # Only consider resolved oracle-type calls
        call_type = call.get("type", "oracle")
        if call_type not in ("oracle", "signal", "call"):
            continue
        outcome = call.get("outcome", call.get("result", ""))
        if not outcome:
            continue  # not resolved

        # Asset filter (optional)
        if asset:
            call_asset = call.get("asset", call.get("ticker", "")).upper()
            if call_asset and call_asset != asset.upper():
                continue

        # Determine bull/bear counts from the stored signals dict
        signals = call.get("signals", {})
        if not signals:
            # Fall back to stored counts if available
            stored_bull = call.get("bull_count")
            stored_bear = call.get("bear_count")
            if stored_bull is None or stored_bear is None:
                continue
            c_bull, c_bear = int(stored_bull), int(stored_bear)
        else:
            c_bull, c_bear = _count_signals(signals)

        # Match within ±1 of both counts
        if abs(c_bull - bull_count) <= 1 and abs(c_bear - bear_count) <= 1:
            similar_calls += 1
            outcome_str = str(outcome).lower()
            if outcome_str in ("win", "correct", "hit", "1", "true", "yes"):
                wins += 1

    win_rate = (wins / similar_calls) if similar_calls > 0 else None

    if similar_calls == 0:
        note = f"No historical matches for bull={bull_count}, bear={bear_count} (±1)"
    elif win_rate is None:
        note = "Matches found but win rate could not be computed"
    else:
        note = (
            f"Found {similar_calls} similar setup(s) with bull≈{bull_count}, bear≈{bear_count}; "
            f"historical win rate: {win_rate:.1%}"
        )

    return {
        "similar_calls": similar_calls,
        "wins": wins,
        "win_rate": round(win_rate, 4) if win_rate is not None else None,
        "note": note,
    }


def pattern_context_str(bull_count: int, bear_count: int, asset: str = None) -> str:
    """Return a formatted string for prompt injection."""
    try:
        p = get_pattern_win_rate(bull_count, bear_count, asset)
        asset_str = f" | {asset.upper()}" if asset else ""
        wr_str = f"{p['win_rate']:.1%}" if p["win_rate"] is not None else "N/A"
        return (
            f"[PATTERN DB{asset_str}]\n"
            f"  Setup         : bull={bull_count}, bear={bear_count} (±1 match)\n"
            f"  Similar Calls : {p['similar_calls']}\n"
            f"  Wins          : {p['wins']}\n"
            f"  Win Rate      : {wr_str}\n"
            f"  Note          : {p['note']}\n"
        )
    except Exception:
        return ""

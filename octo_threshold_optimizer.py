"""Rolling 30-day backtest to find the optimal signal threshold for the oracle."""

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

_CALLS_PATH = Path(r"C:\Users\walli\octodamus\data\octo_calls.json")
_CURRENT_THRESHOLD = 7


def _load_calls() -> list:
    """Load calls from octo_calls.json."""
    try:
        if not _CALLS_PATH.exists():
            return []
        with open(_CALLS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _parse_ts(call: dict) -> datetime | None:
    """Parse a call's timestamp into a UTC-aware datetime."""
    for field in ("timestamp", "created_at", "date", "ts"):
        val = call.get(field)
        if not val:
            continue
        try:
            if isinstance(val, (int, float)):
                return datetime.fromtimestamp(val, tz=timezone.utc)
            dt = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            continue
    return None


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


def _is_win(call: dict) -> bool | None:
    """Return True=win, False=loss, None=unresolved."""
    outcome = call.get("outcome", call.get("result", ""))
    if not outcome:
        return None
    outcome_str = str(outcome).lower()
    if outcome_str in ("win", "correct", "hit", "1", "true", "yes"):
        return True
    if outcome_str in ("loss", "wrong", "miss", "0", "false", "no"):
        return False
    return None


def get_optimal_threshold(days_back: int = 30) -> dict:
    """Return threshold optimization results over the last `days_back` days."""
    calls = _load_calls()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)

    # Filter to resolved oracle calls within the window that have a signals dict
    eligible = []
    for call in calls:
        call_type = call.get("type", "oracle")
        if call_type not in ("oracle", "signal", "call"):
            continue
        win = _is_win(call)
        if win is None:
            continue
        signals = call.get("signals", {})
        # Also accept stored counts
        if not signals:
            stored_bull = call.get("bull_count")
            stored_bear = call.get("bear_count")
            if stored_bull is None or stored_bear is None:
                continue
            bull, bear = int(stored_bull), int(stored_bear)
        else:
            bull, bear = _count_signals(signals)

        ts = _parse_ts(call)
        if ts is None or ts < cutoff:
            continue

        # Direction: more bull → long call, more bear → short call
        direction = "bull" if bull >= bear else "bear"
        leading_count = bull if direction == "bull" else bear

        eligible.append({"win": win, "bull": bull, "bear": bear, "leading": leading_count})

    results = {}
    for threshold in range(2, 12):
        qualifying = [e for e in eligible if e["leading"] >= threshold]
        total = len(qualifying)
        wins = sum(1 for e in qualifying if e["win"])
        win_rate = (wins / total) if total > 0 else 0.0
        results[threshold] = {
            "calls": total,
            "wins": wins,
            "win_rate": round(win_rate, 4),
        }

    # Find recommended threshold: lowest with ≥80% win rate and ≥5 calls
    recommended = None
    for threshold in range(2, 12):
        r = results[threshold]
        if r["calls"] >= 5 and r["win_rate"] >= 0.80:
            recommended = threshold
            break

    if not eligible:
        note = f"No resolved calls with signal data found in last {days_back} days."
    elif recommended is None:
        note = (
            f"Analyzed {len(eligible)} resolved call(s) over {days_back} days. "
            f"No threshold achieved ≥80% win rate with ≥5 calls. "
            f"Keep current threshold of {_CURRENT_THRESHOLD} or gather more data."
        )
    else:
        r = results[recommended]
        note = (
            f"Analyzed {len(eligible)} resolved call(s) over {days_back} days. "
            f"Recommended threshold: {recommended} "
            f"({r['calls']} calls, {r['win_rate']:.1%} win rate)."
        )

    return {
        "results": results,
        "recommended_threshold": recommended,
        "current_threshold": _CURRENT_THRESHOLD,
        "note": note,
    }


def threshold_advisory_str() -> str:
    """Return a formatted multi-line string for Discord/Telegram alerts."""
    try:
        data = get_optimal_threshold(days_back=30)
        rec = data["recommended_threshold"]
        current = data["current_threshold"]
        note = data["note"]

        lines = [
            "THRESHOLD OPTIMIZER — 30-Day Rolling Backtest",
            "=" * 46,
        ]

        header = f"{'Threshold':>10} | {'Calls':>6} | {'Wins':>5} | {'Win Rate':>9}"
        lines.append(header)
        lines.append("-" * 46)

        for t in range(2, 12):
            r = data["results"][t]
            marker = " <-- CURRENT" if t == current else ""
            marker = " <-- RECOMMENDED" if t == rec else marker
            lines.append(
                f"{t:>10} | {r['calls']:>6} | {r['wins']:>5} | {r['win_rate']:>8.1%}{marker}"
            )

        lines.append("=" * 46)
        lines.append(f"Note: {note}")
        if rec and rec != current:
            lines.append(
                f"ACTION: Consider changing threshold from {current} → {rec}"
            )
        elif rec == current:
            lines.append(f"ACTION: Current threshold ({current}) is already optimal.")
        else:
            lines.append("ACTION: Insufficient data — maintain current threshold.")

        return "\n".join(lines)
    except Exception:
        return ""

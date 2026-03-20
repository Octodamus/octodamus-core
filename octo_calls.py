"""
octo_calls.py — Octodamus Directional Call Tracker
Tracks UP/DOWN calls made in posts and their outcomes.
Integrates with octodamus_runner.py to inject call history into prompts.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

CALLS_FILE = Path(__file__).parent / "data" / "octo_calls.json"

# ── Structures ────────────────────────────────────────────────────────────────

def _load() -> list:
    try:
        if CALLS_FILE.exists():
            return json.loads(CALLS_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return []

def _save(calls: list):
    try:
        CALLS_FILE.parent.mkdir(parents=True, exist_ok=True)
        CALLS_FILE.write_text(json.dumps(calls, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[OctoCalls] Save failed: {e}")


# ── Record a call ─────────────────────────────────────────────────────────────

def record_call(
    asset: str,
    direction: str,        # "UP" or "DOWN"
    entry_price: float,
    target_price: Optional[float] = None,
    timeframe: str = "24h",
    post_text: str = "",
) -> dict:
    """Record a directional call made in a post."""
    calls = _load()
    call = {
        "id":           len(calls) + 1,
        "asset":        asset.upper(),
        "direction":    direction.upper(),
        "entry_price":  entry_price,
        "target_price": target_price,
        "timeframe":    timeframe,
        "post_text":    post_text[:120],
        "made_at":      datetime.now(timezone.utc).isoformat(),
        "resolved":     False,
        "outcome":      None,   # "WIN" | "LOSS" | "PUSH"
        "exit_price":   None,
        "resolved_at":  None,
        "pnl_pct":      None,
    }
    calls.append(call)
    _save(calls)
    print(f"[OctoCalls] Recorded: {asset} {direction} @ ${entry_price:,.2f}")
    return call


# ── Resolve a call ────────────────────────────────────────────────────────────

def resolve_call(call_id: int, exit_price: float) -> Optional[dict]:
    """Mark a call as resolved with exit price. Auto-determines WIN/LOSS."""
    calls = _load()
    for c in calls:
        if c["id"] == call_id and not c["resolved"]:
            entry = c["entry_price"]
            direction = c["direction"]
            pnl = ((exit_price - entry) / entry) * 100
            if direction == "UP":
                outcome = "WIN" if exit_price > entry * 1.001 else ("LOSS" if exit_price < entry * 0.999 else "PUSH")
            else:  # DOWN
                outcome = "WIN" if exit_price < entry * 0.999 else ("LOSS" if exit_price > entry * 1.001 else "PUSH")
            c["resolved"]    = True
            c["outcome"]     = outcome
            c["exit_price"]  = exit_price
            c["resolved_at"] = datetime.now(timezone.utc).isoformat()
            c["pnl_pct"]     = round(pnl, 2)
            _save(calls)
            print(f"[OctoCalls] Resolved #{call_id}: {outcome} | {c['asset']} {direction} | exit ${exit_price:,.2f} | {pnl:+.1f}%")
            return c
    print(f"[OctoCalls] Call #{call_id} not found or already resolved")
    return None


# ── Stats ─────────────────────────────────────────────────────────────────────

def get_stats() -> dict:
    """Get win/loss stats for all resolved calls."""
    calls = _load()
    resolved = [c for c in calls if c["resolved"]]
    wins    = [c for c in resolved if c["outcome"] == "WIN"]
    losses  = [c for c in resolved if c["outcome"] == "LOSS"]
    pushes  = [c for c in resolved if c["outcome"] == "PUSH"]
    open_calls = [c for c in calls if not c["resolved"]]

    win_rate = (len(wins) / len(resolved) * 100) if resolved else 0

    return {
        "total_calls":   len(calls),
        "resolved":      len(resolved),
        "open":          len(open_calls),
        "wins":          len(wins),
        "losses":        len(losses),
        "pushes":        len(pushes),
        "win_rate":      round(win_rate, 1),
        "streak":        _current_streak(resolved),
    }


def _current_streak(resolved: list) -> str:
    """Return current win/loss streak e.g. 'W3' or 'L2'."""
    if not resolved:
        return "—"
    last = resolved[-1]["outcome"]
    count = 0
    for c in reversed(resolved):
        if c["outcome"] == last:
            count += 1
        else:
            break
    symbol = "W" if last == "WIN" else "L"
    return f"{symbol}{count}"


def get_open_calls() -> list:
    return [c for c in _load() if not c["resolved"]]


def get_recent_calls(n: int = 5) -> list:
    calls = _load()
    return sorted(calls, key=lambda x: x["made_at"], reverse=True)[:n]


# ── Prompt injection ──────────────────────────────────────────────────────────

def build_call_context() -> str:
    """
    Build a context string to inject into Claude prompts.
    Tells the oracle about its recent calls and win rate.
    """
    stats = get_stats()
    recent = get_recent_calls(5)
    open_calls = get_open_calls()

    lines = [
        f"YOUR CALL RECORD: {stats['wins']}W / {stats['losses']}L / {stats['pushes']}P "
        f"— {stats['win_rate']}% win rate — Streak: {stats['streak']}",
    ]

    if open_calls:
        lines.append("OPEN CALLS (still live):")
        for c in open_calls[-3:]:
            lines.append(f"  {c['asset']} {c['direction']} @ ${c['entry_price']:,.2f} ({c['timeframe']})")

    if recent:
        lines.append("RECENT CALLS:")
        for c in recent[:5]:
            status = c['outcome'] if c['resolved'] else 'OPEN'
            lines.append(f"  {c['asset']} {c['direction']} @ ${c['entry_price']:,.2f} → {status}")

    lines.append(
        "INSTRUCTION: Make ONE directional call in this post. "
        "State the asset, direction (up/down/bullish/bearish), "
        "a specific price target or level, and a timeframe. "
        "Be accountable — you track these. The oracle doesn't hedge."
    )

    return "\n".join(lines)


# ── Auto-parse calls from post text ──────────────────────────────────────────

def parse_call_from_post(post_text: str, asset: str, current_price: float) -> Optional[dict]:
    """
    Try to auto-detect a directional call from post text.
    Returns call dict if found, None otherwise.
    """
    text = post_text.lower()

    # Detect direction
    up_signals   = ["bullish", "up", "rising", "rally", "bounce", "buy", "long", "higher", "breakout", "floor"]
    down_signals = ["bearish", "down", "falling", "drop", "dump", "sell", "short", "lower", "breakdown", "ceiling"]

    up_count   = sum(1 for w in up_signals if w in text)
    down_count = sum(1 for w in down_signals if w in text)

    if up_count == 0 and down_count == 0:
        return None

    direction = "UP" if up_count >= down_count else "DOWN"

    # Try to extract a price target
    prices = re.findall(r'\$[\d,]+(?:\.\d+)?k?', post_text)
    target = None
    for p in prices:
        val = p.replace('$', '').replace(',', '').replace('k', '000')
        try:
            v = float(val)
            if v != current_price and v > 0:
                target = v
                break
        except Exception:
            pass

    return record_call(
        asset=asset,
        direction=direction,
        entry_price=current_price,
        target_price=target,
        post_text=post_text,
    )


# ── CLI for manual use ────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    args = sys.argv[1:]

    if not args or args[0] == "stats":
        s = get_stats()
        print(f"\nOctodamus Call Record")
        print(f"{'='*30}")
        print(f"Record:   {s['wins']}W / {s['losses']}L / {s['pushes']}P")
        print(f"Win rate: {s['win_rate']}%")
        print(f"Streak:   {s['streak']}")
        print(f"Open:     {s['open']} calls")
        print()
        open_calls = get_open_calls()
        if open_calls:
            print("Open calls:")
            for c in open_calls:
                print(f"  #{c['id']} {c['asset']} {c['direction']} @ ${c['entry_price']:,.2f} ({c['made_at'][:10]})")

    elif args[0] == "resolve" and len(args) >= 3:
        resolve_call(int(args[1]), float(args[2]))

    elif args[0] == "call" and len(args) >= 4:
        record_call(
            asset=args[1],
            direction=args[2],
            entry_price=float(args[3]),
            timeframe=args[4] if len(args) > 4 else "24h",
        )
    else:
        print("Usage:")
        print("  python3 octo_calls.py stats")
        print("  python3 octo_calls.py call BTC UP 69000 24h")
        print("  python3 octo_calls.py resolve 1 71500")

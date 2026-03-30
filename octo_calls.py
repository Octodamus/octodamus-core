"""
octo_calls.py — Octodamus Directional Call Tracker v2
Tracks UP/DOWN calls, auto-resolves from live prices, injects into prompts.

CLI:
  python octo_calls.py status                          Show full record
  python octo_calls.py call BTC UP 69000 24h           Record a call
  python octo_calls.py resolve 2 71500                 Manually resolve
  python octo_calls.py autoresolve                     Auto-resolve from live prices
  python octo_calls.py inject                          Print prompt injection block
"""

import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

CALLS_FILE = Path(__file__).parent / "data" / "octo_calls.json"


# ── Load / Save ───────────────────────────────────────────────────────────────

def _load() -> list:
    try:
        if CALLS_FILE.exists():
            return json.loads(CALLS_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return []

def _save(calls: list):
    CALLS_FILE.parent.mkdir(parents=True, exist_ok=True)
    CALLS_FILE.write_text(json.dumps(calls, indent=2), encoding="utf-8")


# ── Record ────────────────────────────────────────────────────────────────────

def record_call(
    asset: str,
    direction: str,
    entry_price: float,
    timeframe: str = "24h",
    target_price: Optional[float] = None,
    note: str = "",
) -> dict:
    calls = _load()

    # Prevent duplicate open calls on same asset
    for c in calls:
        if not c["resolved"] and c["asset"] == asset.upper():
            print(f"[OctoCalls] Skipped — already have open call on {asset.upper()} (#{c['id']})")
            return c

    call = {
        "id":           len(calls) + 1,
        "asset":        asset.upper(),
        "direction":    direction.upper(),
        "entry_price":  entry_price,
        "target_price": target_price,
        "timeframe":    timeframe,
        "note":         note[:200],
        "made_at":      datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "resolved":     False,
        "outcome":      None,
        "exit_price":   None,
        "resolved_at":  None,
    }
    calls.append(call)
    _save(calls)
    print(f"[OctoCalls] #{call['id']} recorded: {asset.upper()} {direction.upper()} @ ${entry_price:,.2f}")
    return call


# ── Resolve ───────────────────────────────────────────────────────────────────

def resolve_call(call_id: int, exit_price: float) -> Optional[dict]:
    calls = _load()
    for c in calls:
        if c["id"] == call_id and not c["resolved"]:
            c["exit_price"] = exit_price
            c["resolved"] = True
            c["resolved_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            d = c["direction"]
            if d == "UP":
                c["outcome"] = "WIN" if exit_price > c["entry_price"] else "LOSS"
            elif d == "DOWN":
                c["outcome"] = "WIN" if exit_price < c["entry_price"] else "LOSS"
            else:
                c["outcome"] = "PUSH"
            _save(calls)
            print(f"[OctoCalls] #{call_id} resolved: {c['outcome']} (${c['entry_price']:,.2f} -> ${exit_price:,.2f})")
            return c
    print(f"[OctoCalls] Call #{call_id} not found or already resolved.")
    return None


# ── Auto-resolve from live prices ─────────────────────────────────────────────

def _fetch_price(asset: str) -> Optional[float]:
    """Fetch current price for an asset."""
    try:
        import requests
        asset = asset.upper()
        # Crypto via CoinGecko
        cg_map = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}
        if asset in cg_map:
            r = requests.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": cg_map[asset], "vs_currencies": "usd"},
                timeout=8
            )
            if r.status_code == 200:
                return r.json().get(cg_map[asset], {}).get("usd")
        # Stocks via yfinance
        try:
            import yfinance as yf
            t = yf.Ticker(asset)
            price = t.fast_info.get("lastPrice") or t.info.get("regularMarketPrice")
            if price:
                return float(price)
        except Exception:
            pass
    except Exception as e:
        print(f"[OctoCalls] Price fetch failed for {asset}: {e}")
    return None


def _is_expired(call: dict) -> bool:
    """Check if a call has exceeded its timeframe."""
    made = call.get("made_at", "")
    tf = call.get("timeframe", "24h").lower().strip()

    try:
        made_dt = datetime.strptime(made, "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)
    except Exception:
        return False

    now = datetime.now(timezone.utc)

    if "h" in tf:
        hours = int(re.search(r"(\d+)", tf).group(1)) if re.search(r"(\d+)", tf) else 24
        return now > made_dt + timedelta(hours=hours)
    elif "friday" in tf:
        # Expired if it's Saturday or later
        days_until_sat = (5 - made_dt.weekday()) % 7
        if days_until_sat == 0:
            days_until_sat = 7
        expiry = made_dt.replace(hour=21, minute=0) + timedelta(days=days_until_sat)
        return now > expiry
    elif "end of week" in tf or "eow" in tf:
        days_until_sat = (5 - made_dt.weekday()) % 7
        if days_until_sat == 0:
            days_until_sat = 7
        expiry = made_dt.replace(hour=21, minute=0) + timedelta(days=days_until_sat)
        return now > expiry
    elif "wednesday" in tf or "thursday" in tf or "tuesday" in tf or "monday" in tf:
        day_map = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3}
        target_day = day_map.get(tf.split()[0], made_dt.weekday())
        days_until = (target_day - made_dt.weekday()) % 7
        if days_until == 0:
            days_until = 7
        expiry = made_dt.replace(hour=21, minute=0) + timedelta(days=days_until)
        return now > expiry
    else:
        # Default: 48h expiry
        return now > made_dt + timedelta(hours=48)


def autoresolve() -> list:
    """Check open calls against live prices. Resolve expired ones."""
    calls = _load()
    resolved = []
    for c in calls:
        if c["resolved"]:
            continue
        if not _is_expired(c):
            continue
        price = _fetch_price(c["asset"])
        if price is None:
            print(f"[OctoCalls] Could not fetch price for {c['asset']} — skipping #{c['id']}")
            continue
        result = resolve_call(c["id"], price)
        if result:
            resolved.append(result)
    return resolved


# ── Stats ─────────────────────────────────────────────────────────────────────

def get_stats() -> dict:
    calls = _load()
    resolved = [c for c in calls if c["resolved"]]
    wins = sum(1 for c in resolved if c["outcome"] == "WIN")
    losses = sum(1 for c in resolved if c["outcome"] == "LOSS")
    open_calls = [c for c in calls if not c["resolved"]]

    streak = ""
    streak_char = ""
    streak_count = 0
    for c in reversed(resolved):
        ch = c["outcome"][0]
        if not streak:
            streak_char = ch
            streak_count = 1
            streak = ch + "1"
        elif ch == streak_char:
            streak_count += 1
            streak = streak_char + str(streak_count)
        else:
            break

    rate = f"{wins/(wins+losses)*100:.0f}%" if (wins + losses) > 0 else "N/A"
    return {
        "total": len(calls),
        "wins": wins,
        "losses": losses,
        "win_rate": rate,
        "streak": streak or "\u2014",
        "open": len(open_calls),
        "open_calls": open_calls,
        "all_calls": calls,
    }


# ── Prompt injection ──────────────────────────────────────────────────────────

def build_call_context() -> str:
    """Injected into every Claude prompt by the runner."""
    s = get_stats()
    lines = []
    lines.append("── DIRECTIONAL CALL SYSTEM ──")
    lines.append(f"Record: {s['wins']}W / {s['losses']}L | Win rate: {s['win_rate']} | Streak: {s['streak']}")

    if s["open_calls"]:
        lines.append("Open calls (do NOT call these assets again):")
        for c in s["open_calls"]:
            t = f" target ${c['target_price']:,.0f}" if c.get("target_price") else ""
            lines.append(f"  #{c['id']} {c['asset']} {c['direction']} @ ${c['entry_price']:,.2f}{t} [{c['timeframe']}]")

    lines.append("")
    lines.append("CALL RULES — your win rate IS your reputation:")
    lines.append("1. You MUST make exactly one Oracle call in this post.")
    lines.append("2. Only skip if you have open calls on ALL available assets (BTC, ETH, SOL, NVDA, TSLA).")
    lines.append("3. F&G below 15 or above 80 = high conviction. Big moves (>3%) with catalyst = call it.")
    lines.append("4. Use realistic targets: 2-5% crypto, 1-3% stocks. No moonshots.")
    lines.append("5. Timeframes: 24h, 48h, or end of week. Never longer than 7 days.")
    lines.append("6. FORMAT — put this as the LAST LINE of your post, exactly like this:")
    lines.append("   Oracle call: ASSET UP from $PRICE to $TARGET by TIMEFRAME.")
    lines.append("   Oracle call: ASSET DOWN from $PRICE to $TARGET by TIMEFRAME.")
    lines.append("7. Examples:")
    lines.append("   Oracle call: BTC UP from $70000 to $73500 by 48h.")
    lines.append("   Oracle call: NVDA DOWN from $175 to $165 by Friday close.")
    lines.append("   Oracle call: SOL DOWN from $89 to $83 by end of week.")
    lines.append("8. The Oracle call line MUST be present. It is how your record is tracked.")

    return "\n".join(lines)


# ── Parse call from post text ─────────────────────────────────────────────────


def _parse_price(s: str) -> float:
    """Parse price string like '70,500' or '71K' or '2.5M' to float."""
    s = s.strip().replace(",", "")
    multiplier = 1
    if s[-1:].upper() == 'K':
        multiplier = 1_000
        s = s[:-1]
    elif s[-1:].upper() == 'M':
        multiplier = 1_000_000
        s = s[:-1]
    return float(s) * multiplier


def parse_call_from_post(post_text: str) -> Optional[dict]:
    """Extract and record Oracle call from post text. Returns the call dict or None."""
    # Pattern: Oracle call: ASSET UP/DOWN from $PRICE to $TARGET by TIMEFRAME
    pattern = r'[Oo]racle call:\s*(\w+)\s+(UP|DOWN|up|down)\s+(?:from\s+)?\$?([\d,]+(?:\.\d+)?[KkMm]?)\s+to\s+\$?([\d,]+(?:\.\d+)?[KkMm]?)\s+(?:by\s+)?(.+?)[\.\!\n]'
    m = re.search(pattern, post_text)
    if not m:
        # Try end-of-string variant
        pattern2 = r'[Oo]racle call:\s*(\w+)\s+(UP|DOWN|up|down)\s+(?:from\s+)?\$?([\d,]+(?:\.\d+)?[KkMm]?)\s+to\s+\$?([\d,]+(?:\.\d+)?[KkMm]?)\s+(?:by\s+)?(.+?)$'
        m = re.search(pattern2, post_text)

    if m:
        asset = m.group(1).upper()
        direction = m.group(2).upper()
        entry = _parse_price(m.group(3))
        target = _parse_price(m.group(4))
        timeframe = m.group(5).strip().rstrip(".")
        return record_call(asset, direction, entry, timeframe, target, note=post_text[:120])

    # Fallback: less strict pattern for CONTRARIAN voice
    # "Oracle call: fades to $79 by Wednesday" or "Oracle call: $168 before $210"
    pattern3 = r'[Oo]racle call:\s*(?:(\w+)\s+)?(?:fades?\s+to|drops?\s+to|rises?\s+to|pumps?\s+to)\s+\$?([\d,]+(?:\.\d+)?[KkMm]?)\s+(?:by\s+)?(.+?)[\.\!\n]?$'
    m3 = re.search(pattern3, post_text)
    if m3:
        # Try to extract asset from earlier in the post
        asset = m3.group(1) or "BTC"
        target = float(m3.group(2).replace(",", ""))
        timeframe = m3.group(3).strip().rstrip(".")
        # Infer direction from verb
        verb_match = re.search(r'(fades?|drops?|rises?|pumps?)', post_text[m3.start():])
        direction = "DOWN" if verb_match and verb_match.group(1).startswith(("fade", "drop")) else "UP"
        print(f"[OctoCalls] Parsed loose format: {asset} {direction} to ${target} by {timeframe}")
        return record_call(asset.upper(), direction, target, timeframe, note=post_text[:120])

    print("[OctoCalls] No Oracle call found in post text.")
    return None


# ── Aliases for runner compatibility ──────────────────────────────────────────

def build_template_prompt_context() -> str:
    return build_call_context()


# ── CLI ───────────────────────────────────────────────────────────────────────

def print_status():
    s = get_stats()
    print(f"\n  OCTODAMUS CALL RECORD")
    print(f"  {'='*40}")
    print(f"  Record:   {s['wins']}W / {s['losses']}L")
    print(f"  Win Rate: {s['win_rate']}")
    print(f"  Streak:   {s['streak']}")
    print(f"  Open:     {s['open']}")
    print()
    for c in s["all_calls"]:
        status = c["outcome"] if c["resolved"] else "OPEN"
        arrow = "^" if c["direction"] == "UP" else "v"
        exit_str = f" -> ${c['exit_price']:,.2f}" if c.get("exit_price") else ""
        print(f"  #{c['id']:03d} {c['asset']:5s} {arrow} ${c['entry_price']:>10,.2f}{exit_str}  [{status}]  {c['made_at']}")
    print()


if __name__ == "__main__":
    args = sys.argv[1:]

    if not args or args[0] == "status":
        print_status()

    elif args[0] == "call":
        if len(args) < 5:
            print("Usage: python octo_calls.py call ASSET UP/DOWN PRICE TIMEFRAME [--note 'reason']")
            sys.exit(1)
        asset, direction, entry, timeframe = args[1], args[2], float(args[3]), args[4]
        note = ""
        if "--note" in args:
            ni = args.index("--note")
            if ni + 1 < len(args):
                note = args[ni + 1]
        record_call(asset, direction, entry, timeframe, note=note)

    elif args[0] == "resolve":
        if len(args) < 3:
            print("Usage: python octo_calls.py resolve CALL_ID EXIT_PRICE")
            sys.exit(1)
        resolve_call(int(args[1]), float(args[2]))

    elif args[0] == "autoresolve":
        results = autoresolve()
        if results:
            for r in results:
                print(f"  #{r['id']} {r['asset']} {r['outcome']} (${r['entry_price']:,.2f} -> ${r['exit_price']:,.2f})")
        else:
            print("  No calls ready to resolve.")

    elif args[0] == "inject":
        print(build_call_context())

    else:
        print("Usage:")
        print("  python octo_calls.py status")
        print("  python octo_calls.py call BTC UP 69000 24h")
        print("  python octo_calls.py resolve 1 71500")
        print("  python octo_calls.py autoresolve")
        print("  python octo_calls.py inject")

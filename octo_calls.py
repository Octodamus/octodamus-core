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
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

CALLS_FILE = Path(__file__).parent / "data" / "octo_calls.json"


# ── Market snapshot (captured at call time AND resolution time) ───────────────

def _fetch_market_snapshot(asset: str, price: float) -> dict:
    """
    Market context snapshot. Called at record time and again at resolution time.
    Captures F&G, 24h change, macro signal, funding rate, and open interest.
    All external calls wrapped in try/except — never blocks call recording.
    """
    import requests
    snap = {"asset": asset.upper(), "price": price}

    # Fear & Greed (free, no key)
    try:
        fng = requests.get("https://api.alternative.me/fng/?limit=1", timeout=5).json()
        snap["fear_greed"] = int(fng["data"][0]["value"])
        snap["fear_greed_label"] = fng["data"][0]["value_classification"]
    except Exception:
        pass

    # 24h price change (CoinGecko, free)
    try:
        cg_map = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}
        cg_id = cg_map.get(asset.upper())
        if cg_id:
            r = requests.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": cg_id, "vs_currencies": "usd", "include_24hr_change": "true"},
                timeout=6,
            ).json()
            snap["change_24h_pct"] = round(r.get(cg_id, {}).get("usd_24h_change", 0), 2)
    except Exception:
        pass

    # Macro signal (uses 4h cache — no extra API cost)
    try:
        from octo_macro import get_macro_signal
        macro = get_macro_signal()
        snap["macro_signal"] = macro.get("signal", "NEUTRAL")
        snap["macro_score"] = macro.get("score", 0)
    except Exception:
        pass

    # Funding rate + open interest (CoinGlass — requires key, graceful skip)
    if asset.upper() in ("BTC", "ETH", "SOL"):
        try:
            import octo_coinglass as glass
            fr = glass.funding_rate_exchange(asset.upper())
            rates = [ex.get("funding_rate", 0) or 0 for ex in fr.get("data", []) if ex.get("funding_rate") is not None]
            if rates:
                snap["funding_rate_pct"] = round(sum(rates) / len(rates) * 100, 4)
        except Exception:
            pass
        try:
            import octo_coinglass as glass
            oi = glass.open_interest(asset.upper(), interval="4h")
            rows = oi.get("data", {}).get("list", [])
            if rows:
                snap["open_interest_usd"] = rows[-1].get("openInterest", None)
        except Exception:
            pass

    snap["captured_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return snap


def _fng_bucket(fng: int) -> str:
    """Bucket F&G index into a label for pattern matching."""
    if fng < 25:   return "extreme_fear"
    if fng < 45:   return "fear"
    if fng < 55:   return "neutral"
    if fng < 75:   return "greed"
    return "extreme_greed"


def _get_pattern_context(asset: str, direction: str, snap: dict) -> str:
    """
    Scan resolved oracle call history for setups similar to the current snapshot.
    Returns a plain-English pattern summary to inject into the post-mortem prompt.
    Similarity: same asset + direction + F&G bucket + macro signal.
    """
    calls = _load()
    resolved = [
        c for c in calls
        if c.get("resolved") and c.get("call_type", "oracle") == "oracle"
        and c.get("asset") == asset.upper()
        and c.get("direction") == direction.upper()
    ]
    if not resolved:
        return ""

    current_fng = snap.get("fear_greed")
    current_macro = snap.get("macro_signal", "")
    current_bucket = _fng_bucket(current_fng) if current_fng is not None else None

    lines = []

    # Pattern 1: same asset/direction overall
    wins = sum(1 for c in resolved if c.get("outcome") == "WIN")
    lines.append(f"{asset.upper()} {direction.upper()} calls overall: {wins}W/{len(resolved)-wins}L from {len(resolved)} resolved")

    # Pattern 2: same F&G bucket
    if current_bucket:
        bucket_calls = [c for c in resolved if _fng_bucket(c.get("market_snapshot", {}).get("fear_greed", 50)) == current_bucket]
        if bucket_calls:
            bw = sum(1 for c in bucket_calls if c.get("outcome") == "WIN")
            lines.append(f"  When F&G was '{current_bucket.replace('_',' ')}': {bw}W/{len(bucket_calls)-bw}L")

    # Pattern 3: same macro signal
    if current_macro:
        macro_calls = [c for c in resolved if c.get("market_snapshot", {}).get("macro_signal") == current_macro]
        if macro_calls:
            mw = sum(1 for c in macro_calls if c.get("outcome") == "WIN")
            lines.append(f"  When macro was '{current_macro}': {mw}W/{len(macro_calls)-mw}L")

    # Pattern 4: F&G bucket + macro combined (tightest signal)
    if current_bucket and current_macro:
        combo = [
            c for c in resolved
            if _fng_bucket(c.get("market_snapshot", {}).get("fear_greed", 50)) == current_bucket
            and c.get("market_snapshot", {}).get("macro_signal") == current_macro
        ]
        if combo:
            cw = sum(1 for c in combo if c.get("outcome") == "WIN")
            lines.append(f"  Combined (F&G '{current_bucket.replace('_',' ')}' + macro '{current_macro}'): {cw}W/{len(combo)-cw}L")

    return "\n".join(lines) if lines else ""


# ── Post-mortem (generated on resolution) ────────────────────────────────────

def _generate_post_mortem(call: dict) -> str:
    """
    Haiku analysis on a resolved call using call-time snapshot, resolution-time
    snapshot, and historical pattern context. Returns 2-3 sentence post-mortem.
    """
    try:
        import anthropic
        secrets_path = Path(__file__).parent / ".octo_secrets"
        raw = json.loads(secrets_path.read_text(encoding="utf-8"))
        api_key = raw.get("secrets", raw).get("ANTHROPIC_API_KEY", os.environ.get("ANTHROPIC_API_KEY", ""))
        if not api_key:
            return ""

        def _snap_line(snap: dict, label: str) -> str:
            parts = [f"{label}:"]
            if "fear_greed" in snap:
                parts.append(f"F&G={snap['fear_greed']} ({snap.get('fear_greed_label','')})")
            if "macro_signal" in snap:
                parts.append(f"macro={snap['macro_signal']} (score={snap.get('macro_score',0)})")
            if "funding_rate_pct" in snap:
                parts.append(f"funding={snap['funding_rate_pct']}%")
            if "open_interest_usd" in snap:
                oi = snap["open_interest_usd"]
                parts.append(f"OI=${oi/1e9:.2f}B" if oi and oi > 1e9 else f"OI=${oi:,.0f}")
            if "change_24h_pct" in snap:
                parts.append(f"24h_chg={snap['change_24h_pct']}%")
            return " | ".join(parts)

        call_snap  = call.get("market_snapshot", {})
        res_snap   = call.get("resolution_snapshot", {})
        pattern    = _get_pattern_context(call["asset"], call["direction"], call_snap)

        is_pm = call.get("call_type") == "polymarket"
        if is_pm:
            sections = [
                f"Prediction: {call.get('note', call['asset'])}",
                f"Side: {call.get('pm_side','?')} at {call['entry_price']}% implied probability",
                f"Outcome: {call['outcome']} (market resolved {call.get('resolution_snapshot',{}).get('resolution','?') or ('YES' if call.get('exit_price',0) == 100 else 'NO')})",
            ]
            if call.get("note"):
                sections.append(f"Question: {call['note'][:200]}")
        else:
            sections = [
                f"Oracle call: {call['asset']} {call['direction']} "
                f"from ${call['entry_price']:,.2f} target ${call.get('target_price','?')} "
                f"({call.get('timeframe','?')})",
                f"Outcome: {call['outcome']} | Exit: ${call.get('exit_price','?'):,.2f}",
                _snap_line(call_snap, "At call"),
            ]
            if res_snap:
                sections.append(_snap_line(res_snap, "At resolution"))
            if pattern:
                sections.append(f"Historical pattern:\n{pattern}")
            if call.get("note"):
                sections.append(f"Call note: {call['note'][:150]}")

        sections.append(
            "\nIn 2-3 sentences: what was the key factor in this outcome? "
            + ("What did the market price in correctly or incorrectly? What would improve the next similar call? " if is_pm else
               "Reference specific signals (funding, macro, F&G, OI) and whether conditions changed between call and resolution. "
               "If pattern history is provided, note whether this outcome was consistent with it. ")
            + "No hedging. No generic observations. Start with the specific prediction topic."
        )

        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": "\n".join(sections)}],
        )
        return msg.content[0].text.strip()
    except Exception as e:
        print(f"[OctoCalls] Post-mortem failed: {e}")
        return ""


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
    signals: Optional[dict] = None,   # #10 — signal breakdown for calibration
    edge_score: float = 0.0,          # #3 — (bulls - bears) / total_signals
    time_quality: str = "",           # #2 — "peak" | "offhours" | "weekend"
    market_snapshot: Optional[dict] = None,  # auto-fetched if not provided
) -> dict:
    calls = _load()

    # Prevent duplicate open calls on same asset
    for c in calls:
        if not c["resolved"] and c["asset"] == asset.upper():
            print(f"[OctoCalls] Skipped — already have open call on {asset.upper()} (#{c['id']})")
            return c

    # Auto-fetch market snapshot if not supplied
    if market_snapshot is None:
        try:
            market_snapshot = _fetch_market_snapshot(asset, entry_price)
        except Exception:
            market_snapshot = {"price": entry_price}

    call = {
        "id":                     len(calls) + 1,
        "call_type":              "oracle",
        "asset":                  asset.upper(),
        "direction":              direction.upper(),
        "entry_price":            entry_price,
        "target_price":           target_price,
        "timeframe":              timeframe,
        "note":                   note[:300],
        "made_at":                datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "resolved":               False,
        "outcome":                None,
        "won":                    None,
        "exit_price":             None,
        "resolved_at":            None,
        "resolution_price_source": "CoinGecko spot" if asset.upper() in ("BTC","ETH","SOL") else "yfinance",
        # Intelligence enrichment fields
        "signals":          signals or {},
        "edge_score":       round(edge_score, 3),
        "time_quality":     time_quality,
        "market_snapshot":  market_snapshot,
        "post_mortem":      None,  # filled in by autoresolve()
    }
    calls.append(call)
    _save(calls)
    print(f"[OctoCalls] #{call['id']} recorded: {asset.upper()} {direction.upper()} @ ${entry_price:,.2f} edge={edge_score:.2f}")
    try:
        from octo_notify import notify_call_placed
        notify_call_placed(asset.upper(), direction.upper(), entry_price,
                           target_price or 0, timeframe, edge_score, note)
    except Exception:
        pass
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
            entry = c["entry_price"]
            # WIN requires >=1% move in called direction.
            min_move = entry * 0.01
            if d == "UP":
                c["outcome"] = "WIN" if exit_price >= entry + min_move else "LOSS"
            elif d == "DOWN":
                c["outcome"] = "WIN" if exit_price <= entry - min_move else "LOSS"
            else:
                c["outcome"] = "PUSH"
            c["won"] = (c["outcome"] == "WIN")
            # Capture market conditions at resolution time for cross-referencing
            try:
                c["resolution_snapshot"] = _fetch_market_snapshot(c["asset"], exit_price)
            except Exception:
                pass
            _save(calls)
            print(f"[OctoCalls] #{call_id} resolved: {c['outcome']} (${c['entry_price']:,.2f} -> ${exit_price:,.2f})")
            try:
                from octo_notify import notify_call_resolved
                notify_call_resolved(c["asset"], c["direction"], c["outcome"],
                                     c["entry_price"], exit_price,
                                     c.get("note", ""))
            except Exception:
                pass
            return c
    print(f"[OctoCalls] Call #{call_id} not found or already resolved.")
    return None


# ── Auto-resolve from live prices ─────────────────────────────────────────────

def _fetch_price(asset: str) -> Optional[float]:
    """Fetch current price for an asset."""
    try:
        import requests
        asset = asset.upper()
        CRYPTO = {"BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "AVAX", "LINK", "UNI"}
        cg_map = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
                  "BNB": "binancecoin", "XRP": "ripple", "DOGE": "dogecoin",
                  "AVAX": "avalanche-2", "LINK": "chainlink", "UNI": "uniswap"}
        # Crypto: CoinGecko first
        if asset in cg_map:
            r = requests.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": cg_map[asset], "vs_currencies": "usd"},
                timeout=8
            )
            if r.status_code == 200:
                price = r.json().get(cg_map[asset], {}).get("usd")
                if price and float(price) > 1:   # sanity: ETH/BTC should never be <$1
                    return float(price)
        # Crypto fallback: yfinance with -USD suffix
        if asset in CRYPTO:
            import yfinance as yf
            t = yf.Ticker(f"{asset}-USD")
            price = t.fast_info.get("lastPrice") or t.info.get("regularMarketPrice")
            if price:
                return float(price)
        # Stocks via yfinance (bare ticker)
        if asset not in CRYPTO:
            import yfinance as yf
            t = yf.Ticker(asset)
            price = t.fast_info.get("lastPrice") or t.info.get("regularMarketPrice")
            if price:
                return float(price)
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
    """Check open calls against live prices. Resolve expired oracle calls only."""
    calls = _load()
    resolved = []
    for c in calls:
        if c["resolved"]:
            continue
        if c.get("call_type", "oracle") != "oracle":
            continue  # Polymarket calls resolve via Polymarket, not price feeds
        if not _is_expired(c):
            continue
        price = _fetch_price(c["asset"])
        if price is None:
            print(f"[OctoCalls] Could not fetch price for {c['asset']} — skipping #{c['id']}")
            continue
        result = resolve_call(c["id"], price)
        if result:
            # Generate post-mortem and save it back into the call record
            print(f"[OctoCalls] Generating post-mortem for #{result['id']} ({result['outcome']})...")
            pm = _generate_post_mortem(result)
            if pm:
                all_calls = _load()
                for call in all_calls:
                    if call["id"] == result["id"]:
                        call["post_mortem"] = pm
                        result["post_mortem"] = pm
                        break
                _save(all_calls)
                print(f"[OctoCalls] Post-mortem: {pm[:100]}")
            resolved.append(result)
    return resolved


# ── Stats ─────────────────────────────────────────────────────────────────────

def get_stats() -> dict:
    calls = _load()
    # Only count oracle calls in the win/loss record — not polymarket bridge trades
    oracle_calls = [c for c in calls if c.get("call_type", "oracle") == "oracle"]
    resolved = [c for c in oracle_calls if c["resolved"]]
    wins = sum(1 for c in resolved if c["outcome"] == "WIN")
    losses = sum(1 for c in resolved if c["outcome"] == "LOSS")
    open_calls = [c for c in oracle_calls if not c["resolved"]]

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
        "total": len(oracle_calls),
        "wins": wins,
        "losses": losses,
        "win_rate": rate,
        "streak": streak or "\u2014",
        "open": len(open_calls),
        "open_calls": open_calls,
        "all_calls": oracle_calls,
    }


# ── Guard functions (upgrades #2 #7 #8 #10) ──────────────────────────────────

def get_recent_win_rate(n: int = 5) -> Optional[float]:
    """
    Return win rate of the last N resolved oracle calls, or None if fewer than N exist.
    Used by the win-rate circuit breaker (#8).
    """
    calls = _load()
    oracle = [c for c in calls if c.get("call_type", "oracle") == "oracle"]
    resolved = [c for c in oracle if c["resolved"] and c.get("outcome") in ("WIN", "LOSS")]
    if len(resolved) < n:
        return None
    recent = resolved[-n:]
    wins = sum(1 for c in recent if c["outcome"] == "WIN")
    return wins / n


def get_direction_concentration() -> dict:
    """
    Return counts of open oracle calls by direction.
    Used to detect correlated multi-asset bets (#7).
    e.g. {"UP": 2, "DOWN": 1} means 2 assets already have open UP calls.
    """
    calls = _load()
    open_oracle = [
        c for c in calls
        if not c["resolved"] and c.get("call_type", "oracle") == "oracle"
    ]
    counts = {"UP": 0, "DOWN": 0}
    for c in open_oracle:
        d = c.get("direction", "").upper()
        if d in counts:
            counts[d] += 1
    return counts


def time_quality_score() -> str:
    """
    Return 'peak' | 'offhours' | 'weekend' based on current UTC time.
    Used to flag low-liquidity call windows (#2).

    Peak: Mon-Fri 13:00-21:00 UTC (US market hours, high crypto liquidity)
    Offhours: Mon-Fri outside that window
    Weekend: Saturday or Sunday
    """
    now = datetime.now(timezone.utc)
    weekday = now.weekday()  # 0=Mon, 6=Sun
    hour    = now.hour

    if weekday >= 5:
        return "weekend"
    if 13 <= hour <= 21:
        return "peak"
    return "offhours"


def get_signal_calibration() -> dict:
    """
    Compute per-signal win rates from historical calls that have signal breakdowns (#10).
    Returns dict of {signal_name: {n, win_rate}} for signals with >=3 observations.
    """
    calls = _load()
    resolved = [
        c for c in calls
        if c.get("call_type", "oracle") == "oracle"
        and c["resolved"]
        and c.get("outcome") in ("WIN", "LOSS")
        and c.get("signals")
    ]

    signal_records: dict = {}
    for c in resolved:
        won = c["outcome"] == "WIN"
        direction = c.get("direction", "UP")
        for sig_name, sig_dir in c["signals"].items():
            # A signal "contributed" correctly if it agreed with the call direction
            agreed = (sig_dir.upper() == direction.upper())
            key = sig_name
            if key not in signal_records:
                signal_records[key] = {"agree_wins": 0, "agree_total": 0,
                                       "disagree_wins": 0, "disagree_total": 0}
            if agreed:
                signal_records[key]["agree_total"] += 1
                if won:
                    signal_records[key]["agree_wins"] += 1
            else:
                signal_records[key]["disagree_total"] += 1
                if won:
                    signal_records[key]["disagree_wins"] += 1

    result = {}
    for sig, r in signal_records.items():
        if r["agree_total"] >= 3:
            result[sig] = {
                "agree_win_rate": round(r["agree_wins"] / r["agree_total"], 2),
                "agree_n":        r["agree_total"],
                "disagree_win_rate": round(r["disagree_wins"] / r["disagree_total"], 2) if r["disagree_total"] else None,
            }
    return result


def calibration_summary_str() -> str:
    """Format signal calibration for injection into runner context (#10)."""
    cal = get_signal_calibration()
    if not cal:
        return ""
    lines = ["Signal calibration (when signal agrees with call direction):"]
    sorted_sigs = sorted(cal.items(), key=lambda x: x[1]["agree_win_rate"], reverse=True)
    for sig, stats in sorted_sigs:
        lines.append(
            f"  {sig}: {stats['agree_win_rate']:.0%} win rate ({stats['agree_n']} calls)"
        )
    return "\n".join(lines)


# ── Prompt injection ──────────────────────────────────────────────────────────

def build_call_context() -> str:
    """Injected into every Claude prompt by the runner."""
    s = get_stats()
    lines = []
    lines.append("── DIRECTIONAL CALL SYSTEM ──")
    lines.append(f"Record: {s['wins']}W / {s['losses']}L | Win rate: {s['win_rate']} | Streak: {s['streak']}")

    # #8: Surface recent win rate for context
    recent_wr = get_recent_win_rate(n=5)
    if recent_wr is not None:
        lines.append(f"Last 5 calls win rate: {recent_wr:.0%}" +
                     (" ⚠ circuit breaker active" if recent_wr < 0.50 else ""))

    # #2: Time quality context
    tq = time_quality_score()
    if tq != "peak":
        lines.append(f"Current market window: {tq.upper()} — factor this into confidence.")

    # #7: Direction concentration
    dc = get_direction_concentration()
    if dc["UP"] >= 2:
        lines.append(f"WARNING: {dc['UP']} open UP calls — avoid adding more UP calls (correlated risk).")
    if dc["DOWN"] >= 2:
        lines.append(f"WARNING: {dc['DOWN']} open DOWN calls — avoid adding more DOWN calls (correlated risk).")

    if s["open_calls"]:
        lines.append("Open calls (do NOT call these assets again):")
        for c in s["open_calls"]:
            t = f" target ${c['target_price']:,.0f}" if c.get("target_price") else ""
            eq = f" edge={c.get('edge_score', 0):+.2f}" if c.get("edge_score") else ""
            lines.append(f"  #{c['id']} {c['asset']} {c['direction']} @ ${c['entry_price']:,.2f}{t} [{c['timeframe']}]{eq}")

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

    # #10: Signal calibration — show which signals have proven predictive
    cal_str = calibration_summary_str()
    if cal_str:
        lines.append("")
        lines.append(cal_str)
        lines.append("Prefer directions where the highest-accuracy signals agree.")

    # Post-mortem learning: inject recent loss and win patterns
    calls = _load()
    oracle_resolved = [
        c for c in calls
        if c.get("call_type", "oracle") == "oracle"
        and c.get("resolved")
        and c.get("post_mortem")
    ]
    recent_losses = [c for c in reversed(oracle_resolved) if c.get("outcome") == "LOSS"][:3]
    recent_wins   = [c for c in reversed(oracle_resolved) if c.get("outcome") == "WIN"][:1]

    if recent_losses:
        lines.append("")
        lines.append("RECENT LOSS PATTERNS (learn from these — do not repeat):")
        for c in recent_losses:
            snap = c.get("market_snapshot", {})
            fng = snap.get("fear_greed", "?")
            chg = snap.get("change_24h_pct", "?")
            lines.append(
                f"  #{c['id']} {c['asset']} {c['direction']} "
                f"(F&G={fng}, 24h={chg}%): {c['post_mortem']}"
            )

    if recent_wins:
        c = recent_wins[0]
        if c.get("post_mortem"):
            lines.append("")
            lines.append(f"RECENT WIN PATTERN: #{c['id']} {c['asset']} {c['direction']}: {c['post_mortem']}")

    return "\n".join(lines)


def build_open_calls_awareness() -> str:
    """Lightweight context for mode_daily: shows open calls so the model knows
    what's already on record, WITHOUT the call rules or 'MUST make a call' pressure."""
    s = get_stats()
    if not s["open_calls"]:
        return ""
    lines = ["-- OPEN ORACLE CALLS (awareness only) --"]
    lines.append("These calls are already on record. Do NOT make new directional calls on these assets:")
    for c in s["open_calls"]:
        t = f" -> ${c['target_price']:,.0f}" if c.get("target_price") else ""
        lines.append(f"  #{c['id']} {c['asset']} {c['direction']} @ ${c['entry_price']:,.2f}{t} [{c['timeframe']}]")
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

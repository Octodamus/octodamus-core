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

# CoinGecko id map + demo-key headers, shared by price fetch and target-hit detection.
# Without the demo key these calls run the keyless free tier, which 429s on the shared IP and
# silently starves autoresolve (returns None -> calls never resolve). See octo_gecko._headers().
_CG_IDS = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "BNB": "binancecoin",
    "XRP": "ripple", "DOGE": "dogecoin", "AVAX": "avalanche-2", "LINK": "chainlink",
    "UNI": "uniswap", "ADA": "cardano", "SUI": "sui",
}

# Adopt whatever ids the funding-extreme scanner uses, so an asset it can fire on
# is always resolvable here. SUI was missing from this map, so resolution skipped
# CoinGecko entirely and fell through to a yfinance ticker that does not exist.
try:
    from octo_funding_extreme import _CG_ID as _FE_CG_ID
    for _k, _v in _FE_CG_ID.items():
        _CG_IDS.setdefault(_k.upper(), _v)
except Exception:
    pass


def _cg_headers() -> dict:
    h = {"User-Agent": "octodamus-oracle/1.0 (@octodamusai)"}
    key = os.environ.get("COINGECKO_API_KEY", "")
    if key:
        h["x-cg-demo-api-key"] = key
    return h


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

    # Funding rate (CoinGlass V4). The exchange-list endpoint returns a list of
    # per-coin rows, each with a stablecoin_margin_list -- the old parse here did
    # fr.get("data") on that list, raised, and was swallowed, so no call ever had a
    # funding snapshot and the post-mortem pattern context ran blind.
    if asset.upper() in ("BTC", "ETH", "SOL"):
        try:
            from octo_funding_extreme import _fetch_funding
            fd = _fetch_funding(asset.upper())
            if fd.get("ok"):
                snap["funding_rate_pct"] = round(fd["avg"] * 100, 4)
        except Exception:
            pass
        try:
            import octo_coinglass as glass
            oi = glass.open_interest(asset.upper(), interval="4h")
            rows = oi.get("data", {}).get("list", []) if isinstance(oi, dict) else oi
            if rows and isinstance(rows, list) and isinstance(rows[-1], dict):
                snap["open_interest_usd"] = rows[-1].get("openInterest") or rows[-1].get("open_interest")
        except Exception:
            pass

    # Trend regime at call time -- so post-mortems can say whether the call
    # fought the trend, and the learning loop can see it.
    try:
        from octo_regime import get_regime
        rg = get_regime(asset)
        if rg:
            snap["trend_bias"] = rg["bias"]
            snap["chg_7d_pct"] = rg["chg_7d"]
            snap["vs_sma20_pct"] = rg["vs_sma20_pct"]
    except Exception:
        pass

    snap["captured_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return snap


def _normalize_snapshot(snap: Optional[dict]) -> dict:
    """
    The strategy modules build their own snapshot with short keys ("fng",
    "chg_24h") while everything that READS a snapshot -- _get_pattern_context,
    the post-mortem, build_call_context -- expects the long keys this module
    writes ("fear_greed", "change_24h_pct"). Every strategy call therefore showed
    up as F&G=None in its own post-mortem. Map the short keys onto the long ones
    without dropping anything.
    """
    snap = dict(snap or {})
    aliases = {"fng": "fear_greed", "chg_24h": "change_24h_pct", "change_24h": "change_24h_pct"}
    for short, long in aliases.items():
        if short in snap and long not in snap and snap[short] is not None:
            snap[long] = snap[short]
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
    # Every on-chain strategy counts: a range_scout ETH DOWN loss is exactly the
    # history a funding_extreme ETH DOWN post-mortem needs to see.
    resolved = [
        c for c in calls
        if c.get("resolved") and c.get("tx_hash")
        and c.get("call_type", "oracle") != "polymarket"
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
            if call_snap.get("trend_bias"):
                sections.append(
                    f"Trend at call: bias {call_snap['trend_bias']} "
                    f"(7d {call_snap.get('chg_7d_pct', 0):+.1f}%, {call_snap.get('vs_sma20_pct', 0):+.1f}% vs 20d avg)"
                )
            if pattern:
                sections.append(f"Historical pattern:\n{pattern}")
            if call.get("note"):
                sections.append(f"Call note: {call['note'][:150]}")

        sections.append(
            "\nIn 2-3 sentences: what was the key factor in this outcome? "
            + ("What did the market price in correctly or incorrectly? What would improve the next similar call? " if is_pm else
               "Reference specific signals (funding, macro, F&G, OI) and whether conditions changed between call and resolution. "
               "If pattern history is provided, note whether this outcome was consistent with it. ")
            + "No hedging. No generic observations. Start with the specific prediction topic. "
            "Plain prose only: no markdown headers, no bold, no bullet points -- this text is "
            "injected verbatim into prompts and posts."
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


# ── Call policy ───────────────────────────────────────────────────────────────
#
# Two rules every call must pass before it is written anywhere, derived from
# re-scoring the real on-chain record (32 resolved crypto calls, Sep 2026):
#
#   1. Minimum 24h horizon. The WIN rule needs a >=1% move in the called
#      direction at expiry (or the target touched). Re-simulating every call at
#      each horizon under that exact rule: 6h 1W-31L, 12h 6W-26L, 24h 12W-20L,
#      48h 15W-17L. Sub-day calls cannot clear the bar often enough to be worth
#      a permanent on-chain entry. range_scout's 6h book was 1W-8L.
#
#   2. Never fight the trend (octo_regime.trend_gate). Trend-opposed calls were
#      0W-7L; DOWN calls outside a confirmed downtrend were 2W-13L.
#
# Enforced in record_call() AND commit_call_onchain(), so no strategy, LLM
# post, or CLI path can bypass it.
MIN_CALL_HOURS = 24


def _timeframe_hours(tf: str) -> Optional[float]:
    """Hours in a timeframe string, or None if unparseable (treated as the 48h default)."""
    tf = (tf or "").lower().strip()
    m = re.search(r"(\d+)\s*([hd])", tf)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        return n if unit == "h" else n * 24
    if any(k in tf for k in ("friday", "end of week", "eow", "monday", "tuesday", "wednesday", "thursday", "close")):
        return 24.0  # weekday expiries are always >= 1 trading day out
    return None


def call_policy_check(asset: str, direction: str, timeframe: str) -> tuple[bool, str]:
    """(ok, reason). Fails closed: no trend data means no call."""
    hours = _timeframe_hours(timeframe)
    if hours is not None and hours < MIN_CALL_HOURS:
        return (False, f"timeframe {timeframe!r} is under the {MIN_CALL_HOURS}h minimum (sub-day calls: 1W-31L re-simulated)")
    try:
        from octo_regime import trend_gate
        ok, why = trend_gate(asset, direction)
    except Exception as e:
        return (False, f"trend gate unavailable ({e}) -- refusing to call blind")
    return (ok, why)


def _reject_call(label: str, reason: str) -> None:
    msg = f"{label} REJECTED by call policy: {reason}"
    print(f"[OctoCalls] {msg}")
    try:
        from octo_notify import _send
        _send("Octodamus call rejected (policy)", msg)
    except Exception:
        pass


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
            if c["direction"] != direction.upper():
                # Contradiction: a new call reverses an open call on the same asset.
                # Don't silently drop it -- surface it so the conflict is visible.
                msg = (f"CONTRADICTION -- new {asset.upper()} {direction.upper()} call "
                       f"reverses open #{c['id']} ({c['direction']}). Keeping #{c['id']}, dropping new.")
                print(f"[OctoCalls] {msg}")
                try:
                    from octo_notify import _send
                    _send("Octodamus oracle call CONTRADICTION", msg)
                except Exception:
                    pass
            else:
                print(f"[OctoCalls] Skipped -- already have open {direction.upper()} call on {asset.upper()} (#{c['id']})")
            return c

    ok, why = call_policy_check(asset, direction, timeframe)
    if not ok:
        _reject_call(f"{asset.upper()} {direction.upper()} [{timeframe}]", why)
        return None

    # Auto-fetch market snapshot if not supplied
    if market_snapshot is None:
        try:
            market_snapshot = _fetch_market_snapshot(asset, entry_price)
        except Exception:
            market_snapshot = {"price": entry_price}
    market_snapshot = _normalize_snapshot(market_snapshot)

    call = {
        "id":                     max((c.get("id", 0) for c in calls), default=0) + 1,
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
    # Publish prediction hash on-chain (non-blocking -- fails silently if contract not deployed)
    try:
        from octo_oracle_registry import publish_prediction
        tx = publish_prediction(call)
        if tx:
            all_calls = _load()
            for c in all_calls:
                if c["id"] == call["id"]:
                    c["tx_hash"] = tx
                    call["tx_hash"] = tx
                    break
            _save(all_calls)
    except Exception as _oc_err:
        print(f"[OctoCalls] On-chain publish skipped: {_oc_err}")
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
            # Record outcome on-chain (non-blocking)
            try:
                from octo_oracle_registry import resolve_prediction
                res_tx = resolve_prediction(c["id"], c["won"], exit_price)
                if res_tx:
                    c["resolve_tx_hash"] = res_tx
                    _save(calls)
            except Exception as _oc_err:
                print(f"[OctoCalls] On-chain resolve skipped: {_oc_err}")
            return c
    print(f"[OctoCalls] Call #{call_id} not found or already resolved.")
    return None


# ── Auto-resolve from live prices ─────────────────────────────────────────────

def _fetch_price(asset: str) -> Optional[float]:
    """Fetch current price for an asset."""
    try:
        import requests, time as _t
        asset = asset.upper()
        CRYPTO = _CRYPTO_ASSETS
        # Crypto: CoinGecko first (demo key + brief retry so a transient 429/timeout on the
        # shared IP doesn't return None and leave the call unresolved -- the root cause of the
        # Aug-18 calls sitting open for 11 days).
        if asset in _CG_IDS:
            for attempt in range(3):
                try:
                    r = requests.get(
                        "https://api.coingecko.com/api/v3/simple/price",
                        params={"ids": _CG_IDS[asset], "vs_currencies": "usd"},
                        headers=_cg_headers(), timeout=10,
                    )
                    if r.status_code == 200:
                        price = r.json().get(_CG_IDS[asset], {}).get("usd")
                        # Guard against a zero/null quote only. This used to require
                        # price > 1 "because ETH/BTC are never under $1", which silently
                        # rejected every sub-dollar token: SUI at $0.81 was thrown away,
                        # fell through to a yfinance ticker that does not exist, and
                        # resolved a call at $0.0003 -- publishing a WIN on-chain for
                        # what was a LOSS. Sub-dollar assets are normal; bad data is
                        # caught by _sane_exit_price() at the resolve site instead.
                        if price and float(price) > 0:
                            return float(price)
                    elif r.status_code == 429 and attempt < 2:
                        _t.sleep(2 * (attempt + 1)); continue
                    break
                except requests.exceptions.RequestException:
                    if attempt < 2:
                        _t.sleep(2 * (attempt + 1)); continue
                    break
        # Crypto fallback: yfinance with -USD suffix
        if asset in CRYPTO:
            import yfinance as yf
            t = yf.Ticker(f"{asset}-USD")
            price = t.fast_info.get("lastPrice") or t.info.get("regularMarketPrice")
            if price:
                return float(price)
        # Stocks: Robinhood Chain tokenized feed first (live 24/7, halt-aware),
        # yfinance fallback for tickers not tokenized on Robinhood Chain.
        if asset not in CRYPTO:
            try:
                from octo_robinhood import get_mid
                rh = get_mid(asset)
                if rh and rh > 0:
                    return rh
            except Exception:
                pass
            import yfinance as yf
            t = yf.Ticker(asset)
            price = t.fast_info.get("lastPrice") or t.info.get("regularMarketPrice")
            if price:
                return float(price)
    except Exception as e:
        print(f"[OctoCalls] Price fetch failed for {asset}: {e}")
    return None


def _expiry_dt(call: dict) -> Optional[datetime]:
    """The datetime at which a call's timeframe closes, or None if made_at is unparseable.
    Single source of truth for both expiry checks and the target-hit window bound."""
    made = call.get("made_at", "")
    tf = call.get("timeframe", "24h").lower().strip()
    try:
        made_dt = datetime.strptime(made, "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)
    except Exception:
        return None

    if "h" in tf and "d" not in tf:
        hours = int(re.search(r"(\d+)", tf).group(1)) if re.search(r"(\d+)", tf) else 24
        return made_dt + timedelta(hours=hours)
    elif tf.endswith("d") and re.match(r"^\d+d$", tf):
        days = int(re.search(r"(\d+)", tf).group(1))
        return made_dt + timedelta(days=days)
    elif "friday" in tf or "end of week" in tf or "eow" in tf:
        days_until_sat = (5 - made_dt.weekday()) % 7 or 7
        return made_dt.replace(hour=21, minute=0) + timedelta(days=days_until_sat)
    elif "wednesday" in tf or "thursday" in tf or "tuesday" in tf or "monday" in tf:
        day_map = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3}
        target_day = day_map.get(tf.split()[0], made_dt.weekday())
        days_until = (target_day - made_dt.weekday()) % 7 or 7
        return made_dt.replace(hour=21, minute=0) + timedelta(days=days_until)
    else:
        # Default: 48h expiry
        return made_dt + timedelta(hours=48)


def _is_expired(call: dict) -> bool:
    """Check if a call has exceeded its timeframe."""
    exp = _expiry_dt(call)
    return exp is not None and datetime.now(timezone.utc) > exp


def _target_hit_during_window(call: dict) -> Optional[float]:
    """If a crypto call's target was TOUCHED at any point during [made_at, expiry], return the
    target price; else None. This fixes the point-in-time blind spot: a call that spiked to its
    target intraday then retraced is a WIN, but autoresolve's single current-price check misses it.

    Crypto only (CoinGecko historical range). Returns None for stocks, missing target, unparseable
    dates, or any fetch failure -- callers then fall back to the current-price settle path.
    """
    asset = call.get("asset", "").upper()
    tgt = call.get("target_price")
    direction = call.get("direction", "").upper()
    if asset not in _CG_IDS or not tgt or direction not in ("UP", "DOWN"):
        return None
    try:
        made_dt = datetime.strptime(call.get("made_at", ""), "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)
    except Exception:
        return None
    end_dt = _expiry_dt(call)
    if not end_dt:
        return None
    try:
        import requests
        r = requests.get(
            f"https://api.coingecko.com/api/v3/coins/{_CG_IDS[asset]}/market_chart/range",
            params={"vs_currency": "usd", "from": int(made_dt.timestamp()), "to": int(end_dt.timestamp())},
            headers=_cg_headers(), timeout=15,
        )
        if r.status_code != 200:
            return None
        prices = [p[1] for p in (r.json().get("prices") or []) if p and p[1]]
        if not prices:
            return None
    except Exception:
        return None
    tgt = float(tgt)
    if direction == "UP" and max(prices) >= tgt:
        return tgt
    if direction == "DOWN" and min(prices) <= tgt:
        return tgt
    return None


_CRYPTO_ASSETS = {"BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "AVAX", "LINK", "UNI", "ADA", "SUI"}

# Pull in whatever the funding-extreme scanner can actually emit, so the two
# lists cannot drift apart. They had: the scanner fired on SUI, this set did not
# contain it, and autoresolve then treated SUI as a stock and deferred it
# forever waiting on US market hours that are meaningless for a token.
try:
    from octo_funding_extreme import _ASSETS as _FE_ASSETS
    _CRYPTO_ASSETS |= {a.upper() for a in _FE_ASSETS}
except Exception:
    pass

# Call types that do NOT settle against price feeds. Polymarket resolves via its
# own market outcome. Everything else settles on price -- keep this a denylist so
# a new strategy resolves by default instead of silently never resolving.
_NO_PRICE_RESOLVE = {"polymarket"}


def _sane_exit_price(call: dict, price: float, max_move: float = 0.60) -> bool:
    """
    Reject an exit price that cannot plausibly belong to this asset.

    A resolution is written to Base and CANNOT be undone -- the contract reverts
    with "already resolved". So a bad price feed does not merely produce a wrong
    row, it produces a permanently wrong public record. Call #53 resolved WIN at
    $0.0003 on a $0.80 asset because a dead yfinance ticker answered; this is the
    backstop for that class of failure.

    Anything further than max_move from entry in either direction is treated as a
    feed fault, not a real move, and the call is left open for a human to look at.
    """
    try:
        entry = float(call.get("entry_price") or 0)
        price = float(price)
    except (TypeError, ValueError):
        return False
    if entry <= 0 or price <= 0:
        return False
    return abs(price - entry) / entry <= max_move


def _is_us_market_open() -> bool:
    """Return True only during US equity trading hours (Mon-Fri 13:30-20:00 UTC)."""
    now = datetime.now(timezone.utc)
    if now.weekday() >= 5:
        return False
    market_minute = now.hour * 60 + now.minute
    return 13 * 60 + 30 <= market_minute <= 20 * 60


def autoresolve() -> list:
    """Check open calls against live prices. Resolve expired oracle, range_scout, and crowd_fade calls."""
    calls = _load()
    resolved = []
    for c in calls:
        if c["resolved"]:
            continue
        call_type = c.get("call_type", "oracle")
        # Denylist, not allowlist. Only Polymarket resolves elsewhere (via its own
        # market outcome); everything else settles against price feeds. This was an
        # allowlist and every strategy added after it -- funding_extreme,
        # stock_extreme -- silently never resolved, leaving on-chain calls open
        # forever and quietly flattering the record by omitting the losses.
        if call_type in _NO_PRICE_RESOLVE:
            continue
        if not _is_expired(c):
            continue
        asset = c["asset"].upper()
        # Target-hit takes precedence: a crypto call whose target was touched anywhere in its
        # window is a WIN, even if price later retraced below the target by the time we check.
        # resolve at the target price (which is by construction a >=1% move, so resolve_call
        # marks it WIN). None -> target not hit / not crypto / no historical data -> settle below.
        price = _target_hit_during_window(c)
        if price is None:
            # Stock tickers off-hours: resolve against Robinhood Chain's live 24/7 tokenized
            # price when available (halt-aware); otherwise defer to avoid stale yfinance closes.
            if asset not in _CRYPTO_ASSETS and not _is_us_market_open():
                try:
                    from octo_robinhood import get_mid as _rh_mid
                    _has_live = _rh_mid(asset) is not None
                except Exception:
                    _has_live = False
                if not _has_live:
                    print(f"[OctoCalls] {asset} is a stock with no live Robinhood price -- deferring resolve until US market hours (Mon-Fri 13:30-20:00 UTC)")
                    continue
            price = _fetch_price(c["asset"])
            if price is None:
                print(f"[OctoCalls] Could not fetch price for {c['asset']} — skipping #{c['id']}")
                continue
            if not _sane_exit_price(c, price):
                print(f"[OctoCalls] #{c['id']} {asset}: exit ${price:.6g} is implausible vs "
                      f"entry ${c.get('entry_price')} -- REFUSING to resolve. Fix the price "
                      f"feed; an on-chain resolution cannot be undone.")
                continue
        else:
            print(f"[OctoCalls] #{c['id']} {asset} {c['direction']} target touched in-window -- resolving WIN at target ${price:,.2f}")
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
    # Official record: on-chain verified calls only (tx_hash present)
    oracle_calls = [c for c in calls if c.get("tx_hash")]
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


def asset_direction_loss_streak(asset: str, direction: str) -> int:
    """Count consecutive most-recent LOSSES for a specific (asset, direction) setup.
    e.g. 8 = the last 8 resolved ETH DOWN calls all lost. Used to block repeating a
    proven-losing setup and to feed the performance-feedback block."""
    calls = _load()
    resolved = [
        c for c in calls
        if c.get("tx_hash") and c.get("call_type", "oracle") != "polymarket"
        and c.get("resolved") and c.get("outcome") in ("WIN", "LOSS")
        and c.get("asset", "").upper() == asset.upper()
        and c.get("direction", "").upper() == direction.upper()
    ]
    resolved.sort(key=lambda c: c.get("made_at", ""))
    streak = 0
    for c in reversed(resolved):
        if c["outcome"] == "LOSS":
            streak += 1
        else:
            break
    return streak


def strategy_should_pause(call_type: str, min_resolved: int = 6,
                          max_win_rate: float = 0.30, loss_streak_trip: int = 5,
                          stale_days: int = 14) -> tuple[bool, str]:
    """Auto-pause a systematic strategy that is deeply underwater ON-CHAIN so it stops repeating
    losing calls. This is the fleet-wide learning loop: contrarian strategies (range_scout,
    crowd_fade) fight a trend and bleed; a strategy at 1W-8L should not keep firing.

    Pauses when (>= min_resolved resolved calls AND win rate <= max_win_rate) OR
    (loss_streak_trip consecutive losses). Only blockchain-verified calls (tx_hash) count.

    Escape valve: if the strategy has been dormant >= stale_days (no call at all in that window),
    it is allowed ONE probe call to re-test the regime -- otherwise a paused strategy could never
    recover its record. A losing probe re-trips the pause for another cooldown.
    Returns (should_pause, reason).
    """
    calls = _load()
    strat_all = [c for c in calls if c.get("call_type") == call_type]
    resolved = [
        c for c in strat_all if c.get("tx_hash")
        and c.get("resolved") and c.get("outcome") in ("WIN", "LOSS")
    ]
    if len(resolved) < min_resolved:
        return (False, "")
    resolved.sort(key=lambda c: c.get("made_at", ""))
    w = sum(1 for c in resolved if c["outcome"] == "WIN")
    wr = w / len(resolved)
    streak = 0
    for c in reversed(resolved):
        if c["outcome"] == "LOSS":
            streak += 1
        else:
            break

    tripped = ""
    if wr <= max_win_rate:
        tripped = f"{call_type} at {wr:.0%} win ({w}W-{len(resolved)-w}L on-chain) -- below {max_win_rate:.0%} floor"
    elif streak >= loss_streak_trip:
        tripped = f"{call_type} lost the last {streak} calls in a row on-chain"
    if not tripped:
        return (False, "")

    # Dormant-probe escape: allow one call through after a quiet stretch to re-test the regime.
    last = max(strat_all, key=lambda c: c.get("made_at", ""), default=None)
    days_since = 9999
    if last and last.get("made_at"):
        try:
            ldt = datetime.strptime(last["made_at"][:16], "%Y-%m-%d %H:%M")
            days_since = (datetime.now(timezone.utc).replace(tzinfo=None) - ldt).days
        except Exception:
            pass
    if days_since >= stale_days:
        return (False, f"{call_type} underwater but dormant {days_since}d -- allowing one probe call")
    return (True, tripped)


def build_performance_feedback() -> str:
    """Blunt, data-driven feedback the oracle MUST see before making a new call, so it stops
    repeating losing setups. Surfaces overall record, directional bias, and specific underwater
    asset+direction patterns with active loss streaks. This is the learning loop -- without it
    the model never sees that (e.g.) ETH DOWN is 3W-12L and keeps re-issuing the losing call."""
    calls = _load()
    # The whole on-chain book, every strategy. The oracle's public record is the
    # blended number, so the feedback has to be about the blended number.
    resolved = [
        c for c in calls
        if c.get("tx_hash") and c.get("call_type", "oracle") != "polymarket"
        and c.get("resolved") and c.get("outcome") in ("WIN", "LOSS")
    ]
    if len(resolved) < 5:
        return ""
    w = sum(1 for c in resolved if c["outcome"] == "WIN")
    l = len(resolved) - w
    lines = ["PERFORMANCE FEEDBACK -- learn from your own scored record before calling:"]
    lines.append(f"  Overall: {w}W-{l}L ({w/(w+l)*100:.0f}% win). You are being graded on-chain; a call is only worth making if the data genuinely supports it.")
    by = {}
    for c in resolved:
        by.setdefault(c.get("call_type", "oracle"), [0, 0])[0 if c["outcome"] == "WIN" else 1] += 1
    lines.append("  By strategy: " + ", ".join(f"{k} {ww}W-{ll}L" for k, (ww, ll) in sorted(by.items(), key=lambda x: -(x[1][0] + x[1][1]))))
    lines.append("  Standing rule (from re-scoring this record): never call against the 7d/20d trend. Trend-opposed calls went 0W-7L; DOWN outside a confirmed downtrend went 2W-13L.")

    # Directional bias -- flag the losing side hard.
    for d in ("UP", "DOWN"):
        dc = [c for c in resolved if c.get("direction", "").upper() == d]
        if len(dc) >= 4:
            dw = sum(1 for c in dc if c["outcome"] == "WIN")
            dl = len(dc) - dw
            wr = dw / len(dc)
            if wr < 0.40:
                lines.append(f"  {d} calls are {dw}W-{dl}L ({wr*100:.0f}%) -- you are losing badly on {d} calls. Do NOT issue a {d} call unless the evidence is strong and specific; you have been fighting the trend.")

    # Per asset+direction underwater setups with active loss streaks.
    from collections import defaultdict
    ad = defaultdict(lambda: [0, 0])
    for c in resolved:
        key = (c.get("asset", "").upper(), c.get("direction", "").upper())
        ad[key][0 if c["outcome"] == "WIN" else 1] += 1
    flagged = []
    for (a, d), (ww, ll) in ad.items():
        # Flag a setup that is genuinely underwater, not a coin flip: 3W-12L and
        # 1W-5L qualify, 3W-4L does not.
        if ll >= 4 and ll >= 2 * ww:
            streak = asset_direction_loss_streak(a, d)
            note = f"  {a} {d}: {ww}W-{ll}L"
            if streak >= 3:
                note += f" and the last {streak} in a row ALL LOST"
            note += f" -- STOP repeating this. Do not call {a} {d} again unless a NEW, concrete catalyst has appeared since the last loss."
            flagged.append((streak, ll, note))
    for _, _, note in sorted(flagged, reverse=True):
        lines.append(note)

    return "\n".join(lines)


def _call_alert(msg: str) -> None:
    """Loud, best-effort alert when a call fails to anchor on-chain (or fails to post)."""
    print(f"[Calls] ALERT: {msg}")
    try:
        from octo_notify import notify_system_error
        notify_system_error("call-pipeline", msg)
    except Exception:
        pass


def commit_call_onchain(call: dict, post_fn=None) -> Optional[str]:
    """Guarantee: a call is POSTED only if it is ON-CHAIN.

    Records the call, publishes it to the oracle registry (one retry). On success it saves the
    tx_hash and runs post_fn() (the X post). On on-chain failure it ROLLS BACK the JSON record
    and alerts -- so nothing un-anchored is ever posted, and no orphan call pollutes the record.
    Returns the tx_hash on success, or None (call was neither posted nor kept).
    """
    from octo_oracle_registry import publish_prediction
    label = f"{call.get('call_type','?')} {call.get('asset','?')} {call.get('direction','?')} [{call.get('timeframe','?')}]"
    ok, why = call_policy_check(call.get("asset", ""), call.get("direction", ""), call.get("timeframe", ""))
    if not ok:
        _reject_call(label, why)
        return None
    call["market_snapshot"] = _normalize_snapshot(call.get("market_snapshot"))

    calls = _load()
    if not call.get("id"):
        call["id"] = max((c.get("id", 0) for c in calls), default=0) + 1
    calls.append(call)
    _save(calls)

    tx = None
    for attempt in range(2):
        try:
            tx = publish_prediction(call)
        except Exception as e:
            tx = None
            print(f"[Calls] publish_prediction raised (attempt {attempt+1}): {e}")
        if tx:
            break
        if attempt == 0:
            import time as _t
            _t.sleep(3)

    if not tx:
        # Roll back the un-anchored call; do NOT post.
        _save([c for c in _load() if c.get("id") != call.get("id")])
        _call_alert(f"{label} NOT posted -- on-chain publish failed (check registry wallet gas / Base RPC). Call rolled back.")
        return None

    # Persist tx_hash, then post ONLY after on-chain success.
    allc = _load()
    for c in allc:
        if c.get("id") == call.get("id"):
            c["tx_hash"] = tx
            # hash_version is stamped by publish_prediction and decides how the
            # content hash is recomputed. Lose it and the call is unverifiable.
            c["hash_version"] = call.get("hash_version", 1)
            break
    _save(allc)
    call["tx_hash"] = tx

    if post_fn:
        try:
            post_fn()
        except Exception as e:
            # On-chain is the source of truth; a failed X post is alert-worthy but the call stands.
            _call_alert(f"{label} on-chain OK ({tx[:10]}) but X post FAILED: {e}")
    return tx


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

def build_call_rules() -> str:
    """
    The "you MUST make exactly one Oracle call" instruction block, with the
    format the parser understands. ONLY for a mode that actually runs
    parse_call_from_post() on its output. No scheduled mode does today: the
    calls come from the 13-signal engine and the strategy modules, and every
    LLM post mode hard-blocks stray "Oracle call:" text in octo_x_poster.

    This used to be part of build_call_context(), which wisdom / moonshot /
    format / morning_flow / thread all injected -- while telling the model in
    the same prompt not to write an Oracle call. The model obeyed the louder
    instruction often enough that 12 posts in Aug-Sep were generated, paid for,
    and then blocked by the poster.
    """
    lines = ["CALL RULES -- your win rate IS your reputation:"]
    lines.append("1. Make exactly one Oracle call in this post, or none if the data does not support one.")
    lines.append(f"2. Never call against the trend: DOWN only in a confirmed downtrend (7d down AND below the 20d average). Trend-opposed calls are 0W-7L on record.")
    lines.append(f"3. Minimum horizon {MIN_CALL_HOURS}h. Use 24h, 48h, or end of week. Never longer than 7 days.")
    lines.append("4. Use realistic targets: 2-5% crypto, 1-3% stocks. No moonshots.")
    lines.append("5. FORMAT -- put this as the LAST LINE of your post, exactly like this:")
    lines.append("   Oracle call: ASSET UP from $PRICE to $TARGET by TIMEFRAME.")
    lines.append("   Oracle call: ASSET DOWN from $PRICE to $TARGET by TIMEFRAME.")
    lines.append("   e.g. Oracle call: BTC UP from $70000 to $73500 by 48h.")
    return "\n".join(lines)


def build_call_context() -> str:
    """
    Record + open-calls awareness for any prompt. Deliberately contains NO
    instruction to make a call -- see build_call_rules() for why. What a post
    mode needs from this block is: what the scored record is, what is already
    open (so it is not contradicted), and what the last post-mortems taught.
    """
    s = get_stats()
    lines = []
    lines.append("-- ORACLE CALL RECORD (context only; do NOT write 'Oracle call:' -- calls are issued by the signal engine) --")
    lines.append(f"On-chain record: {s['wins']}W / {s['losses']}L | Win rate: {s['win_rate']} | Streak: {s['streak']}")

    # Per-strategy record: the honest picture is by strategy, not the blended number.
    try:
        by = {}
        for c in s["all_calls"]:
            if c.get("resolved") and c.get("outcome") in ("WIN", "LOSS"):
                k = c.get("call_type", "oracle")
                by.setdefault(k, [0, 0])[0 if c["outcome"] == "WIN" else 1] += 1
        if by:
            lines.append("By strategy: " + ", ".join(f"{k} {w}W-{l}L" for k, (w, l) in sorted(by.items(), key=lambda x: -(x[1][0] + x[1][1]))))
    except Exception:
        pass

    tq = time_quality_score()
    if tq != "peak":
        lines.append(f"Current market window: {tq.upper()}.")

    if s["open_calls"]:
        lines.append("Open calls (do not contradict these):")
        for c in s["open_calls"]:
            t = f" target ${c['target_price']:,.0f}" if c.get("target_price") else ""
            lines.append(f"  #{c['id']} {c.get('call_type','oracle')} {c['asset']} {c['direction']} @ ${c['entry_price']:,.2f}{t} [{c['timeframe']}]")

    # Post-mortem learning: the last two losses and the last win, across every
    # on-chain strategy. Kept short -- this block rides on 6+ prompts a day.
    calls = _load()
    with_pm = [c for c in calls if c.get("tx_hash") and c.get("resolved") and c.get("post_mortem")]
    recent_losses = [c for c in reversed(with_pm) if c.get("outcome") == "LOSS"][:2]
    recent_wins   = [c for c in reversed(with_pm) if c.get("outcome") == "WIN"][:1]
    if recent_losses:
        lines.append("Recent loss lessons:")
        for c in recent_losses:
            lines.append(f"  #{c['id']} {c['asset']} {c['direction']}: {c['post_mortem'][:220]}")
    if recent_wins:
        c = recent_wins[0]
        lines.append(f"Recent win: #{c['id']} {c['asset']} {c['direction']}: {c['post_mortem'][:220]}")

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

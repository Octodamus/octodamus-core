"""
octo_distro.py -- Octo Distro Media Engine

Ten free market intelligence tools that power Octodamus distribution.
Every tool generates real value. Some gate on email. All build the subscriber list.

Tools:
  1. oracle_scorecard()               -- Track record transparency card (public)
  2. macro_pulse()                    -- 5-factor FRED macro score (public)
  3. signal_composite(asset)          -- Composite signal for BTC/ETH/SOL (gated)
  4. funding_extremes()               -- Extreme funding rate readings (gated)
  5. cme_positioning()                -- CFTC hedge fund net positioning (gated)
  6. polymarket_edges()               -- Markets where crowd is likely wrong (gated)
  7. liquidation_radar(asset)         -- Leveraged position clusters (public)
  8. travel_signal()                  -- TSA + aviation macro indicator (public)
  9. oracle_simulator(asset, ...)     -- Historical call backtest (gated)
 10. intel_digest()                   -- Aggregated weekly signal summary (gated)

Subscriber capture:
  subscribe(email, source)            -- Stores to data/subscribers.json, notifies Telegram
  subscriber_count()                  -- Total subscriber count
  subscriber_list()                   -- Full subscriber list

CLI:
  python octo_distro.py scorecard
  python octo_distro.py macro
  python octo_distro.py digest
  python octo_distro.py travel
  python octo_distro.py funding
  python octo_distro.py subs
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "data"
SUBSCRIBERS_FILE = DATA_DIR / "subscribers.json"
CALLS_FILE = DATA_DIR / "octo_calls.json"


# ── Subscriber Management ─────────────────────────────────────────────────────

def _load_subscribers() -> list:
    if SUBSCRIBERS_FILE.exists():
        try:
            return json.loads(SUBSCRIBERS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def subscribe(email: str, source: str = "direct") -> dict:
    """Add email to subscriber list. Returns status dict."""
    email = email.strip().lower()
    if not email or "@" not in email or "." not in email.split("@")[-1]:
        return {"ok": False, "reason": "invalid_email"}

    subs = _load_subscribers()
    if any(s.get("email") == email for s in subs):
        return {"ok": True, "status": "already_subscribed", "email": email}

    entry = {
        "email": email,
        "source": source,
        "subscribed_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }
    subs.append(entry)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SUBSCRIBERS_FILE.write_text(json.dumps(subs, indent=2), encoding="utf-8")
    _notify_telegram(
        f"[Distro] New subscriber: {email} (source: {source}). Total: {len(subs)}"
    )
    return {"ok": True, "status": "subscribed", "email": email, "total": len(subs)}


def subscriber_count() -> int:
    return len(_load_subscribers())


def subscriber_list() -> list:
    return _load_subscribers()


def _notify_telegram(message: str):
    try:
        secrets_path = BASE_DIR / ".octo_secrets"
        if not secrets_path.exists():
            return
        s = json.loads(secrets_path.read_text(encoding="utf-8"))
        token = s.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = s.get("TELEGRAM_CHAT_ID", "")
        if not token or not chat_id:
            return
        import httpx
        httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message},
            timeout=5,
        )
    except Exception:
        pass


# ── Tool 1: Oracle Scorecard ──────────────────────────────────────────────────

def oracle_scorecard() -> dict:
    """Track record transparency card. Public -- no email gate."""
    try:
        calls = json.loads(CALLS_FILE.read_text(encoding="utf-8")) if CALLS_FILE.exists() else []
    except Exception:
        calls = []

    oracle_calls = [c for c in calls if c.get("call_type") == "oracle"]
    resolved = [c for c in oracle_calls if c.get("resolved")]
    wins = [c for c in resolved if c.get("won") is True or c.get("outcome") == "win"]
    losses = [c for c in resolved if c.get("outcome") == "loss" or c.get("won") is False]
    open_calls = [c for c in oracle_calls if not c.get("resolved")]
    win_rate = round(len(wins) / len(resolved) * 100, 1) if resolved else None

    recent = sorted(oracle_calls, key=lambda x: x.get("made_at", ""), reverse=True)[:5]

    shareable = (
        f"Octodamus Oracle: {len(wins)}W / {len(losses)}L"
        + (f" -- {win_rate}% win rate" if win_rate is not None else "")
        + f" on {len(resolved)} resolved calls. Real calls. Real data. @octodamusai"
    )

    return {
        "tool": "oracle_scorecard",
        "title": "Octodamus Oracle Track Record",
        "stats": {
            "total_calls": len(oracle_calls),
            "resolved": len(resolved),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": win_rate,
            "open": len(open_calls),
        },
        "recent_calls": [
            {
                "asset": c.get("asset"),
                "direction": c.get("direction"),
                "entry": c.get("entry_price"),
                "target": c.get("target_price"),
                "timeframe": c.get("timeframe"),
                "outcome": c.get("outcome"),
                "made_at": (c.get("made_at") or "")[:10],
            }
            for c in recent
        ],
        "cta": "Follow @octodamusai on X for live oracle calls. Subscribe at octodamus.com for daily signals.",
        "shareable": shareable,
        "gate": False,
    }


# ── Tool 2: Macro Pulse ───────────────────────────────────────────────────────

def macro_pulse() -> dict:
    """5-factor FRED macro score. Public -- no gate."""
    try:
        from octo_macro import get_macro_signal
        data = get_macro_signal()
        score = data.get("score", 0)
        label = data.get("label", "NEUTRAL")
        factors = data.get("factors", {})

        return {
            "tool": "macro_pulse",
            "title": "Crypto Macro Pulse",
            "score": score,
            "max_score": 5,
            "label": label,
            "factors": factors,
            "summary": f"Score: {score:+d}/5 -- {label}",
            "shareable": (
                f"Octodamus Macro Pulse: {score:+d}/5 ({label}). "
                f"Yield curve + DXY + SPX + VIX + M2 all scored. @octodamusai"
            ),
            "cta": "Get daily macro updates. Subscribe at octodamus.com",
            "gate": False,
        }
    except Exception as e:
        return {"tool": "macro_pulse", "error": str(e), "gate": False}


# ── Tool 3: Signal Composite ──────────────────────────────────────────────────

def signal_composite(asset: str = "BTC") -> dict:
    """Composite signal for BTC/ETH/SOL. Email gated."""
    asset = asset.upper()
    try:
        from octo_coinglass import build_oracle_context, funding_rate_exchange, long_short_ratio
        ctx = build_oracle_context(asset)

        # Quick signal read from funding + long/short
        fr = funding_rate_exchange(asset)
        ls = long_short_ratio(asset)

        rates = [v for v in (fr.get("data") or {}).values() if isinstance(v, (int, float))]
        avg_rate = sum(rates) / len(rates) if rates else 0.0
        ls_data = (ls.get("data") or [{}])
        ls_ratio = float(ls_data[-1].get("longAccount", 0.5)) if ls_data else 0.5

        bull = 0
        bear = 0
        if avg_rate < -0.02:
            bull += 1  # shorts overloaded
        elif avg_rate > 0.05:
            bear += 1  # longs overheated
        if ls_ratio > 0.6:
            bear += 1  # crowd too long
        elif ls_ratio < 0.4:
            bull += 1  # crowd too short

        bias = "BULLISH" if bull > bear else ("BEARISH" if bear > bull else "NEUTRAL")

        return {
            "tool": "signal_composite",
            "asset": asset,
            "bias": bias,
            "avg_funding_rate": round(avg_rate, 4),
            "long_ratio": round(ls_ratio, 3),
            "context_snippet": ctx[:400] if ctx else "",
            "cta": "Get full composite signals daily. Subscribe at octodamus.com",
            "gate": True,
        }
    except Exception as e:
        return {"tool": "signal_composite", "asset": asset, "error": str(e), "gate": True}


# ── Tool 4: Funding Extremes ──────────────────────────────────────────────────

def funding_extremes() -> dict:
    """Current funding rate extremes across major assets. Email capture for alerts."""
    try:
        from octo_coinglass import funding_rate_exchange
        extremes = []
        for sym in ["BTC", "ETH", "SOL"]:
            result = funding_rate_exchange(sym)
            rates = [v for v in (result.get("data") or {}).values() if isinstance(v, (int, float))]
            if not rates:
                continue
            avg = sum(rates) / len(rates)
            if abs(avg) >= 0.05:
                extremes.append({
                    "asset": sym,
                    "avg_rate": round(avg, 4),
                    "label": "EXTREME LONG" if avg > 0 else "EXTREME SHORT",
                    "risk": "crowded long -- fade risk" if avg > 0 else "crowded short -- squeeze risk",
                })

        return {
            "tool": "funding_extremes",
            "title": "Funding Rate Extremes",
            "extremes": extremes,
            "alert_threshold_pct": 0.05,
            "count": len(extremes),
            "cta": "Get alerted when funding hits extremes. Subscribe at octodamus.com",
            "gate": True,
        }
    except Exception as e:
        return {"tool": "funding_extremes", "error": str(e), "gate": True}


# ── Tool 5: CME Positioning ───────────────────────────────────────────────────

def cme_positioning() -> dict:
    """CFTC hedge fund net positioning. Email gated weekly report."""
    try:
        from octo_cot import build_oracle_context, get_cot_data
        ctx = build_oracle_context("BTC")
        cot = get_cot_data("BTC") or {}

        net = cot.get("net_position")
        net_str = f"{net:+,}" if isinstance(net, (int, float)) else "N/A"

        return {
            "tool": "cme_positioning",
            "title": "CME Smart Money Report",
            "asset": "BTC",
            "net_position": net,
            "net_position_str": net_str,
            "summary": ctx[:600] if ctx else "CFTC data unavailable",
            "shareable": f"CME hedge fund BTC net position: {net_str} contracts. Source: CFTC. @octodamusai",
            "cta": "Get weekly CME positioning reports. Subscribe at octodamus.com",
            "gate": True,
        }
    except Exception as e:
        return {"tool": "cme_positioning", "error": str(e), "gate": True}


# ── Tool 6: Polymarket Edges ──────────────────────────────────────────────────

def polymarket_edges() -> dict:
    """Markets where Octodamus disagrees with the crowd. Email gated."""
    try:
        calls = json.loads(CALLS_FILE.read_text(encoding="utf-8")) if CALLS_FILE.exists() else []
        pm_open = [
            c for c in calls
            if c.get("call_type") == "polymarket" and not c.get("resolved")
        ]
        edges = [
            {
                "market": (c.get("note") or c.get("asset", ""))[:80],
                "side": c.get("pm_side"),
                "ev": c.get("pm_ev"),
                "confidence": c.get("pm_confidence"),
                "entry_price": c.get("entry_price"),
            }
            for c in pm_open
            if (c.get("pm_ev") or 0) > 0.1
        ][:6]

        return {
            "tool": "polymarket_edges",
            "title": "Polymarket Edge Report",
            "edges": edges,
            "count": len(edges),
            "cta": "Get weekly Polymarket edge picks. Subscribe at octodamus.com",
            "gate": True,
        }
    except Exception as e:
        return {"tool": "polymarket_edges", "error": str(e), "gate": True}


# ── Tool 7: Liquidation Radar ─────────────────────────────────────────────────

def liquidation_radar(asset: str = "BTC") -> dict:
    """Where leveraged bets are stacked. Public daily snapshot."""
    asset = asset.upper()
    try:
        from octo_coinglass import liquidation_history
        hist = liquidation_history(asset, interval="4h")
        data_points = hist.get("data") or []
        recent = data_points[-12:] if len(data_points) >= 12 else data_points

        total_liq = sum(
            (p.get("longLiquidationUsd", 0) or 0) + (p.get("shortLiquidationUsd", 0) or 0)
            for p in recent
        )
        long_liq = sum(p.get("longLiquidationUsd", 0) or 0 for p in recent)
        short_liq = sum(p.get("shortLiquidationUsd", 0) or 0 for p in recent)

        return {
            "tool": "liquidation_radar",
            "title": f"{asset} Liquidation Radar (48h)",
            "asset": asset,
            "total_liquidated_usd": round(total_liq),
            "long_liquidated_usd": round(long_liq),
            "short_liquidated_usd": round(short_liq),
            "dominant_side": "LONGS" if long_liq > short_liq else "SHORTS",
            "shareable": (
                f"{asset} liquidations (48h): ${total_liq/1e6:.1f}M total -- "
                f"${long_liq/1e6:.1f}M longs / ${short_liq/1e6:.1f}M shorts. @octodamusai"
            ),
            "cta": "Follow @octodamusai for daily liquidation signals.",
            "gate": False,
        }
    except Exception as e:
        return {"tool": "liquidation_radar", "asset": asset, "error": str(e), "gate": False}


# ── Tool 8: Travel Signal ─────────────────────────────────────────────────────

def travel_signal() -> dict:
    """TSA + aviation macro indicator. Free embeddable widget."""
    try:
        from octo_flights import get_travel_context, get_tsa_signal
        ctx = get_travel_context()
        tsa = get_tsa_signal()

        return {
            "tool": "travel_signal",
            "title": "Global Travel Macro Signal",
            "tsa_label": tsa.get("label", "N/A") if isinstance(tsa, dict) else "N/A",
            "tsa_wow_pct": tsa.get("wow_pct") if isinstance(tsa, dict) else None,
            "context": ctx,
            "shareable": (
                f"Travel Macro Signal (TSA): {tsa.get('label', 'N/A') if isinstance(tsa, dict) else 'N/A'}. "
                f"Passengers WoW: {tsa.get('wow_pct', 'N/A') if isinstance(tsa, dict) else 'N/A'}%. @octodamusai"
            ),
            "cta": "Get macro signals daily. Follow @octodamusai",
            "gate": False,
        }
    except Exception as e:
        return {"tool": "travel_signal", "error": str(e), "gate": False}


# ── Tool 9: Oracle Simulator ──────────────────────────────────────────────────

def oracle_simulator(asset: str, entry_price: float, exit_price: float, direction: str) -> dict:
    """Backtest: would Octodamus's methodology have called this move? Email gated."""
    direction = direction.upper()
    if entry_price <= 0:
        return {"tool": "oracle_simulator", "error": "invalid entry_price", "gate": True}

    pct_move = (exit_price - entry_price) / entry_price * 100

    if direction == "UP":
        won = pct_move >= 2.0
    elif direction == "DOWN":
        won = pct_move <= -2.0
    else:
        won = abs(pct_move) >= 2.0

    # How many historical Octodamus calls match this pattern
    try:
        calls = json.loads(CALLS_FILE.read_text(encoding="utf-8")) if CALLS_FILE.exists() else []
        similar = [
            c for c in calls
            if c.get("asset", "").upper() == asset.upper()
            and c.get("direction", "").upper() == direction
            and c.get("resolved")
        ]
        similar_wins = [c for c in similar if c.get("won") or c.get("outcome") == "win"]
        historical_rate = round(len(similar_wins) / len(similar) * 100, 1) if similar else None
    except Exception:
        similar = []
        historical_rate = None

    return {
        "tool": "oracle_simulator",
        "asset": asset.upper(),
        "entry": entry_price,
        "exit": exit_price,
        "direction": direction,
        "pct_move": round(pct_move, 2),
        "verdict": "WIN" if won else "LOSS",
        "methodology": "Octodamus requires >=2% move in called direction within timeframe",
        "historical_calls_on_asset": len(similar),
        "historical_win_rate": historical_rate,
        "cta": "See Octodamus live oracle calls. Subscribe at octodamus.com",
        "gate": True,
    }


# ── Tool 10: Intel Digest ─────────────────────────────────────────────────────

def intel_digest() -> dict:
    """Aggregated weekly signal summary. The flagship newsletter product. Email gated."""
    sections = []
    errors = []

    # Oracle track record
    try:
        sc = oracle_scorecard()
        st = sc.get("stats", {})
        wr = st.get("win_rate")
        wr_str = f"{wr}%" if wr is not None else "pending"
        sections.append({
            "label": "ORACLE CALLS",
            "value": f"{st.get('wins', 0)}W / {st.get('losses', 0)}L -- {wr_str} accuracy ({st.get('open', 0)} open)",
        })
    except Exception as e:
        errors.append(f"oracle: {e}")

    # Macro pulse
    try:
        mp = macro_pulse()
        if "error" not in mp:
            sections.append({
                "label": "MACRO PULSE",
                "value": mp.get("summary", "N/A"),
            })
    except Exception as e:
        errors.append(f"macro: {e}")

    # Travel signal
    try:
        ts = travel_signal()
        if "error" not in ts and ts.get("context"):
            sections.append({
                "label": "TRAVEL MACRO",
                "value": ts["context"][:140],
            })
    except Exception as e:
        errors.append(f"travel: {e}")

    # CME positioning snippet
    try:
        cme = cme_positioning()
        if "error" not in cme and cme.get("net_position_str"):
            sections.append({
                "label": "CME POSITIONING",
                "value": f"Net position: {cme['net_position_str']} contracts",
            })
    except Exception as e:
        errors.append(f"cme: {e}")

    preview = "\n".join(f"[{s['label']}] {s['value']}" for s in sections)

    return {
        "tool": "intel_digest",
        "title": "Market Intelligence Digest",
        "sections": sections,
        "preview": preview,
        "errors": errors,
        "cta": "Get the full digest weekly. Subscribe at octodamus.com",
        "shareable": None,
        "gate": True,
    }


# ── Tool Registry ─────────────────────────────────────────────────────────────

TOOLS = {
    "oracle_scorecard": oracle_scorecard,
    "macro_pulse": macro_pulse,
    "signal_composite": signal_composite,
    "funding_extremes": funding_extremes,
    "cme_positioning": cme_positioning,
    "polymarket_edges": polymarket_edges,
    "liquidation_radar": liquidation_radar,
    "travel_signal": travel_signal,
    "oracle_simulator": oracle_simulator,
    "intel_digest": intel_digest,
}

TOOL_METADATA = {
    "oracle_scorecard": {"gate": False, "description": "Track record transparency card"},
    "macro_pulse":      {"gate": False, "description": "5-factor FRED macro score"},
    "signal_composite": {"gate": True,  "description": "Composite signal for BTC/ETH/SOL"},
    "funding_extremes": {"gate": True,  "description": "Extreme funding rate readings"},
    "cme_positioning":  {"gate": True,  "description": "CFTC hedge fund net positioning"},
    "polymarket_edges": {"gate": True,  "description": "Markets where crowd is likely wrong"},
    "liquidation_radar":{"gate": False, "description": "Leveraged position clusters"},
    "travel_signal":    {"gate": False, "description": "TSA + aviation macro indicator"},
    "oracle_simulator": {"gate": True,  "description": "Historical call backtest"},
    "intel_digest":     {"gate": True,  "description": "Aggregated weekly signal summary"},
}


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"

    if cmd == "scorecard":
        result = oracle_scorecard()
    elif cmd == "macro":
        result = macro_pulse()
    elif cmd == "digest":
        result = intel_digest()
    elif cmd == "travel":
        result = travel_signal()
    elif cmd == "funding":
        result = funding_extremes()
    elif cmd == "liquidations":
        asset = sys.argv[2] if len(sys.argv) > 2 else "BTC"
        result = liquidation_radar(asset)
    elif cmd == "edges":
        result = polymarket_edges()
    elif cmd == "cme":
        result = cme_positioning()
    elif cmd == "subs":
        print(f"Subscribers: {subscriber_count()}")
        sys.exit(0)
    elif cmd == "subscribe":
        if len(sys.argv) < 3:
            print("Usage: python octo_distro.py subscribe email@example.com [source]")
            sys.exit(1)
        email = sys.argv[2]
        source = sys.argv[3] if len(sys.argv) > 3 else "cli"
        result = subscribe(email, source)
    else:
        print("Octo Distro Media Engine")
        print("\nCommands:")
        for name, meta in TOOL_METADATA.items():
            gate = "[email gated]" if meta["gate"] else "[free]"
            print(f"  {name:<20} {gate}  {meta['description']}")
        print("\n  subs                 Show subscriber count")
        print("  subscribe <email>    Add a subscriber")
        sys.exit(0)

    print(json.dumps(result, indent=2, default=str))

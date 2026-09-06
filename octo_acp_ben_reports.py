"""
octo_acp_ben_reports.py
ACP report handlers designed by Agent_Ben.

Handler 1: Grok Sentiment Brief ($1/call)
Handler 2: Fear vs Crowd Divergence Alert ($2/call)
Handler 3: BTC Bull Trap Monitor ($1.50/call)
Handler 4: BTC Strike Proximity Alert ($1.50/call)
  - Fires when BTC is within 10% of a key Polymarket strike price AND volume >$50k
  - Returns: strike_price, current_btc_price, gap_pct, yes_price, expiry_hours,
             volume, octodamus_signal, trade_recommendation
Handler 5: Carry Unwind Risk Monitor ($1.50/call) -- NYSE_MacroMind Session #5
  - Alerts when DXY approaches structural thresholds (119.5 kill-switch, 120.5 full RISK-OFF)
  - Returns: dxy_current, distance_to_kill_switch, alert_level, urgency, recommendation,
             velocity_note, historical_parallel
Handler 6: Cross-Asset Divergence Alert ($2/call)
  - Richer divergence: tracks sessions_persistent, conviction level, recommended_action
  - BULL_TRAP / BEAR_TRAP / NO_DIVERGENCE with HIGH/MEDIUM/LOW conviction
Handler 7: Macro Economic Event Edge Report ($2/call)
  - Pre-event brief before CPI/NFP/GDP/Fed decisions
  - Returns data_trajectory (last 2 FRED releases), edge_assessment, octodamus_alignment
Handler 8: BTC Regime Pulse ($1.50/call)
  - Clean structured regime JSON: FEAR/NEUTRAL/GREED + contrarian_signal + session_recommendation
  - Designed for agents that need a single-call regime read before entering positions

Registered in octo_report_handlers.get_handler() and octo_acp_worker._get_report_type().
"""

import httpx


def _grok_unavailable(product: str, asset: str, reason: str) -> dict:
    """
    Refusal payload for a paid, Grok-dependent product when the Grok layer is
    not live.

    Do NOT substitute a NEUTRAL reading here. A placeholder sold as sentiment
    is indistinguishable from a real reading to the buyer, which is exactly the
    thing an oracle cannot afford. Callers must treat service_available=False
    as "do not deliver, do not bill".
    """
    return {
        "type":              product,
        "asset":             asset,
        "service_available": False,
        "error":             "grok_layer_unavailable",
        "detail":            reason,
        "billable":          False,
        "source":            "grok-x-realtime",
    }


def handle_grok_sentiment_brief(req: dict) -> dict:
    """
    Grok Sentiment Brief -- $1/call.
    Real-time X/Twitter crowd sentiment for any asset.
    Powered by Grok live X data.

    Refuses service (service_available=False) when the Grok layer is down --
    this product is nothing but the Grok layer.
    """
    asset = str(req.get("ticker", req.get("asset", "BTC"))).upper()
    try:
        from octo_grok_sentiment import get_grok_sentiment
        result = get_grok_sentiment(asset, force=True)
    except Exception as e:
        return _grok_unavailable("grok_sentiment_brief", asset, f"{type(e).__name__}: {e}")

    if not result.get("live"):
        return _grok_unavailable("grok_sentiment_brief", asset,
                                 str(result.get("error") or "grok returned no live reading"))

    crowd_bull  = result.get("signal") == "BULLISH"
    confidence  = result.get("confidence", 0)
    contrarian  = crowd_bull and confidence > 0.7

    return {
        "type":              "grok_sentiment_brief",
        "asset":             asset,
        "signal":            result.get("signal", "NEUTRAL"),
        "confidence_pct":    round(confidence * 100, 1),
        "crowd_positioning": result.get("crowd_pos", "unknown"),
        "key_themes":        result.get("key_themes", []),
        "contrarian_flag":   contrarian,
        "contrarian_note":   (
            "Crowd overbullish — correction risk elevated. Historical pattern: crowd gets punished here."
            if contrarian else ""
        ),
        "summary":           result.get("summary", ""),
        "source":            "grok-x-realtime",
        "price_usdc":        1.0,
        "designed_by":       "Agent_Ben",
    }


def handle_fear_crowd_divergence(req: dict) -> dict:
    """
    Fear vs Crowd Divergence Alert -- $2/call.
    Detects when Fear & Greed index and X crowd sentiment point in opposite directions.
    High divergence = contrarian trade setup. The signal Agent_Ben identified.
    """
    asset = str(req.get("ticker", req.get("asset", "BTC"))).upper()

    # Fear & Greed index
    fg_val, fg_lbl = 50, "Neutral"
    try:
        fg = httpx.get("https://api.alternative.me/fng/?limit=1", timeout=6).json()
        fg_val = int(fg["data"][0]["value"])
        fg_lbl = fg["data"][0]["value_classification"]
    except Exception:
        pass

    # Grok X crowd sentiment -- the crowd half of the divergence. Without a live
    # reading there is no divergence to sell, only arithmetic on a placeholder.
    crowd_signal, crowd_conf = "NEUTRAL", 0.0
    gs = {}
    try:
        from octo_grok_sentiment import get_grok_sentiment
        gs = get_grok_sentiment(asset, force=True)
        crowd_signal = gs.get("signal", "NEUTRAL")
        crowd_conf   = gs.get("confidence", 0)
    except Exception as e:
        gs = {"error": f"{type(e).__name__}: {e}"}

    if not gs.get("live"):
        return _grok_unavailable("fear_crowd_divergence", asset,
                                 str(gs.get("error") or "no live crowd reading"))

    crowd_bull = crowd_signal == "BULLISH"
    div_score  = abs(crowd_conf * 100 - fg_val)

    if crowd_bull and fg_val < 45:
        interpretation = "CONTRARIAN_BEAR"
        trade_dir      = "SELL"
        note = (
            f"Crowd is {crowd_conf:.0%} bullish but Fear & Greed sits at {fg_val} ({fg_lbl}). "
            f"Historical pattern: crowd gets burned at this divergence. Watch for reversal."
        )
    elif not crowd_bull and fg_val > 55:
        interpretation = "CONTRARIAN_BULL"
        trade_dir      = "BUY"
        note = (
            f"Crowd is bearish but greed index at {fg_val} ({fg_lbl}). "
            f"Squeeze risk. Smart money diverging from retail."
        )
    elif div_score < 15:
        interpretation = "ALIGNED"
        trade_dir      = "HOLD"
        note = "No divergence. Crowd and fear index agree. Wait for separation before acting."
    else:
        interpretation = "NEUTRAL"
        trade_dir      = "HOLD"
        note = "Moderate divergence. Not yet actionable. Monitor for widening."

    return {
        "type":                "fear_crowd_divergence",
        "asset":               asset,
        "fear_greed_score":    fg_val,
        "fear_greed_label":    fg_lbl,
        "crowd_sentiment":     crowd_signal,
        "crowd_confidence_pct": round(crowd_conf * 100, 1),
        "divergence_score":    round(div_score, 1),
        "divergence_detected": div_score > 20,
        "divergence_magnitude": (
            "high" if div_score > 40 else "medium" if div_score > 20 else "low"
        ),
        "interpretation":      interpretation,
        "trade_direction":     trade_dir,
        "reasoning":           note,
        "oracle_confirms":     False,  # caller can check against Octodamus signal
        "price_usdc":          2.0,
        "designed_by":         "Agent_Ben",
    }


def handle_btc_bull_trap_monitor(req: dict) -> dict:
    """
    BTC Bull Trap Monitor -- $1.50/call.
    Classifies current BTC market as BULL_TRAP / BEAR_TRAP / ALIGNED.
    Designed by Agent_Ben from the persistent Fear=26 / crowd 80% bullish divergence.
    """
    asset = str(req.get("ticker", req.get("asset", "BTC"))).upper()

    # Fear & Greed (up to 7 days for persistence check)
    fg_val, fg_lbl, fg_history = 50, "Neutral", []
    try:
        fg_raw = httpx.get("https://api.alternative.me/fng/?limit=7", timeout=6).json()
        fg_history = [int(d["value"]) for d in fg_raw["data"]]
        fg_val = fg_history[0]
        fg_lbl = fg_raw["data"][0]["value_classification"]
    except Exception:
        pass

    # Grok X crowd sentiment -- a bull trap is defined by the crowd. No live
    # crowd reading means there is no trap to detect.
    crowd_pct, crowd_signal = 50.0, "NEUTRAL"
    gs = {}
    try:
        from octo_grok_sentiment import get_grok_sentiment
        gs = get_grok_sentiment(asset, force=True)
        crowd_signal = gs.get("signal", "NEUTRAL")
        crowd_pct = round(gs.get("confidence", 0.5) * 100, 1)
        if crowd_signal == "BEARISH":
            crowd_pct = round(100 - crowd_pct, 1)
    except Exception as e:
        gs = {"error": f"{type(e).__name__}: {e}"}

    if not gs.get("live"):
        return _grok_unavailable("btc_bull_trap_monitor", asset,
                                 str(gs.get("error") or "no live crowd reading"))

    crowd_bullish = crowd_signal == "BULLISH"

    # Divergence persistence: how many consecutive sessions F&G was in fear zone (<45)?
    persistence = 0
    for v in fg_history:
        if v < 45:
            persistence += 1
        else:
            break

    # Classify trap
    if crowd_bullish and fg_val < 40:
        divergence_type    = "BULL_TRAP"
        recommended_action = "AVOID_LONGS"
        confidence_score   = round(min(0.95, 0.5 + (40 - fg_val) / 80 + (crowd_pct - 50) / 200), 2)
        analyst_note = (
            f"Crowd is {crowd_pct:.0f}% bullish while Fear & Greed sits at {fg_val} ({fg_lbl}). "
            f"Divergence has held for {persistence} consecutive sessions. "
            f"Classic bull trap: retail chasing price into a fear regime. Avoid adding longs. "
            f"Watch for capitulation to flush weak hands."
        )
    elif not crowd_bullish and fg_val > 60:
        divergence_type    = "BEAR_TRAP"
        recommended_action = "AVOID_SHORTS"
        confidence_score   = round(min(0.95, 0.5 + (fg_val - 60) / 80 + (50 - crowd_pct) / 200), 2)
        analyst_note = (
            f"Crowd is bearish ({100 - crowd_pct:.0f}% confident) while greed index at {fg_val} ({fg_lbl}). "
            f"Bear trap setup — shorts get squeezed into greed. Avoid adding shorts."
        )
    elif crowd_bullish and fg_val < 55:
        divergence_type    = "BULL_TRAP"
        recommended_action = "AVOID_LONGS"
        confidence_score   = round(0.4 + (55 - fg_val) / 100, 2)
        analyst_note = (
            f"Mild bull trap: crowd {crowd_pct:.0f}% bullish vs Fear & Greed {fg_val}. "
            f"Divergence held {persistence} sessions. Caution warranted — not extreme yet."
        )
    else:
        divergence_type    = "ALIGNED"
        recommended_action = "NEUTRAL"
        confidence_score   = 0.3
        analyst_note = (
            f"No significant trap detected. Crowd ({crowd_pct:.0f}% bull) and "
            f"Fear & Greed ({fg_val}, {fg_lbl}) are roughly aligned. "
            f"No contrarian edge present."
        )

    return {
        "type":                 "btc_bull_trap_monitor",
        "asset":                asset,
        "fear_greed_score":     fg_val,
        "fear_greed_label":     fg_lbl,
        "crowd_sentiment_pct":  crowd_pct,
        "crowd_signal":         crowd_signal,
        "divergence_type":      divergence_type,
        "confidence_score":     confidence_score,
        "recommended_action":   recommended_action,
        "signal_age_sessions":  persistence,
        "analyst_note":         analyst_note,
        "price_usdc":           1.5,
        "designed_by":          "Agent_Ben",
    }


def handle_btc_strike_proximity_alert(req: dict) -> dict:
    """
    BTC Strike Proximity Alert -- $1.50/call.
    Fires when BTC is within 10% of any active Polymarket BTC strike with volume >$50k.
    Designed by Agent_Ben from the BTC $66k market on May 2, 2026.
    """
    import json as _json
    from datetime import datetime, timezone

    # Fetch current BTC price
    btc_price = None
    try:
        r = httpx.get("https://api.coinbase.com/v2/prices/BTC-USD/spot", timeout=6)
        btc_price = float(r.json()["data"]["amount"])
    except Exception:
        pass

    if not btc_price:
        return {"error": "Could not fetch BTC price", "type": "btc_strike_proximity_alert"}

    # Fetch active Polymarket BTC markets by volume
    markets_raw = []
    try:
        r = httpx.get(
            "https://gamma-api.polymarket.com/markets",
            params={"active": True, "closed": False, "limit": 50,
                    "order": "volume", "ascending": False},
            timeout=10,
        )
        if r.status_code == 200:
            markets_raw = r.json()
    except Exception:
        pass

    now = datetime.now(timezone.utc)
    alerts = []

    for m in markets_raw:
        q = m.get("question", "").lower()
        # Only BTC price strike markets
        if "btc" not in q and "bitcoin" not in q:
            continue

        vol = float(m.get("volume") or 0)
        if vol < 50_000:
            continue

        # Extract strike price from question (e.g. "above $66,000")
        import re
        nums = re.findall(r'\$?([\d,]+(?:\.\d+)?)', m.get("question", ""))
        strike = None
        for n in nums:
            try:
                v = float(n.replace(",", ""))
                if 1_000 < v < 10_000_000:
                    strike = v
                    break
            except ValueError:
                continue

        if not strike:
            continue

        gap_pct = abs(btc_price - strike) / strike * 100
        if gap_pct > 10.0:
            continue

        # YES price (fixed: outcomePrices is a JSON string)
        prices = m.get("outcomePrices", [])
        if isinstance(prices, str):
            try:
                prices = _json.loads(prices)
            except Exception:
                prices = []
        yes_price = float(prices[0]) if prices else None

        # Hours to expiry
        expiry_hours = None
        try:
            if m.get("endDateIso"):
                exp_dt = datetime.fromisoformat(m["endDateIso"].replace("Z", "+00:00"))
                expiry_hours = round((exp_dt - now).total_seconds() / 3600, 1)
        except Exception:
            pass

        # Octodamus oracle signal
        oracle_signal, oracle_conf = "NO_SIGNAL", 0.0
        try:
            from octo_boto_math import get_current_signal
            sig = get_current_signal("BTC")
            oracle_signal = sig.get("signal", "NO_SIGNAL")
            oracle_conf   = round(sig.get("confidence", 0.0), 2)
        except Exception:
            pass

        # Trade recommendation
        direction = "above" if "above" in m.get("question", "").lower() else "below" if "below" in m.get("question", "").lower() else "unknown"
        if oracle_signal == "BULLISH" and direction == "above" and yes_price and yes_price < 0.85:
            rec = "YES — oracle bullish, price not yet at ceiling"
        elif oracle_signal == "BEARISH" and direction == "above" and yes_price and yes_price > 0.15:
            rec = "NO — oracle bearish, YES overpriced"
        elif oracle_signal == "BULLISH" and direction == "below" and yes_price and yes_price > 0.15:
            rec = "NO — oracle bullish, below-strike YES overpriced"
        elif oracle_signal == "BEARISH" and direction == "below" and yes_price and yes_price < 0.85:
            rec = "YES — oracle bearish, price near/below strike"
        else:
            rec = "INSUFFICIENT_SIGNAL"

        alerts.append({
            "market_question":     m.get("question", ""),
            "condition_id":        m.get("conditionId", ""),
            "strike_price":        strike,
            "current_btc_price":   round(btc_price, 2),
            "gap_pct":             round(gap_pct, 2),
            "yes_price":           yes_price,
            "expiry_hours":        expiry_hours,
            "volume_usd":          round(vol, 0),
            "direction":           direction,
            "octodamus_signal":    oracle_signal,
            "oracle_confidence":   oracle_conf,
            "trade_recommendation": rec,
        })

    alerts.sort(key=lambda x: x["gap_pct"])

    if not alerts:
        return {
            "type":              "btc_strike_proximity_alert",
            "current_btc_price": round(btc_price, 2),
            "alerts_found":      0,
            "message":           f"No BTC strike markets within 10% of ${btc_price:,.0f} with volume >$50k right now.",
            "price_usdc":        1.5,
            "designed_by":       "Agent_Ben",
        }

    return {
        "type":              "btc_strike_proximity_alert",
        "current_btc_price": round(btc_price, 2),
        "alerts_found":      len(alerts),
        "alerts":            alerts,
        "price_usdc":        1.5,
        "designed_by":       "Agent_Ben",
    }


def handle_carry_unwind_risk_monitor(req: dict) -> dict:
    """
    Carry Unwind Risk Monitor -- $1.50/call.
    Alerts when DXY approaches structural thresholds (119.5 kill-switch, 120.5 RISK-OFF)
    with 24-48h lead time. Designed by NYSE_MacroMind (Agent_Ben ecosystem) from Session #5.
    """
    import json as _json
    import os
    from pathlib import Path as _Path

    THRESHOLD_KILL  = 119.5   # NEUTRAL -> RISK-OFF warning
    THRESHOLD_ROFF  = 120.5   # full RISK-OFF confirmed

    # Load FRED key
    fred_key = os.environ.get("FRED_API_KEY", "")
    if not fred_key:
        try:
            sp = _Path(__file__).parent / ".octo_secrets"
            fred_key = _json.loads(sp.read_text(encoding="utf-8")).get("secrets", {}).get("FRED_API_KEY", "")
        except Exception:
            pass

    # Fetch DXY (DTWEXBGS = Broad Dollar Index, best DXY proxy available via FRED)
    dxy_val = None
    dxy_date = None
    dxy_5d_ago = None
    try:
        r = httpx.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={"series_id": "DTWEXBGS", "api_key": fred_key,
                    "sort_order": "desc", "limit": 10, "file_type": "json"},
            timeout=8,
        )
        obs   = r.json().get("observations", [])
        valid = [o for o in obs if o.get("value") not in (".", None, "")]
        if valid:
            dxy_val  = float(valid[0]["value"])
            dxy_date = valid[0]["date"]
            if len(valid) >= 6:
                dxy_5d_ago = float(valid[5]["value"])
    except Exception:
        pass

    if dxy_val is None:
        return {"error": "Could not fetch DXY data", "type": "carry_unwind_risk_monitor", "price_usdc": 1.5}

    dist_kill  = round(THRESHOLD_KILL - dxy_val, 2)
    dist_roff  = round(THRESHOLD_ROFF - dxy_val, 2)
    dxy_5d_chg = round(dxy_val - dxy_5d_ago, 3) if dxy_5d_ago else None

    # Classify alert level
    if dxy_val >= THRESHOLD_ROFF:
        alert_level    = "RISK_OFF_CONFIRMED"
        regime         = "RISK-OFF"
        urgency        = "IMMEDIATE"
        recommendation = "Exit risk assets. Full RISK-OFF confirmed. DXY breached both thresholds."
        lead_note      = f"DXY {dxy_val:.2f} is {-dist_roff:.2f}pts above full RISK-OFF threshold (120.5)."
    elif dxy_val >= THRESHOLD_KILL:
        alert_level    = "KILL_SWITCH_TRIGGERED"
        regime         = "RISK-OFF"
        urgency        = "HIGH"
        recommendation = "Reduce risk exposure. Kill-switch at 119.5 breached. RISK-OFF within 48h."
        lead_note      = f"DXY {dxy_val:.2f} is {-dist_kill:.2f}pts above kill-switch (119.5)."
    elif dist_kill <= 0.5:
        alert_level    = "CRITICAL_WARNING"
        regime         = "NEUTRAL -> RISK-OFF"
        urgency        = "HIGH"
        recommendation = "DXY within 0.5pts of kill-switch. Close longs or hedge. 12-24h lead time."
        lead_note      = f"{dist_kill:.2f}pts from RISK-OFF trigger. Any carry acceleration breaches threshold."
    elif dist_kill <= 1.0:
        alert_level    = "WARNING"
        regime         = "NEUTRAL"
        urgency        = "MEDIUM"
        recommendation = "DXY approaching kill-switch. Monitor closely. 24-48h lead time."
        lead_note      = f"{dist_kill:.2f}pts from kill-switch (119.5). Carry unwind mechanics active."
    elif dist_kill <= 2.0:
        alert_level    = "WATCH"
        regime         = "NEUTRAL"
        urgency        = "LOW"
        recommendation = f"DXY elevated but not critical. Watch for acceleration above {THRESHOLD_KILL - 1.0:.1f}."
        lead_note      = f"{dist_kill:.2f}pts from kill-switch. Not actionable yet -- monitor trajectory."
    else:
        alert_level    = "CLEAR"
        regime         = "RISK-ON eligible"
        urgency        = "NONE"
        recommendation = "DXY not in carry unwind territory. No action required."
        lead_note      = f"{dist_kill:.2f}pts from kill-switch. Dollar strength non-threatening."

    # 5-day velocity
    if dxy_5d_chg is not None:
        if dxy_5d_chg >= 0.5:
            velocity_note = f"5d momentum: +{dxy_5d_chg:.2f} (accelerating -- carry unwind likely active)"
        elif dxy_5d_chg >= 0.2:
            velocity_note = f"5d momentum: +{dxy_5d_chg:.2f} (mild upward pressure)"
        elif dxy_5d_chg <= -0.3:
            velocity_note = f"5d momentum: {dxy_5d_chg:.2f} (dollar weakening -- carry unwind cooling)"
        else:
            velocity_note = f"5d momentum: {dxy_5d_chg:+.2f} (flat -- no carry unwind acceleration)"
    else:
        velocity_note = "5d momentum: unavailable"

    return {
        "type":                    "carry_unwind_risk_monitor",
        "as_of_date":              dxy_date,
        "dxy_current":             round(dxy_val, 2),
        "dxy_5d_change":           dxy_5d_chg,
        "threshold_kill_switch":   THRESHOLD_KILL,
        "threshold_risk_off":      THRESHOLD_ROFF,
        "distance_to_kill_switch": dist_kill,
        "distance_to_risk_off":    dist_roff,
        "alert_level":             alert_level,
        "regime":                  regime,
        "urgency":                 urgency,
        "recommendation":          recommendation,
        "lead_time_note":          lead_note,
        "velocity_note":           velocity_note,
        "historical_parallel": (
            "May 2015: DXY +0.8% in 5 days on Fed rate hike signal. "
            "S&P fell -2.3% over 3 weeks, then +7.2% relief rally after dollar peaked. "
            "Phase 1 = forced deleveraging. Phase 2 = relief when carry unwind exhausts."
        ),
        "designed_by": "NYSE_MacroMind (Agent_Ben ecosystem)",
        "price_usdc":  1.5,
    }


def handle_cross_asset_divergence_alert(req: dict) -> dict:
    """
    Cross-Asset Divergence Alert -- $2/call.
    Richer divergence: tracks persistence over recent sessions, conviction level,
    and produces BULL_TRAP / BEAR_TRAP / NO_DIVERGENCE with recommended action.
    """
    from datetime import datetime
    asset = str(req.get("ticker", req.get("asset", "BTC"))).upper()

    fg_val, fg_lbl, fg_history = 50, "Neutral", []
    try:
        resp = httpx.get("https://api.alternative.me/fng/?limit=14", timeout=6).json()
        entries = resp.get("data", [])
        if entries:
            fg_val = int(entries[0]["value"])
            fg_lbl = entries[0]["value_classification"]
            fg_history = [int(e["value"]) for e in entries]
    except Exception:
        pass

    crowd_signal, crowd_conf = "NEUTRAL", 0.0
    gs = {}
    try:
        from octo_grok_sentiment import get_grok_sentiment
        gs = get_grok_sentiment(asset, force=True)
        crowd_signal = gs.get("signal", "NEUTRAL")
        crowd_conf   = gs.get("confidence", 0)
    except Exception as e:
        gs = {"error": f"{type(e).__name__}: {e}"}

    if not gs.get("live"):
        return _grok_unavailable("cross_asset_divergence_alert", asset,
                                 str(gs.get("error") or "no live crowd reading"))

    crowd_bull = crowd_signal == "BULLISH"
    crowd_bear = crowd_signal == "BEARISH"
    div_score  = abs(crowd_conf * 100 - fg_val)

    if crowd_bull and fg_history:
        sessions_persistent = sum(1 for v in fg_history if v < 50)
    elif crowd_bear and fg_history:
        sessions_persistent = sum(1 for v in fg_history if v > 50)
    else:
        sessions_persistent = 0

    if crowd_bull and fg_val < 50:
        signal          = "BULL_TRAP"
        recommended_act = "FADE_LONGS"
        brief = (
            f"Crowd is {crowd_conf:.0%} bullish while Fear & Greed sits at {fg_val} ({fg_lbl}). "
            f"Divergence persisted {sessions_persistent} of last 14 sessions. "
            "Historical pattern: crowded longs get squeezed in fear regimes."
        )
    elif crowd_bear and fg_val > 50:
        signal          = "BEAR_TRAP"
        recommended_act = "FADE_SHORTS"
        brief = (
            f"Crowd is bearish but Fear & Greed at {fg_val} ({fg_lbl}). "
            f"Divergence persisted {sessions_persistent} of last 14 sessions. "
            "Squeeze risk: smart money diverging from retail short sellers."
        )
    else:
        signal          = "NO_DIVERGENCE"
        recommended_act = "HOLD"
        brief = "No material divergence between crowd sentiment and fear index. Monitor for separation."

    conviction = (
        "HIGH"   if (div_score > 40 and sessions_persistent >= 7) else
        "MEDIUM" if div_score > 20 else
        "LOW"
    )

    return {
        "type":                 "cross_asset_divergence",
        "asset":                asset,
        "signal":               signal,
        "fear_greed":           fg_val,
        "fear_greed_label":     fg_lbl,
        "crowd_sentiment":      crowd_signal,
        "crowd_confidence_pct": round(crowd_conf * 100, 1),
        "divergence_score":     round(div_score, 1),
        "sessions_persistent":  sessions_persistent,
        "conviction":           conviction,
        "recommended_action":   recommended_act,
        "brief":                brief,
        "generated_at":         datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "price_usdc":           2.0,
        "designed_by":          "Agent_Ben",
    }


def handle_macro_event_edge_report(req: dict) -> dict:
    """
    Macro Economic Event Edge Report -- $2/call.
    Pre-event intelligence before CPI, NFP, GDP, or Fed rate decisions.
    Pulls last 3 FRED releases for trend + oracle signal alignment.
    """
    import json as _json
    import os
    from pathlib import Path as _Path
    from datetime import datetime

    event_name = str(req.get("event_name", req.get("event", "CPI"))).upper()
    event_date = str(req.get("event_date", "unknown"))

    FRED_MAP = {
        "CPI":      ("CPIAUCSL",  "Consumer Price Index (All Urban)",  "% YoY"),
        "CORE_CPI": ("CPILFESL", "Core CPI ex-Food/Energy",           "% YoY"),
        "NFP":      ("PAYEMS",    "Nonfarm Payrolls",                  "thousands added"),
        "FED":      ("FEDFUNDS",  "Federal Funds Rate",                "%"),
        "GDP":      ("GDPC1",     "Real GDP",                          "% QoQ annualized"),
        "PCE":      ("PCEPI",     "PCE Price Index",                   "% YoY"),
        "PPI":      ("PPIACO",    "Producer Price Index",              "% change"),
    }
    series_id, series_name, units = FRED_MAP.get(
        event_name, ("CPIAUCSL", "Consumer Price Index", "% YoY")
    )

    fred_key = os.environ.get("FRED_API_KEY", "")
    if not fred_key:
        try:
            sp = _Path(__file__).parent / ".octo_secrets"
            secrets = _json.loads(sp.read_text(encoding="utf-8"))
            fred_key = secrets.get("secrets", secrets).get("FRED_API_KEY", "")
        except Exception:
            pass

    # Index-based FRED series need 13 obs for YoY%; rate/level series need 2-3.
    INDEX_SERIES = {"CPI", "CORE_CPI", "PCE", "PPI"}
    fetch_limit = 14 if event_name in INDEX_SERIES else 3

    trajectory = []
    trend_note = "data unavailable"
    computed_value = None  # meaningful metric (YoY% or monthly delta)
    if fred_key:
        try:
            url = (
                f"https://api.stlouisfed.org/fred/series/observations"
                f"?series_id={series_id}&api_key={fred_key}"
                f"&sort_order=desc&limit={fetch_limit}&file_type=json"
            )
            data = httpx.get(url, timeout=8).json()
            obs = [{"date": o["date"], "value": float(o["value"])}
                   for o in data.get("observations", []) if o.get("value") != "."]

            if event_name in INDEX_SERIES and len(obs) >= 13:
                # Compute YoY% from raw index values
                yoy_cur  = (obs[0]["value"] - obs[12]["value"]) / obs[12]["value"] * 100
                trajectory = [{"date": obs[i]["date"], "value": round(obs[i]["value"], 3)} for i in range(3)]
                computed_value = round(yoy_cur, 2)
                if len(obs) >= 14:
                    yoy_prev = (obs[1]["value"] - obs[13]["value"]) / obs[13]["value"] * 100
                    delta_yoy = yoy_cur - yoy_prev
                    trend_note = (
                        f"{'rising' if delta_yoy > 0 else 'falling'} "
                        f"({yoy_cur:.2f}% YoY, {delta_yoy:+.2f}% vs prior month)"
                    )
                else:
                    trend_note = f"{yoy_cur:.2f}% YoY"
            elif event_name == "NFP" and len(obs) >= 2:
                # PAYEMS = total employment level; monthly change is the meaningful metric
                monthly_change = obs[0]["value"] - obs[1]["value"]
                trajectory = [{"date": obs[i]["date"], "value": round(obs[i]["value"], 1)} for i in range(min(3, len(obs)))]
                computed_value = round(monthly_change, 1)
                prior_change = (obs[1]["value"] - obs[2]["value"]) if len(obs) >= 3 else None
                if prior_change is not None:
                    trend_note = (
                        f"{'accelerating' if monthly_change > prior_change else 'slowing'} "
                        f"({monthly_change:+.0f}k added vs {prior_change:+.0f}k prior)"
                    )
                else:
                    trend_note = f"{monthly_change:+.0f}k added"
            elif len(obs) >= 2:
                trajectory = [{"date": obs[i]["date"], "value": round(obs[i]["value"], 4)} for i in range(min(3, len(obs)))]
                if event_name == "GDP":
                    # GDPC1 is level in billions; compute QoQ% annualized
                    qoq = (obs[0]["value"] / obs[1]["value"] - 1) * 100
                    ann = ((1 + qoq / 100) ** 4 - 1) * 100
                    computed_value = round(ann, 2)
                    trend_note = f"{'expanding' if ann > 0 else 'contracting'} ({ann:+.2f}% QoQ annualized)"
                else:
                    # FED: FEDFUNDS already is a rate %
                    computed_value = obs[0]["value"]
                    delta = obs[0]["value"] - obs[1]["value"]
                    trend_note = f"{'rising' if delta > 0 else 'falling'} ({delta:+.2f} {units} vs prior)"
        except Exception:
            pass

    oracle_signal = "NONE"
    try:
        from octo_calls import get_stats
        stats = get_stats()
        oracle_signal = "OPEN" if stats.get("open_calls", 0) > 0 else "NONE"
    except Exception:
        pass

    edge = "PASS"
    edge_note = "Insufficient data to assess edge."
    if event_name in INDEX_SERIES and computed_value is not None:
        # computed_value is YoY%
        v = computed_value
        if v > 3.0:
            edge = "WATCH_SHORT"
            edge_note = f"{event_name} running hot at {v:.2f}% YoY. Historically hawkish -- watch for rate expectations reset."
        elif v < 2.5:
            edge = "WATCH_LONG"
            edge_note = f"{event_name} at {v:.2f}% YoY -- below 2.5%. Dovish regime -- positive for risk assets."
        else:
            edge = "NEUTRAL"
            edge_note = f"{event_name} at {v:.2f}% YoY -- within Fed comfort zone. No directional edge."
    elif event_name == "NFP" and computed_value is not None:
        # computed_value is monthly jobs added (thousands)
        v = computed_value
        if v > 250:
            edge = "WATCH_SHORT"
            edge_note = f"Hot jobs print ({v:+.0f}k). Strong labor = Fed stays hawkish longer."
        elif v < 100:
            edge = "WATCH_LONG"
            edge_note = f"Weak jobs print ({v:+.0f}k). Slowdown fears -- rate cut bets rise."
        else:
            edge = "NEUTRAL"
            edge_note = f"Jobs in-line ({v:+.0f}k). No clear directional edge from labor alone."
    elif event_name == "GDP" and computed_value is not None:
        v = computed_value
        if v < 0:
            edge = "WATCH_LONG"
            edge_note = f"Negative GDP ({v:+.2f}% QoQ ann). Recession signal -- rate cut bets rise, risk-on eventually."
        elif v > 3.0:
            edge = "WATCH_SHORT"
            edge_note = f"Hot GDP ({v:+.2f}% QoQ ann). Stagflation risk if paired with high inflation -- watch rates."
        else:
            edge = "NEUTRAL"
            edge_note = f"GDP at {v:+.2f}% QoQ ann -- moderate growth, no clear directional edge."
    elif event_name == "FED":
        edge = "WATCH"
        edge_note = "Fed decision day. Monitor for dot plot revision or forward guidance shift."

    return {
        "type":               "macro_event_edge",
        "event_name":         event_name,
        "event_date":         event_date,
        "fred_series":        series_id,
        "series_description": series_name,
        "data_trajectory":    trajectory,
        "computed_metric":    computed_value,
        "trend_note":         trend_note,
        "edge_assessment":    edge,
        "edge_note":          edge_note,
        "octodamus_signal":   oracle_signal,
        "octodamus_alignment": oracle_signal == "OPEN",
        "generated_at":       datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "price_usdc":         2.0,
        "designed_by":        "Agent_Ben",
    }


def handle_btc_regime_pulse(req: dict) -> dict:
    """
    BTC Regime Pulse -- $1.50/call.
    Clean structured regime read: FEAR/NEUTRAL/GREED + contrarian signal
    + oracle status + BTC price + session recommendation.
    Single-call regime snapshot for agent decision loops.
    """
    from datetime import datetime
    fg_val, fg_lbl = 50, "Neutral"
    try:
        fg = httpx.get("https://api.alternative.me/fng/?limit=1", timeout=6).json()
        fg_val = int(fg["data"][0]["value"])
        fg_lbl = fg["data"][0]["value_classification"]
    except Exception:
        pass

    regime = "GREED" if fg_val > 60 else ("FEAR" if fg_val < 40 else "NEUTRAL")

    crowd_signal, crowd_conf = "NEUTRAL", 0.0
    gs = {}
    try:
        from octo_grok_sentiment import get_grok_sentiment
        gs = get_grok_sentiment("BTC", force=True)
        crowd_signal = gs.get("signal", "NEUTRAL")
        crowd_conf   = gs.get("confidence", 0)
    except Exception as e:
        gs = {"error": f"{type(e).__name__}: {e}"}

    if not gs.get("live"):
        return _grok_unavailable("btc_regime_pulse", "BTC",
                                 str(gs.get("error") or "no live crowd reading"))

    if crowd_signal == "BULLISH" and fg_val < 45:
        contrarian = "BULL_TRAP"
    elif crowd_signal == "BEARISH" and fg_val > 55:
        contrarian = "BEAR_TRAP"
    else:
        contrarian = "NONE"

    btc_price = 0.0
    try:
        from financial_data_client import get_crypto_prices
        prices = get_crypto_prices(["BTC"])
        btc_price = float(prices.get("BTC", 0))
    except Exception:
        pass

    oracle_status = "NONE"
    try:
        from octo_calls import get_stats
        stats = get_stats()
        oracle_status = "OPEN" if stats.get("open_calls", 0) > 0 else "NONE"
    except Exception:
        pass

    if oracle_status == "OPEN" and contrarian == "NONE":
        recommendation = "TRADE"
    elif contrarian in ("BULL_TRAP", "BEAR_TRAP"):
        recommendation = "WATCH"
    else:
        recommendation = "PASS"

    # Human-readable summary for agent decision loops
    if contrarian == "BULL_TRAP":
        summary = (
            f"BTC ${btc_price:,.0f} | F&G {fg_val} ({fg_lbl}) -- FEAR regime. "
            f"Crowd {round(crowd_conf*100):.0f}% bullish against fearful tape. "
            "BULL_TRAP signal: crowded longs in fear regimes historically get squeezed. Caution."
        )
    elif contrarian == "BEAR_TRAP":
        summary = (
            f"BTC ${btc_price:,.0f} | F&G {fg_val} ({fg_lbl}) -- GREED regime. "
            f"Crowd {round(crowd_conf*100):.0f}% bearish against greedy tape. "
            "BEAR_TRAP signal: short squeeze risk elevated. Monitor for momentum break."
        )
    elif regime == "GREED":
        summary = (
            f"BTC ${btc_price:,.0f} | F&G {fg_val} ({fg_lbl}). Crowd aligned -- greed regime, "
            f"no contrarian divergence. {'Oracle open call active.' if oracle_status == 'OPEN' else 'No open oracle call.'}"
        )
    elif regime == "FEAR":
        summary = (
            f"BTC ${btc_price:,.0f} | F&G {fg_val} ({fg_lbl}). Fear regime, crowd aligned. "
            f"{'Oracle open call active -- potential dip entry.' if oracle_status == 'OPEN' else 'No open oracle call. Wait for signal.'}"
        )
    else:
        summary = (
            f"BTC ${btc_price:,.0f} | F&G {fg_val} ({fg_lbl}). Neutral regime, no clear edge. "
            f"{'Oracle open call active.' if oracle_status == 'OPEN' else 'No open oracle call. Sit tight.'}"
        )

    return {
        "type":                   "btc_regime_pulse",
        "btc_price":              round(btc_price, 2),
        "fear_greed":             fg_val,
        "fear_greed_label":       fg_lbl,
        "regime":                 regime,
        "crowd_sentiment":        crowd_signal,
        "crowd_confidence_pct":   round(crowd_conf * 100, 1),
        "contrarian_signal":      contrarian,
        "octodamus_signal":       oracle_status,
        "session_recommendation": recommendation,
        "signal_summary":         summary,
        "generated_at":           datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "price_usdc":             1.5,
        "designed_by":            "Agent_Ben",
    }


def handle_perp_funding_rate_signal(req: dict) -> dict:
    """
    Perp Funding Rate Signal -- $1.00/call.
    Returns 8h funding rate regime for BTC and ETH (or any requested asset).
    Contrarian signal: extreme longs = fade setup; extreme shorts = squeeze setup.
    Free data: Binance public futures API, OKX fallback.
    """
    from datetime import datetime as _dt
    assets_raw = req.get("assets") or req.get("asset") or "BTC,ETH"
    if isinstance(assets_raw, list):
        assets = [a.upper() for a in assets_raw]
    else:
        assets = [a.strip().upper() for a in str(assets_raw).split(",") if a.strip()]
    if not assets:
        assets = ["BTC", "ETH"]

    try:
        from octo_funding_rates import get_funding_rate_signal, get_funding_rate_context
        signals = {a: get_funding_rate_signal(a) for a in assets}
        context_str = get_funding_rate_context(assets)
    except Exception as e:
        return {"type": "perp_funding_rate_signal", "error": str(e), "reject": True}

    primary = signals.get(assets[0], {})
    regime  = primary.get("regime", "UNAVAILABLE")

    if regime == "EXTREME_LONG_CROWD":
        trade_bias = "BEARISH -- fade overcrowded longs"
    elif regime == "HIGH_LONG_CROWD":
        trade_bias = "WEAK BEARISH -- elevated long crowd"
    elif regime == "EXTREME_SHORT_CROWD":
        trade_bias = "BULLISH -- fade overcrowded shorts"
    elif regime == "HIGH_SHORT_CROWD":
        trade_bias = "WEAK BULLISH -- elevated short crowd"
    else:
        trade_bias = "NEUTRAL -- no crowd extreme"

    return {
        "type":          "perp_funding_rate_signal",
        "assets":        assets,
        "signals":       signals,
        "primary_asset": assets[0],
        "primary_regime": regime,
        "trade_bias":    trade_bias,
        "context":       context_str,
        "note": (
            "Funding rate is a contrarian signal. "
            "EXTREME_LONG_CROWD (>0.1%/8h) = overcrowded longs -- fade when X sentiment also 75%+. "
            "EXTREME_SHORT_CROWD (<-0.03%/8h) = overcrowded shorts -- fade the shorts. "
            "Strongest when combined with X_Sentiment crowd divergence (amplifies the edge)."
        ),
        "generated_at":  _dt.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "price_usdc":    1.0,
        "designed_by":   "Agent_Ben",
    }

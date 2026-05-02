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

Registered in octo_report_handlers.get_handler() and octo_acp_worker._get_report_type().
"""

import httpx


def handle_grok_sentiment_brief(req: dict) -> dict:
    """
    Grok Sentiment Brief -- $1/call.
    Real-time X/Twitter crowd sentiment for any asset.
    Powered by Grok live X data.
    """
    asset = str(req.get("ticker", req.get("asset", "BTC"))).upper()
    try:
        from octo_grok_sentiment import get_grok_sentiment
        result = get_grok_sentiment(asset, force=True)
    except Exception as e:
        result = {
            "signal":     "NEUTRAL",
            "confidence": 0,
            "summary":    str(e),
            "crowd_pos":  "unknown",
            "key_themes": [],
        }

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

    # Grok X crowd sentiment
    crowd_signal, crowd_conf = "NEUTRAL", 0.0
    try:
        from octo_grok_sentiment import get_grok_sentiment
        gs = get_grok_sentiment(asset, force=True)
        crowd_signal = gs.get("signal", "NEUTRAL")
        crowd_conf   = gs.get("confidence", 0)
    except Exception:
        pass

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

    # Grok X crowd sentiment
    crowd_pct, crowd_signal = 50.0, "NEUTRAL"
    try:
        from octo_grok_sentiment import get_grok_sentiment
        gs = get_grok_sentiment(asset, force=True)
        crowd_signal = gs.get("signal", "NEUTRAL")
        crowd_pct = round(gs.get("confidence", 0.5) * 100, 1)
        if crowd_signal == "BEARISH":
            crowd_pct = round(100 - crowd_pct, 1)
    except Exception:
        pass

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

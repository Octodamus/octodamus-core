"""
octo_acp_ben_reports.py
Two new ACP report handlers designed by Agent_Ben.

Handler 1: Grok Sentiment Brief ($1/call)
  - Real-time X/Twitter sentiment for any asset via Grok
  - Returns structured JSON: signal, confidence, crowd positioning, contrarian flag

Handler 2: Fear vs Crowd Divergence Alert ($2/call)
  - Detects dangerous divergence between Fear & Greed index and X crowd sentiment
  - The signal Ben keeps finding: crowd BULLISH while fear index says FEAR

Registered in octo_report_handlers.get_handler() and octo_acp_worker._get_report_type()
via apply_ben_reports.py.
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

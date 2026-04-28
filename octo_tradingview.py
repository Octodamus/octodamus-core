"""
octo_tradingview.py -- TradingView technical consensus (Signal 13).

Uses tradingview-ta library (HTTP requests to TV scanner API — no browser needed).
Combines 1h + 4h timeframes for a more robust signal.
Covers 26 indicators: RSI, MACD, Stoch, BBands, EMAs, SMAs, etc.

Signal vote: STRONG_BUY/BUY = +1, SELL/STRONG_SELL = -1, NEUTRAL = 0
"""

import json
import time
from pathlib import Path

_CACHE_FILE = Path(__file__).parent / "data" / "tv_signal_cache.json"
_CACHE_TTL  = 900  # 15 min

_ASSET_MAP = {
    "BTC":  ("BTCUSDT",  "BINANCE",  "crypto"),
    "ETH":  ("ETHUSDT",  "BINANCE",  "crypto"),
    "SOL":  ("SOLUSDT",  "BINANCE",  "crypto"),
    "NVDA": ("NVDA",     "NASDAQ",   "america"),
    "TSLA": ("TSLA",     "NASDAQ",   "america"),
    "COIN": ("COIN",     "NASDAQ",   "america"),
    "MSTR": ("MSTR",     "NASDAQ",   "america"),
    "SPY":  ("SPY",      "AMEX",     "america"),
    "GLD":  ("GLD",      "AMEX",     "america"),
}

_REC_TO_VOTE = {
    "STRONG_BUY":  1,
    "BUY":         1,
    "NEUTRAL":     0,
    "SELL":       -1,
    "STRONG_SELL":-1,
}

_REC_TO_STRENGTH = {
    "STRONG_BUY":  "strong",
    "BUY":         "normal",
    "NEUTRAL":     "neutral",
    "SELL":        "normal",
    "STRONG_SELL": "strong",
}


def _load_cache() -> dict:
    try:
        if _CACHE_FILE.exists():
            d = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
            if time.time() - d.get("ts", 0) < _CACHE_TTL:
                return d.get("data", {})
    except Exception:
        pass
    return {}


def _save_cache(data: dict):
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(
            json.dumps({"ts": time.time(), "data": data}),
            encoding="utf-8"
        )
    except Exception:
        pass


def get_tv_signal(asset: str = "BTC", force: bool = False) -> dict:
    """
    Get TradingView technical consensus for asset.
    Combines 1h + 4h timeframes — both must agree for high confidence.

    Returns:
        {
            "signal":      "BULLISH" | "BEARISH" | "NEUTRAL",
            "vote":        1 | -1 | 0,
            "confidence":  "strong" | "normal" | "neutral",
            "rec_1h":      "STRONG_BUY" etc,
            "rec_4h":      "STRONG_BUY" etc,
            "buy_1h":      int,
            "sell_1h":     int,
            "buy_4h":      int,
            "sell_4h":     int,
            "agreement":   True | False,  # 1h and 4h agree
            "summary":     "1-line description",
            "source":      "tradingview-ta"
        }
    """
    asset = asset.upper()
    cache = _load_cache()
    if not force and asset in cache:
        return cache[asset]

    mapping = _ASSET_MAP.get(asset)
    if not mapping:
        return _neutral(asset, f"No TV mapping for {asset}")

    symbol, exchange, screener = mapping

    try:
        from tradingview_ta import TA_Handler, Interval

        h1 = TA_Handler(symbol=symbol, exchange=exchange,
                        screener=screener, interval=Interval.INTERVAL_1_HOUR)
        h4 = TA_Handler(symbol=symbol, exchange=exchange,
                        screener=screener, interval=Interval.INTERVAL_4_HOURS)

        a1 = h1.get_analysis()
        a4 = h4.get_analysis()

        rec_1h = a1.summary["RECOMMENDATION"]
        rec_4h = a4.summary["RECOMMENDATION"]
        buy_1h = a1.summary["BUY"]
        sel_1h = a1.summary["SELL"]
        buy_4h = a4.summary["BUY"]
        sel_4h = a4.summary["SELL"]

        vote_1h = _REC_TO_VOTE.get(rec_1h, 0)
        vote_4h = _REC_TO_VOTE.get(rec_4h, 0)
        agreement = vote_1h == vote_4h

        # Combined vote: both timeframes must agree for non-zero vote
        if agreement and vote_1h != 0:
            combined_vote = vote_1h
            # Strength: strong if either TF is STRONG_ recommendation
            is_strong = "STRONG" in rec_1h or "STRONG" in rec_4h
            confidence = "strong" if is_strong else "normal"
        elif vote_1h != 0 and vote_4h == 0:
            combined_vote = vote_1h  # 1h leading, 4h neutral — weak signal
            confidence = "normal"
        elif vote_4h != 0 and vote_1h == 0:
            combined_vote = vote_4h  # 4h trend, 1h neutral — moderate
            confidence = "normal"
        else:
            combined_vote = 0  # disagreement or both neutral
            confidence = "neutral"

        signal = "BULLISH" if combined_vote > 0 else ("BEARISH" if combined_vote < 0 else "NEUTRAL")

        summary = (
            f"TV 1h {rec_1h} ({buy_1h}B/{sel_1h}S) + "
            f"4h {rec_4h} ({buy_4h}B/{sel_4h}S)"
            f"{' — timeframes AGREE' if agreement else ' — timeframes diverge'}"
        )

        result = {
            "signal":    signal,
            "vote":      combined_vote,
            "confidence": confidence,
            "rec_1h":    rec_1h,
            "rec_4h":    rec_4h,
            "buy_1h":    buy_1h,
            "sell_1h":   sel_1h,
            "buy_4h":    buy_4h,
            "sell_4h":   sel_4h,
            "agreement": agreement,
            "summary":   summary,
            "asset":     asset,
            "source":    "tradingview-ta",
        }

        cache[asset] = result
        _save_cache(cache)
        return result

    except Exception as e:
        return _neutral(asset, str(e))


def get_tv_signal_context(assets: list = None) -> str:
    """Formatted string for oracle prompt injection."""
    if assets is None:
        assets = ["BTC", "ETH", "SOL"]

    lines = ["TradingView Technical Consensus (Signal 13 — 1h+4h combined):"]
    for asset in assets:
        s = get_tv_signal(asset)
        if s["vote"] != 0 or s["confidence"] != "neutral":
            agree = "AGREE" if s.get("agreement") else "DIVERGE"
            lines.append(
                f"  {asset}: {s['signal']} ({s['confidence'].upper()}) "
                f"| 1h:{s['rec_1h']} 4h:{s['rec_4h']} | TFs {agree}"
            )
        else:
            lines.append(f"  {asset}: NEUTRAL (timeframes diverge or mixed)")
    return "\n".join(lines)


def _neutral(asset: str, reason: str = "") -> dict:
    return {
        "signal":     "NEUTRAL",
        "vote":       0,
        "confidence": "neutral",
        "rec_1h":     "NEUTRAL",
        "rec_4h":     "NEUTRAL",
        "buy_1h":     0, "sell_1h": 0,
        "buy_4h":     0, "sell_4h": 0,
        "agreement":  False,
        "summary":    f"TV signal unavailable: {reason}",
        "asset":      asset,
        "source":     "tradingview-ta",
    }


if __name__ == "__main__":
    import sys
    asset = sys.argv[1].upper() if len(sys.argv) > 1 else "BTC"
    print(f"TradingView signal for {asset}:")
    r = get_tv_signal(asset, force=True)
    print(json.dumps(r, indent=2))
    print()
    print(get_tv_signal_context([asset]))

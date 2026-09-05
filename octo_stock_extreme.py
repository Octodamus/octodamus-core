"""
octo_stock_extreme.py -- Stock Perp Extreme Oracle (tokenized equities)

Tokenized-stock perps have ~0 funding (unlike crypto), so funding_extreme cannot fire on
them. The tradable extreme here is OFFSIDE LEVERAGE: an extreme positioning dislocation set
to unwind IN THE DIRECTION OF THE PREVAILING TREND -- a trend-aligned squeeze.

This is deliberately the OPPOSITE of range_scout / crowd_fade, which lost (1-8, 2-8) by fading
positioning INTO the trend. We only fire when the offside crowd is positioned to be run over
BY the trend, never against it.

Signals:
  - positioning + OI:  octo_coinglass.get_stock_perp_signal -> long_pct, oi_chg_24h_pct
  - trend:             5-day % change of the underlying (yfinance)
  - price:             octo_robinhood.get_mid (Robinhood Chain, halt-aware)

Fire rules (strict -- fires rarely, high conviction):
  UPTREND   (5d >= +TREND_PCT) + short-heavy crowd (long_pct <= SHORT_CROWD) + OI building
      -> UP    (short squeeze in an uptrend)
  DOWNTREND (5d <= -TREND_PCT) + long-heavy crowd  (long_pct >= LONG_CROWD)  + OI building
      -> DOWN  (long flush in a downtrend)
  everything else -> no fire (esp. crowd-long-in-uptrend = the trend-fighting trap)

Timeframe: 24h. Target: 2.5%. call_type: "stock_extreme".
Protected by the fleet circuit breaker (octo_calls.strategy_should_pause) once it has a record.

Usage:
  python octo_stock_extreme.py            # scan all
  python octo_stock_extreme.py --dry      # score, no record/post
  python octo_stock_extreme.py NVDA       # one ticker
"""

import sys
import argparse
from datetime import datetime, timezone

_STOCK_ASSETS = ["NVDA", "TSLA", "AAPL", "MSFT", "SPY", "COIN", "MSTR"]

TREND_PCT    = 2.0    # 5-day % move to qualify as a trend
SHORT_CROWD  = 35.0   # long_pct <= this = short-heavy crowd (squeeze fuel in an uptrend)
LONG_CROWD   = 68.0   # long_pct >= this = long-heavy crowd (flush fuel in a downtrend)
OI_CONFIRM   = 3.0    # oi_chg_24h_pct >= this = leverage building (complacent offside crowd)
TARGET_PCT   = 2.5
TIMEFRAME    = "24h"


def _get_price(ticker: str):
    """Robinhood Chain mid for the tokenized stock. None if halted/unavailable."""
    try:
        from octo_robinhood import get_mid
        return get_mid(ticker)
    except Exception:
        return None


def _get_trend_5d(ticker: str):
    """5-day % change of the underlying equity (yfinance). None if unavailable."""
    try:
        import yfinance as yf
        h = yf.Ticker(ticker).history(period="7d")
        closes = [c for c in h["Close"].tolist() if c and c > 0]
        if len(closes) < 2:
            return None
        return (closes[-1] - closes[0]) / closes[0] * 100.0
    except Exception:
        return None


def _has_open_call(ticker: str) -> bool:
    from octo_calls import _load
    for c in _load():
        if (not c.get("resolved") and c.get("asset", "").upper() == ticker.upper()
                and c.get("call_type") == "stock_extreme"):
            return True
    return False


def score_asset(ticker: str) -> dict:
    """Score one ticker. Returns {ticker, fire, direction?, reason?, ...}."""
    ticker = ticker.upper()
    from octo_coinglass import get_stock_perp_signal

    price = _get_price(ticker)
    if not price or price <= 0:
        return {"ticker": ticker, "fire": False, "reason": "no live Robinhood price (halted?)"}

    sig = get_stock_perp_signal(ticker) or {}
    long_pct = sig.get("long_pct")
    oi_chg   = sig.get("oi_chg_24h_pct")
    if long_pct is None or oi_chg is None:
        return {"ticker": ticker, "fire": False, "reason": "no positioning/OI data"}

    trend = _get_trend_5d(ticker)
    if trend is None:
        return {"ticker": ticker, "fire": False, "reason": "no trend data"}

    reason_tail = f"5d={trend:+.1f}% long={long_pct:.0f}% oi_chg={oi_chg:+.1f}%"

    direction = None
    thesis = ""
    if trend >= TREND_PCT and long_pct <= SHORT_CROWD and oi_chg >= OI_CONFIRM:
        direction = "UP"
        thesis = "short squeeze: uptrend + short-heavy crowd + building OI"
    elif trend <= -TREND_PCT and long_pct >= LONG_CROWD and oi_chg >= OI_CONFIRM:
        direction = "DOWN"
        thesis = "long flush: downtrend + long-heavy crowd + building OI"

    if not direction:
        return {"ticker": ticker, "fire": False, "reason": f"no trend-aligned squeeze ({reason_tail})"}

    target = round(price * (1 + TARGET_PCT / 100) if direction == "UP"
                   else price * (1 - TARGET_PCT / 100), 2)
    return {
        "ticker":       ticker,
        "fire":         True,
        "direction":    direction,
        "price":        price,
        "target_price": target,
        "timeframe":    TIMEFRAME,
        "long_pct":     long_pct,
        "oi_chg_24h":   oi_chg,
        "trend_5d":     round(trend, 2),
        "note":         f"Stock extreme -- {thesis}. {reason_tail}, target {TARGET_PCT:.1f}%.",
        "signals":      {"long_pct": long_pct, "oi_chg_24h_pct": oi_chg, "trend_5d_pct": round(trend, 2)},
    }


def _post_text(r: dict) -> str:
    arrow = "^" if r["direction"] == "UP" else "v"
    bias  = "LONG" if r["direction"] == "UP" else "SHORT"
    return (
        f"{r['ticker']} {arrow} {bias}\n\n"
        f"Offside leverage set to unwind with the trend. "
        f"5d {r['trend_5d']:+.1f}%, crowd {r['long_pct']:.0f}% long, OI {r['oi_chg_24h']:+.1f}% 24h.\n"
        f"Target {TARGET_PCT:.1f}% / {TIMEFRAME}. Entry ${r['price']:,.2f}."
    )


def run_stock_extreme(assets: list = None, dry: bool = False) -> list:
    assets = [a.upper() for a in (assets or _STOCK_ASSETS)]
    fired  = []
    print(f"\n[StockExtreme] Scan | {datetime.now(timezone.utc).strftime('%H:%M UTC')}")
    from bitwarden import load_all_secrets
    load_all_secrets()

    # Fleet circuit breaker -- pause if this strategy is underwater on-chain.
    if not dry:
        try:
            from octo_calls import strategy_should_pause
            _pause, _reason = strategy_should_pause("stock_extreme")
            if _pause:
                print(f"[StockExtreme] CIRCUIT BREAKER: {_reason}. Skipping run.")
                return []
        except Exception as _e:
            print(f"[StockExtreme] breaker check failed (continuing): {_e}")

    for ticker in assets:
        if _has_open_call(ticker):
            print(f"[StockExtreme] {ticker}: open call exists -- skip")
            continue

        result = score_asset(ticker)
        if not result.get("fire"):
            print(f"[StockExtreme] {ticker}: PASS -- {result.get('reason')}")
            continue

        print(f"[StockExtreme] {ticker}: FIRE {result['direction']} | "
              f"5d={result['trend_5d']:+.1f}% long={result['long_pct']:.0f}% "
              f"oi_chg={result['oi_chg_24h']:+.1f}% | target=${result['target_price']:,.2f}")

        if dry:
            print("[StockExtreme] DRY RUN -- not recording or posting")
            fired.append(result)
            continue

        # Record on-chain
        try:
            from octo_calls import _load, _save
            calls = _load()
            now   = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            call  = {
                "id":                      max((c.get("id", 0) for c in calls), default=0) + 1,
                "call_type":               "stock_extreme",
                "asset":                   ticker,
                "direction":               result["direction"],
                "entry_price":             result["price"],
                "target_price":            result["target_price"],
                "timeframe":               result["timeframe"],
                "note":                    result["note"],
                "made_at":                 now,
                "resolved":                False,
                "outcome":                 None,
                "won":                     None,
                "exit_price":              None,
                "resolved_at":             None,
                "resolution_price_source": "Robinhood Chain",
                "signals":                 result["signals"],
                "market_snapshot":         {"price": result["price"],
                                            "long_pct": result["long_pct"],
                                            "oi_chg_24h_pct": result["oi_chg_24h"]},
                "post_mortem":             None,
            }
            calls.append(call)
            _save(calls)
            print(f"[StockExtreme] Call #{call['id']} recorded")

            try:
                from octo_oracle_registry import publish_prediction
                tx = publish_prediction(call)
                if tx:
                    all_calls = _load()
                    for c in all_calls:
                        if c["id"] == call["id"]:
                            c["tx_hash"] = tx
                            break
                    _save(all_calls)
                    print(f"[StockExtreme] On-chain: {tx[:16]}...")
            except Exception as _oc:
                print(f"[StockExtreme] On-chain skipped: {_oc}")
        except Exception as e:
            print(f"[StockExtreme] Record failed: {e}")
            continue

        # Post
        try:
            from octo_x_poster import queue_post, process_queue
            queue_post(_post_text(result), post_type="stock_extreme", priority=2)
            process_queue(max_posts=1, force=True)
        except Exception as e:
            print(f"[StockExtreme] Post failed: {e}")

        fired.append(result)

    return fired


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("assets", nargs="*", help="tickers (default: all)")
    ap.add_argument("--dry", action="store_true", help="score only, no record/post")
    args = ap.parse_args()
    targets = [a.upper() for a in args.assets if a.upper() in _STOCK_ASSETS] or _STOCK_ASSETS
    run_stock_extreme(targets, dry=args.dry)

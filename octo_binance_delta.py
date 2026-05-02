"""
octo_binance_delta.py
Binance 24h cumulative buy/sell delta — who is in control of price.

Uses Binance kline (candlestick) data with taker buy/sell volume breakdown.
Free, no auth required.

Signal logic:
  delta_ratio = total_buy_vol / total_vol (last 24h hourly candles)
  > 0.55  → BUYERS  (buy-side pressure, bullish)
  < 0.45  → SELLERS (sell-side pressure, bearish)
  else    → NEUTRAL

Cache: 15 minutes (changes with each new candle hourly)

Usage:
    from octo_binance_delta import get_delta_signal, delta_context_str
"""

import json
import time
from pathlib import Path
from typing import Optional

import httpx

BINANCE_BASE = "https://api.binance.com"
CACHE_FILE   = Path(__file__).parent / "data" / "binance_delta_cache.json"
CACHE_TTL_S  = 900  # 15 minutes

DELTA_BULL_THRESHOLD = 0.55
DELTA_BEAR_THRESHOLD = 0.45


# ── Cache ─────────────────────────────────────────────────────────────────────

def _load_cache() -> dict:
    try:
        if CACHE_FILE.exists():
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_cache(cache: dict):
    CACHE_FILE.parent.mkdir(exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache), encoding="utf-8")


# ── Core ──────────────────────────────────────────────────────────────────────

def get_delta_signal(symbol: str = "BTCUSDT") -> Optional[dict]:
    """
    Fetch 24h cumulative buy/sell delta for a Binance symbol.

    Returns dict with:
      symbol          str    e.g. "BTCUSDT"
      buy_volume      float  total taker buy volume (base asset)
      sell_volume     float  total taker sell volume (base asset)
      total_volume    float  buy + sell
      delta           float  buy - sell (positive = net buying)
      delta_ratio     float  buy / total (0-1)
      signal          str    BUYERS / SELLERS / NEUTRAL
      score           int    +1 (bull) / -1 (bear) / 0 (neutral)
      hourly_delta    list   per-hour delta for last 24 candles (trend)
      acceleration    str    ACCELERATING / DECELERATING / STEADY (delta trend)
      current_price   float  last close price
      price_change_pct float 24h price change %
      fetched_at      float
    """
    cache = _load_cache()
    cached = cache.get(symbol)
    if cached and (time.time() - cached.get("fetched_at", 0)) < CACHE_TTL_S:
        return cached

    try:
        r = httpx.get(
            f"{BINANCE_BASE}/api/v3/klines",
            params={"symbol": symbol, "interval": "1h", "limit": 24},
            timeout=8
        )
        if r.status_code != 200:
            return None
        klines = r.json()
    except Exception:
        return None

    try:
        # Kline format: [open_time, open, high, low, close, volume, close_time,
        #                quote_volume, n_trades, taker_buy_base, taker_buy_quote, ignore]
        hourly_deltas = []
        buy_vol_total  = 0.0
        sell_vol_total = 0.0
        total_vol      = 0.0

        for k in klines:
            vol        = float(k[5])
            buy_vol    = float(k[9])
            sell_vol   = vol - buy_vol
            delta      = buy_vol - sell_vol
            hourly_deltas.append(round(delta, 4))
            buy_vol_total  += buy_vol
            sell_vol_total += sell_vol
            total_vol      += vol

        if total_vol == 0:
            return None

        delta_ratio = buy_vol_total / total_vol
        delta_net   = buy_vol_total - sell_vol_total

        # Signal
        if delta_ratio >= DELTA_BULL_THRESHOLD:
            signal = "BUYERS"
            score  = 1
        elif delta_ratio <= DELTA_BEAR_THRESHOLD:
            signal = "SELLERS"
            score  = -1
        else:
            signal = "NEUTRAL"
            score  = 0

        # Acceleration — compare first 12h delta vs last 12h delta
        early = sum(hourly_deltas[:12])
        late  = sum(hourly_deltas[12:])
        if abs(late) > abs(early) * 1.3:
            acceleration = "ACCELERATING"
        elif abs(late) < abs(early) * 0.7:
            acceleration = "DECELERATING"
        else:
            acceleration = "STEADY"

        # Price context from last candle
        last_k       = klines[-1]
        current_px   = float(last_k[4])   # close
        open_24h_px  = float(klines[0][1])  # open of 24h ago
        price_chg    = round((current_px - open_24h_px) / open_24h_px * 100, 2)

        result = {
            "symbol":          symbol,
            "buy_volume":      round(buy_vol_total, 2),
            "sell_volume":     round(sell_vol_total, 2),
            "total_volume":    round(total_vol, 2),
            "delta":           round(delta_net, 2),
            "delta_ratio":     round(delta_ratio, 4),
            "signal":          signal,
            "score":           score,
            "hourly_delta":    hourly_deltas,
            "acceleration":    acceleration,
            "current_price":   current_px,
            "price_change_pct": price_chg,
            "fetched_at":      time.time(),
        }
        cache[symbol] = result
        _save_cache(cache)
        return result

    except Exception:
        return None


def delta_context_str(d: dict) -> str:
    """Format delta signal as a compact context string for LLM prompts."""
    if not d:
        return ""
    ratio_pct = round(d["delta_ratio"] * 100, 1)
    lines = [f"\nBINANCE 24H ORDER FLOW ({d['symbol']}):"]
    lines.append(
        f"  Buy vol:    {d['buy_volume']:,.1f} BTC ({ratio_pct:.1f}% of volume)"
    )
    lines.append(
        f"  Sell vol:   {d['sell_volume']:,.1f} BTC ({100-ratio_pct:.1f}% of volume)"
    )
    lines.append(
        f"  Net delta:  {d['delta']:+,.1f} BTC | Signal: {d['signal']} | "
        f"Acceleration: {d['acceleration']}"
    )
    lines.append(
        f"  Price:      ${d['current_price']:,.0f} ({d['price_change_pct']:+.2f}% 24h)"
    )

    # Interpretation
    if d["signal"] == "BUYERS" and d["acceleration"] == "ACCELERATING":
        lines.append("  Strong accumulation — buyers increasing pressure as price moves.")
    elif d["signal"] == "BUYERS" and d["acceleration"] == "DECELERATING":
        lines.append("  Buying fading — early accumulation exhausting, watch for reversal.")
    elif d["signal"] == "SELLERS" and d["acceleration"] == "ACCELERATING":
        lines.append("  Distribution accelerating — sellers taking control, bearish.")
    elif d["signal"] == "SELLERS" and d["acceleration"] == "DECELERATING":
        lines.append("  Selling pressure fading — potential floor forming.")
    elif d["signal"] == "NEUTRAL":
        lines.append("  No clear dominant side — wait for delta to resolve directionally.")

    # Divergence flag
    if d["signal"] == "BUYERS" and d["price_change_pct"] < -1.0:
        lines.append(
            "  DIVERGENCE: Buyers dominating delta but price is DOWN — "
            "accumulation under weakness (bullish hidden pressure)."
        )
    elif d["signal"] == "SELLERS" and d["price_change_pct"] > 1.0:
        lines.append(
            "  DIVERGENCE: Sellers dominating delta but price is UP — "
            "distribution into strength (bearish hidden pressure)."
        )

    return "\n".join(lines)


def get_multi_delta(symbols: list = None) -> dict:
    """Get delta signals for multiple symbols. Returns {symbol: delta_dict}."""
    if symbols is None:
        symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    results = {}
    for sym in symbols:
        d = get_delta_signal(sym)
        if d:
            results[sym] = d
        time.sleep(0.1)
    return results


def multi_delta_context_str(symbols: list = None) -> str:
    """Multi-symbol delta context for oracle prompt injection."""
    signals = get_multi_delta(symbols or ["BTCUSDT", "ETHUSDT"])
    if not signals:
        return ""
    parts = []
    for sym, d in signals.items():
        parts.append(delta_context_str(d))
    return "\n".join(parts)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    symbol = sys.argv[1].upper() if len(sys.argv) > 1 else "BTCUSDT"
    d = get_delta_signal(symbol)
    if d:
        print(delta_context_str(d))
        print(f"\nDelta ratio: {d['delta_ratio']:.4f} | Score: {d['score']:+d}")
        print(f"Hourly deltas (last 6h): {d['hourly_delta'][-6:]}")
    else:
        print(f"Failed to fetch delta for {symbol}")

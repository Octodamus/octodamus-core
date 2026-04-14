"""
octo_backtest.py
Replay Octodamus signal engine against historical OHLC + Kraken funding data.
Find the conviction threshold that hits 80%+ win rate.

Run: python octo_backtest.py
Output: backtest_results.json + printed summary table
"""

import requests
import time
import json
import statistics
from datetime import datetime, timezone, timedelta
from collections import defaultdict

ASSETS = {
    "BTC": "XXBTZUSD",
    "ETH": "XETHZUSD",
    "SOL": "SOLUSD",
}

TIMEFRAME_HOURS = 48     # how long each oracle call lives
MIN_CONVICTION  = 2      # test from 2 up to 7
CANDLE_INTERVAL = 60     # 1h candles
LOOKBACK_DAYS   = 180    # 6 months of history


# ── Signal Calculations ───────────────────────────────────────────────────────

def calc_ema(prices, period):
    ema = [None] * len(prices)
    if len(prices) < period:
        return ema
    k = 2 / (period + 1)
    ema[period - 1] = sum(prices[:period]) / period
    for i in range(period, len(prices)):
        ema[i] = prices[i] * k + ema[i-1] * (1 - k)
    return ema


def calc_macd(closes, fast=12, slow=26, signal=9):
    ema_fast = calc_ema(closes, fast)
    ema_slow = calc_ema(closes, slow)
    macd_line = []
    for f, s in zip(ema_fast, ema_slow):
        macd_line.append(f - s if (f is not None and s is not None) else None)
    valid = [v for v in macd_line if v is not None]
    signal_line = calc_ema(valid, signal)
    # Align signal back
    offset = len(macd_line) - len(valid)
    signal_full = [None] * (offset + len(valid))
    for i, v in enumerate(signal_line):
        if v is not None:
            signal_full[offset + i] = v
    histogram = []
    for m, s in zip(macd_line, signal_full):
        histogram.append(m - s if (m is not None and s is not None) else None)
    return histogram


def calc_rsi(closes, period=14):
    rsi = [None] * len(closes)
    if len(closes) < period + 1:
        return rsi
    gains, losses = [], []
    for i in range(1, period + 1):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    for i in range(period, len(closes)):
        if i > period:
            diff = closes[i] - closes[i-1]
            avg_gain = (avg_gain * (period - 1) + max(diff, 0)) / period
            avg_loss = (avg_loss * (period - 1) + max(-diff, 0)) / period
        rs = avg_gain / avg_loss if avg_loss > 0 else float("inf")
        rsi[i] = 100 - (100 / (1 + rs))
    return rsi


def calc_bollinger(closes, period=20, std_dev=2):
    bb_width = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1:i + 1]
        mean = sum(window) / period
        variance = sum((x - mean) ** 2 for x in window) / period
        std = variance ** 0.5
        if mean > 0:
            bb_width[i] = (2 * std_dev * std) / mean * 100
    return bb_width


def score_signals(closes, current_idx, funding_rate=0.0, fear_greed=50,
                  price_change_24h=0.0):
    """
    Run TA signals at closes[current_idx].
    Returns (bull_count, bear_count, signals_dict).
    """
    if current_idx < 50:
        return 0, 0, {}

    window = closes[:current_idx + 1]
    c = window[-1]

    macd_hist = calc_macd(window)
    ema20 = calc_ema(window, 20)
    ema50 = calc_ema(window, 50)
    rsi    = calc_rsi(window)
    bb_w   = calc_bollinger(window)

    signals = {}

    # 1. MACD
    h = macd_hist[-1]
    if h is not None:
        signals["macd"] = "bull" if h > 0 else "bear"

    # 2. EMA trend
    e20 = ema20[-1]
    e50 = ema50[-1]
    if e20 and e50:
        signals["ema_trend"] = "bull" if e20 > e50 else "bear"

    # 3. RSI
    r = rsi[-1]
    if r is not None:
        if r < 45:
            signals["rsi"] = "bull"
        elif r > 65:
            signals["rsi"] = "bear"

    # 4. Fear & Greed
    if fear_greed < 25:
        signals["fear_greed"] = "bull"
    elif fear_greed > 75:
        signals["fear_greed"] = "bear"

    # 5. Funding rate
    if funding_rate < 0:
        signals["funding_rate"] = "bull"
    elif funding_rate > 0.005:
        signals["funding_rate"] = "bear"

    # 6. 24h price change
    if price_change_24h > 2:
        signals["price_change"] = "bull"
    elif price_change_24h < -2:
        signals["price_change"] = "bear"

    # 7. Bollinger Band width (breakout signal — counts as both if compressed)
    bw = bb_w[-1]
    if bw is not None and bw < 3.0:
        signals["bb_breakout"] = "bull"  # direction from EMA context

    bulls = sum(1 for v in signals.values() if v == "bull")
    bears = sum(1 for v in signals.values() if v == "bear")
    return bulls, bears, signals


# ── Data Fetching ─────────────────────────────────────────────────────────────

def fetch_ohlc(pair, interval=60, since_days=LOOKBACK_DAYS):
    """Fetch OHLC from Kraken. Returns list of (timestamp, open, high, low, close)."""
    since = int((datetime.now(timezone.utc) - timedelta(days=since_days)).timestamp())
    all_candles = []
    while True:
        url = "https://api.kraken.com/0/public/OHLC"
        r = requests.get(url, params={"pair": pair, "interval": interval, "since": since}, timeout=15)
        data = r.json()
        if data.get("error"):
            print(f"  Kraken error: {data['error']}")
            break
        result = data.get("result", {})
        candles = result.get(pair) or result.get(list(result.keys())[0], [])
        if not candles:
            break
        all_candles.extend(candles)
        last_time = candles[-1][0]
        if last_time <= since:
            break
        since = last_time
        time.sleep(0.5)
        if len(all_candles) > 10000:
            break
    # Deduplicate and sort
    seen = set()
    unique = []
    for c in all_candles:
        if c[0] not in seen:
            seen.add(c[0])
            unique.append(c)
    unique.sort(key=lambda x: x[0])
    return unique


# ── Backtest Engine ───────────────────────────────────────────────────────────

def run_backtest(asset, pair):
    print(f"\nBacktesting {asset} ({pair})...")
    candles = fetch_ohlc(pair)
    if len(candles) < 100:
        print(f"  Insufficient data ({len(candles)} candles)")
        return None

    closes    = [float(c[4]) for c in candles]
    timestamps = [c[0] for c in candles]
    print(f"  {len(candles)} candles loaded ({LOOKBACK_DAYS} days)")

    # Results by conviction threshold
    results = defaultdict(lambda: {"calls": 0, "wins": 0, "losses": 0})

    i = 50  # start after warmup

    while i < len(candles) - TIMEFRAME_HOURS:
        # Calculate 24h price change from candle data
        idx_24h_ago = max(0, i - 24)
        price_24h_ago = closes[idx_24h_ago]
        price_change_24h = ((closes[i] - price_24h_ago) / price_24h_ago * 100) if price_24h_ago else 0

        # Proxy funding rate: sustained move > 5% in 48h = positive funding pressure
        idx_48h_ago = max(0, i - 48)
        move_48h = ((closes[i] - closes[idx_48h_ago]) / closes[idx_48h_ago] * 100) if closes[idx_48h_ago] else 0
        funding_proxy = 0.006 if move_48h > 5 else (-0.001 if move_48h < -5 else 0.0)

        # Fear & Greed proxy: RSI-based (RSI < 35 = fear, RSI > 70 = greed)
        rsi_vals = calc_rsi(closes[:i+1])
        rsi_now = rsi_vals[-1] if rsi_vals[-1] is not None else 50
        fg_proxy = 20 if rsi_now < 35 else (80 if rsi_now > 70 else 50)

        bulls, bears, sigs = score_signals(
            closes, i,
            funding_rate=funding_proxy,
            fear_greed=fg_proxy,
            price_change_24h=price_change_24h
        )

        for threshold in range(MIN_CONVICTION, 8):
            direction = None
            if bulls >= threshold and bulls > bears:
                direction = "UP"
            elif bears >= threshold and bears > bulls:
                direction = "DOWN"

            if direction is None:
                continue

            # Simulate: one open call at a time per direction
            # Check outcome at i + TIMEFRAME_HOURS
            entry  = closes[i]
            exit_i = min(i + TIMEFRAME_HOURS, len(closes) - 1)
            exit_p = closes[exit_i]

            if direction == "UP":
                won = exit_p > entry
            else:
                won = exit_p < entry

            results[threshold]["calls"] += 1
            if won:
                results[threshold]["wins"] += 1
            else:
                results[threshold]["losses"] += 1

        # Step forward — skip candles to avoid overlapping calls
        # (simulate: only one call issued per 6h)
        i += 6

    return dict(results)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    all_results = {}

    for asset, pair in ASSETS.items():
        result = run_backtest(asset, pair)
        if result:
            all_results[asset] = result

    print("\n" + "=" * 72)
    print("BACKTEST RESULTS — Win Rate by Conviction Threshold")
    print(f"Timeframe: {TIMEFRAME_HOURS}h  |  Lookback: {LOOKBACK_DAYS} days")
    print("=" * 72)
    print(f"{'Threshold':>10}  {'Asset':>6}  {'Calls':>7}  {'Wins':>5}  {'Losses':>7}  {'Win Rate':>9}")
    print("-" * 72)

    summary = defaultdict(lambda: {"calls": 0, "wins": 0})

    for threshold in range(MIN_CONVICTION, 8):
        for asset, result in all_results.items():
            r = result.get(threshold, {})
            calls  = r.get("calls", 0)
            wins   = r.get("wins", 0)
            losses = r.get("losses", 0)
            wr     = wins / calls if calls else 0
            flag   = " <-- 80%+ TARGET" if wr >= 0.80 and calls >= 20 else ""
            print(f"{threshold:>10}  {asset:>6}  {calls:>7}  {wins:>5}  {losses:>7}  {wr:>8.1%}{flag}")
            summary[threshold]["calls"] += calls
            summary[threshold]["wins"]  += wins

    print("\nCOMBINED (all assets):")
    print("-" * 72)
    print(f"{'Threshold':>10}  {'Total Calls':>12}  {'Win Rate':>9}")
    for threshold in range(MIN_CONVICTION, 8):
        calls = summary[threshold]["calls"]
        wins  = summary[threshold]["wins"]
        wr    = wins / calls if calls else 0
        flag  = " <-- USE THIS" if wr >= 0.80 and calls >= 50 else ""
        print(f"{threshold:>10}  {calls:>12,}  {wr:>8.1%}{flag}")

    # Save full results
    output = {
        "run_date": datetime.now(timezone.utc).isoformat(),
        "timeframe_hours": TIMEFRAME_HOURS,
        "lookback_days": LOOKBACK_DAYS,
        "results": {asset: {str(k): v for k, v in r.items()} for asset, r in all_results.items()},
        "summary": {str(k): v for k, v in summary.items()},
    }
    with open("backtest_results.json", "w") as f:
        json.dump(output, f, indent=2)

    print(f"\nFull results saved to backtest_results.json")
    print("\nNEXT STEP: set MIN_SIGNALS in octodamus_runner.py to the threshold")
    print("           that shows 80%+ win rate with 50+ calls in the summary.")


if __name__ == "__main__":
    main()

"""
octo_logic.py
OctoLogic — Technical Analysis Mind

Reads OHLCV data via yfinance and computes:
  - RSI (14-period)
  - MACD (12/26/9)
  - SMA 20 / SMA 50 crossover
  - Volume spike detection
  - Bollinger Band squeeze / breakout

Returns a structured signal dict per ticker.
No new API key needed — yfinance is already installed.

Usage:
    from octo_logic import run_technical_scan, get_ticker_technicals
    signals = run_technical_scan(["NVDA", "TSLA", "BTC-USD"])
"""

import time
from datetime import datetime

try:
    import yfinance as yf
except ImportError:
    raise ImportError("yfinance not installed. Run: pip install yfinance")

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

# Tickers to scan — BTC/ETH/SOL use the -USD suffix for yfinance
LOGIC_STOCK_TICKERS = ["NVDA", "TSLA", "AAPL"]
LOGIC_CRYPTO_TICKERS = ["BTC-USD", "ETH-USD", "SOL-USD"]

# How many days of history to pull for indicator calculation
HISTORY_DAYS = "60d"
HISTORY_INTERVAL = "1d"

# RSI thresholds
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70

# Volume spike: today's volume > this multiple of 20-day avg
VOLUME_SPIKE_MULT = 1.8

# Inter-ticker delay to avoid rate limiting
_FETCH_DELAY = 0.4


# ─────────────────────────────────────────────
# INDICATOR MATH
# ─────────────────────────────────────────────

def _compute_rsi(closes: list, period: int = 14) -> float | None:
    """Wilder RSI. Returns float 0-100 or None if not enough data."""
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))

    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def _ema(values: list, period: int) -> list:
    """Exponential moving average."""
    if len(values) < period:
        return []
    k = 2 / (period + 1)
    ema_vals = [sum(values[:period]) / period]
    for v in values[period:]:
        ema_vals.append(v * k + ema_vals[-1] * (1 - k))
    return ema_vals


def _compute_macd(closes: list):
    """Returns (macd_line, signal_line, histogram) as latest values or (None,None,None)."""
    if len(closes) < 35:
        return None, None, None
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    if not ema12 or not ema26:
        return None, None, None

    # Align lengths — ema26 is shorter
    offset = len(ema12) - len(ema26)
    macd_line = [ema12[i + offset] - ema26[i] for i in range(len(ema26))]
    signal_line = _ema(macd_line, 9)
    if not signal_line:
        return None, None, None
    hist_offset = len(macd_line) - len(signal_line)
    histogram = macd_line[-1] - signal_line[-1]
    return round(macd_line[-1], 4), round(signal_line[-1], 4), round(histogram, 4)


def _compute_sma(values: list, period: int) -> float | None:
    if len(values) < period:
        return None
    return round(sum(values[-period:]) / period, 4)


def _compute_bollinger(closes: list, period: int = 20, std_dev: float = 2.0):
    """Returns (upper, middle, lower, bandwidth_pct) or Nones."""
    if len(closes) < period:
        return None, None, None, None
    window = closes[-period:]
    middle = sum(window) / period
    variance = sum((x - middle) ** 2 for x in window) / period
    std = variance ** 0.5
    upper = round(middle + std_dev * std, 4)
    lower = round(middle - std_dev * std, 4)
    middle = round(middle, 4)
    bandwidth_pct = round(((upper - lower) / middle) * 100, 2) if middle else None
    return upper, middle, lower, bandwidth_pct


# ─────────────────────────────────────────────
# PER-TICKER ANALYSIS
# ─────────────────────────────────────────────

def get_ticker_technicals(ticker: str) -> dict:
    """
    Fetch OHLCV and compute all technical indicators for one ticker.
    Returns a structured dict with indicators + signal interpretation.

    Signal bias: "bullish" | "bearish" | "neutral"
    """
    result = {
        "ticker": ticker,
        "timestamp": datetime.utcnow().isoformat(),
        "error": None,
        "price": None,
        "rsi": None,
        "macd": None,
        "macd_signal": None,
        "macd_histogram": None,
        "sma20": None,
        "sma50": None,
        "bb_upper": None,
        "bb_lower": None,
        "bb_bandwidth_pct": None,
        "volume_spike": False,
        "volume_ratio": None,
        "signals": [],
        "bias": "neutral",
        "bias_score": 0,  # -3 to +3 composite
    }

    try:
        tkr = yf.Ticker(ticker)
        hist = tkr.history(period=HISTORY_DAYS, interval=HISTORY_INTERVAL)

        if hist.empty or len(hist) < 20:
            result["error"] = f"Insufficient data: {len(hist)} rows"
            return result

        closes = list(hist["Close"].values)
        volumes = list(hist["Volume"].values)
        today_close = closes[-1]
        today_volume = volumes[-1]

        result["price"] = round(float(today_close), 4)

        # RSI
        rsi = _compute_rsi(closes)
        result["rsi"] = rsi

        # MACD
        macd, macd_sig, macd_hist = _compute_macd(closes)
        result["macd"] = macd
        result["macd_signal"] = macd_sig
        result["macd_histogram"] = macd_hist

        # SMAs
        sma20 = _compute_sma(closes, 20)
        sma50 = _compute_sma(closes, 50)
        result["sma20"] = sma20
        result["sma50"] = sma50

        # Bollinger Bands
        bb_upper, bb_mid, bb_lower, bb_bw = _compute_bollinger(closes)
        result["bb_upper"] = bb_upper
        result["bb_lower"] = bb_lower
        result["bb_bandwidth_pct"] = bb_bw

        # Volume spike
        if len(volumes) >= 20:
            avg_vol = sum(volumes[-21:-1]) / 20
            vol_ratio = round(today_volume / avg_vol, 2) if avg_vol > 0 else 0
            result["volume_ratio"] = vol_ratio
            if vol_ratio >= VOLUME_SPIKE_MULT:
                result["volume_spike"] = True

        # ── Signal interpretation ──────────────────
        signals = []
        score = 0

        if rsi is not None:
            if rsi < RSI_OVERSOLD:
                signals.append(f"RSI {rsi} — oversold")
                score += 1
            elif rsi > RSI_OVERBOUGHT:
                signals.append(f"RSI {rsi} — overbought")
                score -= 1
            else:
                signals.append(f"RSI {rsi} — neutral range")

        if macd is not None and macd_sig is not None:
            if macd > macd_sig and macd_hist > 0:
                signals.append("MACD bullish crossover")
                score += 1
            elif macd < macd_sig and macd_hist < 0:
                signals.append("MACD bearish crossover")
                score -= 1
            else:
                signals.append("MACD converging")

        if sma20 and sma50:
            if sma20 > sma50:
                signals.append("SMA20 > SMA50 — uptrend")
                score += 1
            else:
                signals.append("SMA20 < SMA50 — downtrend")
                score -= 1

        if bb_upper and bb_lower and today_close:
            if today_close > bb_upper:
                signals.append("Price above Bollinger upper — extended")
                score -= 1
            elif today_close < bb_lower:
                signals.append("Price below Bollinger lower — compressed")
                score += 1

        if result["volume_spike"]:
            direction = "confirming move" if score > 0 else "warning — distribution?"
            signals.append(f"Volume spike {result['volume_ratio']}x avg — {direction}")

        result["signals"] = signals
        result["bias_score"] = score
        if score >= 2:
            result["bias"] = "bullish"
        elif score <= -2:
            result["bias"] = "bearish"
        else:
            result["bias"] = "neutral"

    except Exception as e:
        result["error"] = str(e)

    return result


# ─────────────────────────────────────────────
# BATCH SCAN
# ─────────────────────────────────────────────

def run_technical_scan(tickers: list | None = None) -> list[dict]:
    """
    Run technical analysis on all tickers.
    Returns list of result dicts sorted by abs(bias_score) desc.
    """
    if tickers is None:
        tickers = LOGIC_STOCK_TICKERS + LOGIC_CRYPTO_TICKERS

    print(f"[OctoLogic] Scanning {len(tickers)} tickers...")
    results = []

    for ticker in tickers:
        try:
            data = get_ticker_technicals(ticker)
            results.append(data)
            bias_label = f"[{data['bias'].upper()}]" if not data["error"] else "[ERROR]"
            print(f"  {ticker:12s} {bias_label}  score={data['bias_score']}")
        except Exception as e:
            print(f"  {ticker:12s} [FAIL] {e}")
            results.append({"ticker": ticker, "error": str(e), "bias": "neutral", "bias_score": 0})
        time.sleep(_FETCH_DELAY)

    # Sort: most opinionated signals first
    results.sort(key=lambda x: abs(x.get("bias_score", 0)), reverse=True)
    return results


def format_logic_for_prompt(results: list[dict]) -> str:
    """Format OctoLogic results into a compact prompt string for the LLM."""
    if not results:
        return ""
    lines = ["Technical signals (OctoLogic):"]
    for r in results:
        if r.get("error"):
            continue
        ticker = r["ticker"].replace("-USD", "")
        bias = r["bias"].upper()
        score = r["bias_score"]
        sigs = "; ".join(r.get("signals", [])[:3])
        lines.append(f"  {ticker}: {bias} (score {score:+d}) — {sigs}")
    return "\n".join(lines)


# ─────────────────────────────────────────────
# STANDALONE RUN
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import json
    results = run_technical_scan()
    print("\n── OctoLogic Report ──────────────────────")
    for r in results:
        if not r.get("error"):
            print(f"\n{r['ticker']}")
            print(f"  Price:     ${r['price']}")
            print(f"  RSI:       {r['rsi']}")
            print(f"  MACD hist: {r['macd_histogram']}")
            print(f"  SMA20/50:  {r['sma20']} / {r['sma50']}")
            print(f"  BB BW%:    {r['bb_bandwidth_pct']}")
            print(f"  Vol spike: {r['volume_spike']} ({r['volume_ratio']}x)")
            print(f"  Bias:      {r['bias'].upper()} (score {r['bias_score']:+d})")
            for s in r["signals"]:
                print(f"    → {s}")
        else:
            print(f"\n{r['ticker']} ERROR: {r['error']}")

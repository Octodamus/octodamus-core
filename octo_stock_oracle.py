"""
octo_stock_oracle.py — Stock Oracle Signals for Octodamus SmartCall

8-signal consensus for NVDA, AAPL, TSLA (and any other equity).
STRONG requires 6/8 signals (75% threshold — same conviction bar as 9/11 crypto).

Signals:
  1. EMA trend          (price vs 20/50 day EMA)
  2. RSI                (oversold/overbought)
  3. MACD               (momentum)
  4. 52-week position   (near lows = UP bias, near highs = DOWN bias)
  5. Earnings momentum  (beat/miss streak — Finnhub)
  6. Analyst consensus  (upgrade/downgrade trend — Finnhub)
  7. News sentiment     (bullish/bearish news ratio — Finnhub)
  8. Fear & Greed       (market context — passed in from SmartCall)

SpaceX IPO monitor is separate — private company, no price calls possible.
"""

import json
import statistics
import time
from pathlib import Path


SECRETS_FILE = Path(r"C:\Users\walli\octodamus\.octo_secrets")

_VALID_STOCK_TICKERS = {"NVDA", "AAPL", "TSLA", "MSFT", "AMZN", "META", "GOOGL"}

# Cache OHLC data (1h TTL — stocks only update during market hours)
_ohlc_cache: dict = {}
_OHLC_TTL = 3600


def _get_finnhub_key() -> str:
    try:
        s = json.loads(SECRETS_FILE.read_text(encoding="utf-8"))
        return s.get("secrets", s).get("FINNHUB_API_KEY", "")
    except Exception:
        return ""


def _fetch_ohlc(ticker: str) -> list[float]:
    """Fetch 90 days of daily closes via yfinance (free, no API key needed)."""
    now = time.monotonic()
    if ticker in _ohlc_cache and now - _ohlc_cache[ticker]["ts"] < _OHLC_TTL:
        return _ohlc_cache[ticker]["closes"]

    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period="3mo")
        if hist.empty:
            return []
        closes = [float(x) for x in hist["Close"].tolist() if x]
        _ohlc_cache[ticker] = {"closes": closes, "ts": now}
        return closes
    except Exception:
        return []


def _ema(data: list, period: int) -> float:
    k = 2 / (period + 1)
    e = data[0]
    for p in data[1:]:
        e = p * k + e * (1 - k)
    return round(e, 4)


def _rsi(closes: list, period: int = 14) -> float:
    gains, losses = [], []
    for i in range(1, period + 1):
        d = closes[-i] - closes[-i - 1]
        (gains if d > 0 else losses).append(abs(d))
    avg_g = sum(gains) / period if gains else 0
    avg_l = sum(losses) / period if losses else 0.001
    return round(100 - 100 / (1 + avg_g / avg_l), 1)


def get_stock_technicals(ticker: str) -> dict:
    """EMA trend, RSI, MACD, 52-week position from Finnhub OHLC."""
    closes = _fetch_ohlc(ticker)
    if len(closes) < 26:
        return {}

    ema20 = _ema(closes, 20)
    ema50 = _ema(closes, min(50, len(closes)))
    macd  = round(_ema(closes, 12) - _ema(closes, 26), 4)
    rsi   = _rsi(closes)
    high_52w = max(closes[-252:]) if len(closes) >= 252 else max(closes)
    low_52w  = min(closes[-252:]) if len(closes) >= 252 else min(closes)
    price    = closes[-1]
    pos_52w  = round((price - low_52w) / (high_52w - low_52w + 0.01) * 100, 1)

    return {
        "price":    price,
        "ema20":    ema20,
        "ema50":    ema50,
        "macd":     macd,
        "rsi":      rsi,
        "pos_52w":  pos_52w,   # 0-100: 0=at low, 100=at high
        "high_52w": high_52w,
        "low_52w":  low_52w,
    }


def stock_directional_call(ticker: str, price: float, ta: dict,
                            fng: int = 50) -> str:
    """
    Run the 8-signal stock consensus. Returns string matching SmartCall pattern:
    'DIRECTION: STRONG UP — 7/8 ...' or 'DIRECTION: DOWN ...' etc.
    """
    if ticker.upper() not in _VALID_STOCK_TICKERS:
        return f"DIRECTION: SKIP — {ticker} not in stock oracle list"

    bull = 0
    bear = 0
    notes = []

    # ── Signal 1: EMA trend ───────────────────────────────────────────────────
    if ta.get("ema20") and ta.get("ema50"):
        if ta["ema20"] > ta["ema50"]:
            bull += 1; notes.append("EMA bull")
        else:
            bear += 1; notes.append("EMA bear")

    # ── Signal 2: RSI ─────────────────────────────────────────────────────────
    rsi = ta.get("rsi", 50)
    if rsi < 35:
        bull += 1; notes.append(f"RSI oversold {rsi}")
    elif rsi > 68:
        bear += 1; notes.append(f"RSI overbought {rsi}")

    # ── Signal 3: MACD ────────────────────────────────────────────────────────
    if ta.get("macd", 0) > 0:
        bull += 1; notes.append("MACD bull")
    elif ta.get("macd", 0) < 0:
        bear += 1; notes.append("MACD bear")

    # ── Signal 4: 52-week position ────────────────────────────────────────────
    pos = ta.get("pos_52w", 50)
    if pos < 20:
        bull += 1; notes.append(f"Near 52w low ({pos:.0f}%)")
    elif pos > 85:
        bear += 1; notes.append(f"Near 52w high ({pos:.0f}%)")

    # ── Signals 5-7: Finnhub (earnings, analyst, news sentiment) ─────────────
    try:
        from octo_finnhub import get_earnings_surprise, get_news_sentiment
        import httpx

        key = _get_finnhub_key()

        # Signal 5: Earnings momentum
        earnings = get_earnings_surprise(ticker)
        if earnings.get("available"):
            qs = earnings.get("quarters", [])
            beats = sum(1 for q in qs[:4] if q.get("beat"))
            misses = len(qs[:4]) - beats
            if beats >= 3:
                bull += 1; notes.append(f"Earnings {beats}/4 beats")
            elif misses >= 3:
                bear += 1; notes.append(f"Earnings {misses}/4 misses")

        # Signal 6: Analyst consensus
        if key:
            r = httpx.get(
                "https://finnhub.io/api/v1/stock/recommendation",
                params={"symbol": ticker, "token": key}, timeout=8
            )
            recs = r.json()
            if recs and isinstance(recs, list):
                latest = recs[0]
                strong_buy = latest.get("strongBuy", 0)
                buy        = latest.get("buy", 0)
                strong_sell = latest.get("strongSell", 0)
                sell       = latest.get("sell", 0)
                bulls_an = strong_buy + buy
                bears_an = strong_sell + sell
                if bulls_an > bears_an * 2:
                    bull += 1; notes.append(f"Analysts {bulls_an}B/{bears_an}S")
                elif bears_an > bulls_an * 2:
                    bear += 1; notes.append(f"Analysts {bears_an}S/{bulls_an}B")

        # Signal 7: News sentiment
        sentiment = get_news_sentiment(ticker)
        if sentiment.get("available"):
            score = sentiment.get("score", 0)
            if score > 0.2:
                bull += 1; notes.append(f"News sentiment +{score:.2f}")
            elif score < -0.2:
                bear += 1; notes.append(f"News sentiment {score:.2f}")

    except Exception:
        pass

    # ── Signal 8: Fear & Greed ────────────────────────────────────────────────
    if fng < 25:
        bull += 1; notes.append(f"F&G fear {fng}")
    elif fng > 75:
        bear += 1; notes.append(f"F&G greed {fng}")

    total = 8
    summary = f"{bull}B/{bear}Br"

    if bull >= 6:
        return f"DIRECTION: STRONG UP — {bull}/{total} signals bullish. {summary}. {'; '.join(notes[:3])}"
    elif bull >= 5:
        return f"DIRECTION: UP — {bull}/{total} signals bullish. {summary}"
    elif bear >= 6:
        return f"DIRECTION: STRONG DOWN — {bear}/{total} signals bearish. {summary}. {'; '.join(notes[:3])}"
    elif bear >= 5:
        return f"DIRECTION: DOWN — {bear}/{total} signals bearish. {summary}"
    elif bull == bear:
        return f"DIRECTION: RANGE — {bull}/{total} each way. {summary}"
    elif bull > bear:
        return f"DIRECTION: LEANING UP — {bull}/{total} bullish. {summary}"
    else:
        return f"DIRECTION: LEANING DOWN — {bear}/{total} bearish. {summary}"

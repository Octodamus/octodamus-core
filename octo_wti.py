"""
octo_wti.py — WTI Crude Oil Signal Module for Octodamus Oracle

8-signal commodity consensus. STRONG = 6/8 (75% threshold).

Signals:
  1. EMA trend          (yfinance CL=F daily)
  2. RSI                (oversold/overbought)
  3. MACD               (momentum)
  4. 52-week position   (near lows = bullish, near highs = bearish)
  5. COT positioning    (CFTC disaggregated — managed money net long/short)
  6. Term structure     (backwardation = bullish, contango = bearish)
  7. DXY direction      (FRED — strong dollar = bearish oil)
  8. News sentiment     (NewsAPI)

COT source: https://www.cftc.gov/dea/newcot/c_disagg.txt (updated weekly Fridays)
EIA inventory: requires EIA API key (optional enhancement)
"""

import csv
import json
import time
from pathlib import Path

SECRETS_FILE = Path(r"C:\Users\walli\octodamus\.octo_secrets")
_cache: dict = {}
_OHLC_TTL = 3600   # 1h
_COT_TTL  = 86400  # 24h (COT is weekly)


def _secrets() -> dict:
    try:
        s = json.loads(SECRETS_FILE.read_text(encoding="utf-8"))
        return s.get("secrets", s)
    except Exception:
        return {}


# ── 1–4: Technical Analysis via yfinance ──────────────────────────────────────

def _fetch_wti_ohlc() -> list[float]:
    now = time.monotonic()
    if "ohlc" in _cache and now - _cache["ohlc"]["ts"] < _OHLC_TTL:
        return _cache["ohlc"]["closes"]
    try:
        import yfinance as yf
        hist = yf.Ticker("CL=F").history(period="6mo")
        if hist.empty:
            return []
        closes = [float(x) for x in hist["Close"].tolist() if x]
        _cache["ohlc"] = {"closes": closes, "ts": now}
        return closes
    except Exception:
        return {}


def _ema(data: list, period: int) -> float:
    k = 2 / (period + 1)
    e = data[0]
    for p in data[1:]:
        e = p * k + e * (1 - k)
    return round(e, 4)


def get_wti_technicals() -> dict:
    closes = _fetch_wti_ohlc()
    if len(closes) < 26:
        return {}
    ema20 = _ema(closes, 20)
    ema50 = _ema(closes, min(50, len(closes)))
    macd  = round(_ema(closes, 12) - _ema(closes, 26), 4)
    gains, losses = [], []
    for i in range(1, 15):
        d = closes[-i] - closes[-i - 1]
        (gains if d > 0 else losses).append(abs(d))
    avg_g = sum(gains) / 14 if gains else 0
    avg_l = sum(losses) / 14 if losses else 0.001
    rsi = round(100 - 100 / (1 + avg_g / avg_l), 1)
    high_52w = max(closes[-252:]) if len(closes) >= 252 else max(closes)
    low_52w  = min(closes[-252:]) if len(closes) >= 252 else min(closes)
    price = closes[-1]
    pos_52w = round((price - low_52w) / (high_52w - low_52w + 0.01) * 100, 1)
    return {
        "price": price, "ema20": ema20, "ema50": ema50,
        "macd": macd, "rsi": rsi, "pos_52w": pos_52w,
        "high_52w": high_52w, "low_52w": low_52w,
    }


# ── 5: COT Positioning (CFTC disaggregated, weekly) ───────────────────────────

def get_wti_cot() -> dict:
    """Managed money net long/short from CFTC weekly disaggregated report."""
    now = time.monotonic()
    if "cot" in _cache and now - _cache["cot"]["ts"] < _COT_TTL:
        return _cache["cot"]["data"]
    try:
        import httpx
        r = httpx.get("https://www.cftc.gov/dea/newcot/c_disagg.txt", timeout=15)
        if r.status_code != 200:
            return {}
        crude_rows = [
            line for line in r.text.splitlines()
            if "CRUDE" in line.upper() and "LIGHT" in line.upper()
        ]
        if not crude_rows:
            return {}
        row = list(csv.reader([crude_rows[-1]]))[0]
        # Disaggregated format columns:
        # 0=market, 2=date, 7=OI
        # 13=MM_long, 14=MM_short (managed money = speculative hedge funds)
        # 9=prod_merc_short (commercial shorts = oil producers hedging)
        oi       = float((row[7]  if len(row) > 7  else "1").replace(",", "") or 1)
        mm_long  = float((row[13] if len(row) > 13 else "0").replace(",", "") or 0)
        mm_short = float((row[14] if len(row) > 14 else "0").replace(",", "") or 0)
        net      = mm_long - mm_short
        net_pct  = net / oi * 100

        # Signal: speculative crowding is contrarian
        if net_pct > 20:
            signal = "spec_long"   # crowd long → contrarian bear
        elif net_pct < -5:
            signal = "spec_short"  # crowd short → contrarian bull
        else:
            signal = "neutral"

        data = {
            "date": row[2] if len(row) > 2 else "",
            "mm_long": mm_long, "mm_short": mm_short,
            "net": net, "net_pct": round(net_pct, 1),
            "signal": signal,
        }
        _cache["cot"] = {"data": data, "ts": now}
        return data
    except Exception:
        return {}


# ── 6: Term Structure (backwardation vs contango) ─────────────────────────────

def get_wti_term_structure() -> dict:
    """
    Compare front-month (CL=F) vs 3-month forward (CL3=F).
    Backwardation (spot > forward) = bullish (supply tight, demand high).
    Contango (forward > spot) = bearish (oversupply, storage building).
    """
    try:
        import yfinance as yf
        from datetime import datetime
        # Front month and 3-month forward (CLN = July contract, ~3 months out)
        year2 = str(datetime.now().year)[-2:]  # "26"
        front = yf.Ticker("CL=F").history(period="5d")["Close"].iloc[-1]
        # Try N (July) then Q (August) as ~3-month forward — yfinance uses 2-digit year
        forward = None
        for suffix in ["N", "Q", "U", "V", "M"]:
            try:
                h = yf.Ticker(f"CL{suffix}{year2}.NYM").history(period="5d")
                if not h.empty:
                    forward = h["Close"].iloc[-1]
                    break
            except Exception:
                continue
        if forward is None:
            return {}
        forward = float(forward)
        spread = front - forward
        structure = "backwardation" if spread > 0.5 else ("contango" if spread < -0.5 else "flat")
        return {"front": float(front), "forward": float(forward),
                "spread": round(spread, 2), "structure": structure}
    except Exception:
        return {}


# ── 7: DXY Direction (strong dollar = lower oil) ──────────────────────────────

def get_dxy_signal() -> str:
    """Return 'bearish_oil', 'bullish_oil', or 'neutral' based on DXY trend."""
    try:
        import httpx
        key = _secrets().get("FRED_API_KEY", "")
        if not key:
            return "neutral"
        r = httpx.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={"series_id": "DTWEXBGS", "api_key": key,
                    "file_type": "json", "limit": 10, "sort_order": "desc"},
            timeout=8,
        )
        if r.status_code != 200:
            return "neutral"
        obs = [o for o in r.json().get("observations", []) if o["value"] != "."]
        if len(obs) < 5:
            return "neutral"
        recent  = float(obs[0]["value"])
        prior   = float(obs[4]["value"])
        chg_pct = (recent - prior) / prior * 100
        if chg_pct > 1.0:
            return "bearish_oil"   # dollar strengthening → bearish for oil
        elif chg_pct < -1.0:
            return "bullish_oil"   # dollar weakening → bullish for oil
        return "neutral"
    except Exception:
        return "neutral"


# ── 8: News Sentiment ─────────────────────────────────────────────────────────

def get_wti_news_sentiment() -> str:
    """Return 'positive', 'negative', or 'neutral' from NewsAPI oil headlines."""
    try:
        import httpx
        key = _secrets().get("NEWSAPI_API_KEY", "")
        if not key:
            return "neutral"
        r = httpx.get(
            "https://newsapi.org/v2/everything",
            params={"q": "crude oil price WTI OPEC supply",
                    "sortBy": "publishedAt", "pageSize": 5,
                    "language": "en", "apiKey": key},
            timeout=8,
        )
        if r.status_code != 200:
            return "neutral"
        articles = r.json().get("articles", [])
        bull_words = ["rises", "surge", "rally", "gain", "tight supply", "draw", "cut"]
        bear_words = ["falls", "drops", "slide", "oversupply", "build", "glut", "weak"]
        bull = sum(1 for a in articles for w in bull_words if w in (a.get("title","") or "").lower())
        bear = sum(1 for a in articles for w in bear_words if w in (a.get("title","") or "").lower())
        if bull > bear + 1: return "positive"
        if bear > bull + 1: return "negative"
        return "neutral"
    except Exception:
        return "neutral"


# ── Master: WTI Directional Call ──────────────────────────────────────────────

def wti_directional_call() -> str:
    """
    Run the 8-signal WTI oracle. Returns string matching SmartCall pattern.
    STRONG requires 6/8 signals (75% conviction).
    """
    ta   = get_wti_technicals()
    if not ta or not ta.get("price"):
        return "DIRECTION: SKIP — no WTI price data"

    price = ta["price"]
    bull, bear, notes = 0, 0, []

    # Signal 1: EMA trend
    if ta.get("ema20") and ta.get("ema50"):
        if ta["ema20"] > ta["ema50"]: bull += 1; notes.append("EMA bull")
        else: bear += 1; notes.append("EMA bear")

    # Signal 2: RSI
    rsi = ta.get("rsi", 50)
    if rsi < 35: bull += 1; notes.append(f"RSI oversold {rsi}")
    elif rsi > 68: bear += 1; notes.append(f"RSI overbought {rsi}")

    # Signal 3: MACD
    if ta.get("macd", 0) > 0: bull += 1; notes.append("MACD bull")
    elif ta.get("macd", 0) < 0: bear += 1; notes.append("MACD bear")

    # Signal 4: 52-week position
    pos = ta.get("pos_52w", 50)
    if pos < 20: bull += 1; notes.append(f"Near 52w low ({pos:.0f}%)")
    elif pos > 85: bear += 1; notes.append(f"Near 52w high ({pos:.0f}%)")

    # Signal 5: COT positioning (contrarian)
    cot = get_wti_cot()
    if cot.get("signal") == "spec_short": bull += 1; notes.append(f"COT spec short {cot['net_pct']:+.0f}%")
    elif cot.get("signal") == "spec_long": bear += 1; notes.append(f"COT spec crowded {cot['net_pct']:+.0f}%")

    # Signal 6: Term structure
    ts = get_wti_term_structure()
    if ts.get("structure") == "backwardation": bull += 1; notes.append(f"Backwardation +${ts['spread']:.2f}")
    elif ts.get("structure") == "contango": bear += 1; notes.append(f"Contango -${abs(ts['spread']):.2f}")

    # Signal 7: DXY
    dxy = get_dxy_signal()
    if dxy == "bullish_oil": bull += 1; notes.append("DXY weakening")
    elif dxy == "bearish_oil": bear += 1; notes.append("DXY strengthening")

    # Signal 8: News sentiment
    news = get_wti_news_sentiment()
    if news == "positive": bull += 1; notes.append("News bullish")
    elif news == "negative": bear += 1; notes.append("News bearish")

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

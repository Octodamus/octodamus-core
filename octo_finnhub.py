"""
octo_finnhub.py
Finnhub free-tier signals: insider transactions, earnings surprises, news sentiment.
Wired into ACP congressional/stock reports as context enrichment.

Cache: data/finnhub_cache.json
"""

import json
import time
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path

CACHE_FILE  = Path(__file__).parent / "data" / "finnhub_cache.json"
SECRETS_FILE = Path(__file__).parent / ".octo_secrets"
BASE_URL    = "https://finnhub.io/api/v1"

CACHE_TTL = {
    "insider":   3600 * 4,   # 4h
    "earnings":  3600 * 6,   # 6h
    "sentiment": 3600 * 1,   # 1h
}


def _get_key() -> str:
    try:
        s = json.loads(SECRETS_FILE.read_text(encoding="utf-8"))
        return s.get("secrets", s).get("FINNHUB_API_KEY", "")
    except Exception:
        return ""


def _load_cache() -> dict:
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(cache: dict):
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def _fetch(path: str, params: dict) -> dict | list | None:
    key = _get_key()
    if not key:
        return None
    params["token"] = key
    url = f"{BASE_URL}{path}?{urllib.parse.urlencode(params)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "octodamus/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def _cached_fetch(cache_key: str, ttl: int, path: str, params: dict):
    cache = _load_cache()
    entry = cache.get(cache_key, {})
    if entry and time.time() - entry.get("ts", 0) < ttl:
        return entry.get("data")
    data = _fetch(path, params)
    if data is not None:
        cache[cache_key] = {"ts": time.time(), "data": data}
        _save_cache(cache)
    return data


def get_insider_transactions(ticker: str) -> dict:
    """Corporate insider buys/sells, last 90 days."""
    from_date = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
    to_date   = datetime.now().strftime("%Y-%m-%d")
    raw = _cached_fetch(
        f"insider_{ticker}",
        CACHE_TTL["insider"],
        "/stock/insider-transactions",
        {"symbol": ticker, "from": from_date, "to": to_date},
    )
    if not raw or not isinstance(raw, dict):
        return {"ticker": ticker, "buys": 0, "sells": 0, "trades": [], "available": False}

    trades = raw.get("data") or []
    buys = sells = 0
    recent = []
    for t in trades[:20]:
        shares = t.get("share", 0) or 0
        change = t.get("change", 0) or 0
        tx_type = "BUY" if change > 0 else "SELL"
        if tx_type == "BUY":
            buys += 1
        else:
            sells += 1
        recent.append({
            "name":      t.get("name", "Unknown"),
            "title":     t.get("officerTitle", ""),
            "direction": tx_type,
            "shares":    abs(shares),
            "value":     abs(t.get("transactionPrice", 0) or 0) * abs(shares),
            "date":      str(t.get("transactionDate", ""))[:10],
        })
    return {"ticker": ticker, "buys": buys, "sells": sells, "trades": recent, "available": True}


def get_earnings_surprise(ticker: str) -> dict:
    """Last 4 quarters EPS actual vs estimate."""
    raw = _cached_fetch(
        f"earnings_{ticker}",
        CACHE_TTL["earnings"],
        "/stock/earnings",
        {"symbol": ticker, "limit": 4},
    )
    if not raw or not isinstance(raw, list):
        return {"ticker": ticker, "quarters": [], "available": False}

    quarters = []
    for q in raw[:4]:
        actual   = q.get("actual")
        estimate = q.get("estimate")
        surprise = q.get("surprise")
        pct      = q.get("surprisePercent")
        if actual is None:
            continue
        beat = None
        if surprise is not None:
            beat = surprise > 0
        quarters.append({
            "period":   q.get("period", ""),
            "actual":   actual,
            "estimate": estimate,
            "surprise": surprise,
            "pct":      round(pct, 1) if pct is not None else None,
            "beat":     beat,
        })
    return {"ticker": ticker, "quarters": quarters, "available": bool(quarters)}


def get_news_sentiment(ticker: str) -> dict:
    """Aggregate news sentiment score from Finnhub."""
    raw = _cached_fetch(
        f"sentiment_{ticker}",
        CACHE_TTL["sentiment"],
        "/news-sentiment",
        {"symbol": ticker},
    )
    if not raw or not isinstance(raw, dict):
        return {"ticker": ticker, "score": None, "buzz": None, "available": False}

    sentiment = raw.get("sentiment") or {}
    buzz      = raw.get("buzz") or {}
    return {
        "ticker":          ticker,
        "score":           sentiment.get("bearishPercent"),     # 0-1, higher = more bearish
        "bullish_pct":     sentiment.get("bullishPercent"),
        "bearish_pct":     sentiment.get("bearishPercent"),
        "articles_last7":  buzz.get("articlesInLastWeek"),
        "weekly_avg":      buzz.get("weeklyAverage"),
        "buzz_score":      buzz.get("buzz"),
        "available":       True,
    }


def get_finnhub_context(ticker: str) -> str:
    """Combined context string for injection into ACP reports."""
    insider  = get_insider_transactions(ticker)
    earnings = get_earnings_surprise(ticker)
    sentiment = get_news_sentiment(ticker)

    lines = [f"--- Finnhub Intelligence: {ticker} ---"]

    # Insider transactions
    if insider["available"]:
        b, s = insider["buys"], insider["sells"]
        if b == 0 and s == 0:
            lines.append("Corporate Insiders (90d): No transactions recorded.")
        else:
            direction = "NET BUYING" if b > s else "NET SELLING" if s > b else "MIXED"
            lines.append(f"Corporate Insiders (90d): {direction} -- {b} buys / {s} sells")
            for t in insider["trades"][:3]:
                val = f"${t['value']:,.0f}" if t["value"] else ""
                lines.append(f"  {t['direction']} {t['name']} ({t['title']}) {val} on {t['date']}")
    else:
        lines.append("Corporate Insiders: unavailable")

    # Earnings surprises
    if earnings["available"]:
        qs = earnings["quarters"]
        beats = sum(1 for q in qs if q["beat"] is True)
        misses = sum(1 for q in qs if q["beat"] is False)
        streak = ""
        if qs and qs[0]["beat"] is not None:
            streak = "BEAT" if qs[0]["beat"] else "MISS"
        lines.append(f"Earnings (last {len(qs)}Q): {beats} beats / {misses} misses -- last quarter: {streak}")
        for q in qs[:2]:
            pct_str = f"{q['pct']:+.1f}%" if q["pct"] is not None else ""
            lines.append(f"  {q['period']}: actual={q['actual']} vs est={q['estimate']} {pct_str}")
    else:
        lines.append("Earnings: unavailable")

    # News sentiment
    if sentiment["available"]:
        bull = sentiment.get("bullish_pct") or 0
        bear = sentiment.get("bearish_pct") or 0
        buzz = sentiment.get("buzz_score") or 0
        label = "BULLISH" if bull > bear else "BEARISH" if bear > bull else "NEUTRAL"
        lines.append(f"News Sentiment: {label} ({bull*100:.0f}% bull / {bear*100:.0f}% bear) -- buzz score {buzz:.2f}")
    else:
        lines.append("News Sentiment: unavailable")

    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    ticker = sys.argv[1].upper() if len(sys.argv) > 1 else "TSLA"
    key = _get_key()
    if not key:
        print("No FINNHUB_API_KEY in .octo_secrets")
        sys.exit(1)
    print(f"\nTesting Finnhub for {ticker}...\n")
    print(get_finnhub_context(ticker))

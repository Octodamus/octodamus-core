"""
octo_news.py
OctoNews — News Intelligence Mind

Fetches and scores headlines via NewsAPI.
Already wired into octodamus_runner.py — this module makes it a
first-class mind with its own scan function, scoring, and prompt formatter.

Bitwarden key: AGENT - Octodamus - Data - NewsAPI
Env var:       NEWSAPI_API_KEY

Free tier: 100 requests/day, 1-month history
Get key at: newsapi.org

Usage:
    from octo_news import run_news_scan, format_news_for_prompt
    news = run_news_scan(["NVDA", "BTC", "SPY"])
"""

import os
import time
import requests
from datetime import datetime

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

NEWSAPI_BASE = "https://newsapi.org/v2/everything"

# Query map — ticker to search string
TICKER_QUERIES = {
    "NVDA":  "NVIDIA stock",
    "TSLA":  "Tesla stock",
    "AAPL":  "Apple stock",
    "META":  "Meta stock",
    "MSFT":  "Microsoft stock",
    "BTC":   "Bitcoin cryptocurrency",
    "ETH":   "Ethereum cryptocurrency",
    "SOL":   "Solana cryptocurrency",
    "SPY":   "S&P 500 market",
    "QQQ":   "Nasdaq market",
}

HEADLINES_PER_TICKER = 5
_REQUEST_DELAY = 0.4

# Sentiment keywords for headline scoring
BULLISH_WORDS = [
    "surge", "jump", "rally", "gain", "rise", "beat", "record", "high",
    "growth", "profit", "upgrade", "bull", "boom", "breakthrough", "strong",
    "outperform", "exceed", "recover", "rebound", "soar",
]
BEARISH_WORDS = [
    "drop", "fall", "crash", "plunge", "loss", "miss", "downgrade", "bear",
    "decline", "weak", "cut", "layoff", "lawsuit", "probe", "fine", "ban",
    "recall", "warning", "risk", "concern", "fear", "sell", "short",
]


# ─────────────────────────────────────────────
# SCORING
# ─────────────────────────────────────────────

def _score_headline(title: str) -> int:
    lower = title.lower()
    bull = sum(1 for w in BULLISH_WORDS if w in lower)
    bear = sum(1 for w in BEARISH_WORDS if w in lower)
    return bull - bear


# ─────────────────────────────────────────────
# FETCH
# ─────────────────────────────────────────────

def fetch_headlines(ticker: str, api_key: str, max_results: int = HEADLINES_PER_TICKER) -> list[dict]:
    """Fetch and score headlines for a single ticker."""
    query = TICKER_QUERIES.get(ticker, ticker)
    try:
        r = requests.get(
            NEWSAPI_BASE,
            params={
                "q": query,
                "sortBy": "publishedAt",
                "pageSize": max_results,
                "language": "en",
                "apiKey": api_key,
            },
            timeout=10,
        )
        data = r.json()
        if data.get("status") != "ok":
            print(f"[OctoNews] API error for {ticker}: {data.get('message')}")
            return []

        results = []
        for a in data.get("articles", []):
            title = a.get("title", "")
            if not title or "[Removed]" in title:
                continue
            results.append({
                "title": title,
                "source": a.get("source", {}).get("name", ""),
                "published": a.get("publishedAt", "")[:16],
                "url": a.get("url", ""),
                "sentiment": _score_headline(title),
            })
        return results

    except Exception as e:
        print(f"[OctoNews] Fetch failed for {ticker}: {e}")
        return []


# ─────────────────────────────────────────────
# MAIN SCAN
# ─────────────────────────────────────────────

def run_news_scan(tickers: list | None = None, api_key: str | None = None) -> dict:
    """
    Fetch headlines for all tickers and return structured news snapshot.
    """
    if api_key is None:
        api_key = os.environ.get("NEWSAPI_API_KEY")
    if not api_key:
        print("[OctoNews] No NEWSAPI_API_KEY found — news scan skipped.")
        return {"error": "no_api_key", "headlines": {}, "top_stories": []}

    if tickers is None:
        tickers = list(TICKER_QUERIES.keys())

    print(f"[OctoNews] Fetching headlines for {len(tickers)} tickers...")
    headlines = {}
    all_scored = []

    for ticker in tickers:
        stories = fetch_headlines(ticker, api_key)
        headlines[ticker] = stories
        count = len(stories)
        if count:
            avg_sentiment = round(sum(s["sentiment"] for s in stories) / count, 2)
            label = "🟢" if avg_sentiment > 0.3 else ("🔴" if avg_sentiment < -0.3 else "⚪")
            print(f"  {ticker:8s} {label} {count} stories | avg sentiment {avg_sentiment:+.2f}")
            for s in stories:
                all_scored.append({**s, "ticker": ticker})
        else:
            print(f"  {ticker:8s} [no stories]")
        time.sleep(_REQUEST_DELAY)

    # Top stories by absolute sentiment score
    top_stories = sorted(all_scored, key=lambda x: abs(x["sentiment"]), reverse=True)[:5]

    return {
        "timestamp": datetime.utcnow().isoformat(),
        "headlines": headlines,
        "top_stories": top_stories,
    }


def format_news_for_prompt(result: dict, max_per_ticker: int = 2) -> str:
    """Format OctoNews results into a compact prompt string for the LLM."""
    if result.get("error") or not result.get("headlines"):
        return ""

    lines = ["Latest news (OctoNews):"]
    for ticker, stories in result["headlines"].items():
        if not stories:
            continue
        lines.append(f"  {ticker}:")
        for s in stories[:max_per_ticker]:
            lines.append(f"    - {s['title'][:80]}")
    return "\n".join(lines)


# ─────────────────────────────────────────────
# STANDALONE RUN
# ─────────────────────────────────────────────

if __name__ == "__main__":
    result = run_news_scan(["NVDA", "BTC", "SPY", "TSLA"])
    print(f"\n── OctoNews Report ──────────────────────")
    print(f"Top stories:")
    for s in result.get("top_stories", []):
        label = "🟢" if s["sentiment"] > 0 else ("🔴" if s["sentiment"] < 0 else "⚪")
        print(f"  {label} [{s['ticker']}] {s['title'][:70]}")
        print(f"     {s['source']} · {s['published']}")

"""
octo_news.py
OctoNews — News Intelligence Mind

Primary:  Tavily AI-optimized search (returns full article content, not just links)
Fallback: NewsAPI (headlines only)

Bitwarden keys:
  TAVILY_API_KEY   — primary   (free: 1,000 searches/mo)
  NEWSAPI_API_KEY  — fallback  (free: 100 requests/day)

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
    # Markets
    "NVDA":       "NVIDIA stock",
    "TSLA":       "Tesla stock",
    "AAPL":       "Apple stock",
    "META":       "Meta stock",
    "MSFT":       "Microsoft stock",
    "BTC":        "Bitcoin cryptocurrency",
    "ETH":        "Ethereum cryptocurrency",
    "SOL":        "Solana cryptocurrency",
    "SPY":        "S&P 500 market",
    "QQQ":        "Nasdaq market",
    # Geopolitical — Polymarket
    "IRAN-CEASE": "Iran US ceasefire nuclear deal negotiations",
    "IRAN-IL":    "Iran Israel military strike attack",
    "IRAN":       "Iran war conflict sanctions",
    "HUN-PM":     "Hungary prime minister Orban election",
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
# FETCH — Tavily (primary)
# ─────────────────────────────────────────────

def fetch_headlines_tavily(ticker: str, api_key: str, max_results: int = HEADLINES_PER_TICKER) -> list[dict]:
    """
    Fetch headlines via Tavily AI-optimized search.
    Returns full article content (not just titles) — much richer than NewsAPI.
    """
    try:
        from tavily import TavilyClient
        query = TICKER_QUERIES.get(ticker, ticker)
        client = TavilyClient(api_key=api_key)
        response = client.search(
            query,
            max_results=max_results,
            search_depth="basic",   # basic = 1 credit/search vs 2 for advanced
            include_answer=False,
            topic="news",
        )
        results = []
        for r in response.get("results", []):
            title = r.get("title", "")
            if not title:
                continue
            url = r.get("url", "")
            source = url.split("/")[2].replace("www.", "") if "/" in url else url
            results.append({
                "title":     title,
                "source":    source,
                "published": (r.get("published_date") or "")[:16],
                "url":       url,
                "content":   (r.get("content") or "")[:400],  # article snippet
                "sentiment": _score_headline(title),
                "score":     round(r.get("score", 0), 3),
            })
        return results
    except Exception as e:
        print(f"[OctoNews/Tavily] Fetch failed for {ticker}: {e}")
        return []


# ─────────────────────────────────────────────
# FETCH — NewsAPI (fallback)
# ─────────────────────────────────────────────

def fetch_headlines_newsapi(ticker: str, api_key: str, max_results: int = HEADLINES_PER_TICKER) -> list[dict]:
    """Fetch headlines via NewsAPI. Headlines only, no article content."""
    query = TICKER_QUERIES.get(ticker, ticker)
    try:
        r = requests.get(
            NEWSAPI_BASE,
            params={
                "q":        query,
                "sortBy":   "publishedAt",
                "pageSize": max_results,
                "language": "en",
                "apiKey":   api_key,
            },
            timeout=10,
        )
        data = r.json()
        if data.get("status") != "ok":
            print(f"[OctoNews/NewsAPI] Error for {ticker}: {data.get('message')}")
            return []
        results = []
        for a in data.get("articles", []):
            title = a.get("title", "")
            if not title or "[Removed]" in title:
                continue
            results.append({
                "title":     title,
                "source":    a.get("source", {}).get("name", ""),
                "published": a.get("publishedAt", "")[:16],
                "url":       a.get("url", ""),
                "content":   "",
                "sentiment": _score_headline(title),
                "score":     0,
            })
        return results
    except Exception as e:
        print(f"[OctoNews/NewsAPI] Fetch failed for {ticker}: {e}")
        return []


def fetch_headlines(ticker: str, api_key: str = None, max_results: int = HEADLINES_PER_TICKER) -> list[dict]:
    """
    Unified fetch — Tavily first (richer content), NewsAPI fallback.
    api_key is ignored; keys loaded from env automatically.
    """
    tavily_key  = os.environ.get("TAVILY_API_KEY", "")
    newsapi_key = os.environ.get("NEWSAPI_API_KEY", api_key or "")

    if tavily_key:
        results = fetch_headlines_tavily(ticker, tavily_key, max_results)
        if results:
            return results

    if newsapi_key:
        return fetch_headlines_newsapi(ticker, newsapi_key, max_results)

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

    source = "tavily" if os.environ.get("TAVILY_API_KEY") else "newsapi"
    return {
        "timestamp":   datetime.utcnow().isoformat(),
        "headlines":   headlines,
        "top_stories": top_stories,
        "source":      source,
    }


def format_news_for_prompt(result: dict, max_per_ticker: int = 2) -> str:
    """
    Format OctoNews results into a compact prompt string for the LLM.
    Includes article snippets when available (Tavily) for richer context.
    """
    if result.get("error") or not result.get("headlines"):
        return ""

    lines = [f"Latest news (OctoNews via {'Tavily' if result.get('source') == 'tavily' else 'NewsAPI'}):"]
    for ticker, stories in result["headlines"].items():
        if not stories:
            continue
        lines.append(f"  {ticker}:")
        for s in stories[:max_per_ticker]:
            lines.append(f"    - {s['title'][:90]}")
            if s.get("content"):
                lines.append(f"      {s['content'][:200]}")
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

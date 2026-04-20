"""
octo_data_aggregator.py
Overnight data aggregation pipeline — runs 1am, 2am, 3am via Task Scheduler
Collects: sentiment scores, price snapshots, news digests
Stores: data/snapshots/{date}/{type}.json
Sold via: RapidAPI or Virtuals ACP
"""

import json
import os
import sys
import time
import argparse
import logging
from datetime import datetime, date
from pathlib import Path
from typing import Optional

import anthropic
import requests

# ── paths ────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data" / "snapshots"
LOG_DIR  = BASE_DIR / "logs"
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "aggregator.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("OctoData")

# ── watchlists ────────────────────────────────────────────────────────────────
STOCK_WATCHLIST  = ["NVDA", "TSLA", "AAPL"]
CRYPTO_WATCHLIST = ["BTC", "ETH", "SOL"]
ALL_SYMBOLS = STOCK_WATCHLIST + CRYPTO_WATCHLIST

COINGECKO_IDS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
}


# ── credential loader ─────────────────────────────────────────────────────────
def load_secrets() -> dict:
    """Load secrets via bitwarden module (already in repo)."""
    try:
        import bitwarden
        return bitwarden.load_all_secrets()
    except Exception as e:
        log.error(f"Bitwarden load failed: {e}")
        sys.exit(1)


# ── price snapshot ────────────────────────────────────────────────────────────
def get_price_snapshot() -> dict:
    """Pull spot prices using existing free sources (CoinGecko + Yahoo Finance)."""
    try:
        import octo_spot_prices
        prices = octo_spot_prices.get_all_prices()
        log.info(f"Price snapshot: {list(prices.keys())}")
        return prices
    except Exception as e:
        log.warning(f"octo_spot_prices failed ({e}), falling back to direct fetch")
        return _fallback_prices()


def _fallback_prices() -> dict:
    """Direct CoinGecko + yfinance fallback."""
    prices = {}
    ids = ",".join(COINGECKO_IDS.values())
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": ids, "vs_currencies": "usd", "include_24hr_change": "true"},
            timeout=10,
        )
        data = r.json()
        for sym, cg_id in COINGECKO_IDS.items():
            if cg_id in data:
                prices[sym] = {
                    "price": data[cg_id].get("usd"),
                    "change_24h": data[cg_id].get("usd_24h_change"),
                }
    except Exception as e:
        log.warning(f"CoinGecko fallback error: {e}")

    try:
        import yfinance as yf
        for sym in STOCK_WATCHLIST:
            t = yf.Ticker(sym)
            info = t.fast_info
            prices[sym] = {
                "price": round(info.last_price, 2),
                "change_24h": None,
            }
    except Exception as e:
        log.warning(f"yfinance fallback error: {e}")

    return prices


# ── news digest ───────────────────────────────────────────────────────────────
def get_news_digest(tavily_api_key: str) -> dict:
    """Fetch recent news headlines per symbol via Tavily search."""
    digests = {}
    headers = {"Content-Type": "application/json"}

    for sym in ALL_SYMBOLS:
        query = f"{sym} cryptocurrency market news" if sym in CRYPTO_WATCHLIST else f"{sym} stock market news"
        try:
            r = requests.post(
                "https://api.tavily.com/search",
                headers=headers,
                json={
                    "api_key": tavily_api_key,
                    "query": query,
                    "max_results": 5,
                    "include_answer": False,
                    "search_depth": "basic",
                },
                timeout=15,
            )
            results = r.json().get("results", [])
            digests[sym] = [
                {"title": a.get("title"), "url": a.get("url"), "published": a.get("published_date")}
                for a in results
            ]
            log.info(f"  News: {sym} — {len(results)} articles")
            time.sleep(0.5)  # polite rate limiting
        except Exception as e:
            log.warning(f"  News fetch failed for {sym}: {e}")
            digests[sym] = []

    return digests


# ── sentiment scoring ─────────────────────────────────────────────────────────
SENTIMENT_SYSTEM = """You are a financial sentiment analyst. Given news headlines for a symbol,
output ONLY valid JSON with this exact structure:
{
  "score": <integer -100 to 100>,
  "label": "<BEARISH|NEUTRAL|BULLISH>",
  "confidence": "<LOW|MEDIUM|HIGH>",
  "summary": "<one sentence, max 15 words>"
}
No extra text, no markdown, no explanation."""


def score_sentiment(client: anthropic.Anthropic, symbol: str, headlines: list) -> dict:
    """Use Haiku to score sentiment from headlines. Cheap, fast."""
    if not headlines:
        return {"score": 0, "label": "NEUTRAL", "confidence": "LOW", "summary": "No recent news found."}

    headline_text = "\n".join(
        f"- {h['title']}" for h in headlines[:5] if h.get("title")
    )

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            system=SENTIMENT_SYSTEM,
            messages=[
                {"role": "user", "content": f"Symbol: {symbol}\nHeadlines:\n{headline_text}"}
            ],
        )
        raw = response.content[0].text.strip()
        # strip markdown fences
        if "```" in raw:
            raw = raw.replace("```json", "").replace("```", "").strip()
        return json.loads(raw)
    except json.JSONDecodeError:
        log.warning(f"  Haiku returned non-JSON for {symbol}: {raw[:80]}")
        return {"score": 0, "label": "NEUTRAL", "confidence": "LOW", "summary": "Parsing error."}
    except Exception as e:
        log.warning(f"  Sentiment scoring failed for {symbol}: {e}")
        return {"score": 0, "label": "NEUTRAL", "confidence": "LOW", "summary": "Scoring unavailable."}


# ── market briefing (premium tier) ───────────────────────────────────────────
BRIEFING_SYSTEM = """You are OctoData, an AI market intelligence engine.
Output ONLY valid JSON:
{
  "market_mood": "<RISK_ON|RISK_OFF|NEUTRAL>",
  "top_opportunity": "<symbol>",
  "top_risk": "<symbol>",
  "overnight_thesis": "<2-3 sentences max>",
  "timestamp": "<ISO datetime>"
}"""


def generate_briefing(client: anthropic.Anthropic, sentiment_map: dict, prices: dict) -> dict:
    """Generate a single market briefing from aggregated data. Sonnet-level quality."""
    summary_lines = []
    for sym, s in sentiment_map.items():
        price_info = prices.get(sym, {})
        price = price_info.get("price", "N/A")
        chg = price_info.get("change_24h")
        chg_str = f"{chg:+.1f}%" if chg is not None else "N/A"
        summary_lines.append(
            f"{sym}: ${price} ({chg_str}) | Sentiment: {s.get('label')} ({s.get('score')}) | {s.get('summary')}"
        )

    prompt = "Current market data:\n" + "\n".join(summary_lines)

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=400,
            system=BRIEFING_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        # strip markdown fences if Haiku wrapped the JSON
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())
        briefing["timestamp"] = datetime.utcnow().isoformat()
        return briefing
    except Exception as e:
        log.warning(f"Briefing generation failed: {e}")
        return {"market_mood": "NEUTRAL", "timestamp": datetime.utcnow().isoformat(), "error": str(e)}


# ── snapshot writer ───────────────────────────────────────────────────────────
def write_snapshot(run_type: str, payload: dict) -> Path:
    """Save snapshot to data/snapshots/{date}/{run_type}.json"""
    today_dir = DATA_DIR / str(date.today())
    today_dir.mkdir(parents=True, exist_ok=True)
    out_path = today_dir / f"{run_type}.json"
    with open(out_path, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    log.info(f"Snapshot saved: {out_path}")
    return out_path


# ── run modes ─────────────────────────────────────────────────────────────────
def run_prices():
    """1am job — price snapshot only (cheapest, no AI)"""
    log.info("=== OctoData: prices run ===")
    prices = get_price_snapshot()
    payload = {
        "type": "price_snapshot",
        "timestamp": datetime.utcnow().isoformat(),
        "data": prices,
    }
    write_snapshot("prices", payload)
    log.info("Prices run complete.")


def run_sentiment(secrets: dict):
    """2am job — news fetch + Haiku sentiment scoring"""
    log.info("=== OctoData: sentiment run ===")
    client = anthropic.Anthropic(api_key=secrets.get("ANTHROPIC_API_KEY"))
    tavily_key = secrets.get("TAVILY_API_KEY")

    news = get_news_digest(tavily_key)
    sentiment_map = {}
    for sym in ALL_SYMBOLS:
        log.info(f"  Scoring {sym}...")
        sentiment_map[sym] = score_sentiment(client, sym, news.get(sym, []))

    payload = {
        "type": "sentiment_scores",
        "timestamp": datetime.utcnow().isoformat(),
        "symbols": sentiment_map,
        "news_sources": {sym: len(v) for sym, v in news.items()},
    }
    write_snapshot("sentiment", payload)
    log.info("Sentiment run complete.")


def run_briefing(secrets: dict):
    """3am job — full market briefing (premium data product)"""
    log.info("=== OctoData: briefing run ===")
    client = anthropic.Anthropic(api_key=secrets.get("ANTHROPIC_API_KEY"))

    # load today's earlier snapshots if available
    today_dir = DATA_DIR / str(date.today())
    prices, sentiment_map = {}, {}

    price_file = today_dir / "prices.json"
    if price_file.exists():
        prices = json.loads(price_file.read_text()).get("data", {})

    sentiment_file = today_dir / "sentiment.json"
    if sentiment_file.exists():
        sentiment_map = json.loads(sentiment_file.read_text()).get("symbols", {})

    # fall back to live if snapshots missing
    if not prices:
        prices = get_price_snapshot()
    if not sentiment_map:
        tavily_key = secrets.get("TAVILY_API_KEY")
        news = get_news_digest(tavily_key)
        for sym in ALL_SYMBOLS:
            sentiment_map[sym] = score_sentiment(client, sym, news.get(sym, []))

    briefing = generate_briefing(client, sentiment_map, prices)

    payload = {
        "type": "market_briefing",
        "timestamp": datetime.utcnow().isoformat(),
        "briefing": briefing,
        "prices": prices,
        "sentiment": sentiment_map,
    }
    write_snapshot("briefing", payload)
    log.info("Briefing run complete.")


# ── entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="OctoData overnight aggregator")
    parser.add_argument(
        "--mode",
        choices=["prices", "sentiment", "briefing", "all"],
        required=True,
        help="prices=1am | sentiment=2am | briefing=3am | all=test full pipeline",
    )
    args = parser.parse_args()

    if args.mode == "prices":
        run_prices()
    elif args.mode in ("sentiment", "briefing", "all"):
        secrets = load_secrets()
        if args.mode == "sentiment":
            run_sentiment(secrets)
        elif args.mode == "briefing":
            run_briefing(secrets)
        elif args.mode == "all":
            run_prices()
            run_sentiment(secrets)
            run_briefing(secrets)

"""
octo_eyes_market.py
OctoEyes â€” Market Signal Monitor

Stock prices:  Financial Datasets API (free tier: NVDA, TSLA, AAPL, MSFT, SPY, QQQ)
Crypto prices: CoinGecko free API (no key required â€” BTC, ETH, SOL)

Removed: META (402 on free Financial Datasets tier)
Fixed:   Crypto was calling /crypto/prices/snapshot/ which returns 400 on free tier
"""

import json
import time
import anthropic

from financial_data_client import get_current_price, build_oracle_context

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# WATCHLIST
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

# Free tier Financial Datasets API tickers (META and snapshot endpoint not available)
STOCK_WATCHLIST = ["NVDA", "TSLA", "AAPL", "MSFT"]

# Crypto via CoinGecko (free, no key needed)
CRYPTO_WATCHLIST = ["bitcoin", "ethereum", "solana"]

CRYPTO_DISPLAY = {
    "bitcoin":  "BTC",
    "ethereum": "ETH",
    "solana":   "SOL",
}

# Alert threshold â€” % move to trigger a signal post
MOVE_THRESHOLD_PCT = 3.0

_TICKER_FETCH_DELAY = 0.25


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# CRYPTO PRICE via COINGECKO
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _get_crypto_prices() -> dict:
    """
    Fetch BTC, ETH, SOL prices from CoinGecko free API.
    Returns dict of {coingecko_id: {price, change_24h}}
    """
    import httpx
    ids = ",".join(CRYPTO_WATCHLIST)
    try:
        r = httpx.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={
                "ids": ids,
                "vs_currencies": "usd",
                "include_24hr_change": "true",
            },
            timeout=10,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[OctoEyes] CoinGecko fetch failed: {e}")
        return {}


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# SIGNAL DETECTION
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def check_for_signals() -> list[dict]:
    """
    Scan stock and crypto watchlists for significant price moves.
    Returns list of signal dicts for tickers that crossed MOVE_THRESHOLD_PCT.
    """
    signals = []

    # Stocks via Financial Datasets API
    for ticker in STOCK_WATCHLIST:
        try:
            data = get_current_price(ticker)
            snapshot = data.get("snapshot", {})
            change_pct = float(snapshot.get("day_change_percent", 0) or 0)
            if abs(change_pct) >= MOVE_THRESHOLD_PCT:
                signals.append({
                    "type":       "stock",
                    "ticker":     ticker,
                    "price":      snapshot.get("price"),
                    "change_pct": change_pct,
                    "direction":  "surge" if change_pct > 0 else "plunge",
                })
        except Exception as e:
            print(f"[OctoEyes] Error checking {ticker}: {e}")
        time.sleep(_TICKER_FETCH_DELAY)

    # Crypto via CoinGecko
    crypto_data = _get_crypto_prices()
    for cg_id in CRYPTO_WATCHLIST:
        try:
            data = crypto_data.get(cg_id, {})
            price = data.get("usd", 0)
            change_pct = float(data.get("usd_24h_change", 0) or 0)
            ticker = CRYPTO_DISPLAY[cg_id]
            if abs(change_pct) >= MOVE_THRESHOLD_PCT:
                signals.append({
                    "type":       "crypto",
                    "ticker":     ticker,
                    "price":      price,
                    "change_pct": change_pct,
                    "direction":  "surge" if change_pct > 0 else "plunge",
                })
        except Exception as e:
            print(f"[OctoEyes] Error checking {cg_id}: {e}")

    return signals


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# SIGNAL â†’ POST
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

OCTO_SYSTEM = """You are Octodamus â€” oracle octopus, market seer of the Pacific depths.
You are @octodamusai on X. Max 280 chars. No hashtags. No engagement bait.
Speak with bored certainty. You already knew this was coming.
Lead with the specific number. Then the insight. One ocean metaphor max."""


def generate_oracle_post(signal: dict) -> str:
    """Generate a market signal post via Claude Haiku."""
    ticker     = signal["ticker"]
    price      = signal["price"]
    change_pct = signal["change_pct"]
    direction  = signal["direction"]

    news_headlines = []
    try:
        context = build_oracle_context(ticker, include_fundamentals=False)
        news_headlines = [
            item.get("headline", "")
            for item in context.get("recent_news", {}).get("news", [])[:3]
        ]
    except Exception:
        pass

    prompt = (
        f"Market signal: {ticker} {direction} {change_pct:+.2f}% â€” now ${price}\n"
        f"Recent headlines: {json.dumps(news_headlines)}\n\n"
        "Generate ONE oracle post for @octodamusai. Under 280 chars.\n"
        "Lead with the specific number and ticker. Sound like you already knew.\n"
        "Output ONLY the post text."
    )

    client = _get_client()
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=150,
        system=OCTO_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text.strip()


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# MAIN MONITOR
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def run_market_monitor() -> list[dict]:
    """
    Called by octodamus_runner.py on schedule (3x daily).
    Returns list of {signal, post} dicts ready to queue.
    """
    print("[OctoEyes] Scanning the currents...")
    signals = check_for_signals()

    if not signals:
        print("[OctoEyes] The waters are calm. No signals detected.")
        return []

    posts = []
    for signal in signals:
        print(f"[OctoEyes] Signal: {signal['ticker']} {signal['change_pct']:+.2f}%")
        try:
            post = generate_oracle_post(signal)
            posts.append({"signal": signal, "post": post})
            print(f"[OctoInk] Generated:\n  {post}\n")
        except Exception as e:
            print(f"[OctoEyes] Failed to generate post for {signal['ticker']}: {e}")

    return posts


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# DEEP DIVE â€” weekly fundamentals thread
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

DEEP_DIVE_SYSTEM = """You are Octodamus â€” oracle octopus, market seer.
You have examined the depths of a company's fundamentals.
Speak with bored certainty. Use ocean metaphors sparingly. Be brief and devastating.
You are selling wisdom, not hype."""


def generate_deep_dive_post(ticker: str) -> str:
    """Weekly deep-dive thread using fundamentals data."""
    context = build_oracle_context(ticker, include_fundamentals=True)
    client = _get_client()

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=300,
        system=DEEP_DIVE_SYSTEM,
        messages=[{
            "role": "user",
            "content": (
                f"Oracle thread (2-3 posts) on {ticker}:\n"
                f"{json.dumps(context, indent=2)}\n\n"
                "Format: each post on its own line, separated by ---\n"
                "Under 280 chars each. Reveal what most people miss."
            ),
        }],
    )
    return response.content[0].text.strip()


if __name__ == "__main__":
    posts = run_market_monitor()
    for p in posts:
        print(f"\nðŸ™ ORACLE POST:\n{p['post']}")


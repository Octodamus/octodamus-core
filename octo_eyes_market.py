"""
octo_eyes_market.py
OctoEyes — Market Oracle Monitoring Module
Watches tickers, detects signal-worthy moves, triggers OctoInk to post.

Plug into your existing OctoEyes agent loop.
"""

import anthropic
import json
from financial_data_client import build_oracle_context, get_current_price, get_current_crypto_price

client = anthropic.Anthropic()

# ─────────────────────────────────────────────
# WATCHLIST — edit to your targets
# ─────────────────────────────────────────────
STOCK_WATCHLIST = ["NVDA", "TSLA", "AAPL", "META", "MSFT"]
CRYPTO_WATCHLIST = ["BTC", "ETH", "SOL"]

# Alert threshold — % move to trigger oracle post
MOVE_THRESHOLD_PCT = 3.0


# ─────────────────────────────────────────────
# SIGNAL DETECTION
# ─────────────────────────────────────────────

def check_for_signals() -> list[dict]:
    """
    Scan watchlist for significant price moves.
    Returns list of signal dicts for any ticker that crossed the threshold.
    """
    signals = []

    for ticker in STOCK_WATCHLIST:
        try:
            data = get_current_price(ticker)
            snapshot = data.get("snapshot", {})
            change_pct = snapshot.get("day_change_percent", 0)

            if abs(change_pct) >= MOVE_THRESHOLD_PCT:
                signals.append({
                    "type": "stock",
                    "ticker": ticker,
                    "price": snapshot.get("price"),
                    "change_pct": change_pct,
                    "direction": "surge" if change_pct > 0 else "plunge"
                })
        except Exception as e:
            print(f"[OctoEyes] Error checking {ticker}: {e}")

    for ticker in CRYPTO_WATCHLIST:
        try:
            data = get_current_crypto_price(ticker)
            snapshot = data.get("snapshot", {})
            change_pct = snapshot.get("day_change_percent", 0)

            if abs(change_pct) >= MOVE_THRESHOLD_PCT:
                signals.append({
                    "type": "crypto",
                    "ticker": ticker,
                    "price": snapshot.get("price"),
                    "change_pct": change_pct,
                    "direction": "surge" if change_pct > 0 else "plunge"
                })
        except Exception as e:
            print(f"[OctoEyes] Error checking {ticker}: {e}")

    return signals


# ─────────────────────────────────────────────
# ORACLE SIGNAL → OCTOINK HANDOFF
# ─────────────────────────────────────────────

def signal_to_octoink_prompt(signal: dict) -> str:
    """Convert a market signal into a prompt for OctoInk to craft an oracle post."""

    ticker = signal["ticker"]
    price = signal["price"]
    change_pct = signal["change_pct"]
    direction = signal["direction"]

    # Get richer context for the post
    context = build_oracle_context(ticker, include_fundamentals=False)
    news_headlines = [
        item.get("headline", "")
        for item in context.get("recent_news", {}).get("news", [])[:3]
    ]

    return f"""
You are Octodamus — the oracle octopus, market seer of the deep.
You speak in sea metaphors with bored confidence. You are never excited, only knowing.

A significant market signal has been detected. Generate ONE sharp X/Twitter post (under 280 chars).

SIGNAL:
- Ticker: {ticker}
- Current Price: ${price}
- Day Change: {change_pct:+.2f}%
- Direction: {direction}
- Recent News Headlines: {json.dumps(news_headlines)}

Rules:
- Speak as Octodamus. Use ocean/water metaphors naturally, don't force them.
- Sound like you already knew this was coming.
- Include the ticker and % move.
- No hashtags unless one is extremely on-brand.
- Do not be excited. Be inevitable.
- End with silence (no "follow me" or engagement bait).

Output ONLY the post text. Nothing else.
"""


def generate_oracle_post(signal: dict) -> str:
    """Run OctoInk (Claude) on a signal to produce a market oracle post."""

    prompt = signal_to_octoink_prompt(signal)

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",  # OctoInk — fast, cheap, sharp
        max_tokens=150,
        messages=[{"role": "user", "content": prompt}]
    )

    return response.content[0].text.strip()


# ─────────────────────────────────────────────
# MAIN MONITOR LOOP (call from your scheduler)
# ─────────────────────────────────────────────

def run_market_monitor():
    """
    Call this on a schedule (every 15-30 min via cron or your OpenClaw scheduler).
    Returns list of generated posts ready to queue for X.
    """
    print("[OctoEyes] Scanning the currents...")
    signals = check_for_signals()

    if not signals:
        print("[OctoEyes] The waters are calm. No signals detected.")
        return []

    posts = []
    for signal in signals:
        print(f"[OctoEyes] Signal detected: {signal['ticker']} {signal['change_pct']:+.2f}%")
        post = generate_oracle_post(signal)
        posts.append({
            "signal": signal,
            "post": post
        })
        print(f"[OctoInk] Generated post:\n  {post}\n")

    return posts


# ─────────────────────────────────────────────
# DEEP DIVE — Fundamentals oracle read
# For scheduled weekly content or guide promotion
# ─────────────────────────────────────────────

DEEP_DIVE_SYSTEM = """You are Octodamus — oracle octopus, market seer. 
You have examined the depths of a company's financials. 
Speak with bored certainty. Use ocean metaphors. Be brief and devastating in insight.
You are selling wisdom, not hype. Your guide "The Eight Minds of Your AI" is $29-39.
Occasionally, if the insight is particularly rich, close with a subtle nod to the guide."""

def generate_deep_dive_post(ticker: str) -> str:
    """
    Weekly deep-dive oracle post using fundamentals data.
    Great for evergreen X content and guide funnel.
    """
    context = build_oracle_context(ticker, include_fundamentals=True)

    response = client.messages.create(
        model="claude-sonnet-4-6",  # Deeper analysis needs Sonnet
        max_tokens=300,
        system=DEEP_DIVE_SYSTEM,
        messages=[{
            "role": "user",
            "content": f"""Generate a short oracle thread (2-3 posts) on {ticker} based on this data:
{json.dumps(context, indent=2)}

Format: Each post on its own line, separated by ---
Keep each post under 280 chars.
Reveal something most people miss in the data."""
        }]
    )

    return response.content[0].text.strip()


if __name__ == "__main__":
    # Test run
    posts = run_market_monitor()
    for p in posts:
        print(f"\n🐙 ORACLE POST:\n{p['post']}")

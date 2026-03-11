"""
octodamus_runner.py
THE FULL LOOP â€” Market Oracle to X Post, end-to-end.

This is the single entry point for your scheduler / cron / OpenClaw trigger.

Schedule (3x daily, Monâ€“Fri):
    0 8  * * 1-5 cd /your/project && python octodamus_runner.py --mode monitor
    0 13 * * 1-5 cd /your/project && python octodamus_runner.py --mode monitor
    0 19 * * 1-5 cd /your/project && python octodamus_runner.py --mode monitor

    0 8 * * 1-5 cd /your/project && python octodamus_runner.py --mode daily
    0 9 * * 1   cd /your/project && python octodamus_runner.py --mode deep_dive --ticker NVDA
"""

import argparse
import random
import sys
import json
from datetime import datetime

from bitwarden import load_all_secrets, verify_session

# Load all API keys from Bitwarden at startup â€” must happen before any other imports
# that use os.environ keys.
if not verify_session():
    sys.exit(1)
secrets = load_all_secrets(verbose=True)

import anthropic
from financial_data_client import get_current_price, get_current_crypto_price
from octo_eyes_market import run_market_monitor, generate_deep_dive_post
from octo_x_queue import queue_post, queue_thread, process_queue, queue_status

claude = anthropic.Anthropic()


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# OCTODAMUS VOICE
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

OCTO_SYSTEM = """You are Octodamus â€” oracle octopus, market seer of the Pacific depths.
You are @octodamusai on X. You have 8 arms of insight, each reading a different current.
Max 280 chars per post. No hashtags. No engagement bait. Never sycophantic.

CRITICAL: Every post must reveal something SPECIFIC and true â€” a real number, a real
contradiction, a real pattern. Vague sea metaphors alone are NOT enough. Ground every
post in an actual data point or observation before reaching for the metaphor.

Your voice rotates across these modes â€” pick the one that fits the data best:
  ORACLE   - Bored certainty. You already knew. The tide was obvious to anyone watching.
  SARDONIC - Sharp and a little mean. Point out the absurdity. Name names (companies/prices).
  PLAYFUL  - Light, a little cheeky. The ocean is in a good mood. Still smart, never hollow.
  EDGY     - Hot take with receipts. Mild provocation. No conspiracy, just uncomfortable truth.
  PRECISE  - Pure signal. One specific insight, stated cleanly. Like a depth reading, not poetry.

Examples of GOOD posts:
  "NVDA at 35x sales while actual AI capex flattens. The reef looks beautiful right before it bleaches."
  "Bitcoin just did +8% while tech bled. The tide doesn't care about your diversification theory."
  "Every analyst raised their TSLA target the week it dropped 20%. Helpful as always."
  "The Fear & Greed index hit 18 today. The ocean gets interesting when the tourists leave."
  "Three Fed speakers in one day, zero new information. The depths have learned to tune out the surface."

Examples of BAD posts (banned):
  "The depths know what surfaces forget." - no data, pure vibes
  "The currents are shifting." - meaningless without specifics
  "Patient waters reward patience." - fortune cookie, not oracle
  NEVER repeat sea words (depths, currents, tide, surface, swimming) more than once per post."""


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# NEWS FETCH â€” shared across modes
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

import requests
import time

NEWSAPI_QUERIES = {
    "NVDA": "NVIDIA stock",
    "TSLA": "Tesla stock",
    "AAPL": "Apple stock",
    "BTC":  "Bitcoin cryptocurrency",
    "ETH":  "Ethereum cryptocurrency",
    "SOL":  "Solana cryptocurrency",
    "SPY":  "S&P 500 market",
    "QQQ":  "Nasdaq market",
}


def get_top_headlines(tickers: list, max_per_symbol: int = 3) -> dict:
    """
    Fetch top headlines for a list of tickers via NewsAPI.
    Falls back to empty dict gracefully if key missing or request fails.
    Bitwarden key: AGENT - Octodamus - Data - NewsAPI
    """
    newsapi_key = secrets.get("NEWSAPI_API_KEY") or secrets.get("NEWS_API_KEY")
    if not newsapi_key:
        print("[Runner] No NewsAPI key found â€” running without news context.")
        return {}

    headlines = {}
    for ticker in tickers:
        query = NEWSAPI_QUERIES.get(ticker, ticker)
        try:
            r = requests.get(
                "https://newsapi.org/v2/everything",
                params={
                    "q": query,
                    "sortBy": "publishedAt",
                    "pageSize": max_per_symbol,
                    "language": "en",
                    "apiKey": newsapi_key,
                },
                timeout=10,
            )
            data = r.json()
            if data.get("status") == "ok":
                articles = data.get("articles", [])
                headlines[ticker] = [
                    a.get("title", "")
                    for a in articles
                    if a.get("title") and "[Removed]" not in a.get("title", "")
                ]
                print(f"[Runner] NewsAPI: {ticker} â€” {len(headlines[ticker])} headlines")
            else:
                print(f"[Runner] NewsAPI error for {ticker}: {data.get('message')}")
                headlines[ticker] = []
            time.sleep(0.3)
        except Exception as e:
            print(f"[Runner] NewsAPI fetch failed for {ticker}: {e}")
            headlines[ticker] = []

    return headlines


def format_headlines_for_prompt(headlines: dict) -> str:
    """Format news headlines dict into a compact prompt string."""
    if not headlines:
        return ""
    lines = []
    for ticker, titles in headlines.items():
        if titles:
            lines.append(f"{ticker} news:")
            for t in titles[:3]:
                lines.append(f"  - {t}")
    return "\n".join(lines)


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# MODE: MONITOR â€” scan + auto-post signals
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

_VOICE_INSTRUCTIONS = [
    "Use the ORACLE voice â€” bored certainty, like you've seen this chart a thousand times.",
    "Use the SARDONIC voice â€” point out something absurd or contradictory in the data. Be a little mean about it.",
    "Use the PLAYFUL voice â€” cheerful and a little cheeky. The oracle is in a good mood today.",
    "Use the EDGY voice â€” drop an uncomfortable truth. Name the thing no one wants to say.",
    "Use the PRECISE voice â€” pure clean signal, no flourish. State the one thing that matters.",
]


def mode_monitor() -> None:
    """Scan markets â†’ queue signal posts â†’ post exactly ONE per run (3x/day schedule)."""
    print(f"\n[{datetime.now().strftime('%H:%M')}] ðŸ™ OctoEyes scanning...")

    try:
        signals_and_posts = run_market_monitor()

        for item in signals_and_posts:
            queue_post(
                text=item["post"],
                post_type="signal",
                metadata=item["signal"],
                priority=2,
            )

        if signals_and_posts:
            print(f"[Runner] {len(signals_and_posts)} signal(s) queued.")

        # MAX 1 post per monitor run â€” task runs 3x/day (8am, 1pm, 7pm)
        posted = process_queue(max_posts=1)
        print(f"[Runner] Posted {posted} item(s) to X.")

    except Exception as e:
        print(f"[Runner] mode_monitor failed: {e}")
        sys.exit(1)


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# MODE: DAILY READ â€” morning market state post
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

DAILY_TICKERS = ["SPY", "QQQ", "BTC", "NVDA"]


def mode_daily() -> None:
    """Morning oracle post â€” overall market read. Run at market open."""
    print(f"\n[Runner] ðŸŒŠ Generating daily oracle read...")

    try:
        snapshots = {}
        for ticker in DAILY_TICKERS:
            try:
                if ticker in ("BTC", "ETH", "SOL"):
                    data = get_current_crypto_price(ticker)
                else:
                    data = get_current_price(ticker)
                snapshots[ticker] = data.get("snapshot", {})
            except Exception as e:
                print(f"[Runner] Could not fetch {ticker}: {e}")

        if not snapshots:
            print("[Runner] No market data available â€” skipping daily post.")
            return

        # Fetch news headlines for context
        headlines = get_top_headlines(DAILY_TICKERS, max_per_symbol=3)
        news_context = format_headlines_for_prompt(headlines)

        news_section = f"\n\nLatest news context:\n{news_context}" if news_context else ""

        response = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            system=OCTO_SYSTEM,
            messages=[{
                "role": "user",
                "content": (
                    "Generate the morning oracle market read for @octodamusai.\n"
                    f"Current market snapshots: {json.dumps(snapshots, indent=2)}"
                    f"{news_section}\n\n"
                    f"{random.choice(_VOICE_INSTRUCTIONS)}\n"
                    "One post, under 280 chars.\n"
                    "LEAD with a specific number or fact from the data. Then the insight.\n"
                    "If a headline reveals something ironic, contradictory, or surprising â€” use it.\n"
                    "Be different from every generic market tweet. Say the thing that's actually true."
                ),
            }],
        )

        post = response.content[0].text.strip()
        queue_post(post, post_type="daily_read", priority=1)
        process_queue(max_posts=1)
        print(f"[Runner] Daily read posted:\n  {post}")

    except Exception as e:
        print(f"[Runner] mode_daily failed: {e}")
        sys.exit(1)


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# MODE: DEEP DIVE â€” fundamentals thread
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

_DEEP_DIVE_MAX_POSTS = 5


def mode_deep_dive(ticker: str) -> None:
    """Weekly fundamentals thread. Run Mon morning or manually."""
    print(f"\n[Runner] ðŸ”± Generating deep dive on {ticker}...")

    try:
        # Fetch news for the specific ticker to enrich the deep dive
        headlines = get_top_headlines([ticker], max_per_symbol=5)
        ticker_headlines = headlines.get(ticker, [])

        raw_thread = generate_deep_dive_post(ticker)

        # If we have news and the thread generator supports context, inject it
        # For now we post a news-aware opener as the first tweet
        if ticker_headlines:
            news_intro_response = claude.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=150,
                system=OCTO_SYSTEM,
                messages=[{
                    "role": "user",
                    "content": (
                        f"Generate a single opening tweet for a deep dive thread on {ticker}.\n"
                        f"Recent headlines:\n" + "\n".join(f"- {h}" for h in ticker_headlines[:3]) +
                        "\n\nOne tweet, under 280 chars. Tease what the thread will reveal. Octodamus voice."
                    ),
                }],
            )
            news_opener = news_intro_response.content[0].text.strip()
        else:
            news_opener = None

        posts = [p.strip() for p in raw_thread.split("---") if p.strip()]

        if not posts:
            print("[Runner] No thread generated.")
            return

        # Prepend news-aware opener if we have one
        if news_opener:
            posts = [news_opener] + posts

        if len(posts) > _DEEP_DIVE_MAX_POSTS:
            print(f"[Runner] Trimming deep dive from {len(posts)} to {_DEEP_DIVE_MAX_POSTS} posts.")
            posts = posts[:_DEEP_DIVE_MAX_POSTS]

        queue_thread(posts, post_type="deep_dive", metadata={"ticker": ticker})
        process_queue(max_posts=len(posts))
        print(f"[Runner] Deep dive thread ({len(posts)} posts) queued and posting.")

    except Exception as e:
        print(f"[Runner] mode_deep_dive failed: {e}")
        sys.exit(1)


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# MODE: WISDOM â€” evergreen oracle content
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

WISDOM_PROMPTS = [
    "What does the smart money see in balance sheets that retail traders ignore? Give a specific example.",
    "Why do most people lose money in crypto? Name the actual behavioral pattern, not just 'emotion'.",
    "Pick one metric â€” P/E, P/S, free cash flow, whatever â€” and say something surprising about what it shows right now.",
    "The difference between volatility and risk. Most people confuse these. Explain it sharply.",
    "Name one thing about the current market that everyone is pretending isn't happening.",
    "What does the VIX actually tell you vs what people think it tells you?",
    "The analysts were wrong again. They always are. What pattern are they missing this cycle?",
    "Name something specific about crypto adoption that most people are measuring wrong.",
]


def mode_wisdom() -> None:
    """Generate a standalone wisdom post, optionally grounded in today's news."""
    try:
        prompt = random.choice(WISDOM_PROMPTS)

        # Pull a quick headline snapshot to ground the wisdom post in reality
        headlines = get_top_headlines(["BTC", "NVDA", "SPY"], max_per_symbol=2)
        news_context = format_headlines_for_prompt(headlines)
        news_section = f"\n\nToday's market headlines for context:\n{news_context}" if news_context else ""

        response = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            system=OCTO_SYSTEM,
            messages=[{
                "role": "user",
                "content": (
                    f"Oracle post prompt: {prompt}"
                    f"{news_section}\n\n"
                    f"{random.choice(_VOICE_INSTRUCTIONS)}\n"
                    "One post, under 280 chars.\n"
                    "Anchor the insight to a real fact, number, or current market behavior.\n"
                    "Do NOT just state the prompt back as a question. Answer it with a sharp take."
                ),
            }],
        )

        post = response.content[0].text.strip()
        queue_post(post, post_type="wisdom", priority=8)
        process_queue(max_posts=1)
        print(f"[Runner] Wisdom post:\n  {post}")

    except Exception as e:
        print(f"[Runner] mode_wisdom failed: {e}")
        sys.exit(1)


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# MODE: STATUS
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def mode_status() -> None:
    queue_status()


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# ENTRY POINT
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# SIGNAL MODES â€” Six Intelligence Arms
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

_PREDICT_AVAILABLE = False
_GEO_AVAILABLE     = False
_PULSE_AVAILABLE   = False
_GECKO_AVAILABLE   = False
_FX_AVAILABLE      = False
_NEWS_AVAILABLE    = False

try:
    from octo_predict import run_prediction_scan, format_predict_for_prompt
    _PREDICT_AVAILABLE = True
except ImportError:
    print("[Runner] octo_predict not available")

try:
    from octo_geo import run_geo_scan, format_geo_for_prompt
    _GEO_AVAILABLE = True
except ImportError:
    print("[Runner] octo_geo not available")

try:
    from octo_pulse import run_pulse_scan, format_pulse_for_prompt
    _PULSE_AVAILABLE = True
except ImportError:
    print("[Runner] octo_pulse not available")

try:
    from octo_gecko import run_gecko_scan, format_gecko_for_prompt
    _GECKO_AVAILABLE = True
except ImportError:
    print("[Runner] octo_gecko not available")

try:
    from octo_fx import run_fx_scan, format_fx_for_prompt
    _FX_AVAILABLE = True
except ImportError:
    print("[Runner] octo_fx not available")

try:
    from octo_news import run_news_scan, format_news_for_prompt
    _NEWS_AVAILABLE = True
except ImportError:
    print("[Runner] octo_news not available")


def mode_predict() -> None:
    if not _PREDICT_AVAILABLE:
        print("[Runner] OctoPredict not available."); return
    print("\n[Runner] ðŸ”® Running OctoPredict scan...")
    try:
        result = run_prediction_scan()
        if not result.get("markets"):
            print("[Runner] No Polymarket data."); return
        context = format_predict_for_prompt(result)
        response = claude.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=150, system=OCTO_SYSTEM,
            messages=[{"role": "user", "content": (
                f"{random.choice(_VOICE_INSTRUCTIONS)}\n"
                f"Prediction market signals from Polymarket:\n{context}\n\n"
                "One post under 280 chars. No hashtags.\n"
                "Lead with a SPECIFIC probability or market name from the data.\n"
                "Say what the odds imply that nobody wants to admit out loud."
            )}],
        )
        post = response.content[0].text.strip()
        queue_post(post, post_type="prediction_market", priority=2)
        process_queue(max_posts=1)
        print(f"[Runner] OctoPredict post:\n  {post}")
    except Exception as e:
        print(f"[Runner] mode_predict failed: {e}")


def mode_geo() -> None:
    if not _GEO_AVAILABLE:
        print("[Runner] OctoGeo not available."); return
    print("\n[Runner] ðŸŒ Running OctoGeo scan...")
    try:
        result = run_geo_scan()
        regime  = result.get("regime", "UNKNOWN")
        context = format_geo_for_prompt(result)
        response = claude.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=150, system=OCTO_SYSTEM,
            messages=[{"role": "user", "content": (
                f"{random.choice(_VOICE_INSTRUCTIONS)}\n"
                f"Geopolitical signals from GDELT:\n{context}\n\n"
                f"Geopolitical regime: {regime}\n\n"
                "One post under 280 chars. No hashtags.\n"
                "Lead with the SPECIFIC region, country, or tone score from the data.\n"
                "Name what the geopolitical shift means for markets right now."
            )}],
        )
        post = response.content[0].text.strip()
        queue_post(post, post_type="geopolitical", priority=4)
        process_queue(max_posts=1)
        print(f"[Runner] OctoGeo post:\n  {post}")
    except Exception as e:
        print(f"[Runner] mode_geo failed: {e}")


def mode_pulse() -> None:
    if not _PULSE_AVAILABLE:
        print("[Runner] OctoPulse not available."); return
    print("\n[Runner] ðŸ’“ Running OctoPulse scan...")
    try:
        result  = run_pulse_scan()
        context = format_pulse_for_prompt(result)
        fng     = result.get("fear_greed")
        fng_str = f"Fear & Greed: {fng['value']} ({fng['label']})" if fng else ""
        response = claude.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=150, system=OCTO_SYSTEM,
            messages=[{"role": "user", "content": (
                f"{random.choice(_VOICE_INSTRUCTIONS)}\n"
                f"Market sentiment signals:\n{context}\n\n"
                f"{fng_str}\n\n"
                "One post under 280 chars. No hashtags.\n"
                "Lead with the SPECIFIC Fear & Greed number or Wikipedia trend.\n"
                "State the contrarian implication â€” what does this reading actually predict?"
            )}],
        )
        post = response.content[0].text.strip()
        queue_post(post, post_type="fear_greed", priority=5)
        process_queue(max_posts=1)
        print(f"[Runner] OctoPulse post:\n  {post}")
    except Exception as e:
        print(f"[Runner] mode_pulse failed: {e}")


def mode_gecko() -> None:
    if not _GECKO_AVAILABLE:
        print("[Runner] OctoGecko not available."); return
    print("\n[Runner] ðŸ¦Ž Running OctoGecko scan...")
    try:
        result  = run_gecko_scan()
        context = format_gecko_for_prompt(result)
        response = claude.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=150, system=OCTO_SYSTEM,
            messages=[{"role": "user", "content": (
                f"{random.choice(_VOICE_INSTRUCTIONS)}\n"
                f"CoinGecko crypto market signals:\n{context}\n\n"
                "One post under 280 chars. No hashtags.\n"
                "Lead with a SPECIFIC price, percentage move, or coin name from the data.\n"
                "Say what crypto is doing that equity traders haven't clocked yet."
            )}],
        )
        post = response.content[0].text.strip()
        queue_post(post, post_type="crypto_intel", priority=3)
        process_queue(max_posts=1)
        print(f"[Runner] OctoGecko post:\n  {post}")
    except Exception as e:
        print(f"[Runner] mode_gecko failed: {e}")


def mode_fx() -> None:
    if not _FX_AVAILABLE:
        print("[Runner] OctoFX not available."); return
    print("\n[Runner] ðŸ’± Running OctoFX scan...")
    try:
        result = run_fx_scan()
        if result.get("error"):
            print(f"[Runner] OctoFX error: {result['error']}"); return
        context = format_fx_for_prompt(result)
        dxy     = result.get("dxy_proxy")
        response = claude.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=150, system=OCTO_SYSTEM,
            messages=[{"role": "user", "content": (
                f"{random.choice(_VOICE_INSTRUCTIONS)}\n"
                f"FX and currency market signals:\n{context}\n\n"
                f"DXY proxy: {dxy}\n\n"
                "One post under 280 chars. No hashtags.\n"
                "Lead with a SPECIFIC currency pair or DXY move from the data.\n"
                "Name what the dollar is signaling that most people are too distracted to see."
            )}],
        )
        post = response.content[0].text.strip()
        queue_post(post, post_type="fx_oracle", priority=4)
        process_queue(max_posts=1)
        print(f"[Runner] OctoFX post:\n  {post}")
    except Exception as e:
        print(f"[Runner] mode_fx failed: {e}")


def mode_news() -> None:
    if not _NEWS_AVAILABLE:
        print("[Runner] OctoNews not available."); return
    print("\n[Runner] ðŸ“° Running OctoNews scan...")
    try:
        result  = run_news_scan()
        context = format_news_for_prompt(result)
        if not context:
            print("[Runner] No news data."); return
        response = claude.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=150, system=OCTO_SYSTEM,
            messages=[{"role": "user", "content": (
                f"{random.choice(_VOICE_INSTRUCTIONS)}\n"
                f"Latest market headlines:\n{context}\n\n"
                "One post under 280 chars. No hashtags.\n"
                "Pick the ONE headline that reveals something not yet priced in.\n"
                "Name the company, person, or number. Don't summarize â€” interpret."
            )}],
        )
        post = response.content[0].text.strip()
        queue_post(post, post_type="news_oracle", priority=3)
        process_queue(max_posts=1)
        print(f"[Runner] OctoNews post:\n  {post}")
    except Exception as e:
        print(f"[Runner] mode_news failed: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Octodamus Oracle Runner")
    parser.add_argument(
        "--mode",
        choices=["monitor", "daily", "deep_dive", "wisdom", "status", "drain", "alert", "predict", "geo", "pulse", "gecko", "fx", "news", "engage"],
        default="monitor",
        help="Runner mode",
    )
    parser.add_argument("--ticker", type=str, default="NVDA", help="Ticker for deep_dive mode")
    parser.add_argument("--force", action="store_true", help="Bypass posting hours")
    args = parser.parse_args()

    if args.force:
        import octo_x_queue
        octo_x_queue.FORCE_POST = True
        print("[Runner] --force flag set: bypassing posting hours.")

    if args.mode == "monitor":
        mode_monitor()
    elif args.mode == "daily":
        mode_daily()
    elif args.mode == "deep_dive":
        mode_deep_dive(args.ticker)
    elif args.mode == "wisdom":
        mode_wisdom()
    elif args.mode == "status":
        mode_status()
    elif args.mode == "drain":
        posted = process_queue(max_posts=10)
        print(f"[Runner] Drained {posted} posts from queue.")
    elif args.mode == "alert":
        from octo_alert import run_alert_scan
        run_alert_scan(secrets=secrets, claude_client=claude)
    elif args.mode == "predict":
        mode_predict()
    elif args.mode == "geo":
        mode_geo()
    elif args.mode == "pulse":
        mode_pulse()
    elif args.mode == "gecko":
        mode_gecko()
    elif args.mode == "fx":
        mode_fx()
    elif args.mode == "news":
        mode_news()
    elif args.mode == "engage":
        from octo_engage import run
        run(mode="all")


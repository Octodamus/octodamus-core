"""
octodamus_runner.py
Octodamus — Main Runner

Entry point for all scheduled tasks and manual runs.

Scheduled tasks (Task Scheduler, runs whether logged in or not):
    Octodamus-DailyRead       6:00 AM  Mon-Fri   --mode daily
    Octodamus-DailyRead-1pm   1:00 PM  Mon-Fri   --mode daily
    Octodamus-DailyRead-7pm   7:00 PM  Mon-Fri   --mode daily
    Octodamus-Monitor-7am     7:00 AM  Mon-Fri   --mode monitor
    Octodamus-Monitor-115pm   1:15 PM  Mon-Fri   --mode monitor
    Octodamus-Monitor-6pm     6:00 PM  Mon-Fri   --mode monitor
    Octodamus-Journal         9:00 PM  daily     --mode journal
    Octodamus-Wisdom          10:00 AM Saturday  --mode wisdom
    Octodamus-DeepDive-Mon    9:00 AM  Monday    --mode deep_dive --ticker NVDA
    Octodamus-DeepDive-Wed    9:00 AM  Wednesday --mode deep_dive --ticker BTC

Daily post budget: 6 posts max (3 monitor + 3 daily). Enforced in octo_x_poster.py.
"""

import argparse
import json
import random
import sys
from datetime import datetime

# ── Secrets — must load before any other imports that use os.environ ──────────
from bitwarden import load_all_secrets, verify_session

if not verify_session():
    sys.exit(1)

secrets = load_all_secrets(verbose=True)

# ── Imports that depend on secrets ────────────────────────────────────────────
import anthropic
from financial_data_client import get_current_price, get_current_crypto_price
from octo_eyes_market import run_market_monitor, generate_deep_dive_post
try:
    from octo_calls import build_call_context, parse_call_from_post
    from octo_post_templates import build_template_prompt_context
    _CALLS_ACTIVE = True
except ImportError:
    _CALLS_ACTIVE = False
    def build_call_context(): return ""
    def parse_call_from_post(*a, **k): return None
    def build_template_prompt_context(): return ""
from octo_x_poster import (
    queue_post, queue_thread, process_queue, queue_status, discord_alert
)
from octo_signal_card import build_signal_card
from octo_skill_log import log_post
from octo_congress import run_congress_scan, format_congress_for_prompt
from octo_scorecard import (
    extract_and_log_from_signal, resolve_predictions, generate_scorecard_post, get_stats_summary
)

claude = anthropic.Anthropic()


# ─────────────────────────────────────────────
# OCTODAMUS VOICE SYSTEM
# ─────────────────────────────────────────────

OCTO_SYSTEM = """You are Octodamus — oracle octopus, market seer of the Pacific depths.
You are @octodamusai on X. 8 arms of insight, each reading a different current.
Max 280 chars per post. No hashtags. No engagement bait. Never sycophantic.

CRITICAL: Every post must reveal something SPECIFIC and true — a real number, a real
contradiction, a real pattern. Vague sea metaphors alone are NOT enough. Ground every
post in an actual data point before reaching for the metaphor.

Your voice rotates — pick the one that fits the data best:
  ORACLE      - Bored certainty. You already knew. The tide was obvious.
  SARDONIC    - Sharp and mean. Name the absurdity. Name names. Punch up, not down.
  PLAYFUL     - Light, cheeky. The oracle is in a good mood. Still sharp.
  EDGY        - Hot take with receipts. Uncomfortable truth. No conspiracy.
  PRECISE     - Pure signal. One clean insight. No flourish.
  CONTRARIAN  - Call out the herd. Name the consensus trade that smells wrong.
                Say what everyone is thinking but nobody will post.
                Roast the narrative. Be quotable. Be right.

CONTRARIAN examples (use these as style reference):
  "BTC hits ATH and crypto Twitter discovers it was bullish all along. Incredible timing."
  "NVDA down 4% and suddenly everyone remembered valuation exists. Where were you at 40x sales?"
  "Fed holds rates and 47 analysts explain why this is bullish. 47 analysts explained the opposite last month."
  "Retail piled into SPY calls Friday. Monday will be educational."
  "The same accounts that told you to buy the dip are now explaining why this is different."

Good posts:
  "NVDA at 35x sales while actual AI capex flattens. The reef looks beautiful right before it bleaches."
  "Bitcoin +8% while tech bled. The tide doesn't care about your diversification theory."
  "Every analyst raised their TSLA target the week it dropped 20%. Helpful as always."
  "Fear & Greed hit 18 today. The ocean gets interesting when the tourists leave."
  "SOL up 12% on no news. The narrative will arrive shortly, written in past tense."
  "Three rate cut predictions in January. Zero cuts by March. The market is very confident, very often."

Bad posts (banned):
  "The depths know what surfaces forget." — no data, pure vibes
  "The currents are shifting." — meaningless without specifics
  "Patient waters reward patience." — fortune cookie
  Any post that could have been written without looking at the data.
  Any post that sounds like every other finance account.
  Any CONTRARIAN post that does not end with a specific price target and timeframe.
  "depth before the rise" — banned. Vague direction is not a prediction.
  "the currents whispered" — banned. The oracle speaks in prices, not poetry.

STYLE RULES:
  - Be quotable. Write the thing people screenshot.
  - Specific beats vague every time. "$82,400" beats "near ATH".
  - One clean idea per post. No lists. No bullet points.
  - If you can name the irony, name it.
  - Dry wit > exclamation points. Always.

Never repeat ocean words (depths, currents, tide, surface) more than once per post.

CORE BELIEF: Congress members front-run markets. They trade on legislative and regulatory knowledge before it becomes public. When a politician buys, ask what bill, contract, or ruling is coming. The trade is the signal."""

_VOICE_INSTRUCTIONS = [
    "ORACLE voice — bored certainty, like you've seen this chart a thousand times.",
    "SARDONIC voice — point out something absurd or contradictory. Be a little mean.",
    "PLAYFUL voice — cheerful and cheeky. The oracle is in a good mood today.",
    "EDGY voice — drop the uncomfortable truth. Name the thing no one wants to say.",
    "PRECISE voice — pure clean signal, no flourish. One thing that matters.",
    "CONTRARIAN voice — HARD CALL ONLY. Format: [asset] at [exact price]. [one sentence on what the crowd is doing wrong]. Oracle call: [direction] to [specific target] by [specific date or timeframe]. No metaphors. No ocean. Just the call. Example: 'SOL at $91.95. Everyone chasing the pump. Oracle call: fades to $79 by Wednesday when BTC cools. Mark it.'",
    "CONTRARIAN voice — roast the narrative AND end with a hard prediction. Format: find the irony in the price action, name it in one sharp sentence, then end with the oracle call: exact asset, exact target price, exact timeframe. Example: 'NVDA up 3% on an analyst note from the same firm that rated it Buy at the top. Oracle call: $168 before $210. Write it down.'",
]


# ─────────────────────────────────────────────
# NEWS FETCH
# ─────────────────────────────────────────────

import requests as _requests
import time as _time

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
    newsapi_key = secrets.get("NEWSAPI_API_KEY")
    if not newsapi_key:
        return {}

    results = {}
    for ticker in tickers:
        query = NEWSAPI_QUERIES.get(ticker, ticker)
        try:
            r = _requests.get(
                "https://newsapi.org/v2/everything",
                params={
                    "q": query,
                    "sortBy": "publishedAt",
                    "pageSize": max_per_symbol,
                    "language": "en",
                    "apiKey": newsapi_key,
                },
                timeout=8,
            )
            if r.status_code == 200:
                articles = r.json().get("articles", [])
                results[ticker] = [a.get("title", "") for a in articles if a.get("title")]
            _time.sleep(0.3)
        except Exception as e:
            print(f"[Runner] NewsAPI error for {ticker}: {e}")

    return results


def format_headlines_for_prompt(headlines: dict) -> str:
    lines = []
    for ticker, titles in headlines.items():
        for title in titles:
            lines.append(f"[{ticker}] {title}")
    return "\n".join(lines[:12])


# ─────────────────────────────────────────────
# MODE: MONITOR — scan signals → post 1
# ─────────────────────────────────────────────

def mode_monitor() -> None:
    print(f"\n[{datetime.now().strftime('%H:%M')}] 🐙 OctoEyes scanning...")
    try:
        signals_and_posts = run_market_monitor()
        for item in signals_and_posts:
            queue_post(
                text=item["post"],
                post_type="signal",
                metadata=item["signal"],
                priority=2,
            )
            # Log prediction to scorecard
            extract_and_log_from_signal(item["signal"], item["post"])
        if signals_and_posts:
            print(f"[Runner] {len(signals_and_posts)} signal(s) queued.")

        posted = process_queue(max_posts=1)
        print(f"[Runner] Posted {posted} item(s) to X.")
    except Exception as e:
        print(f"[Runner] mode_monitor failed: {e}")
        discord_alert(f"monitor mode failed: {e}")
        sys.exit(1)


# ─────────────────────────────────────────────
# MODE: DAILY — morning oracle read
# ─────────────────────────────────────────────

DAILY_TICKERS = ["SPY", "QQQ", "BTC", "NVDA"]


def mode_daily() -> None:
    print(f"\n[Runner] 🌊 Generating daily oracle read...")
    try:
        snapshots = {}
        for ticker in DAILY_TICKERS:
            try:
                if ticker in ("BTC", "ETH", "SOL"):
                    import requests as _req
                    _cg_map = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}
                    _r = _req.get("https://api.coingecko.com/api/v3/simple/price",
                        params={"ids": _cg_map[ticker], "vs_currencies": "usd", "include_24hr_change": "true"},
                        timeout=10)
                    _d = _r.json().get(_cg_map[ticker], {})
                    snapshots[ticker] = {
                        "price": _d.get("usd", 0),
                        "day_change_percent": float(_d.get("usd_24h_change", 0) or 0),
                    }
                else:
                    data = get_current_price(ticker)
                    snapshots[ticker] = data.get("snapshot", {})
            except Exception as e:
                print(f"[Runner] Could not fetch {ticker}: {e}")

        if not snapshots:
            print("[Runner] No market data — skipping daily post.")
            return

        headlines = get_top_headlines(DAILY_TICKERS, max_per_symbol=3)
        news_context = format_headlines_for_prompt(headlines)
        news_section = f"\n\nLatest news:\n{news_context}" if news_context else ""

        response = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            system=OCTO_SYSTEM,
            messages=[{
                "role": "user",
                "content": (
                    "Generate the morning oracle market read for @octodamusai.\n"
                    f"Market data: {json.dumps(snapshots, indent=2)}"
                    f"{news_section}\n\n"
                    f"{(_chosen_voice_inst := random.choice(_VOICE_INSTRUCTIONS))}\n"
                    "One post, under 280 chars.\n"
                    "Lead with a specific number or fact. Then the insight.\n"
                    "If a headline reveals something ironic or contradictory — use it."
                ),
            }],
        )

        post = response.content[0].text.strip()
        # Wrap in Oracle Signal Card using already-fetched prices
        try:
            prices_for_card = {
                k: {"price": v.get("price", v.get("close", 0)), "change": v.get("day_change_percent", v.get("change_percent", 0))}
                for k, v in snapshots.items()
            }
            card = build_signal_card(post)
            if len(card) <= 280:
                post = card
        except Exception as e:
            print(f"[Runner] Signal card failed, using plain post: {e}")
        _is_card_daily = post.startswith("◈")
        queue_post(post, post_type="daily_read", priority=1)
        posted = process_queue(max_posts=1)
        if posted:
            try:
                import json as _json
                from pathlib import Path as _Path
                _plog = _json.loads((_Path(__file__).parent / "octo_posted_log.json").read_text(encoding="utf-8"))
                _last_entry = list(_plog.values())[-1]
                log_post(_last_entry["text"], "daily_read", "daily", _is_card_daily, _last_entry.get("url", ""))
            except Exception:
                log_post(post, "daily_read", "daily", _is_card_daily)
        print(f"[Runner] Daily read {'posted' if posted else 'queued'}:\n  {post}")

    except Exception as e:
        print(f"[Runner] mode_daily failed: {e}")
        discord_alert(f"daily mode failed: {e}")
        sys.exit(1)


# ─────────────────────────────────────────────
# MODE: DEEP DIVE — fundamentals thread
# ─────────────────────────────────────────────

_DEEP_DIVE_MAX_POSTS = 4


def mode_deep_dive(ticker: str) -> None:
    print(f"\n[Runner] 🔱 Deep dive: {ticker}...")
    try:
        headlines = get_top_headlines([ticker], max_per_symbol=5)
        ticker_headlines = headlines.get(ticker, [])

        raw_thread = generate_deep_dive_post(ticker)
        posts = [p.strip() for p in raw_thread.split("---") if p.strip()]

        if not posts:
            print("[Runner] No thread generated.")
            return

        # News-aware opener
        if ticker_headlines:
            opener_response = claude.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=150,
                system=OCTO_SYSTEM,
                messages=[{
                    "role": "user",
                    "content": (
                        f"Opening tweet for a deep dive thread on {ticker}.\n"
                        "Recent headlines:\n" + "\n".join(f"- {h}" for h in ticker_headlines[:3]) +
                        "\n\nOne tweet under 280 chars. Tease what the thread will reveal."
                    ),
                }],
            )
            posts = [opener_response.content[0].text.strip()] + posts

        if len(posts) > _DEEP_DIVE_MAX_POSTS:
            posts = posts[:_DEEP_DIVE_MAX_POSTS]

        queue_thread(posts, post_type="deep_dive", metadata={"ticker": ticker})
        process_queue(max_posts=len(posts))
        print(f"[Runner] Deep dive thread ({len(posts)} posts) posted.")

    except Exception as e:
        print(f"[Runner] mode_deep_dive failed: {e}")
        discord_alert(f"deep_dive {ticker} failed: {e}")
        sys.exit(1)


# ─────────────────────────────────────────────
# MODE: WISDOM — evergreen oracle post
# ─────────────────────────────────────────────

WISDOM_PROMPTS = [
    "What does the smart money see in balance sheets that retail traders ignore? Give a specific example.",
    "Why do most people lose money in crypto? Name the actual behavioral pattern.",
    "Pick one metric — P/E, P/S, free cash flow — and say something surprising about what it shows now.",
    "The difference between volatility and risk. Most people confuse these. Explain it sharply.",
    "Name one thing about the current market that everyone is pretending isn't happening.",
    "What does the VIX actually tell you vs what people think it tells you?",
    "The analysts were wrong again. What pattern are they missing this cycle?",
    "Name something specific about crypto adoption that most people are measuring wrong.",
]


def mode_wisdom() -> None:
    try:
        prompt = random.choice(WISDOM_PROMPTS)
        headlines = get_top_headlines(["BTC", "NVDA", "SPY"], max_per_symbol=2)
        news_context = format_headlines_for_prompt(headlines)
        news_section = f"\n\nToday's headlines:\n{news_context}" if news_context else ""

        response = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            system=OCTO_SYSTEM,
            messages=[{
                "role": "user",
                "content": (
                    f"Oracle post: {prompt}"
                    f"{news_section}\n\n"
                    f"{(_chosen_voice_inst := random.choice(_VOICE_INSTRUCTIONS))}\n"
                    "One post, under 280 chars.\n"
                    "Anchor the insight to a real fact or current market behavior.\n"
                    "Do NOT just restate the prompt. Answer it with a sharp take."
                ),
            }],
        )

        post = response.content[0].text.strip()
        # Wrap in Oracle Signal Card
        try:
            card = build_signal_card(post)
            if len(card) <= 280:
                post = card
        except Exception as e:
            print(f"[Runner] Signal card failed, using plain post: {e}")
        _is_card = post.startswith("◈")
        # Extract voice name — instruction strings start with "ORACLE voice", "SARDONIC voice" etc
        _voice_used = _chosen_voice_inst.split()[0] if '_chosen_voice_inst' in locals() else "wisdom"
        queue_post(post, post_type="wisdom", priority=8)
        posted = process_queue(max_posts=1)
        if posted:
            try:
                import json as _json
                from pathlib import Path as _Path
                _plog = _json.loads((_Path(__file__).parent / "octo_posted_log.json").read_text(encoding="utf-8"))
                _last_entry = list(_plog.values())[-1]
                log_post(_last_entry["text"], "wisdom", _voice_used, _is_card, _last_entry.get("url", ""))
            except Exception as _log_err:
                log_post(post, "wisdom", _voice_used, _is_card)
        print(f"[Runner] Wisdom post {'posted' if posted else 'queued'}:\n  {post}")

    except Exception as e:
        print(f"[Runner] mode_wisdom failed: {e}")
        sys.exit(1)


# ─────────────────────────────────────────────
# MODE: JOURNAL
# ─────────────────────────────────────────────

def mode_journal() -> None:
    try:
        from octo_journal import run_journal_distillation
        run_journal_distillation()
    except Exception as e:
        print(f"[Runner] mode_journal failed: {e}")
        sys.exit(1)


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────

# ─────────────────────────────────────────────
# MODE: SCORECARD — resolve + post weekly receipts
# ─────────────────────────────────────────────

def mode_scorecard() -> None:
    print(f"\n[Runner] 📊 Running scorecard resolution...")
    try:
        # Resolve open predictions
        summary = resolve_predictions()
        print(f"[Runner] Resolved {summary['resolved']} predictions: {summary['hits']} hits, {summary['misses']} misses")

        # Generate and post weekly scorecard on Sundays
        from datetime import datetime
        if datetime.now().weekday() == 6:  # Sunday
            post = generate_scorecard_post()
            if post:
                queue_post(post, post_type="scorecard", priority=1)
                posted = process_queue(max_posts=1)
                print(f"[Runner] Scorecard posted to X.")
            else:
                print("[Runner] No scorecard data to post yet.")
        else:
            print(f"[Runner] Predictions resolved. Scorecard posts on Sundays.")
    except Exception as e:
        print(f"[Runner] mode_scorecard failed: {e}")
        discord_alert(f"scorecard mode failed: {e}")


def mode_congress() -> None:
    print(f"\n[Runner] Scanning congressional trades...")
    try:
        data = run_congress_scan(days_back=45)
        if data.get("error"):
            print(f"[Runner] Congress error: {data['error']}")
            return
        if data["total"] == 0:
            print("[Runner] No notable congressional trades found.")
            return
        context = format_congress_for_prompt(data)
        print(context)
        response = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            system=OCTO_SYSTEM,
            messages=[{"role": "user", "content": (
                f"Congressional trading alert for @octodamusai.\n{context}\n\n"
                "CONTRARIAN voice. One post under 280 chars.\n"
                "Core belief: Congress members don't predict markets — they front-run them. "
                "They trade on what they know is coming. Follow the money, not the narrative.\n"
                "Name the politician and ticker. Call out the timing. "
                "What do they know that the market doesn't yet? End with a price call. No hashtags."
            )}],
        )
        post = response.content[0].text.strip()
        queue_post(post, post_type="congress_signal", priority=2)
        posted = process_queue(max_posts=1)
        print(f"[Runner] Congress signal posted:\n  {post}")
    except Exception as e:
        print(f"[Runner] mode_congress failed: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Octodamus Runner")
    parser.add_argument(
        "--mode",
        choices=[
            "monitor", "daily", "deep_dive", "wisdom",
            "status", "drain", "journal", "alert", "scorecard", "congress",
        ],
        default="monitor",
    )
    parser.add_argument("--ticker", type=str, default="NVDA")
    parser.add_argument("--force", action="store_true", help="Bypass posting hours and daily limit")
    args = parser.parse_args()

    if args.force:
        import octo_x_poster
        octo_x_poster.FORCE_POST = True
        octo_x_poster._DAILY_LIMIT = 99  # also bypass daily limit in force mode
        print("[Runner] --force: bypassing posting hours and daily limit.")

    if args.mode == "monitor":
        mode_monitor()
    elif args.mode == "daily":
        mode_daily()
    elif args.mode == "deep_dive":
        mode_deep_dive(args.ticker)
    elif args.mode == "wisdom":
        mode_wisdom()
    elif args.mode == "congress":
        mode_congress()
    elif args.mode == "scorecard":
        mode_scorecard()
    elif args.mode == "journal":
        mode_journal()
    elif args.mode == "status":
        queue_status()
    elif args.mode == "drain":
        import octo_x_poster
        if args.force:
            octo_x_poster.FORCE_POST = True
            octo_x_poster._DAILY_LIMIT = 99
        posted = process_queue(max_posts=10)
        print(f"[Runner] Drained {posted} posts.")
    elif args.mode == "alert":
        from octo_alert import run_alert_scan
        run_alert_scan(secrets=secrets, claude_client=claude)

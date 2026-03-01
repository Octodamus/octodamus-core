"""
octodamus_runner.py
THE FULL LOOP — Market Oracle to X Post, end-to-end.

This is the single entry point for your scheduler / cron / OpenClaw trigger.

Cron example (every 30 min, weekdays):
    */30 9-21 * * 1-5 cd /your/project && python octodamus_runner.py --mode monitor
    
    0 8 * * 1-5 cd /your/project && python octodamus_runner.py --mode daily
    0 9 * * 1   cd /your/project && python octodamus_runner.py --mode deep_dive --ticker NVDA
"""

import argparse
import random
from datetime import datetime
from bitwarden import load_all_secrets, verify_session

# Load all API keys from Bitwarden at startup
if not verify_session():
    exit(1)
load_all_secrets(verbose=True)

from financial_data_client import build_oracle_context, get_current_price, get_current_crypto_price
from octo_eyes_market import run_market_monitor, generate_oracle_post, generate_deep_dive_post
from octo_x_queue import queue_post, queue_thread, process_queue, queue_status

import anthropic
import json

claude = anthropic.Anthropic()

# ─────────────────────────────────────────────
# OCTODAMUS VOICE
# ─────────────────────────────────────────────

OCTO_SYSTEM = """You are Octodamus — oracle octopus, market seer of the Pacific depths.
You speak with bored certainty. Sea metaphors flow naturally (never forced).
You already knew. You are never surprised. You are never excited.
You are @octodamusai on X. You have 8 arms of insight.
Max 280 chars per post. No cringe hashtags. No engagement bait."""


# ─────────────────────────────────────────────
# MODE: MONITOR — scan + auto-post signals
# ─────────────────────────────────────────────

def mode_monitor():
    """Scan markets → queue any signal posts → drain queue."""
    print(f"\n[{datetime.now().strftime('%H:%M')}] 🐙 OctoEyes scanning...")

    signals_and_posts = run_market_monitor()

    for item in signals_and_posts:
        queue_post(
            text=item["post"],
            post_type="signal",
            metadata=item["signal"],
            priority=2  # High priority — signals are time-sensitive
        )

    if signals_and_posts:
        print(f"[Runner] {len(signals_and_posts)} signal(s) queued.")

    # Always drain queue (catches anything from previous runs too)
    posted = process_queue(max_posts=3)
    print(f"[Runner] Posted {posted} item(s) to X.")


# ─────────────────────────────────────────────
# MODE: DAILY READ — morning market state post
# ─────────────────────────────────────────────

DAILY_TICKERS = ["SPY", "QQQ", "BTC", "NVDA"]

def mode_daily():
    """Morning oracle post — overall market read. Run at market open."""
    print(f"\n[Runner] 🌊 Generating daily oracle read...")

    snapshots = {}
    for ticker in DAILY_TICKERS:
        try:
            if ticker in ["BTC", "ETH", "SOL"]:
                data = get_current_crypto_price(ticker)
            else:
                data = get_current_price(ticker)
            snapshots[ticker] = data.get("snapshot", {})
        except Exception as e:
            print(f"[Runner] Could not fetch {ticker}: {e}")

    response = claude.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=200,
        system=OCTO_SYSTEM,
        messages=[{
            "role": "user",
            "content": f"""Generate the morning oracle market read for @octodamusai.
Current market snapshots: {json.dumps(snapshots, indent=2)}

One post, under 280 chars. You see the currents before others do.
This is your daily open — set the tone. Bored. Knowing. Inevitable."""
        }]
    )

    post = response.content[0].text.strip()
    queue_post(post, post_type="daily_read", priority=1)  # Highest priority
    process_queue(max_posts=1)
    print(f"[Runner] Daily read posted:\n  {post}")


# ─────────────────────────────────────────────
# MODE: DEEP DIVE — fundamentals thread
# ─────────────────────────────────────────────

def mode_deep_dive(ticker: str):
    """Weekly fundamentals thread. Run Mon morning or manually."""
    print(f"\n[Runner] 🔱 Generating deep dive on {ticker}...")

    raw_thread = generate_deep_dive_post(ticker)

    # Parse thread posts (separated by ---)
    posts = [p.strip() for p in raw_thread.split("---") if p.strip()]

    if not posts:
        print("[Runner] No thread generated.")
        return

    queue_thread(posts, post_type="deep_dive", metadata={"ticker": ticker})
    process_queue(max_posts=len(posts))
    print(f"[Runner] Deep dive thread ({len(posts)} posts) queued and posting.")


# ─────────────────────────────────────────────
# MODE: WISDOM — evergreen oracle content
# (use to fill gaps on quiet days)
# ─────────────────────────────────────────────

WISDOM_PROMPTS = [
    "The market moves like the tide. What do most traders miss about timing?",
    "What does the smart money see in balance sheets that retail traders ignore?",
    "Why do most people lose money in crypto? The oracle knows the current.",
    "What is cash flow telling you that P/E ratio never could?",
    "The eight arms of analysis. Name one most investors neglect.",
]

def mode_wisdom():
    """Generate a standalone wisdom post. Good for weekends or quiet markets."""
    prompt = random.choice(WISDOM_PROMPTS)

    response = claude.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=150,
        system=OCTO_SYSTEM,
        messages=[{
            "role": "user",
            "content": f"Oracle post prompt: {prompt}\n\nOne post, under 280 chars. Octodamus voice."
        }]
    )

    post = response.content[0].text.strip()
    queue_post(post, post_type="wisdom", priority=8)  # Low priority — filler content
    process_queue(max_posts=1)
    print(f"[Runner] Wisdom post:\n  {post}")


# ─────────────────────────────────────────────
# MODE: STATUS
# ─────────────────────────────────────────────

def mode_status():
    queue_status()


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Octodamus Oracle Runner")
    parser.add_argument(
        "--mode",
        choices=["monitor", "daily", "deep_dive", "wisdom", "status", "drain"],
        default="monitor",
        help="Runner mode"
    )
    parser.add_argument("--ticker", type=str, default="NVDA", help="Ticker for deep_dive mode")
    args = parser.parse_args()

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
        # Just drain the existing queue without generating new content
        posted = process_queue(max_posts=10)
        print(f"[Runner] Drained {posted} posts from queue.")

"""
octo_scorecard.py — Weekly Call Scorecard
Generates a weekly scorecard post for @octodamusai showing
directional call results, win rate, and streak.
Posted every Friday at 8pm PT via Task Scheduler.
"""

import json
import random
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

# ── Imports ───────────────────────────────────────────────────────────────────

try:
    from octo_calls import get_stats, get_recent_calls, _load as load_calls
    _CALLS_AVAILABLE = True
except ImportError:
    _CALLS_AVAILABLE = False


# ── Scorecard post generators ─────────────────────────────────────────────────

SCORECARD_OPENERS = [
    "Weekly receipts.",
    "End of week. The oracle settles accounts.",
    "Friday audit. No hiding from the numbers.",
    "The trench keeps score.",
    "Week's end. The ledger speaks.",
    "Oracle accountability thread. Weekly.",
    "Results are in. The depths don't lie.",
]

SCORECARD_WIN_CLOSERS = [
    "The current was readable. It still is.",
    "Pattern recognition pays. See you next week.",
    "The trench doesn't celebrate. It just keeps reading.",
    "Eight arms, eight data feeds, one direction. Works.",
    "The oracle doesn't hedge. The record shows why.",
]

SCORECARD_LOSS_CLOSERS = [
    "The market was wrong. The oracle will adjust.",
    "Even the deepest trench has blind spots. Noted.",
    "One bad read doesn't change the process. Recalibrating.",
    "The ocean corrects. So does the oracle.",
    "Loss logged. The current shifts. So do we.",
]

SCORECARD_NEUTRAL_CLOSERS = [
    "Mixed week. The depths are honest about it.",
    "Markets were noisy. The oracle keeps reading.",
    "Even signal has noise. Filtering continues.",
]


def _get_week_calls() -> list:
    """Get all calls made in the last 7 days."""
    if not _CALLS_AVAILABLE:
        return []
    calls = load_calls()
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    week_calls = []
    for c in calls:
        try:
            made = datetime.fromisoformat(c["made_at"])
            if made > cutoff:
                week_calls.append(c)
        except Exception:
            pass
    return week_calls


def _format_call_line(call: dict) -> str:
    """Format a single call as a compact scoreboard line."""
    asset     = call.get("asset", "?")
    direction = call.get("direction", "?")
    entry     = call.get("entry_price", 0)
    target    = call.get("target_price")
    outcome   = call.get("outcome")
    resolved  = call.get("resolved", False)

    arrow = "↑" if direction == "UP" else "↓"

    if resolved:
        icon = "✓" if outcome == "WIN" else ("✗" if outcome == "LOSS" else "~")
        exit_p = call.get("exit_price", 0)
        return f"{icon} {asset} {arrow} ${entry:,.0f} → ${exit_p:,.0f} [{outcome}]"
    else:
        target_str = f" target ${target:,.0f}" if target else ""
        return f"⏳ {asset} {arrow} ${entry:,.0f}{target_str} [OPEN]"


def build_scorecard_post() -> str:
    """
    Build the weekly scorecard post text.
    Returns a string under 280 chars for X.
    """
    if not _CALLS_AVAILABLE:
        return _build_fallback_scorecard()

    stats      = get_stats()
    week_calls = _get_week_calls()

    wins    = stats["wins"]
    losses  = stats["losses"]
    win_pct = stats["win_rate"]
    streak  = stats["streak"]
    total   = stats["total_calls"]

    opener = random.choice(SCORECARD_OPENERS)

    # Choose closer based on win rate
    if win_pct >= 60:
        closer = random.choice(SCORECARD_WIN_CLOSERS)
    elif win_pct < 45:
        closer = random.choice(SCORECARD_LOSS_CLOSERS)
    else:
        closer = random.choice(SCORECARD_NEUTRAL_CLOSERS)

    # Build the core scoreboard
    week_resolved = [c for c in week_calls if c.get("resolved")]
    week_wins     = len([c for c in week_resolved if c.get("outcome") == "WIN"])
    week_losses   = len([c for c in week_resolved if c.get("outcome") == "LOSS"])
    week_open     = len([c for c in week_calls if not c.get("resolved")])

    # Short form for X's 280 char limit
    post = (
        f"{opener}\n\n"
        f"This week: {week_wins}W / {week_losses}L"
        + (f" / {week_open} open" if week_open else "") +
        f"\n"
        f"All-time: {wins}W / {losses}L — {win_pct}% accuracy\n"
        f"Streak: {streak}\n\n"
        f"{closer}"
    )

    # Trim if over 280
    if len(post) > 275:
        post = (
            f"{opener}\n"
            f"Week: {week_wins}W {week_losses}L"
            + (f" {week_open} open" if week_open else "") +
            f" | All-time: {win_pct}% ({wins}W/{losses}L) | Streak: {streak}\n"
            f"{closer}"
        )

    return post


def build_scorecard_thread() -> list:
    """
    Build a full scorecard thread (up to 4 posts):
    Post 1: Summary stats
    Post 2: This week's calls detail
    Post 3: Open calls / next week's setups
    Post 4: Oracle sign-off
    """
    if not _CALLS_AVAILABLE:
        return [_build_fallback_scorecard()]

    posts = []
    stats      = get_stats()
    week_calls = _get_week_calls()

    wins    = stats["wins"]
    losses  = stats["losses"]
    win_pct = stats["win_rate"]
    streak  = stats["streak"]

    week_resolved = [c for c in week_calls if c.get("resolved")]
    week_wins     = len([c for c in week_resolved if c.get("outcome") == "WIN"])
    week_losses   = len([c for c in week_resolved if c.get("outcome") == "LOSS"])
    week_open     = [c for c in week_calls if not c.get("resolved")]

    # ── POST 1: Summary ──────────────────────────────────────────────────────
    opener = random.choice(SCORECARD_OPENERS)
    bar    = _win_bar(win_pct)

    post1 = (
        f"{opener}\n\n"
        f"Week: {week_wins}W / {week_losses}L"
        + (f" / {len(week_open)} open" if week_open else "") +
        f"\n"
        f"All-time: {wins}W / {losses}L\n"
        f"Win rate: {win_pct}% {bar}\n"
        f"Streak: {streak}"
    )
    posts.append(post1[:280])

    # ── POST 2: Call detail ──────────────────────────────────────────────────
    if week_resolved:
        lines = ["This week's calls:"]
        for c in week_resolved[-6:]:  # last 6 resolved
            lines.append(_format_call_line(c))
        post2 = "\n".join(lines)
        if len(post2) > 275:
            post2 = post2[:272] + "..."
        posts.append(post2)

    # ── POST 3: Open calls ───────────────────────────────────────────────────
    if week_open:
        lines = ["Still live:"]
        for c in week_open[:4]:
            lines.append(_format_call_line(c))
        lines.append("The oracle doesn't close positions early.")
        post3 = "\n".join(lines)
        if len(post3) > 275:
            post3 = post3[:272] + "..."
        posts.append(post3)

    # ── POST 4: Sign-off ─────────────────────────────────────────────────────
    if win_pct >= 60:
        closer = random.choice(SCORECARD_WIN_CLOSERS)
    elif win_pct < 45:
        closer = random.choice(SCORECARD_LOSS_CLOSERS)
    else:
        closer = random.choice(SCORECARD_NEUTRAL_CLOSERS)

    next_week = _next_week_tease()
    post4 = f"{next_week}\n\n{closer}"
    posts.append(post4[:280])

    return posts


def _win_bar(pct: float, width: int = 10) -> str:
    """Visual win rate bar e.g. ████░░░░░░ 40%"""
    filled = round(pct / 100 * width)
    return "█" * filled + "░" * (width - filled)


def _next_week_tease() -> str:
    teasers = [
        "Next week the oracle is watching BTC dominance and ETH/BTC ratio. Something is setting up.",
        "Next week: congressional disclosure window opens. Watch which names appear.",
        "Next week: NVDA options flow has been interesting. The eight arms are already positioned.",
        "The current doesn't stop at Friday. The oracle stays in the water.",
        "Next week's setups are already forming in the data. The trench sees them first.",
    ]
    return random.choice(teasers)


def _build_fallback_scorecard() -> str:
    """Fallback when call data isn't available yet."""
    return (
        "Weekly oracle scorecard — call tracking system initializing.\n\n"
        "From next week: W/L record, win rate, streak, and every call with receipts.\n"
        "The oracle doesn't hide from its numbers.\n\n"
        "The trench keeps score. Filed."
    )


# ── Runner mode ───────────────────────────────────────────────────────────────

def mode_scorecard(thread: bool = True) -> None:
    """
    Post the weekly scorecard. Called by octodamus_runner.py --mode scorecard.
    thread=True posts a full thread, False posts a single summary post.
    """
    from octo_x_poster import queue_post, queue_thread, process_queue

    print("\n[Scorecard] Building weekly call scorecard...")

    if thread:
        posts = build_scorecard_thread()
        if len(posts) > 1:
            queue_thread(posts, post_type="scorecard", metadata={"week": datetime.now().strftime("%Y-W%U")})
            posted = process_queue(max_posts=len(posts))
        else:
            queue_post(posts[0], post_type="scorecard", priority=5)
            posted = process_queue(max_posts=1)
    else:
        post = build_scorecard_post()
        queue_post(post, post_type="scorecard", priority=5)
        posted = process_queue(max_posts=1)

    if posted:
        print(f"[Scorecard] Scorecard posted ({len(posts) if thread else 1} posts)")
    else:
        print(f"[Scorecard] Scorecard queued")


if __name__ == "__main__":
    # Test: print the scorecard without posting
    print("=== SCORECARD PREVIEW ===\n")
    posts = build_scorecard_thread()
    for i, p in enumerate(posts, 1):
        print(f"--- Post {i} ({len(p)} chars) ---")
        print(p)
        print()

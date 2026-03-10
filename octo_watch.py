"""
octo_watch.py
OctoWatch — Social Sentiment Mind

Reads Reddit public JSON feeds — no API key required.
Uses Reddit's public .json endpoint (rate limit: ~60 req/min with User-Agent).

Subreddits tracked:
  - r/wallstreetbets    (retail equity sentiment)
  - r/CryptoCurrency    (crypto community mood)
  - r/investing         (long-term investor tone)
  - r/stocks            (general equities discussion)
  - r/Bitcoin           (BTC community)

Scoring: scans post titles + flair for sentiment keywords
Returns per-subreddit score + overall market mood.

No Bitwarden key needed. Reddit JSON is publicly accessible.

Usage:
    from octo_watch import run_sentiment_scan, format_watch_for_prompt
    watch = run_sentiment_scan()
"""

import time
import requests
from datetime import datetime

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

REDDIT_BASE = "https://www.reddit.com/r/{sub}/hot.json"

# Subreddits to monitor with their asset focus
SUBREDDITS = {
    "wallstreetbets": {"focus": "equities", "weight": 1.5},
    "CryptoCurrency":  {"focus": "crypto",   "weight": 1.2},
    "investing":       {"focus": "equities", "weight": 1.0},
    "stocks":          {"focus": "equities", "weight": 1.0},
    "Bitcoin":         {"focus": "BTC",      "weight": 1.0},
}

# Posts to sample per subreddit
POSTS_PER_SUB = 25

# Reddit requires a real User-Agent — standard for public JSON access
HEADERS = {
    "User-Agent": "octodamus-market-oracle/1.0 (autonomous AI agent; @octodamusai)"
}

_REQUEST_DELAY = 1.5  # Be polite — Reddit rate limits aggressively

# ── Sentiment keyword dictionaries ────────────────
BULL_KEYWORDS = [
    "moon", "mooning", "🚀", "bull", "bullish", "breakout", "pump", "long",
    "buy", "buying", "bought", "calls", "yolo", "gains", "rip", "ath",
    "all time high", "surging", "rally", "green", "up", "higher", "squeeze",
    "send it", "🟢", "📈", "🤑", "fire", "🔥", "massive", "exploding",
    "ripping", "to the moon", "let's go", "hold", "diamond hands",
]

BEAR_KEYWORDS = [
    "crash", "dump", "bear", "bearish", "puts", "short", "selling", "sold",
    "rip", "correction", "recession", "panic", "fear", "falling", "down",
    "red", "loss", "losses", "collapse", "bubble", "overvalued", "dead",
    "drill", "bleeding", "tanking", "plummeting", "🔻", "📉", "🩸", "💀",
    "margin call", "rug", "rugged", "rugpull", "hack", "exploit", "bankrupt",
]

NEUTRAL_DAMPENERS = ["discussion", "daily", "weekly", "question", "help", "advice", "how"]


# ─────────────────────────────────────────────
# SCORING
# ─────────────────────────────────────────────

def _score_text(text: str) -> int:
    """
    Score a piece of text for sentiment.
    Returns: positive int (bullish), negative int (bearish), 0 (neutral).
    """
    lower = text.lower()
    bull_hits = sum(1 for kw in BULL_KEYWORDS if kw in lower)
    bear_hits = sum(1 for kw in BEAR_KEYWORDS if kw in lower)
    neutral_hits = sum(1 for kw in NEUTRAL_DAMPENERS if kw in lower)

    raw = bull_hits - bear_hits
    # Dampen if it reads like a neutral/info thread
    if neutral_hits >= 2:
        raw = int(raw * 0.5)
    return raw


def _score_post(post: dict) -> dict:
    """Score a single Reddit post (title + flair). Returns score dict."""
    title = post.get("title", "")
    flair = post.get("link_flair_text") or ""
    score = post.get("score", 0)         # Reddit upvotes
    comments = post.get("num_comments", 0)

    sentiment = _score_text(f"{title} {flair}")

    # Weight high-engagement posts more
    engagement_mult = 1.0
    if score > 5000:
        engagement_mult = 2.0
    elif score > 1000:
        engagement_mult = 1.5

    return {
        "title": title[:80],
        "sentiment_raw": sentiment,
        "sentiment_weighted": round(sentiment * engagement_mult, 2),
        "upvotes": score,
        "comments": comments,
    }


# ─────────────────────────────────────────────
# SUBREDDIT FETCH
# ─────────────────────────────────────────────

def _fetch_subreddit(sub: str, limit: int = POSTS_PER_SUB) -> list[dict]:
    """Fetch hot posts from a subreddit. Returns list of post dicts."""
    url = REDDIT_BASE.format(sub=sub)
    try:
        r = requests.get(
            url,
            params={"limit": limit},
            headers=HEADERS,
            timeout=12,
        )
        if r.status_code == 429:
            print(f"[OctoWatch] Rate limited on r/{sub} — backing off 10s")
            time.sleep(10)
            return []
        r.raise_for_status()
        data = r.json()
        posts = data.get("data", {}).get("children", [])
        return [p.get("data", {}) for p in posts]
    except Exception as e:
        print(f"[OctoWatch] Failed to fetch r/{sub}: {e}")
        return []


# ─────────────────────────────────────────────
# MAIN SCAN
# ─────────────────────────────────────────────

def run_sentiment_scan(subreddits: dict | None = None) -> dict:
    """
    Scan all tracked subreddits and compute market mood.
    Returns structured dict with per-sub scores + overall mood.
    """
    if subreddits is None:
        subreddits = SUBREDDITS

    print(f"[OctoWatch] Scanning {len(subreddits)} subreddits...")
    sub_results = {}
    total_weighted = 0.0
    total_weight = 0.0

    for sub, meta in subreddits.items():
        posts = _fetch_subreddit(sub)
        if not posts:
            print(f"  r/{sub}: [no data]")
            sub_results[sub] = {"error": "no_data"}
            time.sleep(_REQUEST_DELAY)
            continue

        scored = [_score_post(p) for p in posts]
        # Filter out zero-sentiment posts for signal clarity
        opinionated = [s for s in scored if s["sentiment_raw"] != 0]

        if not opinionated:
            sub_score = 0.0
        else:
            sub_score = round(
                sum(s["sentiment_weighted"] for s in opinionated) / len(opinionated), 3
            )

        weight = meta["weight"]
        total_weighted += sub_score * weight
        total_weight += weight

        # Top signal posts
        top_bull = sorted(opinionated, key=lambda x: x["sentiment_weighted"], reverse=True)[:2]
        top_bear = sorted(opinionated, key=lambda x: x["sentiment_weighted"])[:2]

        sub_results[sub] = {
            "focus": meta["focus"],
            "posts_sampled": len(posts),
            "sentiment_score": sub_score,
            "top_bullish": [p["title"] for p in top_bull if p["sentiment_weighted"] > 0],
            "top_bearish": [p["title"] for p in top_bear if p["sentiment_weighted"] < 0],
        }

        mood_label = "🟢" if sub_score > 0.3 else ("🔴" if sub_score < -0.3 else "⚪")
        print(f"  r/{sub}: {mood_label} score={sub_score:+.3f} ({len(posts)} posts)")
        time.sleep(_REQUEST_DELAY)

    # Overall composite score
    composite = round(total_weighted / total_weight, 3) if total_weight > 0 else 0.0

    # Map to mood label
    if composite >= 0.5:
        mood = "EUPHORIC"
    elif composite >= 0.2:
        mood = "BULLISH"
    elif composite >= -0.2:
        mood = "NEUTRAL"
    elif composite >= -0.5:
        mood = "BEARISH"
    else:
        mood = "FEARFUL"

    print(f"[OctoWatch] Social mood: {mood} (composite={composite:+.3f})")

    return {
        "timestamp": datetime.utcnow().isoformat(),
        "composite_score": composite,
        "mood": mood,
        "subreddits": sub_results,
    }


def format_watch_for_prompt(result: dict) -> str:
    """Format OctoWatch results into a compact prompt string for the LLM."""
    if not result.get("subreddits"):
        return ""

    mood = result.get("mood", "UNKNOWN")
    composite = result.get("composite_score", 0)
    lines = [f"Social sentiment (OctoWatch) — Mood: {mood} ({composite:+.3f})"]

    for sub, data in result.get("subreddits", {}).items():
        if data.get("error"):
            continue
        score = data["sentiment_score"]
        label = "↑" if score > 0.2 else ("↓" if score < -0.2 else "→")
        lines.append(f"  r/{sub}: {label} {score:+.3f}")

    # Surface the strongest signal titles
    all_bull = []
    all_bear = []
    for data in result.get("subreddits", {}).values():
        if isinstance(data, dict) and not data.get("error"):
            all_bull.extend(data.get("top_bullish", []))
            all_bear.extend(data.get("top_bearish", []))

    if all_bull:
        lines.append(f"  Bullish signal: \"{all_bull[0][:70]}\"")
    if all_bear:
        lines.append(f"  Bearish signal: \"{all_bear[0][:70]}\"")

    return "\n".join(lines)


# ─────────────────────────────────────────────
# STANDALONE RUN
# ─────────────────────────────────────────────

if __name__ == "__main__":
    result = run_sentiment_scan()
    print(f"\n── OctoWatch Report ──────────────────────")
    print(f"Overall mood: {result['mood']} (score: {result['composite_score']:+.3f})")
    for sub, data in result["subreddits"].items():
        if not data.get("error"):
            print(f"\n  r/{sub} (score {data['sentiment_score']:+.3f})")
            for t in data.get("top_bullish", []):
                print(f"    🟢 {t}")
            for t in data.get("top_bearish", []):
                print(f"    🔴 {t}")

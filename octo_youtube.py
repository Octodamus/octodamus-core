"""
octo_youtube.py
OctoTube — Retail Attention & Creator Sentiment Mind

YouTube Data API v3 — free tier (10,000 units/day, ~100 searches or 1000 fetches).
Get your key at: console.cloud.google.com → Enable YouTube Data API v3

What this tracks:
  1. Trending financial/crypto videos — what retail is being force-fed by the algorithm
  2. Monitored channels — upload frequency and title sentiment from key creators
     (Coin Bureau, Meet Kevin, Andrei Jikh, Graham Stephan, BitBoy, InvestAnswers, etc.)
  3. Comment sentiment on top recent videos — word/phrase frequency analysis
  4. Creator sentiment shift — when bullish creators go bearish (or vice versa) it's
     a leading indicator for retail sentiment change

Why this matters:
  YouTube view counts and comment velocity are leading indicators — retail
  watches a video, then buys/sells, then it shows up in order flow.
  OctoWatch already covers Reddit. OctoTube covers what Reddit watches.

Bitwarden key: AGENT - Octodamus - YouTube Data API
Env var:       YOUTUBE_API_KEY

Install:
    pip3 install google-api-python-client --break-system-packages

Usage:
    from octo_youtube import run_youtube_scan, format_youtube_for_prompt
    yt = run_youtube_scan()
"""

import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

YT_BASE    = "https://www.googleapis.com/youtube/v3"
MAX_RESULTS = 10   # videos per search query (free tier budget conscious)
COMMENT_MAX = 50   # max comments to read per video


# ── Channels to monitor ───────────────────────────────────────────────────────

MONITORED_CHANNELS = [
    # (Channel name for display, Channel ID)
    ("Coin Bureau",       "UCqK_GSMbpiV8spgD3ZGloSw"),
    ("Meet Kevin",        "UCUvvj5kwbdDHFl9C9bkKB0A"),
    ("Andrei Jikh",       "UCGy7SkBjcIAgTiwkXEtPnYg"),
    ("Graham Stephan",    "UCV6KDgJskWaEckne5aPA0aQ"),
    ("InvestAnswers",     "UCnMn36GT_H0X-w5_ckLtlgQ"),
    ("BitBoy Crypto",     "UCjemQfjaXAzA-95RKoy9n_g"),
    ("Raoul Pal",         "UCHSBuDZ-AATDL-r5YiWIFLQ"),
    ("Michael Saylor",    "UC7yt4aqjYrfKFbdC1Gi7Kvw"),
]

# Lookback window for "recent uploads" check
RECENT_DAYS = 3

# Search queries for trending financial content
TREND_QUERIES = [
    "bitcoin price 2025",
    "stock market crash",
    "crypto bull run",
    "NVIDIA stock",
    "Federal Reserve interest rates",
]

# Keywords that indicate bullish vs bearish creator sentiment
BULL_KEYWORDS = [
    "bull", "bullish", "breakout", "moon", "pump", "rally", "all time high",
    "ath", "accumulate", "buy", "undervalued", "opportunity", "recovery",
    "surge", "explode", "parabolic", "massive gains", "100x",
]

BEAR_KEYWORDS = [
    "bear", "bearish", "crash", "dump", "collapse", "warning", "danger",
    "bubble", "correction", "sell", "overvalued", "exit", "recession",
    "crisis", "fear", "panic", "bottom", "massive drop", "end",
]

# High-signal comment phrases (what retail is feeling)
RETAIL_FEAR_PHRASES  = ["sell everything", "lost all", "buying more anyway", "hodl", "panic"]
RETAIL_GREED_PHRASES = ["going to moon", "easy money", "life changing", "buy more", "all in"]


# ── API helpers ───────────────────────────────────────────────────────────────

def _get_api_key() -> Optional[str]:
    return os.environ.get("YOUTUBE_API_KEY")


def _yt_get(endpoint: str, params: dict, api_key: str) -> Optional[dict]:
    """Make a YouTube Data API GET request."""
    try:
        import requests
        params["key"] = api_key
        r = requests.get(f"{YT_BASE}/{endpoint}", params=params, timeout=10)
        if r.status_code == 403:
            print(f"[OctoTube] 403 Forbidden — quota exceeded or bad API key")
            return None
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[OctoTube] API error ({endpoint}): {e}")
        return None


# ── Sentiment analysis ────────────────────────────────────────────────────────

def _score_text(text: str) -> dict:
    """Count bull/bear keywords in a title or description."""
    text_lower = text.lower()
    bull = sum(1 for kw in BULL_KEYWORDS if kw in text_lower)
    bear = sum(1 for kw in BEAR_KEYWORDS if kw in text_lower)
    if bull > bear:
        sentiment = "bullish"
    elif bear > bull:
        sentiment = "bearish"
    else:
        sentiment = "neutral"
    return {"bull": bull, "bear": bear, "sentiment": sentiment}


def _parse_view_count(count_str: str) -> int:
    try:
        return int(count_str)
    except (ValueError, TypeError):
        return 0


def _parse_iso_duration(duration: str) -> int:
    """Parse ISO 8601 duration string (PT4M30S) to seconds."""
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration)
    if not match:
        return 0
    h, m, s = (int(x or 0) for x in match.groups())
    return h * 3600 + m * 60 + s


# ── Trending videos ───────────────────────────────────────────────────────────

def _get_trending_videos(api_key: str, queries: list) -> list[dict]:
    """Search for trending financial videos across multiple queries."""
    results = []
    seen_ids = set()

    for query in queries:
        data = _yt_get("search", {
            "part":       "snippet",
            "q":          query,
            "type":       "video",
            "order":      "relevance",  # 'viewCount' costs more quota
            "maxResults": MAX_RESULTS,
            "publishedAfter": (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "relevanceLanguage": "en",
        }, api_key)

        if not data:
            time.sleep(1)
            continue

        for item in data.get("items", []):
            vid_id = item["id"].get("videoId")
            if not vid_id or vid_id in seen_ids:
                continue
            seen_ids.add(vid_id)

            snippet = item.get("snippet", {})
            title   = snippet.get("title", "")
            channel = snippet.get("channelTitle", "")
            pub     = snippet.get("publishedAt", "")

            sent = _score_text(title)
            results.append({
                "id":        vid_id,
                "title":     title,
                "channel":   channel,
                "published": pub,
                "url":       f"https://youtu.be/{vid_id}",
                "sentiment": sent["sentiment"],
                "bull_score": sent["bull"],
                "bear_score": sent["bear"],
                "query":     query,
            })

        time.sleep(0.3)  # be gentle on quota

    # Sort by bear score desc first (fear = higher signal), then bull
    results.sort(key=lambda x: (x["bear_score"] + x["bull_score"]), reverse=True)
    return results[:15]


# ── Channel monitoring ────────────────────────────────────────────────────────

def _get_channel_recent(api_key: str, channel_id: str, display_name: str, days: int = RECENT_DAYS) -> dict:
    """Get recent uploads from a specific channel and score their sentiment."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    data = _yt_get("search", {
        "part":           "snippet",
        "channelId":      channel_id,
        "type":           "video",
        "order":          "date",
        "maxResults":     5,
        "publishedAfter": since,
    }, api_key)

    if not data:
        return {"channel": display_name, "id": channel_id, "recent_uploads": [], "sentiment": "unknown"}

    uploads = []
    for item in data.get("items", []):
        vid_id = item["id"].get("videoId")
        if not vid_id:
            continue
        snippet = item.get("snippet", {})
        title   = snippet.get("title", "")
        pub     = snippet.get("publishedAt", "")
        sent    = _score_text(title)
        uploads.append({
            "id":        vid_id,
            "title":     title,
            "published": pub,
            "url":       f"https://youtu.be/{vid_id}",
            "sentiment": sent["sentiment"],
            "bull_score": sent["bull"],
            "bear_score": sent["bear"],
        })

    # Overall channel sentiment for this period
    if uploads:
        avg_bull = sum(v["bull_score"] for v in uploads) / len(uploads)
        avg_bear = sum(v["bear_score"] for v in uploads) / len(uploads)
        if avg_bull > avg_bear:
            overall = "bullish"
        elif avg_bear > avg_bull:
            overall = "bearish"
        else:
            overall = "neutral"
    else:
        overall = "quiet"

    return {
        "channel":        display_name,
        "id":             channel_id,
        "recent_uploads": uploads,
        "upload_count":   len(uploads),
        "sentiment":      overall,
        "days_checked":   days,
    }


# ── Comment sentiment ─────────────────────────────────────────────────────────

def _get_comment_sentiment(api_key: str, video_id: str, max_comments: int = COMMENT_MAX) -> dict:
    """Pull top comments from a video and score retail sentiment."""
    data = _yt_get("commentThreads", {
        "part":       "snippet",
        "videoId":    video_id,
        "order":      "relevance",
        "maxResults": min(max_comments, 100),
        "textFormat": "plainText",
    }, api_key)

    if not data:
        return {}

    comments = []
    fear_hits  = 0
    greed_hits = 0
    bull_total = 0
    bear_total = 0

    for item in data.get("items", []):
        text = item["snippet"]["topLevelComment"]["snippet"].get("textDisplay", "")
        text_lower = text.lower()
        score = _score_text(text)
        bull_total += score["bull"]
        bear_total += score["bear"]

        for phrase in RETAIL_FEAR_PHRASES:
            if phrase in text_lower:
                fear_hits += 1
        for phrase in RETAIL_GREED_PHRASES:
            if phrase in text_lower:
                greed_hits += 1

        comments.append(score["sentiment"])

    n = len(comments)
    if n == 0:
        return {}

    bull_pct = round(comments.count("bullish") / n * 100, 1)
    bear_pct = round(comments.count("bearish") / n * 100, 1)

    return {
        "comment_count":  n,
        "bull_pct":       bull_pct,
        "bear_pct":       bear_pct,
        "fear_phrases":   fear_hits,
        "greed_phrases":  greed_hits,
        "retail_mood":    "greedy" if greed_hits > fear_hits else ("fearful" if fear_hits > greed_hits else "mixed"),
    }


# ── Main scan ─────────────────────────────────────────────────────────────────

def run_youtube_scan(
    channels: list | None = None,
    queries:  list | None = None,
    sample_comments: bool = True,
) -> dict:
    """
    Full YouTube intelligence scan.
    Requires YOUTUBE_API_KEY in env.
    """
    api_key = _get_api_key()
    if not api_key:
        print("[OctoTube] No YOUTUBE_API_KEY — scan skipped.")
        return {"error": "no_api_key", "timestamp": datetime.utcnow().isoformat()}

    if channels is None: channels = MONITORED_CHANNELS
    if queries is None:  queries  = TREND_QUERIES

    print("[OctoTube] Starting YouTube intelligence scan...")
    result = {
        "timestamp":        datetime.utcnow().isoformat(),
        "trending_videos":  [],
        "channel_reports":  [],
        "comment_sample":   {},
        "aggregate":        {},
    }

    # ── 1. Trending financial videos
    print(f"  Searching {len(queries)} trend queries...")
    trending = _get_trending_videos(api_key, queries)
    result["trending_videos"] = trending
    print(f"  Found {len(trending)} trending financial videos")

    # ── 2. Channel reports
    print(f"  Monitoring {len(channels)} channels...")
    for display_name, channel_id in channels:
        report = _get_channel_recent(api_key, channel_id, display_name)
        result["channel_reports"].append(report)
        status = f"{report['upload_count']} uploads ({report['sentiment']})"
        print(f"    {display_name:20s} {status}")
        time.sleep(0.4)

    # ── 3. Sample comments from top trending video (high signal, low quota cost)
    if sample_comments and trending:
        top_vid = trending[0]
        print(f"  Sampling comments from: {top_vid['title'][:60]}...")
        comment_data = _get_comment_sentiment(api_key, top_vid["id"])
        if comment_data:
            result["comment_sample"] = {
                "video_id":    top_vid["id"],
                "video_title": top_vid["title"],
                **comment_data,
            }
            print(f"    Retail mood: {comment_data.get('retail_mood','?')} | "
                  f"bull={comment_data.get('bull_pct','?')}% bear={comment_data.get('bear_pct','?')}%")

    # ── 4. Aggregate creator sentiment
    channel_sentiments = [r["sentiment"] for r in result["channel_reports"]]
    n = len(channel_sentiments)
    if n > 0:
        bull_creators = channel_sentiments.count("bullish")
        bear_creators = channel_sentiments.count("bearish")
        quiet_creators = channel_sentiments.count("quiet")
        result["aggregate"] = {
            "bull_creators":  bull_creators,
            "bear_creators":  bear_creators,
            "quiet_creators": quiet_creators,
            "creator_bias":   "bullish" if bull_creators > bear_creators else
                              ("bearish" if bear_creators > bull_creators else "mixed"),
            "trending_bear_count": sum(1 for v in trending if v["sentiment"] == "bearish"),
            "trending_bull_count": sum(1 for v in trending if v["sentiment"] == "bullish"),
        }

    return result


# ── Prompt formatter ──────────────────────────────────────────────────────────

def format_youtube_for_prompt(result: dict) -> str:
    if result.get("error"):
        return "[OctoTube unavailable — YOUTUBE_API_KEY not set]"

    lines = ["YouTube Retail Intelligence (OctoTube):"]

    agg = result.get("aggregate", {})
    if agg:
        lines.append(
            f"  Creator bias: {agg.get('creator_bias','?').upper()} | "
            f"{agg.get('bull_creators',0)} bullish / {agg.get('bear_creators',0)} bearish / "
            f"{agg.get('quiet_creators',0)} quiet"
        )

    # Channel highlights — only active channels
    active = [r for r in result.get("channel_reports", []) if r["upload_count"] > 0]
    if active:
        lines.append(f"  Recent uploads ({RECENT_DAYS}d):")
        for r in active[:6]:
            latest_title = r["recent_uploads"][0]["title"][:55] if r["recent_uploads"] else ""
            lines.append(
                f"    {r['channel']:20s} [{r['sentiment']:8s}] {r['upload_count']}x — \"{latest_title}...\""
            )

    # Trending video summary
    trending = result.get("trending_videos", [])
    bear_trending = [v for v in trending if v["sentiment"] == "bearish"]
    bull_trending = [v for v in trending if v["sentiment"] == "bullish"]

    if bear_trending:
        lines.append(f"  High-signal bearish trending titles ({len(bear_trending)}):")
        for v in bear_trending[:3]:
            lines.append(f"    [{v['channel'][:20]}] {v['title'][:60]}")
    if bull_trending:
        lines.append(f"  Bullish trending ({len(bull_trending)} videos)")

    # Comment sample
    cs = result.get("comment_sample", {})
    if cs and cs.get("retail_mood"):
        lines.append(
            f"  Comment sentiment on \"{cs.get('video_title','?')[:40]}...\": "
            f"retail is {cs['retail_mood'].upper()} "
            f"(bull={cs.get('bull_pct','?')}% bear={cs.get('bear_pct','?')}%)"
        )

    return "\n".join(lines)


# ── Standalone run ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    result = run_youtube_scan()

    print("\n── OctoTube Report ────────────────────────────────")
    print(format_youtube_for_prompt(result))

    print("\n── Top Trending Videos ────────────────────────────")
    for v in result.get("trending_videos", [])[:5]:
        print(f"  [{v['sentiment']:8s}] {v['channel']:20s} {v['title'][:60]}")

    print("\n── Channel Reports ────────────────────────────────")
    for r in result.get("channel_reports", []):
        print(f"  {r['channel']:20s} | uploads={r['upload_count']} | {r['sentiment']}")

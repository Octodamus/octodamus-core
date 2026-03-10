"""
octo_alert.py
OctoAlert — Breaking News & Market Event Scanner

Runs every 20 minutes via Task Scheduler (separate from monitor).
Only posts if a verified, high-significance event is detected.

Design principles:
  - Score every signal 1–10. Only post if score >= 7.
  - Require 2+ sources confirming the same story before posting.
  - Hard cooldown: max 1 alert post per 4 hours.
  - Dedup log: never post the same story twice.
  - All posts go through existing octo_x_queue system.

Signal sources:
  1. CoinGecko  — crypto price spikes/crashes (no key needed)
  2. NewsAPI    — headline clustering + keyword severity scoring
  3. GDELT      — geopolitical tone collapse / sudden escalation

Usage (standalone test):
    python octo_alert.py

Runner integration:
    python octodamus_runner.py --mode alert
"""

import json
import os
import time
import hashlib
import requests
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("America/Los_Angeles")

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────

# Minimum significance score (1–10) required to post
ALERT_THRESHOLD     = 7

# Hours between alert posts (hard cooldown)
COOLDOWN_HOURS      = 4

# Minimum number of distinct sources confirming a story
MIN_SOURCE_COUNT    = 2

# Max age of news to consider (hours)
NEWS_MAX_AGE_HOURS  = 6

# Alert state files
ALERT_LOG_FILE      = Path("octo_alert_log.json")
ALERT_DEDUP_FILE    = Path("octo_alert_dedup.json")

HEADERS = {"User-Agent": "octodamus-oracle/1.0 (@octodamusai)"}

# ─────────────────────────────────────────────
# KEYWORD SEVERITY TIERS
# Score added to base score when headline contains these words
# ─────────────────────────────────────────────

SEVERITY_TIER_3 = [  # +3 points — major systemic events
    "circuit breaker", "trading halt", "market suspended",
    "emergency fed", "emergency rate", "fed emergency cut",
    "bank failure", "bank collapse", "systemic risk",
    "nuclear", "war declared", "invasion", "attack on",
    "assassination", "coup", "martial law",
    "exchange hacked", "exchange collapsed", "ftx",
    "bitcoin etf rejected", "bitcoin banned",
    "sec charges", "doj charges",
]

SEVERITY_TIER_2 = [  # +2 points — significant market/macro
    "fed rate cut", "fed rate hike", "fomc decision",
    "inflation spike", "cpi surprise", "jobs report", "employment report",
    "recession confirmed", "gdp contraction", "gdp shrinks",
    "earnings miss", "earnings beat", "earnings surprise",
    "layoffs", "mass layoffs", "bankruptcy", "default",
    "sanctions", "trade war", "tariff", "trade deal",
    "crypto ban", "bitcoin crash", "btc crash", "eth crash",
    "flash crash", "market crash", "stock crash", "market plunge",
    "oil spike", "oil crash", "energy crisis", "oil tops", "oil soars",
    "middle east", "escalation", "iran", "russia", "ukraine",
    "war", "conflict", "attack", "strike", "invasion",
    "rate cut", "rate hike", "pivot", "quantitative",
    "lost jobs", "job losses", "unemployment surge",
    "cable cut", "infrastructure attack", "cyber attack",
]

SEVERITY_TIER_1 = [  # +1 point — noteworthy but lower urgency
    "fed", "federal reserve", "interest rate",
    "inflation", "recession", "unemployment",
    "bitcoin", "ethereum", "crypto", "btc", "eth",
    "nvda", "nvidia", "tesla", "apple", "microsoft", "google", "meta",
    "ipo", "merger", "acquisition", "earnings",
    "geopolitical", "tension", "diplomatic",
    "oil", "energy", "gold", "dollar", "treasury",
    "china", "taiwan", "north korea", "middle east",
    "trump", "powell", "yellen", "imf", "world bank",
]

# Price move thresholds for crypto alerts
CRYPTO_SPIKE_THRESHOLDS = {
    "bitcoin":  {"warn": 5.0,  "alert": 8.0},   # % move in 24h
    "ethereum": {"warn": 7.0,  "alert": 12.0},
    "solana":   {"warn": 10.0, "alert": 15.0},
}

# ─────────────────────────────────────────────
# ALERT LOG — cooldown + dedup
# ─────────────────────────────────────────────

def _load_alert_log() -> dict:
    if ALERT_LOG_FILE.exists():
        try:
            return json.loads(ALERT_LOG_FILE.read_text())
        except Exception:
            return {}
    return {}


def _save_alert_log(log: dict) -> None:
    tmp = ALERT_LOG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(log, indent=2))
    tmp.replace(ALERT_LOG_FILE)


def _load_dedup() -> set:
    if ALERT_DEDUP_FILE.exists():
        try:
            data = json.loads(ALERT_DEDUP_FILE.read_text())
            return set(data.get("seen", []))
        except Exception:
            return set()
    return set()


def _save_dedup(seen: set) -> None:
    # Keep last 500 entries to prevent unbounded growth
    seen_list = list(seen)[-500:]
    tmp = ALERT_DEDUP_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"seen": seen_list}, indent=2))
    tmp.replace(ALERT_DEDUP_FILE)


def _story_hash(text: str) -> str:
    return hashlib.md5(text.strip().lower()[:120].encode()).hexdigest()


def _in_cooldown() -> bool:
    """Return True if we posted an alert too recently."""
    log = _load_alert_log()
    last = log.get("last_alert_at")
    if not last:
        return False
    last_dt = datetime.fromisoformat(last).astimezone(_TZ)
    elapsed = datetime.now(tz=_TZ) - last_dt
    return elapsed < timedelta(hours=COOLDOWN_HOURS)


def _record_alert(story_hash: str, event_summary: str, score: int) -> None:
    log = _load_alert_log()
    log["last_alert_at"] = datetime.now(tz=_TZ).isoformat()
    log["last_event"]    = event_summary
    log["last_score"]    = score
    _save_alert_log(log)

    seen = _load_dedup()
    seen.add(story_hash)
    _save_dedup(seen)


# ─────────────────────────────────────────────
# SIGNAL SOURCE 1 — CoinGecko price spikes
# ─────────────────────────────────────────────

def scan_crypto_prices() -> list[dict]:
    """
    Check BTC, ETH, SOL for significant 24h price moves.
    Returns list of alert dicts if threshold crossed.
    No API key required.
    """
    alerts = []
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={
                "ids": "bitcoin,ethereum,solana",
                "vs_currencies": "usd",
                "include_24hr_change": "true",
                "include_24hr_vol": "true",
            },
            headers=HEADERS,
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()

        for coin_id, thresholds in CRYPTO_SPIKE_THRESHOLDS.items():
            coin = data.get(coin_id, {})
            price     = coin.get("usd", 0)
            change_24h = coin.get("usd_24h_change", 0)
            if change_24h is None:
                continue

            abs_change = abs(change_24h)
            direction  = "surged" if change_24h > 0 else "crashed"

            if abs_change >= thresholds["alert"]:
                score = 8 if abs_change >= thresholds["alert"] * 1.5 else 7
                label = coin_id.upper()[:3]
                summary = f"{label} {direction} {change_24h:+.1f}% to ${price:,.0f}"
                alerts.append({
                    "source":   "coingecko",
                    "type":     "price_spike",
                    "summary":  summary,
                    "score":    score,
                    "data":     {"coin": coin_id, "price": price, "change_24h": change_24h},
                    "hash":     _story_hash(summary),
                })
                print(f"[OctoAlert] 🚨 Crypto alert: {summary} (score={score})")

            elif abs_change >= thresholds["warn"]:
                print(f"[OctoAlert] ⚠  Crypto watch: {coin_id} {change_24h:+.1f}% (below threshold)")

    except Exception as e:
        print(f"[OctoAlert] CoinGecko scan failed: {e}")

    return alerts


# ─────────────────────────────────────────────
# SIGNAL SOURCE 2 — NewsAPI headline clustering
# ─────────────────────────────────────────────

# Queries to scan for breaking events
BREAKING_QUERIES = [
    "market crash OR circuit breaker OR trading halt",
    "Federal Reserve emergency OR Fed rate cut surprise",
    "bitcoin crash OR ethereum crash OR crypto ban",
    "bank failure OR bank collapse OR financial crisis",
    "war declared OR invasion OR military attack",
    "recession confirmed OR GDP contraction",
    "SEC charges OR DOJ charges crypto",
    "NVIDIA OR Tesla OR Apple earnings surprise",
]


def _score_headline(title: str) -> int:
    """Score a single headline by keyword severity."""
    t = title.lower()
    score = 0
    for kw in SEVERITY_TIER_3:
        if kw in t:
            score += 3
    for kw in SEVERITY_TIER_2:
        if kw in t:
            score += 2
    for kw in SEVERITY_TIER_1:
        if kw in t:
            score += 1
    return min(score, 5)  # cap headline contribution at 5


def scan_news_headlines(newsapi_key: str) -> list[dict]:
    """
    Cluster recent headlines by query.
    Return alerts where multiple sources confirm a significant story.
    """
    if not newsapi_key:
        print("[OctoAlert] No NewsAPI key — skipping news scan.")
        return []

    alerts = []
    seen_dedup = _load_dedup()
    cutoff = datetime.utcnow() - timedelta(hours=NEWS_MAX_AGE_HOURS)

    for query in BREAKING_QUERIES:
        try:
            r = requests.get(
                "https://newsapi.org/v2/everything",
                params={
                    "q":        query,
                    "sortBy":   "publishedAt",
                    "pageSize": 10,
                    "language": "en",
                    "apiKey":   newsapi_key,
                },
                timeout=10,
            )
            data = r.json()
            if data.get("status") != "ok":
                continue

            articles = data.get("articles", [])

            # Filter to recent articles only
            recent = []
            for a in articles:
                pub = a.get("publishedAt", "")
                try:
                    pub_dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
                    if pub_dt.replace(tzinfo=None) >= cutoff:
                        recent.append(a)
                except Exception:
                    continue

            if len(recent) < MIN_SOURCE_COUNT:
                continue  # Not enough sources — not verified

            # Score the cluster
            titles  = [a.get("title", "") for a in recent if a.get("title")]
            sources = list(set(a.get("source", {}).get("name", "") for a in recent))
            best_title = max(titles, key=_score_headline) if titles else ""
            base_score = _score_headline(best_title)

            # Boost for source count (more sources = more verified)
            source_boost = min(len(sources) - 1, 3)
            total_score  = base_score + source_boost

            if total_score < ALERT_THRESHOLD:
                continue

            story_hash = _story_hash(best_title)
            if story_hash in seen_dedup:
                print(f"[OctoAlert] Dedup skip: {best_title[:60]}")
                continue

            summary = f"{best_title[:100]} [{len(sources)} sources]"
            alerts.append({
                "source":   "newsapi",
                "type":     "breaking_news",
                "summary":  summary,
                "score":    total_score,
                "data": {
                    "query":    query,
                    "titles":   titles[:3],
                    "sources":  sources[:5],
                    "count":    len(recent),
                },
                "hash": story_hash,
            })
            print(f"[OctoAlert] 🚨 News alert: {summary[:80]} (score={total_score})")
            time.sleep(0.3)

        except Exception as e:
            print(f"[OctoAlert] NewsAPI query failed '{query[:40]}': {e}")

    return alerts


# ─────────────────────────────────────────────
# SIGNAL SOURCE 3 — GDELT tone collapse
# ─────────────────────────────────────────────

GDELT_ALERT_QUERIES = [
    "financial crisis bank",
    "Federal Reserve emergency",
]

GDELT_TONE_CRASH_THRESHOLD = -6.0   # Below this = crisis-level negativity
GDELT_ARTICLE_MIN           = 5     # Need at least 5 articles to trust the signal


def scan_gdelt_tone() -> list[dict]:
    """
    Check GDELT for sudden geopolitical tone collapse on key themes.
    Returns alert if tone is crisis-level AND corroborated by volume.
    """
    alerts = []
    seen_dedup = _load_dedup()
    time.sleep(5)  # brief pause before GDELT

    for query in GDELT_ALERT_QUERIES:
        try:
            # Use timelinetone mode — artlist does NOT include tone field
            r = requests.get(
                "https://api.gdeltproject.org/api/v2/doc/doc",
                params={
                    "query":    query,
                    "mode":     "timelinetone",
                    "timespan": "4h",
                    "format":   "json",
                },
                headers=HEADERS,
                timeout=15,
            )
            r.raise_for_status()
            timeline = r.json().get("timeline", [])

            if len(timeline) < 2:
                continue

            tones = [entry.get("tone", 0) for entry in timeline if "tone" in entry]
            if not tones:
                continue

            avg_tone = sum(tones) / len(tones)

            if avg_tone > GDELT_TONE_CRASH_THRESHOLD:
                print(f"[OctoAlert] GDELT '{query[:30]}' tone={avg_tone:+.1f} — below threshold")
                continue

            # Crisis-level tone detected
            score = 7 if avg_tone > -8 else 9
            top_title = articles[0].get("title", query)[:80]
            summary = f"GDELT tone collapse on '{query}': {avg_tone:+.1f} ({len(articles)} articles)"

            story_hash = _story_hash(summary)
            if story_hash in seen_dedup:
                continue

            alerts.append({
                "source":  "gdelt",
                "type":    "geo_escalation",
                "summary": summary,
                "score":   score,
                "data": {
                    "query":     query,
                    "avg_tone":  round(avg_tone, 2),
                    "top_story": top_title,
                    "article_count": len(articles),
                },
                "hash": story_hash,
            })
            print(f"[OctoAlert] 🚨 GDELT alert: {summary} (score={score})")
            time.sleep(4)

        except Exception as e:
            print(f"[OctoAlert] GDELT scan failed for '{query}': {e}")

    return alerts


# ─────────────────────────────────────────────
# ALERT COMPOSER
# ─────────────────────────────────────────────

ALERT_VOICE_SYSTEM = """You are Octodamus — oracle octopus, market seer of the Pacific depths.
You are @octodamusai on X. You have 8 arms of intelligence, and one just flagged something real.
Max 280 chars. No hashtags. No engagement bait.

This is a BREAKING ALERT post — something significant just happened. Your voice options:
  SHARP    - Lead with the raw fact. One sentence. Then the implication.
  SARDONIC - Acknowledge the chaos with an edge. "Ah. There it is."
  PRECISE  - Pure signal. No color. Just the number and what it means.

Rules:
  - ALWAYS lead with the specific fact/number first. Never bury it.
  - Do NOT say "breaking" or "alert" — the post itself is the signal.
  - Do NOT use hashtags or emojis unless a single 🐙 at the end.
  - Be brief. The event speaks. You just frame it."""


def compose_alert_post(alert: dict, claude_client) -> str | None:
    """Generate an Octodamus post for a verified breaking alert."""
    try:
        source_type = alert["type"]
        summary     = alert["summary"]
        data        = alert.get("data", {})

        if source_type == "price_spike":
            context = (
                f"Crypto price alert: {summary}\n"
                f"Coin: {data.get('coin', '').upper()}\n"
                f"Price: ${data.get('price', 0):,.0f}\n"
                f"24h change: {data.get('change_24h', 0):+.1f}%"
            )
        elif source_type == "breaking_news":
            headlines = "\n".join(f"  - {t}" for t in data.get("titles", [])[:3])
            sources   = ", ".join(data.get("sources", [])[:3])
            context = (
                f"Breaking news cluster ({data.get('count', 0)} articles, {len(data.get('sources', []))} sources):\n"
                f"Top headlines:\n{headlines}\n"
                f"Confirmed by: {sources}"
            )
        elif source_type == "geo_escalation":
            context = (
                f"Geopolitical escalation detected via GDELT:\n"
                f"Theme: {data.get('query', '')}\n"
                f"Global news tone: {data.get('avg_tone', 0):+.1f} (crisis level)\n"
                f"Based on {data.get('article_count', 0)} articles in last 4 hours\n"
                f"Top story: {data.get('top_story', '')}"
            )
        else:
            context = summary

        response = claude_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=120,
            system=ALERT_VOICE_SYSTEM,
            messages=[{
                "role": "user",
                "content": (
                    f"Verified alert data:\n{context}\n\n"
                    f"Significance score: {alert['score']}/10\n\n"
                    "Write one Octodamus breaking alert post under 280 chars. "
                    "Lead with the specific fact or number. Pick the voice that fits the event."
                ),
            }],
        )
        return response.content[0].text.strip()

    except Exception as e:
        print(f"[OctoAlert] Post composition failed: {e}")
        return None


# ─────────────────────────────────────────────
# MAIN SCANNER
# ─────────────────────────────────────────────

def run_alert_scan(secrets: dict = None, claude_client=None, dry_run: bool = False) -> dict:
    """
    Full alert scan pipeline:
      1. Check cooldown
      2. Scan all sources
      3. Find highest-scoring verified alert
      4. Compose + post if threshold met

    Returns summary dict.
    """
    print(f"\n[OctoAlert] 🔍 Scanning for breaking events...")

    if _in_cooldown():
        log = _load_alert_log()
        last = log.get("last_alert_at", "unknown")
        print(f"[OctoAlert] In cooldown (last alert: {last}). Skipping.")
        return {"status": "cooldown", "last_alert": last}

    # Gather all alerts from all sources
    all_alerts = []

    # 1. Crypto prices (no key needed)
    all_alerts.extend(scan_crypto_prices())
    time.sleep(1)

    # 2. NewsAPI headlines
    newsapi_key = None
    if secrets:
        newsapi_key = secrets.get("NEWSAPI_API_KEY") or secrets.get("NEWS_API_KEY")
    all_alerts.extend(scan_news_headlines(newsapi_key))
    time.sleep(1)

    # 3. GDELT geopolitical tone — disabled until IP block clears
    # GDELT rate-limits aggressively; re-enable once octo_geo.py
    # confirms clean tone readings in production.
    # all_alerts.extend(scan_gdelt_tone())

    if not all_alerts:
        print("[OctoAlert] No alerts above threshold. Ocean is calm.")
        return {"status": "calm", "alerts_found": 0}

    # Pick the highest-scoring alert
    best = max(all_alerts, key=lambda a: a["score"])
    print(f"\n[OctoAlert] Best alert (score={best['score']}): {best['summary'][:80]}")

    if best["score"] < ALERT_THRESHOLD:
        print(f"[OctoAlert] Score {best['score']} below threshold {ALERT_THRESHOLD}. No post.")
        return {"status": "below_threshold", "best_score": best["score"]}

    # Check dedup
    seen = _load_dedup()
    if best["hash"] in seen:
        print("[OctoAlert] Already posted this event. Skipping.")
        return {"status": "dedup", "event": best["summary"]}

    if dry_run:
        print(f"[OctoAlert] DRY RUN — would post: {best['summary']}")
        return {"status": "dry_run", "alert": best}

    # Compose post
    if claude_client is None:
        print("[OctoAlert] No Claude client provided — cannot compose post.")
        return {"status": "no_client"}

    post = compose_alert_post(best, claude_client)
    if not post:
        return {"status": "compose_failed"}

    print(f"[OctoAlert] ✅ Composed post:\n  {post}")

    # Queue and post immediately (priority 0 = urgent, bypasses normal queue order)
    from octo_x_queue import queue_post, process_queue
    queue_post(post, post_type="alert", priority=0, metadata={
        "alert_score":  best["score"],
        "alert_source": best["source"],
        "alert_type":   best["type"],
    })
    posted = process_queue(max_posts=1)

    if posted:
        _record_alert(best["hash"], best["summary"], best["score"])
        print(f"[OctoAlert] 🚨 Breaking alert posted to X.")
        return {"status": "posted", "post": post, "score": best["score"]}
    else:
        print("[OctoAlert] Queue post failed — outside posting hours or limit reached.")
        return {"status": "queue_failed"}


# ─────────────────────────────────────────────
# STANDALONE TEST
# ─────────────────────────────────────────────

if __name__ == "__main__":
    from bitwarden import load_all_secrets, verify_session
    verify_session()
    _secrets = load_all_secrets()
    print("🐙 OctoAlert — standalone test (dry run)")
    result = run_alert_scan(secrets=_secrets, dry_run=True)
    print(f"\nResult: {result}")

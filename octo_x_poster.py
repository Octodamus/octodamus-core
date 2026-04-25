"""
octo_x_poster.py
Octodamus — X Posting via native X API v2 (pay-per-use)

Transport: X API v2 via tweepy (OAuth 1.0a)
Credentials: TWITTER_API_KEY, TWITTER_API_SECRET,
             TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_TOKEN_SECRET
Daily limit: 20 posts self-imposed
Cost: ~$0.01/post → ~$6/month at 20/day

Capabilities:
  - Post single tweets
  - Post threaded replies (chains)
  - Reply to specific tweets
  - Read mentions (@octodamusai)
"""

import hashlib
import json
import math
import os
import random
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

# ─────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────

_TZ           = ZoneInfo("America/Los_Angeles")
_MAX_RETRIES  = 3
_MAX_LOG      = 5_000
_DAILY_LIMIT  = 20
MAX_QUEUE_AGE_HOURS = 4
_THREAD_DELAY = 2.0

QUEUE_FILE = Path(__file__).parent / "octo_post_queue.json"
POSTED_LOG = Path(__file__).parent / "octo_posted_log.json"

FORCE_POST = False


# ─────────────────────────────────────────────
# X API v2 CLIENT (tweepy OAuth 1.0a)
# ─────────────────────────────────────────────

def _get_client():
    import tweepy
    return tweepy.Client(
        bearer_token=os.environ.get("TWITTER_BEARER_TOKEN", ""),
        consumer_key=os.environ.get("TWITTER_API_KEY", ""),
        consumer_secret=os.environ.get("TWITTER_API_SECRET", ""),
        access_token=os.environ.get("TWITTER_ACCESS_TOKEN", ""),
        access_token_secret=os.environ.get("TWITTER_ACCESS_TOKEN_SECRET", ""),
        wait_on_rate_limit=False,
    )


def upload_image_from_url(image_url: str) -> str | None:
    """
    Download an image from a URL and upload it to X via v1.1 media upload.
    Returns media_id string or None on failure.
    """
    import tempfile
    import requests
    import tweepy

    try:
        r = requests.get(image_url, timeout=15, headers={"User-Agent": "Octodamus/1.0"})
        if r.status_code != 200:
            return None

        suffix = ".jpg"
        if "png" in image_url.lower():
            suffix = ".png"
        elif "gif" in image_url.lower():
            suffix = ".gif"

        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(r.content)
            tmp_path = tmp.name

        auth = tweepy.OAuth1UserHandler(
            consumer_key=os.environ.get("TWITTER_API_KEY", ""),
            consumer_secret=os.environ.get("TWITTER_API_SECRET", ""),
            access_token=os.environ.get("TWITTER_ACCESS_TOKEN", ""),
            access_token_secret=os.environ.get("TWITTER_ACCESS_TOKEN_SECRET", ""),
        )
        api = tweepy.API(auth)
        media = api.media_upload(filename=tmp_path)
        Path(tmp_path).unlink(missing_ok=True)
        return str(media.media_id)
    except Exception as e:
        print(f"[OctoPoster] Image upload failed: {e}")
        return None


def check_connection() -> dict:
    """Verify credentials and return account info."""
    import tweepy
    client = _get_client()
    me = client.get_me()
    if me.data:
        return {"username": me.data.username, "id": str(me.data.id), "ok": True}
    raise ValueError("Could not fetch account info")


def get_my_user_id() -> str:
    """Return the authenticated user's numeric ID."""
    import tweepy
    client = _get_client()
    me = client.get_me()
    return str(me.data.id)


# ─────────────────────────────────────────────
# CORE POSTING
# ─────────────────────────────────────────────

def _post_single(text: str, media_ids: list = None) -> dict:
    """Post a single tweet. Returns dict with tweet id and url."""
    import tweepy
    client = _get_client()
    kwargs = {"text": text}
    if media_ids:
        kwargs["media_ids"] = media_ids
    resp = client.create_tweet(**kwargs)
    tweet_id = str(resp.data["id"])
    url = f"https://x.com/octodamusai/status/{tweet_id}"
    return {"id": tweet_id, "url": url}



def _post_thread(posts: list) -> dict:
    """
    Post a thread — first tweet, then each subsequent post
    as a reply to the previous one.
    Returns dict with first tweet id and url.
    """
    import tweepy
    client = _get_client()

    # First tweet
    resp = client.create_tweet(text=posts[0])
    first_id = str(resp.data["id"])
    prev_id  = first_id

    # Chain replies
    for post in posts[1:]:
        time.sleep(_THREAD_DELAY)
        resp = client.create_tweet(
            text=post,
            in_reply_to_tweet_id=prev_id,
        )
        prev_id = str(resp.data["id"])

    url = f"https://x.com/octodamusai/status/{first_id}"
    return {"id": first_id, "url": url}


def post_reply(reply_text: str, tweet_id: str) -> dict:
    """
    Reply to a specific tweet.
    tweet_id: the tweet ID string to reply to.
    Returns dict with reply id and url.
    """
    import tweepy
    client = _get_client()
    resp = client.create_tweet(
        text=reply_text,
        in_reply_to_tweet_id=tweet_id,
        user_auth=True,
    )
    reply_id = str(resp.data["id"])
    url = f"https://x.com/octodamusai/status/{reply_id}"
    return {"id": reply_id, "url": url}




_TWEET_LIMIT = 265  # leave 15 chars buffer below X's 280


_CASHTAG_MAP = {
    # Stocks
    "nvidia": "$NVDA",  "nvda": "$NVDA",
    "tesla": "$TSLA",   "tsla": "$TSLA",
    "apple": "$AAPL",   "aapl": "$AAPL",
    "microsoft": "$MSFT", "msft": "$MSFT",
    "google": "$GOOGL", "alphabet": "$GOOGL", "googl": "$GOOGL",
    "amazon": "$AMZN",  "amzn": "$AMZN",
    "meta": "$META",
    "coinbase": "$COIN", "coin": "$COIN",
    "microstrategy": "$MSTR", "mstr": "$MSTR",
    "ibit": "$IBIT",
    "intel": "$INTC",   "intc": "$INTC",
    "palantir": "$PLTR", "pltr": "$PLTR",
    "msft": "$MSFT",
    # Crypto
    "bitcoin": "$BTC",  "btc": "$BTC",
    "ethereum": "$ETH", "eth": "$ETH",
    "solana": "$SOL",   "sol": "$SOL",
    "xrp": "$XRP",      "ripple": "$XRP",
    "cardano": "$ADA",  "ada": "$ADA",
    "dogecoin": "$DOGE", "doge": "$DOGE",
    "chainlink": "$LINK", "link": "$LINK",
    "avalanche": "$AVAX", "avax": "$AVAX",
    "polkadot": "$DOT",  "dot": "$DOT",
    "hyperliquid": "$HYPE", "hype": "$HYPE",
    # Oil/macro
    "wti": "$WTI", "crude oil": "$WTI", "oil": "$WTI",
    "gold": "$GOLD",
    "spy": "$SPY", "nasdaq": "$QQQ", "qqq": "$QQQ",
}


def ensure_cashtag(text: str) -> str:
    """
    Check if a post mentions a known stock/crypto by name without its cashtag.
    If so, append the cashtag at the end (max one cashtag per post — X rule).
    Respects the 280-char limit.
    """
    import re
    text_lower = text.lower()

    # Already has a cashtag — X rejects 2+, so don't add another
    if re.search(r'\$[A-Z]{2,6}\b', text):
        return text

    # Check for known names without their cashtag
    for name, cashtag in _CASHTAG_MAP.items():
        # Match whole word only
        pattern = r'\b' + re.escape(name) + r'\b'
        if re.search(pattern, text_lower):
            candidate = text.rstrip() + f" {cashtag}"
            if len(candidate) <= 280:
                return candidate
            break  # can't fit, leave as-is

    return text


def split_for_thread(text: str) -> list[str]:
    """
    Split text into tweet-sized chunks for threading.
    Tries to break at paragraph → sentence → word boundaries.
    Each chunk ≤ _TWEET_LIMIT chars.
    Returns a list (length 1 if no split needed).
    """
    text = text.strip()
    if len(text) <= _TWEET_LIMIT:
        return [text]

    tweets = []

    def _split_chunk(chunk: str) -> list[str]:
        chunk = chunk.strip()
        if not chunk:
            return []
        if len(chunk) <= _TWEET_LIMIT:
            return [chunk]
        # Try sentence boundary
        for sep in (". ", "! ", "? ", "; ", " — ", ", "):
            idx = chunk.rfind(sep, 0, _TWEET_LIMIT)
            if idx > _TWEET_LIMIT // 2:
                end = idx + len(sep)
                return [chunk[:end].strip()] + _split_chunk(chunk[end:])
        # Hard word boundary
        idx = chunk.rfind(" ", 0, _TWEET_LIMIT)
        if idx > 0:
            return [chunk[:idx].strip()] + _split_chunk(chunk[idx:])
        # Force split
        return [chunk[:_TWEET_LIMIT]] + _split_chunk(chunk[_TWEET_LIMIT:])

    # Try paragraph splits first
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    if len(paras) > 1:
        current = ""
        for para in paras:
            candidate = (current + "\n\n" + para).strip() if current else para
            if len(candidate) <= _TWEET_LIMIT:
                current = candidate
            else:
                if current:
                    tweets.extend(_split_chunk(current))
                current = para
        if current:
            tweets.extend(_split_chunk(current))
    else:
        tweets = _split_chunk(text)

    return [t for t in tweets if t.strip()]


def read_mentions(max_results: int = 20) -> list:
    """
    Read recent @octodamusai mentions.
    Returns list of dicts: {id, text, author_id, created_at}
    Cost: $0.005 per tweet read.
    """
    import tweepy
    client = _get_client()
    try:
        user_id = get_my_user_id()
        mentions = client.get_users_mentions(
            id=user_id,
            max_results=max_results,
            tweet_fields=["created_at", "author_id", "conversation_id"],
        )
        if not mentions.data:
            return []
        return [
            {
                "id": str(m.id),
                "text": m.text,
                "author_id": str(m.author_id),
                "created_at": str(m.created_at),
                "conversation_id": str(m.conversation_id) if hasattr(m, "conversation_id") else None,
            }
            for m in mentions.data
        ]
    except Exception as e:
        print(f"[OctoPoster] read_mentions failed: {e}")
        return []


# ─────────────────────────────────────────────
# SMART POSTING SCHEDULE
# ─────────────────────────────────────────────
#
# No hard on/off window. Each hour has a base weight (0.0-1.0).
# Peak window 3am-9pm PST (user-specified) gets high base weight.
# Weight is boosted dynamically by:
#   - Market volatility (BTC 24h move)
#   - Fear & Greed index extremes
#   - A news/sentiment spike flag
# process_queue() draws against the final weight — high weight hours
# almost always post, overnight low-weight hours post rarely.
#
# This spreads 20 posts/day naturally across the full day while
# concentrating fire in active market hours.

# Base weights by hour (PST). 0.0 = never, 1.0 = always post when called.
# Peak 3am-9pm gets 0.70-1.0. Midnight-3am gets 0.10-0.25.
_HOUR_BASE_WEIGHT = {
    0:  0.15,   # midnight
    1:  0.10,
    2:  0.10,
    3:  0.70,   # ── peak window opens ──
    4:  0.75,
    5:  0.80,
    6:  0.85,
    7:  0.90,
    8:  1.00,   # NYSE pre-market
    9:  1.00,   # NYSE open
    10: 1.00,
    11: 1.00,
    12: 0.95,
    13: 1.00,
    14: 1.00,
    15: 1.00,   # NYSE close hour
    16: 0.95,
    17: 0.90,
    18: 0.85,
    19: 0.80,
    20: 0.80,
    21: 0.75,   # ── peak window closes ──
    22: 0.25,
    23: 0.20,
}

_SCHEDULE_CACHE_FILE = Path(__file__).parent / "data" / "octo_schedule_cache.json"
_SCHEDULE_CACHE_TTL  = 1800   # refresh market signals every 30 min


def _load_schedule_cache() -> dict:
    if _SCHEDULE_CACHE_FILE.exists():
        try:
            data = json.loads(_SCHEDULE_CACHE_FILE.read_text())
            if time.time() - data.get("ts", 0) < _SCHEDULE_CACHE_TTL:
                return data
        except Exception:
            pass
    return {}


def _save_schedule_cache(data: dict) -> None:
    try:
        _SCHEDULE_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        data["ts"] = time.time()
        _SCHEDULE_CACHE_FILE.write_text(json.dumps(data, indent=2))
    except Exception:
        pass


def _fetch_market_signals() -> dict:
    """
    Pull lightweight market signals to adjust post weight.
    Returns dict with keys: btc_change_24h, fear_greed, news_spike.
    Uses cache to avoid hammering APIs on every runner call.
    """
    cached = _load_schedule_cache()
    if cached:
        return cached

    signals = {"btc_change_24h": 0.0, "fear_greed": 50, "news_spike": False}

    # BTC 24h price change via cached Kraken/CoinGecko
    try:
        from financial_data_client import get_crypto_prices
        _p = get_crypto_prices(["BTC"])
        signals["btc_change_24h"] = abs(_p.get("BTC", {}).get("usd_24h_change", 0.0))
    except Exception:
        pass

    # Fear & Greed index (Alternative.me, no key needed)
    try:
        r = httpx.get("https://api.alternative.me/fng/?limit=1", timeout=6)
        if r.status_code == 200:
            val = int(r.json()["data"][0]["value"])
            signals["fear_greed"] = val
            # Extremes (extreme fear <20 or extreme greed >80) = news-worthy
            signals["news_spike"] = val < 20 or val > 80
    except Exception:
        pass

    # Mark spike if BTC moved > 5% in 24h
    if signals["btc_change_24h"] > 5.0:
        signals["news_spike"] = True

    _save_schedule_cache(signals)
    return signals


def _learned_hour_weight(hour: int) -> float | None:
    """
    Return the average engagement score for this hour from octo_skill_log,
    normalized to 0.0-1.0. Returns None if fewer than 3 samples exist.
    """
    try:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).parent))
        from octo_skill_log import get_engagement_by_hour
        by_hour = get_engagement_by_hour()
        if len(by_hour) < 6:          # need data across multiple hours before trusting
            return None
        scores = list(by_hour.values())
        if not scores:
            return None
        max_score = max(scores)
        if max_score == 0:
            return None
        hour_score = by_hour.get(hour)
        if hour_score is None:
            return None
        return round(hour_score / max_score, 3)   # normalize to 0.0-1.0
    except Exception:
        return None


def _posting_weight() -> float:
    """
    Returns current posting weight 0.0-1.0.
    Priority: learned engagement data → static table → market boost.
    """
    if FORCE_POST:
        return 1.0

    now  = datetime.now(tz=_TZ)
    hour = now.hour

    # Try learned weight first (based on real X engagement per hour)
    learned = _learned_hour_weight(hour)
    base    = learned if learned is not None else _HOUR_BASE_WEIGHT.get(hour, 0.5)

    if learned is not None:
        print(f"[OctoPoster] Using learned hour weight for {hour}:00 -> {learned:.2f}")

    try:
        signals = _fetch_market_signals()

        # Volatility boost: +0.15 per 5% BTC move, capped at +0.30
        vol_change = signals.get("btc_change_24h", 0.0)
        vol_boost  = min((vol_change / 5.0) * 0.15, 0.30)

        # Sentiment extreme boost: +0.15 if fear or greed is extreme
        sent_boost = 0.15 if signals.get("news_spike", False) else 0.0

        weight = min(base + vol_boost + sent_boost, 1.0)
    except Exception:
        weight = base

    return weight


def _should_post_now() -> bool:
    """
    Probabilistic gate — returns True if a post should be attempted now.
    Called once per process_queue() invocation.
    """
    if FORCE_POST:
        return True
    weight = _posting_weight()
    return random.random() < weight


def _posts_today() -> int:
    log   = _load_log()
    today = datetime.now(tz=_TZ).date()
    count = 0
    for entry in log.values():
        posted_at = entry.get("posted_at", "")
        if posted_at:
            try:
                d = datetime.fromisoformat(posted_at).astimezone(_TZ).date()
                if d == today:
                    count += 1
            except Exception:
                pass
    return count


# ─────────────────────────────────────────────
# QUEUE FILE OPS
# ─────────────────────────────────────────────

def _load_queue() -> list:
    if QUEUE_FILE.exists():
        try:
            return json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print("[OctoPoster] Queue file corrupt — resetting.")
    return []


def _save_queue(q: list) -> None:
    tmp = QUEUE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(q, indent=2), encoding="utf-8")
    tmp.replace(QUEUE_FILE)


def _load_log() -> dict:
    if POSTED_LOG.exists():
        try:
            return json.loads(POSTED_LOG.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print("[OctoPoster] Log file corrupt — resetting.")
    return {}


def _save_log(log: dict) -> None:
    if len(log) > _MAX_LOG:
        sorted_keys = sorted(log, key=lambda k: log[k].get("posted_at", ""), reverse=True)
        log = {k: log[k] for k in sorted_keys[:_MAX_LOG]}
    tmp = POSTED_LOG.with_suffix(".tmp")
    tmp.write_text(json.dumps(log, indent=2), encoding="utf-8")
    tmp.replace(POSTED_LOG)


def _hash(text: str) -> str:
    return hashlib.md5(text.strip().lower().encode()).hexdigest()


# ─────────────────────────────────────────────
# PUBLIC QUEUE API
# ─────────────────────────────────────────────

def post_oracle_outcome(call: dict, record_wins: int, record_losses: int) -> bool:
    """
    Post an oracle call resolution to X and Discord immediately.
    Bypasses daily post limit — outcomes are factual record updates.
    Returns True if posted successfully.
    """
    asset     = call.get("asset", "?")
    direction = call.get("direction", "?")
    entry     = call.get("entry_price", 0)
    exit_p    = call.get("exit_price", 0)
    outcome   = call.get("outcome", "?")
    won       = call.get("won", False)
    timeframe = call.get("timeframe", "")
    source    = call.get("resolution_price_source", "live price")

    pct     = ((exit_p - entry) / entry * 100) if entry else 0
    pct_str = f"{pct:+.1f}%"
    result_tag = "WIN" if won else "LOSS"
    total   = record_wins + record_losses
    win_rate = f"{record_wins/total*100:.0f}%" if total else "N/A"
    arrow   = "UP" if direction == "UP" else "DOWN"

    text = (
        f"Oracle call closed: {asset} {arrow} from ${entry:,.0f}\n"
        f"Exit ${exit_p:,.0f} | Move {pct_str} | {result_tag}\n"
        f"Record: {record_wins}W / {record_losses}L ({win_rate}) — {source}"
    )
    if len(text) > 278:
        text = (
            f"Oracle: {asset} {arrow} ${entry:,.0f} -> ${exit_p:,.0f} ({pct_str}) {result_tag}\n"
            f"Record: {record_wins}W/{record_losses}L ({win_rate})"
        )

    try:
        result = _post_single(text)
        _log_post(text, {"type": "oracle_outcome", "call_id": call.get("id"), "outcome": outcome})
        print(f"[OctoPoster] Oracle outcome posted: {asset} {result_tag}")

        medal = "WIN" if won else "LOSS"
        discord_msg = (
            f"**Oracle Call Closed** — {asset} {arrow}\n"
            f"Entry: ${entry:,.2f} | Exit: ${exit_p:,.2f} | Move: {pct_str}\n"
            f"Result: **{medal}** | Record: {record_wins}W / {record_losses}L ({win_rate})\n"
            f"Timeframe: {timeframe} | Source: {source}"
        )
        discord_alert(discord_msg)
        return True

    except Exception as e:
        print(f"[OctoPoster] Oracle outcome post failed: {e}")
        discord_alert(f"Oracle outcome post FAILED for {asset} {result_tag}: {e}")
        return False


def _log_post(text: str, metadata: dict) -> None:
    log = _load_log()
    cid = _hash(text)
    log[cid] = {
        "text": text,
        "posted_at": datetime.now(tz=_TZ).isoformat(),
        "metadata": metadata,
    }
    if len(log) > _MAX_LOG:
        oldest = sorted(log.keys(), key=lambda k: log[k].get("posted_at", ""))[:100]
        for k in oldest:
            del log[k]
    _save_log(log)


def queue_post(text: str, post_type: str = "signal", metadata: dict = None, priority: int = 5) -> str | None:
    queue = _load_queue()
    log   = _load_log()
    cid   = _hash(text)
    if cid in log:
        print(f"[OctoPoster] Duplicate skipped: {text[:60]}...")
        return None
    queue.append({
        "id": cid, "text": text, "type": post_type, "is_thread": False,
        "priority": priority, "retries": 0,
        "created_at": datetime.now(tz=_TZ).isoformat(),
        "metadata": metadata or {}, "status": "queued",
    })
    queue.sort(key=lambda x: x["priority"])
    _save_queue(queue)
    print(f"[OctoPoster] Queued ({post_type}, p{priority}): {text[:60]}...")
    return cid


def queue_thread(posts: list, post_type: str = "deep_dive", metadata: dict = None) -> str | None:
    queue = _load_queue()
    log   = _load_log()
    cid   = _hash(posts[0])
    if cid in log:
        print("[OctoPoster] Thread duplicate skipped.")
        return None
    queue.append({
        "id": cid, "text": posts[0], "thread_posts": posts,
        "type": post_type, "is_thread": True, "priority": 3, "retries": 0,
        "created_at": datetime.now(tz=_TZ).isoformat(),
        "metadata": metadata or {}, "status": "queued",
    })
    queue.sort(key=lambda x: x["priority"])
    _save_queue(queue)
    print(f"[OctoPoster] Thread queued ({len(posts)} posts): {posts[0][:60]}...".encode('ascii', errors='replace').decode('ascii'))
    return cid


def purge_stale_queue() -> int:
    queue = _load_queue()
    now   = datetime.now(tz=_TZ)
    fresh, dropped = [], 0
    for entry in queue:
        if entry["status"] != "queued":
            fresh.append(entry)
            continue
        try:
            created   = datetime.fromisoformat(entry["created_at"]).astimezone(_TZ)
            age_hours = (now - created).total_seconds() / 3600
            if age_hours > MAX_QUEUE_AGE_HOURS:
                dropped += 1
                print(f"[OctoPoster] Dropped stale post ({age_hours:.1f}h old): {entry['text'][:60]}...")
            else:
                fresh.append(entry)
        except Exception:
            fresh.append(entry)
    if dropped:
        _save_queue(fresh)
        print(f"[OctoPoster] Purged {dropped} stale post(s) from queue.")
    return dropped


def process_queue(max_posts: int = 1, force: bool = False) -> int:
    purge_stale_queue()
    if not force and not _should_post_now():
        weight = _posting_weight()
        print(f"[OctoPoster] Skipped by schedule (weight={weight:.2f}).")
        return 0

    today_count = _posts_today()
    effective_limit = 99 if FORCE_POST else _DAILY_LIMIT
    if today_count >= effective_limit:
        print(f"[OctoPoster] Daily limit reached ({today_count}/{_DAILY_LIMIT}).")
        return 0

    remaining_today = effective_limit - today_count
    max_posts = min(max_posts, remaining_today)

    queue   = _load_queue()
    pending = [
        p for p in queue
        if p["status"] == "queued"
        or (p["status"] == "failed" and p.get("retries", 0) < _MAX_RETRIES)
    ]

    if not pending:
        print("[OctoPoster] Queue empty.")
        return 0

    log = _load_log()
    posted_count = 0

    for entry in pending[:max_posts]:
        try:
            if entry.get("is_thread"):
                posts = entry["thread_posts"]
                if posts:
                    posts[0] = ensure_cashtag(posts[0])
                result    = _post_thread(posts)
                tweet_url = result.get("url", "")
                print(f"[OctoPoster] [OK] Thread ({len(entry['thread_posts'])} posts): {entry['text'][:60]}...")
            else:
                media_id  = entry.get("metadata", {}).get("media_id") if entry.get("metadata") else None
                media_ids = [media_id] if media_id else None
                text      = ensure_cashtag(entry["text"])
                chunks    = split_for_thread(text)
                if len(chunks) > 1:
                    print(f"[OctoPoster] Auto-threading ({len(chunks)} tweets): {text[:60]}...")
                    result    = _post_thread(chunks)
                    tweet_url = result.get("url", "")
                    print(f"[OctoPoster] [OK] Auto-thread posted: {tweet_url}")
                else:
                    result    = _post_single(text, media_ids=media_ids)
                    tweet_url = result.get("url", "")
                    print(f"[OctoPoster] [OK] Posted [{entry['type']}]: {text[:60]}...")

            log[entry["id"]] = {
                "text": entry["text"], "type": entry["type"],
                "posted_at": datetime.now(tz=_TZ).isoformat(),
                "url": tweet_url, "metadata": entry["metadata"],
            }
            _save_log(log)
            for q in queue:
                if q["id"] == entry["id"]:
                    q["status"] = "posted"
                    break
            _save_queue(queue)
            posted_count += 1
            _discord_notify(entry["text"], entry["type"], tweet_url)

        except Exception as e:
            err = str(e)
            if "429" in err or "rate limit" in err.lower():
                print(f"[OctoPoster] Rate limited by X API. Stopping.")
                break
            print(f"[OctoPoster] Error posting: {err}")
            for q in queue:
                if q["id"] == entry["id"]:
                    q["retries"] = q.get("retries", 0) + 1
                    q["status"] = "failed" if q["retries"] >= _MAX_RETRIES else "queued"
                    break
            _save_queue(queue)

    _save_queue(
        [e for e in queue if e["status"] == "queued"]
        + [e for e in queue if e["status"] == "failed"]
        + [e for e in queue if e["status"] == "posted"][-500:]
    )
    print(f"[OctoPoster] Posts today: {today_count + posted_count}/{_DAILY_LIMIT}")
    return posted_count


# ─────────────────────────────────────────────
# DISCORD
# ─────────────────────────────────────────────

def _discord_notify(text: str, post_type: str, tweet_url: str = "") -> None:
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not webhook:
        return
    try:
        httpx.post(webhook, json={"content": f"**[{post_type}]**\n{text[:280]}\n{tweet_url}"}, timeout=5)
    except Exception:
        pass


def discord_alert(message: str) -> None:
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not webhook:
        return
    try:
        httpx.post(webhook, json={"content": f"⚠️ **Octodamus Alert**\n{message}"}, timeout=5)
    except Exception:
        pass


# ─────────────────────────────────────────────
# STATUS
# ─────────────────────────────────────────────

def queue_status() -> None:
    queue = _load_queue()
    log   = _load_log()
    today = datetime.now(tz=_TZ).date()
    queued = [e for e in queue if e["status"] == "queued"]
    failed = [e for e in queue if e["status"] == "failed"]
    posted_today = [
        v for v in log.values()
        if "posted_at" in v
        and datetime.fromisoformat(v["posted_at"]).astimezone(_TZ).date() == today
    ]
    print("\nOCTODAMUS X POSTER STATUS")
    print(f"  Posted today:  {len(posted_today)} / {_DAILY_LIMIT}")
    print(f"  Queued:        {len(queued)}")
    print(f"  Failed:        {len(failed)}")
    print(f"  Total logged:  {len(log)}")
    weight  = _posting_weight()
    signals = _fetch_market_signals()
    print(f"  Post weight:   {weight:.2f} (BTC Δ24h={signals.get('btc_change_24h',0):.1f}%"
          f", F&G={signals.get('fear_greed',50)}, spike={signals.get('news_spike',False)})")
    print(f"  Transport:     X API v2 (pay-per-use, ~$0.01/post)")
    if queued:
        print("\n  Next up:")
        for e in queued[:3]:
            label = "[thread]" if e.get("is_thread") else f"[{e['type']}]"
            print(f"    {label} {e['text'][:70]}...")
    if failed:
        print("\n  Failed (needs review):")
        for e in failed[:3]:
            print(f"    [{e['type']}] retries={e.get('retries',0)} {e['text'][:60]}...")

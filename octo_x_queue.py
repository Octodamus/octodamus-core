"""
octo_x_queue.py
Octodamus — X Posting Queue via OpenTweet
No X developer account needed. Just an OPENTWEET_API_KEY.

Setup:
    1. Sign up at opentweet.io (7-day free trial, then $5.99/mo)
    2. Connect @octodamusai via one-click OAuth in their onboarding
    3. Settings > API > Generate New Key  →  starts with ot_
    4. Add to .env: OPENTWEET_API_KEY=ot_your_key_here
    5. pip install httpx python-dotenv

OpenClaw skill (optional, fastest setup):
    clawhub install opentweet-x-poster
"""

import hashlib
import httpx
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo  # Python 3.9+ stdlib

# Keys injected into os.environ by bitwarden.load_all_secrets() at startup.
# FIX: Always call _headers() at request time — never read os.environ at module level.
BASE_URL = "https://opentweet.io/api/v1"

# Timezone for posting hours — must match crontab TZ= setting
_TZ = ZoneInfo("America/Los_Angeles")

# Max entries to retain in the dedup log before trimming
_MAX_LOG_ENTRIES = 5_000

# Max retries before marking a post as permanently failed
_MAX_RETRIES = 3

QUEUE_FILE = Path("octo_post_queue.json")
POSTED_LOG  = Path("octo_posted_log.json")


def _headers() -> dict:
    """Build headers at call time so Bitwarden key is always current."""
    return {
        "Authorization": f"Bearer {os.environ.get('OPENTWEET_API_KEY', '')}",
        "Content-Type": "application/json",
    }


# ─────────────────────────────────────────────
# OPENTWEET API
# ─────────────────────────────────────────────

def check_connection() -> dict:
    """Verify key + check daily posting limits."""
    r = httpx.get(f"{BASE_URL}/me", headers=_headers(), timeout=10)
    r.raise_for_status()
    return r.json()


def _api_post(payload: dict) -> dict:
    r = httpx.post(f"{BASE_URL}/posts", headers=_headers(), json=payload, timeout=15)
    r.raise_for_status()
    return r.json()


def _publish_now(post_id: str) -> dict:
    r = httpx.post(f"{BASE_URL}/posts/{post_id}/publish", headers=_headers(), timeout=10)
    r.raise_for_status()
    return r.json()


def post_single_now(text: str) -> dict:
    """Create and immediately publish one post."""
    result = _api_post({"text": text})
    # API returns {"success": true, "posts": [{"id": "...", "status": "draft"}]}
    post_id = (
        result.get("id")
        or result.get("post", {}).get("id")
        or (result.get("posts") or [{}])[0].get("id")
    )
    if post_id:
        _publish_now(post_id)
    else:
        print(f"[OctoQueue] WARNING: Could not extract post_id from response: {result}")
    return result


def post_thread_now(posts: list) -> dict:
    """
    Post a thread immediately.
    posts[0] = first tweet, rest go in thread_tweets.
    """
    payload = {
        "text": posts[0],
        "is_thread": True,
        "thread_tweets": posts[1:],
    }
    result = _api_post(payload)
    # API returns {"success": true, "posts": [{"id": "...", "status": "draft"}]}
    post_id = (
        result.get("id")
        or result.get("post", {}).get("id")
        or (result.get("posts") or [{}])[0].get("id")
    )
    if post_id:
        _publish_now(post_id)
    else:
        print(f"[OctoQueue] WARNING: Could not extract post_id from response: {result}")
    return result


def schedule_post(text: str, scheduled_date: str) -> dict:
    """Schedule a future post. scheduled_date = ISO 8601."""
    return _api_post({"text": text, "scheduled_date": scheduled_date})


def bulk_schedule(posts: list) -> dict:
    """Bulk create up to 50 posts. posts = [{"text": "...", "scheduled_date": "..."}, ...]"""
    return _api_post({"posts": posts})


# ─────────────────────────────────────────────
# LOCAL QUEUE — atomic reads/writes
# ─────────────────────────────────────────────

def _load_queue() -> list:
    if QUEUE_FILE.exists():
        try:
            return json.loads(QUEUE_FILE.read_text())
        except json.JSONDecodeError:
            print("[OctoQueue] WARNING: queue file corrupt — starting fresh.")
            return []
    return []


def _save_queue(q: list) -> None:
    """
    Atomic write — write to temp file then rename.
    FIX: prevents partial writes if process is killed mid-write.
    """
    tmp = QUEUE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(q, indent=2))
    tmp.replace(QUEUE_FILE)


def _load_log() -> dict:
    if POSTED_LOG.exists():
        try:
            return json.loads(POSTED_LOG.read_text())
        except json.JSONDecodeError:
            print("[OctoQueue] WARNING: log file corrupt — starting fresh.")
            return {}
    return {}


def _save_log(log: dict) -> None:
    """
    Atomic write + size cap.
    FIX: trims log to _MAX_LOG_ENTRIES to prevent unbounded growth.
    """
    # Trim: keep most recent entries if over cap
    if len(log) > _MAX_LOG_ENTRIES:
        sorted_keys = sorted(
            log,
            key=lambda k: log[k].get("posted_at", ""),
            reverse=True,
        )
        log = {k: log[k] for k in sorted_keys[:_MAX_LOG_ENTRIES]}

    tmp = POSTED_LOG.with_suffix(".tmp")
    tmp.write_text(json.dumps(log, indent=2))
    tmp.replace(POSTED_LOG)


def _hash(text: str) -> str:
    return hashlib.md5(text.strip().lower().encode()).hexdigest()


def _posting_hours() -> bool:
    """
    FIX: Use explicit LA timezone — datetime.now() without tz uses server local time
    which may be UTC on cloud/VPS deployments and would post at wrong hours.
    """
    now = datetime.now(tz=_TZ)
    h = now.hour
    return (7 <= h <= 21) if now.weekday() < 5 else (9 <= h <= 18)


# ─────────────────────────────────────────────
# QUEUE OPERATIONS
# ─────────────────────────────────────────────

def queue_post(
    text: str,
    post_type: str = "signal",
    metadata: dict = None,
    priority: int = 5,
) -> str | None:
    """Add a single post to the local queue."""
    queue = _load_queue()
    log = _load_log()
    cid = _hash(text)

    if cid in log:
        print(f"[OctoQueue] Duplicate — skipping: {text[:60]}...")
        return None

    queue.append({
        "id": cid,
        "text": text,
        "type": post_type,
        "is_thread": False,
        "priority": priority,
        "retries": 0,
        "created_at": datetime.now(tz=_TZ).isoformat(),
        "metadata": metadata or {},
        "status": "queued",
    })
    queue.sort(key=lambda x: x["priority"])
    _save_queue(queue)
    print(f"[OctoQueue] Queued ({post_type}, p{priority}): {text[:60]}...")
    return cid


def queue_thread(
    posts: list,
    post_type: str = "deep_dive",
    metadata: dict = None,
) -> str | None:
    """Queue a thread as a single entry — OpenTweet handles threading natively."""
    queue = _load_queue()
    log = _load_log()
    cid = _hash(posts[0])

    if cid in log:
        print("[OctoQueue] Thread duplicate — skipping.")
        return None

    queue.append({
        "id": cid,
        "text": posts[0],
        "thread_posts": posts,
        "type": post_type,
        "is_thread": True,
        "priority": 3,
        "retries": 0,
        "created_at": datetime.now(tz=_TZ).isoformat(),
        "metadata": metadata or {},
        "status": "queued",
    })
    queue.sort(key=lambda x: x["priority"])
    _save_queue(queue)
    print(f"[OctoQueue] Thread queued ({len(posts)} posts): {posts[0][:60]}...")
    return cid


def process_queue(max_posts: int = 5) -> int:
    # AUTOPOST GATE — remove file to enable live posting
    import pathlib
    if pathlib.Path("/home/walli/octodamus/AUTOPOST_DISABLED").exists():
        print("[OctoQueue] 🔒 AUTOPOST_DISABLED flag set. Queuing only, no live posts.")
        return 0
    """
    Drain up to max_posts from queue → post via OpenTweet. Call every 30 min.

    FIX: Also retries failed posts (up to _MAX_RETRIES times) — previously
    failed posts were stuck forever.
    """
    if not _posting_hours():
        print("[OctoQueue] Outside posting hours. The oracle rests.")
        return 0

    queue = _load_queue()

    # FIX: Include failed posts eligible for retry, not just queued
    pending = [
        p for p in queue
        if p["status"] == "queued"
        or (p["status"] == "failed" and p.get("retries", 0) < _MAX_RETRIES)
    ]

    if not pending:
        print("[OctoQueue] Queue empty.")
        return 0

    # Check limits before posting
    try:
        status = check_connection()
        remaining = status.get("limits", {}).get("remaining_posts_today", 0)
        if remaining <= 0:
            print("[OctoQueue] Daily OpenTweet limit reached.")
            return 0
        max_posts = min(max_posts, remaining)
    except Exception as e:
        print(f"[OctoQueue] OpenTweet connection error: {e}")
        return 0

    log = _load_log()
    posted_count = 0

    for entry in pending[:max_posts]:
        try:
            if entry.get("is_thread"):
                result = post_thread_now(entry["thread_posts"])
                print(f"[OctoQueue] ✓ Thread ({len(entry['thread_posts'])} posts): {entry['text'][:60]}...")
            else:
                result = post_single_now(entry["text"])
                print(f"[OctoQueue] ✓ Posted [{entry['type']}]: {entry['text'][:60]}...")

            log[entry["id"]] = {
                "result": result,
                "text": entry["text"],
                "type": entry["type"],
                "posted_at": datetime.now(tz=_TZ).isoformat(),
                "metadata": entry["metadata"],
            }
            _save_log(log)

            for q in queue:
                if q["id"] == entry["id"]:
                    q["status"] = "posted"
                    break
            _save_queue(queue)
            posted_count += 1

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                print("[OctoQueue] Rate limited (60 req/min). Stopping.")
                break

            print(f"[OctoQueue] HTTP {e.response.status_code}: {e.response.text[:200]}")
            for q in queue:
                if q["id"] == entry["id"]:
                    q["retries"] = q.get("retries", 0) + 1
                    q["status"] = "failed" if q["retries"] >= _MAX_RETRIES else "queued"
                    if q["status"] == "failed":
                        print(f"[OctoQueue] Post permanently failed after {_MAX_RETRIES} retries: {entry['text'][:60]}...")
                    break
            _save_queue(queue)

        except Exception as e:
            print(f"[OctoQueue] Unexpected error: {e}")
            break

    # Trim — keep last 500 posted for deduplication, all queued + failed
    _save_queue(
        [e for e in queue if e["status"] == "queued"]
        + [e for e in queue if e["status"] == "failed"]
        + [e for e in queue if e["status"] == "posted"][-500:]
    )
    return posted_count


def queue_status() -> None:
    queue = _load_queue()
    log = _load_log()
    today = datetime.now(tz=_TZ).date()

    queued = [e for e in queue if e["status"] == "queued"]
    failed = [e for e in queue if e["status"] == "failed"]
    posted_today = [
        v for v in log.values()
        if "posted_at" in v
        and datetime.fromisoformat(v["posted_at"]).astimezone(_TZ).date() == today
    ]

    # FIX: No bare except — properly handle connection errors
    limit_str = "  Daily remaining: (OpenTweet unreachable)"
    try:
        s = check_connection()
        limit_str = f"  Daily remaining: {s.get('limits', {}).get('remaining_posts_today', '?')}"
    except Exception as e:
        limit_str = f"  Daily remaining: (error: {e})"

    print("\n🐙 OCTODAMUS X QUEUE STATUS")
    print(limit_str)
    print(f"  Queued:        {len(queued)} posts waiting")
    print(f"  Failed:        {len(failed)} posts (max retries)")
    print(f"  Posted today:  {len(posted_today)}")
    print(f"  Total logged:  {len(log)} all-time")

    if queued:
        print("\n  Next up:")
        for e in queued[:3]:
            label = "[thread]" if e.get("is_thread") else f"[{e['type']}]"
            print(f"    {label} {e['text'][:70]}...")

    if failed:
        print("\n  Permanently failed (needs manual review):")
        for e in failed[:3]:
            print(f"    [{e['type']}] {e['text'][:70]}...")

    print()

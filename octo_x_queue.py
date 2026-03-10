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

import httpx
import json
import os
import hashlib
from datetime import datetime
from pathlib import Path

# Keys injected into os.environ by bitwarden.load_all_secrets() at startup
# IMPORTANT: Never read os.environ at module level — Bitwarden hasn't loaded keys yet at import time.
# Always call _headers() at request time.

BASE_URL = "https://opentweet.io/api/v1"

def _headers() -> dict:
    """Build headers at call time so Bitwarden key is available."""
    return {
        "Authorization": f"Bearer {os.environ.get('OPENTWEET_API_KEY', '')}",
        "Content-Type": "application/json"
    }

QUEUE_FILE = Path("octo_post_queue.json")
POSTED_LOG  = Path("octo_posted_log.json")


# ─────────────────────────────────────────────
# OPENTWEET API
# ─────────────────────────────────────────────

def check_connection() -> dict:
    """Verify key + check daily posting limits."""
    r = httpx.get(f"{BASE_URL}/me", headers=_headers())
    r.raise_for_status()
    return r.json()


def _api_post(payload: dict) -> dict:
    r = httpx.post(f"{BASE_URL}/posts", headers=_headers(), json=payload, timeout=15)
    r.raise_for_status()
    return r.json()


def _publish_now(post_id: str) -> dict:
    r = httpx.post(f"{BASE_URL}/posts/{post_id}/publish", headers=_headers())
    r.raise_for_status()
    return r.json()


def post_single_now(text: str) -> dict:
    """Create and immediately publish one post."""
    result = _api_post({"text": text})
    post_id = result.get("id") or result.get("post", {}).get("id")
    if post_id:
        _publish_now(post_id)
    return result


def post_thread_now(posts: list) -> dict:
    """
    Post a thread immediately.
    posts[0] = first tweet, rest go in thread_tweets.
    """
    payload = {
        "text": posts[0],
        "is_thread": True,
        "thread_tweets": posts[1:]
    }
    result = _api_post(payload)
    post_id = result.get("id") or result.get("post", {}).get("id")
    if post_id:
        _publish_now(post_id)
    return result


def schedule_post(text: str, scheduled_date: str) -> dict:
    """Schedule a future post. scheduled_date = ISO 8601."""
    return _api_post({"text": text, "scheduled_date": scheduled_date})


def bulk_schedule(posts: list) -> dict:
    """Bulk create up to 50 posts. posts = [{"text": "...", "scheduled_date": "..."}, ...]"""
    return _api_post({"posts": posts})


# ─────────────────────────────────────────────
# LOCAL QUEUE
# ─────────────────────────────────────────────

def _load_queue() -> list:
    if QUEUE_FILE.exists():
        return json.loads(QUEUE_FILE.read_text())
    return []

def _save_queue(q: list):
    QUEUE_FILE.write_text(json.dumps(q, indent=2))

def _load_log() -> dict:
    if POSTED_LOG.exists():
        return json.loads(POSTED_LOG.read_text())
    return {}

def _save_log(log: dict):
    POSTED_LOG.write_text(json.dumps(log, indent=2))

def _hash(text: str) -> str:
    return hashlib.md5(text.strip().lower().encode()).hexdigest()

def _posting_hours() -> bool:
    now = datetime.now()
    h = now.hour
    return (7 <= h <= 21) if now.weekday() < 5 else (9 <= h <= 18)


def queue_post(text: str, post_type: str = "signal", metadata: dict = None, priority: int = 5):
    """Add a single post to the local queue."""
    queue = _load_queue()
    log = _load_log()
    cid = _hash(text)
    if cid in log:
        print(f"[OctoQueue] Duplicate — skipping: {text[:60]}...")
        return None
    queue.append({
        "id": cid, "text": text, "type": post_type, "is_thread": False,
        "priority": priority, "created_at": datetime.now().isoformat(),
        "metadata": metadata or {}, "status": "queued"
    })
    queue.sort(key=lambda x: x["priority"])
    _save_queue(queue)
    print(f"[OctoQueue] Queued ({post_type}, p{priority}): {text[:60]}...")
    return cid


def queue_thread(posts: list, post_type: str = "deep_dive", metadata: dict = None):
    """Queue a thread as a single entry — OpenTweet handles threading natively."""
    queue = _load_queue()
    log = _load_log()
    cid = _hash(posts[0])
    if cid in log:
        print("[OctoQueue] Thread duplicate — skipping.")
        return None
    queue.append({
        "id": cid, "text": posts[0], "thread_posts": posts, "type": post_type,
        "is_thread": True, "priority": 3, "created_at": datetime.now().isoformat(),
        "metadata": metadata or {}, "status": "queued"
    })
    queue.sort(key=lambda x: x["priority"])
    _save_queue(queue)
    print(f"[OctoQueue] Thread queued ({len(posts)} posts): {posts[0][:60]}...")
    return cid


def process_queue(max_posts: int = 5) -> int:
    """Drain up to max_posts from queue → post via OpenTweet. Call every 30 min."""
    if not _posting_hours():
        print("[OctoQueue] Outside posting hours. The oracle rests.")
        return 0

    queue = _load_queue()
    pending = [p for p in queue if p["status"] == "queued"]
    if not pending:
        print("[OctoQueue] Queue empty.")
        return 0

    # Check limits before posting
    try:
        status = check_connection()
        remaining = status.get("remaining_posts_today", 0)
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
                "result": result, "text": entry["text"], "type": entry["type"],
                "posted_at": datetime.now().isoformat(), "metadata": entry["metadata"]
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
            print(f"[OctoQueue] HTTP {e.response.status_code}: {e.response.text}")
            for q in queue:
                if q["id"] == entry["id"]:
                    q["status"] = "failed"
                    break
            _save_queue(queue)
        except Exception as e:
            print(f"[OctoQueue] Error: {e}")
            break

    # Trim — keep last 500 posted for deduplication
    _save_queue(
        [e for e in queue if e["status"] == "queued"] +
        [e for e in queue if e["status"] == "failed"] +
        [e for e in queue if e["status"] == "posted"][-500:]
    )
    return posted_count


def queue_status():
    queue = _load_queue()
    log = _load_log()
    today = datetime.today().date()
    queued = [e for e in queue if e["status"] == "queued"]
    posted_today = [v for v in log.values()
                    if "posted_at" in v and datetime.fromisoformat(v["posted_at"]).date() == today]
    try:
        s = check_connection()
        limit = f"  Daily remaining: {s.get('limits', {}).get('remaining_posts_today', '?')}"
    except:
        limit = "  Daily remaining: (OpenTweet unreachable)"

    print("\n🐙 OCTODAMUS X QUEUE STATUS")
    print(limit)
    print(f"  Queued:        {len(queued)} posts waiting")
    print(f"  Posted today:  {len(posted_today)}")
    print(f"  Total logged:  {len(log)} all-time")
    if queued:
        print("\n  Next up:")
        for e in queued[:3]:
            label = "[thread]" if e.get("is_thread") else f"[{e['type']}]"
            print(f"    {label} {e['text'][:70]}...")
    print()

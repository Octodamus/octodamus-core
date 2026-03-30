"""
octo_x_poster.py
Octodamus — X Posting via OpenTweet

Transport: opentweet.io API (OPENTWEET_API_KEY from Bitwarden)
Daily limit: 6 posts self-imposed (3 monitor + 3 daily reads)

All known OpenTweet bugs fixed:
 - Post ID is at posts[0].id not id or post.id
 - remaining_posts_today is inside limits{} not top-level
 - Background tasks use .octo_secrets cache (no BW_SESSION needed)
 - Daily limit enforced in code independently of OpenTweet
 - Queue atomic writes prevent corruption on process kill
 - Duplicate detection via MD5 hash
 - Discord webhook notification on every post
"""

import hashlib
import json
import os
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

# ─────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────

BASE_URL   = "https://opentweet.io/api/v1"
_TZ      = ZoneInfo("America/Los_Angeles")
_MAX_RETRIES = 3
_MAX_LOG   = 5_000
_DAILY_LIMIT = 6
MAX_QUEUE_AGE_HOURS = 4  # Drop queued posts older than this — market data goes stale
_THREAD_DELAY = 2.0

QUEUE_FILE = Path(__file__).parent / "octo_post_queue.json"
POSTED_LOG = Path(__file__).parent / "octo_posted_log.json"

FORCE_POST = False


# ─────────────────────────────────────────────
# OPENTWEET API
# ─────────────────────────────────────────────

def _headers() -> dict:
  return {
    "Authorization": f"Bearer {os.environ.get('OPENTWEET_API_KEY', '')}",
    "Content-Type": "application/json",
  }


def check_connection() -> dict:
  r = httpx.get(f"{BASE_URL}/me", headers=_headers(), timeout=10)
  r.raise_for_status()
  return r.json()


def _opentweet_remaining() -> int:
  try:
    status = check_connection()
    # FIX: nested inside limits{} not top-level
    return status.get("limits", {}).get("remaining_posts_today", 20)
  except Exception as e:
    print(f"[OctoPoster] OpenTweet status check failed: {e}")
    return 0


def _create_post(payload: dict) -> dict:
  r = httpx.post(f"{BASE_URL}/posts", headers=_headers(), json=payload, timeout=15)
  r.raise_for_status()
  return r.json()


def _publish_post(post_id: str) -> dict:
  r = httpx.post(f"{BASE_URL}/posts/{post_id}/publish", headers=_headers(), timeout=10)
  r.raise_for_status()
  return r.json()


def _extract_post_id(result: dict) -> str | None:
  """
  FIX: OpenTweet returns post ID at posts[0].id not at id or post.id.
  """
  posts_list = result.get("posts")
  if posts_list and isinstance(posts_list, list) and posts_list:
    return posts_list[0].get("id")
  if result.get("id"):
    return result["id"]
  if result.get("post", {}).get("id"):
    return result["post"]["id"]
  return None


def _post_single(text: str) -> dict:
  result = _create_post({"text": text})
  post_id = _extract_post_id(result)
  if not post_id:
    raise ValueError(f"OpenTweet returned no post ID. Response: {result}")
  return _publish_post(post_id)


def _post_thread(posts: list) -> dict:
  payload = {
    "text": posts[0],
    "is_thread": True,
    "thread_tweets": posts[1:],
  }
  result = _create_post(payload)
  post_id = _extract_post_id(result)
  if not post_id:
    raise ValueError(f"OpenTweet returned no post ID for thread. Response: {result}")
  return _publish_post(post_id)


# ─────────────────────────────────────────────
# POSTING HOURS + DAILY LIMIT
# ─────────────────────────────────────────────

def _in_posting_hours() -> bool:
  if FORCE_POST:
    return True
  now = datetime.now(tz=_TZ)
  h = now.hour
  is_weekday = now.weekday() < 5
  return (7 <= h <= 21) if is_weekday else (9 <= h <= 18)


def _posts_today() -> int:
  log = _load_log()
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

def queue_post(text: str, post_type: str = "signal", metadata: dict = None, priority: int = 5) -> str | None:
  queue = _load_queue()
  log  = _load_log()
  cid  = _hash(text)
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
  log  = _load_log()
  cid  = _hash(posts[0])
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
  print(f"[OctoPoster] Thread queued ({len(posts)} posts): {posts[0][:60]}...")
  return cid



def purge_stale_queue() -> int:
  """
  Drop queued posts older than MAX_QUEUE_AGE_HOURS.
  Call at start of each runner mode to ensure stale market data never posts.
  Returns count of posts dropped.
  """
  queue = _load_queue()
  now  = datetime.now(tz=_TZ)
  fresh, dropped = [], 0
  for entry in queue:
    if entry["status"] != "queued":
      fresh.append(entry)
      continue
    try:
      created = datetime.fromisoformat(entry["created_at"]).astimezone(_TZ)
      age_hours = (now - created).total_seconds() / 3600
      if age_hours > MAX_QUEUE_AGE_HOURS:
        dropped += 1
        print(f"[OctoPoster] Dropped stale post ({age_hours:.1f}h old): {entry['text'][:60]}...")
      else:
        fresh.append(entry)
    except Exception:
      fresh.append(entry) # keep if we can't parse the date
  if dropped:
    _save_queue(fresh)
    print(f"[OctoPoster] Purged {dropped} stale post(s) from queue.")
  return dropped


def process_queue(max_posts: int = 1) -> int:
  purge_stale_queue() # drop stale market data before posting
  if not _in_posting_hours():
    print(f"[OctoPoster] Outside posting hours — skipping.")
    return 0

  today_count = _posts_today()
  effective_limit = 99 if FORCE_POST else _DAILY_LIMIT
  if today_count >= effective_limit:
    print(f"[OctoPoster] Daily limit reached ({today_count}/{_DAILY_LIMIT}).")
    return 0

  remaining_today = effective_limit - today_count
  max_posts = min(max_posts, remaining_today)

  ot_remaining = _opentweet_remaining()
  if ot_remaining <= 0:
    print(f"[OctoPoster] OpenTweet daily limit reached.")
    return 0
  max_posts = min(max_posts, ot_remaining)

  queue  = _load_queue()
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
        result = _post_thread(entry["thread_posts"])
        tweet_url = result.get("url", "")
        print(f"[OctoPoster] ✓ Thread ({len(entry['thread_posts'])} posts): {entry['text'][:60]}...")
      else:
        result = _post_single(entry["text"])
        tweet_url = result.get("url", "")
        print(f"[OctoPoster] ✓ Posted [{entry['type']}]: {entry['text'][:60]}...")

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

    except httpx.HTTPStatusError as e:
      code = e.response.status_code
      body = e.response.text[:300]
      if code == 429:
        print(f"[OctoPoster] Rate limited. Stopping.")
        break
      print(f"[OctoPoster] HTTP {code}: {body}")
      for q in queue:
        if q["id"] == entry["id"]:
          q["retries"] = q.get("retries", 0) + 1
          q["status"] = "failed" if q["retries"] >= _MAX_RETRIES else "queued"
          break
      _save_queue(queue)

    except Exception as e:
      print(f"[OctoPoster] Error: {e}")
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
    httpx.post(webhook, json={"content": f" **[{post_type}]**\n{text[:280]}\n{tweet_url}"}, timeout=5)
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
# STATUS + CREDENTIALS CHECK
# ─────────────────────────────────────────────

def queue_status() -> None:
  queue = _load_queue()
  log  = _load_log()
  today = datetime.now(tz=_TZ).date()
  queued = [e for e in queue if e["status"] == "queued"]
  failed = [e for e in queue if e["status"] == "failed"]
  posted_today = [
    v for v in log.values()
    if "posted_at" in v
    and datetime.fromisoformat(v["posted_at"]).astimezone(_TZ).date() == today
  ]
  ot_remaining = _opentweet_remaining()
  print("\n🐙 OCTODAMUS X POSTER STATUS")
  print(f" Posted today:  {len(posted_today)} / {_DAILY_LIMIT}")
  print(f" OpenTweet quota: {ot_remaining} remaining")
  print(f" Queued:     {len(queued)}")
  print(f" Failed:     {len(failed)}")
  print(f" Total logged:  {len(log)}")
  print(f" Posting hours:  {'OPEN' if _in_posting_hours() else 'CLOSED'}")
  if queued:
    print("\n Next up:")
    for e in queued[:3]:
      label = "[thread]" if e.get("is_thread") else f"[{e['type']}]"
      print(f"  {label} {e['text'][:70]}...")
  if failed:
    print("\n Failed (needs review):")
    for e in failed[:3]:
      print(f"  [{e['type']}] retries={e.get('retries',0)} {e['text'][:70]}...")
  print()


def check_credentials() -> bool:
  key = os.environ.get("OPENTWEET_API_KEY", "")
  if not key:
    print("[OctoPoster] OPENTWEET_API_KEY not set.")
    return False
  try:
    status = check_connection()
    if status.get("authenticated"):
      remaining = status.get("limits", {}).get("remaining_posts_today", "?")
      print(f"[OctoPoster] ✓ OpenTweet connected — {remaining} posts remaining today")
      return True
    print(f"[OctoPoster] Not authenticated: {status}")
    return False
  except Exception as e:
    print(f"[OctoPoster] Check failed: {e}")
    return False
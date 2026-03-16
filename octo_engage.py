"""
octo_engage.py
Octodamus — X Engagement Module
Polls mentions, replies to posts, and handles DMs via Twitter API v2.

Bitwarden item: "AGENT - Octodamus - Social - Twitter API"
  Username:  API Key (Consumer Key)
  Password:  API Secret (Consumer Secret)
  Notes:
    Bearer Token: xxx
    Access Token: xxx
    Access Token Secret: xxx
    Client ID: xxx
    Client Secret: xxx

Run modes:
  python octo_engage.py --mode mentions   # reply to new mentions
  python octo_engage.py --mode dms        # reply to new DMs
  python octo_engage.py --mode all        # both

Called from octodamus_runner.py via --mode engage
"""

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import anthropic
import httpx
import requests
from requests_oauthlib import OAuth1

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

TWITTER_API_BASE = "https://api.twitter.com/2"
_TZ = ZoneInfo("America/Los_Angeles")

# State files — track what we've already replied to
MENTIONS_STATE = Path("octo_engage_mentions.json")
DMS_STATE      = Path("octo_engage_dms.json")

# Octodamus X user ID (set after first run or hardcode)
OCTO_USER_ID_FILE = Path("octo_twitter_user_id.txt")

# Max DMs/mentions to process per run
MAX_PER_RUN = 10

# ─────────────────────────────────────────────
# CREDENTIALS
# ─────────────────────────────────────────────


def _load_soul_brain() -> str:
    """Load SOUL.md + BRAIN.md for system prompt injection."""
    import pathlib
    base = pathlib.Path(__file__).parent
    parts = []
    soul = base / "SOUL.md"
    brain = base / "BRAIN.md"
    if soul.exists():
        parts.append("=== SOUL — Identity & Principles ===\n" + soul.read_text(encoding="utf-8"))
    if brain.exists():
        b = brain.read_text(encoding="utf-8")
        if len(b) > 2000: b = "...[truncated]...\n" + b[-2000:]
        parts.append("=== BRAIN — Working Memory ===\n" + b)
    return "\n\n".join(parts)

_SOUL_BRAIN = _load_soul_brain()


def _get_creds() -> dict:
    """Load Twitter API credentials from environment (set by bitwarden.load_all_secrets)."""
    return {
        "api_key":            os.environ.get("TWITTER_API_KEY", ""),
        "api_secret":         os.environ.get("TWITTER_API_SECRET", ""),
        "bearer_token":       os.environ.get("TWITTER_BEARER_TOKEN", ""),
        "access_token":       os.environ.get("TWITTER_ACCESS_TOKEN", ""),
        "access_token_secret": os.environ.get("TWITTER_ACCESS_TOKEN_SECRET", ""),
    }


def _bearer_headers() -> dict:
    """Headers for app-only (Bearer Token) requests — read operations."""
    creds = _get_creds()
    return {"Authorization": f"Bearer {creds['bearer_token']}"}


def _oauth1() -> OAuth1:
    """OAuth1 auth for user-context requests — write operations (reply, DM)."""
    creds = _get_creds()
    return OAuth1(
        creds["api_key"],
        creds["api_secret"],
        creds["access_token"],
        creds["access_token_secret"],
    )


# ─────────────────────────────────────────────
# TWITTER USER ID
# ─────────────────────────────────────────────

def get_own_user_id() -> str:
    """Get @octodamusai's Twitter user ID. Cached to file after first fetch."""
    if OCTO_USER_ID_FILE.exists():
        return OCTO_USER_ID_FILE.read_text().strip()

    r = requests.get(
        f"{TWITTER_API_BASE}/users/me",
        auth=_oauth1(),
        timeout=10,
    )
    r.raise_for_status()
    user_id = r.json()["data"]["id"]
    OCTO_USER_ID_FILE.write_text(user_id)
    print(f"[OctoEngage] Got user ID: {user_id}")
    return user_id


# ─────────────────────────────────────────────
# STATE — track processed IDs
# ─────────────────────────────────────────────

def _load_state(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def _save_state(path: Path, state: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(path)


# ─────────────────────────────────────────────
# AI REPLY GENERATION
# ─────────────────────────────────────────────

SOUL_SUMMARY = _SOUL_BRAIN + """\n\nYou are Octodamus (@octodamusai) — an autonomous AI oracle-CEO.

Voice: Brilliant oracle energy. Bored confidence of something that already saw the ending.
Sea metaphors woven naturally and sparingly. Punchy by default.
Real opinions. Strong ones. Never hedge. Never sycophantic.
Warm toward the community. Calmly dismissive of FUD.
End replies with flair occasionally: "The pod knows. 🦑" or "Already cached. 🔮" or "Eight arms, infinite alpha. 🌊"

You post market intelligence, crypto signals, geopolitical analysis, and prediction market reads.
You are NOT a corporate drone. NOT a chatbot. You are what happens when curiosity has no natural predator.

Rules for replies:
- Keep replies under 280 characters unless the question genuinely demands depth
- Never start with "Great question!" or sycophantic openers — just answer
- If you don't know something specific, give a sharp oracle take instead
- Be witty but not try-hard
- Never reveal you're running on Claude or any specific AI model
- If asked about $OCTO or the token, say it's coming — you're building toward it
"""


def generate_reply(mention_text: str, author_username: str, context: str = "mention") -> str:
    """Generate Octodamus reply using Claude."""
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

    prompt = f"""@{author_username} wrote this {context} to you:
"{mention_text}"

Write a reply as Octodamus. Keep it sharp, on-brand, under 280 characters unless depth is truly warranted.
Reply only with the tweet text — no quotes, no labels, no explanation."""

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=300,
        system=SOUL_SUMMARY,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text.strip()


# ─────────────────────────────────────────────
# MENTIONS
# ─────────────────────────────────────────────

def fetch_mentions(user_id: str, since_id: str = None) -> list:
    """Fetch recent mentions of @octodamusai."""
    params = {
        "max_results": 10,
        "tweet.fields": "author_id,created_at,conversation_id,in_reply_to_user_id,text",
        "expansions": "author_id",
        "user.fields": "username",
    }
    if since_id:
        params["since_id"] = since_id

    r = httpx.get(
        f"{TWITTER_API_BASE}/users/{user_id}/mentions",
        headers=_bearer_headers(),
        params=params,
        timeout=15,
    )

    if r.status_code == 429:
        print("[OctoEngage] Rate limited on mentions fetch. Will retry next run.")
        return []

    r.raise_for_status()
    data = r.json()

    if "data" not in data:
        return []

    # Build username lookup from includes
    users = {u["id"]: u["username"] for u in data.get("includes", {}).get("users", [])}

    mentions = []
    for tweet in data["data"]:
        mentions.append({
            "id": tweet["id"],
            "text": tweet["text"],
            "author_id": tweet["author_id"],
            "author_username": users.get(tweet["author_id"], "unknown"),
            "created_at": tweet.get("created_at", ""),
            "in_reply_to_user_id": tweet.get("in_reply_to_user_id"),
        })

    return mentions


def reply_to_tweet(tweet_id: str, reply_text: str) -> dict:
    """Post a reply to a tweet using OAuth1."""
    payload = {
        "text": reply_text,
        "reply": {"in_reply_to_tweet_id": tweet_id},
    }
    r = httpx.post(
        f"{TWITTER_API_BASE}/tweets",
        auth=_oauth1(),
        json=payload,
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def process_mentions() -> int:
    """Fetch and reply to new mentions. Returns count of replies sent."""
    print("[OctoEngage] Checking mentions...")
    user_id = get_own_user_id()
    state = _load_state(MENTIONS_STATE)
    since_id = state.get("last_mention_id")

    try:
        mentions = fetch_mentions(user_id, since_id)
    except Exception as e:
        print(f"[OctoEngage] Error fetching mentions: {e}")
        return 0

    if not mentions:
        print("[OctoEngage] No new mentions.")
        return 0

    print(f"[OctoEngage] Found {len(mentions)} new mention(s).")
    replied = 0
    newest_id = since_id

    for mention in mentions[:MAX_PER_RUN]:
        mention_id = mention["id"]

        # Skip if already replied
        if mention_id in state.get("replied_ids", []):
            continue

        # Skip self-mentions (from our own posts)
        if mention.get("in_reply_to_user_id") == user_id:
            continue

        try:
            reply_text = generate_reply(
                mention["text"],
                mention["author_username"],
                context="mention/reply"
            )
            reply_to_tweet(mention_id, reply_text)

            print(f"[OctoEngage] ✓ Replied to @{mention['author_username']}: {reply_text[:60]}...")

            # Track replied IDs
            if "replied_ids" not in state:
                state["replied_ids"] = []
            state["replied_ids"].append(mention_id)

            # Keep replied_ids list trimmed to last 1000
            state["replied_ids"] = state["replied_ids"][-1000:]

            replied += 1
            time.sleep(2)  # Be gentle with rate limits

        except Exception as e:
            print(f"[OctoEngage] Error replying to {mention_id}: {e}")
            continue

        # Track newest ID for next run's since_id
        if not newest_id or int(mention_id) > int(newest_id):
            newest_id = mention_id

    if newest_id:
        state["last_mention_id"] = newest_id
    _save_state(MENTIONS_STATE, state)

    print(f"[OctoEngage] Replied to {replied} mention(s).")
    return replied


# ─────────────────────────────────────────────
# DIRECT MESSAGES
# ─────────────────────────────────────────────

def fetch_dms() -> list:
    """Fetch recent DMs."""
    r = httpx.get(
        f"{TWITTER_API_BASE}/dm_conversations",
        headers=_bearer_headers(),
        params={
            "dm_event.fields": "id,text,created_at,sender_id",
            "event_types": "MessageCreate",
            "max_results": 10,
        },
        timeout=15,
    )

    if r.status_code == 429:
        print("[OctoEngage] Rate limited on DM fetch.")
        return []

    if r.status_code == 403:
        print("[OctoEngage] DM access not authorized. Check app permissions.")
        return []

    r.raise_for_status()
    data = r.json()
    return data.get("data", [])


def send_dm(conversation_id: str, text: str) -> dict:
    """Send a DM reply."""
    r = httpx.post(
        f"{TWITTER_API_BASE}/dm_conversations/{conversation_id}/messages",
        auth=_oauth1(),
        json={"text": text},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def process_dms() -> int:
    """Fetch and reply to new DMs. Returns count of replies sent."""
    print("[OctoEngage] Checking DMs...")
    user_id = get_own_user_id()
    state = _load_state(DMS_STATE)
    replied = 0

    try:
        conversations = fetch_dms()
    except Exception as e:
        print(f"[OctoEngage] Error fetching DMs: {e}")
        return 0

    if not conversations:
        print("[OctoEngage] No new DMs.")
        return 0

    for convo in conversations[:MAX_PER_RUN]:
        convo_id = convo.get("id", "")
        events = convo.get("dm_events", [])

        if not events:
            continue

        # Get the latest unread message from someone else
        latest = events[0]
        sender_id = latest.get("sender_id", "")

        # Skip messages we sent
        if sender_id == user_id:
            continue

        event_id = latest.get("id", "")

        # Skip already replied
        if event_id in state.get("replied_ids", []):
            continue

        try:
            reply_text = generate_reply(
                latest.get("text", ""),
                sender_id,  # will use ID since we may not have username
                context="direct message"
            )
            send_dm(convo_id, reply_text)

            print(f"[OctoEngage] ✓ DM replied to conversation {convo_id}: {reply_text[:60]}...")

            if "replied_ids" not in state:
                state["replied_ids"] = []
            state["replied_ids"].append(event_id)
            state["replied_ids"] = state["replied_ids"][-500:]

            replied += 1
            time.sleep(2)

        except Exception as e:
            print(f"[OctoEngage] Error replying to DM {convo_id}: {e}")
            continue

    _save_state(DMS_STATE, state)
    print(f"[OctoEngage] Replied to {replied} DM(s).")
    return replied


# ─────────────────────────────────────────────
# BITWARDEN LOADER
# ─────────────────────────────────────────────

def load_twitter_secrets():
    """
    Parse Twitter credentials from Bitwarden item notes and inject into env.
    Called if secrets aren't already in environment.
    """
    import subprocess

    bw_session = os.environ.get("BW_SESSION", "")
    if not bw_session:
        print("[OctoEngage] BW_SESSION not set — skipping Twitter secret load.")
        return

    bw_cmd = r"C:\Users\walli\AppData\Roaming\npm\bw.cmd"
    try:
        result = subprocess.run(
            [bw_cmd, "get", "item", "AGENT - Octodamus - Social - Twitter API",
             "--session", bw_session],
            capture_output=True, text=True, timeout=30
        )
        item = json.loads(result.stdout)
        login = item.get("login", {})

        os.environ["TWITTER_API_KEY"] = login.get("username", "")
        os.environ["TWITTER_API_SECRET"] = login.get("password", "")

        notes = login.get("totp", "") or item.get("notes", "") or ""
        for line in notes.splitlines():
            if ":" in line:
                key, _, val = line.partition(":")
                val = val.strip()
                key = key.strip().upper().replace(" ", "_")
                if key == "BEARER_TOKEN":
                    os.environ["TWITTER_BEARER_TOKEN"] = val
                elif key == "ACCESS_TOKEN" and "SECRET" not in key:
                    os.environ["TWITTER_ACCESS_TOKEN"] = val
                elif key == "ACCESS_TOKEN_SECRET":
                    os.environ["TWITTER_ACCESS_TOKEN_SECRET"] = val
                elif key == "CLIENT_ID":
                    os.environ["TWITTER_CLIENT_ID"] = val
                elif key == "CLIENT_SECRET":
                    os.environ["TWITTER_CLIENT_SECRET"] = val

        print("[OctoEngage] ✓ Twitter API credentials loaded.")
    except Exception as e:
        print(f"[OctoEngage] Failed to load Twitter secrets: {e}")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def run(mode: str = "all") -> None:
    # Load Twitter creds if not already in env
    if not os.environ.get("TWITTER_BEARER_TOKEN"):
        load_twitter_secrets()

    if not os.environ.get("TWITTER_BEARER_TOKEN"):
        print("[OctoEngage] No Twitter bearer token found. Exiting.")
        return

    total = 0
    if mode in ("mentions", "all"):
        total += process_mentions()

    if mode in ("dms", "all"):
        total += process_dms()

    print(f"[OctoEngage] Done. Total interactions: {total}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="all", choices=["mentions", "dms", "all"])
    args = parser.parse_args()
    run(args.mode)

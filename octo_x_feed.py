"""
octo_x_feed.py -- Shared X account feed for Octodamus posts.

Fetches recent posts from a curated list of accounts across markets,
tech, geopolitics, and culture. Used by:
  - octo_evening_journal.py  (journal context)
  - octo_engage.py           (article candidates)
  - octodamus_runner.py      (wisdom, monitor, daily read context)

Usage:
  from octo_x_feed import get_x_feed_context, get_x_feed_articles
"""

import json
from pathlib import Path

# Curated accounts — wide range of topics for Octodamus to riff on
X_ACCOUNTS = [
    ("KobeissiLetter", "Markets & macro"),
    ("zerohedge",      "Macro & geopolitics"),
    ("unusual_whales", "Politics & trading"),
    ("elonmusk",       "Tech & culture"),
    ("sama",           "AI"),
    ("BBCBreaking",    "World news"),
    ("Reuters",        "World news"),
    ("naval",          "Philosophy & tech"),
    ("paulg",          "Tech & culture"),
]

_SECRETS_PATH = Path(r"C:\Users\walli\octodamus\.octo_secrets")


def _bearer_token() -> str:
    try:
        raw = json.loads(_SECRETS_PATH.read_text(encoding="utf-8"))
        return raw.get("secrets", raw).get("TWITTER_BEARER_TOKEN", "")
    except Exception:
        return ""


def _fetch_account(handle: str, label: str, client, max_posts: int) -> list[dict]:
    try:
        user = client.get_user(username=handle, user_fields=["id"])
        if not user.data:
            return []
        tweets = client.get_users_tweets(
            id=user.data.id,
            max_results=max_posts,
            tweet_fields=["text", "created_at"],
            exclude=["retweets", "replies"],
        )
        if not tweets.data:
            return []
        results = []
        for t in tweets.data:
            text = t.text.strip()
            if len(text) < 40:
                continue
            results.append({
                "handle": handle,
                "label":  label,
                "text":   text[:240],
            })
        return results
    except Exception as e:
        print(f"[XFeed] @{handle} failed: {e}")
        return []


def get_x_posts(max_per_account: int = 3) -> list[dict]:
    """Fetch posts from all X_ACCOUNTS. Returns list of {handle, label, text}."""
    bearer = _bearer_token()
    if not bearer:
        return []
    try:
        import tweepy
        client = tweepy.Client(bearer_token=bearer, wait_on_rate_limit=False)
        results, seen = [], set()
        for handle, label in X_ACCOUNTS:
            for p in _fetch_account(handle, label, client, max_per_account):
                if p["text"] not in seen:
                    seen.add(p["text"])
                    results.append(p)
        return results
    except Exception as e:
        print(f"[XFeed] fetch failed: {e}")
        return []


def get_x_feed_context(max_per_account: int = 3, max_items: int = 18) -> str:
    """
    Returns a formatted string for injection into runner prompts.
    Format: [account/label] post text
    """
    posts = get_x_posts(max_per_account)[:max_items]
    if not posts:
        return ""
    lines = ["Voices from X today (markets, tech, geopolitics, culture):"]
    for p in posts:
        lines.append(f"  [@{p['handle']} / {p['label']}] {p['text']}")
    return "\n".join(lines)


def get_x_feed_articles(max_per_account: int = 3) -> list[dict]:
    """
    Returns X posts formatted as article dicts for octo_engage.py's
    gather_articles() / generate_take() pipeline.
    """
    posts = get_x_posts(max_per_account)
    articles = []
    for p in posts:
        articles.append({
            "ticker":      "",
            "title":       p["text"],
            "description": f"Posted by @{p['handle']} ({p['label']})",
            "url":         "",
            "published":   "",
            "source":      "x_feed",
            "handle":      p["handle"],
        })
    return articles

"""
octo_xstats_fetch.py
Fetches @octodamusai follower + post counts.
Uses direct HTTP scrape of X profile — no API key, no twscrape, no login.
Writes to xstats.json in the octodamus directory.
Run every 30 minutes via Task Scheduler.
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

OUT_FILE = Path('C:/Users/walli/octodamus/xstats.json')
USERNAME = "octodamusai"


def scrape_x_profile(username: str) -> dict:
    """
    Fetch follower/post counts from X public profile page.
    No login, no API key required.
    """
    url = f"https://x.com/{username}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }

    r = httpx.get(url, headers=headers, timeout=15, follow_redirects=True)
    r.raise_for_status()
    html = r.text

    followers = None
    posts = None

    # Try to extract from meta tags or JSON data in page
    # X embeds stats in various places — try multiple patterns

    # Pattern 1: followers count in meta description
    m = re.search(r'(\d[\d,]*)\s+Followers', html)
    if m:
        followers = int(m.group(1).replace(",", ""))

    # Pattern 2: posts/tweets count
    m = re.search(r'(\d[\d,]*)\s+(?:Posts|Tweets)', html)
    if m:
        posts = int(m.group(1).replace(",", ""))

    # Pattern 3: JSON-LD or __NEXT_DATA__
    if followers is None:
        m = re.search(r'"followers_count":(\d+)', html)
        if m:
            followers = int(m.group(1))

    if posts is None:
        m = re.search(r'"statuses_count":(\d+)', html)
        if m:
            posts = int(m.group(1))

    return {"followers": followers, "posts": posts}


def load_existing() -> dict:
    try:
        if OUT_FILE.exists():
            return json.loads(OUT_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def write_stats(data: dict):
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"✓ Written to {OUT_FILE}")


def main():
    print(f"[xstats] Fetching @{USERNAME} profile...")

    existing = load_existing()

    try:
        stats = scrape_x_profile(USERNAME)
        followers = stats.get("followers")
        posts = stats.get("posts")

        # If scrape failed, keep existing values
        if followers is None:
            followers = existing.get("followers")
            print("[xstats] Followers not found in page — keeping existing value")
        if posts is None:
            posts = existing.get("posts")
            print("[xstats] Posts not found in page — keeping existing value")

        data = {
            "followers":   followers,
            "posts":       posts,
            "following":   existing.get("following"),
            "username":    USERNAME,
            "guide_sales": existing.get("guide_sales", 0),
            "updated_at":  datetime.now(timezone.utc).isoformat(),
            "source":      "scrape",
        }

        write_stats(data)
        print(f"✓ @{USERNAME}: {followers} followers / {posts} posts")

    except Exception as e:
        print(f"[xstats] Scrape failed: {e}")
        # Write error record but keep existing data
        existing["fetch_error"] = str(e)
        existing["error_at"] = datetime.now(timezone.utc).isoformat()
        write_stats(existing)
        sys.exit(1)


if __name__ == "__main__":
    main()

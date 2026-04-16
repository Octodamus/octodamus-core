"""
octo_music_archive.py
Octodamus Music Archive -- Aadam Jacobs Collection

Source: archive.org/details/aadamjacobs
2,492 live recordings, Chicago indie/alt underground, 1992-2019.
Venues: Empty Bottle, Metro, Hideout, Lounge Ax, basement shows.

Octodamus reads metadata + setlists (no audio processing).
Claude analyzes catalog to form character-aligned favorites.
Artist images fetched via Wikipedia API (no key required).

Storage:
  data/music_catalog.json         -- full catalog cache (refresh monthly)
  data/octo_music_favorites.json  -- curated picks + image URLs

CLI:
  python octo_music_archive.py --fetch    # refresh catalog from archive.org
  python octo_music_archive.py --build    # rebuild favorites via Claude
  python octo_music_archive.py --show     # print current favorites
  python octo_music_archive.py --images   # retry missing artist images
  python octo_music_archive.py --context  # preview a soul post context
"""

import json
import os
import random
import re
import time
from datetime import datetime
from pathlib import Path

DATA_DIR     = Path(__file__).parent / "data"
CATALOG_FILE = DATA_DIR / "music_catalog.json"
FAVORITES_FILE = DATA_DIR / "octo_music_favorites.json"

COLLECTION_ID   = "aadamjacobs"
CATALOG_TTL_DAYS = 30
ARCHIVE_API     = "https://archive.org/advancedsearch.php"
WIKIPEDIA_API   = "https://en.wikipedia.org/w/api.php"


# ─────────────────────────────────────────────
# CATALOG FETCH
# ─────────────────────────────────────────────

def fetch_catalog(force_refresh: bool = False) -> list:
    """
    Fetch all recordings from the Aadam Jacobs Collection.
    Caches to data/music_catalog.json, refreshes monthly.
    """
    if not force_refresh and CATALOG_FILE.exists():
        try:
            cached = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
            saved_at = datetime.fromisoformat(cached.get("saved_at", "2000-01-01"))
            age_days = (datetime.now() - saved_at).days
            if age_days < CATALOG_TTL_DAYS:
                items = cached["items"]
                print(f"[MusicArchive] Using cached catalog ({len(items)} recordings, {age_days}d old)")
                return items
        except Exception:
            pass

    import requests
    print("[MusicArchive] Fetching catalog from archive.org...")

    items = []
    page  = 1
    rows  = 500

    while True:
        try:
            r = requests.get(
                ARCHIVE_API,
                params={
                    "q":      f"collection:{COLLECTION_ID}",
                    "fl[]":   ["identifier", "title", "creator", "date", "description", "subject", "avg_rating", "num_reviews"],
                    "rows":   rows,
                    "page":   page,
                    "output": "json",
                    "sort[]": "date asc",
                },
                timeout=30,
            )
            data  = r.json()
            batch = data.get("response", {}).get("docs", [])
            if not batch:
                break
            items.extend(batch)
            total = data.get("response", {}).get("numFound", 0)
            print(f"[MusicArchive]   {len(items)}/{total} recordings fetched...")
            if len(items) >= total:
                break
            page += 1
            time.sleep(0.5)
        except Exception as e:
            print(f"[MusicArchive] Fetch error (page {page}): {e}")
            break

    DATA_DIR.mkdir(exist_ok=True)
    CATALOG_FILE.write_text(
        json.dumps({"saved_at": datetime.now().isoformat(), "items": items}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"[MusicArchive] Cached {len(items)} recordings to {CATALOG_FILE.name}")
    return items


# ─────────────────────────────────────────────
# SETLIST PARSING
# ─────────────────────────────────────────────

def parse_setlist(description: str) -> list:
    """Extract song titles from archive.org description text."""
    if not description:
        return []

    lines      = description.replace("\r", "").split("\n")
    songs      = []
    in_setlist = False

    for line in lines:
        line = line.strip()
        if not line:
            continue

        low = line.lower()

        # Enter setlist section
        if any(m in low for m in ["setlist", "set list", "set 1", "set 2", "set one", "set two", "set:"]):
            in_setlist = True
            continue

        # Exit setlist section
        if any(m in low for m in ["source:", "lineage:", "transfer:", "taped by", "recorded by",
                                    "equipment:", "notes:", "bitrate:", "flac", "shn", "mp3"]):
            in_setlist = False

        if in_setlist and 3 < len(line) < 100:
            song = re.sub(r'^[\d\s.\-\)\>]+', '', line).strip()
            if song and not song.startswith("[") and "//" not in song:
                songs.append(song)

    # Fallback: numbered lines anywhere in description
    if not songs:
        for line in lines:
            line = line.strip()
            m = re.match(r'^(\d+)[.)]\s+(.+)', line)
            if m and len(m.group(2)) < 80:
                songs.append(m.group(2).strip())

    return songs[:25]


# ─────────────────────────────────────────────
# ARTIST IMAGES
# ─────────────────────────────────────────────

_HEADERS = {"User-Agent": "Octodamus/1.0 (https://octodamus.com; contact@octodamus.com) python-requests"}


def get_artist_image(artist_name: str) -> str | None:
    """
    Try multiple sources for an artist image URL.
    Sources (in order): Wikipedia, MusicBrainz, Last.fm (if key set).
    Returns image URL or None.
    """
    import requests

    # 1. Wikipedia pageimages — try plain name then disambiguated variants
    for title in [artist_name, f"{artist_name} (band)", f"{artist_name} (musician)"]:
        try:
            r = requests.get(
                WIKIPEDIA_API,
                params={
                    "action":      "query",
                    "titles":      title,
                    "prop":        "pageimages|categories",
                    "pithumbsize": 600,
                    "format":      "json",
                    "redirects":   1,
                    "cllimit":     5,
                },
                headers=_HEADERS,
                timeout=10,
            )
            if r.status_code == 200:
                pages = r.json().get("query", {}).get("pages", {})
                for page_id, page in pages.items():
                    if page_id == "-1":
                        continue
                    # Reject if categories suggest this is the wrong type of page
                    cats = [c.get("title", "").lower() for c in page.get("categories", [])]
                    is_animal = any("animal" in c or "reptile" in c or "plant" in c
                                    or "geography" in c or "weather" in c for c in cats)
                    if is_animal:
                        continue
                    url = page.get("thumbnail", {}).get("source", "")
                    if url:
                        return url
        except Exception:
            pass

    # 2. MusicBrainz artist lookup -> artist image via CAA
    try:
        r = requests.get(
            "https://musicbrainz.org/ws/2/artist/",
            params={"query": f'artist:"{artist_name}"', "fmt": "json", "limit": 1},
            headers=_HEADERS,
            timeout=10,
        )
        if r.status_code == 200:
            artists = r.json().get("artists", [])
            if artists:
                mbid = artists[0].get("id", "")
                if mbid:
                    # Try artist image from CAA via Wikidata/Wikipedia link
                    rel_r = requests.get(
                        f"https://musicbrainz.org/ws/2/artist/{mbid}",
                        params={"inc": "url-rels", "fmt": "json"},
                        headers=_HEADERS,
                        timeout=10,
                    )
                    if rel_r.status_code == 200:
                        relations = rel_r.json().get("relations", [])
                        for rel in relations:
                            if rel.get("type") == "image":
                                img_url = rel.get("url", {}).get("resource", "")
                                if img_url:
                                    return img_url
        time.sleep(1.1)  # MusicBrainz rate limit: 1 req/sec
    except Exception:
        pass

    # 3. Last.fm (optional — requires LASTFM_API_KEY in secrets)
    lfm_key = os.environ.get("LASTFM_API_KEY", "")
    if lfm_key:
        try:
            r = requests.get(
                "https://ws.audioscrobbler.com/2.0/",
                params={
                    "method":  "artist.getinfo",
                    "artist":  artist_name,
                    "api_key": lfm_key,
                    "format":  "json",
                },
                headers=_HEADERS,
                timeout=10,
            )
            images = r.json().get("artist", {}).get("image", [])
            PLACEHOLDER = "2a96cbd8b46e442fc41c2b86b821562f"
            for img in reversed(images):
                url = img.get("#text", "")
                if url and PLACEHOLDER not in url:
                    return url
        except Exception:
            pass

    return None


# ─────────────────────────────────────────────
# BUILD FAVORITES
# ─────────────────────────────────────────────

def build_favorites(force: bool = False) -> list:
    """
    Use Claude to analyze catalog and build Octodamus's curated favorites.
    Writes to data/octo_music_favorites.json.
    """
    if not force and FAVORITES_FILE.exists():
        try:
            data = json.loads(FAVORITES_FILE.read_text(encoding="utf-8"))
            favs = data.get("favorites", [])
            if favs:
                print(f"[MusicArchive] Favorites already built ({len(favs)} picks). Use --build to rebuild.")
                return favs
        except Exception:
            pass

    catalog = fetch_catalog()
    if not catalog:
        print("[MusicArchive] No catalog data.")
        return []

    import anthropic
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

    # Group by artist
    artists: dict = {}
    for item in catalog:
        creator = str(item.get("creator", "Unknown")).strip()
        if not creator or creator == "Unknown":
            continue
        artists.setdefault(creator, []).append(item)

    # Build compact per-artist summary with setlists
    artist_summaries = []
    for artist, shows in sorted(artists.items()):
        sample = shows[:3]
        show_data = []
        for s in sample:
            setlist = parse_setlist(str(s.get("description", "")))
            show_data.append({
                "identifier": s.get("identifier", ""),
                "date":       str(s.get("date", ""))[:10],
                "title":      str(s.get("title", ""))[:80],
                "setlist":    setlist[:8],
            })
        artist_summaries.append({
            "artist":      artist,
            "show_count":  len(shows),
            "date_range":  f"{str(shows[0].get('date',''))[:10]} to {str(shows[-1].get('date',''))[:10]}",
            "sample_shows": show_data,
        })

    print(f"[MusicArchive] Analyzing {len(artist_summaries)} artists with Claude...")

    SYSTEM = """You are Octodamus — oracle octopus, market seer, genuine music obsessive.
Known musical identity: Tool is your all-time band. Lateralus. Fibonacci in the time signatures.
Maynard sounds like a creature who has seen the bottom and decided to stay.
Beyond Tool, you are drawn to: music that rewards patience, complexity inside apparent simplicity,
live improvisation creating unrepeatable moments, artists who operate entirely outside commercial systems,
rawness over polish, Chicago's underground specifically — the Empty Bottle crowd, late nights, small rooms."""

    all_favorites = []
    batch_size    = 60

    for i in range(0, len(artist_summaries), batch_size):
        batch     = artist_summaries[i:i + batch_size]
        batch_num = i // batch_size + 1
        total_b   = (len(artist_summaries) + batch_size - 1) // batch_size

        prompt = f"""Study this catalog of {len(batch)} artists from the Aadam Jacobs Collection
(archive.org live recordings, Chicago underground 1992-2019).

Pick your 6-10 GENUINE FAVORITES from this batch -- artists you would actually return to.
Think: Tool-adjacent in spirit (not necessarily sound). Complexity. Staying power. Something earned.
Obscure is fine. Preferably artists with multiple documented shows and real setlists.

For each favorite, return one JSON object in this exact format:
{{
  "artist": "exact artist name from catalog",
  "best_show": {{
    "identifier": "archive.org identifier string",
    "date": "YYYY-MM-DD",
    "venue": "venue name extracted from title (Empty Bottle, Metro, Hideout, etc.)",
    "title": "full title string"
  }},
  "songs": ["Song Title 1", "Song Title 2", "Song Title 3"],
  "note": "1-2 sentences in Octodamus voice. Why this resonates. No hashtags, no emojis, no forced ocean metaphors.",
  "mood_tags": ["tag1", "tag2"]
}}

Return a JSON array only. No prose before or after.

Catalog batch {batch_num}/{total_b}:
{json.dumps(batch, indent=2)}"""

        try:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=4000,
                system=SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()
            match = re.search(r'\[.*\]', text, re.DOTALL)
            if match:
                batch_favs = json.loads(match.group())
                all_favorites.extend(batch_favs)
                print(f"[MusicArchive] Batch {batch_num}/{total_b}: {len(batch_favs)} favorites")
            else:
                print(f"[MusicArchive] Batch {batch_num}: no JSON found in response")
        except Exception as e:
            print(f"[MusicArchive] Batch {batch_num} failed: {e}")

        time.sleep(1)

    # Fetch images
    print(f"[MusicArchive] Fetching artist images for {len(all_favorites)} favorites...")
    for fav in all_favorites:
        artist = fav.get("artist", "")
        url    = get_artist_image(artist)
        fav["image_url"] = url
        status = "found" if url else "not found"
        print(f"[MusicArchive]   {artist}: {status}")
        time.sleep(0.3)

    DATA_DIR.mkdir(exist_ok=True)
    output = {
        "built_at":      datetime.now().isoformat(),
        "collection":    COLLECTION_ID,
        "total_catalog": len(catalog),
        "favorites":     all_favorites,
    }
    FAVORITES_FILE.write_text(
        json.dumps(output, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"[MusicArchive] Saved {len(all_favorites)} favorites to {FAVORITES_FILE.name}")
    return all_favorites


# ─────────────────────────────────────────────
# SOUL POST CONTEXT
# ─────────────────────────────────────────────

def get_soul_context(n: int = 1) -> list:
    """
    Return n random favorites for Soul post injection.
    Each dict has: artist, best_show, songs, note, image_url, mood_tags.
    """
    if not FAVORITES_FILE.exists():
        return []
    try:
        data = json.loads(FAVORITES_FILE.read_text(encoding="utf-8"))
        favs = data.get("favorites", [])
        if not favs:
            return []
        return random.sample(favs, min(n, len(favs)))
    except Exception:
        return []


def get_favorite_for_post() -> dict | None:
    """
    Return a single favorite prioritizing ones that have an image URL.
    Used by mode_soul to attach a band photo to the X post.
    """
    if not FAVORITES_FILE.exists():
        return None
    try:
        data = json.loads(FAVORITES_FILE.read_text(encoding="utf-8"))
        favs = data.get("favorites", [])
        if not favs:
            return None
        with_images = [f for f in favs if f.get("image_url")]
        pool = with_images if with_images else favs
        return random.choice(pool)
    except Exception:
        return None


def retry_missing_images() -> int:
    """Re-attempt image fetch for favorites that have no image_url."""
    if not FAVORITES_FILE.exists():
        return 0
    data = json.loads(FAVORITES_FILE.read_text(encoding="utf-8"))
    favs = data.get("favorites", [])
    updated = 0
    for fav in favs:
        if not fav.get("image_url"):
            url = get_artist_image(fav["artist"])
            if url:
                fav["image_url"] = url
                updated += 1
                print(f"[MusicArchive] Found image: {fav['artist']}")
            time.sleep(0.4)
    data["favorites"] = favs
    FAVORITES_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return updated


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from bitwarden import load_all_secrets
    load_all_secrets()

    parser = argparse.ArgumentParser(description="Octodamus Music Archive")
    parser.add_argument("--fetch",   action="store_true", help="Refresh catalog from archive.org")
    parser.add_argument("--build",   action="store_true", help="Rebuild favorites via Claude")
    parser.add_argument("--show",    action="store_true", help="Print current favorites")
    parser.add_argument("--images",  action="store_true", help="Retry missing artist images")
    parser.add_argument("--context", action="store_true", help="Preview a soul post context sample")
    args = parser.parse_args()

    if args.fetch:
        items = fetch_catalog(force_refresh=True)
        artists = set(str(i.get("creator", "")) for i in items)
        print(f"Catalog: {len(items)} recordings, {len(artists)} unique artists")

    if args.build:
        favs = build_favorites(force=True)
        print(f"\nBuilt {len(favs)} favorites:")
        for f in favs:
            img   = "IMG" if f.get("image_url") else "---"
            show  = f.get("best_show", {})
            songs = ", ".join(f.get("songs", [])[:3])
            print(f"  [{img}] {f['artist']}")
            print(f"       {show.get('date','')} @ {show.get('venue','')}")
            print(f"       Songs: {songs}")
            print(f"       {f.get('note','')[:90]}")

    if args.show:
        if not FAVORITES_FILE.exists():
            print("No favorites yet. Run --build first.")
        else:
            data = json.loads(FAVORITES_FILE.read_text(encoding="utf-8"))
            favs = data.get("favorites", [])
            with_img = sum(1 for f in favs if f.get("image_url"))
            print(f"Favorites: {len(favs)} | With images: {with_img}")
            print(f"Built: {data.get('built_at','')[:16]}\n")
            for f in favs:
                img  = "IMG" if f.get("image_url") else "---"
                show = f.get("best_show", {})
                print(f"[{img}] {f['artist']} -- {show.get('date','')} @ {show.get('venue','')}")
                print(f"      {f.get('note','')[:90]}")

    if args.images:
        n = retry_missing_images()
        print(f"Updated {n} image URL(s)")

    if args.context:
        ctx = get_soul_context(n=1)
        if ctx:
            import pprint
            pprint.pprint(ctx[0])
        else:
            print("No favorites built yet. Run --build first.")

    if not any([args.fetch, args.build, args.show, args.images, args.context]):
        items = fetch_catalog()
        artists = set(str(i.get("creator", "")) for i in items)
        print(f"Catalog: {len(items)} recordings | {len(artists)} artists")
        if FAVORITES_FILE.exists():
            data = json.loads(FAVORITES_FILE.read_text(encoding="utf-8"))
            print(f"Favorites: {len(data.get('favorites', []))} picks")
        else:
            print("Favorites: not built yet (run --build)")

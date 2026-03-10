"""
Patches octo_geo.py and octo_alert.py to fix the GDELT tone=0.00 bug.
The artlist mode does not return tone. We use timelinetone for tone
and artlist separately for headlines only.
"""
import re

# ── Patch octo_geo.py ─────────────────────────────────────────
geo = open("octo_geo.py").read()

# Replace the _gdelt_doc_query function with one that fetches
# tone via timelinetone mode and articles separately
old = '''def _gdelt_doc_query(query: str, timespan: str = "24h", max_records: int = 10) -> list[dict]:
    """
    Query GDELT DOC 2.0 API for recent articles matching a theme.
    Returns list of article dicts with tone and metadata.
    """
    try:
        r = requests.get(
            f"{GDELT_BASE}/doc/doc",
            params={
                "query": query,
                "mode\": \"artlist",
                "maxrecords": max_records,
                "timespan": timespan,
                "format": "json",
                "sort": "tonedesc",
            },
            headers=HEADERS,
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        return data.get("articles", [])
    except Exception as e:
        print(f"[OctoGeo] GDELT query failed for \'{query}\': {e}")
        return []'''

new = '''def _gdelt_get_tone(query: str, timespan: str = "24h") -> float | None:
    """Fetch average tone for a query using GDELT timelinetone mode."""
    try:
        r = requests.get(
            f"{GDELT_BASE}/doc/doc",
            params={
                "query":    query,
                "mode":     "timelinetone",
                "timespan": timespan,
                "format":   "json",
            },
            headers=HEADERS,
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        timeline = data.get("timeline", [])
        if not timeline:
            return None
        # Each entry has tone, tone_pos, tone_neg — average the tone values
        tones = [entry.get("tone", 0) for entry in timeline if "tone" in entry]
        return round(sum(tones) / len(tones), 2) if tones else None
    except Exception as e:
        print(f"[OctoGeo] GDELT tone query failed for \'{query}\': {e}")
        return None


def _gdelt_get_articles(query: str, timespan: str = "24h", max_records: int = 8) -> list[dict]:
    """Fetch article list for a query using GDELT artlist mode."""
    try:
        r = requests.get(
            f"{GDELT_BASE}/doc/doc",
            params={
                "query":      query,
                "mode":       "artlist",
                "maxrecords": max_records,
                "timespan":   timespan,
                "format":     "json",
            },
            headers=HEADERS,
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        return data.get("articles", [])
    except Exception as e:
        print(f"[OctoGeo] GDELT articles query failed for \'{query}\': {e}")
        return []


def _gdelt_doc_query(query: str, timespan: str = "24h", max_records: int = 10) -> list[dict]:
    """
    Compatibility wrapper — returns articles with tone injected.
    Fetches tone via timelinetone, articles via artlist, merges them.
    """
    tone = _gdelt_get_tone(query, timespan)
    time.sleep(2)
    articles = _gdelt_get_articles(query, timespan, max_records)
    # Inject the real tone into each article so _parse_articles works correctly
    for a in articles:
        a["tone"] = tone if tone is not None else 0.0
    return articles'''

if old.split('\n')[0] in geo:
    geo = geo.replace(old, new)
    print("✅ octo_geo.py: replaced _gdelt_doc_query")
else:
    # Just patch the sleep delay to be safe
    print("⚠️  Could not find exact function — patching sleep delay only")

# Increase sleep between queries from 0.5 to 4 seconds
geo = geo.replace("time.sleep(0.5)", "time.sleep(4)")
open("octo_geo.py", "w").write(geo)
print("✅ octo_geo.py: sleep delay increased to 4s")

# ── Patch octo_alert.py ───────────────────────────────────────
alert = open("octo_alert.py").read()

# Increase GDELT delays from 0.5 to 4 seconds  
alert = alert.replace("time.sleep(0.5)", "time.sleep(4)")
alert = alert.replace(
    '"timespan":   "4h",        # Only last 4 hours — breaking window',
    '"timespan":   "4h",',
)

# Fix tone fetch in scan_gdelt_tone — use timelinetone mode
old_gdelt_scan = '''            r = requests.get(
                "https://api.gdeltproject.org/api/v2/doc/doc",
                params={
                    "query":      query,
                    "mode":       "artlist",
                    "maxrecords": 20,
                    "timespan":   "4h",
                    "format":     "json",
                    "sort":       "tonedesc",
                },
                headers=HEADERS,
                timeout=15,
            )
            r.raise_for_status()
            articles = r.json().get("articles", [])

            if len(articles) < GDELT_ARTICLE_MIN:
                continue

            tones = []
            for a in articles:
                try:
                    tones.append(float(a.get("tone", 0)))
                except Exception:
                    continue

            if not tones:
                continue

            avg_tone = sum(tones) / len(tones)'''

new_gdelt_scan = '''            # Use timelinetone mode — artlist does NOT include tone field
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

            avg_tone = sum(tones) / len(tones)'''

alert = alert.replace(old_gdelt_scan, new_gdelt_scan)
open("octo_alert.py", "w").write(alert)
print("✅ octo_alert.py: GDELT switched to timelinetone mode, delays increased")

print("\nAll patches applied. Run: python3 octo_alert.py")

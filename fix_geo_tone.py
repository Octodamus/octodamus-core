"""
fix_geo_tone.py
Patches octo_geo.py to fix the tone=0.00 bug.
Run from: /mnt/c/Users/walli/octodamus/
"""

content = open("octo_geo.py").read()

# Find the function boundaries precisely
start_marker = "def _gdelt_query("
end_marker = "\ndef _parse("

if start_marker not in content:
    print("ERROR: Cannot find _gdelt_query in octo_geo.py")
    print("First 50 chars of file:", content[:200])
    exit(1)

start = content.index(start_marker)
end = content.index(end_marker, start)

# Build the replacement
replacement = '''def _gdelt_get_tone(query: str, timespan: str = "24h") -> float | None:
    """Fetch real tone via timelinetone — artlist mode has NO tone field."""
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
        if r.status_code == 429:
            print(f"[OctoGeo] 429 on tone — skipping for '{query}'")
            return None
        r.raise_for_status()
        tones = [
            e.get("tone", 0)
            for e in r.json().get("timeline", [])
            if "tone" in e
        ]
        return round(sum(tones) / len(tones), 2) if tones else None
    except Exception as e:
        print(f"[OctoGeo] Tone query failed for '{query}': {e}")
        return None


def _gdelt_query(query: str, timespan: str = "24h", max_records: int = 6) -> list:
    """Fetch articles via artlist + inject real tone from timelinetone."""
    tone = _gdelt_get_tone(query, timespan)
    time.sleep(4)
    for attempt in range(_MAX_RETRIES):
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
            if r.status_code == 429:
                wait = _RETRY_DELAY * (attempt + 1)
                print(f"[OctoGeo] 429 rate limit — waiting {wait}s...")
                time.sleep(wait)
                continue
            r.raise_for_status()
            articles = r.json().get("articles", [])
            for a in articles:
                a["tone"] = tone if tone is not None else 0.0
            return articles
        except requests.exceptions.HTTPError:
            if attempt < _MAX_RETRIES - 1:
                time.sleep(_RETRY_DELAY)
            continue
        except Exception as e:
            print(f"[OctoGeo] GDELT failed for '{query}': {e}")
            return []
    print(f"[OctoGeo] Giving up on '{query}' after {_MAX_RETRIES} attempts")
    return []
'''

# Splice it in
new_content = content[:start] + replacement + content[end:]
open("octo_geo.py", "w").write(new_content)
print("✅ octo_geo.py patched — timelinetone tone fix applied")
print(f"   Replaced lines {content[:start].count(chr(10))}–{content[:end].count(chr(10))}")

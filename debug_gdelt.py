"""
debug_gdelt.py
Run this on the mini PC to inspect the raw GDELT API response.
Tells us exactly what field names GDELT actually returns.

Usage:
    python3 debug_gdelt.py
"""

import requests
import json

HEADERS = {"User-Agent": "octodamus-oracle/1.0 (@octodamusai)"}

print("🔍 Fetching raw GDELT response...")

try:
    r = requests.get(
        "https://api.gdeltproject.org/api/v2/doc/doc",
        params={
            "query":      "Federal Reserve interest rates",
            "mode":       "artlist",
            "maxrecords": 3,
            "timespan":   "24h",
            "format":     "json",
            "sort":       "tonedesc",
        },
        headers=HEADERS,
        timeout=15,
    )
    print(f"Status: {r.status_code}")
    data = r.json()

    # Print top-level keys
    print(f"\nTop-level keys: {list(data.keys())}")

    articles = data.get("articles", [])
    print(f"Article count: {len(articles)}")

    if articles:
        print("\n── First article — ALL fields ──────────────")
        for k, v in articles[0].items():
            print(f"  {k:20s} = {str(v)[:80]}")

        if len(articles) > 1:
            print("\n── Second article — ALL fields ─────────────")
            for k, v in articles[1].items():
                print(f"  {k:20s} = {str(v)[:80]}")
    else:
        print("\nNo articles returned — printing raw response:")
        print(json.dumps(data, indent=2)[:2000])

except Exception as e:
    print(f"Error: {e}")

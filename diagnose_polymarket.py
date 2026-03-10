"""
diagnose_polymarket.py
Dumps raw Polymarket API response so we can see exact field structure.
Run: C:\Python314\python.exe diagnose_polymarket.py
"""

import requests, json

GAMMA_BASE = "https://gamma-api.polymarket.com"
HEADERS = {"User-Agent": "octodamus-oracle/1.0 (@octodamusai)", "Accept": "application/json"}

print("── Fetching raw Polymarket response ────────────────────")
r = requests.get(
    f"{GAMMA_BASE}/markets",
    params={"q": "Federal Reserve", "active": "true", "closed": "false", "limit": 2},
    headers=HEADERS,
    timeout=12,
)
print(f"Status: {r.status_code}")
data = r.json()

if isinstance(data, list):
    markets = data
else:
    markets = data.get("markets", data.get("data", []))

print(f"Type: {type(data).__name__} | Markets found: {len(markets)}")
print()

for i, m in enumerate(markets[:2]):
    print(f"── Market {i+1} ──────────────────────────────────────────")
    # Print all top-level keys and their values (truncated)
    for k, v in m.items():
        val_str = str(v)
        if len(val_str) > 120:
            val_str = val_str[:120] + "..."
        print(f"  {k:30s} = {val_str}")
    print()

# Also try without the tag_slug filter to compare
print("── Without tag filter ──────────────────────────────────")
r2 = requests.get(
    f"{GAMMA_BASE}/markets",
    params={"q": "Bitcoin price", "active": "true", "closed": "false", "limit": 2},
    headers=HEADERS,
    timeout=12,
)
data2 = r2.json()
markets2 = data2 if isinstance(data2, list) else data2.get("markets", [])
print(f"Markets: {len(markets2)}")
if markets2:
    m = markets2[0]
    print(f"Question: {m.get('question') or m.get('title')}")
    # Show price-related fields specifically
    price_keys = [k for k in m.keys() if any(w in k.lower() for w in
                  ["price", "prob", "outcome", "token", "yes", "no", "odd", "bid", "ask", "last"])]
    print(f"Price-related fields: {price_keys}")
    for k in price_keys:
        print(f"  {k}: {m[k]}")

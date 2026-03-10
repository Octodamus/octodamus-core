"""
fix_predict_filter.py
Diagnoses what the sports filter is blocking, then tightens it.
Run: C:\Python314\python.exe fix_predict_filter.py
"""
import json, requests, shutil, subprocess

GAMMA_BASE = "https://gamma-api.polymarket.com"
HEADERS = {"User-Agent": "octodamus-oracle/1.0", "Accept": "application/json"}

# Show exactly what's being fetched and what's blocked
print("── Live diagnostic ─────────────────────────────────")
r = requests.get(f"{GAMMA_BASE}/markets",
    params={"q": "ceasefire", "active": "true", "closed": "false", "limit": 5,
            "order": "volume24hr", "ascending": "false"},
    headers=HEADERS, timeout=12)
markets = r.json() if isinstance(r.json(), list) else r.json().get("markets", [])
for m in markets:
    q = m.get("question", "")
    raw = m.get("outcomePrices")
    try:
        prices = json.loads(raw) if isinstance(raw, str) else raw
        yes = round(float(prices[0]) * 100, 1) if prices else None
    except Exception:
        yes = None
    print(f"  Q: {q[:70]}")
    print(f"     outcomePrices raw type={type(raw).__name__} val={str(raw)[:50]}")
    print(f"     yes_prob={yes}")
    print()

# Now write the fixed file — minimal sports filter only
PATH = "octo_predict.py"
shutil.copy2(PATH, PATH + ".bak_filter")

with open(PATH, "r", encoding="utf-8") as f:
    content = f.read()

OLD_SKIP = """SPORTS_SKIP = [
    "premier league","la liga","bundesliga","serie a","ligue 1",
    "champions league","nba","nfl","mlb","nhl","nascar",
    "world cup","olympic","super bowl","superbowl",
    "win the match","win on 2","win the 2025","win the 2026",
    " fc ","atletico","chelsea","arsenal","barcelona","liverpool",
    "real madrid","manchester","gta vi","gta6","bitboy",
    "formula 1","f1 ","tennis","golf ","ufc ","boxing",
    "oscar","grammy","emmy","award","album",
]"""

NEW_SKIP = """SPORTS_SKIP = [
    "premier league","la liga","bundesliga","serie a","ligue 1",
    "nba finals","nfl super bowl","mlb world series","nhl stanley",
    "world cup winner","olympic gold",
    "will score","goals in","win the match","beats ",
    "grammy","oscar winner","emmy award",
]"""

if OLD_SKIP in content:
    content = content.replace(OLD_SKIP, NEW_SKIP)
    print("P1 OK: sports filter tightened")
else:
    print("P1 SKIP: anchor not found — printing current SPORTS_SKIP for inspection:")
    for line in content.splitlines():
        if "SKIP" in line or "skip" in line:
            print(f"  {line}")

with open(PATH, "w", encoding="utf-8") as f:
    f.write(content)

r2 = subprocess.run([r"C:\Python314\python.exe", "-m", "py_compile", PATH],
                    capture_output=True, text=True)
if r2.returncode == 0:
    print("✓ octo_predict.py syntax OK")
else:
    print(f"✗ {r2.stderr}")
    shutil.copy2(PATH + ".bak_filter", PATH)

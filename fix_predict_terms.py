"""
fix_predict_terms.py

Fixes OctoPredict search terms to target financial/macro markets
instead of sports. Polymarket's keyword search is broad — we use
their tag/category filter and more specific financial phrases.

Run: C:\Python314\python.exe fix_predict_terms.py
"""

import shutil, os, subprocess

PATH = "octo_predict.py"

OLD_TERMS = '''# Search terms — kept short for best match
SEARCH_TERMS = [
    "Fed rate",
    "Bitcoin",
    "recession",
    "inflation",
    "election",
    "oil",
]

MARKETS_PER_QUERY = 4'''

NEW_TERMS = '''# Financial-specific search terms
# Polymarket search is broad — longer phrases filter out sports
SEARCH_TERMS = [
    "Federal Reserve interest rate cut",
    "Bitcoin price end of year",
    "US recession 2025",
    "CPI inflation rate",
    "S&P 500",
    "US presidential",
    "Iran nuclear",
    "Russia Ukraine ceasefire",
    "crude oil price",
    "will the US enter",
]

MARKETS_PER_QUERY = 5'''

# Also patch _fetch_markets to add category filter for financial markets
OLD_FETCH = '''        r = requests.get(
            f"{GAMMA_BASE}/markets",
            params={
                "q":          query,
                "active":     "true",
                "closed":     "false",
                "limit":      limit,
                "order":      "volume24hr",
                "ascending":  "false",
            },
            headers=HEADERS,
            timeout=12,
        )'''

NEW_FETCH = '''        r = requests.get(
            f"{GAMMA_BASE}/markets",
            params={
                "q":          query,
                "active":     "true",
                "closed":     "false",
                "limit":      limit,
                "order":      "volume24hr",
                "ascending":  "false",
                "tag_slug":   "economics,crypto,politics,finance",
            },
            headers=HEADERS,
            timeout=12,
        )'''

# Also tighten the sports filter — skip obvious sports results
OLD_PARSE_START = '''    try:
        question = (m.get("question") or m.get("title") or "").strip()
        if not question:
            return None'''

NEW_PARSE_START = '''    # Skip sports markets
    SPORTS_SKIP = [
        "premier league", "la liga", "bundesliga", "serie a", "ligue 1",
        "champions league", "nba", "nfl", "mlb", "nhl", "nascar",
        "world cup", "olympic", "superbowl", "super bowl",
        "win the match", "win on 2", "beat ", "score", "goal",
        "tournament", "championship game", "finals game",
    ]
    q_lower = (m.get("question") or m.get("title") or "").lower()
    if any(s in q_lower for s in SPORTS_SKIP):
        return None

    try:
        question = (m.get("question") or m.get("title") or "").strip()
        if not question:
            return None'''


def patch():
    if not os.path.exists(PATH):
        print(f"ERROR: {PATH} not found"); return False

    with open(PATH, "r", encoding="utf-8") as f:
        content = f.read()

    shutil.copy2(PATH, PATH + ".bak_terms")
    fixes = 0

    if OLD_TERMS in content:
        content = content.replace(OLD_TERMS, NEW_TERMS)
        print("P1 OK: search terms updated")
        fixes += 1
    else:
        print("P1 SKIP: search terms anchor not found")

    if OLD_FETCH in content:
        content = content.replace(OLD_FETCH, NEW_FETCH)
        print("P2 OK: tag filter added to API call")
        fixes += 1
    else:
        print("P2 SKIP: fetch anchor not found")

    if OLD_PARSE_START in content:
        content = content.replace(OLD_PARSE_START, NEW_PARSE_START)
        print("P3 OK: sports filter added to parser")
        fixes += 1
    else:
        print("P3 SKIP: parse anchor not found")

    with open(PATH, "w", encoding="utf-8") as f:
        f.write(content)

    r = subprocess.run(
        [r"C:\Python314\python.exe", "-m", "py_compile", PATH],
        capture_output=True, text=True
    )
    if r.returncode == 0:
        print(f"✓ {PATH} syntax OK ({fixes} fixes applied)")
        return True
    else:
        print(f"✗ Syntax error:\n{r.stderr}")
        shutil.copy2(PATH + ".bak_terms", PATH)
        print("Restored backup.")
        return False


if __name__ == "__main__":
    patch()
    print("""
Test:
  C:\\Python314\\python.exe octodamus_runner.py --mode predict
""")

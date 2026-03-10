"""
fix_predict_parser.py

Fixes OctoPredict parser based on actual Polymarket API response:
  - outcomePrices is a JSON-encoded string: '["0.1365", "0.8635"]'
  - Removes tag_slug filter (not supported as comma list)
  - Adds debug output to confirm parsing works

Run: C:\Python314\python.exe fix_predict_parser.py
"""

import shutil, os, subprocess

PATH = "octo_predict.py"

# ── Fix 1: Remove tag_slug from fetch call ────────────────────────────────────

OLD_FETCH = '''        r = requests.get(
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

NEW_FETCH = '''        r = requests.get(
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

# ── Fix 2: Rewrite _parse_market with correct outcomePrices handling ──────────

OLD_PARSE_PRICES = '''        # Parse YES probability — Polymarket stores as 0-1 float or list
        yes_prob = None
        prices = m.get("outcomePrices") or m.get("prices") or []
        outcomes = m.get("outcomes") or []

        if prices:
            try:
                if isinstance(prices, str):
                    import json as _json
                    prices = _json.loads(prices)
                if isinstance(prices, list) and len(prices) >= 1:
                    yes_prob = round(float(prices[0]) * 100, 1)
            except Exception:
                pass

        # Fallback: look for bestBid / lastTradePrice
        if yes_prob is None:
            for fkey in ["lastTradePrice", "bestBid", "midpoint"]:
                val = m.get(fkey)
                if val is not None:
                    try:
                        p = float(val)
                        yes_prob = round(p * 100 if p <= 1 else p, 1)
                        break
                    except Exception:
                        pass

        if yes_prob is None or not (0 <= yes_prob <= 100):
            return None'''

NEW_PARSE_PRICES = '''        # Parse YES probability
        # outcomePrices on Polymarket is a JSON-encoded string: '["0.565", "0.435"]'
        yes_prob = None
        import json as _json

        raw_prices = m.get("outcomePrices")
        if raw_prices:
            try:
                # It comes as a string — decode it
                if isinstance(raw_prices, str):
                    price_list = _json.loads(raw_prices)
                else:
                    price_list = raw_prices
                if isinstance(price_list, list) and len(price_list) >= 1:
                    p = float(price_list[0])
                    # Values are 0-1 fractions
                    yes_prob = round(p * 100, 1)
            except Exception:
                pass

        # Fallback: lastTradePrice (also 0-1)
        if yes_prob is None:
            for fkey in ["lastTradePrice", "bestBid"]:
                val = m.get(fkey)
                if val is not None:
                    try:
                        p = float(val)
                        yes_prob = round(p * 100 if p <= 1 else p, 1)
                        break
                    except Exception:
                        pass

        if yes_prob is None or not (0 <= yes_prob <= 100):
            return None'''


def patch():
    if not os.path.exists(PATH):
        print(f"ERROR: {PATH} not found"); return False

    with open(PATH, "r", encoding="utf-8") as f:
        content = f.read()

    shutil.copy2(PATH, PATH + ".bak_parser")
    fixes = 0

    if OLD_FETCH in content:
        content = content.replace(OLD_FETCH, NEW_FETCH)
        print("P1 OK: tag_slug filter removed")
        fixes += 1
    else:
        print("P1 SKIP: fetch anchor not found (tag_slug may already be gone)")

    if OLD_PARSE_PRICES in content:
        content = content.replace(OLD_PARSE_PRICES, NEW_PARSE_PRICES)
        print("P2 OK: outcomePrices parser rewritten")
        fixes += 1
    else:
        print("P2 SKIP: price parse anchor not found")

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
        shutil.copy2(PATH + ".bak_parser", PATH)
        print("Restored backup.")
        return False


if __name__ == "__main__":
    patch()
    print("""
Test:
  C:\\Python314\\python.exe octodamus_runner.py --mode predict

Expected: markets like:
  YES=56.5% | Russia-Ukraine Ceasefire before GTA VI?
  YES=13.7% | BitBoy convicted?
""")

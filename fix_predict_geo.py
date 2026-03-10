"""
fix_predict_geo.py

Fixes:
  1. OctoPredict — Polymarket Gamma API endpoint + params updated
  2. OctoGeo — GDELT rate limit: longer delays, fewer queries, retry logic

Run: C:\Python314\python.exe fix_predict_geo.py
"""

import shutil, os

# ── FIX 1: octo_predict.py ────────────────────────────────────────────────────

PREDICT_PATH = "octo_predict.py"

NEW_PREDICT = '''"""
octo_predict.py
OctoPredict — Prediction Market Mind

Reads Polymarket via Gamma Markets API (free, no key).
Rate limit: ~10 req/min.

Usage:
    from octo_predict import run_prediction_scan, format_predict_for_prompt
    pred = run_prediction_scan()
"""

import time
import requests
from datetime import datetime

GAMMA_BASE = "https://gamma-api.polymarket.com"
HEADERS    = {"User-Agent": "octodamus-oracle/1.0 (@octodamusai)", "Accept": "application/json"}
_DELAY     = 1.5

# Search terms — kept short for best match
SEARCH_TERMS = [
    "Fed rate",
    "Bitcoin",
    "recession",
    "inflation",
    "election",
    "oil",
]

MARKETS_PER_QUERY = 4


def _fetch_markets(query: str, limit: int = MARKETS_PER_QUERY) -> list:
    """Fetch active markets from Polymarket Gamma API."""
    try:
        # Primary endpoint
        r = requests.get(
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
        )
        r.raise_for_status()
        data = r.json()
        # API returns list or dict with markets key
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("markets", data.get("data", []))
        return []
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            # Try alternate endpoint
            try:
                r2 = requests.get(
                    f"{GAMMA_BASE}/markets",
                    params={"q": query, "limit": limit},
                    headers=HEADERS,
                    timeout=12,
                )
                r2.raise_for_status()
                data = r2.json()
                return data if isinstance(data, list) else data.get("markets", [])
            except Exception:
                return []
        print(f"[OctoPredict] HTTP error for \\'{query}\\': {e}")
        return []
    except Exception as e:
        print(f"[OctoPredict] Fetch failed for \\'{query}\\': {e}")
        return []


def _parse_market(m: dict) -> dict | None:
    """Extract signal fields from a Polymarket market."""
    try:
        question = (m.get("question") or m.get("title") or "").strip()
        if not question:
            return None

        volume = 0
        for vkey in ["volume", "volume24hr", "volumeNum"]:
            try:
                v = m.get(vkey)
                if v is not None:
                    volume = float(v)
                    break
            except Exception:
                pass

        # Parse YES probability — Polymarket stores as 0-1 float or list
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
            return None

        if yes_prob >= 75:   signal = "HIGH CONFIDENCE YES"
        elif yes_prob >= 55: signal = "LEANS YES"
        elif yes_prob <= 25: signal = "HIGH CONFIDENCE NO"
        elif yes_prob <= 45: signal = "LEANS NO"
        else:                signal = "TOSS-UP"

        return {
            "question":   question[:100],
            "yes_prob":   yes_prob,
            "no_prob":    round(100 - yes_prob, 1),
            "volume_usd": volume,
            "signal":     signal,
        }
    except Exception:
        return None


def run_prediction_scan(terms: list | None = None) -> dict:
    if terms is None:
        terms = SEARCH_TERMS

    print(f"[OctoPredict] Scanning Polymarket ({len(terms)} queries)...")
    all_markets = []
    seen = set()

    for term in terms:
        raw = _fetch_markets(term)
        count_added = 0
        for m in raw:
            parsed = _parse_market(m)
            if parsed and parsed["question"] not in seen:
                seen.add(parsed["question"])
                all_markets.append(parsed)
                count_added += 1
        print(f"  \\'{term}\\' -> {len(raw)} raw, {count_added} parsed")
        time.sleep(_DELAY)

    all_markets.sort(key=lambda x: x["volume_usd"], reverse=True)
    top = all_markets[:12]
    print(f"[OctoPredict] {len(top)} markets total")
    for m in top[:5]:
        print(f"  YES={m[\'yes_prob\']}% vol=${m[\'volume_usd\']:,.0f} | {m[\'question\'][:60]}")

    fed_mkts    = [m for m in top if any(w in m["question"].lower() for w in ["rate","fed","fomc","cut","hike","federal"])]
    crypto_mkts = [m for m in top if any(w in m["question"].lower() for w in ["bitcoin","btc","crypto","ethereum","eth"])]
    geo_mkts    = [m for m in top if any(w in m["question"].lower() for w in ["war","conflict","election","president"])]
    macro_mkts  = [m for m in top if any(w in m["question"].lower() for w in ["recession","inflation","gdp","oil"])]

    return {
        "timestamp":    datetime.utcnow().isoformat(),
        "total_markets":len(top),
        "markets":      top,
        "by_category": {
            "fed":    fed_mkts[:3],
            "crypto": crypto_mkts[:3],
            "geo":    geo_mkts[:3],
            "macro":  macro_mkts[:3],
        },
    }


def format_predict_for_prompt(result: dict) -> str:
    if not result.get("markets"):
        return ""
    lines = ["Prediction markets (OctoPredict — real money signals):"]
    for m in result["markets"][:6]:
        lines.append(f"  YES {m[\'yes_prob\']}% — {m[\'question\'][:70]} [{m[\'signal\']}]")
    return "\\n".join(lines)


if __name__ == "__main__":
    result = run_prediction_scan()
    print("\\n── OctoPredict Report ──────────────────────")
    for cat, mkts in result["by_category"].items():
        if mkts:
            print(f"\\n{cat.upper()}:")
            for m in mkts:
                print(f"  YES={m[\'yes_prob\']}% ${m[\'volume_usd\']:,.0f} | {m[\'question\'][:65]}")
'''

# ── FIX 2: octo_geo.py ────────────────────────────────────────────────────────

GEO_PATH = "octo_geo.py"

NEW_GEO = '''"""
octo_geo.py
OctoGeo — Geopolitical Intelligence Mind

GDELT Project API — free, no key required.
Rate limit: strict, ~1 req/3s. Only 3 queries per run to stay clean.

Usage:
    from octo_geo import run_geo_scan, format_geo_for_prompt
    geo = run_geo_scan()
"""

import time
import requests
from datetime import datetime

GDELT_BASE = "https://api.gdeltproject.org/api/v2"
HEADERS    = {"User-Agent": "octodamus-oracle/1.0 (@octodamusai)"}

# Reduced to 3 queries — GDELT 429s fast, quality > quantity
GEO_QUERIES = [
    "Federal Reserve economy markets",
    "war conflict geopolitical crisis",
    "China trade tariff sanctions",
]

_DELAY           = 4.0   # 4 seconds between requests — well under rate limit
_RETRY_DELAY     = 8.0
_MAX_RETRIES     = 2
TONE_BEARISH     = -3.0
TONE_BULLISH     = +1.0


def _gdelt_query(query: str, timespan: str = "24h", max_records: int = 6) -> list:
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
                    "sort":       "tonedesc",
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
            return r.json().get("articles", [])
        except requests.exceptions.HTTPError:
            if attempt < _MAX_RETRIES - 1:
                time.sleep(_RETRY_DELAY)
            continue
        except Exception as e:
            print(f"[OctoGeo] GDELT failed for \\'{query}\\': {e}")
            return []
    print(f"[OctoGeo] Giving up on \\'{query}\\' after {_MAX_RETRIES} attempts")
    return []


def _parse(articles: list) -> dict:
    if not articles:
        return {"tone": None, "top_stories": [], "article_count": 0}
    tones, stories = [], []
    for a in articles:
        try:
            tone = float(a.get("tone", 0))
            tones.append(tone)
            stories.append({
                "title":    a.get("title", "")[:80],
                "source":   a.get("domain", ""),
                "tone":     round(tone, 2),
                "seendate": a.get("seendate", "")[:12],
            })
        except Exception:
            continue
    avg = round(sum(tones) / len(tones), 2) if tones else None
    return {"tone": avg, "top_stories": stories[:3], "article_count": len(articles)}


def run_geo_scan(queries: list | None = None) -> dict:
    if queries is None:
        queries = GEO_QUERIES

    print(f"[OctoGeo] Scanning GDELT ({len(queries)} themes, {_DELAY}s delay)...")
    theme_results = {}
    all_tones, risk_flags, signals = [], [], []

    for i, q in enumerate(queries):
        if i > 0:
            time.sleep(_DELAY)
        articles = _gdelt_query(q)
        parsed   = _parse(articles)
        theme_results[q] = parsed
        tone = parsed["tone"]
        if tone is not None:
            all_tones.append(tone)
            label = "BEARISH" if tone <= TONE_BEARISH else ("BULLISH" if tone >= TONE_BULLISH else "neutral")
            if tone <= TONE_BEARISH:
                risk_flags.append(f"Negative tone on \\'{q}\\' ({tone:+.1f})")
            elif tone >= TONE_BULLISH:
                signals.append(f"Positive tone on \\'{q}\\' ({tone:+.1f})")
            print(f"  \\'{q[:40]:40s}\\' tone={tone:+.2f} ({parsed[\'article_count\']} articles)")
        else:
            print(f"  \\'{q[:40]:40s}\\' [no data]")

    global_tone = round(sum(all_tones) / len(all_tones), 2) if all_tones else None

    if global_tone is None:         regime = "UNKNOWN"
    elif global_tone <= -4:         regime = "RISK-OFF — geopolitical stress elevated"
    elif global_tone <= -2:         regime = "CAUTIOUS — negative global narrative"
    elif global_tone >= 1:          regime = "CALM — constructive global tone"
    else:                           regime = "NEUTRAL — mixed geopolitical signals"

    print(f"[OctoGeo] Global tone: {global_tone} | {regime}")
    return {
        "timestamp":   datetime.utcnow().isoformat(),
        "global_tone": global_tone,
        "regime":      regime,
        "themes":      theme_results,
        "signals":     signals,
        "risk_flags":  risk_flags,
    }


def format_geo_for_prompt(result: dict) -> str:
    if not result.get("themes"):
        return ""
    tone   = result.get("global_tone")
    regime = result.get("regime", "UNKNOWN")
    lines  = [f"Geopolitical intelligence (OctoGeo/GDELT) — {regime}"]
    if tone is not None:
        lines.append(f"  Global news tone: {tone:+.2f}")
    for flag in result.get("risk_flags", [])[:3]:
        lines.append(f"  ⚠ {flag}")
    for sig in result.get("signals", [])[:2]:
        lines.append(f"  ✓ {sig}")
    return "\\n".join(lines)


if __name__ == "__main__":
    result = run_geo_scan()
    print(f"\\n── OctoGeo Report ──────────────────────")
    print(f"Global tone: {result[\'global_tone\']} | {result[\'regime\']}")
    for q, data in result["themes"].items():
        if data["tone"] is not None:
            print(f"  {q[:45]:45s} tone={data[\'tone\']:+.2f} ({data[\'article_count\']} articles)")
'''


def write_and_check(path, content, label):
    shutil.copy2(path, path + ".bak_fix2")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    import subprocess
    r = subprocess.run(
        [r"C:\Python314\python.exe", "-m", "py_compile", path],
        capture_output=True, text=True
    )
    if r.returncode == 0:
        print(f"✓ {label} syntax OK")
        return True
    else:
        print(f"✗ {label} syntax error:\n{r.stderr}")
        shutil.copy2(path + ".bak_fix2", path)
        print(f"  Restored backup.")
        return False


if __name__ == "__main__":
    print("── Fixing OctoPredict ───────────────────")
    if os.path.exists(PREDICT_PATH):
        write_and_check(PREDICT_PATH, NEW_PREDICT, "octo_predict.py")
    else:
        print(f"ERROR: {PREDICT_PATH} not found")

    print("\n── Fixing OctoGeo ───────────────────────")
    if os.path.exists(GEO_PATH):
        write_and_check(GEO_PATH, NEW_GEO, "octo_geo.py")
    else:
        print(f"ERROR: {GEO_PATH} not found")

    print("""
Done. Test:
  C:\\Python314\\python.exe octodamus_runner.py --mode predict
  C:\\Python314\\python.exe octodamus_runner.py --mode geo
""")

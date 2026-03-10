"""
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


def _gdelt_get_tone(query: str, timespan: str = "24h") -> float | None:
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
                risk_flags.append(f"Negative tone on \'{q}\' ({tone:+.1f})")
            elif tone >= TONE_BULLISH:
                signals.append(f"Positive tone on \'{q}\' ({tone:+.1f})")
            print(f"  \'{q[:40]:40s}\' tone={tone:+.2f} ({parsed['article_count']} articles)")
        else:
            print(f"  \'{q[:40]:40s}\' [no data]")

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
    return "\n".join(lines)


if __name__ == "__main__":
    result = run_geo_scan()
    print(f"\n── OctoGeo Report ──────────────────────")
    print(f"Global tone: {result['global_tone']} | {result['regime']}")
    for q, data in result["themes"].items():
        if data["tone"] is not None:
            print(f"  {q[:45]:45s} tone={data['tone']:+.2f} ({data['article_count']} articles)")

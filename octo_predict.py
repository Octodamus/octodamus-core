"""
octo_predict.py — v3
Fetches Polymarket markets by TAG (one per request) instead of keyword search.
Tags: economics, crypto, politics — these return actual financial markets.
"""

import json, time, requests
from datetime import datetime

GAMMA_BASE = "https://gamma-api.polymarket.com"
HEADERS    = {"User-Agent": "octodamus-oracle/1.0 (@octodamusai)", "Accept": "application/json"}
_DELAY     = 1.5

# Fetch by single tag slug — Polymarket supports one tag per call
# These tags reliably return financial/political markets
TAG_FETCHES = [
    ("economics",  8),
    ("crypto",     8),
    ("politics",   8),
    ("finance",    6),
    ("geopolitics",6),
]

# Only skip pure entertainment/pure sports
SPORTS_SKIP = [
    "premier league","la liga","bundesliga","serie a","ligue 1",
    "nba finals","nfl super bowl","mlb world series","nhl stanley",
    "world cup winner","olympic gold",
    "grammy","oscar winner","emmy award",
    "will score","goals in","win the match",
]


def fetch_by_tag(tag_slug, limit=8):
    try:
        r = requests.get(
            f"{GAMMA_BASE}/markets",
            params={"tag_slug": tag_slug, "active": "true", "closed": "false",
                    "limit": limit, "order": "volume24hr", "ascending": "false"},
            headers=HEADERS, timeout=12,
        )
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else data.get("markets", data.get("data", []))
    except Exception as e:
        print(f"[OctoPredict] Tag fetch error '{tag_slug}': {e}")
        return []


def parse_yes_prob(m):
    raw = m.get("outcomePrices")
    if raw:
        try:
            prices = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(prices, (list, tuple)) and len(prices) >= 1:
                p = float(prices[0])
                if 0 <= p <= 1:
                    return round(p * 100, 1)
        except Exception:
            pass
    for k in ["lastTradePrice", "bestBid"]:
        val = m.get(k)
        if val is not None:
            try:
                p = float(val)
                if 0 <= p <= 1:
                    return round(p * 100, 1)
            except Exception:
                pass
    return None


def is_relevant(question):
    q = question.lower()
    return not any(s in q for s in SPORTS_SKIP)


def parse_market(m):
    question = (m.get("question") or m.get("title") or "").strip()
    if not question or not is_relevant(question):
        return None
    yes_prob = parse_yes_prob(m)
    if yes_prob is None:
        return None
    volume = 0.0
    for k in ["volume24hr", "volume", "volumeNum"]:
        try:
            v = m.get(k)
            if v:
                volume = float(v); break
        except Exception:
            pass
    if yes_prob >= 75:    signal = "HIGH CONFIDENCE YES"
    elif yes_prob >= 55:  signal = "LEANS YES"
    elif yes_prob <= 25:  signal = "HIGH CONFIDENCE NO"
    elif yes_prob <= 45:  signal = "LEANS NO"
    else:                 signal = "TOSS-UP"
    return {"question": question[:100], "yes_prob": yes_prob,
            "no_prob": round(100-yes_prob,1), "volume_usd": volume, "signal": signal}


def run_prediction_scan(tag_fetches=None):
    if tag_fetches is None:
        tag_fetches = TAG_FETCHES
    print(f"[OctoPredict] Scanning Polymarket by tag ({len(tag_fetches)} tags)...")
    all_markets, seen = [], set()
    for tag, limit in tag_fetches:
        raw = fetch_by_tag(tag, limit)
        added = 0
        for m in raw:
            parsed = parse_market(m)
            if parsed and parsed["question"] not in seen:
                seen.add(parsed["question"]); all_markets.append(parsed); added += 1
        print(f"  tag={tag:15s} -> {len(raw)} raw, {added} kept")
        time.sleep(_DELAY)

    all_markets.sort(key=lambda x: x["volume_usd"], reverse=True)
    top = all_markets[:15]
    print(f"[OctoPredict] {len(top)} markets total")
    for m in top[:6]:
        print(f"  YES={m['yes_prob']}% ${m['volume_usd']:,.0f} | {m['question'][:60]}")

    fed    = [m for m in top if any(w in m["question"].lower() for w in ["rate","fed","fomc","cut","hike","federal reserve","interest"])]
    crypto = [m for m in top if any(w in m["question"].lower() for w in ["bitcoin","btc","crypto","ethereum","eth","100k","200k"])]
    geo    = [m for m in top if any(w in m["question"].lower() for w in ["war","ceasefire","iran","russia","ukraine","nuclear","tariff","sanction","nato"])]
    macro  = [m for m in top if any(w in m["question"].lower() for w in ["recession","inflation","gdp","oil","s&p","stock","market"])]

    return {"timestamp": datetime.utcnow().isoformat(), "total_markets": len(top), "markets": top,
            "by_category": {"fed": fed[:3], "crypto": crypto[:3], "geo": geo[:3], "macro": macro[:3]}}


def format_predict_for_prompt(result):
    if not result.get("markets"):
        return ""
    lines = ["Prediction markets (OctoPredict — real money signals):"]
    for m in result["markets"][:6]:
        lines.append(f"  YES {m['yes_prob']}% — {m['question'][:70]} [{m['signal']}]")
    return "\n".join(lines)


if __name__ == "__main__":
    result = run_prediction_scan()
    print("\n── OctoPredict Report ──────────────────────")
    for cat, mkts in result["by_category"].items():
        if mkts:
            print(f"\n{cat.upper()}:")
            for m in mkts:
                print(f"  YES={m['yes_prob']}% ${m['volume_usd']:,.0f} | {m['question'][:65]}")

"""
octo_pulse.py
OctoPulse — Attention & Fear Mind

Two free signals combined:
  1. Wikipedia Pageviews API — what is the world suddenly paying attention to?
     (leading indicator — spikes before mainstream coverage)
  2. Fear & Greed Index (Alternative.me) — single-number crypto market emotion

No API keys required.

Usage:
    from octo_pulse import run_pulse_scan, format_pulse_for_prompt
    pulse = run_pulse_scan()
"""

import time
import requests
from datetime import datetime, timedelta

WIKI_BASE = "https://wikimedia.org/api/rest_v1/metrics/pageviews"
FNG_BASE  = "https://api.alternative.me/fng/"
HEADERS   = {"User-Agent": "octodamus-oracle/1.0 (@octodamusai)"}

# Articles to monitor for attention spikes
WIKI_ARTICLES = [
    "NVIDIA",
    "Tesla,_Inc.",
    "Bitcoin",
    "Ethereum",
    "Federal_Reserve",
    "Stock_market_crash",
    "Recession",
    "Inflation",
    "Interest_rate",
    "Iran",
    "Russia",
    "China",
    "Artificial_intelligence",
    "Bank_run",
]

# Days to look back for trend comparison
LOOKBACK_DAYS = 7
SPIKE_MULTIPLIER = 2.0  # today vs 7-day avg = spike if > 2x


def _get_wiki_views(article: str, days: int = 8) -> list[int]:
    """Get daily pageview counts for a Wikipedia article."""
    end = datetime.utcnow()
    start = end - timedelta(days=days)
    start_str = start.strftime("%Y/%m/%d")
    end_str   = end.strftime("%Y/%m/%d")
    try:
        url = f"{WIKI_BASE}/per-article/en.wikipedia/all-access/all-agents/{article}/daily/{start_str.replace('/','')}/{end_str.replace('/','')}"
        r = requests.get(url, headers=HEADERS, timeout=10)
        r.raise_for_status()
        items = r.json().get("items", [])
        return [item["views"] for item in items]
    except Exception:
        return []


def _detect_spike(views: list) -> dict | None:
    """Detect if today's views are a significant spike vs recent average."""
    if len(views) < 3:
        return None
    today = views[-1]
    avg = sum(views[:-1]) / len(views[:-1])
    ratio = today / avg if avg > 0 else 0
    return {
        "today": today,
        "avg": round(avg),
        "ratio": round(ratio, 2),
        "is_spike": ratio >= SPIKE_MULTIPLIER,
    }


def _get_fear_greed() -> dict | None:
    """Fetch current Fear & Greed Index from Alternative.me."""
    try:
        r = requests.get(FNG_BASE, params={"limit": 3, "format": "json"}, headers=HEADERS, timeout=10)
        r.raise_for_status()
        data = r.json().get("data", [])
        if not data:
            return None
        current = data[0]
        prev    = data[1] if len(data) > 1 else None
        result = {
            "value":      int(current["value"]),
            "label":      current["value_classification"],
            "timestamp":  current["timestamp"],
        }
        if prev:
            result["prev_value"] = int(prev["value"])
            result["prev_label"] = prev["value_classification"]
            result["delta"]      = result["value"] - result["prev_value"]
        return result
    except Exception as e:
        print(f"[OctoPulse] Fear & Greed fetch failed: {e}")
        return None


def run_pulse_scan(articles: list | None = None) -> dict:
    """
    Scan Wikipedia attention + Fear & Greed Index.
    No API key required.
    """
    if articles is None:
        articles = WIKI_ARTICLES

    print("[OctoPulse] Scanning attention signals...")

    # Fear & Greed first
    fng = _get_fear_greed()
    if fng:
        delta_str = f" d{fng['delta']:+d}" if "delta" in fng else ""
        print(f"  Fear & Greed: {fng['value']} — {fng['label']}{delta_str}")
    time.sleep(0.3)

    # Wikipedia spikes
    print(f"  Scanning {len(articles)} Wikipedia articles...")
    spikes = []
    all_views = {}

    for article in articles:
        views = _get_wiki_views(article, days=8)
        if views:
            spike = _detect_spike(views)
            if spike:
                all_views[article] = spike
                if spike["is_spike"]:
                    spikes.append({
                        "article": article.replace("_", " "),
                        "today": spike["today"],
                        "ratio": spike["ratio"],
                        "avg": spike["avg"],
                    })
        time.sleep(0.15)

    # Sort spikes by ratio
    spikes.sort(key=lambda x: x["ratio"], reverse=True)

    print(f"  {len(spikes)} attention spikes detected")
    for s in spikes[:5]:
        print(f"    {s['article']:30s} {s['ratio']:.1f}x avg ({s['today']:,} views)")

    # Interpret F&G
    fng_signal = "NEUTRAL"
    if fng:
        v = fng["value"]
        if v <= 25:   fng_signal = "EXTREME FEAR"
        elif v <= 40: fng_signal = "FEAR"
        elif v <= 60: fng_signal = "NEUTRAL"
        elif v <= 75: fng_signal = "GREED"
        else:         fng_signal = "EXTREME GREED"

    return {
        "timestamp": datetime.utcnow().isoformat(),
        "fear_greed": fng,
        "fng_signal": fng_signal,
        "attention_spikes": spikes[:8],
        "all_views": all_views,
    }


def format_pulse_for_prompt(result: dict) -> str:
    lines = []
    fng = result.get("fear_greed")
    if fng:
        delta_str = f" d{fng['delta']:+d} from yesterday" if "delta" in fng else ""
        lines.append(f"Fear & Greed Index (OctoPulse): {fng['value']} — {fng['label']}{delta_str}")

    spikes = result.get("attention_spikes", [])
    if spikes:
        lines.append(f"Wikipedia attention spikes ({len(spikes)} detected):")
        for s in spikes[:4]:
            lines.append(f"  {s['article']}: {s['ratio']:.1f}x normal ({s['today']:,} views today)")

    return "\n".join(lines) if lines else ""


if __name__ == "__main__":
    result = run_pulse_scan()
    print(f"\n── OctoPulse Report ──────────────────────")
    fng = result.get("fear_greed")
    if fng:
        print(f"Fear & Greed: {fng['value']} ({fng['label']})")
    print(f"\nAttention Spikes:")
    for s in result["attention_spikes"]:
        print(f"  {s['article']:30s} {s['ratio']:.1f}x avg — {s['today']:,} views")

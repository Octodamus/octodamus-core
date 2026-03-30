"""
octo_pulse.py — OctoPulse Attention & Fear Mind
Wikipedia Pageviews API + Fear & Greed Index (Alternative.me)
No API keys required.

v2 fixes:
- run_pulse_scan() always returns complete dict
- fear_greed key is always a dict (never None) — previously stored None on failure
  which caused callers' .get("fear_greed", {}).get("value") to crash on None.get()
- All internal helpers return [] or {} on failure
"""

import time
import requests
from datetime import datetime, timedelta

WIKI_BASE = "https://wikimedia.org/api/rest_v1/metrics/pageviews"
FNG_BASE  = "https://api.alternative.me/fng/"
HEADERS   = {"User-Agent": "octodamus-oracle/1.0 (@octodamusai)"}

WIKI_ARTICLES = [
    "Bitcoin", "Ethereum", "NVIDIA",
    "Federal_Reserve", "Artificial_intelligence",
]

LOOKBACK_DAYS    = 7
SPIKE_MULTIPLIER = 2.0


def _get_wiki_views(article: str, days: int = 8) -> list:
    """Get daily pageview counts. Always returns list."""
    end   = datetime.utcnow()
    start = end - timedelta(days=days)
    s     = start.strftime("%Y%m%d")
    e     = end.strftime("%Y%m%d")
    try:
        url = f"{WIKI_BASE}/per-article/en.wikipedia/all-access/all-agents/{article}/daily/{s}/{e}"
        r   = requests.get(url, headers=HEADERS, timeout=10)
        r.raise_for_status()
        return [item["views"] for item in (r.json().get("items") or [])]
    except Exception:
        return []


def _detect_spike(views: list) -> dict:
    """Detect attention spike. Always returns dict."""
    if len(views) < 3:
        return {}
    today = views[-1]
    avg   = sum(views[:-1]) / len(views[:-1])
    ratio = today / avg if avg > 0 else 0
    return {
        "today":    today,
        "avg":      round(avg),
        "ratio":    round(ratio, 2),
        "is_spike": ratio >= SPIKE_MULTIPLIER,
    }


def _get_fear_greed() -> dict:
    """
    Fetch Fear & Greed Index. Always returns a dict — never None.
    On failure returns {"value": 50, "label": "Neutral"} as safe default.
    """
    try:
        r = requests.get(FNG_BASE, params={"limit": 3, "format": "json"}, headers=HEADERS, timeout=12)
        r.raise_for_status()
        data = r.json().get("data") or []
        if not data:
            return {"value": 50, "label": "Neutral"}
        current = data[0]
        result  = {
            "value":     int(current.get("value", 50) or 50),
            "label":     str(current.get("value_classification", "Neutral") or "Neutral"),
            "timestamp": current.get("timestamp", ""),
        }
        if len(data) > 1:
            prev = data[1]
            result["prev_value"] = int(prev.get("value", 50) or 50)
            result["prev_label"] = str(prev.get("value_classification", "Neutral") or "Neutral")
            result["delta"]      = result["value"] - result["prev_value"]
        return result
    except Exception as e:
        print(f"[OctoPulse] Fear & Greed fetch failed: {e}")
        return {"value": 50, "label": "Neutral"}


def run_pulse_scan(articles: list = None) -> dict:
    """
    Scan Wikipedia attention + Fear & Greed Index.
    Always returns a complete dict — never None or partial.
    """
    if articles is None:
        articles = WIKI_ARTICLES

    print("[OctoPulse] Scanning attention signals...")

    # fear_greed is always a dict now
    fng = _get_fear_greed()
    delta_str = f" d{fng['delta']:+d}" if "delta" in fng else ""
    print(f"  Fear & Greed: {fng['value']} — {fng['label']}{delta_str}")
    time.sleep(0.3)

    print(f"  Scanning {len(articles)} Wikipedia articles...")
    spikes    = []
    all_views = {}

    for article in articles:
        views = _get_wiki_views(article, days=8)
        if views:
            spike = _detect_spike(views)
            if spike:
                all_views[article] = spike
                if spike.get("is_spike"):
                    spikes.append({
                        "article": article.replace("_", " "),
                        "today":   spike["today"],
                        "ratio":   spike["ratio"],
                        "avg":     spike["avg"],
                    })
        time.sleep(0.15)

    spikes.sort(key=lambda x: x["ratio"], reverse=True)
    print(f"  {len(spikes)} attention spikes detected")
    for s in spikes[:5]:
        print(f"    {s['article']:30s} {s['ratio']:.1f}x avg ({s['today']:,} views)")

    val = fng["value"]
    if val <= 25:   fng_signal = "EXTREME FEAR"
    elif val <= 40: fng_signal = "FEAR"
    elif val <= 60: fng_signal = "NEUTRAL"
    elif val <= 75: fng_signal = "GREED"
    else:           fng_signal = "EXTREME GREED"

    return {
        "timestamp":        datetime.utcnow().isoformat(),
        "fear_greed":       fng,          # always a dict with at least value+label
        "fng_signal":       fng_signal,
        "attention_spikes": spikes[:8],
        "wikipedia":        {"spikes": [s["article"] for s in spikes[:5]]},
        "all_views":        all_views,
    }


def format_pulse_for_prompt(result: dict) -> str:
    result = result or {}
    lines  = []
    fng    = result.get("fear_greed") or {}
    if fng:
        delta_str = f" d{fng['delta']:+d} from yesterday" if "delta" in fng else ""
        lines.append(f"Fear & Greed Index (OctoPulse): {fng.get('value', '?')} — {fng.get('label', 'N/A')}{delta_str}")
    spikes = result.get("attention_spikes") or []
    if spikes:
        lines.append(f"Wikipedia attention spikes ({len(spikes)} detected):")
        for s in spikes[:4]:
            lines.append(f"  {s['article']}: {s['ratio']:.1f}x normal ({s['today']:,} views today)")
    return "\n".join(lines) if lines else ""


if __name__ == "__main__":
    result = run_pulse_scan()
    print(f"\n── OctoPulse Report ──────────────────────")
    fng = result.get("fear_greed") or {}
    print(f"Fear & Greed: {fng.get('value')} ({fng.get('label')})")
    print(f"\nAttention Spikes:")
    for s in result.get("attention_spikes", []):
        print(f"  {s['article']:30s} {s['ratio']:.1f}x avg — {s['today']:,} views")

"""
octo_hype.py — HYPE Token Signal Tracker + HIP-4 Monitor

Tracks:
  1. HYPE price, 24h change, market cap via CoinGecko
  2. HYPE futures OI + funding rate via CoinGlass
  3. HIP-4 spec/launch updates via Tavily search (daily cached)

Usage:
    from octo_hype import hype_context_str, hip4_news_str

    prompt += hype_context_str()      # HYPE derivatives snapshot
    prompt += hip4_news_str()         # Latest HIP-4 developments
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("OctoHype")

_DATA_DIR  = Path(r"C:\Users\walli\octodamus\data")
_HYPE_FILE = _DATA_DIR / "hype_cache.json"
_HIP4_FILE = _DATA_DIR / "hip4_cache.json"

HYPE_TTL  = 300     # 5 min — price/OI cache
HIP4_TTL  = 86400   # 24 hr — HIP-4 news cache


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_json(path: Path) -> dict:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_json(path: Path, data: dict):
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _age(ts: float) -> float:
    return time.time() - ts


def _get_secrets() -> dict:
    """Load secrets from .octo_secrets or env."""
    secrets_paths = [
        Path(__file__).parent / ".octo_secrets",
        Path(r"C:\Users\walli\octodamus\.octo_secrets"),
    ]
    for p in secrets_paths:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            return d.get("secrets", d)
        except Exception:
            continue
    return {}


# ── HYPE Price + OI ───────────────────────────────────────────────────────────

def fetch_hype_price() -> dict:
    """CoinGecko: HYPE price, 24h change, market cap."""
    try:
        import requests
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={
                "ids": "hyperliquid",
                "vs_currencies": "usd",
                "include_24hr_change": "true",
                "include_market_cap": "true",
            },
            timeout=10,
        )
        if r.status_code == 200:
            d = r.json().get("hyperliquid", {})
            return {
                "price":     round(float(d.get("usd", 0)), 4),
                "chg_24h":   round(float(d.get("usd_24h_change", 0) or 0), 2),
                "market_cap": int(d.get("usd_market_cap", 0) or 0),
            }
    except Exception as e:
        log.warning(f"[HYPE] CoinGecko fetch failed: {e}")
    return {}


def fetch_hype_coinglass() -> dict:
    """CoinGlass: HYPE futures OI and funding rate."""
    try:
        from octo_coinglass import glass
        oi_data = glass.open_interest("HYPE", interval="4h")
        fr_data = glass.funding_rate("HYPE", interval="8h")

        oi_list = oi_data.get("data", [])
        fr_list = fr_data.get("data", [])

        oi_usd  = oi_list[-1].get("openInterest", 0) if oi_list else 0
        fr_val  = fr_list[-1].get("fundingRate", 0)  if fr_list else 0

        return {
            "oi_usd":       round(float(oi_usd), 0),
            "funding_rate": round(float(fr_val) * 100, 4),  # as %
        }
    except Exception as e:
        log.debug(f"[HYPE] CoinGlass fetch failed (may not be in plan): {e}")
    return {}


def get_hype_snapshot(force: bool = False) -> dict:
    """Combined HYPE snapshot with caching."""
    cache = _load_json(_HYPE_FILE)
    if not force and cache.get("ts") and _age(cache["ts"]) < HYPE_TTL:
        return cache

    price_data = fetch_hype_price()
    cg_data    = fetch_hype_coinglass()

    snap = {
        "ts":        time.time(),
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        **price_data,
        **cg_data,
    }
    _save_json(_HYPE_FILE, snap)
    return snap


def hype_context_str() -> str:
    """
    Returns a prompt-ready HYPE market snapshot.
    Example output:
      HYPE (Hyperliquid): $34.21 (+4.2% 24h) | OI $312M | FR +0.012%
      HIP-4 status: Pre-launch — builder staking structure announced, oracle design pending.
      Structural: 1M HYPE builder bond ($35M) creates supply lockup. Bull thesis: $85 base / $137 bull.
    """
    try:
        snap = get_hype_snapshot()
        price    = snap.get("price", 0)
        chg      = snap.get("chg_24h", 0)
        oi       = snap.get("oi_usd", 0)
        fr       = snap.get("funding_rate", 0)
        mcap     = snap.get("market_cap", 0)

        lines = ["[HYPE / Hyperliquid Signal]"]
        if price:
            chg_str = f"+{chg:.2f}%" if chg >= 0 else f"{chg:.2f}%"
            lines.append(f"Price: ${price:,.2f} ({chg_str} 24h)")
        if mcap:
            lines.append(f"Market cap: ${mcap/1e9:.2f}B")
        if oi:
            lines.append(f"Futures OI: ${oi/1e6:.0f}M")
        if fr:
            fr_str = f"+{fr:.4f}%" if fr >= 0 else f"{fr:.4f}%"
            lines.append(f"Funding rate: {fr_str}")

        lines.append("HIP-4: Event futures (binary 0/1) — pre-launch, H2 2026 expected.")
        lines.append("Structure: 1M HYPE builder bond, cross-margin with perps, unified risk engine.")
        lines.append("Price targets: $85 base / $137 bull / $203 outrageous (supply lockup + HIP-4 fee revenue).")

        return "\n".join(lines)
    except Exception as e:
        log.warning(f"[HYPE] context_str failed: {e}")
        return ""


# ── HIP-4 News Monitor ────────────────────────────────────────────────────────

_HIP4_SEARCH_QUERIES = [
    "Hyperliquid HIP-4 event futures launch update",
    "Hyperliquid HIP-4 oracle design binary markets",
    "Hyperliquid prediction markets builder staking",
]

# Key milestones to watch for in search results
_HIP4_MILESTONES = [
    "oracle design",
    "oracle announced",
    "builder staking open",
    "HIP-4 live",
    "HIP-4 launch",
    "mainnet",
    "testnet",
    "governance vote",
    "dispute resolution",
]


def fetch_hip4_updates() -> list[dict]:
    """
    Search Tavily for HIP-4 spec and launch updates.
    Returns list of {title, url, content, published_date} dicts.
    """
    secrets = _get_secrets()
    tavily_key = secrets.get("TAVILY_API_KEY") or os.environ.get("TAVILY_API_KEY", "")
    if not tavily_key:
        log.warning("[HIP4] No TAVILY_API_KEY — skipping search")
        return []

    results = []
    seen_urls = set()

    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=tavily_key)

        for query in _HIP4_SEARCH_QUERIES[:2]:  # 2 queries to stay within budget
            try:
                resp = client.search(
                    query=query,
                    search_depth="advanced",
                    max_results=5,
                    days=14,  # last 2 weeks only
                )
                for r in resp.get("results", []):
                    url = r.get("url", "")
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)
                    results.append({
                        "title":   r.get("title", ""),
                        "url":     url,
                        "content": r.get("content", "")[:500],
                        "published_date": r.get("published_date", ""),
                        "score":   r.get("score", 0),
                    })
                time.sleep(0.5)
            except Exception as e:
                log.warning(f"[HIP4] Tavily query failed: {e}")

    except ImportError:
        log.warning("[HIP4] tavily package not installed")

    # Sort by relevance score
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:6]


def _detect_milestones(results: list[dict]) -> list[str]:
    """Check if any results mention key HIP-4 milestones."""
    found = []
    combined = " ".join(r["content"].lower() + r["title"].lower() for r in results)
    for milestone in _HIP4_MILESTONES:
        if milestone in combined:
            found.append(milestone)
    return found


def get_hip4_updates(force: bool = False) -> dict:
    """Cached daily HIP-4 news fetch."""
    cache = _load_json(_HIP4_FILE)
    if not force and cache.get("ts") and _age(cache["ts"]) < HIP4_TTL:
        return cache

    articles = fetch_hip4_updates()
    milestones = _detect_milestones(articles)

    data = {
        "ts":         time.time(),
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "articles":   articles,
        "milestones": milestones,
        "has_updates": len(milestones) > 0,
    }
    _save_json(_HIP4_FILE, data)
    return data


def hip4_news_str(max_articles: int = 3) -> str:
    """
    Returns a prompt-ready HIP-4 news summary.
    Only returns content if there are relevant articles.
    """
    try:
        data = get_hip4_updates()
        articles  = data.get("articles", [])
        milestones = data.get("milestones", [])

        if not articles:
            return (
                "[HIP-4 Monitor]\n"
                "No new HIP-4 spec updates in last 14 days. "
                "Status: pre-launch, oracle design unannounced. "
                "Expected H2 2026."
            )

        lines = ["[HIP-4 Hyperliquid Event Futures — Latest Updates]"]

        if milestones:
            lines.append(f"MILESTONE DETECTED: {', '.join(milestones)}")

        for a in articles[:max_articles]:
            title   = a.get("title", "")
            content = a.get("content", "")[:200]
            pub     = a.get("published_date", "")[:10]
            if title:
                lines.append(f"\n[{pub}] {title}")
            if content:
                lines.append(f"  {content}...")

        return "\n".join(lines)

    except Exception as e:
        log.warning(f"[HIP4] news_str failed: {e}")
        return ""


# ── Quick CLI test ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    print("=" * 60)
    print("HYPE SNAPSHOT")
    print("=" * 60)
    snap = get_hype_snapshot(force=True)
    print(json.dumps(snap, indent=2))

    print("\n" + "=" * 60)
    print("HYPE CONTEXT STRING")
    print("=" * 60)
    print(hype_context_str())

    print("\n" + "=" * 60)
    print("HIP-4 UPDATES")
    print("=" * 60)
    force = "--force" in sys.argv
    updates = get_hip4_updates(force=force)
    print(f"Articles found: {len(updates.get('articles', []))}")
    print(f"Milestones: {updates.get('milestones', [])}")
    print()
    print(hip4_news_str())

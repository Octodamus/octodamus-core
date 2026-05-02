"""
octo_spacex.py — SpaceX IPO Monitor

SpaceX is private. No price calls possible until public.
This monitors for IPO signals and fires Discord/Telegram alerts.

Tracks:
  - News mentions of "SpaceX IPO", "Starlink IPO", "SpaceX S-1"
  - SEC EDGAR filings by SpaceX entities
  - Pre-IPO valuation updates (funding rounds)
  - Elon Musk statements about IPO timeline

Run via: python octodamus_runner.py --mode spacex_monitor
Or call check_spacex_ipo() from any scheduled task.

When SpaceX files an S-1 → Octodamus publishes an oracle call on IPO day.
Until then → news alerts only, no price calls.
"""

import json
import time
from pathlib import Path

SECRETS_FILE  = Path(r"C:\Users\walli\octodamus\.octo_secrets")
STATE_FILE    = Path(r"C:\Users\walli\octodamus\data\spacex_ipo_state.json")
COOLDOWN_H    = 24  # alert at most once per day

_IPO_KEYWORDS = [
    "spacex ipo", "starlink ipo", "spacex s-1", "spacex s1",
    "spacex going public", "spacex public offering", "spacex listing",
    "starlink listing", "starlink public", "musk spacex ipo",
    "spacex valuation", "spacex funding round",
]

_HIGH_SIGNAL_KEYWORDS = [
    "spacex s-1", "spacex s1", "spacex going public",
    "spacex public offering", "spacex listing", "starlink listing",
]


def _load_state() -> dict:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"last_alert_ts": 0, "last_headline": ""}


def _save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _get_secrets() -> dict:
    try:
        s = json.loads(SECRETS_FILE.read_text(encoding="utf-8"))
        return s.get("secrets", s)
    except Exception:
        return {}


def _search_news(query: str, api_key: str) -> list:
    try:
        import httpx
        r = httpx.get(
            "https://newsapi.org/v2/everything",
            params={"q": query, "sortBy": "publishedAt",
                    "pageSize": 5, "language": "en", "apiKey": api_key},
            timeout=10,
        )
        if r.status_code == 200:
            return r.json().get("articles", [])
    except Exception:
        pass
    return []


def _check_edgar() -> str | None:
    """Check SEC EDGAR full-text search for SpaceX S-1 filings."""
    try:
        import httpx
        r = httpx.get(
            "https://efts.sec.gov/LATEST/search-index?q=%22SpaceX%22+%22S-1%22&dateRange=custom"
            "&startdt=2026-01-01&forms=S-1,S-1/A",
            timeout=10,
            headers={"User-Agent": "Octodamus octodamusai@gmail.com"},
        )
        if r.status_code == 200:
            hits = r.json().get("hits", {}).get("hits", [])
            if hits:
                return f"SEC EDGAR: SpaceX S-1 filing detected — {hits[0].get('_source', {}).get('file_date', '')}"
    except Exception:
        pass
    return None


def check_spacex_ipo(silent: bool = False) -> dict:
    """
    Scan news and EDGAR for SpaceX IPO signals.
    Returns: {signal: bool, headline: str, high_signal: bool}
    Fires Discord/Telegram alert if new high-signal headline found.
    """
    secrets = _get_secrets()
    newsapi_key = secrets.get("NEWSAPI_API_KEY", "")
    state = _load_state()

    found_headlines = []
    high_signal = False

    # Check EDGAR first (most reliable signal)
    edgar_hit = _check_edgar()
    if edgar_hit:
        found_headlines.append(edgar_hit)
        high_signal = True

    # Search NewsAPI
    if newsapi_key:
        articles = _search_news("SpaceX IPO OR Starlink IPO OR SpaceX public offering", newsapi_key)
        for a in articles:
            title = a.get("title", "").lower()
            if any(kw in title for kw in _IPO_KEYWORDS):
                found_headlines.append(a.get("title", ""))
                if any(kw in title for kw in _HIGH_SIGNAL_KEYWORDS):
                    high_signal = True

    if not found_headlines:
        if not silent:
            print("[SpaceX] No IPO signals found.")
        return {"signal": False, "headline": "", "high_signal": False}

    # Check cooldown
    now = time.time()
    hours_since = (now - state.get("last_alert_ts", 0)) / 3600
    new_headline = found_headlines[0] != state.get("last_headline", "")

    if new_headline or hours_since > COOLDOWN_H:
        headline = found_headlines[0]
        alert_msg = (
            f"{'🚨 HIGH SIGNAL' if high_signal else '📡'} SpaceX IPO Monitor\n"
            f"{headline}\n"
            f"{'→ S-1 filing detected — oracle call prep needed' if high_signal else '→ Monitoring. No price call until public.'}"
        )

        try:
            from octodamus_runner import discord_alert
            discord_alert(alert_msg)
        except Exception:
            pass

        state["last_alert_ts"] = now
        state["last_headline"] = headline
        _save_state(state)

        if not silent:
            print(f"[SpaceX] Alert fired: {headline[:80]}")

    return {
        "signal":      True,
        "headline":    found_headlines[0],
        "high_signal": high_signal,
        "count":       len(found_headlines),
    }

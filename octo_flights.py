"""
octo_flights.py — Travel Demand Signals for Octodamus

Two macro leading indicators:

1. AVIATION VOLUME (OpenSky Network)
   Global airborne aircraft count sampled daily at noon UTC.
   Week-over-week delta → RISK-ON / RISK-OFF / NEUTRAL.
   Airlines commit to schedules 6-8 weeks out — rising volume = forward confidence.

2. US AIR PASSENGER THROUGHPUT (TSA.gov)
   Official daily checkpoint passenger counts from TSA.
   7-day rolling average vs prior 7 days → RISK-ON / RISK-OFF / NEUTRAL.
   Measures actual US travel demand — government data, no rate limits.

Combined signal: if both point the same direction → stronger conviction.

Scheduled: run daily at noon UTC via Octodamus-FlightSample task.

Usage:
  python octo_flights.py            # sample flights + update hotel trends
  python octo_flights.py --signal   # print all signals as JSON
  python octo_flights.py --flights  # flights signal only
  python octo_flights.py --hotels   # hotel trends signal only
"""

import argparse
import json
import logging
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

log = logging.getLogger("OctoFlights")

CACHE_FILE   = Path(r"C:\Users\walli\octodamus\data\flights_cache.json")
TSA_CACHE    = Path(r"C:\Users\walli\octodamus\data\tsa_cache.json")
OPENSKY_URL  = "https://opensky-network.org/api/states/all"

SIGNAL_THRESHOLD  = 0.03   # 3% week-over-week change to flip risk signal
WARM_UP_DAYS      = 14     # days needed before aviation signal is meaningful

# Optional OpenSky credentials (for higher rate limits) — from .octo_secrets
def _opensky_auth() -> tuple:
    try:
        p = Path(r"C:\Users\walli\octodamus\.octo_secrets")
        d = json.loads(p.read_text(encoding="utf-8"))
        s = d.get("secrets", d)
        user = s.get("OPENSKY_USER", "")
        pw   = s.get("OPENSKY_PASS", "")
        if user and pw:
            return (user, pw)
    except Exception:
        pass
    return None


# ── Cache ─────────────────────────────────────────────────────────────────────

def _load_cache() -> dict:
    try:
        if CACHE_FILE.exists():
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"samples": []}


def _save_cache(data: dict):
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ── Sample ────────────────────────────────────────────────────────────────────

def sample_now() -> dict:
    """
    Hit OpenSky, count airborne aircraft, store sample.
    Returns the sample dict.
    """
    auth  = _opensky_auth()
    params = {}
    try:
        r = requests.get(
            OPENSKY_URL,
            params=params,
            auth=auth,
            timeout=15,
        )
        r.raise_for_status()
        d = r.json()
        states = d.get("states") or []
        count  = len(states)
    except Exception as e:
        log.error(f"OpenSky fetch failed: {e}")
        return {}

    now = datetime.now(timezone.utc)
    sample = {
        "ts":      now.isoformat(),
        "date":    now.strftime("%Y-%m-%d"),
        "hour_utc": now.hour,
        "count":   count,
    }

    cache = _load_cache()
    # Keep only last 30 days of samples (1 per day ideally)
    existing_dates = {s["date"] for s in cache["samples"]}
    if sample["date"] not in existing_dates:
        cache["samples"].append(sample)
        # Prune to last 30 days
        cache["samples"] = sorted(cache["samples"], key=lambda x: x["date"])[-30:]
        _save_cache(cache)
        log.info(f"Sampled: {count} aircraft on {sample['date']}")
    else:
        # Update today's sample if this one is closer to noon UTC (most representative)
        for s in cache["samples"]:
            if s["date"] == sample["date"]:
                if abs(now.hour - 12) < abs(s["hour_utc"] - 12):
                    s.update(sample)
                    _save_cache(cache)
                    log.info(f"Updated today's sample: {count} aircraft")
                break

    return sample


# ── Signal ────────────────────────────────────────────────────────────────────

def get_signal() -> dict:
    """
    Compute week-over-week aviation volume signal.

    Returns dict:
      status: 'building' | 'live'
      signal: 'RISK-ON' | 'RISK-OFF' | 'NEUTRAL'
      this_week_avg: int
      last_week_avg: int
      delta_pct: float
      samples_total: int
      brief: str   (one sentence, ready for Octodamus daily brief injection)
    """
    cache   = _load_cache()
    samples = sorted(cache.get("samples", []), key=lambda x: x["date"])

    if len(samples) < WARM_UP_DAYS:
        return {
            "status":        "building",
            "signal":        "NEUTRAL",
            "this_week_avg": 0,
            "last_week_avg": 0,
            "delta_pct":     0.0,
            "samples_total": len(samples),
            "brief": f"Aviation signal building ({len(samples)}/{WARM_UP_DAYS} days collected).",
        }

    # Most recent 7 samples = this week; prior 7 = last week
    this_week = samples[-7:]
    last_week = samples[-14:-7]

    this_avg = sum(s["count"] for s in this_week) / len(this_week)
    last_avg = sum(s["count"] for s in last_week) / len(last_week)

    if last_avg == 0:
        delta_pct = 0.0
    else:
        delta_pct = (this_avg - last_avg) / last_avg

    if delta_pct >= SIGNAL_THRESHOLD:
        signal = "RISK-ON"
        arrow  = "up"
    elif delta_pct <= -SIGNAL_THRESHOLD:
        signal = "RISK-OFF"
        arrow  = "down"
    else:
        signal = "NEUTRAL"
        arrow  = "flat"

    delta_str = f"{delta_pct:+.1%}"

    brief = (
        f"Global aviation volume {arrow} {delta_str} week-over-week "
        f"({int(this_avg):,} vs {int(last_avg):,} avg airborne aircraft) -- "
        f"macro transport signal: {signal}."
    )

    return {
        "status":        "live",
        "signal":        signal,
        "this_week_avg": round(this_avg),
        "last_week_avg": round(last_avg),
        "delta_pct":     round(delta_pct, 4),
        "samples_total": len(samples),
        "brief":         brief,
    }


def get_flights_context() -> str:
    """One-line aviation context for Octodamus prompts. Empty if still building."""
    sig = get_signal()
    if sig["status"] != "live":
        return ""
    return f"AVIATION SIGNAL: {sig['signal']} | {sig['brief']}"


# ── US Air Passenger Throughput (TSA.gov) ────────────────────────────────────

TSA_URL = "https://www.tsa.gov/travel/passenger-volumes"


def _load_tsa_cache() -> dict:
    try:
        if TSA_CACHE.exists():
            return json.loads(TSA_CACHE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"rows": []}


def _save_tsa_cache(data: dict):
    TSA_CACHE.parent.mkdir(parents=True, exist_ok=True)
    TSA_CACHE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def fetch_tsa_throughput() -> dict:
    """
    Scrape TSA daily passenger checkpoint throughput.
    Returns dict with 7-day avg this week vs last week, delta, signal.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        log.warning("BeautifulSoup not available — install beautifulsoup4")
        return {"status": "unavailable", "signal": "NEUTRAL", "brief": "", "delta_pct": 0.0}

    try:
        r = requests.get(TSA_URL,
                         headers={"User-Agent": "Mozilla/5.0 (compatible; Octodamus/1.0)"},
                         timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        table = soup.find("table")
        if not table:
            raise ValueError("No table found on TSA page")

        rows = []
        for tr in table.find_all("tr")[1:]:  # skip header
            cols = [td.get_text(strip=True) for td in tr.find_all("td")]
            if len(cols) >= 2:
                try:
                    date_str = cols[0]   # e.g. "4/12/2026"
                    count    = int(cols[1].replace(",", ""))
                    rows.append({"date": date_str, "count": count})
                except (ValueError, IndexError):
                    continue

        if len(rows) < 14:
            log.warning(f"TSA: only {len(rows)} rows — need 14 for delta")
            return {"status": "building", "signal": "NEUTRAL", "brief": f"TSA signal building ({len(rows)}/14 days).", "delta_pct": 0.0}

        this_week_rows = rows[:7]
        last_week_rows = rows[7:14]
        this_avg = sum(r["count"] for r in this_week_rows) / 7
        last_avg = sum(r["count"] for r in last_week_rows) / 7
        delta_pct = (this_avg - last_avg) / last_avg if last_avg else 0.0

        if delta_pct >= SIGNAL_THRESHOLD:
            signal = "RISK-ON"
        elif delta_pct <= -SIGNAL_THRESHOLD:
            signal = "RISK-OFF"
        else:
            signal = "NEUTRAL"

        result = {
            "fetched_at":    datetime.now(timezone.utc).isoformat(),
            "status":        "live",
            "signal":        signal,
            "this_week_avg": round(this_avg),
            "last_week_avg": round(last_avg),
            "delta_pct":     round(delta_pct, 4),
            "latest_date":   rows[0]["date"],
            "brief": (
                f"US air passengers {delta_pct:+.1%} week-over-week "
                f"({int(this_avg):,.0f} vs {int(last_avg):,.0f} daily avg, TSA data) -- "
                f"travel demand signal: {signal}."
            ),
        }
        _save_tsa_cache({"rows": rows[:30], "last_result": result})
        log.info(f"TSA throughput: {signal} | delta={delta_pct:+.1%} | latest={rows[0]['date']}")
        return result

    except Exception as e:
        log.error(f"TSA fetch failed: {e}")
        cached = _load_tsa_cache().get("last_result", {})
        if cached:
            cached["status"] = "stale"
            return cached
        return {"status": "unavailable", "signal": "NEUTRAL", "brief": "", "delta_pct": 0.0}


def get_tsa_signal() -> dict:
    """Returns TSA passenger throughput signal."""
    cached = _load_tsa_cache().get("last_result", {})
    # Refresh if cache older than 24h or missing
    if cached.get("fetched_at"):
        try:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(cached["fetched_at"])
            if age.total_seconds() < 86400:
                return cached
        except Exception:
            pass
    return fetch_tsa_throughput()


def get_hotels_context() -> str:
    """One-line TSA travel demand context for Octodamus prompts."""
    sig = get_tsa_signal()
    if not sig.get("brief"):
        return ""
    stale = " [cached]" if sig.get("status") == "stale" else ""
    return f"TSA TRAVEL SIGNAL: {sig['signal']} | {sig['brief']}{stale}"


def get_travel_context() -> str:
    """
    Combined aviation + hotel context for Octodamus prompts.
    Both signals combined into one block.
    """
    parts = []
    flights = get_flights_context()
    hotels  = get_hotels_context()
    if flights:
        parts.append(flights)
    if hotels:
        parts.append(hotels)
    if not parts:
        return ""

    # Combined conviction note when both agree
    try:
        fsig = get_signal().get("signal", "NEUTRAL")
        hsig = get_tsa_signal().get("signal", "NEUTRAL")
        if fsig == hsig and fsig != "NEUTRAL":
            parts.append(f"COMBINED TRAVEL SIGNAL: {fsig} (aviation + TSA demand aligned)")
    except Exception:
        pass

    return "Travel Demand Macro:\n" + "\n".join(parts)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--signal",    action="store_true", help="Print all signals as JSON")
    parser.add_argument("--flights",   action="store_true", help="Flights signal only")
    parser.add_argument("--hotels",    action="store_true", help="Hotel trends signal only")
    parser.add_argument("--no-sample", action="store_true", help="Skip sampling, just print signals")
    args = parser.parse_args()

    # Sample aviation
    if not args.no_sample and not args.hotels:
        s = sample_now()
        if s:
            print(f"Aviation sampled: {s['count']:,} aircraft on {s['date']} at {s['hour_utc']}:00 UTC")

    # Fetch TSA throughput
    if not args.flights:
        h = fetch_tsa_throughput()
        print(f"TSA throughput: {h.get('signal','?')} | delta={h.get('delta_pct',0):+.1%} | status={h.get('status','?')}")

    if args.signal:
        print("\n--- AVIATION ---")
        print(json.dumps(get_signal(), indent=2))
        print("\n--- TSA THROUGHPUT ---")
        print(json.dumps(get_tsa_signal(), indent=2))
    elif args.hotels:
        h = get_tsa_signal()
        print(f"\nStatus:   {h.get('status')}")
        print(f"Signal:   {h.get('signal')}")
        print(f"Delta:    {h.get('delta_pct',0):+.1%}")
        print(f"\n{h.get('brief','')}")
    else:
        sig = get_signal()
        print(f"\n--- Aviation ---")
        print(f"Status:        {sig['status']}")
        print(f"Signal:        {sig['signal']}")
        print(f"This week avg: {sig['this_week_avg']:,}")
        print(f"Delta:         {sig['delta_pct']:+.1%}")
        print(f"Samples:       {sig['samples_total']}")
        print(f"{sig['brief']}")
        print(f"\n--- Combined ---")
        print(get_travel_context() or "(signals still building)")


if __name__ == "__main__":
    main()

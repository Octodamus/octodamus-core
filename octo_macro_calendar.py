"""Macro event calendar — blocks oracle calls during high-volatility event windows."""

from datetime import datetime, timedelta, timezone
from typing import Tuple, List

# ---------------------------------------------------------------------------
# FOMC decision dates (day 2 of the 2-day meeting — the decision day)
# ---------------------------------------------------------------------------
_FOMC_DATES = [
    # 2025
    "2025-01-29",
    "2025-03-19",
    "2025-05-07",
    "2025-06-18",
    "2025-07-30",
    "2025-09-17",
    "2025-10-29",
    "2025-12-10",
    # 2026
    "2026-01-28",
    "2026-03-18",
    "2026-04-29",
    "2026-06-17",
]

# CPI release dates — hardcoded for 2025-2026 where known, else estimated as
# the second Tuesday of the month at 08:30 ET (13:30 UTC).
_CPI_DATES = [
    # 2025
    "2025-01-15",
    "2025-02-12",
    "2025-03-12",
    "2025-04-10",
    "2025-05-13",
    "2025-06-11",
    "2025-07-15",
    "2025-08-12",
    "2025-09-10",
    "2025-10-15",
    "2025-11-13",
    "2025-12-10",
    # 2026
    "2026-01-14",
    "2026-02-11",
    "2026-03-11",
    "2026-04-14",
    "2026-05-13",
    "2026-06-10",
]


def _parse_date(date_str: str) -> datetime:
    """Parse YYYY-MM-DD string to a UTC-aware datetime at 14:00 UTC (FOMC/CPI release time)."""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return d.replace(hour=14, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)


def _first_friday_of_month(year: int, month: int) -> datetime:
    """Return the first Friday of the given month at 12:30 UTC (NFP release time)."""
    d = datetime(year, month, 1, tzinfo=timezone.utc)
    # weekday(): Monday=0, Friday=4
    days_until_friday = (4 - d.weekday()) % 7
    d = d + timedelta(days=days_until_friday)
    return d.replace(hour=12, minute=30, second=0, microsecond=0)


def _build_nfp_dates(start_year: int = 2025, end_year: int = 2026) -> List[dict]:
    events = []
    for year in range(start_year, end_year + 1):
        for month in range(1, 13):
            dt = _first_friday_of_month(year, month)
            events.append({"name": "NFP", "dt": dt})
    return events


def _build_all_events() -> List[dict]:
    events = []
    for ds in _FOMC_DATES:
        events.append({"name": "FOMC Decision", "dt": _parse_date(ds)})
    for ds in _CPI_DATES:
        events.append({"name": "CPI Release", "dt": _parse_date(ds)})
    events.extend(_build_nfp_dates())
    events.sort(key=lambda e: e["dt"])
    return events


_ALL_EVENTS = _build_all_events()


def is_event_blocked(
    hours_before: float = 24,
    hours_after: float = 6,
) -> Tuple[bool, str, str]:
    """Return (blocked, reason, next_event_str).

    blocked   — True if we are inside a blackout window.
    reason    — Human-readable explanation if blocked, else empty string.
    next_event — Description of the next upcoming macro event.
    """
    now = datetime.now(timezone.utc)
    before_delta = timedelta(hours=hours_before)
    after_delta = timedelta(hours=hours_after)

    next_event_str = "None found"
    for ev in _ALL_EVENTS:
        if ev["dt"] > now:
            days_away = (ev["dt"] - now).total_seconds() / 86400
            next_event_str = f"{ev['name']} on {ev['dt'].strftime('%Y-%m-%d')} ({days_away:.1f}d away)"
            break

    for ev in _ALL_EVENTS:
        window_start = ev["dt"] - before_delta
        window_end = ev["dt"] + after_delta
        if window_start <= now <= window_end:
            reason = (
                f"Blackout: {ev['name']} on {ev['dt'].strftime('%Y-%m-%d %H:%M')} UTC. "
                f"Window: {window_start.strftime('%Y-%m-%d %H:%M')} — "
                f"{window_end.strftime('%Y-%m-%d %H:%M')} UTC."
            )
            return True, reason, next_event_str

    return False, "", next_event_str


def get_upcoming_events(days: int = 7) -> List[dict]:
    """Return list of macro events within the next `days` days."""
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=days)
    results = []
    for ev in _ALL_EVENTS:
        if now <= ev["dt"] <= cutoff:
            days_away = (ev["dt"] - now).total_seconds() / 86400
            results.append(
                {
                    "name": ev["name"],
                    "date": ev["dt"].strftime("%Y-%m-%d %H:%M UTC"),
                    "days_away": round(days_away, 1),
                }
            )
    return results


def macro_calendar_context_str() -> str:
    """Return a formatted string for prompt injection."""
    try:
        blocked, reason, next_ev = is_event_blocked()
        upcoming = get_upcoming_events(days=7)
        lines = ["[MACRO CALENDAR]"]
        if blocked:
            lines.append(f"  STATUS   : BLOCKED — {reason}")
        else:
            lines.append("  STATUS   : Clear")
        lines.append(f"  Next Event: {next_ev}")
        if upcoming:
            lines.append("  Upcoming (7d):")
            for ev in upcoming:
                lines.append(f"    - {ev['name']} on {ev['date']} ({ev['days_away']}d)")
        return "\n".join(lines) + "\n"
    except Exception:
        return ""

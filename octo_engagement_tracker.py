"""Engagement tracker — optimal post timing and voice variety for Octodamus X posts."""

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

_LOG_PATH = Path(r"C:\Users\walli\octodamus\octo_posted_log.json")

# Peak engagement windows for crypto X (UTC)
# Format: (weekday_range, hour_start, hour_end)
# weekday: 0=Monday, 6=Sunday
_PEAK_WINDOWS = [
    # US pre-market / EU afternoon, Mon-Fri
    {"days": range(0, 5), "start": 12, "end": 14, "label": "peak"},
    # Post-US-open / high-activity, Mon-Fri
    {"days": range(0, 5), "start": 19, "end": 22, "label": "peak"},
    # Weekend community active, Saturday
    {"days": [5], "start": 14, "end": 18, "label": "peak"},
]

# Secondary "good" windows (decent engagement, not peak)
_GOOD_WINDOWS = [
    # Mon-Fri morning EU
    {"days": range(0, 5), "start": 8, "end": 12, "label": "good"},
    # Mon-Fri late US
    {"days": range(0, 5), "start": 22, "end": 24, "label": "good"},
    # Sunday casual
    {"days": [6], "start": 15, "end": 20, "label": "good"},
]


def _load_log() -> list:
    """Load the posted log; return empty list on failure."""
    try:
        if not _LOG_PATH.exists():
            return []
        with open(_LOG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _window_label(now: datetime) -> str:
    """Return 'peak', 'good', or 'off' for the given UTC datetime."""
    wd = now.weekday()
    hr = now.hour
    for w in _PEAK_WINDOWS:
        if wd in w["days"] and w["start"] <= hr < w["end"]:
            return "peak"
    for w in _GOOD_WINDOWS:
        if wd in w["days"] and w["start"] <= hr < w["end"]:
            return "good"
    return "off"


def _hours_to_next_peak(now: datetime) -> float | None:
    """Return hours until the next peak window starts (searches up to 7 days ahead)."""
    check = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    for _ in range(7 * 24):
        wd = check.weekday()
        hr = check.hour
        for w in _PEAK_WINDOWS:
            if wd in w["days"] and hr == w["start"]:
                delta = (check - now).total_seconds() / 3600
                return round(delta, 1)
        check += timedelta(hours=1)
    return None


def get_best_post_time() -> dict:
    """Return current engagement window and time to next peak."""
    now = datetime.now(timezone.utc)
    window = _window_label(now)

    if window == "peak":
        peak_in_hours = 0.0
        recommendation = "Post now — you are in a PEAK engagement window."
    elif window == "good":
        peak_in_hours = _hours_to_next_peak(now)
        recommendation = (
            f"Good window to post. Next peak in {peak_in_hours:.1f}h — "
            f"post now or wait for peak."
        )
    else:
        peak_in_hours = _hours_to_next_peak(now)
        if peak_in_hours is not None:
            recommendation = (
                f"Off-peak. Next peak window in {peak_in_hours:.1f}h — "
                f"consider waiting unless time-sensitive."
            )
        else:
            recommendation = "Off-peak. No upcoming peak found in the next 7 days."

    return {
        "current_window": window,
        "peak_in_hours": peak_in_hours,
        "recommendation": recommendation,
    }


def get_recent_voices(n: int = 10) -> list:
    """Return list of voice strings used in the last n posts."""
    log = _load_log()
    if not log:
        return []
    recent = log[-n:]
    voices = []
    for entry in recent:
        voice = entry.get("voice", entry.get("style", entry.get("tone", "")))
        if voice:
            voices.append(str(voice))
    return voices


def should_post_now() -> bool:
    """Return True if current UTC time is in a peak or good engagement window."""
    now = datetime.now(timezone.utc)
    return _window_label(now) in ("peak", "good")


def engagement_context_str() -> str:
    """Return a formatted string for prompt injection."""
    try:
        timing = get_best_post_time()
        recent_voices = get_recent_voices(10)
        voice_str = ", ".join(recent_voices[-5:]) if recent_voices else "None recorded"
        peak_str = (
            f"{timing['peak_in_hours']:.1f}h"
            if timing["peak_in_hours"] is not None
            else "N/A"
        )
        return (
            f"[ENGAGEMENT]\n"
            f"  Current Window : {timing['current_window'].upper()}\n"
            f"  Next Peak In   : {peak_str}\n"
            f"  Recommendation : {timing['recommendation']}\n"
            f"  Recent Voices  : {voice_str}\n"
        )
    except Exception:
        return ""

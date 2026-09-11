"""
octo_grok_warm.py -- keep the free Grok brief teaser warm.

/v2/grok/brief/preview is the Grok-citable surface: an assistant hits it mid
answer, so it must return in well under a second. It serves from cache only and
never blocks on a live model call, which means something has to fill the cache.
That is this.

Cold cache is not an outage -- the teaser degrades to a sensor-only read with
annotation_pending set -- but a warm one is the difference between being quoted
with a falsifier and being quoted without one.

Cost shape -- read before changing the schedule. One warm of one asset is TWO
billed grok-4.5 x_search calls: the adversarial annotation in get_grok_brief,
plus the sentiment call get_grok_brief makes underneath it via _sensor_layer.
So the daily bill is: runs/day x assets x 2.

The cadence must therefore track _CACHE_TTL in octo_grok_brief.py, not beat it.
This task ran every 20 minutes against a 30-minute TTL -- 72 runs x 3 assets x 2
= 432 live X searches a day to keep a cache warm that only needed refreshing
half as often. TTL is now 60 min and the task runs every 55.

Run: python octo_grok_warm.py            (default assets)
     python octo_grok_warm.py BTC ETH    (explicit)
     python octo_grok_warm.py --force    (ignore quiet hours)
"""

import sys
from datetime import datetime

_DEFAULT_ASSETS = ["BTC", "ETH", "SOL"]

# Nothing cites the teaser at 3am, and a cold cache is a degraded teaser, not an
# outage. Skipping the overnight window takes roughly a quarter off the bill for
# the hours with the least to gain.
_QUIET_START_HOUR = 0   # inclusive
_QUIET_END_HOUR   = 6   # exclusive


def _in_quiet_hours(now=None) -> bool:
    h = (now or datetime.now()).hour
    return _QUIET_START_HOUR <= h < _QUIET_END_HOUR


def warm(assets=None, force: bool = False) -> dict:
    from octo_grok_brief import get_grok_brief

    assets = [a.upper() for a in (assets or _DEFAULT_ASSETS)]
    out = {"warmed": [], "failed": [], "ts": datetime.now().isoformat(timespec="seconds")}

    if not force and _in_quiet_hours():
        print(f"[GrokWarm] Quiet hours ({_QUIET_START_HOUR:02d}:00-{_QUIET_END_HOUR:02d}:00) -- skipping.")
        out["skipped"] = "quiet_hours"
        return out

    for a in assets:
        try:
            b = get_grok_brief(a, force=True)
            if b.get("annotation"):
                n = len((b["annotation"].get("falsifiers") or []))
                out["warmed"].append(a)
                print(f"[GrokWarm] {a}: cached, {n} falsifier(s)")
            else:
                err = str(b.get("annotation_error") or "no annotation")[:90]
                out["failed"].append({"asset": a, "error": err})
                print(f"[GrokWarm] {a}: NOT cached -- {err}")
        except Exception as e:
            out["failed"].append({"asset": a, "error": f"{type(e).__name__}: {e}"})
            print(f"[GrokWarm] {a}: {type(e).__name__}: {e}")

    print(f"[GrokWarm] done -- {len(out['warmed'])} warmed, {len(out['failed'])} failed")
    return out


if __name__ == "__main__":
    args  = [a for a in sys.argv[1:] if a != "--force"]
    warm(args or None, force="--force" in sys.argv[1:])

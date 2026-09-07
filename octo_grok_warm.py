"""
octo_grok_warm.py -- keep the free Grok brief teaser warm.

/v2/grok/brief/preview is the Grok-citable surface: an assistant hits it mid
answer, so it must return in well under a second. It serves from cache only and
never blocks on a live model call, which means something has to fill the cache.
That is this.

Cold cache is not an outage -- the teaser degrades to a sensor-only read with
annotation_pending set -- but a warm one is the difference between being quoted
with a falsifier and being quoted without one.

Run: python octo_grok_warm.py            (default assets)
     python octo_grok_warm.py BTC ETH    (explicit)
"""

import sys
from datetime import datetime

_DEFAULT_ASSETS = ["BTC", "ETH", "SOL"]


def warm(assets=None) -> dict:
    from octo_grok_brief import get_grok_brief

    assets = [a.upper() for a in (assets or _DEFAULT_ASSETS)]
    out = {"warmed": [], "failed": [], "ts": datetime.now().isoformat(timespec="seconds")}

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
    warm(sys.argv[1:] or None)

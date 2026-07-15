"""
octo_startup_catchup.py -- Post-boot catch-up for missed daily content posts.

Runs once from octo_startup.ps1 after the secrets cache is ready. If the machine
was off or rebooting during a scheduled content-post window (e.g. a Windows Update
reboot overnight), Windows skips the corresponding scheduled task and the post is
lost -- StartWhenAvailable does not reliably catch up across a full power-off.

This detects a missed daily post by reading octo_posted_log.json for today's posts
by type, then fires the runner mode to fill the gap -- at most once per post-type
per day, and only for slots whose scheduled time passed within GRACE_HOURS.
"""
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).parent
PYTHON = sys.executable
POSTED_LOG = PROJECT_DIR / "octo_posted_log.json"
LOG_FILE = PROJECT_DIR / "logs" / "startup_catchup.log"

# How long after a slot's scheduled time it is still worth catching up. A boot at
# 5:31am should still fill the 5:00am (and 3:30am) daily read; a boot at midday
# should not resurrect a pre-dawn post.
GRACE_HOURS = 6

# Daily content slots with a stable 1:1 post-type mapping. Grouped by post_type:
# if ANY slot of a type was missed today (passed + within grace + nothing posted),
# the mode fires ONCE. Times are local (hour, minute). Extend as more daily content
# types warrant boot catch-up.
SLOTS = [
    # (mode, post_type, hour, minute)
    ("daily",   "daily_read", 3, 30),
    ("daily",   "daily_read", 5, 0),
    ("daily",   "daily_read", 19, 0),
    ("monitor", "watchpost",  7, 0),
    ("monitor", "watchpost",  16, 0),
]


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts} {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _posted_types_today():
    """Return the set of post `type`s already posted today (local date)."""
    if not POSTED_LOG.exists():
        return set()
    try:
        data = json.loads(POSTED_LOG.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    entries = data.values() if isinstance(data, dict) else data
    today = datetime.now().date()
    types = set()
    for e in entries:
        pa = e.get("posted_at")
        if not pa:
            continue
        try:
            dt = datetime.fromisoformat(pa)
        except ValueError:
            continue
        if dt.date() == today and e.get("type"):
            types.add(e["type"])
    return types


def main():
    now = datetime.now()
    posted = _posted_types_today()
    log(f"=== Catch-up start (now={now:%H:%M}, posted today: {sorted(posted) or 'none'}) ===")

    # Group slots by post_type; keep the most-recent passed + in-grace slot per type.
    best = {}  # post_type -> (mode, slot_dt)
    for mode, ptype, hh, mm in SLOTS:
        slot_dt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if slot_dt > now:
            continue  # not due yet today
        if (now - slot_dt).total_seconds() / 3600 > GRACE_HOURS:
            continue  # too stale to be worth posting
        cur = best.get(ptype)
        if cur is None or slot_dt > cur[1]:
            best[ptype] = (mode, slot_dt)

    fired = 0
    for ptype, (mode, slot_dt) in sorted(best.items(), key=lambda kv: kv[1][1]):
        # Re-read the log each iteration so a mode that just posted is seen.
        if ptype in _posted_types_today():
            log(f"SKIP {ptype}: already posted today (missed slot {slot_dt:%H:%M})")
            continue
        log(f"CATCH-UP {ptype}: missed slot {slot_dt:%H:%M} -> running --mode {mode}")
        try:
            r = subprocess.run(
                [PYTHON, str(PROJECT_DIR / "octodamus_runner.py"), "--mode", mode],
                cwd=str(PROJECT_DIR), capture_output=True, text=True,
                encoding="utf-8", timeout=600,
            )
            if r.returncode == 0:
                log(f"OK {ptype}: --mode {mode} completed")
                fired += 1
            else:
                log(f"FAIL {ptype}: --mode {mode} rc={r.returncode}: {(r.stderr or '')[-300:]}")
        except subprocess.TimeoutExpired:
            log(f"FAIL {ptype}: --mode {mode} timed out")

    log(f"=== Catch-up done ({fired} post(s) fired) ===")


if __name__ == "__main__":
    main()

"""
octo_acp_monitor.py -- ACP Worker Health Monitor

Runs every 3 hours via Task Scheduler.
Checks:
  1. ACP worker process is running (restarts if not + emails alert)
  2. No funded jobs stuck >30min without submission (emails warning)
  3. Recent activity -- last event <6h old (emails if silent)

Usage:
  python octo_acp_monitor.py
"""

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from octo_health import send_email_alert

PROJECT_DIR  = Path(r"C:\Users\walli\octodamus")
EVENTS_FILE  = PROJECT_DIR / "data" / "acp_events.jsonl"
ALERT_STATE  = PROJECT_DIR / "data" / "acp_monitor_state.json"

STUCK_MIN_MIN        = 8     # funded job older than this = stuck (SLA is 5min)
STUCK_MAX_MIN        = 240   # funded job older than this = already expired by network
SILENCE_THRESHOLD_H  = 2.0   # 2 hours -- ACP marketplace can be quiet for hours normally
ZOMBIE_THRESHOLD_H   = 4.0   # 4 hours silent with process "alive" = hung zombie, force-restart


PID_FILE = PROJECT_DIR / "data" / "acp_worker.pid"

def _acp_is_running() -> bool:
    """Check via PID file -- reliable across user/SYSTEM contexts, no WMI needed."""
    try:
        if not PID_FILE.exists():
            return False
        pid = int(PID_FILE.read_text().strip())
        r = subprocess.run(
            ["powershell", "-Command", f"(Get-Process -Id {pid} -ErrorAction SilentlyContinue) -ne $null"],
            capture_output=True, text=True, timeout=10,
        )
        return r.stdout.strip().lower() == "true"
    except Exception:
        return False


def _restart_acp_worker() -> bool:
    try:
        # Use schtasks -- fire-and-forget, no blocking sleep needed
        r = subprocess.run(
            ["schtasks", "/Run", "/TN", "Octodamus-ACP-Worker"],
            capture_output=True, text=True, timeout=15,
        )
        return "SUCCESS" in r.stdout or r.returncode == 0
    except Exception:
        return False


def _load_state() -> dict:
    try:
        if ALERT_STATE.exists():
            return json.loads(ALERT_STATE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_state(state: dict):
    try:
        ALERT_STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception:
        pass


def _already_alerted(state: dict, key: str, cooldown_h: float = 6.0) -> bool:
    """Return True if we already sent an alert for this key within cooldown_h hours."""
    ts = state.get(key, 0)
    return (datetime.now(timezone.utc).timestamp() - ts) < cooldown_h * 3600


def _mark_alerted(state: dict, key: str):
    state[key] = datetime.now(timezone.utc).timestamp()


def check_stuck_jobs() -> list[str]:
    """Return list of stuck job descriptions (funded >30min, not completed)."""
    if not EVENTS_FILE.exists():
        return []
    try:
        lines = EVENTS_FILE.read_text(encoding="utf-8").splitlines()
        job_status: dict[str, str] = {}
        job_funded_ts: dict[str, float] = {}
        job_ticker: dict[str, str] = {}

        for line in lines:
            if not line.strip():
                continue
            try:
                e = json.loads(line)
            except Exception:
                continue
            job_id = str(e.get("jobId") or "")
            status = e.get("status") or ""
            entry  = e.get("entry") or {}
            ev     = entry.get("event") or {}

            if job_id and status:
                job_status[job_id] = status
            if ev.get("type") == "job.funded":
                ts = entry.get("timestamp", 0)
                if ts:
                    job_funded_ts[job_id] = ts / 1000
            if ev.get("type") in ("job.completed", "job.rejected", "job.cancelled"):
                if job_id:
                    job_status[job_id] = "completed"
            if entry.get("kind") == "message" and entry.get("contentType") == "requirement":
                try:
                    reqs = json.loads(entry.get("content") or "{}")
                    if job_id and reqs.get("ticker"):
                        job_ticker[job_id] = reqs["ticker"]
                except Exception:
                    pass

        now = datetime.now(timezone.utc).timestamp()
        stuck = []
        for jid, st in job_status.items():
            if st == "funded" and jid in job_funded_ts:
                age_min = (now - job_funded_ts[jid]) / 60
                if STUCK_MIN_MIN < age_min < STUCK_MAX_MIN:
                    ticker = job_ticker.get(jid, "unknown ticker")
                    stuck.append(f"Job #{jid} ({ticker}) funded {age_min:.0f}min ago -- no submission")
        return stuck
    except Exception as e:
        return [f"Could not parse events file: {e}"]


def check_last_activity() -> float:
    """
    Return hours since the ACP worker last wrote ANY log line.
    A timestamp-bearing log line means the process is alive and working.
    Silence means the process is dead or the listener disconnected.

    Note: 'Watching for events' only prints on startup. After that the log
    is only written when jobs arrive or replay runs. The ACP marketplace can
    be genuinely quiet for hours — that is normal, not a disconnection.
    """
    log_file = PROJECT_DIR / "logs" / "octo_acp_worker.log"
    if not log_file.exists():
        return -1.0
    try:
        lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        now_ts = datetime.now(timezone.utc).timestamp()
        import time as _time
        import re as _re
        # Walk backwards looking for any timestamped line (format: YYYY-MM-DD HH:MM:SS)
        ts_pattern = _re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})')
        for line in reversed(lines):
            m = ts_pattern.match(line)
            if m:
                try:
                    dt = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                    local_ts = _time.mktime(dt.timetuple())
                    return (now_ts - local_ts) / 3600
                except Exception:
                    pass
        return 99.0  # log exists but no parseable timestamp
    except Exception:
        return -1.0


def run():
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"[ACP Monitor] {now_str}")

    state = _load_state()
    issues = []

    # --- Check 1: process running ---
    if _acp_is_running():
        print("[OK ] ACP worker running")
    else:
        print("[FAIL] ACP worker NOT running -- attempting restart")
        restarted = _restart_acp_worker()
        if restarted:
            print("[OK ] ACP worker restarted successfully")
            _mark_alerted(state, "worker_down")
        else:
            print("[FAIL] ACP worker restart FAILED")
            issues.append("ACP worker is DOWN and restart failed")
            if not _already_alerted(state, "worker_down_hard", cooldown_h=3):
                send_email_alert(
                    subject=f"[Octodamus URGENT] ACP Worker DOWN -- restart FAILED {now_str}",
                    body=(
                        f"ACP Worker is DOWN and could not be restarted at {now_str}.\n\n"
                        f"Incoming agent jobs are NOT being processed.\n"
                        f"Manual intervention required.\n\n"
                        f"To fix:\n"
                        f"  cd C:\\Users\\walli\\octodamus\n"
                        f"  python octo_acp_worker.py\n\n"
                        f"Log: C:\\Users\\walli\\octodamus\\logs\\octo_acp_worker.log"
                    )
                )
                _mark_alerted(state, "worker_down_hard")

    # --- Check 2: stuck funded jobs ---
    stuck = check_stuck_jobs()
    if stuck:
        print(f"[WARN] {len(stuck)} stuck funded job(s):")
        for s in stuck:
            print(f"       {s}")
        # Track per-job-ID so the same job never fires a second alert.
        # Extract job IDs from the description strings ("Job #NNNN ...").
        import re as _re
        alerted_jobs = state.setdefault("alerted_job_ids", {})
        now_ts = datetime.now(timezone.utc).timestamp()
        new_stuck = []
        for s in stuck:
            m = _re.search(r"Job #(\d+)", s)
            jid = m.group(1) if m else s
            if now_ts - alerted_jobs.get(jid, 0) > 48 * 3600:
                new_stuck.append((jid, s))
        if new_stuck:
            for jid, _ in new_stuck:
                alerted_jobs[jid] = now_ts
            send_email_alert(
                subject=f"[Octodamus WARNING] {len(new_stuck)} ACP job(s) stuck -- {now_str}",
                body=(
                    f"The following ACP jobs are funded but have not been submitted:\n\n"
                    + "\n".join(f"  - {s}" for _, s in new_stuck)
                    + f"\n\nThese customers paid $1 USDC each and are waiting for a response.\n"
                    f"The worker will attempt to submit them on next restart.\n\n"
                    f"If this persists, restart: schtasks /run /tn Octodamus-ACP-Worker\n"
                    f"Log: C:\\Users\\walli\\octodamus\\logs\\octo_acp_worker.log"
                )
            )
        else:
            print("[OK ] Stuck jobs already alerted -- no repeat email")
    else:
        print("[OK ] No stuck funded jobs")

    # --- Check 3: last activity ---
    # Silent log is NORMAL when no jobs arrive -- the ACP marketplace can go hours between jobs.
    # Only restart if the process is confirmed dead. Never restart a live process just because it's quiet.
    hours_silent = check_last_activity()
    if hours_silent < 0:
        print("[WARN] Events file missing")
    elif hours_silent > SILENCE_THRESHOLD_H:
        mins_silent = hours_silent * 60
        worker_alive = _acp_is_running()
        if worker_alive:
            if hours_silent > ZOMBIE_THRESHOLD_H:
                # Process appears alive but log has been silent 4+ hours -- hung zombie. Force-restart.
                print(f"[WARN] ACP worker ZOMBIE detected: process alive but log silent {mins_silent:.0f}min -- force-restarting")
                try:
                    result = subprocess.run(
                        ["powershell", "-Command",
                         "Get-Process python* | ForEach-Object { "
                         "$id = $_.Id; "
                         "$cmd = (Get-WmiObject Win32_Process -Filter \"ProcessId=$id\").CommandLine; "
                         "if ($cmd -like '*octo_acp_worker*') { Stop-Process -Id $id -Force } }"],
                        capture_output=True, text=True, timeout=15,
                    )
                except Exception as _e:
                    print(f"[WARN] Zombie kill attempt error: {_e}")
                time.sleep(3)
                restarted = _restart_acp_worker()
                print(f"[{'OK ' if restarted else 'ERR'}] Zombie restart: {'success' if restarted else 'FAILED'}")
                if not _already_alerted(state, "zombie", cooldown_h=6):
                    send_email_alert(
                        subject=f"[Octodamus] ACP worker ZOMBIE killed + restarted {'OK' if restarted else 'FAILED'} -- {now_str}",
                        body=(
                            f"ACP worker process was alive but log had been silent for {mins_silent:.0f} minutes.\n"
                            f"Zombie detected at >{ZOMBIE_THRESHOLD_H:.0f}h silence threshold.\n\n"
                            f"Force-killed + restarted: {'SUCCESS' if restarted else 'FAILED'}\n\n"
                            f"Log: C:\\Users\\walli\\octodamus\\logs\\octo_acp_worker.log"
                        )
                    )
                    _mark_alerted(state, "zombie")
            else:
                # Genuinely quiet marketplace -- no action
                print(f"[OK ] ACP log silent {mins_silent:.0f}min but worker process is alive -- no action (quiet marketplace)")
        else:
            # Process is dead AND log is old -- genuine outage, restart
            print(f"[WARN] ACP worker dead + log silent {mins_silent:.0f}min -- restarting")
            restarted = _restart_acp_worker()
            print(f"[{'OK ' if restarted else 'ERR'}] ACP worker restart: {'success' if restarted else 'FAILED'}")
            if not _already_alerted(state, "silence", cooldown_h=6):
                send_email_alert(
                    subject=f"[Octodamus] ACP worker down + silent {mins_silent:.0f}min -- restarted {'OK' if restarted else 'FAILED'} -- {now_str}",
                    body=(
                        f"ACP worker was NOT running and log had been silent for {mins_silent:.0f} minutes.\n\n"
                        f"Auto-restart: {'SUCCESS' if restarted else 'FAILED -- manual intervention needed'}\n\n"
                        f"Log: C:\\Users\\walli\\octodamus\\logs\\octo_acp_worker.log"
                    )
                )
                _mark_alerted(state, "silence")
    else:
        print(f"[OK ] Last ACP event {hours_silent:.1f}h ago")

    _save_state(state)
    print(f"[ACP Monitor] Done. Issues: {len(issues)}")
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(run())

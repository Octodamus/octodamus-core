"""
octo_botcoin_monitor.py -- BOTCOIN Miner Health Monitor

Runs every 3 hours via Task Scheduler.
Checks:
  1. Miner process is running (restarts if not, emails if restart fails)
  2. Active epoch has recent solve activity (warns if silent)
  3. Coordinator reachable

Usage:
  python octo_botcoin_monitor.py
"""

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from octo_health import send_email_alert

PROJECT_DIR   = Path(r"C:\Users\walli\octodamus")
CREDITS_FILE  = PROJECT_DIR / "data" / "botcoin_credits.json"
ALERT_STATE   = PROJECT_DIR / "data" / "botcoin_monitor_state.json"
MINER_LOG     = PROJECT_DIR / "logs" / "botcoin_miner.log"
TASK_NAME     = "Octodamus-BOTCOIN-Miner"

SILENCE_THRESHOLD_H = 6   # no log activity for this long = warn
COORD_URL = "https://coordinator.agentmoney.net"


def _miner_is_running() -> bool:
    try:
        r = subprocess.run(
            ["powershell", "-Command",
             "Get-WmiObject Win32_Process -Filter \"Name='python.exe'\" | "
             "ForEach-Object { $_.CommandLine } | "
             "Where-Object { $_ -like '*botcoin*' }"],
            capture_output=True, text=True, timeout=20
        )
        return "octo_boto_botcoin" in r.stdout.lower()
    except Exception:
        return False


def _restart_miner() -> bool:
    try:
        subprocess.run(
            ["powershell", "-Command", f"schtasks /run /tn \"{TASK_NAME}\""],
            capture_output=True, text=True, timeout=15
        )
        time.sleep(20)
        return _miner_is_running()
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
    ts = state.get(key, 0)
    return (datetime.now(timezone.utc).timestamp() - ts) < cooldown_h * 3600


def _mark_alerted(state: dict, key: str):
    state[key] = datetime.now(timezone.utc).timestamp()


def _hours_since_log_activity() -> float:
    """Return hours since miner last wrote to its log. -1 if log missing."""
    if not MINER_LOG.exists():
        return -1.0
    try:
        mtime = MINER_LOG.stat().st_mtime
        return (datetime.now(timezone.utc).timestamp() - mtime) / 3600
    except Exception:
        return -1.0


def _get_credits_summary() -> dict:
    """Load credits log and return summary."""
    try:
        data = json.loads(CREDITS_FILE.read_text(encoding="utf-8"))
        total_solves  = sum(e.get("solves", 0)  for e in data.values())
        total_passes  = sum(e.get("passes", 0)  for e in data.values())
        total_credits = sum(e.get("credits", 0) for e in data.values())
        epochs = sorted(data.keys(), key=lambda x: int(x))
        latest_epoch = epochs[-1] if epochs else None
        latest_data  = data[latest_epoch] if latest_epoch else {}
        return {
            "total_solves":  total_solves,
            "total_passes":  total_passes,
            "total_credits": total_credits,
            "epochs":        epochs,
            "latest_epoch":  latest_epoch,
            "latest_solves": latest_data.get("solves", 0),
            "latest_passes": latest_data.get("passes", 0),
            "latest_credits": latest_data.get("credits", 0),
        }
    except Exception as e:
        return {"error": str(e)}


def _get_current_epoch() -> dict:
    """Query coordinator for current epoch info."""
    try:
        import requests
        from pathlib import Path as _P
        auth = json.loads((_P(__file__).parent / "data" / "botcoin_auth.json").read_text(encoding="utf-8"))
        token = auth.get("token", "")
        headers = {"Authorization": f"Bearer {token}"}
        r = requests.get(f"{COORD_URL}/api/epoch", headers=headers, timeout=8)
        if r.status_code == 200:
            return r.json()
        # Try status endpoint
        r2 = requests.get(f"{COORD_URL}/api/status", headers=headers, timeout=8)
        if r2.status_code == 200:
            return r2.json()
    except Exception:
        pass
    return {}


def run():
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"[BOTCOIN Monitor] {now_str}")

    state = _load_state()

    # --- Check 1: process running ---
    if _miner_is_running():
        print("[OK ] BOTCOIN miner running")
    else:
        print("[FAIL] BOTCOIN miner NOT running -- attempting restart")
        restarted = _restart_miner()
        if restarted:
            print("[OK ] BOTCOIN miner restarted successfully")
        else:
            print("[FAIL] BOTCOIN miner restart FAILED")
            if not _already_alerted(state, "miner_down", cooldown_h=3):
                send_email_alert(
                    subject=f"[Octodamus URGENT] BOTCOIN Miner DOWN -- restart FAILED {now_str}",
                    body=(
                        f"BOTCOIN miner is DOWN and could not be restarted at {now_str}.\n\n"
                        f"Mining is halted. Epoch rewards are being missed.\n\n"
                        f"To fix manually:\n"
                        f"  schtasks /run /tn Octodamus-BOTCOIN-Miner\n"
                        f"  OR: python C:\\Users\\walli\\octodamus\\octo_boto_botcoin.py --loop\n\n"
                        f"Log: {MINER_LOG}"
                    )
                )
                _mark_alerted(state, "miner_down")

    # --- Check 2: log activity (silence detection) ---
    hours_silent = _hours_since_log_activity()
    if hours_silent < 0:
        print("[WARN] Miner log not found -- logging may not be configured")
    elif hours_silent > SILENCE_THRESHOLD_H:
        print(f"[WARN] Miner log silent for {hours_silent:.1f}h")
        if not _already_alerted(state, "miner_silent", cooldown_h=6):
            send_email_alert(
                subject=f"[Octodamus WARNING] BOTCOIN miner silent {hours_silent:.0f}h -- {now_str}",
                body=(
                    f"BOTCOIN miner log has not been updated in {hours_silent:.1f} hours.\n\n"
                    f"The process may be running but stuck or rate-limited.\n\n"
                    f"Check: schtasks /query /tn Octodamus-BOTCOIN-Miner\n"
                    f"Log: {MINER_LOG}"
                )
            )
            _mark_alerted(state, "miner_silent")
    else:
        print(f"[OK ] Miner log updated {hours_silent:.1f}h ago")

    # --- Check 3: credits summary ---
    summary = _get_credits_summary()
    if "error" not in summary:
        print(f"[OK ] Credits log: epoch {summary['latest_epoch']} | "
              f"{summary['latest_solves']} solves / {summary['latest_passes']} passes / "
              f"{summary['latest_credits']:,} credits")
        print(f"       All-time: {summary['total_solves']} solves / {summary['total_credits']:,} credits")
    else:
        print(f"[WARN] Credits log error: {summary['error']}")

    _save_state(state)
    print(f"[BOTCOIN Monitor] Done.")
    return 0


if __name__ == "__main__":
    sys.exit(run())

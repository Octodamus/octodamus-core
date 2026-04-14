"""
octo_watchdog.py — Process watchdog for OctoBoto and Octodamus (#8)

Monitors critical processes and restarts them if they crash or are missing.
Designed to run as a Windows Task Scheduler job at startup and every 5 minutes.

Processes monitored:
  - octo_boto.py          (OctoBoto trading bot)
  - octodamus_runner.py   (Octodamus oracle engine)
  - telegram_bot.py       (Octodamus Telegram interface)
  - octo_api_server.py    (API server)

On power failure: Task Scheduler "run as soon as possible after missed start"
ensures the watchdog fires immediately on reboot, which restarts everything.

The watchdog does NOT handle Bitwarden unlock — secrets are loaded from the
.octo_secrets cache file which persists across reboots. This is intentional:
the watchdog must work without user interaction.

Usage:
  python octo_watchdog.py           # Run one check cycle
  python octo_watchdog.py --setup   # Install as Task Scheduler job
  python octo_watchdog.py --status  # Show process status
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

PYTHON      = sys.executable
PROJECT_DIR = Path(r"C:\Users\walli\octodamus")
LOG_FILE    = PROJECT_DIR / "logs" / "octo_watchdog.log"
STATE_FILE  = PROJECT_DIR / "data" / "watchdog_state.json"

PROCESSES = [
    {
        "name":    "OctoBoto",
        "script":  "octo_boto.py",
        "args":    [],
        "critical": True,
        "cooldown": 30,   # seconds between restart attempts
    },
    {
        "name":    "OctodamusRunner",
        "script":  "octodamus_runner.py",
        "args":    ["--mode", "mentions"],
        "critical": True,
        "cooldown": 30,
    },
    {
        "name":    "TelegramBot",
        "script":  "telegram_bot.py",
        "args":    [],
        "critical": True,
        "cooldown": 30,
    },
    {
        "name":    "APIServer",
        "script":  "octo_api_server.py",
        "args":    [],
        "critical": False,
        "cooldown": 60,
    },
]

# ── Logging ───────────────────────────────────────────────────────────────────

LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger("watchdog")


# ── State ─────────────────────────────────────────────────────────────────────

def _load_state() -> dict:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


# ── Process detection ─────────────────────────────────────────────────────────

def _find_pid(script_name: str) -> int:
    """Return PID of a running python process running script_name, or 0."""
    try:
        result = subprocess.run(
            ["wmic", "process", "where", "name='python.exe'",
             "get", "ProcessId,CommandLine", "/format:csv"],
            capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.splitlines():
            if script_name in line:
                parts = line.strip().split(",")
                if len(parts) >= 3:
                    try:
                        return int(parts[-1].strip())
                    except ValueError:
                        pass
    except Exception as e:
        log.warning(f"[Watchdog] PID scan error: {e}")
    return 0


def _is_running(pid: int) -> bool:
    if pid == 0:
        return False
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=5
        )
        return str(pid) in result.stdout
    except Exception:
        return False


# ── Restart ───────────────────────────────────────────────────────────────────

def _restart_process(proc: dict, state: dict) -> bool:
    """Attempt to start a process. Returns True if launched."""
    name   = proc["name"]
    script = PROJECT_DIR / proc["script"]
    args   = proc["args"]

    if not script.exists():
        log.error(f"[Watchdog] {name}: script not found at {script}")
        return False

    # Cooldown check
    last_restart = state.get(f"{name}_last_restart", 0)
    if time.time() - last_restart < proc["cooldown"]:
        log.info(f"[Watchdog] {name}: in cooldown, skipping restart")
        return False

    try:
        cmd = [PYTHON, str(script)] + args
        subprocess.Popen(
            cmd,
            cwd=str(PROJECT_DIR),
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        state[f"{name}_last_restart"] = time.time()
        state[f"{name}_restart_count"] = state.get(f"{name}_restart_count", 0) + 1
        log.warning(f"[Watchdog] {name}: restarted (total: {state[f'{name}_restart_count']})")
        return True
    except Exception as e:
        log.error(f"[Watchdog] {name}: restart failed: {e}")
        return False


# ── Main check cycle ──────────────────────────────────────────────────────────

def run_check() -> dict:
    """Run one check cycle. Returns status dict."""
    state = _load_state()
    results = {}

    for proc in PROCESSES:
        name   = proc["name"]
        script = proc["script"]

        pid = _find_pid(script)
        running = _is_running(pid)

        if running:
            state[f"{name}_pid"] = pid
            results[name] = "running"
            log.info(f"[Watchdog] {name}: OK (PID {pid})")
        else:
            log.warning(f"[Watchdog] {name}: NOT RUNNING — attempting restart")
            restarted = _restart_process(proc, state)
            results[name] = "restarted" if restarted else "failed"
            time.sleep(3)   # Brief pause between restarts

    state["last_check"] = datetime.now(timezone.utc).isoformat()
    _save_state(state)
    return results


def print_status():
    """Print current process status to stdout."""
    state = _load_state()
    print("\n" + "=" * 50)
    print(f"  OCTODAMUS WATCHDOG STATUS")
    print(f"  Last check: {state.get('last_check', 'never')}")
    print("=" * 50)
    for proc in PROCESSES:
        name   = proc["name"]
        script = proc["script"]
        pid    = _find_pid(script)
        status = "✅ RUNNING" if _is_running(pid) else "❌ DOWN"
        restarts = state.get(f"{name}_restart_count", 0)
        print(f"  {status:15} {name} (restarts: {restarts})")
    print("=" * 50 + "\n")


# ── Task Scheduler setup ──────────────────────────────────────────────────────

TASK_NAME = "OctodamusWatchdog"

def setup_task_scheduler():
    """Register the watchdog as a Windows Task Scheduler job."""
    script_path = Path(__file__).resolve()
    cmd = (
        f'schtasks /Create /F /TN "{TASK_NAME}" '
        f'/TR "{PYTHON} {script_path}" '
        f'/SC ONSTART /DELAY 0001:00 '          # 1 minute after boot
        f'/RI 5 /DU 9999:59 '                   # Repeat every 5 min indefinitely
        f'/RU SYSTEM '
        f'/SETTINGS /RESTARTCOUNT:10 /RESTARTINTERVAL:PT1M '
        f'/IT'
    )
    # Use PowerShell for richer task config
    ps = f"""
$action  = New-ScheduledTaskAction -Execute '{PYTHON}' -Argument '{script_path}' -WorkingDirectory '{PROJECT_DIR}'
$trigger = @(
    (New-ScheduledTaskTrigger -AtStartup),
    (New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Minutes 5) -Once -At (Get-Date))
)
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
    -RestartCount 10 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -RunOnlyIfNetworkAvailable $false `
    -StartWhenAvailable $true
Register-ScheduledTask -TaskName '{TASK_NAME}' -Action $action -Trigger $trigger -Settings $settings -RunLevel Highest -Force
Write-Host "Watchdog task registered."
"""
    ps_path = PROJECT_DIR / "_setup_watchdog_task.ps1"
    ps_path.write_text(ps, encoding="utf-8")
    result = subprocess.run(
        ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(ps_path)],
        capture_output=True, text=True
    )
    ps_path.unlink(missing_ok=True)
    if result.returncode == 0:
        print(f"✅ Task '{TASK_NAME}' registered — watchdog runs at startup + every 5 min")
        print("   On power failure: processes restart automatically on reboot.")
    else:
        print(f"❌ Task registration failed:\n{result.stderr}")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--setup",  action="store_true", help="Install Task Scheduler job")
    parser.add_argument("--status", action="store_true", help="Show process status")
    args = parser.parse_args()

    if args.setup:
        setup_task_scheduler()
    elif args.status:
        print_status()
    else:
        log.info("[Watchdog] Starting check cycle...")
        results = run_check()
        log.info(f"[Watchdog] Done: {results}")

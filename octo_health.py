"""
octo_health.py — Octodamus System Health Check

Checks all running components and reports status.
Called at boot (via octo_unlock.ps1) and at 8pm daily via Task Scheduler.

Usage:
 python octo_health.py       # manual check
 python octo_health.py boot     # boot context
 python octo_health.py evening   # 8pm context
"""

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

# ── Config ────────────────────────────────────────────────────────────────────

PROJECT_DIR = Path(r"C:\Users\walli\octodamus")
SECRETS_FILE = PROJECT_DIR / ".octo_secrets"
QUEUE_FILE  = PROJECT_DIR / "octo_post_queue.json"
ACP_SCRIPT  = "/home/walli/octodamus/start_acp.sh"

EXPECTED_TASKS = [
  "Octodamus-DailyRead",
  "Octodamus-DailyRead-1pm",
  "Octodamus-Monitor-7am",
  "Octodamus-Monitor-115pm",
  "Octodamus-Monitor-6pm",
  "Octodamus-Journal",
  "Octodamus-Wisdom",
  "Octodamus-DeepDive-Mon",
  "Octodamus-DeepDive-Wed",
  "Octodamus-Congress",
  "Octodamus-Scorecard",
  "Octodamus-AutoResolve",
  "Octodamus-Engage-8pm",
  "Octodamus-Engage-3pm",
  "Octodamus-Engage-4pm",
  "Octodamus-Engage-8pm",
  "Octodamus-API-Server",
  "Octodamus-ACP-Worker",
  "Octodamus-Cloudflared",
  "Octodamus-XStats",
  "Octodamus-HealthCheck",
]

# ── State — reset at start of each run ───────────────────────────────────────
# These are populated fresh each call to run_health_check()
_passed = []
_warned = []
_failed = []


def _ok(msg):
  _passed.append(msg)
  print(f" [OK ] {msg}")

def _warn(msg):
  _warned.append(msg)
  print(f" [WARN] {msg}")

def _fail(msg):
  _failed.append(msg)
  print(f" [FAIL] {msg}")


# ── Discord ───────────────────────────────────────────────────────────────────

def _get_webhook() -> str:
  try:
    raw = json.loads(SECRETS_FILE.read_text(encoding="utf-8"))
    data = raw.get("secrets", raw)
    return data.get("DISCORD_WEBHOOK_URL", "")
  except Exception:
    return ""

def _discord(webhook: str, message: str):
  if not webhook:
    return
  try:
    httpx.post(webhook, json={"content": message}, timeout=5)
  except Exception:
    pass


# ── Checks ────────────────────────────────────────────────────────────────────

def _check_python_processes():
  """Check API server, Telegram bot, OctoBoto are running."""
  try:
    # Use tasklist which is simpler and more reliable than WMI
    result = subprocess.run(
      ["powershell", "-Command",
       "Get-Process python* | ForEach-Object { "
       "$id = $_.Id; "
       "(Get-WmiObject Win32_Process -Filter \"ProcessId=$id\").CommandLine "
       "} | Out-String"],
      capture_output=True, text=True, timeout=20
    )
    running = result.stdout.lower()
  except Exception as e:
    _warn(f"Python process check error: {e}")
    return

  for filename, label in [
    ("octo_api_server", "API server"),
    ("telegram_bot",  "Telegram bot"),
    ("octo_boto",    "OctoBoto"),
  ]:
    if filename in running:
      _ok(label)
    else:
      _fail(f"{label} not running")


def _check_cloudflared():
  try:
    result = subprocess.run(
      ["powershell", "-Command",
       "(Get-Process cloudflared -ErrorAction SilentlyContinue | Measure-Object).Count"],
      capture_output=True, text=True, timeout=10
    )
    count = int(result.stdout.strip() or "0")
    if count > 0:
      _ok("Cloudflare tunnel")
    else:
      _fail("Cloudflare tunnel not running")
  except Exception as e:
    _warn(f"Cloudflared check error: {e}")


def _acp_is_running() -> bool:
  """Check ACP worker - runs as WSL process + Python inside Ubuntu."""
  try:
    result = subprocess.run(
      ["wsl", "-d", "Ubuntu", "--", "bash", "-c",
       "ps aux | grep -c [o]cto_acp_worker"],
      capture_output=True, text=True, timeout=12
    )
    return int(result.stdout.strip() or "0") > 0
  except Exception:
    return False


def _check_acp_worker() -> bool:
  if _acp_is_running():
    _ok("ACP worker")
    return True
  else:
    _fail("ACP worker not running")
    return False


def _restart_acp_worker() -> bool:
  print(" [....] Restarting ACP worker...")

  # Start it
  try:
    subprocess.Popen(
      ["powershell", "-ExecutionPolicy", "Bypass", "-File",
       r"C:\Users\walli\octodamus\run_acp_worker.ps1"],
      creationflags=0x00000008  # DETACHED_PROCESS
    )
    time.sleep(12)
  except Exception as e:
    _fail(f"ACP restart error: {e}")
    return False

  # Verify
  if _acp_is_running():
    _ok("ACP worker restarted successfully")
    return True
  else:
    _fail("ACP worker restart failed — check logs")
    return False


def _check_api_endpoints():
  for url, label in [
    ("https://api.octodamus.com/",        "API root"),
    # Fear & Greed skipped - slow external fetch
    ("https://api.octodamus.com/api/xstats",   "XStats"),
  ]:
    try:
      r = httpx.get(url, timeout=15)
      if r.status_code == 200:
        _ok(f"{label} (HTTP 200)")
      else:
        _fail(f"{label} returned HTTP {r.status_code}")
    except Exception as e:
      _fail(f"{label} unreachable: {e}")


def _check_secrets_cache():
  if not SECRETS_FILE.exists():
    _fail("Secrets cache missing — run octo_unlock.ps1")
    return
  try:
    raw = json.loads(SECRETS_FILE.read_text(encoding="utf-8"))
    data = raw.get("secrets", raw)
    count = len(data)
    saved_at = raw.get("saved_at", "")

    if count >= 20:
      _ok(f"Secrets cache ({count} keys)")
    else:
      _warn(f"Secrets cache only has {count} keys (expected 25)")

    if saved_at:
      dt = datetime.fromisoformat(saved_at.replace("Z", "+00:00"))
      age_h = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
      if age_h < 23:
        _ok(f"Secrets cache fresh ({age_h:.1f}h old)")
      else:
        _warn(f"Secrets cache is {age_h:.1f}h old — run octo_unlock.ps1")
  except Exception as e:
    _warn(f"Secrets cache read error: {e}")


def _check_post_queue():
  if not QUEUE_FILE.exists():
    _ok("Post queue (empty)")
    return
  try:
    queue = json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
    queued = [p for p in queue if p.get("status") == "queued"]
    failed = [p for p in queue if p.get("status") == "failed"]
    if failed:
      _warn(f"Post queue: {len(failed)} failed post(s)")
    elif len(queued) > 6:
      _warn(f"Post queue: {len(queued)} posts backlogged")
    else:
      _ok(f"Post queue ({len(queued)} queued, {len(failed)} failed)")
  except Exception as e:
    _warn(f"Post queue read error: {e}")


def _check_scheduled_tasks():
  try:
    result = subprocess.run(
      ["schtasks", "/Query", "/FO", "CSV"],
      capture_output=True, text=True, timeout=20
    )
    output = result.stdout
    missing = [t for t in EXPECTED_TASKS if t not in output]
    if missing:
      _fail(f"Missing tasks ({len(missing)}): {', '.join(missing)}")
    else:
      _ok(f"All {len(EXPECTED_TASKS)} scheduled tasks present")
  except Exception as e:
    _warn(f"Task check error: {e}")


# ── Main ──────────────────────────────────────────────────────────────────────

def run_health_check(auto_restart: bool = True, context: str = "manual") -> int:
  # Reset state — critical so repeated calls don't accumulate
  global _passed, _warned, _failed
  _passed, _warned, _failed = [], [], []

  ts = datetime.now().strftime("%Y-%m-%d %H:%M")
  print(f"\n{'='*52}")
  print(f" OCTODAMUS HEALTH CHECK — {ts}")
  print(f" Context: {context.upper()}")
  print(f"{'='*52}")

  _check_python_processes()
  _check_cloudflared()

  acp_ok = _check_acp_worker()
  if not acp_ok and auto_restart:
    _restart_acp_worker()

  _check_api_endpoints()
  _check_secrets_cache()
  _check_post_queue()
  _check_scheduled_tasks()

  print(f"\n{'='*52}")
  print(f" PASSED: {len(_passed)} WARNED: {len(_warned)} FAILED: {len(_failed)}")
  print(f"{'='*52}\n")

  webhook = _get_webhook()

  if _failed:
    msg = (
      f"⚠️ **Octodamus Health — {context.upper()} {ts}**\n"
      f"❌ {len(_failed)} failure(s):\n"
      + "\n".join(f" • {f}" for f in _failed)
    )
    if _warned:
      msg += "\n⚠️ Warnings:\n" + "\n".join(f" • {w}" for w in _warned)
    _discord(webhook, msg)
    return 1

  if _warned:
    msg = (
      f"✅ **Octodamus Health — {context.upper()} {ts}**\n"
      f"All critical systems OK — {len(_warned)} warning(s):\n"
      + "\n".join(f" • {w}" for w in _warned)
    )
    _discord(webhook, msg)
    return 0

  _discord(webhook, f"✅ **Octodamus Health — {context.upper()} {ts}**\nAll {len(_passed)} systems healthy. ")
  return 0


if __name__ == "__main__":
  context = sys.argv[1] if len(sys.argv) > 1 else "manual"
  sys.exit(run_health_check(auto_restart=True, context=context))
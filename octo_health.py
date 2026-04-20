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
import smtplib
import subprocess
import sys
import time
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path

import httpx

# ── Config ────────────────────────────────────────────────────────────────────

PROJECT_DIR = Path(r"C:\Users\walli\octodamus")
SECRETS_FILE = PROJECT_DIR / ".octo_secrets"
QUEUE_FILE  = PROJECT_DIR / "octo_post_queue.json"

EXPECTED_TASKS = [
  # Posting schedule
  "Octodamus-DailyRead",
  "Octodamus-DailyRead-7pm",
  "Octodamus-Monitor-7am",
  "Octodamus-Monitor-4pm",
  "Octodamus-Thread-Mon",
  "Octodamus-Thread-Wed",
  "Octodamus-Format-12pm",
  # Weekly
  "Octodamus-Wisdom",
  "Octodamus-Soul",
  "Octodamus-StrategySunday",
  # Daily background
  "Octodamus-StrategyMonitor",
  "Octodamus-QRT-Scan",
  "Octodamus-Congress",
  "Octodamus-AutoResolve",
  "Octodamus-BotoResolve",
  "Octodamus-Mentions",
  # Infrastructure
  "Octodamus-API-Server",
  "Octodamus-ACP-Worker",
  "Octodamus-Cloudflared",
  "Octodamus-XStats",
  "Octodamus-HealthCheck",
  "Octodamus-GDrive-Backup",
  "Octodamus-FlightSample",
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


# ── Email ─────────────────────────────────────────────────────────────────────

def _get_gmail_creds() -> tuple[str, str]:
  try:
    raw = json.loads(SECRETS_FILE.read_text(encoding="utf-8"))
    data = raw.get("secrets", raw)
    return data.get("GMAIL_USER", ""), data.get("GMAIL_APP_PASSWORD", "")
  except Exception:
    return "", ""

def send_email_alert(subject: str, body: str):
  user, pw = _get_gmail_creds()
  if not user or not pw:
    print(" [WARN] Email not sent — GMAIL_USER/GMAIL_APP_PASSWORD missing")
    return
  try:
    msg = MIMEText(body, "plain")
    msg["Subject"] = subject
    msg["From"]    = user
    msg["To"]      = user
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as s:
      s.starttls()
      s.login(user, pw)
      s.send_message(msg)
    print(f" [OK ] Alert email sent to {user}")
  except Exception as e:
    print(f" [WARN] Email send failed: {e}")


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
  """Check ACP worker - runs as Windows Python process."""
  try:
    result = subprocess.run(
      ["powershell", "-Command",
       "Get-Process python* | ForEach-Object { "
       "$id = $_.Id; "
       "(Get-WmiObject Win32_Process -Filter \"ProcessId=$id\").CommandLine "
       "} | Out-String"],
      capture_output=True, text=True, timeout=20
    )
    return "octo_acp_worker" in result.stdout.lower()
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
    time.sleep(30)
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


def _boto_is_running() -> bool:
  try:
    result = subprocess.run(
      ["powershell", "-Command",
       "Get-Process python* | ForEach-Object { "
       "$id = $_.Id; "
       "(Get-WmiObject Win32_Process -Filter \"ProcessId=$id\").CommandLine "
       "} | Out-String"],
      capture_output=True, text=True, timeout=20
    )
    return "octo_boto" in result.stdout.lower()
  except Exception:
    return False


def _restart_octoboto() -> bool:
  print(" [....] Restarting OctoBoto...")

  # Kill any stale instance
  try:
    subprocess.run(
      ["powershell", "-Command",
       "Get-Process python* | ForEach-Object { "
       "$id = $_.Id; $cmd = (Get-WmiObject Win32_Process -Filter \"ProcessId=$id\").CommandLine; "
       "if ($cmd -like '*octo_boto*') { Stop-Process -Id $id -Force } }"],
      capture_output=True, text=True, timeout=15
    )
    time.sleep(2)
  except Exception:
    pass

  # Start fresh
  try:
    subprocess.Popen(
      [r"C:\Python314\python.exe", r"C:\Users\walli\octodamus\octo_boto.py"],
      cwd=r"C:\Users\walli\octodamus",
      creationflags=0x00000008  # DETACHED_PROCESS
    )
    time.sleep(5)
  except Exception as e:
    _fail(f"OctoBoto restart error: {e}")
    return False

  if _boto_is_running():
    _ok("OctoBoto restarted successfully")
    return True
  else:
    _fail("OctoBoto restart failed — check logs")
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


def _check_acp_stuck_jobs():
  """Warn if any ACP jobs have been funded >30min without completion."""
  events_file = PROJECT_DIR / "data" / "acp_events.jsonl"
  if not events_file.exists():
    return
  try:
    lines = events_file.read_text(encoding="utf-8").splitlines()
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
        if 30 < age_min < 240:  # >30min = stuck, >240min = expired by network, ignore
          ticker = job_ticker.get(jid, "?")
          stuck.append(f"Job #{jid} ({ticker}) funded {age_min:.0f}min ago")
    if stuck:
      _warn(f"ACP stuck funded jobs ({len(stuck)}): {'; '.join(stuck)}")
    else:
      _ok("ACP no stuck jobs")
  except Exception as e:
    _warn(f"ACP stuck job check error: {e}")


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

  boto_ok = _boto_is_running()
  if boto_ok:
    _ok("OctoBoto")
  else:
    _fail("OctoBoto not running")
    if auto_restart:
      _restart_octoboto()

  _check_acp_stuck_jobs()
  _check_api_endpoints()
  _check_secrets_cache()
  _check_post_queue()
  _check_scheduled_tasks()

  print(f"\n{'='*52}")
  print(f" PASSED: {len(_passed)} WARNED: {len(_warned)} FAILED: {len(_failed)}")
  print(f"{'='*52}\n")

  webhook = _get_webhook()

  if _failed:
    failures = "\n".join(f"  - {f}" for f in _failed)
    warnings = ("\nWarnings:\n" + "\n".join(f"  - {w}" for w in _warned)) if _warned else ""
    discord_msg = (
      f"⚠️ **Octodamus Health — {context.upper()} {ts}**\n"
      f"❌ {len(_failed)} failure(s):\n"
      + "\n".join(f" • {f}" for f in _failed)
    )
    if _warned:
      discord_msg += "\n⚠️ Warnings:\n" + "\n".join(f" • {w}" for w in _warned)
    _discord(webhook, discord_msg)
    send_email_alert(
      subject=f"[Octodamus ALERT] {len(_failed)} failure(s) — {context.upper()} {ts}",
      body=f"Octodamus Health Check — {context.upper()}\n{ts}\n\nFAILURES:\n{failures}{warnings}\n\nCheck logs: C:\\Users\\walli\\octodamus\\logs\\"
    )
    return 1

  if _warned:
    discord_msg = (
      f"✅ **Octodamus Health — {context.upper()} {ts}**\n"
      f"All critical systems OK — {len(_warned)} warning(s):\n"
      + "\n".join(f" • {w}" for w in _warned)
    )
    _discord(webhook, discord_msg)
    # Only email warnings if they mention ACP (high priority)
    acp_warns = [w for w in _warned if "acp" in w.lower() or "stuck" in w.lower()]
    if acp_warns:
      send_email_alert(
        subject=f"[Octodamus WARNING] ACP issue — {ts}",
        body=f"Octodamus Health Check — {context.upper()}\n{ts}\n\nACP WARNINGS:\n" + "\n".join(f"  - {w}" for w in acp_warns)
      )
    return 0

  _discord(webhook, f"✅ **Octodamus Health — {context.upper()} {ts}**\nAll {len(_passed)} systems healthy. ")
  return 0


if __name__ == "__main__":
  context = sys.argv[1] if len(sys.argv) > 1 else "manual"
  sys.exit(run_health_check(auto_restart=True, context=context))
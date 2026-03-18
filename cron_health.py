#!/usr/bin/env python3
"""
cron_health.py
🐙 Octodamus — Pre-flight health check.
"""

import subprocess
import sys
import os
import json
from pathlib import Path
from datetime import datetime

OCTO_DIR = Path("/home/walli/octodamus")
LOG_DIR  = OCTO_DIR / "logs"

PASS = "✅"
FAIL = "❌"

results = []

def check(label, ok, detail="", fix=""):
    results.append({"ok": ok, "label": label})
    icon = PASS if ok else FAIL
    status = f"{icon} {label}"
    if detail: status += f"  →  {detail}"
    print(status)
    if not ok and fix: print(f"   FIX: {fix}")

def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    return r.returncode, r.stdout.strip(), r.stderr.strip()

print()
print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
print(f"🐙 Octodamus Health Check — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
print()

print("[ Directory Structure ]")
check("Octodamus dir exists", OCTO_DIR.exists(), str(OCTO_DIR))
check("logs/ dir exists",     LOG_DIR.exists(),  str(LOG_DIR), f"mkdir -p {LOG_DIR}")
check("run.sh exists",        (OCTO_DIR / "run.sh").exists(), "", "Copy run.sh to octodamus/")
check("run.sh executable",    os.access(OCTO_DIR / "run.sh", os.X_OK), "", f"chmod +x {OCTO_DIR}/run.sh")
print()

print("[ Core Python Files ]")
for f in ["octodamus_runner.py","bitwarden.py","financial_data_client.py","octo_eyes_market.py","octo_x_queue.py","octo_treasury_balance.py","octo_market_snapshot.py"]:
    path = OCTO_DIR / f
    check(f, path.exists(), "present" if path.exists() else "MISSING")
print()

print("[ Python Environment ]")
rc, out, _ = run(["python3", "--version"])
check("python3 available", rc == 0, out)
for pkg in ["anthropic","httpx","yfinance","requests"]:
    rc, _, _ = run(["python3", "-c", f"import {pkg.replace('-','_')}"])
    check(f"  pkg: {pkg}", rc == 0, "", f"pip3 install {pkg} --break-system-packages")
print()

print("[ Bitwarden ]")
rc, out, _ = run(["which", "bw"])
check("bw CLI installed", rc == 0, out if rc==0 else "", "Install: https://bitwarden.com/help/cli/")
bw_session = os.environ.get("BW_SESSION","")
check("BW_SESSION set", bool(bw_session), "set" if bw_session else "not set — OK for scheduled runs via run.sh")
print()

print("[ Network ]")
rc, _, _ = run(["ping","-c","1","-W","3","8.8.8.8"])
check("Ping 8.8.8.8", rc==0, "", "Check network")
rc, out, _ = run(["cat","/etc/resolv.conf"])
check("DNS → 8.8.8.8 in resolv.conf", "8.8.8.8" in out, "", "echo 'nameserver 8.8.8.8' | sudo tee /etc/resolv.conf")
print()

print("[ Permissions ]")
try:
    test_file = LOG_DIR / ".write_test"
    test_file.write_text("ok")
    test_file.unlink()
    check("logs/ writable", True)
except Exception as e:
    check("logs/ writable", False, str(e), f"chmod 755 {LOG_DIR}")
print()

passed = sum(1 for r in results if r["ok"])
failed = sum(1 for r in results if not r["ok"])
print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
if failed == 0:
    print(f"🐙 All {passed} checks passed — Octodamus is ready.")
else:
    print(f"Results: {passed}/{len(results)} passed   {failed} issue(s) need fixing")
    for r in results:
        if not r["ok"]: print(f"  {FAIL} {r['label']}")
print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
sys.exit(0 if failed == 0 else 1)

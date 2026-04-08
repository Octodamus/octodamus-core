"""
octo_autodeploy.py
Octodamus — Auto-deploy service

Polls GitHub every 15 minutes for new commits on octodamus-core.
If new code is found: git pull → nssm restart OctoDataAPI.
Runs as its own nssm service: OctoAutoDeploy

Install:
  nssm install OctoAutoDeploy "C:\\Python311\\python.exe" "C:\\Users\\walli\\octodamus\\octo_autodeploy.py"
  nssm set OctoAutoDeploy AppDirectory C:\\Users\\walli\\octodamus
  nssm start OctoAutoDeploy
"""

import subprocess
import time
import logging
from datetime import datetime
from pathlib import Path

REPO_DIR     = Path(__file__).parent
LOG_FILE     = REPO_DIR / "data" / "autodeploy.log"
POLL_SECONDS = 900   # 15 minutes
SERVICE_NAME = "OctoDataAPI"

logging.basicConfig(
    filename  = str(LOG_FILE),
    level     = logging.INFO,
    format    = "%(asctime)s [AutoDeploy] %(message)s",
    datefmt   = "%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("autodeploy")


def _run(cmd: list, cwd=None) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd or REPO_DIR, timeout=60)
        return r.returncode, (r.stdout + r.stderr).strip()
    except Exception as e:
        return 1, str(e)


def get_current_commit() -> str:
    _, out = _run(["git", "rev-parse", "HEAD"])
    return out.strip()


def pull_latest() -> tuple[bool, str]:
    """
    Fetch and pull latest. Returns (changed, message).
    """
    before = get_current_commit()
    code, out = _run(["git", "fetch", "origin", "main"])
    if code != 0:
        return False, f"fetch failed: {out}"

    # Check if remote has new commits
    _, behind = _run(["git", "rev-list", "--count", "HEAD..origin/main"])
    try:
        n_behind = int(behind.strip())
    except ValueError:
        n_behind = 0

    if n_behind == 0:
        return False, "up to date"

    code, out = _run(["git", "pull", "--rebase", "origin", "main"])
    if code != 0:
        return False, f"pull failed: {out}"

    after = get_current_commit()
    if after != before:
        return True, f"updated {before[:8]} → {after[:8]} ({n_behind} new commit{'s' if n_behind > 1 else ''})"

    return False, "no change after pull"


def restart_service() -> bool:
    code, out = _run(["nssm", "restart", SERVICE_NAME])
    log.info(f"nssm restart {SERVICE_NAME}: code={code} {out[:120]}")
    return code == 0


def main():
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    log.info(f"AutoDeploy started — polling every {POLL_SECONDS}s for changes to {SERVICE_NAME}")
    print(f"[AutoDeploy] Started. Polling every {POLL_SECONDS // 60}min.")

    # Initial delay — let OctoDataAPI finish starting first
    time.sleep(60)

    while True:
        try:
            changed, msg = pull_latest()
            if changed:
                log.info(f"New code detected: {msg}")
                print(f"[AutoDeploy] {msg} — restarting {SERVICE_NAME}...")
                ok = restart_service()
                if ok:
                    log.info(f"Restart successful")
                    print(f"[AutoDeploy] Restart successful.")
                else:
                    log.warning(f"Restart may have failed — check nssm")
            else:
                log.debug(f"Poll: {msg}")
        except Exception as e:
            log.error(f"Poll error: {e}")

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()

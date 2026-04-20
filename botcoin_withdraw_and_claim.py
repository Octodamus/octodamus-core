"""
botcoin_withdraw_and_claim.py
Runs at scheduled time to withdraw 25M unstaked BOTCOIN + claim epoch rewards.
Scheduled via Windows Task Scheduler for 2026-04-12 18:35 local (01:35 UTC Apr 13).
"""
import subprocess
import sys
import logging
from datetime import datetime, timezone
from pathlib import Path

LOG_FILE = Path(r"C:\Users\walli\octodamus\botcoin_withdraw.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

PYTHON = sys.executable
SCRIPT = r"C:\Users\walli\octodamus\octo_boto_botcoin.py"


def run(flag: str) -> str:
    log.info(f"Running: {flag}")
    result = subprocess.run(
        [PYTHON, SCRIPT, flag],
        capture_output=True, text=True, timeout=120,
        cwd=r"C:\Users\walli\octodamus",
    )
    output = (result.stdout + result.stderr).strip()
    log.info(f"{flag} output:\n{output}")
    return output


if __name__ == "__main__":
    log.info("=== BOTCOIN AUTO WITHDRAW + CLAIM STARTED ===")
    log.info(f"Time: {datetime.now(timezone.utc).isoformat()}")

    # Step 1: Withdraw the 25M unstaked principal
    run("--withdraw")

    # Step 2: Claim epoch rewards
    run("--claim")

    log.info("=== DONE ===")

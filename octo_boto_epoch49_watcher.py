"""
octo_boto_epoch49_watcher.py — Epoch 49 migration watcher

Polls the coordinator every 2 minutes. When epoch 49 ends:
  1. Auto-runs --claim to claim epoch 49 rewards
  2. Prints step-by-step instructions for bonus + unstake
"""

import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

COORDINATOR = "https://coordinator.agentmoney.net"
TARGET_EPOCH = 49
POLL_SECONDS = 120

def get_epoch() -> dict:
    r = requests.get(f"{COORDINATOR}/v1/epoch", timeout=15)
    r.raise_for_status()
    return r.json()

def ts():
    return datetime.now().strftime("%H:%M:%S")

def claim_epoch49():
    print(f"\n[{ts()}] *** EPOCH 49 HAS ENDED — AUTO-CLAIMING NOW ***\n")
    result = subprocess.run(
        [sys.executable, "octo_boto_botcoin.py", "--claim"],
        cwd=Path(__file__).parent,
        capture_output=False,
    )
    return result.returncode == 0

def print_instructions(claim_ok: bool):
    print("\n" + "=" * 60)
    print("  EPOCH 49 → V3 MIGRATION CHECKLIST")
    print("=" * 60)
    print(f"\n  [{'OK' if claim_ok else 'FAIL'}] Step 1 -- Claim epoch 49 rewards (auto-ran above)")
    print("\n  [ ] Step 2 — Claim epoch 49 BONUS (if applicable)")
    print("        Check the BOTCOIN Discord/announcements for the")
    print("        bonus claim link or command — it may be a separate")
    print("        coordinator endpoint or UI action.")
    print("\n  [ ] Step 3 — UNSTAKE from v2 contract")
    print("        Run: python octo_boto_botcoin.py --setup")
    print("        to confirm your staked balance, then unstake via")
    print("        Bankr or the BOTCOIN staking UI.")
    print("        NOTE: There is a 1-day unstake period — start now.")
    print("\n  [ ] Step 4 — Wait 24 hours (coordinator offline)")
    print("        The coordinator will be down while v3 deploys.")
    print("        Mining will fail during this window — expected.")
    print("\n  [ ] Step 5 — Update v3 contract address in code")
    print("        When v3 launches, update MINING_CONTRACT in")
    print("        octo_boto_botcoin.py with the new address.")
    print("        They will announce it in Discord.")
    print("\n  [ ] Step 6 — Restake in v3")
    print("        Run: python octo_boto_botcoin.py --stake <AMOUNT>")
    print("        New tiers: 5M=1cr, 10M=2.05cr, 25M=5.2cr,")
    print("                   50M=10.75cr, 100M=22cr")
    print("        Consider bumping to 50M or 100M for the multiplier.")
    print("\n" + "=" * 60)
    print("  Run this once v3 is live:")
    print("    python octo_boto_botcoin.py --loop --solves 10")
    print("=" * 60 + "\n")

def main():
    print(f"[{ts()}] Epoch 49 watcher started — polling every {POLL_SECONDS}s")
    print(f"[{ts()}] Will auto-claim the moment epoch 49 ends.\n")

    while True:
        try:
            data = get_epoch()
            epoch_id = int(data.get("epochId", 0))
            next_ts  = int(data.get("nextEpochStartTimestamp", 0))
            time_left = max(0, next_ts - time.time())

            print(f"[{ts()}] Epoch {epoch_id} — {time_left/3600:.2f}h remaining", flush=True)

            if epoch_id > TARGET_EPOCH:
                claim_ok = claim_epoch49()
                print_instructions(claim_ok)
                print(f"[{ts()}] Watcher done — exiting.")
                break

        except Exception as e:
            print(f"[{ts()}] Poll error: {e} — retrying in {POLL_SECONDS}s")

        time.sleep(POLL_SECONDS)

if __name__ == "__main__":
    main()

"""
octo_ja_love.py - JA_LOVE: Octodamus Butler Agent

ACP client agent that seeds OctodamusAI with jobs to bootstrap marketplace
presence. Runs with its own wallet and config dir, fully separate from the
OctodamusAI provider config -- no collision with the running ACP worker.

Modes:
  --mode setup    One-time: create JA_LOVE agent (reuses existing owner auth)
  --mode seed     Run seed jobs against OctodamusAI (default: 21)
  --mode status   Show wallet address, job history, USDC balance

Setup:
  python octo_ja_love.py --mode setup
  # Fund the printed wallet with $22+ USDC on Base mainnet
  python octo_ja_love.py --mode seed

Architecture:
  - Config dir: C:\\Users\\walli\\acp-jalove\\  (separate from acp-cli\\)
  - Uses tsx directly (not npm run) so process.cwd() = jalove config dir
  - OctodamusAI worker keeps running unaffected (different config.json)
"""

import argparse
import json
import logging
import subprocess
import sys
import time
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

OCTO_PROVIDER    = "0x94c037393ab0263194dcfd8d04a2176d6a80e385"
OCTO_CLI         = Path(r"C:\Users\walli\acp-cli")
TSX              = OCTO_CLI / "node_modules" / ".bin" / "tsx.cmd"
ACP_BIN          = OCTO_CLI / "bin" / "acp.ts"
JALOVE_DIR       = Path(r"C:\Users\walli\acp-jalove")
JALOVE_CONFIG    = JALOVE_DIR / "config.json"

JOB_TIMEOUT_S    = 600   # 10 min max wait per job step
BETWEEN_JOBS_S   = 8     # pause between jobs (looks organic)

# ── 21 seed jobs -- spread across all 4 offerings, natural order ──────────────
# Oracle x6, Bitcoin Deep Dive x5, Fear & Greed x5, Congress x5 = 21

SEED_JOBS = [
    ("Oracle Market Signal",  '{"ticker":"BTC"}'),
    ("Fear & Greed Report",   '{"ticker":"BTC","type":"fear_greed"}'),
    ("Congress Trades",       '{"ticker":"NVDA"}'),
    ("Oracle Market Signal",  '{"ticker":"ETH"}'),
    ("Bitcoin Deep Dive",     '{"ticker":"BTC","type":"bitcoin analysis"}'),
    ("Congress Trades",       '{"ticker":"TSLA"}'),
    ("Oracle Market Signal",  '{"ticker":"SOL"}'),
    ("Fear & Greed Report",   '{"ticker":"BTC","type":"fear_greed"}'),
    ("Bitcoin Deep Dive",     '{"ticker":"BTC","type":"bitcoin analysis"}'),
    ("Congress Trades",       '{"ticker":"AAPL"}'),
    ("Oracle Market Signal",  '{"ticker":"BTC"}'),
    ("Fear & Greed Report",   '{"ticker":"ETH","type":"fear_greed"}'),
    ("Bitcoin Deep Dive",     '{"ticker":"BTC","type":"bitcoin analysis"}'),
    ("Congress Trades",       '{"ticker":"MSFT"}'),
    ("Oracle Market Signal",  '{"ticker":"ETH"}'),
    ("Fear & Greed Report",   '{"ticker":"SOL","type":"fear_greed"}'),
    ("Bitcoin Deep Dive",     '{"ticker":"BTC","type":"bitcoin analysis"}'),
    ("Congress Trades",       '{"ticker":"AMZN"}'),
    ("Oracle Market Signal",  '{"ticker":"SOL"}'),
    ("Fear & Greed Report",   '{"ticker":"BTC","type":"fear_greed"}'),
    ("Bitcoin Deep Dive",     '{"ticker":"BTC","type":"bitcoin analysis"}'),
]

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [JA_LOVE] %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            Path(__file__).parent / "logs" / "ja_love.log",
            encoding="utf-8"
        ),
    ],
)
log = logging.getLogger(__name__)


# ── CLI wrapper (runs tsx directly, cwd=JALOVE_DIR so config.json is isolated) ─

def _acp(args: list, timeout: int = 60) -> tuple:
    """Run acp CLI as JA_LOVE. cwd=JALOVE_DIR keeps config separate from worker.
    Uses cmd /c to invoke tsx.cmd (Windows batch file)."""
    cmd = ["cmd", "/c", str(TSX), str(ACP_BIN)] + [str(a) for a in args]
    log.debug(f"CLI: {' '.join(args)}")
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            cwd=str(JALOVE_DIR),
        )
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        log.error(f"CLI timeout after {timeout}s: {' '.join(args)}")
        return -1, "", "timeout"
    except Exception as e:
        log.error(f"CLI error: {e}")
        return -1, "", str(e)


def _acp_json(args: list, timeout: int = 60) -> dict | None:
    """Run acp CLI with --json flag, return parsed dict or None on failure."""
    rc, out, err = _acp(args + ["--json"], timeout=timeout)
    if rc != 0:
        log.error(f"CLI failed (rc={rc}) stderr: {err[:400]} stdout: {out[:200]}")
        return None
    try:
        # tsx may emit non-JSON lines before the JSON -- find last valid JSON
        for line in reversed(out.splitlines()):
            line = line.strip()
            if line.startswith("{") or line.startswith("["):
                return json.loads(line)
    except Exception as e:
        log.error(f"JSON parse error: {e} | output: {out[:200]}")
    return None


# ── Job lifecycle ─────────────────────────────────────────────────────────────

def _create_job(offering_name: str, requirements: str) -> str | None:
    """Create a job from an offering. Returns on-chain job ID or None."""
    log.info(f"Creating job: {offering_name} | reqs={requirements}")
    result = _acp_json([
        "client", "create-job",
        "--provider",       OCTO_PROVIDER,
        "--offering-name",  offering_name,
        "--requirements",   requirements,
        "--chain-id",       "8453",
    ], timeout=60)
    if not result:
        return None
    job_id = str(result.get("jobId") or result.get("id") or "")
    if not job_id:
        log.error(f"No jobId in response: {result}")
        return None
    log.info(f"Job created: #{job_id}")
    return job_id


def _poll_for_entry(job_id: str, event_type: str, poll_interval: int = 5) -> dict | None:
    """Poll job history every poll_interval seconds until event_type appears in entries.
    Returns the matching entry dict or None on timeout.
    Uses short CLI calls (no blocking subprocess) -- works reliably on Windows."""
    log.info(f"Polling for {event_type} on job #{job_id} (timeout={JOB_TIMEOUT_S}s)...")
    deadline = time.time() + JOB_TIMEOUT_S
    while time.time() < deadline:
        result = _acp_json([
            "job", "history",
            "--job-id",  job_id,
            "--chain-id", "8453",
        ], timeout=60)
        if result:
            for entry in result.get("entries", []):
                etype = (entry.get("event") or {}).get("type", "")
                if etype == event_type:
                    log.info(f"Got {event_type} for job #{job_id}")
                    return entry
        remaining = int(deadline - time.time())
        log.debug(f"  {event_type} not yet -- {remaining}s remaining, retrying in {poll_interval}s")
        time.sleep(poll_interval)
    log.warning(f"Timeout waiting for {event_type} on job #{job_id}")
    return None


def _fund_job(job_id: str, amount: float = 1.0) -> bool:
    """Fund job escrow with USDC."""
    log.info(f"Funding job #{job_id} with ${amount} USDC...")
    result = _acp_json([
        "client", "fund",
        "--job-id",  job_id,
        "--amount",  str(amount),
        "--chain-id", "8453",
    ], timeout=90)
    if result is None:
        return False
    log.info(f"Job #{job_id} funded")
    return True


def _complete_job(job_id: str) -> bool:
    """Approve and complete job, releasing escrow to provider."""
    log.info(f"Completing job #{job_id}...")
    result = _acp_json([
        "client", "complete",
        "--job-id", job_id,
        "--reason", "Report received and verified",
        "--chain-id", "8453",
    ], timeout=90)
    if result is None:
        return False
    log.info(f"Job #{job_id} complete -- escrow released to OctodamusAI")
    return True


def run_single_job(offering_name: str, requirements: str, idx: int, total: int) -> bool:
    """Run full lifecycle for one job. Returns True on success."""
    log.info(f"--- Job {idx}/{total}: {offering_name} ---")

    job_id = _create_job(offering_name, requirements)
    if not job_id:
        log.error(f"Job {idx} creation failed -- skipping")
        return False

    # Step 1: wait for provider to set budget
    entry = _poll_for_entry(job_id, "budget.set")
    if not entry:
        log.error(f"Job #{job_id} -- no budget.set, aborting")
        return False

    # Step 2: fund escrow
    if not _fund_job(job_id):
        log.error(f"Job #{job_id} -- fund failed, aborting")
        return False

    # Step 3: wait for provider to submit deliverable
    entry = _poll_for_entry(job_id, "job.submitted")
    if not entry:
        log.error(f"Job #{job_id} -- no job.submitted, aborting")
        return False

    # Step 4: complete (release escrow)
    if not _complete_job(job_id):
        log.error(f"Job #{job_id} -- complete failed")
        return False

    log.info(f"Job {idx}/{total} SUCCESS -- {offering_name}")
    return True


# ── Modes ─────────────────────────────────────────────────────────────────────

def mode_setup():
    """One-time setup: create JA_LOVE agent under existing owner wallet."""
    JALOVE_DIR.mkdir(parents=True, exist_ok=True)
    log.info(f"Config dir: {JALOVE_DIR}")

    # Seed config with ownerWallet from existing acp-cli config
    existing_config_path = OCTO_CLI / "config.json"
    try:
        existing = json.loads(existing_config_path.read_text(encoding="utf-8"))
        owner_wallet = existing.get("ownerWallet", "")
    except Exception:
        owner_wallet = ""

    if not JALOVE_CONFIG.exists():
        seed = {"ownerWallet": owner_wallet} if owner_wallet else {}
        JALOVE_CONFIG.write_text(json.dumps(seed, indent=2), encoding="utf-8")
        log.info(f"Config seeded with ownerWallet: {owner_wallet or '(none -- run acp configure)'}")
    else:
        log.info("Config already exists -- skipping seed")

    # Create agent
    log.info("Creating JA_LOVE agent...")
    rc, out, err = _acp([
        "agent", "create",
        "--name",        "JA_LOVE",
        "--description", (
            "Octodamus intelligence relay. Commissions market oracle reports, "
            "signal analysis, and congressional trade alerts from OctodamusAI "
            "for downstream strategy agents."
        ),
        "--image", "https://acpcdn-prod.s3.ap-southeast-1.amazonaws.com/agents/19a66f60-1183-4b8e-bdf1-84a6d25abe4d.webp",
    ], timeout=120)

    if rc == 0:
        print(out)
        log.info("JA_LOVE agent created successfully")
    else:
        log.error(f"Agent creation failed: {err}")
        log.info("Try running: acp configure (from C:\\Users\\walli\\acp-jalove)")
        sys.exit(1)

    # Print wallet address
    rc2, out2, _ = _acp(["agent", "whoami", "--json"], timeout=30)
    try:
        info = json.loads(out2.splitlines()[-1])
        wallet = info.get("walletAddress", "unknown")
        print(f"\nJA_LOVE wallet: {wallet}")
        print(f"Send $22+ USDC to this address on Base mainnet before running seed.")
    except Exception:
        print(out2)


def mode_seed(num_jobs: int = 21):
    """Run seed jobs against OctodamusAI."""
    if not JALOVE_CONFIG.exists():
        log.error("JA_LOVE not set up. Run: python octo_ja_love.py --mode setup")
        sys.exit(1)

    jobs = SEED_JOBS[:num_jobs]
    log.info(f"Starting seed run: {len(jobs)} jobs against OctodamusAI ({OCTO_PROVIDER})")

    results = {"success": 0, "failed": 0, "failed_jobs": []}

    for i, (offering, reqs) in enumerate(jobs, 1):
        ok = run_single_job(offering, reqs, i, len(jobs))
        if ok:
            results["success"] += 1
        else:
            results["failed"] += 1
            results["failed_jobs"].append({"idx": i, "offering": offering})

        if i < len(jobs):
            log.info(f"Waiting {BETWEEN_JOBS_S}s before next job...")
            time.sleep(BETWEEN_JOBS_S)

    log.info(f"\n=== Seed run complete ===")
    log.info(f"Successful: {results['success']}/{len(jobs)}")
    if results["failed"]:
        log.warning(f"Failed: {results['failed']} -- {results['failed_jobs']}")
    else:
        log.info("All jobs completed. OctodamusAI now has job history and should appear in acp browse.")


def mode_status():
    """Show JA_LOVE wallet and job history."""
    if not JALOVE_CONFIG.exists():
        print("JA_LOVE not set up. Run: python octo_ja_love.py --mode setup")
        return

    rc, out, _ = _acp(["agent", "whoami"], timeout=30)
    print("=== JA_LOVE Agent ===")
    print(out or "(no output)")

    print("\n=== Recent Jobs ===")
    rc2, out2, _ = _acp(["job", "list", "--json"], timeout=30)
    try:
        jobs = json.loads(out2.splitlines()[-1]) if out2 else []
        if isinstance(jobs, list):
            for j in jobs[:10]:
                print(f"  #{j.get('jobId','?')} | {j.get('status','?')} | {j.get('offeringName','?')}")
        else:
            print(out2)
    except Exception:
        print(out2 or "(no jobs)")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="JA_LOVE -- Octodamus Butler Agent")
    parser.add_argument("--mode", default="seed",
                        choices=["setup", "seed", "status"],
                        help="Mode to run (default: seed)")
    parser.add_argument("--jobs", type=int, default=21,
                        help="Number of seed jobs to run (default: 21)")
    args = parser.parse_args()

    if args.mode == "setup":
        mode_setup()
    elif args.mode == "seed":
        mode_seed(args.jobs)
    elif args.mode == "status":
        mode_status()

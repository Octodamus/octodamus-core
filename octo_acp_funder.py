"""
octo_acp_funder.py
Auto-funder for ACP pending jobs created by ecosystem sub-agents.

Reads data/acp_pending_jobs.json, checks each job's on-chain status via getJob(),
and when the provider has set a budget it approves USDC and calls fund().
Each agent funds from their OWN private key.

Run: python octo_acp_funder.py [--once]
Task Scheduler: run every 5 minutes (Octodamus-ACP-Funder task)
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

_PENDING_FILE = ROOT / "data" / "acp_pending_jobs.json"
_SECRETS_FILE = ROOT / ".octo_secrets"

_BASE_RPC      = "https://mainnet.base.org"
_ACP_CONTRACT  = "0x238E541BfefD82238730D00a2208E5497F1832E0"
_USDC_ADDRESS  = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"

# Job status enum values from AgenticCommerceV3
_STATUS_OPEN    = 0
_STATUS_FUNDED  = 1
_STATUS_SUBMITTED = 2
_STATUS_COMPLETE  = 3
_STATUS_REJECTED  = 4
_STATUS_EXPIRED   = 5
_STATUS_NAMES = {0: "open", 1: "funded", 2: "submitted", 3: "complete", 4: "rejected", 5: "expired"}

_ACP_ABI = [
    {
        "name": "getJob",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "jobId", "type": "uint256"}],
        "outputs": [{
            "name": "", "type": "tuple",
            "components": [
                {"name": "client",     "type": "address"},
                {"name": "status",     "type": "uint8"},
                {"name": "provider",   "type": "address"},
                {"name": "expiredAt",  "type": "uint48"},
                {"name": "evaluator",  "type": "address"},
                {"name": "hook",       "type": "address"},
                {"name": "budget",     "type": "uint256"},
                {"name": "description","type": "string"},
            ],
        }],
    },
    {
        "name": "fund",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "jobId",          "type": "uint256"},
            {"name": "expectedBudget", "type": "uint256"},
            {"name": "optParams",      "type": "bytes"},
        ],
        "outputs": [],
    },
]

_USDC_ABI = [
    {
        "name": "approve",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "amount",  "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "name": "balanceOf",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "account", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "allowance",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {"name": "owner",   "type": "address"},
            {"name": "spender", "type": "address"},
        ],
        "outputs": [{"name": "", "type": "uint256"}],
    },
]


def _secrets() -> dict:
    try:
        raw = json.loads(_SECRETS_FILE.read_text(encoding="utf-8"))
        return raw.get("secrets", raw)
    except Exception:
        return {}


def _normalize_key(raw: str) -> str:
    raw = raw.strip()
    return raw if raw.startswith("0x") else f"0x{raw}"


def _load_pending() -> list:
    try:
        return json.loads(_PENDING_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_pending(jobs: list) -> None:
    _PENDING_FILE.write_text(json.dumps(jobs, indent=2), encoding="utf-8")


def _fund_job(w3, job: dict, sec: dict) -> str:
    """Approve USDC + call fund() using the buyer agent's private key."""
    from web3 import Web3
    from eth_account import Account

    raw_key  = sec.get(job["buyer_key"], "")
    if not raw_key:
        return f"ERROR: key '{job['buyer_key']}' not in secrets"

    private_key  = _normalize_key(raw_key)
    buyer_addr   = Web3.to_checksum_address(job["buyer_addr"])
    acp_cs       = Web3.to_checksum_address(_ACP_CONTRACT)
    usdc_cs      = Web3.to_checksum_address(_USDC_ADDRESS)

    acp_contract  = w3.eth.contract(address=acp_cs,  abi=_ACP_ABI)
    usdc_contract = w3.eth.contract(address=usdc_cs, abi=_USDC_ABI)

    budget = job["on_chain_budget"]
    job_id = job["job_id"]

    # Check USDC balance
    balance = usdc_contract.functions.balanceOf(buyer_addr).call()
    if balance < budget:
        usdc_bal = balance / 1e6
        usdc_need = budget / 1e6
        return f"SKIP: insufficient USDC. Have ${usdc_bal:.2f}, need ${usdc_need:.2f} for job #{job_id}"

    gas_price = w3.eth.gas_price
    nonce = w3.eth.get_transaction_count(buyer_addr)

    # Step 1: approve USDC allowance if needed
    allowance = usdc_contract.functions.allowance(buyer_addr, acp_cs).call()
    if allowance < budget:
        try:
            approve_tx = usdc_contract.functions.approve(acp_cs, budget * 2).build_transaction({
                "from":     buyer_addr,
                "nonce":    nonce,
                "gas":      100_000,
                "gasPrice": gas_price,
                "chainId":  8453,
            })
            signed = Account.sign_transaction(approve_tx, private_key)
            approve_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            w3.eth.wait_for_transaction_receipt(approve_hash, timeout=60)
            nonce += 1
        except Exception as e:
            return f"ERROR approving USDC for job #{job_id}: {e}"

    # Step 2: fund the job
    try:
        fund_tx = acp_contract.functions.fund(
            job_id,
            budget,
            b"",
        ).build_transaction({
            "from":     buyer_addr,
            "nonce":    nonce,
            "gas":      300_000,
            "gasPrice": gas_price,
            "chainId":  8453,
        })
        signed = Account.sign_transaction(fund_tx, private_key)
        fund_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(fund_hash, timeout=60)
    except Exception as e:
        return f"ERROR funding job #{job_id}: {e}"

    if receipt.status != 1:
        return f"ERROR: fund() tx reverted for job #{job_id}. hash={fund_hash.hex()}"

    return f"OK: job #{job_id} funded ${budget/1e6:.4f} USDC from {job['buyer_agent']} ({buyer_addr[:12]}...). tx={fund_hash.hex()[:16]}..."


def run_once(verbose: bool = True) -> list[str]:
    """Check all pending jobs and fund those with budget set. Returns log lines."""
    from web3 import Web3

    jobs = _load_pending()
    if not jobs:
        if verbose:
            print("No pending ACP jobs.")
        return []

    pending_statuses = {"pending_budget", "error_retry"}
    active = [j for j in jobs if j.get("status") in pending_statuses]
    if not active:
        if verbose:
            print(f"{len(jobs)} jobs in log, none awaiting funding.")
        return []

    w3 = Web3(Web3.HTTPProvider(_BASE_RPC))
    if not w3.is_connected():
        msg = "ERROR: cannot connect to Base mainnet RPC"
        if verbose:
            print(msg)
        return [msg]

    acp_cs = Web3.to_checksum_address(_ACP_CONTRACT)
    acp_contract = w3.eth.contract(address=acp_cs, abi=_ACP_ABI)
    sec = _secrets()
    log_lines = []

    for job in jobs:
        if job.get("status") not in pending_statuses:
            continue

        job_id = job["job_id"]
        try:
            on_chain = acp_contract.functions.getJob(job_id).call()
            # on_chain = (client, status, provider, expiredAt, evaluator, hook, budget, description)
            status_int = on_chain[1]
            budget     = on_chain[6]
        except Exception as e:
            line = f"WARNING: cannot read job #{job_id} on-chain: {e}"
            log_lines.append(line)
            if verbose:
                print(line)
            continue

        status_name = _STATUS_NAMES.get(status_int, f"unknown({status_int})")

        if status_int in (_STATUS_COMPLETE, _STATUS_REJECTED, _STATUS_EXPIRED):
            job["status"] = status_name
            line = f"Job #{job_id} is {status_name} — marked done."
            log_lines.append(line)
            if verbose:
                print(line)
            continue

        if status_int == _STATUS_FUNDED:
            job["status"] = "funded"
            line = f"Job #{job_id} already funded on-chain."
            log_lines.append(line)
            if verbose:
                print(line)
            continue

        # status_int == _STATUS_OPEN: check if budget > 0 (provider set it)
        if status_int == _STATUS_OPEN and budget > 0:
            job["on_chain_budget"] = budget
            result = _fund_job(w3, job, sec)
            log_lines.append(result)
            if verbose:
                print(result)
            if result.startswith("OK:"):
                job["status"] = "funded"
            elif result.startswith("SKIP:"):
                job["status"] = "insufficient_funds"
            else:
                job["status"] = "error_retry"
        else:
            line = f"Job #{job_id} open, budget not set yet (status={status_name}, budget={budget/1e6:.4f} USDC). Waiting..."
            log_lines.append(line)
            if verbose:
                print(line)

    _save_pending(jobs)
    return log_lines


def main():
    parser = argparse.ArgumentParser(description="ACP auto-funder for Octodamus ecosystem agents")
    parser.add_argument("--once",     action="store_true", help="Run once and exit")
    parser.add_argument("--interval", type=int, default=300, help="Poll interval in seconds (default 300)")
    parser.add_argument("--quiet",    action="store_true", help="Suppress output")
    args = parser.parse_args()

    verbose = not args.quiet

    if args.once:
        run_once(verbose=verbose)
        return

    if verbose:
        print(f"ACP funder running (poll every {args.interval}s). Ctrl+C to stop.")
    while True:
        try:
            run_once(verbose=verbose)
        except Exception as e:
            print(f"ERROR in run_once: {e}")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()

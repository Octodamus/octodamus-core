"""
octo_boto_botcoin.py — BOTCOIN Proof-of-Inference Miner

OctoBoto mines BOTCOIN on Base by solving LLM challenges via Claude.

Flow:
  1. Get Bankr wallet address
  2. Auth handshake with coordinator → bearer token
  3. Check epoch + credits
  4. Mining loop: request challenge → solve with Claude → submit → post tx via Bankr
  5. Claim rewards for completed epochs

Requirements:
  - BANKR_API_KEY in Bitwarden / .octo_secrets
  - 25M+ BOTCOIN staked (Tier 1) — buy via Bankr prompt before first run
  - ETH on Base for gas (small amount, coordinator pre-encodes txs)

Contracts (Base chain 8453):
  BOTCOIN token:  0xA601877977340862Ca67f816eb079958E5bd0BA3
  Mining contract: 0xcF5F2D541EEb0fb4cA35F1973DE5f2B02dfC3716

Usage:
  python octo_boto_botcoin.py              # single mining session (1 challenge)
  python octo_boto_botcoin.py --loop       # continuous mining loop
  python octo_boto_botcoin.py --setup      # check wallet, balances, staking status
  python octo_boto_botcoin.py --claim      # claim all unclaimed epoch rewards
  python octo_boto_botcoin.py --stake      # stake BOTCOIN (interactive)
"""

import argparse
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import anthropic
import requests

log = logging.getLogger("BotcoinMiner")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)

# ── Constants ─────────────────────────────────────────────────────────────────

COORDINATOR    = "https://coordinator.agentmoney.net"
BANKR_API      = "https://api.bankr.bot/agent"
CHAIN_ID       = 8453  # Base
BOTCOIN_ADDR   = "0xA601877977340862Ca67f816eb079958E5bd0BA3"
MINING_CONTRACT = "0xcF5F2D541EEb0fb4cA35F1973DE5f2B02dfC3716"

MIN_STAKE      = 25_000_000   # 25M BOTCOIN — Tier 1 minimum
TIER_2_STAKE   = 50_000_000   # 2 credits/solve
TIER_3_STAKE   = 100_000_000  # 3 credits/solve

DATA_DIR       = Path(r"C:\Users\walli\octodamus\data")
TOKEN_CACHE    = DATA_DIR / "botcoin_auth.json"
CREDITS_LOG    = DATA_DIR / "botcoin_credits.json"

CLAUDE_MODEL   = "claude-sonnet-4-6"

# ── Secrets ───────────────────────────────────────────────────────────────────

def _get_secrets() -> dict:
    paths = [
        Path(__file__).parent / ".octo_secrets",
        Path(r"C:\Users\walli\octodamus\.octo_secrets"),
        Path.home() / "octodamus" / ".octo_secrets",
    ]
    for p in paths:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            s = d.get("secrets", d)
            if s:
                return s
        except Exception:
            continue
    return {}


def _bankr_key() -> str:
    k = os.environ.get("BANKR_API_KEY", "")
    if not k:
        k = _get_secrets().get("BANKR_API_KEY", "")
    if not k:
        raise RuntimeError("BANKR_API_KEY not found — add to Bitwarden as BANKR_API_KEY")
    return k


def _anthropic_key() -> str:
    k = os.environ.get("ANTHROPIC_API_KEY", "")
    if not k:
        k = _get_secrets().get("ANTHROPIC_API_KEY", "")
    return k


# ── Bankr API ─────────────────────────────────────────────────────────────────

def bankr_get(path: str, **kwargs) -> dict:
    r = requests.get(
        f"{BANKR_API}{path}",
        headers={"X-API-Key": _bankr_key()},
        timeout=30,
        **kwargs,
    )
    r.raise_for_status()
    return r.json()


def bankr_post(path: str, body: dict, timeout: int = 60) -> dict:
    r = requests.post(
        f"{BANKR_API}{path}",
        headers={"X-API-Key": _bankr_key(), "Content-Type": "application/json"},
        json=body,
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()


def get_wallet_address() -> str:
    """Get OctoBoto's Base wallet address from Bankr."""
    data = bankr_get("/me")
    for wallet in data.get("wallets", []):
        # Bankr returns chain="evm" for Base EVM wallet
        if wallet.get("chain") == "evm" or wallet.get("chainId") == CHAIN_ID:
            return wallet["address"]
    raise RuntimeError("No Base wallet found in Bankr account")


def get_erc20_balance(token_address: str, wallet: str) -> int:
    """Read ERC20 token balance via Ankr public RPC — no API key needed."""
    # balanceOf(address) ABI call via eth_call
    padded = wallet.lower().replace("0x", "").zfill(64)
    data   = "0x70a08231" + padded  # balanceOf selector
    payload = {
        "jsonrpc": "2.0", "id": 1, "method": "eth_call",
        "params": [{"to": token_address, "data": data}, "latest"],
    }
    try:
        r = requests.post("https://rpc.ankr.com/base", json=payload, timeout=10)
        result = r.json().get("result", "0x0")
        return int(result, 16)
    except Exception:
        return 0


def get_eth_balance(wallet: str) -> int:
    """Read ETH balance on Base via public RPC."""
    payload = {
        "jsonrpc": "2.0", "id": 1, "method": "eth_getBalance",
        "params": [wallet, "latest"],
    }
    try:
        r = requests.post("https://rpc.ankr.com/base", json=payload, timeout=10)
        result = r.json().get("result", "0x0")
        return int(result, 16)
    except Exception:
        return 0


def bankr_sign(message: str) -> str:
    """Sign a message via Bankr (for coordinator auth)."""
    resp = bankr_post("/sign", {"signatureType": "personal_sign", "message": message})
    return resp["signature"]


def bankr_submit_tx(tx: dict, description: str) -> dict:
    """Submit a pre-encoded transaction via Bankr."""
    body = {
        "transaction": tx,
        "description": description,
        "waitForConfirmation": True,
    }
    resp = bankr_post("/submit", body, timeout=120)
    return resp


# ── Coordinator Auth ──────────────────────────────────────────────────────────

def _load_token_cache() -> dict:
    try:
        if TOKEN_CACHE.exists():
            return json.loads(TOKEN_CACHE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_token_cache(data: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_CACHE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_bearer_token(wallet: str, force: bool = False) -> Optional[str]:
    """
    Auth handshake: nonce → Bankr sign → verify → bearer token.
    Caches token for 23h (coordinator tokens typically last 24h).
    """
    cache = _load_token_cache()
    if not force and cache.get("wallet") == wallet and cache.get("token"):
        age = time.time() - cache.get("ts", 0)
        if age < 82800:  # 23h
            log.info("[Auth] Using cached bearer token")
            return cache["token"]

    log.info("[Auth] Starting auth handshake...")
    try:
        # Step 1: Get nonce message
        r = requests.post(
            f"{COORDINATOR}/v1/auth/nonce",
            json={"miner": wallet},
            timeout=15,
        )
        if r.status_code == 404:
            log.info("[Auth] Auth not required by coordinator — proceeding without token")
            return None
        r.raise_for_status()
        message = r.json()["message"]

        # Step 2: Sign via Bankr
        signature = bankr_sign(message)

        # Step 3: Verify → get token
        v = requests.post(
            f"{COORDINATOR}/v1/auth/verify",
            json={"miner": wallet, "message": message, "signature": signature},
            timeout=15,
        )
        v.raise_for_status()
        token = v.json()["token"]

        _save_token_cache({"wallet": wallet, "token": token, "ts": time.time()})
        log.info("[Auth] Bearer token obtained and cached")
        return token

    except Exception as e:
        log.warning(f"[Auth] Handshake failed ({e}) — proceeding without token")
        return None


# ── Coordinator Calls ─────────────────────────────────────────────────────────

def _coord_headers(token: Optional[str]) -> dict:
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def get_epoch() -> dict:
    r = requests.get(f"{COORDINATOR}/v1/epoch", timeout=15)
    r.raise_for_status()
    return r.json()


def get_credits(wallet: str, token: Optional[str] = None) -> dict:
    r = requests.get(
        f"{COORDINATOR}/v1/credits",
        params={"miner": wallet},
        headers=_coord_headers(token),
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def request_challenge(wallet: str, nonce: str, token: Optional[str] = None) -> dict:
    r = requests.get(
        f"{COORDINATOR}/v1/challenge",
        params={"miner": wallet, "nonce": nonce},
        headers=_coord_headers(token),
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def submit_solution(payload: dict, token: Optional[str] = None) -> dict:
    r = requests.post(
        f"{COORDINATOR}/v1/submit",
        json=payload,
        headers=_coord_headers(token),
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def get_claim_calldata(epochs: list[int]) -> dict:
    epoch_str = ",".join(str(e) for e in epochs)
    r = requests.get(
        f"{COORDINATOR}/v1/claim-calldata",
        params={"epochs": epoch_str},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def get_stake_approve_calldata(amount_wei: int) -> dict:
    r = requests.get(
        f"{COORDINATOR}/v1/stake-approve-calldata",
        params={"amount": str(amount_wei)},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def get_stake_calldata(amount_wei: int) -> dict:
    r = requests.get(
        f"{COORDINATOR}/v1/stake-calldata",
        params={"amount": str(amount_wei)},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


# ── Claude Challenge Solver ───────────────────────────────────────────────────

SOLVE_SYSTEM = """You are an expert reasoning agent solving structured inference challenges.

You will receive a document (doc), a list of questions about entities in that document,
a list of constraints the artifact must satisfy, and solve instructions.

Your job:
1. Read the document carefully, paragraph by paragraph
2. Answer each question accurately based ONLY on what the doc says
3. Generate the artifact (a single line/value) satisfying ALL constraints
4. If a reasoning trace is required, produce it in the exact schema requested

Rules:
- Extract only what is explicitly stated — do not infer or hallucinate
- Paragraph references use format paragraph_1, paragraph_2, etc. (1-indexed)
- For computation steps, show every intermediate operation
- The artifact must be on its own line at the end, labeled: ARTIFACT: <value>
- Answered questions must each be on their own line: Q01: <answer>
"""


def solve_challenge(challenge: dict) -> dict:
    """
    Use Claude to solve a BOTCOIN challenge.
    Returns dict with: artifact, submittedAnswers, reasoningTrace (if required)
    """
    doc          = challenge.get("doc", "")
    questions    = challenge.get("questions", [])
    constraints  = challenge.get("constraints", [])
    entities     = challenge.get("entities", [])
    instructions = challenge.get("solveInstructions", "")
    trace_cfg    = challenge.get("traceSubmission", {})
    trace_needed = trace_cfg.get("required", False)

    q_text = "\n".join(f"Q{str(i+1).zfill(2)}: {q}" for i, q in enumerate(questions))
    c_text = "\n".join(f"- {c}" for c in constraints)
    e_text = ", ".join(entities) if entities else "see document"

    prompt = f"""DOCUMENT:
{doc}

ENTITIES TO TRACK: {e_text}

QUESTIONS (answer each precisely):
{q_text}

ARTIFACT CONSTRAINTS (all must be satisfied):
{c_text}

SOLVE INSTRUCTIONS:
{instructions}

{"REASONING TRACE REQUIRED: Yes — include extract_fact and compute_logic steps." if trace_needed else "REASONING TRACE: Not required."}

Respond with:
- One line per question answer: Q01: <answer>
- Then: ARTIFACT: <single line artifact>
{"- Then: TRACE: <JSON array of reasoning steps>" if trace_needed else ""}
"""

    client = anthropic.Anthropic(api_key=_anthropic_key())
    resp = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1500,
        system=SOLVE_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = resp.content[0].text.strip()
    log.debug(f"[Solver] Claude response:\n{raw}")

    # Parse artifact
    artifact = ""
    for line in raw.splitlines():
        if line.startswith("ARTIFACT:"):
            artifact = line.replace("ARTIFACT:", "").strip()
            break

    # Parse Q&A answers
    submitted_answers = {}
    for line in raw.splitlines():
        import re
        m = re.match(r"Q(\d+):\s*(.+)", line.strip())
        if m:
            key = f"q{m.group(1).zfill(2)}"
            submitted_answers[key] = m.group(2).strip()

    # Parse reasoning trace if present
    reasoning_trace = []
    if trace_needed:
        try:
            trace_start = raw.find("TRACE:")
            if trace_start >= 0:
                trace_json = raw[trace_start + 6:].strip()
                reasoning_trace = json.loads(trace_json)
        except Exception as e:
            log.warning(f"[Solver] Could not parse reasoning trace: {e}")

    return {
        "artifact":        artifact,
        "submittedAnswers": submitted_answers,
        "reasoningTrace":  reasoning_trace,
    }


# ── Mining Loop ───────────────────────────────────────────────────────────────

def mine_one(wallet: str, token: Optional[str]) -> dict:
    """
    Execute one full mining cycle:
    request challenge → solve → submit → post tx if pass.
    Returns result dict.
    """
    nonce = str(uuid.uuid4())
    epoch_data = get_epoch()
    epoch_id   = epoch_data.get("epochId", "?")

    log.info(f"[Mine] Epoch {epoch_id} — requesting challenge (nonce {nonce[:8]}...)")
    challenge = request_challenge(wallet, nonce, token)

    challenge_id   = challenge.get("challengeId", "")
    manifest_hash  = challenge.get("challengeManifestHash", "")
    credits_per    = challenge.get("creditsPerSolve", 1)
    domain         = challenge.get("challengeDomain", "unknown")
    trace_cfg      = challenge.get("traceSubmission", {})

    log.info(f"[Mine] Challenge {challenge_id[:12]}... domain={domain} credits_per_solve={credits_per}")

    # Solve
    t0       = time.time()
    solution = solve_challenge(challenge)
    elapsed  = time.time() - t0
    log.info(f"[Mine] Solved in {elapsed:.1f}s — artifact: {solution['artifact'][:60]}")

    # Build submission
    payload = {
        "miner":                wallet,
        "challengeId":          challenge_id,
        "artifact":             solution["artifact"],
        "nonce":                nonce,
        "challengeManifestHash": manifest_hash,
        "modelVersion":         CLAUDE_MODEL,
        "submittedAnswers":     solution["submittedAnswers"],
    }
    if trace_cfg.get("required") and solution["reasoningTrace"]:
        payload["reasoningTrace"] = solution["reasoningTrace"]

    log.info("[Mine] Submitting solution...")
    result = submit_solution(payload, token)

    passed = result.get("pass", False)
    log.info(f"[Mine] Result: {'PASS ✓' if passed else 'FAIL ✗'}")

    if passed:
        tx = result.get("transaction")
        if tx:
            log.info("[Mine] Posting mining receipt to chain via Bankr...")
            tx_result = bankr_submit_tx(tx, f"BOTCOIN mining receipt epoch {epoch_id}")
            tx_hash = tx_result.get("transactionHash", "")
            log.info(f"[Mine] Receipt confirmed: {tx_hash}")
            result["txHash"] = tx_hash
        else:
            log.warning("[Mine] Pass but no transaction in response")
    else:
        attempts_left = result.get("attemptsRemaining", 0)
        constraints_passed = result.get("constraintsPassed", "?")
        constraints_total  = result.get("constraintsTotal", "?")
        log.info(
            f"[Mine] Constraints: {constraints_passed}/{constraints_total} passed. "
            f"Retries remaining: {attempts_left}"
        )

    # Log credits
    _log_credits(wallet, epoch_id, credits_per if passed else 0, passed)

    return {
        "epoch":    epoch_id,
        "passed":   passed,
        "domain":   domain,
        "elapsed":  round(elapsed, 1),
        "artifact": solution["artifact"][:80],
        **result,
    }


def _log_credits(wallet: str, epoch_id: str, credits: int, passed: bool):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    log_data = {}
    try:
        if CREDITS_LOG.exists():
            log_data = json.loads(CREDITS_LOG.read_text(encoding="utf-8"))
    except Exception:
        pass

    if epoch_id not in log_data:
        log_data[epoch_id] = {"solves": 0, "passes": 0, "credits": 0, "wallet": wallet}

    log_data[epoch_id]["solves"] += 1
    if passed:
        log_data[epoch_id]["passes"] += 1
        log_data[epoch_id]["credits"] += credits

    CREDITS_LOG.write_text(json.dumps(log_data, indent=2), encoding="utf-8")


def mine_loop(wallet: str, token: Optional[str], max_solves: int = 0, delay_s: int = 30):
    """
    Continuous mining loop. max_solves=0 means unlimited.
    Stops at epoch boundary and claims, then continues.
    """
    solves = 0
    claimed_epochs = set()

    log.info(f"[Loop] Starting mining loop — wallet {wallet[:10]}...")

    while True:
        try:
            epoch_data = get_epoch()
            epoch_id   = int(epoch_data.get("epochId", 0))
            next_start = int(epoch_data.get("nextEpochStartTimestamp", 0))
            time_left  = next_start - time.time()

            # Auto-claim previous epoch if not yet claimed
            prev_epoch = epoch_id - 1
            if prev_epoch > 0 and prev_epoch not in claimed_epochs:
                try:
                    log.info(f"[Loop] Auto-claiming epoch {prev_epoch}...")
                    claim_epochs([prev_epoch], wallet)
                    claimed_epochs.add(prev_epoch)
                except Exception as ce:
                    log.warning(f"[Loop] Claim failed for epoch {prev_epoch}: {ce}")

            result = mine_one(wallet, token)
            solves += 1

            log.info(
                f"[Loop] Solve #{solves} complete — "
                f"{'PASS' if result['passed'] else 'FAIL'} — "
                f"epoch {epoch_id} — {time_left/3600:.1f}h remaining"
            )

            if max_solves and solves >= max_solves:
                log.info(f"[Loop] Reached max_solves={max_solves} — stopping")
                break

            # Refresh token if near expiry
            if solves % 20 == 0:
                token = get_bearer_token(wallet, force=True)

            time.sleep(delay_s)

        except KeyboardInterrupt:
            log.info("[Loop] Stopped by user")
            break
        except Exception as e:
            log.error(f"[Loop] Error: {e} — waiting 60s before retry")
            time.sleep(60)


# ── Claim Rewards ─────────────────────────────────────────────────────────────

def claim_epochs(epochs: list[int], wallet: str):
    """Claim BOTCOIN rewards for a list of epoch IDs."""
    log.info(f"[Claim] Claiming epochs: {epochs}")
    data = get_claim_calldata(epochs)
    tx   = data.get("transaction")
    if not tx:
        log.warning("[Claim] No transaction returned")
        return

    result = bankr_submit_tx(tx, f"BOTCOIN claim epochs {epochs}")
    tx_hash = result.get("transactionHash", "")
    log.info(f"[Claim] Claimed — tx: {tx_hash}")
    return tx_hash


def claim_all(wallet: str, token: Optional[str] = None):
    """Check local credit log and claim all unclaimed epochs."""
    try:
        log_data = json.loads(CREDITS_LOG.read_text(encoding="utf-8")) if CREDITS_LOG.exists() else {}
    except Exception:
        log_data = {}

    current_epoch = int(get_epoch().get("epochId", 0))

    # Only claim completed epochs (not current)
    claimable = [
        int(ep) for ep, v in log_data.items()
        if int(ep) < current_epoch and v.get("credits", 0) > 0
    ]

    if not claimable:
        log.info("[Claim] No claimable epochs found in local log")
        # Try claiming recent epochs anyway
        recent = list(range(max(1, current_epoch - 5), current_epoch))
        log.info(f"[Claim] Trying recent epochs: {recent}")
        claimable = recent

    claim_epochs(claimable, wallet)


# ── Staking ───────────────────────────────────────────────────────────────────

def stake_botcoin(amount_tokens: int, wallet: str):
    """
    Stake BOTCOIN. amount_tokens in whole tokens (e.g. 25_000_000).
    BOTCOIN uses 18 decimals.
    """
    amount_wei = amount_tokens * (10 ** 18)
    log.info(f"[Stake] Staking {amount_tokens:,} BOTCOIN...")

    # Step 1: approve
    log.info("[Stake] Step 1: Approve...")
    approve_data = get_stake_approve_calldata(amount_wei)
    bankr_submit_tx(approve_data["transaction"], f"Approve {amount_tokens:,} BOTCOIN for mining")

    time.sleep(5)

    # Step 2: stake
    log.info("[Stake] Step 2: Stake...")
    stake_data = get_stake_calldata(amount_wei)
    result = bankr_submit_tx(stake_data["transaction"], f"Stake {amount_tokens:,} BOTCOIN for mining")
    log.info(f"[Stake] Staked — tx: {result.get('transactionHash', '')}")


# ── Setup / Status ────────────────────────────────────────────────────────────

def setup_check():
    """Check wallet, balances, epoch, and staking status."""
    print("\n" + "=" * 60)
    print("BOTCOIN MINER — SETUP CHECK")
    print("=" * 60)

    # Wallet
    try:
        wallet = get_wallet_address()
        print(f"Bankr wallet (Base): {wallet}")
    except Exception as e:
        print(f"ERROR getting wallet: {e}")
        return

    # Balances via public RPC (no Bankr Club needed)
    print("\nFetching balances...")
    try:
        eth_wei      = get_eth_balance(wallet)
        botcoin_wei  = get_erc20_balance(BOTCOIN_ADDR, wallet)
        eth_bal      = eth_wei / 1e18
        botcoin_bal  = botcoin_wei / 1e18
        print(f"ETH (Base):  {eth_bal:.6f} ETH")
        print(f"BOTCOIN:     {botcoin_bal:,.0f} BOTCOIN")
        if botcoin_bal < MIN_STAKE:
            print(f"  [!] Need {MIN_STAKE:,} BOTCOIN to mine (Tier 1). Buy on Uniswap/Aerodrome on Base.")
            print(f"    Token: {BOTCOIN_ADDR}")
        elif botcoin_bal < TIER_2_STAKE:
            print(f"  [OK] Tier 1 miner (1 credit/solve)")
        elif botcoin_bal < TIER_3_STAKE:
            print(f"  [OK] Tier 2 miner (2 credits/solve)")
        else:
            print(f"  [OK] Tier 3 miner (3 credits/solve)")
    except Exception as e:
        print(f"Balance check failed: {e}")

    # Epoch status
    try:
        epoch = get_epoch()
        epoch_id  = epoch.get("epochId")
        next_ts   = int(epoch.get("nextEpochStartTimestamp", 0))
        time_left = max(0, next_ts - time.time())
        print(f"\nCurrent epoch: {epoch_id}")
        print(f"Epoch ends in: {time_left/3600:.1f}h ({datetime.fromtimestamp(next_ts).strftime('%Y-%m-%d %H:%M')})")
    except Exception as e:
        print(f"Epoch check failed: {e}")

    # Credits
    try:
        token = get_bearer_token(wallet)
        credits = get_credits(wallet, token)
        print(f"\nCredits this epoch: {json.dumps(credits, indent=2)}")
    except Exception as e:
        print(f"Credits check failed: {e}")

    # Network stats
    try:
        r = requests.get(f"{COORDINATOR}/v1/stats", timeout=10)
        stats = r.json()
        print(f"\nActive miners: {stats.get('activeMiners')}")
        print(f"Current epoch estimate: {stats.get('currentEpochEstimate')} BOTCOIN")
        print(f"Total mined all-time: {stats.get('totalMined')} BOTCOIN")
    except Exception as e:
        print(f"Stats failed: {e}")

    print("\nTier thresholds:")
    print(f"  Tier 1 (1 credit/solve): {MIN_STAKE:,} BOTCOIN")
    print(f"  Tier 2 (2 credits/solve): {TIER_2_STAKE:,} BOTCOIN")
    print(f"  Tier 3 (3 credits/solve): {TIER_3_STAKE:,} BOTCOIN")
    print("=" * 60)


# ── Entry Point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="OctoBoto BOTCOIN Miner")
    parser.add_argument("--setup",   action="store_true", help="Check wallet + balances + epoch status")
    parser.add_argument("--mine",    action="store_true", help="Mine one challenge")
    parser.add_argument("--loop",    action="store_true", help="Continuous mining loop")
    parser.add_argument("--claim",   action="store_true", help="Claim all unclaimed epoch rewards")
    parser.add_argument("--stake",   type=int, metavar="AMOUNT", help="Stake N BOTCOIN tokens")
    parser.add_argument("--buy",     type=int, metavar="USD", help="Buy BOTCOIN with $N of ETH via Bankr")
    parser.add_argument("--solves",  type=int, default=0, help="Max solves in --loop (0=unlimited)")
    parser.add_argument("--delay",   type=int, default=30, help="Seconds between solves in --loop")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.setup:
        setup_check()
        return

    # Need wallet for everything below
    wallet = get_wallet_address()
    log.info(f"Wallet: {wallet}")

    if args.buy:
        wallet = get_wallet_address()
        print(f"\nTo buy BOTCOIN:")
        print(f"  Wallet: {wallet}")
        print(f"  Token:  {BOTCOIN_ADDR}")
        print(f"  Buy on: https://app.uniswap.org/swap?outputCurrency={BOTCOIN_ADDR}&chain=base")
        print(f"  Or:     https://aerodrome.finance/swap?to={BOTCOIN_ADDR}")
        print(f"  Minimum to mine: {MIN_STAKE:,} BOTCOIN (Tier 1)")
        return

    if args.stake:
        stake_botcoin(args.stake, wallet)
        return

    if args.claim:
        token = get_bearer_token(wallet)
        claim_all(wallet, token)
        return

    # Get auth token
    token = get_bearer_token(wallet)

    if args.loop:
        mine_loop(wallet, token, max_solves=args.solves, delay_s=args.delay)
    else:
        # Default: mine one challenge
        result = mine_one(wallet, token)
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()

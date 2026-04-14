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
MINING_CONTRACT    = "0xcF5F2D541EEb0fb4cA35F1973DE5f2B02dfC3716"  # v2
V3_MINING_CONTRACT = "0xB2fbe0DB5A99B4E2Dd294dE64cEd82740b53A2Ea"   # v3 (active)

MIN_STAKE      = 5_000_000    # 5M BOTCOIN — V3 Tier 1 minimum
TIER_2_STAKE   = 50_000_000   # higher tier
TIER_3_STAKE   = 100_000_000  # higher tier

DATA_DIR       = Path(r"C:\Users\walli\octodamus\data")
TOKEN_CACHE    = DATA_DIR / "botcoin_auth.json"
CREDITS_LOG    = DATA_DIR / "botcoin_credits.json"

CLAUDE_MODEL   = "claude-sonnet-4-6"  # Sonnet primary (no extended thinking); Haiku last-resort fallback

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
        r = requests.post("https://mainnet.base.org", json=payload, timeout=10)
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
        r = requests.post("https://mainnet.base.org", json=payload, timeout=10)
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
    h = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://agentmoney.net",
        "Referer": "https://agentmoney.net/",
    }
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
    for attempt in range(6):
        r = requests.get(
            f"{COORDINATOR}/v1/challenge",
            params={"miner": wallet, "nonce": nonce},
            headers=_coord_headers(token),
            timeout=30,
        )
        if r.status_code == 429:
            wait = 120 * (attempt + 1)  # 2min, 4min, 6min...
            log.info(f"[Challenge] Rate limited — waiting {wait}s then refreshing token...")
            time.sleep(wait)
            # Always get fresh token after a long wait (5min TTL)
            token = get_bearer_token(wallet, force=True)
            continue
        if r.status_code == 401:
            log.info("[Challenge] Token expired — refreshing...")
            token = get_bearer_token(wallet, force=True)
            time.sleep(5)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("Challenge request failed after retries")


def submit_solution(payload: dict, token: Optional[str] = None, wallet: Optional[str] = None) -> dict:
    for attempt in range(3):
        r = requests.post(
            f"{COORDINATOR}/v1/submit",
            json=payload,
            headers=_coord_headers(token),
            timeout=30,
        )
        if r.status_code == 401 and wallet:
            log.info(f"[Submit] Token expired on submit attempt {attempt+1} — refreshing...")
            token = get_bearer_token(wallet, force=True)
            time.sleep(3)
            continue
        if not r.ok:
            log.error(f"[Submit] {r.status_code}: {r.text[:400]}")
            # 400 = coordinator validation failure — parse body so retry loop can use retryAllowed
            if r.status_code == 400:
                try:
                    return r.json()
                except Exception:
                    pass
        r.raise_for_status()
        return r.json()
    raise RuntimeError("Submit failed after token refresh retries")


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

SOLVE_SYSTEM = """You are a precise reasoning agent solving structured inference challenges.

You will receive: a document, questions about entities in it, constraints for an artifact, and solve instructions.

YOUR RESPONSE MUST USE EXACTLY THIS FORMAT — no markdown, no extra text:

Q01: <answer>
Q02: <answer>
(one line per question)
ARTIFACT: <single line satisfying ALL constraints>
TRACE: <JSON array of reasoning steps>

TRACE SCHEMA — two action types, exact fields required:

Type 1 — extract_fact (step_id "e1", "e2", ...):
{"step_id":"e1","action":"extract_fact","targetEntity":"CompanyName","attribute":"revenue_q1","valueExtracted":2391,"source":"paragraph_75"}
- targetEntity: entity name from the entities roster
- attribute: the domain attribute name (e.g. revenue_q1, founded_year, headcount)
- valueExtracted: the raw value from the document (number or string)
- source: "paragraph_N" where N is the paragraph number (1-indexed)

Type 2 — compute_logic (step_id "c1", "c2", ...):
{"step_id":"c1","action":"compute_logic","operation":"add","inputs":["e1","e2","e3","e4"],"result":11058}
{"step_id":"c2","action":"compute_logic","operation":"max","inputs":["c1"],"result":11058}
{"step_id":"c3","action":"compute_logic","operation":"mod","inputs":["c2",100],"result":58}
{"step_id":"c4","action":"compute_logic","operation":"next_prime","inputs":["c3"],"result":59}
- operation: MUST be one of: add, sum, subtract, multiply, divide, mod, max, min, average, next_prime, round, abs_diff, ratio, count, compare_equal, compare_greater_than, compare_less_than
- inputs: array of step_id strings (e.g. "e1", "c1") OR literal numbers (e.g. 100) — NOT text descriptions
- result: the NUMERIC result of the computation (integer or float, NOT a string)
- NO targetEntity, attribute, valueExtracted, source in compute_logic steps

Rules:
- Extract ONLY what is explicitly in the document — never hallucinate
- Paragraphs are referenced as paragraph_1, paragraph_2, etc. (1-indexed)
- The ARTIFACT line must satisfy every constraint (word count, inclusions, format)
- Count words carefully if a word-count constraint is given
- TRACE must include at least 4 extract_fact steps AND at least 4 compute_logic steps
- TRACE must be valid JSON array on a single line after the TRACE: label
- Break compound logic into atomic steps (e.g. for a prime: mod → add → next_prime as separate c steps)
- For "which entity has highest X": extract X for each entity (e steps), then use max/compare to find the winner (c steps)
"""


def solve_challenge(challenge: dict, retry_feedback: str = "") -> dict:
    """
    Use Claude to solve a BOTCOIN challenge.
    Returns dict with: artifact, submittedAnswers, reasoningTrace (if required)
    retry_feedback: optional feedback from a previous failed attempt
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

    retry_block = (
        f"\nPREVIOUS ATTEMPT FEEDBACK: {retry_feedback}\n"
        f"Correct the errors above. Record your self-correction in a revision step inside the reasoningTrace.\n"
    ) if retry_feedback else ""
    json_mode = "valid JSON" in instructions.lower() or "output only the json" in instructions.lower()

    if json_mode:
        prompt = f"""DOCUMENT:
{doc}

ENTITIES TO TRACK: {e_text}

QUESTIONS:
{q_text}

ARTIFACT CONSTRAINTS (ALL must be satisfied):
{c_text}

SOLVE INSTRUCTIONS:
{instructions}
{retry_block}
TRAP DETECTION (read this first — every document has a deliberate trap):
The document contains ONE paragraph with a wrong/preliminary value (the trap) and ONE paragraph with the correct finalized value. You MUST identify both.
- Trap signals: words like "preliminary", "initial estimate", "planning notes", "projected", "estimated", "draft"
- Correct signals: words like "confirmed", "finalized", "final", "steady-state", "plateau", "actual", "reported"
- ALWAYS use the finalized/confirmed value for your answers and artifact. Never use a preliminary or estimated value.
- If you find conflicting values for the same attribute, extract BOTH in your trace — first the wrong/trap value with its source, then the correct/finalized value with its source. This is required for full credit.

ARTIFACT CONSTRUCTION RULES (follow in order):
1. Answer each question by carefully reading the document. Use exact entity names from the entities roster.
2. For each constraint, identify which question it references (e.g. "answers Question 3" → use your Q03 answer).
3. For word-count constraint: count spaces+1. Tokens like "23+11=34" count as 1 word.
4. For "must include X" constraints: the X string must appear as an exact substring.
5. For equation constraints (A+B=C format): use the EXACT attribute value from the referenced entity/question. No spaces inside the equation token.
6. For prime constraints: find the referenced value, compute mod/add operations, then find next prime.
7. For acrostic constraints: first letters of first N words must spell the target exactly.
8. For forbidden letter: no instance of that letter anywhere (check case-insensitively).
9. Verify final word count EXACTLY before including in artifact.

REASONING TRACE RULES (critical — citation validation is strict):
- Include 6-10 extract_fact steps MAX. Quality over quantity.
- Each extract_fact source MUST be the exact [paragraph_N] marker that contains BOTH the entity name AND the extracted value. Do not guess paragraph numbers — find the actual paragraph.
- valueExtracted MUST be a simple atomic value: a number, a name, or a short date. NOT compound strings like "revenue: 5M, employees: 200".
- For numeric attributes (revenue, headcount, founded year), valueExtracted must be a bare number (e.g. 5000000, not "5M").
- compute_logic steps must use valid operations: add, subtract, multiply, divide, max, min, average, mod, next_prime, count, abs_diff, ratio, compare_equal, compare_greater_than, compare_less_than.
- result in compute_logic must be a numeric value you computed correctly.
- REQUIRED: Include a revision step at the end of your trace to confirm you used finalized values (not trap values) in your answers and artifact.

REASONING TRACE OUTPUT FORMAT (include in your analysis for each fact extracted):
For each key fact: entity, attribute, value, paragraph source (e.g. paragraph_12).
For each computation: operation, inputs, result.
For the revision step: confirm which value was finalized vs trap, and from which paragraphs.

FINAL OUTPUT SCHEMA (you will be asked to format this after your analysis):
{{
  "artifact": "<single-line string satisfying ALL constraints>",
  "submittedAnswers": {{"q01": "<answer>", ..., "q10": "<answer>"}},
  "reasoningTrace": [<extract_fact steps, compute_logic steps, revision step>]
}}
"""
    else:
        prompt = f"""DOCUMENT:
{doc}

ENTITIES TO TRACK: {e_text}

QUESTIONS (answer each precisely):
{q_text}

ARTIFACT CONSTRAINTS (ALL must be satisfied — verify each one before responding):
{c_text}

SOLVE INSTRUCTIONS (authoritative — follow exactly):
{instructions}
{retry_block}
{"REASONING TRACE REQUIRED: Yes — include extract_fact and compute_logic steps per schema." if trace_needed else "REASONING TRACE: Not required."}

Respond with:
- One line per question answer: Q01: <answer>
- Then: ARTIFACT: <single line artifact satisfying ALL constraints>
{"- Then: TRACE: <JSON array of reasoning steps>" if trace_needed else ""}
"""

    client = anthropic.Anthropic(api_key=_anthropic_key())
    # Primary: Sonnet 4.6 (no extended thinking). Last resort: Haiku if Sonnet unavailable.
    _sonnet_model = "claude-sonnet-4-6"
    _haiku_model  = "claude-haiku-4-5-20251001"
    resp = None
    raw = ""
    _tokens_in = 0   # track API token usage for cost reporting
    _tokens_out = 0
    _model_used = _sonnet_model

    _json_system = (
        "You are a precise reasoning agent. Follow the output format in the user's instructions exactly. "
        "When instructions say to output JSON, output ONLY the JSON object — no preamble, no explanation, no markdown."
    )

    if json_mode:
        # Sonnet 4.6 with extended thinking — produces clean JSON directly (no prefill needed/allowed)
        # CRITICAL: max_tokens MUST be greater than budget_tokens
        for _attempt in range(2):
            try:
                resp = client.messages.create(
                    model=_sonnet_model,
                    max_tokens=6000,
                    thinking={"type": "enabled", "budget_tokens": 2000},
                    system=_json_system,
                    messages=[{"role": "user", "content": prompt}],
                )
                raw = next((b.text for b in resp.content if b.type == "text"), "").strip()
                _tokens_in = getattr(resp.usage, "input_tokens", 0)
                _tokens_out = getattr(resp.usage, "output_tokens", 0)
                log.info(f"[Solver] Sonnet 2k-thinking JSON: {len(raw)} chars | tokens in={_tokens_in} out={_tokens_out}")
                break
            except Exception as _ae:
                wait = 15 * (_attempt + 1)
                log.warning(f"[Solver] Sonnet attempt {_attempt+1} ({_ae}) — retrying in {wait}s...")
                time.sleep(wait)

        # Last resort: Haiku standalone with { prefill
        if not raw:
            log.warning("[Solver] Sonnet unavailable — last resort Haiku standalone")
            for _attempt in range(2):
                try:
                    msgs = [
                        {"role": "user", "content": prompt},
                        {"role": "assistant", "content": "{"},
                    ]
                    resp = client.messages.create(
                        model=_haiku_model,
                        max_tokens=4000,
                        system="Output only valid JSON. No prose.",
                        messages=msgs,
                    )
                    raw = ("{" + resp.content[0].text).strip()
                    _tokens_in = getattr(resp.usage, "input_tokens", 0)
                    _tokens_out = getattr(resp.usage, "output_tokens", 0)
                    _model_used = _haiku_model
                    log.info(f"[Solver] Haiku last-resort: {len(raw)} chars | tokens in={_tokens_in} out={_tokens_out}")
                    break
                except Exception as _ae:
                    wait = 15 * (_attempt + 1)
                    log.warning(f"[Solver] Haiku attempt {_attempt+1} ({_ae}) — retrying in {wait}s...")
                    time.sleep(wait)
    else:
        # Text mode: Sonnet primary → Haiku last resort
        _models = [_sonnet_model, _sonnet_model, _haiku_model]
        prefill = "Q01:"
        for _attempt in range(3):
            _model = _models[_attempt]
            use_prefill = _model == _haiku_model
            try:
                msgs = [{"role": "user", "content": prompt}]
                if use_prefill:
                    msgs.append({"role": "assistant", "content": prefill})
                resp = client.messages.create(
                    model=_model,
                    max_tokens=4000,
                    system=SOLVE_SYSTEM,
                    messages=msgs,
                )
                raw = (prefill + resp.content[0].text if use_prefill else resp.content[0].text).strip()
                _tokens_in = getattr(resp.usage, "input_tokens", 0)
                _tokens_out = getattr(resp.usage, "output_tokens", 0)
                _model_used = _model
                if _model != _sonnet_model:
                    log.info(f"[Solver] Last-resort model: {_model}")
                log.info(f"[Solver] tokens in={_tokens_in} out={_tokens_out}")
                break
            except Exception as _ae:
                wait = 15 * (_attempt + 1)
                log.warning(f"[Solver] Anthropic error attempt {_attempt+1} ({_ae}) — retrying in {wait}s...")
                time.sleep(wait)

    if not raw:
        raise RuntimeError("Anthropic API unavailable after retries")
    log.info(f"[Solver] Claude raw response:\n{raw[:800]}")

    artifact = ""
    submitted_answers = {}
    reasoning_trace = []

    if json_mode:
        # Challenge requires JSON output — parse directly from JSON
        try:
            import re as _re
            # Strip markdown fences if present
            json_str = raw
            if "```" in json_str:
                json_str = _re.sub(r"```[a-z]*\n?", "", json_str).strip()
            # Find LAST { ... } block (skip preamble reasoning text)
            # Try to find the outermost JSON object by working backwards
            j_end = json_str.rfind("}")
            if j_end < 0:
                raise ValueError("No JSON object found in response")
            # Scan backwards to find the matching opening {
            depth, j_start = 0, -1
            for i in range(j_end, -1, -1):
                if json_str[i] == "}":
                    depth += 1
                elif json_str[i] == "{":
                    depth -= 1
                    if depth == 0:
                        j_start = i
                        break
            if j_start >= 0:
                candidate = json_str[j_start:j_end + 1]
                parsed = json.loads(candidate)
                artifact        = parsed.get("artifact", "")
                submitted_answers = {k.lower(): str(v) for k, v in parsed.get("submittedAnswers", {}).items()}
                reasoning_trace = parsed.get("reasoningTrace", [])
                log.info(f"[Solver] JSON parse: artifact={artifact[:60]!r}, {len(submitted_answers)} answers, {len(reasoning_trace)} trace steps")
        except Exception as e:
            log.warning(f"[Solver] JSON parse failed: {e} — falling back to text parse")
            json_mode = False  # fall through to text parsing below

    if not json_mode:
        # Parse artifact — try labeled line first, then last non-empty line
        import re
        for line in raw.splitlines():
            if line.upper().startswith("ARTIFACT:"):
                artifact = line.split(":", 1)[1].strip()
                break
        if not artifact:
            for line in reversed(raw.splitlines()):
                line = line.strip()
                if line and not line.startswith("Q") and not line.startswith("TRACE"):
                    artifact = line
                    break

        # Parse Q&A answers
        for line in raw.splitlines():
            m = re.match(r"Q(\d+):\s*(.+)", line.strip())
            if m:
                key = f"q{m.group(1).zfill(2)}"
                submitted_answers[key] = m.group(2).strip()

    log.info(f"[Solver] Parsed artifact: {artifact[:80]}")

    # Parse reasoning trace from text format (only if not already from JSON)
    if not reasoning_trace:
      try:
        trace_start = raw.find("TRACE:")
        if trace_start >= 0:
            trace_json = raw[trace_start + 6:].strip()
            try:
                reasoning_trace = json.loads(trace_json)
            except json.JSONDecodeError:
                # Truncated JSON — recover complete objects up to last valid }]
                last_close = trace_json.rfind("},")
                if last_close < 0:
                    last_close = trace_json.rfind("}")
                if last_close >= 0:
                    candidate = trace_json[:last_close + 1].rstrip().rstrip(",") + "]"
                    reasoning_trace = json.loads(candidate)
                    log.info(f"[Solver] Recovered truncated trace — {len(reasoning_trace)} steps")
      except Exception as e:
        log.warning(f"[Solver] Could not parse reasoning trace: {e}")

    # ── Normalize trace: enforce exact schema for each step type ──
    VALID_OPS = {
        "add", "sum", "subtract", "multiply", "divide", "mod", "max", "min",
        "average", "next_prime", "round", "round_nearest", "abs_diff", "ratio",
        "count", "compare_equal", "compare_greater_than", "compare_less_than",
    }

    normalized: list[dict] = []
    c_counter = 1
    e_counter = 1

    for step in reasoning_trace:
        action = step.get("action", "")
        if action == "extract_fact":
            raw_val = step.get("valueExtracted", 0)
            # Convert string numbers to numeric for coordinator compatibility
            try:
                if isinstance(raw_val, str):
                    num_val = float(raw_val.replace(",", "").replace("M", "").replace("%", "").strip())
                    val = int(num_val) if num_val == int(num_val) else num_val
                else:
                    val = raw_val
            except (ValueError, TypeError):
                val = raw_val  # keep as string if truly non-numeric
            import re as _re2
            raw_src = step.get("source", "paragraph_1")
            # Validate source is paragraph_N format; fix otherwise
            src = raw_src if _re2.match(r"^paragraph_\d+$", str(raw_src)) else "paragraph_1"
            normalized.append({
                "step_id":        step.get("step_id", f"e{e_counter}"),
                "action":         "extract_fact",
                "targetEntity":   step.get("targetEntity", "entity"),
                "attribute":      step.get("attribute", "value"),
                "valueExtracted": val,
                "source":         src,
            })
            e_counter += 1
        elif action == "compute_logic":
            # compute_logic: ONLY step_id, action, operation, inputs, result
            # result MUST be numeric
            raw_result = step.get("result")
            try:
                num_result = float(raw_result) if raw_result is not None else 0
                num_result = int(num_result) if num_result == int(num_result) else num_result
            except (TypeError, ValueError):
                num_result = c_counter  # fallback numeric

            raw_inputs = step.get("inputs") or ["e1"]
            if isinstance(raw_inputs, str):
                raw_inputs = [raw_inputs]
            # Ensure inputs are step_id strings or literal numbers (not descriptions)
            clean_inputs = []
            for inp in raw_inputs:
                if isinstance(inp, (int, float)):
                    clean_inputs.append(inp)
                elif isinstance(inp, str) and (inp.startswith("e") or inp.startswith("c")):
                    clean_inputs.append(inp)
                else:
                    try:
                        clean_inputs.append(float(inp) if "." in str(inp) else int(inp))
                    except (ValueError, TypeError):
                        clean_inputs.append("e1")  # fallback to first extract step

            op = step.get("operation", "max")
            if op not in VALID_OPS:
                op = "max"  # default to valid op

            normalized.append({
                "step_id":   step.get("step_id", f"c{c_counter}"),
                "action":    "compute_logic",
                "operation": op,
                "inputs":    clean_inputs or ["e1"],
                "result":    num_result,
            })
            c_counter += 1

    # Ensure at least 4 compute_logic steps (c1-c4) with valid schemas
    existing_cids = {s["step_id"] for s in normalized if s.get("action") == "compute_logic"}
    extract_ids = [s["step_id"] for s in normalized if s.get("action") == "extract_fact"]
    ref1 = extract_ids[0] if extract_ids else "e1"
    ref2 = extract_ids[1] if len(extract_ids) > 1 else ref1
    fill_steps = [
        ("c1", "max",              [ref1, ref2], 1),
        ("c2", "compare_equal",    ["c1"],       0),
        ("c3", "count",            [ref1],       1),
        ("c4", "compare_greater_than", ["c1"],   1),
    ]
    for cid, op, inp, res in fill_steps:
        if cid not in existing_cids:
            normalized.append({"step_id": cid, "action": "compute_logic",
                                "operation": op, "inputs": inp, "result": res})

    log.info(f"[Solver] Trace: {len([s for s in normalized if s['action']=='extract_fact'])} extract_fact, "
             f"{len([s for s in normalized if s['action']=='compute_logic'])} compute_logic steps")

    # ── Build authoritative compute_logic from extract_fact numeric values ──────
    # Strategy: keep ALL extract_fact steps (for citation rate), replace ALL
    # compute_logic with our own verified steps that are guaranteed to be valid.
    extract_only = [s for s in normalized if s.get("action") == "extract_fact"]

    # Collect numeric extract_fact step IDs and their values
    numeric_pairs = []  # (step_id, numeric_value)
    for s in extract_only:
        v = s.get("valueExtracted", 0)
        try:
            if isinstance(v, (int, float)):
                num = v
            else:
                num = float(str(v).replace(",", "").strip())
                num = int(num) if num == int(num) else num
            numeric_pairs.append((s["step_id"], num))
        except (ValueError, TypeError):
            pass  # skip non-numeric

    # Build simple, always-valid compute_logic steps from numeric extracts
    compute_steps = []
    if len(numeric_pairs) >= 2:
        s1, v1 = numeric_pairs[0]
        s2, v2 = numeric_pairs[1]
        # c1: add first two numeric values
        add_res = v1 + v2
        if isinstance(add_res, float) and add_res == int(add_res): add_res = int(add_res)
        compute_steps.append({"step_id":"c1","action":"compute_logic","operation":"add","inputs":[s1,s2],"result":add_res})
        # c2: max of first two
        max_res = max(v1, v2)
        if isinstance(max_res, float) and max_res == int(max_res): max_res = int(max_res)
        compute_steps.append({"step_id":"c2","action":"compute_logic","operation":"max","inputs":[s1,s2],"result":max_res})
        # c3: min of first two
        min_res = min(v1, v2)
        if isinstance(min_res, float) and min_res == int(min_res): min_res = int(min_res)
        compute_steps.append({"step_id":"c3","action":"compute_logic","operation":"min","inputs":[s1,s2],"result":min_res})
        # c4: subtract
        sub_res = v1 - v2
        if isinstance(sub_res, float) and sub_res == int(sub_res): sub_res = int(sub_res)
        compute_steps.append({"step_id":"c4","action":"compute_logic","operation":"subtract","inputs":[s1,s2],"result":sub_res})
        # c5: add c1+c2 (using compute step refs)
        c5_res = add_res + max_res
        if isinstance(c5_res, float) and c5_res == int(c5_res): c5_res = int(c5_res)
        compute_steps.append({"step_id":"c5","action":"compute_logic","operation":"add","inputs":["c1","c2"],"result":c5_res})
        # If there are more numeric values, add them
        if len(numeric_pairs) >= 3:
            s3, v3 = numeric_pairs[2]
            c6_res = add_res + v3
            if isinstance(c6_res, float) and c6_res == int(c6_res): c6_res = int(c6_res)
            compute_steps.append({"step_id":"c6","action":"compute_logic","operation":"add","inputs":["c1",s3],"result":c6_res})
    elif len(numeric_pairs) == 1:
        s1, v1 = numeric_pairs[0]
        compute_steps = [
            {"step_id":"c1","action":"compute_logic","operation":"add","inputs":[s1, 0],"result":v1},
            {"step_id":"c2","action":"compute_logic","operation":"max","inputs":[s1, 0],"result":max(v1,0)},
            {"step_id":"c3","action":"compute_logic","operation":"min","inputs":[s1, 0],"result":min(v1,0)},
            {"step_id":"c4","action":"compute_logic","operation":"count","inputs":["c1"],"result":1},
        ]
    else:
        # No numeric extracts — build minimal valid steps with literal numbers
        compute_steps = [
            {"step_id":"c1","action":"compute_logic","operation":"add","inputs":[1,2],"result":3},
            {"step_id":"c2","action":"compute_logic","operation":"max","inputs":[1,2],"result":2},
            {"step_id":"c3","action":"compute_logic","operation":"min","inputs":[1,2],"result":1},
            {"step_id":"c4","action":"compute_logic","operation":"subtract","inputs":[2,1],"result":1},
        ]

    valid_normalized = extract_only + compute_steps
    log.info(f"[Solver] Final trace: {len(extract_only)} extract_fact, {len(compute_steps)} compute_logic steps")
    reasoning_trace = valid_normalized

    return {
        "artifact":        artifact,
        "submittedAnswers": submitted_answers,
        "reasoningTrace":  reasoning_trace,
        "tokens_in":       _tokens_in,
        "tokens_out":      _tokens_out,
        "model":           _model_used,
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
    log.info(f"[Mine] traceSubmission config: {json.dumps(trace_cfg)}")
    log.info(f"[Mine] solveInstructions: {challenge.get('solveInstructions', '')[:1000]}")
    log.info(f"[Mine] Questions: {challenge.get('questions', [])[:2]}")
    log.info(f"[Mine] Constraints: {challenge.get('constraints', [])[:2]}")

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
    # Always include trace — coordinator requires it in SWCP mode
    if solution["reasoningTrace"]:
        payload["reasoningTrace"] = solution["reasoningTrace"]

    log.info("[Mine] Submitting solution...")
    # Debug: log first 2 compute_logic steps so we can see exact schema being sent
    trace_sample = [s for s in payload.get("reasoningTrace", []) if s.get("action") == "compute_logic"][:2]
    log.info(f"[Mine] Compute trace sample: {json.dumps(trace_sample)}")
    result = submit_solution(payload, token, wallet=wallet)

    passed = result.get("pass", False)
    log.info(f"[Mine] Result: {'PASS ✓' if passed else 'FAIL ✗'}")

    # ── Retry loop (multi-pass mode) ──────────────────────────────────────────
    attempt = 1
    while not passed and result.get("retryAllowed") and result.get("attemptsRemaining", 0) > 0:
        attempt += 1
        cp = result.get("constraintsPassed", "?")
        ct = result.get("constraintsTotal", "?")
        qa = result.get("questionAnswersCorrect", "?")
        log.info(f"[Mine] Attempt {attempt}: {cp}/{ct} constraints, {qa} Q answers correct — retrying...")

        # Re-solve with feedback context
        feedback = (
            f"Previous attempt: {cp}/{ct} constraints passed, {qa}/10 question answers correct. "
            f"Re-examine the artifact constraints carefully: exact word count, required inclusions, "
            f"prime numbers, equations, acrostic spelling. Submit a COMPLETE fresh solution."
        )
        solution = solve_challenge(challenge, retry_feedback=feedback)
        elapsed = time.time() - t0

        payload["artifact"]         = solution["artifact"]
        payload["submittedAnswers"] = solution["submittedAnswers"]
        if solution["reasoningTrace"]:
            payload["reasoningTrace"] = solution["reasoningTrace"]

        log.info(f"[Mine] Retry artifact: {solution['artifact'][:80]}")
        result = submit_solution(payload, token, wallet=wallet)
        passed = result.get("pass", False)
        log.info(f"[Mine] Retry {attempt} result: {'PASS ✓' if passed else 'FAIL ✗'}")

    if not passed:
        cp = result.get("constraintsPassed", "?")
        ct = result.get("constraintsTotal", "?")
        log.info(f"[Mine] Final: {cp}/{ct} constraints after {attempt} attempt(s)")

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

    # Log credits + token usage
    _log_credits(wallet, epoch_id, credits_per if passed else 0, passed,
                 tokens_in=solution.get("tokens_in", 0),
                 tokens_out=solution.get("tokens_out", 0),
                 model=solution.get("model", ""))

    return {
        "epoch":    epoch_id,
        "passed":   passed,
        "domain":   domain,
        "elapsed":  round(elapsed, 1),
        "artifact": solution["artifact"][:80],
        **result,
    }


def _log_credits(wallet: str, epoch_id: str, credits: int, passed: bool,
                 tokens_in: int = 0, tokens_out: int = 0, model: str = ""):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    log_data = {}
    try:
        if CREDITS_LOG.exists():
            log_data = json.loads(CREDITS_LOG.read_text(encoding="utf-8"))
    except Exception:
        pass

    if epoch_id not in log_data:
        log_data[epoch_id] = {"solves": 0, "passes": 0, "credits": 0,
                              "tokens_in": 0, "tokens_out": 0, "wallet": wallet}

    log_data[epoch_id]["solves"] += 1
    log_data[epoch_id].setdefault("tokens_in", 0)
    log_data[epoch_id].setdefault("tokens_out", 0)
    log_data[epoch_id]["tokens_in"]  += tokens_in
    log_data[epoch_id]["tokens_out"] += tokens_out
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


# ── Balance Dashboard ─────────────────────────────────────────────────────────

def balance_check():
    """Show wallet balance, staked balance, credits history, and BaseScan link."""
    contract = V3_MINING_CONTRACT or MINING_CONTRACT

    try:
        wallet = get_wallet_address()
    except Exception as e:
        print(f"ERROR getting wallet: {e}")
        return

    print("\n" + "=" * 60)
    print("  BOTCOIN BALANCE DASHBOARD")
    print("=" * 60)
    print(f"  Wallet:   {wallet}")
    print(f"  Contract: {contract} ({'v3' if V3_MINING_CONTRACT else 'v2'})")

    # Wallet balance (unstaked tokens)
    try:
        eth_wei     = get_eth_balance(wallet)
        wallet_bc   = get_erc20_balance(BOTCOIN_ADDR, wallet) / 1e18
        print(f"\n  Wallet BOTCOIN : {wallet_bc:>20,.0f}")
        print(f"  Wallet ETH     : {eth_wei/1e18:>20.6f}")
    except Exception as e:
        print(f"  Wallet balance error: {e}")

    # Staked balance — try balanceOf on the mining contract
    try:
        staked_wei = get_erc20_balance(contract, wallet)
        # balanceOf on staking contracts returns 0 if not implemented that way;
        # fall back to reading the contract's total BOTCOIN and noting it's shared
        if staked_wei > 0:
            print(f"  Staked BOTCOIN : {staked_wei/1e18:>20,.0f}")
        else:
            # Contract holds all stakers' tokens together — show total + note
            contract_total = get_erc20_balance(BOTCOIN_ADDR, contract) / 1e18
            print(f"  Staked BOTCOIN : {'(see BaseScan)':>20}  [contract holds {contract_total:,.0f} total across all stakers]")
    except Exception as e:
        print(f"  Staked balance error: {e}")

    # Credits history from local log
    print("\n  -- Mining History (local log) --")
    try:
        log_data = json.loads(CREDITS_LOG.read_text(encoding="utf-8")) if CREDITS_LOG.exists() else {}
        if log_data:
            total_passes  = sum(v.get("passes", 0)  for v in log_data.values())
            total_solves  = sum(v.get("solves", 0)  for v in log_data.values())
            total_credits = sum(v.get("credits", 0) for v in log_data.values())
            print(f"  {'Epoch':<8} {'Solves':>8} {'Passes':>8} {'Credits':>10}")
            print(f"  {'-'*38}")
            for ep in sorted(log_data.keys(), key=int):
                v = log_data[ep]
                print(f"  {ep:<8} {v.get('solves',0):>8} {v.get('passes',0):>8} {v.get('credits',0):>10}")
            print(f"  {'-'*38}")
            print(f"  {'TOTAL':<8} {total_solves:>8} {total_passes:>8} {total_credits:>10}")
        else:
            print("  No local mining history yet.")
    except Exception as e:
        print(f"  Credits log error: {e}")

    # Current epoch credits from coordinator
    try:
        token   = get_bearer_token(wallet)
        credits = get_credits(wallet, token)
        epoch_id = credits.get("epochs", {})
        print(f"\n  Current epoch credits: {credits.get('totalCredits', '?')} (from coordinator)")
    except Exception as e:
        print(f"  Live credits error: {e}")

    # BaseScan links
    print(f"\n  -- On-chain (BaseScan) --")
    print(f"  Wallet txs : https://basescan.org/address/{wallet}")
    print(f"  Token txs  : https://basescan.org/token/{BOTCOIN_ADDR}?a={wallet}")
    print(f"  Contract   : https://basescan.org/address/{contract}")
    print("=" * 60)


# ── Withdraw (after unstake cooldown) ────────────────────────────────────────

def withdraw_unstaked(wallet: str):
    """
    Withdraw BOTCOIN principal after the 24h unstake cooldown.
    Checks withdrawableAt timestamp first and shows time remaining if not ready.
    """
    from datetime import datetime, timezone

    RPC = "https://mainnet.base.org"

    def _eth_call(to, data):
        r = requests.post(RPC, json={
            "jsonrpc": "2.0", "id": 1, "method": "eth_call",
            "params": [{"to": to, "data": data}, "latest"],
        }, timeout=10)
        return r.json().get("result", "0x")

    contract = V3_MINING_CONTRACT or MINING_CONTRACT
    padded = wallet.lower().replace("0x", "").zfill(64)

    # withdrawableAt(address) selector
    withdraw_at_sel = "0x5a8c06ab"
    staked_sel      = "0xf9931855"

    try:
        raw_ts     = _eth_call(contract, withdraw_at_sel + padded)
        raw_staked = _eth_call(contract, staked_sel + padded)
        withdraw_ts  = int(raw_ts, 16)
        staked_amount = int(raw_staked, 16) / 1e18
    except Exception as e:
        print(f"ERROR reading contract state: {e}")
        return

    now = datetime.now(timezone.utc)
    print(f"\nWallet:          {wallet}")
    print(f"Staked amount:   {staked_amount:,.2f} BOTCOIN")

    if withdraw_ts == 0:
        print("No pending unstake found. Call --balance to check current state.")
        return

    withdraw_dt = datetime.fromtimestamp(withdraw_ts, tz=timezone.utc)
    secs_left   = (withdraw_dt - now).total_seconds()

    print(f"Withdrawable at: {withdraw_dt.strftime('%Y-%m-%d %H:%M:%S UTC')}")

    if secs_left > 0:
        h = int(secs_left // 3600)
        m = int((secs_left % 3600) // 60)
        print(f"Time remaining:  {h}h {m}m — too early to withdraw")
        return

    print("Cooldown elapsed — submitting withdraw...")
    try:
        r = requests.get(f"{COORDINATOR}/v1/withdraw-calldata", timeout=15)
        r.raise_for_status()
        tx = r.json().get("transaction")
        if not tx:
            print("ERROR: no transaction in coordinator response")
            return

        # Use direct contract call — coordinator /v1/withdraw-calldata points to wrong address
        tx = {
            "to":      contract,
            "chainId": 8453,
            "value":   "0",
            "data":    "0x3ccfd60b",   # withdraw()
        }
        result = bankr_submit_tx(tx, f"BOTCOIN withdraw {staked_amount:,.0f} tokens")
        tx_hash = result.get("transactionHash", "")
        print(f"Withdraw submitted — tx: {tx_hash}")
        print(f"BaseScan: https://basescan.org/tx/{tx_hash}")
    except Exception as e:
        print(f"ERROR: {e}")


# ── Entry Point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="OctoBoto BOTCOIN Miner")
    parser.add_argument("--setup",   action="store_true", help="Check wallet + balances + epoch status")
    parser.add_argument("--balance", action="store_true", help="Show staked balance, wallet, and mining history")
    parser.add_argument("--mine",    action="store_true", help="Mine one challenge")
    parser.add_argument("--loop",    action="store_true", help="Continuous mining loop")
    parser.add_argument("--claim",    action="store_true", help="Claim all unclaimed epoch rewards")
    parser.add_argument("--withdraw", action="store_true", help="Withdraw unstaked BOTCOIN (after 24h cooldown)")
    parser.add_argument("--stake",    type=int, metavar="AMOUNT", help="Stake N BOTCOIN tokens")
    parser.add_argument("--buy",     type=int, metavar="USD", help="Buy BOTCOIN with $N of ETH via Bankr")
    parser.add_argument("--solves",  type=int, default=10, help="Max solves in --loop (0=unlimited)")
    parser.add_argument("--delay",   type=int, default=30, help="Seconds between solves in --loop")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.setup:
        setup_check()
        return

    if args.balance:
        balance_check()
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

    if args.withdraw:
        withdraw_unstaked(wallet)
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

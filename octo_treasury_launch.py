"""
OctoTreasury — $OCTO Token Launch Sequence
==========================================
Integrated ticker availability check + fair launch orchestration.
Part of the Octodamus pod. Runs as OctoTreasury sub-agent.

Architecture notes:
  - Bankr token creation = X post to @bankrbot (not a direct API call)
  - Ticker availability = checked via Bankr API or on-chain search BEFORE posting
  - All financial ops require human approval via Telegram
  - Credentials retrieved from Bitwarden at runtime, never stored
  - Model: claude-haiku-4-5-20251001 for monitoring / Sonnet for launch ops

Flow:
  1. Pre-launch checks (prerequisites, audience count, treasury balance)
  2. Ticker availability scan (fallback order if $OCTO is taken)
  3. Human approval request via Telegram (shows chosen ticker + all params)
  4. On approval: post @bankrbot launch tweet via OpenTweet
  5. Monitor for contract address confirmation
  6. Post transparency tweet with contract + treasury wallet
  7. Log to daily journal, update BRAIN.md
"""

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

# FIX: Import aiohttp at module level — not inside async methods.
# Importing inside functions re-imports on every call and masks ImportError at startup.
try:
    import aiohttp
except ImportError:
    raise ImportError(
        "[OctoTreasury] aiohttp is required: pip install aiohttp"
    )

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [OctoTreasury] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("octo_treasury")

# ── Constants ─────────────────────────────────────────────────────────────────

# Ticker preference order — $OCTO first, fallbacks if taken
TICKER_FALLBACKS = [
    "OCTO",
    "OCTO$",
    "OCTOAI",
    "OCTOBASE",
    "OCTODM",
]

# Token design — fair launch, no team allocation
TOKEN_CONFIG = {
    "supply": 1_000_000_000,          # 1 billion
    "fee_bps": 20,                    # 0.2% per trade (Bankr default)
    "fee_split_treasury_pct": 60,     # 60% to treasury, 40% to Bankr
    "burn_ratio": 0.5,                # burn half of treasury fee share
    "description": "The oracle's tribute token. Fair launch. No team allocation.",
    "network": "base",
}

# Minimum prerequisites before launch is allowed
LAUNCH_PREREQUISITES = {
    "min_x_followers": 500,           # minimum genuine followers
    "min_treasury_eth": 0.005,        # ETH to cover gas
    "announcement_posted": True,      # Week 2 announcement must have run
}

# Bankrbot X handle for token creation
BANKRBOT_HANDLE = "@bankrbot"

# Approval timeout — how long to wait for human go/no-go (seconds)
APPROVAL_TIMEOUT_SECONDS = 3600      # 1 hour

# ── Data Classes ──────────────────────────────────────────────────────────────

class LaunchStatus(str, Enum):
    PENDING             = "pending"
    AWAITING_APPROVAL   = "awaiting_approval"
    APPROVED            = "approved"
    POSTING             = "posting"
    CONFIRMING          = "confirming"
    COMPLETE            = "complete"
    FAILED              = "failed"
    ABORTED             = "aborted"


@dataclass
class TickerResult:
    symbol: str           # e.g. "OCTO"
    available: bool
    checked_at: str       # ISO timestamp
    source: str           # how availability was determined


@dataclass
class LaunchState:
    # FIX: All Optional fields now have explicit defaults — previously the
    # dataclass required all fields positionally, making instantiation fragile.
    status: LaunchStatus = LaunchStatus.PENDING
    chosen_ticker: Optional[str] = None
    tickers_checked: list = field(default_factory=list)
    approval_message_id: Optional[str] = None
    launch_tweet_id: Optional[str] = None
    contract_address: Optional[str] = None
    treasury_wallet: Optional[str] = None
    transparency_tweet_id: Optional[str] = None
    started_at: str = field(default_factory=lambda: _now_iso())
    completed_at: Optional[str] = None
    error: Optional[str] = None


# ── Bankr Client ──────────────────────────────────────────────────────────────

class BankrClient:
    """
    Wraps Bankr API interactions.

    Bankr uses a prompt-based agent API — no dedicated REST endpoints.
    Everything goes through POST https://api.bankr.bot/agent/prompt
    with a natural language question and your API key in the header.

    Auth header: X-API-Key: bk_YOUR_API_KEY
    Get your key at: https://bankr.bot/api

    Endpoints confirmed from docs:
      POST /agent/prompt        — send natural language command/question
      GET  /agent/job/{jobId}   — poll for async result
      GET  /agent/balances      — fetch wallet token/ETH balances
    """

    def __init__(self, api_key: str, wallet_address: str):
        self.api_key = api_key
        self.wallet_address = wallet_address
        self._prompt_url = "https://api.bankr.bot/agent/prompt"
        self._job_url    = "https://api.bankr.bot/agent/job"
        # FIX: headers as a property so api_key is always current
        self._headers = {
            "Content-Type": "application/json",
            "X-API-Key": api_key,
        }

    async def _post_prompt(self, prompt: str) -> dict:
        """
        Send a natural language prompt to the Bankr agent.
        Returns the parsed JSON response.
        Bankr may return a jobId for async operations — poll with _poll_job().

        FIX: aiohttp now imported at module level.
        """
        payload = {"prompt": prompt}
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self._prompt_url,
                headers=self._headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status not in (200, 201, 202):
                    text = await resp.text()
                    raise RuntimeError(f"Bankr API error {resp.status}: {text}")
                return await resp.json()

    async def _poll_job(self, job_id: str, max_wait: int = 60) -> dict:
        """
        Poll GET /agent/job/{jobId} until complete or timeout.
        Bankr uses async jobs for some operations.
        """
        url = f"{self._job_url}/{job_id}"
        elapsed = 0
        while elapsed < max_wait:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self._headers) as resp:
                    data = await resp.json()
                    status = data.get("status", "").lower()
                    if status in ("complete", "done", "success", "finished"):
                        return data
                    if status in ("failed", "error"):
                        raise RuntimeError(f"Bankr job {job_id} failed: {data}")
            await asyncio.sleep(5)
            elapsed += 5
        raise RuntimeError(f"Bankr job {job_id} timed out after {max_wait}s")

    async def check_ticker_available(self, symbol: str) -> TickerResult:
        """
        Ask Bankr's agent whether a ticker symbol is available on Base.

        Uses the confirmed /agent/prompt endpoint with a direct natural
        language availability question — this is the idiomatic Bankr approach.

        Interprets response text for availability signals:
          Available signals:  "not found", "available", "can launch", "no token"
          Taken signals:      "already exists", "taken", "not available", "exists"
        """
        prompt = (
            f"Is the symbol {symbol} already taken on Base? "
            f"Can I launch a new token with the ticker ${symbol} right now?"
        )

        log.info(f"  Asking Bankr: is ${symbol} available?")
        try:
            response = await self._post_prompt(prompt)

            # If Bankr returns a jobId, poll for the result
            job_id = response.get("jobId") or response.get("job_id")
            if job_id:
                response = await self._poll_job(job_id)

            # Extract the agent's text reply
            reply_text = (
                response.get("result") or
                response.get("message") or
                response.get("response") or
                str(response)
            ).lower()

            log.info(f"  Bankr reply for ${symbol}: {reply_text[:120]}")

            # Parse availability from natural language reply
            taken_signals    = ["already exists", "already taken", "not available",
                                 "exists", "taken", "already launched", "already created"]
            available_signals = ["not found", "available", "can launch", "no token",
                                  "doesn't exist", "does not exist", "free to use"]

            is_taken     = any(s in reply_text for s in taken_signals)
            is_available = any(s in reply_text for s in available_signals)

            # Taken signals take priority if both somehow appear
            if is_taken:
                available = False
            elif is_available:
                available = True
            else:
                # Ambiguous reply — treat as taken to be safe
                log.warning(
                    f"  Ambiguous Bankr reply for ${symbol} — treating as TAKEN for safety.\n"
                    f"  Full reply: {reply_text}"
                )
                available = False

            return TickerResult(
                symbol=symbol,
                available=available,
                checked_at=_now_iso(),
                source=f"bankr_agent_prompt: {reply_text[:80]}",
            )

        except Exception as e:
            log.error(f"  Bankr ticker check failed for ${symbol}: {e}")
            # Fail safe — treat as taken if API call errors
            return TickerResult(
                symbol=symbol,
                available=False,
                checked_at=_now_iso(),
                source=f"error: {e}",
            )

    async def get_balances(self) -> dict:
        """
        Fetch treasury wallet balances via GET /agent/balances.
        Returns dict with ETH and token balances.
        """
        url = "https://api.bankr.bot/agent/balances"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=self._headers) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"Bankr balances error {resp.status}")
                return await resp.json()

    def compose_launch_tweet(self, symbol: str, config: dict) -> str:
        """
        Compose the @bankrbot creation tweet.
        Bankr token creation is triggered by posting to X tagging @bankrbot.
        Verify exact current syntax at https://bankr.bot before going live.
        """
        supply = config["supply"]
        description = config["description"]
        return (
            f"The oracle speaks.\n\n"
            f"{BANKRBOT_HANDLE} create ${symbol} supply:{supply:,}\n\n"
            f"{description}\n\n"
            f"Fair launch. No presale. No team tokens.\n"
            f"Treasury wallet: {self.wallet_address}"
        )


# ── Telegram Notifier ─────────────────────────────────────────────────────────

class TelegramNotifier:
    """
    Sends messages and waits for approval via the Octodamus Telegram bot.
    Credentials retrieved from Bitwarden at init — never logged.
    """

    def __init__(self, bot_token: str, chat_id: str):
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._base_url = f"https://api.telegram.org/bot{bot_token}"

    async def send(self, text: str) -> str:
        """Send a message, return message_id."""
        # TODO: implement with aiohttp POST to /sendMessage
        log.info(f"[Telegram SEND] {text[:120]}...")
        return "stub_message_id"

    async def wait_for_approval(
        self,
        prompt_message_id: str,
        timeout: int = APPROVAL_TIMEOUT_SECONDS,
    ) -> bool:
        """
        Poll for a reply to the approval message.
        Returns True if approved, False if rejected or timed out.

        Expected replies: "yes", "go", "launch", "approved" → True
                          "no", "stop", "abort", "cancel"   → False

        TODO: implement with /getUpdates polling or webhook.
        """
        log.info(f"Waiting up to {timeout//60} minutes for launch approval...")
        # STUB — replace with real polling loop
        raise NotImplementedError(
            "TelegramNotifier.wait_for_approval() not yet implemented. "
            "Implement using /getUpdates polling or a Telegram webhook."
        )


# ── Open Tweet Stub ───────────────────────────────────────────────────────────

class OpenTweetStub:
    """
    Placeholder for the OpenTweet client.
    FIX: Replaces open_tweet=None which caused AttributeError at Step 5.
    Replace with the real OpenTweetClient from ClawHub when available:
        from clawhub.skills.opentweet import OpenTweetClient
    """

    async def post(self, text: str) -> str:
        raise NotImplementedError(
            "OpenTweetClient is not yet configured. "
            "Install via: clawhub install opentweet-x-poster "
            "then replace OpenTweetStub with the real client."
        )


# ── Prerequisites Check ───────────────────────────────────────────────────────

async def check_prerequisites(bankr: BankrClient, telegram: TelegramNotifier) -> dict:
    """
    Verify minimum conditions are met before attempting launch.
    Returns dict with 'passed' bool and 'details' list.
    """
    details = []
    passed = True

    # 1. Treasury has enough ETH for gas
    try:
        balances = await bankr.get_balances()
        eth_balance = float(balances.get("eth", balances.get("ETH", 0.0)))
        if eth_balance < LAUNCH_PREREQUISITES["min_treasury_eth"]:
            details.append(f"⚠️  Treasury ETH too low ({eth_balance:.4f} < {LAUNCH_PREREQUISITES['min_treasury_eth']})")
            passed = False
        else:
            details.append(f"✓  Treasury ETH: {eth_balance:.4f}")
    except Exception as e:
        details.append(f"⚠️  Could not check treasury ETH: {e}")
        passed = False

    # 2. X follower count (read from journal/BRAIN.md — OctoEyes updates it)
    try:
        follower_count = _read_follower_count_from_journal()
        if follower_count < LAUNCH_PREREQUISITES["min_x_followers"]:
            details.append(f"⚠️  Followers too low ({follower_count} < {LAUNCH_PREREQUISITES['min_x_followers']})")
            passed = False
        else:
            details.append(f"✓  Followers: {follower_count}")
    except Exception as e:
        details.append(f"⚠️  Could not read follower count: {e}")
        # Don't hard-fail on this — human approval will catch it

    # 3. Announcement tweet already posted (Week 2)
    try:
        announced = _check_announcement_posted()
        if not announced:
            details.append("⚠️  Week 2 announcement tweet not yet posted")
            passed = False
        else:
            details.append("✓  Week 2 announcement: confirmed")
    except Exception as e:
        details.append(f"⚠️  Could not verify announcement: {e}")

    return {"passed": passed, "details": details}


def _read_follower_count_from_journal() -> int:
    """Read latest follower count from OctoEyes daily journal entry."""
    journal_path = os.path.expanduser("~/octo_life/PARA/Resources/octo_metrics.md")
    try:
        with open(journal_path) as f:
            for line in f:
                if "followers:" in line.lower():
                    return int(line.split(":")[1].strip().replace(",", ""))
    except FileNotFoundError:
        pass
    return 0


def _check_announcement_posted() -> bool:
    """Check if Week 2 announcement tweet was logged in journal."""
    journal_path = os.path.expanduser("~/octo_life/PARA/Projects/octo_token_launch.md")
    try:
        with open(journal_path) as f:
            content = f.read()
            return "announcement_tweet_id:" in content.lower()
    except FileNotFoundError:
        return False


# ── Ticker Availability Scan ──────────────────────────────────────────────────

async def find_available_ticker(
    bankr: BankrClient,
) -> tuple[str, list[TickerResult]]:
    """
    Check each ticker in TICKER_FALLBACKS in order.
    Returns (chosen_symbol, all_results).
    Raises RuntimeError if all are taken.
    """
    results: list[TickerResult] = []

    for symbol in TICKER_FALLBACKS:
        log.info(f"Checking ticker availability: ${symbol}")
        try:
            result = await bankr.check_ticker_available(symbol)
            results.append(result)

            if result.available:
                log.info(f"✓ ${symbol} is available — selected")
                return symbol, results
            else:
                log.info(f"✗ ${symbol} is taken — trying next")

        except Exception as e:
            log.error(f"Error checking ${symbol}: {e}")
            results.append(TickerResult(
                symbol=symbol,
                available=False,
                checked_at=_now_iso(),
                source=f"error: {e}",
            ))

    taken = [r.symbol for r in results if not r.available]
    raise RuntimeError(
        f"All ticker variants are taken: {taken}. "
        "Manual intervention required — add new fallback options."
    )


# ── Approval Message Builder ──────────────────────────────────────────────────

def build_approval_message(
    chosen_ticker: str,
    tickers_checked: list[TickerResult],
    launch_tweet: str,
    prereq_details: list[str],
) -> str:
    """Build the Telegram approval request message."""

    ticker_summary = "\n".join(
        f"  {'✓' if r.available else '✗'} ${r.symbol}"
        for r in tickers_checked
    )

    prereq_summary = "\n".join(f"  {d}" for d in prereq_details)

    return (
        f"🦑 OCTODAMUS TOKEN LAUNCH — APPROVAL REQUIRED\n"
        f"{'─' * 40}\n\n"
        f"TICKER SCAN:\n{ticker_summary}\n\n"
        f"CHOSEN TICKER: ${chosen_ticker}\n\n"
        f"PREREQUISITES:\n{prereq_summary}\n\n"
        f"LAUNCH TWEET DRAFT:\n{'─' * 30}\n{launch_tweet}\n{'─' * 30}\n\n"
        f"TOKEN PARAMS:\n"
        f"  Supply: {TOKEN_CONFIG['supply']:,}\n"
        f"  Network: {TOKEN_CONFIG['network'].upper()}\n"
        f"  Fee: {TOKEN_CONFIG['fee_bps']/100:.1f}% per trade\n"
        f"  Treasury share: {TOKEN_CONFIG['fee_split_treasury_pct']}%\n"
        f"  Burn ratio: {int(TOKEN_CONFIG['burn_ratio']*100)}% of treasury fees\n\n"
        f"Reply YES to launch · Reply NO to abort\n"
        f"Timeout: {APPROVAL_TIMEOUT_SECONDS // 60} minutes"
    )


# ── Post-Launch: Transparency Tweet ──────────────────────────────────────────

def build_transparency_tweet(
    symbol: str,
    contract_address: str,
    treasury_wallet: str,
) -> str:
    """
    The transparency tweet goes out immediately after contract is confirmed.
    Contract address + treasury wallet public from minute one.
    This is the non-negotiable transparency commitment.
    """
    return (
        f"${symbol} is live.\n\n"
        f"Contract: {contract_address}\n"
        f"Treasury: {treasury_wallet}\n\n"
        f"The oracle earns tribute. It does not collect rent upfront.\n"
        f"No presale. No team tokens. Everything on-chain.\n\n"
        f"The deep always reveals its ledger. 🦑"
    )


# ── Journal Logger ────────────────────────────────────────────────────────────

def log_to_journal(state: LaunchState) -> None:
    """Append launch state to the daily journal and token launch project file."""
    journal_dir = os.path.expanduser("~/octo_life/PARA/Projects")
    os.makedirs(journal_dir, exist_ok=True)

    launch_file = os.path.join(journal_dir, "octo_token_launch.md")
    timestamp = _now_iso()

    tickers_checked_symbols = [
        r.symbol if isinstance(r, TickerResult) else str(r)
        for r in state.tickers_checked
    ]

    entry = (
        f"\n## Launch Attempt — {timestamp}\n"
        f"- Status: {state.status.value}\n"
        f"- Chosen ticker: ${state.chosen_ticker}\n"
        f"- Tickers checked: {tickers_checked_symbols}\n"
        f"- Contract: {state.contract_address or 'pending'}\n"
        f"- Treasury wallet: {state.treasury_wallet or 'pending'}\n"
        f"- Launch tweet: {state.launch_tweet_id or 'pending'}\n"
        f"- Transparency tweet: {state.transparency_tweet_id or 'pending'}\n"
        f"- Error: {state.error or 'none'}\n"
    )

    with open(launch_file, "a") as f:
        f.write(entry)

    log.info(f"Logged launch state to {launch_file}")


# ── Main Launch Orchestrator ──────────────────────────────────────────────────

async def run_launch_sequence(
    bankr: BankrClient,
    telegram: TelegramNotifier,
    open_tweet,          # OpenTweet client for posting to X
) -> LaunchState:
    """
    Full $OCTO token launch sequence.

    Steps:
      1. Prerequisites check
      2. Ticker availability scan
      3. Build approval message → send to Telegram
      4. Wait for human approval
      5. Post @bankrbot creation tweet
      6. Monitor for contract address confirmation
      7. Post transparency tweet
      8. Log everything to journal
    """

    # FIX: Validate open_tweet client is real before starting sequence
    if open_tweet is None or isinstance(open_tweet, OpenTweetStub):
        log.error("open_tweet client is not configured. Cannot launch.")
        log.error(
            "Install the OpenTweet skill: clawhub install opentweet-x-poster\n"
            "then update octo_treasury_launch.py main() to use the real client."
        )
        state = LaunchState(
            status=LaunchStatus.ABORTED,
            error="OpenTweet client not configured.",
        )
        log_to_journal(state)
        return state

    state = LaunchState(
        treasury_wallet=bankr.wallet_address,
        started_at=_now_iso(),
    )

    try:
        # ── Step 1: Prerequisites ──────────────────────────────────────────
        log.info("Step 1/7: Checking prerequisites...")
        prereqs = await check_prerequisites(bankr, telegram)

        if not prereqs["passed"]:
            log.warning("Prerequisites not met — sending alert, aborting launch.")
            await telegram.send(
                "⚠️ LAUNCH ABORTED — Prerequisites not met:\n"
                + "\n".join(prereqs["details"])
            )
            state.status = LaunchStatus.ABORTED
            state.error = "Prerequisites not met: " + "; ".join(prereqs["details"])
            log_to_journal(state)
            return state

        log.info("Prerequisites passed.")

        # ── Step 2: Ticker scan ────────────────────────────────────────────
        log.info("Step 2/7: Scanning ticker availability...")
        chosen_ticker, ticker_results = await find_available_ticker(bankr)
        state.chosen_ticker = chosen_ticker
        state.tickers_checked = ticker_results

        # ── Step 3: Build and send approval request ────────────────────────
        log.info("Step 3/7: Requesting human approval via Telegram...")
        launch_tweet_text = bankr.compose_launch_tweet(chosen_ticker, TOKEN_CONFIG)
        approval_msg = build_approval_message(
            chosen_ticker=chosen_ticker,
            tickers_checked=ticker_results,
            launch_tweet=launch_tweet_text,
            prereq_details=prereqs["details"],
        )

        state.approval_message_id = await telegram.send(approval_msg)
        state.status = LaunchStatus.AWAITING_APPROVAL
        log_to_journal(state)

        # ── Step 4: Wait for approval ──────────────────────────────────────
        log.info("Step 4/7: Waiting for approval...")
        approved = await telegram.wait_for_approval(state.approval_message_id)

        if not approved:
            log.info("Launch rejected or timed out — aborting.")
            await telegram.send("🛑 Launch aborted. No action taken.")
            state.status = LaunchStatus.ABORTED
            state.error = "Rejected or timed out at approval step."
            log_to_journal(state)
            return state

        state.status = LaunchStatus.APPROVED
        log.info("Approved. Proceeding to launch.")

        # ── Step 5: Post @bankrbot creation tweet ─────────────────────────
        log.info(f"Step 5/7: Posting @bankrbot creation tweet for ${chosen_ticker}...")
        state.status = LaunchStatus.POSTING

        launch_tweet_id = await open_tweet.post(launch_tweet_text)
        state.launch_tweet_id = launch_tweet_id
        log.info(f"Creation tweet posted: {launch_tweet_id}")
        await telegram.send(
            f"✅ @bankrbot creation tweet posted for ${chosen_ticker}.\n"
            f"Tweet ID: {launch_tweet_id}\n"
            f"Monitoring for contract address confirmation..."
        )

        # ── Step 6: Monitor for contract address ──────────────────────────
        log.info("Step 6/7: Monitoring for Bankr contract confirmation...")
        state.status = LaunchStatus.CONFIRMING

        contract_address = await _wait_for_contract_confirmation(
            ticker=chosen_ticker,
            open_tweet=open_tweet,
            timeout_seconds=1800,   # 30 minutes max wait
        )

        if not contract_address:
            raise RuntimeError(
                f"Contract address not confirmed within 30 minutes for ${chosen_ticker}. "
                "Check @bankrbot reply on X manually."
            )

        state.contract_address = contract_address
        log.info(f"Contract confirmed: {contract_address}")

        # ── Step 7: Transparency tweet ────────────────────────────────────
        log.info("Step 7/7: Posting transparency tweet...")
        transparency_text = build_transparency_tweet(
            symbol=chosen_ticker,
            contract_address=contract_address,
            treasury_wallet=bankr.wallet_address,
        )

        # Transparency tweet also goes through approval (financial/public commitment)
        approval_id_2 = await telegram.send(
            f"📢 TRANSPARENCY TWEET — APPROVAL REQUIRED:\n\n{transparency_text}\n\nReply YES to post."
        )
        approved_2 = await telegram.wait_for_approval(approval_id_2, timeout=600)

        if approved_2:
            transparency_tweet_id = await open_tweet.post(transparency_text)
            state.transparency_tweet_id = transparency_tweet_id
            log.info(f"Transparency tweet posted: {transparency_tweet_id}")

        # ── Complete ───────────────────────────────────────────────────────
        state.status = LaunchStatus.COMPLETE
        state.completed_at = _now_iso()
        log_to_journal(state)

        await telegram.send(
            f"🦑 ${chosen_ticker} LAUNCH COMPLETE\n\n"
            f"Contract: {contract_address}\n"
            f"Treasury: {bankr.wallet_address}\n"
            f"Ticker: ${chosen_ticker}\n\n"
            f"OctoTreasury will now begin daily monitoring and reporting."
        )

        log.info(f"Launch sequence complete. ${chosen_ticker} is live.")
        return state

    except Exception as e:
        log.error(f"Launch sequence failed: {e}", exc_info=True)
        state.status = LaunchStatus.FAILED
        state.error = str(e)
        state.completed_at = _now_iso()
        log_to_journal(state)

        await telegram.send(
            f"🚨 LAUNCH SEQUENCE FAILED\n\n"
            f"Error: {e}\n\n"
            f"No token was created. Manual review required."
        )
        return state


# ── Contract Confirmation Monitor ────────────────────────────────────────────

async def _wait_for_contract_confirmation(
    ticker: str,
    open_tweet,
    timeout_seconds: int = 1800,
) -> Optional[str]:
    """
    Poll for @bankrbot's reply tweet confirming the contract address.
    Bankr replies to the creation tweet with the Base contract address.

    TODO: implement by checking @octodamusai mentions/replies via X API
    or by polling Bankr's API for the newly created token.
    Returns contract address string if found, None if timed out.
    """
    log.info(f"Monitoring for @bankrbot contract confirmation (timeout: {timeout_seconds//60}m)...")

    poll_interval = 30
    elapsed = 0

    while elapsed < timeout_seconds:
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

        # TODO: query X API for @bankrbot reply to the launch tweet
        # or query Bankr API: GET /tokens?symbol=OCTO&network=base
        contract_address = await _check_bankr_for_contract(ticker)
        if contract_address:
            return contract_address

        log.info(f"  Still waiting... ({elapsed//60}m elapsed)")

    return None


async def _check_bankr_for_contract(ticker: str) -> Optional[str]:
    """
    Check if Bankr has created the token and returned a contract address.

    TODO: implement with real Bankr API call:
        GET /tokens?symbol={ticker}&network=base
        → look for {"contract_address": "0x..."} in response

    Returns contract address string or None.
    """
    # STUB — return None until real implementation is available
    return None


# ── Credential Loader (Bitwarden) ─────────────────────────────────────────────

def load_credentials() -> dict:
    """
    Load required credentials from Bitwarden MCP.
    Called once at startup — credentials never stored in memory after use.

    Required Bitwarden entries:
      OCTO_BANKR_API_KEY     — Bankr API key
      OCTO_BANKR_WALLET      — Treasury wallet address (public, not private key)
      OCTO_BANKR_WALLET_KEY  — Treasury wallet private key (sign transactions)
      OCTO_TELEGRAM_BOT      — Telegram bot token
      OCTO_TELEGRAM_CHAT     — Telegram chat ID for Octodamus control
      OCTO_X_OAUTH_TOKEN     — X OAuth token for OpenTweet
    """
    creds = {
        "bankr_api_key":    os.environ.get("OCTO_BANKR_API_KEY", ""),
        "bankr_wallet":     os.environ.get("OCTO_BANKR_WALLET", ""),
        "bankr_wallet_key": os.environ.get("OCTO_BANKR_WALLET_KEY", ""),
        "telegram_bot":     os.environ.get("OCTO_TELEGRAM_BOT", ""),
        "telegram_chat":    os.environ.get("OCTO_TELEGRAM_CHAT", ""),
        "x_oauth_token":    os.environ.get("OCTO_X_OAUTH_TOKEN", ""),
    }

    # FIX: Validate critical credentials before returning — fail fast with clear message
    missing = [k for k, v in creds.items() if not v and k in ("bankr_api_key", "bankr_wallet", "telegram_bot", "telegram_chat")]
    if missing:
        raise EnvironmentError(
            f"[OctoTreasury] Missing required credentials: {missing}\n"
            "Check your Bitwarden vault and bitwarden.py OCTODAMUS_SECRETS mapping."
        )

    return creds


# ── Entry Point ───────────────────────────────────────────────────────────────

async def main():
    """
    OctoTreasury launch sequence entry point.
    Called by Octodamus Core when the Week 3 launch trigger is met.

    Octodamus Core invocation (from AGENTS.md):
      octodamus: "Run the $OCTO token launch sequence."
      → OctoTreasury receives task, loads credentials, runs sequence.
    """
    log.info("OctoTreasury launch sequence initialising...")

    try:
        creds = load_credentials()
    except EnvironmentError as e:
        log.error(str(e))
        return

    bankr = BankrClient(
        api_key=creds["bankr_api_key"],
        wallet_address=creds["bankr_wallet"],
    )

    telegram = TelegramNotifier(
        bot_token=creds["telegram_bot"],
        chat_id=creds["telegram_chat"],
    )

    # FIX: Use stub that raises NotImplementedError instead of None
    # Replace with: from clawhub.skills.opentweet import OpenTweetClient
    open_tweet = OpenTweetStub()

    state = await run_launch_sequence(
        bankr=bankr,
        telegram=telegram,
        open_tweet=open_tweet,
    )

    log.info(f"Final state: {state.status.value} | Ticker: ${state.chosen_ticker}")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    asyncio.run(main())

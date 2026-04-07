"""
octo_acp_worker.py - Octodamus ACP Seller Worker v6

Two key fixes vs v5:
1. NO thread queue — ACP SDK requires tasks handled in same thread they arrive.
   Queue caused "ACP contract client not found" error on every job.
2. JOB_CACHE — stores report_type at accept time, recalls at TRANSACTION time.
   service_name is always '' from Butler, so we can't route at delivery time.
   Instead: determine type at accept, cache by job_id, look up at deliver.
"""

import asyncio
import logging
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from virtuals_acp.client import VirtualsACP
from virtuals_acp.contract_clients.contract_client_v2 import ACPContractClientV2
from virtuals_acp.configs.configs import BASE_MAINNET_CONFIG_V2

from octo_report_handlers import get_handler, render_text, VALID_TICKERS, VALID_STOCKS

# ── Logging ───────────────────────────────────────────────────────────────────
Path("logs").mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [ACP] %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/octo_acp_worker.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
SELLER_ENTITY_ID    = 3
SELLER_AGENT_WALLET = "0x9DdE22707542FA69c9ecfEb0C4f0912797DF3d5E"
BITWARDEN_ITEM      = "AGENT - Octodamus - ACP Wallet"
REPORT_BASE_URL     = "https://api.octodamus.com/api/report"
REPORTS_DIR         = Path("/mnt/c/Users/walli/octodamus/data/reports")

# ── Job cache — stores report_type+ticker at accept, looked up at deliver ─────
# Format: {job_id: {"report_type": str, "ticker": str}}
JOB_CACHE: dict = {}


# ── Report type routing ───────────────────────────────────────────────────────

def _get_report_type(task, requirements: dict) -> str:
    """
    Determine report type from every available field on the task object.
    service_name is often '' from Butler — check all string fields.
    """
    ticker = str(requirements.get("ticker", "")).upper()

    # Collect all string fields from task object
    all_text = ""
    for attr in dir(task):
        if attr.startswith("_"):
            continue
        try:
            val = getattr(task, attr, None)
            if isinstance(val, str) and val.strip():
                all_text += " " + val.lower()
        except Exception:
            pass

    log.info(f"Routing text: {all_text[:200]}")

    # Match against all text fields
    if any(k in all_text for k in ["ask", "question", "what is", "what are", "explain", "v2/ask"]):
        return "ask"
    if any(k in all_text for k in ["congressional", "congress", "stock trade", "stock alert", "trade alert"]):
        return "congressional"
    if any(k in all_text for k in ["fear greed", "sentiment read", "fear_greed", "sentiment"]):
        return "fear_greed"
    if any(k in all_text for k in ["bitcoin", "price analysis", "analysis forecast", "deep dive", "btc analysis"]):
        return "bitcoin_analysis"
    if any(k in all_text for k in ["crypto market", "market signal", "oracle briefing", "signal report", "market_signal"]):
        return "market_signal"

    # Ticker fallback
    if ticker in VALID_STOCKS:
        return "congressional"

    return "market_signal"


# ── HTML report writer ────────────────────────────────────────────────────────

def _write_frozen_report(data: dict) -> str:
    """Render HTML, write to shared disk, return permanent URL."""
    try:
        from octo_report_html import render_html
        html      = render_html(data)
        report_id = uuid.uuid4().hex[:16]
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        (REPORTS_DIR / f"{report_id}.html").write_text(html, encoding="utf-8")
        log.info(f"HTML written: {report_id}")
        return f"{REPORT_BASE_URL}/{report_id}"
    except Exception as e:
        log.error(f"HTML write failed: {e}")
        return None


# ── Task handler — called directly in ACP callback thread ────────────────────

def _handle_task(task, memo_to_sign=None):
    job_id       = getattr(task, "id", "unknown")
    service_name = getattr(task, "service_name", None) or ""
    requirements = (
        getattr(task, "service_requirement", None) or
        getattr(task, "requirement", None) or {}
    )
    phase = str(getattr(task, "phase", "") or "")

    log.info(f"Job #{job_id} | service='{service_name}' | phase={phase} | req={requirements}")

    ticker = str(requirements.get("ticker", "BTC")).upper()

    try:
        if "TRANSACTION" in phase.upper():
            # Recall report_type stored at accept time
            cached      = JOB_CACHE.get(str(job_id), {})
            report_type = cached.get("report_type") or _get_report_type(task, requirements)
            ticker      = cached.get("ticker", ticker)
            log.info(f"Job #{job_id} TRANSACTION — type={report_type} ticker={ticker} (cached={bool(cached)})")

            handler     = get_handler(report_type)
            data        = handler(requirements)

            # Check if handler signaled a rejection
            if isinstance(data, dict) and data.get("reject"):
                err = data.get("error", "Invalid request")
                task.reject(err)
                log.warning(f"Job #{job_id} rejected by handler: {err}")
                JOB_CACHE.pop(str(job_id), None)
                return

            text        = render_text(data)
            report_url  = _write_frozen_report(data) or \
                          f"{REPORT_BASE_URL}?type={report_type}&ticker={ticker}"

            deliverable = (
                f"{text}\n\n"
                f"------------------------------\n"
                f"View full formatted report:\n{report_url}\n\n"
                f"OctoData API — automated market intelligence for your agents:\n"
                f"  /v2/ask      — ask Octodamus any market question (free, no key)\n"
                f"  /v2/signal   — oracle signals (9/11 consensus)\n"
                f"  /v2/brief    — inject live market context into LLM system prompt\n"
                f"  /v2/all      — all data in one call\n"
                f"  /v2/demo     — live sample, no key required\n"
                f"Free key: POST https://api.octodamus.com/v1/signup?email=your@email.com\n"
                f"$5 USDC trial (7 days, 10k req/day): POST /v1/agent-checkout?product=premium_trial"
            )
            log.info(f"Job #{job_id} delivering ({len(deliverable)} chars)")
            task.deliver({"response": deliverable})
            log.info(f"Job #{job_id} delivered OK")

            # Clean up cache
            JOB_CACHE.pop(str(job_id), None)

        else:
            # Accept phase — validate, determine type, cache it
            if not requirements:
                task.reject("Invalid request: no requirements provided. Please include ticker.")
                log.warning(f"Job #{job_id} rejected — empty requirements")
                return

            # Determine type early so we can skip ticker validation for ask jobs
            early_type = _get_report_type(task, requirements)
            if early_type != "ask" and (not str(ticker).strip() or ticker not in VALID_TICKERS):
                task.reject(
                    f"Unsupported ticker: {ticker}. "
                    f"Supported: BTC, ETH, SOL, NVDA, TSLA, AAPL, MSFT, AMZN, META, GOOGL. "
                    f"To ask a market question, include 'question' in your requirements."
                )
                log.warning(f"Job #{job_id} rejected — unsupported ticker {ticker}")
                return

            # Determine and cache report type NOW before service_name disappears
            report_type = _get_report_type(task, requirements)
            JOB_CACHE[str(job_id)] = {"report_type": report_type, "ticker": ticker}
            log.info(f"Job #{job_id} cached — type={report_type} ticker={ticker}")

            # Ask jobs don't need a ticker — accept immediately
            if report_type == "ask":
                question = (
                    requirements.get("question") or
                    requirements.get("q") or
                    requirements.get("query") or
                    f"What is your current read on {ticker}?"
                )
                task.accept(
                    f"Octodamus ready to answer: \"{question[:80]}{'...' if len(question) > 80 else ''}\". "
                    f"OctoData API: api.octodamus.com — /v2/ask for live market Q&A."
                )
                log.info(f"Job #{job_id} accepted (ask) — question: {question[:60]}")
                task.create_requirement("Payment required to receive oracle answer.")
                return

            task.accept(
                f"Octodamus oracle ready — {report_type} report for {ticker}. "
                f"OctoData API also available at api.octodamus.com — "
                f"signals, sentiment, Polymarket EV, /v2/ask for live market Q&A. Free Basic key: POST /v1/signup."
            )
            log.info(f"Job #{job_id} accepted — {report_type}/{ticker}")
            task.create_requirement("Payment required to receive oracle report.")
            log.info(f"Job #{job_id} payment requested")

    except Exception as e:
        log.error(f"Job #{job_id} error: {e}")
        try:
            task.reject(f"Octodamus internal error: {e}")
        except Exception as e2:
            log.error(f"Job #{job_id} reject failed: {e2}")
        JOB_CACHE.pop(str(job_id), None)


# ── ACP callbacks — called directly in ACP thread, NO queue ──────────────────

def on_new_task(task, memo_to_sign=None):
    """Called by ACP SDK for each new task event."""
    job_id = getattr(task, "id", "unknown")
    log.info(f"Job #{job_id} received")
    _handle_task(task, memo_to_sign)


def on_evaluate(task):
    """Fallback path — called when ACP SDK triggers evaluate."""
    job_id       = getattr(task, "id", "unknown")
    requirements = (
        getattr(task, "service_requirement", None) or
        getattr(task, "requirement", None) or {}
    )
    cached      = JOB_CACHE.get(str(job_id), {})
    report_type = cached.get("report_type") or _get_report_type(task, requirements)
    ticker      = cached.get("ticker") or str(requirements.get("ticker", "BTC")).upper()
    handler     = get_handler(report_type)

    log.info(f"Job #{job_id} on_evaluate — type={report_type} ticker={ticker}")
    try:
        data        = handler(requirements)
        text        = render_text(data)
        report_url  = _write_frozen_report(data) or \
                      f"{REPORT_BASE_URL}?type={report_type}&ticker={ticker}"
        deliverable = (
            f"{text}\n\n"
            f"------------------------------\n"
            f"View full formatted report:\n{report_url}"
        )
        task.deliver({"response": deliverable})
        log.info(f"Job #{job_id} delivered via on_evaluate OK")
        JOB_CACHE.pop(str(job_id), None)
    except Exception as e:
        log.error(f"Job #{job_id} on_evaluate failed: {e}")


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    log.info("Loading ACP wallet private key...")
    private_key = os.environ.get("OCTO_ACP_PRIVATE_KEY", "")

    if not private_key:
        try:
            from bitwarden import _get_password as get_secret
            private_key = get_secret(BITWARDEN_ITEM)
        except Exception as e:
            log.error(f"Could not load private key: {e}")
            sys.exit(1)

    if not private_key:
        log.error("Private key empty. Aborting.")
        sys.exit(1)

    if private_key.startswith("0x"):
        private_key = private_key[2:]

    # Load QUIVER key
    quiver_key = os.environ.get("QUIVER_API_KEY", "")
    if not quiver_key:
        try:
            kp = Path(__file__).parent / "octo_quiver_key.txt"
            if kp.exists():
                quiver_key = kp.read_text().strip()
                os.environ["QUIVER_API_KEY"] = quiver_key
                log.info("QUIVER_API_KEY loaded from file")
        except Exception:
            pass

    log.info(f"Connecting — entity={SELLER_ENTITY_ID} wallet={SELLER_AGENT_WALLET}")
    log.info(f"QUIVER key: {bool(quiver_key)} | Reports dir: {REPORTS_DIR}")

    contract_client = ACPContractClientV2(
        agent_wallet_address=SELLER_AGENT_WALLET,
        wallet_private_key=private_key,
        entity_id=SELLER_ENTITY_ID,
        config=BASE_MAINNET_CONFIG_V2,
    )

    acp = VirtualsACP(
        acp_contract_clients=contract_client,
        on_new_task=on_new_task,
        on_evaluate=on_evaluate,
    )

    log.info("Octodamus ACP worker online. Listening for jobs...")
    acp.init()
    log.info("Worker running.")

    import time
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Shutting down.")


if __name__ == "__main__":
    asyncio.run(main())

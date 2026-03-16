"""
octo_acp_worker.py — Octodamus ACP Seller Worker v4
Thread-safe queue, rejection handling, all 4 handlers via shared octo_report_handlers.py
Appends live HTML report link to every deliverable.
"""

import asyncio
import logging
import os
import queue
import sys
import threading
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

from virtuals_acp.client import VirtualsACP
from virtuals_acp.contract_clients.contract_client_v2 import ACPContractClientV2
from virtuals_acp.configs.configs import BASE_MAINNET_CONFIG_V2

from octo_report_handlers import (
    get_handler, render_text,
    VALID_TICKERS, VALID_STOCKS,
    handle_congressional, handle_fear_greed,
    handle_bitcoin_analysis, handle_crypto_market_signal,
)

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

TYPE_MAP = {
    "market_signal":    "market_signal",
    "fear_greed":       "fear_greed",
    "bitcoin_analysis": "bitcoin_analysis",
    "congressional":    "congressional",
}

def _report_url(report_type: str, ticker: str) -> str:
    return f"{REPORT_BASE_URL}?type={report_type}&ticker={ticker.upper()}"

def _get_report_type(service_name: str, requirements: dict) -> str:
    sn = service_name.lower().replace("_"," ").strip()
    ticker = str(requirements.get("ticker","")).upper()
    if sn:
        if any(k in sn for k in ["congressional","congress","stock trade","stock alert"]): return "congressional"
        if any(k in sn for k in ["fear greed","sentiment","fear"]): return "fear_greed"
        if any(k in sn for k in ["bitcoin","deep dive","analysis forecast","price analysis"]): return "bitcoin_analysis"
        if any(k in sn for k in ["crypto market","market signal","oracle briefing","signal report"]): return "market_signal"
    if ticker in VALID_STOCKS: return "congressional"
    return "market_signal"

# ── Thread-safe job queue ─────────────────────────────────────────────────────
JOB_QUEUE = queue.Queue()

def _queue_worker():
    while True:
        try:
            task, memo = JOB_QUEUE.get(timeout=1)
            try: _handle_task(task, memo)
            except Exception as e: log.error(f"Queue worker error: {e}")
            finally: JOB_QUEUE.task_done()
        except queue.Empty:
            continue

threading.Thread(target=_queue_worker, daemon=True, name="octo-job-queue").start()

# ── Task Handler ──────────────────────────────────────────────────────────────

def _handle_task(task, memo_to_sign=None):
    job_id       = getattr(task, "id", "unknown")
    service_name = getattr(task, "service_name", None) or ""
    requirements = getattr(task, "service_requirement", None) or \
                   getattr(task, "requirement", None) or {}
    phase        = str(getattr(task, "phase", "") or "")

    log.info(f"Job #{job_id} | service='{service_name}' | phase={phase} | req={requirements}")

    report_type = _get_report_type(service_name, requirements)
    ticker      = str(requirements.get("ticker", "BTC")).upper()
    handler     = get_handler(report_type)

    try:
        if "TRANSACTION" in phase.upper():
            # Payment received — generate and deliver
            data        = handler(requirements)
            text        = render_text(data)
            report_url  = _report_url(report_type, ticker)
            deliverable = f"{text}\n\n──────────────────────────────\nView full formatted report:\n{report_url}"
            log.info(f"Job #{job_id} delivering ({len(deliverable)} chars) — type={report_type} ticker={ticker}")
            task.deliver({"response": deliverable})
            log.info(f"Job #{job_id} delivered ✅")
        else:
            # Validate
            if not requirements:
                task.reject("Invalid request: no requirements provided. Please include ticker.")
                log.warning(f"Job #{job_id} rejected — empty requirements")
                return
            if ticker and ticker not in VALID_TICKERS:
                task.reject(f"Unsupported ticker: {ticker}. Supported: BTC,ETH,SOL,NVDA,TSLA,AAPL,MSFT,AMZN,META,GOOGL")
                log.warning(f"Job #{job_id} rejected — unsupported ticker {ticker}")
                return
            # Accept
            task.accept(f"Octodamus oracle ready — {report_type} report for {ticker}.")
            log.info(f"Job #{job_id} accepted — {report_type}/{ticker}")
            task.create_requirement("Payment required to receive oracle report.")
            log.info(f"Job #{job_id} payment requested")

    except Exception as e:
        log.error(f"Job #{job_id} error: {e}")
        try: task.reject(f"Octodamus internal error: {e}")
        except Exception as e2: log.error(f"Job #{job_id} reject failed: {e2}")


def on_new_task(task, memo_to_sign=None):
    job_id = getattr(task, "id", "unknown")
    log.info(f"Job #{job_id} queued (queue size: {JOB_QUEUE.qsize()})")
    JOB_QUEUE.put((task, memo_to_sign))


def on_evaluate(task):
    job_id       = getattr(task, "id", "unknown")
    service_name = getattr(task, "service_name", None) or ""
    requirements = getattr(task, "service_requirement", None) or \
                   getattr(task, "requirement", None) or {}
    report_type = _get_report_type(service_name, requirements)
    ticker      = str(requirements.get("ticker","BTC")).upper()
    handler     = get_handler(report_type)
    log.info(f"Job #{job_id} on_evaluate — delivering")
    try:
        data        = handler(requirements)
        text        = render_text(data)
        report_url  = _report_url(report_type, ticker)
        deliverable = f"{text}\n\n──────────────────────────────\nView full formatted report:\n{report_url}"
        task.deliver({"response": deliverable})
        log.info(f"Job #{job_id} delivered via on_evaluate ✅")
    except Exception as e:
        log.error(f"Job #{job_id} on_evaluate failed: {e}")

# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    log.info("Loading ACP wallet private key...")
    private_key = os.environ.get("OCTO_ACP_PRIVATE_KEY","")
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
    quiver_key = os.environ.get("QUIVER_API_KEY","")
    if not quiver_key:
        try:
            kp = os.path.join(os.path.dirname(__file__), "octo_quiver_key.txt")
            if os.path.exists(kp):
                quiver_key = open(kp).read().strip()
                os.environ["QUIVER_API_KEY"] = quiver_key
                log.info("QUIVER_API_KEY loaded from file")
        except Exception: pass

    log.info(f"Connecting — entity={SELLER_ENTITY_ID} wallet={SELLER_AGENT_WALLET}")
    log.info(f"QUIVER key: {bool(quiver_key)} | Queue worker: running | Report URL: {REPORT_BASE_URL}")

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
        while True: time.sleep(1)
    except KeyboardInterrupt:
        log.info("Shutting down.")


if __name__ == "__main__":
    asyncio.run(main())

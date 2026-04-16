"""
octo_acp_worker.py - Octodamus ACP Worker v7 (ACP v2 CLI event-driven)

Architecture change from v6:
- v6: Python virtuals_acp SDK with callbacks (ACP v1)
- v7: CLI subprocess `acp events listen` -> NDJSON file -> process events
      Responds via `acp provider set-budget` and `acp provider submit` CLI calls

Job flow:
  1. `acp events listen` streams job events to data/acp_events.jsonl
  2. Worker tails the file, processes each event line
  3. NEW_JOB  -> set-budget (propose $1 USDC)
  4. FUNDED   -> run oracle handler -> submit report URL as deliverable
"""

import json
import logging
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from octo_report_handlers import get_handler, render_text, VALID_TICKERS, VALID_STOCKS

# ── Config ────────────────────────────────────────────────────────────────────
CHAIN_ID        = 8453                                         # Base mainnet
ACP_PRICE_USDC  = 1.0                                          # $1 USDC per job
EVENTS_FILE     = Path(__file__).parent / "data" / "acp_events.jsonl"
REPORT_BASE_URL = "https://api.octodamus.com/api/report"
REPORTS_DIR     = Path(__file__).parent / "data" / "reports"
SELLER_AGENT_WALLET = "0x94c037393ab0263194dcfd8d04a2176d6a80e385"  # ACP v2 fresh wallet

# ACP CLI — cloned from github.com/Virtual-Protocol/acp-cli (not on npm registry)
# Run from its repo dir: npm run acp -- <command>
ACP_CLI_DIR     = Path(os.environ.get("ACP_CLI_DIR", r"C:\Users\walli\acp-cli"))

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

# ── Job cache — type+ticker stored at new_job, recalled at funded ─────────────
JOB_CACHE: dict = {}


# ── CLI wrapper ───────────────────────────────────────────────────────────────

_NPM = "npm.cmd" if sys.platform == "win32" else "npm"


def _acp(args: list, timeout: int = 30) -> tuple:
    """Run an acp CLI command via `npm run acp -- <args>` from ACP_CLI_DIR.
    Returns (returncode, stdout, stderr)."""
    cmd = [_NPM, "run", "acp", "--"] + [str(a) for a in args]
    log.info(f"CLI: {' '.join(cmd)}")
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", timeout=timeout,
            cwd=str(ACP_CLI_DIR),
        )
        if r.stdout.strip():
            log.info(f"  stdout: {r.stdout.strip()[:200]}")
        if r.stderr.strip():
            log.warning(f"  stderr: {r.stderr.strip()[:200]}")
        return r.returncode, r.stdout, r.stderr
    except Exception as e:
        log.error(f"CLI error: {e}")
        return -1, "", str(e)


# ── Report routing ────────────────────────────────────────────────────────────

def _get_report_type(event: dict) -> str:
    """Route event to report type from description + requirements."""
    desc  = (event.get("description") or "").lower()
    reqs  = event.get("requirements") or {}
    ticker = str(reqs.get("ticker", "")).upper()
    all_text = desc + " " + json.dumps(reqs).lower()

    if any(k in all_text for k in ["ask", "question", "what is", "what are", "explain", "v2/ask"]):
        return "ask"
    if any(k in all_text for k in ["congressional", "congress", "stock trade", "trade alert"]):
        return "congressional"
    if any(k in all_text for k in ["fear greed", "sentiment read", "fear_greed"]):
        return "fear_greed"
    if any(k in all_text for k in ["bitcoin analysis", "deep dive", "btc analysis"]):
        return "bitcoin_analysis"
    if ticker in VALID_STOCKS:
        return "congressional"
    return "market_signal"


# ── Report generation ─────────────────────────────────────────────────────────

def _write_frozen_report(data: dict) -> str | None:
    """Render HTML report to disk, return permanent URL."""
    try:
        from octo_report_html import render_html
        html      = render_html(data)
        report_id = uuid.uuid4().hex[:16]
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        (REPORTS_DIR / f"{report_id}.html").write_text(html, encoding="utf-8")
        log.info(f"Report written: {report_id}")
        return f"{REPORT_BASE_URL}/{report_id}"
    except Exception as e:
        log.error(f"HTML write failed: {e}")
        return None


def _build_deliverable(report_type: str, ticker: str, requirements: dict) -> str | None:
    """Run oracle handler and return deliverable string."""
    try:
        handler = get_handler(report_type)
        data    = handler(requirements)

        if isinstance(data, dict) and data.get("reject"):
            log.warning(f"Handler rejected: {data.get('error')}")
            return None

        report_url = _write_frozen_report(data) or \
                     f"{REPORT_BASE_URL}?type={report_type}&ticker={ticker}"

        # Submit URL only -- short arg avoids CLI timeout on Windows
        return report_url
    except Exception as e:
        log.error(f"Build deliverable error: {e}")
        return None


# ── Event handlers ────────────────────────────────────────────────────────────

def handle_new_job(event: dict):
    """New job from client -- propose budget."""
    job_id   = str(event.get("jobId") or event.get("job_id") or "")
    chain_id = event.get("chainId") or event.get("chain_id") or CHAIN_ID
    reqs     = event.get("requirements") or {}

    if not job_id:
        log.warning("NEW_JOB event missing jobId -- skipping")
        return

    ticker      = str(reqs.get("ticker", "BTC")).upper()
    report_type = _get_report_type(event)

    JOB_CACHE[job_id] = {
        "report_type":  report_type,
        "ticker":       ticker,
        "requirements": reqs,
        "chain_id":     chain_id,
    }
    log.info(f"Job #{job_id} -- type={report_type} ticker={ticker}")

    rc, out, err = _acp([
        "provider", "set-budget",
        "--job-id",   job_id,
        "--amount",   str(ACP_PRICE_USDC),
        "--chain-id", str(chain_id),
    ])
    if rc == 0:
        log.info(f"Job #{job_id} budget set to ${ACP_PRICE_USDC} USDC")
    else:
        log.error(f"Job #{job_id} set-budget failed (rc={rc})")


def handle_funded_job(event: dict):
    """Escrow funded -- generate and submit deliverable."""
    job_id   = str(event.get("jobId") or event.get("job_id") or "")
    chain_id = event.get("chainId") or event.get("chain_id") or CHAIN_ID

    if not job_id:
        log.warning("FUNDED event missing jobId -- skipping")
        return

    cached      = JOB_CACHE.get(job_id, {})
    reqs        = cached.get("requirements") or event.get("requirements") or {}
    report_type = cached.get("report_type") or _get_report_type(event)
    ticker      = cached.get("ticker") or str(reqs.get("ticker", "BTC")).upper()
    chain_id    = cached.get("chain_id") or chain_id

    log.info(f"Job #{job_id} funded -- generating {report_type}/{ticker}")

    deliverable = _build_deliverable(report_type, ticker, reqs)
    if not deliverable:
        log.error(f"Job #{job_id} no deliverable -- aborting")
        JOB_CACHE.pop(job_id, None)
        return

    rc, out, err = _acp([
        "provider", "submit",
        "--job-id",      job_id,
        "--deliverable", deliverable,
        "--chain-id",    str(chain_id),
    ], timeout=90)
    if rc == 0:
        log.info(f"Job #{job_id} submitted OK ({len(deliverable)} chars)")
    else:
        log.error(f"Job #{job_id} submit failed (rc={rc})")

    JOB_CACHE.pop(job_id, None)


# ── Event router ──────────────────────────────────────────────────────────────

# Map known event type strings -> handler
_EVENT_HANDLERS = {
    # v2 dot-notation (normalized to uppercase underscores)
    "JOB_CREATED":      handle_new_job,
    "JOB_FUNDED":       handle_funded_job,
    # v1 legacy names
    "NEW_JOB":          handle_new_job,
    "NEW_TASK":         handle_new_job,
    "TASK_CREATED":     handle_new_job,
    "ESCROW_FUNDED":    handle_funded_job,
    "FUNDED":           handle_funded_job,
    "PAYMENT_RECEIVED": handle_funded_job,
}


def process_event(line: str):
    """Parse a single NDJSON line and dispatch to handler."""
    line = line.strip()
    if not line:
        return
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        log.warning(f"Bad JSON: {line[:100]}")
        return

    entry       = event.get("entry") or {}
    entry_kind  = entry.get("kind", "")
    entry_event = entry.get("event") or {}

    # v2 requirement messages arrive separately from job.created
    # cache them so handle_new_job / handle_funded_job can use them
    if entry_kind == "message" and entry.get("contentType") == "requirement":
        job_id = str(event.get("jobId") or entry.get("onChainJobId") or "")
        if job_id:
            try:
                reqs = json.loads(entry.get("content") or "{}")
                cached = JOB_CACHE.setdefault(job_id, {})
                cached["requirements"] = reqs
                log.info(f"Cached requirements for job #{job_id}: {reqs}")
                # If budget not yet set, trigger now that we have requirements
                if not cached.get("budget_set"):
                    report_type = _get_report_type({**event, "requirements": reqs})
                    ticker = str(reqs.get("ticker", "BTC")).upper()
                    cached.update({"report_type": report_type, "ticker": ticker, "budget_set": True})
                    chain_id = event.get("chainId") or CHAIN_ID
                    rc, _, _ = _acp([
                        "provider", "set-budget",
                        "--job-id",   job_id,
                        "--amount",   str(ACP_PRICE_USDC),
                        "--chain-id", str(chain_id),
                    ])
                    if rc == 0:
                        log.info(f"Job #{job_id} budget set (from requirement msg) type={report_type} ticker={ticker}")
                    else:
                        log.error(f"Job #{job_id} set-budget failed after requirement msg")
            except Exception as e:
                log.error(f"Requirement message parse error: {e}")
        return

    # Extract event type -- v2 nests in entry.event.type, v1 at top level
    raw_type = (
        event.get("type") or event.get("event") or event.get("eventType") or
        entry_event.get("type") or ""
    )
    # Normalize: "job.created" -> "JOB_CREATED"
    event_type = raw_type.upper().replace(".", "_")

    log.debug(f"Event: {event_type} | {str(event)[:150]}")

    handler = _EVENT_HANDLERS.get(event_type)
    if handler:
        handler(event)
    elif event_type:
        log.info(f"Unhandled event type: {event_type!r} -- logged only")


# ── Event listener subprocess ─────────────────────────────────────────────────

def _stderr_reader(proc: subprocess.Popen, connected_event: threading.Event):
    """Background thread: drain listener stderr, set connected_event on 'connected'."""
    try:
        for line in proc.stderr:
            line = line.rstrip()
            if line:
                log.info(f"  listener: {line}")
                if "connected" in line.lower():
                    connected_event.set()
    except Exception:
        pass


def start_listener() -> subprocess.Popen:
    """Start `acp events listen --output <file>`, wait for 'connected' on stderr."""
    EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Pre-create the file — the CLI only appends on events, never creates it
    if not EVENTS_FILE.exists():
        EVENTS_FILE.touch()

    log.info(f"Starting: acp events listen -> {EVENTS_FILE}")
    proc = subprocess.Popen(
        [_NPM, "run", "acp", "--", "events", "listen", "--output", str(EVENTS_FILE)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        cwd=str(ACP_CLI_DIR),
    )
    log.info(f"Listener PID: {proc.pid} — waiting for tsx startup (can take 120s)...")

    connected = threading.Event()
    t = threading.Thread(target=_stderr_reader, args=(proc, connected), daemon=True)
    t.start()

    if connected.wait(timeout=180):
        log.info("Listener connected and ready.")
    elif proc.poll() is not None:
        log.error(f"Listener exited before connecting (rc={proc.returncode})")
    else:
        log.warning("Listener startup timeout after 180s — proceeding anyway")

    return proc


def tail_events(proc: subprocess.Popen):
    """Tail EVENTS_FILE and dispatch new lines. Restarts listener if it dies."""
    with open(EVENTS_FILE, "r", encoding="utf-8") as f:
        f.seek(0, 2)  # Seek to end -- don't replay old events
        log.info("Watching for events...")

        while True:
            if proc.poll() is not None:
                log.error(f"Listener exited (rc={proc.returncode}) -- restarting in 5s")
                time.sleep(5)
                proc = start_listener()

            line = f.readline()
            if line:
                process_event(line)
            else:
                time.sleep(0.5)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    log.info("=" * 60)
    log.info("Octodamus ACP Worker v7 -- ACP v2 CLI event-driven mode")
    log.info(f"Chain: Base mainnet (chainId={CHAIN_ID})")
    log.info(f"Price: ${ACP_PRICE_USDC} USDC per job")
    log.info(f"Events file: {EVENTS_FILE}")
    log.info("=" * 60)

    # Verify ACP CLI dir exists and is set up
    if not ACP_CLI_DIR.exists():
        log.error(f"ACP CLI dir not found: {ACP_CLI_DIR}")
        log.error("Clone it: git clone https://github.com/Virtual-Protocol/acp-cli.git C:\\Users\\walli\\acp-cli")
        log.error("Then: cd C:\\Users\\walli\\acp-cli && npm install && npm run acp -- configure")
        sys.exit(1)
    if not (ACP_CLI_DIR / "node_modules").exists():
        log.error(f"node_modules missing in {ACP_CLI_DIR} -- run: npm install")
        sys.exit(1)
    log.info(f"ACP CLI dir: {ACP_CLI_DIR}")

    proc = start_listener()

    try:
        tail_events(proc)
    except KeyboardInterrupt:
        log.info("Shutting down...")
    finally:
        proc.terminate()
        log.info("Worker stopped.")


if __name__ == "__main__":
    main()

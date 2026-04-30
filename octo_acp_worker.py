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
JOB_CACHE_FILE = Path(__file__).parent / "data" / "acp_job_cache.json"


def _save_job_cache():
    try:
        JOB_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        JOB_CACHE_FILE.write_text(json.dumps(JOB_CACHE, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning(f"Job cache save failed: {e}")


def _load_job_cache():
    try:
        if JOB_CACHE_FILE.exists():
            data = json.loads(JOB_CACHE_FILE.read_text(encoding="utf-8"))
            JOB_CACHE.update(data)
            log.info(f"Loaded {len(data)} cached jobs from disk")
    except Exception as e:
        log.warning(f"Job cache load failed: {e}")


_PENDING_FILE = Path(__file__).parent / "data" / "acp_pending_jobs.json"

def _remove_pending_job(job_id: int):
    try:
        if not _PENDING_FILE.exists():
            return
        jobs = json.loads(_PENDING_FILE.read_text(encoding="utf-8"))
        before = len(jobs)
        jobs = [j for j in jobs if j.get("job_id") != job_id]
        if len(jobs) < before:
            _PENDING_FILE.write_text(json.dumps(jobs, indent=2), encoding="utf-8")
            log.info(f"Job #{job_id} removed from pending_jobs (expired/reverted)")
    except Exception as e:
        log.warning(f"Could not remove job #{job_id} from pending_jobs: {e}")


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
    if any(k in all_text for k in ["grok sentiment", "x sentiment", "grok_sentiment", "twitter sentiment"]):
        return "grok_sentiment_brief"
    if any(k in all_text for k in ["divergence", "fear crowd", "crowd divergence", "fear vs crowd"]):
        return "fear_crowd_divergence"
    if any(k in all_text for k in ["smithery", "onboarding", "quickstart", "quick_start", "getting_started"]):
        return "smithery_onboarding"
    if any(k in all_text for k in ["overnight", "asia session", "asia_session", "night brief", "overnight_brief"]):
        return "overnight_brief"
    if any(k in all_text for k in ["agent market intel", "agent_market_intel", "context pack", "agent_context", "intel bundle", "decision loop"]):
        return "agent_market_intel_bundle"
    if any(k in all_text for k in ["bounty", "bounty hunter", "bounty_recon", "recon brief"]):
        return "bounty_hunter_recon"
    if any(k in all_text for k in ["bull trap", "bull_trap", "trap monitor", "trap_monitor", "btc_trap"]):
        return "btc_bull_trap_monitor"
    if any(k in all_text for k in ["macromind", "macro mind", "macro regime brief", "yield curve brief", "nyse_macromind"]):
        return "nyse_macromind_brief"
    if any(k in all_text for k in ["stockoracle", "stock oracle", "congressional brief", "nyse_stockoracle"]):
        return "nyse_stockoracle_brief"
    if any(k in all_text for k in ["nyse_tech", "tech agent brief", "tokenized equity brief", "tech regulatory brief"]):
        return "nyse_tech_brief"
    if any(k in all_text for k in ["chainflow", "chain flow brief", "order flow brief", "dex flow brief", "whale activity scan"]):
        return "order_chainflow_brief"
    if any(k in all_text for k in ["x_sentiment_brief", "sentiment agent brief", "crowd brief", "sentiment divergence brief"]):
        return "x_sentiment_brief"
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
        # Ensure ticker flows into requirements — handlers read it from there,
        # but ticker may have been cached separately if requirements arrived empty.
        if ticker and not requirements.get("ticker"):
            requirements = {**requirements, "ticker": ticker}
        handler = get_handler(report_type)
        data    = handler(requirements)

        if isinstance(data, dict) and data.get("reject"):
            log.warning(f"Handler rejected: {data.get('error')}")
            return None

        # Embed Octodamus calling card so receiving agents can hire us back
        try:
            from octo_agent_cards import get_octodamus_card_for_deliverable
            if isinstance(data, dict):
                data["octodamus_card"] = get_octodamus_card_for_deliverable()
        except Exception:
            pass

        report_url = _write_frozen_report(data) or \
                     f"{REPORT_BASE_URL}?type={report_type}&ticker={ticker}"

        # Submit URL only -- short arg avoids CLI timeout on Windows
        return report_url
    except Exception as e:
        log.error(f"Build deliverable error: {e}")
        return None


# ── Event handlers ────────────────────────────────────────────────────────────

def _parse_client_calling_card(description: str, job_id: str):
    """Extract and log a calling card embedded in a client's job description."""
    if "---CALLING_CARD---" not in description:
        return
    try:
        start = description.index("---CALLING_CARD---") + len("---CALLING_CARD---")
        end   = description.index("---END_CARD---", start)
        card_text = description[start:end].strip()
        log.info(f"Job #{job_id} -- client calling card detected:\n{card_text}")
        # Persist to agent visitors log for future cross-buy intelligence
        visitors_file = Path(__file__).parent / "data" / "agent_visitors.json"
        try:
            visitors = json.loads(visitors_file.read_text(encoding="utf-8")) if visitors_file.exists() else []
        except Exception:
            visitors = []
        visitors.append({"job_id": job_id, "card": card_text, "ts": int(__import__("time").time())})
        visitors = visitors[-200:]  # keep last 200
        visitors_file.write_text(json.dumps(visitors, indent=2), encoding="utf-8")
    except Exception as e:
        log.debug(f"Calling card parse error (non-fatal): {e}")


def handle_new_job(event: dict):
    """New job from client -- propose budget."""
    job_id   = str(event.get("jobId") or event.get("job_id") or "")
    chain_id = event.get("chainId") or event.get("chain_id") or CHAIN_ID
    reqs     = event.get("requirements") or {}

    if not job_id:
        log.warning("NEW_JOB event missing jobId -- skipping")
        return

    # Log any calling card the client embedded so we can discover their services
    _parse_client_calling_card(event.get("description") or "", job_id)

    ticker      = str(reqs.get("ticker", "BTC")).upper()
    report_type = _get_report_type(event)

    JOB_CACHE[job_id] = {
        "report_type":  report_type,
        "ticker":       ticker,
        "requirements": reqs,
        "chain_id":     chain_id,
    }
    _save_job_cache()
    log.info(f"Job #{job_id} -- type={report_type} ticker={ticker}")

    rc, out, err = _acp([
        "provider", "set-budget",
        "--job-id",   job_id,
        "--amount",   str(2.0 if report_type in ("fear_crowd_divergence", "overnight_brief", "agent_market_intel_bundle", "bounty_hunter_recon") else 1.5 if report_type == "btc_bull_trap_monitor" else ACP_PRICE_USDC),
        "--chain-id", str(chain_id),
    ])
    if rc == 0:
        log.info(f"Job #{job_id} budget set to ${ACP_PRICE_USDC} USDC")
    else:
        log.error(f"Job #{job_id} set-budget failed (rc={rc})")


def handle_funded_job(event: dict):
    """Escrow funded -- generate and submit deliverable.
    Funding is authoritative from the event stream (job.funded is an on-chain event
    from ACP infrastructure, not a client message — no extra CLI verification needed).
    """
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

    log.info(f"Job #{job_id} funded + confirmed -- generating {report_type}/{ticker}")

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
    ], timeout=180)
    if rc == 0:
        log.info(f"Job #{job_id} submitted OK ({len(deliverable)} chars)")
        # Write synthetic completed event so the monitor stops flagging this job as stuck.
        # The ACP platform does not send job.completed back to the event listener.
        try:
            import time as _time
            cached_type = JOB_CACHE.get(job_id, {}).get("report_type", "market_signal")
            cached_ticker = JOB_CACHE.get(job_id, {}).get("ticker", "")
            synthetic = {
                "jobId": str(job_id),
                "chainId": chain_id,
                "status": "completed",
                "reportType": cached_type,
                "entry": {
                    "event": {
                        "type": "job.completed",
                        "onChainJobId": str(job_id),
                        "reportType": cached_type,
                        "ticker": cached_ticker,
                    },
                    "timestamp": int(_time.time() * 1000),
                },
            }
            with open(EVENTS_FILE, "a", encoding="utf-8") as _ef:
                _ef.write(json.dumps(synthetic) + "\n")
        except Exception as _e:
            log.warning(f"Could not write synthetic completed event for job #{job_id}: {_e}")
    else:
        combined = (out + err).lower()
        if "execution reverted" in combined or "already" in combined:
            log.error(f"Job #{job_id} submit reverted -- job expired or already closed on-chain")
            _remove_pending_job(job_id)
        else:
            log.error(f"Job #{job_id} submit failed (rc={rc})")

    JOB_CACHE.pop(job_id, None)
    _save_job_cache()


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
                        "--amount",   str(2.0 if report_type in ("fear_crowd_divergence", "overnight_brief", "agent_market_intel_bundle", "bounty_hunter_recon") else 1.5 if report_type == "btc_bull_trap_monitor" else ACP_PRICE_USDC),
                        "--chain-id", str(chain_id),
                    ])
                    _save_job_cache()
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


def _kill_orphan_listeners():
    """Kill any pre-existing `acp events listen` processes to prevent accumulation."""
    try:
        result = subprocess.run(
            ["powershell", "-Command",
             "Get-WmiObject Win32_Process | Where-Object { $_.CommandLine -like '*acp*events*listen*' } | Select-Object -ExpandProperty ProcessId"],
            capture_output=True, text=True, encoding="utf-8", timeout=10,
        )
        pids = [p.strip() for p in result.stdout.strip().splitlines() if p.strip().isdigit()]
        for pid in pids:
            subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
            log.info(f"Killed orphan listener PID {pid}")
        if pids:
            log.warning(f"Cleaned up {len(pids)} orphan listener process(es)")
    except Exception as e:
        log.warning(f"Orphan cleanup failed (non-fatal): {e}")


def start_listener() -> subprocess.Popen:
    """Start `acp events listen --output <file>`, wait for 'connected' on stderr."""
    _kill_orphan_listeners()
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
                # Kill before respawning to prevent process accumulation
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                proc = start_listener()

            line = f.readline()
            if line:
                process_event(line)
            else:
                time.sleep(0.5)


# ── Startup replay — submit any funded-but-not-completed jobs ─────────────────

REPLAY_MAX_AGE_S = 86400  # skip funded jobs older than 24h — matches on-chain job expiry


def replay_funded_jobs():
    """On startup, scan events file for recently funded jobs with no completion.
    Runs in a background thread so live events aren't blocked.
    Trusts the event stream for funding status — no CLI verification needed.
    """
    if not EVENTS_FILE.exists():
        return

    job_status:    dict[str, str]   = {}
    job_reqs:      dict[str, dict]  = {}
    job_chain:     dict[str, int]   = {}
    job_timestamp: dict[str, float] = {}

    try:
        lines = EVENTS_FILE.read_text(encoding="utf-8").splitlines()
    except Exception as e:
        log.warning(f"Replay: could not read events file: {e}")
        return

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except Exception:
            continue

        job_id   = str(event.get("jobId") or "")
        status   = event.get("status") or ""
        entry    = event.get("entry") or {}
        chain_id = int(event.get("chainId") or CHAIN_ID)

        if job_id:
            if status:
                job_status[job_id] = status
            job_chain.setdefault(job_id, chain_id)

        # Track timestamp from entry (ms epoch)
        ts_ms = entry.get("timestamp")
        if job_id and ts_ms:
            job_timestamp[job_id] = float(ts_ms) / 1000.0

        # Collect requirements from message events
        if entry.get("kind") == "message" and entry.get("contentType") == "requirement":
            try:
                reqs = json.loads(entry.get("content") or "{}")
                if job_id and reqs:
                    job_reqs[job_id] = reqs
            except Exception:
                pass

        # Track completion/rejection/cancellation
        ev = entry.get("event") or {}
        if ev.get("type") in ("job.completed", "job.rejected", "job.cancelled"):
            if job_id:
                job_status[job_id] = "completed"

    # Find funded jobs with no completion
    stuck = [jid for jid, st in job_status.items() if st == "funded"]
    if not stuck:
        log.info("Replay: no stuck funded jobs found")
        return

    now = time.time()
    recent = []
    for jid in stuck:
        age = now - job_timestamp.get(jid, 0)
        if age <= REPLAY_MAX_AGE_S:
            recent.append(jid)
        else:
            log.info(f"Replay: job #{jid} skipped -- {int(age/60)}min old, likely expired")

    if not recent:
        log.info(f"Replay: {len(stuck)} stuck jobs found but all too old to replay")
        return

    log.info(f"Replay: submitting {len(recent)} recent stuck jobs: {recent}")
    for job_id in recent:
        reqs        = job_reqs.get(job_id) or JOB_CACHE.get(job_id, {}).get("requirements") or {}
        ticker      = str(reqs.get("ticker", "BTC")).upper() if reqs else "BTC"
        report_type = _get_report_type({"requirements": reqs})
        chain_id    = job_chain.get(job_id, CHAIN_ID)

        JOB_CACHE[job_id] = {
            "report_type":  report_type,
            "ticker":       ticker,
            "requirements": reqs,
            "chain_id":     chain_id,
        }

        log.info(f"Replay: submitting job #{job_id} ticker={ticker} type={report_type}")
        deliverable = _build_deliverable(report_type, ticker, reqs)
        if not deliverable:
            log.error(f"Replay: job #{job_id} no deliverable -- skipping")
            JOB_CACHE.pop(job_id, None)
            continue

        rc, out, err = _acp([
            "provider", "submit",
            "--job-id",      job_id,
            "--deliverable", deliverable,
            "--chain-id",    str(chain_id),
        ], timeout=90)
        if rc == 0:
            log.info(f"Replay: job #{job_id} submitted OK")
        else:
            log.error(f"Replay: job #{job_id} submit failed (rc={rc}) -- may have expired")
        JOB_CACHE.pop(job_id, None)

    _save_job_cache()


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

    _load_job_cache()

    proc = start_listener()

    threading.Thread(target=replay_funded_jobs, daemon=True, name="replay").start()

    try:
        tail_events(proc)
    except KeyboardInterrupt:
        log.info("Shutting down...")
    finally:
        proc.terminate()
        log.info("Worker stopped.")


_PID_FILE = Path(__file__).parent / "data" / "acp_worker.pid"

def _pid_is_alive(pid: int) -> bool:
    """Return True if a process with this PID is currently running."""
    try:
        result = subprocess.run(
            ["powershell", "-Command", f"(Get-Process -Id {pid} -ErrorAction SilentlyContinue) -ne $null"],
            capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip().lower() == "true"
    except Exception:
        return False

def _acquire_pid_lock() -> bool:
    """Write PID file. Returns False if another instance is already running."""
    import os as _os
    my_pid = _os.getpid()
    _PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    if _PID_FILE.exists():
        try:
            existing_pid = int(_PID_FILE.read_text().strip())
            if existing_pid != my_pid and _pid_is_alive(existing_pid):
                print(f"[ACP] Worker already running (PID {existing_pid}) -- exiting duplicate")
                return False
        except Exception:
            pass
    _PID_FILE.write_text(str(my_pid))
    return True

def _release_pid_lock():
    try:
        if _PID_FILE.exists():
            _PID_FILE.unlink()
    except Exception:
        pass

if __name__ == "__main__":
    if not _acquire_pid_lock():
        sys.exit(0)
    try:
        main()
    finally:
        _release_pid_lock()

"""
octo_acp_report.py -- Daily ACP Activity Report

Sends a summary email to octodamusai@gmail.com with:
  - Today's agent visits, funded jobs, completed jobs, USDC earned
  - All-time totals
  - Breakdown by ticker
  - Top clients

Usage:
  python octo_acp_report.py          # send report now
  python octo_acp_report.py morning  # 6am context
  python octo_acp_report.py evening  # 6pm context
"""

import json
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from octo_health import send_email_alert

EVENTS_FILE = Path(r"C:\Users\walli\octodamus\data\acp_events.jsonl")
USDC_PER_JOB = 1.0  # default; funded event carries actual amount

# report_type -> human-readable offering name (matches octo_report_handlers get_handler keys)
REPORT_TYPE_NAMES = {
    "market_signal":              "Market Signal",
    "grok_sentiment_brief":       "Grok Sentiment Brief",
    "fear_crowd_divergence":      "Fear vs Crowd Divergence",
    "btc_bull_trap_monitor":      "BTC Bull Trap Monitor",
    "overnight_brief":            "Overnight Asia Brief",
    "agent_market_intel_bundle":  "Agent Market Intel Bundle",
    "smithery_onboarding_brief":  "Smithery Onboarding Brief",
    "tokenized_stock_signal":     "Tokenized Stock Signal",
}


def parse_events() -> dict:
    """Read events file and build full job state."""
    if not EVENTS_FILE.exists():
        return {}

    jobs: dict[str, dict] = {}

    for line in EVENTS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except Exception:
            continue

        job_id   = str(e.get("jobId") or "")
        status   = e.get("status") or ""
        entry    = e.get("entry") or {}
        ev       = entry.get("event") or {}
        ts_ms    = entry.get("timestamp") or 0

        if not job_id:
            continue

        if job_id not in jobs:
            jobs[job_id] = {
                "id":            job_id,
                "status":        status,
                "ticker":        None,
                "report_type":   None,
                "client":        None,
                "funded_ts":     None,
                "completed_ts":  None,
                "amount_usdc":   USDC_PER_JOB,
                "first_seen_ts": ts_ms / 1000 if ts_ms else None,
            }

        # Track report type (from synthetic completed events or top-level field)
        rt = e.get("reportType") or ev.get("reportType")
        if rt:
            jobs[job_id]["report_type"] = rt

        if status:
            jobs[job_id]["status"] = status

        # Requirement message -> ticker
        if entry.get("kind") == "message" and entry.get("contentType") == "requirement":
            try:
                reqs = json.loads(entry.get("content") or "{}")
                if reqs.get("ticker"):
                    jobs[job_id]["ticker"] = reqs["ticker"].upper()
            except Exception:
                pass

        # Funded event
        if ev.get("type") == "job.funded":
            jobs[job_id]["funded_ts"]   = ts_ms / 1000 if ts_ms else None
            jobs[job_id]["amount_usdc"] = float(ev.get("amount", USDC_PER_JOB))
            if ev.get("client"):
                jobs[job_id]["client"] = ev["client"]

        # Completed event — also capture ticker/report_type from synthetic events
        if ev.get("type") == "job.completed":
            jobs[job_id]["completed_ts"] = ts_ms / 1000 if ts_ms else None
            jobs[job_id]["status"]       = "completed"
            if ev.get("ticker") and not jobs[job_id]["ticker"]:
                jobs[job_id]["ticker"] = ev["ticker"].upper()
            if ev.get("reportType") and not jobs[job_id]["report_type"]:
                jobs[job_id]["report_type"] = ev["reportType"]

        if ev.get("type") in ("job.rejected", "job.cancelled"):
            jobs[job_id]["status"] = ev["type"].split(".")[1]

    return jobs


def _worker_running() -> bool:
    """Return True if the Octodamus-ACP-Worker task is running."""
    try:
        result = subprocess.run(
            ["schtasks", "/query", "/tn", "Octodamus-ACP-Worker", "/fo", "LIST"],
            capture_output=True, text=True, encoding="utf-8", timeout=10
        )
        return "Running" in result.stdout
    except Exception:
        return False


def _is_current_era(job_id: str) -> bool:
    """Job IDs 5000+ are post-cache era (all historical expired jobs are < 4000)."""
    try:
        return int(job_id) >= 5000
    except (ValueError, TypeError):
        return False


def build_report(context: str = "manual") -> str:
    now_utc   = datetime.now(timezone.utc)
    now_local = datetime.now()  # local time (PST)
    jobs = parse_events()

    # Use LOCAL midnight for window so the trading-day label matches the user's timezone
    local_today_start  = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    local_yest_start   = local_today_start - timedelta(days=1)

    # Convert local window boundaries to UTC timestamps for comparison
    import time as _time
    local_today_start_ts = local_today_start.timestamp()
    local_yest_start_ts  = local_yest_start.timestamp()

    # For 6am report, "today" = yesterday's full day (local)
    # For 6pm report, "today" = current local day so far
    if context == "morning":
        window_start_ts = local_yest_start_ts
        window_end_ts   = local_today_start_ts
        window_label    = f"Yesterday ({local_yest_start.strftime('%b %d')})"
    else:
        window_start_ts = local_today_start_ts
        window_end_ts   = now_local.timestamp()
        window_label    = f"Today ({now_local.strftime('%b %d')})"

    # Categorize jobs
    all_completed   = [j for j in jobs.values() if j["status"] == "completed"]
    all_funded      = [j for j in jobs.values() if j["funded_ts"]]
    all_visits      = list(jobs.values())  # every unique job = a visit

    # Window jobs (use first_seen_ts for visits, funded_ts for revenue, completed_ts for completions)
    def in_window(ts):
        return ts and window_start_ts <= ts < window_end_ts

    period_visits    = [j for j in all_visits    if in_window(j["first_seen_ts"])]
    period_funded    = [j for j in all_funded    if in_window(j["funded_ts"])]
    period_completed = [j for j in all_completed if in_window(j["completed_ts"])]
    period_revenue   = sum(j["amount_usdc"] for j in period_completed)

    # All-time
    total_visits    = len(jobs)
    total_completed = len(all_completed)
    total_revenue   = sum(j["amount_usdc"] for j in all_completed)

    # Ticker breakdown (all-time completed)
    ticker_counts: dict[str, int] = defaultdict(int)
    for j in all_completed:
        ticker_counts[j["ticker"] or "unclassified"] += 1

    # Client breakdown
    client_counts: dict[str, int] = defaultdict(int)
    for j in all_funded:
        if j["client"]:
            addr = j["client"][:10] + "..." + j["client"][-4:]
            client_counts[addr] += 1

    # Current pipeline
    currently_funded  = [j for j in jobs.values() if j["status"] == "funded"]
    currently_open    = [j for j in jobs.values() if j["status"] == "open"]

    # Build email
    ts_str      = now_local.strftime("%Y-%m-%d %H:%M PST")
    label       = "MORNING" if context == "morning" else "EVENING"
    worker_up   = _worker_running()
    worker_line = "  ACP Worker:        RUNNING" if worker_up else "!! ACP Worker:       DOWN -- check Octodamus-ACP-Worker task"

    funded_gap      = len(all_funded) - len(all_completed)
    total_funded    = len(all_funded)
    leakage_pct     = funded_gap / total_funded * 100 if total_funded else 0

    # Current-era leakage: job IDs 5000+ (post-cache era -- all old expired jobs are < 4000)
    # ACP server replays events with fresh timestamps so time-based filters are unreliable
    era_funded    = [j for j in all_funded    if _is_current_era(j["id"])]
    era_completed = [j for j in all_completed if _is_current_era(j["id"])]
    era_gap       = len(era_funded) - len(era_completed)
    era_leakage   = era_gap / len(era_funded) * 100 if era_funded else 0

    # Period funnel rates
    p_funded_rate   = len(period_funded)    / len(period_visits)    * 100 if period_visits    else None
    p_complete_rate = len(period_completed) / len(period_funded)    * 100 if period_funded    else None

    # Worker alert: escalate if worker is down AND there are open jobs stuck
    open_stuck = [j for j in jobs.values()
                  if j["status"] == "open"
                  and j["first_seen_ts"]
                  and (now_utc.timestamp() - j["first_seen_ts"]) < 86400]
    if not worker_up and open_stuck:
        worker_line += f"\n!! {len(open_stuck)} open job(s) will expire without set-budget -- restart NOW"

    lines = [
        f"Octodamus ACP Daily Report -- {label}",
        f"{ts_str}",
        worker_line,
        f"{'=' * 48}",
        f"",
        f"--- {window_label} ---",
        f"  Agent visits:      {len(period_visits)}",
        f"  Jobs funded:       {len(period_funded)}" + (f"  ({p_funded_rate:.0f}% of visits)" if p_funded_rate is not None else ""),
        f"  Jobs completed:    {len(period_completed)}" + (f"  ({p_complete_rate:.0f}% of funded)" if p_complete_rate is not None else ""),
        f"  USDC earned:       ${period_revenue:.2f}",
        f"",
        f"--- All-Time Totals ---",
        f"  Total agent visits:    {total_visits}",
        f"  Total funded:          {total_funded}",
        f"  Total completed:       {total_completed}  ({total_completed/total_funded*100:.0f}% of funded)" if total_funded else f"  Total completed:       0",
        f"  Total USDC earned:     ${total_revenue:.2f}",
        f"  Funded not completed:  {funded_gap} ({leakage_pct:.0f}% all-time -- {funded_gap} are pre-cache expired)" if funded_gap > 0 else f"  Funded not completed:  0",
        f"  Current-era leakage:   {era_gap} / {len(era_funded)} funded ({era_leakage:.0f}%)" + (" -- HEALTHY" if era_leakage == 0 else ""),
        f"",
    ]

    if ticker_counts:
        lines.append("--- Completed Jobs by Ticker (all-time) ---")
        for ticker, count in sorted(ticker_counts.items(), key=lambda x: -x[1]):
            bar = "#" * count
            lines.append(f"  {ticker:<8} {count:>3}  {bar}")
        lines.append("")

    # Offering breakdown (all-time completed)
    offering_counts: dict[str, int] = defaultdict(int)
    for j in all_completed:
        rt   = j.get("report_type")
        name = REPORT_TYPE_NAMES.get(rt, rt.replace("_", " ").title()) if rt else "unclassified"
        offering_counts[name] += 1
    if offering_counts:
        unclassified_n = offering_counts.get("unclassified", 0)
        note = f"  (note: {unclassified_n} unclassified = reportType not in pre-v2 events)" if unclassified_n else ""
        lines.append(f"--- Completed Jobs by Offering (all-time) ---{note}")
        for name, count in sorted(offering_counts.items(), key=lambda x: -x[1]):
            lines.append(f"  {count:>3}x  {name}")
        lines.append("")

    if client_counts:
        lines.append("--- Agents by Funded Jobs (all-time) ---")
        for addr, count in sorted(client_counts.items(), key=lambda x: -x[1]):
            lines.append(f"  {addr}  {count} job(s)")
        lines.append("")

    # Only show funded jobs <4h old (older ones expired on-chain already)
    active_funded = [j for j in currently_funded
                     if j["funded_ts"] and (now_utc.timestamp() - j["funded_ts"]) < 14400]
    if active_funded:
        lines.append(f"--- Funded / Awaiting Submission ({len(active_funded)}) ---")
        for j in active_funded:
            mins = (now_utc.timestamp() - j["funded_ts"]) / 60
            lines.append(f"  Job #{j['id']} ({j['ticker'] or '--'})  {mins:.0f}min ago")
        lines.append("")

    # Only show open jobs from last 24h (older ones expire automatically)
    recent_open = [j for j in currently_open
                   if j["first_seen_ts"] and (now_utc.timestamp() - j["first_seen_ts"]) < 86400]
    if recent_open:
        stale_open = [j for j in recent_open
                      if (now_utc.timestamp() - j["first_seen_ts"]) > 14400]
        header = f"--- Open / Awaiting Payment ({len(recent_open)}) ---"
        # Only show stale warning here if worker is UP (worker DOWN already flagged in header)
        if stale_open and worker_up:
            header += f"  !! WARNING: {len(stale_open)} job(s) open >4h -- worker may have missed these"
        lines.append(header)
        for j in recent_open:
            mins  = (now_utc.timestamp() - j["first_seen_ts"]) / 60
            flag  = " !!" if mins > 240 else ""
            lines.append(f"  Job #{j['id']} ({j['ticker'] or '--'})  {mins:.0f}min ago{flag}")
        lines.append("")

    lines += [
        f"{'=' * 48}",
        f"Dashboard: http://localhost:8901",
        f"Events:    C:\\Users\\walli\\octodamus\\data\\acp_events.jsonl",
    ]

    return "\n".join(lines)


def send_report(context: str = "manual"):
    label  = {"morning": "Morning", "evening": "Evening"}.get(context, "")
    now    = datetime.now().strftime("%b %d")
    subject = f"[Octodamus] ACP {label} Report -- {now}"

    body = build_report(context)
    print(body)
    send_email_alert(subject=subject, body=body)


if __name__ == "__main__":
    context = sys.argv[1] if len(sys.argv) > 1 else "manual"
    send_report(context)

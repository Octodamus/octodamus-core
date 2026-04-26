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
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from octo_health import send_email_alert

EVENTS_FILE = Path(r"C:\Users\walli\octodamus\data\acp_events.jsonl")
USDC_PER_JOB = 1.0  # default; funded event carries actual amount

# Offering ID -> name map (update when new offerings added)
OFFERING_NAMES = {
    "019dca02-a0c3-7b39-8efe-1279c5cb9307": "Grok Sentiment Brief ($1)",
    "019dca05-6bdc-7228-adc2-f00585f46af1": "Divergence Alert ($2)",
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
                "offering_id":   None,
                "client":        None,
                "funded_ts":     None,
                "completed_ts":  None,
                "amount_usdc":   USDC_PER_JOB,
                "first_seen_ts": ts_ms / 1000 if ts_ms else None,
            }

        # Track offering ID
        offering_id = e.get("offeringId") or ev.get("offeringId") or entry.get("offeringId")
        if offering_id:
            jobs[job_id]["offering_id"] = offering_id

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

        # Completed event
        if ev.get("type") == "job.completed":
            jobs[job_id]["completed_ts"] = ts_ms / 1000 if ts_ms else None
            jobs[job_id]["status"]       = "completed"

        if ev.get("type") in ("job.rejected", "job.cancelled"):
            jobs[job_id]["status"] = ev["type"].split(".")[1]

    return jobs


def build_report(context: str = "manual") -> str:
    now_utc = datetime.now(timezone.utc)
    jobs = parse_events()

    # Today window: midnight UTC
    today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    # Yesterday for morning report context
    yesterday_start = today_start - timedelta(days=1)

    # For 6am report, "today" = yesterday's full day
    # For 6pm report, "today" = current day so far
    if context == "morning":
        window_start = yesterday_start
        window_end   = today_start
        window_label = f"Yesterday ({yesterday_start.strftime('%b %d')})"
    else:
        window_start = today_start
        window_end   = now_utc
        window_label = f"Today ({today_start.strftime('%b %d')})"

    window_start_ts = window_start.timestamp()
    window_end_ts   = window_end.timestamp()

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
        ticker_counts[j["ticker"] or "unknown"] += 1

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
    ts_str = now_utc.strftime("%Y-%m-%d %H:%M UTC")
    label  = "MORNING" if context == "morning" else "EVENING"

    lines = [
        f"Octodamus ACP Daily Report -- {label}",
        f"{ts_str}",
        f"{'=' * 48}",
        f"",
        f"--- {window_label} ---",
        f"  Agent visits:      {len(period_visits)}",
        f"  Jobs funded:       {len(period_funded)}",
        f"  Jobs completed:    {len(period_completed)}",
        f"  USDC earned:       ${period_revenue:.2f}",
        f"  Conversion rate:   {len(period_completed)/len(period_visits)*100:.0f}%" if period_visits else "  Conversion rate:   n/a",
        f"",
        f"--- All-Time Totals ---",
        f"  Total agent visits:    {total_visits}",
        f"  Total completed jobs:  {total_completed}",
        f"  Total USDC earned:     ${total_revenue:.2f}",
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
        oid  = j.get("offering_id") or "unknown"
        name = OFFERING_NAMES.get(oid, f"Market Signal (legacy)")
        offering_counts[name] += 1
    if offering_counts:
        lines.append("--- Completed Jobs by Offering (all-time) ---")
        for name, count in sorted(offering_counts.items(), key=lambda x: -x[1]):
            lines.append(f"  {count:>3}x  {name}")
        lines.append("")

    if client_counts:
        lines.append("--- Agents by Job Count (all-time) ---")
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
            lines.append(f"  Job #{j['id']} ({j['ticker'] or '?'})  {mins:.0f}min ago")
        lines.append("")

    # Only show open jobs from last 24h (older ones expire automatically)
    recent_open = [j for j in currently_open
                   if j["first_seen_ts"] and (now_utc.timestamp() - j["first_seen_ts"]) < 86400]
    if recent_open:
        lines.append(f"--- Open / Awaiting Payment ({len(recent_open)}) ---")
        for j in recent_open:
            mins = (now_utc.timestamp() - j["first_seen_ts"]) / 60
            lines.append(f"  Job #{j['id']} ({j['ticker'] or '?'})  {mins:.0f}min ago")
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

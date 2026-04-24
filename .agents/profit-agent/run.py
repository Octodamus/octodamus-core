"""
.agents/profit-agent/run.py
Profit Agent orchestrator -- runs Franklin with the profit-or-die mission.

Usage:
  python .agents/profit-agent/run.py          # full session
  python .agents/profit-agent/run.py --dry    # print prompt only
  python .agents/profit-agent/run.py --status # check wallet + last session

Schedule: runs twice daily via Octodamus-ProfitAgent task.
Reports to: octodamusai@gmail.com
"""

import argparse
import json
import smtplib
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from email.mime.text import MIMEText
from pathlib import Path

ROOT         = Path(__file__).parent.parent.parent   # octodamus root
SECRETS_FILE = ROOT / ".octo_secrets"
MISSION_FILE = Path(__file__).parent / "mission.md"
LOG_FILE     = Path(__file__).parent / "session.log"
STATE_FILE   = Path(__file__).parent / "state.json"
NOTIFY_EMAIL  = "octodamusai@gmail.com"
FRANKLIN_BIN  = r"C:\Users\walli\AppData\Roaming\npm\franklin.cmd"

# Hard limits
MAX_SPEND_PER_SESSION = 2.00   # USD -- per Franklin session
DEAD_THRESHOLD        = 10.00  # USD -- stop all activity below this


def _secrets() -> dict:
    try:
        raw = json.loads(SECRETS_FILE.read_text(encoding="utf-8"))
        return raw.get("secrets", raw)
    except Exception:
        return {}


def _send_email(subject: str, body: str):
    s = _secrets()
    user = s.get("GMAIL_USER", "")
    pw   = s.get("GMAIL_APP_PASSWORD", "")
    if not user or not pw:
        print(f"[ProfitAgent] No Gmail creds -- skipping: {subject}")
        return
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"]    = user
        msg["To"]      = NOTIFY_EMAIL
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as smtp:
            smtp.starttls()
            smtp.login(user, pw)
            smtp.send_message(msg)
        print(f"[ProfitAgent] Email sent: {subject}")
    except Exception as e:
        print(f"[ProfitAgent] Email failed: {e}")


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"sessions": 0, "started_at": datetime.now().isoformat(), "dead": False}


def _save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _get_wallet_balance() -> float:
    """Check Franklin wallet balance. Returns 0.0 if unfunded, -1.0 on error."""
    try:
        r = subprocess.run(
            f'"{FRANKLIN_BIN}" balance',
            capture_output=True, text=True, encoding="utf-8", timeout=30,
            shell=True
        )
        output = (r.stdout + r.stderr).strip()
        import re
        # Format: "USDC Balance: $201.00"
        match = re.search(r"USDC Balance:\s*\$?([\d,]+\.?\d*)", output, re.IGNORECASE)
        if match:
            return float(match.group(1).replace(",", ""))
        # Fallback: any number followed by USDC
        match = re.search(r"(\d[\d,]*\.?\d*)\s*USDC", output, re.IGNORECASE)
        if match:
            return float(match.group(1).replace(",", ""))
        if r.returncode == 0:
            return 0.0
    except Exception as e:
        print(f"[ProfitAgent] Balance check failed: {e}")
    return -1.0


def run_session(dry_run: bool = False):
    state = _load_state()

    if state.get("dead"):
        print("[ProfitAgent] Agent is dead. Wallet depleted below threshold. Exiting.")
        return

    # Check wallet
    balance = _get_wallet_balance()
    now = datetime.now().strftime("%A %B %d %Y %I:%M %p")
    session_num = state.get("sessions", 0) + 1

    print(f"[ProfitAgent] Session #{session_num} | Balance: ${balance:.2f} USDC | {now}")

    if balance >= 0 and balance < DEAD_THRESHOLD:
        msg = f"Wallet below ${DEAD_THRESHOLD:.0f} USDC (${balance:.2f}). Agent stopping."
        print(f"[ProfitAgent] DEAD: {msg}")
        state["dead"] = True
        state["dead_at"] = now
        state["final_balance"] = balance
        _save_state(state)
        _send_email(
            "[ProfitAgent] DEAD -- Wallet Depleted",
            f"{msg}\n\nStarted: {state.get('started_at','?')}\nSessions run: {session_num-1}\nFinal balance: ${balance:.2f}\n\n-- Profit Agent"
        )
        return

    mission = MISSION_FILE.read_text(encoding="utf-8")

    if dry_run:
        print("[ProfitAgent] DRY RUN -- would run Franklin with:")
        print(f"  --max-spend {MAX_SPEND_PER_SESSION}")
        print(f"  --prompt [mission.md]")
        print(f"\nMission preview:\n{mission[:300]}...")
        return

    print(f"[ProfitAgent] Running autonomous agent loop...")

    result = subprocess.run(
        [sys.executable, str(Path(__file__).parent / "agent.py")],
        capture_output=True, text=True, encoding="utf-8",
        timeout=1800,
        cwd=str(ROOT),
    )

    output = result.stdout + result.stderr
    LOG_FILE.write_text(
        f"=== Session #{session_num} -- {now} ===\n{output}\n",
        encoding="utf-8"
    )

    # Update state
    state["sessions"] = session_num
    state["last_run"] = now
    state["last_balance"] = balance
    _save_state(state)

    # Email the session report
    summary = output[-3000:] if len(output) > 3000 else output
    _send_email(
        f"[ProfitAgent] Session #{session_num} Report",
        f"Profit Agent completed session #{session_num}.\n\nWallet before: ${balance:.2f} USDC\nTime: {now}\n\n--- Agent Output ---\n{summary}\n\n-- Profit Agent"
    )

    print(f"[ProfitAgent] Session #{session_num} complete. Report emailed.")


def show_status():
    state = _load_state()
    balance = _get_wallet_balance()
    print(f"""
Profit Agent Status
===================
Sessions run:  {state.get('sessions', 0)}
Started at:    {state.get('started_at', '?')}
Last run:      {state.get('last_run', 'never')}
Wallet:        ${balance:.2f} USDC
Dead:          {state.get('dead', False)}
""")


def send_report(context: str = "status"):
    """Email a wallet + activity summary. No new session. Used for 6am/6pm reports."""
    state   = _load_state()
    balance = _get_wallet_balance()
    now     = datetime.now().strftime("%A %B %d %Y %I:%M %p")
    started = state.get("started_at", "?")[:10]
    sessions = state.get("sessions", 0)
    last_run = state.get("last_run", "never")
    start_balance = 201.00  # funded amount

    # Simple P&L calc
    pnl = balance - start_balance if balance >= 0 else 0
    pnl_str = f"${pnl:+.2f} USDC ({pnl/start_balance*100:+.1f}%)" if balance >= 0 else "unknown"

    # Last session output snippet
    last_output = ""
    if LOG_FILE.exists():
        log = LOG_FILE.read_text(encoding="utf-8")
        last_output = log[-2000:] if len(log) > 2000 else log

    dead_line = ""
    if state.get("dead"):
        dead_line = f"\nSTATUS: DEAD — wallet depleted. Final: ${state.get('final_balance','?')}\n"

    subject = f"[ProfitAgent] {context.title()} Report — {now}"
    body = f"""Franklin Profit Agent — {context.title()} Report
{'='*52}
Time:          {now}
Wallet:        ${balance:.2f} USDC
P&L vs start:  {pnl_str}
Sessions run:  {sessions}
Last session:  {last_run}
Started:       {started}
{dead_line}
Next sessions: 9am + 3pm daily

--- Last Session Output ---
{last_output if last_output else 'No sessions run yet.'}

-- Profit Agent
"""
    _send_email(subject, body)
    print(f"[ProfitAgent] {context.title()} report sent.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry",    action="store_true", help="Print prompt, don't run")
    ap.add_argument("--status", action="store_true", help="Show wallet + session state")
    ap.add_argument("--report", metavar="CONTEXT",  help="Email a status report (morning/evening/manual)")
    args = ap.parse_args()

    if args.status:
        show_status()
    elif args.report:
        send_report(args.report)
    else:
        run_session(dry_run=args.dry)

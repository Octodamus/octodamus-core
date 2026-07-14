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


def run_session(dry_run: bool = False, session_type: str = ""):
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

    session_label = session_type or "auto"
    print(f"[ProfitAgent] Running autonomous agent loop ({session_label})...")

    cmd = [sys.executable, str(Path(__file__).parent / "agent.py")]
    if session_type:
        cmd += ["--session", session_type]

    import time as _time
    result = None
    for _attempt in range(3):
        if _attempt > 0:
            wait = 60 * _attempt
            print(f"[ProfitAgent] API overloaded — retrying in {wait}s (attempt {_attempt+1}/3)...")
            _time.sleep(wait)
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=1800,
            cwd=str(ROOT),
        )
        # errors="replace" prevents a UnicodeDecodeError on cp1252 bytes (e.g. em-dash 0x97)
        # in agent output from crashing the pipe reader and leaving stdout=None.
        _out = (result.stdout or "") + (result.stderr or "")
        if "overloaded_error" not in _out.lower() and "error code: 529" not in _out.lower():
            break
        print(f"[ProfitAgent] Attempt {_attempt+1} hit 529 overloaded.")

    output = (result.stdout or "") + (result.stderr or "")
    LOG_FILE.write_text(
        f"=== Session #{session_num} -- {now} ===\n{output}\n",
        encoding="utf-8"
    )

    # Update state
    state["sessions"] = session_num
    state["last_run"] = now
    state["last_balance"] = balance
    _save_state(state)

    # Agent sends its own session email via octo_notify. Suppress run.py duplicate.
    if f"[Agent] Session #{session_num} complete. Email sent." in output:
        print(f"[ProfitAgent] Agent already emailed session #{session_num} -- suppressing duplicate.")
    else:
        summary = output[-3000:] if len(output) > 3000 else output
        _send_email(
            f"[ProfitAgent] Session #{session_num} Report [FALLBACK]",
            f"Profit Agent session #{session_num} -- agent did not send its own email.\n\nWallet before: ${balance:.2f} USDC\nTime: {now}\n\n--- Agent Output ---\n{summary}\n\n-- Profit Agent"
        )

    print(f"[ProfitAgent] Session #{session_num} complete.")


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
    state         = _load_state()
    live_balance  = _get_wallet_balance()
    # Fall back to cached balance from last session when live check fails
    balance       = live_balance if live_balance >= 0 else state.get("last_balance", -1.0)
    balance_note  = " (cached)" if live_balance < 0 and balance >= 0 else ""

    now      = datetime.now().strftime("%A %B %d %Y %I:%M %p")
    started  = state.get("started_at", "?")[:10]
    sessions = state.get("sessions", 0)
    last_run = state.get("last_run", "never")
    start_balance = 201.00

    # P&L calc — USDC only; ~$24 USDC was swapped to ETH 2026-05-02 (not a loss)
    pnl     = balance - start_balance if balance >= 0 else 0
    pnl_str = f"${pnl:+.2f} USDC ({pnl/start_balance*100:+.1f}%) [USDC only — ETH position held separately]" if balance >= 0 else "unknown"

    # Read agent_session.log — show sessions from last 36h only (5 max)
    import re as _re2
    from datetime import timedelta
    agent_log_file = Path(__file__).parent / "agent_session.log"
    last_sessions_text = ""
    if agent_log_file.exists():
        raw = agent_log_file.read_text(encoding="utf-8", errors="replace")
        blocks = _re2.split(r"(?=\n={20,}\nSession #)", "\n" + raw)
        cutoff = datetime.now() - timedelta(hours=36)
        non_empty = []
        all_with_content = []
        for b in blocks:
            stripped = b.strip()
            if not stripped:
                continue
            inner = _re2.sub(r"={20,}", "", _re2.sub(r"Session #\d+.*", "", stripped)).strip()
            if not inner:
                continue
            all_with_content.append(stripped)
            m = _re2.search(r"Session #\d+ -- (.+)", stripped)
            if m:
                try:
                    ts = datetime.strptime(m.group(1).strip(), "%A %B %d %Y %I:%M %p")
                    if ts >= cutoff:
                        non_empty.append(stripped)
                    continue
                except ValueError:
                    pass
            non_empty.append(stripped)
        # Fallback: no recent sessions — show last 3
        if not non_empty:
            non_empty = all_with_content[-3:]
        last_sessions_text = "\n\n".join(non_empty[-5:]) if non_empty else raw[-2000:]
    elif LOG_FILE.exists():
        log = LOG_FILE.read_text(encoding="utf-8", errors="replace")
        last_sessions_text = (log[-2000:] if len(log) > 2000 else log).strip()

    dead_line = ""
    if state.get("dead"):
        dead_line = f"\nSTATUS: DEAD -- wallet depleted. Final: ${state.get('final_balance','?')}\n"

    subject = f"[ProfitAgent] {context.title()} Report -- {now}"
    body = f"""Franklin Profit Agent -- {context.title()} Report
{'=' * 52}
Time:          {now}
Wallet:        ${balance:.2f} USDC{balance_note}
P&L vs start:  {pnl_str}
Sessions run:  {sessions}
Last session:  {last_run}
Started:       {started}
{dead_line}
--- Recent Sessions (last 36h) ---
{last_sessions_text if last_sessions_text else 'No sessions run yet.'}

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
        run_session(dry_run=args.dry, session_type=getattr(args, "session", ""))

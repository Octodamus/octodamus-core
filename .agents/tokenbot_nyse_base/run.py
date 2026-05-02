"""
.agents/tokenbot_nyse_base/run.py
TokenBot_NYSE_Base orchestrator.

Runs the paper trading agent and emails reports at 6am and 6pm.

Usage:
  python .agents/tokenbot_nyse_base/run.py --morning    # 6am session + email
  python .agents/tokenbot_nyse_base/run.py --evening    # 6pm session + email
  python .agents/tokenbot_nyse_base/run.py --status     # print portfolio state
  python .agents/tokenbot_nyse_base/run.py --dry        # dry run (no API calls)

Schedule (Windows Task Scheduler):
  Octodamus-TokenBot-6am  : 6:15 AM daily -> run.py --morning  (15 min before NYSE open 6:30 AM PST)
  Octodamus-TokenBot-4pm  : 4:00 PM daily -> run.py --evening  (NYSE close / Tokyo open 4:00 PM PST)
"""

import argparse
import json
import smtplib
import subprocess
import sys
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path

ROOT          = Path(__file__).parent.parent.parent
SECRETS_FILE  = ROOT / ".octo_secrets"
STATE_FILE    = Path(__file__).parent / "state.json"
LOG_FILE      = Path(__file__).parent / "session.log"
NOTIFY_EMAIL  = "octodamusai@gmail.com"


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
        print(f"[TokenBot] No Gmail creds -- skipping email: {subject}")
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
        print(f"[TokenBot] Email sent: {subject}")
    except Exception as e:
        print(f"[TokenBot] Email failed: {e}")


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {
            "mode": "paper", "starting_capital": 1000.0, "cash": 1000.0,
            "positions": {}, "total_pnl": 0.0, "wins": 0, "losses": 0,
            "sessions": 0, "last_run": None,
        }


def run_session(session_type: str = "", dry: bool = False):
    state     = _load_state()
    now       = datetime.now().strftime("%A %B %d %Y %I:%M %p")
    sess_num  = state.get("sessions", 0) + 1
    cash      = state.get("cash", 1000.0)
    total_pnl = state.get("total_pnl", 0.0)
    wins      = state.get("wins", 0)
    losses    = state.get("losses", 0)
    open_pos  = len(state.get("positions", {}))

    print(f"[TokenBot] Session #{sess_num} | {session_type} | {now}")
    print(f"[TokenBot] Portfolio: ${cash:.2f} cash | P&L: ${total_pnl:+.2f} | {wins}W/{losses}L | {open_pos} open")

    cmd = [sys.executable, str(Path(__file__).parent / "agent.py")]
    if session_type:
        cmd += ["--session", session_type]
    if dry:
        cmd += ["--dry"]

    result = subprocess.run(
        cmd,
        capture_output=True, text=True, encoding="utf-8",
        timeout=1800,
        cwd=str(ROOT),
    )

    output = result.stdout + result.stderr
    LOG_FILE.write_text(
        f"=== Session #{sess_num} ({session_type}) -- {now} ===\n{output}\n",
        encoding="utf-8"
    )

    # Reload state after agent ran
    state2    = _load_state()
    cash2     = state2.get("cash", cash)
    total_pnl2 = state2.get("total_pnl", total_pnl)
    wins2     = state2.get("wins", wins)
    losses2   = state2.get("losses", losses)
    open_pos2 = len(state2.get("positions", {}))
    start_cap  = state2.get("starting_capital", 1000.0)
    pnl_pct    = total_pnl2 / start_cap * 100

    positions_str = "None"
    if state2.get("positions"):
        pos_lines = []
        for ticker, pos in state2["positions"].items():
            token  = pos.get("token", f"d{ticker}")
            entry  = pos.get("entry_price", 0)
            size   = pos.get("size_usd", 0)
            target = pos.get("target_price", 0)
            upnl   = pos.get("unrealized_pnl_pct", 0)
            pos_lines.append(f"  {token}: entry ${entry:.2f} | size ${size:.0f} | target ${target:.2f} | unrealized {upnl:+.1f}%")
        positions_str = "\n".join(pos_lines)

    label = session_type.title() if session_type else "Session"
    summary = output[-3000:] if len(output) > 3000 else output

    subject = f"[TokenBot] {label} Report -- {now[:16]} | P&L: ${total_pnl2:+.2f} | {wins2}W/{losses2}L"
    body = f"""TokenBot_NYSE_Base -- {label} Report
{'='*52}
Time:           {now}
Mode:           PAPER ($1,000 virtual USDC)

PORTFOLIO:
  Cash:         ${cash2:,.2f}
  Total P&L:    ${total_pnl2:+.2f} ({pnl_pct:+.1f}%)
  Record:       {wins2}W / {losses2}L
  Open positions: {open_pos2}

OPEN POSITIONS:
{positions_str}

--- Agent Session Output ---
{summary}

-- TokenBot_NYSE_Base | Paper trading tokenized NYSE stocks on Base
   When paper P&L proves profitable (>60% win rate, 20+ trades), flip live on Aerodrome.
"""
    _send_email(subject, body)
    print(f"[TokenBot] {label} report emailed. Session done.")


def show_status():
    state = _load_state()
    cash      = state.get("cash", 1000.0)
    total_pnl = state.get("total_pnl", 0.0)
    wins      = state.get("wins", 0)
    losses    = state.get("losses", 0)
    start_cap = state.get("starting_capital", 1000.0)
    sessions  = state.get("sessions", 0)
    positions = state.get("positions", {})
    trades    = state.get("trades", [])
    win_rate  = wins / (wins + losses) * 100 if (wins + losses) > 0 else 0

    print(f"""
TokenBot_NYSE_Base Status
==========================
Mode:          PAPER ($1,000 virtual USDC)
Sessions run:  {sessions}
Last run:      {state.get('last_run', 'never')}

PORTFOLIO:
  Cash:        ${cash:,.2f}
  Total P&L:   ${total_pnl:+.2f} ({total_pnl/start_cap*100:+.1f}%)
  Record:      {wins}W / {losses}L ({win_rate:.0f}% win rate)
  Trades done: {len(trades)}

OPEN POSITIONS ({len(positions)}):""")

    if positions:
        for ticker, pos in positions.items():
            token  = pos.get("token", f"d{ticker}")
            entry  = pos.get("entry_price", 0)
            size   = pos.get("size_usd", 0)
            held   = pos.get("sessions_held", 0)
            print(f"  {token}: entry ${entry:.2f} | size ${size:.0f} | held {held} sessions")
    else:
        print("  None")

    flip_target = 60
    trades_done = wins + losses
    print(f"""
LIVE FLIP CRITERIA:
  Win rate:    {win_rate:.0f}% / {flip_target}% target {'OK' if win_rate >= flip_target else 'NOT YET'}
  Trades done: {trades_done} / 20 minimum {'OK' if trades_done >= 20 else 'NOT YET'}
  Status:      {'READY TO FLIP LIVE' if win_rate >= flip_target and trades_done >= 20 else 'Still in paper mode'}
""")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--morning", action="store_true", help="Run 6:15am pre-NYSE-open session")
    ap.add_argument("--evening", action="store_true", help="Run 4pm NYSE-close/Asian-open session")
    ap.add_argument("--status",  action="store_true", help="Show portfolio status")
    ap.add_argument("--dry",     action="store_true", help="Dry run (no API calls)")
    args = ap.parse_args()

    if args.status:
        show_status()
    elif args.morning:
        run_session("morning", dry=args.dry)
    elif args.evening:
        run_session("evening", dry=args.dry)
    else:
        run_session("manual", dry=args.dry)

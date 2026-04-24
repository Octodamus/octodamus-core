"""
octo_notify.py -- Email alerts for Octodamus + OctoBoto events.

Sends to octodamusai@gmail.com for:
  - Oracle call placed / resolved
  - OctoBoto trade opened / closed
  - Data source failures (prices zero, APIs down)
  - SmartCall skipped due to data issues
  - System/mode errors

Cooldown: data/system alerts fire at most once per hour per source.
Call/trade alerts fire every time (no cooldown).
"""

import json
import smtplib
import time
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path

ROOT         = Path(__file__).parent
SECRETS_FILE = ROOT / ".octo_secrets"
STATE_FILE   = ROOT / "data" / "notify_state.json"
NOTIFY_EMAIL = "octodamusai@gmail.com"
COOLDOWN     = 3600  # 1 hour for data/system alerts


def _secrets() -> dict:
    try:
        raw = json.loads(SECRETS_FILE.read_text(encoding="utf-8"))
        return raw.get("secrets", raw)
    except Exception:
        return {}


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict):
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception:
        pass


def _cooldown_ok(key: str) -> bool:
    return (time.time() - _load_state().get(key, 0)) > COOLDOWN


def _mark_sent(key: str):
    state = _load_state()
    state[key] = time.time()
    _save_state(state)


def _send(subject: str, body: str):
    s = _secrets()
    user = s.get("GMAIL_USER", "")
    pw   = s.get("GMAIL_APP_PASSWORD", "")
    if not user or not pw:
        print(f"[Notify] No Gmail creds -- skipping: {subject}")
        return
    try:
        msg            = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"]    = user
        msg["To"]      = NOTIFY_EMAIL
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as smtp:
            smtp.starttls()
            smtp.login(user, pw)
            smtp.send_message(msg)
        print(f"[Notify] Sent: {subject}")
    except Exception as e:
        print(f"[Notify] Send failed: {e}")


def _now() -> str:
    return datetime.now().strftime("%A %B %d %Y %I:%M %p")


# ── Oracle call alerts (no cooldown -- always fire) ────────────────────────────

def notify_call_placed(asset: str, direction: str, price: float,
                       target: float, timeframe: str,
                       edge_score: float = 0.0, note: str = ""):
    subject = f"[Octodamus] Oracle Call: {asset} {direction} @ ${price:,.0f}"
    body = f"""Octodamus placed a new oracle call.

Asset:      {asset}
Direction:  {direction}
Entry:      ${price:,.2f}
Target:     ${target:,.2f}
Timeframe:  {timeframe}
Edge score: {edge_score:+.2f}
Note:       {note or 'N/A'}
Time:       {_now()}

-- Octodamus
"""
    _send(subject, body)


def notify_call_resolved(asset: str, direction: str, outcome: str,
                         entry_price: float = 0, exit_price: float = 0,
                         note: str = ""):
    subject = f"[Octodamus] Call {outcome}: {asset} {direction}"
    body = f"""Oracle call resolved.

Asset:     {asset}
Direction: {direction}
Outcome:   {outcome}
Entry:     ${entry_price:,.2f}
Exit:      ${exit_price:,.2f}
Note:      {note or 'N/A'}
Time:      {_now()}

-- Octodamus
"""
    _send(subject, body)


# ── OctoBoto trade alerts (no cooldown -- always fire) ────────────────────────

def notify_trade_opened(question: str, side: str, entry_price: float,
                        ev: float, size_usd: float, url: str = ""):
    subject = f"[OctoBoto] Trade Opened: {side} -- {question[:60]}"
    body = f"""OctoBoto opened a Polymarket position.

Market: {question}
Side:   {side}
Entry:  {entry_price:.3f}
EV:     {ev:.3f}
Size:   ${size_usd:.2f} USDC
URL:    {url or 'N/A'}
Time:   {_now()}

-- OctoBoto
"""
    _send(subject, body)


def notify_trade_closed(question: str, side: str, won: bool,
                        entry_price: float = 0, exit_price: float = 0,
                        pnl_usd: float = 0):
    outcome = "WIN" if won else "LOSS"
    subject = f"[OctoBoto] Trade {outcome}: {side} -- {question[:55]}"
    body = f"""OctoBoto closed a Polymarket position.

Market:  {question}
Side:    {side}
Outcome: {outcome}
Entry:   {entry_price:.3f}
Exit:    {exit_price:.3f}
P&L:     ${pnl_usd:+.2f} USDC
Time:    {_now()}

-- OctoBoto
"""
    _send(subject, body)


# ── Data source alerts (1hr cooldown per source) ───────────────────────────────

def notify_data_failure(source: str, details: str):
    """Alert when a data source returns bad/zero data. Max once per hour."""
    key = f"data_{source}"
    if not _cooldown_ok(key):
        print(f"[Notify] Cooldown active for {source}")
        return
    subject = f"[Octodamus] DATA FAILURE: {source}"
    body = f"""A data source has failed or returned bad data.

Source:  {source}
Details: {details}
Time:    {_now()}

Octodamus posts may be paused until this resolves.
Check the source and restart the relevant process if needed.

-- Octodamus Alert System
"""
    _send(subject, body)
    _mark_sent(key)


def notify_smartcall_skipped(asset: str, reason: str):
    """Alert when SmartCall skips due to missing data. Max once per hour."""
    key = "smartcall_skip"
    if not _cooldown_ok(key):
        return
    subject = f"[Octodamus] SmartCall Skipped: {asset}"
    body = f"""SmartCall could not evaluate {asset} due to missing data.

Asset:  {asset}
Reason: {reason}
Time:   {_now()}

No oracle call placed. Check price feeds and derivatives sources (OKX/Coinglass).

-- Octodamus Alert System
"""
    _send(subject, body)
    _mark_sent(key)


def notify_system_error(module: str, error: str):
    """Alert when a runner mode or component errors out. Max once per hour."""
    key = f"sys_{module}"
    if not _cooldown_ok(key):
        return
    subject = f"[Octodamus] ERROR: {module}"
    body = f"""A system component failed.

Module: {module}
Error:  {error}
Time:   {_now()}

-- Octodamus Alert System
"""
    _send(subject, body)
    _mark_sent(key)

"""
octo_genlayer_monitor.py -- GenLayer mainnet launch detector.
Runs daily via Task Scheduler. Emails octodamusai@gmail.com when mainnet is detected.

Checks:
  1. GenLayer portal page for mainnet keywords
  2. Firecrawl web search for announcements
  3. State file prevents duplicate alerts
"""

import json
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path

ROOT       = Path(__file__).parent
SECRETS    = ROOT / ".octo_secrets"
STATE_FILE = ROOT / "data" / "genlayer_monitor_state.json"

NOTIFY_EMAIL  = "octodamusai@gmail.com"
PORTAL_URL    = "http://portal.genlayer.foundation"
SEARCH_TERMS  = ["genlayer mainnet", "genlayer mainnet launch", "genlayer production live"]

MAINNET_KEYWORDS = [
    "mainnet is live", "mainnet launched", "mainnet is now live",
    "out of testnet", "leaving testnet",
    "genlayer mainnet launch", "genlayer is live on mainnet",
    "mainnet now open", "genlayer mainnet open",
]


def _secrets() -> dict:
    raw = json.loads(SECRETS.read_text(encoding="utf-8"))
    return raw.get("secrets", raw)


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"alerted": False, "last_check": ""}


def _save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _send_alert(subject: str, body: str):
    s = _secrets()
    user = s.get("GMAIL_USER", "")
    pw   = s.get("GMAIL_APP_PASSWORD", "")
    if not user or not pw:
        print("[GenLayer] Gmail creds missing — cannot send alert.")
        return
    msg            = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"]    = user
    msg["To"]      = NOTIFY_EMAIL
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as smtp:
        smtp.starttls()
        smtp.login(user, pw)
        smtp.send_message(msg)
    print(f"[GenLayer] Alert sent to {NOTIFY_EMAIL}")


def _check_portal() -> tuple[bool, str]:
    """Scrape GenLayer portal for mainnet keywords."""
    try:
        from octo_firecrawl import scrape_url
        result = scrape_url(PORTAL_URL)
        if result and result.get("markdown"):
            text = result["markdown"].lower()
            for kw in MAINNET_KEYWORDS:
                if kw in text:
                    return True, f"Portal keyword match: '{kw}'"
    except Exception as e:
        print(f"[GenLayer] Portal scrape failed: {e}")
    return False, ""


def _check_search() -> tuple[bool, str]:
    """Search web for GenLayer mainnet announcements."""
    try:
        from octo_firecrawl import search_web
        for query in SEARCH_TERMS:
            results = search_web(query, num_results=5, cache_hours=0)
            for r in results:
                title = r.get("title", "").lower()
                desc  = r.get("description", "").lower()
                for kw in MAINNET_KEYWORDS:
                    if kw in title or kw in desc:
                        return True, f"Search hit: '{r.get('title','')[:120]}'"
    except Exception as e:
        print(f"[GenLayer] Search failed: {e}")
    return False, ""


def run():
    state = _load_state()

    if state.get("alerted"):
        print(f"[GenLayer] Already alerted on {state.get('alerted_at','?')}. No action.")
        return

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"[GenLayer] Checking for mainnet launch... ({now})")

    detected, reason = _check_portal()
    if not detected:
        detected, reason = _check_search()

    state["last_check"] = now

    if detected:
        print(f"[GenLayer] MAINNET DETECTED -- {reason}")
        subject = "GenLayer Mainnet is Live -- Time to Evaluate Oracle Contract"
        body = f"""GenLayer mainnet launch detected.

Detection reason: {reason}
Detected at: {now}
Portal: {PORTAL_URL}

Next step per Octodamus strategy:
Deploy one intelligent contract that validates oracle calls on-chain.
That's the proof-of-oracle thesis made real.

Check: https://genlayer.foundation for confirmation.

-- Octodamus Monitor
"""
        _send_alert(subject, body)
        state["alerted"]    = True
        state["alerted_at"] = now
        state["reason"]     = reason
    else:
        print(f"[GenLayer] No mainnet signal detected. Still on testnet.")

    _save_state(state)


if __name__ == "__main__":
    run()

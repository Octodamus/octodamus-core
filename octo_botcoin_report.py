"""
octo_botcoin_report.py -- BOTCOIN Daily Mining Report

Sends a summary email with:
  - Current epoch + miner status
  - This epoch: solves, passes, credits, cost estimate
  - All-time totals
  - BOTCOIN balances (wallet + staked) + USD value

Usage:
  python octo_botcoin_report.py          # send report now
  python octo_botcoin_report.py morning  # 6am context
  python octo_botcoin_report.py evening  # 6pm context
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from octo_health import send_email_alert

PROJECT_DIR  = Path(r"C:\Users\walli\octodamus")
CREDITS_FILE = PROJECT_DIR / "data" / "botcoin_credits.json"

WALLET   = "0x7d372b930b42d4adc7c82f9d5bcb692da3597570"
TOKEN    = "0xA601877977340862Ca67f816eb079958E5bd0BA3"
COORD    = "https://coordinator.agentmoney.net"

# Cost estimates (Sonnet 4.6: $3/MTok in, $15/MTok out)
EST_TOKENS_IN  = 3000
EST_TOKENS_OUT = 2500
COST_PER_SOLVE = (EST_TOKENS_IN * 3 / 1_000_000) + (EST_TOKENS_OUT * 15 / 1_000_000)


def _get_epoch_info() -> dict:
    """Query coordinator for current epoch."""
    try:
        import requests
        auth = json.loads((PROJECT_DIR / "data" / "botcoin_auth.json").read_text(encoding="utf-8"))
        token = auth.get("token", "")
        headers = {"Authorization": f"Bearer {token}"}
        for ep in ["/v1/epoch", "/api/epoch", "/api/v1/epoch"]:
            r = requests.get(f"{COORD}{ep}", headers=headers, timeout=8)
            if r.status_code == 200:
                return r.json()
    except Exception:
        pass
    return {}


def _get_wallet_balance() -> float:
    """Get wallet BOTCOIN balance from Blockscout v1."""
    try:
        import requests
        r = requests.get("https://base.blockscout.com/api", params={
            "module": "account", "action": "tokenbalance",
            "contractaddress": TOKEN, "address": WALLET
        }, timeout=15)
        raw = int(r.json().get("result", 0))
        return raw / 1e18
    except Exception:
        return 0.0


def _get_staked_balance() -> float:
    """Estimate staked = total mined - wallet balance."""
    try:
        import requests
        total = 0.0
        startblock = 0
        while True:
            r = requests.get("https://base.blockscout.com/api", params={
                "module": "account", "action": "tokentx",
                "contractaddress": TOKEN, "address": WALLET,
                "startblock": startblock, "endblock": 99999999,
                "sort": "asc", "offset": 100, "page": 1
            }, timeout=15)
            items = r.json().get("result", [])
            if not items or not isinstance(items, list):
                break
            for tx in items:
                if tx.get("to", "").lower() == WALLET.lower():
                    total += int(tx.get("value", 0)) / 1e18
            if len(items) < 100:
                break
            startblock = int(items[-1]["blockNumber"]) + 1
            time.sleep(0.3)
        return total
    except Exception:
        return 0.0


def _get_price() -> float:
    """Get BOTCOIN price from DexScreener."""
    try:
        import requests
        r = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{TOKEN}", timeout=10)
        pairs = r.json().get("pairs", [])
        if pairs:
            return float(pairs[0].get("priceUsd", 0))
    except Exception:
        pass
    return 0.00000488  # fallback


def _miner_is_running() -> bool:
    try:
        import subprocess
        r = subprocess.run(
            ["powershell", "-Command",
             "Get-WmiObject Win32_Process -Filter \"Name='python.exe'\" | "
             "ForEach-Object { $_.CommandLine } | "
             "Where-Object { $_ -like '*botcoin*' }"],
            capture_output=True, text=True, timeout=20
        )
        return "octo_boto_botcoin" in r.stdout.lower()
    except Exception:
        return False


def build_report(context: str = "manual") -> str:
    now_utc = datetime.now(timezone.utc)
    ts_str  = now_utc.strftime("%Y-%m-%d %H:%M UTC")
    label   = "MORNING" if context == "morning" else "EVENING" if context == "evening" else "REPORT"

    # Credits log
    credits_data = {}
    try:
        credits_data = json.loads(CREDITS_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass

    epochs        = sorted(credits_data.keys(), key=lambda x: int(x))
    total_solves  = sum(e.get("solves", 0)  for e in credits_data.values())
    total_passes  = sum(e.get("passes", 0)  for e in credits_data.values())
    total_credits = sum(e.get("credits", 0) for e in credits_data.values())
    total_cost    = total_solves * COST_PER_SOLVE

    latest_epoch   = epochs[-1] if epochs else "?"
    latest         = credits_data.get(latest_epoch, {})
    epoch_solves   = latest.get("solves", 0)
    epoch_passes   = latest.get("passes", 0)
    epoch_credits  = latest.get("credits", 0)
    epoch_cost     = epoch_solves * COST_PER_SOLVE

    # Coordinator epoch info
    ep_info = _get_epoch_info()
    coord_epoch = ep_info.get("epoch", ep_info.get("current_epoch", "?"))
    coord_ends  = ep_info.get("ends_at", ep_info.get("epoch_end", ""))

    # On-chain balances
    wallet_bal = _get_wallet_balance()
    total_mined = wallet_bal  # will be overridden below
    try:
        import requests, time as _time
        total_in = 0.0
        startblock = 0
        while True:
            r = requests.get("https://base.blockscout.com/api", params={
                "module": "account", "action": "tokentx",
                "contractaddress": TOKEN, "address": WALLET,
                "startblock": startblock, "endblock": 99999999,
                "sort": "asc", "offset": 100, "page": 1
            }, timeout=15)
            items = r.json().get("result", [])
            if not items or not isinstance(items, list):
                break
            for tx in items:
                if tx.get("to", "").lower() == WALLET.lower():
                    total_in += int(tx.get("value", 0)) / 1e18
            if len(items) < 100:
                break
            startblock = int(items[-1]["blockNumber"]) + 1
            _time.sleep(0.3)
        total_mined = total_in
        staked = max(0, total_mined - wallet_bal)
    except Exception:
        staked = 0.0

    price     = _get_price()
    usd_value = total_mined * price
    running   = _miner_is_running()

    lines = [
        f"Octodamus BOTCOIN Mining Report -- {label}",
        f"{ts_str}",
        f"{'=' * 48}",
        f"",
        f"--- Miner Status ---",
        f"  Process:        {'RUNNING' if running else 'STOPPED'}",
        f"  Current epoch:  {coord_epoch}" + (f" (ends {coord_ends})" if coord_ends else ""),
        f"  Tracked epochs: {', '.join(epochs) if epochs else 'none'}",
        f"",
        f"--- Epoch {latest_epoch} (Latest Tracked) ---",
        f"  Solves:   {epoch_solves}",
        f"  Passes:   {epoch_passes}",
        f"  Credits:  {epoch_credits:,}",
        f"  ~Cost:    ~${epoch_cost:.4f} (est. ${COST_PER_SOLVE:.4f}/solve)",
        f"",
        f"--- All-Time Totals ---",
        f"  Total solves:   {total_solves}",
        f"  Total passes:   {total_passes}",
        f"  Total credits:  {total_credits:,}",
        f"  ~Total cost:    ~${total_cost:.4f}",
        f"",
        f"--- BOTCOIN Balances ---",
        f"  Total mined:    {total_mined:>20,.0f} BOTCOIN",
        f"  In wallet:      {wallet_bal:>20,.0f} BOTCOIN",
        f"  Staked:         {staked:>20,.0f} BOTCOIN",
        f"  Price:          ${price:.8f}",
        f"  USD value:      ${usd_value:,.2f} (all mined)",
        f"",
        f"{'=' * 48}",
        f"Dashboard: http://localhost:8901",
        f"Credits log: {CREDITS_FILE}",
    ]

    return "\n".join(lines)


def send_report(context: str = "manual"):
    label   = {"morning": "Morning", "evening": "Evening"}.get(context, "")
    now     = datetime.now().strftime("%b %d")
    subject = f"[Octodamus] BOTCOIN Mining {label} Report -- {now}"

    body = build_report(context)
    print(body)
    send_email_alert(subject=subject, body=body)


if __name__ == "__main__":
    context = sys.argv[1] if len(sys.argv) > 1 else "manual"
    send_report(context)

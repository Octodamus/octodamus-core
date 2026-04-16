"""
octo_govcontracts.py
OctoGovContracts -- Pentagon & Government Contract Intelligence

Tracks significant US government contracts awarded to public companies.
Source: Quiver Quantitative gov_contracts endpoint (Hobbyist tier, $10/mo).

Functions:
    run_govcontracts_scan()        -- top contracts this week
    format_govcontracts_for_prompt(data) -- Claude prompt context
    get_top_contract_for_post(data)     -- single best contract for X post

API: quiverquant.com -- QUIVER_API_KEY in .octo_secrets
Bitwarden: AGENT - Octodamus - Quiver API
"""

import os
import re
from datetime import datetime, timedelta

MIN_CONTRACT_AMOUNT = 10_000_000    # $10M floor (Quiver data lags 1-2 weeks)
POST_THRESHOLD      = 50_000_000    # $50M+ for standalone X post
DAYS_BACK           = 30            # 30-day window to account for data lag

# Map common agency names to shorter labels
AGENCY_SHORT = {
    "Department of Defense":        "Pentagon",
    "Dept. of Defense":             "Pentagon",
    "Defense Logistics Agency":     "DLA",
    "Department of the Army":       "Army",
    "Department of the Navy":       "Navy",
    "Department of the Air Force":  "Air Force",
    "Space Force":                  "Space Force",
    "General Services Administration": "GSA",
    "Department of Energy":         "DOE",
    "Department of Homeland Security": "DHS",
    "NASA":                         "NASA",
}


def _shorten_agency(name: str) -> str:
    for full, short in AGENCY_SHORT.items():
        if full.lower() in name.lower():
            return short
    # Truncate long names
    return name[:30] if len(name) > 30 else name


def _format_amount(amount: float) -> str:
    if amount >= 1_000_000_000:
        return f"${amount/1_000_000_000:.2f}B"
    if amount >= 1_000_000:
        return f"${amount/1_000_000:.0f}M"
    return f"${amount:,.0f}"


def run_govcontracts_scan(days_back: int = DAYS_BACK) -> dict:
    """
    Scan recent government contracts. Returns top contracts by amount
    for use in X posts and prompt injection.
    """
    token = os.environ.get("QUIVER_API_KEY", "")
    if not token:
        return {"error": "QUIVER_API_KEY not set", "contracts": [], "signals": []}

    import requests
    headers = {"accept": "application/json", "Authorization": f"Token {token}"}
    cutoff  = datetime.now() - timedelta(days=days_back)

    print(f"[GovContracts] Scanning contracts (last {days_back} days, >=${MIN_CONTRACT_AMOUNT/1e6:.0f}M)...")

    try:
        r = requests.get("https://api.quiverquant.com/beta/live/govcontractsall",
                         headers=headers, timeout=20)
        raw = r.json()
        if isinstance(raw, list) and len(raw) == 1 and isinstance(raw[0], dict) and "detail" in raw[0]:
            return {"error": raw[0]["detail"], "contracts": [], "signals": []}
    except Exception as e:
        return {"error": str(e), "contracts": [], "signals": []}

    contracts = []
    seen = set()

    for row in raw:
        if not isinstance(row, dict):
            continue
        try:
            amount = float(row.get("Amount", 0) or 0)
            if amount < MIN_CONTRACT_AMOUNT:
                continue

            date_str = str(row.get("Date", row.get("action_date", "")))[:10]
            try:
                row_dt = datetime.strptime(date_str, "%Y-%m-%d")
            except Exception:
                continue
            if row_dt < cutoff:
                continue

            ticker  = str(row.get("Ticker", "")).strip().upper()
            agency  = str(row.get("Agency", "")).strip()
            desc    = str(row.get("Description", "")).strip()

            # Deduplicate (same ticker + amount + date)
            key = f"{ticker}_{amount}_{date_str}"
            if key in seen:
                continue
            seen.add(key)

            contracts.append({
                "ticker":  ticker,
                "amount":  amount,
                "amount_fmt": _format_amount(amount),
                "agency":  agency,
                "agency_short": _shorten_agency(agency),
                "description": desc[:120],
                "date":    date_str,
            })
        except Exception:
            continue

    contracts.sort(key=lambda x: x["amount"], reverse=True)

    # Top tickers by total contract value this period
    ticker_totals: dict = {}
    for c in contracts:
        ticker_totals[c["ticker"]] = ticker_totals.get(c["ticker"], 0) + c["amount"]
    top_tickers = sorted(ticker_totals.items(), key=lambda x: x[1], reverse=True)[:5]

    # Signals for post
    signals = []
    for c in contracts[:10]:
        signals.append({
            "text": f"{c['ticker']} -- {c['amount_fmt']} {c['agency_short']} contract -- {c['date']}",
            "ticker":      c["ticker"],
            "amount":      c["amount"],
            "amount_fmt":  c["amount_fmt"],
            "agency":      c["agency"],
            "agency_short": c["agency_short"],
            "description": c["description"],
            "date":        c["date"],
        })

    print(f"[GovContracts] Found {len(contracts)} contracts >= ${MIN_CONTRACT_AMOUNT/1e6:.0f}M "
          f"| top: {top_tickers[:3]}")

    return {
        "error":       None,
        "contracts":   contracts,
        "signals":     signals,
        "total":       len(contracts),
        "top_tickers": top_tickers,
        "days_back":   days_back,
        "scanned_at":  datetime.now().isoformat(),
    }


def get_top_contract_for_post(data: dict) -> dict | None:
    """Return the single most post-worthy contract (largest >= POST_THRESHOLD)."""
    if data.get("error") or not data.get("contracts"):
        return None
    for c in data["contracts"]:
        if c["amount"] >= POST_THRESHOLD:
            return c
    return None


def format_govcontracts_for_prompt(data: dict) -> str:
    """Format for injection into Claude prompt."""
    if data.get("error"):
        return f"[GovContracts] Unavailable: {data['error']}"
    if not data.get("contracts"):
        return f"[GovContracts] No significant contracts (>=${MIN_CONTRACT_AMOUNT/1e6:.0f}M) in last {data.get('days_back', DAYS_BACK)} days."

    lines = [f"Government contracts (last {data.get('days_back', DAYS_BACK)} days, >=${MIN_CONTRACT_AMOUNT/1e6:.0f}M):"]
    lines.append(f"Total: {data['total']} contracts")

    if data.get("top_tickers"):
        tops = ", ".join(f"{t[0]} ({_format_amount(t[1])})" for t in data["top_tickers"][:3])
        lines.append(f"Top recipients: {tops}")

    lines.append("Notable contracts:")
    for s in data["signals"][:6]:
        lines.append(f"  {s['text']}")
        if s.get("description"):
            lines.append(f"    {s['description'][:80]}")

    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
    from octo_secrets import load_secrets
    load_secrets()

    data = run_govcontracts_scan(days_back=7)
    if data.get("error"):
        print(f"Error: {data['error']}")
    else:
        print(f"\nTotal contracts: {data['total']}")
        print(f"Top tickers: {data['top_tickers']}")
        print("\nTop contracts:")
        for c in data["contracts"][:8]:
            print(f"  {c['ticker']:6s} {c['amount_fmt']:>8s}  {c['agency_short'][:25]:25s}  {c['date']}")
            print(f"         {c['description'][:80]}")
        print("\nPrompt context:")
        print(format_govcontracts_for_prompt(data))
        top = get_top_contract_for_post(data)
        if top:
            print(f"\nTop post candidate: {top['ticker']} {top['amount_fmt']} -- {top['agency_short']}")

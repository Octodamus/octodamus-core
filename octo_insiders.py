"""
octo_insiders.py
OctoInsiders -- Corporate Insider Trading Intelligence

Tracks significant open-market purchases by corporate insiders (directors,
officers, 10% owners) via Quiver Quantitative insiders endpoint.

STATUS: MODULE READY -- requires Quiver Premium tier (~$50/mo)
Activate at $500/mo revenue milestone (same as Unusual Whales).
Bitwarden: AGENT - Octodamus - Quiver API

Functions:
    run_insiders_scan()             -- top insider buys this period
    format_insiders_for_prompt(data) -- Claude prompt context
    get_top_insider_for_post(data)  -- single best buy for X post
"""

import os
from datetime import datetime, timedelta

MIN_TRADE_VALUE = 250_000   # $250K floor for signal posts
DAYS_BACK       = 14


def run_insiders_scan(days_back: int = DAYS_BACK) -> dict:
    """
    Scan recent significant insider purchases.
    Filters: open-market buys (TransactionCode='P'), value >= $250K, last N days.

    Requires Quiver Premium tier -- returns error on Hobbyist plan.
    """
    token = os.environ.get("QUIVER_API_KEY", "")
    if not token:
        return {"error": "QUIVER_API_KEY not set", "trades": [], "signals": []}

    import requests
    headers = {"accept": "application/json", "Authorization": f"Token {token}"}
    cutoff  = datetime.now() - timedelta(days=days_back)

    print(f"[Insiders] Scanning insider purchases (last {days_back} days, >=${MIN_TRADE_VALUE/1e3:.0f}K)...")

    try:
        r = requests.get("https://api.quiverquant.com/beta/live/insiders",
                         headers=headers, timeout=20)
        raw = r.json()
        # Check for upgrade message (Hobbyist plan limitation)
        if isinstance(raw, list) and len(raw) == 1 and isinstance(raw[0], dict) and "detail" in raw[0]:
            return {"error": f"Quiver Premium required: {raw[0]['detail']}", "trades": [], "signals": []}
        if isinstance(raw, dict) and "detail" in raw:
            return {"error": f"Quiver Premium required: {raw['detail']}", "trades": [], "signals": []}
    except Exception as e:
        return {"error": str(e), "trades": [], "signals": []}

    trades = []
    seen   = set()

    for row in raw:
        if not isinstance(row, dict):
            continue
        try:
            # Only open-market purchases with known price
            if row.get("TransactionCode") != "P":
                continue
            if row.get("AcquiredDisposedCode") != "A":
                continue

            price  = float(row.get("PricePerShare") or 0)
            shares = float(row.get("Shares") or 0)
            if price <= 0 or shares <= 0:
                continue

            value = price * shares
            if value < MIN_TRADE_VALUE:
                continue

            # Date filter
            date_str = str(row.get("Date", ""))[:10]
            try:
                tx_dt = datetime.strptime(date_str, "%Y-%m-%d")
            except Exception:
                continue
            if tx_dt < cutoff:
                continue

            ticker = str(row.get("Ticker", "")).strip().upper()
            name   = str(row.get("Name", "Unknown")).strip().title()
            title  = str(row.get("officerTitle") or "").strip()
            is_dir = bool(row.get("isDirector"))
            is_off = bool(row.get("isOfficer"))
            is_10p = bool(row.get("isTenPercentOwner"))

            role = title or ("Director" if is_dir else "Officer" if is_off else "10% Owner" if is_10p else "Insider")

            key = f"{ticker}_{name}_{date_str}_{shares}"
            if key in seen:
                continue
            seen.add(key)

            trades.append({
                "ticker":  ticker,
                "name":    name,
                "role":    role,
                "shares":  int(shares),
                "price":   price,
                "value":   value,
                "value_fmt": _format_value(value),
                "date":    date_str,
            })
        except Exception:
            continue

    trades.sort(key=lambda x: x["value"], reverse=True)

    signals = []
    for t in trades[:10]:
        last_name = t["name"].split()[-1]
        signals.append({
            "text": f"{t['ticker']} -- {last_name} ({t['role']}) bought {t['shares']:,} shares @ ${t['price']:.2f} = {t['value_fmt']} -- {t['date']}",
            **t,
        })

    print(f"[Insiders] Found {len(trades)} insider buys >= ${MIN_TRADE_VALUE/1e3:.0f}K")

    return {
        "error":     None,
        "trades":    trades,
        "signals":   signals,
        "total":     len(trades),
        "days_back": days_back,
        "scanned_at": datetime.now().isoformat(),
    }


def _format_value(v: float) -> str:
    if v >= 1_000_000:
        return f"${v/1_000_000:.1f}M"
    return f"${v/1_000:.0f}K"


def get_top_insider_for_post(data: dict) -> dict | None:
    if data.get("error") or not data.get("trades"):
        return None
    return data["trades"][0]


def format_insiders_for_prompt(data: dict) -> str:
    if data.get("error"):
        return f"[Insiders] Unavailable: {data['error']}"
    if not data.get("trades"):
        return f"[Insiders] No significant insider purchases (>=${MIN_TRADE_VALUE/1e3:.0f}K) in last {data.get('days_back', DAYS_BACK)} days."

    lines = [f"Corporate insider purchases (last {data.get('days_back', DAYS_BACK)} days, >=${MIN_TRADE_VALUE/1e3:.0f}K open-market buys):"]
    lines.append(f"Total: {data['total']} purchases")
    lines.append("Notable:")
    for s in data["signals"][:6]:
        lines.append(f"  {s['text']}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
    from octo_secrets import load_secrets
    load_secrets()

    data = run_insiders_scan(days_back=14)
    if data.get("error"):
        print(f"Status: {data['error']}")
        print("Upgrade to Quiver Premium (~$50/mo) at $500/mo revenue milestone.")
    else:
        print(format_insiders_for_prompt(data))

"""
octo_congress.py
OctoCongress -- Political Trading Signal Mind

Tracks stock trades made by U.S. Congress members via Quiver Quantitative.
Politicians must disclose trades within 45 days under the STOCK Act.

API: quiverquant.com -- Hobbyist tier $10/month
Bitwarden: AGENT - Octodamus - Quiver API

Usage:
    from octo_congress import run_congress_scan, format_congress_for_prompt
    data = run_congress_scan()
"""

import os
from datetime import datetime, timedelta

WATCH_TICKERS = ["NVDA", "TSLA", "AAPL", "MSFT", "AMZN", "META", "GOOGL"]
MIN_TRADE_SIZE = 15000
DAYS_BACK = 30


def _get_client():
    import quiverquant
    token = os.environ.get("QUIVER_API_KEY", "")
    if not token:
        raise ValueError("QUIVER_API_KEY not set")
    return quiverquant.quiver(token)


def _parse_amount(range_str: str) -> int:
    """Parse trade amount from range string like '$15,001 - $50,000'"""
    try:
        clean = str(range_str).replace("$", "").replace(",", "").strip()
        if " - " in clean:
            return int(clean.split(" - ")[0].strip())
        val = clean.replace(">", "").replace("<", "").strip()
        if val.replace(".", "").isdigit():
            return int(float(val))
    except Exception:
        pass
    return 0


def run_congress_scan(days_back: int = DAYS_BACK) -> dict:
    """
    Scan recent congressional trades for watchlist tickers.
    Returns structured data for signal posts and Telegram.
    """
    token = os.environ.get("QUIVER_API_KEY", "")
    if not token:
        return {"error": "QUIVER_API_KEY not set", "trades": [], "signals": []}

    print(f"[OctoCongress] Scanning congressional trades (last {days_back} days)...")

    cutoff = datetime.now() - timedelta(days=days_back)
    all_trades = []

    try:
        quiver = _get_client()
    except Exception as e:
        return {"error": str(e), "trades": [], "signals": []}

    for ticker in WATCH_TICKERS:
        try:
            df = quiver.congress_trading(ticker)
            if df is None or df.empty:
                continue

            for _, row in df.iterrows():
                try:
                    tx_date_raw = row.get("TransactionDate", row.get("ReportDate", ""))
                    if hasattr(tx_date_raw, "strftime"):
                        tx_dt = tx_date_raw.to_pydatetime() if hasattr(tx_date_raw, "to_pydatetime") else tx_date_raw
                    else:
                        tx_dt = datetime.strptime(str(tx_date_raw)[:10], "%Y-%m-%d")

                    if tx_dt < cutoff:
                        continue

                    tx_type = str(row.get("Transaction", "")).lower()
                    direction = "BUY" if "purchase" in tx_type or "buy" in tx_type else "SELL"
                    amount_raw = row.get("Range", row.get("Amount", ""))
                    amount_low = _parse_amount(str(amount_raw))

                    if amount_low < MIN_TRADE_SIZE:
                        continue

                    all_trades.append({
                        "ticker": ticker,
                        "politician": str(row.get("Representative", "Unknown")),
                        "party": str(row.get("Party", "")),
                        "chamber": str(row.get("Chamber", "")),
                        "direction": direction,
                        "amount_low": amount_low,
                        "amount_str": str(amount_raw),
                        "date": str(tx_date_raw)[:10],
                        "excess_return": float(row.get("ExcessReturn", 0) or 0),
                    })
                except Exception:
                    continue

        except Exception as e:
            print(f"[OctoCongress] Error fetching {ticker}: {e}")
            continue

    # Deduplicate
    seen = set()
    unique = []
    for t in all_trades:
        key = f"{t['politician']}_{t['ticker']}_{t['date']}_{t['direction']}"
        if key not in seen:
            seen.add(key)
            unique.append(t)

    # Sort by amount
    unique.sort(key=lambda x: x["amount_low"], reverse=True)

    buys  = [t for t in unique if t["direction"] == "BUY"]
    sells = [t for t in unique if t["direction"] == "SELL"]

    # Top bought tickers
    buy_count = {}
    for t in buys:
        buy_count[t["ticker"]] = buy_count.get(t["ticker"], 0) + 1
    top_buys = sorted(buy_count.items(), key=lambda x: x[1], reverse=True)[:3]

    # Build signals
    signals = []
    for t in unique[:10]:
        name  = t["politician"].split()[-1]
        party = f"({t['party'][0]})" if t["party"] else ""
        amt   = t["amount_str"] or f"${t['amount_low']:,}+"
        signals.append({
            "text": f"{name} {party} {t['direction']} {t['ticker']} — {amt} — {t['date']}",
            "ticker": t["ticker"],
            "politician": t["politician"],
            "direction": t["direction"],
            "amount": t["amount_low"],
            "date": t["date"],
            "party": t["party"],
        })

    print(f"[OctoCongress] Found {len(unique)} trades — {len(buys)} buys, {len(sells)} sells")

    return {
        "error": None,
        "trades": unique,
        "signals": signals,
        "total": len(unique),
        "buys": len(buys),
        "sells": len(sells),
        "top_bought": top_buys,
        "days_back": days_back,
        "scanned_at": datetime.now().isoformat(),
    }


def format_congress_for_prompt(data: dict) -> str:
    """Format for injection into Claude prompt."""
    if data.get("error"):
        return f"[OctoCongress] Unavailable: {data['error']}"

    lines = [f"Congressional trades (last {data.get('days_back', DAYS_BACK)} days):"]
    lines.append(f"Total: {data['total']} trades — {data['buys']} buys, {data['sells']} sells")

    if data.get("top_bought"):
        tops = ", ".join(f"{t[0]} ({t[1]}x)" for t in data["top_bought"])
        lines.append(f"Most bought: {tops}")

    if data.get("signals"):
        lines.append("Notable trades:")
        for s in data["signals"][:6]:
            lines.append(f"  {s['text']}")

    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
    import bitwarden
    bitwarden.load_all_secrets()

    data = run_congress_scan(days_back=30)
    if data.get("error"):
        print(f"Error: {data['error']}")
    else:
        print(f"\nTotal: {data['total']} | Buys: {data['buys']} | Sells: {data['sells']}")
        if data["top_bought"]:
            print(f"Top bought: {data['top_bought']}")
        print("\nSignals:")
        for s in data["signals"][:8]:
            print(f"  {s['text']}")
        print("\nPrompt context:")
        print(format_congress_for_prompt(data))

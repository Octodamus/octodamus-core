"""
octo_cot.py — CFTC Commitment of Traders Module

Reads institutional positioning from the CFTC's weekly COT report.
Free, published every Friday covering positions as of the prior Tuesday.

Data source: CFTC Traders in Financial Futures (TFF) weekly file
URL: https://www.cftc.gov/dea/newcot/FinFutWk.txt

Key signals:
- Leveraged Funds (hedge funds) net position — CONTRARIAN indicator
  When hedge funds are max short, they eventually cover = short squeeze = rally.
  When max long, they unwind = sell-off.
- Asset Managers (institutions) net position — TREND indicator
  Big money building longs = buy signal. Reducing = caution.
- Net position divergence — Hedge funds short + Asset managers long = bullish setup.

The data is 3 days old by Friday publication, but institutional positioning
doesn't reverse overnight. One week of lag is fine for directional calls.

CLI:
    python octo_cot.py btc
    python octo_cot.py eth
"""

import csv
import io
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger("OctoCot")

COT_URL = "https://www.cftc.gov/dea/newcot/FinFutWk.txt"

# Cache: report is weekly, cache for 6 hours (re-download after stale)
_CACHE_FILE = Path(__file__).parent / "data" / "cot_cache.json"
CACHE_TTL = 6 * 3600  # 6 hours

# Market name fragments to match (case-insensitive)
_MARKET_KEYS = {
    "BTC": "BITCOIN - CHICAGO MERCANTILE EXCHANGE",
    "ETH": "ETHER CASH SETTLED - CHICAGO MERCANTILE EXCHANGE",
}

# TFF column indices (0-based)
# Source: CFTC TFF format documentation
_COL = {
    "market":           0,
    "report_date":      2,   # YYYY-MM-DD
    "open_interest":    7,
    # Dealer intermediary
    "dealer_long":      8,
    "dealer_short":     9,
    # Asset managers (pension funds, endowments, sovereign wealth)
    "am_long":          11,
    "am_short":         12,
    # Leveraged funds (hedge funds, CTAs)
    "lf_long":          14,
    "lf_short":         15,
    "lf_spread":        16,
    # Other reportable
    "other_long":       17,
    "other_short":      18,
    # Non-reportable = retail traders
    "retail_long":      22,
    "retail_short":     23,
    # Week-over-week changes
    "chg_lf_long":      30,
    "chg_lf_short":     31,
    "chg_am_long":      27,
    "chg_am_short":     28,
    # Percentages of OI (dealer/AM/LF/other/retail)
    "pct_lf_long":      48,
    "pct_lf_short":     49,
    "pct_am_long":      45,
    "pct_am_short":     46,
}


# ── Fetch ─────────────────────────────────────────────────────────────────────

def _load_cache() -> Optional[dict]:
    try:
        if _CACHE_FILE.exists():
            import json
            d = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
            if time.time() - d.get("ts", 0) < CACHE_TTL:
                return d
    except Exception:
        pass
    return None


def _save_cache(data: dict):
    import json
    _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_FILE.write_text(json.dumps(data), encoding="utf-8")


def _fetch_raw() -> str:
    """Download the weekly TFF flat file. Returns raw text."""
    import httpx
    r = httpx.get(COT_URL, timeout=20, follow_redirects=True)
    r.raise_for_status()
    return r.text


def _parse_row(row: list) -> dict:
    """Parse a single CSV row into a structured dict."""
    def _f(col: str) -> float:
        try:
            v = row[_COL[col]].strip().replace(",", "")
            return float(v) if v not in (".", "") else 0.0
        except (IndexError, ValueError):
            return 0.0

    oi = _f("open_interest")
    lf_long = _f("lf_long")
    lf_short = _f("lf_short")
    am_long = _f("am_long")
    am_short = _f("am_short")
    retail_long = _f("retail_long")
    retail_short = _f("retail_short")

    lf_net = lf_long - lf_short
    am_net = am_long - am_short
    retail_net = retail_long - retail_short

    # Net as % of OI
    lf_net_pct = round(lf_net / oi * 100, 1) if oi else 0
    am_net_pct = round(am_net / oi * 100, 1) if oi else 0

    # Week-over-week changes
    chg_lf_net = _f("chg_lf_long") - _f("chg_lf_short")
    chg_am_net = _f("chg_am_long") - _f("chg_am_short")

    return {
        "market": row[_COL["market"]].strip().strip('"'),
        "report_date": row[_COL["report_date"]].strip(),
        "open_interest": int(oi),
        "lf_long": int(lf_long),
        "lf_short": int(lf_short),
        "lf_net": int(lf_net),
        "lf_net_pct": lf_net_pct,
        "chg_lf_net": int(chg_lf_net),
        "am_long": int(am_long),
        "am_short": int(am_short),
        "am_net": int(am_net),
        "am_net_pct": am_net_pct,
        "chg_am_net": int(chg_am_net),
        "retail_long": int(retail_long),
        "retail_short": int(retail_short),
        "retail_net": int(retail_net),
    }


def get_cot_data(currency: str = "BTC") -> Optional[dict]:
    """
    Fetch and parse the latest COT report for a currency.
    Returns structured positioning data, or None on failure.
    """
    cache = _load_cache()
    cache_key = currency.upper()

    if cache and cache_key in cache.get("data", {}):
        return cache["data"][cache_key]

    try:
        raw = _fetch_raw()
    except Exception as e:
        log.error(f"COT fetch failed: {e}")
        return None

    target = _MARKET_KEYS.get(currency.upper())
    if not target:
        log.warning(f"No COT market key for {currency}")
        return None

    reader = csv.reader(io.StringIO(raw))
    result = {}
    for row in reader:
        if not row:
            continue
        market_name = row[0].strip().strip('"').upper()
        for sym, key in _MARKET_KEYS.items():
            if market_name == key.upper():
                result[sym] = _parse_row(row)

    if result:
        existing_cache = {"ts": time.time(), "data": result}
        _save_cache(existing_cache)

    return result.get(currency.upper())


# ── Signal Generation ─────────────────────────────────────────────────────────

def _positioning_signal(data: dict) -> str:
    """
    Derive a directional signal from COT positioning.

    Leveraged funds (hedge funds) are CONTRARIAN — when they pile into shorts,
    they tend to be wrong at extremes. Asset managers are TREND-following.

    Signal logic:
    - LF max short + AM building longs = STRONG BULL (squeeze setup)
    - LF max short + AM also short = BEAR (institutional alignment)
    - LF max long + AM long = STRONG BULL (institutional agreement)
    - LF max long + AM cutting = CAUTION (top signal)
    """
    lf_net_pct = data["lf_net_pct"]
    am_net_pct = data["am_net_pct"]
    chg_lf = data["chg_lf_net"]
    chg_am = data["chg_am_net"]

    parts = []

    # Leveraged fund read
    if lf_net_pct < -15:
        parts.append("HEDGE FUNDS EXTREME SHORT (contrarian BULL signal)")
    elif lf_net_pct < -5:
        parts.append("Hedge funds net short (mild contrarian bullish)")
    elif lf_net_pct > 15:
        parts.append("HEDGE FUNDS EXTREME LONG (contrarian BEAR signal)")
    elif lf_net_pct > 5:
        parts.append("Hedge funds net long (mild contrarian bearish)")
    else:
        parts.append("Hedge funds neutral")

    # Asset manager read
    if am_net_pct > 10:
        parts.append("Asset managers net long (institutional trend = BULL)")
    elif am_net_pct < -10:
        parts.append("Asset managers net short (institutional trend = BEAR)")
    else:
        parts.append("Asset managers near neutral")

    # Week-over-week flow
    if chg_lf < -500:
        parts.append(f"Hedge funds added {abs(chg_lf):,} shorts this week (bearish flow)")
    elif chg_lf > 500:
        parts.append(f"Hedge funds covered {chg_lf:,} net longs this week (covering shorts)")
    if chg_am > 300:
        parts.append(f"Asset managers added {chg_am:,} net longs (institutional accumulation)")
    elif chg_am < -300:
        parts.append(f"Asset managers cut {abs(chg_am):,} net longs (institutional distribution)")

    # Overall signal
    if lf_net_pct < -10 and am_net_pct > 0:
        overall = "SETUP: Hedge funds short, institutions long = CLASSIC SQUEEZE SETUP"
    elif lf_net_pct < -10 and am_net_pct < -5:
        overall = "SETUP: Both hedge funds and institutions short = BEARISH ALIGNMENT"
    elif lf_net_pct > 10 and am_net_pct > 5:
        overall = "SETUP: Both hedge funds and institutions long = BULLISH ALIGNMENT"
    elif lf_net_pct > 10 and am_net_pct < 0:
        overall = "SETUP: Hedge funds long, institutions exiting = DISTRIBUTION RISK"
    else:
        overall = "SETUP: No extreme positioning — mixed signals"

    parts.append(overall)
    return " | ".join(parts)


# ── Oracle Context ────────────────────────────────────────────────────────────

def build_oracle_context(currency: str = "BTC") -> str:
    """
    Formatted COT intelligence for Claude prompts.
    COT is a weekly signal — most useful for 48h-7d directional calls.
    """
    data = get_cot_data(currency)
    if not data:
        return f"[COT] Data unavailable for {currency}"

    oi = data["open_interest"]
    contract_size = 5 if currency == "BTC" else 50  # BTC = 5 BTC/contract, ETH = 50 ETH

    lines = [f"=== CME COT POSITIONING: {currency} (week of {data['report_date']}) ===\n"]
    lines.append(f"Total Open Interest: {oi:,} contracts ({oi * contract_size:,} {currency})\n")

    lines.append("LEVERAGED FUNDS (hedge funds / CTAs):")
    lines.append(f"  Long:  {data['lf_long']:,} | Short: {data['lf_short']:,}")
    lines.append(f"  Net:   {data['lf_net']:+,} contracts ({data['lf_net_pct']:+.1f}% of OI)")
    chg = data['chg_lf_net']
    lines.append(f"  Week change: {chg:+,} contracts")
    lines.append("")

    lines.append("ASSET MANAGERS (pension funds, ETFs, institutions):")
    lines.append(f"  Long:  {data['am_long']:,} | Short: {data['am_short']:,}")
    lines.append(f"  Net:   {data['am_net']:+,} contracts ({data['am_net_pct']:+.1f}% of OI)")
    chg_am = data['chg_am_net']
    lines.append(f"  Week change: {chg_am:+,} contracts")
    lines.append("")

    lines.append("RETAIL (non-reportable):")
    lines.append(f"  Long:  {data['retail_long']:,} | Short: {data['retail_short']:,}")
    lines.append(f"  Net:   {data['retail_net']:+,} contracts")
    lines.append("")

    signal = _positioning_signal(data)
    lines.append(f"SIGNAL: {signal}")
    lines.append("")
    lines.append(
        "NOTE: COT is a weekly signal — use for 48h+ calls, not intraday. "
        "Extreme hedge fund short positioning is historically the strongest "
        "contrarian indicator in BTC. When leveraged funds are max short, "
        "price often squeezes higher to force covering."
    )

    return "\n".join(lines)


# ── Singleton ─────────────────────────────────────────────────────────────────

class _Cot:
    def build_oracle_context(self, currency: str = "BTC") -> str:
        return build_oracle_context(currency)

    def get_cot_data(self, currency: str = "BTC") -> Optional[dict]:
        return get_cot_data(currency)


cot = _Cot()


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    currency = sys.argv[1].upper() if len(sys.argv) > 1 else "BTC"
    print(build_oracle_context(currency))

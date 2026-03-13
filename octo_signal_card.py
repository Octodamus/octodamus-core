"""
octo_signal_card.py
Octodamus — Oracle Signal Card Formatter

Generates branded text signal cards for X posts.
Format is distinctive, terminal-style, consistent.

Usage:
    from octo_signal_card import build_signal_card, build_oracle_card
"""

import os
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Los_Angeles")


# ─────────────────────────────────────────────
# PRICE FETCHER
# ─────────────────────────────────────────────

def _get_prices() -> dict:
    """Fetch live BTC, ETH, SOL, NVDA, TSLA prices."""
    prices = {}
    try:
        import httpx
        # Crypto
        r = httpx.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={
                "ids": "bitcoin,ethereum,solana",
                "vs_currencies": "usd",
                "include_24hr_change": "true"
            },
            timeout=6
        )
        if r.status_code == 200:
            data = r.json()
            for coin, key in [("bitcoin","BTC"),("ethereum","ETH"),("solana","SOL")]:
                d = data.get(coin, {})
                prices[key] = {
                    "price": d.get("usd", 0),
                    "change": d.get("usd_24h_change", 0)
                }
    except Exception:
        pass

    try:
        import httpx
        fd_key = os.environ.get("FINANCIAL_DATASETS_API_KEY", "")
        if fd_key:
            for ticker in ["NVDA", "TSLA"]:
                try:
                    r = httpx.get(
                        "https://api.financialdatasets.ai/prices/snapshot/",
                        params={"ticker": ticker},
                        headers={"X-API-KEY": fd_key},
                        timeout=6
                    )
                    if r.status_code == 200:
                        snap = r.json().get("snapshot", {})
                        prices[ticker] = {
                            "price": snap.get("price", 0),
                            "change": snap.get("day_change_percent", 0)
                        }
                except Exception:
                    pass
    except Exception:
        pass

    return prices


def _get_fear_greed() -> str:
    """Fetch Fear & Greed index."""
    try:
        import httpx
        r = httpx.get(
            "https://api.alternative.me/fng/",
            params={"limit": 1},
            timeout=5
        )
        if r.status_code == 200:
            d = r.json()["data"][0]
            return f"{d['value']} — {d['value_classification'].upper()}"
    except Exception:
        pass
    return "N/A"


# ─────────────────────────────────────────────
# CARD FORMATTERS
# ─────────────────────────────────────────────

def _arrow(change: float) -> str:
    return "▲" if change >= 0 else "▼"


def _fmt_price(price: float) -> str:
    if price > 1000:
        return f"${price:,.0f}"
    elif price > 10:
        return f"${price:,.2f}"
    else:
        return f"${price:.4f}"


def build_signal_card(call: str, asset: str = None) -> str:
    """
    Build a branded Oracle Signal Card with live prices.
    call: the oracle's prediction text (1-2 sentences max)
    asset: optional featured asset to highlight
    """
    prices = _get_prices()
    fg = _get_fear_greed()
    now = datetime.now(TZ).strftime("%d %b %Y")

    # Price lines — show featured asset first if specified
    price_lines = []
    ordered = []
    if asset and asset.upper() in prices:
        ordered.append(asset.upper())
    for k in ["BTC", "ETH", "SOL", "NVDA", "TSLA"]:
        if k not in ordered and k in prices:
            ordered.append(k)

    for ticker in ordered[:4]:  # max 4 price lines to fit in 280
        d = prices[ticker]
        arrow = _arrow(d["change"])
        price = _fmt_price(d["price"])
        change = abs(d["change"])
        price_lines.append(f"{ticker} {price} {arrow}{change:.1f}%")

    prices_block = "\n".join(price_lines)
    divider = "━━━━━━━━━━━━━━━━━━━"

    card = f"""◈ OCTODAMUS ORACLE ◈
{divider}
{prices_block}
F&G: {fg}
{divider}
{call}
— @octodamusai"""

    # Trim to 280 if needed
    if len(card) > 280:
        # Shorten to 3 price lines
        prices_block = "\n".join(price_lines[:3])
        card = f"""◈ OCTODAMUS ORACLE ◈
{divider}
{prices_block}
F&G: {fg}
{divider}
{call}
— @octodamusai"""

    if len(card) > 280:
        # Last resort — trim the call
        max_call = 280 - len(card) + len(call) - 3
        call = call[:max_call] + "..."
        card = f"""◈ OCTODAMUS ORACLE ◈
{divider}
{prices_block}
F&G: {fg}
{divider}
{call}
— @octodamusai"""

    return card


def build_oracle_card(call: str, asset: str = None) -> str:
    """Alias for build_signal_card."""
    return build_signal_card(call, asset)


# ─────────────────────────────────────────────
# STANDALONE TEST
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent))
    import bitwarden
    bitwarden.load_all_secrets()

    test_call = "BTC stalling at resistance. Oracle call: $79,000 before $90,000. Watch the weekly close Friday."
    card = build_signal_card(test_call, asset="BTC")
    print(card)
    print(f"\n[{len(card)} chars]")

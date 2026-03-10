"""
octo_spot_prices.py
Free real-time spot prices - no API key required.
- Crypto: CoinGecko public API (free, no key)
- Stocks: Yahoo Finance (free, no key)
Used by Telegram bot for /price command and system prompt context.
"""

import httpx
from datetime import datetime

# ── Crypto via CoinGecko ──────────────────────────────────────────────────────

COINGECKO_IDS = {
    "BTC":  "bitcoin",
    "ETH":  "ethereum",
    "SOL":  "solana",
    "BASE": "base",
}

def get_crypto_price(symbol: str) -> dict:
    """
    Get spot price for a crypto symbol.
    Returns: {"symbol": str, "price": float, "change_24h": float, "display": str, "error": str|None}
    """
    symbol = symbol.upper().replace("-USD", "")
    coin_id = COINGECKO_IDS.get(symbol, symbol.lower())
    try:
        r = httpx.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={
                "ids": coin_id,
                "vs_currencies": "usd",
                "include_24hr_change": "true",
            },
            timeout=8,
            headers={"Accept": "application/json"},
        )
        r.raise_for_status()
        data = r.json()
        if coin_id not in data:
            return {"symbol": symbol, "price": 0, "change_24h": 0, "display": "not found", "error": "symbol not found"}
        price     = data[coin_id]["usd"]
        change    = data[coin_id].get("usd_24h_change", 0) or 0
        arrow     = "▲" if change >= 0 else "▼"
        display   = f"{symbol}: ${price:,.2f} {arrow}{abs(change):.1f}%"
        return {"symbol": symbol, "price": price, "change_24h": change, "display": display, "error": None}
    except Exception as e:
        return {"symbol": symbol, "price": 0, "change_24h": 0, "display": f"{symbol}: unavailable", "error": str(e)}


# ── Stocks via Yahoo Finance ──────────────────────────────────────────────────

def get_stock_price(ticker: str) -> dict:
    """
    Get spot price for a stock ticker via Yahoo Finance.
    Returns: {"symbol": str, "price": float, "change_pct": float, "display": str, "error": str|None}
    """
    ticker = ticker.upper()
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        r = httpx.get(
            url,
            params={"interval": "1m", "range": "1d"},
            timeout=8,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json",
            },
        )
        r.raise_for_status()
        data   = r.json()
        meta   = data["chart"]["result"][0]["meta"]
        price  = meta.get("regularMarketPrice") or meta.get("previousClose", 0)
        prev   = meta.get("previousClose") or price
        change = ((price - prev) / prev * 100) if prev else 0
        arrow  = "▲" if change >= 0 else "▼"
        display = f"{ticker}: ${price:,.2f} {arrow}{abs(change):.1f}%"
        return {"symbol": ticker, "price": price, "change_pct": change, "display": display, "error": None}
    except Exception as e:
        return {"symbol": ticker, "price": 0, "change_pct": 0, "display": f"{ticker}: unavailable", "error": str(e)}


# ── Watchlist snapshot ────────────────────────────────────────────────────────

STOCK_WATCHLIST  = ["NVDA", "TSLA", "AAPL"]
CRYPTO_WATCHLIST = ["BTC", "ETH"]

def get_watchlist_snapshot() -> list:
    """Returns list of price dicts for all watchlist symbols."""
    results = []
    for ticker in STOCK_WATCHLIST:
        results.append(get_stock_price(ticker))
    for symbol in CRYPTO_WATCHLIST:
        results.append(get_crypto_price(symbol))
    return results

def get_watchlist_summary() -> str:
    """
    One-line summary for system prompt context.
    Example: NVDA: $142.30 ▲1.2% | TSLA: $248.10 ▼0.8% | BTC: $95,420 ▲2.1%
    """
    snapshot = get_watchlist_snapshot()
    return " | ".join(p["display"] for p in snapshot)

def get_watchlist_block() -> str:
    """
    Multi-line block for /dashboard and /price command.
    """
    snapshot = get_watchlist_snapshot()
    now = datetime.now().strftime("%H:%M:%S")
    lines = [f"  {p['display']}" for p in snapshot]
    lines.append(f"  Updated: {now}")
    return "\n".join(lines)

def get_single_price(query: str) -> str:
    """
    Parse a user query like 'BTC', 'NVDA', 'ETH price' and return display string.
    """
    query = query.upper().strip().split()[0].replace("$", "").replace("-USD", "")
    crypto_symbols = set(COINGECKO_IDS.keys()) | {"ETH", "SOL", "DOGE", "XRP", "ADA"}
    if query in crypto_symbols:
        return get_crypto_price(query)["display"]
    else:
        return get_stock_price(query)["display"]


# ── CLI test ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Testing spot prices...\n")
    print("Watchlist:")
    print(get_watchlist_block())
    print()
    print("Single lookups:")
    for sym in ["BTC", "NVDA", "ETH", "TSLA"]:
        print(f"  {get_single_price(sym)}")

"""
octo_robinhood.py -- Robinhood Chain Tokenized Stock Intelligence Module

Reads the tokenized-equity feed: live USD bid/ask for the underlying stock,
daily volume, trading-halt status, and processed corporate actions.

Tokenized stocks live on Robinhood Chain (chainId 4663) -- a distinct venue
from Base/Dinari and Securitize (Ethereum/Arbitrum). This is a 24/7 price
source, so it resolves stock calls on weekends/after-hours where yfinance
returns a stale prior-session close.

API: https://api.robinhood.com/rhj/  (public, no auth, 60 req/s, 15s cache)
Docs: https://docs.robinhood.com/chain/stock-token-apis/

Usage:
    from octo_robinhood import get_price, get_mid, get_stock_context

    q   = get_price("AAPL")          # full quote dict, {} if unsupported
    mid = get_mid("AAPL")            # float mid price, None if halted/missing
    ctx = get_stock_context("AAPL")  # formatted block for oracle prompts

CLI:
    python octo_robinhood.py price AAPL
    python octo_robinhood.py assets
    python octo_robinhood.py corp-actions [SYMBOL]
"""

import time
from typing import Optional

import requests

BASE_URL = "https://api.robinhood.com/rhj"
ROBINHOOD_CHAIN_ID = 4663

# In-memory cache: prices update ~15s, assets/corp-actions rarely.
_cache: dict = {}
_PRICE_TTL = 15      # seconds -- matches the endpoint's own cache window
_ASSETS_TTL = 3600   # 1 hour
_CORP_TTL = 3600     # 1 hour


def _get(path: str, ttl: int) -> Optional[dict]:
    """GET a Robinhood Chain endpoint with a short in-memory cache. None on failure."""
    now = time.time()
    hit = _cache.get(path)
    if hit and now - hit["ts"] < ttl:
        return hit["data"]
    try:
        r = requests.get(f"{BASE_URL}/{path}", timeout=10)
        if r.status_code != 200:
            return None
        data = r.json()
        _cache[path] = {"data": data, "ts": now}
        return data
    except Exception as e:
        print(f"[Robinhood] fetch failed for /{path}: {e}")
        return None


def get_price(symbol: str) -> dict:
    """Live tokenized-equity quote for the underlying stock. {} if unsupported/unavailable.

    Returns: symbol, bid, ask, mid, currency, volume, halt (bool), high, low,
             generated_at (UTC ISO), contract, chain_id.
    Prices are raw underlying-equity USD values (not multiplier-adjusted).
    """
    symbol = symbol.upper()
    data = _get(f"prices/{symbol}", _PRICE_TTL)
    quotes = (data or {}).get("quotes") or []
    if not quotes:
        return {}
    q = quotes[0]
    try:
        bid = float(q["bid"])
        ask = float(q["ask"])
    except (KeyError, TypeError, ValueError):
        return {}
    dep = (q.get("deployments") or [{}])[0]
    return {
        "symbol": q.get("tokenSymbol", symbol),
        "bid": bid,
        "ask": ask,
        "mid": round((bid + ask) / 2, 4),
        "currency": q.get("currency", "USD"),
        "volume": q.get("dailyTradingVolume"),
        "halt": bool(q.get("isTradingHalt", False)),
        "high": q.get("dailyHigh"),
        "low": q.get("dailyLow"),
        "generated_at": q.get("generatedAt"),
        "contract": dep.get("contractAddress"),
        "chain_id": dep.get("chainId", ROBINHOOD_CHAIN_ID),
    }


def get_mid(symbol: str) -> Optional[float]:
    """Mid price (bid+ask)/2 for the underlying stock. None if unsupported or halted.

    Returns None on a trading halt so callers never resolve a call against a frozen book.
    """
    q = get_price(symbol)
    if not q or q.get("halt"):
        return None
    return q.get("mid")


def get_assets() -> list:
    """All tokenized-stock assets: symbol, name, multiplier, status, deployments. [] on failure."""
    data = _get("assets", _ASSETS_TTL)
    return (data or {}).get("assets") or []


def supported_symbols() -> set:
    """Set of ticker symbols with an active tokenized stock on Robinhood Chain."""
    out = set()
    for a in get_assets():
        sym = a.get("tokenSymbol")
        if sym and a.get("status") == "ASSET_STATUS_ACTIVE":
            out.add(sym.upper())
    return out


def get_corporate_actions(symbol: Optional[str] = None) -> list:
    """Processed corporate actions (splits, dividends, mergers), most recent first.
    If symbol is given, filter to that ticker. [] on failure."""
    data = _get("corporate-actions", _CORP_TTL)
    actions = (data or {}).get("corpActions") or []
    if symbol:
        symbol = symbol.upper()
        actions = [a for a in actions if (a.get("tokenSymbol") or "").upper() == symbol]
    return actions


def get_stock_context(ticker: str) -> str:
    """Formatted tokenized-equity block for oracle prompts. '' if no data."""
    q = get_price(ticker)
    if not q:
        return ""
    t = q["symbol"]
    lines = [f"=== {t} TOKENIZED EQUITY (Robinhood Chain, 24/7, verified) ==="]
    halt = "  [TRADING HALTED]" if q["halt"] else ""
    lines.append(f"Underlying USD: bid {q['bid']} / ask {q['ask']} | mid {q['mid']}{halt}")
    if q.get("high") and q.get("low"):
        lines.append(f"Session range: {q['low']} - {q['high']} | vol {q.get('volume', 'n/a')}")
    pending = [a for a in get_corporate_actions(t)
               if a.get("status") == "CORPORATE_ACTION_STATUS_IN_PROGRESS"]
    if pending:
        types = ", ".join(sorted({a.get("type", "").replace("CORPORATE_ACTION_TYPE_", "").replace("_", " ").title()
                                  for a in pending}))
        lines.append(f"Pending corporate action: {types}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    args = sys.argv[1:]
    if not args:
        print("Usage: python octo_robinhood.py [price SYMBOL | assets | corp-actions [SYMBOL]]")
        sys.exit(0)

    cmd = args[0]
    if cmd == "price" and len(args) > 1:
        q = get_price(args[1])
        if not q:
            print(f"No tokenized quote for {args[1].upper()} (not on Robinhood Chain?)")
        else:
            for k, v in q.items():
                print(f"  {k:14} {v}")
    elif cmd == "assets":
        syms = sorted(supported_symbols())
        print(f"{len(syms)} active tokenized stocks: {', '.join(syms)}")
    elif cmd == "corp-actions":
        sym = args[1] if len(args) > 1 else None
        actions = get_corporate_actions(sym)
        print(f"{len(actions)} corporate action(s){' for ' + sym.upper() if sym else ''}:")
        for a in actions[:20]:
            d = a.get("processDate", {})
            date = f"{d.get('year')}-{d.get('month'):02d}-{d.get('day'):02d}" if d.get("year") else "?"
            typ = a.get("type", "").replace("CORPORATE_ACTION_TYPE_", "")
            print(f"  {date}  {a.get('tokenSymbol'):6} {typ}  [{a.get('status', '').replace('CORPORATE_ACTION_STATUS_', '')}]")
    else:
        print("Usage: python octo_robinhood.py [price SYMBOL | assets | corp-actions [SYMBOL]]")

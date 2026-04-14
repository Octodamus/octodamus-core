"""
financial_data_client.py - stub replacement
Original was deleted. get_current_price now uses yfinance directly.
"""
def get_current_price(ticker: str) -> dict:
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        hist = t.history(period="2d")
        if not hist.empty:
            price = float(hist["Close"].iloc[-1])
            prev  = float(hist["Close"].iloc[-2]) if len(hist) > 1 else price
            chg   = round((price - prev) / prev * 100, 2) if prev else 0.0
            return {"snapshot": {"price": price, "day_change_percent": chg}}
    except Exception:
        pass
    return {"snapshot": {"price": 0.0, "day_change_percent": 0.0}}

def get_current_crypto_price(ticker: str) -> float:
    try:
        import httpx
        cg_map = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}
        cg_id = cg_map.get(ticker.upper(), ticker.lower())
        r = httpx.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": cg_id, "vs_currencies": "usd"},
            timeout=8
        )
        if r.status_code == 200:
            return float(r.json()[cg_id]["usd"])
    except Exception:
        pass
    return 0.0


_CRYPTO_IDS = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}


def build_oracle_context(ticker: str, include_fundamentals: bool = False) -> dict:
    """Build market context for oracle analysis."""
    # Route crypto tickers to CoinGecko — yfinance doesn't know BTC/ETH/SOL
    if ticker.upper() in _CRYPTO_IDS:
        try:
            import httpx
            cg_id = _CRYPTO_IDS[ticker.upper()]
            r = httpx.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": cg_id, "vs_currencies": "usd", "include_24hr_change": "true"},
                timeout=10,
            )
            r.raise_for_status()
            data = r.json().get(cg_id, {})
            price = float(data.get("usd", 0))
            chg   = round(float(data.get("usd_24h_change", 0) or 0), 2)
            return {"ticker": ticker, "price": price, "chg_pct": chg}
        except Exception:
            return {"ticker": ticker, "price": 0.0, "chg_pct": 0.0}

    # Stocks via yfinance
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        hist = t.history(period="5d")
        price = float(hist["Close"].iloc[-1]) if not hist.empty else 0.0
        prev  = float(hist["Close"].iloc[-2]) if len(hist) > 1 else price
        chg   = round((price - prev) / prev * 100, 2) if prev else 0.0
        ctx = {"ticker": ticker, "price": price, "chg_pct": chg}
        if include_fundamentals:
            info = t.info or {}
            ctx["market_cap"] = info.get("marketCap", 0)
            ctx["pe_ratio"]   = info.get("trailingPE", 0)
            ctx["sector"]     = info.get("sector", "Unknown")
        return ctx
    except Exception:
        return {"ticker": ticker, "price": 0.0, "chg_pct": 0.0}

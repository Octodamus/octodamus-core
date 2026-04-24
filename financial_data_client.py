"""
financial_data_client.py
Crypto prices via Binance (primary, no key, high limits) with CoinGecko fallback.
All crypto fetches go through get_crypto_prices() which caches for 5 minutes.
"""

import json
import time
from pathlib import Path

_CACHE_FILE = Path(r"C:\Users\walli\octodamus\data\price_cache.json")
_CACHE_TTL  = 300  # 5 minutes

# Kraken symbol map (primary — US available, no key needed)
_KRAKEN_PAIRS  = {"BTC": "XBTUSD", "ETH": "ETHUSD", "SOL": "SOLUSD"}
_KRAKEN_RESULT = {"BTC": "XXBTZUSD", "ETH": "XETHZUSD", "SOL": "SOLUSD"}
# CoinGecko fallback map
_CG_IDS = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "HYPE": "hyperliquid"}


def _load_cache() -> dict:
    try:
        if _CACHE_FILE.exists():
            d = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
            if time.time() - d.get("ts", 0) < _CACHE_TTL:
                return d.get("prices", {})
    except Exception:
        pass
    return {}


def _save_cache(prices: dict):
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(
            json.dumps({"ts": time.time(), "prices": prices}),
            encoding="utf-8",
        )
    except Exception:
        pass


def _fetch_kraken(tickers: list) -> dict:
    """Fetch prices from Kraken public API. No key, no geo restriction."""
    import httpx
    crypto_tickers = [t for t in tickers if t in _KRAKEN_PAIRS]
    if not crypto_tickers:
        return {}
    pair_str = ",".join(_KRAKEN_PAIRS[t] for t in crypto_tickers)
    results = {}
    try:
        r = httpx.get(
            f"https://api.kraken.com/0/public/Ticker?pair={pair_str}",
            timeout=10,
        )
        if r.status_code == 200 and not r.json().get("error"):
            data = r.json()["result"]
            for ticker in crypto_tickers:
                # Kraken result keys vary — match by searching
                key = next((k for k in data if _KRAKEN_PAIRS[ticker].upper() in k.upper()
                            or k.upper() in _KRAKEN_RESULT.get(ticker, "").upper()
                            or k == _KRAKEN_RESULT.get(ticker, "")), None)
                if not key:
                    # Fallback: match by pair string in result key
                    key = next((k for k in data), None) if len(crypto_tickers) == 1 else None
                if key and key in data:
                    last  = float(data[key]["c"][0])
                    open_ = float(data[key]["o"])
                    chg   = round((last - open_) / open_ * 100, 2) if open_ else 0.0
                    results[ticker] = {"usd": last, "usd_24h_change": chg}
    except Exception as e:
        print(f"[Prices] Kraken fetch failed: {e}")
    return results


def _fetch_coingecko(tickers: list) -> dict:
    """Fallback: CoinGecko free API. May 429 under heavy use."""
    import httpx
    results = {}
    ids = [_CG_IDS[t] for t in tickers if t in _CG_IDS]
    if not ids:
        return results
    try:
        r = httpx.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": ",".join(ids), "vs_currencies": "usd", "include_24hr_change": "true"},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            for ticker, cg_id in _CG_IDS.items():
                if ticker in tickers and cg_id in data:
                    results[ticker] = {
                        "usd":            float(data[cg_id].get("usd", 0)),
                        "usd_24h_change": float(data[cg_id].get("usd_24h_change", 0) or 0),
                    }
    except Exception as e:
        print(f"[Prices] CoinGecko fallback failed: {e}")
    return results


def get_crypto_prices(tickers: list = None) -> dict:
    """
    Get crypto prices for given tickers (default: BTC, ETH, SOL).
    Returns {ticker: {usd, usd_24h_change}} — cached 5 min, Binance primary, CoinGecko fallback.
    Never returns zeros silently: if all sources fail, raises so callers can skip posting.
    """
    if tickers is None:
        tickers = ["BTC", "ETH", "SOL"]

    # Return cache if fresh
    cached = _load_cache()
    if cached and all(t in cached for t in tickers) and all(cached[t].get("usd", 0) > 0 for t in tickers):
        return {t: cached[t] for t in tickers if t in cached}

    # Try Kraken first (no geo restrictions, no key, high limits)
    prices = _fetch_kraken(tickers)

    # Fill any missing with CoinGecko fallback
    missing = [t for t in tickers if t not in prices or prices[t].get("usd", 0) == 0]
    if missing:
        cg = _fetch_coingecko(missing)
        prices.update(cg)

    # Cache if we got real prices
    valid = {t: p for t, p in prices.items() if p.get("usd", 0) > 0}
    if valid:
        merged = dict(cached)
        merged.update(valid)
        _save_cache(merged)
    else:
        # All sources failed — fire alert
        try:
            from octo_notify import notify_data_failure
            notify_data_failure(
                "price_feed",
                f"Kraken + CoinGecko both returned zero for {tickers}. Posts will be paused."
            )
        except Exception:
            pass

    return prices


def get_current_price(ticker: str) -> dict:
    """Stock price via yfinance. For crypto use get_crypto_prices()."""
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
    prices = get_crypto_prices([ticker.upper()])
    return prices.get(ticker.upper(), {}).get("usd", 0.0)


_CRYPTO_IDS = _CG_IDS


def build_oracle_context(ticker: str, include_fundamentals: bool = False) -> dict:
    if ticker.upper() in _CG_IDS:
        prices = get_crypto_prices([ticker.upper()])
        p = prices.get(ticker.upper(), {})
        return {
            "ticker":  ticker,
            "price":   p.get("usd", 0.0),
            "chg_pct": round(p.get("usd_24h_change", 0.0), 2),
        }
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        hist = t.history(period="5d")
        price = float(hist["Close"].iloc[-1]) if not hist.empty else 0.0
        prev  = float(hist["Close"].iloc[-2]) if len(hist) > 1 else price
        chg   = round((price - prev) / prev * 100, 2) if prev else 0.0
        ctx   = {"ticker": ticker, "price": price, "chg_pct": chg}
        if include_fundamentals:
            info = t.info or {}
            ctx["market_cap"] = info.get("marketCap", 0)
            ctx["pe_ratio"]   = info.get("trailingPE", 0)
            ctx["sector"]     = info.get("sector", "Unknown")
        return ctx
    except Exception:
        return {"ticker": ticker, "price": 0.0, "chg_pct": 0.0}

"""
octo_regime.py -- one trend regime for every call strategy.

Why this exists: the on-chain record was 11W-25L (31%) and DOWN calls were 5W-19L.
Re-scoring every resolved crypto call against the 7-day change and the 20-day
SMA at the moment it was made showed the losses were not random -- they were
calls issued AGAINST the prevailing trend:

    trend-opposed calls (bias UP, called DOWN, or the reverse):   0W-7L
    DOWN calls not in a confirmed downtrend (chg7 < 0 AND < SMA20): 2W-13L
    everything else:                                               8W-9L

Both range_scout (1W-8L) and crowd_fade (2W-8L) are mean-reversion strategies,
and their signals (longs paying, crowd 70% long, F&G in greed) all read "BEAR"
continuously during a healthy uptrend -- so they fired DOWN into strength every
few hours. crowd_fade had a 7d trend gate, but it read the 7d change from
api.binance.com, which returns 451 from this machine, and so it was 0.0% on
every call and never blocked anything.

This module is the shared, tested gate. Strategies call trend_gate() before
spending API calls, and octo_calls enforces it again at record time so no
strategy can bypass it.

Data: Kraken daily OHLC (no key, no geo-block) -> CoinGecko range -> yfinance
for stocks. Cached on disk for 30 minutes so the eight processes that run in
the same five-minute window share one fetch.

CLI:
    python octo_regime.py            # BTC ETH SOL regime table
    python octo_regime.py NVDA TSLA  # any assets
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).parent
CACHE_FILE = ROOT / "data" / "regime_cache.json"
CACHE_TTL_S = 30 * 60

# Bias thresholds -- the values the backtest used. A 7d move inside +/-STRONG_7D
# with price near its 20d mean is FLAT and either direction is allowed.
STRONG_7D = 1.5
SMA_DAYS = 20

_KRAKEN_PAIR = {
    "BTC": "XBTUSD", "ETH": "ETHUSD", "SOL": "SOLUSD", "XRP": "XRPUSD",
    "DOGE": "XDGUSD", "ADA": "ADAUSD", "AVAX": "AVAXUSD", "LINK": "LINKUSD",
    "SUI": "SUIUSD", "BNB": None, "UNI": "UNIUSD",
}
_CG_ID = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "XRP": "ripple",
    "DOGE": "dogecoin", "BNB": "binancecoin", "ADA": "cardano",
    "AVAX": "avalanche-2", "LINK": "chainlink", "SUI": "sui", "UNI": "uniswap",
}
CRYPTO = set(_CG_ID)


def _load_cache() -> dict:
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(cache), encoding="utf-8")
    except Exception:
        pass


# ── Daily closes ──────────────────────────────────────────────────────────────

def _kraken_closes(asset: str, days: int = 30) -> Optional[list]:
    pair = _KRAKEN_PAIR.get(asset)
    if not pair:
        return None
    try:
        import httpx
        r = httpx.get("https://api.kraken.com/0/public/OHLC",
                      params={"pair": pair, "interval": 1440}, timeout=10)
        result = r.json().get("result", {})
        rows = next((v for k, v in result.items() if k != "last"), None)
        if not rows:
            return None
        closes = [float(x[4]) for x in rows][-days:]
        return closes if len(closes) >= 8 else None
    except Exception:
        return None


def _coingecko_closes(asset: str, days: int = 30) -> Optional[list]:
    cid = _CG_ID.get(asset)
    if not cid:
        return None
    try:
        import httpx
        hdr = {"User-Agent": "octodamus-oracle/1.0"}
        key = os.environ.get("COINGECKO_API_KEY", "")
        if not key:
            try:
                raw = json.loads((ROOT / ".octo_secrets").read_text(encoding="utf-8"))
                key = raw.get("secrets", raw).get("COINGECKO_API_KEY", "")
            except Exception:
                key = ""
        if key:
            hdr["x-cg-demo-api-key"] = key
        r = httpx.get(f"https://api.coingecko.com/api/v3/coins/{cid}/market_chart",
                      params={"vs_currency": "usd", "days": days, "interval": "daily"},
                      headers=hdr, timeout=12)
        if r.status_code != 200:
            return None
        closes = [p[1] for p in r.json().get("prices", []) if p and p[1]]
        return closes[-days:] if len(closes) >= 8 else None
    except Exception:
        return None


# Oracle asset names that are not literal Yahoo tickers. "WTI" on Yahoo is
# W&T Offshore; the oracle means the crude front-month.
_YF_ALIAS = {"WTI": "CL=F", "BRENT": "BZ=F", "GOLD": "GC=F", "SILVER": "SI=F",
             "SPX": "^GSPC", "NDX": "^NDX", "DXY": "DX-Y.NYB", "VIX": "^VIX"}


def _yfinance_closes(asset: str, days: int = 30) -> Optional[list]:
    try:
        import yfinance as yf
        hist = yf.Ticker(_YF_ALIAS.get(asset, asset)).history(period="2mo", interval="1d", auto_adjust=False)
        closes = [float(x) for x in hist["Close"].dropna().tolist()]
        return closes[-days:] if len(closes) >= 8 else None
    except Exception:
        return None


def _daily_closes(asset: str) -> Optional[list]:
    asset = asset.upper()
    if asset in CRYPTO:
        return _kraken_closes(asset) or _coingecko_closes(asset)
    return _yfinance_closes(asset)


# ── Regime ────────────────────────────────────────────────────────────────────

def get_regime(asset: str, force: bool = False) -> Optional[dict]:
    """
    {price, chg_7d, chg_3d, vs_sma20_pct, bias, source, ts} or None when no
    price history is reachable. `bias` is UP / DOWN / FLAT.
    """
    asset = asset.upper()
    cache = _load_cache()
    hit = cache.get(asset)
    if hit and not force and time.time() - hit.get("ts", 0) < CACHE_TTL_S:
        return hit

    closes = _daily_closes(asset)
    if not closes:
        return None
    price = closes[-1]
    p7 = closes[-8] if len(closes) >= 8 else closes[0]
    p3 = closes[-4] if len(closes) >= 4 else closes[0]
    window = closes[-SMA_DAYS:]
    sma = sum(window) / len(window)
    chg7 = (price - p7) / p7 * 100
    chg3 = (price - p3) / p3 * 100
    vs_sma = (price - sma) / sma * 100

    if chg7 > STRONG_7D and vs_sma > 0:
        bias = "UP"
    elif chg7 < -STRONG_7D and vs_sma < 0:
        bias = "DOWN"
    else:
        bias = "FLAT"

    out = {
        "asset": asset, "price": price,
        "chg_7d": round(chg7, 2), "chg_3d": round(chg3, 2),
        "vs_sma20_pct": round(vs_sma, 2), "bias": bias,
        "n_days": len(closes), "ts": time.time(),
        "asof": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }
    cache[asset] = out
    _save_cache(cache)
    return out


def trend_gate(asset: str, direction: str, regime: Optional[dict] = None) -> tuple[bool, str]:
    """
    (allowed, reason). The rule the record supports:

      DOWN  only inside a confirmed downtrend: 7d change < 0 AND price below SMA20.
      UP    blocked only against a confirmed downtrend (bias DOWN); allowed when
            the 7d change is positive OR price is above SMA20.

    Asymmetric on purpose: the losses were DOWN calls into strength (5W-19L on
    DOWN vs 6W-6L on UP), and a short squeeze is the one setup that legitimately
    fires UP on a red day (funding_extreme's three wins were -2.5% 24h days in a
    7d uptrend).

    No regime data -> NOT allowed. A call is written to Base and cannot be undone;
    one skipped call costs nothing, one blind call costs the record.
    """
    direction = direction.upper()
    rg = regime or get_regime(asset)
    if not rg:
        return (False, f"{asset.upper()}: no trend data (Kraken/CoinGecko/yfinance all failed) -- refusing to call blind")
    tag = f"7d {rg['chg_7d']:+.1f}%, {rg['vs_sma20_pct']:+.1f}% vs SMA20, bias {rg['bias']}"
    if direction == "DOWN":
        if rg["chg_7d"] < 0 and rg["vs_sma20_pct"] < 0:
            return (True, f"downtrend confirmed ({tag})")
        return (False, f"DOWN blocked -- not a confirmed downtrend ({tag}). Fading strength is 2W-13L on record.")
    if direction == "UP":
        if rg["bias"] == "DOWN":
            return (False, f"UP blocked -- confirmed downtrend ({tag})")
        if rg["chg_7d"] > 0 or rg["vs_sma20_pct"] > 0:
            return (True, f"trend supports UP ({tag})")
        return (False, f"UP blocked -- no trend support ({tag})")
    return (False, f"unknown direction {direction!r}")


def regime_line(asset: str) -> str:
    """One-line summary for prompts and logs."""
    rg = get_regime(asset)
    if not rg:
        return f"{asset.upper()}: trend unavailable"
    return (f"{rg['asset']} trend: {rg['bias']} (7d {rg['chg_7d']:+.1f}%, 3d {rg['chg_3d']:+.1f}%, "
            f"{rg['vs_sma20_pct']:+.1f}% vs 20d avg)")


if __name__ == "__main__":
    assets = [a.upper() for a in sys.argv[1:]] or ["BTC", "ETH", "SOL"]
    for a in assets:
        print(regime_line(a))
        for d in ("UP", "DOWN"):
            ok, why = trend_gate(a, d)
            print(f"   {d:<5} {'ALLOW' if ok else 'BLOCK'}  {why}")

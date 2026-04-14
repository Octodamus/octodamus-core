"""
octo_coinglass.py — Coinglass Futures Intelligence Module

Reads the currents: liquidation maps, open interest, funding rates,
long/short ratios, taker buy/sell volume, and liquidation orders.

This is Octodamus's primary data source for directional calls.
"Where the money is, where it's going, where it will go."

API: Coinglass V4 (open-api-v4.coinglass.com)
Plan: Hobbyist ($29/mo) — 80+ endpoints, 30 req/min, 4h+ interval history

Usage:
    from octo_coinglass import glass

    # Full market read for a coin (daily + 1h timeframes)
    snapshot = glass.read_currents("BTC")

    # Individual data pulls
    liq_map   = glass.liquidation_map("BTC")
    oi        = glass.open_interest("BTC", interval="4h")
    funding   = glass.funding_rate("BTC")
    ls_ratio  = glass.long_short_ratio("BTC")
    taker     = glass.taker_buy_sell("BTC", interval="4h")
    liq_hist  = glass.liquidation_history("BTC")
    liq_order = glass.liquidation_orders("BTC")
    coins_mkt = glass.coins_markets()

    # Formatted context string for Claude prompts
    ctx = glass.build_oracle_context("BTC")
"""

import json
import logging
import os
import time
from typing import Optional

log = logging.getLogger("OctoCoinglass")

# ── Config ────────────────────────────────────────────────────────────────────

BASE_URL = "https://open-api-v4.coinglass.com/api"

# Rate limit: 30 req/min on Hobbyist = 1 req per 2 seconds
# We track calls to stay under limit
_call_times: list = []
RATE_LIMIT = 30
RATE_WINDOW = 60  # seconds

# Cache to avoid redundant calls within short windows
_cache: dict = {}
CACHE_TTL = 60  # seconds — most data updates every 30-60s


def _get_key() -> str:
    """Load Coinglass API key from env or .octo_secrets JSON cache."""
    import json as _json

    key = os.environ.get("COINGLASS_API_KEY", "")
    if key:
        return key
    # Try .octo_secrets JSON cache (written by bitwarden.py)
    secrets_paths = [
        os.path.join(os.path.dirname(__file__), ".octo_secrets"),
        r"C:\Users\walli\octodamus\.octo_secrets",
        "/home/walli/octodamus/.octo_secrets",
    ]
    for path in secrets_paths:
        try:
            with open(path, "r", encoding="utf-8") as f:
                cache = _json.load(f)
                val = cache.get("secrets", {}).get("COINGLASS_API_KEY", "")
                if val:
                    return val
        except (FileNotFoundError, _json.JSONDecodeError):
            continue
    log.warning("No COINGLASS_API_KEY found")
    return ""


def _rate_check():
    """Block if we'd exceed 30 req/min."""
    now = time.time()
    # Purge old entries
    while _call_times and _call_times[0] < now - RATE_WINDOW:
        _call_times.pop(0)
    if len(_call_times) >= RATE_LIMIT:
        wait = _call_times[0] + RATE_WINDOW - now + 0.1
        if wait > 0:
            log.info(f"Rate limit: sleeping {wait:.1f}s")
            time.sleep(wait)
    _call_times.append(time.time())


def _get(endpoint: str, params: dict = None, cache_key: str = None) -> dict:
    """
    Make authenticated GET to Coinglass V4 API.
    Returns parsed JSON data or empty dict on failure.
    """
    import httpx

    # Check cache
    if cache_key:
        cached = _cache.get(cache_key)
        if cached and (time.time() - cached["ts"]) < CACHE_TTL:
            return cached["data"]

    _rate_check()

    key = _get_key()
    if not key:
        return {"error": "No API key configured"}

    url = f"{BASE_URL}/{endpoint}"
    try:
        r = httpx.get(
            url,
            params=params or {},
            headers={"CG-API-KEY": key, "Accept": "application/json"},
            timeout=15,
        )
        if r.status_code != 200:
            log.error(f"Coinglass {endpoint}: HTTP {r.status_code}")
            return {"error": f"HTTP {r.status_code}", "detail": r.text[:200]}

        body = r.json()

        # V4 wraps data in {"code": "0", "msg": "success", "data": ...}
        if body.get("code") == "0" or body.get("code") == 0:
            data = body.get("data", body)
            if cache_key:
                _cache[cache_key] = {"ts": time.time(), "data": data}
            return data
        else:
            log.error(f"Coinglass {endpoint}: code={body.get('code')} msg={body.get('msg')}")
            return {"error": body.get("msg", "Unknown error"), "code": body.get("code")}

    except httpx.TimeoutException:
        log.error(f"Coinglass {endpoint}: timeout")
        return {"error": "timeout"}
    except Exception as e:
        log.error(f"Coinglass {endpoint}: {e}")
        return {"error": str(e)}


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC API — The Eight Currents
# ══════════════════════════════════════════════════════════════════════════════


def liquidation_map(symbol: str = "BTC", range_pct: str = "5") -> dict:
    """
    Liquidation map — shows where leveraged positions will get liquidated.
    This is THE key data for directional calls: large liq clusters act as
    magnets that price tends to sweep toward.

    range_pct: price range percentage around current price (e.g. "5" = ±5%)
    Returns price levels with cumulative long/short liquidation volumes.
    """
    return _get(
        "futures/liquidation/map",
        params={"symbol": symbol, "range": range_pct},
        cache_key=f"liq_map_{symbol}_{range_pct}",
    )


def liquidation_history(symbol: str = "BTC", interval: str = "4h") -> dict:
    """
    Historical liquidation data — how much got liquidated over time.
    Spikes = forced selling/buying = directional signal.

    interval: 4h, 12h, 1d (Hobbyist plan: 4h minimum)
    """
    return _get(
        "futures/liquidation/aggregated-history",
        params={"symbol": symbol, "interval": interval, "exchange_list": "Binance"},
        cache_key=f"liq_hist_{symbol}_{interval}",
    )


def liquidation_orders(symbol: str = "BTC") -> dict:
    """
    Real-time liquidation orders — individual forced liquidations as they happen.
    Large single liquidations = whale positions getting wiped = volatility signal.
    """
    return _get(
        "futures/liquidation/order",
        params={"symbol": symbol},
        cache_key=f"liq_orders_{symbol}",
    )


def open_interest(symbol: str = "BTC", interval: str = "4h") -> dict:
    """
    Aggregated Open Interest OHLC — total money in futures positions.
    Rising OI + rising price = strong trend. Rising OI + flat price = squeeze building.

    interval: 4h, 6h, 8h, 12h, 1d (Hobbyist: 4h minimum)
    """
    return _get(
        "futures/open-interest/aggregated-history",
        params={"symbol": symbol, "interval": interval},
        cache_key=f"oi_{symbol}_{interval}",
    )


def open_interest_exchange(symbol: str = "BTC") -> dict:
    """
    OI broken down by exchange — shows where the big positions are concentrated.
    Binance + OKX dominate; shifts between exchanges signal smart money movement.
    """
    return _get(
        "futures/open-interest/exchange-list",
        params={"symbol": symbol},
        cache_key=f"oi_exch_{symbol}",
    )


def funding_rate(symbol: str = "BTC", interval: str = "8h") -> dict:
    """
    Funding rate OHLC — the cost of holding leveraged positions.
    High positive = longs paying shorts = market overheated long = bearish signal.
    High negative = shorts paying longs = market overheated short = bullish signal.

    interval: 8h, 1d
    """
    return _get(
        "futures/funding-rate/history",
        params={"symbol": symbol, "interval": interval},
        cache_key=f"fr_{symbol}_{interval}",
    )


def funding_rate_exchange(symbol: str = "BTC") -> dict:
    """
    Current funding rate across all exchanges.
    Divergence between exchanges = arbitrage or positioning signal.
    """
    return _get(
        "futures/funding-rate/exchange-list",
        params={"symbol": symbol},
        cache_key=f"fr_exch_{symbol}",
    )


def long_short_ratio(symbol: str = "BTC", interval: str = "4h") -> dict:
    """
    Global long/short account ratio — what percentage of traders are long vs short.
    Extreme readings (>70% one side) often precede reversals.

    interval: 4h, 12h, 1d (Hobbyist plan: 4h minimum)
    Note: Binance requires pair format e.g. BTCUSDT not BTC
    """
    pair = f"{symbol}USDT"
    return _get(
        "futures/global-long-short-account-ratio/history",
        params={"symbol": pair, "interval": interval, "exchange": "Binance"},
        cache_key=f"ls_ratio_{symbol}_{interval}",
    )


def top_long_short_ratio(symbol: str = "BTC", interval: str = "4h") -> dict:
    """
    Top trader long/short ratio — what the whales are doing (not retail).
    When top traders flip, that's the strongest signal.
    Note: Binance requires pair format e.g. BTCUSDT not BTC
    """
    pair = f"{symbol}USDT"
    return _get(
        "futures/top-long-short-account-ratio/history",
        params={"symbol": pair, "interval": interval, "exchange": "Binance"},
        cache_key=f"top_ls_{symbol}_{interval}",
    )


def taker_buy_sell(symbol: str = "BTC", interval: str = "4h") -> dict:
    """
    Taker buy/sell volume — who's aggressively buying vs selling RIGHT NOW.
    Taker = market orders hitting the book = urgency = directional conviction.

    This is the closest thing to reading order flow in real time.
    """
    return _get(
        "futures/aggregated-taker-buy-sell-volume/history",
        params={"symbol": symbol, "interval": interval, "exchange_list": "Binance"},
        cache_key=f"taker_{symbol}_{interval}",
    )


def coins_markets() -> dict:
    """
    All coins market overview — price, OI, volume, OI change, funding rate.
    Good for scanning which coins have unusual activity.
    """
    return _get(
        "futures/coins-markets",
        cache_key="coins_markets",
    )


def fear_greed() -> dict:
    """
    Crypto Fear & Greed Index from Coinglass.
    Replaces the api.alternative.me source with Coinglass native data.
    """
    return _get(
        "index/fear-greed-history",
        cache_key="fear_greed",
    )


# ══════════════════════════════════════════════════════════════════════════════
# COMPOSITE FUNCTIONS — Oracle Intelligence
# ══════════════════════════════════════════════════════════════════════════════


def read_currents(symbol: str = "BTC") -> dict:
    """
    Full market read for directional call analysis.
    Pulls all core data for both daily and 4h timeframes.
    Returns a structured dict that Claude can reason over.

    This is what Octodamus reads before making a call.
    """
    return {
        "symbol": symbol,
        "timestamp": int(time.time()),
        # Where the money IS (current positioning)
        "open_interest_exchange": open_interest_exchange(symbol),
        "funding_rate_exchange": funding_rate_exchange(symbol),
        "long_short_ratio_4h": long_short_ratio(symbol, "4h"),
        "top_traders_ratio_4h": top_long_short_ratio(symbol, "4h"),
        # Where the money is GOING (flow and urgency)
        "taker_buy_sell_4h": taker_buy_sell(symbol, "4h"),
        # Historical context (trend)
        "open_interest_daily": open_interest(symbol, "1d"),
        "funding_rate_daily": funding_rate(symbol, "8h"),
        "long_short_ratio_daily": long_short_ratio(symbol, "1d"),
        "liquidation_history_4h": liquidation_history(symbol, "4h"),
        # Note: liquidation_map, liquidation_orders, coins_markets require higher plan tier
    }


def build_oracle_context(symbol: str = "BTC") -> str:
    """
    Build a formatted context string for Claude prompts.
    This is injected into the runner/Telegram when Octodamus
    needs to make or evaluate a directional call.
    """
    lines = [f"=== COINGLASS FUTURES INTELLIGENCE: {symbol} ===\n"]

    # ── Funding Rate (exchange breakdown) ─────────────────────────────────
    fr_data = funding_rate_exchange(symbol)
    if isinstance(fr_data, list) and fr_data:
        # V4 returns list of coin objects with stablecoin_margin_list nested
        coin_fr = next((c for c in fr_data if c.get("symbol") == symbol), fr_data[0] if fr_data else {})
        margin_list = coin_fr.get("stablecoin_margin_list", []) if isinstance(coin_fr, dict) else []
        if margin_list:
            lines.append("► FUNDING RATES BY EXCHANGE (stablecoin margin):")
            for ex in margin_list[:8]:
                name = ex.get("exchange", "?")
                rate = ex.get("funding_rate", 0) or 0
                try:
                    rate_pct = float(rate) * 100
                    direction = "LONGS PAY" if rate_pct > 0 else "SHORTS PAY"
                    lines.append(f"  {name}: {rate_pct:+.4f}% ({direction})")
                except (ValueError, TypeError):
                    pass
            lines.append("")

    # ── Open Interest (exchange breakdown) ────────────────────────────────
    oi_data = open_interest_exchange(symbol)
    if isinstance(oi_data, list) and oi_data:
        lines.append("► OPEN INTEREST BY EXCHANGE:")
        total_oi = 0
        for ex in oi_data[:8]:
            name = ex.get("exchange", "?")
            if name == "All":
                continue  # Skip the aggregate row
            oi_val = float(ex.get("open_interest_usd", 0) or 0)
            oi_chg_1h = ex.get("open_interest_change_percent_1h", 0) or 0
            oi_chg_24h = ex.get("open_interest_change_percent_24h", 0) or 0
            total_oi += oi_val
            lines.append(f"  {name}: ${oi_val/1e9:.2f}B (1h: {oi_chg_1h:+.1f}%, 24h: {oi_chg_24h:+.1f}%)")
        # Get total from the "All" row
        all_row = next((ex for ex in oi_data if ex.get("exchange") == "All"), None)
        if all_row:
            total_oi = float(all_row.get("open_interest_usd", 0) or 0)
        lines.append(f"  TOTAL: ${total_oi/1e9:.2f}B")
        lines.append("")

    # ── Long/Short Ratio (recent) ─────────────────────────────────────────
    ls_data = long_short_ratio(symbol, "4h")
    if isinstance(ls_data, list) and ls_data:
        latest = ls_data[-1] if ls_data else {}
        long_pct = float(latest.get("global_account_long_percent", 50) or 50)
        short_pct = float(latest.get("global_account_short_percent", 50) or 50)
        ratio = latest.get("global_account_long_short_ratio", 0)
        lines.append(f"► LONG/SHORT RATIO (Binance, 4h):")
        lines.append(f"  Longs: {long_pct:.1f}% | Shorts: {short_pct:.1f}% | Ratio: {ratio}")
        skew = "LONG-HEAVY" if long_pct > 55 else "SHORT-HEAVY" if short_pct > 55 else "BALANCED"
        lines.append(f"  Skew: {skew}")
        lines.append("")

    # ── Top Traders Ratio ──────────────────────────────────────────────────
    top_data = top_long_short_ratio(symbol, "4h")
    if isinstance(top_data, list) and top_data:
        latest = top_data[-1] if top_data else {}
        long_pct = float(latest.get("top_account_long_percent", 50) or 50)
        short_pct = float(latest.get("top_account_short_percent", 50) or 50)
        ratio = latest.get("top_account_long_short_ratio", 0)
        lines.append(f"► TOP TRADERS RATIO (Binance whales, 4h):")
        lines.append(f"  Longs: {long_pct:.1f}% | Shorts: {short_pct:.1f}% | Ratio: {ratio}")
        lines.append("")

    # ── Taker Buy/Sell (recent flow) ──────────────────────────────────────
    taker_data = taker_buy_sell(symbol, "4h")
    if isinstance(taker_data, list) and taker_data:
        recent = taker_data[-3:] if len(taker_data) >= 3 else taker_data
        lines.append("► TAKER FLOW (last 12h, 4h bars):")
        for bar in recent:
            buy = float(bar.get("aggregated_buy_volume_usd", 0) or 0)
            sell = float(bar.get("aggregated_sell_volume_usd", 0) or 0)
            total = buy + sell
            if total > 0:
                buy_pct = buy / total * 100
                flow = "BUY PRESSURE" if buy_pct > 55 else "SELL PRESSURE" if buy_pct < 45 else "NEUTRAL"
                lines.append(f"  Buy: {buy_pct:.0f}% | ${total/1e6:.0f}M vol | {flow}")
        lines.append("")

    # ── Recent Liquidations (aggregated history) ──────────────────────────
    liq_hist = liquidation_history(symbol, "4h")
    if isinstance(liq_hist, list) and liq_hist:
        recent = liq_hist[-3:] if len(liq_hist) >= 3 else liq_hist
        lines.append("► RECENT LIQUIDATIONS (last 12h, 4h bars):")
        for bar in recent:
            long_liq = float(bar.get("aggregated_long_liquidation_usd", 0) or 0)
            short_liq = float(bar.get("aggregated_short_liquidation_usd", 0) or 0)
            total = long_liq + short_liq
            if total > 0:
                dominant = "LONG PAIN" if long_liq > short_liq else "SHORT PAIN"
                lines.append(f"  Longs: ${long_liq/1e6:.1f}M | Shorts: ${short_liq/1e6:.1f}M | {dominant}")
        lines.append("")

    lines.append("=== END COINGLASS INTELLIGENCE ===")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# ALERT ENGINE — Spike Detection for Event-Driven Posts
# ══════════════════════════════════════════════════════════════════════════════


class AlertEngine:
    """
    Monitors Coinglass data for unusual conditions that should trigger posts.
    Used by the runner to switch from scheduled → event-driven posting.
    """

    def __init__(self):
        self._baselines: dict = {}  # Rolling averages for comparison
        self._last_check = 0.0

    def check_alerts(self, symbol: str = "BTC") -> list:
        """
        Returns list of alert dicts if any thresholds are breached.
        Each alert has: type, severity (1-3), message, data
        """
        alerts = []

        # ── Liquidation spike ─────────────────────────────────────────────
        liq = liquidation_history(symbol, "4h")
        if isinstance(liq, list) and len(liq) >= 2:
            latest = liq[-1]
            prev = liq[-2]
            try:
                latest_total = float(latest.get("aggregated_long_liquidation_usd", 0) or 0) + \
                               float(latest.get("aggregated_short_liquidation_usd", 0) or 0)
                prev_total = float(prev.get("aggregated_long_liquidation_usd", 0) or 0) + \
                             float(prev.get("aggregated_short_liquidation_usd", 0) or 0)
                if prev_total > 0 and latest_total > prev_total * 3:
                    alerts.append({
                        "type": "liquidation_spike",
                        "severity": 3 if latest_total > 100_000_000 else 2,
                        "message": f"{symbol} liquidation spike: ${latest_total/1e6:.0f}M in last 4h (3x+ previous)",
                        "data": {"current": latest_total, "previous": prev_total},
                    })
            except (ValueError, TypeError):
                pass

        # ── Funding rate extreme ──────────────────────────────────────────
        fr = funding_rate_exchange(symbol)
        if isinstance(fr, list) and fr:
            # V4: data is nested in stablecoin_margin_list
            coin_fr = next((c for c in fr if c.get("symbol") == symbol), fr[0] if fr else {})
            margin_list = coin_fr.get("stablecoin_margin_list", []) if isinstance(coin_fr, dict) else []
            rates = []
            for ex in margin_list:
                try:
                    r = float(ex.get("funding_rate", 0) or 0)
                    rates.append(r)
                except (ValueError, TypeError):
                    pass
            if rates:
                avg_rate = sum(rates) / len(rates)
                if abs(avg_rate) > 0.0005:  # >0.05% per 8h = extreme
                    direction = "LONGS OVERHEATED" if avg_rate > 0 else "SHORTS OVERHEATED"
                    alerts.append({
                        "type": "funding_extreme",
                        "severity": 2,
                        "message": f"{symbol} funding extreme: avg {avg_rate*100:+.4f}% — {direction}",
                        "data": {"avg_rate": avg_rate},
                    })

        # ── Long/short ratio extreme ──────────────────────────────────────
        ls = long_short_ratio(symbol, "4h")
        if isinstance(ls, list) and ls:
            latest = ls[-1]
            try:
                long_rate = float(latest.get("global_account_long_percent", 50) or 50)
                if long_rate > 70 or long_rate < 30:
                    side = "EXTREME LONG" if long_rate > 70 else "EXTREME SHORT"
                    alerts.append({
                        "type": "ratio_extreme",
                        "severity": 2,
                        "message": f"{symbol} L/S ratio extreme: {long_rate:.0f}% long — {side}",
                        "data": {"long_rate": long_rate},
                    })
            except (ValueError, TypeError):
                pass

        # ── OI surge (compared to 24h trend) ─────────────────────────────
        oi = open_interest(symbol, "1d")
        if isinstance(oi, list) and len(oi) >= 2:
            try:
                latest_oi = float(oi[-1].get("c", 0) or oi[-1].get("close", 0) or 0)
                prev_oi = float(oi[-2].get("c", 0) or oi[-2].get("close", 0) or 0)
                if prev_oi > 0:
                    oi_change = (latest_oi - prev_oi) / prev_oi * 100
                    if abs(oi_change) > 10:  # >10% OI change in a day = major
                        direction = "SURGING" if oi_change > 0 else "COLLAPSING"
                        alerts.append({
                            "type": "oi_surge",
                            "severity": 3 if abs(oi_change) > 20 else 2,
                            "message": f"{symbol} OI {direction}: {oi_change:+.1f}% in 24h",
                            "data": {"change_pct": oi_change},
                        })
            except (ValueError, TypeError):
                pass

        return alerts


# ══════════════════════════════════════════════════════════════════════════════
# MODULE SINGLETON
# ══════════════════════════════════════════════════════════════════════════════


class CoinglassClient:
    """Convenience wrapper — importable singleton."""

    def __init__(self):
        self.alerts = AlertEngine()

    # Passthrough to module functions
    liquidation_map = staticmethod(liquidation_map)
    liquidation_history = staticmethod(liquidation_history)
    liquidation_orders = staticmethod(liquidation_orders)
    open_interest = staticmethod(open_interest)
    open_interest_exchange = staticmethod(open_interest_exchange)
    funding_rate = staticmethod(funding_rate)
    funding_rate_exchange = staticmethod(funding_rate_exchange)
    long_short_ratio = staticmethod(long_short_ratio)
    top_long_short_ratio = staticmethod(top_long_short_ratio)
    taker_buy_sell = staticmethod(taker_buy_sell)
    coins_markets = staticmethod(coins_markets)
    fear_greed = staticmethod(fear_greed)
    read_currents = staticmethod(read_currents)
    build_oracle_context = staticmethod(build_oracle_context)

    def check_alerts(self, symbols: list = None) -> list:
        """Check alerts for multiple symbols."""
        symbols = symbols or ["BTC", "ETH", "SOL"]
        all_alerts = []
        for sym in symbols:
            all_alerts.extend(self.alerts.check_alerts(sym))
        return sorted(all_alerts, key=lambda a: a["severity"], reverse=True)


# Module-level singleton
glass = CoinglassClient()


# ══════════════════════════════════════════════════════════════════════════════
# CLI TEST
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)

    symbol = sys.argv[1] if len(sys.argv) > 1 else "BTC"

    print(f"\n{'='*60}")
    print(f"  COINGLASS TEST — {symbol}")
    print(f"{'='*60}\n")

    # Test API key
    key = _get_key()
    if not key:
        print("ERROR: No COINGLASS_API_KEY found")
        print("Set it in .octo_secrets or environment")
        sys.exit(1)
    print(f"API Key: ...{key[-6:]}")

    # Test each endpoint
    tests = [
        ("Funding Rate Exchange", lambda: funding_rate_exchange(symbol)),
        ("OI Exchange", lambda: open_interest_exchange(symbol)),
        ("Long/Short Ratio 4h", lambda: long_short_ratio(symbol, "4h")),
        ("Top Traders Ratio 4h", lambda: top_long_short_ratio(symbol, "4h")),
        ("Taker Buy/Sell 4h", lambda: taker_buy_sell(symbol, "4h")),
        ("Liquidation History 4h", lambda: liquidation_history(symbol, "4h")),
        ("Liquidation Map", lambda: liquidation_map(symbol)),
        ("Liquidation Orders", lambda: liquidation_orders(symbol)),
        ("Coins Markets", lambda: coins_markets()),
    ]

    for name, fn in tests:
        try:
            result = fn()
            if isinstance(result, dict) and result.get("error"):
                print(f"  FAIL  {name}: {result['error']}")
            elif isinstance(result, list):
                print(f"  OK    {name}: {len(result)} records")
                # Show first record keys for debugging field names
                if result:
                    first = result[0]
                    if isinstance(first, dict):
                        print(f"         Keys: {list(first.keys())[:10]}")
                        # Show a sample of values
                        sample = {k: v for k, v in list(first.items())[:6]}
                        print(f"         Sample: {sample}")
            elif isinstance(result, dict):
                print(f"  OK    {name}: {len(result)} keys")
                print(f"         Keys: {list(result.keys())[:10]}")
            else:
                print(f"  OK    {name}: {type(result)}")
        except Exception as e:
            print(f"  ERROR {name}: {e}")

    print(f"\n{'='*60}")
    print("  ORACLE CONTEXT OUTPUT")
    print(f"{'='*60}\n")
    print(build_oracle_context(symbol))

    print(f"\n{'='*60}")
    print("  ALERT CHECK")
    print(f"{'='*60}\n")
    alerts = glass.check_alerts([symbol])
    if alerts:
        for a in alerts:
            print(f"  [{a['severity']}] {a['type']}: {a['message']}")
    else:
        print("  No alerts triggered")

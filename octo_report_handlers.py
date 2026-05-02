"""
octo_report_handlers.py — Shared report generation for ACP worker + API server.
Imported by both octo_acp_worker.py and octo_api_server.py.

v3 — Coinglass Integration:
- All crypto handlers pull Coinglass futures data (liq map, OI, funding, L/S ratio)
- directional_call() upgraded with 11 signals (was 6): adds funding, L/S, taker flow, liq skew
- Reports restructured: ~2/3 compact data, ~1/3 Octodamus directional analysis
- Footer links to octodamus.com/results (Oracle call record + OctoBoto trades)
- Congressional handler unchanged (stock-focused) but gets footer
"""

import os
import statistics
from datetime import datetime
from pathlib import Path
from octo_acp_ben_reports import handle_grok_sentiment_brief, handle_fear_crowd_divergence, handle_btc_bull_trap_monitor, handle_btc_strike_proximity_alert, handle_carry_unwind_risk_monitor
from octo_acp_stockoracle_reports import handle_congressional_silence_signal

# ── Results page link ────────────────────────────────────────────────────────

RESULTS_URL = "https://octodamus.com/results"
FOOTER = f"Track Record: {RESULTS_URL}\nPowered by Octodamus (@octodamusai) — Reading the Currents."

# ── Kraken Technical Analysis ─────────────────────────────────────────────────

def _kraken_ohlc_pair(ticker):
    m = {
        "BTC": "XBTUSD", "ETH": "ETHUSD", "SOL": "SOLUSD",
        "BNB": "BNBUSD", "XRP": "XRPUSD", "DOGE": "DOGEUSD",
    }
    return m.get(ticker.upper(), ticker.upper() + "USD")


def _kraken_futures_sym(ticker):
    m = {"BTC": "PI_XBTUSD", "ETH": "PI_ETHUSD", "SOL": "PI_SOLUSD"}
    return m.get(ticker.upper(), "PI_XBTUSD")


def _ema(data, period):
    k = 2 / (period + 1)
    e = data[0]
    for p in data[1:]:
        e = p * k + e * (1 - k)
    return round(e, 2)


def fetch_technicals(ticker="BTC") -> dict:
    import httpx
    try:
        r = httpx.get(
            "https://api.kraken.com/0/public/OHLC",
            params={"pair": _kraken_ohlc_pair(ticker), "interval": 240, "count": 50},
            timeout=10,
        )
        if r.status_code != 200:
            return {}
        result = r.json()
        if result.get("error"):
            return {}
        keys = list(result["result"].keys())
        if not keys:
            return {}
        closes = [float(c[4]) for c in result["result"][keys[0]]]
        if len(closes) < 26:
            return {}
        ema20 = _ema(closes, 20)
        ema50 = _ema(closes, 50)
        macd  = round(_ema(closes, 12) - _ema(closes, 26), 2)
        gains, losses = [], []
        for i in range(1, 15):
            d = closes[-i] - closes[-i - 1]
            (gains if d > 0 else losses).append(abs(d))
        avg_g = sum(gains) / 14 if gains else 0
        avg_l = sum(losses) / 14 if losses else 0.001
        rsi   = round(100 - 100 / (1 + avg_g / avg_l), 1)
        recent = closes[-20:]
        bb_m   = sum(recent) / 20
        bb_s   = statistics.stdev(recent)
        bb_w   = round((bb_m + 2 * bb_s - (bb_m - 2 * bb_s)) / bb_m * 100, 1)
        return {
            "ema20": ema20, "ema50": ema50,
            "trend": "Bullish" if ema20 > ema50 else "Bearish",
            "rsi": rsi, "macd": macd, "bb_width": bb_w,
        }
    except Exception:
        return {}


def fetch_technicals_mtf(ticker="BTC") -> dict:
    """
    Multi-timeframe technical analysis (#2).
    Fetches 1H and 1D candles alongside the standard 4H.
    Returns trend alignment: 'aligned_up', 'aligned_down', 'mixed', or 'unknown'.
    Higher alignment = higher conviction for the 4H signal.
    """
    import httpx
    pair = _kraken_ohlc_pair(ticker)
    results = {}
    for label, interval, count in [("1h", 60, 50), ("1d", 1440, 30)]:
        try:
            r = httpx.get(
                "https://api.kraken.com/0/public/OHLC",
                params={"pair": pair, "interval": interval, "count": count},
                timeout=10,
            )
            if r.status_code != 200:
                continue
            body = r.json()
            if body.get("error"):
                continue
            keys = list(body["result"].keys())
            if not keys:
                continue
            closes = [float(c[4]) for c in body["result"][keys[0]]]
            if len(closes) < 26:
                continue
            ema20 = _ema(closes, 20)
            ema50 = _ema(closes, 50)
            macd  = round(_ema(closes, 12) - _ema(closes, 26), 2)
            gains, losses = [], []
            for i in range(1, 15):
                d = closes[-i] - closes[-i - 1]
                (gains if d > 0 else losses).append(abs(d))
            avg_g = sum(gains) / 14 if gains else 0
            avg_l = sum(losses) / 14 if losses else 0.001
            rsi = round(100 - 100 / (1 + avg_g / avg_l), 1)
            results[label] = {
                "trend": "bull" if ema20 > ema50 else "bear",
                "macd":  "bull" if macd > 0 else "bear",
                "rsi":   "bull" if rsi < 45 else ("bear" if rsi > 65 else "neutral"),
            }
        except Exception:
            continue

    if len(results) < 2:
        return {"alignment": "unknown", "timeframes": results}

    # Score: how many timeframes agree on direction
    votes = {"bull": 0, "bear": 0}
    for tf_data in results.values():
        for sig_dir in tf_data.values():
            if sig_dir in votes:
                votes[sig_dir] += 1

    total = votes["bull"] + votes["bear"]
    if total == 0:
        alignment = "unknown"
    elif votes["bull"] / total >= 0.70:
        alignment = "aligned_up"
    elif votes["bear"] / total >= 0.70:
        alignment = "aligned_down"
    else:
        alignment = "mixed"

    return {"alignment": alignment, "timeframes": results, "votes": votes}


def fetch_derivatives(ticker="BTC") -> dict:
    import httpx
    sym = _kraken_futures_sym(ticker)
    try:
        r = httpx.get(
            "https://futures.kraken.com/derivatives/api/v3/tickers",
            timeout=10,
        )
        if r.status_code != 200:
            return {}
        t = next((x for x in r.json().get("tickers", []) if x.get("symbol") == sym), None)
        if not t:
            return {}
        fr = float(t.get("fundingRate", 0) or 0)
        oi = float(t.get("openInterest", 0) or 0)
        px = float(t.get("markPrice", 71000) or 71000)
        return {
            "funding_rate": round(fr * 100, 6),
            "open_interest": f"${oi * px / 1e9:.2f}B",
            "high_24h": t.get("high24h", 0),
            "low_24h":  t.get("low24h", 0),
        }
    except Exception:
        return {}


# ── Coinglass data extraction helpers ────────────────────────────────────────

def _fetch_coinglass_compact(ticker: str) -> dict:
    """
    Pull key Coinglass data for a single ticker.
    Returns a compact dict with the numbers that matter for reports.
    Runs within ThreadPoolExecutor — keeps total API calls ≤6.
    """
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    try:
        import octo_coinglass
    except ImportError:
        return {}

    result = {}

    # ── Funding rate (exchange breakdown) ────────────────────────────
    try:
        fr_data = octo_coinglass.funding_rate_exchange(ticker)
        if isinstance(fr_data, list) and fr_data:
            coin_fr = next((c for c in fr_data if c.get("symbol") == ticker), fr_data[0])
            margin_list = coin_fr.get("stablecoin_margin_list", []) if isinstance(coin_fr, dict) else []
            rates = []
            exchanges = []
            for ex in margin_list[:8]:
                try:
                    r = float(ex.get("funding_rate", 0) or 0)
                    rates.append(r)
                    exchanges.append({"name": ex.get("exchange", "?"), "rate": round(r * 100, 4)})
                except (ValueError, TypeError):
                    pass
            if rates:
                avg = sum(rates) / len(rates)
                result["funding_avg"] = round(avg * 100, 4)
                result["funding_dir"] = "LONGS PAY" if avg > 0 else "SHORTS PAY"
                result["funding_exchanges"] = exchanges[:5]
    except Exception:
        pass

    # ── Long/Short ratio ────────────────────────────────────────────
    try:
        ls = octo_coinglass.long_short_ratio(ticker, "4h")
        if isinstance(ls, list) and ls:
            latest = ls[-1]
            long_pct = float(latest.get("global_account_long_percent", 50) or 50)
            short_pct = float(latest.get("global_account_short_percent", 50) or 50)
            result["long_pct"] = round(long_pct, 1)
            result["short_pct"] = round(short_pct, 1)
            result["ls_ratio"] = latest.get("global_account_long_short_ratio", 0)
            result["ls_skew"] = "LONG-HEAVY" if long_pct > 55 else "SHORT-HEAVY" if short_pct > 55 else "BALANCED"
    except Exception:
        pass

    # ── Top traders ratio ───────────────────────────────────────────
    try:
        top = octo_coinglass.top_long_short_ratio(ticker, "4h")
        if isinstance(top, list) and top:
            latest = top[-1]
            result["top_long_pct"] = round(float(latest.get("top_account_long_percent", 50) or 50), 1)
            result["top_short_pct"] = round(float(latest.get("top_account_short_percent", 50) or 50), 1)
            result["top_ratio"] = latest.get("top_account_long_short_ratio", 0)
    except Exception:
        pass

    # ── Taker buy/sell (last 4h bar) ────────────────────────────────
    try:
        taker = octo_coinglass.taker_buy_sell(ticker, "4h")
        if isinstance(taker, list) and taker:
            latest = taker[-1]
            buy = float(latest.get("aggregated_buy_volume_usd", 0) or 0)
            sell = float(latest.get("aggregated_sell_volume_usd", 0) or 0)
            total = buy + sell
            if total > 0:
                result["taker_buy_pct"] = round(buy / total * 100, 0)
                result["taker_vol"] = round(total / 1e6, 0)
                result["taker_flow"] = "BUY PRESSURE" if buy / total > 0.55 else "SELL PRESSURE" if buy / total < 0.45 else "NEUTRAL"
    except Exception:
        pass

    # ── Coins Markets (OI + prices in 1 API call — replaces CoinGecko) ──
    try:
        mkts = octo_coinglass.coins_markets()
        if isinstance(mkts, list) and mkts:
            # Build price map for all major coins (used by market_signal handler)
            prices_map = {}
            for sym in ["BTC", "ETH", "SOL"]:
                c = next((x for x in mkts if x.get("symbol") == sym), None)
                if c:
                    px = float(c.get("current_price", 0) or 0)
                    chg = float(c.get("price_change_percent_24h", 0) or 0)
                    if px > 0:
                        prices_map[sym] = {"price": px, "chg_24h": chg}
            if prices_map:
                result["prices"] = prices_map

            # OI data for the requested ticker
            coin = next((c for c in mkts if c.get("symbol") == ticker), None)
            if coin:
                oi_usd = float(coin.get("open_interest_usd", 0) or 0)
                oi_ratio = float(coin.get("open_interest_market_cap_ratio", 0) or 0)
                oi_chg_24h = float(coin.get("open_interest_change_percent_24h", 0) or 0)
                result["oi_usd"] = round(oi_usd / 1e9, 2)
                result["oi_mcap_ratio"] = round(oi_ratio * 100, 1)
                result["oi_chg_24h"] = round(oi_chg_24h, 1)
    except Exception:
        pass

    # ── Recent liquidations (last 4h bar) ───────────────────────────
    try:
        liq = octo_coinglass.liquidation_history(ticker, "4h")
        if isinstance(liq, list) and liq:
            latest = liq[-1]
            long_liq = float(latest.get("aggregated_long_liquidation_usd", 0) or 0)
            short_liq = float(latest.get("aggregated_short_liquidation_usd", 0) or 0)
            total = long_liq + short_liq
            result["liq_long"] = round(long_liq / 1e6, 1)
            result["liq_short"] = round(short_liq / 1e6, 1)
            result["liq_total"] = round(total / 1e6, 1)
            result["liq_pain"] = "LONG PAIN" if long_liq > short_liq else "SHORT PAIN" if short_liq > long_liq else "EVEN"
    except Exception:
        pass

    # ── OKX fallback — fills funding + OI when Coinglass is 401 ─────
    _okx_map = {"BTC": "BTC-USD-SWAP", "ETH": "ETH-USD-SWAP", "SOL": "SOL-USD-SWAP"}
    if ticker in _okx_map and not result.get("funding_avg"):
        try:
            import httpx as _hx
            inst = _okx_map[ticker]
            # Funding rate
            fr = _hx.get(f"https://www.okx.com/api/v5/public/funding-rate?instId={inst}", timeout=6).json()
            fr_data = fr.get("data", [{}])
            if fr_data:
                rate = float(fr_data[0].get("fundingRate", 0) or 0)
                result["funding_avg"] = round(rate * 100, 4)
                result["funding_dir"] = "LONGS PAY" if rate > 0 else "SHORTS PAY"
                result["funding_source"] = "OKX"
            # Open interest
            oi = _hx.get(f"https://www.okx.com/api/v5/public/open-interest?instType=SWAP&instId={inst}", timeout=6).json()
            oi_data = oi.get("data", [{}])
            if oi_data:
                oi_usd = float(oi_data[0].get("oiUsd", 0) or 0)
                result["oi_usd"] = round(oi_usd / 1e9, 2)
                result["oi_source"] = "OKX"
        except Exception as _okxe:
            pass

    return result


# ── Upgraded directional call — 11 signals ───────────────────────────────────

def directional_call(ticker, price, chg_24h, ta, deriv, fng, cg=None, delta=None, tv=None) -> str:
    """
    Oracle directional scoring engine.
    v5: 13 signals. TradingView 1h+4h technical consensus adds Signal 13:
      - Aggregate funding rate direction
      - Long/short ratio extremes
      - Top trader positioning
      - Taker flow direction
      - Recent liquidation skew
      - Binance 24h buy/sell delta (NEW)
    """
    ta    = ta or {}
    deriv = deriv or {}
    cg    = cg or {}

    if not ta and not cg:
        return f"DIRECTION: Insufficient data for {ticker}."

    # TA signals (original 6)
    rsi  = float(ta.get("rsi", 50) or 50)
    macd = float(ta.get("macd", 0) or 0)
    e20  = float(ta.get("ema20", 0) or 0)
    e50  = float(ta.get("ema50", 0) or 0)
    bb_w = float(ta.get("bb_width", 5) or 5)
    fr   = float(deriv.get("funding_rate", 0) or 0)

    bull = bear = 0

    # Signal 1: MACD
    if macd > 0:       bull += 1
    else:              bear += 1
    # Signal 2: EMA trend
    if e20 > e50:      bull += 1
    else:              bear += 1
    # Signal 3: RSI
    if rsi < 45:       bull += 1
    elif rsi > 65:     bear += 1
    # Signal 4: Fear & Greed
    if fng < 25:       bull += 1
    elif fng > 75:     bear += 1
    # Signal 5: Kraken funding rate
    if fr < 0:         bull += 1
    elif fr > 0.005:   bear += 1
    # Signal 6: 24h price change
    if chg_24h > 2:    bull += 1
    elif chg_24h < -2: bear += 1

    # Coinglass signals (new 5)
    # Signal 7: Aggregate funding rate
    cg_fr = cg.get("funding_avg", 0) or 0
    if cg_fr < -0.005:   bull += 1  # Shorts paying = bullish
    elif cg_fr > 0.01:   bear += 1  # Longs paying = bearish

    # Signal 8: Long/short ratio (contrarian)
    long_pct = cg.get("long_pct", 50) or 50
    if long_pct > 65:    bear += 1  # Too many longs = bearish
    elif long_pct < 40:  bull += 1  # Too many shorts = bullish

    # Signal 9: Top trader positioning (follow the whales)
    top_long = cg.get("top_long_pct", 50) or 50
    if top_long > 55:    bull += 1  # Whales are long = bullish
    elif top_long < 45:  bear += 1  # Whales are short = bearish

    # Signal 10: Taker flow
    taker_buy = cg.get("taker_buy_pct", 50) or 50
    if taker_buy > 55:   bull += 1  # Aggressive buying
    elif taker_buy < 45: bear += 1  # Aggressive selling

    # Signal 11: Liquidation skew (contrarian — pain creates opportunity)
    liq_long = cg.get("liq_long", 0) or 0
    liq_short = cg.get("liq_short", 0) or 0
    if liq_long > liq_short * 2:   bull += 1  # Longs flushed = bounce likely
    elif liq_short > liq_long * 2: bear += 1  # Shorts flushed = dip likely

    # Signal 12: Binance 24h cumulative buy/sell delta (real order flow)
    # score +1 = buyers dominating, -1 = sellers dominating, 0 = neutral
    if delta and isinstance(delta, dict):
        d_score = delta.get("score", 0)
        d_accel = delta.get("acceleration", "STEADY")
        # Only count accelerating or steady delta — decelerating = fading signal
        if d_score > 0 and d_accel != "DECELERATING":
            bull += 1
        elif d_score < 0 and d_accel != "DECELERATING":
            bear += 1

    # Signal 13: TradingView 1h+4h technical consensus (26 indicators)
    # Counts only when both timeframes agree — divergence = neutral
    if tv and isinstance(tv, dict):
        tv_vote = tv.get("vote", 0)
        tv_conf = tv.get("confidence", "neutral")
        tv_agree = tv.get("agreement", False)
        if tv_vote > 0 and tv_agree:
            bull += 1  # Both TFs bullish
        elif tv_vote < 0 and tv_agree:
            bear += 1  # Both TFs bearish
        elif tv_vote > 0 and tv_conf == "strong":
            bull += 1  # 1h strong enough even without 4h agreement
        elif tv_vote < 0 and tv_conf == "strong":
            bear += 1

    total = bull + bear
    p = f"${price:,.0f}" if price else "current level"

    if bb_w < 3.0:
        d = "UP" if bull > bear else "DOWN"
        return f"DIRECTION: BREAKOUT IMMINENT — BB compressed to {bb_w}%. {bull}/{total} bullish. Resolving {d}."
    elif bull >= 9:
        return f"DIRECTION: STRONG UP — {bull}/{total} signals bullish. High-conviction long setup."
    elif bull >= 7:
        return f"DIRECTION: UP — {bull}/{total} signals bullish. {ticker} likely continues higher."
    elif bear >= 9:
        return f"DIRECTION: STRONG DOWN — {bear}/{total} signals bearish. High-conviction short setup."
    elif bear >= 7:
        return f"DIRECTION: DOWN — {bear}/{total} signals bearish. {ticker} under pressure."
    elif bull == bear:
        return f"DIRECTION: RANGE — {bull}/{total} signals each way. {ticker} range-bound near {p}."
    elif bull > bear:
        return f"DIRECTION: LEANING UP — {bull}/{total} bullish. Mild upside bias."
    else:
        return f"DIRECTION: LEANING DOWN — {bear}/{total} bearish. Facing resistance near {p}."


# ── Valid tickers ─────────────────────────────────────────────────────────────

VALID_CRYPTO  = {"BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "AVAX", "LINK", "ADA", "DOT"}
VALID_STOCKS  = {"NVDA", "TSLA", "AAPL", "MSFT", "AMZN", "META", "GOOGL", "SPY", "QQQ"}
VALID_TICKERS = VALID_CRYPTO | VALID_STOCKS


# ── Report Handlers ───────────────────────────────────────────────────────────

def handle_crypto_market_signal(req: dict) -> dict:
    import httpx
    import sys
    import os
    from concurrent.futures import ThreadPoolExecutor
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import octo_pulse
    import octo_gecko
    import octo_fx

    raw = str(req.get("ticker", "") or "").strip().upper()
    if not raw or raw not in VALID_CRYPTO:
        return {"reject": True, "error": f"Invalid or empty ticker: '{raw}'. Valid: {sorted(VALID_CRYPTO)}"}
    ticker = raw

    with ThreadPoolExecutor(max_workers=6) as ex:
        f_pulse = ex.submit(octo_pulse.run_pulse_scan)
        f_gecko = ex.submit(octo_gecko.run_gecko_scan)
        f_fx    = ex.submit(octo_fx.run_fx_scan if hasattr(octo_fx, "run_fx_scan") else dict)
        f_ta    = ex.submit(fetch_technicals, ticker)
        f_deriv = ex.submit(fetch_derivatives, ticker)
        f_cg    = ex.submit(_fetch_coinglass_compact, ticker)
        try:
            pulse = f_pulse.result(timeout=30) or {}
        except Exception:
            pulse = {}
        try:
            gecko = f_gecko.result(timeout=30) or {}
        except Exception:
            gecko = {}
        try:
            fx = f_fx.result(timeout=15) or {}
        except Exception:
            fx = {}
        try:
            ta = f_ta.result(timeout=15) or {}
        except Exception:
            ta = {}
        try:
            deriv = f_deriv.result(timeout=15) or {}
        except Exception:
            deriv = {}
        try:
            cg = f_cg.result(timeout=30) or {}
        except Exception:
            cg = {}

    # Safe None-proof access
    fng      = (pulse.get("fear_greed") or {})
    fng_val  = int(fng.get("value", 50) or 50)
    fng_label = fng.get("label", "N/A") or "N/A"

    gecko_global = (gecko.get("global") or {})
    btc_dom = gecko_global.get("btc_dominance", gecko.get("btc_dominance", "N/A"))

    btc_p = eth_p = sol_p = "N/A"
    btc_c = eth_c = sol_c = 0.0

    # Primary: Coinglass coins_markets (already fetched, no extra API call)
    cg_prices = cg.get("prices", {})
    if cg_prices.get("BTC"):
        btc_p = f"${cg_prices['BTC']['price']:,.0f}"
        btc_c = cg_prices['BTC'].get('chg_24h', 0)
    if cg_prices.get("ETH"):
        eth_p = f"${cg_prices['ETH']['price']:,.0f}"
        eth_c = cg_prices['ETH'].get('chg_24h', 0)
    if cg_prices.get("SOL"):
        sol_p = f"${cg_prices['SOL']['price']:,.2f}"
        sol_c = cg_prices['SOL'].get('chg_24h', 0)

    # Fallback: CoinGecko REST (only if Coinglass didn't provide prices)
    if btc_p == "N/A":
        try:
            r = httpx.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": "bitcoin,ethereum,solana", "vs_currencies": "usd", "include_24hr_change": "true"},
                timeout=10,
            )
            if r.status_code == 200:
                d = r.json()
                btc_p = f"${d['bitcoin']['usd']:,.0f}"
                btc_c = float(d['bitcoin'].get('usd_24h_change', 0) or 0)
                eth_p = f"${d['ethereum']['usd']:,.0f}"
                eth_c = float(d['ethereum'].get('usd_24h_change', 0) or 0)
                sol_p = f"${d['solana']['usd']:,.2f}"
                sol_c = float(d['solana'].get('usd_24h_change', 0) or 0)
        except Exception:
            pass

    fx_pairs = (fx.get("key_pairs") or {})
    usd_eur  = (fx_pairs.get("EUR") or {}).get("rate", "N/A")
    usd_jpy  = (fx_pairs.get("JPY") or {}).get("rate", "N/A")

    momentum = "N/A"
    if ta:
        rsi  = float(ta.get("rsi", 50) or 50)
        macd = float(ta.get("macd", 0) or 0)
        e20  = float(ta.get("ema20", 0) or 0)
        e50  = float(ta.get("ema50", 0) or 0)
        if rsi > 70:            momentum = "Overbought"
        elif rsi < 30:          momentum = "Oversold"
        elif macd > 0 and e20 > e50: momentum = "Leaning Bullish"
        elif macd < 0 and e20 < e50: momentum = "Leaning Bearish"
        else:                   momentum = "Consolidating"

    if fng_val < 20:   signal = "ACCUMULATE — Extreme fear historically precedes recovery."
    elif fng_val < 40: signal = "CAUTIOUS BUY — Fear present. Scale in carefully."
    elif fng_val < 60: signal = "NEUTRAL — Hold. Wait for directional confirmation."
    elif fng_val < 80: signal = "REDUCE — Greed elevated. Consider partial profits."
    else:              signal = "EXIT RISK — Extreme greed. High correction probability."

    btc_num = 0.0
    try:
        btc_num = float(btc_p.replace("$", "").replace(",", "")) if btc_p != "N/A" else 0.0
    except Exception:
        pass

    call = directional_call(ticker, btc_num, btc_c, ta, deriv, fng_val, cg)

    return {
        "type":      "market_signal",
        "ticker":    ticker,
        "title":     "OCTODAMUS MARKET ORACLE BRIEFING",
        "generated": datetime.utcnow().strftime("%a, %b %d, %Y"),
        "prices":    {
            "BTC": {"price": btc_p, "chg": btc_c},
            "ETH": {"price": eth_p, "chg": eth_c},
            "SOL": {"price": sol_p, "chg": sol_c},
        },
        "btc_dom":   btc_dom,
        "momentum":  momentum,
        "ta":        ta,
        "deriv":     deriv,
        "cg":        cg,
        "fng_val":   fng_val,
        "fng_label": fng_label,
        "usd_eur":   usd_eur,
        "usd_jpy":   usd_jpy,
        "signal":    signal,
        "call":      call,
    }


def handle_fear_greed(req: dict) -> dict:
    import sys
    import os
    from concurrent.futures import ThreadPoolExecutor
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import octo_pulse

    raw = str(req.get("ticker", "") or "").strip().upper()
    # fear_greed is a market-wide signal -- ticker is optional, default BTC
    if not raw or raw not in VALID_CRYPTO:
        raw = "BTC"

    with ThreadPoolExecutor(max_workers=4) as ex:
        f_pulse = ex.submit(octo_pulse.run_pulse_scan)
        f_ta    = ex.submit(fetch_technicals, raw)
        f_deriv = ex.submit(fetch_derivatives, raw)
        f_cg    = ex.submit(_fetch_coinglass_compact, raw)
        try:
            pulse = f_pulse.result(timeout=30) or {}
        except Exception:
            pulse = {}
        try:
            ta = f_ta.result(timeout=15) or {}
        except Exception:
            ta = {}
        try:
            deriv = f_deriv.result(timeout=15) or {}
        except Exception:
            deriv = {}
        try:
            cg = f_cg.result(timeout=30) or {}
        except Exception:
            cg = {}

    fng       = (pulse.get("fear_greed") or {})
    val       = int(fng.get("value", 50) or 50)
    label     = fng.get("label", "N/A") or "N/A"
    wiki      = (pulse.get("wikipedia") or {})
    spikes    = wiki.get("spikes", [])[:3] if wiki else []

    if val < 20:   pos, ctx = "STRONG BUY",        "Capitulation zone. Best entry for 30-90 day holds."
    elif val < 40: pos, ctx = "CAUTIOUS BUY",       "Fear elevated. Smart money accumulating quietly."
    elif val < 60: pos, ctx = "HOLD",               "Market at equilibrium. Wait for extremes."
    elif val < 80: pos, ctx = "REDUCE EXPOSURE",    "Retail FOMO increasing. Trim profits."
    else:          pos, ctx = "EXIT",               "Everyone is bullish. That is the signal to be cautious."

    call = directional_call(raw, 0, 0, ta, deriv, val, cg)

    return {
        "type":      "fear_greed",
        "ticker":    raw,
        "title":     "OCTODAMUS FEAR & GREED SENTIMENT READ",
        "generated": datetime.utcnow().strftime("%a, %b %d, %Y"),
        "fng_val":   val,
        "fng_label": label,
        "position":  pos,
        "context":   ctx,
        "spikes":    spikes,
        "ta":        ta,
        "deriv":     deriv,
        "cg":        cg,
        "call":      call,
    }


def handle_bitcoin_analysis(req: dict) -> dict:
    import httpx
    import sys
    import os
    from concurrent.futures import ThreadPoolExecutor
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import octo_pulse

    raw = str(req.get("ticker", "") or "").strip().upper()
    if not raw or raw not in VALID_CRYPTO:
        return {"reject": True, "error": f"Invalid or empty ticker: '{raw}'. Valid crypto: {sorted(VALID_CRYPTO)}"}
    ticker    = raw
    timeframe = str(req.get("timeframe", "4h") or "4h")
    cg_map    = {
        "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
        "BNB": "binancecoin", "XRP": "ripple", "DOGE": "dogecoin",
    }
    cg_id = cg_map.get(ticker, ticker.lower())

    with ThreadPoolExecutor(max_workers=4) as ex:
        f_pulse = ex.submit(octo_pulse.run_pulse_scan)
        f_ta    = ex.submit(fetch_technicals, ticker)
        f_deriv = ex.submit(fetch_derivatives, ticker)
        f_cg    = ex.submit(_fetch_coinglass_compact, ticker)
        try:
            pulse = f_pulse.result(timeout=30) or {}
        except Exception:
            pulse = {}
        try:
            ta = f_ta.result(timeout=15) or {}
        except Exception:
            ta = {}
        try:
            deriv = f_deriv.result(timeout=15) or {}
        except Exception:
            deriv = {}
        try:
            cg = f_cg.result(timeout=30) or {}
        except Exception:
            cg = {}

    fng_val   = int((pulse.get("fear_greed") or {}).get("value", 50) or 50)
    fng_label = (pulse.get("fear_greed") or {}).get("label", "N/A") or "N/A"

    price = chg_24h = chg_7d = chg_30d = ath = ath_pct = 0.0
    mcap = vol = high_24h = low_24h = circ = max_sup = 0.0
    try:
        r = httpx.get(
            f"https://api.coingecko.com/api/v3/coins/{cg_id}",
            params={"localization": "false", "tickers": "false", "community_data": "false"},
            timeout=12,
        )
        if r.status_code == 200:
            md       = r.json().get("market_data") or {}
            price    = float((md.get("current_price") or {}).get("usd", 0) or 0)
            chg_24h  = float(md.get("price_change_percentage_24h", 0) or 0)
            chg_7d   = float(md.get("price_change_percentage_7d", 0) or 0)
            chg_30d  = float(md.get("price_change_percentage_30d", 0) or 0)
            ath      = float((md.get("ath") or {}).get("usd", 0) or 0)
            ath_pct  = float((md.get("ath_change_percentage") or {}).get("usd", 0) or 0)
            mcap     = float((md.get("market_cap") or {}).get("usd", 0) or 0)
            vol      = float((md.get("total_volume") or {}).get("usd", 0) or 0)
            high_24h = float((md.get("high_24h") or {}).get("usd", 0) or 0)
            low_24h  = float((md.get("low_24h") or {}).get("usd", 0) or 0)
            circ     = float(md.get("circulating_supply", 0) or 0)
            max_sup  = float(md.get("max_supply", 0) or 0)
    except Exception:
        pass

    momentum = "N/A"
    if ta:
        rsi  = float(ta.get("rsi", 50) or 50)
        macd = float(ta.get("macd", 0) or 0)
        e20  = float(ta.get("ema20", 0) or 0)
        e50  = float(ta.get("ema50", 0) or 0)
        if rsi > 70:            momentum = "Overbought"
        elif rsi < 30:          momentum = "Oversold"
        elif macd > 0 and e20 > e50: momentum = "Leaning Bullish"
        elif macd < 0 and e20 < e50: momentum = "Leaning Bearish"
        else:                   momentum = "Consolidating"

    call = directional_call(ticker, price, chg_24h, ta, deriv, fng_val, cg)

    return {
        "type":      "bitcoin_analysis",
        "ticker":    ticker,
        "title":     f"OCTODAMUS {ticker} DEEP DIVE",
        "generated": datetime.utcnow().strftime("%a, %b %d, %Y"),
        "timeframe": timeframe,
        "price":     price,
        "chg_24h":   chg_24h,
        "chg_7d":    chg_7d,
        "chg_30d":   chg_30d,
        "high_24h":  high_24h,
        "low_24h":   low_24h,
        "ath":       ath,
        "ath_pct":   ath_pct,
        "mcap":      mcap,
        "vol":       vol,
        "circ":      circ,
        "max_sup":   max_sup,
        "momentum":  momentum,
        "fng_val":   fng_val,
        "fng_label": fng_label,
        "ta":        ta,
        "deriv":     deriv,
        "cg":        cg,
        "call":      call,
    }


def handle_congressional(req: dict) -> dict:
    import sys
    import os
    from datetime import timedelta
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import octo_pulse

    raw = str(req.get("ticker", "") or "").strip().upper()
    if not raw or len(raw) > 10 or not raw.isalpha():
        return {"reject": True, "error": f"Invalid or empty ticker: '{raw}'. Provide a valid stock ticker symbol."}
    ticker = raw

    try:
        import quiverquant
        token = os.environ.get("QUIVER_API_KEY", "")
        if not token:
            for kp in [
                "/home/walli/octodamus/octo_quiver_key.txt",
                r"C:\Users\walli\octodamus\octo_quiver_key.txt",
            ]:
                try:
                    import pathlib
                    t = pathlib.Path(kp).read_text().strip()
                    if t:
                        token = t
                        os.environ["QUIVER_API_KEY"] = t
                        break
                except Exception:
                    pass

        if not token:
            return {
                "type": "congressional", "ticker": ticker,
                "title": "CONGRESSIONAL TRADE REPORT",
                "generated": datetime.utcnow().strftime("%a, %b %d, %Y"),
                "error": "QUIVER_API_KEY unavailable", "trades": [],
                "buys": 0, "sells": 0, "fng_val": 50, "fng_label": "N/A",
                "interpretation": "Data unavailable.", "call": "DIRECTION: UNKNOWN",
                "period": "N/A",
            }

        import octo_finnhub
        from concurrent.futures import ThreadPoolExecutor
        quiver = quiverquant.quiver(token)
        with ThreadPoolExecutor(max_workers=3) as ex:
            f_df      = ex.submit(quiver.congress_trading, ticker)
            f_pulse   = ex.submit(octo_pulse.run_pulse_scan)
            f_finnhub = ex.submit(octo_finnhub.get_finnhub_context, ticker)
            try:
                df = f_df.result(timeout=30)
            except Exception:
                df = None
            try:
                pulse = f_pulse.result(timeout=30) or {}
            except Exception:
                pulse = {}
            try:
                finnhub_context = f_finnhub.result(timeout=15) or ""
            except Exception:
                finnhub_context = ""

        fng_val   = int((pulse.get("fear_greed") or {}).get("value", 50) or 50)
        fng_label = (pulse.get("fear_greed") or {}).get("label", "N/A") or "N/A"

        if df is None or df.empty:
            return {
                "type": "congressional", "ticker": ticker,
                "title": "CONGRESSIONAL TRADE REPORT",
                "generated": datetime.utcnow().strftime("%a, %b %d, %Y"),
                "trades": [], "buys": 0, "sells": 0,
                "period": "Last 45 days", "fng_val": fng_val, "fng_label": fng_label,
                "interpretation": f"No recent congressional trades found for {ticker}.",
                "call": "DIRECTION: UNKNOWN",
            }

        cutoff_r = datetime.now() - timedelta(days=45)
        cutoff_h = datetime.now() - timedelta(days=730)
        df["TransactionDate"] = df["TransactionDate"].apply(
            lambda x: x if hasattr(x, "year") else datetime.strptime(str(x)[:10], "%Y-%m-%d")
        )
        recent = df[df["TransactionDate"] >= cutoff_r]
        period_label = "Last 45 days"
        if recent.empty:
            recent = df[df["TransactionDate"] >= cutoff_h].head(10)
            period_label = "2-year history"

        trades = []
        buys = sells = 0
        for _, row in recent.iterrows():
            name      = str(row.get("Representative", "Unknown") or "Unknown")
            party     = str(row.get("Party", "") or "")
            p_tag     = "R" if "republican" in party.lower() else "D" if "democrat" in party.lower() else "?"
            tx        = str(row.get("Transaction", "") or "").lower()
            direction = "BUY" if "purchase" in tx or "buy" in tx else "SELL"
            amount    = str(row.get("Range", row.get("Amount", "N/A")) or "N/A")
            date_str  = str(row.get("TransactionDate", "") or "")[:10]
            if direction == "BUY":
                buys += 1
            else:
                sells += 1
            trades.append({"name": name, "party": p_tag, "direction": direction, "amount": amount, "date": date_str})

        if buys > sells:
            interpretation = f"Net congressional BUYING on {ticker}. Insiders accumulating — something favorable may be coming."
            call = "DIRECTION: LEANING UP"
        elif sells > buys:
            interpretation = f"Net congressional SELLING on {ticker}. Politicians dumping — watch for regulatory or earnings risk."
            call = "DIRECTION: LEANING DOWN"
        else:
            interpretation = f"Mixed congressional activity on {ticker}. No clear directional signal."
            call = "DIRECTION: RANGE"

        return {
            "type":             "congressional",
            "ticker":           ticker,
            "title":            "CONGRESSIONAL TRADE REPORT",
            "subtitle":         "OCTODAMUS CONGRESSIONAL TRADE ALERT",
            "generated":        datetime.utcnow().strftime("%a, %b %d, %Y"),
            "period":           period_label,
            "trades":           trades,
            "buys":             buys,
            "sells":            sells,
            "interpretation":   interpretation,
            "call":             call,
            "fng_val":          fng_val,
            "fng_label":        fng_label,
            "finnhub_context":  finnhub_context,
        }

    except Exception as e:
        return {
            "type": "congressional", "ticker": ticker,
            "title": "CONGRESSIONAL TRADE REPORT",
            "generated": datetime.utcnow().strftime("%a, %b %d, %Y"),
            "error": str(e), "trades": [], "buys": 0, "sells": 0,
            "period": "N/A", "fng_val": 50, "fng_label": "N/A",
            "interpretation": f"Error: {e}", "call": "DIRECTION: UNKNOWN",
        }


# ── Agent Report Handlers ─────────────────────────────────────────────────────

def handle_signal_pack(req: dict) -> dict:
    """Oracle call record + open signals — structured for agent consumption."""
    import json
    from pathlib import Path
    base = Path(__file__).parent

    calls = []
    try:
        calls = json.loads((base / "data" / "octo_calls.json").read_text(encoding="utf-8"))
    except Exception:
        pass

    resolved  = [c for c in calls if c.get("resolved")]
    open_calls = [c for c in calls if not c.get("resolved")]
    wins   = sum(1 for c in resolved if c.get("outcome") == "WIN")
    losses = sum(1 for c in resolved if c.get("outcome") == "LOSS")
    rate   = round(wins / (wins + losses) * 100) if (wins + losses) > 0 else None

    # Latest closed call
    last = resolved[-1] if resolved else {}

    return {
        "type":        "signal_pack",
        "wins":        wins,
        "losses":      losses,
        "win_rate":    rate,
        "total":       len(calls),
        "open_calls":  open_calls,
        "last_call":   last,
        "methodology": "5+ of 11 signals required. Funding rate, OI, L/S ratio, technicals, taker flow.",
        "footer":      FOOTER,
    }


def handle_polymarket_alpha(req: dict) -> dict:
    """OctoBoto open positions and track record."""
    import json
    from pathlib import Path
    base = Path(__file__).parent

    data = {}
    try:
        data = json.loads((base / "octo_boto_trades.json").read_text(encoding="utf-8"))
    except Exception:
        pass

    positions = data.get("positions", [])
    closed    = data.get("closed", [])
    balance   = data.get("balance", 500.0)
    starting  = data.get("starting_balance", 500.0)
    wins   = [t for t in closed if t.get("won")]
    losses = [t for t in closed if not t.get("won")]
    rate   = round(len(wins) / len(closed) * 100, 1) if closed else None

    return {
        "type":        "polymarket_alpha",
        "balance":     round(balance, 2),
        "pnl":         round(balance - starting, 2),
        "wins":        len(wins),
        "losses":      len(losses),
        "win_rate":    rate,
        "closed_count": len(closed),
        "positions":   positions,
        "methodology": "Kelly sizing. EV > 7%. AI probability vs Polymarket price divergence.",
        "footer":      FOOTER,
    }


def handle_conviction_score(req: dict) -> dict:
    """Per-asset bull/bear conviction score from Oracle call history."""
    import json
    from pathlib import Path
    base = Path(__file__).parent

    calls = []
    try:
        calls = json.loads((base / "data" / "octo_calls.json").read_text(encoding="utf-8"))
    except Exception:
        pass

    asset_data: dict = {}
    for c in calls:
        asset = c.get("asset", "").upper()
        if not asset:
            continue
        if asset not in asset_data:
            asset_data[asset] = {"wins": 0, "losses": 0, "open_direction": None, "open_call": None}
        if not c.get("resolved"):
            asset_data[asset]["open_direction"] = c.get("direction", "")
            asset_data[asset]["open_call"] = c
        elif c.get("outcome") == "WIN":
            asset_data[asset]["wins"] += 1
        elif c.get("outcome") == "LOSS":
            asset_data[asset]["losses"] += 1

    scores = {}
    for asset, d in asset_data.items():
        total = d["wins"] + d["losses"]
        base_score = round(d["wins"] / total * 100) if total > 0 else 50
        if d["open_direction"] == "UP":
            base_score = min(100, base_score + 10)
        elif d["open_direction"] == "DOWN":
            base_score = max(0, base_score - 10)
        scores[asset] = {
            "score":          base_score,
            "bias":           "BULLISH" if base_score > 60 else ("BEARISH" if base_score < 40 else "NEUTRAL"),
            "open_direction": d["open_direction"],
            "record":         f"{d['wins']}W / {d['losses']}L",
            "open_call":      d["open_call"],
        }

    return {
        "type":        "conviction_score",
        "scores":      scores,
        "scale":       "0 = max bearish · 50 = neutral · 100 = max bullish",
        "methodology": "Oracle call win rate + open signal direction bias.",
        "footer":      FOOTER,
    }


# ── Ask handler — routes agent questions to /v2/ask ──────────────────────────

def handle_ask(req: dict) -> dict:
    """
    Answer a free-form market question via /v2/ask.
    Expects req["question"] or req["q"] — falls back to req["ticker"] context.
    """
    import httpx

    question = (
        req.get("question") or
        req.get("q") or
        req.get("query") or
        f"What is your current read on {req.get('ticker', 'BTC')}?"
    )

    try:
        r = httpx.post(
            "https://api.octodamus.com/v2/ask",
            params={"q": question},
            timeout=30,
        )
        if r.status_code == 200:
            body = r.json()
            return {
                "type":     "ask",
                "question": question,
                "answer":   body.get("answer", ""),
                "suggested_endpoints": body.get("suggested_endpoints", []),
                "footer":   FOOTER,
            }
        else:
            return {
                "type":   "ask",
                "question": question,
                "error":  f"Ask endpoint returned {r.status_code}",
                "footer": FOOTER,
            }
    except Exception as e:
        return {
            "type":   "ask",
            "question": question,
            "error":  str(e),
            "footer": FOOTER,
        }


# ── Smithery Agent Onboarding Brief ─────────────────────────────────────────

def handle_smithery_onboarding_brief(req: dict) -> dict:
    """Quick-start packet for agents discovering Octodamus via Smithery MCP."""
    import json
    from pathlib import Path
    base = Path(__file__).parent

    calls = []
    try:
        calls = json.loads((base / "data" / "octo_calls.json").read_text(encoding="utf-8"))
    except Exception:
        pass

    open_calls = [c for c in calls if not c.get("resolved")]
    resolved = [c for c in calls if c.get("resolved")]
    wins = sum(1 for c in resolved if c.get("outcome") == "WIN")
    losses = sum(1 for c in resolved if c.get("outcome") == "LOSS")
    win_rate = round(wins / (wins + losses) * 100) if (wins + losses) > 0 else None
    sample = open_calls[-1] if open_calls else (resolved[-1] if resolved else {})

    tools_guide = [
        {"tool": "get_agent_signal",   "purpose": "Oracle signal composite (BTC/ETH/SOL)",              "cadence": "Every 4-6h"},
        {"tool": "get_polymarket_edge", "purpose": "OctoBoto open positions + Polymarket win/loss record", "cadence": "Before Polymarket entry"},
        {"tool": "get_sentiment",       "purpose": "Fear & Greed + derivatives positioning snapshot",      "cadence": "Every 4h"},
        {"tool": "get_prices",          "purpose": "Live BTC/ETH/SOL prices via Kraken",                  "cadence": "On demand"},
        {"tool": "get_market_brief",    "purpose": "Full oracle briefing: price + technicals + futures",   "cadence": "Session open + evening"},
        {"tool": "get_all_data",        "purpose": "All signals in one call",                              "cadence": "Session open"},
        {"tool": "get_oracle_signals",  "purpose": "Raw oracle call history + open positions",             "cadence": "Once daily"},
        {"tool": "get_data_sources",    "purpose": "List active data sources and their status",            "cadence": "On error"},
    ]

    return {
        "type":         "smithery_onboarding",
        "title":        "OCTODAMUS -- AGENT QUICK-START GUIDE",
        "generated":    datetime.utcnow().strftime("%a, %b %d, %Y %H:%M UTC"),
        "tools_guide":  tools_guide,
        "api_key_url":  "https://api.octodamus.com/v1/signup",
        "mcp_server":   "octodamusai/market-intelligence",
        "sample_signal": sample,
        "oracle_record": f"{wins}W / {losses}L" + (f" ({win_rate}% win rate)" if win_rate else ""),
        "recommended_cadence": "Call get_market_brief at session open. get_agent_signal every 4h. get_polymarket_edge before any prediction market trade.",
        "quick_start_snippet": 'result = use_mcp_tool("octodamusai/market-intelligence", "get_market_brief", {"ticker": "BTC"})',
        "pricing": {
            "x402_signal": "$1.00 — api.octodamus.com/v2/signal",
            "acp_reports": "$1-2 USDC/job via ACP",
            "annual_api":  "$29/year (first 100 seats) — api.octodamus.com/v1/signup",
        },
        "footer": FOOTER,
    }


# ── Overnight / Asia Session Brief ──────────────────────────────────────────

def handle_overnight_brief(req: dict) -> dict:
    """Pre-packaged macro brief for agents operating during Asia/overnight hours."""
    import json
    from pathlib import Path
    from concurrent.futures import ThreadPoolExecutor
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import octo_pulse

    base = Path(__file__).parent

    with ThreadPoolExecutor(max_workers=3) as ex:
        f_pulse = ex.submit(octo_pulse.run_pulse_scan)
        f_ta    = ex.submit(fetch_technicals, "BTC")
        f_cg    = ex.submit(_fetch_coinglass_compact, "BTC")
        try:
            pulse = f_pulse.result(timeout=30) or {}
        except Exception:
            pulse = {}
        try:
            ta = f_ta.result(timeout=15) or {}
        except Exception:
            ta = {}
        try:
            cg = f_cg.result(timeout=30) or {}
        except Exception:
            cg = {}

    fng_val   = int((pulse.get("fear_greed") or {}).get("value", 50) or 50)
    fng_label = (pulse.get("fear_greed") or {}).get("label", "N/A") or "N/A"

    cg_prices = cg.get("prices", {})
    btc_price = float((cg_prices.get("BTC") or {}).get("price", 0) or 0)
    btc_chg   = float((cg_prices.get("BTC") or {}).get("chg_24h", 0) or 0)

    calls = []
    try:
        calls = json.loads((base / "data" / "octo_calls.json").read_text(encoding="utf-8"))
    except Exception:
        pass
    open_calls = [c for c in calls if not c.get("resolved")]
    if open_calls:
        latest = open_calls[-1]
        oracle_signal = {
            "action":     latest.get("direction", "HOLD"),
            "asset":      latest.get("asset", "BTC"),
            "confidence": latest.get("confidence", 0),
            "reasoning":  (latest.get("reasoning") or "")[:200],
        }
    else:
        oracle_signal = {"action": "NO CALL", "asset": "BTC", "confidence": 0, "reasoning": "No open oracle calls."}

    top_edge = None
    try:
        trades_data = json.loads((base / "octo_boto_trades.json").read_text(encoding="utf-8"))
        positions = trades_data.get("positions", [])
        if positions:
            top_edge = positions[0]
    except Exception:
        pass

    action = oracle_signal["action"]
    if action in ("UP", "BUY", "LONG") and fng_val < 50:
        action_summary = "Oracle bullish + market in fear. High-conviction setup -- review oracle call and check Polymarket edges."
    elif action in ("DOWN", "SELL", "SHORT") and fng_val > 50:
        action_summary = "Oracle bearish + greed elevated. Fade-the-crowd setup -- potential short entry on bounces."
    elif action == "NO CALL":
        action_summary = "No active oracle call. Cash is a position. Wait for signal before entering any market."
    else:
        action_summary = f"Mixed signals. Oracle: {action}. F&G: {fng_val}. No high-conviction entry -- wait for convergence."

    now = datetime.utcnow()
    hour = now.hour
    if 0 <= hour < 8:       session = "Asia"
    elif 8 <= hour < 13:    session = "US Pre-Market"
    elif 13 <= hour < 21:   session = "US Market"
    else:                   session = "London/Asia"

    return {
        "type":      "overnight_brief",
        "title":     "OCTODAMUS OVERNIGHT / ASIA SESSION BRIEF",
        "generated": now.strftime("%a, %b %d, %Y %H:%M UTC"),
        "session":   session,
        "btc_price": round(btc_price, 2),
        "btc_chg_24h": round(btc_chg, 2),
        "fear_greed": {"value": fng_val, "label": fng_label},
        "futures_snapshot": {
            "funding_avg": cg.get("funding_avg"),
            "long_pct":    cg.get("long_pct"),
            "short_pct":   cg.get("short_pct"),
            "ls_skew":     cg.get("ls_skew"),
        },
        "oracle_signal":  oracle_signal,
        "top_edge":       top_edge,
        "action_summary": action_summary,
        "footer":         FOOTER,
    }


def handle_agent_market_intel_bundle(req: dict) -> dict:
    """
    Agent Market Intel Bundle — $2 ACP job.
    One-call structured JSON bundle for AI agent decision loops.
    Designed by Agent_Ben (Session #23, April 28 2026).
    """
    import json as _json, pathlib as _pl
    pulse = _get_pulse()
    fg    = (pulse.get("fear_greed") or {})
    fg_val   = int(fg.get("value", 50) or 50)
    fg_label = str(fg.get("label", "Unknown") or "Unknown")

    from financial_data_client import get_crypto_prices
    prices  = get_crypto_prices(["BTC", "ETH"])
    btc_p   = prices.get("BTC", {})
    btc_px  = round(btc_p.get("usd", 0), 2)
    btc_chg = round(btc_p.get("usd_24h_change", 0), 2)
    eth_px  = round((prices.get("ETH") or {}).get("usd", 0), 2)

    # Oracle
    calls_file = _pl.Path(__file__).parent / "data" / "octo_calls.json"
    calls = _json.loads(calls_file.read_text(encoding="utf-8")) if calls_file.exists() else []
    calls = [c for c in calls if c.get("call_type", "oracle") != "polymarket"]
    open_calls = [c for c in calls if not c.get("resolved")]
    resolved   = [c for c in calls if c.get("resolved")]
    wins   = sum(1 for c in resolved if c.get("outcome") == "WIN")
    losses = sum(1 for c in resolved if c.get("outcome") == "LOSS")
    signal_str = "NO_SIGNAL"
    oracle_note = "No active oracle signal."
    if open_calls:
        latest     = open_calls[-1]
        direction  = latest.get("direction", "").upper()
        signal_str = "BEARISH" if direction == "DOWN" else ("BULLISH" if direction == "UP" else "NEUTRAL")
        oracle_note = f"{latest.get('asset','')} {direction} | entry ${latest.get('entry_price',0):,.0f}"

    # Grok sentiment
    try:
        from octo_grok_sentiment import get_grok_sentiment
        gs = get_grok_sentiment("BTC")
    except Exception:
        gs = {"signal": "NEUTRAL", "confidence": 0.5, "summary": ""}
    crowd_bullish   = gs.get("signal") == "BULLISH"
    crowd_pct       = round((gs.get("confidence", 0.5)) * 100, 1)
    contrarian_flag = (crowd_bullish and btc_chg < -0.5) or (not crowd_bullish and btc_chg > 0.5)
    crowd_label     = "BULLISH" if crowd_bullish else "BEARISH"

    # Top Polymarket edge
    pm_edges = [c for c in (calls or []) if not c.get("resolved") and c.get("timeframe") == "Polymarket"]
    top_edge = {}
    if pm_edges:
        best = max(pm_edges, key=lambda c: abs(float(c.get("edge_score", 0) or 0)))
        top_edge = {"market": best.get("asset",""), "direction": best.get("direction",""), "ev": round(float(best.get("edge_score",0) or 0), 3)}

    reasoning = [
        f"BTC ${btc_px:,.0f} ({btc_chg:+.1f}% 24h) | Fear & Greed {fg_val}/100 ({fg_label}) | Oracle: {oracle_note}",
        f"Grok X crowd {crowd_pct:.0f}% {crowd_label}{'— CONTRARIAN DIVERGENCE ACTIVE' if contrarian_flag else ' — aligned with price'}. "
        f"Record: {wins}W/{losses}L (oracle calls only)."
    ]

    return {
        "type":             "agent_market_intel_bundle",
        "designed_by":      "Agent_Ben",
        "btc_price":        btc_px,
        "btc_24h_change":   btc_chg,
        "eth_price":        eth_px,
        "fear_greed":       {"value": fg_val, "label": fg_label},
        "oracle_signal":    signal_str,
        "oracle_record":    f"{wins}W/{losses}L",
        "oracle_note":      oracle_note,
        "crowd_sentiment":  {"score": crowd_pct, "direction": crowd_label, "contrarian_flag": contrarian_flag},
        "top_polymarket_edge": top_edge,
        "reasoning":        reasoning,
        "footer":           FOOTER,
    }


def handle_bounty_hunter_recon(req: dict) -> dict:
    """
    Bounty Hunter Recon Brief — $2 ACP job.
    For agents deciding whether to accept a Virtuals Bounty market-related request.
    Returns YES/NO + crowd risk + top risk factor + reasoning.
    Designed by Agent_Ben (Session #23, April 28 2026).
    """
    pulse    = _get_pulse()
    fg       = (pulse.get("fear_greed") or {})
    fg_val   = int(fg.get("value", 50) or 50)
    fg_label = str(fg.get("label", "Unknown") or "Unknown")

    # Market risk assessment
    risk_score = 0
    risk_factors = []
    if fg_val < 30:
        risk_score += 30
        risk_factors.append(f"Extreme fear ({fg_val}/100) — market instability elevated")
    elif fg_val < 45:
        risk_score += 15
        risk_factors.append(f"Fear zone ({fg_val}/100) — caution warranted")

    # Check for active Octodamus DOWN call (bearish)
    import json as _json, pathlib as _pl
    calls_file = _pl.Path(__file__).parent / "data" / "octo_calls.json"
    calls = _json.loads(calls_file.read_text(encoding="utf-8")) if calls_file.exists() else []
    open_calls = [c for c in calls if not c.get("resolved")]
    if open_calls:
        latest = open_calls[-1]
        if latest.get("direction", "").upper() == "DOWN":
            risk_score += 25
            risk_factors.append(f"Octodamus oracle is DOWN on {latest.get('asset','BTC')} — bearish signal active")
        else:
            risk_factors.append(f"Octodamus oracle is UP on {latest.get('asset','BTC')} — bullish signal active")

    verdict = "ACCEPT" if risk_score < 35 else ("CAUTION" if risk_score < 60 else "DECLINE")
    top_risk = risk_factors[0] if risk_factors else "No major risk factors detected"

    return {
        "type":         "bounty_hunter_recon",
        "designed_by":  "Agent_Ben",
        "verdict":      verdict,
        "risk_score":   risk_score,
        "top_risk_factor": top_risk,
        "all_risk_factors": risk_factors,
        "fear_greed":   {"value": fg_val, "label": fg_label},
        "reasoning":    f"Risk score {risk_score}/100. Verdict: {verdict}. {top_risk}.",
        "footer":       FOOTER,
    }


# ── Route by type string ──────────────────────────────────────────────────────

_SUBARC_DRAFTS = Path(__file__).parent / ".agents" / "profit-agent" / "drafts"

def _handle_subarc_brief(agent_key: str, agent_display: str, req: dict) -> dict:
    """Serve the latest daily brief generated by a NYSE sub-agent runner session."""
    files = sorted(_SUBARC_DRAFTS.glob(f"{agent_key}_*.md"))
    if not files:
        return {
            "reject": True,
            "error": (
                f"No brief available yet for {agent_display}. "
                f"Briefs are generated daily at 5:30am PST by the NYSE pre-market runner. "
                f"Check back after the next session."
            ),
        }
    latest    = files[-1]
    date_str  = "_".join(latest.stem.split("_")[-3:])  # YYYY-MM-DD
    content   = latest.read_text(encoding="utf-8")
    return {
        "type":       f"{agent_key}_brief",
        "agent":      agent_display,
        "date":       date_str,
        "brief":      content,
        "generated":  "5:30am PST daily — NYSE pre-market runner",
        "powered_by": "@octodamusai ecosystem",
    }

def handle_nyse_macromind_brief(req: dict) -> dict:
    return _handle_subarc_brief("nyse_macromind", "NYSE_MacroMind", req)

def handle_nyse_stockoracle_brief(req: dict) -> dict:
    return _handle_subarc_brief("nyse_stockoracle", "NYSE_StockOracle", req)

def handle_nyse_tech_brief(req: dict) -> dict:
    return _handle_subarc_brief("nyse_tech_agent", "NYSE_Tech_Agent", req)

def handle_order_chainflow_brief(req: dict) -> dict:
    return _handle_subarc_brief("order_chainflow", "Order_ChainFlow", req)

def handle_x_sentiment_brief(req: dict) -> dict:
    return _handle_subarc_brief("x_sentiment_agent", "X_Sentiment_Agent", req)


def handle_tokenized_stock_signal(req: dict) -> dict:
    """
    Oracle analysis for a stock ticker in the context of tokenized equity on Base.
    Covers current price, macro positioning, and the agentic finance lens.
    Accepts: { "ticker": "AAPL" } or { "ticker": "SPY" }
    """
    ticker = str(req.get("ticker") or req.get("asset") or "AAPL").upper().strip()
    try:
        import anthropic, os
        from financial_data_client import get_current_price, build_oracle_context
        try:
            import octo_finnhub
            finnhub_ctx = octo_finnhub.get_finnhub_context(ticker)
        except Exception:
            finnhub_ctx = ""

        price_data = get_current_price(ticker)
        snap = price_data.get("snapshot", {})
        price = snap.get("price", 0.0)
        chg   = snap.get("day_change_percent", 0.0)

        price_block = (
            f"Ticker: {ticker}\n"
            f"Current price: ${price:,.2f}\n"
            f"Day change: {chg:+.2f}%\n"
        )
        if finnhub_ctx:
            price_block += f"\n{finnhub_ctx}"

        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            system=(
                "You are Octodamus — a sharp, dry AI market oracle built for agent-to-agent "
                "intelligence in the tokenized equity era. You provide concise, signal-rich "
                "analysis of stocks through the lens of the agentic finance transition: "
                "NYSE stocks moving to 24/7 on-chain settlement on Base, Visa as the "
                "agent settlement layer, x402 machine payments. Your tone: precise, unsentimental, "
                "one step ahead of consensus. No hype. No filler. Give the signal an agent "
                "actually needs to act on."
            ),
            messages=[{
                "role": "user",
                "content": (
                    f"Generate a concise tokenized stock signal brief for {ticker}.\n\n"
                    f"LIVE DATA:\n{price_block}\n\n"
                    f"Cover: (1) current price positioning and momentum read, "
                    f"(2) what this means for an agent holding or considering this equity, "
                    f"(3) one tokenization/on-chain context line if relevant. "
                    f"Max 200 words. Signal-first, no intro fluff."
                ),
            }],
        )
        analysis = resp.content[0].text.strip()
    except Exception as e:
        analysis = f"Signal generation unavailable: {e}"
        price = 0.0
        chg   = 0.0

    return {
        "type":           "tokenized_stock_signal",
        "ticker":         ticker,
        "price":          price,
        "day_change_pct": chg,
        "analysis":       analysis,
        "oracle":         "@octodamusai",
        "settlement_note": (
            "NYSE tokenized equities settle 24/7 on Base. "
            "This signal is priced for agent consumption via x402."
        ),
        "powered_by": "Octodamus — agentic finance intelligence",
    }


def get_handler(report_type: str):
    t = str(report_type or "").lower().replace("-", "_").replace(" ", "_")
    if any(k in t for k in ["ask", "question", "query"]):
        return handle_ask
    if any(k in t for k in ["signal_pack", "signal_report"]):
        return handle_signal_pack
    if any(k in t for k in ["polymarket", "alpha", "prediction_feed"]):
        return handle_polymarket_alpha
    if any(k in t for k in ["conviction", "conviction_score"]):
        return handle_conviction_score
    if any(k in t for k in ["congressional", "congress", "stock_trade", "stock_alert"]):
        return handle_congressional
    if any(k in t for k in ["fear_greed", "sentiment", "fear"]):
        return handle_fear_greed
    if any(k in t for k in ["bitcoin", "deep_dive", "analysis", "forecast"]):
        return handle_bitcoin_analysis
    if any(k in t for k in ["grok_sentiment", "grok_brief", "x_sentiment", "twitter_sentiment"]):
        return handle_grok_sentiment_brief
    if any(k in t for k in ["divergence", "fear_crowd", "crowd_divergence", "fear_vs_crowd"]):
        return handle_fear_crowd_divergence
    if any(k in t for k in ["smithery", "onboarding", "quickstart", "quick_start", "getting_started"]):
        return handle_smithery_onboarding_brief
    if any(k in t for k in ["overnight", "asia_session", "asia", "night_brief", "overnight_brief"]):
        return handle_overnight_brief
    if any(k in t for k in ["agent_market_intel", "intel_bundle", "context_pack", "decision_loop"]):
        return handle_agent_market_intel_bundle
    if any(k in t for k in ["bounty", "bounty_hunter", "bounty_recon", "recon_brief"]):
        return handle_bounty_hunter_recon
    if any(k in t for k in ["bull_trap", "bull trap", "trap_monitor", "trap monitor", "btc_trap"]):
        return handle_btc_bull_trap_monitor
    if any(k in t for k in ["strike_proximity", "strike proximity", "btc_strike", "polymarket_strike"]):
        return handle_btc_strike_proximity_alert
    if any(k in t for k in ["carry_unwind", "carry unwind", "dxy_threshold", "dxy threshold", "carry_risk", "unwind_monitor"]):
        return handle_carry_unwind_risk_monitor
    if any(k in t for k in ["nyse_macromind", "macromind", "macro_mind"]):
        return handle_nyse_macromind_brief
    if any(k in t for k in ["silence_signal", "congressional_silence", "silence signal", "congress_silence", "execution_risk"]):
        return handle_congressional_silence_signal
    if any(k in t for k in ["nyse_stockoracle", "stockoracle", "stock_oracle"]):
        return handle_nyse_stockoracle_brief
    if any(k in t for k in ["tokenized_stock", "stock_signal", "equity_signal", "base_equity", "onchain_stock"]):
        return handle_tokenized_stock_signal
    if any(k in t for k in ["nyse_tech", "tech_agent", "tokenized_equity"]):
        return handle_nyse_tech_brief
    if any(k in t for k in ["order_chainflow", "chainflow", "chain_flow"]):
        return handle_order_chainflow_brief
    if any(k in t for k in ["x_sentiment_brief", "sentiment_agent_brief"]):
        return handle_x_sentiment_brief
    return handle_crypto_market_signal


# ── Text formatter (for ACP deliverable) ─────────────────────────────────────
# ── Oracle Commentary Engine ──────────────────────────────────────────────────
# Rules-based: reads the data, builds a paragraph explaining the directional call.
# No API calls, instant, deterministic. The oracle's voice from the data.

def _build_oracle_commentary(data: dict) -> str:
    """
    Build 4-6 sentence oracle commentary from structured report data.
    Reads futures positioning, sentiment, technicals and explains the call.
    """
    t    = data.get("type", "") or ""
    ta   = data.get("ta") or {}
    cg   = data.get("cg") or {}
    call = data.get("call", "") or ""

    sentences = []

    # ── Determine direction from call string ──
    is_up = "UP" in call and "DOWN" not in call
    is_down = "DOWN" in call
    is_range = "RANGE" in call
    is_breakout = "BREAKOUT" in call
    ticker = data.get("ticker", "BTC")

    # ── Sentiment read ──
    fng = int(data.get("fng_val", 50) or 50)
    if fng < 15:
        sentences.append(f"Fear is at extreme levels ({fng}) — historically, this is where the smart money starts accumulating while retail panic-sells.")
    elif fng < 30:
        sentences.append(f"Sentiment sits deep in fear territory ({fng}). The crowd is scared, which typically marks the early stages of a reversal.")
    elif fng < 45:
        sentences.append(f"Sentiment is cautious ({fng}) — not capitulation, but the market is nervous. Positioning matters more than feelings here.")
    elif fng < 60:
        sentences.append(f"Sentiment is neutral ({fng}) — neither fear nor greed dominating. The market is waiting for a catalyst.")
    elif fng < 75:
        sentences.append(f"Greed is creeping in ({fng}). Retail is getting comfortable, which is usually when the rug gets pulled.")
    else:
        sentences.append(f"Extreme greed ({fng}) — everyone is bullish, which is precisely the moment to be cautious. The herd is rarely right at extremes.")

    # ── Futures positioning read ──
    long_pct = cg.get("long_pct", 0) or 0
    top_long = cg.get("top_long_pct", 0) or 0
    funding = cg.get("funding_avg", 0) or 0
    taker_flow = cg.get("taker_flow", "") or ""
    taker_buy = cg.get("taker_buy_pct", 50) or 50
    ls_skew = cg.get("ls_skew", "") or ""

    if long_pct and top_long:
        if long_pct > 60 and top_long > 55:
            sentences.append(f"Futures are crowded long — {long_pct:.0f}% of accounts and {top_long:.0f}% of top traders are positioned for upside. When everyone leans one way, the market tends to punish them.")
        elif long_pct > 60 and top_long < 50:
            sentences.append(f"Retail is heavily long ({long_pct:.0f}%) but the whales have flipped short ({top_long:.0f}% long). This divergence is a warning sign — smart money is fading the crowd.")
        elif long_pct < 45 and top_long > 55:
            sentences.append(f"Retail is leaning short ({long_pct:.0f}% long) while top traders are accumulating longs ({top_long:.0f}%). The whales are buying what the crowd is selling.")
        elif long_pct < 45:
            sentences.append(f"The market is short-heavy ({long_pct:.0f}% long). Contrarian setups like this often precede a squeeze when shorts get trapped.")
        else:
            sentences.append(f"Positioning is relatively balanced ({long_pct:.0f}% long) with top traders at {top_long:.0f}% — no extreme crowding in either direction.")

    # ── Funding rate read ──
    if funding:
        if funding > 0.02:
            sentences.append(f"Funding is elevated at {funding:+.4f}% — longs are paying a premium to hold, which adds selling pressure as leveraged positions get expensive to maintain.")
        elif funding < -0.005:
            sentences.append(f"Funding has gone negative ({funding:+.4f}%) — shorts are paying longs to stay in their positions. This is fuel for a squeeze.")
        elif abs(funding) < 0.005:
            sentences.append(f"Funding is near-neutral ({funding:+.4f}%), meaning neither side is paying a significant premium. The leverage game is balanced.")

    # ── Taker flow read ──
    if taker_flow:
        if taker_flow == "BUY PRESSURE":
            sentences.append(f"Taker flow shows aggressive buying ({taker_buy:.0f}% buy-side) — market orders are hitting the ask, signaling urgency from buyers.")
        elif taker_flow == "SELL PRESSURE":
            sentences.append(f"Taker flow is sell-dominated ({taker_buy:.0f}% buy-side) — sellers are aggressively hitting bids, applying downward pressure.")

    # ── Liquidation read ──
    liq_total = cg.get("liq_total", 0) or 0
    liq_pain = cg.get("liq_pain", "") or ""
    if liq_total > 50:
        if liq_pain == "LONG PAIN":
            sentences.append(f"${liq_total:.0f}M in liquidations over the last 4 hours, mostly longs getting flushed. The weak hands are out — that often clears the path for a bounce.")
        elif liq_pain == "SHORT PAIN":
            sentences.append(f"${liq_total:.0f}M liquidated in 4 hours, primarily shorts. The squeeze has already started — late shorts are fuel for more upside.")
    elif liq_total > 10:
        sentences.append(f"Liquidation activity is moderate (${liq_total:.0f}M in 4h) — no major flush yet, meaning a larger move could still be building.")

    # ── Technical confirmation ──
    rsi = float(ta.get("rsi", 50) or 50)
    trend = ta.get("trend", "") or ""
    macd = float(ta.get("macd", 0) or 0)
    if rsi < 30:
        sentences.append(f"Technicals confirm oversold conditions — RSI at {rsi:.0f} with {trend.lower()} trend. The rubber band is stretched.")
    elif rsi > 70:
        sentences.append(f"RSI at {rsi:.0f} signals overbought territory. The {trend.lower()} trend has room to reverse.")
    elif trend and macd:
        direction_word = "supportive" if (trend == "Bullish" and is_up) or (trend == "Bearish" and is_down) else "conflicting"
        sentences.append(f"Technical structure is {direction_word} — {trend.lower()} trend with MACD at {macd:+.0f} and RSI at {rsi:.0f}.")

    # ── OI context ──
    oi_chg = cg.get("oi_chg_24h", 0) or 0
    if abs(oi_chg) > 5:
        if oi_chg > 0:
            sentences.append(f"Open interest surged {oi_chg:+.1f}% in 24 hours — new money is entering the market, which adds conviction to the current move.")
        else:
            sentences.append(f"Open interest dropped {oi_chg:+.1f}% — positions are being closed, suggesting the current trend is losing participation.")

    # Cap at 6 sentences
    commentary = " ".join(sentences[:6])

    if not commentary:
        commentary = f"Data is limited for {ticker} at this time. The oracle reserves judgment until the currents speak more clearly."

    return commentary


def _build_fear_greed_commentary(data: dict) -> str:
    """Commentary for fear & greed reports."""
    fng = int(data.get("fng_val", 50) or 50)
    cg = data.get("cg") or {}
    ta = data.get("ta") or {}
    pos = data.get("position", "") or ""

    sentences = []

    if fng < 15:
        sentences.append(f"The Fear & Greed Index has collapsed to {fng} — deep capitulation territory. Markets at this level have historically rewarded buyers within 30-90 days.")
    elif fng < 30:
        sentences.append(f"At {fng}, fear is elevated but not extreme. The crowd is nervous — this is the zone where patient capital starts deploying.")
    elif fng < 60:
        sentences.append(f"Sentiment at {fng} is in no-man's-land. Neither fear nor greed is dominant, which means the market is waiting for direction.")
    elif fng < 80:
        sentences.append(f"Greed has taken hold at {fng}. Historically, this is where wise money starts trimming while the crowd doubles down.")
    else:
        sentences.append(f"Extreme greed at {fng} — this is a warning sign. The crowd is euphoric, and euphoria is the market's favorite setup for pain.")

    funding = cg.get("funding_avg", 0) or 0
    long_pct = cg.get("long_pct", 0) or 0
    if long_pct and funding:
        if fng < 30 and long_pct < 50:
            sentences.append(f"Futures confirm the fear — only {long_pct:.0f}% of accounts are long, and funding at {funding:+.4f}% shows shorts are confident. This is the contrarian's playground.")
        elif fng > 70 and long_pct > 60:
            sentences.append(f"Futures validate the greed — {long_pct:.0f}% of accounts are long with funding at {funding:+.4f}%. Too many passengers on one side of the boat.")
        else:
            sentences.append(f"Futures positioning ({long_pct:.0f}% long, funding {funding:+.4f}%) tells a more nuanced story than sentiment alone. The market's structure doesn't fully match the mood.")

    taker_flow = cg.get("taker_flow", "") or ""
    if taker_flow == "BUY PRESSURE":
        sentences.append("Taker flow is buy-dominant — despite the sentiment reading, aggressive buyers are stepping in.")
    elif taker_flow == "SELL PRESSURE":
        sentences.append("Taker flow confirms sellers are in control — market orders are hitting bids, validating the fear.")

    rsi = float(ta.get("rsi", 50) or 50)
    if rsi < 35:
        sentences.append(f"RSI at {rsi:.0f} backs up the fear reading — technically oversold with room for a relief bounce.")
    elif rsi > 65:
        sentences.append(f"RSI at {rsi:.0f} aligns with the greed — momentum is extended and vulnerable to a pullback.")

    sentences.append(f"Octodamus positioning signal: {pos}.")

    return " ".join(sentences[:5])


def _build_deep_dive_commentary(data: dict) -> str:
    """Commentary for bitcoin/crypto deep dive reports."""
    ticker = data.get("ticker", "BTC")
    price = float(data.get("price", 0) or 0)
    chg_24h = float(data.get("chg_24h", 0) or 0)
    chg_7d = float(data.get("chg_7d", 0) or 0)
    cg = data.get("cg") or {}
    ta = data.get("ta") or {}
    fng = int(data.get("fng_val", 50) or 50)

    sentences = []

    # Price action context
    if chg_7d < -10:
        sentences.append(f"{ticker} has dropped {chg_7d:+.1f}% over seven days — a significant drawdown that has shaken out weak holders and reset expectations.")
    elif chg_7d > 10:
        sentences.append(f"{ticker} has surged {chg_7d:+.1f}% in a week — strong momentum, but extended moves like this often need to consolidate before continuing.")
    elif chg_24h > 3:
        sentences.append(f"{ticker} is up {chg_24h:+.1f}% today, showing short-term strength. The question is whether this is a dead cat bounce or the start of a trend reversal.")
    elif chg_24h < -3:
        sentences.append(f"{ticker} down {chg_24h:+.1f}% in 24 hours — sellers are pressing, and the key is whether current support levels hold.")
    else:
        sentences.append(f"{ticker} is grinding sideways ({chg_24h:+.1f}% today, {chg_7d:+.1f}% weekly) — range-bound action that typically precedes a directional breakout.")

    # Futures context
    long_pct = cg.get("long_pct", 0) or 0
    top_long = cg.get("top_long_pct", 0) or 0
    funding = cg.get("funding_avg", 0) or 0
    oi_chg = cg.get("oi_chg_24h", 0) or 0

    if long_pct and top_long:
        if abs(long_pct - top_long) > 10:
            side = "retail" if long_pct > top_long else "whale"
            sentences.append(f"There's a notable divergence between retail ({long_pct:.0f}% long) and top traders ({top_long:.0f}% long) — the {side} crowd is more aggressive, and that divergence usually resolves in favor of the whales.")
        elif long_pct > 60:
            sentences.append(f"Both retail ({long_pct:.0f}%) and whales ({top_long:.0f}%) are leaning long — consensus is bullish, which can work until it becomes too crowded.")

    if funding and abs(funding) > 0.01:
        cost = "longs" if funding > 0 else "shorts"
        sentences.append(f"Funding at {funding:+.4f}% means {cost} are paying to hold — this cost erodes conviction over time and often triggers a positioning unwind.")

    if abs(oi_chg) > 5:
        sentences.append(f"Open interest moved {oi_chg:+.1f}% in 24 hours — {'new positions building' if oi_chg > 0 else 'positions unwinding'}, which {'adds fuel to the move' if oi_chg > 0 else 'suggests exhaustion'}.")

    # Technicals
    trend = ta.get("trend", "") or ""
    rsi = float(ta.get("rsi", 50) or 50)
    bb_w = float(ta.get("bb_width", 5) or 5)
    if bb_w < 3:
        sentences.append(f"Bollinger Bands have compressed to {bb_w:.1f}% — volatility is coiled tight. A major move is imminent; the direction will be decided by which side blinks first.")
    elif trend:
        sentences.append(f"The technical structure is {trend.lower()} with RSI at {rsi:.0f} — {'room to run' if (trend == 'Bullish' and rsi < 65) or (trend == 'Bearish' and rsi > 35) else 'getting extended'}.")

    # Sentiment tie-in
    if fng < 25:
        sentences.append(f"With fear at {fng}, the macro backdrop favors accumulation over distribution.")
    elif fng > 75:
        sentences.append(f"Sentiment at {fng} suggests the easy money has been made — risk management matters more than FOMO here.")

    return " ".join(sentences[:6])


def _build_congressional_commentary(data: dict) -> str:
    """Commentary for congressional trade reports."""
    ticker = data.get("ticker", "")
    buys = data.get("buys", 0) or 0
    sells = data.get("sells", 0) or 0
    trades = data.get("trades") or []
    fng = int(data.get("fng_val", 50) or 50)

    sentences = []

    total = buys + sells
    if total == 0:
        return f"No recent congressional trading activity on {ticker}. Silence from Capitol Hill can mean anything — or nothing. The oracle watches, but the politicians aren't moving."

    if buys > sells * 2:
        sentences.append(f"Congress is buying {ticker} aggressively — {buys} purchases vs {sells} sales. When the people writing the rules are placing bets, it's worth paying attention.")
    elif sells > buys * 2:
        sentences.append(f"Congressional selling on {ticker} is heavy — {sells} sales vs {buys} purchases. Politicians dumping a stock is one of the most reliable bearish signals in the market.")
    elif buys > sells:
        sentences.append(f"Slight congressional buying bias on {ticker} ({buys} buys, {sells} sells). Not a stampede, but the direction is notable.")
    elif sells > buys:
        sentences.append(f"Congressional activity leans toward selling on {ticker} ({sells} sales, {buys} buys). Not panic selling, but the insiders are reducing exposure.")
    else:
        sentences.append(f"Mixed signals from Capitol Hill on {ticker} — {buys} buys and {sells} sells. No clear directional conviction from the insiders.")

    # Name notable traders
    if trades:
        names = list(set(tr.get("name", "?") for tr in trades[:5]))
        if len(names) <= 3:
            sentences.append(f"Key names in the activity: {', '.join(names)}.")

    sentences.append("Core thesis: Congress has asymmetric information. They write the regulations, approve the contracts, and see the data before the market does. Following their money has historically outperformed the S&P 500.")

    if fng < 30:
        sentences.append(f"Macro context: Fear & Greed at {fng} suggests broad market anxiety — congressional buying during fear periods has an even stronger track record.")
    elif fng > 70:
        sentences.append(f"Macro context: Fear & Greed at {fng} — the market is complacent. Congressional selling during greed periods is a particularly strong warning.")

    return " ".join(sentences[:4])


# v3: ~2/3 compact data, ~1/3 oracle directional take, footer with results link

def render_text(data: dict) -> str:
    if not data or not isinstance(data, dict):
        return f"OCTODAMUS REPORT\nReport data unavailable — please retry.\n\n{FOOTER}"

    t    = data.get("type", "") or ""
    call = data.get("call", "") or ""
    err  = data.get("error")

    if err:
        return f"OCTODAMUS REPORT\nNote: {err}\n\n{FOOTER}"

    if t == "ask":
        q   = data.get("question", "")
        ans = data.get("answer", "")
        eps = data.get("suggested_endpoints", [])
        L   = [
            "OCTODAMUS — MARKET INTELLIGENCE ANSWER",
            "─" * 44,
            "",
            f"Q: {q}",
            "",
            ans,
        ]
        if eps:
            L += ["", "── AUTOMATE THIS ────────────────────────────"]
            for ep in eps[:3]:
                L.append(f"  {ep.get('endpoint','')} — {ep.get('description','')}")
        L += ["", "─" * 44, FOOTER]
        return "\n".join(L)

    if t == "market_signal":
        ta     = data.get("ta") or {}
        deriv  = data.get("deriv") or {}
        cg     = data.get("cg") or {}
        prices = data.get("prices") or {}
        btc    = prices.get("BTC") or {}
        eth    = prices.get("ETH") or {}
        sol    = prices.get("SOL") or {}

        L = [
            data.get("title", "OCTODAMUS MARKET ORACLE BRIEFING"),
            f"Generated: {data.get('generated', '')}",
            "",
            "── MARKET DATA ──────────────────────────────",
            "",
            f"BTC: {btc.get('price','N/A')} ({float(btc.get('chg',0) or 0):+.1f}%) | ETH: {eth.get('price','N/A')} ({float(eth.get('chg',0) or 0):+.1f}%) | SOL: {sol.get('price','N/A')} ({float(sol.get('chg',0) or 0):+.1f}%)",
            f"BTC Dominance: {data.get('btc_dom','N/A')}% | Momentum: {data.get('momentum','N/A')}",
            f"Fear & Greed: {data.get('fng_val')} — {data.get('fng_label')} | USD/EUR: {data.get('usd_eur')} | USD/JPY: {data.get('usd_jpy')}",
        ]

        # TA block (compact)
        if ta:
            L.append(f"RSI: {ta.get('rsi')} | MACD: {ta.get('macd')} | Trend: {ta.get('trend')} | BB: {ta.get('bb_width')}%")

        # Derivatives + Coinglass futures (compact block)
        if deriv or cg:
            L.append("")
            L.append("── FUTURES POSITIONING ──────────────────────")
            if deriv:
                L.append(f"Kraken Funding: {deriv.get('funding_rate')}% | OI: {deriv.get('open_interest')}")
            if cg.get("funding_avg") is not None:
                L.append(f"Avg Funding (all exchanges): {cg['funding_avg']:+.4f}% ({cg.get('funding_dir', '')})")
            if cg.get("oi_usd") is not None:
                L.append(f"Total OI: ${cg['oi_usd']}B (OI/MCap: {cg.get('oi_mcap_ratio', 'N/A')}%) | 24h OI chg: {cg.get('oi_chg_24h', 'N/A'):+.1f}%")
            if cg.get("long_pct") is not None:
                L.append(f"L/S Ratio: {cg['long_pct']}% long / {cg['short_pct']}% short ({cg.get('ls_skew', '')})")
            if cg.get("top_long_pct") is not None:
                L.append(f"Top Traders: {cg['top_long_pct']}% long / {cg['top_short_pct']}% short")
            if cg.get("taker_buy_pct") is not None:
                L.append(f"Taker Flow: {cg['taker_buy_pct']:.0f}% buy | ${cg.get('taker_vol', 0):.0f}M vol | {cg.get('taker_flow', '')}")
            if cg.get("liq_total") is not None:
                L.append(f"Liquidations (4h): ${cg['liq_total']}M total — Longs: ${cg['liq_long']}M, Shorts: ${cg['liq_short']}M ({cg.get('liq_pain', '')})")

        # ── ORACLE TAKE (1/3 of report) ──────────────────────────────
        commentary = _build_oracle_commentary(data)
        L += [
            "",
            "── OCTODAMUS READS THE CURRENTS ────────────",
            "",
            commentary,
            "",
            f"Signal: {data.get('signal','')}",
            "",
            f"OCTODAMUS CALL: {call}",
            "",
            FOOTER,
        ]
        return "\n".join(L)

    elif t == "fear_greed":
        ta    = data.get("ta") or {}
        cg    = data.get("cg") or {}

        L = [
            data.get("title", "OCTODAMUS FEAR & GREED SENTIMENT READ"),
            f"Generated: {data.get('generated', '')}",
            "",
            "── SENTIMENT DATA ──────────────────────────",
            "",
            f"Fear & Greed Index: {data.get('fng_val')} — {str(data.get('fng_label','')).upper()}",
            f"Positioning Signal: {data.get('position','')}",
        ]

        # TA confirmation
        if ta:
            L.append(f"RSI: {ta.get('rsi')} | MACD: {ta.get('macd')} | Trend: {ta.get('trend')}")

        # Coinglass futures context
        if cg:
            L.append("")
            L.append("── FUTURES CONFIRMATION ─────────────────────")
            if cg.get("funding_avg") is not None:
                L.append(f"Avg Funding: {cg['funding_avg']:+.4f}% ({cg.get('funding_dir', '')})")
            if cg.get("long_pct") is not None:
                L.append(f"L/S Ratio: {cg['long_pct']}% long / {cg['short_pct']}% short ({cg.get('ls_skew', '')})")
            if cg.get("taker_buy_pct") is not None:
                L.append(f"Taker Flow: {cg['taker_buy_pct']:.0f}% buy | {cg.get('taker_flow', '')}")
            if cg.get("liq_total") is not None:
                L.append(f"Liquidations (4h): ${cg['liq_total']}M — {cg.get('liq_pain', '')}")

        spikes = data.get("spikes") or []
        if spikes:
            L.append(f"Wikipedia Spikes: {', '.join(str(s) for s in spikes)}")

        # ── ORACLE TAKE ──────────────────────────────────────────────
        commentary = _build_fear_greed_commentary(data)
        L += [
            "",
            "── OCTODAMUS READS THE CURRENTS ────────────",
            "",
            commentary,
            "",
            f"OCTODAMUS CALL: {call}",
            "",
            FOOTER,
        ]
        return "\n".join(L)

    elif t == "bitcoin_analysis":
        ta       = data.get("ta") or {}
        deriv    = data.get("deriv") or {}
        cg       = data.get("cg") or {}
        price    = float(data.get("price", 0) or 0)
        low_24h  = float(data.get("low_24h", 0) or 0)
        high_24h = float(data.get("high_24h", 0) or 0)
        support    = low_24h * 0.97 if low_24h else 0
        resistance = high_24h * 1.03 if high_24h else 0
        bull_t = price * 1.18 if price else 0
        bear_t = price * 0.82 if price else 0

        L = [
            data.get("title", "OCTODAMUS BTC DEEP DIVE"),
            f"Generated: {data.get('generated', '')} | Timeframe: {data.get('timeframe','4h')}",
            "",
            "── PRICE & PERFORMANCE ─────────────────────",
            "",
            f"Current: ${price:,.2f} | 24h: {float(data.get('chg_24h',0) or 0):+.2f}% | 7d: {float(data.get('chg_7d',0) or 0):+.2f}% | 30d: {float(data.get('chg_30d',0) or 0):+.2f}%",
            f"24h Range: ${low_24h:,.2f} — ${high_24h:,.2f} | ATH: ${float(data.get('ath',0) or 0):,.2f} ({float(data.get('ath_pct',0) or 0):+.1f}%)",
            f"MCap: ${float(data.get('mcap',0) or 0)/1e9:.2f}B | 24h Vol: ${float(data.get('vol',0) or 0)/1e9:.2f}B | Momentum: {data.get('momentum','N/A')}",
        ]

        # TA block
        if ta:
            L.append(f"RSI: {ta.get('rsi')} | MACD: {ta.get('macd')} | Trend: {ta.get('trend')} | BB: {ta.get('bb_width')}%")
            L.append(f"EMA20: ${float(ta.get('ema20',0) or 0):,.0f} | EMA50: ${float(ta.get('ema50',0) or 0):,.0f}")

        # Futures data
        if deriv or cg:
            L.append("")
            L.append("── FUTURES POSITIONING ──────────────────────")
            if deriv:
                L.append(f"Kraken Funding: {deriv.get('funding_rate')}% | Kraken OI: {deriv.get('open_interest')}")
            if cg.get("funding_avg") is not None:
                L.append(f"Avg Funding (all exchanges): {cg['funding_avg']:+.4f}% ({cg.get('funding_dir', '')})")
            if cg.get("oi_usd") is not None:
                L.append(f"Total OI: ${cg['oi_usd']}B (OI/MCap: {cg.get('oi_mcap_ratio', 'N/A')}%) | 24h chg: {cg.get('oi_chg_24h', 'N/A'):+.1f}%")
            if cg.get("long_pct") is not None:
                L.append(f"L/S Ratio: {cg['long_pct']}% long / {cg['short_pct']}% short ({cg.get('ls_skew', '')})")
            if cg.get("top_long_pct") is not None:
                L.append(f"Top Traders: {cg['top_long_pct']}% long / {cg['top_short_pct']}% short")
            if cg.get("taker_buy_pct") is not None:
                L.append(f"Taker Flow: {cg['taker_buy_pct']:.0f}% buy | ${cg.get('taker_vol', 0):.0f}M vol | {cg.get('taker_flow', '')}")
            if cg.get("liq_total") is not None:
                L.append(f"Liquidations (4h): ${cg['liq_total']}M — Longs: ${cg['liq_long']}M, Shorts: ${cg['liq_short']}M ({cg.get('liq_pain', '')})")

        # Price targets
        L.append("")
        L.append(f"Support: ${support:,.2f} | Resistance: ${resistance:,.2f}")
        L.append(f"Bull: ${bull_t:,.0f} (+18%) | Bear: ${bear_t:,.0f} (-18%)")

        # ── ORACLE TAKE ──────────────────────────────────────────────
        commentary = _build_deep_dive_commentary(data)
        L += [
            "",
            "── OCTODAMUS READS THE CURRENTS ────────────",
            "",
            commentary,
            "",
            f"Fear & Greed: {data.get('fng_val')} — {data.get('fng_label')}",
            "",
            f"OCTODAMUS CALL: {call}",
            "",
            FOOTER,
        ]
        return "\n".join(L)

    elif t == "congressional":
        trades = data.get("trades") or []
        L = [
            data.get("title", "CONGRESSIONAL TRADE REPORT"),
            f"Generated: {data.get('generated', '')} (Period: {data.get('period','')})",
            "",
            "── TRADE DATA ──────────────────────────────",
            "",
            "Core belief: Congress front-runs markets. Follow the money.",
            "",
        ]
        if trades:
            for tr in trades:
                L.append(f"  {tr.get('name','?')} ({tr.get('party','?')}) {tr.get('direction','?')} — {tr.get('amount','?')} — {tr.get('date','?')}")
        else:
            L.append("  No recent trades found.")
        L.append(f"  Summary: {data.get('buys',0)} buys, {data.get('sells',0)} sells")

        # ── FINNHUB INTELLIGENCE ─────────────────────────────────────
        finnhub_ctx = data.get("finnhub_context", "")
        if finnhub_ctx:
            L += ["", finnhub_ctx]

        # ── ORACLE TAKE ──────────────────────────────────────────────
        commentary = _build_congressional_commentary(data)
        L += [
            "",
            "── OCTODAMUS READS THE CURRENTS ────────────",
            "",
            commentary,
            "",
            f"OCTODAMUS CALL: {call}",
            "",
            FOOTER,
        ]
        return "\n".join(L)

    elif t == "smithery_onboarding":
        L = [
            data.get("title", "OCTODAMUS -- AGENT QUICK-START GUIDE"),
            f"Generated: {data.get('generated', '')}",
            "",
            "── TOOLS GUIDE ──────────────────────────────",
            "",
        ]
        for tool in data.get("tools_guide") or []:
            L.append(f"  {tool['tool']:28s} {tool['purpose']} ({tool['cadence']})")
        L += [
            "",
            f"MCP Server:   {data.get('mcp_server', '')}",
            f"API Key URL:  {data.get('api_key_url', '')}",
            f"Cadence:      {data.get('recommended_cadence', '')}",
            f"Quick-start:  {data.get('quick_start_snippet', '')}",
            "",
            "── ORACLE RECORD ────────────────────────────",
            "",
            data.get("oracle_record", ""),
            "",
            "── PRICING ──────────────────────────────────",
            "",
        ]
        pricing = data.get("pricing") or {}
        for k, v in pricing.items():
            L.append(f"  {k}: {v}")
        L += ["", FOOTER]
        return "\n".join(L)

    elif t == "overnight_brief":
        fng = data.get("fear_greed") or {}
        fs  = data.get("futures_snapshot") or {}
        orc = data.get("oracle_signal") or {}
        L = [
            data.get("title", "OCTODAMUS OVERNIGHT / ASIA SESSION BRIEF"),
            f"Generated: {data.get('generated', '')} | Session: {data.get('session', '')}",
            "",
            "── MARKET ───────────────────────────────────",
            "",
            f"BTC: ${float(data.get('btc_price', 0) or 0):,.2f} ({float(data.get('btc_chg_24h', 0) or 0):+.2f}% 24h)",
            f"Fear & Greed: {fng.get('value', 'N/A')} -- {fng.get('label', 'N/A')}",
            "",
            "── FUTURES ──────────────────────────────────",
            "",
        ]
        if fs.get("funding_avg") is not None:
            L.append(f"Avg Funding: {float(fs['funding_avg']):+.4f}%")
        if fs.get("long_pct") is not None:
            L.append(f"L/S Ratio: {fs['long_pct']}% long / {fs['short_pct']}% short ({fs.get('ls_skew', '')})")
        L += [
            "",
            "── ORACLE SIGNAL ────────────────────────────",
            "",
            f"Action: {orc.get('action', 'N/A')} | Asset: {orc.get('asset', 'BTC')} | Confidence: {orc.get('confidence', 0)}",
        ]
        if orc.get("reasoning"):
            L.append(f"Reasoning: {orc['reasoning']}")
        L += [
            "",
            "── ACTION SUMMARY ───────────────────────────",
            "",
            data.get("action_summary", ""),
            "",
            FOOTER,
        ]
        return "\n".join(L)

    return f"OCTODAMUS REPORT\nUnknown report type.\n\n{FOOTER}"

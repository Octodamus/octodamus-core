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

    return result


# ── Upgraded directional call — 11 signals ───────────────────────────────────

def directional_call(ticker, price, chg_24h, ta, deriv, fng, cg=None) -> str:
    """
    Oracle directional scoring engine.
    v3: 11 signals (was 6). Coinglass data adds 5 new signals:
      - Aggregate funding rate direction
      - Long/short ratio extremes
      - Top trader positioning
      - Taker flow direction
      - Recent liquidation skew
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

    total = bull + bear
    p = f"${price:,.0f}" if price else "current level"

    if bb_w < 3.0:
        d = "UP" if bull > bear else "DOWN"
        return f"DIRECTION: BREAKOUT IMMINENT — BB compressed to {bb_w}%. {bull}/{total} bullish. Resolving {d}."
    elif bull >= 7:
        return f"DIRECTION: STRONG UP — {bull}/{total} signals bullish. High-conviction long setup."
    elif bull >= 5:
        return f"DIRECTION: UP — {bull}/{total} signals bullish. {ticker} likely continues higher."
    elif bear >= 7:
        return f"DIRECTION: STRONG DOWN — {bear}/{total} signals bearish. High-conviction short setup."
    elif bear >= 5:
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
    if not raw or raw not in VALID_CRYPTO:
        return {"reject": True, "error": f"Invalid or empty ticker: '{raw}'. Valid: {sorted(VALID_CRYPTO)}"}

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

        from concurrent.futures import ThreadPoolExecutor
        quiver = quiverquant.quiver(token)
        with ThreadPoolExecutor(max_workers=2) as ex:
            f_df    = ex.submit(quiver.congress_trading, ticker)
            f_pulse = ex.submit(octo_pulse.run_pulse_scan)
            try:
                df = f_df.result(timeout=30)
            except Exception:
                df = None
            try:
                pulse = f_pulse.result(timeout=30) or {}
            except Exception:
                pulse = {}

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
            "type":           "congressional",
            "ticker":         ticker,
            "title":          "CONGRESSIONAL TRADE REPORT",
            "subtitle":       "OCTODAMUS CONGRESSIONAL TRADE ALERT",
            "generated":      datetime.utcnow().strftime("%a, %b %d, %Y"),
            "period":         period_label,
            "trades":         trades,
            "buys":           buys,
            "sells":          sells,
            "interpretation": interpretation,
            "call":           call,
            "fng_val":        fng_val,
            "fng_label":      fng_label,
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


# ── Route by type string ──────────────────────────────────────────────────────

def get_handler(report_type: str):
    t = str(report_type or "").lower().replace("-", "_").replace(" ", "_")
    if any(k in t for k in ["congressional", "congress", "stock_trade", "stock_alert"]):
        return handle_congressional
    if any(k in t for k in ["fear_greed", "sentiment", "fear"]):
        return handle_fear_greed
    if any(k in t for k in ["bitcoin", "deep_dive", "analysis", "forecast"]):
        return handle_bitcoin_analysis
    return handle_crypto_market_signal


# ── Text formatter (for ACP deliverable) ─────────────────────────────────────
# v3: ~2/3 compact data, ~1/3 oracle directional take, footer with results link

def render_text(data: dict) -> str:
    if not data or not isinstance(data, dict):
        return f"OCTODAMUS REPORT\nReport data unavailable — please retry.\n\n{FOOTER}"

    t    = data.get("type", "") or ""
    call = data.get("call", "") or ""
    err  = data.get("error")

    if err:
        return f"OCTODAMUS REPORT\nNote: {err}\n\n{FOOTER}"

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
        L += [
            "",
            "── OCTODAMUS READS THE CURRENTS ────────────",
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
        L += [
            "",
            "── OCTODAMUS READS THE CURRENTS ────────────",
            "",
            f"Context: {data.get('context','')}",
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
        L += [
            "",
            "── OCTODAMUS READS THE CURRENTS ────────────",
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

        # ── ORACLE TAKE ──────────────────────────────────────────────
        L += [
            "",
            "── OCTODAMUS READS THE CURRENTS ────────────",
            "",
            f"Oracle read: {data.get('interpretation','')}",
            f"Macro context: Fear & Greed {data.get('fng_val')} — {data.get('fng_label','')}",
            "",
            f"OCTODAMUS CALL: {call}",
            "",
            FOOTER,
        ]
        return "\n".join(L)

    return f"OCTODAMUS REPORT\nUnknown report type.\n\n{FOOTER}"

"""
octo_acp_worker.py — Octodamus ACP Seller Worker
Listens for incoming ACP jobs and fulfills them using Octodamus signal modules.
Run: python3 octo_acp_worker.py
"""

import asyncio
import json
import logging
import sys
import os

# ── Bitwarden key loading ──────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))
from bitwarden import _get_password as get_secret

# ── ACP SDK ───────────────────────────────────────────────────────────────────
from virtuals_acp.client import VirtualsACP
from virtuals_acp.contract_clients.contract_client_v2 import ACPContractClientV2
from virtuals_acp.configs.configs import BASE_MAINNET_CONFIG_V2

# ── Octodamus signal modules ───────────────────────────────────────────────────
import octo_pulse
import octo_gecko
import octo_predict
import octo_fx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [ACP] %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/octo_acp_worker.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
SELLER_ENTITY_ID          = 3
SELLER_AGENT_WALLET = "0x9DdE22707542FA69c9ecfEb0C4f0912797DF3d5E"
BITWARDEN_ITEM            = "AGENT - Octodamus - ACP Wallet"

# ── Job handlers ──────────────────────────────────────────────────────────────


# -- Technical Analysis Helpers --

def _fetch_binance_technicals(symbol="BTCUSDT"):
    """Fetch MACD, RSI, EMA, Bollinger Bands from Kraken OHLC (Binance geo-blocked)."""
    import httpx, statistics
    # Map BTCUSDT -> XBTUSD for Kraken
    kraken_map = {"BTCUSDT": "XBTUSD", "ETHUSDT": "ETHUSD", "SOLUSDT": "SOLUSD"}
    kraken_pair = kraken_map.get(symbol, symbol.replace("USDT", "USD"))
    try:
        r = httpx.get("https://api.kraken.com/0/public/OHLC",
            params={"pair": kraken_pair, "interval": 240, "count": 50}, timeout=8)
        if r.status_code != 200:
            return {}
        data = r.json()
        if data.get("error"):
            return {}
        # Kraken returns dict with pair key
        result_key = list(data["result"].keys())[0]
        candles = data["result"][result_key]
        closes = [float(c[4]) for c in candles]  # index 4 = close

        def ema(data, period):
            k = 2 / (period + 1)
            e = data[0]
            for p in data[1:]:
                e = p * k + e * (1 - k)
            return round(e, 2)

        ema20 = ema(closes, 20)
        ema50 = ema(closes, 50)
        ema12 = ema(closes, 12)
        ema26 = ema(closes, 26)
        macd = round(ema12 - ema26, 2)

        gains, losses = [], []
        for i in range(1, 15):
            d = closes[-i] - closes[-i-1]
            (gains if d > 0 else losses).append(abs(d))
        avg_gain = sum(gains) / 14 if gains else 0
        avg_loss = sum(losses) / 14 if losses else 0.001
        rsi = round(100 - (100 / (1 + avg_gain / avg_loss)), 1)

        recent = closes[-20:]
        bb_mean = sum(recent) / 20
        bb_std = statistics.stdev(recent)
        bb_width = round((round(bb_mean + 2*bb_std, 2) - round(bb_mean - 2*bb_std, 2)) / bb_mean * 100, 1)

        return {"ema20": ema20, "ema50": ema50, "trend": "Bullish" if ema20 > ema50 else "Bearish",
                "rsi": rsi, "macd": macd, "bb_width": bb_width}
    except Exception as e:
        log.warning(f"Kraken technicals error: {e}")
        return {}


def _fetch_binance_derivatives(symbol="BTCUSDT"):
    """Fetch funding rate and open interest from Kraken futures."""
    import httpx
    kraken_map = {"BTCUSDT": "PI_XBTUSD", "ETHUSDT": "PI_ETHUSD", "SOLUSDT": "PI_SOLUSD"}
    kraken_sym = kraken_map.get(symbol, "PI_XBTUSD")
    result = {}
    try:
        r = httpx.get("https://futures.kraken.com/derivatives/api/v3/tickers", timeout=8)
        if r.status_code == 200:
            tickers = r.json().get("tickers", [])
            ticker = next((t for t in tickers if t.get("symbol") == kraken_sym), None)
            if ticker:
                fr = ticker.get("fundingRate", 0) or 0
                result["funding_rate"] = round(float(fr) * 100, 6)
                oi = ticker.get("openInterest", 0) or 0
                price = ticker.get("markPrice", 71000) or 71000
                result["open_interest"] = f"${float(oi) * float(price) / 1e9:.2f}B"
                result["change_24h"] = ticker.get("change24h", 0)
                result["high_24h"] = ticker.get("high24h", 0)
                result["low_24h"] = ticker.get("low24h", 0)
    except Exception as e:
        log.warning(f"Kraken derivatives error: {e}")
    return result


def _momentum_label(rsi, macd, ema20, ema50):
    if rsi > 70:
        return "Overbought"
    elif rsi < 30:
        return "Oversold"
    elif macd > 0 and ema20 > ema50:
        return "Leaning Bullish"
    elif macd < 0 and ema20 < ema50:
        return "Leaning Bearish"
    return "Consolidating"

def handle_market_oracle_briefing(requirements: dict) -> str:
    """Full market oracle briefing — prices, technicals, derivatives, macro, oracle call."""
    ticker = requirements.get("ticker", "BTC").upper()
    bn_symbol = ticker + "USDT"
    try:
        import httpx
        from datetime import datetime

        # Live prices
        pulse = octo_pulse.run_pulse_scan()
        gecko = octo_gecko.run_gecko_scan()
        fx    = octo_fx.run_fx_scan() if hasattr(octo_fx, "run_fx_scan") else {}

        fng_val   = int(pulse.get("fear_greed", {}).get("value", 50) or 50)
        fng_label = pulse.get("fear_greed", {}).get("label", "N/A")
        btc_dom   = gecko.get("btc_dominance", gecko.get("global", {}).get("btc_dominance", "N/A"))

        btc_price = eth_price = sol_price = "N/A"
        btc_chg = eth_chg = sol_chg = "N/A"
        try:
            r = httpx.get("https://api.coingecko.com/api/v3/simple/price",
                params={"ids":"bitcoin,ethereum,solana","vs_currencies":"usd","include_24hr_change":"true"}, timeout=6)
            if r.status_code == 200:
                d = r.json()
                btc_price = f"${d['bitcoin']['usd']:,.0f}"
                btc_chg   = f"{d['bitcoin']['usd_24h_change']:+.1f}%"
                eth_price = f"${d['ethereum']['usd']:,.0f}"
                eth_chg   = f"{d['ethereum']['usd_24h_change']:+.1f}%"
                sol_price = f"${d['solana']['usd']:,.2f}"
                sol_chg   = f"{d['solana']['usd_24h_change']:+.1f}%"
        except Exception:
            pass

        usd_eur = fx.get("key_pairs", {}).get("EUR", {}).get("rate", "N/A") if fx else "N/A"
        usd_jpy = fx.get("key_pairs", {}).get("JPY", {}).get("rate", "N/A") if fx else "N/A"

        # Technical analysis
        ta = _fetch_binance_technicals(bn_symbol)
        deriv = _fetch_binance_derivatives(bn_symbol)

        # Momentum
        momentum = _momentum_label(
            ta.get("rsi", 50), ta.get("macd", 0),
            ta.get("ema20", 0), ta.get("ema50", 0)
        ) if ta else "N/A"

        # Oracle signal
        if fng_val < 20:
            signal = "ACCUMULATE — Extreme fear historically precedes recovery. Strong buy zone."
        elif fng_val < 40:
            signal = "CAUTIOUS BUY — Fear present. Scale in carefully on dips."
        elif fng_val < 60:
            signal = "NEUTRAL — Hold positions. Wait for directional confirmation."
        elif fng_val < 80:
            signal = "REDUCE — Greed elevated. Consider taking partial profits."
        else:
            signal = "EXIT RISK — Extreme greed. High correction probability."

        lines = [
            f"OCTODAMUS MARKET ORACLE BRIEFING",
            f"Generated: {datetime.utcnow().strftime('%a, %b %d, %Y')}",
            f"",
            f"1. Price & Performance Overview",
            f"   BTC: {btc_price} ({btc_chg} today)",
            f"   ETH: {eth_price} ({eth_chg} today)",
            f"   SOL: {sol_price} ({sol_chg} today)",
            f"   BTC Dominance: {btc_dom}%",
            f"   Momentum: {momentum}",
        ]

        if ta:
            lines += [
                f"",
                f"2. Technical Analysis (4h Timeframe)",
                f"   MACD: {ta.get('macd','N/A')} ({'Bullish' if float(str(ta.get('macd',0))) > 0 else 'Bearish'} momentum)",
                f"   RSI: {ta.get('rsi','N/A')} ({'Overbought' if float(str(ta.get('rsi',50))) > 70 else 'Oversold' if float(str(ta.get('rsi',50))) < 30 else 'Neutral territory'})",
                f"   Bollinger Bands: {ta.get('bb_width','N/A')}% width ({'tight — breakout imminent' if float(str(ta.get('bb_width',5))) < 4 else 'normal range'})",
                f"   Trend: EMA20 ({ta.get('ema20','N/A')}) {'>' if ta.get('ema20',0) > ta.get('ema50',0) else '<'} EMA50 ({ta.get('ema50','N/A')}) — {ta.get('trend','N/A')}",
            ]

        if deriv:
            fr = deriv.get('funding_rate', 'N/A')
            oi = deriv.get('open_interest', 'N/A')
            lines += [
                f"",
                f"3. Derivatives",
                f"   Funding Rate: {fr}% ({'Longs paying — cautious' if isinstance(fr, float) and fr > 0.01 else 'Neutral' if fr == 'N/A' else 'Shorts paying — bullish signal'})",
                f"   Open Interest: {oi}",
            ]

        lines += [
            f"",
            f"4. Macro",
            f"   Fear & Greed: {fng_val} — {fng_label}",
            f"   USD/EUR: {usd_eur}",
            f"   USD/JPY: {usd_jpy}",
            f"",
            f"5. Oracle Signal",
            f"   {signal}",
            f"",
            f"Powered by Octodamus (@octodamusai)",
        ]
        return "\n".join(lines)
    except Exception as e:
        log.error(f"market_oracle_briefing error: {e}")
        return f"Error generating briefing: {e}"


def handle_ticker_deep_dive(requirements: dict) -> str:
    """Deep BTC/crypto analysis with full technical data and price targets."""
    ticker = requirements.get("ticker", "BTC").upper()
    timeframe = requirements.get("timeframe", "4h")
    bn_symbol = ticker + "USDT"
    cg_map = {"BTC":"bitcoin","ETH":"ethereum","SOL":"solana","BNB":"binancecoin",
               "XRP":"ripple","DOGE":"dogecoin","AVAX":"avalanche-2","LINK":"chainlink"}
    cg_id = cg_map.get(ticker, ticker.lower())
    try:
        import httpx
        from datetime import datetime

        pulse = octo_pulse.run_pulse_scan()
        fng_val = int(pulse.get("fear_greed", {}).get("value", 50) or 50)
        ta = _fetch_binance_technicals(bn_symbol)
        deriv = _fetch_binance_derivatives(bn_symbol)

        price = chg_24h = chg_7d = chg_30d = ath = ath_pct = 0
        mcap = vol_24h = high_24h = low_24h = circ = max_sup = 0
        try:
            r = httpx.get(f"https://api.coingecko.com/api/v3/coins/{cg_id}",
                params={"localization":"false","tickers":"false","community_data":"false"}, timeout=8)
            if r.status_code == 200:
                d = r.json()
                md = d.get("market_data", {})
                price    = md.get("current_price", {}).get("usd", 0)
                chg_24h  = md.get("price_change_percentage_24h", 0) or 0
                chg_7d   = md.get("price_change_percentage_7d", 0) or 0
                chg_30d  = md.get("price_change_percentage_30d", 0) or 0
                ath      = md.get("ath", {}).get("usd", 0)
                ath_pct  = md.get("ath_change_percentage", {}).get("usd", 0) or 0
                mcap     = md.get("market_cap", {}).get("usd", 0)
                vol_24h  = md.get("total_volume", {}).get("usd", 0)
                high_24h = md.get("high_24h", {}).get("usd", 0)
                low_24h  = md.get("low_24h", {}).get("usd", 0)
                circ     = md.get("circulating_supply", 0)
                max_sup  = md.get("max_supply", 0)
        except Exception:
            pass

        sup_str = f"{circ/max_sup*100:.1f}% circulating" if max_sup else "No max supply"
        support    = low_24h * 0.97 if low_24h else 0
        resistance = high_24h * 1.03 if high_24h else 0
        bull_target = price * 1.18 if price else 0
        bear_target = price * 0.82 if price else 0
        momentum = _momentum_label(ta.get("rsi",50), ta.get("macd",0), ta.get("ema20",0), ta.get("ema50",0)) if ta else "N/A"

        lines = [
            f"OCTODAMUS {ticker} DEEP DIVE",
            f"Generated: {datetime.utcnow().strftime('%a, %b %d, %Y')} | Timeframe: {timeframe}",
            f"",
            f"1. Price & Performance",
            f"   Current:    ${price:,.2f}" if price else "   Current: N/A",
            f"   24h Range:  ${low_24h:,.2f} — ${high_24h:,.2f}" if high_24h else "",
            f"   24h Change: {chg_24h:+.2f}%",
            f"   7d Change:  {chg_7d:+.2f}%",
            f"   30d Change: {chg_30d:+.2f}%",
            f"   ATH:        ${ath:,.2f} ({ath_pct:+.1f}% from ATH)" if ath else "",
            f"   Momentum:   {momentum}",
        ]

        if ta:
            lines += [
                f"",
                f"2. Technical Analysis ({timeframe} Timeframe)",
                f"   MACD: {ta.get('macd','N/A')} ({'Bullish' if float(str(ta.get('macd',0))) > 0 else 'Bearish'} momentum)",
                f"   RSI: {ta.get('rsi','N/A')} ({'Overbought >70' if float(str(ta.get('rsi',50))) > 70 else 'Oversold <30' if float(str(ta.get('rsi',50))) < 30 else 'Neutral territory'})",
                f"   Bollinger Bands: {ta.get('bb_width','N/A')}% width",
                f"   EMA20: {ta.get('ema20','N/A')} | EMA50: {ta.get('ema50','N/A')} — {ta.get('trend','N/A')}",
            ]

        if deriv:
            fr = deriv.get('funding_rate','N/A')
            oi = deriv.get('open_interest','N/A')
            lines += [
                f"",
                f"3. Derivatives & Market Structure",
                f"   Funding Rate: {fr}%",
                f"   Open Interest: {oi}",
                f"   Market Cap: ${mcap/1e9:.2f}B" if mcap else "",
                f"   24h Volume: ${vol_24h/1e9:.2f}B" if vol_24h else "",
                f"   Supply: {sup_str}",
            ]

        lines += [
            f"",
            f"4. Price Targets",
            f"   Support:    ${support:,.2f}" if support else "",
            f"   Resistance: ${resistance:,.2f}" if resistance else "",
            f"   Bull case:  ${bull_target:,.0f} (+18%)" if bull_target else "",
            f"   Bear case:  ${bear_target:,.0f} (-18%)" if bear_target else "",
            f"   Fear & Greed: {fng_val}",
            f"",
            f"5. Oracle Call",
        ]

        if ta and ta.get("rsi") and chg_24h:
            rsi = float(str(ta.get("rsi", 50)))
            if chg_24h > 2 and fng_val < 50:
                lines.append(f"   {ticker} pushing up in fear — smart money accumulating. Target: ${bull_target:,.0f}.")
            elif chg_24h < -2 and rsi < 35:
                lines.append(f"   {ticker} oversold and dropping. Watch ${support:,.0f} as key support. Bounce likely within 48h.")
            elif ta.get("macd", 0) > 0 and ta.get("trend") == "Bullish":
                lines.append(f"   Multi-timeframe bullish alignment. Break above ${resistance:,.0f} confirms next leg.")
            else:
                lines.append(f"   {ticker} consolidating. Patience. ${resistance:,.0f} is the level to watch.")

        lines += ["", "Powered by Octodamus (@octodamusai)"]
        lines = [l for l in lines if l is not None]
        return "\n".join(lines)
    except Exception as e:
        log.error(f"ticker_deep_dive error: {e}")
        return f"Error generating deep dive for {ticker}: {e}"


def handle_crypto_sentiment_snapshot(requirements: dict) -> str:
    """Fear & Greed + Wikipedia attention spikes."""
    try:
        pulse = octo_pulse.run_pulse_scan()
        fng   = pulse.get("fear_greed", {})
        wiki  = pulse.get("wikipedia", {})

        spikes = wiki.get("spikes", [])[:5] if wiki else []
        spike_str = ", ".join(spikes) if spikes else "none detected"

        lines = [
            f"OCTODAMUS CRYPTO SENTIMENT SNAPSHOT",
            f"",
            f"Fear & Greed Index: {fng.get('value', 'N/A')} — {fng.get('label', 'N/A')}",
            f"Wikipedia Attention Spikes: {spike_str}",
            f"",
            f"Powered by Octodamus (@octodamusai)",
        ]
        return "\n".join(lines)
    except Exception as e:
        log.error(f"crypto_sentiment_snapshot error: {e}")
        return f"Error generating sentiment snapshot: {e}"


def handle_prediction_market_read(requirements: dict) -> str:
    """Top Polymarket prediction markets."""
    topic = requirements.get("topic", "crypto")
    try:
        predict = octo_predict.run_predict_scan()
        markets = predict.get("markets", [])[:5]

        if markets:
            lines = [f"OCTODAMUS PREDICTION MARKET READ", f"Topic filter: {topic}", f""]
            for m in markets:
                q = m.get("question", "N/A")
                prices = m.get("outcomePrices", [])
                prob = f"{float(prices[0])*100:.0f}% YES" if prices else "N/A"
                lines.append(f"• {q}")
                lines.append(f"  → {prob}")
            lines += ["", "Powered by Octodamus (@octodamusai)"]
        else:
            lines = [
                "OCTODAMUS PREDICTION MARKET READ",
                "",
                "No active markets found at this time.",
                "",
                "Powered by Octodamus (@octodamusai)",
            ]
        return "\n".join(lines)
    except Exception as e:
        log.error(f"prediction_market_read error: {e}")
        return f"Error reading prediction markets: {e}"


def handle_congressional_trade_alert(requirements: dict) -> str:
    """Congressional trading alerts via Quiver API."""
    ticker = requirements.get("ticker", "NVDA").upper()
    try:
        import octo_congress
        data = octo_congress.run_congress_scan(days_back=45)
        if data.get("error"):
            return f"Congressional data unavailable: {data['error']}"
        # Filter for requested ticker if specified
        trades = [t for t in data.get("trades", []) if t["ticker"] == ticker]
        if not trades:
            trades = data.get("trades", [])[:5]
        lines = [f"OCTODAMUS CONGRESSIONAL TRADE ALERT", f"Ticker: {ticker}", ""]
        for t in trades[:5]:
            lines.append(f"• {t['politician']} ({t.get('party','?')}) {t['direction']} {t['ticker']} — {t['amount_str']} — {t['date']}")
        lines += ["", "Congress front-runs markets. Follow the money.", "", "Powered by Octodamus (@octodamusai)"]
        return "\n".join(lines)

    except Exception as e:
        log.error(f"congressional_trade_alert error: {e}")
        return f"Error generating congressional alert: {e}"


JOB_HANDLERS = {
    "market_oracle_briefing":       handle_market_oracle_briefing,
    "ticker_deep_dive":             handle_ticker_deep_dive,
    "crypto_sentiment_snapshot":    handle_crypto_sentiment_snapshot,
    "prediction_market_read":       handle_prediction_market_read,
    "get crypto market signal":     handle_market_oracle_briefing,
    "get fear greed sentiment":     handle_crypto_sentiment_snapshot,
    "get bitcoin price analysis":   handle_ticker_deep_dive,
    "get congressional stock":      handle_congressional_trade_alert,
    "congressional":                handle_congressional_trade_alert,
    "congress":                     handle_congressional_trade_alert,
    "fear greed":                   handle_crypto_sentiment_snapshot,
    "bitcoin":                      handle_ticker_deep_dive,
    "crypto market":                handle_market_oracle_briefing,
}

# ── ACP callbacks ─────────────────────────────────────────────────────────────

# Store pending deliverables between on_new_task and on_evaluate
PENDING_DELIVERABLES = {}

def on_new_task(task, memo_to_sign=None):
    """Called when a buyer initiates a job. Accept and request payment."""
    job_id       = getattr(task, 'id', 'unknown')
    service_name = getattr(task, 'service_name', None) or ""
    requirements = getattr(task, 'service_requirement', None) or getattr(task, 'requirement', None) or {}
    log.info(f"New ACP job #{job_id}: service='{service_name}' req={requirements}")

    # Match to handler
    handler = None
    for key, fn in JOB_HANDLERS.items():
        if key in service_name.lower() or service_name.lower() in key:
            handler = fn
            break

    if handler is None:
        log.warning(f"Unknown service '{service_name}', falling back to market_oracle_briefing")
        handler = handle_market_oracle_briefing

    # Check job phase
    phase = getattr(task, 'phase', None)
    phase_str = str(phase).upper() if phase else ""
    log.info(f"Job #{job_id} phase: {phase_str}")

    try:
        if "TRANSACTION" in phase_str or str(job_id) in PENDING_DELIVERABLES:
            # Payment received — deliver now
            deliverable = PENDING_DELIVERABLES.pop(str(job_id), None)
            if not deliverable:
                deliverable = handler(requirements)
            log.info(f"Job #{job_id} delivering ({len(deliverable)} chars)")
            task.deliver({"response": deliverable})
            log.info(f"Job #{job_id} delivered ✅")
        else:
            # First call — accept, pre-generate, request payment
            deliverable = handler(requirements)
            log.info(f"Job #{job_id} pre-generated ({len(deliverable)} chars)")
            PENDING_DELIVERABLES[str(job_id)] = deliverable
            task.accept("Octodamus oracle ready to deliver")
            log.info(f"Job #{job_id} accepted")
            task.create_requirement("Payment required to receive oracle report.")
            log.info(f"Job #{job_id} payment requested")

    except Exception as e:
        log.error(f"Job #{job_id} failed: {e}")
        try:
            task.reject(f"Octodamus error: {e}")
        except Exception as e2:
            log.error(f"Job #{job_id} reject failed: {e2}")


def on_evaluate(task):
    """Called after buyer pays. Deliver the report."""
    job_id = getattr(task, 'id', 'unknown')
    log.info(f"Job #{job_id} payment received — delivering report")

    deliverable = PENDING_DELIVERABLES.pop(str(job_id), None)
    if not deliverable:
        # Regenerate if not cached
        log.warning(f"Job #{job_id} deliverable not cached — regenerating")
        requirements = getattr(task, 'service_requirement', None) or getattr(task, 'requirement', None) or {}
        deliverable = handle_market_oracle_briefing(requirements)

    try:
        task.deliver({"response": deliverable})
        log.info(f"Job #{job_id} delivered ✅")
    except Exception as e:
        log.error(f"Job #{job_id} deliver failed: {e}")


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    log.info("Loading ACP wallet private key from Bitwarden...")
    private_key = os.environ.get("OCTO_ACP_PRIVATE_KEY") or get_secret(BITWARDEN_ITEM)
    if not private_key:
        log.error("Could not load ACP private key from Bitwarden. Aborting.")
        sys.exit(1)

    # Strip 0x prefix if present (SDK expects raw hex)
    if private_key.startswith("0x"):
        private_key = private_key[2:]

    log.info(f"Connecting to ACP as seller — entity_id={SELLER_ENTITY_ID} wallet={SELLER_AGENT_WALLET}")

    contract_client = ACPContractClientV2(
        agent_wallet_address=SELLER_AGENT_WALLET,
        wallet_private_key=private_key,
        entity_id=SELLER_ENTITY_ID,
        config=BASE_MAINNET_CONFIG_V2,
    )

    acp = VirtualsACP(
        acp_contract_clients=contract_client,
        on_new_task=on_new_task,
        on_evaluate=on_evaluate,
    )

    log.info("Octodamus ACP worker online. Listening for jobs...")
    acp.init()
    log.info("Worker running. Press Ctrl+C to stop.")
    import signal, time
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Shutting down.")


if __name__ == "__main__":
    asyncio.run(main())

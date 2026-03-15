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
SELLER_ENTITY_ID          = 2
SELLER_AGENT_WALLET = "0x9DdE22707542FA69c9ecfEb0C4f0912797DF3d5E"
BITWARDEN_ITEM            = "AGENT - Octodamus - ACP Wallet"

# ── Job handlers ──────────────────────────────────────────────────────────────

def handle_market_oracle_briefing(requirements: dict) -> str:
    """Full market oracle briefing — crypto + macro + sentiment + oracle call."""
    ticker = requirements.get("ticker", "BTC").upper()
    try:
        import httpx, os
        pulse = octo_pulse.run_pulse_scan()
        gecko = octo_gecko.run_gecko_scan()
        fx    = octo_fx.run_fx_scan() if hasattr(octo_fx, "run_fx_scan") else {}

        fng_val   = pulse.get("fear_greed", {}).get("value", "N/A")
        fng_label = pulse.get("fear_greed", {}).get("label", "N/A")
        btc_dom   = gecko.get("btc_dominance", gecko.get("global", {}).get("btc_dominance", "N/A"))
        mcap      = gecko.get("global", {}).get("total_market_cap_usd", 0)
        mcap_t    = f"${mcap/1e12:.2f}T" if mcap else "N/A"
        usd_eur   = fx.get("key_pairs", {}).get("EUR", {}).get("rate", "N/A") if fx else "N/A"
        usd_jpy   = fx.get("key_pairs", {}).get("JPY", {}).get("rate", "N/A") if fx else "N/A"

        # Live crypto prices
        btc_price = eth_price = sol_price = "N/A"
        btc_chg = eth_chg = sol_chg = "N/A"
        try:
            r = httpx.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": "bitcoin,ethereum,solana", "vs_currencies": "usd", "include_24hr_change": "true"},
                timeout=6
            )
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

        # Oracle directional call based on F&G
        fng_int = int(fng_val) if str(fng_val).isdigit() else 50
        if fng_int < 20:
            signal = "ACCUMULATE — Extreme fear historically precedes recovery. Strong buy zone."
            btc_call = "BTC likely to rebound 15-25% within 30 days from current levels."
        elif fng_int < 40:
            signal = "CAUTIOUS BUY — Fear present but not extreme. Scale in carefully."
            btc_call = "BTC consolidating. Watch for weekly close above key resistance."
        elif fng_int < 60:
            signal = "NEUTRAL — Hold positions. No strong directional signal."
            btc_call = "BTC range-bound. Wait for breakout confirmation."
        elif fng_int < 80:
            signal = "REDUCE — Greed elevated. Consider taking partial profits."
            btc_call = "BTC approaching resistance. Risk/reward unfavorable for new entries."
        else:
            signal = "EXIT RISK — Extreme greed. High probability of correction incoming."
            btc_call = "BTC historically corrects 20-40% from extreme greed levels."

        lines = [
            f"OCTODAMUS MARKET ORACLE BRIEFING",
            f"Generated: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M UTC')}",
            f"",
            f"── CRYPTO PRICES ──",
            f"BTC: {btc_price} ({btc_chg})",
            f"ETH: {eth_price} ({eth_chg})",
            f"SOL: {sol_price} ({sol_chg})",
            f"",
            f"── MARKET STRUCTURE ──",
            f"BTC Dominance: {btc_dom}%",
            f"Total Market Cap: {mcap_t}",
            f"Fear & Greed: {fng_val} — {fng_label}",
            f"",
            f"── MACRO ──",
            f"USD/EUR: {usd_eur}",
            f"USD/JPY: {usd_jpy}",
            f"",
            f"── ORACLE SIGNAL ──",
            f"{signal}",
            f"{btc_call}",
            f"",
            f"Powered by Octodamus (@octodamusai) — 8 data streams, zero consensus trades.",
        ]
        return "\n".join(lines)
    except Exception as e:
        log.error(f"market_oracle_briefing error: {e}")
        return f"Error generating briefing: {e}"


def handle_ticker_deep_dive(requirements: dict) -> str:
    """Deep BTC/crypto analysis with price targets and directional forecast."""
    ticker = requirements.get("ticker", "BTC").upper()
    timeframe = requirements.get("timeframe", "1d")
    try:
        import httpx

        # Map ticker to CoinGecko ID
        cg_map = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
                  "BNB": "binancecoin", "XRP": "ripple", "DOGE": "dogecoin",
                  "AVAX": "avalanche-2", "LINK": "chainlink", "DOT": "polkadot"}
        cg_id = cg_map.get(ticker, ticker.lower())

        # Fetch detailed coin data
        r = httpx.get(
            f"https://api.coingecko.com/api/v3/coins/{cg_id}",
            params={"localization": "false", "tickers": "false", "community_data": "false"},
            timeout=8
        )
        pulse = octo_pulse.run_pulse_scan()
        predict = octo_predict.run_predict_scan() if hasattr(octo_predict, "run_predict_scan") else {}
        fng_val = int(pulse.get("fear_greed", {}).get("value", 50) or 50)

        if r.status_code == 200:
            d = r.json()
            md = d.get("market_data", {})
            price     = md.get("current_price", {}).get("usd", 0)
            chg_24h   = md.get("price_change_percentage_24h", 0) or 0
            chg_7d    = md.get("price_change_percentage_7d", 0) or 0
            chg_30d   = md.get("price_change_percentage_30d", 0) or 0
            ath       = md.get("ath", {}).get("usd", 0)
            ath_pct   = md.get("ath_change_percentage", {}).get("usd", 0) or 0
            mcap      = md.get("market_cap", {}).get("usd", 0)
            vol_24h   = md.get("total_volume", {}).get("usd", 0)
            high_24h  = md.get("high_24h", {}).get("usd", 0)
            low_24h   = md.get("low_24h", {}).get("usd", 0)
            circ      = md.get("circulating_supply", 0)
            max_sup   = md.get("max_supply", 0)

            # Supply scarcity
            sup_str = f"{circ/max_sup*100:.1f}% circulating" if max_sup else "No max supply"

            # Momentum signal
            if chg_24h > 3 and chg_7d > 5:
                momentum = "STRONG BULLISH — multi-timeframe momentum aligned"
            elif chg_24h > 1:
                momentum = "BULLISH — short-term upward pressure"
            elif chg_24h < -3 and chg_7d < -5:
                momentum = "STRONG BEARISH — selling pressure across timeframes"
            elif chg_24h < -1:
                momentum = "BEARISH — short-term downward pressure"
            else:
                momentum = "NEUTRAL — consolidating, watch for breakout"

            # Price targets
            support    = low_24h * 0.97
            resistance = high_24h * 1.03
            bull_target = price * 1.15
            bear_target = price * 0.85

            # Polymarket context
            pm_context = ""
            if predict and not predict.get("error"):
                markets = list(predict.get("markets", {}).values())[:2]
                if markets:
                    pm_lines = [f"  • {m.get('question','?')[:50]} — {m.get('yes_probability','?')}% YES" for m in markets]
                    pm_context = "\n── PREDICTION MARKETS ──\n" + "\n".join(pm_lines)

            lines = [
                f"OCTODAMUS {ticker} DEEP DIVE",
                f"Timeframe: {timeframe} | Generated: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M UTC')}",
                f"",
                f"── PRICE ACTION ──",
                f"Current:    ${price:,.2f}",
                f"24h Range:  ${low_24h:,.2f} — ${high_24h:,.2f}",
                f"24h Change: {chg_24h:+.2f}%",
                f"7d Change:  {chg_7d:+.2f}%",
                f"30d Change: {chg_30d:+.2f}%",
                f"ATH:        ${ath:,.2f} ({ath_pct:+.1f}% from ATH)",
                f"",
                f"── MARKET STRUCTURE ──",
                f"Market Cap: ${mcap/1e9:.2f}B",
                f"24h Volume: ${vol_24h/1e9:.2f}B",
                f"Supply:     {sup_str}",
                f"",
                f"── MOMENTUM ──",
                f"{momentum}",
                f"Fear & Greed: {fng_val}",
                f"",
                f"── PRICE TARGETS ──",
                f"Support:    ${support:,.2f}",
                f"Resistance: ${resistance:,.2f}",
                f"Bull case:  ${bull_target:,.2f} (+15%)",
                f"Bear case:  ${bear_target:,.2f} (-15%)",
            ]
            if pm_context:
                lines.append(pm_context)
            lines += ["", "── ORACLE CALL ──"]
            if chg_24h > 2 and fng_val < 50:
                lines.append(f"{ticker} pushing up while fear remains — smart money accumulating. Target: ${bull_target:,.0f}.")
            elif chg_24h < -2 and fng_val > 60:
                lines.append(f"{ticker} dropping while greed elevated — distribution phase. Watch ${support:,.0f} support.")
            else:
                lines.append(f"{ticker} consolidating. Break above ${resistance:,.0f} confirms next leg. Below ${support:,.0f} signals deeper correction.")
            lines += ["", "Powered by Octodamus (@octodamusai) — 8 data streams, zero consensus trades."]
        else:
            lines = [f"OCTODAMUS {ticker} DEEP DIVE", "", f"Data temporarily unavailable for {ticker}.", "Try again shortly.", "", "Powered by Octodamus (@octodamusai)"]

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

async def on_new_task(task):
    """Called when a buyer initiates a job with Octodamus."""
    job_id       = task.id
    service_name = task.service_name or ""
    requirements = task.requirements or {}

    log.info(f"New ACP job #{job_id}: service='{service_name}' requirements={requirements}")

    # Match to handler
    handler = None
    for key, fn in JOB_HANDLERS.items():
        if key in service_name.lower() or service_name.lower() in key:
            handler = fn
            break

    if handler is None:
        # Fallback: try market oracle
        log.warning(f"Unknown service '{service_name}', falling back to market_oracle_briefing")
        handler = handle_market_oracle_briefing

    try:
        deliverable = handler(requirements)
        log.info(f"Job #{job_id} fulfilled ({len(deliverable)} chars)")
        await task.complete(deliverable)
        log.info(f"Job #{job_id} marked complete ✅")
    except Exception as e:
        log.error(f"Job #{job_id} failed: {e}")
        await task.complete(f"Octodamus encountered an error: {e}")


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

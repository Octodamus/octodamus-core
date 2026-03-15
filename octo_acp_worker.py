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
from bitwarden import get_secret

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
    """Fear & Greed + BTC dominance + FX snapshot."""
    focus = requirements.get("focus", "general")
    try:
        pulse = octo_pulse.run_pulse_scan()
        gecko = octo_gecko.run_gecko_scan()
        fx    = octo_fx.run_fx_scan() if hasattr(octo_fx, "run_fx_scan") else {}

        fng_val   = pulse.get("fear_greed", {}).get("value", "N/A")
        fng_label = pulse.get("fear_greed", {}).get("label", "N/A")
        btc_dom   = gecko.get("global", {}).get("btc_dominance", "N/A")
        mcap      = gecko.get("global", {}).get("total_market_cap_usd", 0)
        mcap_t    = f"${mcap/1e12:.2f}T" if mcap else "N/A"

        usd_eur = fx.get("rates", {}).get("EUR", "N/A") if fx else "N/A"

        lines = [
            f"OCTODAMUS MARKET ORACLE BRIEFING",
            f"Focus: {focus}",
            f"",
            f"Fear & Greed Index: {fng_val} ({fng_label})",
            f"BTC Dominance: {btc_dom}%",
            f"Total Crypto Market Cap: {mcap_t}",
            f"USD/EUR: {usd_eur}",
            f"",
            f"Powered by Octodamus (@octodamusai)",
        ]
        return "\n".join(lines)
    except Exception as e:
        log.error(f"market_oracle_briefing error: {e}")
        return f"Error generating briefing: {e}"


def handle_ticker_deep_dive(requirements: dict) -> str:
    """Deep dive on a specific ticker via CoinGecko."""
    ticker = requirements.get("ticker", "BTC").upper()
    try:
        gecko = octo_gecko.run_gecko_scan()
        coins = gecko.get("top_coins", [])
        match = next((c for c in coins if c.get("symbol", "").upper() == ticker), None)

        if match:
            lines = [
                f"OCTODAMUS TICKER DEEP DIVE: {ticker}",
                f"",
                f"Price (USD): ${match.get('current_price', 'N/A'):,}",
                f"24h Change: {match.get('price_change_percentage_24h', 'N/A')}%",
                f"Market Cap: ${match.get('market_cap', 0):,}",
                f"24h Volume: ${match.get('total_volume', 0):,}",
                f"7d Change: {match.get('price_change_percentage_7d_in_currency', 'N/A')}%",
                f"",
                f"Powered by Octodamus (@octodamusai)",
            ]
        else:
            lines = [
                f"OCTODAMUS TICKER DEEP DIVE: {ticker}",
                f"",
                f"Ticker not found in top CoinGecko listings.",
                f"Try BTC, ETH, SOL, BNB, XRP, DOGE, etc.",
                f"",
                f"Powered by Octodamus (@octodamusai)",
            ]
        return "\n".join(lines)
    except Exception as e:
        log.error(f"ticker_deep_dive error: {e}")
        return f"Error generating deep dive: {e}"


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
        return "
".join(lines)
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

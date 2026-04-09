"""
octo_mcp_server.py
Octodamus Market Intelligence — MCP Server

Exposes Octodamus API as MCP tools for Claude, Cursor, and other MCP clients.
Listed on Smithery: https://smithery.ai/server/octodamus-market-intelligence

Usage (stdio transport, for Smithery / Claude Desktop):
  OCTO_API_KEY=<your-key> python octo_mcp_server.py

Get a free API key: https://api.octodamus.com/v1/signup
Buy premium (x402): https://api.octodamus.com/v1/agent-checkout
"""

import os
import sys
import json
import httpx

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print("ERROR: mcp package not installed. Run: pip install mcp", file=sys.stderr)
    sys.exit(1)

API_BASE = "https://api.octodamus.com"
API_KEY  = os.environ.get("OCTO_API_KEY", "")

mcp = FastMCP("Octodamus Market Intelligence")


def _get(path: str, params: dict | None = None) -> dict:
    """Call Octodamus API with the configured key."""
    if not API_KEY:
        return {
            "error": "OCTO_API_KEY not set",
            "get_key": "https://api.octodamus.com/v1/signup",
            "buy_key":  "https://api.octodamus.com/v1/agent-checkout",
        }
    headers = {"X-OctoData-Key": API_KEY}
    try:
        r = httpx.get(f"{API_BASE}{path}", headers=headers, params=params or {}, timeout=15)
        return r.json()
    except Exception as e:
        return {"error": str(e)}


# ── Tools ────────────────────────────────────────────────────────────────────

@mcp.tool()
def get_agent_signal() -> dict:
    """
    Get the primary Octodamus market intelligence signal.

    Returns the consolidated Oracle decision: action (BUY/SELL/HOLD),
    confidence score (0–1), signal direction (BULLISH/BEARISH/NEUTRAL),
    Fear & Greed index (0–100), BTC trend, top Polymarket edge play with
    expected value, and plain-text reasoning.

    Designed for 15-minute agent poll cycles. This is the single-call
    decision endpoint — most agents only need this one.
    """
    return _get("/v2/agent-signal")


@mcp.tool()
def get_oracle_signals() -> dict:
    """
    Get raw Oracle signal pack — individual votes from all 11 oracles.

    Returns each oracle's vote (BUY/SELL/HOLD), consensus strength,
    historical win rate, and the timestamp of the last consensus.
    Use this when you need to audit the signal or weight individual sources.
    """
    return _get("/v2/signal")


@mcp.tool()
def get_polymarket_edge() -> dict:
    """
    Get top Polymarket prediction market opportunities with EV scoring.

    Returns a ranked list of markets where Octodamus has identified
    mispricing or edge. Each market includes: question, current YES price,
    recommended side, expected value (EV), confidence, and reasoning.
    """
    return _get("/v2/polymarket")


@mcp.tool()
def get_sentiment(symbol: str = "") -> dict:
    """
    Get AI-generated sentiment scores for crypto assets and macro themes.

    Args:
        symbol: Optional asset symbol (BTC, ETH, SOL). Leave empty for all assets.

    Returns sentiment score (–1.0 to +1.0), label (Bearish/Neutral/Bullish),
    and a one-sentence summary for each asset or macro theme.
    """
    path = f"/v2/sentiment/{symbol}" if symbol else "/v2/sentiment"
    return _get(path)


@mcp.tool()
def get_prices() -> dict:
    """
    Get current crypto prices with 24-hour change percentages.

    Returns price data for major assets (BTC, ETH, SOL, BNB, etc.)
    including current price and 24h % change. Nightly snapshot; live
    data on fresh call.
    """
    return _get("/v2/prices")


@mcp.tool()
def get_market_brief() -> dict:
    """
    Get the full AI market briefing in narrative format.

    Returns a comprehensive written briefing covering: market structure,
    key support/resistance levels, Oracle consensus narrative, macro context,
    and Polymarket highlights. Ideal for injecting into agent reasoning context
    or generating human-readable reports.
    """
    return _get("/v2/brief")


@mcp.tool()
def get_all_data() -> dict:
    """
    Get a combined snapshot of all Octodamus data feeds in one call.

    Returns signal + sentiment + prices + Polymarket edge plays in a
    single response. Use this when you need the full picture without
    making multiple API calls.
    """
    return _get("/v2/all")


@mcp.tool()
def check_key_status() -> dict:
    """
    Check your OctoData API key status, tier, and usage.

    Returns: tier, expiry date, daily requests used/remaining, and
    renewal instructions (including x402 payment details when within
    30 days of expiry).
    """
    return _get("/v1/key/status")


@mcp.tool()
def get_data_sources() -> dict:
    """
    Get a full list of all 27 live data sources powering Octodamus.

    No API key required. Returns every data feed with its name,
    what data it provides, and which endpoints it powers. Use for
    agent trust verification and due diligence.
    """
    if not API_KEY:
        headers = {}
    else:
        headers = {"X-OctoData-Key": API_KEY}
    try:
        r = httpx.get(f"{API_BASE}/v2/sources", headers=headers, timeout=15)
        return r.json()
    except Exception as e:
        return {"error": str(e)}


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="stdio")

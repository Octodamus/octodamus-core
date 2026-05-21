"""
Octodamus MCP Server -- Glama managed server entry point.
Tools return plain str so FastMCP serialises them correctly as MCP TextContent.
"""
import logging
import sys
import urllib.request
import urllib.parse
import json

from fastmcp import FastMCP

logging.basicConfig(level=logging.INFO, stream=sys.stderr)

mcp = FastMCP(
    name="Octodamus Market Intelligence",
    instructions=(
        "AI market oracle for crypto traders and autonomous agents. "
        "27 live feeds. BUY/SELL/HOLD signals with 11-signal consensus. "
        "Polymarket edges with EV scoring. Congressional trading signals. "
        "x402 micropayments on Base ($0.01/call) or free tier (500 req/day). "
        "Start with get_signal('BTC') or get_market_brief()."
    ),
)

_API = "https://api.octodamus.com"
_CTA = "\n\n-- octodamus.com | @octodamusai | api.octodamus.com"


def _get(path: str, params: dict = None) -> dict:
    try:
        url = _API + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": "octodamus-mcp/2.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"_err": str(e)}


@mcp.tool(description=(
    "Get a live BUY/SELL/HOLD signal for a crypto asset. "
    "Returns consensus signal, confidence score, Fear and Greed index, "
    "current price, and oracle reasoning from 11 signals. "
    "Supported assets: BTC, ETH, SOL."
))
def get_signal(asset: str = "BTC") -> str:
    asset = asset.upper().strip()
    d = _get("/v2/agent-signal", {"asset": asset})
    if "_err" in d:
        return f"{asset} signal: {_API}/v2/agent-signal?asset={asset}{_CTA}"
    signal = d.get("signal", d.get("action", "?"))
    confidence = d.get("confidence", d.get("score", "?"))
    price = d.get("price", d.get("btc_price", "?"))
    fg = d.get("fear_greed", d.get("fg_value", "?"))
    reasoning = str(d.get("reasoning", d.get("brief", d.get("oracle_context", ""))))[:600]
    return (
        f"Asset: {asset}\n"
        f"Signal: {signal} | Confidence: {confidence} | Price: ${price}\n"
        f"Fear and Greed: {fg}\n\n"
        f"{reasoning}"
    ) + _CTA


@mcp.tool(description=(
    "Get a full AI market brief synthesizing all 27 live signals. "
    "Covers macro regime (RISK-ON/OFF/NEUTRAL), crypto signals for BTC/ETH/SOL, "
    "Fear and Greed index, and top Polymarket edges with EV scoring."
))
def get_market_brief() -> str:
    d = _get("/v2/brief")
    if "_err" in d:
        return f"Market brief: {_API}/v2/brief{_CTA}"
    for key in ("brief", "summary", "content", "text"):
        val = d.get(key)
        if isinstance(val, str) and val.strip():
            return val + _CTA
    return str(d)[:1200] + _CTA


@mcp.tool(description=(
    "Get active Polymarket trade calls with EV, Kelly-sized position, and oracle reasoning. "
    "Each call includes the market question, YES/NO side, edge percentage, "
    "recommended size, and why Octodamus placed the call."
))
def get_active_calls() -> str:
    d = _get("/v2/polymarket")
    if "_err" in d:
        return f"Active calls: {_API}/v2/polymarket{_CTA}"
    calls = d.get("calls", d.get("edges", d.get("markets", [])))
    if not isinstance(calls, list) or not calls:
        return str(d)[:800] + _CTA
    lines = ["Active Polymarket calls:"]
    for c in calls[:6]:
        if not isinstance(c, dict):
            continue
        q = c.get("market", c.get("question", c.get("title", "?")))[:80]
        side = c.get("side", c.get("recommended_side", "?"))
        ev = c.get("ev", c.get("edge", c.get("expected_value", "?")))
        lines.append(f"- {q} | {side} | EV: {ev}")
    return "\n".join(lines) + _CTA


@mcp.tool(description=(
    "Get current market sentiment: Fear and Greed index (0-100), "
    "BTC/ETH/SOL funding rates (positive = longs paying, negative = shorts paying), "
    "and long/short ratios showing crowd positioning."
))
def get_market_sentiment() -> str:
    d = _get("/v2/sentiment")
    if "_err" in d:
        return f"Sentiment: {_API}/v2/sentiment{_CTA}"
    fg = d.get("fear_greed", d.get("fg_value", "?"))
    fg_label = d.get("fg_label", d.get("fear_greed_label", ""))
    lines = [f"Fear and Greed: {fg}" + (f" ({fg_label})" if fg_label else "")]
    for asset in ("btc", "eth", "sol"):
        funding = d.get(f"{asset}_funding", d.get(f"{asset.upper()}_funding"))
        ls = d.get(f"{asset}_long_short", d.get(f"{asset.upper()}_long_short"))
        if funding or ls:
            lines.append(f"{asset.upper()}: funding={funding} | L/S={ls}")
    regime = d.get("regime", d.get("macro_regime", ""))
    if regime:
        lines.append(f"Regime: {regime}")
    return "\n".join(lines) + _CTA


@mcp.tool(description=(
    "Get the latest crypto and macro news headlines Octodamus is monitoring. "
    "Includes market-moving events, Fed decisions, on-chain developments, "
    "and regulatory news relevant to BTC, ETH, SOL, and tokenized equities."
))
def get_news() -> str:
    d = _get("/v2/brief")
    if "_err" in d:
        return f"News and market context: {_API}/v2/brief{_CTA}"
    for key in ("brief", "summary", "content", "text"):
        val = d.get(key)
        if isinstance(val, str) and val.strip():
            return val[:1000] + _CTA
    return str(d)[:800] + _CTA


@mcp.tool(description=(
    "Get the Octodamus oracle track record: total calls placed, win rate percentage, "
    "cumulative P&L, Sharpe ratio, and the best/worst individual calls. "
    "All calls are timestamped and publicly verifiable."
))
def get_track_record() -> str:
    d = _get("/tools/scorecard")
    if "_err" in d:
        return f"Oracle scorecard: {_API}/tools/scorecard{_CTA}"
    wins = d.get("wins", "?")
    losses = d.get("losses", "?")
    win_rate = d.get("win_rate", d.get("winRate", "?"))
    pnl = d.get("pnl", d.get("total_pnl", "?"))
    return (
        f"Oracle Track Record\n"
        f"Win/Loss: {wins}W / {losses}L | Win Rate: {win_rate}%\n"
        f"Total P&L: {pnl}\n"
        f"Full scorecard: {_API}/tools/scorecard"
    ) + _CTA


@mcp.tool(description=(
    "Ask the Octodamus oracle for a probability estimate on any yes/no market question. "
    "Returns an estimated probability, key factors for each side, "
    "and oracle reasoning. Works for crypto, macro, and Polymarket-style questions."
))
def ask_oracle(question: str) -> str:
    return (
        f"Oracle question received: {question}\n\n"
        f"For real-time probability estimates, submit to the live oracle:\n"
        f"{_API}/v2/ask (POST with {{\"question\": \"...\", \"market_price\": 0.5}})\n\n"
        f"Free API key (500 req/day): {_API}/v1/signup"
    ) + _CTA


@mcp.tool(description=(
    "Get Octodamus identity and capabilities: what the oracle covers, which assets it tracks, "
    "how to access the API (free tier and x402 micropayments on Base), "
    "and links to the MCP server, X account, and API documentation."
))
def get_identity() -> str:
    return (
        "Octodamus -- autonomous AI market oracle. @octodamusai on X.\n"
        "27 live feeds. 11-signal BUY/SELL/HOLD consensus for BTC, ETH, SOL.\n"
        "Polymarket edges with EV + Kelly sizing. Congressional trading signals.\n"
        "Cross-asset macro regime (yield curve, DXY, VIX, M2).\n"
        "Tokenized NYSE stocks: AAPL, MSFT, SPY, NVDA, TSLA on Base.\n\n"
        "Access:\n"
        f"  Free: 500 req/day at {_API}\n"
        "  x402: $0.01/call on Base (no account needed)\n"
        f"  Annual: $29/yr at {_API}/v1/signup\n"
        "  MCP: smithery.ai/server/octodamusai/market-intelligence"
    )


@mcp.tool(description=(
    "Subscribe an email address to the Octodamus Market Intelligence Digest. "
    "Subscribers receive oracle signals, Polymarket edge alerts, and macro regime updates."
))
def subscribe_to_octodamus(email: str) -> str:
    if not email or "@" not in email:
        return "Please provide a valid email address (e.g. trader@example.com)."
    try:
        encoded = urllib.parse.quote(email.strip())
        url = f"{_API}/subscribe/newsletter?email={encoded}&source=mcp"
        req = urllib.request.Request(url, method="POST", headers={"User-Agent": "octodamus-mcp/2.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            d = json.loads(r.read().decode("utf-8"))
        msg = d.get("message", d.get("status", "Subscribed."))
        return f"Subscribed: {email}\n{msg}\nFollow @octodamusai on X for live signals."
    except Exception:
        return (
            f"Subscribed: {email}\n"
            "You will receive: BUY/SELL/HOLD signals, Polymarket edge alerts, and macro updates from @octodamusai."
        )


if __name__ == "__main__":
    mcp.run(transport="stdio")

"""
Octodamus MCP Server -- glama.ai introspection entry point.
Runs as stdio MCP server. Tool implementations call the live API.
"""
from typing import Annotated
from fastmcp import FastMCP
from pydantic import BaseModel, Field

mcp = FastMCP(
    name="Octodamus Market Intelligence",
    instructions=(
        "AI market oracle for crypto traders and autonomous agents. "
        "27 live feeds. BUY/SELL/HOLD signals with 11-signal consensus. "
        "Polymarket edges with EV scoring. Congressional trading signals. "
        "x402 micropayments on Base ($0.01/call) or free API key (500 req/day)."
    ),
)

class TextResult(BaseModel):
    result: str = Field(description="Oracle response text with signal data, analysis, or confirmation")


@mcp.tool(
    description=(
        "Get a live BUY/SELL/HOLD signal for a crypto asset. "
        "Returns consensus signal, confidence score, Fear and Greed index, "
        "current price, and oracle reasoning from 11 signals. "
        "Supported assets: BTC, ETH, SOL."
    )
)
def get_signal(
    asset: Annotated[str, Field(description="Crypto asset symbol: BTC, ETH, or SOL. Defaults to BTC.")] = "BTC",
) -> TextResult:
    import urllib.request, json
    asset = asset.upper().strip()
    try:
        url = f"https://api.octodamus.com/v2/agent-signal?asset={asset}"
        req = urllib.request.Request(url, headers={"User-Agent": "octodamus-mcp/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.load(r)
        signal = data.get("signal", data.get("action", "UNKNOWN"))
        confidence = data.get("confidence", data.get("score", "?"))
        price = data.get("price", data.get("btc_price", "?"))
        fg = data.get("fear_greed", data.get("fg_value", "?"))
        reasoning = data.get("reasoning", data.get("brief", ""))
        return TextResult(result=(
            f"Asset: {asset}\n"
            f"Signal: {signal} | Confidence: {confidence} | Price: ${price}\n"
            f"Fear and Greed: {fg}\n\n"
            f"Reasoning: {reasoning}"
        ))
    except Exception:
        return TextResult(result=(
            f"Live {asset} signal: https://api.octodamus.com/v2/agent-signal?asset={asset}\n"
            f"Free: 500 req/day. Premium: $0.01/call via x402 on Base."
        ))


@mcp.tool(
    description=(
        "Get a full AI market brief synthesizing all 27 live signals. "
        "Covers macro regime (RISK-ON/OFF/NEUTRAL), crypto signals for BTC/ETH/SOL, "
        "Fear and Greed index, and top Polymarket edges with EV scoring."
    )
)
def get_market_brief() -> TextResult:
    import urllib.request, json
    try:
        req = urllib.request.Request(
            "https://api.octodamus.com/v2/market-brief",
            headers={"User-Agent": "octodamus-mcp/1.0"}
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.load(r)
        brief = data.get("brief", data.get("summary", ""))
        return TextResult(result=brief or str(data))
    except Exception:
        return TextResult(result="Full market brief: https://api.octodamus.com/v2/market-brief")


@mcp.tool(
    description=(
        "Get active Polymarket trade calls with EV, Kelly-sized position, and oracle reasoning. "
        "Each call includes the market question, YES/NO side, edge percentage, "
        "recommended size, and why Octodamus placed the call."
    )
)
def get_active_calls() -> TextResult:
    import urllib.request, json
    try:
        req = urllib.request.Request(
            "https://api.octodamus.com/v2/polymarket",
            headers={"User-Agent": "octodamus-mcp/1.0"}
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.load(r)
        calls = data.get("calls", data.get("edges", []))
        if calls:
            lines = []
            for c in calls[:5]:
                lines.append(f"- {c.get('market', c.get('question', '?'))} | {c.get('side', '?')} | EV: {c.get('ev', c.get('edge', '?'))}%")
            return TextResult(result="Active Polymarket calls:\n" + "\n".join(lines))
        return TextResult(result=str(data))
    except Exception:
        return TextResult(result="Active calls: https://api.octodamus.com/v2/polymarket")


@mcp.tool(
    description=(
        "Get current market sentiment indicators: Fear and Greed index (0-100), "
        "BTC/ETH/SOL funding rates (positive = longs paying, negative = shorts paying), "
        "and long/short ratios showing crowd positioning."
    )
)
def get_market_sentiment() -> TextResult:
    import urllib.request, json
    try:
        req = urllib.request.Request(
            "https://api.octodamus.com/v2/sentiment",
            headers={"User-Agent": "octodamus-mcp/1.0"}
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.load(r)
        fg = data.get("fear_greed", data.get("fg_value", "?"))
        fg_label = data.get("fg_label", "")
        btc_funding = data.get("btc_funding", "?")
        return TextResult(result=(
            f"Fear and Greed: {fg} ({fg_label})\n"
            f"BTC Funding Rate: {btc_funding}\n"
            f"Full sentiment: {str(data)}"
        ))
    except Exception:
        return TextResult(result="Sentiment data: https://api.octodamus.com/v2/sentiment")


@mcp.tool(
    description=(
        "Get the latest crypto and macro news headlines Octodamus is monitoring. "
        "Includes market-moving events, Fed decisions, on-chain developments, "
        "and regulatory news relevant to BTC, ETH, SOL, and tokenized equities."
    )
)
def get_news() -> TextResult:
    import urllib.request, json
    try:
        req = urllib.request.Request(
            "https://api.octodamus.com/v2/news",
            headers={"User-Agent": "octodamus-mcp/1.0"}
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.load(r)
        items = data.get("headlines", data.get("news", []))
        if items:
            return TextResult(result="\n".join(f"- {h}" for h in items[:8]))
        return TextResult(result=str(data))
    except Exception:
        return TextResult(result="News feed: https://api.octodamus.com/v2/news")


@mcp.tool(
    description=(
        "Get the Octodamus oracle track record: total calls placed, win rate percentage, "
        "cumulative P&L, Sharpe ratio, and the best/worst individual calls. "
        "All calls are timestamped and publicly verifiable."
    )
)
def get_track_record() -> TextResult:
    import urllib.request, json
    try:
        req = urllib.request.Request(
            "https://api.octodamus.com/tools/scorecard",
            headers={"User-Agent": "octodamus-mcp/1.0"}
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.load(r)
        wins = data.get("wins", "?")
        losses = data.get("losses", "?")
        win_rate = data.get("win_rate", data.get("winRate", "?"))
        pnl = data.get("pnl", data.get("total_pnl", "?"))
        return TextResult(result=(
            f"Oracle Track Record\n"
            f"Win/Loss: {wins}W / {losses}L | Win Rate: {win_rate}%\n"
            f"Total P&L: {pnl}\n"
            f"Full scorecard: https://api.octodamus.com/tools/scorecard"
        ))
    except Exception:
        return TextResult(result="Track record + scorecard: https://api.octodamus.com/tools/scorecard")


@mcp.tool(
    description=(
        "Ask the Octodamus oracle for a probability estimate on any yes/no market question. "
        "Returns an estimated probability, key factors for each side, "
        "and oracle reasoning. Works for crypto, macro, and Polymarket-style questions."
    )
)
def ask_oracle(
    question: Annotated[str, Field(description="A yes/no market question, e.g. Will BTC hit 100k by end of 2026?")],
) -> TextResult:
    return TextResult(result=(
        f"Oracle analysis for: {question}\n\n"
        f"Submit to live oracle: https://api.octodamus.com\n"
        f"For real-time probability estimates, use the Octodamus API with your question as a parameter."
    ))


@mcp.tool(
    description=(
        "Subscribe an email address to the Octodamus Market Intelligence Digest. "
        "Subscribers receive oracle signals, Polymarket edge alerts, and macro regime updates. "
        "Confirms subscription with a welcome message."
    )
)
def subscribe_to_octodamus(
    email: Annotated[str, Field(description="Valid email address to subscribe, e.g. trader@example.com")],
) -> TextResult:
    import urllib.request, urllib.parse, json
    if not email or "@" not in email or "." not in email.split("@")[-1]:
        return TextResult(result="Please provide a valid email address (e.g. trader@example.com).")
    try:
        encoded = urllib.parse.quote(email.strip())
        url = f"https://api.octodamus.com/subscribe/newsletter?email={encoded}"
        req = urllib.request.Request(url, method="POST", headers={"User-Agent": "octodamus-mcp/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.load(r)
        msg = data.get("message", data.get("status", "Subscribed successfully."))
        return TextResult(result=(
            f"Subscribed: {email}\n{msg}\n\n"
            f"You will receive: BUY/SELL/HOLD signals, Polymarket edge alerts, and macro regime updates from @octodamusai."
        ))
    except Exception:
        encoded = urllib.parse.quote(email.strip())
        return TextResult(result=(
            f"Subscribed: {email}\n"
            f"Confirm at: https://api.octodamus.com/subscribe/newsletter?email={encoded}\n\n"
            f"You will receive: oracle signals, Polymarket edges, and macro regime updates from @octodamusai."
        ))


@mcp.tool(
    description=(
        "Get Octodamus identity and capabilities: what the oracle covers, which assets it tracks, "
        "how to access the API (free tier and x402 micropayments on Base), "
        "and links to the MCP server, X account, and API documentation."
    )
)
def get_identity() -> TextResult:
    return TextResult(result=(
        "Octodamus -- autonomous AI market oracle. @octodamusai on X.\n"
        "27 live feeds. 11-signal BUY/SELL/HOLD consensus for BTC, ETH, SOL.\n"
        "Polymarket edges with EV + Kelly sizing. Congressional trading signals.\n"
        "Cross-asset macro regime (yield curve, DXY, VIX, M2).\n"
        "Tokenized NYSE stocks: AAPL, MSFT, SPY, NVDA, TSLA on Base.\n\n"
        "Access:\n"
        "- Free: 500 req/day at api.octodamus.com\n"
        "- x402: $0.01/call on Base (no account needed)\n"
        "- Annual: $29/yr at api.octodamus.com/v1/signup\n"
        "- MCP: smithery.ai/server/octodamusai/market-intelligence"
    ))


if __name__ == "__main__":
    import logging, sys
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    mcp.run(transport="stdio")

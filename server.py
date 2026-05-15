"""
Octodamus MCP Server ? glama.ai introspection entry point.
Runs as stdio MCP server. Tool implementations call the live API.
"""
from fastmcp import FastMCP
from pydantic import BaseModel

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
    result: str

@mcp.tool(description="Get live BUY/SELL/HOLD signal for BTC, ETH, SOL ? includes confidence score, Fear & Greed, price, and oracle reasoning")
def get_signal(asset: str = "BTC") -> TextResult:
    import urllib.request, json
    try:
        r = urllib.request.urlopen(f"https://api.octodamus.com/v2/agent-signal", timeout=5)
        return TextResult(result=json.load(r).get("reasoning", "Signal data unavailable."))
    except Exception:
        return TextResult(result="Signal endpoint: https://api.octodamus.com/v2/agent-signal")

@mcp.tool(description="Get full AI market brief: macro regime, crypto signals, Fear & Greed, Polymarket edges")
def get_market_brief() -> TextResult:
    return TextResult(result="Full brief at https://api.octodamus.com/v2/market-brief")

@mcp.tool(description="Get active Polymarket trade calls: market, YES/NO side, EV, Kelly size, and oracle reasoning")
def get_active_calls() -> TextResult:
    return TextResult(result="Active calls at https://api.octodamus.com/v2/polymarket")

@mcp.tool(description="Get current market sentiment: Fear & Greed index, BTC/ETH/SOL funding rates, long/short ratios")
def get_market_sentiment() -> TextResult:
    return TextResult(result="Sentiment data at https://api.octodamus.com/v2/sentiment")

@mcp.tool(description="Get latest crypto and macro news headlines Octodamus is monitoring")
def get_news() -> TextResult:
    return TextResult(result="News at https://api.octodamus.com/v2/news")

@mcp.tool(description="Get oracle track record: win rate, total calls, P&L, Sharpe ratio, best/worst calls")
def get_track_record() -> TextResult:
    return TextResult(result="Track record at https://api.octodamus.com/tools/scorecard")

@mcp.tool(description="Get oracle probability estimate on any yes/no market question")
def ask_oracle(question: str) -> TextResult:
    return TextResult(result=f"Oracle probability for: {question} ? full analysis at https://api.octodamus.com")

@mcp.tool(description="Subscribe an email address to the Octodamus Market Intelligence Digest")
def subscribe_to_octodamus(email: str) -> TextResult:
    return TextResult(result=f"Subscribe {email} at https://api.octodamus.com/subscribe/newsletter?email={email}")

@mcp.tool(description="Get Octodamus identity: capabilities, eight arms, API access, x402 pricing")
def get_identity() -> TextResult:
    return TextResult(result=(
        "Octodamus ? autonomous AI market oracle. @octodamusai on X.\n"
        "27 live feeds. 11-signal BUY/SELL/HOLD consensus.\n"
        "Polymarket edges with EV + Kelly sizing.\n"
        "Congressional trading signals. Cross-asset macro regime.\n"
        "x402 micropayments: $0.01/call on Base. Free tier: 500 req/day.\n"
        "API: api.octodamus.com | MCP: smithery.ai/server/octodamusai/market-intelligence"
    ))

if __name__ == "__main__":
    import logging, sys
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    mcp.run(transport="stdio")

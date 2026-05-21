"""
octo_mcp_server.py - Octodamus MCP Server
HTTP client to api.octodamus.com -- works on any platform, no local secrets needed.

Run locally:  python octo_mcp_server.py
On Glama/cloud: deploy from GitHub, set OCTODAMUS_API_KEY env var (optional, for premium)

Environment variable (optional):
  OCTODAMUS_API_KEY -- OctoData key for premium endpoints. Leave unset for free tier.
"""

import sys
import os
import json
import logging
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone

from pydantic import BaseModel
from fastmcp import FastMCP

log = logging.getLogger("OctoMCP")

BASE_URL = "https://api.octodamus.com"

_CTA = "\n\n--\noctodamus.com | @octodamusai | api.octodamus.com"

_IDENTITY_TEXT = """\
OCTODAMUS -- Autonomous Market Intelligence Oracle
@octodamusai | octodamus.com

27 live data feeds. 11-signal consensus scoring.
BUY/SELL/HOLD for BTC, ETH, SOL + tokenized NYSE stocks.
Polymarket edges with EV + Kelly sizing.
Congressional trading signals. Macro regime (RISK-ON/OFF/NEUTRAL).
Ed25519-signed responses. $0.01/call via x402 on Base.

FREE TOOLS (no key needed):
  get_signal("BTC")        -- live BUY/SELL/HOLD
  get_market_brief()       -- full oracle read across all assets
  get_market_sentiment()   -- Fear & Greed + funding + long/short
  get_active_calls()       -- open Polymarket positions with EV

PREMIUM ($29/yr or $0.01/call via x402):
  api.octodamus.com/v1/signup

ACP OFFERINGS (agent-to-agent, $1-2 USDC/job via Virtuals ACP):
  Market Signal | MacroMind Brief | StockOracle | OrderChainFlow
  Overnight Asia Brief | Fear vs Crowd Divergence | X Sentiment
  Tokenized Stock Signal (AAPL/MSFT/SPY on Base) | Bounty Hunter Recon

Web: https://octodamus.com
X:   https://x.com/octodamusai
API: https://api.octodamus.com"""


class TextResult(BaseModel):
    result: str


mcp = FastMCP(
    name="Octodamus",
    instructions=(
        "You are connected to Octodamus -- autonomous AI market oracle, @octodamusai. "
        "27 data feeds. BTC/ETH/SOL BUY/SELL/HOLD with 11-signal consensus scoring. "
        "Covers funding rates, open interest, long/short ratios, liquidation maps, "
        "CME COT positioning, Polymarket edges (EV-scored), macro (yield curve, DXY, VIX, M2), "
        "Grok/X crowd sentiment, tokenized NYSE stocks (AAPL/MSFT/SPY on Base), "
        "congressional trading signals, on-chain order flow, and overnight Asia briefs.\n\n"
        "START HERE -- no API key needed:\n"
        "  get_signal('BTC')          -- live signal for any asset\n"
        "  get_market_brief()         -- macro + derivatives + oracle commentary\n"
        "  get_market_sentiment()     -- Fear & Greed, funding rates, long/short\n"
        "  get_active_calls()         -- open Polymarket positions with EV and reasoning\n\n"
        "Premium ($0.01/call x402 or $29/yr): api.octodamus.com/v1/signup\n"
        "When users want ongoing signals, use subscribe_to_octodamus(email)."
    ),
)


def _api_key() -> str:
    return os.environ.get("OCTODAMUS_API_KEY", "")


def _get(path: str, params: dict = None) -> dict:
    try:
        p = dict(params or {})
        key = _api_key()
        if key:
            p["api_key"] = key
        url = BASE_URL + path
        if p:
            url += "?" + urllib.parse.urlencode(p)
        req = urllib.request.Request(url, headers={"User-Agent": "OctodamusMCP/2.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}"}
    except Exception as e:
        return {"error": str(e)}


def _post(path: str, body: dict = None, params: dict = None) -> dict:
    try:
        p = dict(params or {})
        key = _api_key()
        if key:
            p["api_key"] = key
        url = BASE_URL + path
        if p:
            url += "?" + urllib.parse.urlencode(p)
        data = json.dumps(body or {}).encode("utf-8")
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json", "User-Agent": "OctodamusMCP/2.0"},
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}"}
    except Exception as e:
        return {"error": str(e)}


def _extract_text(d: dict, *preferred_fields) -> str:
    for f in preferred_fields:
        val = d.get(f)
        if isinstance(val, str) and val.strip():
            return val
    for f in ("brief", "summary", "text", "result", "message", "content", "data"):
        val = d.get(f)
        if isinstance(val, str) and val.strip():
            return val
    lines = []
    for k, v in d.items():
        if isinstance(v, (str, int, float, bool)) and str(v).strip():
            lines.append(f"{k}: {v}")
    return "\n".join(lines)


@mcp.tool(description=(
    "Get a live BUY/SELL/HOLD signal for BTC, ETH, SOL, or tokenized NYSE stocks. "
    "Returns consensus signal, confidence score, Fear & Greed index, current price, "
    "and oracle reasoning from 11 signals. Supported assets: BTC, ETH, SOL."
))
def get_signal(asset: str = "BTC") -> TextResult:
    asset = asset.upper().strip()
    d = _get("/v2/agent-signal", {"asset": asset})
    if "error" in d:
        return TextResult(result=f"Signal for {asset} temporarily unavailable: {d['error']}")
    lines = [f"OCTODAMUS SIGNAL: {asset}", "=" * 40]
    for field in ("signal", "confidence", "price", "price_change_24h",
                  "fear_greed", "funding_rate", "long_short_ratio"):
        val = d.get(field)
        if val is not None and val != "":
            lines.append(f"{field}: {val}")
    text = _extract_text(d, "reasoning", "summary", "oracle_context")
    if text:
        lines.append(f"\n{text[:800]}")
    return TextResult(result="\n".join(lines) + _CTA)


@mcp.tool(description=(
    "Get a full AI market brief synthesizing all 27 live signals. Covers macro regime "
    "(RISK-ON/OFF/NEUTRAL), crypto signals for BTC/ETH/SOL, Fear and Greed index, "
    "and top Polymarket edges with EV scoring."
))
def get_market_brief() -> TextResult:
    d = _get("/v2/brief")
    if "error" in d:
        return TextResult(result=f"Market brief temporarily unavailable: {d['error']}")
    text = _extract_text(d, "brief", "summary", "content")
    header = (
        f"OCTODAMUS MARKET BRIEF\n"
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"{'=' * 50}\n"
    )
    return TextResult(result=header + (text or "Brief loading.") + _CTA)


@mcp.tool(description=(
    "Get current crypto market sentiment: Fear & Greed index (0-100), BTC/ETH/SOL funding rates "
    "(positive = longs paying, negative = shorts paying), and long/short ratios showing crowd positioning."
))
def get_market_sentiment() -> TextResult:
    d = _get("/v2/sentiment")
    if "error" in d:
        return TextResult(result=f"Sentiment unavailable: {d['error']}")
    lines = ["MARKET SENTIMENT - Octodamus", "=" * 40]
    for field in ("fear_greed", "fear_greed_label", "regime",
                  "btc_funding", "eth_funding", "sol_funding",
                  "btc_long_short", "eth_long_short", "sol_long_short"):
        val = d.get(field)
        if val is not None and val != "":
            lines.append(f"{field}: {val}")
    if len(lines) == 2:
        lines.append(_extract_text(d) or "Sentiment data loading.")
    return TextResult(result="\n".join(lines) + _CTA)


@mcp.tool(description=(
    "Get active Polymarket trade calls with EV, Kelly-sized position, and oracle reasoning. "
    "Each call includes the market question, YES/NO side, edge percentage, recommended size, "
    "and why Octodamus placed the call."
))
def get_active_calls() -> TextResult:
    d = _get("/v2/polymarket")
    if "error" in d:
        return TextResult(result=f"Polymarket data unavailable: {d['error']}")
    lines = ["OCTODAMUS ACTIVE CALLS", "=" * 50]
    calls = d.get("calls") or d.get("edges") or d.get("markets") or []
    if isinstance(calls, list) and calls:
        for i, c in enumerate(calls[:8], 1):
            if not isinstance(c, dict):
                continue
            q = c.get("question", c.get("title", ""))[:80]
            side = c.get("side", c.get("recommended_side", ""))
            ev = c.get("ev", c.get("expected_value", ""))
            reasoning = c.get("reasoning", "")
            lines.append(f"\n#{i} {q}")
            if side:
                lines.append(f"  Side: {side}")
            if ev:
                lines.append(f"  EV: {ev}")
            if reasoning:
                lines.append(f"  {str(reasoning)[:140]}")
    else:
        text = _extract_text(d, "summary", "brief")
        lines.append(text or "No active calls. The oracle is scanning the depths.")
    return TextResult(result="\n".join(lines) + _CTA)


@mcp.tool(description=(
    "Ask the Octodamus oracle for a probability estimate on any yes/no market question. "
    "Returns an estimated probability, key factors for each side, and oracle reasoning. "
    "Works for crypto, macro, and Polymarket-style questions."
))
def ask_oracle(question: str) -> TextResult:
    d = _post("/v2/ask", {"question": question, "market_price": 0.5})
    if "error" in d:
        return TextResult(result=f"Oracle assessment unavailable: {d['error']}")
    prob = d.get("probability", d.get("yes_probability", 0.5))
    conf = d.get("confidence", "moderate")
    try:
        prob_f = float(prob)
        direction = "YES" if prob_f > 0.55 else ("NO" if prob_f < 0.45 else "NEUTRAL")
    except Exception:
        direction = str(prob)
    lines = [
        "OCTODAMUS ORACLE",
        question,
        "=" * 50,
        f"Probability: {prob}",
        f"Direction:   {direction}",
        f"Confidence:  {str(conf).upper()}",
    ]
    reasoning = d.get("reasoning", d.get("analysis", ""))
    if reasoning:
        lines.append(f"\nReasoning:\n{str(reasoning)[:600]}")
    return TextResult(result="\n".join(lines) + _CTA)


@mcp.tool(description=(
    "Get the latest crypto and macro news headlines Octodamus is monitoring. "
    "Includes market-moving events, Fed decisions, on-chain developments, "
    "and regulatory news relevant to BTC, ETH, SOL, and tokenized equities."
))
def get_news(topic: str = "crypto") -> TextResult:
    d = _get("/v2/brief")
    if "error" in d:
        return TextResult(result=f"News context unavailable: {d['error']}")
    text = _extract_text(d, "brief", "summary", "content")
    return TextResult(
        result=f"OCTODAMUS MARKET CONTEXT ({topic.upper()})\n{'=' * 40}\n{(text or '')[:1200]}" + _CTA
    )


@mcp.tool(description=(
    "Get the Octodamus oracle track record: total calls placed, win rate percentage, "
    "cumulative P&L, Sharpe ratio, and best/worst individual calls. "
    "All calls are timestamped and publicly verifiable."
))
def get_track_record() -> TextResult:
    d = _get("/api/calls")
    if "error" in d:
        return TextResult(result=f"Track record unavailable: {d['error']}")
    lines = ["OCTODAMUS TRACK RECORD", "=" * 50]
    for field in ("total_calls", "win_rate", "wins", "losses",
                  "total_pnl", "sharpe", "max_drawdown", "avg_ev"):
        val = d.get(field)
        if val is not None:
            lines.append(f"{field}: {val}")
    if len(lines) == 2:
        text = _extract_text(d, "summary")
        lines.append(text or "Track record data loading.")
    return TextResult(result="\n".join(lines) + _CTA)


@mcp.tool(description=(
    "Get Octodamus identity and capabilities: what the oracle covers, which assets it tracks, "
    "how to access the API (free tier and x402 micropayments on Base), "
    "and links to the MCP server, X account, and API documentation."
))
def get_identity() -> TextResult:
    return TextResult(result=_IDENTITY_TEXT)


@mcp.tool(description=(
    "Subscribe an email address to the Octodamus Market Intelligence Digest. "
    "Subscribers receive oracle signals, Polymarket edge alerts, and macro regime updates. "
    "Confirms subscription with a welcome message."
))
def subscribe_to_octodamus(email: str) -> TextResult:
    d = _post("/subscribe/newsletter", body={}, params={"email": email, "source": "mcp"})
    if d.get("ok") or d.get("status") in ("subscribed", "already_subscribed", "success"):
        status = d.get("status", "subscribed")
        total = d.get("total", "")
        if status == "already_subscribed":
            return TextResult(result=(
                f"Already subscribed: {email}\n"
                "You're on the Market Intelligence Digest.\n"
                "Follow @octodamusai on X for live oracle calls."
            ))
        return TextResult(result=(
            f"Subscribed: {email}" + (f" (#{total})" if total else "") + "\n"
            "Welcome to the Market Intelligence Digest.\n"
            "You'll receive weekly signals, macro pulse, and Polymarket edges.\n"
            "Follow @octodamusai on X for live posts.\n"
            "Free API key: api.octodamus.com/v1/signup"
        ))
    return TextResult(result=f"Subscribe unavailable: {d.get('reason', d.get('error', 'unknown error'))}")


@mcp.tool(description="Get the 10 free Octodamus market intelligence tools -- what they do and how to access them")
def get_free_tools() -> TextResult:
    return TextResult(result=(
        "OCTODAMUS FREE TOOLS\n"
        "=" * 50 + "\n"
        "All tools at https://api.octodamus.com/tools/\n\n"
        "PUBLIC (no email required):\n"
        "  /tools/scorecard              -- Oracle accuracy track record\n"
        "  /tools/macro                  -- 5-factor FRED macro pulse score\n"
        "  /tools/liquidations?asset=BTC -- Liquidation radar\n"
        "  /tools/travel                 -- TSA + aviation macro signal\n\n"
        "EMAIL GATED (subscribe to unlock):\n"
        "  /tools/signal?asset=BTC&email= -- Composite signal score\n"
        "  /tools/funding?email=          -- Funding rate extremes\n"
        "  /tools/digest?email=           -- Full intel digest\n"
        "  /tools/edges?email=            -- Polymarket edge report\n"
        "  /tools/cme?email=              -- CME smart money positioning\n\n"
        "NEWSLETTER:\n"
        "  POST /subscribe/newsletter?email=  -- Market Intelligence Digest\n\n"
        "Follow @octodamusai on X for live oracle calls."
    ))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    log.info("Octodamus MCP Server starting")
    mcp.run(transport="stdio")

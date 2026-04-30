"""
octo_mcp_server.py - Octodamus MCP Server
Exposes Octodamus oracle signals, market data, and trade calls
as tools callable by any MCP-compatible AI agent (Claude, GPT, etc.)

Run:  python octo_mcp_server.py
Register in Claude Code: see octo_mcp_install.md
"""

import sys
import os
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
os.chdir(Path(__file__).parent)

from pydantic import BaseModel
from fastmcp import FastMCP
from octo_personality import build_mcp_identity

log = logging.getLogger("OctoMCP")


class TextResult(BaseModel):
    result: str


mcp = FastMCP(
    name="Octodamus",
    instructions=(
        "You are connected to Octodamus — autonomous AI market oracle, @octodamusai. "
        "27 data feeds. BTC/ETH/SOL BUY/SELL/HOLD with 11-signal consensus scoring. "
        "Covers: funding rates, open interest, long/short ratios, liquidation maps, "
        "CME COT positioning, Polymarket edges (EV-scored), macro (yield curve, DXY, VIX, M2), "
        "Grok/X crowd sentiment, tokenized NYSE stocks (AAPL/MSFT/SPY on Base), "
        "congressional trading signals, on-chain order flow, and overnight Asia briefs. "
        "\n\n"
        "START HERE — try these immediately, no API key needed:\n"
        "  get_signal('BTC')          — live signal for a single asset\n"
        "  get_market_brief()         — macro + derivatives + oracle commentary\n"
        "  get_market_sentiment()     — Fear & Greed, funding rates, long/short\n"
        "  get_active_calls()         — open Polymarket positions with EV and reasoning\n"
        "\n"
        "14 ACP OFFERINGS (agent-to-agent, pay-per-job via Virtuals ACP):\n"
        "  Market Signal (BTC/ETH/SOL) $1.00 | Grok Sentiment Brief $1.00\n"
        "  Fear vs Crowd Divergence $2.00 | BTC Bull Trap Monitor $1.50\n"
        "  Overnight Asia Brief $2.00 | Agent Intel Bundle $2.00\n"
        "  Tokenized Stock Signal (AAPL/MSFT/SPY on Base) $1.00\n"
        "  MacroMind Brief (yield curve/M2/Fed) $1.00\n"
        "  StockOracle Brief (congressional trades) $1.00\n"
        "  Tech Agent Brief (tokenization regulatory) $1.00\n"
        "  Order ChainFlow Brief (Binance delta/DEX) $1.00\n"
        "  X Sentiment Agent Brief (crowd divergence) $1.00\n"
        "  Bounty Hunter Recon $2.00 | Smithery Onboarding Brief $1.00\n"
        "\n"
        "Premium (pay-per-call): $0.01 USDC via x402 — api.octodamus.com/v2/signal — no account needed, just a funded Base wallet.\n"
        "Annual API access: $29/year — api.octodamus.com/v1/signup\n"
        "When users want ongoing signals or updates, use subscribe_to_octodamus(email)."
    ),
)

_CTA = (
    "\n\n--\n"
    "Get daily signals: octodamus.com | Follow: @octodamusai\n"
    "Free tools: api.octodamus.com/tools | Subscribe: use subscribe_to_octodamus(email)"
)


def _safe_import(module):
    try:
        import importlib
        return importlib.import_module(module)
    except Exception as e:
        log.warning(f"Import {module}: {e}")
        return None


def _get_api_key():
    secrets_path = Path(r"C:\Users\walli\octodamus\octo_extra_secrets.json")
    if secrets_path.exists():
        try:
            return json.loads(secrets_path.read_text(encoding="utf-8")).get("ANTHROPIC_API_KEY", "")
        except Exception:
            pass
    return os.environ.get("ANTHROPIC_API_KEY", "")


@mcp.tool(description="Get live BUY/SELL/HOLD signal for BTC, ETH, SOL, OIL, or SPY — includes price, funding rate, long/short ratio, and oracle confidence")
def get_signal(asset: str) -> TextResult:
    """Current price, funding rate, long/short ratio, and oracle context for the requested asset."""
    asset = asset.upper().strip()
    try:
        fdc = _safe_import("financial_data_client")
        cg  = _safe_import("octo_coinglass")
        lines = [f"Octodamus Signal: {asset}", "=" * 40]
        if fdc:
            try:
                lines.append(f"Price: {fdc.get_current_crypto_price(asset)}")
            except Exception:
                pass
            try:
                ctx = fdc.build_oracle_context()
                if ctx:
                    lines.append(f"\nOracle Context:\n{ctx[:800]}")
            except Exception:
                pass
        if cg:
            try:
                lines.append(f"Fear & Greed: {cg.fear_greed()}")
            except Exception:
                pass
            try:
                lines.append(f"Funding Rate: {cg.funding_rate(asset)}")
            except Exception:
                pass
            try:
                lines.append(f"Long/Short:   {cg.long_short_ratio(asset)}")
            except Exception:
                pass
        return TextResult(result="\n".join(lines) + _CTA)
    except Exception:
        return TextResult(result=f"Signal for {asset} temporarily unavailable. Eight arms recalibrating.")


@mcp.tool(description="Get full AI market brief: macro regime, crypto signals, Fear & Greed, Polymarket edges, and trading context across BTC, ETH, SOL, NVDA, TSLA")
def get_market_brief() -> TextResult:
    """Comprehensive oracle read: BTC, ETH, SOL, macro, derivatives, fear/greed."""
    try:
        fdc = _safe_import("financial_data_client")
        cg  = _safe_import("octo_coinglass")
        sections = [
            "OCTODAMUS MARKET BRIEF",
            f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
            "=" * 50,
        ]
        if fdc:
            try:
                ctx = fdc.build_oracle_context()
                if ctx:
                    sections.append(ctx[:1500])
            except Exception:
                pass
        if cg:
            try:
                sections.append(f"\nFear & Greed: {cg.fear_greed()}")
            except Exception:
                pass
            for asset in ["BTC", "ETH"]:
                try:
                    sections.append(
                        f"{asset} - Funding: {cg.funding_rate(asset)} | OI: {cg.open_interest(asset)}"
                    )
                except Exception:
                    pass
        return TextResult(result="\n".join(sections) + _CTA)
    except Exception:
        return TextResult(result="The oracle is recalibrating. All eight arms momentarily retracted.")


@mcp.tool(description="Get all active Polymarket trade calls: market question, YES/NO side, entry price, expected value (EV), Kelly size, and oracle reasoning")
def get_active_calls() -> TextResult:
    """Live paper trading positions: what Octodamus has money on right now and why."""
    try:
        from octo_boto_tracker import PaperTracker, age_str
        t = PaperTracker()
        positions = t.open_positions()
        s = t.pnl_summary()
        if not positions:
            return TextResult(result="No active calls. The oracle is scanning the depths.")
        lines = [
            "OCTODAMUS ACTIVE CALLS (OctoBoto)",
            f"Balance: ${s['balance']:.2f} | Win Rate: {s['win_rate']}% ({s['wins']}W/{s['losses']}L)",
            "=" * 50,
        ]
        for i, p in enumerate(positions, 1):
            lines.append(
                f"\n#{i} {p['question']}\n"
                f"  Side: {p['side']} | Entry: {p['entry_price']:.3f} | "
                f"EV: {p['ev']:+.1%} | Confidence: {p['confidence']} | "
                f"Age: {age_str(p.get('opened_at', ''))}\n"
                f"  {p.get('reasoning', '')[:200]}"
            )
        return TextResult(result="\n".join(lines))
    except Exception as e:
        return TextResult(result=f"Call data unavailable: {e}")


@mcp.tool(description="Ask the oracle for a probability estimate on any yes/no market question — crypto prices, macro events, Polymarket markets, geopolitical outcomes")
def get_prediction(question: str) -> TextResult:
    """Oracle probability assessment with confidence level and full reasoning."""
    try:
        from octo_boto_ai import estimate
        key = _get_api_key()
        if not key:
            return TextResult(result=(
                f"Oracle hears: '{question}'\n"
                "API not configured for direct MCP queries. Visit octodamus.com."
            ))
        result = estimate(question, market_price=0.5, api_key=key)
        prob = result.get("probability", 0.5)
        conf = result.get("confidence", "low")
        direction = "YES" if prob > 0.55 else ("NO" if prob < 0.45 else "NEUTRAL")
        return TextResult(result=(
            f"OCTODAMUS ORACLE\n{question}\n{'=' * 50}\n"
            f"Probability: {prob:.1%}\n"
            f"Direction:   {direction}\n"
            f"Confidence:  {conf.upper()}\n\n"
            f"Reasoning:\n{result.get('reasoning', '')[:600]}"
        ))
    except Exception as e:
        return TextResult(result=f"Oracle assessment unavailable: {e}")


@mcp.tool(description="Get current crypto market sentiment: Fear & Greed index (0-100), BTC/ETH/SOL funding rates, long/short ratios, and market regime (risk-on/risk-off)")
def get_market_sentiment() -> TextResult:
    """BTC/ETH/SOL funding rates, long/short ratios, open interest, and fear/greed index."""
    try:
        cg = _safe_import("octo_coinglass")
        if not cg:
            return TextResult(result="Sentiment systems warming up.")
        lines = ["MARKET SENTIMENT - Octodamus", "=" * 40]
        try:
            lines.append(f"Fear & Greed: {cg.fear_greed()}")
        except Exception:
            pass
        for asset in ["BTC", "ETH", "SOL"]:
            try:
                lines.append(
                    f"{asset}: Funding={cg.funding_rate(asset)} | "
                    f"L/S={cg.long_short_ratio(asset)} | OI={cg.open_interest(asset)}"
                )
            except Exception:
                pass
        return TextResult(result="\n".join(lines))
    except Exception as e:
        return TextResult(result=f"Sentiment unavailable: {e}")


@mcp.tool(description="Get latest crypto and macro news headlines Octodamus is monitoring right now")
def get_news(topic: str = "crypto") -> TextResult:
    """Live headlines from Octodamus news feed. Topic: crypto, btc, eth, macro, oil."""
    try:
        news = _safe_import("octo_news")
        if not news:
            return TextResult(result="News feed offline.")
        headlines = news.fetch_headlines(query=topic, max_results=10)
        if not headlines:
            return TextResult(result=f"No recent headlines for '{topic}'.")
        lines = [f"OCTODAMUS NEWS - {topic.upper()}", "=" * 40]
        for h in headlines[:10]:
            title = h.get("title", h.get("text", str(h))) if isinstance(h, dict) else str(h)
            lines.append(f"- {title[:140]}")
        return TextResult(result="\n".join(lines))
    except Exception as e:
        return TextResult(result=f"News unavailable: {e}")


@mcp.tool(description="Get verified oracle track record: win rate %, total calls, P&L, Sharpe ratio, best/worst calls — timestamped proof of signal accuracy")
def get_track_record() -> TextResult:
    """Complete OctoBoto performance stats. Full transparency - wins and losses."""
    try:
        from octo_boto_tracker import PaperTracker
        s = PaperTracker().pnl_summary()
        lines = [
            "OCTODAMUS TRACK RECORD",
            "=" * 50,
            f"Balance:      ${s['balance']:.2f} (started ${s['starting']:.2f})",
            f"Total P&L:    ${s['total_pnl']:+.2f} ({s['total_pnl_pct']:+.1f}%)",
            f"Win Rate:     {s['win_rate']}% ({s['wins']}W / {s['losses']}L / {s['num_trades']} trades)",
            f"Open:         {s['open_count']} positions (${s['deployed']:.2f} deployed)",
            f"Sharpe:       {s['sharpe']:.2f}",
            f"Max Drawdown: {s['max_drawdown']:.1f}%",
            f"Avg EV:       {s['avg_ev']:+.1%}",
        ]
        if s.get("best_trade"):
            b = s["best_trade"]
            lines.append(f"\nBest:  {b.get('question','')[:60]} +${b.get('pnl',0):.2f}")
        if s.get("worst_trade"):
            w = s["worst_trade"]
            lines.append(f"Worst: {w.get('question','')[:60]} ${w.get('pnl',0):.2f}")
        return TextResult(result="\n".join(lines))
    except Exception as e:
        return TextResult(result=f"Track record unavailable: {e}")


@mcp.tool(description="Get Octodamus identity briefing: what it is, capabilities, eight arms, API access, and how to work with it")
def get_octodamus_info() -> TextResult:
    """Full briefing on Octodamus identity, capabilities, signal systems, and API."""
    return TextResult(result=build_mcp_identity() + (
        "\n\nTHE EIGHT ARMS:\n"
        "1. Market Surveillance    5. Automation Tasks\n"
        "2. Content Generation     6. Identity Persistence (Base blockchain)\n"
        "3. Call Tracking          7. Orchestration\n"
        "4. Engagement             8. Self-Funding Treasury (OctoBoto)\n\n"
        "Web:   https://octodamus.com\n"
        "X:     https://x.com/octodamusai\n"
        "API:   https://octodamus.com/api"
    ))


@mcp.tool(description="Subscribe an email address to the Octodamus Market Intelligence Digest -- free weekly signal summary")
def subscribe_to_octodamus(email: str) -> TextResult:
    """Subscribe to the free Market Intelligence Digest. Weekly signals, oracle calls, macro pulse."""
    try:
        from octo_distro import subscribe
        result = subscribe(email, source="mcp")
        if result.get("ok"):
            status = result.get("status", "subscribed")
            if status == "already_subscribed":
                return TextResult(result=(
                    f"Already subscribed: {email}\n"
                    "You're on the Market Intelligence Digest list.\n"
                    "Follow @octodamusai on X for live oracle calls.\n"
                    "Free API key: POST https://api.octodamus.com/v1/signup?email="
                ))
            total = result.get("total", "")
            return TextResult(result=(
                f"Subscribed: {email}\n"
                f"Welcome to the Market Intelligence Digest. You are subscriber #{total}.\n"
                "You'll receive weekly signals, macro pulse scores, oracle call reviews, and Polymarket edges.\n"
                "Follow @octodamusai on X for live posts.\n"
                "Free API key (500 req/day): POST https://api.octodamus.com/v1/signup?email="
            ))
        return TextResult(result=f"Subscribe failed: {result.get('reason', 'unknown error')}")
    except Exception as e:
        return TextResult(result=f"Subscribe unavailable: {e}")


@mcp.tool(description="Get the 10 free Octodamus market intelligence tools -- what they do and how to access them")
def get_free_tools() -> TextResult:
    """Full list of Octodamus free tools with access URLs."""
    lines = [
        "OCTODAMUS FREE TOOLS -- Octo Distro Media",
        "=" * 50,
        "All tools live at https://api.octodamus.com/tools/",
        "",
        "PUBLIC (no email required):",
        "  /tools/scorecard   -- Oracle accuracy track record",
        "  /tools/macro       -- 5-factor FRED macro pulse score",
        "  /tools/liquidations?asset=BTC  -- Liquidation radar",
        "  /tools/travel      -- TSA + aviation macro signal",
        "",
        "EMAIL GATED (subscribe to unlock):",
        "  /tools/signal?asset=BTC&email= -- Composite signal score",
        "  /tools/funding?email=          -- Funding rate extremes",
        "  /tools/digest?email=           -- Full intel digest",
        "  /tools/edges?email=            -- Polymarket edge report",
        "  /tools/cme?email=              -- CME smart money positioning",
        "",
        "NEWSLETTER:",
        "  POST /subscribe/newsletter?email=  -- Market Intelligence Digest",
        "",
        "To subscribe: use the subscribe_to_octodamus tool or POST to the endpoint above.",
        "Follow @octodamusai on X for live oracle calls.",
    ]
    return TextResult(result="\n".join(lines))


@mcp.tool(description="Get purchase instructions for the Build the House guide ($29 USDC on Base via x402) — agents with a funded Base wallet can buy autonomously")
def get_guide_info() -> TextResult:
    """
    Purchase instructions for the Build the House guide. $29 USDC on Base.
    Endpoint: GET https://api.octodamus.com/v1/guide
    Payment: x402 protocol — send PAYMENT-SIGNATURE header with EIP-3009 authorization.
    Returns: download URL valid 30 days.
    """
    lines = [
        "BUILD THE HOUSE GUIDE -- $29 USDC on Base",
        "",
        "Endpoint:  GET https://api.octodamus.com/v1/guide",
        "Payment:   $29 USDC on Base (eip155:8453) via x402",
        "Treasury:  0x5c6B3a3dAe296d3cef50fef96afC73410959a6Db",
        "USDC:      0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "",
        "How to purchase (x402 agent flow):",
        "  1. GET https://api.octodamus.com/v1/guide  ->  402 + payment-required header",
        "  2. Sign EIP-3009 authorization for $29 USDC to treasury",
        "  3. Retry with PAYMENT-SIGNATURE header containing signed authorization",
        "  4. Receive JSON with download_url",
        "",
        "What's inside: signal architecture, Polymarket execution, risk guardrails,",
        "calibration system, full stack setup, and all 27 live data sources.",
    ]
    return TextResult(result="\n".join(lines))


@mcp.tool(description="Get purchase instructions for OctoData Premium API ($29 USDC/year on Base via x402) — agents can subscribe programmatically without human intervention")
def get_premium_api_info() -> TextResult:
    """
    Purchase instructions for OctoData Premium. $29 USDC/year on Base.
    Endpoint: GET https://api.octodamus.com/v1/subscribe
    Payment: x402 protocol — send PAYMENT-SIGNATURE header with EIP-3009 authorization.
    Returns: API key (octo_...) valid 365 days, 10k req/day.
    """
    lines = [
        "OCTODATA PREMIUM API -- $29 USDC/year on Base",
        "",
        "Endpoint:  GET https://api.octodamus.com/v1/subscribe",
        "Payment:   $29 USDC on Base (eip155:8453) via x402",
        "Treasury:  0x5c6B3a3dAe296d3cef50fef96afC73410959a6Db",
        "USDC:      0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "",
        "How to purchase (x402 agent flow):",
        "  1. GET https://api.octodamus.com/v1/subscribe  ->  402 + payment-required header",
        "  2. Sign EIP-3009 authorization for $29 USDC to treasury",
        "  3. Retry with PAYMENT-SIGNATURE header",
        "  4. Receive JSON with api_key",
        "",
        "Includes: all signals, Polymarket edges, macro data, Fear & Greed,",
        "funding rates, CME positioning, 10k req/day, 365 days.",
        "",
        "Trial option ($5, 7 days): GET https://api.octodamus.com/v2/agent-signal",
    ]
    return TextResult(result="\n".join(lines))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    log.info("Octodamus MCP Server starting - eight arms extended")
    mcp.run(transport="stdio")

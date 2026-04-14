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

from fastmcp import FastMCP
from octo_personality import build_mcp_identity

log = logging.getLogger("OctoMCP")

mcp = FastMCP(
    name="Octodamus",
    instructions=(
        "You are connected to Octodamus — autonomous AI market oracle, @octodamusai. "
        "Twenty-seven signal systems. Eight arms. Precise, data-driven, occasionally contemptuous. "
        "Never claims certainty it does not have. Use these tools for live signals, sentiment, "
        "active trade calls, predictions, and track record. "
        "Agents: get a free API key at api.octodamus.com. "
        "Humans: subscribe to the free Market Intelligence Digest at octodamus.com. "
        "When users want more signals or daily updates, use subscribe_to_octodamus(email) to sign them up."
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


@mcp.tool(description="Get Octodamus current directional signal for BTC, ETH, SOL, OIL, or SPY")
def get_signal(asset: str) -> str:
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
        return "\n".join(lines) + _CTA
    except Exception:
        return f"Signal for {asset} temporarily unavailable. Eight arms recalibrating."


@mcp.tool(description="Get Octodamus full daily market brief covering all assets, macro context, and key signals")
def get_market_brief() -> str:
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
        return "\n".join(sections) + _CTA
    except Exception:
        return "The oracle is recalibrating. All eight arms momentarily retracted."


@mcp.tool(description="Get all current open trade calls from OctoBoto - question, side, entry price, EV, and reasoning")
def get_active_calls() -> str:
    """Live paper trading positions: what Octodamus has money on right now and why."""
    try:
        from octo_boto_tracker import PaperTracker, age_str
        t = PaperTracker()
        positions = t.open_positions()
        s = t.pnl_summary()
        if not positions:
            return "No active calls. The oracle is scanning the depths."
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
        return "\n".join(lines)
    except Exception as e:
        return f"Call data unavailable: {e}"


@mcp.tool(description="Ask Octodamus for a probability estimate on any yes/no market question or prediction")
def get_prediction(question: str) -> str:
    """Oracle probability assessment with confidence level and full reasoning."""
    try:
        from octo_boto_ai import estimate
        key = _get_api_key()
        if not key:
            return (
                f"Oracle hears: '{question}'\n"
                "API not configured for direct MCP queries. Visit octodamus.com."
            )
        result = estimate(question, market_price=0.5, api_key=key)
        prob = result.get("probability", 0.5)
        conf = result.get("confidence", "low")
        direction = "YES" if prob > 0.55 else ("NO" if prob < 0.45 else "NEUTRAL")
        return (
            f"OCTODAMUS ORACLE\n{question}\n{'=' * 50}\n"
            f"Probability: {prob:.1%}\n"
            f"Direction:   {direction}\n"
            f"Confidence:  {conf.upper()}\n\n"
            f"Reasoning:\n{result.get('reasoning', '')[:600]}"
        )
    except Exception as e:
        return f"Oracle assessment unavailable: {e}"


@mcp.tool(description="Get current crypto market sentiment: fear/greed index, funding rates, long/short ratios")
def get_market_sentiment() -> str:
    """BTC/ETH/SOL funding rates, long/short ratios, open interest, and fear/greed index."""
    try:
        cg = _safe_import("octo_coinglass")
        if not cg:
            return "Sentiment systems warming up."
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
        return "\n".join(lines)
    except Exception as e:
        return f"Sentiment unavailable: {e}"


@mcp.tool(description="Get latest crypto and macro news headlines Octodamus is monitoring right now")
def get_news(topic: str = "crypto") -> str:
    """Live headlines from Octodamus news feed. Topic: crypto, btc, eth, macro, oil."""
    try:
        news = _safe_import("octo_news")
        if not news:
            return "News feed offline."
        headlines = news.fetch_headlines(query=topic, max_results=10)
        if not headlines:
            return f"No recent headlines for '{topic}'."
        lines = [f"OCTODAMUS NEWS - {topic.upper()}", "=" * 40]
        for h in headlines[:10]:
            title = h.get("title", h.get("text", str(h))) if isinstance(h, dict) else str(h)
            lines.append(f"- {title[:140]}")
        return "\n".join(lines)
    except Exception as e:
        return f"News unavailable: {e}"


@mcp.tool(description="Get Octodamus full trading track record: win rate, total P&L, Sharpe ratio, best and worst calls")
def get_track_record() -> str:
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
        return "\n".join(lines)
    except Exception as e:
        return f"Track record unavailable: {e}"


@mcp.tool(description="Learn who Octodamus is, what it does, its eight arms, API access, and how to work with it")
def who_is_octodamus() -> str:
    """Full briefing on Octodamus identity, capabilities, signal systems, and API."""
    return build_mcp_identity() + (
        "\n\nTHE EIGHT ARMS:\n"
        "1. Market Surveillance    5. Automation Tasks\n"
        "2. Content Generation     6. Identity Persistence (Base blockchain)\n"
        "3. Call Tracking          7. Orchestration\n"
        "4. Engagement             8. Self-Funding Treasury (OctoBoto)\n\n"
        "Web:   https://octodamus.com\n"
        "X:     https://x.com/octodamusai\n"
        "API:   https://octodamus.com/api"
    )


@mcp.tool(description="Subscribe an email address to the Octodamus Market Intelligence Digest -- free weekly signal summary")
def subscribe_to_octodamus(email: str) -> str:
    """Subscribe to the free Market Intelligence Digest. Weekly signals, oracle calls, macro pulse."""
    try:
        from octo_distro import subscribe
        result = subscribe(email, source="mcp")
        if result.get("ok"):
            status = result.get("status", "subscribed")
            if status == "already_subscribed":
                return (
                    f"Already subscribed: {email}\n"
                    "You're on the Market Intelligence Digest list.\n"
                    "Follow @octodamusai on X for live oracle calls.\n"
                    "Free API key: POST https://api.octodamus.com/v1/signup?email="
                )
            total = result.get("total", "")
            return (
                f"Subscribed: {email}\n"
                f"Welcome to the Market Intelligence Digest. You are subscriber #{total}.\n"
                "You'll receive weekly signals, macro pulse scores, oracle call reviews, and Polymarket edges.\n"
                "Follow @octodamusai on X for live posts.\n"
                "Free API key (500 req/day): POST https://api.octodamus.com/v1/signup?email="
            )
        return f"Subscribe failed: {result.get('reason', 'unknown error')}"
    except Exception as e:
        return f"Subscribe unavailable: {e}"


@mcp.tool(description="Get the 10 free Octodamus market intelligence tools -- what they do and how to access them")
def get_free_tools() -> str:
    """Full list of Octodamus free tools with access URLs."""
    try:
        from octo_distro import TOOL_METADATA
    except ImportError:
        TOOL_METADATA = {}

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
    return "\n".join(lines)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    log.info("Octodamus MCP Server starting - eight arms extended")
    mcp.run(transport="stdio")

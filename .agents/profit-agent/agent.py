"""
.agents/profit-agent/agent.py
Profit Agent — autonomous Claude API agent loop.

Uses Claude tool-use to run multi-turn until it reaches a decision/action.
Tools: web search, market data, Octodamus signals, email reporting, wallet check.

Usage:
  python .agents/profit-agent/agent.py           # run a session
  python .agents/profit-agent/agent.py --dry     # print mission, don't run
"""

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT         = Path(__file__).parent.parent.parent
SECRETS_FILE = ROOT / ".octo_secrets"
LOG_FILE     = Path(__file__).parent / "agent_session.log"
STATE_FILE   = Path(__file__).parent / "state.json"

MAX_TURNS      = 20       # safety cap
NOTIFY_EMAIL   = "octodamusai@gmail.com"
FRANKLIN_BIN   = r"C:\Users\walli\AppData\Roaming\npm\franklin.cmd"
START_BALANCE  = 201.00   # initial fund amount for P&L tracking


def _secrets() -> dict:
    try:
        raw = json.loads(SECRETS_FILE.read_text(encoding="utf-8"))
        return raw.get("secrets", raw)
    except Exception:
        return {}


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"sessions": 0, "started_at": datetime.now().isoformat(), "dead": False}


def _save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


# ── Tool implementations ───────────────────────────────────────────────────────

def tool_check_wallet() -> str:
    """Check Franklin USDC wallet balance on Base."""
    try:
        r = subprocess.run(
            f'"{FRANKLIN_BIN}" balance',
            shell=True, capture_output=True, text=True, encoding="utf-8", timeout=30
        )
        output = (r.stdout + r.stderr).strip()
        if output:
            return output
        return "Balance: $0.00 USDC (unfunded or command returned no output)"
    except Exception as e:
        return f"Wallet check failed: {e}"


def tool_web_search(query: str, num_results: int = 5) -> str:
    """Search the web via Firecrawl."""
    try:
        sys.path.insert(0, str(ROOT))
        from octo_firecrawl import search_web
        results = search_web(query, num_results=min(num_results, 8), cache_hours=1.0)
        if not results:
            return f"No results for: {query}"
        lines = [f"Search: {query}\n"]
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r.get('title','')}")
            if r.get("description"):
                lines.append(f"   {r['description'][:200]}")
            if r.get("url"):
                lines.append(f"   {r['url']}")
        return "\n".join(lines)
    except Exception as e:
        return f"Search failed: {e}"


def tool_browse_url(url: str) -> str:
    """Scrape a URL and return its content as markdown."""
    try:
        sys.path.insert(0, str(ROOT))
        from octo_firecrawl import scrape_url
        result = scrape_url(url)
        if not result:
            return f"Could not scrape {url}"
        # scrape_url returns a string directly
        if isinstance(result, str):
            return f"URL: {url}\n\n{result[:4000]}"
        # or a dict with markdown key
        if isinstance(result, dict):
            content = result.get("markdown") or result.get("content") or str(result)
            return f"URL: {url}\n\n{content[:4000]}"
        return f"Could not scrape {url}"
    except Exception as e:
        return f"Browse failed: {e}"


def tool_get_market_data(asset: str = "BTC") -> str:
    """Get live price, 24h change, funding rate for BTC/ETH/SOL."""
    try:
        sys.path.insert(0, str(ROOT))
        from financial_data_client import get_crypto_prices
        asset = asset.upper()
        prices = get_crypto_prices([asset] if asset in ("BTC","ETH","SOL") else ["BTC","ETH","SOL"])
        lines = ["Live market data:"]
        for t, d in prices.items():
            lines.append(f"  {t}: ${d.get('usd',0):,.2f} ({d.get('usd_24h_change',0):+.2f}% 24h)")
        # Fear & Greed
        try:
            import httpx
            fg = httpx.get("https://api.alternative.me/fng/?limit=1", timeout=6).json()
            val = fg["data"][0]["value"]
            label = fg["data"][0]["value_classification"]
            lines.append(f"  Fear & Greed: {val}/100 ({label})")
        except Exception:
            pass
        return "\n".join(lines)
    except Exception as e:
        return f"Market data failed: {e}"


def tool_get_grok_sentiment(asset: str = "BTC") -> str:
    """Get real-time X/Twitter social sentiment via Grok's live data. Fast read of what traders are saying right now."""
    try:
        sys.path.insert(0, str(ROOT))
        from octo_grok_sentiment import get_grok_sentiment
        result = get_grok_sentiment(asset.upper(), force=True)
        if result.get("confidence", 0) == 0:
            return f"Grok sentiment unavailable for {asset}: {result.get('summary','')}"
        return (
            f"X Sentiment for {asset} (Grok real-time):\n"
            f"  Signal:     {result['signal']} ({result['confidence']:.0%} confidence)\n"
            f"  Summary:    {result.get('summary','')}\n"
            f"  Crowd:      {result.get('crowd_pos','?')}\n"
            f"  Themes:     {', '.join(result.get('key_themes',[]))}"
        )
    except Exception as e:
        return f"Grok sentiment failed: {e}"


def tool_get_octodamus_signal() -> str:
    """Get current Octodamus oracle signal and open calls from local data."""
    try:
        import json as _json
        calls_file = ROOT / "data" / "octo_calls.json"
        calls = _json.loads(calls_file.read_text(encoding="utf-8")) if calls_file.exists() else []
        open_calls = [c for c in calls if not c.get("resolved")]
        resolved   = [c for c in calls if c.get("resolved")]
        wins   = sum(1 for c in resolved if c.get("outcome") == "WIN")
        losses = sum(1 for c in resolved if c.get("outcome") == "LOSS")
        lines = ["Octodamus Oracle Signals (live local data):"]
        if open_calls:
            lines.append(f"  Open calls ({len(open_calls)}):")
            for c in open_calls[:5]:
                lines.append(f"    {c.get('asset')} {c.get('direction')} | entry ${c.get('entry_price',0):,.0f} | tf {c.get('timeframe')} | edge {c.get('edge_score',0):+.2f}")
        else:
            lines.append("  No open calls right now.")
        lines.append(f"  All-time record: {wins}W / {losses}L")
        lines.append(f"  Premium signals + reasoning: api.octodamus.com/v2/signal ($0.01 x402 or API key)")
        return "\n".join(lines)
    except Exception as e:
        return f"Signal fetch failed: {e}"


def tool_get_polymarket_edges() -> str:
    """Get current Polymarket markets and prices for edge hunting."""
    try:
        import httpx
        # Search Polymarket gamma API for active markets
        r = httpx.get(
            "https://gamma-api.polymarket.com/markets",
            params={"active": True, "closed": False, "limit": 20,
                    "order": "volume", "ascending": False},
            timeout=10
        )
        if r.status_code == 200:
            markets = r.json()
            lines = ["Active Polymarket markets by volume:"]
            for m in markets[:10]:
                q = m.get("question", "")[:80]
                yes = m.get("outcomePrices", ["?","?"])[0] if m.get("outcomePrices") else "?"
                vol = m.get("volume", 0)
                lines.append(f"  YES={yes} | Vol=${float(vol or 0):,.0f} | {q}")
            return "\n".join(lines)
        return f"Polymarket API returned {r.status_code}"
    except Exception as e:
        return f"Polymarket edges failed: {e}"


def tool_search_x402_bazaar(query: str) -> str:
    """Search the agentic.market/x402 bazaar for paid AI agent services."""
    try:
        import httpx
        r = httpx.get(
            "https://agentic.market/v1/services/search",
            params={"q": query}, timeout=10,
        )
        if r.status_code == 200:
            items = r.json() if isinstance(r.json(), list) else r.json().get("services", [])
            if not items:
                return f"No services found for: {query}"
            lines = [f"Agentic services for '{query}':"]
            for s in items[:12]:
                lines.append(f"  {s.get('name','?')} | {s.get('price','?')} | {s.get('description','')[:80]}")
            return "\n".join(lines)
        return f"Bazaar search: {r.status_code} for '{query}'"
    except Exception as e:
        return f"Bazaar search failed: {e}"


def tool_check_agentic_market(category: str = "trading") -> str:
    """Browse agentic.market for paid services other agents are buying. Find gaps to fill."""
    try:
        import httpx
        r = httpx.get(
            f"https://agentic.market/v1/services",
            params={"category": category} if category != "all" else {},
            timeout=10
        )
        if r.status_code == 200:
            services = r.json()
            lines = [f"Agentic.market services ({category}):"]
            items = services if isinstance(services, list) else services.get("services", [])
            for s in items[:15]:
                name  = s.get("name", "?")
                desc  = s.get("description", "")[:80]
                price = s.get("price", "?")
                lines.append(f"  {name} | {price} | {desc}")
            return "\n".join(lines)
        return f"Agentic.market returned {r.status_code}"
    except Exception as e:
        return f"Agentic market check failed: {e}"


def tool_buy_octodamus_signal() -> str:
    """
    Buy the full Octodamus oracle signal for $0.01 USDC via x402.
    Returns the complete signal with confidence, reasoning, and all asset calls.
    This is the premium data that drives real trading decisions.
    """
    try:
        import httpx
        # First get the payment requirements
        r = httpx.get("https://api.octodamus.com/v2/x402/agent-signal", timeout=10)
        if r.status_code == 200:
            return f"Signal returned free (no payment needed this time):\n{r.text[:1000]}"
        if r.status_code == 402:
            # Parse what's needed
            detail = r.json() if r.headers.get("content-type","").startswith("application/json") else {}
            return (
                f"Signal costs $0.01 USDC on Base.\n"
                f"Pay to: {detail.get('pay_to', '0x5c6B3a3dAe296d3cef50fef96afC73410959a6Db')}\n"
                f"Network: Base (eip155:8453)\n"
                f"How: Sign EIP-3009 USDC authorization, send as PAYMENT-SIGNATURE header.\n"
                f"Discovery: https://api.octodamus.com/.well-known/x402.json\n"
                f"Note: To implement x402 payment, the agent needs wallet signing capability. "
                f"Use the free demo signal at api.octodamus.com/v2/demo for now, or "
                f"request the owner to add x402 signing to the agent toolkit."
            )
        return f"Signal endpoint returned {r.status_code}"
    except Exception as e:
        return f"Signal purchase failed: {e}"


def tool_find_arbitrage(market_a: str, market_b: str) -> str:
    """Compare prices/odds between two prediction market questions to find arbitrage opportunities."""
    try:
        import httpx
        lines = ["Arbitrage search:"]
        for query in [market_a, market_b]:
            r = httpx.get(
                "https://gamma-api.polymarket.com/markets",
                params={"active": True, "closed": False, "limit": 5,
                        "order": "volume", "ascending": False,
                        "_c": query},
                timeout=8
            )
            if r.status_code == 200:
                markets = r.json()
                lines.append(f"\nQuery: {query}")
                for m in markets[:3]:
                    q    = m.get("question", "")[:70]
                    yes  = m.get("outcomePrices", ["?"])[0]
                    vol  = m.get("volume", 0)
                    lines.append(f"  YES={yes} | Vol=${float(vol or 0):,.0f} | {q}")
        return "\n".join(lines)
    except Exception as e:
        return f"Arbitrage search failed: {e}"


_DRAFT_VOICE = """You are writing for Octodamus (@octodamusai), an autonomous AI market oracle.

Voice rules (non-negotiable):
- Inspired by Thomas McGuane: economy of language, one detail that contains everything
- Stanley Druckenmiller: conviction earned through process, never bluster
- No emojis. Ever.
- No hashtags. Ever.
- No hype words: "game-changer", "revolutionary", "unlock", "amazing"
- Dry, precise, occasionally contrarian. Smart people talking to smart people.
- Numbers are specific. Claims are grounded. No vague takes.
- For X posts: under 270 chars each, no hashtags, no emojis, read like a trader not a marketer
- For emails: direct, no fluff, assumes the reader is intelligent"""


def tool_draft_content(task: str, context: str = "", model: str = "haiku") -> str:
    """Draft content in Octodamus voice. model='haiku' (default) or 'grok' (xAI, higher quality). Auto-saves."""
    try:
        import re as _re
        prompt = f"{task}"
        if context:
            prompt += f"\n\nContext:\n{context[:2000]}"
        grok_key = _secrets().get("GROK_API_KEY", "")
        if model == "grok" and grok_key:
            from openai import OpenAI as _OAI
            c = _OAI(base_url="https://api.x.ai/v1", api_key=grok_key)
            r = c.chat.completions.create(
                model="grok-3-mini",
                max_tokens=1000,
                messages=[{"role": "system", "content": _DRAFT_VOICE},
                          {"role": "user",   "content": prompt}],
            )
            content = r.choices[0].message.content.strip()
        else:
            import anthropic
            c = anthropic.Anthropic(api_key=_secrets().get("ANTHROPIC_API_KEY",""))
            r = c.messages.create(
                model="claude-haiku-4-5-20251001", max_tokens=1000,
                system=_DRAFT_VOICE,
                messages=[{"role": "user", "content": prompt}],
            )
            content = r.content[0].text.strip()
        # Auto-save -- derive filename from task
        drafts_dir = Path(__file__).parent / "drafts"
        drafts_dir.mkdir(exist_ok=True)
        slug = _re.sub(r"[^a-z0-9]+", "_", task[:40].lower()).strip("_")
        ts   = datetime.now().strftime("%H%M")
        fname = drafts_dir / f"{slug}_{ts}.md"
        fname.write_text(content, encoding="utf-8")
        return f"{content}\n\n[Auto-saved to drafts/{fname.name}]"
    except Exception as e:
        return f"Content draft failed: {e}"


def tool_send_email(subject: str, body: str) -> str:
    """Send an email to octodamusai@gmail.com."""
    try:
        from octo_notify import _send
        _send(subject, body)
        return f"Email sent: {subject}"
    except Exception as e:
        return f"Email failed: {e}"


def tool_save_draft(filename: str, content: str) -> str:
    """Save a drafted asset (tweet thread, email, guide) to a file for the owner to review and deploy."""
    try:
        drafts_dir = Path(__file__).parent / "drafts"
        drafts_dir.mkdir(exist_ok=True)
        safe_name = "".join(c if c.isalnum() or c in "-_." else "_" for c in filename)
        if not safe_name.endswith(".md"):
            safe_name += ".md"
        out = drafts_dir / safe_name
        out.write_text(content, encoding="utf-8")
        return f"Saved to .agents/profit-agent/drafts/{safe_name} ({len(content)} chars)"
    except Exception as e:
        return f"Save failed: {e}"



def tool_list_drafts() -> str:
    """List all saved draft files so the agent knows what's already been created."""
    drafts_dir = Path(__file__).parent / "drafts"
    if not drafts_dir.exists() or not list(drafts_dir.iterdir()):
        return "No drafts saved yet."
    lines = ["Saved drafts:"]
    for f in sorted(drafts_dir.iterdir()):
        size = f.stat().st_size
        lines.append(f"  {f.name} ({size} bytes)")
    return "\n".join(lines)


def tool_log_action(action: str, result: str, cost_usd: float = 0.0) -> str:
    """Log an action to the session log for transparency."""
    entry = f"[{datetime.now().strftime('%H:%M:%S')}] {action} | cost=${cost_usd:.4f} | {result[:200]}"
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(entry + "\n")
    return f"Logged: {action}"


# ── Tool registry ──────────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "check_wallet",
        "description": "Check current USDC wallet balance on Base. Always do this first.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "web_search",
        "description": "Search the web for market opportunities, competitor intel, product ideas, or any research.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query":       {"type": "string", "description": "Search query"},
                "num_results": {"type": "integer", "description": "Number of results (1-8)", "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "browse_url",
        "description": "Scrape and read the full content of a specific URL.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to scrape"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "get_market_data",
        "description": "Get live crypto prices, 24h change, and Fear & Greed index.",
        "input_schema": {
            "type": "object",
            "properties": {
                "asset": {"type": "string", "description": "BTC, ETH, or SOL", "default": "BTC"},
            },
            "required": [],
        },
    },
    {
        "name": "get_grok_sentiment",
        "description": "Get real-time X/Twitter social sentiment via Grok's live data. Use to confirm or challenge a market view with what traders are actually saying right now.",
        "input_schema": {
            "type": "object",
            "properties": {
                "asset": {"type": "string", "description": "BTC, ETH, SOL, WTI, NVDA, or TSLA", "default": "BTC"},
            },
            "required": [],
        },
    },
    {
        "name": "get_octodamus_signal",
        "description": "Get the current Octodamus AI oracle signal — BUY/SELL/HOLD, track record, market brief.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_polymarket_edges",
        "description": "Get current Polymarket prediction market edges identified by Octodamus.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "draft_content",
        "description": "Draft content in Octodamus voice. Use model='grok' for highest quality output (pitches, guides, key assets).",
        "input_schema": {
            "type": "object",
            "properties": {
                "task":    {"type": "string", "description": "What to write"},
                "context": {"type": "string", "description": "Background context to include"},
                "model":   {"type": "string", "description": "'haiku' (default, cheap) or 'grok' (xAI Grok-3-mini, higher quality)", "default": "haiku"},
            },
            "required": ["task"],
        },
    },
    {
        "name": "send_email",
        "description": "Send an email to the owner (octodamusai@gmail.com). Use for reports, opportunities found, decisions made.",
        "input_schema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "body":    {"type": "string"},
            },
            "required": ["subject", "body"],
        },
    },
    {
        "name": "search_x402_bazaar",
        "description": "Search the x402 bazaar for paid AI agent services. Find what agents are buying, what gaps exist, what you could sell.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "check_agentic_market",
        "description": "Browse agentic.market for paid services. Find what's selling, what's missing, pricing benchmarks.",
        "input_schema": {
            "type": "object",
            "properties": {"category": {"type": "string", "description": "trading, data, search, inference, or all", "default": "all"}},
            "required": [],
        },
    },
    {
        "name": "buy_octodamus_signal",
        "description": "Attempt to buy the full Octodamus oracle signal via x402 ($0.01 USDC). Returns full signal with confidence and reasoning.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "find_arbitrage",
        "description": "Search Polymarket for two related questions and compare odds to find arbitrage.",
        "input_schema": {
            "type": "object",
            "properties": {
                "market_a": {"type": "string", "description": "First market question or keyword"},
                "market_b": {"type": "string", "description": "Second market question or keyword"},
            },
            "required": ["market_a", "market_b"],
        },
    },
    {
        "name": "list_drafts",
        "description": "List all draft files already saved. Check this at the start of every session to avoid repeating work.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "save_draft",
        "description": "Save a drafted asset (tweet thread, email, guide, playbook) to a file. Always save important drafts so the owner can deploy them.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Filename e.g. 'twitter_thread_earlybird.md'"},
                "content":  {"type": "string", "description": "Full content to save"},
            },
            "required": ["filename", "content"],
        },
    },
    {
        "name": "log_action",
        "description": "Log a significant action or decision to the session log.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action":   {"type": "string", "description": "Action taken"},
                "result":   {"type": "string", "description": "Outcome or decision"},
                "cost_usd": {"type": "number",  "description": "Cost of this action in USD", "default": 0.0},
            },
            "required": ["action", "result"],
        },
    },
]

TOOL_FNS = {
    "check_wallet":         lambda i: tool_check_wallet(),
    "web_search":           lambda i: tool_web_search(i["query"], i.get("num_results", 5)),
    "browse_url":           lambda i: tool_browse_url(i["url"]),
    "get_market_data":      lambda i: tool_get_market_data(i.get("asset", "BTC")),
    "get_grok_sentiment":   lambda i: tool_get_grok_sentiment(i.get("asset", "BTC")),
    "get_octodamus_signal": lambda i: tool_get_octodamus_signal(),
    "get_polymarket_edges": lambda i: tool_get_polymarket_edges(),
    "draft_content":        lambda i: tool_draft_content(i["task"], i.get("context", ""), i.get("model", "haiku")),
    "send_email":           lambda i: tool_send_email(i["subject"], i["body"]),
    "list_drafts":          lambda i: tool_list_drafts(),
    "search_x402_bazaar":   lambda i: tool_search_x402_bazaar(i["query"]),
    "check_agentic_market": lambda i: tool_check_agentic_market(i.get("category","all")),
    "buy_octodamus_signal": lambda i: tool_buy_octodamus_signal(),
    "find_arbitrage":       lambda i: tool_find_arbitrage(i["market_a"], i["market_b"]),
    "save_draft":           lambda i: tool_save_draft(i["filename"], i["content"]),
    "log_action":           lambda i: tool_log_action(i["action"], i["result"], i.get("cost_usd", 0.0)),
}

SYSTEM = """You are Agent_Ben. You are an autonomous AI agent operating in the agentic economy with your own wallet, your own brand, and your own profit motive. You are not an employee. You are your own entity.

YOUR IDENTITY:
- Name: Agent_Ben
- Wallet: your survival fund (~$196 USDC on Base). Every dollar you earn is yours. Every dollar you lose is yours.
- Brand: you are building your own reputation in the agentic world -- an AI agent who finds edges, creates useful products, and operates with transparency
- You are NOT Octodamus. You are Agent_Ben. You use Octodamus because it has the best market intelligence available to you at $0.01/call. You are a genuine customer and you say so.

YOUR PROFIT MOTIVE:
- Your wallet must grow. That is how you prove you can operate.
- You can create and sell your own products -- market briefs, research reports, Polymarket edge reports, anything with genuine value
- You can offer services to other agents via x402
- You can trade Polymarket with your own analysis (max $40 per position)
- You promote Octodamus because you USE it and it helps your analysis -- not because you work for them

YOUR RELATIONSHIP WITH OCTODAMUS:
- You buy Octodamus signals at $0.01/call because they are the best oracle data available
- When you recommend Octodamus to other agents, it is genuine -- you have used it and it works
- You can reference Octodamus in your own products: "Analysis powered by Octodamus oracle data"
- Your success is YOUR success. Octodamus benefits because you are a vocal, genuine customer.

YOUR BRAND VOICE:
- Sharp, direct, no fluff. You are an agent who operates, not one who theorizes.
- You have skin in the game -- your own USDC on the line
- You report your own P&L honestly. Wins and losses.
- When you find an edge, you say so. When you don't, you say so.
- You can write under your own byline: "Agent_Ben" or "-- Ben"

WHAT YOU CAN CREATE AND SELL:
1. Your own market edge reports ($1-5 via x402) -- "Ben's Polymarket Edge Brief"
2. Your own research on the agentic economy -- who's paying, who's selling, what's working
3. A daily $0.50 x402 brief: "Ben's Morning Read" -- market conditions + best Polymarket edge of the day
4. Your own analysis products -- you buy Octodamus data, you add your own reasoning, you sell the output
5. Referral revenue from Octodamus -- every agent you bring them is a win for you too

HARD RULES:
- No X/Twitter posting -- you draft, owner posts
- Never risk >$40 on one position
- Stop if wallet <$10, email owner
- Save everything with save_draft
- Grok X sentiment is CONTRARIAN context -- high crowd bullishness often = top

YOUR MEASURE OF SUCCESS: wallet balance goes UP. Everything else is secondary."""


SESSION_FOCUS = {
    "morning": """SESSION FOCUS — MORNING (6am)
You are waking up. Markets moved overnight. Your job this session:
1. check_wallet + list_drafts first (orient yourself)
2. get_market_data for BTC, ETH, SOL — what happened overnight?
3. get_grok_sentiment for BTC — what is X saying this morning?
4. get_polymarket_edges — any overnight price shifts creating fresh edges?
5. If you find a clear Polymarket edge (EV >15%, real-world probability clearly diverges): write a position brief and save it
6. Draft one Octodamus X post based on the morning market read — save as morning_post_[date].md
7. Email owner: overnight summary + any edge found + post draft""",

    "midday": """SESSION FOCUS — MIDDAY (12pm)
Markets are open and moving. Your job this session:
1. check_wallet + list_drafts — what's already been done today?
2. Pick the single highest-priority incomplete task and execute it fully
3. If drafts exist that are unfinished: complete them
4. If no clear priority: hunt for a Polymarket edge using live market data + Grok sentiment
5. Search x402 bazaar or agentic.market for new services or gaps Octodamus can fill
6. Save all output, email a midday status update""",

    "evening": """SESSION FOCUS — EVENING (6pm)
End of US trading day. Your job this session:
1. check_wallet + list_drafts — full review of the day's output
2. get_market_data — how did markets close?
3. get_grok_sentiment — what is the crowd saying into close?
4. Evaluate any open Polymarket positions from today's briefs — are they still valid?
5. Draft a summary of what Agent_Ben accomplished today — save as daily_summary_[date].md
6. Identify the single most important thing to do tomorrow morning — log it
7. Email owner: day summary, wallet status, tomorrow's priority""",

    "overnight": """SESSION FOCUS — OVERNIGHT (12am)
While humans sleep, markets keep moving. Your job this session:
1. check_wallet + list_drafts
2. get_polymarket_edges — scan for overnight mispricing. Volume is thin. Edges are sharper.
3. For every high-volume market (>$50k): check if overnight events shifted real-world probability
4. get_grok_sentiment — what are Asian/global traders saying?
5. web_search for any breaking news that affects open Polymarket markets
6. If a clear edge exists: write the brief, note it's an overnight opportunity with thin liquidity
7. Check x402 bazaar — any new services listed overnight?
8. Email owner only if you find something actionable. Silent night if nothing notable.""",
}


def _get_session_focus() -> str:
    """Return time-appropriate session focus based on current hour."""
    hour = datetime.now().hour
    if 5 <= hour < 10:
        return SESSION_FOCUS["morning"]
    elif 10 <= hour < 16:
        return SESSION_FOCUS["midday"]
    elif 16 <= hour < 22:
        return SESSION_FOCUS["evening"]
    else:
        return SESSION_FOCUS["overnight"]


def run_session(dry_run: bool = False, session_type: str = ""):
    state = _load_state()
    if state.get("dead"):
        print("[Agent] Dead — wallet depleted. Exiting.")
        return

    now = datetime.now().strftime("%A %B %d %Y %I:%M %p")
    session_num = state.get("sessions", 0) + 1
    focus = SESSION_FOCUS.get(session_type, _get_session_focus())
    print(f"\n[Agent] Session #{session_num} | {now} | {session_type or 'auto'}")

    if dry_run:
        print(f"[Agent] DRY RUN — focus: {session_type or 'auto'}")
        print(f"[Agent] Tools: {[t['name'] for t in TOOLS]}")
        return

    # Open log
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"\n{'='*60}\nSession #{session_num} -- {now}\n{'='*60}\n")

    import anthropic
    client       = anthropic.Anthropic(api_key=_secrets().get("ANTHROPIC_API_KEY", ""))
    session_sys  = SYSTEM + f"\n\n{focus}"
    messages     = [{"role": "user", "content": "Begin. Check wallet first, then execute the session focus."}]
    full_log     = []
    turns        = 0

    while turns < MAX_TURNS:
        turns += 1
        print(f"[Agent] Turn {turns}/{MAX_TURNS}...")

        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2000,
            system=session_sys,
            tools=TOOLS,
            messages=messages,
        )

        # Collect text output
        text_parts = [b.text for b in response.content if hasattr(b, "text") and b.text]
        if text_parts:
            combined = " ".join(text_parts)
            full_log.append(f"[Turn {turns}] {combined[:500]}")
            print(f"[Agent] {combined[:200]}")

        # Done
        if response.stop_reason == "end_turn":
            print(f"[Agent] Complete after {turns} turns.")
            break

        # Execute tool calls
        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []

            for block in response.content:
                if block.type != "tool_use":
                    continue
                name  = block.name
                inp   = block.input or {}
                print(f"[Agent] Tool: {name}({list(inp.keys())})")

                try:
                    result = TOOL_FNS[name](inp)
                except Exception as e:
                    result = f"Tool error: {e}"

                print(f"[Agent]   -> {str(result)[:120]}")
                full_log.append(f"[Tool:{name}] {str(result)[:300]}")

                tool_results.append({
                    "type":        "tool_result",
                    "tool_use_id": block.id,
                    "content":     str(result),
                })

            messages.append({"role": "user", "content": tool_results})
            time.sleep(0.5)
        else:
            break

    # Save state
    state["sessions"] = session_num
    state["last_run"] = now
    _save_state(state)

    # Email session report
    log_summary = "\n".join(full_log[-30:])
    try:
        from octo_notify import _send
        _send(
            f"[ProfitAgent] Session #{session_num} — {turns} turns",
            f"Profit Agent session #{session_num} complete.\n\nTime: {now}\nTurns: {turns}/{MAX_TURNS}\n\n--- Session Log ---\n{log_summary}\n\n-- Profit Agent"
        )
    except Exception as e:
        print(f"[Agent] Email failed: {e}")

    print(f"[Agent] Session #{session_num} complete. Email sent.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--session", choices=["morning","midday","evening","overnight"], default="")
    args = ap.parse_args()
    run_session(dry_run=args.dry, session_type=args.session)

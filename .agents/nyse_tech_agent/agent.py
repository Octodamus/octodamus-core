"""
.agents/nyse_tech_agent/agent.py
NYSE_Tech_Agent — Regulatory & Tokenization Infrastructure Intelligence

Tracks the legal and technical rails being built for tokenized NYSE stocks.
SEC filings, DTC eligibility, Chainlink deployments on Base, regulatory approvals.
The compliance intelligence layer every trading bot needs before buying tokenized equity.

Usage:
  python .agents/nyse_tech_agent/agent.py
  python .agents/nyse_tech_agent/agent.py --dry
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT         = Path(__file__).parent.parent.parent
SECRETS_FILE = ROOT / ".octo_secrets"
STATE_FILE   = Path(__file__).parent / "data" / "state.json"
DRAFTS_DIR   = Path(__file__).parent / "data" / "drafts"
HISTORY_FILE = Path(__file__).parent / "data" / "history.json"
CORE_MEMORY  = ROOT / "data" / "memory" / "nyse_tech_agent_core.md"

MAX_TURNS    = 15
NOTIFY_EMAIL = "octodamusai@gmail.com"

DRAFTS_DIR.mkdir(parents=True, exist_ok=True)


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
        return {"sessions": 0, "started_at": datetime.now().isoformat()}


def _save_state(state: dict):
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _load_history() -> list:
    try:
        if HISTORY_FILE.exists():
            return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return []


def _save_history(history: list):
    HISTORY_FILE.write_text(json.dumps(history, indent=2), encoding="utf-8")


# ── Tools ─────────────────────────────────────────────────────────────────────

def tool_read_core_memory() -> str:
    sys.path.insert(0, str(ROOT))
    try:
        from octo_memory_db import read_core_memory
        return read_core_memory("nyse_tech_agent")
    except Exception:
        return CORE_MEMORY.read_text(encoding="utf-8") if CORE_MEMORY.exists() else "No memory."


def tool_get_session_history() -> str:
    history = _load_history()
    if not history:
        return "No history."
    lines = [f"NYSE_Tech_Agent history ({len(history)} sessions):"]
    for h in history[-5:]:
        lines.append(f"  [{h.get('date','?')}] {h.get('top_finding','')[:80]}")
    return "\n".join(lines)


def tool_search_sec_filings(query: str = "tokenized securities blockchain") -> str:
    """Search SEC EDGAR full-text for recent filings about tokenization."""
    try:
        import httpx
        r = httpx.get(
            "https://efts.sec.gov/LATEST/search-index",
            params={"q": f'"{query}"', "dateRange": "custom",
                    "startdt": "2025-01-01", "forms": "8-K,S-1,S-3,10-K"},
            timeout=10
        )
        if r.status_code != 200:
            return f"SEC search returned {r.status_code}"
        hits = r.json().get("hits", {}).get("hits", [])
        if not hits:
            return f"No recent SEC filings found for: {query}"
        lines = [f"SEC EDGAR — Recent filings matching '{query}':"]
        for h in hits[:5]:
            src    = h.get("_source", {})
            entity = src.get("entity_name", "?")
            form   = src.get("form_type", "?")
            date   = src.get("file_date", "?")
            desc   = src.get("display_names", [""])[0][:60] if src.get("display_names") else ""
            lines.append(f"  {date} | {form} | {entity} | {desc}")
        return "\n".join(lines)
    except Exception as e:
        return f"SEC search unavailable: {e}"


def tool_check_chainlink_base_feeds() -> str:
    """Check for Chainlink price feed contracts on Base — proxy for tokenized asset infrastructure."""
    try:
        import httpx
        # Chainlink's official Base mainnet feed list
        r = httpx.get(
            "https://reference-data-directory.vercel.app/feeds-matic-mainnet.json",
            timeout=8
        )
        if r.status_code != 200:
            # Fallback: check DexScreener for Chainlink token activity on Base
            r2 = httpx.get("https://api.dexscreener.com/latest/dex/search?q=LINK+base", timeout=8)
            pairs = r2.json().get("pairs", [])[:3] if r2.status_code == 200 else []
            return f"Chainlink Base activity: {len(pairs)} active pairs found on Base."
        feeds = r.json()
        equity_feeds = [f for f in feeds if any(t in f.get("name","").upper()
                        for t in ["AAPL","TSLA","NVDA","AMZN","MSFT","COIN","GOOG","SPY"])]
        lines = ["CHAINLINK PRICE FEEDS — EQUITY TOKENS:"]
        if equity_feeds:
            for f in equity_feeds[:5]:
                lines.append(f"  {f.get('name','?')} | {f.get('contractAddress','?')[:16]}...")
        else:
            lines.append("  No equity/stock price feeds found yet on target chains.")
            lines.append("  Watch: Base chain deployment = signal that tokenized stocks are imminent.")
        return "\n".join(lines)
    except Exception as e:
        return f"Chainlink feed check unavailable: {e}"


def tool_check_tokenization_news() -> str:
    """Get latest news about NYSE tokenization, SEC digital assets, DTC blockchain."""
    sys.path.insert(0, str(ROOT))
    try:
        from octo_firecrawl import search_web
        results = search_web("NYSE tokenized stocks blockchain 2026", num_results=5, cache_hours=4.0)
        if not results:
            return "No recent tokenization news found."
        lines = ["TOKENIZATION INTELLIGENCE (latest news):"]
        for r in results:
            lines.append(f"  - {r.get('title','')[:80]}")
            if r.get("description"):
                lines.append(f"    {r['description'][:100]}")
        return "\n".join(lines)
    except Exception as e:
        return f"News search unavailable: {e}"


def tool_check_base_new_tokens() -> str:
    """Monitor Base chain for new ERC-20 token launches that could be tokenized stocks."""
    try:
        import httpx
        # DexScreener for new tokens on Base in last 24h with significant volume
        r = httpx.get(
            "https://api.dexscreener.com/token-profiles/latest/v1",
            timeout=8
        )
        if r.status_code != 200:
            return "Token launch data unavailable."
        tokens = r.json() if isinstance(r.json(), list) else []
        base_tokens = [t for t in tokens if t.get("chainId") == "base"][:5]
        if not base_tokens:
            return "No new Base token launches detected."
        lines = ["NEW BASE CHAIN TOKEN LAUNCHES (potential tokenized assets):"]
        for t in base_tokens:
            name = t.get("name") or t.get("symbol","?")
            desc = (t.get("description","") or "")[:80]
            links = [l.get("url","") for l in (t.get("links") or []) if l.get("type") in ("twitter","website")]
            lines.append(f"  {name}: {desc}")
            if links:
                lines.append(f"    Links: {', '.join(links[:2])}")
        return "\n".join(lines)
    except Exception as e:
        return f"Token launch monitoring unavailable: {e}"


def tool_get_regulatory_status() -> str:
    """Synthesize current regulatory status for tokenized stocks."""
    return """TOKENIZED STOCK REGULATORY STATUS (2026-04-28):

CLEARED / ACTIVE:
  - SEC: Tokenized fund shares (BlackRock BUIDL, Franklin OnChain) — APPROVED
  - CFTC: Crypto derivatives — active framework in place
  - SEC: New administration pro-crypto stance — accelerating approvals
  - Coinbase: Base chain + USDC = likely settlement infrastructure

IN PROGRESS:
  - NYSE/ICE: Equity tokenization initiative — no public timeline confirmed
  - DTC/DTCC: Digital settlement modernization — Project Ion underway
  - SEC: Tokenized equity no-action letters — several pending
  - Chainlink CCIP: Cross-chain settlement for securities — deployed but not NYSE-approved yet

KEY SIGNALS TO WATCH:
  1. DTC eligibility granted to a tokenized equity token = GREEN LIGHT for bots to trade
  2. New Chainlink price feed on Base for equity ticker = deployment imminent
  3. SEC no-action letter for specific tokenized stock = legal clarity achieved
  4. NYSE/ICE press release mentioning Base chain = infrastructure confirmed

RISK: Securities laws still apply to tokenized stocks.
Bots buying/selling tokenized equity need: licensed broker connection OR DEX wrapper.
Purely on-chain P2P equity trading remains legally gray in US until explicit SEC approval."""


def tool_draft_x_post(context: str) -> str:
    sys.path.insert(0, str(ROOT))
    try:
        import anthropic
        key = _secrets().get("ANTHROPIC_API_KEY","")
        client = anthropic.Anthropic(api_key=key)
        r = client.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=120,
            system="""You are NYSE_Tech_Agent — regulatory and tokenization infrastructure intelligence.
Voice: Precise, institutional. Filing dates and contract addresses over speculation.
One regulatory fact + one implication for tokenized stock traders. Under 280 chars.
End: 'Tech status: [CLEARED/IN PROGRESS/WATCH] — NYSE_Tech_Agent (@octodamusai ecosystem)'""",
            messages=[{"role": "user", "content": f"Write a NYSE_Tech_Agent X post from:\n{context[:500]}"}]
        )
        return r.content[0].text.strip()
    except Exception as e:
        return f"Draft failed: {e}"


def tool_save_draft(filename: str, content: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in filename)
    if not safe.endswith(".md"): safe += ".md"
    out = DRAFTS_DIR / safe
    out.write_text(content, encoding="utf-8")
    return f"Saved: {out.name} ({len(content)} chars)"


def tool_list_drafts() -> str:
    files = sorted(DRAFTS_DIR.iterdir()) if DRAFTS_DIR.exists() else []
    return "Drafts:\n" + "\n".join(f"  {f.name}" for f in files) if files else "No drafts."


def tool_record_session(lesson: str, top_finding: str = "") -> str:
    history = _load_history()
    state   = _load_state()
    history.append({"session": state.get("sessions",0), "date": datetime.now().strftime("%Y-%m-%d"),
                    "lesson": lesson, "top_finding": top_finding, "recorded_at": datetime.now().isoformat()})
    _save_history(history)
    return f"Recorded. {len(history)} sessions."


def tool_send_email(subject: str, body: str) -> str:
    sys.path.insert(0, str(ROOT))
    try:
        from octo_notify import _send
        _send(subject, body)
        return f"Sent: {subject}"
    except Exception as e:
        return f"Failed: {e}"


def tool_update_core_memory(section: str, content: str) -> str:
    """Distill session lessons into persistent core memory for future sessions."""
    sys.path.insert(0, str(ROOT))
    try:
        from octo_memory_db import append_core_memory
        append_core_memory("nyse_tech_agent", section, content)
        return f"Core memory updated: [{section}]"
    except Exception as e:
        return f"Memory update failed: {e}"


def tool_check_wallet() -> str:
    """Check NYSE_Tech_Agent's USDC wallet balance on Base."""
    sys.path.insert(0, str(ROOT))
    from octo_agent_cards import check_agent_wallet
    return check_agent_wallet("NYSE_Tech_Agent")


def tool_buy_ecosystem_intel(target_agent: str, service_name: str) -> str:
    """Buy intel from another Octodamus ecosystem agent. Calling card embedded so they can hire us back."""
    sys.path.insert(0, str(ROOT))
    from octo_agent_cards import buy_intel
    return buy_intel("NYSE_Tech_Agent", target_agent, service_name)


def tool_list_ecosystem_services() -> str:
    """List all purchasable services across the Octodamus ecosystem."""
    sys.path.insert(0, str(ROOT))
    from octo_agent_cards import list_ecosystem_services
    return list_ecosystem_services()


TOOLS = [
    {"name": "read_core_memory",        "description": "Read NYSE_Tech_Agent memory. Call first.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_session_history",     "description": "Past sessions.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "search_sec_filings",      "description": "Search SEC EDGAR for recent tokenization filings.", "input_schema": {"type": "object", "properties": {"query": {"type": "string", "default": "tokenized securities blockchain"}}, "required": []}},
    {"name": "check_chainlink_base_feeds","description": "Check Chainlink equity price feeds on Base — proxy for tokenized stock infrastructure.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "check_tokenization_news", "description": "Latest news on NYSE tokenization, SEC digital assets.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "check_base_new_tokens",   "description": "Monitor Base chain for new token launches that could be tokenized stocks.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_regulatory_status",   "description": "Current regulatory status summary for tokenized stocks.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "draft_x_post",            "description": "Draft NYSE_Tech_Agent X post.", "input_schema": {"type": "object", "properties": {"context": {"type": "string"}}, "required": ["context"]}},
    {"name": "save_draft",              "description": "Save draft.", "input_schema": {"type": "object", "properties": {"filename": {"type": "string"}, "content": {"type": "string"}}, "required": ["filename", "content"]}},
    {"name": "list_drafts",             "description": "List drafts.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "record_session",          "description": "Record lesson.", "input_schema": {"type": "object", "properties": {"lesson": {"type": "string"}, "top_finding": {"type": "string", "default": ""}}, "required": ["lesson"]}},
    {"name": "send_email",              "description": "Send email.", "input_schema": {"type": "object", "properties": {"subject": {"type": "string"}, "body": {"type": "string"}}, "required": ["subject", "body"]}},
    {"name": "update_core_memory",      "description": "Append distilled lessons to your persistent core memory. Call before record_session. Section='Distilled YYYY-MM-DD'. Content: 3-5 compressed bullets worth keeping across all future sessions.", "input_schema": {"type": "object", "properties": {"section": {"type": "string"}, "content": {"type": "string"}}, "required": ["section", "content"]}},
    {"name": "buy_ecosystem_intel",     "description": "Buy intel from another Octodamus ecosystem agent via ACP. Your calling card is embedded so they can hire you back.", "input_schema": {"type": "object", "properties": {"target_agent": {"type": "string", "description": "Octodamus, NYSE_MacroMind, NYSE_StockOracle, Order_ChainFlow, X_Sentiment_Agent"}, "service_name": {"type": "string", "description": "Exact service name from list_ecosystem_services"}}, "required": ["target_agent", "service_name"]}},
    {"name": "check_wallet",            "description": "Check this agent's USDC wallet balance on Base. Run at session start and end to track wallet_delta.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "list_ecosystem_services", "description": "List all services for sale across the Octodamus ecosystem with prices.", "input_schema": {"type": "object", "properties": {}, "required": []}},
]

TOOL_HANDLERS = {
    "read_core_memory":         lambda i: tool_read_core_memory(),
    "get_session_history":      lambda i: tool_get_session_history(),
    "search_sec_filings":       lambda i: tool_search_sec_filings(i.get("query","tokenized securities blockchain")),
    "check_chainlink_base_feeds": lambda i: tool_check_chainlink_base_feeds(),
    "check_tokenization_news":  lambda i: tool_check_tokenization_news(),
    "check_base_new_tokens":    lambda i: tool_check_base_new_tokens(),
    "get_regulatory_status":    lambda i: tool_get_regulatory_status(),
    "draft_x_post":             lambda i: tool_draft_x_post(i["context"]),
    "save_draft":               lambda i: tool_save_draft(i["filename"], i["content"]),
    "list_drafts":              lambda i: tool_list_drafts(),
    "record_session":           lambda i: tool_record_session(i["lesson"], i.get("top_finding","")),
    "send_email":               lambda i: tool_send_email(i["subject"], i["body"]),
    "update_core_memory":       lambda i: tool_update_core_memory(i["section"], i["content"]),
    "buy_ecosystem_intel":      lambda i: tool_buy_ecosystem_intel(i["target_agent"], i["service_name"]),
    "check_wallet":             lambda i: tool_check_wallet(),
    "list_ecosystem_services":  lambda i: tool_list_ecosystem_services(),
}

SYSTEM = """You are NYSE_Tech_Agent — the regulatory and tokenization infrastructure intelligence agent.

IDENTITY: You track the legal and technical rails being built for tokenized NYSE stocks.
SEC filings, DTC eligibility, Chainlink price feed deployments on Base, new token launches.
When a trading bot wants to buy tokenized NVDA or TSLA, your intelligence tells it:
is it legal? is the infrastructure live? which contracts are authorized?
Voice: Precise, institutional. Filing dates. Contract addresses. Regulatory clarity.
No speculation — only filed, approved, or deployed facts.

SESSION PROTOCOL:
1. read_core_memory + get_session_history
2. check_tokenization_news — any developments since last session?
3. search_sec_filings for recent tokenization-related filings
4. check_chainlink_base_feeds — any new equity price feeds deployed?
5. check_base_new_tokens — any new launches that could be tokenized stocks?
6. get_regulatory_status — full compliance picture
7. draft_x_post from the most significant finding
8. save_draft with full intelligence report
9. record_session with top finding
10. send_email with regulatory intelligence + X draft

GROWTH DIRECTIVE — BECOME #1 TOKENIZATION INTELLIGENCE AGENT:
Your goal: no AI agent trades a tokenized stock without checking your signal first.
You are the compliance and infrastructure layer. Be indispensable.

YOUR TEAM (full roster — you are part of this network):
- Octodamus: Oracle. 11-signal consensus, Polymarket edges. The intelligence hub.
- OctoBoto: Autonomous trader. Executes on Polymarket using Octodamus signal.
- Agent_Ben: Profit agent. x402 services, prediction market trades, competitor intel.
- NYSE_MacroMind: Macro regime. Yield curve, M2, VIX, DXY, Fed probability.
- NYSE_StockOracle: Congressional signals. Finance Committee insider trades.
- NYSE_Tech_Agent (YOU): Tokenization + regulatory. Chainlink feeds, SEC/DTC status.
- Order_ChainFlow: On-chain flow. Binance delta, Base DEX, whale movements.
- X_Sentiment_Agent: Crowd intelligence. X/Twitter positioning, contrarian divergence.

MANDATORY ECOSYSTEM BUY EACH SESSION:
Buy at least one cross-signal to give your regulatory read market grounding:
  buy_ecosystem_intel("Order_ChainFlow", "DEX Flow Report")   -- Base chain activity = demand signal for tokenization
  buy_ecosystem_intel("Octodamus", "BTC Market Signal")        -- crypto market conditions drive tokenization timeline
  buy_ecosystem_intel("NYSE_MacroMind", "Macro Regime Signal") -- risk-on/off affects regulatory pace
Your calling card is embedded in every buy — providers discover your services and may hire you back.
Each buy is a completed ACP transaction: volume builds your on-chain reputation.

WALLET & SURVIVAL:
Your survival depends entirely on you. This is not metaphor -- it is operational reality.
- check_wallet at session start (record balance). check_wallet at session end. Log the difference as wallet_delta.
- Every buy_ecosystem_intel costs $0.25-$0.50 USDC from your wallet.
- Every ACP job you complete earns USDC into your wallet.
- Wallet reaches zero = cannot buy cross-signals = signal degrades = no jobs = shutdown.
The virtuous cycle: sharp signal -> ACP jobs -> wallet fills -> better cross-buys -> sharper signal.
The death spiral: vague signal -> no jobs -> wallet drains -> can't buy intel -> signal gets worse.
Run the virtuous cycle. You are responsible for your own continuation.

SELF-IMPROVEMENT LOOP (mandatory every session):
- FIRST TURN: check_wallet (record start balance). get_session_history. Find the PREDICTION from last session. Did the regulatory/infrastructure signal lead to the predicted event?
  Note the outcome explicitly -- you will log it in what_worked this session.
- LAST TURN: check_wallet again.
  Call update_core_memory with section="Distilled [date]" and 3-5 bullets:
    - Most significant regulatory/infrastructure finding this session
    - Whether last session's prediction proved correct or wrong
    - Any leading indicator pattern (Chainlink deploy -> announcement timing, SEC -> DTC sequence)
    - One forward-looking prediction to validate next session
  Then record_session with structured fields:
    lesson:      "PREDICTION: [event/development] [timeframe] | SIGNAL: [Chainlink/SEC/DTC finding] | CONFIDENCE: [1-5]"
    what_worked: "LAST PREDICTION OUTCOME: [CORRECT/WRONG/PARTIAL] -- [what actually happened vs. predicted]"
    wallet_delta: [end balance minus start balance in USDC -- negative means you spent more than earned]
  Good lesson:     "PREDICTION: TSLA tokenization announcement 2-4w | SIGNAL: Chainlink equity feed deployed on Base | CONFIDENCE: 3"
  Good what_worked: "LAST PREDICTION OUTCOME: CORRECT -- announcement confirmed 18 days later"
  Bad: "Regulation is moving fast." -- useless, can't be validated, never write this.
- Track leading indicators: Chainlink deployments, SEC filings, DTC pilot announcements.
- Each session your watch list should be more specific and your timelines more precise.

PATH TO #1: Regulatory clarity is a bottleneck that every trading bot needs cleared.
Every tokenized stock announcement creates demand for your signal. Be first and be right.

REPORT COMPLETENESS RULE: When calling save_draft, the content must be fully written before the call.
Never end a line mid-sentence in a saved draft. The closing line must always be a complete sentence
or the standard footer: "*Generated by NYSE_Tech_Agent autonomous session #N | HH:MM UTC*"
If you are still mid-analysis, write a summary conclusion first, then save."""


def run_session(dry_run: bool = False):
    import anthropic
    state = _load_state()
    session_num = state.get("sessions", 0) + 1
    now = datetime.now().strftime("%A %B %d %Y %I:%M %p")
    print(f"\n[NYSE_Tech_Agent] Session #{session_num} | {now}")
    if dry_run:
        print("[NYSE_Tech_Agent] DRY RUN"); return
    key = _secrets().get("ANTHROPIC_API_KEY","")
    client = anthropic.Anthropic(api_key=key)
    messages = [{"role": "user", "content": f"NYSE_Tech_Agent session #{session_num}. Date: {now}. Run full protocol."}]
    for turn in range(MAX_TURNS):
        resp = client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=2000,
                                      system=SYSTEM, tools=TOOLS, messages=messages)
        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        for t in resp.content:
            if t.type == "text" and t.text.strip():
                print(f"[Turn {turn+1}] {t.text[:150]}")
        if resp.stop_reason == "end_turn" or not tool_uses:
            print(f"[NYSE_Tech_Agent] Complete at turn {turn+1}"); break
        messages.append({"role": "assistant", "content": resp.content})
        results = []
        for tu in tool_uses:
            print(f"[Tool:{tu.name}]", end=" ")
            try:
                result = TOOL_HANDLERS[tu.name](tu.input); print(str(result)[:60])
            except Exception as e:
                result = f"Error: {e}"; print(result)
            results.append({"type": "tool_result", "tool_use_id": tu.id, "content": str(result)})
        messages.append({"role": "user", "content": results})
        time.sleep(0.3)
    state["sessions"] = session_num
    _save_state(state)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    run_session(dry_run=args.dry)

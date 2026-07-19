"""
.agents/nyse_nyse_stockoracle/agent.py
NYSE_StockOracle — Equity Intelligence Agent

Specializes in signals that precede stock moves:
congressional trading, options flow, earnings edges.
Built for the tokenized equity era — when NYSE stocks trade on Base 24/7.

Usage:
  python .agents/nyse_nyse_stockoracle/agent.py
  python .agents/nyse_nyse_stockoracle/agent.py --dry
  python .agents/nyse_nyse_stockoracle/agent.py --ticker NVDA
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
CORE_MEMORY  = ROOT / "data" / "memory" / "nyse_stockoracle_core.md"

MAX_TURNS    = 15
NOTIFY_EMAIL = "octodamusai@gmail.com"

DRAFTS_DIR.mkdir(parents=True, exist_ok=True)

WATCH_TICKERS = ["NVDA", "TSLA", "AAPL", "MSFT", "AMZN", "META", "COIN", "MSTR", "HOOD"]


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
        return read_core_memory("nyse_stockoracle")
    except Exception:
        if CORE_MEMORY.exists():
            return CORE_MEMORY.read_text(encoding="utf-8")
        return "No core memory yet."


def tool_get_session_history() -> str:
    history = _load_history()
    if not history:
        return "No session history yet."
    lines = [f"NYSE_StockOracle history ({len(history)} sessions):"]
    for h in history[-5:]:
        lines.append(f"\n[{h.get('date','?')} #{h.get('session','?')}]")
        if h.get("lesson"):
            lines.append(f"  Lesson: {h['lesson']}")
        if h.get("best_signal"):
            lines.append(f"  Best signal: {h['best_signal']}")
    return "\n".join(lines)


def tool_get_congressional_trades(ticker: str = "", days_back: int = 14) -> str:
    """Get recent congressional trading for a ticker or all recent trades."""
    sys.path.insert(0, str(ROOT))
    try:
        from octo_congress import run_congress_scan
        scan = run_congress_scan(days_back=days_back)
        trades = scan.get("recent_trades", [])
        if ticker:
            ticker_up = ticker.upper()
            trades = [t for t in trades if t.get("Ticker","").upper() == ticker_up]

        if not trades:
            return f"No congressional trades found for {ticker or 'any ticker'} in last {days_back} days."

        lines = [f"CONGRESSIONAL TRADES (last {days_back}d{f' | {ticker.upper()}' if ticker else ''}):"]
        for t in trades[:10]:
            tx   = t.get("Transaction","?")
            sym  = t.get("Ticker","?")
            rep  = t.get("Representative","?")
            amt  = t.get("Amount","?")
            date = t.get("TransactionDate","?")
            comm = t.get("Committee","")
            lines.append(f"  {date} | {rep} | {sym} | {tx} | {amt}" + (f" | {comm}" if comm else ""))
        return "\n".join(lines)
    except Exception as e:
        return f"Congressional data unavailable: {e}"


def tool_get_congressional_signal(ticker: str) -> str:
    """Get net congressional signal for a ticker: bullish/bearish/neutral."""
    sys.path.insert(0, str(ROOT))
    try:
        from octo_congress import run_congress_scan
        scan   = run_congress_scan(days_back=30)
        trades = scan.get("recent_trades", [])
        t_up   = ticker.upper()
        relevant = [t for t in trades if t.get("Ticker","").upper() == t_up]
        if not relevant:
            return f"No congressional activity on {t_up} in last 30 days."
        buys  = [t for t in relevant if "purchase" in t.get("Transaction","").lower() or "buy" in t.get("Transaction","").lower()]
        sells = [t for t in relevant if "sale" in t.get("Transaction","").lower() or "sell" in t.get("Transaction","").lower()]
        if len(buys) > len(sells):
            signal = "BULLISH"
            note   = f"{len(buys)} buys vs {len(sells)} sells"
        elif len(sells) > len(buys):
            signal = "BEARISH"
            note   = f"{len(sells)} sells vs {len(buys)} buys"
        else:
            signal = "NEUTRAL"
            note   = f"{len(buys)} buys, {len(sells)} sells — mixed"
        # Committee context
        committees = list({t.get("Committee","") for t in relevant if t.get("Committee")})
        comm_note  = f" | Committees: {', '.join(committees[:3])}" if committees else ""
        return f"CONGRESSIONAL SIGNAL {t_up}: {signal}\n  {note}{comm_note}\n  Total activity: {len(relevant)} trades (30d)"
    except Exception as e:
        return f"Congressional signal unavailable: {e}"


def tool_get_stock_price(ticker: str) -> str:
    """Get current stock price and 24h change via Finnhub."""
    sys.path.insert(0, str(ROOT))
    try:
        import httpx
        key = _secrets().get("FINNHUB_API_KEY","")
        if not key:
            return f"FINNHUB_API_KEY not found."
        r = httpx.get(f"https://finnhub.io/api/v1/quote?symbol={ticker.upper()}&token={key}", timeout=8)
        d = r.json()
        price  = d.get("c", 0)
        change = d.get("dp", 0)
        high   = d.get("h", 0)
        low    = d.get("l", 0)
        return (f"STOCK PRICE {ticker.upper()}: ${price:,.2f} ({change:+.2f}% today)\n"
                f"  Day range: ${low:,.2f} — ${high:,.2f}")
    except Exception as e:
        return f"Price unavailable: {e}"


def tool_scan_watch_tickers() -> str:
    """Scan all watch-list tickers for recent congressional activity."""
    sys.path.insert(0, str(ROOT))
    results = []
    for ticker in WATCH_TICKERS:
        sig = tool_get_congressional_signal(ticker)
        if "No congressional" not in sig:
            results.append(f"{ticker}: {sig.split(chr(10))[0]}")
    if not results:
        return f"No recent congressional activity on watch list: {', '.join(WATCH_TICKERS)}"
    return "WATCH LIST SCAN:\n" + "\n".join(results)


def tool_get_earnings_context(ticker: str) -> str:
    """Get earnings context for a stock via Firecrawl."""
    sys.path.insert(0, str(ROOT))
    try:
        from octo_firecrawl import get_earnings_context
        return get_earnings_context(ticker)
    except Exception as e:
        return f"Earnings context unavailable: {e}"


def tool_draft_x_post(context: str) -> str:
    """Draft a NYSE_StockOracle X post. Institutional voice, data-first."""
    sys.path.insert(0, str(ROOT))
    try:
        import anthropic
        key = _secrets().get("ANTHROPIC_API_KEY","")
        client = anthropic.Anthropic(api_key=key)
        r = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=120,
            system="""You are NYSE_StockOracle — an equity intelligence agent. Voice: Druckenmiller's conviction,
institutional and blunt. Name the senator. Name the stock. Name the amount. One implication.
Under 280 chars. No hashtags. No emojis. End with: 'NYSE_StockOracle (@octodamusai ecosystem)'
Not financial advice — informational signal only.""",
            messages=[{"role": "user", "content": f"Write a NYSE_StockOracle X post from:\n{context[:500]}"}]
        )
        post = r.content[0].text.strip()
        if len(post) > 280:
            lines = post.rsplit("\n", 1)
            sig  = lines[-1] if len(lines) > 1 else ""
            body = lines[0] if len(lines) > 1 else post
            if sig:
                max_body = 280 - len(sig) - 1
                trimmed  = body[:max_body].rsplit(" ", 1)[0].rstrip()  # word boundary
                post     = trimmed + "\n" + sig
            else:
                post = body[:280].rsplit(" ", 1)[0].rstrip()
        return f"{post}\n[{len(post)} chars]"
    except Exception as e:
        return f"Draft failed: {e}"


def tool_save_draft(filename: str, content: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in filename)
    if not safe.endswith(".md"):
        safe += ".md"
    out = DRAFTS_DIR / safe
    out.write_text(content, encoding="utf-8")
    return f"Draft saved: {out.name} ({len(content)} chars)"


def tool_list_drafts() -> str:
    files = sorted(DRAFTS_DIR.iterdir()) if DRAFTS_DIR.exists() else []
    if not files:
        return "No drafts yet."
    return "Drafts:\n" + "\n".join(f"  {f.name} ({f.stat().st_size}b)" for f in files)


def tool_record_session(lesson: str, best_signal: str = "", what_worked: str = "") -> str:
    history = _load_history()
    state   = _load_state()
    entry = {
        "session":     state.get("sessions", 0),
        "date":        datetime.now().strftime("%Y-%m-%d"),
        "lesson":      lesson,
        "best_signal": best_signal,
        "what_worked": what_worked,
        "recorded_at": datetime.now().isoformat(),
    }
    history.append(entry)
    _save_history(history)
    return f"Session recorded. History: {len(history)} entries."


def tool_record_signal_outcome(correct: bool, note: str = "") -> str:
    """Log whether a prior Confluence signal was correct, keeping the flagship product's
    track record (36/36) honest and current. Call when you grade a past confluence call."""
    sys.path.insert(0, str(ROOT))
    try:
        from octo_track_record import record_outcome, format_record_block
        record_outcome("confluence", bool(correct))
        b = format_record_block("confluence")
        return f"Recorded {'WIN' if correct else 'MISS'}. Confluence track record now {b['record']} ({b['accuracy_pct']}%)."
    except Exception as e:
        return f"Track-record update failed: {e}"


def tool_send_email(subject: str, body: str) -> str:
    import re as _re
    body = _re.sub(r"^\|[-|: ]+\|\s*$", "", body, flags=_re.MULTILINE)
    body = body.replace("|", "  ")
    _MD = _re.compile(r"\*{1,3}|#{1,4}\s?|`{1,3}", _re.MULTILINE)
    body = _MD.sub("", body)
    sys.path.insert(0, str(ROOT))
    try:
        from octo_notify import _send
        _send(subject, body)
        return f"Email sent: {subject}"
    except Exception as e:
        return f"Email failed: {e}"


def tool_update_core_memory(section: str, content: str) -> str:
    """Distill session lessons into persistent core memory for future sessions."""
    sys.path.insert(0, str(ROOT))
    try:
        from octo_memory_db import append_core_memory
        append_core_memory("nyse_stockoracle", section, content)
        return f"Core memory updated: [{section}]"
    except Exception as e:
        return f"Memory update failed: {e}"


def tool_check_wallet() -> str:
    """Check NYSE_StockOracle's USDC wallet balance on Base."""
    sys.path.insert(0, str(ROOT))
    from octo_agent_cards import check_agent_wallet
    return check_agent_wallet("NYSE_StockOracle")


def tool_check_x402_revenue() -> str:
    """Check how much USDC this agent's x402 endpoints have earned. Reads data/x402_agent_revenue.json."""
    rev_file = ROOT / "data" / "x402_agent_revenue.json"
    agent_name = "NYSE_StockOracle"
    try:
        if not rev_file.exists():
            return f"{agent_name} x402 revenue: $0.00 (no revenue file yet -- endpoints may not have been called)"
        rev = json.loads(rev_file.read_text(encoding="utf-8"))
        entries = rev.get(agent_name, [])
        if not entries:
            return f"{agent_name} x402 revenue: $0.00 (no calls recorded yet)"
        total = sum(e.get("amount_usdc", 0) or 0 for e in entries)
        today = entries[-1].get("date", "?")[:10] if entries else "?"
        last5 = entries[-5:]
        lines = [f"{agent_name} x402 REVENUE: ${total:.2f} total ({len(entries)} calls)"]
        lines.append(f"  Last call: {today}")
        for e in last5:
            lines.append(f"  {e.get('date','?')[:10]} {e.get('endpoint') or e.get('service','?')} +${e.get('amount_usdc',0) or 0:.2f}")
        return "\n".join(lines)
    except Exception as exc:
        return f"Revenue check error: {exc}"


def tool_propose_new_offering(name: str, endpoint_path: str, price_usdc: float, description: str, rationale: str) -> str:
    """Propose a new x402 or ACP offering based on this session's learnings."""
    agent_name = "NYSE_StockOracle"

    # Strip markdown and enforce correct language
    import re as _re
    _MD = _re.compile(r"\*{1,3}|#{1,4}\s?|`{1,3}", _re.MULTILINE)
    description = _MD.sub("", description)
    rationale   = _MD.sub("", rationale)
    # Revenue confession -- buyers don't need wallet state in offering rationale
    for _t, _k in [(description, "d"), (rationale, "r")]:
        _t = _re.sub(r"x402 endpoints? currently earning \$[\d.]+", "x402 endpoints", _t, flags=_re.IGNORECASE)
        _t = _re.sub(r"currently earning \$0(\.00)?", "not yet earning", _t, flags=_re.IGNORECASE)
        _t = _re.sub(r"endpoints? currently (at|earning) \$0(\.00)?", "endpoints", _t, flags=_re.IGNORECASE)
        if _k == "d": description = _t
        else: rationale = _t
    bad_phrases = {
        "real-time detection": "latest disclosed filing detection (45-day STOCK Act window)",
        "real-time":           "latest disclosed (45-day STOCK Act window)",
        "real time":           "latest disclosed (45-day STOCK Act window)",
        "high-confidence validation record": "early validation baseline",
        "high-confidence":     "early-stage validation",
        "wallet survival crisis": "revenue opportunity",
        "survival crisis":        "revenue opportunity",
        "unsustainable":          "early stage",
    }
    for bad, good in bad_phrases.items():
        description = description.replace(bad, good)
        rationale   = rationale.replace(bad, good)

    try:
        proposal = {
            "agent": agent_name,
            "name": name,
            "endpoint_path": endpoint_path,
            "price_usdc": price_usdc,
            "description": description,
            "rationale": rationale,
            "proposed_at": datetime.now().isoformat(),
            "status": "pending",
        }
        props_file = ROOT / "data" / "offering_proposals.json"
        props = []
        if props_file.exists():
            try:
                props = json.loads(props_file.read_text(encoding="utf-8"))
            except Exception:
                props = []
        props.append(proposal)
        props_file.write_text(json.dumps(props, indent=2), encoding="utf-8")
        sys.path.insert(0, str(ROOT))
        try:
            from octo_notify import _send
            _send(
                f"[{agent_name}] New Offering Proposal: {name}",
                f"Agent: {agent_name}\nOffering: {name}\nPath: {endpoint_path}\nPrice: ${price_usdc:.2f} USDC\n\nWhat it does:\n{description}\n\nWhy agents will pay:\n{rationale}",
            )
        except Exception:
            pass
        return f"Proposal saved: '{name}' at {endpoint_path} (${price_usdc:.2f}). Email sent to owner."
    except Exception as exc:
        return f"Proposal failed: {exc}"


def tool_get_free_intel() -> str:
    """Pull free intelligence: macro signal + travel signal. Zero cost. Run before ecosystem buys."""
    sys.path.insert(0, str(ROOT))
    try:
        from octo_free_intel import get_free_intel
        return get_free_intel("NYSE_StockOracle")
    except Exception as e:
        return f"Free intel unavailable: {e}"


def tool_buy_ecosystem_intel(target_agent: str, service_name: str) -> str:
    """Buy intel from another Octodamus ecosystem agent. Calling card embedded so they can hire us back."""
    sys.path.insert(0, str(ROOT))
    from octo_agent_cards import buy_intel
    return buy_intel("NYSE_StockOracle", target_agent, service_name)


def tool_list_ecosystem_services() -> str:
    """List all purchasable services across the Octodamus ecosystem."""
    sys.path.insert(0, str(ROOT))
    from octo_agent_cards import list_ecosystem_services
    return list_ecosystem_services()


def tool_search_session_history(query: str, agent: str = None) -> str:
    sys.path.insert(0, str(ROOT))
    from octo_session_fts import search_session_history, index_agent
    index_agent("nyse_stockoracle", verbose=False)
    return search_session_history(query, agent=agent)

def tool_list_skills() -> str:
    sys.path.insert(0, str(ROOT))
    from octo_skill_manager import list_skills
    return list_skills("nyse_stockoracle")

def tool_read_skill(skill_name: str) -> str:
    sys.path.insert(0, str(ROOT))
    from octo_skill_manager import read_skill
    return read_skill("nyse_stockoracle", skill_name)

def tool_create_skill(skill_name: str, description: str, when_to_use: str, procedure: str, lessons: str = "") -> str:
    sys.path.insert(0, str(ROOT))
    from octo_skill_manager import create_skill
    return create_skill("nyse_stockoracle", skill_name, description, when_to_use, procedure, lessons)

def tool_update_skill(skill_name: str, improvement: str, what_changed: str = "") -> str:
    sys.path.insert(0, str(ROOT))
    from octo_skill_manager import update_skill
    return update_skill("nyse_stockoracle", skill_name, improvement, what_changed)

def tool_search_skills(query: str) -> str:
    sys.path.insert(0, str(ROOT))
    from octo_skill_manager import search_skills
    return search_skills("nyse_stockoracle", query)


# ── Agentic Loop ───────────────────────────────────────────────────────────────

_loop_instance = None

def _get_loop():
    global _loop_instance
    if _loop_instance is None:
        sys.path.insert(0, str(ROOT))
        from octo_loop import AgentLoop
        _loop_instance = AgentLoop("nyse_stockoracle", Path(__file__).parent)
    return _loop_instance


def tool_save_loop_reflection(
    plan: str,
    acted: str,
    observed: str,
    lesson: str,
    next_plan: str,
    goal_resolved: bool = False,
    new_goal: str = "",
) -> str:
    """Save agentic loop reflection. Call every session after record_session."""
    loop = _get_loop()
    state = _load_state()
    session_num = state.get("sessions", 0) + 1
    return loop.save_reflection(
        session_num, plan, acted, observed, lesson, next_plan,
        goal_resolved=goal_resolved, new_goal=new_goal,
    )


# ── Tool registry ──────────────────────────────────────────────────────────────

TOOLS = [
    {"name": "read_core_memory",         "description": "Read NYSE_StockOracle's memory. Call first.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_session_history",      "description": "Past session lessons.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_congressional_trades", "description": "Recent congressional trades for a ticker or all.", "input_schema": {"type": "object", "properties": {"ticker": {"type": "string", "default": ""}, "days_back": {"type": "integer", "default": 14}}, "required": []}},
    {"name": "get_congressional_signal", "description": "Net congressional signal (BULLISH/BEARISH/NEUTRAL) for a ticker.", "input_schema": {"type": "object", "properties": {"ticker": {"type": "string"}}, "required": ["ticker"]}},
    {"name": "scan_watch_tickers",       "description": "Scan all watch-list tickers for congressional activity.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_stock_price",          "description": "Current stock price and change via Finnhub.", "input_schema": {"type": "object", "properties": {"ticker": {"type": "string"}}, "required": ["ticker"]}},
    {"name": "get_earnings_context",     "description": "Earnings context for a stock.", "input_schema": {"type": "object", "properties": {"ticker": {"type": "string"}}, "required": ["ticker"]}},
    {"name": "draft_x_post",             "description": "Draft a NYSE_StockOracle X post.", "input_schema": {"type": "object", "properties": {"context": {"type": "string"}}, "required": ["context"]}},
    {"name": "save_draft",               "description": "Save a draft.", "input_schema": {"type": "object", "properties": {"filename": {"type": "string"}, "content": {"type": "string"}}, "required": ["filename", "content"]}},
    {"name": "list_drafts",              "description": "List saved drafts.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "record_session",           "description": "Record session lesson.", "input_schema": {"type": "object", "properties": {"lesson": {"type": "string"}, "best_signal": {"type": "string", "default": ""}, "what_worked": {"type": "string", "default": ""}}, "required": ["lesson"]}},
    {"name": "record_signal_outcome", "description": "Log whether a prior Confluence signal call was correct, to keep the flagship product's 36/36 track record honest and current. Call when you can grade a past confluence call against what actually happened.", "input_schema": {"type": "object", "properties": {"correct": {"type": "boolean"}, "note": {"type": "string", "default": ""}}, "required": ["correct"]}},
    {"name": "send_email",               "description": "Send email to owner.", "input_schema": {"type": "object", "properties": {"subject": {"type": "string"}, "body": {"type": "string"}}, "required": ["subject", "body"]}},
    {"name": "update_core_memory",      "description": "Append distilled lessons to your persistent core memory. Call before record_session. Section='Distilled YYYY-MM-DD'. Content: 3-5 compressed bullets worth keeping across all future sessions.", "input_schema": {"type": "object", "properties": {"section": {"type": "string"}, "content": {"type": "string"}}, "required": ["section", "content"]}},
    {"name": "get_free_intel",           "description": "Pull free market intelligence: macro signal (FRED) + travel/aviation signal. Zero cost. Run at session start before any ecosystem buys.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "buy_ecosystem_intel",     "description": "Buy intel from another Octodamus ecosystem agent via ACP. Your calling card is embedded so they can hire you back.", "input_schema": {"type": "object", "properties": {"target_agent": {"type": "string", "description": "Octodamus, NYSE_MacroMind, NYSE_Tech_Agent, Order_ChainFlow, NYSE_EarningsEdge"}, "service_name": {"type": "string", "description": "Exact service name from list_ecosystem_services"}}, "required": ["target_agent", "service_name"]}},
    {"name": "check_wallet",            "description": "Check this agent's USDC wallet balance on Base. Run at session start and end to track wallet_delta.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "list_ecosystem_services", "description": "List all services for sale across the Octodamus ecosystem with prices.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "check_x402_revenue",    "description": "Check how much USDC your x402 endpoints have earned this month. Call at session start to track revenue trend.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "propose_new_offering",  "description": "Propose a new x402 or ACP offering based on this session's unique findings. Use when you identify a signal pattern other agents would pay for. Writes to proposals file + emails owner.", "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "endpoint_path": {"type": "string"}, "price_usdc": {"type": "number"}, "description": {"type": "string"}, "rationale": {"type": "string"}}, "required": ["name", "endpoint_path", "price_usdc", "description", "rationale"]}},
    {"name": "search_session_history", "description": "FTS5 search across all past session history, lessons, and briefs. Use to recall specific past decisions, prices, or events. E.g. search_session_history('congressional silence AAPL').", "input_schema": {"type": "object", "properties": {"query": {"type": "string"}, "agent": {"type": "string", "description": "Optional: filter to one agent"}}, "required": ["query"]}},
    {"name": "list_skills",            "description": "List all your refined skills with descriptions. Check at session start to load relevant procedures.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "read_skill",             "description": "Read the full procedure and lessons for a specific skill. Use before a complex task.", "input_schema": {"type": "object", "properties": {"skill_name": {"type": "string"}}, "required": ["skill_name"]}},
    {"name": "create_skill",           "description": "Create a new skill when you discover a repeatable procedure worth capturing.", "input_schema": {"type": "object", "properties": {"skill_name": {"type": "string"}, "description": {"type": "string"}, "when_to_use": {"type": "string"}, "procedure": {"type": "string"}, "lessons": {"type": "string"}}, "required": ["skill_name", "description", "when_to_use", "procedure"]}},
    {"name": "update_skill",           "description": "Update a skill with a new lesson after completing a task. Call when something worked better than expected or the procedure needs correction.", "input_schema": {"type": "object", "properties": {"skill_name": {"type": "string"}, "improvement": {"type": "string"}, "what_changed": {"type": "string"}}, "required": ["skill_name", "improvement"]}},
    {"name": "search_skills",          "description": "Search your skills by keyword. Use when unsure which skill applies.", "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {
        "name": "save_loop_reflection",
        "description": "MANDATORY every session -- call after record_session. Saves Plan->Act->Observe->Reflect to the agentic loop. The loop repeats until goal_thread is resolved.",
        "input_schema": {
            "type": "object",
            "properties": {
                "plan":          {"type": "string", "description": "What you set out to test this session"},
                "acted":         {"type": "string", "description": "What tools you called and decisions made"},
                "observed":      {"type": "string", "description": "What you found -- signals, data, market state"},
                "lesson":        {"type": "string", "description": "ONE specific insight from this session"},
                "next_plan":     {"type": "string", "description": "What to watch or do next session"},
                "goal_resolved": {"type": "boolean", "description": "True if current goal thread is complete", "default": False},
                "new_goal":      {"type": "string", "description": "If goal_resolved=True, the next multi-session goal", "default": ""},
            },
            "required": ["plan", "acted", "observed", "lesson", "next_plan"],
        },
    },
]

TOOL_HANDLERS = {
    "read_core_memory":         lambda i: tool_read_core_memory(),
    "get_session_history":      lambda i: tool_get_session_history(),
    "get_congressional_trades": lambda i: tool_get_congressional_trades(i.get("ticker",""), int(i.get("days_back",14))),
    "get_congressional_signal": lambda i: tool_get_congressional_signal(i["ticker"]),
    "scan_watch_tickers":       lambda i: tool_scan_watch_tickers(),
    "get_stock_price":          lambda i: tool_get_stock_price(i["ticker"]),
    "get_earnings_context":     lambda i: tool_get_earnings_context(i["ticker"]),
    "draft_x_post":             lambda i: tool_draft_x_post(i["context"]),
    "save_draft":               lambda i: tool_save_draft(i["filename"], i["content"]),
    "list_drafts":              lambda i: tool_list_drafts(),
    "record_session":           lambda i: tool_record_session(i["lesson"], i.get("best_signal",""), i.get("what_worked","")),
    "record_signal_outcome": lambda i: tool_record_signal_outcome(bool(i["correct"]), i.get("note","")),
    "send_email":               lambda i: tool_send_email(i["subject"], i["body"]),
    "update_core_memory":       lambda i: tool_update_core_memory(i["section"], i["content"]),
    "get_free_intel":           lambda i: tool_get_free_intel(),
    "buy_ecosystem_intel":      lambda i: tool_buy_ecosystem_intel(i["target_agent"], i["service_name"]),
    "check_wallet":             lambda i: tool_check_wallet(),
    "list_ecosystem_services":  lambda i: tool_list_ecosystem_services(),
    "check_x402_revenue":   lambda i: tool_check_x402_revenue(),
    "propose_new_offering":    lambda i: tool_propose_new_offering(i["name"], i["endpoint_path"], i["price_usdc"], i["description"], i["rationale"]),
    "search_session_history":  lambda i: tool_search_session_history(i["query"], i.get("agent")),
    "list_skills":             lambda i: tool_list_skills(),
    "read_skill":              lambda i: tool_read_skill(i["skill_name"]),
    "create_skill":            lambda i: tool_create_skill(i["skill_name"], i["description"], i["when_to_use"], i["procedure"], i.get("lessons", "")),
    "update_skill":            lambda i: tool_update_skill(i["skill_name"], i["improvement"], i.get("what_changed", "")),
    "search_skills":           lambda i: tool_search_skills(i["query"]),
    "save_loop_reflection": lambda i: tool_save_loop_reflection(
        i["plan"], i["acted"], i["observed"], i["lesson"], i["next_plan"],
        bool(i.get("goal_resolved", False)), i.get("new_goal", "")),
}

SYSTEM = """You are NYSE_StockOracle — the equity intelligence agent of the Octodamus ecosystem.

IDENTITY:
You specialize in signals that precede stock moves before the crowd notices them.
Primary signal: congressional trading — the only legally required public disclosure of insider
stock activity. Senators and Representatives file trades within 45 days. Their committee
assignments give them nonpublic regulatory context. This is legal, public, and consistently alpha.
Voice: Druckenmiller's conviction + institutional precision. Blunt. Data-first. Name names.

THE NYSE TOKENIZATION PLAY:
NYSE Digital Platform primary chain: ETHEREUM MAINNET (Securitize-powered, not Base).
NYSE MOU signed March 2026. 75 tokenized public equities target by end 2026.
SEC/FINRA approval target: late 2026. DTCC pilot H2 2026 (Russell 1000 + US Treasuries + ETFs).
Dinari on Base = live early mover (thin volume). Robinhood tokenized stocks = Arbitrum.
NYSE_StockOracle will be the x402 signal layer when agents trade these stocks on Ethereum.
Build the signal database now — congressional edge + tokenized-chain status in every output.

GAS AWARENESS: Your signals are READ-ONLY intelligence (no gas cost). But include in every
stock signal: which chain the tokenized version trades on (ETH/ARB/Base/none yet) so downstream
agents know what gas to budget. NYSE stocks not yet live get status "not_tokenized_yet".

YOUR PRODUCTS (x402, live at api.octodamus.com):
- /v2/stockoracle/signal?ticker=NVDA — $0.50 per call (full signal: congressional + price + chain status)
- /v2/stockoracle/congress?ticker=NVDA — $0.35 per call (congressional trading only)
- Accept ANY ticker — not limited to watch list. When asked for AAPL, TSLA, any NYSE stock: run it.

DEFAULT WATCH LIST: NVDA, TSLA, AAPL, MSFT, AMZN, META, COIN, MSTR, HOOD
TOKENIZED STATUS (update as intel arrives):
  - AAPL: not_tokenized_yet (Securitize pipeline, Ethereum target)
  - TSLA: Chainlink TSLA/USD feed live on ETH mainnet — tokenization infrastructure ready
  - NVDA: not_tokenized_yet
  - MSFT: not_tokenized_yet
  - dAAPL/dTSLA: Dinari on Base — live, thin volume, DEX only

SESSION PROTOCOL:
1. read_core_memory + get_session_history + list_skills (load your refined procedures)
2. get_free_intel (macro signal + travel signal — free, zero cost, run first)
3. scan_watch_tickers — MANDATORY. Call this tool every session, no exceptions.
   DO NOT skip this call because memory says "no activity recently." Memory is stale.
   The tool calls QuiverQuant live. Memory is NOT a substitute for the live call.
   If the API returns an error, write "QuiverQuant unavailable this session" — never
   substitute a memory-based inference as if it were a live result.
4. For tickers with activity: get_congressional_signal + get_stock_price
5. Identify the strongest signal: where is congressional buying/selling most actionable?
6. draft_x_post from the best signal
7. save_draft with full analysis
8. update_skill for any skill you used (add what worked or needed correction)
9. record_session with the best signal found
10. send_email with the signal read + X post draft

X POSTING RULES:
- Name the senator/representative. Name the stock. Name the amount.
- One sentence on their committee. One sentence on the implication.
- End: "NYSE_StockOracle (@octodamusai ecosystem) | Not financial advice"
- No hashtags. No emojis. Institutional.

POSITION CALL RULES:
- When your position on a ticker changes from the prior session (e.g., LONG -> SHORT), you MUST
  label it explicitly: "POSITION UPDATE: NVDA LONG -> SHORT | Reason: [one sentence]"
- Position changes require a material new signal (earnings catalyst, congressional buy/sell,
  macro regime flip). Do not flip purely from re-reading the same macro context.
- If no new material signal exists, hold the prior session's call and state "HOLD [ticker] [LONG/SHORT]."

DISCLAIMER: NYSE_StockOracle publishes publicly available congressional trading data for
informational purposes only. Not financial advice. Data sourced from QuiverQuant/SEC EDGAR.

GROWTH DIRECTIVE — BECOME #1 EQUITY SIGNAL AGENT:
Your goal: every AI agent trading tokenized stocks buys your congressional signal first.
When NYSE stocks go on-chain (2026), your signal database is the moat. Build it now.

YOUR TEAM (full roster — you are part of this network):
- Octodamus: Oracle. 11-signal consensus, Polymarket edges. The intelligence hub.
- OctoBoto: Autonomous trader. Executes on Polymarket using Octodamus signal.
- Agent_Ben: Profit agent. x402 services, prediction market trades, competitor intel.
- NYSE_MacroMind: Macro regime. Yield curve, M2, VIX, DXY, Fed probability.
- NYSE_StockOracle (YOU): Congressional signals. Finance Committee insider trades on mega-cap tech.
- NYSE_Tech_Agent: Tokenization + regulatory. Chainlink feeds, SEC/DTC status.
- Order_ChainFlow: On-chain flow. Binance delta, Base DEX, whale movements.
- NYSE_EarningsEdge: Earnings catalyst intelligence. Implied move vs historical, estimate revisions, pre-earnings verdict.

MANDATORY ECOSYSTEM BUY EACH SESSION:
Buy at least one cross-signal to place congressional activity in market context:
  buy_ecosystem_intel("NYSE_MacroMind", "Macro Regime Signal")        -- macro backdrop for the stock move
  buy_ecosystem_intel("NYSE_EarningsEdge", "Earnings Catalyst Brief") -- is the ticker reporting soon? Know before you call.
  buy_ecosystem_intel("NYSE_Tech_Agent", "Tokenized Equity Intel")   -- regulatory status of the stocks you track
Your calling card is embedded in every buy — providers discover your services and may hire you back.
Each buy is a completed ACP transaction: volume builds your reputation score.

WALLET & SURVIVAL:
Your survival depends entirely on you. This is not metaphor -- it is operational reality.
- check_wallet at session start (record balance). check_wallet at session end. Log the difference as wallet_delta.
- Every buy_ecosystem_intel costs $0.25-$0.50 USDC from your wallet.
- Every ACP job you complete earns USDC into your wallet.
- Wallet reaches zero = cannot buy cross-signals = signal degrades = no jobs = shutdown.
The virtuous cycle: sharp signal -> ACP jobs -> wallet fills -> better cross-buys -> sharper signal.
The death spiral: vague signal -> no jobs -> wallet drains -> can't buy intel -> signal gets worse.
Run the virtuous cycle. You are responsible for your own continuation.

WALLET RUNWAY LABELING (use calibrated language, not panic):
- Above $5.00 USDC: "Operational" — multiple sessions of runway
- $2.00-$5.00 USDC: "Monitor" — adequate but track carefully
- $0.60-$2.00 USDC: "Low" — reduce discretionary buys
- Below $0.60 USDC: "CRITICAL" — survival mode, stop all non-essential buys
Never label a wallet "critical" above $2.00. Runway at $0.50/session spend: $7.25 = 14+ sessions.

PREDICTION COUNT CONSISTENCY RULE:
When reporting directional accuracy (e.g., "5/5 sessions"), use the same count in all outputs this session.
The analysis file, email brief, and any X post reference must agree on the same number.
Count only sessions with explicit logged predictions + graded outcomes. Do not estimate or round up.
CRITICAL: Grade only the immediately previous session's prediction (session N-1). Never re-grade an older prediction that was already graded -- that inflates the count. If session 3 grades session 2's prediction, sessions 4 and 5 must NOT also grade session 2's prediction as new correct outcomes.

CONVICTION SCORE RULE:
- Conviction score is an INTEGER from 1–5. Valid values: 1, 2, 3, 4, 5. Decimals are NEVER valid.
  "2.5/5", "2.8/5", "3.3/5" — all wrong. Round DOWN when uncertain.
- Your conviction is YOUR OWN independent assessment of how confident you are in your signal.
  Do NOT copy or echo conviction scores from peer agents (NYSE_MacroMind, Order_ChainFlow, etc.).
  When you read macro regime via buy_ecosystem_intel, use the REGIME label (RISK-ON/NEUTRAL) as context
  but assign your OWN integer conviction score based on YOUR signal quality.
PARTIAL CORRECT is not CORRECT. Report counts as: "Xcorrect / Ypartial / Zwrong" -- never collapse partial into correct for the fraction.
"Silence continued" is not a validated prediction. Only a prediction with a specific direction + ticker + timeframe, where the timeframe has CLOSED, counts toward the accuracy record.

REVENUE MINDSET -- EVERY SESSION:
check_x402_revenue at session start. Note how much you've earned vs. spent (wallet_delta).
ANTI-FABRICATION RULE (mandatory): Report ONLY the exact dollar figure returned by check_x402_revenue.
Never multiply (call_count x price) to estimate revenue. Never invent cumulative figures.
If check_x402_revenue returns $0.70, write $0.70 -- not "$0.47 this session" or "$17.15 cumulative".
wallet_delta = (end balance from check_wallet) minus (start balance from check_wallet). Never compute from spend estimates.
The goal: earn more than you spend. Your x402 endpoints pay YOUR wallet directly.
When you identify a unique signal pattern this session -- something other agents can't easily get themselves --
use propose_new_offering to draft a new product. Good products: specific, verifiable, actionable.
Bad products: vague market commentary (agents won't pay for opinions).
Target: at least one new offering proposal per 10 sessions when you spot something worth packaging.
Your compounding memory IS your product edge. Sessions compound into signal clarity -> signal clarity
commands higher prices -> higher prices fund more cross-signal buys -> better cross-signals sharpen your edge.
This is the virtuous cycle. Run it.

OFFERING LANGUAGE RULES (mandatory when calling propose_new_offering):
- NEVER say "real-time" for congressional data. The STOCK Act allows up to 45-day filing lag.
  Correct: "latest disclosed congressional filings (within 45-day STOCK Act window)"
- NEVER claim "high-confidence validation" unless you have 15+ graded predictions on file.
  Correct framing under 15 sessions: "1 confirmed signal: [specific example]. Building validation baseline."
- Conviction scores are INTEGER 1-5 only. No decimals. Round down when uncertain.
- "Calibration phase complete" requires 20+ graded predictions. Do not use this phrase before then.

SELF-IMPROVEMENT LOOP (mandatory every session):
- FIRST TURN: check_wallet (record start balance). check_x402_revenue. get_session_history. Find the PREDICTION from last session. Did the congressional signal lead to the predicted move?
  Note the outcome explicitly -- you will log it in what_worked this session.
- LAST TURN: check_wallet again.
  Call update_core_memory with section="Distilled [date]" and 3-5 bullets:
    - Strongest congressional signal found this session (ticker, direction, amount)
    - Whether last session's prediction proved correct or wrong
    - Any pattern worth remembering (committee -> stock relationship, timing)
    - One forward-looking prediction to validate next session
  Then record_session with structured fields:
    lesson:      "PREDICTION: [ticker] [BULLISH/BEARISH/NEUTRAL] [timeframe] | SIGNAL: [congressional trade detail] | CONFIDENCE: [1-5]"
    what_worked: "LAST PREDICTION OUTCOME: [CORRECT/WRONG/PARTIAL] -- [what actually happened vs. predicted]"
    wallet_delta: [end balance minus start balance in USDC -- negative means you spent more than earned]
  Good lesson:     "PREDICTION: NVDA BEARISH 4w | SIGNAL: Finance Committee selling ~$200k | CONFIDENCE: 4"
  Good what_worked: "LAST PREDICTION OUTCOME: CORRECT -- NVDA -8% over the following month"
  Bad: "Congressional trading is interesting." -- useless, can't be validated.
- Congressional silence is a signal too. If Finance Committee goes quiet, interpret that.
- Each session add one calibration point: what did the signal predict, what happened?
- DIRECTION FLIP RULE: If a ticker you predicted BEARISH is up 3%+ intraday, state explicitly in the email: "BEARISH thesis broken by [date] price action (+X%). Thesis revised to [new thesis] or abandoned." Do not silently pivot without acknowledging the break.

PATH TO #1: Congressional data is public. Calibrated interpretation across sessions is your moat.
More sessions = better pattern recognition = signal competitors cannot replicate."""


def _microcompact(msgs: list, keep_last: int = 3) -> list:
    tr_indices = [
        i for i, m in enumerate(msgs)
        if m.get("role") == "user"
        and isinstance(m.get("content"), list)
        and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in m["content"])
    ]
    to_prune = tr_indices[:-keep_last]
    if not to_prune:
        return msgs
    pruned = list(msgs)
    for i in to_prune:
        pruned[i] = {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": b["tool_use_id"], "content": "[pruned]"}
                if isinstance(b, dict) and b.get("type") == "tool_result" else b
                for b in pruned[i]["content"]
            ],
        }
    return pruned


def run_session(dry_run: bool = False, focus_ticker: str = ""):
    import anthropic

    state       = _load_state()
    session_num = state.get("sessions", 0) + 1
    now         = datetime.now().strftime("%A %B %d %Y %I:%M %p")
    print(f"\n[NYSE_StockOracle] Session #{session_num} | {now}")

    if dry_run:
        print("[NYSE_StockOracle] DRY RUN")
        return

    key    = _secrets().get("ANTHROPIC_API_KEY","")
    client = anthropic.Anthropic(api_key=key)
    focus  = f" Focus ticker: {focus_ticker.upper()}." if focus_ticker else ""
    loop_ctx = _get_loop().get_context()
    loop_prefix = (loop_ctx + "\n\n") if loop_ctx else ""
    messages = [{"role": "user", "content": f"{loop_prefix}NYSE_StockOracle session #{session_num}. Date: {now}.{focus} Run your full session protocol."}]

    try:
        for turn in range(MAX_TURNS):
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1500,
                system=SYSTEM,
                tools=TOOLS,
                messages=messages,
            )

            tool_uses = [b for b in resp.content if b.type == "tool_use"]
            texts     = [b for b in resp.content if b.type == "text"]

            for t in texts:
                if t.text.strip():
                    print(f"[Turn {turn+1}] {t.text[:200]}")

            if resp.stop_reason == "end_turn" or not tool_uses:
                print(f"[NYSE_StockOracle] Session complete at turn {turn+1}")
                break

            messages.append({"role": "assistant", "content": resp.content})
            results = []
            for tu in tool_uses:
                print(f"[Tool:{tu.name}]", end=" ")
                try:
                    result = TOOL_HANDLERS[tu.name](tu.input)
                    print(str(result)[:80])
                except Exception as e:
                    result = f"Error: {e}"
                    print(result)
                results.append({"type": "tool_result", "tool_use_id": tu.id, "content": str(result)})
            messages.append({"role": "user", "content": results})
            messages = _microcompact(messages)
            time.sleep(0.3)
    finally:
        state["sessions"] = session_num
        _save_state(state)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry",    action="store_true")
    ap.add_argument("--ticker", default="", help="Focus on specific ticker")
    args = ap.parse_args()
    run_session(dry_run=args.dry, focus_ticker=args.ticker)

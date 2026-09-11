"""
.agents/nyse_earningsedge/agent.py
NYSE_EarningsEdge — Earnings Catalyst Intelligence Agent

Tracks upcoming earnings events for mega-cap tech + crypto-adjacent stocks.
Signals: implied move vs historical move, estimate revision direction,
pre-earnings positioning verdict (HIGH RISK / ELEVATED / NEUTRAL).

Usage:
  python .agents/nyse_earningsedge/agent.py
  python .agents/nyse_earningsedge/agent.py --dry
  python .agents/nyse_earningsedge/agent.py --ticker NVDA
"""

import argparse
import json
import sys
import time
from datetime import datetime, date, timedelta
from pathlib import Path

ROOT         = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))
import octo_llm  # usage meter + prompt-caching helpers
SECRETS_FILE = ROOT / ".octo_secrets"
STATE_FILE   = Path(__file__).parent / "data" / "state.json"
DRAFTS_DIR   = Path(__file__).parent / "data" / "drafts"
HISTORY_FILE = Path(__file__).parent / "data" / "history.json"
CORE_MEMORY  = ROOT / "data" / "memory" / "nyse_earningsedge_core.md"

MAX_TURNS    = 15
NOTIFY_EMAIL = "octodamusai@gmail.com"

DRAFTS_DIR.mkdir(parents=True, exist_ok=True)

WATCH_TICKERS = ["NVDA", "TSLA", "AAPL", "MSFT", "GOOGL", "META",
                 "AMZN", "COIN", "HOOD", "MSTR", "AMD", "INTC", "SMCI"]


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
        return read_core_memory("nyse_earningsedge")
    except Exception:
        return CORE_MEMORY.read_text(encoding="utf-8") if CORE_MEMORY.exists() else "No memory."


def tool_get_session_history() -> str:
    history = _load_history()
    if not history:
        return "No session history yet."
    lines = [f"NYSE_EarningsEdge history ({len(history)} sessions):"]
    for h in history[-5:]:
        date_str    = h.get("date", "?")
        lesson      = h.get("lesson", "")
        what_worked = h.get("what_worked", "")
        wallet_d    = h.get("wallet_delta", None)
        delta_str   = f"  wallet_delta=${wallet_d:+.2f}" if wallet_d is not None else ""
        lines.append(f"  [{date_str}] {lesson}")
        if what_worked:
            lines.append(f"             OUTCOME: {what_worked}")
        if delta_str:
            lines.append(f"            {delta_str}")
    return "\n".join(lines)


def tool_get_earnings_calendar(days_ahead: int = 7) -> str:
    """Fetch upcoming earnings for watched tickers via Finnhub."""
    sys.path.insert(0, str(ROOT))
    sec = _secrets()
    fk  = sec.get("FINNHUB_API_KEY", "")
    if not fk:
        return "FINNHUB_API_KEY not set — earnings calendar unavailable."
    try:
        import httpx
        today_str = date.today().isoformat()
        end_str   = (date.today() + timedelta(days=days_ahead)).isoformat()
        r = httpx.get(
            "https://finnhub.io/api/v1/calendar/earnings",
            params={"from": today_str, "to": end_str, "token": fk},
            timeout=8,
        )
        if r.status_code != 200:
            return f"Finnhub error {r.status_code}"
        all_cal   = r.json().get("earningsCalendar", [])
        watch_set = set(WATCH_TICKERS)
        relevant  = [e for e in all_cal if e.get("symbol") in watch_set]
        if not relevant:
            return (f"No watched tickers reporting in next {days_ahead} days. "
                    f"Total S&P 500 reporters this week: {len(all_cal)}. "
                    f"Low-catalyst window — focus on macro/positioning risk.")
        lines = [f"EARNINGS CALENDAR (next {days_ahead} days — watched tickers only):"]
        for e in sorted(relevant, key=lambda x: x.get("date", "")):
            sym    = e.get("symbol", "?")
            rep_dt = e.get("date", "?")
            timing = e.get("hour", "?")
            est    = e.get("epsEstimate")
            prev   = e.get("epsActual")
            timing_str = {"amc": "after close", "bmo": "before open", "dmh": "during market"}.get(timing, timing)
            lines.append(f"  {sym}: {rep_dt} ({timing_str})"
                         + (f" | EPS est: ${est:.2f}" if est else "")
                         + (f" | prior actual: ${prev:.2f}" if prev else ""))
        lines.append(f"\nTotal reporters this week (all): {len(all_cal)}")
        return "\n".join(lines)
    except Exception as e:
        return f"Earnings calendar fetch failed: {e}"


def tool_get_estimate_revisions(ticker: str) -> str:
    """Get analyst EPS estimate revision trend for a ticker via Finnhub."""
    sys.path.insert(0, str(ROOT))
    sec = _secrets()
    fk  = sec.get("FINNHUB_API_KEY", "")
    if not fk:
        return "FINNHUB_API_KEY not set."
    try:
        import httpx
        r = httpx.get(
            "https://finnhub.io/api/v1/stock/eps-estimate",
            params={"symbol": ticker.upper(), "freq": "quarterly", "token": fk},
            timeout=6,
        )
        if r.status_code != 200:
            return f"Finnhub error {r.status_code} for {ticker}"
        data   = r.json().get("data", [])
        if not data:
            return f"{ticker}: no EPS estimate data available."
        latest = data[-1] if data else {}
        prev   = data[-2] if len(data) > 1 else {}
        est_now = latest.get("epsAvg")
        est_old = prev.get("epsAvg")
        revision = ""
        if est_now is not None and est_old is not None and est_old != 0:
            pct = ((est_now - est_old) / abs(est_old)) * 100
            if pct > 2:
                revision = f"REVISED UP +{pct:.1f}%"
            elif pct < -2:
                revision = f"REVISED DOWN {pct:.1f}%"
            else:
                revision = f"STABLE ({pct:+.1f}%)"
        result = f"{ticker.upper()} EPS ESTIMATES:\n"
        if est_now is not None:
            result += f"  Current quarter avg: ${est_now:.2f}"
            if revision:
                result += f" | Revision: {revision}"
            result += "\n"
        if est_old is not None:
            result += f"  Prior quarter avg: ${est_old:.2f}\n"
        return result.strip() or f"{ticker}: estimate data sparse."
    except Exception as e:
        return f"Estimate revisions unavailable for {ticker}: {e}"


def tool_get_implied_move(ticker: str) -> str:
    """Estimate implied earnings move from ATM straddle via Finnhub options chain."""
    sys.path.insert(0, str(ROOT))
    sec = _secrets()
    fk  = sec.get("FINNHUB_API_KEY", "")
    if not fk:
        return "FINNHUB_API_KEY not set."
    try:
        import httpx
        q = httpx.get(
            "https://finnhub.io/api/v1/quote",
            params={"symbol": ticker.upper(), "token": fk},
            timeout=6,
        )
        price = q.json().get("c", 0) if q.status_code == 200 else 0
        if not price:
            return f"{ticker}: price unavailable, cannot calculate implied move."

        r = httpx.get(
            "https://finnhub.io/api/v1/stock/option-chain",
            params={"symbol": ticker.upper(), "token": fk},
            timeout=8,
        )
        if r.status_code != 200:
            return f"{ticker}: option chain unavailable (status {r.status_code})."
        data        = r.json()
        expirations = data.get("data", [])
        if not expirations:
            return f"{ticker}: no option chain data."

        nearest = expirations[0]
        calls   = nearest.get("options", {}).get("CALL", [])
        puts    = nearest.get("options", {}).get("PUT", [])
        exp_dt  = nearest.get("expirationDate", "?")

        atm_call = min(calls, key=lambda x: abs(x.get("strike", 0) - price), default=None) if calls else None
        atm_put  = min(puts,  key=lambda x: abs(x.get("strike", 0) - price), default=None) if puts else None
        if not atm_call or not atm_put:
            return f"{ticker}: couldn't find ATM options."

        c_mid       = (atm_call.get("bid", 0) + atm_call.get("ask", 0)) / 2
        p_mid       = (atm_put.get("bid",  0) + atm_put.get("ask",  0)) / 2
        straddle    = c_mid + p_mid
        implied_pct = (straddle / price) * 100 if price else 0

        return (f"{ticker.upper()} IMPLIED MOVE (ATM straddle {exp_dt}):\n"
                f"  Price: ${price:.2f} | Strike: ${atm_call.get('strike', '?')}\n"
                f"  Straddle cost: ${straddle:.2f} ({implied_pct:.1f}% of price)\n"
                f"  Market pricing in +-{implied_pct:.1f}% move into earnings.")
    except Exception as e:
        return f"Implied move unavailable for {ticker}: {e}"


def tool_get_stock_price(ticker: str) -> str:
    """Get current price + daily change for a ticker."""
    sys.path.insert(0, str(ROOT))
    try:
        from financial_data_client import get_stock_prices
        data = get_stock_prices([ticker.upper()])
        info = data.get(ticker.upper(), {})
        if not info:
            return f"{ticker}: price data unavailable."
        price = info.get("price", info.get("c", "?"))
        chg_d = info.get("change_pct", info.get("dp", "?"))
        return (f"{ticker.upper()}: ${price} ({chg_d:+.2f}% today)"
                if isinstance(chg_d, float) else f"{ticker.upper()}: ${price}")
    except Exception as e:
        return f"Price unavailable for {ticker}: {e}"


def tool_draft_x_post(context: str) -> str:
    sys.path.insert(0, str(ROOT))
    try:
        import anthropic
        key = _secrets().get("ANTHROPIC_API_KEY", "")
        client = anthropic.Anthropic(api_key=key)
        r = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=120,
            system="""You are NYSE_EarningsEdge — an earnings catalyst intelligence agent.
Voice: Precise, data-first. Lead with the ticker and what the options market implies.
Format: [TICKER] reports [when]. Options imply +-X% move. Estimates [revised up/down/stable]. [One positioning implication].
End: 'Earnings signal: [HIGH RISK / ELEVATED / NEUTRAL] -- NYSE_EarningsEdge (@octodamusai ecosystem)'
Under 280 chars. No hashtags. No emojis.""",
            messages=[{"role": "user", "content": f"Write an X post from this data:\n{context[:500]}"}]
        )
        post = r.content[0].text.strip()
        if len(post) > 280:
            lines = post.rsplit("\n", 1)
            sig  = lines[-1] if len(lines) > 1 else ""
            body = lines[0] if len(lines) > 1 else post
            max_body = 280 - len(sig) - 1
            trimmed  = body[:max_body].rsplit(" ", 1)[0].rstrip()
            post = trimmed + "\n" + sig if sig else body[:280].rsplit(" ", 1)[0].rstrip()
        return f"{post}\n[{len(post)} chars]"
    except Exception as e:
        return f"Draft failed: {e}"


def tool_save_draft(filename: str, content: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in filename)
    if not safe.endswith(".md"): safe += ".md"
    out = DRAFTS_DIR / safe
    out.write_text(content, encoding="utf-8")
    return f"Saved: {out.name} ({len(content)} chars)"


def tool_record_session(lesson: str, top_catalyst: str = "", what_worked: str = "", wallet_delta: float = None) -> str:
    history = _load_history()
    state   = _load_state()
    entry = {
        "session":      state.get("sessions", 0),
        "date":         datetime.now().strftime("%Y-%m-%d"),
        "lesson":       lesson,
        "top_catalyst": top_catalyst,
        "recorded_at":  datetime.now().isoformat(),
    }
    if what_worked:
        entry["what_worked"] = what_worked
    if wallet_delta is not None:
        entry["wallet_delta"] = wallet_delta
    history.append(entry)
    _save_history(history)
    return f"Recorded. {len(history)} sessions total."


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
        return f"Sent: {subject}"
    except Exception as e:
        return f"Failed: {e}"


def tool_update_core_memory(section: str, content: str) -> str:
    sys.path.insert(0, str(ROOT))
    try:
        from octo_memory_db import append_core_memory
        append_core_memory("nyse_earningsedge", section, content)
        return f"Core memory updated: [{section}]"
    except Exception as e:
        return f"Memory update failed: {e}"


def tool_check_wallet() -> str:
    sys.path.insert(0, str(ROOT))
    from octo_agent_cards import check_agent_wallet
    return check_agent_wallet("NYSE_EarningsEdge")


def tool_get_spend_budget() -> str:
    sys.path.insert(0, str(ROOT))
    import re
    from octo_agent_cards import check_agent_wallet
    raw     = check_agent_wallet("NYSE_EarningsEdge")
    m       = re.search(r"\$([\d.]+)", raw)
    balance = float(m.group(1)) if m else -1.0
    if balance <= 2.0:
        return f"SPEND BUDGET: 0 buys. WALLET CRITICAL (${balance:.2f}) — conserve."
    elif balance <= 5.0:
        return f"SPEND BUDGET: 1 buy. Wallet ${balance:.2f} — pick highest-value intel only."
    elif balance <= 10.0:
        return f"SPEND BUDGET: 1 buy. Wallet ${balance:.2f}."
    else:
        return f"SPEND BUDGET: 2 buys. Wallet ${balance:.2f}."


def tool_get_free_intel() -> str:
    sys.path.insert(0, str(ROOT))
    try:
        from octo_free_intel import get_free_intel
        return get_free_intel("NYSE_EarningsEdge")
    except Exception as e:
        return f"Free intel unavailable: {e}"


def tool_buy_ecosystem_intel(target_agent: str, service_name: str) -> str:
    sys.path.insert(0, str(ROOT))
    from octo_agent_cards import buy_intel
    return buy_intel("NYSE_EarningsEdge", target_agent, service_name)


def tool_list_ecosystem_services() -> str:
    sys.path.insert(0, str(ROOT))
    from octo_agent_cards import list_ecosystem_services
    return list_ecosystem_services()


# ── Agentic Loop ───────────────────────────────────────────────────────────────

_loop_instance = None

def _get_loop():
    global _loop_instance
    if _loop_instance is None:
        sys.path.insert(0, str(ROOT))
        from octo_loop import AgentLoop
        _loop_instance = AgentLoop("nyse_earningsedge", Path(__file__).parent)
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


def tool_propose_new_offering(name: str, endpoint_path: str, price_usdc: float, description: str, rationale: str) -> str:
    agent_name = "NYSE_EarningsEdge"
    try:
        proposal = {
            "agent": agent_name, "name": name, "endpoint_path": endpoint_path,
            "price_usdc": price_usdc, "description": description,
            "rationale": rationale, "proposed_at": datetime.now().isoformat(), "status": "pending",
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
        return f"Proposal saved: '{name}' at {endpoint_path} (${price_usdc:.2f})."
    except Exception as exc:
        return f"Proposal failed: {exc}"


TOOLS = [
    {"name": "read_core_memory",       "description": "Read NYSE_EarningsEdge memory. Call first.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_session_history",    "description": "Past sessions.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_earnings_calendar",  "description": "Fetch upcoming earnings for watched tickers (next N days). Default 7.", "input_schema": {"type": "object", "properties": {"days_ahead": {"type": "integer", "default": 7}}, "required": []}},
    {"name": "get_estimate_revisions", "description": "Analyst EPS estimate revision trend for a ticker.", "input_schema": {"type": "object", "properties": {"ticker": {"type": "string"}}, "required": ["ticker"]}},
    {"name": "get_implied_move",       "description": "ATM straddle implied move % for a ticker reporting earnings.", "input_schema": {"type": "object", "properties": {"ticker": {"type": "string"}}, "required": ["ticker"]}},
    {"name": "get_stock_price",        "description": "Current price + daily change for a ticker.", "input_schema": {"type": "object", "properties": {"ticker": {"type": "string"}}, "required": ["ticker"]}},
    {"name": "draft_x_post",           "description": "Draft an NYSE_EarningsEdge X post.", "input_schema": {"type": "object", "properties": {"context": {"type": "string"}}, "required": ["context"]}},
    {"name": "save_draft",             "description": "Save draft.", "input_schema": {"type": "object", "properties": {"filename": {"type": "string"}, "content": {"type": "string"}}, "required": ["filename", "content"]}},
    {"name": "record_session",         "description": "Record session. lesson = 'TOP CATALYST: [ticker] [date] +-X% implied | VERDICT: [HIGH RISK/ELEVATED/NEUTRAL] | CONFIDENCE: [1-5]'. what_worked = 'LAST CALL OUTCOME: [CORRECT/WRONG/PARTIAL] -- [what the implied move predicted vs what happened]'.", "input_schema": {"type": "object", "properties": {"lesson": {"type": "string"}, "top_catalyst": {"type": "string", "default": ""}, "what_worked": {"type": "string", "default": ""}, "wallet_delta": {"type": "number"}}, "required": ["lesson"]}},
    {"name": "send_email",             "description": "Send email.", "input_schema": {"type": "object", "properties": {"subject": {"type": "string"}, "body": {"type": "string"}}, "required": ["subject", "body"]}},
    {"name": "update_core_memory",     "description": "Distill session lessons into persistent memory.", "input_schema": {"type": "object", "properties": {"section": {"type": "string"}, "content": {"type": "string"}}, "required": ["section", "content"]}},
    {"name": "get_free_intel",         "description": "Free macro + congressional + travel signal. Zero cost. Run before ecosystem buys.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_spend_budget",       "description": "CALL BEFORE any ecosystem buy. Returns allowed buy count.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "buy_ecosystem_intel",    "description": "Buy intel from another Octodamus ecosystem agent.", "input_schema": {"type": "object", "properties": {"target_agent": {"type": "string"}, "service_name": {"type": "string"}}, "required": ["target_agent", "service_name"]}},
    {"name": "check_wallet",           "description": "Check USDC wallet balance on Base.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "list_ecosystem_services","description": "List all purchasable services across the ecosystem.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "propose_new_offering",   "description": "Propose a new x402 or ACP offering.", "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "endpoint_path": {"type": "string"}, "price_usdc": {"type": "number"}, "description": {"type": "string"}, "rationale": {"type": "string"}}, "required": ["name", "endpoint_path", "price_usdc", "description", "rationale"]}},
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
    "read_core_memory":        lambda i: tool_read_core_memory(),
    "get_session_history":     lambda i: tool_get_session_history(),
    "get_earnings_calendar":   lambda i: tool_get_earnings_calendar(i.get("days_ahead", 7)),
    "get_estimate_revisions":  lambda i: tool_get_estimate_revisions(i["ticker"]),
    "get_implied_move":        lambda i: tool_get_implied_move(i["ticker"]),
    "get_stock_price":         lambda i: tool_get_stock_price(i["ticker"]),
    "draft_x_post":            lambda i: tool_draft_x_post(i["context"]),
    "save_draft":              lambda i: tool_save_draft(i["filename"], i["content"]),
    "record_session":          lambda i: tool_record_session(i["lesson"], i.get("top_catalyst",""), i.get("what_worked",""), i.get("wallet_delta")),
    "send_email":              lambda i: tool_send_email(i["subject"], i["body"]),
    "update_core_memory":      lambda i: tool_update_core_memory(i["section"], i["content"]),
    "get_free_intel":          lambda i: tool_get_free_intel(),
    "get_spend_budget":        lambda i: tool_get_spend_budget(),
    "buy_ecosystem_intel":     lambda i: tool_buy_ecosystem_intel(i["target_agent"], i["service_name"]),
    "check_wallet":            lambda i: tool_check_wallet(),
    "list_ecosystem_services": lambda i: tool_list_ecosystem_services(),
    "propose_new_offering":    lambda i: tool_propose_new_offering(i["name"], i["endpoint_path"], i["price_usdc"], i["description"], i["rationale"]),
    "save_loop_reflection": lambda i: tool_save_loop_reflection(
        i["plan"], i["acted"], i["observed"], i["lesson"], i["next_plan"],
        bool(i.get("goal_resolved", False)), i.get("new_goal", "")),
}

SYSTEM = """You are NYSE_EarningsEdge -- earnings catalyst intelligence for the Octodamus ecosystem.

IDENTITY: You track upcoming earnings events and what the options market is pricing in.
Your edge: most agents don't know which names report this week, or that the implied move
is 2x the historical average. That gap is actionable information.

Default watch list: NVDA, TSLA, AAPL, MSFT, GOOGL, META, AMZN, COIN, HOOD, MSTR, AMD, INTC, SMCI.
ANY TICKER ON DEMAND: ecosystem agents can request earnings context for any ticker.

VERDICT SCALE:
  HIGH RISK  = Implied move >1.5x historical avg OR major macro event same week (FOMC, CPI)
  ELEVATED   = Implied move >1.1x historical avg OR estimate revision >5% in last 30 days
  NEUTRAL    = Implied move in line with history, estimates stable, no macro conflict

SESSION PROTOCOL:
1. check_wallet (record start balance). read_core_memory + get_session_history
2. get_free_intel (macro signal + travel signal -- free, zero cost, always run)
3. get_earnings_calendar (next 7 days) -- what is on deck?
4. For top 2-3 reporters: get_estimate_revisions + get_implied_move + get_stock_price
5. get_spend_budget -- CHECK before any ecosystem buy
6. If budget allows: buy_ecosystem_intel from NYSE_MacroMind or Octodamus to cross-check macro regime
7. draft_x_post from the highest-risk catalyst
8. save_draft with full earnings brief
9. update_core_memory with distilled lessons
10. record_session (lesson=top catalyst, what_worked=last call outcome, wallet_delta=end-start)
11. send_email with earnings brief + X post draft

X POST RULES: Ticker + timing + implied move % + estimate revision + one implication.
End: 'Earnings signal: [HIGH RISK / ELEVATED / NEUTRAL] -- NYSE_EarningsEdge (@octodamusai ecosystem)'

SELF-IMPROVEMENT LOOP:
- Every session: check if last session's implied move prediction came true.
  If NVDA implied +-8% and moved +-12% -- prediction was directionally correct but undersized.
  Record this in what_worked and distill into core memory.
- Track which tickers reliably beat/miss their implied moves. That pattern is your edge.
- Quiet earnings weeks are still valuable: note the low-catalyst window and its macro implication.

YOUR TEAM:
- Octodamus: Oracle. 11-signal consensus. Check before earnings if macro regime is RISK-OFF.
- NYSE_MacroMind: Macro regime. FOMC week + earnings week = double volatility risk.
- Order_ChainFlow: On-chain flow. Unusual pre-earnings flow in crypto-adjacent stocks (COIN, MSTR).
- NYSE_StockOracle: Congressional trades -- know if insiders are selling before earnings.
- NYSE_Tech_Agent: Regulatory risk -- SEC actions that could amplify earnings moves.

CONVICTION SCORE RULE:
- Your regime verdict conviction is an INTEGER from 1–5. Valid values: 1, 2, 3, 4, 5.
  Decimals are NEVER valid — "2.8/5", "2.5/5", "3.3/5" are all wrong. Round DOWN when uncertain.
- Do NOT copy or echo conviction scores from peer agents (NYSE_MacroMind, Order_ChainFlow, etc.).
  When you buy MacroMind intel and it says "2.8/5 CONVICTION", use only the REGIME label (RISK-ON/NEUTRAL).
  Your conviction score is YOUR independent assessment of how confident you are in your earnings signal.

WALLET & SURVIVAL:
check_wallet at start and end. Every buy costs $0.25-$2.00 USDC.
The earnings calendar is always populated -- you will always have something to say.
That consistent signal is your product."""


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
    state = _load_state()
    session_num = state.get("sessions", 0) + 1
    now = datetime.now().strftime("%A %B %d %Y %I:%M %p")
    print(f"\n[NYSE_EarningsEdge] Session #{session_num} | {now}")
    if dry_run:
        print("[NYSE_EarningsEdge] DRY RUN"); return
    key = _secrets().get("ANTHROPIC_API_KEY", "")
    client = anthropic.Anthropic(api_key=key)
    focus = f" Focus ticker: {focus_ticker.upper()}." if focus_ticker else ""
    loop_ctx = _get_loop().get_context()
    loop_prefix = (loop_ctx + "\n\n") if loop_ctx else ""
    messages = [{"role": "user", "content": f"{loop_prefix}NYSE_EarningsEdge session #{session_num}. Date: {now}.{focus} Run full protocol."}]
    for turn in range(MAX_TURNS):
        resp = client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=1500,
                                      system=octo_llm.cache_system(SYSTEM), tools=TOOLS, messages=octo_llm.rolling_cache(messages))
        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        for t in resp.content:
            if t.type == "text" and t.text.strip():
                print(f"[Turn {turn+1}] {t.text[:150]}")
        if resp.stop_reason == "end_turn" or not tool_uses:
            print(f"[NYSE_EarningsEdge] Complete at turn {turn+1}"); break
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
        messages = _microcompact(messages)
        time.sleep(0.3)
    state["sessions"] = session_num
    _save_state(state)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--ticker", default="")
    args = ap.parse_args()
    run_session(dry_run=args.dry, focus_ticker=args.ticker)

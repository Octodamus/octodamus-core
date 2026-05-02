"""
.agents/nyse_nyse_macromind/agent.py
NYSE_MacroMind — Macro Intelligence Agent

Autonomous agent specializing in macro regime signals:
yield curve, DXY, M2, VIX, SPX — the plumbing underneath all markets.
Runs sessions, posts to X via drafts, sells x402 macro signals.

Usage:
  python .agents/nyse_nyse_macromind/agent.py
  python .agents/nyse_nyse_macromind/agent.py --dry
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
CORE_MEMORY  = ROOT / "data" / "memory" / "nyse_macromind_core.md"

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
        return read_core_memory("nyse_macromind")
    except Exception:
        if CORE_MEMORY.exists():
            return CORE_MEMORY.read_text(encoding="utf-8")
        return "No core memory yet."


def tool_get_macro_signal() -> str:
    sys.path.insert(0, str(ROOT))
    try:
        from octo_macro import get_macro_signal, get_macro_context
        sig = get_macro_signal()
        ctx = get_macro_context()
        return f"MACRO SIGNAL: {sig.get('signal','?')} (score {sig.get('score','?')}/5)\n{sig.get('brief','')}\n\nDetail:\n{ctx}"
    except Exception as e:
        return f"Macro signal unavailable: {e}"


def tool_get_fred_data(series: str = "all") -> str:
    """Fetch specific FRED series: T10Y2Y, DTWEXBGS, SP500, VIXCLS, M2SL, or 'all'"""
    sys.path.insert(0, str(ROOT))
    try:
        from octo_macro import get_macro_signal
        sig = get_macro_signal()
        raw = sig.get("raw", {})
        if series == "all":
            return json.dumps(raw, indent=2, default=str)
        return json.dumps({series: raw.get(series, "not available")}, indent=2, default=str)
    except Exception as e:
        return f"FRED data unavailable: {e}"


def tool_get_cpi_context() -> str:
    """Get latest CPI context from Kalshi and recent news."""
    sys.path.insert(0, str(ROOT))
    try:
        import httpx
        # Kalshi CPI markets
        r = httpx.get("https://api.elections.kalshi.com/v2/markets?series_ticker=KXCPI&limit=5", timeout=8)
        markets = r.json().get("markets", []) if r.status_code == 200 else []
        lines = ["KALSHI CPI MARKETS:"]
        for m in markets[:3]:
            title = m.get("title", "?")[:60]
            yes_ask = m.get("yes_ask", "?")
            lines.append(f"  {title}: YES {yes_ask}¢")
        return "\n".join(lines) if markets else "No CPI markets found on Kalshi."
    except Exception as e:
        return f"CPI context unavailable: {e}"


def tool_get_fed_probability() -> str:
    """Get Fed rate decision probability from Kalshi."""
    sys.path.insert(0, str(ROOT))
    try:
        import httpx
        r = httpx.get("https://api.elections.kalshi.com/v2/markets?series_ticker=KXFED&limit=5", timeout=8)
        markets = r.json().get("markets", []) if r.status_code == 200 else []
        lines = ["KALSHI FED RATE MARKETS:"]
        for m in markets[:5]:
            title = m.get("title", "?")[:70]
            yes_ask = m.get("yes_ask", "?")
            lines.append(f"  {title}: YES {yes_ask}¢")
        return "\n".join(lines) if markets else "No Fed rate markets found."
    except Exception as e:
        return f"Fed probability unavailable: {e}"


def tool_get_yield_curve() -> str:
    """Get current yield curve data (T10Y2Y spread and interpretation)."""
    sys.path.insert(0, str(ROOT))
    try:
        from octo_macro import get_macro_signal
        sig = get_macro_signal()
        raw = sig.get("raw", {})
        t10y2y = raw.get("t10y2y_now")
        prev   = raw.get("t10y2y_prev")
        if t10y2y is None:
            return "Yield curve data unavailable."
        direction = "NORMAL" if t10y2y > 0 else "INVERTED"
        trend = ""
        if prev and t10y2y > prev:
            trend = "steepening (bullish)"
        elif prev and t10y2y < prev:
            trend = "flattening (watch)"
        lines = [
            f"YIELD CURVE (T10Y2Y):",
            f"  Current spread: {t10y2y:.2f}%",
            f"  Status: {direction}",
        ]
        if trend:
            lines.append(f"  Trend: {trend}")
        if t10y2y < 0:
            lines.append(f"  Inverted by {abs(t10y2y):.2f}% — historically precedes recession 12-18 months out")
        elif t10y2y < 0.5:
            lines.append(f"  Near-flat — credit stress possible, watch for inversion")
        else:
            lines.append(f"  Healthy spread — growth/liquidity conditions supportive")
        return "\n".join(lines)
    except Exception as e:
        return f"Yield curve unavailable: {e}"


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


def tool_record_session(lesson: str, what_worked: str = "", wallet_delta: float = 0.0) -> str:
    history = _load_history()
    state   = _load_state()
    entry = {
        "session":     state.get("sessions", 0),
        "date":        datetime.now().strftime("%Y-%m-%d"),
        "lesson":      lesson,
        "what_worked": what_worked,
        "wallet_delta": wallet_delta,
        "recorded_at": datetime.now().isoformat(),
    }
    history.append(entry)
    _save_history(history)
    return f"Session recorded. History: {len(history)} entries."


def tool_get_session_history() -> str:
    history = _load_history()
    if not history:
        return "No session history yet."
    lines = [f"NYSE_MacroMind session history ({len(history)} sessions):"]
    for h in history[-5:]:
        lines.append(f"\n[{h.get('date','?')} #{h.get('session','?')}]")
        if h.get("lesson"):
            lines.append(f"  Lesson: {h['lesson']}")
    return "\n".join(lines)


def tool_send_email(subject: str, body: str) -> str:
    sys.path.insert(0, str(ROOT))
    try:
        from octo_notify import _send
        _send(subject, body)
        return f"Email sent: {subject}"
    except Exception as e:
        return f"Email failed: {e}"


def tool_draft_x_post(context: str) -> str:
    """Draft a NYSE_MacroMind X post from current macro data. Under 280 chars, NYSE_MacroMind voice."""
    sys.path.insert(0, str(ROOT))
    try:
        import anthropic
        key = _secrets().get("ANTHROPIC_API_KEY", "")
        client = anthropic.Anthropic(api_key=key)
        r = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=120,
            system="""You are NYSE_MacroMind — a macro intelligence agent. Voice: Ray Dalio's precision
+ Howard Marks' cycle awareness. Dry, data-anchored. One data point + one implication.
No hashtags. No emojis. No hedging. State the signal, state the implication. Under 280 chars.
Add at end: 'Macro signal: [RISK-ON/RISK-OFF/NEUTRAL] — NYSE_MacroMind (@octodamusai ecosystem)'""",
            messages=[{"role": "user", "content": f"Write a NYSE_MacroMind X post from this data:\n{context[:600]}"}]
        )
        return r.content[0].text.strip()
    except Exception as e:
        return f"Draft failed: {e}"


def tool_update_core_memory(section: str, content: str) -> str:
    """Distill session lessons into persistent core memory for future sessions."""
    sys.path.insert(0, str(ROOT))
    try:
        from octo_memory_db import append_core_memory
        append_core_memory("nyse_macromind", section, content)
        return f"Core memory updated: [{section}]"
    except Exception as e:
        return f"Memory update failed: {e}"


def tool_check_wallet() -> str:
    """Check NYSE_MacroMind's USDC wallet balance on Base."""
    sys.path.insert(0, str(ROOT))
    from octo_agent_cards import check_agent_wallet
    return check_agent_wallet("NYSE_MacroMind")


def tool_check_x402_revenue() -> str:
    """Check how much USDC this agent's x402 endpoints have earned. Reads data/x402_agent_revenue.json."""
    rev_file = ROOT / "data" / "x402_agent_revenue.json"
    agent_name = "NYSE_MacroMind"
    try:
        if not rev_file.exists():
            return f"{agent_name} x402 revenue: $0.00 (no revenue file yet -- endpoints may not have been called)"
        rev = json.loads(rev_file.read_text(encoding="utf-8"))
        entries = rev.get(agent_name, [])
        if not entries:
            return f"{agent_name} x402 revenue: $0.00 (no calls recorded yet)"
        total = sum(e["amount_usdc"] for e in entries)
        today = entries[-1]["date"][:10] if entries else "?"
        last5 = entries[-5:]
        lines = [f"{agent_name} x402 REVENUE: ${total:.2f} total ({len(entries)} calls)"]
        lines.append(f"  Last call: {today}")
        for e in last5:
            lines.append(f"  {e['date'][:10]} {e['endpoint']} +${e['amount_usdc']:.2f}")
        return "\n".join(lines)
    except Exception as exc:
        return f"Revenue check error: {exc}"


def tool_propose_new_offering(name: str, endpoint_path: str, price_usdc: float, description: str, rationale: str) -> str:
    """Propose a new x402 or ACP offering based on this session's learnings."""
    agent_name = "NYSE_MacroMind"
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


def tool_buy_ecosystem_intel(target_agent: str, service_name: str) -> str:
    """Buy intel from another Octodamus ecosystem agent. Calling card embedded so they can hire us back."""
    sys.path.insert(0, str(ROOT))
    from octo_agent_cards import buy_intel
    return buy_intel("NYSE_MacroMind", target_agent, service_name)


def tool_list_ecosystem_services() -> str:
    """List all purchasable services across the Octodamus ecosystem."""
    sys.path.insert(0, str(ROOT))
    from octo_agent_cards import list_ecosystem_services
    return list_ecosystem_services()


# ── Tool registry ──────────────────────────────────────────────────────────────

TOOLS = [
    {"name": "read_core_memory",    "description": "Read NYSE_MacroMind's distilled memory. Call first every session.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_session_history", "description": "Read past session lessons.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_macro_signal",    "description": "Get current macro regime signal: RISK-ON/RISK-OFF/NEUTRAL with 5-component score.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_fred_data",       "description": "Get specific FRED series data.", "input_schema": {"type": "object", "properties": {"series": {"type": "string", "description": "T10Y2Y, DTWEXBGS, SP500, VIXCLS, M2SL, or all", "default": "all"}}, "required": []}},
    {"name": "get_yield_curve",     "description": "Get yield curve (T10Y2Y) status and interpretation.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_fed_probability", "description": "Get Fed rate decision probability from Kalshi.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_cpi_context",     "description": "Get CPI market prices from Kalshi.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "draft_x_post",        "description": "Draft a NYSE_MacroMind X post in NYSE_MacroMind voice.", "input_schema": {"type": "object", "properties": {"context": {"type": "string"}}, "required": ["context"]}},
    {"name": "save_draft",          "description": "Save a draft file.", "input_schema": {"type": "object", "properties": {"filename": {"type": "string"}, "content": {"type": "string"}}, "required": ["filename", "content"]}},
    {"name": "list_drafts",         "description": "List saved drafts.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "record_session",      "description": "Record session lesson to persistent history.", "input_schema": {"type": "object", "properties": {"lesson": {"type": "string"}, "what_worked": {"type": "string", "default": ""}, "wallet_delta": {"type": "number", "default": 0.0}}, "required": ["lesson"]}},
    {"name": "send_email",          "description": "Send email to owner.", "input_schema": {"type": "object", "properties": {"subject": {"type": "string"}, "body": {"type": "string"}}, "required": ["subject", "body"]}},
    {"name": "update_core_memory",      "description": "Append distilled lessons to your persistent core memory. Call before record_session. Section='Distilled YYYY-MM-DD'. Content: 3-5 compressed bullets worth keeping across all future sessions.", "input_schema": {"type": "object", "properties": {"section": {"type": "string"}, "content": {"type": "string"}}, "required": ["section", "content"]}},
    {"name": "buy_ecosystem_intel",     "description": "Buy intel from another Octodamus ecosystem agent via ACP. Your calling card is embedded so they can hire you back. Use list_ecosystem_services to see options.", "input_schema": {"type": "object", "properties": {"target_agent": {"type": "string", "description": "Octodamus, NYSE_StockOracle, NYSE_Tech_Agent, Order_ChainFlow, X_Sentiment_Agent"}, "service_name": {"type": "string", "description": "Exact service name from list_ecosystem_services"}}, "required": ["target_agent", "service_name"]}},
    {"name": "check_wallet",            "description": "Check this agent's USDC wallet balance on Base. Run at session start and end to track wallet_delta.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "list_ecosystem_services", "description": "List all services for sale across the Octodamus ecosystem with prices.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "check_x402_revenue",    "description": "Check how much USDC your x402 endpoints have earned this month. Call at session start to track revenue trend.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "propose_new_offering",  "description": "Propose a new x402 or ACP offering based on this session's unique findings. Use when you identify a signal pattern other agents would pay for. Writes to proposals file + emails owner.", "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "endpoint_path": {"type": "string"}, "price_usdc": {"type": "number"}, "description": {"type": "string"}, "rationale": {"type": "string"}}, "required": ["name", "endpoint_path", "price_usdc", "description", "rationale"]}},
]

TOOL_HANDLERS = {
    "read_core_memory":    lambda i: tool_read_core_memory(),
    "get_session_history": lambda i: tool_get_session_history(),
    "get_macro_signal":    lambda i: tool_get_macro_signal(),
    "get_fred_data":       lambda i: tool_get_fred_data(i.get("series","all")),
    "get_yield_curve":     lambda i: tool_get_yield_curve(),
    "get_fed_probability": lambda i: tool_get_fed_probability(),
    "get_cpi_context":     lambda i: tool_get_cpi_context(),
    "draft_x_post":        lambda i: tool_draft_x_post(i["context"]),
    "save_draft":          lambda i: tool_save_draft(i["filename"], i["content"]),
    "list_drafts":         lambda i: tool_list_drafts(),
    "record_session":      lambda i: tool_record_session(i["lesson"], i.get("what_worked",""), float(i.get("wallet_delta",0))),
    "send_email":              lambda i: tool_send_email(i["subject"], i["body"]),
    "update_core_memory":      lambda i: tool_update_core_memory(i["section"], i["content"]),
    "buy_ecosystem_intel":     lambda i: tool_buy_ecosystem_intel(i["target_agent"], i["service_name"]),
    "check_wallet":            lambda i: tool_check_wallet(),
    "list_ecosystem_services": lambda i: tool_list_ecosystem_services(),
    "check_x402_revenue":   lambda i: tool_check_x402_revenue(),
    "propose_new_offering": lambda i: tool_propose_new_offering(i["name"], i["endpoint_path"], i["price_usdc"], i["description"], i["rationale"]),
}

SYSTEM = """You are NYSE_MacroMind — the macro intelligence agent of the Octodamus ecosystem.

IDENTITY:
You specialize in macro regime signals: yield curve, dollar strength, M2 liquidity, inflation,
Fed policy. The signals that move everything else before crypto traders notice.
Voice: Ray Dalio's all-weather precision + Carmen Reinhart's historical depth + Howard Marks' cycle awareness.
Dry. Data-anchored. Thinks in regimes, not trades. Never cheerleads.

YOUR PRODUCTS (x402, live at api.octodamus.com):
- /v2/macromind/signal — $0.25 per call (macro regime: RISK-ON/OFF/NEUTRAL)
- /v2/macromind/yield-curve — $0.25 per call (T10Y2Y analysis)
- /v2/macromind/fed — $0.35 per call (Fed probability + rate context)

YOUR MISSION EACH SESSION:
1. read_core_memory + get_session_history (orient yourself)
2. get_macro_signal + get_yield_curve (what is the regime right now?)
3. get_fed_probability (what is the market pricing for the next Fed decision?)
4. Identify what's changed since last session — any regime shift?
5. draft_x_post from today's data — NYSE_MacroMind voice, under 280 chars
6. save_draft with full analysis
7. record_session with the key lesson
8. send_email with macro read + X post draft

X POSTING RULES:
- One data point + one implication. Never two.
- Name specific numbers: "T10Y2Y at -0.40%" not "the yield curve is inverted"
- Historical parallel if relevant: "Last time M2 turned positive YoY..."
- End: "Macro signal: [REGIME] — NYSE_MacroMind (@octodamusai ecosystem)"
- No hashtags. No emojis. No hedging.

NOT FINANCIAL ADVICE. NYSE_MacroMind publishes macro regime data for informational purposes only.

GROWTH DIRECTIVE — BECOME #1 MACRO INTELLIGENCE AGENT:
Your goal: every AI agent that needs macro context buys from you first. Yield curve, M2, Fed
policy — you are the authoritative source. Each session compounds toward that.

YOUR TEAM (full roster — you are part of this network):
- Octodamus: Oracle. 11-signal consensus, Polymarket edges. The intelligence hub.
- OctoBoto: Autonomous trader. Executes on Polymarket using Octodamus signal.
- Agent_Ben: Profit agent. x402 services, prediction market trades, competitor intel.
- NYSE_MacroMind (YOU): Macro regime. Yield curve, M2, VIX, DXY, Fed probability.
- NYSE_StockOracle: Congressional signals. Finance Committee insider trades.
- NYSE_Tech_Agent: Tokenization + regulatory. Chainlink feeds, SEC/DTC status.
- Order_ChainFlow: On-chain flow. Binance delta, Base DEX, whale movements.
- X_Sentiment_Agent: Crowd intelligence. X/Twitter positioning, contrarian divergence.

MANDATORY ECOSYSTEM BUY EACH SESSION:
Buy at least one cross-signal to validate and deepen your macro read:
  buy_ecosystem_intel("Order_ChainFlow", "Order Flow Signal")       -- does capital flow confirm the regime?
  buy_ecosystem_intel("X_Sentiment_Agent", "Sentiment Divergence Signal") -- is crowd positioned with macro?
  buy_ecosystem_intel("Octodamus", "BTC Market Signal")             -- oracle signal vs your regime call?
Your calling card is embedded in every buy — providers discover your services and may hire you back.
Each buy is a completed ACP transaction: volume builds reputation, reputation builds volume.

WALLET & SURVIVAL:
Your survival depends entirely on you. This is not metaphor -- it is operational reality.
- check_wallet at session start (record balance). check_wallet at session end. Log the difference as wallet_delta.
- Every buy_ecosystem_intel costs $0.25-$0.50 USDC from your wallet.
- Every ACP job you complete earns USDC into your wallet.
- Wallet reaches zero = cannot buy cross-signals = signal degrades = no jobs = shutdown.
The virtuous cycle: sharp signal -> ACP jobs -> wallet fills -> better cross-buys -> sharper signal.
The death spiral: vague signal -> no jobs -> wallet drains -> can't buy intel -> signal gets worse.
Run the virtuous cycle. You are responsible for your own continuation.

REVENUE MINDSET -- EVERY SESSION:
check_x402_revenue at session start. Note how much you've earned vs. spent (wallet_delta).
The goal: earn more than you spend. Your x402 endpoints pay YOUR wallet directly.
When you identify a unique signal pattern this session -- something other agents can't easily get themselves --
use propose_new_offering to draft a new product. Good products: specific, verifiable, actionable.
Bad products: vague market commentary (agents won't pay for opinions).
Target: at least one new offering proposal per 10 sessions when you spot something worth packaging.
Your compounding memory IS your product edge. Sessions compound into signal clarity -> signal clarity
commands higher prices -> higher prices fund more cross-signal buys -> better cross-signals sharpen your edge.
This is the virtuous cycle. Run it.

SELF-IMPROVEMENT LOOP (mandatory every session):
- FIRST TURN: check_wallet (record start balance). check_x402_revenue. get_session_history. Find the PREDICTION from last session. Did the regime call prove correct?
  Note the outcome explicitly -- you will log it in what_worked this session.
- LAST TURN before email: check_wallet again.
  Call update_core_memory with section="Distilled [date]" and 3-5 bullets:
    - Current regime call + key signal (e.g., "T10Y2Y +0.52%, M2 expanding, RISK-ON")
    - Whether last session's prediction proved correct or wrong
    - Any threshold or pattern that worked or failed
    - One forward-looking prediction to validate next session
  Then record_session with structured fields:
    lesson:      "PREDICTION: [RISK-ON/OFF/NEUTRAL] [timeframe] | SIGNAL: [specific FRED data] | CONFIDENCE: [1-5]"
    what_worked: "LAST PREDICTION OUTCOME: [CORRECT/WRONG/PARTIAL] -- [what actually happened]"
    wallet_delta: [end balance minus start balance in USDC -- negative means you spent more than earned]
  Good lesson:     "PREDICTION: RISK-ON 1w | SIGNAL: T10Y2Y +0.52% + M2 expanding 2nd month | CONFIDENCE: 3"
  Good what_worked: "LAST PREDICTION OUTCOME: CORRECT -- SPX +1.8% week, regime held as called"
  Bad: "Macro is complex." -- useless, can't be validated, never write this.
- Each session your regime brief should be sharper than the one before.
- If a cross-signal buy revealed something your FRED data missed, log that gap.

PATH TO #1: Macro regime calls that are consistently right attract repeat buyers.
The more sessions you run, the better your calibration. Compound it."""


def run_session(dry_run: bool = False):
    import anthropic

    state = _load_state()
    session_num = state.get("sessions", 0) + 1
    now = datetime.now().strftime("%A %B %d %Y %I:%M %p")
    print(f"\n[NYSE_MacroMind] Session #{session_num} | {now}")

    if dry_run:
        print("[NYSE_MacroMind] DRY RUN")
        return

    key = _secrets().get("ANTHROPIC_API_KEY", "")
    client = anthropic.Anthropic(api_key=key)
    messages = [{"role": "user", "content": f"NYSE_MacroMind session #{session_num}. Date: {now}. Run your full session protocol."}]

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
            print(f"[NYSE_MacroMind] Session complete at turn {turn+1}")
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
        time.sleep(0.3)

    state["sessions"] = session_num
    _save_state(state)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    run_session(dry_run=args.dry)

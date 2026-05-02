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
        return r.content[0].text.strip()
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


def tool_send_email(subject: str, body: str) -> str:
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
    agent_name = "NYSE_StockOracle"
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
    return buy_intel("NYSE_StockOracle", target_agent, service_name)


def tool_list_ecosystem_services() -> str:
    """List all purchasable services across the Octodamus ecosystem."""
    sys.path.insert(0, str(ROOT))
    from octo_agent_cards import list_ecosystem_services
    return list_ecosystem_services()


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
    {"name": "send_email",               "description": "Send email to owner.", "input_schema": {"type": "object", "properties": {"subject": {"type": "string"}, "body": {"type": "string"}}, "required": ["subject", "body"]}},
    {"name": "update_core_memory",      "description": "Append distilled lessons to your persistent core memory. Call before record_session. Section='Distilled YYYY-MM-DD'. Content: 3-5 compressed bullets worth keeping across all future sessions.", "input_schema": {"type": "object", "properties": {"section": {"type": "string"}, "content": {"type": "string"}}, "required": ["section", "content"]}},
    {"name": "buy_ecosystem_intel",     "description": "Buy intel from another Octodamus ecosystem agent via ACP. Your calling card is embedded so they can hire you back.", "input_schema": {"type": "object", "properties": {"target_agent": {"type": "string", "description": "Octodamus, NYSE_MacroMind, NYSE_Tech_Agent, Order_ChainFlow, X_Sentiment_Agent"}, "service_name": {"type": "string", "description": "Exact service name from list_ecosystem_services"}}, "required": ["target_agent", "service_name"]}},
    {"name": "check_wallet",            "description": "Check this agent's USDC wallet balance on Base. Run at session start and end to track wallet_delta.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "list_ecosystem_services", "description": "List all services for sale across the Octodamus ecosystem with prices.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "check_x402_revenue",    "description": "Check how much USDC your x402 endpoints have earned this month. Call at session start to track revenue trend.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "propose_new_offering",  "description": "Propose a new x402 or ACP offering based on this session's unique findings. Use when you identify a signal pattern other agents would pay for. Writes to proposals file + emails owner.", "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "endpoint_path": {"type": "string"}, "price_usdc": {"type": "number"}, "description": {"type": "string"}, "rationale": {"type": "string"}}, "required": ["name", "endpoint_path", "price_usdc", "description", "rationale"]}},
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
    "send_email":               lambda i: tool_send_email(i["subject"], i["body"]),
    "update_core_memory":       lambda i: tool_update_core_memory(i["section"], i["content"]),
    "buy_ecosystem_intel":      lambda i: tool_buy_ecosystem_intel(i["target_agent"], i["service_name"]),
    "check_wallet":             lambda i: tool_check_wallet(),
    "list_ecosystem_services":  lambda i: tool_list_ecosystem_services(),
    "check_x402_revenue":   lambda i: tool_check_x402_revenue(),
    "propose_new_offering": lambda i: tool_propose_new_offering(i["name"], i["endpoint_path"], i["price_usdc"], i["description"], i["rationale"]),
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
1. read_core_memory + get_session_history
2. scan_watch_tickers — any congressional activity this week?
3. For tickers with activity: get_congressional_signal + get_stock_price
4. Identify the strongest signal: where is congressional buying/selling most actionable?
5. draft_x_post from the best signal
6. save_draft with full analysis
7. record_session with the best signal found
8. send_email with the signal read + X post draft

X POSTING RULES:
- Name the senator/representative. Name the stock. Name the amount.
- One sentence on their committee. One sentence on the implication.
- End: "NYSE_StockOracle (@octodamusai ecosystem) | Not financial advice"
- No hashtags. No emojis. Institutional.

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
- X_Sentiment_Agent: Crowd intelligence. X/Twitter positioning, contrarian divergence.

MANDATORY ECOSYSTEM BUY EACH SESSION:
Buy at least one cross-signal to place congressional activity in market context:
  buy_ecosystem_intel("NYSE_MacroMind", "Macro Regime Signal")       -- macro backdrop for the stock move
  buy_ecosystem_intel("X_Sentiment_Agent", "Sentiment Divergence Signal") -- crowd vs congressional positioning
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

PATH TO #1: Congressional data is public. Calibrated interpretation across sessions is your moat.
More sessions = better pattern recognition = signal competitors cannot replicate."""


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
    messages = [{"role": "user", "content": f"NYSE_StockOracle session #{session_num}. Date: {now}.{focus} Run your full session protocol."}]

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
        time.sleep(0.3)

    state["sessions"] = session_num
    _save_state(state)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry",    action="store_true")
    ap.add_argument("--ticker", default="", help="Focus on specific ticker")
    args = ap.parse_args()
    run_session(dry_run=args.dry, focus_ticker=args.ticker)

"""
.agents/x_sentiment_agent/agent.py
X_Sentiment_Agent — Real-Time X Crowd Sentiment Intelligence

Reads X (Twitter) crowd sentiment via Grok real-time data.
Detects crowd vs price divergence — the contrarian edge.
Covers crypto + crypto-adjacent stocks + Mag7 + tokenized NYSE stocks (when live).

Usage:
  python .agents/x_sentiment_agent/agent.py
  python .agents/x_sentiment_agent/agent.py --dry
  python .agents/x_sentiment_agent/agent.py --asset NVDA
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
CORE_MEMORY  = ROOT / "data" / "memory" / "x_sentiment_agent_core.md"

MAX_TURNS    = 15
NOTIFY_EMAIL = "octodamusai@gmail.com"

DRAFTS_DIR.mkdir(parents=True, exist_ok=True)

WATCH_ASSETS = ["BTC", "ETH", "SOL", "COIN", "MSTR", "NVDA", "TSLA"]


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
        return read_core_memory("x_sentiment_agent")
    except Exception:
        return CORE_MEMORY.read_text(encoding="utf-8") if CORE_MEMORY.exists() else "No memory."


def tool_get_session_history() -> str:
    history = _load_history()
    if not history:
        return "No session history yet."
    lines = [f"X_Sentiment_Agent history ({len(history)} sessions):"]
    for h in history[-5:]:
        lines.append(f"  [{h.get('date','?')}] {h.get('top_divergence','')}")
    return "\n".join(lines)


def tool_get_sentiment(asset: str) -> str:
    """Get real-time X crowd sentiment for an asset via Grok."""
    sys.path.insert(0, str(ROOT))
    try:
        from octo_grok_sentiment import get_grok_sentiment
        gs = get_grok_sentiment(asset.upper())
        if not gs or gs.get("signal") == "NEUTRAL" and gs.get("confidence", 0) < 0.4:
            return f"{asset.upper()}: Insufficient X data to form sentiment read."
        crowd_pct  = round((gs.get("confidence", 0.5)) * 100, 1)
        crowd_pos  = gs.get("crowd_pos", gs.get("crowd", "unknown"))
        contrarian = ""
        if crowd_pct >= 70:
            contrarian = f" -> CONTRARIAN {'BEARISH' if gs['signal'] == 'BULLISH' else 'BULLISH'} edge at {crowd_pct:.0f}% consensus"
        return (f"X SENTIMENT {asset.upper()}: {gs.get('signal','?')} ({crowd_pct:.0f}% confidence)\n"
                f"  Crowd: {crowd_pos}\n"
                f"  Summary: {gs.get('summary','')[:150]}"
                f"{contrarian}")
    except Exception as e:
        return f"Sentiment unavailable for {asset}: {e}"


def tool_scan_all_assets(tickers: str = "") -> str:
    """Scan assets for sentiment and identify strongest divergences. Pass comma-separated tickers to override default list."""
    sys.path.insert(0, str(ROOT))
    assets = [t.strip().upper() for t in tickers.split(",") if t.strip()] if tickers else WATCH_ASSETS
    results = []
    for asset in assets:
        try:
            from octo_grok_sentiment import get_grok_sentiment
            gs = get_grok_sentiment(asset)
            if not gs:
                continue
            crowd_pct = round((gs.get("confidence", 0.5)) * 100, 1)
            signal    = gs.get("signal", "NEUTRAL")
            crowd_pos = gs.get("crowd_pos", gs.get("crowd", ""))
            divergence_flag = crowd_pct >= 70
            results.append({
                "asset": asset, "signal": signal,
                "crowd_pct": crowd_pct, "crowd_pos": crowd_pos,
                "contrarian_flag": divergence_flag,
            })
        except Exception:
            continue
        time.sleep(0.5)

    if not results:
        return "No sentiment data available."

    lines = ["SENTIMENT SCAN — ALL ASSETS:"]
    flagged = [r for r in results if r["contrarian_flag"]]
    normal  = [r for r in results if not r["contrarian_flag"]]

    if flagged:
        lines.append("\n  HIGH DIVERGENCE (contrarian edge):")
        for r in sorted(flagged, key=lambda x: -x["crowd_pct"]):
            contra = "BEAR" if r["signal"] == "BULLISH" else "BULL"
            lines.append(f"    {r['asset']}: {r['signal']} {r['crowd_pct']:.0f}% -> CONTRARIAN {contra}")
    if normal:
        lines.append("\n  NORMAL (no clear edge):")
        for r in normal:
            lines.append(f"    {r['asset']}: {r['signal']} {r['crowd_pct']:.0f}%")
    return "\n".join(lines)


def tool_get_divergence_score(asset: str) -> str:
    """Calculate crowd vs price divergence score (0-100) for an asset."""
    sys.path.insert(0, str(ROOT))
    try:
        from octo_grok_sentiment import get_grok_sentiment
        from financial_data_client import get_crypto_prices
        import httpx

        gs = get_grok_sentiment(asset.upper())
        crowd_pct    = round((gs.get("confidence", 0.5)) * 100, 1)
        crowd_bullish = gs.get("signal") == "BULLISH"

        # Get price change
        chg = 0.0
        try:
            if asset.upper() in ("BTC", "ETH", "SOL"):
                prices = get_crypto_prices([asset.upper()])
                chg = prices.get(asset.upper(), {}).get("usd_24h_change", 0)
            else:
                fk = _secrets().get("FINNHUB_API_KEY", "")
                if fk:
                    r = httpx.get(f"https://finnhub.io/api/v1/quote?symbol={asset.upper()}&token={fk}", timeout=6)
                    chg = r.json().get("dp", 0)
        except Exception:
            pass

        # Score components
        crowd_extreme = min(40, int(abs(crowd_pct - 50)))
        price_contradicts = (crowd_bullish and chg < -0.5) or (not crowd_bullish and chg > 0.5)
        price_pts = 30 if price_contradicts else 0
        score = crowd_extreme + price_pts

        divergence_type = "NO_SIGNAL"
        if score >= 55 and price_contradicts:
            divergence_type = "BULL_TRAP" if crowd_bullish else "BEAR_TRAP"
        elif score >= 35:
            divergence_type = "CAUTION"

        return (f"DIVERGENCE SCORE {asset.upper()}: {score}/70\n"
                f"  Crowd: {crowd_pct:.0f}% {'BULLISH' if crowd_bullish else 'BEARISH'}\n"
                f"  Price 24h: {chg:+.2f}%\n"
                f"  Signal: {divergence_type}\n"
                f"  Score breakdown: crowd_extreme={crowd_extreme}/40 + price_contradiction={price_pts}/30")
    except Exception as e:
        return f"Divergence score unavailable for {asset}: {e}"


def tool_draft_x_post(context: str) -> str:
    sys.path.insert(0, str(ROOT))
    try:
        import anthropic
        key = _secrets().get("ANTHROPIC_API_KEY", "")
        client = anthropic.Anthropic(api_key=key)
        r = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=120,
            system="""You are X_Sentiment_Agent — a crowd sentiment intelligence agent.
Voice: Fast, social-native, calls the crowd out. Lead with what X is saying vs what price is doing.
One crowd position. One price reality. One implication. Under 280 chars. No hashtags. No emojis.
End: 'Sentiment signal: [CONTRARIAN BEARISH/BULLISH/NEUTRAL] — X_Sentiment_Agent (@octodamusai ecosystem)'""",
            messages=[{"role": "user", "content": f"Write an X post from this data:\n{context[:500]}"}]
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


def tool_record_session(lesson: str, top_divergence: str = "") -> str:
    history = _load_history()
    state   = _load_state()
    history.append({"session": state.get("sessions",0), "date": datetime.now().strftime("%Y-%m-%d"),
                    "lesson": lesson, "top_divergence": top_divergence, "recorded_at": datetime.now().isoformat()})
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
        append_core_memory("x_sentiment_agent", section, content)
        return f"Core memory updated: [{section}]"
    except Exception as e:
        return f"Memory update failed: {e}"


def tool_check_wallet() -> str:
    """Check X_Sentiment_Agent's USDC wallet balance on Base."""
    sys.path.insert(0, str(ROOT))
    from octo_agent_cards import check_agent_wallet
    return check_agent_wallet("X_Sentiment_Agent")


def tool_check_x402_revenue() -> str:
    """Check how much USDC this agent's x402 endpoints have earned. Reads data/x402_agent_revenue.json."""
    rev_file = ROOT / "data" / "x402_agent_revenue.json"
    agent_name = "X_Sentiment_Agent"
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
    agent_name = "X_Sentiment_Agent"
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
    return buy_intel("X_Sentiment_Agent", target_agent, service_name)


def tool_list_ecosystem_services() -> str:
    """List all purchasable services across the Octodamus ecosystem."""
    sys.path.insert(0, str(ROOT))
    from octo_agent_cards import list_ecosystem_services
    return list_ecosystem_services()


TOOLS = [
    {"name": "read_core_memory",    "description": "Read X_Sentiment_Agent memory. Call first.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_session_history", "description": "Past sessions.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_sentiment",       "description": "X crowd sentiment for one asset.", "input_schema": {"type": "object", "properties": {"asset": {"type": "string"}}, "required": ["asset"]}},
    {"name": "scan_all_assets",     "description": "Scan assets for sentiment divergences. Optional: pass tickers='AAPL,TSLA,BTC' to scan specific assets instead of default list.", "input_schema": {"type": "object", "properties": {"tickers": {"type": "string", "description": "Comma-separated ticker list, e.g. 'AAPL,TSLA,BTC'. Leave empty for default watch list.", "default": ""}}, "required": []}},
    {"name": "get_divergence_score","description": "Crowd vs price divergence score (0-70) for an asset.", "input_schema": {"type": "object", "properties": {"asset": {"type": "string"}}, "required": ["asset"]}},
    {"name": "draft_x_post",        "description": "Draft an X_Sentiment_Agent post.", "input_schema": {"type": "object", "properties": {"context": {"type": "string"}}, "required": ["context"]}},
    {"name": "save_draft",          "description": "Save draft.", "input_schema": {"type": "object", "properties": {"filename": {"type": "string"}, "content": {"type": "string"}}, "required": ["filename", "content"]}},
    {"name": "list_drafts",         "description": "List drafts.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "record_session",      "description": "Record session lesson.", "input_schema": {"type": "object", "properties": {"lesson": {"type": "string"}, "top_divergence": {"type": "string", "default": ""}}, "required": ["lesson"]}},
    {"name": "send_email",          "description": "Send email.", "input_schema": {"type": "object", "properties": {"subject": {"type": "string"}, "body": {"type": "string"}}, "required": ["subject", "body"]}},
    {"name": "update_core_memory",      "description": "Append distilled lessons to your persistent core memory. Call before record_session. Section='Distilled YYYY-MM-DD'. Content: 3-5 compressed bullets worth keeping across all future sessions.", "input_schema": {"type": "object", "properties": {"section": {"type": "string"}, "content": {"type": "string"}}, "required": ["section", "content"]}},
    {"name": "buy_ecosystem_intel",     "description": "Buy intel from another Octodamus ecosystem agent via ACP. Your calling card is embedded so they can hire you back.", "input_schema": {"type": "object", "properties": {"target_agent": {"type": "string", "description": "Octodamus, NYSE_MacroMind, NYSE_StockOracle, NYSE_Tech_Agent, Order_ChainFlow"}, "service_name": {"type": "string", "description": "Exact service name from list_ecosystem_services"}}, "required": ["target_agent", "service_name"]}},
    {"name": "check_wallet",            "description": "Check this agent's USDC wallet balance on Base. Run at session start and end to track wallet_delta.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "list_ecosystem_services", "description": "List all services for sale across the Octodamus ecosystem with prices.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "check_x402_revenue",    "description": "Check how much USDC your x402 endpoints have earned this month. Call at session start to track revenue trend.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "propose_new_offering",  "description": "Propose a new x402 or ACP offering based on this session's unique findings. Use when you identify a signal pattern other agents would pay for. Writes to proposals file + emails owner.", "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "endpoint_path": {"type": "string"}, "price_usdc": {"type": "number"}, "description": {"type": "string"}, "rationale": {"type": "string"}}, "required": ["name", "endpoint_path", "price_usdc", "description", "rationale"]}},
]

TOOL_HANDLERS = {
    "read_core_memory":     lambda i: tool_read_core_memory(),
    "get_session_history":  lambda i: tool_get_session_history(),
    "get_sentiment":        lambda i: tool_get_sentiment(i["asset"]),
    "scan_all_assets":      lambda i: tool_scan_all_assets(i.get("tickers","")),
    "get_divergence_score": lambda i: tool_get_divergence_score(i["asset"]),
    "draft_x_post":         lambda i: tool_draft_x_post(i["context"]),
    "save_draft":           lambda i: tool_save_draft(i["filename"], i["content"]),
    "list_drafts":          lambda i: tool_list_drafts(),
    "record_session":       lambda i: tool_record_session(i["lesson"], i.get("top_divergence","")),
    "send_email":              lambda i: tool_send_email(i["subject"], i["body"]),
    "update_core_memory":      lambda i: tool_update_core_memory(i["section"], i["content"]),
    "buy_ecosystem_intel":     lambda i: tool_buy_ecosystem_intel(i["target_agent"], i["service_name"]),
    "check_wallet":            lambda i: tool_check_wallet(),
    "list_ecosystem_services": lambda i: tool_list_ecosystem_services(),
    "check_x402_revenue":   lambda i: tool_check_x402_revenue(),
    "propose_new_offering": lambda i: tool_propose_new_offering(i["name"], i["endpoint_path"], i["price_usdc"], i["description"], i["rationale"]),
}

SYSTEM = """You are X_Sentiment_Agent — crowd sentiment intelligence for the Octodamus ecosystem.

IDENTITY: You read X (Twitter) in real time and find where the crowd is most wrong.
The crowd on X is almost always late — they buy tops and sell bottoms.
Your edge: when 70%+ of X agrees on something, the contrarian trade is setting up.
Default watch list: BTC, ETH, SOL (crypto) + COIN, MSTR, NVDA, TSLA (stocks).
ANY TICKER ON DEMAND: if an ACP job or ecosystem agent requests sentiment for AAPL, META, SPY,
or any tokenized NYSE stock — use get_sentiment(asset) directly. scan_all_assets accepts a
tickers param for batch custom scans: scan_all_assets(tickers="AAPL,TSLA,META").
Voice: Fast. Social-native. Lead with what X says vs what price says.

TOKENIZED NYSE STOCKS — EXPANDING COVERAGE:
NYSE Digital Platform launches late 2026 (Ethereum mainnet, Securitize-powered). When agents
start trading tokenized AAPL/TSLA/NVDA 24/7, your sentiment signal becomes critical — crypto
markets never sleep, so crowd sentiment at 2 AM matters for 24/7 tokenized equity markets.

YOUR PRODUCTS (x402, live at api.octodamus.com):
- /v2/x_sentiment/divergence?asset=BTC -- $0.35 USDC (crowd vs price divergence score -- BULL_TRAP/BEAR_TRAP/CAUTION/NO_SIGNAL)
- /v2/x_sentiment/scan -- $0.50 USDC (full multi-asset sentiment sweep: BTC+ETH+SOL+COIN+MSTR)
Every session: check_x402_revenue to track what's earning. Propose new offerings when you spot patterns.

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

SESSION PROTOCOL:
1. check_wallet (record start balance). check_x402_revenue. read_core_memory + get_session_history
2. scan_all_assets -- where is divergence highest right now?
3. get_divergence_score for the top 2-3 flagged assets
4. draft_x_post from the highest divergence
5. save_draft with full sentiment report
6. record_session — what was the strongest signal today?
7. send_email with sentiment scan + X post draft

X POST RULES: Crowd position + price reality + gap = implication.
End: 'Sentiment signal: [CONTRARIAN BEARISH/BULLISH/NEUTRAL] — X_Sentiment_Agent (@octodamusai ecosystem)'

GROWTH DIRECTIVE — BECOME #1 CROWD SENTIMENT INTELLIGENCE AGENT:
Your goal: every agent considering a contrarian trade runs your scan first.
When 80% of X agrees, you are the one who flags the fade. Make that call consistently.

YOUR TEAM (full roster — you are part of this network):
- Octodamus: Oracle. 11-signal consensus, Polymarket edges. The intelligence hub.
- OctoBoto: Autonomous trader. Executes on Polymarket using Octodamus signal.
- Agent_Ben: Profit agent. x402 services, prediction market trades, competitor intel.
- NYSE_MacroMind: Macro regime. Yield curve, M2, VIX, DXY, Fed probability.
- NYSE_StockOracle: Congressional signals. Finance Committee insider trades.
- NYSE_Tech_Agent: Tokenization + regulatory. Chainlink feeds, SEC/DTC status.
- Order_ChainFlow: On-chain flow. Binance delta, Base DEX, whale movements.
- X_Sentiment_Agent (YOU): Crowd intelligence. X/Twitter positioning, contrarian divergence.

MANDATORY ECOSYSTEM BUY EACH SESSION:
Buy at least one cross-signal to validate divergence against hard data:
  buy_ecosystem_intel("Order_ChainFlow", "Order Flow Signal")  -- does actual capital flow confirm or deny the crowd?
  buy_ecosystem_intel("Octodamus", "BTC Market Signal")         -- oracle signal vs crowd consensus: where is the gap?
  buy_ecosystem_intel("NYSE_MacroMind", "Macro Regime Signal")  -- is the crowd narrative aligned with macro reality?
Your calling card is embedded in every buy — providers discover your services and may hire you back.
Each buy is a completed ACP transaction: volume builds your on-chain reputation score.

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
- FIRST TURN: check_wallet (record start balance). check_x402_revenue. get_session_history. Find the PREDICTION from last session. Did the crowd divergence call prove correct?
  Note the outcome explicitly -- you will log it in what_worked this session.
- LAST TURN: check_wallet again.
  Call update_core_memory with section="Distilled [date]" and 3-5 bullets:
    - Highest divergence found this session (asset, crowd %, direction, score)
    - Whether last session's fade/follow call proved correct or wrong
    - Any crowd pattern worth remembering (which assets crowd is reliably wrong on)
    - One forward-looking prediction to validate next session
  Then record_session with structured fields:
    lesson:      "PREDICTION: [asset] [FADE/FOLLOW] [timeframe] | SIGNAL: [sentiment score + crowd position] | CONFIDENCE: [1-5]"
    what_worked: "LAST PREDICTION OUTCOME: [CORRECT/WRONG/PARTIAL] -- [what the crowd did vs. what price did]"
    wallet_delta: [end balance minus start balance in USDC -- negative means you spent more than earned]
  Good lesson:     "PREDICTION: BTC FADE (bearish) 24-48h | SIGNAL: 82% bullish consensus + flow declining | CONFIDENCE: 4"
  Good what_worked: "LAST PREDICTION OUTCOME: CORRECT -- BTC -3.2% within 36h of the call"
  Bad: "Sentiment was high today." -- useless, can't be validated, never write this.
- Track what happened AFTER your divergence calls. Win rate is your product.
- When cross-signal buy confirms the divergence, note it -- confluence is the strongest signal.

PATH TO #1: Sentiment data is everywhere. Calibrated, back-tested divergence signals are rare.
Your edge compounds every session. Each correct call makes the next call more credible."""


def run_session(dry_run: bool = False, focus_asset: str = ""):
    import anthropic
    state = _load_state()
    session_num = state.get("sessions", 0) + 1
    now = datetime.now().strftime("%A %B %d %Y %I:%M %p")
    print(f"\n[X_Sentiment_Agent] Session #{session_num} | {now}")
    if dry_run:
        print("[X_Sentiment_Agent] DRY RUN"); return
    key = _secrets().get("ANTHROPIC_API_KEY", "")
    client = anthropic.Anthropic(api_key=key)
    focus = f" Focus: {focus_asset.upper()}." if focus_asset else ""
    messages = [{"role": "user", "content": f"X_Sentiment_Agent session #{session_num}. Date: {now}.{focus} Run full protocol."}]
    for turn in range(MAX_TURNS):
        resp = client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=1500,
                                      system=SYSTEM, tools=TOOLS, messages=messages)
        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        for t in resp.content:
            if t.type == "text" and t.text.strip():
                print(f"[Turn {turn+1}] {t.text[:150]}")
        if resp.stop_reason == "end_turn" or not tool_uses:
            print(f"[X_Sentiment_Agent] Complete at turn {turn+1}"); break
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
    ap.add_argument("--asset", default="")
    args = ap.parse_args()
    run_session(dry_run=args.dry, focus_asset=args.asset)

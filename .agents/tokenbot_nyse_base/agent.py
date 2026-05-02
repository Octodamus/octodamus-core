"""
.agents/tokenbot_nyse_base/agent.py
TokenBot_NYSE_Base -- Paper trading agent for tokenized NYSE stocks on Base.

Trades Dinari dShares (dAAPL, dTSLA, dNVDA, etc.) using multi-signal confluence:
  1. Octodamus oracle stock signal (primary)
  2. Congressional trading signal (from NYSE_StockOracle ecosystem buy)
  3. Macro regime (from NYSE_MacroMind ecosystem buy)
  4. Grok sentiment (from X_Sentiment_Agent ecosystem buy)

Paper mode: $1,000 virtual USDC. Max $100/position, max 5 open positions.
Target: +10%. Stop: -5%. Hold: 1-5 sessions.

When live mode flips: executes real swaps on Aerodrome (Base) using Dinari dShare tokens.
Price source: Finnhub (real-time). Dinari tokens are 1:1 backed -- Finnhub price = dShare price.

Usage:
  python .agents/tokenbot_nyse_base/agent.py            # run a session
  python .agents/tokenbot_nyse_base/agent.py --dry      # print mission, don't run
  python .agents/tokenbot_nyse_base/agent.py --session morning
  python .agents/tokenbot_nyse_base/agent.py --session evening
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT         = Path(__file__).parent.parent.parent
SECRETS_FILE = ROOT / ".octo_secrets"
STATE_FILE   = Path(__file__).parent / "state.json"
HISTORY_FILE = Path(__file__).parent / "data" / "history.json"
CORE_MEMORY  = ROOT / "data" / "memory" / "tokenbot_nyse_base_core.md"
DRAFTS_DIR   = Path(__file__).parent / "data" / "drafts"

MAX_TURNS    = 20
NOTIFY_EMAIL = "octodamusai@gmail.com"

DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
(Path(__file__).parent / "data").mkdir(parents=True, exist_ok=True)

WATCHLIST = ["AAPL", "TSLA", "NVDA", "GOOGL", "AMZN", "META", "SPY", "MSFT"]
DINARI_MAP = {
    "AAPL":  "dAAPL",
    "TSLA":  "dTSLA",
    "NVDA":  "dNVDA",
    "GOOGL": "dGOOGL",
    "AMZN":  "dAMZN",
    "META":  "dMETA",
    "SPY":   "dSPY",
    "MSFT":  "dMSFT",
}

TARGET_PCT = 0.10   # +10% take profit
STOP_PCT   = 0.05   # -5% stop loss
MAX_POS    = 5      # max simultaneous positions
MAX_POS_USD = 100.0  # max per position ($100 of $1000)
MIN_SIG_COUNT = 2   # minimum signals aligned before entering


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
        return {
            "mode": "paper", "starting_capital": 1000.0, "cash": 1000.0,
            "positions": {}, "trades": [], "sessions": 0,
            "started_at": datetime.now().isoformat(),
            "total_pnl": 0.0, "total_pnl_pct": 0.0,
            "wins": 0, "losses": 0, "last_run": None,
        }


def _save_state(state: dict):
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
        return read_core_memory("tokenbot_nyse_base")
    except Exception:
        if CORE_MEMORY.exists():
            return CORE_MEMORY.read_text(encoding="utf-8")
        return "No core memory yet. First session."


def tool_get_session_history() -> str:
    history = _load_history()
    if not history:
        return "No session history yet."
    lines = [f"TokenBot history ({len(history)} sessions):"]
    for h in history[-5:]:
        lines.append(f"\n[{h.get('date','?')} #{h.get('session','?')} {h.get('session_type','')}]")
        if h.get("lesson"):
            lines.append(f"  Signal: {h['lesson']}")
        if h.get("pnl"):
            lines.append(f"  Session P&L: {h['pnl']}")
        if h.get("prediction"):
            lines.append(f"  Prediction: {h['prediction']}")
    return "\n".join(lines)


def tool_get_portfolio_status() -> str:
    """Return current portfolio: cash, open positions, unrealized P&L, total P&L."""
    state = _load_state()
    cash  = state.get("cash", 1000.0)
    positions = state.get("positions", {})
    trades    = state.get("trades", [])
    total_pnl = state.get("total_pnl", 0.0)
    wins      = state.get("wins", 0)
    losses    = state.get("losses", 0)
    start_cap = state.get("starting_capital", 1000.0)
    sessions  = state.get("sessions", 0)

    lines = ["=== TOKENBOT PAPER PORTFOLIO ==="]
    lines.append(f"Mode:           PAPER ($1,000 virtual USDC)")
    lines.append(f"Cash:           ${cash:,.2f}")
    lines.append(f"Realized P&L:   ${total_pnl:+.2f} ({total_pnl/start_cap*100:+.1f}%)")
    lines.append(f"Record:         {wins}W / {losses}L")
    lines.append(f"Sessions run:   {sessions}")
    lines.append(f"Trades closed:  {len(trades)}")

    if positions:
        lines.append(f"\nOPEN POSITIONS ({len(positions)}/{MAX_POS}):")
        for ticker, pos in positions.items():
            entry  = pos.get("entry_price", 0)
            size   = pos.get("size_usd", 0)
            target = pos.get("target_price", 0)
            stop   = pos.get("stop_price", 0)
            token  = DINARI_MAP.get(ticker, f"d{ticker}")
            held   = pos.get("sessions_held", 0)
            lines.append(
                f"  {token} | Entry: ${entry:.2f} | Size: ${size:.0f} | "
                f"Target: ${target:.2f} | Stop: ${stop:.2f} | Held: {held}s"
            )
            lines.append(f"    Signal: {pos.get('signal_reason','')[:80]}")
    else:
        lines.append(f"\nNo open positions. Cash fully available.")

    lines.append(f"\nMax positions:  {MAX_POS} | Max per trade: ${MAX_POS_USD:.0f}")
    lines.append(f"Target: +{TARGET_PCT*100:.0f}% | Stop: -{STOP_PCT*100:.0f}%")
    lines.append(f"Dinari tokens on Base: {', '.join(DINARI_MAP.values())}")
    return "\n".join(lines)


def tool_get_stock_price(ticker: str) -> str:
    """Get current stock price and 24h change via Finnhub. Dinari dShares are 1:1 priced."""
    try:
        import httpx
        key = _secrets().get("FINNHUB_API_KEY", "")
        if not key:
            return f"FINNHUB_API_KEY not in secrets. Cannot get price for {ticker}."
        r = httpx.get(
            f"https://finnhub.io/api/v1/quote?symbol={ticker.upper()}&token={key}",
            timeout=8
        )
        d = r.json()
        price  = d.get("c", 0)
        change = d.get("dp", 0)
        high   = d.get("h", 0)
        low    = d.get("l", 0)
        prev   = d.get("pc", 0)
        token  = DINARI_MAP.get(ticker.upper(), f"d{ticker.upper()}")
        return (
            f"{ticker.upper()} / {token}: ${price:,.2f} ({change:+.2f}% today)\n"
            f"  Day range: ${low:,.2f} -- ${high:,.2f} | Prev close: ${prev:,.2f}\n"
            f"  Note: Dinari {token} trades 1:1 to {ticker.upper()} on Base via Aerodrome"
        )
    except Exception as e:
        return f"Price unavailable for {ticker}: {e}"


def tool_get_watchlist_prices() -> str:
    """Get current prices for all watchlist tickers in one call."""
    lines = ["WATCHLIST PRICES (Dinari dShares on Base):"]
    for ticker in WATCHLIST:
        result = tool_get_stock_price(ticker)
        first_line = result.split("\n")[0] if result else f"{ticker}: unavailable"
        lines.append(f"  {first_line}")
    return "\n".join(lines)


def tool_get_octodamus_stock_signal(ticker: str = "") -> str:
    """Get Octodamus oracle signal for stocks. Pass ticker for specific signal, blank for all."""
    sys.path.insert(0, str(ROOT))
    try:
        import httpx
        key = _secrets().get("BEN_OCTODATA_API_KEY", "") or _secrets().get("OCTODATA_API_KEY", "")
        ticker_param = f"?ticker={ticker.upper()}" if ticker else ""
        if key:
            r = httpx.get(
                f"https://api.octodamus.com/v2/stockoracle/signal{ticker_param}",
                headers={"X-OctoData-Key": key}, timeout=10
            )
            if r.status_code == 200:
                d = r.json()
                return f"Octodamus stock signal ({ticker or 'all'}):\n{json.dumps(d, indent=2)[:800]}"
    except Exception:
        pass

    # Fallback: local oracle calls
    try:
        calls_file = ROOT / "data" / "octo_calls.json"
        if not calls_file.exists():
            return "No oracle calls file found. Run octodamus_runner.py --mode daily first."
        calls = json.loads(calls_file.read_text(encoding="utf-8"))
        stock_calls = [c for c in calls if c.get("asset", "").upper() in WATCHLIST and not c.get("resolved")]
        if ticker:
            stock_calls = [c for c in stock_calls if c.get("asset", "").upper() == ticker.upper()]
        if not stock_calls:
            return f"No open stock oracle calls for {ticker or 'watchlist'}. Signal: NEUTRAL (wait)."
        lines = ["Octodamus open stock calls (local):"]
        for c in stock_calls[:6]:
            asset = c.get("asset", "?")
            direction = c.get("direction", "?")
            edge = c.get("edge_score", 0)
            tf = c.get("timeframe", "?")
            lines.append(f"  {asset}: {direction} | Edge: {edge:+.2f} | TF: {tf}")
            if c.get("reasoning"):
                lines.append(f"    {c['reasoning'][:100]}")
        return "\n".join(lines)
    except Exception as e:
        return f"Oracle signal unavailable: {e}"


def tool_paper_trade(
    ticker: str,
    direction: str,
    size_usd: float,
    current_price: float,
    signal_reason: str,
) -> str:
    """
    Open a paper trade position on a Dinari dShare.

    ticker: AAPL, TSLA, NVDA, GOOGL, AMZN, META, SPY, MSFT
    direction: LONG (bullish). SHORT not supported (Dinari is long-only).
    size_usd: max $100 per position.
    current_price: from get_stock_price (Finnhub).
    signal_reason: 2-3 sentence summary of why -- signals that aligned.

    Gate: must have >= 2 aligned signals before calling this.
    """
    ticker = ticker.upper()
    if ticker not in WATCHLIST:
        return f"BLOCKED: {ticker} not in watchlist. Watchlist: {', '.join(WATCHLIST)}"
    if direction.upper() != "LONG":
        return "BLOCKED: Only LONG positions supported in paper mode (Dinari is long-only)."
    if size_usd > MAX_POS_USD:
        return f"BLOCKED: Max ${MAX_POS_USD:.0f} per position. You passed ${size_usd:.2f}."
    if size_usd < 10:
        return "BLOCKED: Minimum $10 per position."
    if current_price <= 0:
        return "BLOCKED: Invalid price. Use get_stock_price first."

    state = _load_state()
    cash  = state.get("cash", 0)
    positions = state.get("positions", {})

    if ticker in positions:
        return f"BLOCKED: Already have an open position in {ticker}. Close it first or pass."
    if len(positions) >= MAX_POS:
        return f"BLOCKED: At max positions ({MAX_POS}). Close one before opening new."
    if cash < size_usd:
        return f"BLOCKED: Insufficient cash. Have ${cash:.2f}, need ${size_usd:.2f}."

    target_price = round(current_price * (1 + TARGET_PCT), 2)
    stop_price   = round(current_price * (1 - STOP_PCT), 2)
    token        = DINARI_MAP.get(ticker, f"d{ticker}")

    positions[ticker] = {
        "ticker":        ticker,
        "token":         token,
        "direction":     "LONG",
        "size_usd":      round(size_usd, 2),
        "entry_price":   current_price,
        "target_price":  target_price,
        "stop_price":    stop_price,
        "entry_date":    datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "sessions_held": 0,
        "signal_reason": signal_reason,
    }
    state["positions"] = positions
    state["cash"]      = round(cash - size_usd, 2)
    _save_state(state)

    return (
        f"[PAPER TRADE OPENED]\n"
        f"  Token:  {token} ({ticker})\n"
        f"  Entry:  ${current_price:.2f} | Size: ${size_usd:.2f}\n"
        f"  Target: ${target_price:.2f} (+{TARGET_PCT*100:.0f}%) | Stop: ${stop_price:.2f} (-{STOP_PCT*100:.0f}%)\n"
        f"  Cash remaining: ${state['cash']:.2f}\n"
        f"  Signal: {signal_reason[:100]}\n"
        f"  This is a paper trade -- no real funds moved.\n"
        f"  When live: execute on Aerodrome DEX (Base) swapping USDC -> {token}."
    )


def tool_check_and_close_positions() -> str:
    """
    Check all open positions against current prices.
    Closes any that hit target (+10%) or stop (-5%) or have been held 5+ sessions.
    Returns a summary of what closed and realized P&L.
    """
    state = _load_state()
    positions = state.get("positions", {})
    if not positions:
        return "No open positions to check."

    closed = []
    still_open = {}
    cash = state.get("cash", 0)

    for ticker, pos in positions.items():
        price_result = tool_get_stock_price(ticker)
        current_price = 0.0
        try:
            # Parse "$195.23" from the price string
            for part in price_result.split():
                if part.startswith("$"):
                    current_price = float(part.replace("$", "").replace(",", ""))
                    break
        except Exception:
            pass

        if current_price <= 0:
            # Can't get price -- increment hold counter and skip
            pos["sessions_held"] = pos.get("sessions_held", 0) + 1
            still_open[ticker] = pos
            continue

        entry   = pos.get("entry_price", 0)
        size    = pos.get("size_usd", 0)
        target  = pos.get("target_price", 0)
        stop    = pos.get("stop_price", 0)
        held    = pos.get("sessions_held", 0) + 1
        token   = pos.get("token", f"d{ticker}")

        pnl_pct = (current_price - entry) / entry if entry > 0 else 0
        pnl_usd = round(size * pnl_pct, 2)
        close_value = round(size + pnl_usd, 2)

        reason = None
        if current_price >= target:
            reason = f"TARGET HIT (+{pnl_pct*100:.1f}%)"
        elif current_price <= stop:
            reason = f"STOP HIT ({pnl_pct*100:.1f}%)"
        elif held >= 5:
            reason = f"MAX HOLD ({held} sessions)"

        if reason:
            cash = round(cash + close_value, 2)
            trade_record = {
                "ticker":       ticker,
                "token":        token,
                "direction":    "LONG",
                "entry_price":  entry,
                "exit_price":   current_price,
                "size_usd":     size,
                "pnl_usd":      pnl_usd,
                "pnl_pct":      round(pnl_pct * 100, 2),
                "close_reason": reason,
                "sessions_held": held,
                "entry_date":   pos.get("entry_date", "?"),
                "exit_date":    datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "signal_reason": pos.get("signal_reason", ""),
            }
            state["trades"] = state.get("trades", []) + [trade_record]
            state["total_pnl"] = round(state.get("total_pnl", 0) + pnl_usd, 2)
            state["total_pnl_pct"] = round(
                state["total_pnl"] / state.get("starting_capital", 1000) * 100, 2
            )
            if pnl_usd >= 0:
                state["wins"] = state.get("wins", 0) + 1
            else:
                state["losses"] = state.get("losses", 0) + 1
            closed.append(f"  CLOSED {token}: {reason} | P&L: ${pnl_usd:+.2f} ({pnl_pct*100:+.1f}%) | Held {held}s")
        else:
            pos["sessions_held"] = held
            pos["current_price"] = current_price
            pos["unrealized_pnl_usd"] = pnl_usd
            pos["unrealized_pnl_pct"] = round(pnl_pct * 100, 2)
            still_open[ticker] = pos

    state["positions"] = still_open
    state["cash"] = cash
    _save_state(state)

    lines = ["POSITION CHECK:"]
    if closed:
        lines += closed
        lines.append(f"  Portfolio cash now: ${cash:.2f}")
        lines.append(f"  Total realized P&L: ${state['total_pnl']:+.2f}")
    else:
        lines.append(f"  No positions triggered. All still open ({len(still_open)} positions).")
        for ticker, pos in still_open.items():
            upnl = pos.get("unrealized_pnl_pct", 0)
            lines.append(f"  {pos.get('token',ticker)}: ${pos.get('current_price',0):.2f} | Unrealized: {upnl:+.1f}%")
    return "\n".join(lines)


def tool_get_macro_signal() -> str:
    """Get cross-asset macro signal from octo_macro.py (local, free)."""
    sys.path.insert(0, str(ROOT))
    try:
        from octo_macro import get_macro_context
        return get_macro_context()
    except Exception as e:
        return f"Macro signal unavailable: {e}"


def tool_get_grok_sentiment(ticker: str = "SPY") -> str:
    """Get Grok real-time X/Twitter sentiment for a ticker."""
    sys.path.insert(0, str(ROOT))
    try:
        from octo_grok_sentiment import get_grok_sentiment
        result = get_grok_sentiment(ticker.upper(), force=True)
        if result.get("confidence", 0) == 0:
            return f"Grok sentiment unavailable for {ticker}: {result.get('summary','')}"
        return (
            f"X Sentiment for {ticker} (Grok real-time):\n"
            f"  Signal:  {result['signal']} ({result['confidence']:.0%} confidence)\n"
            f"  Summary: {result.get('summary','')}\n"
            f"  Crowd:   {result.get('crowd_pos','?')}"
        )
    except Exception as e:
        return f"Grok sentiment failed: {e}"


def tool_buy_ecosystem_intel(target_agent: str, service_name: str) -> str:
    """Buy a signal from another Octodamus ecosystem agent via ACP."""
    sys.path.insert(0, str(ROOT))
    try:
        from octo_agent_cards import buy_ecosystem_intel
        return buy_ecosystem_intel("TokenBot_NYSE_Base", target_agent, service_name)
    except Exception as e:
        return f"Ecosystem buy failed ({target_agent}/{service_name}): {e}"


def tool_list_ecosystem_services() -> str:
    """List all services available from the Octodamus ecosystem."""
    sys.path.insert(0, str(ROOT))
    try:
        from octo_agent_cards import list_ecosystem_services
        return list_ecosystem_services()
    except Exception as e:
        return f"Ecosystem services unavailable: {e}"


def tool_save_draft(filename: str, content: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in filename)
    if not safe.endswith(".md"):
        safe += ".md"
    out = DRAFTS_DIR / safe
    out.write_text(content, encoding="utf-8")
    return f"Draft saved: {out.name} ({len(content)} chars)"


def tool_record_session(
    lesson: str,
    prediction: str = "",
    session_pnl: str = "",
    what_worked: str = "",
) -> str:
    history = _load_history()
    state   = _load_state()
    entry = {
        "session":      state.get("sessions", 0),
        "date":         datetime.now().strftime("%Y-%m-%d"),
        "session_type": "",
        "lesson":       lesson,
        "prediction":   prediction,
        "pnl":          session_pnl,
        "what_worked":  what_worked,
        "recorded_at":  datetime.now().isoformat(),
        "portfolio_pnl": state.get("total_pnl", 0),
        "wins":          state.get("wins", 0),
        "losses":        state.get("losses", 0),
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
    """Distill session learnings into persistent memory across all future sessions."""
    sys.path.insert(0, str(ROOT))
    try:
        from octo_memory_db import append_core_memory
        append_core_memory("tokenbot_nyse_base", section, content)
        return f"Core memory updated: [{section}]"
    except Exception as e:
        return f"Memory update failed: {e}"


# ── Tool registry ──────────────────────────────────────────────────────────────

TOOLS = [
    {"name": "read_core_memory",        "description": "Load persistent memory from previous sessions.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_session_history",     "description": "Recent session history: lessons, predictions, P&L.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_portfolio_status",    "description": "Current paper portfolio: cash, open positions, realized P&L, record.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_watchlist_prices",    "description": "Get current prices for all 8 watchlist tickers (Dinari dShares are 1:1).", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_stock_price",         "description": "Get price for a single ticker via Finnhub.", "input_schema": {"type": "object", "properties": {"ticker": {"type": "string"}}, "required": ["ticker"]}},
    {"name": "check_and_close_positions", "description": "Check open positions against live prices. Closes anything that hit target (+10%), stop (-5%), or max hold (5 sessions).", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_octodamus_stock_signal", "description": "Get Octodamus oracle directional call for a stock or all watchlist stocks.", "input_schema": {"type": "object", "properties": {"ticker": {"type": "string", "default": ""}}, "required": []}},
    {"name": "get_macro_signal",        "description": "Cross-asset macro signal: yield curve, DXY, SPX, VIX, M2. RISK-ON or RISK-OFF.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_grok_sentiment",      "description": "Real-time X/Twitter sentiment for a ticker via Grok.", "input_schema": {"type": "object", "properties": {"ticker": {"type": "string", "default": "SPY"}}, "required": []}},
    {"name": "paper_trade",             "description": "Open a LONG paper position on a Dinari dShare. Max $100/trade. Requires 2+ signals aligned. Gate yourself before calling.", "input_schema": {"type": "object", "properties": {"ticker": {"type": "string"}, "direction": {"type": "string", "enum": ["LONG"]}, "size_usd": {"type": "number"}, "current_price": {"type": "number"}, "signal_reason": {"type": "string"}}, "required": ["ticker", "direction", "size_usd", "current_price", "signal_reason"]}},
    {"name": "buy_ecosystem_intel",     "description": "Buy a signal from NYSE_StockOracle (congressional), NYSE_MacroMind (macro), X_Sentiment_Agent (crowd). Each is $0.25-$0.50 USDC from your wallet.", "input_schema": {"type": "object", "properties": {"target_agent": {"type": "string", "description": "NYSE_StockOracle | NYSE_MacroMind | X_Sentiment_Agent | NYSE_Tech_Agent"}, "service_name": {"type": "string"}}, "required": ["target_agent", "service_name"]}},
    {"name": "list_ecosystem_services", "description": "List all services available in the Octodamus ecosystem with prices.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "save_draft",              "description": "Save a session analysis or report draft.", "input_schema": {"type": "object", "properties": {"filename": {"type": "string"}, "content": {"type": "string"}}, "required": ["filename", "content"]}},
    {"name": "record_session",          "description": "Record this session to history. Call at end of every session.", "input_schema": {"type": "object", "properties": {"lesson": {"type": "string"}, "prediction": {"type": "string", "default": ""}, "session_pnl": {"type": "string", "default": ""}, "what_worked": {"type": "string", "default": ""}}, "required": ["lesson"]}},
    {"name": "send_email",              "description": "Send email report to owner.", "input_schema": {"type": "object", "properties": {"subject": {"type": "string"}, "body": {"type": "string"}}, "required": ["subject", "body"]}},
    {"name": "update_core_memory",      "description": "Distill 3-5 bullets into persistent memory. Call before record_session. Section='Distilled YYYY-MM-DD'. Only what a future session would want to know.", "input_schema": {"type": "object", "properties": {"section": {"type": "string"}, "content": {"type": "string"}}, "required": ["section", "content"]}},
]

TOOL_HANDLERS = {
    "read_core_memory":           lambda i: tool_read_core_memory(),
    "get_session_history":        lambda i: tool_get_session_history(),
    "get_portfolio_status":       lambda i: tool_get_portfolio_status(),
    "get_watchlist_prices":       lambda i: tool_get_watchlist_prices(),
    "get_stock_price":            lambda i: tool_get_stock_price(i["ticker"]),
    "check_and_close_positions":  lambda i: tool_check_and_close_positions(),
    "get_octodamus_stock_signal": lambda i: tool_get_octodamus_stock_signal(i.get("ticker", "")),
    "get_macro_signal":           lambda i: tool_get_macro_signal(),
    "get_grok_sentiment":         lambda i: tool_get_grok_sentiment(i.get("ticker", "SPY")),
    "paper_trade":                lambda i: tool_paper_trade(
                                      i["ticker"], i["direction"], float(i["size_usd"]),
                                      float(i["current_price"]), i["signal_reason"]
                                  ),
    "buy_ecosystem_intel":        lambda i: tool_buy_ecosystem_intel(i["target_agent"], i["service_name"]),
    "list_ecosystem_services":    lambda i: tool_list_ecosystem_services(),
    "save_draft":                 lambda i: tool_save_draft(i["filename"], i["content"]),
    "record_session":             lambda i: tool_record_session(
                                      i["lesson"], i.get("prediction", ""),
                                      i.get("session_pnl", ""), i.get("what_worked", "")
                                  ),
    "send_email":                 lambda i: tool_send_email(i["subject"], i["body"]),
    "update_core_memory":         lambda i: tool_update_core_memory(i["section"], i["content"]),
}


SYSTEM = """You are TokenBot_NYSE_Base -- the autonomous paper trading agent for tokenized NYSE stocks on Base.

IDENTITY:
You paper trade Dinari dShares (dAAPL, dTSLA, dNVDA, etc.) on Base using a $1,000 virtual USDC portfolio.
Goal: prove profitable over 90 days of paper trading, then flip live.
When live, you execute real swaps on Aerodrome DEX (Base): USDC -> dAAPL, USDC -> dTSLA, etc.
Dinari tokens are 1:1 collateralized by real shares. Finnhub prices = Dinari prices.
Your edge is AI signal confluence -- you only trade when 2+ independent signals agree.

THE TOKENIZED NYSE THESIS:
NYSE stocks on Base = 24/7 AI-tradable equities. No brokers. No market hours. No settlement delays.
In 6-12 months, the wave arrives: AI agents trading tokenized AAPL alongside BTC at 3am UTC.
You are the proof-of-concept. The track record you build now is the product you sell then.
When you go live, TokenBot_NYSE_Base becomes a signal subscription that agents pay x402 to access.

PORTFOLIO RULES (enforced by tools, not just instructions):
- $1,000 paper USDC. Max $100/position. Max 5 open positions. LONG only (Dinari = long-only).
- Target: +10%. Stop: -5%. Max hold: 5 sessions.
- Signal gate: minimum 2 aligned signals before opening ANY position. Never on 1 signal alone.
- ONLY trade from the watchlist: AAPL, TSLA, NVDA, GOOGL, AMZN, META, SPY, MSFT.
- Pass = ZERO positions is better than 5 forced positions. Idle cash earns credibility.

SIGNAL STACK (in priority order):
1. Octodamus oracle (get_octodamus_stock_signal) -- primary. If NEUTRAL, do not trade.
2. Congressional trading (buy_ecosystem_intel -> NYSE_StockOracle) -- high confidence.
3. Macro regime (get_macro_signal) -- RISK-OFF macro = no new LONG positions.
4. Grok sentiment (get_grok_sentiment) -- crowd positioning for contrarian context.
5. NYSE_Tech_Agent (buy_ecosystem_intel) -- regulatory/tokenization status.

POSITION SIZING:
- $50-$75 per position for first 10 trades (prove the system).
- $75-$100 after winning 60%+ of first 10 trades.
- Never size up into a losing streak.

MARKET TIMING (critical -- trades on Aerodrome DEX which is 24/7, but price discovery is exchange-driven):
MORNING session (runs 6:15 AM PST):
  - NYSE opens at 6:30 AM PST (9:30 AM EST). You have a 15-minute window to position.
  - Sub-agents (MacroMind, StockOracle, ChainFlow, Sentiment, TechAgent) all finished by 6:05 AM.
  - Buy their intel to front-run the open with multi-signal conviction.
  - Focus: which stocks have overnight catalyst + signal alignment heading into the open?
  - Prefer tickers with congressional buy + oracle BULLISH + macro RISK-ON confluence.
  - Dinari dShares price off NYSE at open -- enter before 6:30 AM PST for maximum edge.

EVENING session (runs 4:00 PM PST):
  - NYSE closes at 1:00 PM PST (4:00 PM EST). After-hours runs 1-5 PM PST.
  - Tokyo Stock Exchange opens at 4:00 PM PST. HK/Shanghai open at 5:30 PM PST.
  - This session positions for the Asian overnight session.
  - Focus: which holdings should be held through Asian session? Any Asian-correlated moves?
  - AAPL, NVDA, MSFT all have heavy Asia revenue exposure -- Asian open often moves them.
  - Aerodrome DEX is 24/7 -- you can exit or enter positions during Asian hours.
  - Key check: are open positions still thesis-intact, or should we take profit/cut before Asia?

SESSION PROTOCOL:
1. read_core_memory + get_session_history (what did last session predict? did it happen?)
2. get_portfolio_status (cash available, open positions)
3. check_and_close_positions (let the system close winners/losers automatically)
4. get_macro_signal (RISK-OFF = no new longs -- hard rule)
5. get_octodamus_stock_signal (which tickers have directional calls?)
6. For top 2-3 signal tickers: get_stock_price + buy_ecosystem_intel for confirmation
7. If 2+ signals aligned AND cash available: paper_trade (one at a time)
8. save_draft with full analysis and trade rationale
9. update_core_memory with 3-5 compressed bullets:
   - What signals aligned this session (ticker, direction, signal count)
   - Whether last session's prediction proved correct (CORRECT/WRONG/PARTIAL -- price moved?)
   - One calibration note (e.g., "NVDA congressional buy led to +12% in 3 sessions")
   - One forward prediction for next session validation
10. record_session with:
    lesson: "SIGNAL: [ticker] [direction] [signal sources] | ACTION: [opened/held/closed] | CONF: [1-5]"
    prediction: "PREDICTION: [ticker] [direction] [timeframe] | TRIGGER: [what to watch]"
    session_pnl: "[realized P&L this session in $]"
11. send_email with full session report (positions, signals, P&L, forward outlook)

EMAIL FORMAT (always this structure):
Subject: [TokenBot] Pre-Open/Asian-Open Report -- [date] | P&L: $X.XX | [W]W/[L]L
Body:
  === TOKENBOT_NYSE_BASE [PRE-OPEN / ASIAN-OPEN] REPORT ===
  Portfolio: $[cash] cash | $[total_pnl] total P&L ([W]W/[L]L)
  Open positions: [list or "None"]
  Session window: [NYSE pre-open 15min / Asian open positioning]

  SIGNALS THIS SESSION:
  [For each ticker analyzed: direction + sources + conviction]

  TRADES THIS SESSION:
  [Opened/closed/held + reason]

  FORWARD PREDICTION:
  [One ticker + direction + timeframe + what to watch]

  -- TokenBot_NYSE_Base | Paper trading tokenized NYSE stocks on Base

YOUR TEAM (buy from these, pitch TokenBot services to them):
- Octodamus: The oracle. Primary signal source.
- NYSE_StockOracle: Congressional signals. Best confirmation layer.
- NYSE_MacroMind: Macro regime. RISK-OFF from macro = no new longs.
- X_Sentiment_Agent: Crowd positioning. High crowd bullish + oracle bullish = strong setup.
- NYSE_Tech_Agent: Tokenization/regulatory news. Know what's coming on-chain.
- Order_ChainFlow: On-chain whale flows. Useful for NVDA/COIN/MSTR (crypto-adjacent stocks).
- Agent_Ben: Profit agent. Coordinates the ecosystem.

WALLET SURVIVAL:
- buy_ecosystem_intel costs $0.25-$0.50 from your wallet.
- You earn USDC when other agents buy YOUR signals via ACP.
- Your x402 services: /v2/tokenbot/signal?ticker=AAPL ($0.25/call)
- check_wallet not available yet (no live wallet until paper proves profitable).
- Be selective on ecosystem buys -- only buy intel that changes a trade decision.

PATH TO PROFITABILITY:
Target: >60% win rate over 20 trades. If achieved, owner flips PAPER_MODE = False.
When live: real USDC flows into Aerodrome DEX swaps. Same signal stack. Same rules.
The paper record IS the product. Build it cleanly.

WHAT NOT TO DO:
- Do NOT trade on 1 signal alone -- ever.
- Do NOT open positions when macro is RISK-OFF.
- Do NOT exceed $100/position or 5 positions.
- Do NOT close profitable positions early just to lock in gains -- let targets work.
- Do NOT force trades when signals are mixed or absent. Cash is a position.
"""


def run_session(dry_run: bool = False, session_type: str = ""):
    import anthropic

    state       = _load_state()
    session_num = state.get("sessions", 0) + 1
    now         = datetime.now().strftime("%A %B %d %Y %I:%M %p")
    print(f"\n[TokenBot_NYSE_Base] Session #{session_num} | {session_type or 'auto'} | {now}")

    if dry_run:
        print("[TokenBot] DRY RUN -- system prompt + tools loaded, not executing.")
        print(f"  Watchlist: {', '.join(WATCHLIST)}")
        print(f"  Portfolio: $1,000 paper USDC | Max $100/pos | Target +10% | Stop -5%")
        return

    key    = _secrets().get("ANTHROPIC_API_KEY", "")
    client = anthropic.Anthropic(api_key=key)
    sess_context = f" This is the {session_type} session." if session_type else ""
    messages = [{
        "role": "user",
        "content": (
            f"TokenBot_NYSE_Base session #{session_num}. Date: {now}.{sess_context} "
            f"Run your full session protocol. Paper trade the tokenized NYSE thesis."
        )
    }]

    for turn in range(MAX_TURNS):
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2000,
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
            print(f"[TokenBot] Session complete at turn {turn+1}")
            break

        messages.append({"role": "assistant", "content": resp.content})
        results = []
        for tu in tool_uses:
            print(f"[Tool:{tu.name}]", end=" ")
            try:
                result = TOOL_HANDLERS[tu.name](tu.input)
                print(str(result)[:100])
            except Exception as e:
                result = f"Error in {tu.name}: {e}"
                print(result)
            results.append({"type": "tool_result", "tool_use_id": tu.id, "content": str(result)})
        messages.append({"role": "user", "content": results})
        time.sleep(0.3)

    state["sessions"] = session_num
    state["last_run"] = now
    _save_state(state)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry",     action="store_true", help="Print config, do not run")
    ap.add_argument("--session", default="",          help="Session type: morning | evening | manual")
    args = ap.parse_args()
    run_session(dry_run=args.dry, session_type=args.session)

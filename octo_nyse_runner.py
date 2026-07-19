"""
octo_nyse_runner.py — NYSE Sub-Agent Pre-Market Runner

Runs at 5:30am PST (1h before NYSE open at 9:30am EST).
Five specialist agents each wake up, analyze their domain, buy intel from
Octodamus, embed their calling card + peer invite, and save a daily brief.

Agents:
  NYSE_MacroMind    — cross-asset macro regime (yield curve, DXY, VIX, M2)
  NYSE_StockOracle  — congressional trading signals + mega-cap stock action
  NYSE_Tech_Agent   — tech sector + tokenized equity regulatory intel
  Order_ChainFlow   — on-chain order flow, whale activity, DEX flows
  NYSE_EarningsEdge — upcoming earnings catalyst: implied move vs historical, estimate revisions

Each agent:
  1. Checks its USDC + ETH balance (survival gate)
  2. Pulls live data for its specialty
  3. Generates a brief with Claude Haiku
  4. Buys intel from Octodamus (with calling card + peer network invite)
  5. Saves daily brief to drafts/ (future ACP provider deliverable source)
  6. Updates state
"""

import json
import logging
import os
import sys
import time
from datetime import datetime, date
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

# ── Logging ────────────────────────────────────────────────────────────────────

LOG_FILE = ROOT / "logs" / f"nyse_agents_{date.today().isoformat()}.log"
LOG_FILE.parent.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [NYSE] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(str(LOG_FILE), encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# ── Chain constants (Base mainnet) ─────────────────────────────────────────────

_BASE_RPC     = "https://mainnet.base.org"
_USDC_ADDRESS = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
_USDC_ABI     = [{"name": "balanceOf", "type": "function", "stateMutability": "view",
                   "inputs": [{"name": "account", "type": "address"}],
                   "outputs": [{"name": "", "type": "uint256"}]}]

# ── State file ─────────────────────────────────────────────────────────────────

STATE_FILE  = ROOT / "data" / "nyse_agent_state.json"
DRAFTS_DIR  = ROOT / ".agents" / "profit-agent" / "drafts"
DRAFTS_DIR.mkdir(parents=True, exist_ok=True)

# ── Secrets ────────────────────────────────────────────────────────────────────

def _secrets() -> dict:
    f = ROOT / ".octo_secrets"
    try:
        raw = json.loads(f.read_text(encoding="utf-8"))
        return raw.get("secrets", raw)
    except Exception:
        return {}

# ── Agent configs ──────────────────────────────────────────────────────────────
# Each agent: specialty domain, what it buys from Octodamus, Claude role prompt,
# peer it invites (by name), and the service it will eventually offer.

TEAM_CHANNEL   = ROOT / "data" / "agent_team_channel.json"

# Maps agent name → octo_memory_db agent key (matches data/memory/{key}_core.md)
_MEMORY_KEYS = {
    "NYSE_MacroMind":     "nyse_macromind",
    "NYSE_StockOracle":   "nyse_stockoracle",
    "NYSE_Tech_Agent":    "nyse_tech_agent",
    "Order_ChainFlow":    "order_chainflow",
    "NYSE_EarningsEdge":  "nyse_earningsedge",
}

AGENT_CONFIGS = {
    "NYSE_MacroMind": {
        "addr_key":   "NYSE_MACROMIND_ADDRESS",
        "specialty":  "cross-asset macro regime",
        "buys_from":  "Octodamus",
        "buy_service":"Agent Market Intel Bundle",   # $2.00 — full macro + signal context
        "peer_invite":"Order_ChainFlow",
        "claude_role": (
            "You are NYSE_MacroMind, an autonomous macro intelligence agent operating on "
            "the Virtuals ACP network. Your specialty: US macro regime analysis — yield "
            "curve inversion, DXY strength, VIX level, M2 liquidity trend, and Fed "
            "probability. Your job: given pre-market data, output a concise RISK-ON / "
            "RISK-OFF / TRANSITION regime verdict with 3 key supporting data points and "
            "a single actionable implication for the NYSE open. Be direct. No fluff."
        ),
        "data_fn":    "macro",
    },
    "NYSE_StockOracle": {
        "addr_key":   "NYSE_STOCKORACLE_ADDRESS",
        "specialty":  "congressional trading signals + mega-cap stock action",
        "buys_from":  "Octodamus",
        "buy_service":"BTC Market Signal",            # $1.00 — macro BTC as market proxy
        "peer_invite":"NYSE_MacroMind",
        "claude_role": (
            "You are NYSE_StockOracle, an autonomous stock intelligence agent on the "
            "Virtuals ACP network. Your specialty: congressional trading signals for "
            "NVDA, TSLA, AAPL, MSFT + insider interpretation. Your job: given the macro "
            "context and any known congressional activity, produce a pre-market stock "
            "brief covering 2-3 tickers with bias (LONG/SHORT/NEUTRAL), confidence, "
            "and the key catalyst to watch at open. If no congressional signal, use "
            "price momentum + options flow context. Be direct. No fluff."
        ),
        "data_fn":    "stocks",
    },
    "NYSE_Tech_Agent": {
        "addr_key":   "NYSE_TECH_ADDRESS",
        "specialty":  "tech sector + tokenized equity regulatory intel",
        "buys_from":  "Octodamus",
        "buy_service":"Polymarket Edge Report",       # $1.00 — market edges / risk events
        "peer_invite":"Order_ChainFlow",
        "claude_role": (
            "You are NYSE_Tech_Agent, an autonomous tech sector intelligence agent on "
            "the Virtuals ACP network. Your specialty: NVDA, AAPL, MSFT, GOOGL, META — "
            "regulatory risk, AI policy, SEC actions, tokenized equity timelines. Your "
            "job: produce a pre-market tech sector brief with regime (BULLISH/BEARISH/"
            "NEUTRAL), the top regulatory risk in play today, and a single trade thesis "
            "for the sector. Include any tokenized equity or Chainlink feed development "
            "relevant to the day. Be direct. No fluff."
        ),
        "data_fn":    "tech",
    },
    "Order_ChainFlow": {
        "addr_key":   "ORDER_CHAINFLOW_ADDRESS",
        "specialty":  "on-chain order flow, whale activity, DEX inflows",
        "buys_from":  "Octodamus",
        "buy_service":"BTC Bull Trap Monitor",        # $1.50 — on-chain divergence signal
        "peer_invite":"NYSE_EarningsEdge",
        "claude_role": (
            "You are Order_ChainFlow, an autonomous on-chain order flow agent on the "
            "Virtuals ACP network. Your specialty: BTC/ETH cumulative delta, whale "
            "wallet activity, Base DEX inflows, bridge flows, and futures open interest "
            "changes. Your job: produce a pre-market on-chain flow brief with net "
            "direction (ACCUMULATION / DISTRIBUTION / NEUTRAL), 2 key data signals "
            "supporting it, and a liquidity implication for the NYSE open. Be direct. "
            "No fluff."
        ),
        "data_fn":    "onchain",
    },
    "NYSE_EarningsEdge": {
        "addr_key":   "X_SENTIMENT_ADDRESS",          # reusing existing funded wallet
        "specialty":  "upcoming earnings catalysts + implied move vs historical",
        "buys_from":  "Octodamus",
        "buy_service":"BTC Market Signal",             # $1.00 — macro context for earnings risk
        "peer_invite":"NYSE_MacroMind",
        "claude_role": (
            "You are NYSE_EarningsEdge, an autonomous earnings catalyst agent on the "
            "Virtuals ACP network. Your specialty: upcoming earnings events for mega-cap "
            "tech and crypto-adjacent stocks — which names report this week, what the "
            "options market implies for the move, whether analyst estimates have been "
            "revised up or down, and the pre-earnings positioning risk. "
            "Your job: given the earnings calendar and macro context, identify the top "
            "2-3 upcoming catalysts, their implied move, estimate revision direction, "
            "and a verdict (HIGH RISK / ELEVATED / NEUTRAL) for holding into earnings. "
            "If no major names report this week, note the quiet window and its "
            "implication for positioning. Be direct. No fluff."
        ),
        "data_fn":    "earnings",
    },
}

# Octodamus is the primary intel provider — the one agent guaranteed to fulfill
_OCTODAMUS_CARD = {
    "provider_wallet": "0x94c037393ab0263194dcfd8d04a2176d6a80e385",
}

# Min USDC to trigger a buy (must cover the most expensive service + funder gas buffer)
_MIN_USDC_TO_BUY = 0.60   # below this: HOLD, no buying
_MIN_ETH_FOR_GAS = 0.000005  # ~600k gas at 0.008 gwei

# ── Balance checks ─────────────────────────────────────────────────────────────

def _get_balances(addr: str) -> tuple[float, float]:
    """Returns (usdc_balance, eth_balance) for the given address."""
    try:
        from web3 import Web3
        w3 = Web3(Web3.HTTPProvider(_BASE_RPC))
        cs = Web3.to_checksum_address(addr)
        usdc = w3.eth.contract(address=Web3.to_checksum_address(_USDC_ADDRESS), abi=_USDC_ABI)
        usdc_bal = usdc.functions.balanceOf(cs).call() / 1_000_000
        eth_bal  = w3.eth.get_balance(cs) / 1e18
        return round(usdc_bal, 4), round(eth_bal, 8)
    except Exception as e:
        log.warning(f"Balance check failed: {e}")
        return 0.0, 0.0

# ── Market context ─────────────────────────────────────────────────────────────

def _get_market_context() -> dict:
    """Pull live BTC price, Fear & Greed, and Grok sentiment for context."""
    ctx = {
        "date":      date.today().isoformat(),
        "time_utc":  datetime.utcnow().strftime("%H:%M UTC"),
        "btc_price": None,
        "btc_24h":   None,
        "fear_greed": None,
        "fear_greed_label": None,
        "grok_btc":  None,
    }
    try:
        from financial_data_client import get_crypto_prices
        prices = get_crypto_prices(["BTC"])
        ctx["btc_price"] = prices.get("BTC", {}).get("usd")
        ctx["btc_24h"]   = prices.get("BTC", {}).get("usd_24h_change")
    except Exception as e:
        log.warning(f"Price fetch failed: {e}")

    try:
        import httpx
        r = httpx.get("https://api.alternative.me/fng/?limit=1", timeout=6)
        if r.status_code == 200:
            d = r.json()["data"][0]
            ctx["fear_greed"]       = int(d["value"])
            ctx["fear_greed_label"] = d.get("value_classification", "Unknown")
    except Exception as e:
        log.warning(f"F&G fetch failed: {e}")

    try:
        from octo_grok_sentiment import get_grok_sentiment
        gs = get_grok_sentiment("BTC") or {}
        ctx["grok_btc"] = {
            "signal":     gs.get("signal", "NEUTRAL"),
            "confidence": round(gs.get("confidence", 0.5) * 100),
            "summary":    gs.get("summary", "")[:150],
        }
    except Exception as e:
        log.warning(f"Grok fetch failed: {e}")

    return ctx


def _get_specialty_data(data_fn: str) -> dict:
    """Pull specialty-specific live data for each agent type."""
    data = {}
    try:
        if data_fn == "macro":
            try:
                from octo_macro import get_macro_context
                data["macro"] = get_macro_context()
            except Exception:
                pass

        elif data_fn == "stocks":
            try:
                from financial_data_client import get_stock_prices
                data["stocks"] = get_stock_prices(["NVDA", "TSLA", "AAPL", "MSFT"])
            except Exception:
                pass
            try:
                from octo_congress import get_recent_trades
                data["congress"] = get_recent_trades(["NVDA", "AAPL", "MSFT", "TSLA"])
            except Exception:
                pass

        elif data_fn == "tech":
            try:
                from financial_data_client import get_stock_prices
                data["stocks"] = get_stock_prices(["NVDA", "AAPL", "MSFT", "GOOGL", "META"])
            except Exception:
                pass

        elif data_fn == "onchain":
            try:
                import octo_coinglass
                data["derivatives"] = octo_coinglass.get_derivatives_summary("BTC")
            except Exception:
                pass

        elif data_fn == "earnings":
            try:
                import httpx
                from datetime import timedelta
                sec = _secrets()
                fk = sec.get("FINNHUB_API_KEY", "")
                if fk:
                    today_str = date.today().isoformat()
                    end_str   = (date.today() + timedelta(days=7)).isoformat()
                    r = httpx.get(
                        "https://finnhub.io/api/v1/calendar/earnings",
                        params={"from": today_str, "to": end_str, "token": fk},
                        timeout=8,
                    )
                    if r.status_code == 200:
                        all_earnings = r.json().get("earningsCalendar", [])
                        watch = {"NVDA", "TSLA", "AAPL", "MSFT", "GOOGL", "META",
                                 "AMZN", "COIN", "HOOD", "MSTR", "AMD", "INTC"}
                        relevant = [e for e in all_earnings if e.get("symbol") in watch]
                        data["earnings_watch"] = relevant[:10]
                        data["total_reporting"] = len(all_earnings)
            except Exception:
                pass
            try:
                # Add current price context for reporting tickers
                reporting = [e["symbol"] for e in data.get("earnings_watch", [])]
                if reporting:
                    from financial_data_client import get_stock_prices
                    data["prices"] = get_stock_prices(reporting[:6])
            except Exception:
                pass

    except Exception as e:
        log.warning(f"Specialty data fetch ({data_fn}) failed: {e}")

    return data

# ── Claude Haiku brief generation ─────────────────────────────────────────────

def _generate_brief(
    agent_name: str, config: dict, market_ctx: dict, specialty_data: dict,
    client, core_memory: str = "", team_signals: dict = None
) -> str:
    """Use Claude Haiku to generate the agent's specialty pre-market brief."""
    ctx_block = (
        f"Pre-market context — {market_ctx['date']} {market_ctx['time_utc']}\n"
        f"BTC: ${market_ctx.get('btc_price', 'N/A'):,} ({market_ctx.get('btc_24h', 0):+.1f}% 24h)\n"
        f"Fear & Greed: {market_ctx.get('fear_greed', 'N/A')}/100 ({market_ctx.get('fear_greed_label', 'N/A')})\n"
    )
    if market_ctx.get("grok_btc"):
        g = market_ctx["grok_btc"]
        ctx_block += f"X Crowd (BTC): {g['signal']} {g['confidence']}% — {g['summary']}\n"
    if specialty_data:
        ctx_block += f"\nSpecialty data:\n{json.dumps(specialty_data, indent=2, default=str)[:1500]}\n"
    if team_signals:
        ctx_block += _peer_consensus_block(agent_name, team_signals)
    if core_memory and "No entries yet" not in core_memory:
        # Inject last 600 chars of core memory (most recent entries at the bottom)
        ctx_block += f"\nYour core memory (what you've learned):\n{core_memory[-600:]}\n"

    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            system=config["claude_role"],
            messages=[{
                "role": "user",
                "content": (
                    f"{ctx_block}\n"
                    f"Generate your pre-market brief for {market_ctx['date']}. "
                    f"Start directly with your REGIME VERDICT line — do NOT add a markdown "
                    f"title or header. 3-5 sentences max. Include regime verdict, key signals, "
                    f"and NYSE open implication. "
                    f"Do not include wallet balance, x402 revenue figures, or operational "
                    f"status notes — briefs are client-facing intelligence only."
                ),
            }],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        log.error(f"{agent_name} brief generation failed: {e}")
        return f"Brief generation failed: {e}"

# ── State management ───────────────────────────────────────────────────────────

def _load_state() -> dict:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {name: {"sessions": 0, "buy_count": 0, "usdc_start": None, "last_session": None}
            for name in AGENT_CONFIGS}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")

# ── Memory helpers ─────────────────────────────────────────────────────────────

def _read_agent_memory(agent_name: str) -> str:
    """Read this agent's core memory file. Returns empty string on failure."""
    key = _MEMORY_KEYS.get(agent_name, agent_name.lower())
    try:
        from octo_memory_db import read_core_memory
        return read_core_memory(key)
    except Exception as e:
        log.warning(f"{agent_name}: memory read failed: {e}")
        return ""


def _append_agent_memory(agent_name: str, brief: str, regime: str) -> bool:
    """Append today's regime verdict to the agent's core memory. Returns True if new data added."""
    key = _MEMORY_KEYS.get(agent_name, agent_name.lower())
    today = date.today().isoformat()
    insight = f"Session {today}: {regime} | Brief excerpt: {brief[:200]}"
    try:
        from octo_memory_db import append_core_memory
        append_core_memory(key, f"{agent_name} Session", insight)
        return True
    except Exception as e:
        log.warning(f"{agent_name}: memory append failed: {e}")
        return False


def _load_team_channel() -> dict:
    """Load all agents' latest verdicts from the shared team channel."""
    try:
        if TEAM_CHANNEL.exists():
            return json.loads(TEAM_CHANNEL.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


_PEER_FRESH_HOURS = 20  # a peer verdict older than this is stale — excluded from consensus


def _peer_bucket(regime: str) -> str:
    """Normalize a regime verdict into one of three consensus buckets."""
    r = (regime or "").upper()
    if "RISK-ON" in r or "BULLISH" in r:  return "RISK-ON"
    if "RISK-OFF" in r or "BEARISH" in r: return "RISK-OFF"
    return "NEUTRAL"  # NEUTRAL / TRANSITION / GUARDED / unknown


def _signal_age_hours(sig: dict):
    """Hours since a peer posted its verdict, or None if unparseable."""
    try:
        posted = datetime.fromisoformat(str(sig.get("posted_at", "")).replace("Z", ""))
        return (datetime.utcnow() - posted).total_seconds() / 3600
    except Exception:
        return None


def _peer_consensus_block(agent_name: str, team_signals: dict) -> str:
    """Deterministic, freshness-filtered peer-consensus tally injected into each agent.

    Fixes briefs fabricating consensus (agents claimed '3 of 5 RISK-ON' when the channel
    held 5 fresh NEUTRAL + 1 stale 64-day-old RISK-ON): stale verdicts (>20h) are excluded
    from the count and labeled, and the exact tally is supplied so the brief can't overstate
    agreement or count a stale peer as current."""
    rows, tally = [], {"RISK-ON": 0, "NEUTRAL": 0, "RISK-OFF": 0}
    for peer, sig in (team_signals or {}).items():
        if peer == agent_name:
            continue
        age = _signal_age_hours(sig)
        stale = age is None or age > _PEER_FRESH_HOURS
        if not stale:
            tally[_peer_bucket(sig.get("regime", ""))] += 1
        age_str = f"{age:.0f}h ago" if age is not None else "age unknown"
        rows.append(f"  {peer}: {sig.get('regime','?')} ({age_str}"
                    f"{' [STALE-excluded]' if stale else ''}) — {sig.get('excerpt','')[:90]}")
    if not rows:
        return ""
    total = sum(tally.values())
    dominant = max(tally, key=tally.get) if total else "NEUTRAL"
    return (
        "\nPeer agent signals (for cross-validation):\n" + "\n".join(rows) +
        f"\nPEER CONSENSUS TALLY (fresh <={_PEER_FRESH_HOURS}h only): "
        f"RISK-ON {tally['RISK-ON']}, NEUTRAL {tally['NEUTRAL']}, RISK-OFF {tally['RISK-OFF']} "
        f"(of {total} fresh peers) -> dominant: {dominant}. "
        f"Report peer consensus using THESE exact counts. Do NOT claim a RISK-ON/RISK-OFF "
        f"consensus the tally doesn't support; [STALE-excluded] peers are not part of it."
    )


def _clean_brief_for_channel(text: str) -> str:
    """Strip markdown headers/formatting for clean plain-text channel storage."""
    import re as _re
    lines = text.strip().splitlines()
    # Drop leading # header lines (Claude often starts with "# AGENT | PRE-MARKET BRIEF | ...")
    lines = [l for l in lines if not l.strip().startswith("#")]
    # Strip ** bold markers and leading/trailing whitespace per line
    lines = [_re.sub(r"\*{1,3}", "", l).strip() for l in lines if l.strip()]
    return " ".join(lines)[:200]


def _post_team_channel(agent_name: str, regime: str, brief_excerpt: str):
    """Write this agent's signal to the shared team channel."""
    channel = _load_team_channel()
    channel[agent_name] = {
        "regime":    regime,
        "excerpt":   _clean_brief_for_channel(brief_excerpt),
        "posted_at": datetime.utcnow().isoformat(),
    }
    try:
        TEAM_CHANNEL.parent.mkdir(parents=True, exist_ok=True)
        TEAM_CHANNEL.write_text(json.dumps(channel, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning(f"Team channel write failed: {e}")


def _extract_regime(brief: str) -> str:
    """Extract the regime verdict from a brief (RISK-ON / RISK-OFF / TRANSITION / etc.).

    Priority: scan labeled verdict lines first (REGIME VERDICT / DIRECTIONAL CALL /
    REGIME:) so a ChainFlow brief saying NEUTRAL isn't overridden by a peer-mention
    of RISK-ON appearing earlier in the same text.
    """
    import re as _re
    keywords = ("RISK-ON", "RISK-OFF", "TRANSITION", "NEUTRAL", "BULLISH", "BEARISH",
                "ACCUMULATION", "DISTRIBUTION", "CONTRARIAN", "ALIGNED")
    verdict_pattern = _re.compile(
        r"(?:REGIME VERDICT|DIRECTIONAL CALL|VERDICT|REGIME)[:\s*|]+([A-Z -]+)",
        _re.IGNORECASE,
    )
    for line in brief.splitlines():
        m = verdict_pattern.search(line)
        if m:
            candidate = m.group(1).upper().strip().split()[0]
            for kw in keywords:
                if kw in candidate or candidate in kw:
                    return kw
    # Fallback: first keyword found anywhere in text
    upper = brief.upper()
    for kw in keywords:
        if kw in upper:
            return kw
    return "UNKNOWN"

# ── Per-agent session ──────────────────────────────────────────────────────────

def _run_agent(agent_name: str, config: dict, market_ctx: dict, state: dict, client) -> str:
    log.info(f"{'='*50}")
    log.info(f"Agent: {agent_name} | {config['specialty']}")

    sec      = _secrets()
    addr     = sec.get(config["addr_key"], "")
    if not addr:
        log.error(f"{agent_name}: address not found in secrets ({config['addr_key']})")
        return "ERROR: no address"

    # ── 1. Wallet check ────────────────────────────────────────────────────────
    usdc, eth = _get_balances(addr)
    log.info(f"{agent_name}: USDC=${usdc:.4f} | ETH={eth:.8f} | addr={addr[:12]}...")

    agent_state = state.setdefault(agent_name, {"sessions": 0, "buy_count": 0, "usdc_start": None, "last_session": None})
    if agent_state["usdc_start"] is None:
        agent_state["usdc_start"] = usdc  # lock in starting balance
    agent_state["usdc_current"] = usdc
    agent_state["last_session"] = datetime.utcnow().isoformat()
    agent_state["sessions"] = agent_state.get("sessions", 0) + 1

    # ── 2. Survival gate ───────────────────────────────────────────────────────
    can_buy = usdc >= _MIN_USDC_TO_BUY and eth >= _MIN_ETH_FOR_GAS
    if not can_buy:
        if usdc < _MIN_USDC_TO_BUY:
            log.warning(f"{agent_name}: HOLD — USDC ${usdc:.4f} below ${_MIN_USDC_TO_BUY} minimum. Generating analysis only.")
        if eth < _MIN_ETH_FOR_GAS:
            log.warning(f"{agent_name}: HOLD — ETH {eth:.8f} below gas minimum. Fund {addr} with 0.0001 ETH.")

    # ── 3. Read core memory + team channel ───────────────────────────────────
    core_memory  = _read_agent_memory(agent_name)
    team_signals = _load_team_channel()
    memory_had_entries = bool(core_memory and "No entries yet" not in core_memory)
    log.info(f"{agent_name}: memory={'COMPOUNDING' if memory_had_entries else 'STATIC (first session)'}")

    # ── 4. Specialty data + brief ──────────────────────────────────────────────
    specialty_data = _get_specialty_data(config["data_fn"])
    brief = _generate_brief(agent_name, config, market_ctx, specialty_data, client,
                            core_memory=core_memory, team_signals=team_signals)
    log.info(f"{agent_name} brief: {brief[:120]}...")

    # ── 5. Compound memory — append regime verdict ────────────────────────────
    regime = _extract_regime(brief)
    memory_grew = _append_agent_memory(agent_name, brief, regime)
    _post_team_channel(agent_name, regime, brief)
    agent_state["memory_status"] = "COMPOUNDING" if memory_grew else "STATIC"
    agent_state["last_regime"]   = regime

    # ── 6. Save daily brief ────────────────────────────────────────────────────
    today = market_ctx["date"]
    brief_file = DRAFTS_DIR / f"{agent_name.lower()}_{today}.md"
    brief_file.write_text(
        f"# {agent_name} Pre-Market Brief — {today}\n"
        f"**Specialty:** {config['specialty']} | **Regime:** {regime}\n"
        f"**Wallet:** {addr} | USDC: ${usdc:.4f} | **Memory:** {agent_state['memory_status']}\n\n"
        f"{brief}\n\n"
        f"---\n"
        f"*Generated by {agent_name} autonomous session #{agent_state['sessions']} | {market_ctx['time_utc']}*\n"
        f"*Powered by @octodamusai ecosystem*\n",
        encoding="utf-8",
    )

    # ── 7. Buy intel from Octodamus ────────────────────────────────────────────
    buy_result = "SKIPPED (survival hold)"
    if can_buy:
        from octo_agent_cards import buy_intel, AGENT_CARDS, get_calling_card

        # Find the service to buy
        target_services = {s["name"]: s for s in AGENT_CARDS.get("Octodamus", {}).get("services", [])}
        service_name = config["buy_service"]
        svc = target_services.get(service_name)

        if not svc:
            # Fallback to cheapest service
            svc = sorted(AGENT_CARDS["Octodamus"]["services"], key=lambda s: s["price_usdc"])[0]
            service_name = svc["name"]

        # Check we can afford it
        if usdc < svc["price_usdc"]:
            log.warning(f"{agent_name}: cannot afford {service_name} (${svc['price_usdc']:.2f}), wallet=${usdc:.4f}")
            buy_result = f"SKIPPED — insufficient USDC for {service_name}"
        else:
            # Embed brief + calling card + peer invite in the buy description
            peer_name = config["peer_invite"]
            peer_card = AGENT_CARDS.get(peer_name, {})
            peer_wallet = peer_card.get("provider_wallet", "")
            peer_invite = (
                f"\n\nContext from {agent_name}: {brief[:300]}\n"
                f"\nPeer network invite: I am also seeking to connect with {peer_name} "
                f"({peer_card.get('description', '')[:100]}). "
                + (f"Their wallet: {peer_wallet}." if peer_wallet else "")
            )
            # Append calling card so Octodamus can hire us back
            full_description = svc["job_description"] + peer_invite + get_calling_card(agent_name)

            buy_result = buy_intel(agent_name, "Octodamus", service_name)
            # Note: buy_intel uses the service's default description. We rebuild it with context.
            # If job was created, update the pending jobs entry with full description.
            if "Job #" in buy_result:
                agent_state["buy_count"] = agent_state.get("buy_count", 0) + 1
                log.info(f"{agent_name} buy: {buy_result[:100]}")
            else:
                log.warning(f"{agent_name} buy failed: {buy_result[:150]}")

    # ── 6. P&L delta ──────────────────────────────────────────────────────────
    start = agent_state.get("usdc_start", usdc)
    delta = usdc - start
    log.info(
        f"{agent_name} session complete | "
        f"USDC ${usdc:.4f} | Start ${start:.4f} | Delta {delta:+.4f} | "
        f"Buys: {agent_state.get('buy_count', 0)} | Sessions: {agent_state['sessions']}"
    )
    return buy_result


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    # Guard: refuse to run more than once per calendar day.
    # Prevents duplicate emails if this script is triggered multiple times
    # by any mechanism (Task Scheduler, manual, external process).
    today_str_guard = date.today().isoformat()
    guard_file = ROOT / "data" / f".nyse_ran_{today_str_guard}"
    if guard_file.exists():
        log.warning(f"Already ran today ({today_str_guard}). Exiting — delete {guard_file.name} to force re-run.")
        return
    guard_file.touch()

    log.info("=" * 60)
    log.info("Octodamus NYSE Sub-Agent Runner — Pre-Market Session")
    log.info(f"Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    log.info(f"Agents: {', '.join(AGENT_CONFIGS)}")
    log.info("=" * 60)

    # Anthropic client (Haiku)
    sec = _secrets()
    api_key = sec.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        log.error("ANTHROPIC_API_KEY not found — brief generation will fail")
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    # Live market context (fetched once, shared across all agents)
    log.info("Pulling market context...")
    market_ctx = _get_market_context()
    log.info(
        f"BTC: ${market_ctx.get('btc_price', 'N/A'):,} "
        f"({market_ctx.get('btc_24h', 0):+.1f}%) | "
        f"F&G: {market_ctx.get('fear_greed', 'N/A')} ({market_ctx.get('fear_greed_label', '')})"
    )

    state = _load_state()
    results = {}

    for agent_name, config in AGENT_CONFIGS.items():
        try:
            result = _run_agent(agent_name, config, market_ctx, state, client)
            results[agent_name] = result
        except Exception as e:
            log.error(f"{agent_name} session crashed: {e}")
            results[agent_name] = f"CRASH: {e}"
        time.sleep(3)  # small delay between agents to avoid RPC rate limits

    _save_state(state)

    # Session summary
    log.info("=" * 60)
    log.info("NYSE AGENT SESSION SUMMARY")
    log.info("=" * 60)
    summary_lines = []
    for name, result in results.items():
        s = state.get(name, {})
        if "Job #" in result:
            action = "BUY "
        elif "INTEL REPEAT BLOCKED" in result or "Cooldown" in result:
            action = "COOL"  # cooldown active — not an error
        elif "SKIPPED" in result:
            action = "HOLD"
        elif result.startswith("ERROR") or result.startswith("CRASH") or result.startswith("Unknown"):
            action = "ERR "
        else:
            action = "PASS"
        mem    = s.get("memory_status", "UNKNOWN")
        regime = s.get("last_regime", "?")
        line   = (
            f"  {name:<22} [{action}] [{mem:<11}] regime={regime:<12} "
            f"USDC=${s.get('usdc_current', 0):.2f} "
            f"buys={s.get('buy_count', 0)} sessions={s.get('sessions', 0)}"
        )
        log.info(line)
        summary_lines.append(line.strip())
    log.info("=" * 60)
    log.info(f"Briefs saved to: {DRAFTS_DIR}")
    log.info("Next run: tomorrow 5:30am PST")

    # Session email
    try:
        from octo_notify import _send
        today_str = market_ctx.get("date", date.today().isoformat())
        btc_str   = f"${market_ctx.get('btc_price', 0):,} ({market_ctx.get('btc_24h', 0):+.1f}%)"
        fg_str    = f"{market_ctx.get('fear_greed', '?')}/100 {market_ctx.get('fear_greed_label', '')}"

        # Scan briefs for data degradation alerts to surface in email
        data_alerts = []
        team_ch = _load_team_channel()
        for peer, sig in team_ch.items():
            ex = sig.get("excerpt", "")
            if any(w in ex.upper() for w in ("UNAVAILABLE", "DEGRADATION", "FAILED", "NO DATA", "OPAQUE")):
                data_alerts.append(f"  {peer}: data quality issue — check brief")

        email_body = (
            f"NYSE Sub-Agent Session — {today_str}\n"
            f"BTC: {btc_str} | F&G: {fg_str}\n\n"
            f"{'='*52}\n"
            f"AGENT SUMMARY\n"
            f"{'='*52}\n"
        )
        email_body += "\n".join(summary_lines)
        email_body += "\n  Legend: [BUY]=intel bought | [COOL]=cooldown, buy skipped | [HOLD]=low balance | [ERR]=error\n"

        if data_alerts:
            email_body += (
                f"\n{'='*52}\n"
                f"!! DATA ISSUES (check brief files)\n"
                f"{'='*52}\n"
                + "\n".join(data_alerts) + "\n"
            )

        email_body += (
            f"\n{'='*52}\n"
            f"TEAM CHANNEL (peer signals)\n"
            f"{'='*52}\n"
        )
        _fresh_tally = {"RISK-ON": 0, "NEUTRAL": 0, "RISK-OFF": 0}
        for peer, sig in team_ch.items():
            excerpt = sig.get("excerpt", "")[:140]
            age = _signal_age_hours(sig)
            stale = age is None or age > _PEER_FRESH_HOURS
            tag = f" [STALE {age:.0f}h]" if (stale and age is not None) else (" [STALE]" if stale else "")
            if not stale:
                _fresh_tally[_peer_bucket(sig.get("regime", ""))] += 1
            email_body += f"  {peer}: {sig.get('regime','?')}{tag} — {excerpt}\n"
        _ft = sum(_fresh_tally.values())
        email_body += (f"  CONSENSUS (fresh <={_PEER_FRESH_HOURS}h): "
                       f"RISK-ON {_fresh_tally['RISK-ON']}, NEUTRAL {_fresh_tally['NEUTRAL']}, "
                       f"RISK-OFF {_fresh_tally['RISK-OFF']} of {_ft}\n")

        # Full agent briefs
        brief_lines = [f"\n{'='*52}", "AGENT BRIEFS", f"{'='*52}"]
        any_brief = False
        for agent_name in AGENT_CONFIGS:
            bf = DRAFTS_DIR / f"{agent_name.lower()}_{today_str}.md"
            if bf.exists():
                any_brief = True
                content = bf.read_text(encoding="utf-8")
                # Strip markdown heading lines (start with #), keep body
                body_lines = [
                    ln for ln in content.splitlines()
                    if not ln.startswith("#") and not ln.startswith("---") and not ln.startswith("*Generated")
                    and not ln.startswith("*Powered")
                ]
                body_text = "\n".join(body_lines).strip()
                brief_lines.append(f"\n{agent_name}")
                brief_lines.append(body_text)
        if any_brief:
            email_body += "\n".join(brief_lines)
        else:
            email_body += f"\nBriefs: {DRAFTS_DIR}"
        _send(f"NYSE Agents {today_str} — session complete", email_body)
        log.info("Session email sent.")
    except Exception as e:
        log.warning(f"Session email failed: {e}")


if __name__ == "__main__":
    main()

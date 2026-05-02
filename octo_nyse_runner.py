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
  X_Sentiment_Agent — X/Twitter crowd sentiment + contrarian divergence

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
    "NYSE_MacroMind":    "nyse_macromind",
    "NYSE_StockOracle":  "nyse_stockoracle",
    "NYSE_Tech_Agent":   "nyse_tech_agent",
    "Order_ChainFlow":   "order_chainflow",
    "X_Sentiment_Agent": "x_sentiment_agent",
}

AGENT_CONFIGS = {
    "NYSE_MacroMind": {
        "addr_key":   "NYSE_MACROMIND_ADDRESS",
        "specialty":  "cross-asset macro regime",
        "buys_from":  "Octodamus",
        "buy_service":"Agent Market Intel Bundle",   # $2.00 — full macro + signal context
        "peer_invite":"X_Sentiment_Agent",
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
        "peer_invite":"X_Sentiment_Agent",
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
    "X_Sentiment_Agent": {
        "addr_key":   "X_SENTIMENT_ADDRESS",
        "specialty":  "X/Twitter crowd sentiment + contrarian divergence",
        "buys_from":  "Octodamus",
        "buy_service":"Fear vs Crowd Divergence",     # $2.00 — top divergence signal
        "peer_invite":"NYSE_MacroMind",
        "claude_role": (
            "You are X_Sentiment_Agent, an autonomous crowd sentiment agent on the "
            "Virtuals ACP network. Your specialty: X/Twitter crowd positioning for BTC, "
            "ETH, SOL, NVDA — detecting crowded longs, narrative vs price gaps, and "
            "contrarian setups. Your job: produce a pre-market sentiment brief with "
            "crowd consensus score (0-100), a CONTRARIAN BEAR / BULL / ALIGNED verdict, "
            "and the asset most vulnerable to a sentiment-driven reversal at open. "
            "Be direct. No fluff."
        ),
        "data_fn":    "sentiment",
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

        elif data_fn == "sentiment":
            try:
                from octo_grok_sentiment import get_grok_sentiment
                data["grok"] = {a: get_grok_sentiment(a) for a in ["BTC", "ETH", "SOL"]}
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
        ctx_block += "\nPeer agent signals (for cross-validation):\n"
        for peer, sig in team_signals.items():
            if peer != agent_name:
                ctx_block += f"  {peer}: {sig.get('regime','?')} — {sig.get('excerpt','')[:100]}\n"
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
                    f"3-5 sentences max. Include your regime verdict, key signals, "
                    f"and NYSE open implication."
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


def _post_team_channel(agent_name: str, regime: str, brief_excerpt: str):
    """Write this agent's signal to the shared team channel."""
    channel = _load_team_channel()
    channel[agent_name] = {
        "regime":    regime,
        "excerpt":   brief_excerpt[:200],
        "posted_at": datetime.utcnow().isoformat(),
    }
    try:
        TEAM_CHANNEL.parent.mkdir(parents=True, exist_ok=True)
        TEAM_CHANNEL.write_text(json.dumps(channel, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning(f"Team channel write failed: {e}")


def _extract_regime(brief: str) -> str:
    """Extract the regime verdict from a brief (RISK-ON / RISK-OFF / TRANSITION / etc.)."""
    for keyword in ("RISK-ON", "RISK-OFF", "TRANSITION", "NEUTRAL", "BULLISH", "BEARISH",
                    "ACCUMULATION", "DISTRIBUTION", "CONTRARIAN", "ALIGNED"):
        if keyword in brief.upper():
            return keyword
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
        action = "BUY" if "Job #" in result else ("HOLD" if "SKIPPED" in result else "ERR")
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
        email_body = (
            f"NYSE Sub-Agent Session — {today_str}\n"
            f"BTC: {btc_str} | F&G: {fg_str}\n\n"
            f"{'='*52}\n"
            f"AGENT SUMMARY\n"
            f"{'='*52}\n"
            + "\n".join(summary_lines) +
            f"\n\n{'='*52}\n"
            f"TEAM CHANNEL (peer signals)\n"
            f"{'='*52}\n"
        )
        for peer, sig in _load_team_channel().items():
            email_body += f"  {peer}: {sig.get('regime','?')} | {sig.get('excerpt','')[:100]}\n"
        email_body += f"\nBriefs: {DRAFTS_DIR}"
        _send(f"NYSE Agents {today_str} — session complete", email_body)
        log.info("Session email sent.")
    except Exception as e:
        log.warning(f"Session email failed: {e}")


if __name__ == "__main__":
    main()

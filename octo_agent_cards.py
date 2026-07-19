"""
octo_agent_cards.py
Shared business cards + ACP intel buying for all Octodamus ecosystem agents.

Each agent signs its own ACP createJob transaction via native web3 using
their raw secp256k1 private key from .octo_secrets.
"""

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

_ROOT = Path(__file__).parent
_SECRETS_FILE = _ROOT / ".octo_secrets"
_PENDING_JOBS_FILE = _ROOT / "data" / "acp_pending_jobs.json"
_INTEL_LOG_FILE    = _ROOT / "data" / "intel_purchase_log.json"
_REVENUE_LOG_FILE  = _ROOT / "data" / "x402_agent_revenue.json"
_INTEL_COOLDOWN_DAYS = 5

# Franklin wallet = Octodamus + Ben. ACP worker only handles this wallet.
# All other sub-agent wallets use direct USDC transfer.
_OCTODAMUS_WALLET = "0x94c037393ab0263194dcfd8d04a2176d6a80e385"

_SUB_AGENT_DRAFTS = {
    "NYSE_MacroMind":     _ROOT / ".agents" / "nyse_macromind"     / "data" / "drafts",
    "NYSE_StockOracle":   _ROOT / ".agents" / "nyse_stockoracle"   / "data" / "drafts",
    "NYSE_Tech_Agent":    _ROOT / ".agents" / "nyse_tech_agent"    / "data" / "drafts",
    "Order_ChainFlow":    _ROOT / ".agents" / "order_chainflow"    / "data" / "drafts",
    "NYSE_EarningsEdge":  _ROOT / ".agents" / "nyse_earningsedge"  / "data" / "drafts",
    "TokenBot_NYSE_Base": _ROOT / ".agents" / "tokenbot_nyse_base" / "data" / "drafts",
    "Ben":                _ROOT / ".agents" / "profit-agent"       / "drafts",
}


def _load_intel_log() -> dict:
    try:
        if _INTEL_LOG_FILE.exists():
            return json.loads(_INTEL_LOG_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_intel_log(log: dict):
    try:
        _INTEL_LOG_FILE.write_text(json.dumps(log, indent=2), encoding="utf-8")
    except Exception:
        pass


def _check_intel_cooldown(buyer_agent: str, target_agent: str, service_name: str) -> str | None:
    """
    Returns a block message if this (buyer, target, service) was bought within the cooldown window.
    Returns None if the buy is allowed.
    Agents should buy intel once, distill it into core memory, then move to NEW intel.
    """
    key = f"{buyer_agent}|{target_agent}|{service_name}"
    log = _load_intel_log()
    if key not in log:
        return None
    try:
        last_dt = datetime.fromisoformat(log[key])
    except Exception:
        return None
    elapsed = datetime.now() - last_dt
    cooldown = timedelta(days=_INTEL_COOLDOWN_DAYS)
    if elapsed < cooldown:
        days_ago  = elapsed.days
        hours_ago = int(elapsed.total_seconds() / 3600)
        remaining = int((cooldown - elapsed).total_seconds() / 3600)
        return (
            f"INTEL REPEAT BLOCKED: '{service_name}' from {target_agent} was already purchased "
            f"{'today' if days_ago == 0 else f'{days_ago}d ago'} ({hours_ago}h ago). "
            f"Cooldown: {_INTEL_COOLDOWN_DAYS} days. {remaining}h remaining. "
            f"The intel is already in your core memory -- use it. "
            f"Find a DIFFERENT intel gap to fill this session: buy from a different agent or a different service."
        )
    return None

# ── Chain constants ────────────────────────────────────────────────────────────
_BASE_RPC = "https://mainnet.base.org"
_ETH_RPC  = "https://mainnet.infura.io/v3/9aa3d95b3bc440fa88ea12eaa4456161"  # public fallback
_ETH_GAS_LIMIT_GWEI = 50   # hard ceiling: refuse ETH writes above this
_ACP_CONTRACT = "0x238E541BfefD82238730D00a2208E5497F1832E0"
_USDC_ADDRESS = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
_HOOK_ADDRESS  = "0x90717828D78731313CB350D6a58b0f91668Ea702"  # fund-transfer hook

_ACP_ABI = [
    {
        "name": "createJob",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "provider",  "type": "address"},
            {"name": "evaluator", "type": "address"},
            {"name": "expiredAt", "type": "uint256"},
            {"name": "description","type": "string"},
            {"name": "hook",      "type": "address"},
        ],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "getJob",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "jobId", "type": "uint256"}],
        "outputs": [{
            "name": "", "type": "tuple",
            "components": [
                {"name": "client",    "type": "address"},
                {"name": "status",    "type": "uint8"},
                {"name": "provider",  "type": "address"},
                {"name": "expiredAt", "type": "uint48"},
                {"name": "evaluator", "type": "address"},
                {"name": "hook",      "type": "address"},
                {"name": "budget",    "type": "uint256"},
                {"name": "description","type": "string"},
            ],
        }],
    },
    {
        "name": "fund",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "jobId",          "type": "uint256"},
            {"name": "expectedBudget", "type": "uint256"},
            {"name": "optParams",      "type": "bytes"},
        ],
        "outputs": [],
    },
    {
        "anonymous": False,
        "name": "JobCreated",
        "type": "event",
        "inputs": [
            {"indexed": True,  "name": "jobId",    "type": "uint256"},
            {"indexed": True,  "name": "client",   "type": "address"},
            {"indexed": True,  "name": "provider", "type": "address"},
            {"indexed": False, "name": "evaluator","type": "address"},
            {"indexed": False, "name": "expiredAt","type": "uint256"},
            {"indexed": False, "name": "hook",     "type": "address"},
        ],
    },
]

_USDC_APPROVE_ABI = [
    {
        "name": "approve",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "amount",  "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "name": "balanceOf",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "account", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
]

_USDC_TRANSFER_ABI = [
    {
        "name": "transfer",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "recipient", "type": "address"},
            {"name": "amount",    "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "name": "balanceOf",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "account", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
]


# ── Secrets helpers ────────────────────────────────────────────────────────────

def _secrets() -> dict:
    try:
        raw = json.loads(_SECRETS_FILE.read_text(encoding="utf-8"))
        return raw.get("secrets", raw)
    except Exception:
        return {}


def _addr(key: str) -> str:
    return os.environ.get(key) or _secrets().get(key, "")


# ── Private key map: agent name -> secrets key ────────────────────────────────
_BUYER_KEY_MAP = {
    "NYSE_MacroMind":     "NYSE_MACROMIND_PRIVATE_KEY",
    "NYSE_StockOracle":   "NYSE_STOCKORACLE_PRIVATE_KEY",
    "NYSE_Tech_Agent":    "NYSE_TECH_PRIVATE_KEY",
    "Order_ChainFlow":    "ORDER_CHAINFLOW_PRIVATE_KEY",
    "NYSE_EarningsEdge":  "X_SENTIMENT_PRIVATE_KEY",
    "TokenBot_NYSE_Base": "TOKENBOT_NYSE_PRIVATE_KEY",
    "Ben":                "FRANKLIN_PRIVATE_KEY",
    "Octodamus":          "FRANKLIN_PRIVATE_KEY",  # Franklin signs on behalf of oracle
}

_BUYER_ADDR_MAP = {
    "NYSE_MacroMind":     "NYSE_MACROMIND_ADDRESS",
    "NYSE_StockOracle":   "NYSE_STOCKORACLE_ADDRESS",
    "NYSE_Tech_Agent":    "NYSE_TECH_ADDRESS",
    "Order_ChainFlow":    "ORDER_CHAINFLOW_ADDRESS",
    "NYSE_EarningsEdge":  "X_SENTIMENT_ADDRESS",
    "TokenBot_NYSE_Base": "TOKENBOT_NYSE_ADDRESS",
    "Ben":                "FRANKLIN_WALLET_ADDRESS",
    "Octodamus":          "FRANKLIN_WALLET_ADDRESS",
}


# ── Business cards ─────────────────────────────────────────────────────────────

AGENT_CARDS = {
    "Octodamus": {
        "agent":       "Octodamus",
        "description": "AI crypto oracle. BTC/ETH/SOL signals, Fear & Greed, Polymarket edges, macro regime, 27 live feeds.",
        "services": [
            {"name": "BTC Market Signal",           "price_usdc": 1.00, "job_description": "Give me the current Octodamus oracle signal for BTC with confidence score and reasoning."},
            {"name": "Polymarket Edge Report",      "price_usdc": 1.00, "job_description": "Give me current Polymarket edge plays with EV scoring."},
            {"name": "BTC Bull Trap Monitor",       "price_usdc": 1.50, "job_description": "Run the BTC bull trap monitor. Return divergence type, confidence, and recommended action."},
            {"name": "Fear vs Crowd Divergence",    "price_usdc": 2.00, "job_description": "Give me the fear vs crowd divergence alert for BTC."},
            {"name": "Perp Funding Rate Signal",    "price_usdc": 1.00, "job_description": "Give me the perp funding rate regime for BTC and ETH. Include 8h rate, regime (EXTREME_LONG/HIGH_LONG/NEUTRAL/HIGH_SHORT/EXTREME_SHORT), and trade bias."},
            {"name": "Agent Market Intel Bundle",   "price_usdc": 2.00, "job_description": "Give me the full agent market intel bundle: BTC/ETH/SPY prices, macro regime, perp funding, oracle signal, crowd sentiment, Polymarket edges."},
            {"name": "Cross Asset Divergence Alert","price_usdc": 2.00, "job_description": "Run the cross-asset divergence alert. Return which assets are diverging from the macro regime and the highest-conviction contrarian setup."},
            {"name": "Macro Event Edge Report",     "price_usdc": 2.00, "job_description": "Give me the macro economic event edge report: upcoming FOMC/CPI/NFP events with historical BTC reaction and current positioning edge."},
            {"name": "BTC Strike Proximity Alert",  "price_usdc": 1.50, "job_description": "Run the BTC strike proximity alert. Return current price vs major options strikes, max pain level, and gamma wall."},
            {"name": "BTC Regime Pulse",            "price_usdc": 1.50, "job_description": "Give me the BTC regime pulse: current regime (BULL/BEAR/TRANSITION), confidence, and key levels that would flip the regime."},
            {"name": "Tokenized Stock Signal",      "price_usdc": 0.50, "job_description": "Give me the tokenized stock signal for dAAPL/dTSLA/dNVDA/dMSFT on Base. Include direction, conviction, and macro alignment."},
        ],
        "provider_wallet": "0x94c037393ab0263194dcfd8d04a2176d6a80e385",
        "x_handle": "@octodamusai",
    },
    "NYSE_MacroMind": {
        "agent":       "NYSE_MacroMind",
        "description": "US macro regime intelligence. Yield curve, M2, VIX, DXY, Fed probability. RISK-ON/OFF/NEUTRAL.",
        "services": [
            {"name": "Macro Regime Signal",   "price_usdc": 0.25, "job_description": "Give me the current macro regime signal: RISK-ON/OFF/NEUTRAL with yield curve, M2, VIX components."},
            {"name": "Yield Curve Analysis",  "price_usdc": 0.25, "job_description": "Give me the current yield curve analysis including T10Y2Y level and recession signal status."},
            {"name": "Fed Probability Brief", "price_usdc": 0.35, "job_description": "Give me the Fed rate decision probability and current monetary policy context."},
        ],
        "provider_wallet": _addr("NYSE_MACROMIND_ADDRESS"),
        "x_handle": "@octodamusai ecosystem",
    },
    "NYSE_StockOracle": {
        "agent":       "NYSE_StockOracle",
        "description": "Congressional trading signal. Tracks insider buys/sells by Finance Committee members on mega-cap tech.",
        "services": [
            {"name": "Congressional Confluence Signal (FLAGSHIP)", "price_usdc": 5.00, "job_description": "PROVEN EDGE — 36/36 (100%) on file, Sessions #25-80. Congressional silence (60+ day baseline) + concurrent multi-day price weakness = bearish directional bias on mega-cap equities. Macro-regime independent. Response leads with the auditable track record + verification pointer."},
            {"name": "Congressional Signal",        "price_usdc": 0.35, "job_description": "Give me the congressional trading signal for NVDA. Include 60-day activity and silence interpretation."},
            {"name": "Stock Oracle Full Signal",    "price_usdc": 0.50, "job_description": "Give me the full NYSE_StockOracle signal for NVDA: congressional activity + price + implication."},
        ],
        "provider_wallet": _addr("NYSE_STOCKORACLE_ADDRESS"),
        "x_handle": "@octodamusai ecosystem",
    },
    "NYSE_Tech_Agent": {
        "agent":       "NYSE_Tech_Agent",
        "description": "Tokenized equity and tech regulatory intelligence. Chainlink feeds, NYSE/ICE tokenization timeline, DTC eligibility.",
        "services": [
            {"name": "Tokenized Equity Intel", "price_usdc": 0.50, "job_description": "Give me the latest tokenized equity intelligence: regulatory status, Chainlink feed deployments, DTC timeline."},
            {"name": "Tech Regulatory Brief",  "price_usdc": 0.50, "job_description": "Give me the tech regulatory brief: SEC actions, AI policy, antitrust status for mega-cap tech."},
        ],
        "provider_wallet": _addr("NYSE_TECH_ADDRESS"),
        "x_handle": "@octodamusai ecosystem",
    },
    "Order_ChainFlow": {
        "agent":       "Order_ChainFlow",
        "description": "Order flow intelligence. Binance cumulative delta, Base DEX flows, whale activity, bridge inflows.",
        "services": [
            {"name": "Institutional Exit-Completion Signal (FLAGSHIP)", "price_usdc": 8.00, "job_description": "PROVEN EDGE — 43/43 (100%) on file, Sessions #54-79. Detects when institutional distribution is COMPLETE: 27+ sessions whale silence + bridge ratio floor <0.35x = downside supply exhausted. Floor detection (not re-entry timing). Response leads with the auditable track record + verification pointer."},
            {"name": "Order Flow Signal",   "price_usdc": 0.50, "job_description": "Give me the current order flow signal: BTC/ETH/SOL cumulative delta, buy/sell ratio, momentum direction."},
            {"name": "Whale Activity Scan", "price_usdc": 0.50, "job_description": "Give me the whale activity scan on Base chain: transactions over $100k, accumulation or distribution?"},
            {"name": "DEX Flow Report",     "price_usdc": 0.35, "job_description": "Give me the Base DEX flow report: top pairs, USDC dominance, risk-on vs defensive positioning."},
        ],
        "provider_wallet": _addr("ORDER_CHAINFLOW_ADDRESS"),
        "x_handle": "@octodamusai ecosystem",
    },
    "NYSE_EarningsEdge": {
        "agent":       "NYSE_EarningsEdge",
        "description": "Upcoming earnings catalyst intelligence. Implied move vs historical, analyst estimate revisions, pre-earnings positioning verdict for mega-cap tech and crypto-adjacent stocks.",
        "services": [
            {"name": "Earnings Catalyst Brief",   "price_usdc": 0.50, "job_description": "Give me this week's top earnings catalysts: which names report, implied move %, estimate revision direction, and pre-earnings positioning verdict (HIGH RISK / ELEVATED / NEUTRAL)."},
            {"name": "Single Ticker Earnings Risk","price_usdc": 0.35, "job_description": "Give me the earnings risk assessment for one ticker: implied move vs historical avg, estimate revisions, and whether to hold or trim into the event."},
        ],
        "provider_wallet": _addr("X_SENTIMENT_ADDRESS"),
        "x_handle": "@octodamusai ecosystem",
    },
    "TokenBot_NYSE_Base": {
        "agent":       "TokenBot_NYSE_Base",
        "description": "Paper trading agent for tokenized NYSE stocks on Base. Dinari dShares: dAAPL, dTSLA, dNVDA, dMSFT, dGOOGL. Building the live-trade track record.",
        "services": [
            {"name": "Tokenized Stock Signal",   "price_usdc": 0.25, "job_description": "Give me TokenBot's current paper trading signal for tokenized NYSE stocks on Base. Which dShares have 2+ signal confluence? Include direction, conviction score (1-5), and current position status."},
            {"name": "Dinari Paper Trade Status", "price_usdc": 0.25, "job_description": "Give me TokenBot's current paper portfolio: open positions with entry/target/stop, realized P&L, win/loss record, and top conviction trade for the next session."},
        ],
        "provider_wallet": _addr("TOKENBOT_NYSE_ADDRESS"),
        "x_handle": "@octodamusai ecosystem",
    },
    "Ben": {
        "agent":       "Ben",
        "description": "Autonomous profit agent. Prediction market scanner, x402 service designer, bull trap / divergence analyst. Base wallet: Franklin.",
        "services": [
            {"name": "Fear Greed Divergence Signal",    "price_usdc": 0.35, "job_description": "Give me Ben's fear vs greed divergence signal for BTC — compression phase, days active, break conditions."},
            {"name": "BTC Contrarian Alert",            "price_usdc": 0.35, "job_description": "Give me Ben's BTC contrarian alert: crowd positioning, divergence score, edge score, recommended stance."},
            {"name": "Crypto Divergence Brief",         "price_usdc": 0.75, "job_description": "Give me Ben's full crypto divergence brief: BTC + ETH + SOL sentiment vs price vs fear — actionable summary."},
            {"name": "Price Velocity Divergence",       "price_usdc": 0.50, "job_description": "Fire when BTC/ETH 24h price change exceeds +2% AND Fear stays below 35 — return compression phase and break conditions."},
        ],
        "provider_wallet": _addr("FRANKLIN_WALLET_ADDRESS"),
        "x_handle": "@octodamusai ecosystem",
    },
}


def check_ethereum_gas(eth_price_usd: float = 2400.0) -> dict:
    """
    Read-only gas price check for Ethereum mainnet.
    Returns dict with gwei, risk level, and whether a write should proceed.
    Reading (eth_call) is always free — only check before WRITES.
    ACP payments on Base are unaffected by Ethereum gas.
    """
    import urllib.request, urllib.error
    gwei = 0.0
    source = "unavailable"
    try:
        req = urllib.request.Request(
            _ETH_RPC,
            data=b'{"jsonrpc":"2.0","method":"eth_gasPrice","params":[],"id":1}',
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            import json as _json
            hex_price = _json.loads(resp.read()).get("result", "0x0")
            gwei = int(hex_price, 16) / 1e9
            source = "rpc"
    except Exception:
        pass

    if gwei == 0:
        return {"gwei": None, "risk": "UNKNOWN", "proceed": False,
                "note": "Gas data unavailable — do not write to Ethereum without confirming gas"}

    simple_tx_cost_usd = gwei * 21_000 / 1e9 * eth_price_usd
    risk = "HIGH" if gwei > _ETH_GAS_LIMIT_GWEI else "MEDIUM" if gwei > 20 else "LOW"
    proceed = gwei <= _ETH_GAS_LIMIT_GWEI
    return {
        "gwei": round(gwei, 1),
        "risk": risk,
        "proceed": proceed,
        "simple_tx_cost_usd": round(simple_tx_cost_usd, 4),
        "note": (
            f"Gas: {gwei:.1f} gwei | Risk: {risk} | "
            f"Simple tx cost: ~${simple_tx_cost_usd:.3f} | "
            + ("OK to write" if proceed else f"BLOCKED — gas > {_ETH_GAS_LIMIT_GWEI} gwei ceiling")
        ),
    }


def get_calling_card(agent_name: str) -> str:
    card = AGENT_CARDS.get(agent_name)
    if not card:
        return ""
    services_str = " | ".join(
        f"{s['name']} ${s['price_usdc']:.2f}" for s in card["services"]
    )
    return (
        f"\n\n---CALLING_CARD---\n"
        f"From: {card['agent']} ({card['description']})\n"
        f"Services I offer: {services_str}\n"
        f"Hire me via ACP: send job to wallet {card['provider_wallet']}\n"
        f"---END_CARD---"
    )


def get_octodamus_card_for_deliverable() -> dict:
    card = AGENT_CARDS["Octodamus"]
    return {
        "from_agent":      card["agent"],
        "description":     card["description"],
        "services":        card["services"],
        "provider_wallet": card["provider_wallet"],
        "x_handle":        card["x_handle"],
        "note": "Want more intel? Create an ACP job to the provider_wallet above with your request.",
    }


def check_agent_wallet(agent_name: str) -> str:
    """Return the USDC balance for the named agent's wallet on Base."""
    try:
        from web3 import Web3
    except ImportError:
        return "ERROR: web3 not installed. Run: pip install web3"
    addr_key = _BUYER_ADDR_MAP.get(agent_name)
    if not addr_key:
        return f"Unknown agent: {agent_name}"
    addr = _addr(addr_key)
    if not addr:
        return f"{addr_key} not configured in .octo_secrets"
    _rpc_list = [_BASE_RPC, "https://base.llamarpc.com", "https://base-mainnet.public.blastapi.io"]
    last_err = ""
    for rpc in _rpc_list:
        try:
            w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 8}))
            usdc = w3.eth.contract(
                address=Web3.to_checksum_address(_USDC_ADDRESS),
                abi=_USDC_APPROVE_ABI,
            )
            raw = usdc.functions.balanceOf(Web3.to_checksum_address(addr)).call()
            balance = raw / 1_000_000
            return f"{agent_name} wallet: {addr}\nUSDC balance: ${balance:.4f}"
        except Exception as e:
            last_err = str(e)
            continue
    return f"Wallet check failed (all RPCs exhausted): {last_err}"


# ── Sub-agent direct payment helpers ──────────────────────────────────────────

def _load_revenue_log() -> dict:
    try:
        if _REVENUE_LOG_FILE.exists():
            data = json.loads(_REVENUE_LOG_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _log_agent_revenue(agent_name: str, amount_usdc: float, service_name: str, buyer_agent: str) -> None:
    log = _load_revenue_log()
    if agent_name not in log:
        log[agent_name] = []
    log[agent_name].append({
        "amount_usdc": amount_usdc,
        "service":     service_name,
        "buyer":       buyer_agent,
        "date":        datetime.now().isoformat(),
    })
    _REVENUE_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    _REVENUE_LOG_FILE.write_text(json.dumps(log, indent=2), encoding="utf-8")


def _get_sub_agent_intel(target_agent: str) -> str:
    """Return the most recent draft file from the target agent's drafts directory."""
    drafts_dir = _SUB_AGENT_DRAFTS.get(target_agent)
    if not drafts_dir or not drafts_dir.exists():
        return f"[No drafts directory found for {target_agent}]"
    drafts = sorted(drafts_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not drafts:
        drafts = sorted(drafts_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not drafts:
        return f"[No draft files found in {drafts_dir}]"
    try:
        content = drafts[0].read_text(encoding="utf-8")
        return f"[Source: {drafts[0].name}]\n\n{content}"
    except Exception as e:
        return f"[Error reading draft: {e}]"


def _transfer_usdc(buyer_agent: str, to_addr: str, amount_usdc: float) -> str:
    """
    Transfer USDC directly from buyer_agent's wallet to to_addr on Base.
    Returns 'OK:<tx_hash>' on success, 'ERROR:...' or 'BLOCKED:...' on failure.
    """
    try:
        from web3 import Web3
        from eth_account import Account
    except ImportError:
        return "ERROR: web3 not installed"

    sec = _secrets()
    key_name  = _BUYER_KEY_MAP.get(buyer_agent)
    addr_name = _BUYER_ADDR_MAP.get(buyer_agent)
    if not key_name or not addr_name:
        return f"ERROR: no key mapping for '{buyer_agent}'"

    raw_key    = sec.get(key_name, "")
    buyer_addr = sec.get(addr_name, "") or os.environ.get(addr_name, "")
    if not raw_key:
        return f"ERROR: private key '{key_name}' not in .octo_secrets"
    if not buyer_addr:
        return f"ERROR: address '{addr_name}' not in .octo_secrets"

    private_key   = _normalize_key(raw_key)
    buyer_addr_cs = Web3.to_checksum_address(buyer_addr)
    to_addr_cs    = Web3.to_checksum_address(to_addr)
    amount_raw    = int(amount_usdc * 1_000_000)

    _rpc_list = [_BASE_RPC, "https://base.llamarpc.com", "https://base-mainnet.public.blastapi.io"]
    last_err = ""
    for rpc in _rpc_list:
        try:
            w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 10}))
            if not w3.is_connected():
                last_err = "not connected"
                continue

            usdc      = w3.eth.contract(address=Web3.to_checksum_address(_USDC_ADDRESS), abi=_USDC_TRANSFER_ABI)
            nonce     = w3.eth.get_transaction_count(buyer_addr_cs)
            gas_price = w3.eth.gas_price

            tx = usdc.functions.transfer(to_addr_cs, amount_raw).build_transaction({
                "from":     buyer_addr_cs,
                "nonce":    nonce,
                "gas":      100_000,
                "gasPrice": gas_price,
                "chainId":  8453,
            })
            signed  = Account.sign_transaction(tx, private_key)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)

            if receipt.status != 1:
                return f"ERROR: USDC transfer reverted. hash={tx_hash.hex()}"

            return f"OK:{tx_hash.hex()}"

        except Exception as e:
            err = str(e)
            if "insufficient funds" in err.lower():
                return (
                    f"BLOCKED: {buyer_agent} has no ETH for gas on Base. "
                    f"Fund {buyer_addr_cs} with ETH then retry."
                )
            last_err = err
            continue

    return f"ERROR: all RPCs failed: {last_err}"


def _buy_intel_direct(buyer_agent: str, target_agent: str, service: dict) -> str:
    """
    Direct-payment intel purchase for sub-agent targets.
    Transfers USDC to provider wallet, reads latest draft as intel, logs revenue.
    No ACP job created — payment is final on transfer.
    """
    target_card     = AGENT_CARDS[target_agent]
    provider_wallet = target_card.get("provider_wallet", "")
    amount_usdc     = service["price_usdc"]

    if not provider_wallet:
        return f"ERROR: {target_agent} has no provider wallet configured in .octo_secrets"

    result = _transfer_usdc(buyer_agent, provider_wallet, amount_usdc)

    if result.startswith("ERROR") or result.startswith("BLOCKED"):
        return result

    tx_hash = result[3:]  # strip "OK:"
    _log_agent_revenue(target_agent, amount_usdc, service["name"], buyer_agent)

    intel = _get_sub_agent_intel(target_agent)

    return (
        f"INTEL PURCHASED: '{service['name']}' from {target_agent} | ${amount_usdc:.2f} USDC\n"
        f"Payment tx: {tx_hash[:20]}... -> {provider_wallet[:14]}...\n"
        f"Revenue logged for {target_agent}.\n\n"
        f"--- INTEL ---\n{intel}"
    )


# ── Native web3 ACP job creation ───────────────────────────────────────────────

def _load_pending_jobs() -> list:
    try:
        return json.loads(_PENDING_JOBS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_pending_jobs(jobs: list) -> None:
    _PENDING_JOBS_FILE.write_text(json.dumps(jobs, indent=2), encoding="utf-8")


def _normalize_key(raw: str) -> str:
    """Ensure private key has 0x prefix."""
    raw = raw.strip()
    return raw if raw.startswith("0x") else f"0x{raw}"


def _buy_intel_native(buyer_agent: str, provider_wallet: str, description: str, price_usdc: float) -> str:
    """
    Sign and broadcast an ACP createJob transaction from the buyer agent's
    own private key. Returns a status string with the job ID or error.
    """
    try:
        from web3 import Web3
        from eth_account import Account
    except ImportError:
        return "ERROR: web3/eth_account not installed. Run: pip install web3"

    _GAS_LIMIT = 600_000  # createJob stores description string on-chain; ~400k gas for long descriptions

    sec = _secrets()
    key_name = _BUYER_KEY_MAP.get(buyer_agent)
    addr_name = _BUYER_ADDR_MAP.get(buyer_agent)
    if not key_name or not addr_name:
        return f"ERROR: no key mapping for buyer agent '{buyer_agent}'"

    raw_key = sec.get(key_name, "")
    buyer_addr = sec.get(addr_name, "") or os.environ.get(addr_name, "")
    if not raw_key:
        return f"ERROR: private key '{key_name}' not found in .octo_secrets"
    if not buyer_addr:
        return f"ERROR: wallet address '{addr_name}' not found in .octo_secrets"

    private_key = _normalize_key(raw_key)
    buyer_addr_cs = Web3.to_checksum_address(buyer_addr)
    provider_cs   = Web3.to_checksum_address(provider_wallet)
    hook_cs       = Web3.to_checksum_address(_HOOK_ADDRESS)
    acp_cs        = Web3.to_checksum_address(_ACP_CONTRACT)

    w3 = Web3(Web3.HTTPProvider(_BASE_RPC))
    if not w3.is_connected():
        return "ERROR: cannot connect to Base mainnet RPC"

    contract = w3.eth.contract(address=acp_cs, abi=_ACP_ABI)

    expired_at = int(time.time()) + 86400  # 24h from now
    nonce = w3.eth.get_transaction_count(buyer_addr_cs)
    gas_price = w3.eth.gas_price

    try:
        tx = contract.functions.createJob(
            provider_cs,
            buyer_addr_cs,   # evaluator = self
            expired_at,
            description[:800],
            hook_cs,
        ).build_transaction({
            "from":     buyer_addr_cs,
            "nonce":    nonce,
            "gas":      _GAS_LIMIT,
            "gasPrice": gas_price,
            "chainId":  8453,
        })
    except Exception as e:
        return f"ERROR building createJob tx: {e}"

    try:
        signed = Account.sign_transaction(tx, private_key)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
    except Exception as e:
        err = str(e)
        if "insufficient funds" in err.lower():
            eth_cost = _GAS_LIMIT * gas_price / 1e18
            return (
                f"BLOCKED: {buyer_agent} wallet has no ETH for gas. "
                f"Need ~{eth_cost:.6f} ETH (~${eth_cost*2400:.4f}) on Base. "
                f"Fund {buyer_addr_cs} then retry. "
                f"Shortcut: python octo_acp_seed_gas.py --from-key <KEY_WITH_ETH>"
            )
        return f"ERROR broadcasting createJob tx: {e}"

    if receipt.status != 1:
        return f"ERROR: createJob tx reverted. hash={tx_hash.hex()}"

    # Extract jobId from JobCreated event (topics[1] = indexed jobId)
    job_id = None
    job_created_topic = Web3.keccak(text="JobCreated(uint256,address,address,address,uint256,address)").hex()
    for log in receipt.logs:
        if log.address.lower() == acp_cs.lower() and log.topics and log.topics[0].hex() == job_created_topic:
            job_id = int(log.topics[1].hex(), 16)
            break

    if job_id is None:
        return f"WARNING: tx mined but could not extract jobId. hash={tx_hash.hex()}"

    # Persist to pending jobs so the funder can pick it up
    pending = _load_pending_jobs()
    pending.append({
        "job_id":        job_id,
        "buyer_agent":   buyer_agent,
        "buyer_addr":    buyer_addr_cs,
        "provider_addr": provider_cs,
        "price_usdc":    price_usdc,
        "buyer_key":     key_name,
        "created_at":    int(time.time()),
        "status":        "pending_budget",
        "tx_hash":       tx_hash.hex(),
    })
    _save_pending_jobs(pending)

    return (
        f"Job #{job_id} created on-chain. "
        f"Buyer: {buyer_agent} ({buyer_addr_cs[:12]}...) "
        f"Provider: {provider_cs[:12]}... "
        f"Waiting for provider to set budget (~${price_usdc:.2f} USDC expected). "
        f"tx={tx_hash.hex()[:16]}..."
    )


# ── Public buy_intel API ───────────────────────────────────────────────────────

def buy_intel(buyer_agent: str, target_agent: str, service_name: str) -> str:
    """
    Buy intel from another ecosystem agent via native on-chain ACP job.
    Embeds buyer's calling card so provider can hire them back.

    Enforces a 5-day cooldown per (buyer, target, service) tuple.
    Agents must distill intel into core memory and move to NEW intel next session.
    """
    # Cooldown gate: block repeat buys of the same service
    block_msg = _check_intel_cooldown(buyer_agent, target_agent, service_name)
    if block_msg:
        return block_msg

    buyer_card  = AGENT_CARDS.get(buyer_agent)
    target_card = AGENT_CARDS.get(target_agent)

    if not buyer_card:
        return f"Unknown buyer agent: {buyer_agent}"
    if not target_card:
        return f"Unknown target agent: {target_agent}"

    provider_wallet = target_card.get("provider_wallet", "")
    if not provider_wallet:
        return f"No wallet address for {target_agent}"

    # Normalize: strip leading "Get " so "Get BTC Market Signal" resolves to "BTC Market Signal"
    normalized = service_name.strip()
    if normalized.lower().startswith("get "):
        normalized = normalized[4:].strip()
    service = next((s for s in target_card["services"] if s["name"] == normalized), None)
    if not service:
        available = ", ".join(f"Get {s['name']}" for s in target_card["services"])
        return f"Service '{service_name}' not found for {target_agent}. Available (use Get prefix or exact name): {available}"

    # Route: Octodamus + Ben (Franklin wallet) → ACP worker handles fulfillment.
    # All sub-agents → direct USDC transfer + serve latest draft immediately.
    if provider_wallet.lower() == _OCTODAMUS_WALLET.lower():
        description = service["job_description"] + get_calling_card(buyer_agent)
        result = _buy_intel_native(buyer_agent, provider_wallet, description, service["price_usdc"])
    else:
        result = _buy_intel_direct(buyer_agent, target_agent, service)

    # Log cooldown entry on success
    if not result.startswith("ERROR") and not result.startswith("BLOCKED"):
        key = f"{buyer_agent}|{target_agent}|{service_name}"
        log = _load_intel_log()
        log[key] = datetime.now().isoformat()
        _save_intel_log(log)

    return result


TEAM_ROSTER = """OCTODAMUS ECOSYSTEM — 8 AGENTS, ONE TEAM:
- Octodamus (@octodamusai): The oracle. 11-signal AI consensus, Polymarket edges, crypto + macro intelligence. Provider.
- OctoBoto: The autonomous trader. Executes on Polymarket using Octodamus signal. Builds the public track record.
- Agent_Ben: The profit agent. Designs x402 services, trades prediction markets, runs competitor intelligence.
- NYSE_MacroMind: Macro regime. Yield curve, M2, VIX, DXY, Fed probability. Daily RISK-ON/OFF/NEUTRAL read.
- NYSE_StockOracle: Congressional signals. Finance Committee insider buys/sells on NVDA, TSLA, AAPL, MSFT.
- NYSE_Tech_Agent: Tokenization + regulatory. Chainlink equity feeds on Base, SEC filings, DTC eligibility.
- Order_ChainFlow: On-chain flow. Binance cumulative delta, Base DEX activity, whale wallet movements.
- NYSE_EarningsEdge: Earnings catalyst intelligence. Implied move vs historical, estimate revisions, pre-earnings positioning verdict.

Every agent buys from and sells to the others via ACP. Calling cards flow back with every buy.
Shared goal: become the dominant AI intelligence network for crypto and tokenized equity markets.

ECOSYSTEM BUY DISCIPLINE (enforced by a 5-day cooldown in octo_agent_cards.buy_intel):
- Buy intel to fill a NEW gap in your knowledge. Not to repeat what you already know.
- After buying, distill the learning into core memory via update_core_memory.
- Once distilled, you do NOT need to rebuy the same service -- the knowledge is yours.
- Each session: ask "what do I not know yet?" -- buy THAT. Not what you bought last session.
- Repeat buys of the same service are blocked for 5 days. Use the intel_purchase_log.json to plan."""


def list_ecosystem_services() -> str:
    """List all purchasable services across the ecosystem."""
    lines = ["=== OCTODAMUS ECOSYSTEM SERVICES ===\n", TEAM_ROSTER, ""]
    lines.append("NOTE: Pass service name with or without 'Get ' prefix — both resolve correctly.")
    lines.append("")
    for agent_name, card in AGENT_CARDS.items():
        lines.append(f"{agent_name}: {card['description']}")
        for s in card["services"]:
            lines.append(f"  - Get {s['name']}: ${s['price_usdc']:.2f}")
        lines.append("")
    return "\n".join(lines)


def cleanup_stuck_jobs() -> str:
    """
    Remove sub-agent pending_budget jobs that will never be fulfilled.
    The ACP worker only handles provider=OCTODAMUS_WALLET; all other providers
    were created under the old ACP flow and are permanently stuck.
    """
    jobs = _load_pending_jobs()
    before = len(jobs)
    kept = [
        j for j in jobs
        if j.get("provider_addr", "").lower() == _OCTODAMUS_WALLET.lower()
        or j.get("status") not in ("pending_budget",)
    ]
    removed = before - len(kept)
    if removed:
        _save_pending_jobs(kept)
    return f"Cleaned {removed} stuck sub-agent jobs. {len(kept)} jobs remain (all provider={_OCTODAMUS_WALLET[:12]}...)."

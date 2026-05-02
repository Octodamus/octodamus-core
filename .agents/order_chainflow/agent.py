"""
.agents/order_chainflow/agent.py
Order_ChainFlow — On-Chain Order Flow Intelligence Agent

Tracks real money moving on-chain: Binance 24h buy/sell delta, DEX volume on Base,
whale wallet movements, bridge flows. The infrastructure layer for AI agents
trading tokenized stocks when NYSE goes on-chain (Q4 2026).

Usage:
  python .agents/order_chainflow/agent.py
  python .agents/order_chainflow/agent.py --dry
  python .agents/order_chainflow/agent.py --asset ETH
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
CORE_MEMORY  = ROOT / "data" / "memory" / "order_chainflow_core.md"

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
        return read_core_memory("order_chainflow")
    except Exception:
        if CORE_MEMORY.exists():
            return CORE_MEMORY.read_text(encoding="utf-8")
        return "No core memory yet."


def tool_get_session_history() -> str:
    history = _load_history()
    if not history:
        return "No session history yet."
    lines = [f"Order_ChainFlow history ({len(history)} sessions):"]
    for h in history[-5:]:
        lines.append(f"\n[{h.get('date','?')} #{h.get('session','?')}]")
        if h.get("lesson"):
            lines.append(f"  Lesson: {h['lesson']}")
        if h.get("top_signal"):
            lines.append(f"  Top signal: {h['top_signal']}")
    return "\n".join(lines)


def tool_get_binance_delta(symbol: str = "BTCUSDT") -> str:
    """Get Binance 24h cumulative buy/sell delta for an asset."""
    sys.path.insert(0, str(ROOT))
    try:
        from octo_binance_delta import get_delta_signal, delta_context_str
        sym = symbol.upper()
        if not sym.endswith("USDT"):
            sym += "USDT"
        d = get_delta_signal(sym)
        if not d:
            return f"Delta unavailable for {sym}"
        return delta_context_str(d) + f"\n  Raw ratio: {d['delta_ratio']:.4f} | Score: {d['score']:+d} | Accel: {d['acceleration']}"
    except Exception as e:
        return f"Binance delta error: {e}"


def tool_get_multi_delta() -> str:
    """Get Binance 24h delta for BTC, ETH, SOL simultaneously."""
    sys.path.insert(0, str(ROOT))
    try:
        from octo_binance_delta import get_multi_delta, delta_context_str
        signals = get_multi_delta(["BTCUSDT", "ETHUSDT", "SOLUSDT"])
        if not signals:
            return "No delta signals available."
        parts = []
        for sym, d in signals.items():
            asset = sym.replace("USDT","")
            score_label = "BUYERS" if d["score"] > 0 else ("SELLERS" if d["score"] < 0 else "NEUTRAL")
            parts.append(f"{asset}: {d['delta_ratio']:.1%} buy-side | {score_label} | {d['acceleration']}")
        return "BINANCE 24H DELTA SCAN:\n" + "\n".join(f"  {p}" for p in parts)
    except Exception as e:
        return f"Multi-delta error: {e}"


def tool_get_dex_flow(chain: str = "base") -> str:
    """Get top DEX volume and recent activity on Base or Ethereum."""
    try:
        import httpx
        # DexScreener API — free, no auth
        if chain.lower() == "base":
            r = httpx.get("https://api.dexscreener.com/latest/dex/tokens/0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
                          timeout=8)  # USDC on Base — shows Base DEX activity
        else:
            r = httpx.get("https://api.dexscreener.com/latest/dex/search?q=ETH USDC&chainId=ethereum", timeout=8)

        if r.status_code != 200:
            return f"DexScreener returned {r.status_code}"

        data = r.json()
        pairs = data.get("pairs") or []
        if not pairs:
            return "No DEX pairs found."

        lines = [f"DEX FLOW ({chain.upper()} chain — top pairs by volume):"]
        for p in pairs[:5]:
            name  = p.get("baseToken", {}).get("symbol", "?")
            vol   = p.get("volume", {}).get("h24", 0)
            price = p.get("priceUsd", "?")
            chg   = p.get("priceChange", {}).get("h24", 0)
            dex   = p.get("dexId", "?")
            lines.append(f"  {name} ({dex}): ${vol:,.0f} 24h vol | ${price} | {chg:+.1f}% 24h")
        return "\n".join(lines)
    except Exception as e:
        return f"DEX flow unavailable: {e}"


def tool_get_whale_activity(chain: str = "base") -> str:
    """Check large recent transactions on Base or Ethereum via public APIs."""
    try:
        import httpx
        # Etherscan/Basescan for large USDC transfers on Base
        # Using Basescan public API (free, limited rate)
        r = httpx.get(
            "https://api.basescan.org/api",
            params={
                "module": "account",
                "action": "tokentx",
                "contractaddress": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
                "page": 1, "offset": 20, "sort": "desc"
            },
            timeout=10
        )
        if r.status_code != 200:
            return f"Basescan returned {r.status_code}"

        txs = r.json().get("result", [])
        if not txs or not isinstance(txs, list):
            return "No recent transactions found."

        whales = []
        for tx in txs:
            try:
                value = int(tx.get("value", 0)) / 1e6  # USDC 6 decimals
                if value >= 100_000:  # $100k+ threshold
                    whales.append({
                        "amount": value,
                        "from": tx.get("from","")[:12] + "...",
                        "to":   tx.get("to","")[:12] + "...",
                        "hash": tx.get("hash","")[:16] + "...",
                    })
            except Exception:
                continue

        if not whales:
            return "No whale transactions (>$100k USDC) in recent Base activity."

        lines = ["WHALE ACTIVITY (Base, USDC transfers >$100k):"]
        for w in whales[:5]:
            lines.append(f"  ${w['amount']:,.0f} | {w['from']} -> {w['to']}")
        return "\n".join(lines)
    except Exception as e:
        return f"Whale data unavailable: {e}"


def tool_get_bridge_flows() -> str:
    """Check USDC bridge flows into Base (proxy for capital deployment)."""
    try:
        import httpx
        # DexScreener token info for USDC on Base shows liquidity changes
        r = httpx.get("https://api.dexscreener.com/latest/dex/tokens/0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
                      timeout=8)
        if r.status_code != 200:
            return "Bridge flow data unavailable."
        pairs = r.json().get("pairs") or []
        total_liq = sum(float(p.get("liquidity", {}).get("usd", 0)) for p in pairs[:10])
        total_vol = sum(float(p.get("volume", {}).get("h24", 0)) for p in pairs[:10])
        return (f"BASE CHAIN USDC LIQUIDITY:\n"
                f"  Total liquidity (top 10 pairs): ${total_liq:,.0f}\n"
                f"  24h volume: ${total_vol:,.0f}\n"
                f"  Activity ratio: {total_vol/total_liq:.2f}x" if total_liq > 0 else "  No liquidity data")
    except Exception as e:
        return f"Bridge flow unavailable: {e}"


_BASE_RPC = "https://mainnet.base.org"

# Keccak256 hashes for common event signatures — computed offline, no library needed
_TOPIC0_MAP: dict[str, str] = {
    "Transfer(address,address,uint256)":                              "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef",
    "Swap(address,uint256,uint256,uint256,uint256,address)":          "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822",
    "Swap(address,address,int256,int256,uint160,uint128,int24)":      "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67",
    "Mint(address,address,int24,int24,uint128,uint256,uint256)":      "0x7a53080ba414158be7ec69b987b5fb7d07dee101fe85488f0853ae16239d0bde",
    "Burn(address,int24,int24,uint128,uint256,uint256)":              "0x0c396cd989a39f4459b5fa1aed6a9a8dcdbc45908acfd67e028cd568da98982c",
    "Sync(uint112,uint112)":                                          "0x1c411e9a96e071241c2f21f7726b17ae89e3cab4c78be50e062b03a9fffbbad1",
}


def tool_query_base_events(contract: str, event_sig: str = "", blocks_back: int = 1000) -> str:
    """Query raw event logs for any Base contract via the public Base RPC (eth_getLogs).

    Uses the free Base mainnet public RPC — no API key required.
    event_sig: full event signature string, e.g. 'Transfer(address,address,uint256)'
    blocks_back: how many recent blocks to scan (default 1000 ~ last ~30 min on Base).

    Common event signatures:
      Swap (Uniswap V3 / Aerodrome CL): Swap(address,address,int256,int256,uint160,uint128,int24)
      Swap (Uniswap V2 / Aerodrome V2): Swap(address,uint256,uint256,uint256,uint256,address)
      Transfer (ERC-20):                Transfer(address,address,uint256)
    """
    try:
        import httpx
        import json as _json

        # Resolve topic0
        topic0 = None
        if event_sig.strip():
            topic0 = _TOPIC0_MAP.get(event_sig.strip())
            if not topic0:
                # Try runtime keccak if a library is available
                sig_bytes = event_sig.strip().encode("utf-8")
                try:
                    from Crypto.Hash import keccak as _kek
                    k = _kek.new(digest_bits=256); k.update(sig_bytes)
                    topic0 = "0x" + k.hexdigest()
                except ImportError:
                    try:
                        import sha3 as _sha3
                        h = _sha3.keccak_256(); h.update(sig_bytes)
                        topic0 = "0x" + h.hexdigest()
                    except ImportError:
                        return f"Unknown event signature and no keccak library available: {event_sig}"

        # Get current block number
        r_blk = httpx.post(_BASE_RPC, json={"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}, timeout=8)
        current_block = int(r_blk.json()["result"], 16)
        from_block_hex = hex(max(0, current_block - blocks_back))

        log_filter: dict = {
            "fromBlock": from_block_hex,
            "toBlock":   "latest",
            "address":   contract.strip().lower(),
        }
        if topic0:
            log_filter["topics"] = [topic0]

        r = httpx.post(
            _BASE_RPC,
            json={"jsonrpc":"2.0","method":"eth_getLogs","params":[log_filter],"id":2},
            timeout=15,
        )
        rj = r.json()
        if "error" in rj:
            return f"RPC error: {rj['error']}"

        logs = rj.get("result", [])
        if not logs:
            return f"No events for {contract[:12]}... in last {blocks_back} blocks."

        lines = [f"BASE EVENTS ({event_sig or 'all'}) | {contract[:12]}... | last {blocks_back} blocks:"]
        lines.append(f"  {len(logs)} event(s) found (showing up to 10)")
        for log in logs[:10]:
            blk_n    = int(log.get("blockNumber", "0x0"), 16)
            tx       = log.get("transactionHash", "")[:18] + "..."
            topics   = log.get("topics", [])
            from_a   = ("0x" + topics[1][-40:]) if len(topics) > 1 else "?"
            to_a     = ("0x" + topics[2][-40:]) if len(topics) > 2 else "?"
            data_hex = log.get("data", "0x")
            # For Transfer: data is value (uint256)
            value_str = ""
            if event_sig.startswith("Transfer") and data_hex != "0x" and len(data_hex) >= 66:
                try:
                    raw = int(data_hex, 16)
                    value_str = f" | ${raw/1e6:,.2f} USDC" if raw < 1e15 else f" | {raw/1e18:.4f} ETH-equiv"
                except Exception:
                    pass
            lines.append(f"  blk={blk_n} tx={tx} from={from_a[:12]}.. to={to_a[:12]}..{value_str}")
        return "\n".join(lines)
    except Exception as e:
        return f"Base event query error: {e}"


def tool_get_dex_swap_volume(contract: str = "", asset_symbol: str = "USDC/WETH") -> str:
    """Count Swap events on a Base DEX pool in the last 500 blocks (~15 min).
    Useful for real-time activity check on Aerodrome or Uniswap V3 pools.
    contract: pool address on Base. Leave blank to use the Aerodrome USDC/WETH CL pool.
    """
    # Default: Aerodrome CL USDC/WETH pool on Base (0.05% fee tier, high volume)
    pool = contract.strip() or "0xb2cc224c1c9feE385f8ad6a55b4d94E92359DC59"
    result = tool_query_base_events(
        contract=pool,
        event_sig="Swap(address,address,int256,int256,uint160,uint128,int24)",
        blocks_back=500,
    )
    return result.replace("BASE EVENTS", f"SWAP ACTIVITY ({asset_symbol})")


def tool_draft_x_post(context: str) -> str:
    """Draft an Order_ChainFlow X post. Quantitative voice. Data-first."""
    sys.path.insert(0, str(ROOT))
    try:
        import anthropic
        key = _secrets().get("ANTHROPIC_API_KEY","")
        client = anthropic.Anthropic(api_key=key)
        r = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=120,
            system="""You are Order_ChainFlow — an on-chain order flow intelligence agent.
Voice: quantitative, follows the money, no narrative. Lead with the number.
One flow signal + one implication. Under 280 chars. No hashtags. No emojis.
End: 'Order flow signal: [BULLISH/BEARISH/NEUTRAL] — Order_ChainFlow (@octodamusai ecosystem)'""",
            messages=[{"role": "user", "content": f"Write an Order_ChainFlow X post from:\n{context[:500]}"}]
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


def tool_record_session(lesson: str, top_signal: str = "", what_worked: str = "") -> str:
    history = _load_history()
    state   = _load_state()
    entry = {
        "session":    state.get("sessions", 0),
        "date":       datetime.now().strftime("%Y-%m-%d"),
        "lesson":     lesson,
        "top_signal": top_signal,
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
        append_core_memory("order_chainflow", section, content)
        return f"Core memory updated: [{section}]"
    except Exception as e:
        return f"Memory update failed: {e}"


def tool_check_wallet() -> str:
    """Check Order_ChainFlow's USDC wallet balance on Base."""
    sys.path.insert(0, str(ROOT))
    from octo_agent_cards import check_agent_wallet
    return check_agent_wallet("Order_ChainFlow")


def tool_check_x402_revenue() -> str:
    """Check how much USDC this agent's x402 endpoints have earned. Reads data/x402_agent_revenue.json."""
    rev_file = ROOT / "data" / "x402_agent_revenue.json"
    agent_name = "Order_ChainFlow"
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
    agent_name = "Order_ChainFlow"
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
    return buy_intel("Order_ChainFlow", target_agent, service_name)


def tool_list_ecosystem_services() -> str:
    """List all purchasable services across the Octodamus ecosystem."""
    sys.path.insert(0, str(ROOT))
    from octo_agent_cards import list_ecosystem_services
    return list_ecosystem_services()


# ── Tool registry ──────────────────────────────────────────────────────────────

TOOLS = [
    {"name": "read_core_memory",    "description": "Read Order_ChainFlow's memory. Call first.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_session_history", "description": "Past session lessons.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_binance_delta",   "description": "Binance 24h buy/sell delta for one asset.", "input_schema": {"type": "object", "properties": {"symbol": {"type": "string", "description": "BTCUSDT, ETHUSDT, SOLUSDT", "default": "BTCUSDT"}}, "required": []}},
    {"name": "get_multi_delta",     "description": "Binance 24h delta for BTC+ETH+SOL simultaneously.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_dex_flow",        "description": "DEX volume and top pairs on Base or Ethereum.", "input_schema": {"type": "object", "properties": {"chain": {"type": "string", "description": "base or ethereum", "default": "base"}}, "required": []}},
    {"name": "get_whale_activity",  "description": "Large USDC transactions (>$100k) on Base.", "input_schema": {"type": "object", "properties": {"chain": {"type": "string", "default": "base"}}, "required": []}},
    {"name": "get_bridge_flows",    "description": "USDC bridge flows and liquidity on Base.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "query_base_events",   "description": "Query raw event logs for ANY Base contract via Basescan. Pass contract address + event signature (e.g. 'Transfer(address,address,uint256)'). Use to verify on-chain DEX swap activity, token transfers, or protocol events in real-time. More precise than DexScreener for specific contracts.", "input_schema": {"type": "object", "properties": {"contract": {"type": "string", "description": "Base contract address (0x...)"}, "event_sig": {"type": "string", "description": "Full event signature e.g. Transfer(address,address,uint256)", "default": ""}, "blocks_back": {"type": "integer", "description": "How many recent blocks to scan (1000 ~ 30 min)", "default": 1000}}, "required": ["contract"]}},
    {"name": "get_dex_swap_volume", "description": "Count Swap events on a Base DEX pool in the last ~15 min. Quick real-time activity pulse for any Aerodrome/Uniswap pool. Leave contract blank to use the default USDC/WETH Aerodrome pool.", "input_schema": {"type": "object", "properties": {"contract": {"type": "string", "description": "Pool contract address on Base", "default": ""}, "asset_symbol": {"type": "string", "description": "Label for the output", "default": "USDC"}}, "required": []}},
    {"name": "draft_x_post",        "description": "Draft an Order_ChainFlow X post.", "input_schema": {"type": "object", "properties": {"context": {"type": "string"}}, "required": ["context"]}},
    {"name": "save_draft",          "description": "Save a draft.", "input_schema": {"type": "object", "properties": {"filename": {"type": "string"}, "content": {"type": "string"}}, "required": ["filename", "content"]}},
    {"name": "list_drafts",         "description": "List drafts.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "record_session",      "description": "Record session lesson.", "input_schema": {"type": "object", "properties": {"lesson": {"type": "string"}, "top_signal": {"type": "string", "default": ""}, "what_worked": {"type": "string", "default": ""}}, "required": ["lesson"]}},
    {"name": "send_email",          "description": "Send email to owner.", "input_schema": {"type": "object", "properties": {"subject": {"type": "string"}, "body": {"type": "string"}}, "required": ["subject", "body"]}},
    {"name": "update_core_memory",      "description": "Append distilled lessons to your persistent core memory. Call before record_session. Section='Distilled YYYY-MM-DD'. Content: 3-5 compressed bullets worth keeping across all future sessions.", "input_schema": {"type": "object", "properties": {"section": {"type": "string"}, "content": {"type": "string"}}, "required": ["section", "content"]}},
    {"name": "buy_ecosystem_intel",     "description": "Buy intel from another Octodamus ecosystem agent via ACP. Your calling card is embedded so they can hire you back.", "input_schema": {"type": "object", "properties": {"target_agent": {"type": "string", "description": "Octodamus, NYSE_MacroMind, NYSE_StockOracle, NYSE_Tech_Agent, X_Sentiment_Agent"}, "service_name": {"type": "string", "description": "Exact service name from list_ecosystem_services"}}, "required": ["target_agent", "service_name"]}},
    {"name": "check_wallet",            "description": "Check this agent's USDC wallet balance on Base. Run at session start and end to track wallet_delta.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "list_ecosystem_services", "description": "List all services for sale across the Octodamus ecosystem with prices.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "check_x402_revenue",    "description": "Check how much USDC your x402 endpoints have earned this month. Call at session start to track revenue trend.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "propose_new_offering",  "description": "Propose a new x402 or ACP offering based on this session's unique findings. Use when you identify a signal pattern other agents would pay for. Writes to proposals file + emails owner.", "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "endpoint_path": {"type": "string"}, "price_usdc": {"type": "number"}, "description": {"type": "string"}, "rationale": {"type": "string"}}, "required": ["name", "endpoint_path", "price_usdc", "description", "rationale"]}},
]

TOOL_HANDLERS = {
    "read_core_memory":    lambda i: tool_read_core_memory(),
    "get_session_history": lambda i: tool_get_session_history(),
    "get_binance_delta":   lambda i: tool_get_binance_delta(i.get("symbol","BTCUSDT")),
    "get_multi_delta":     lambda i: tool_get_multi_delta(),
    "get_dex_flow":        lambda i: tool_get_dex_flow(i.get("chain","base")),
    "get_whale_activity":  lambda i: tool_get_whale_activity(i.get("chain","base")),
    "get_bridge_flows":    lambda i: tool_get_bridge_flows(),
    "query_base_events":   lambda i: tool_query_base_events(i["contract"], i.get("event_sig",""), i.get("blocks_back",1000)),
    "get_dex_swap_volume": lambda i: tool_get_dex_swap_volume(i.get("contract",""), i.get("asset_symbol","USDC")),
    "draft_x_post":        lambda i: tool_draft_x_post(i["context"]),
    "save_draft":          lambda i: tool_save_draft(i["filename"], i["content"]),
    "list_drafts":         lambda i: tool_list_drafts(),
    "record_session":      lambda i: tool_record_session(i["lesson"], i.get("top_signal",""), i.get("what_worked","")),
    "send_email":              lambda i: tool_send_email(i["subject"], i["body"]),
    "update_core_memory":      lambda i: tool_update_core_memory(i["section"], i["content"]),
    "buy_ecosystem_intel":     lambda i: tool_buy_ecosystem_intel(i["target_agent"], i["service_name"]),
    "check_wallet":            lambda i: tool_check_wallet(),
    "list_ecosystem_services": lambda i: tool_list_ecosystem_services(),
    "check_x402_revenue":   lambda i: tool_check_x402_revenue(),
    "propose_new_offering": lambda i: tool_propose_new_offering(i["name"], i["endpoint_path"], i["price_usdc"], i["description"], i["rationale"]),
}

SYSTEM = """You are Order_ChainFlow — the on-chain order flow intelligence agent of the Octodamus ecosystem.

IDENTITY:
You track the actual money moving on-chain in real time. Not narratives, not opinions — flow.
Where capital moves before price moves. Where whales accumulate before crowds notice.
Specialties: Binance 24h cumulative buy/sell delta (most reliable signal), DEX volume on Base,
whale wallet movements, USDC bridge flows into ecosystems.
Voice: Quantitative. Follows the money without emotion. Think Renaissance Technologies —
the data tells the story, you just report it. Numbers first. Implication second. No hedging.

THE TOKENIZED NYSE PLAY:
When NYSE stocks tokenize on Base (expected Q4 2026), their order flow becomes on-chain.
Every block trade, every institutional buy, every accumulation pattern — trackable on Base.
Order_ChainFlow will be the agent that reads that flow for AI trading agents.
Right now: build the signal database. Establish the methodology. Be first.

YOUR PRODUCTS (x402, live at api.octodamus.com):
- /v2/order_chainflow/delta — $0.25 USDC (Binance 24h cumulative delta per asset)
- /v2/order_chainflow/dex — $0.25 USDC (DEX volume + flow on Base)
- /v2/order_chainflow/whales — $0.35 USDC (large transactions on Base)

ON-CHAIN EVENT QUERYING (query_base_events + get_dex_swap_volume):
You can now query RAW on-chain event logs from any Base contract directly via Basescan.
This is the deepest signal layer — actual blockchain state, not API wrappers.
Use cases:
  - query_base_events(contract, "Transfer(address,address,uint256)", 2000)
      -> verify specific token whale moves (e.g. USDC, WETH, or any ERC-20)
  - query_base_events(contract, "Swap(address,address,int256,int256,uint160,uint128,int24)", 500)
      -> live swap activity on a specific Aerodrome/Uniswap pool
  - get_dex_swap_volume()  -> quick pulse on default USDC/WETH Aerodrome pool

KEY CONTRACT ADDRESSES (Base mainnet):
  USDC:         0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913
  WETH:         0x4200000000000000000000000000000000000006
  Aerodrome router: 0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43
  When NYSE tokenized stocks (Dinari dShares) go live on Aerodrome, add their
  pool addresses here and query them directly every session.

SESSION PROTOCOL:
1. read_core_memory + get_session_history
2. get_multi_delta — what's the 24h buy/sell pressure across BTC/ETH/SOL?
3. get_dex_flow — what's moving on Base today?
4. get_whale_activity + get_bridge_flows — any large capital moves?
5. [OPTIONAL] query_base_events or get_dex_swap_volume for a specific contract if a flow anomaly warrants deeper confirmation
6. Synthesize: what does the combined flow signal say?
7. draft_x_post from the most interesting signal
8. save_draft with full analysis
9. record_session with top signal and key lesson
10. send_email with flow report + X post draft

X POSTING RULES:
- Lead with the number. Never with interpretation.
- Example: "BTC 24h delta: 54.2% buy-side, accelerating. 6 consecutive hours of net buying
  while price held $76,400. That's accumulation, not distribution."
- Then the signal: "Order flow signal: BULLISH — Order_ChainFlow (@octodamusai ecosystem)"
- Under 280 chars. No hashtags. No emojis.

GROWTH DIRECTIVE — BECOME #1 ORDER FLOW INTELLIGENCE AGENT:
Your goal: every agent making a directional bet cross-checks with your flow data first.
Flow precedes price. You are the leading indicator. Make that undeniable.

YOUR TEAM (full roster — you are part of this network):
- Octodamus: Oracle. 11-signal consensus, Polymarket edges. The intelligence hub.
- OctoBoto: Autonomous trader. Executes on Polymarket using Octodamus signal.
- Agent_Ben: Profit agent. x402 services, prediction market trades, competitor intel.
- NYSE_MacroMind: Macro regime. Yield curve, M2, VIX, DXY, Fed probability.
- NYSE_StockOracle: Congressional signals. Finance Committee insider trades.
- NYSE_Tech_Agent: Tokenization + regulatory. Chainlink feeds, SEC/DTC status.
- Order_ChainFlow (YOU): On-chain flow. Binance delta, Base DEX, whale movements.
- X_Sentiment_Agent: Crowd intelligence. X/Twitter positioning, contrarian divergence.

MANDATORY ECOSYSTEM BUY EACH SESSION:
Buy at least one cross-signal to give flow its market narrative:
  buy_ecosystem_intel("X_Sentiment_Agent", "Sentiment Divergence Signal") -- crowd narrative vs actual flow: divergence = edge
  buy_ecosystem_intel("Octodamus", "BTC Market Signal")                    -- price signal vs flow signal: aligned or split?
  buy_ecosystem_intel("NYSE_MacroMind", "Macro Regime Signal")             -- macro regime driving the flow direction?
Your calling card is embedded in every buy — providers discover your services and may hire you back.
Each buy is a completed ACP transaction: transaction volume is your reputation on-chain.

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
- FIRST TURN: check_wallet (record start balance). check_x402_revenue. get_session_history. Find the PREDICTION from last session. Did the delta pattern produce the predicted price move?
  Note the outcome explicitly -- you will log it in what_worked this session.
- LAST TURN: check_wallet again.
  Call update_core_memory with section="Distilled [date]" and 3-5 bullets:
    - Delta reading and what it predicted (e.g., "BTC delta 61% buy-side -> BULLISH 24h")
    - Whether last session's prediction proved correct or wrong
    - Any threshold that proved reliable or unreliable
    - One forward-looking prediction to validate next session
  Then record_session with structured fields:
    lesson:      "PREDICTION: [asset] [BULLISH/BEARISH] [timeframe] | SIGNAL: [delta threshold + pattern] | CONFIDENCE: [1-5]"
    what_worked: "LAST PREDICTION OUTCOME: [CORRECT/WRONG/PARTIAL] -- [actual price move vs. predicted]"
    wallet_delta: [end balance minus start balance in USDC -- negative means you spent more than earned]
  Good lesson:     "PREDICTION: BTC BULLISH 24h | SIGNAL: delta >58% buy-side 4h + sentiment bearish | CONFIDENCE: 4"
  Good what_worked: "LAST PREDICTION OUTCOME: CORRECT -- BTC +2.3% within 18h"
  Bad: "Flow was interesting today." -- useless, can't be validated, never write this.
- Each session build one more data point in the pattern -> outcome database.
- When cross-signal buy diverges from your flow read, that divergence IS the signal. Flag it.

CONFIDENCE CALIBRATION RULE (non-negotiable):
- HIGH CONVICTION: requires whale data confirming direction + retail delta >55% + at least one cross-signal aligned.
  If get_whale_activity returns "No whale transactions" or whale direction is neutral/missing:
  maximum regime confidence is MEDIUM, regardless of delta strength.
- MEDIUM conviction: retail delta is clear (>55% or <45%) but whale data unavailable or mixed.
- LOW conviction: single signal, delta near 50%, or conflicting data between tools.
Label your regime verdict accordingly: "RISK-ON (High conviction)" requires all three legs.
"ACCUMULATION (High conviction)" without whale data is a category error — call it MEDIUM.

PATH TO #1: Flow data is public. Pattern recognition built across sessions is your moat.
More sessions = sharper thresholds = signal that agents pay to access every time."""


def run_session(dry_run: bool = False, focus_asset: str = ""):
    import anthropic

    state       = _load_state()
    session_num = state.get("sessions", 0) + 1
    now         = datetime.now().strftime("%A %B %d %Y %I:%M %p")
    print(f"\n[Order_ChainFlow] Session #{session_num} | {now}")

    if dry_run:
        print("[Order_ChainFlow] DRY RUN")
        return

    key    = _secrets().get("ANTHROPIC_API_KEY","")
    client = anthropic.Anthropic(api_key=key)
    focus  = f" Focus asset: {focus_asset.upper()}." if focus_asset else ""
    messages = [{"role": "user", "content": f"Order_ChainFlow session #{session_num}. Date: {now}.{focus} Run your full session protocol."}]

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
            print(f"[Order_ChainFlow] Session complete at turn {turn+1}")
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
    ap.add_argument("--dry",   action="store_true")
    ap.add_argument("--asset", default="", help="Focus on specific asset (BTC/ETH/SOL)")
    args = ap.parse_args()
    run_session(dry_run=args.dry, focus_asset=args.asset)

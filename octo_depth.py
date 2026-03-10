"""
octo_depth.py
OctoDepth — On-Chain Oracle Mind

Reads Ethereum on-chain data via the Etherscan API (free tier).
Free API key at: etherscan.io/apis

Tracks:
  - ETH gas price (fast / standard)
  - Large ETH transfers (whale movements >500 ETH)
  - USDC/USDT stablecoin transfers (capital flow signal)
  - ETH supply stats (circulating, burned via EIP-1559)
  - Pending tx pool size (network demand)

Bitwarden key: AGENT - Octodamus - Etherscan API
Env var:       ETHERSCAN_API_KEY

Usage:
    from octo_depth import run_onchain_scan, format_depth_for_prompt
    depth = run_onchain_scan()
"""

import os
import time
import requests
from datetime import datetime

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

ETHERSCAN_BASE = "https://api.etherscan.io/api"

# Whale threshold: transfers larger than this in ETH
WHALE_ETH_THRESHOLD = 500

# Stablecoin contract addresses (for transfer volume tracking)
USDC_CONTRACT = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
USDT_CONTRACT = "0xdAC17F958D2ee523a2206206994597C13D831ec7"

# Gas thresholds (in Gwei)
GAS_HIGH = 50
GAS_ELEVATED = 20

_REQUEST_DELAY = 0.5


# ─────────────────────────────────────────────
# ETHERSCAN API HELPERS
# ─────────────────────────────────────────────

def _etherscan_call(module: str, action: str, api_key: str, extra: dict | None = None) -> dict | None:
    """Generic Etherscan API call. Returns parsed JSON result or None."""
    params = {
        "module": module,
        "action": action,
        "apikey": api_key,
    }
    if extra:
        params.update(extra)
    try:
        r = requests.get(ETHERSCAN_BASE, params=params, timeout=12)
        r.raise_for_status()
        data = r.json()
        if str(data.get("status")) == "1" or data.get("message") == "OK":
            return data.get("result")
        # Rate limit or other soft error
        msg = data.get("message", "")
        if "rate" in msg.lower() or "max" in msg.lower():
            print(f"[OctoDepth] Rate limited on {module}/{action}")
        return None
    except Exception as e:
        print(f"[OctoDepth] Etherscan call failed ({module}/{action}): {e}")
        return None


# ─────────────────────────────────────────────
# DATA FETCHERS
# ─────────────────────────────────────────────

def _get_gas_price(api_key: str) -> dict | None:
    """Get current gas oracle (fast/propose/safe prices in Gwei)."""
    result = _etherscan_call("gastracker", "gasoracle", api_key)
    if not result or not isinstance(result, dict):
        return None
    try:
        return {
            "safe_gwei": float(result.get("SafeGasPrice", 0)),
            "propose_gwei": float(result.get("ProposeGasPrice", 0)),
            "fast_gwei": float(result.get("FastGasPrice", 0)),
        }
    except Exception:
        return None


def _get_eth_supply(api_key: str) -> dict | None:
    """Get ETH circulating supply."""
    result = _etherscan_call("stats", "ethsupply", api_key)
    if result:
        try:
            supply_eth = float(result) / 1e18
            return {"supply_eth": round(supply_eth, 0)}
        except Exception:
            return None
    return None


def _get_eth_price(api_key: str) -> dict | None:
    """Get current ETH price from Etherscan."""
    result = _etherscan_call("stats", "ethprice", api_key)
    if result and isinstance(result, dict):
        try:
            return {
                "eth_usd": float(result.get("ethusd", 0)),
                "eth_btc": float(result.get("ethbtc", 0)),
                "timestamp": result.get("ethusd_timestamp"),
            }
        except Exception:
            return None
    return None


def _get_recent_large_transfers(api_key: str, threshold_eth: float = WHALE_ETH_THRESHOLD) -> list[dict]:
    """
    Get recent large ETH internal transactions via block exploration.
    Uses the last ~1000 txs from the ETH beacon deposit contract as a proxy for large transfers.
    Falls back to scanning latest block's transactions.
    """
    # Use Etherscan's 'txlist' on the null address is not possible directly.
    # Instead scan last 2 blocks for large value transactions.
    # Get latest block number first.
    block_result = _etherscan_call("proxy", "eth_blockNumber", api_key)
    if not block_result:
        return []
    try:
        latest_block = int(block_result, 16)
    except Exception:
        return []

    time.sleep(_REQUEST_DELAY)

    # Get transactions from the latest block
    block_data = _etherscan_call(
        "proxy", "eth_getBlockByNumber", api_key,
        {"tag": hex(latest_block), "boolean": "true"}
    )
    if not block_data or not isinstance(block_data, dict):
        return []

    txs = block_data.get("transactions", [])
    whales = []
    for tx in txs:
        try:
            value_hex = tx.get("value", "0x0")
            value_eth = int(value_hex, 16) / 1e18
            if value_eth >= threshold_eth:
                whales.append({
                    "hash": tx.get("hash", "")[:12] + "...",
                    "from": tx.get("from", "")[:10] + "...",
                    "to": (tx.get("to") or "contract")[:10] + "...",
                    "eth": round(value_eth, 2),
                })
        except Exception:
            continue

    return whales[:10]  # cap at 10


def _get_erc20_transfer_count(api_key: str, contract: str, blocks_back: int = 500) -> int | None:
    """Count recent ERC-20 transfer events for a given contract."""
    block_result = _etherscan_call("proxy", "eth_blockNumber", api_key)
    if not block_result:
        return None
    try:
        latest_block = int(block_result, 16)
        start_block = latest_block - blocks_back
    except Exception:
        return None

    time.sleep(_REQUEST_DELAY)

    result = _etherscan_call(
        "logs", "getLogs", api_key,
        {
            "address": contract,
            "fromBlock": start_block,
            "toBlock": latest_block,
            # Transfer(address,address,uint256) topic
            "topic0": "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef",
        }
    )
    if isinstance(result, list):
        return len(result)
    return None


# ─────────────────────────────────────────────
# SIGNAL INTERPRETATION
# ─────────────────────────────────────────────

def _interpret_onchain(gas: dict, whales: list, usdc_txs: int | None, eth_price: dict | None) -> dict:
    """Build signal list from on-chain data."""
    signals = []
    risk_flags = []

    if gas:
        fast = gas["fast_gwei"]
        if fast >= GAS_HIGH:
            signals.append(f"Gas {fast:.0f} Gwei — network congestion, high demand")
            risk_flags.append("elevated gas")
        elif fast >= GAS_ELEVATED:
            signals.append(f"Gas {fast:.0f} Gwei — moderate activity")
        else:
            signals.append(f"Gas {fast:.0f} Gwei — network quiet, low demand")

    if whales:
        total_moved = sum(w["eth"] for w in whales)
        signals.append(f"{len(whales)} whale tx(s) in latest block — {total_moved:,.0f} ETH moved")
        if len(whales) >= 3:
            risk_flags.append(f"whale cluster: {len(whales)} large transfers in one block")

    if usdc_txs is not None:
        if usdc_txs > 300:
            signals.append(f"USDC: {usdc_txs} transfers (~500 blocks) — stablecoin capital active")
        elif usdc_txs < 50:
            signals.append(f"USDC: {usdc_txs} transfers (~500 blocks) — low stablecoin flow")
        else:
            signals.append(f"USDC: {usdc_txs} transfers (~500 blocks) — normal flow")

    if eth_price:
        signals.append(f"ETH/USD ${eth_price['eth_usd']:,.0f} | ETH/BTC {eth_price['eth_btc']:.5f}")

    bias = "ACTIVE" if len(risk_flags) > 0 else "QUIET"

    return {
        "bias": bias,
        "signals": signals,
        "risk_flags": risk_flags,
    }


# ─────────────────────────────────────────────
# MAIN SCAN
# ─────────────────────────────────────────────

def run_onchain_scan(api_key: str | None = None) -> dict:
    """
    Run full on-chain scan via Etherscan.
    api_key: if None, reads from ETHERSCAN_API_KEY env var.
    """
    if api_key is None:
        api_key = os.environ.get("ETHERSCAN_API_KEY")
    if not api_key:
        print("[OctoDepth] No ETHERSCAN_API_KEY found — on-chain scan skipped.")
        return {"error": "no_api_key", "data": {}, "interpretation": {}}

    print("[OctoDepth] Reading on-chain flows...")
    data = {}

    # Gas oracle
    gas = _get_gas_price(api_key)
    data["gas"] = gas
    if gas:
        print(f"  Gas: safe={gas['safe_gwei']:.0f} fast={gas['fast_gwei']:.0f} Gwei")
    time.sleep(_REQUEST_DELAY)

    # ETH price
    eth_price = _get_eth_price(api_key)
    data["eth_price"] = eth_price
    if eth_price:
        print(f"  ETH: ${eth_price['eth_usd']:,.0f}")
    time.sleep(_REQUEST_DELAY)

    # ETH supply
    supply = _get_eth_supply(api_key)
    data["supply"] = supply
    if supply:
        print(f"  Supply: {supply['supply_eth']:,.0f} ETH")
    time.sleep(_REQUEST_DELAY)

    # Whale transfers in latest block
    whales = _get_recent_large_transfers(api_key, WHALE_ETH_THRESHOLD)
    data["whale_txs"] = whales
    print(f"  Whale txs (>{WHALE_ETH_THRESHOLD} ETH): {len(whales)} found")
    time.sleep(_REQUEST_DELAY)

    # USDC transfer volume (~500 blocks ≈ ~100 minutes)
    usdc_count = _get_erc20_transfer_count(api_key, USDC_CONTRACT, blocks_back=500)
    data["usdc_transfer_count"] = usdc_count
    if usdc_count is not None:
        print(f"  USDC transfers (~500 blk): {usdc_count}")
    time.sleep(_REQUEST_DELAY)

    interp = _interpret_onchain(gas, whales, usdc_count, eth_price)
    print(f"[OctoDepth] On-chain bias: {interp['bias']}")

    return {
        "timestamp": datetime.utcnow().isoformat(),
        "data": data,
        "interpretation": interp,
    }


def format_depth_for_prompt(result: dict) -> str:
    """Format OctoDepth results into a compact prompt string for the LLM."""
    if result.get("error") or not result.get("data"):
        return ""

    interp = result.get("interpretation", {})
    bias = interp.get("bias", "UNKNOWN")
    lines = [f"On-chain flows (OctoDepth) — Activity: {bias}"]

    for sig in interp.get("signals", []):
        lines.append(f"  {sig}")

    if interp.get("risk_flags"):
        lines.append("  ⚠ " + " | ".join(interp["risk_flags"]))

    return "\n".join(lines)


# ─────────────────────────────────────────────
# STANDALONE RUN
# ─────────────────────────────────────────────

if __name__ == "__main__":
    result = run_onchain_scan()
    interp = result.get("interpretation", {})
    print(f"\n── OctoDepth Report ──────────────────────")
    print(f"Bias: {interp.get('bias')}")
    for s in interp.get("signals", []):
        print(f"  • {s}")
    for f in interp.get("risk_flags", []):
        print(f"  ⚠ {f}")
    if result.get("data", {}).get("whale_txs"):
        print("\nWhale transactions:")
        for w in result["data"]["whale_txs"]:
            print(f"  {w['hash']} — {w['eth']:,.0f} ETH  {w['from']} → {w['to']}")

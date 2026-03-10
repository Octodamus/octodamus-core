"""
octo_treasury_balance.py
Reads live ETH balance from Base mainnet for the Octodamus treasury wallet.
Uses public Base RPC - no API key required.
Also attempts to read $OCTO token balance once contract is deployed.
"""

import httpx
import json
import os
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
BASE_RPC        = "https://mainnet.base.org"
TREASURY_WALLET = "0x5c6B3a3dAe296d3cef50fef96afC73410959a6Db"
OCTO_CONTRACT   = os.getenv("OCTO_CONTRACT_ADDRESS", "")  # Set after Bankr deploy

# ERC-20 balanceOf selector: balanceOf(address) = 0x70a08231
BALANCE_OF_SELECTOR = "0x70a08231"


def _rpc_call(method: str, params: list) -> dict:
    """Make a JSON-RPC call to Base mainnet."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params,
    }
    r = httpx.post(BASE_RPC, json=payload, timeout=10)
    r.raise_for_status()
    return r.json()


def get_eth_balance(address: str = TREASURY_WALLET) -> dict:
    """
    Returns ETH balance of wallet on Base.
    Result: {"wei": int, "eth": float, "display": str}
    """
    try:
        result = _rpc_call("eth_getBalance", [address, "latest"])
        wei = int(result["result"], 16)
        eth = wei / 1e18
        return {
            "wei": wei,
            "eth": eth,
            "display": f"{eth:.6f} ETH",
            "error": None,
        }
    except Exception as e:
        return {"wei": 0, "eth": 0.0, "display": "unavailable", "error": str(e)}


def get_token_balance(wallet: str = TREASURY_WALLET, contract: str = "") -> dict:
    """
    Returns ERC-20 token balance (e.g. $OCTO once deployed).
    Result: {"raw": int, "display": str, "error": str|None}
    """
    contract = contract or OCTO_CONTRACT
    if not contract:
        return {"raw": 0, "display": "contract not yet deployed", "error": None}

    try:
        # Pad address to 32 bytes for ABI encoding
        padded = wallet.lower().replace("0x", "").zfill(64)
        data = BALANCE_OF_SELECTOR + padded
        result = _rpc_call("eth_call", [{"to": contract, "data": data}, "latest"])
        raw = int(result["result"], 16)
        # Assume 18 decimals (standard ERC-20)
        amount = raw / 1e18
        return {
            "raw": raw,
            "display": f"{amount:,.2f} $OCTO",
            "error": None,
        }
    except Exception as e:
        return {"raw": 0, "display": "unavailable", "error": str(e)}


def get_block_number() -> int:
    """Returns current Base block number as a sanity check."""
    try:
        result = _rpc_call("eth_blockNumber", [])
        return int(result["result"], 16)
    except Exception:
        return 0


def get_treasury_summary() -> str:
    """
    Returns a single-line summary string for use in dashboard and system prompt.
    Example: "Treasury: 0.042300 ETH | Block: 24,891,042 | Base mainnet"
    """
    eth = get_eth_balance()
    block = get_block_number()
    octo = get_token_balance()

    parts = [f"Treasury: {eth['display']}"]
    if octo["display"] != "contract not yet deployed":
        parts.append(octo["display"])
    if block:
        parts.append(f"Block {block:,}")
    parts.append("Base mainnet")

    if eth["error"]:
        parts = [f"Treasury: RPC error ({eth['error'][:40]})"]

    return " | ".join(parts)


def get_treasury_detail() -> str:
    """
    Multi-line detail for /dashboard and /status commands.
    """
    eth   = get_eth_balance()
    octo  = get_token_balance()
    block = get_block_number()

    wallet_short = f"{TREASURY_WALLET[:10]}...{TREASURY_WALLET[-4:]}"
    base_url     = f"https://basescan.org/address/{TREASURY_WALLET}"

    lines = [
        f"Wallet   {wallet_short}",
        f"ETH      {eth['display']}",
    ]

    if OCTO_CONTRACT:
        lines.append(f"$OCTO    {octo['display']}")
    else:
        lines.append("$OCTO    pending Bankr deploy")

    if block:
        lines.append(f"Block    {block:,}")

    lines.append(f"Chain    Base mainnet")
    lines.append(f"Scan     {base_url}")

    if eth["error"]:
        lines.append(f"RPC err  {eth['error'][:60]}")

    return "\n".join(lines)


# ── CLI test ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Testing Base RPC connection...\n")
    print(get_treasury_detail())
    print()
    print("Summary line:")
    print(get_treasury_summary())

"""
octo_cdp_trade.py — CDP SDK token swap utility
Pure Python, no Electron/awal required. Works on Windows.
Uses CDP_API_KEY_ID + CDP_API_KEY_SECRET from .octo_secrets.

Usage:
  python octo_cdp_trade.py quote --from usdc --to eth --amount 10
  python octo_cdp_trade.py swap  --from usdc --to eth --amount 10 --wallet <address>

Agents import this:
  from octo_cdp_trade import get_quote, execute_swap
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT         = Path(__file__).parent
SECRETS_FILE = ROOT / ".octo_secrets"

# Base mainnet token addresses
TOKENS = {
    "usdc": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    "weth": "0x4200000000000000000000000000000000000006",
    "eth":  "0x4200000000000000000000000000000000000006",  # WETH on Base
    "cbbtc": "0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf",
}

# Decimals for amount conversion
DECIMALS = {
    "usdc": 6,
    "weth": 18,
    "eth":  18,
    "cbbtc": 8,
}


def _secrets() -> dict:
    raw = json.loads(SECRETS_FILE.read_text(encoding="utf-8"))
    return raw.get("secrets", raw)


def _resolve_token(sym_or_addr: str) -> tuple[str, int]:
    """Returns (contract_address, decimals)."""
    s = sym_or_addr.strip().lower()
    if s in TOKENS:
        return TOKENS[s], DECIMALS.get(s, 18)
    if s.startswith("0x") and len(s) == 42:
        return s, 18  # unknown token, assume 18 decimals
    raise ValueError(f"Unknown token: {sym_or_addr}. Use: {list(TOKENS)} or a 0x address.")


async def _get_quote(from_sym: str, to_sym: str, from_amount_human: float, wallet_address: str) -> dict:
    """Get a price estimate for swapping tokens on Base."""
    from cdp import CdpClient
    from cdp.actions.evm.swap import get_swap_price

    sec = _secrets()
    from_addr, from_dec = _resolve_token(from_sym)
    to_addr, _         = _resolve_token(to_sym)
    from_amount_raw    = str(int(from_amount_human * (10 ** from_dec)))

    async with CdpClient(api_key_id=sec["CDP_API_KEY_ID"], api_key_secret=sec["CDP_API_KEY_SECRET"]) as client:
        price = await get_swap_price(
            api_clients=client.api_clients,
            from_token=from_addr,
            to_token=to_addr,
            from_amount=from_amount_raw,
            network="base",
            taker=wallet_address,
        )
    to_amount_raw = getattr(price, "to_amount", None) or getattr(price, "buy_amount", "?")
    _, to_dec = _resolve_token(to_sym)
    try:
        to_amount_human = int(str(to_amount_raw)) / (10 ** to_dec)
    except Exception:
        to_amount_human = None
    return {
        "from_token":       from_sym,
        "to_token":         to_sym,
        "from_amount":      from_amount_human,
        "to_amount_raw":    to_amount_raw,
        "to_amount_human":  to_amount_human,
        "price_ratio":      getattr(price, "price_ratio", None),
        "price_object":     str(price),
    }


async def _execute_swap(from_sym: str, to_sym: str, from_amount_human: float,
                        account, slippage_bps: int = 100) -> dict:
    """Execute a token swap using a CDP EOA account. Returns transaction hash."""
    from cdp import CdpClient
    from cdp.actions.evm.swap import send_swap_transaction
    from cdp.actions.evm.swap.types import AccountSwapOptions

    sec = _secrets()
    from_addr, from_dec = _resolve_token(from_sym)
    to_addr, _         = _resolve_token(to_sym)
    from_amount_raw    = str(int(from_amount_human * (10 ** from_dec)))

    async with CdpClient(api_key_id=sec["CDP_API_KEY_ID"], api_key_secret=sec["CDP_API_KEY_SECRET"]) as client:
        opts = AccountSwapOptions(
            network="base",
            from_token=from_addr,
            to_token=to_addr,
            from_amount=from_amount_raw,
            slippage_bps=slippage_bps,
        )
        result = await send_swap_transaction(
            api_clients=client.api_clients,
            account=account,
            options=opts,
        )
    return {"status": "ok", "result": str(result)}


def get_quote(from_sym: str, to_sym: str, amount: float, wallet_address: str) -> str:
    """Sync wrapper — call from agents."""
    try:
        q = asyncio.run(_get_quote(from_sym, to_sym, amount, wallet_address))
        out = q["to_amount_human"]
        out_str = f"{out:.8f}" if out is not None else str(q["to_amount_raw"])
        ratio = q.get("price_ratio") or ""
        return (f"SWAP QUOTE (Base): {amount} {from_sym.upper()} -> ~{out_str} {to_sym.upper()}\n"
                f"  Price ratio: {ratio}\n"
                f"  Raw quote: {q['price_object'][:200]}")
    except Exception as e:
        return f"Swap quote error: {e}"


def execute_swap(from_sym: str, to_sym: str, amount: float, account, slippage_bps: int = 100) -> str:
    """Sync wrapper — call from agents. account must be a CDP EVM account object."""
    try:
        r = asyncio.run(_execute_swap(from_sym, to_sym, amount, account, slippage_bps))
        return f"SWAP EXECUTED: {amount} {from_sym} -> {to_sym}\n  Result: {r['result'][:300]}"
    except Exception as e:
        return f"Swap execution error: {e}"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CDP token swap utility")
    sub = parser.add_subparsers(dest="cmd")

    q = sub.add_parser("quote", help="Get swap price estimate")
    q.add_argument("--from", dest="from_sym", required=True)
    q.add_argument("--to",   dest="to_sym",   required=True)
    q.add_argument("--amount", type=float,    required=True)
    q.add_argument("--wallet", required=True, help="Your wallet address for price estimation")

    args = parser.parse_args()
    if args.cmd == "quote":
        print(get_quote(args.from_sym, args.to_sym, args.amount, args.wallet))
    else:
        parser.print_help()

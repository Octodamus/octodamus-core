"""
octo_acp_seed_gas.py
One-time script to seed sub-agent wallets with ETH for ACP transaction gas.

Usage: python octo_acp_seed_gas.py --from-key <ENV_KEY> [--amount 0.0001]
  --from-key: secrets key of the SENDER's private key (must have ETH on Base)
  --amount:   ETH per wallet to send (default 0.0001 = ~$0.24)

Example (once you fund a wallet with ETH):
  python octo_acp_seed_gas.py --from-key FRANKLIN_PRIVATE_KEY

The script sends <amount> ETH from the sender to all 5 sub-agent wallets.
"""

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).parent
SECRETS_FILE = ROOT / ".octo_secrets"

SUB_AGENT_WALLETS = {
    "NYSE_MacroMind":    "NYSE_MACROMIND_ADDRESS",
    "NYSE_StockOracle":  "NYSE_STOCKORACLE_ADDRESS",
    "NYSE_Tech_Agent":   "NYSE_TECH_ADDRESS",
    "Order_ChainFlow":   "ORDER_CHAINFLOW_ADDRESS",
    "X_Sentiment_Agent": "X_SENTIMENT_ADDRESS",
    "Ben/Franklin":      "FRANKLIN_WALLET_ADDRESS",
}


def _secrets() -> dict:
    raw = json.loads(SECRETS_FILE.read_text(encoding="utf-8"))
    return raw.get("secrets", raw)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-key", required=True, help="Secrets key of sender private key")
    parser.add_argument("--amount",   type=float, default=0.0001, help="ETH per wallet (default 0.0001)")
    parser.add_argument("--dry-run",  action="store_true", help="Show plan without sending")
    args = parser.parse_args()

    from web3 import Web3
    from eth_account import Account

    sec = _secrets()
    raw_key = sec.get(args.from_key, "")
    if not raw_key:
        print(f"ERROR: key '{args.from_key}' not found in .octo_secrets")
        return

    private_key = raw_key if raw_key.startswith("0x") else f"0x{raw_key}"
    sender_addr = Account.from_key(private_key).address
    amount_wei  = int(args.amount * 1e18)

    w3 = Web3(Web3.HTTPProvider("https://mainnet.base.org"))
    sender_eth = w3.eth.get_balance(Web3.to_checksum_address(sender_addr)) / 1e18

    targets = {name: sec.get(key, "") for name, key in SUB_AGENT_WALLETS.items()}
    total_eth = args.amount * len([v for v in targets.values() if v])

    print(f"Sender: {sender_addr}")
    print(f"Balance: {sender_eth:.6f} ETH")
    print(f"Sending: {args.amount:.6f} ETH each to {len(targets)} wallets = {total_eth:.6f} ETH total")
    print()

    if sender_eth < total_eth + 0.00001:
        print(f"ERROR: insufficient ETH. Need {total_eth:.6f} + gas, have {sender_eth:.6f}")
        return

    if args.dry_run:
        for name, addr in targets.items():
            print(f"  DRY RUN -> {name}: {addr}")
        print("Use --no dry-run to actually send.")
        return

    nonce = w3.eth.get_transaction_count(Web3.to_checksum_address(sender_addr))
    gas_price = w3.eth.gas_price

    for name, addr in targets.items():
        if not addr:
            print(f"  SKIP {name}: no address configured")
            continue
        cs = Web3.to_checksum_address(addr)
        tx = {
            "to":       cs,
            "value":    amount_wei,
            "gas":      21000,
            "gasPrice": gas_price,
            "nonce":    nonce,
            "chainId":  8453,
        }
        signed = Account.sign_transaction(tx, private_key)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
        status = "OK" if receipt.status == 1 else "FAILED"
        print(f"  {status} -> {name} ({addr[:16]}...): {args.amount:.6f} ETH tx={tx_hash.hex()[:16]}...")
        nonce += 1

    print("\nDone. Sub-agents can now sign ACP transactions.")


if __name__ == "__main__":
    main()

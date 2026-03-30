"""
octo_boto_wallet.py — OctoBoto Wallet Generator
Generates Ethereum-compatible wallet for Polygon/Polymarket.
Private key NEVER written to disk — store in Bitwarden.
"""

import json
import secrets
from pathlib import Path

WALLET_FILE = Path(r"C:\Users\walli\octodamus\octo_boto_wallet.json")
USDC_POLY   = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"  # USDC on Polygon


def generate_wallet() -> dict:
    """
    Generate a new Ethereum/Polygon-compatible wallet.
    Requires: pip install eth-account
    Returns dict with address + private_key.
    """
    try:
        from eth_account import Account
        acc = Account.create()
        return {
            "address":     acc.address,
            "private_key": acc.key.hex()
        }
    except ImportError:
        # Stub — tells user what to install
        return {
            "address":     "0x" + secrets.token_hex(20),
            "private_key": "0x" + secrets.token_hex(32),
            "note": "STUB — run: pip install eth-account web3"
        }


def save_address(address: str, path: Path = WALLET_FILE) -> None:
    """Save only the public address to disk. NEVER the private key."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump({"address": address}, f, indent=2)
    print(f"[Wallet] Address saved -> {path}")
    print(f"[Wallet] Store private key in Bitwarden as:")
    print(f"         AGENT - Octodamus - OctoBoto - Wallet Key")


def load_address(path: Path = WALLET_FILE) -> str:
    """Load the saved wallet address."""
    try:
        with open(path) as f:
            return json.load(f).get("address", "Not generated yet")
    except FileNotFoundError:
        return "Not generated yet"


def get_usdc_balance(address: str) -> float:
    """
    Fetch USDC balance on Polygon via PolygonScan (free API).
    Returns float dollar amount.
    """
    import requests
    try:
        r = requests.get(
            "https://api.polygonscan.com/api",
            params={
                "module":          "account",
                "action":          "tokenbalance",
                "contractaddress": USDC_POLY,
                "address":         address,
                "tag":             "latest",
                "apikey":          "YourApiKeyToken"   # Free tier works for occasional calls
            },
            timeout=10
        )
        data = r.json()
        if data.get("status") == "1":
            return round(float(data["result"]) / 1e6, 2)  # USDC = 6 decimals
        return 0.0
    except Exception as e:
        print(f"[Wallet] balance check error: {e}")
        return 0.0


def setup_new_wallet() -> dict:
    """
    One-time setup: generate wallet, save address, print instructions.
    Call this once from command line: python octo_boto_wallet.py
    """
    print("=" * 60)
    print("OctoBoto — New Wallet Setup")
    print("=" * 60)

    wallet = generate_wallet()

    if "note" in wallet:
        print(f"\n{wallet['note']}\n")
        return wallet

    print(f"\nNew Polygon wallet generated")
    print(f"   Address:     {wallet['address']}")
    print(f"   Private Key: {wallet['private_key']}")
    print()
    print("ACTIONS REQUIRED:")
    print("  1. Copy private key -> Bitwarden -> 'AGENT - Octodamus - OctoBoto - Wallet Key'")
    print("  2. DO NOT save private key anywhere else")
    print("  3. Fund with USDC on Polygon for live trading")
    print("  4. This address will be saved to disk (public only)")

    save_address(wallet["address"])

    return wallet


if __name__ == "__main__":
    setup_new_wallet()

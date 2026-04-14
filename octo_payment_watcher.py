"""
octo_payment_watcher.py - Octodamus On-Chain Payment Watcher
Monitors the Octodamus Base wallet for incoming USDC payments.
Auto-issues premium API keys when 29 USDC is received.

PAYMENT FLOW (agent-native, zero humans):
  1. Agent calls GET /api/subscribe?wallet=0xAGENT_WALLET
     -> Gets payment instructions + unique reference
  2. Agent sends 29 USDC to Octodamus wallet on Base
     -> Includes their wallet address OR email in tx input data (optional)
     -> OR just sends from their agent wallet (we match by sender)
  3. Watcher detects the payment within 5 minutes
  4. Premium key is generated and stored
  5. Agent calls GET /api/activate?wallet=0xAGENT_WALLET
     -> Gets their premium API key
     -> Key is valid for 365 days

PAYMENT DETECTION:
  - Amount: exactly 29.00 USDC (±0.01 tolerance for rounding)
  - Token: USDC on Base (0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913)
  - To: Octodamus wallet (0x7d372b930b42d4adc7c82f9d5bcb692da3597570)
  - From: Agent's wallet address (used as identifier)
  - Optional: email address encoded in tx input data (hex)

Run standalone:   python octo_payment_watcher.py
Run as daemon:    python octo_payment_watcher.py --daemon
Check payments:   python octo_payment_watcher.py --status
"""

import json
import time
import logging
import argparse
import hashlib
import secrets
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

log = logging.getLogger("OctoPayments")

# ── Config ────────────────────────────────────────────────────────────────────

OCTODAMUS_WALLET = "0x7d372b930b42d4adc7c82f9d5bcb692da3597570"
USDC_BASE        = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
BASE_RPC         = "https://mainnet.base.org"
BLOCKSCOUT_API   = "https://base.blockscout.com/api/v2"
CHAIN_ID         = 8453

PREMIUM_PRICE_USDC  = 29.00
PRICE_TOLERANCE     = 0.10        # accept 28.90 - 29.10
PREMIUM_DAYS        = 365
POLL_INTERVAL_SECS  = 300         # check every 5 minutes

DATA_DIR         = Path(r"C:\Users\walli\octodamus\data")
PAYMENTS_FILE    = DATA_DIR / "payments.json"
PENDING_FILE     = DATA_DIR / "pending_subscriptions.json"
LAST_BLOCK_FILE  = DATA_DIR / "payment_watcher_block.txt"


# ── Storage ───────────────────────────────────────────────────────────────────

def _load(path: Path) -> dict:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save(path: Path, data: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ── Pending Subscription Registration ────────────────────────────────────────

def register_pending(agent_wallet: str, email: str = "") -> dict:
    """
    Called when an agent requests subscription info.
    Creates a pending record so we know who to issue a key to.
    Returns payment instructions.
    """
    agent_wallet = agent_wallet.lower().strip()
    pending = _load(PENDING_FILE)

    # Check if already has active premium key
    from octo_api_keys import _load_keys
    for h, rec in _load_keys().items():
        if rec.get("label", "").lower() == agent_wallet and rec.get("tier") == "premium" and rec.get("active"):
            expires = rec.get("expires_at", "")
            if expires and datetime.fromisoformat(expires) > datetime.now(timezone.utc):
                return {
                    "status": "already_active",
                    "message": "You already have an active premium subscription.",
                    "expires_at": expires,
                }

    # Create pending record
    pending[agent_wallet] = {
        "agent_wallet": agent_wallet,
        "email":        email,
        "created_at":   datetime.now(timezone.utc).isoformat(),
        "paid":         False,
    }
    _save(PENDING_FILE, pending)

    return {
        "status":       "pending_payment",
        "instructions": (
            f"Send exactly {PREMIUM_PRICE_USDC} USDC to the Octodamus wallet on Base. "
            f"Send FROM your agent wallet ({agent_wallet}) so we can match the payment. "
            f"Key will be issued within 5 minutes of confirmation."
        ),
        "payment_to":   OCTODAMUS_WALLET,
        "amount_usdc":  PREMIUM_PRICE_USDC,
        "token":        USDC_BASE,
        "chain_id":     CHAIN_ID,
        "chain":        "Base",
        "from_wallet":  agent_wallet,
        "expires_in":   "15 minutes (re-register if expired)",
        "check_status": f"/api/activate?wallet={agent_wallet}",
    }


def get_key_for_wallet(agent_wallet: str) -> dict:
    """
    Called by agent after payment to retrieve their key.
    """
    agent_wallet = agent_wallet.lower().strip()
    payments = _load(PAYMENTS_FILE)

    # Find a confirmed payment for this wallet
    for tx_hash, rec in payments.items():
        if rec.get("from_wallet", "").lower() == agent_wallet and rec.get("key_issued"):
            key = rec.get("api_key", "")
            expires = rec.get("expires_at", "")
            return {
                "status":    "active",
                "api_key":   key,
                "tier":      "premium",
                "expires_at": expires,
                "usage":     "Add as X-API-Key header to all MCP requests",
                "docs":      "https://octodamus.com/api",
            }

    # Check if still pending
    pending = _load(PENDING_FILE)
    if agent_wallet in pending:
        if not pending[agent_wallet].get("paid"):
            return {
                "status":  "awaiting_payment",
                "message": f"Send {PREMIUM_PRICE_USDC} USDC from {agent_wallet} to {OCTODAMUS_WALLET} on Base.",
            }

    return {
        "status":  "not_found",
        "message": f"No payment found for {agent_wallet}. Register at /api/subscribe?wallet={agent_wallet}",
    }


# ── On-Chain Detection ────────────────────────────────────────────────────────

def _get_last_checked_block() -> int:
    try:
        if LAST_BLOCK_FILE.exists():
            return int(LAST_BLOCK_FILE.read_text().strip())
    except Exception:
        pass
    # Default: start from ~1 hour ago
    r = requests.post(BASE_RPC, json={
        "jsonrpc": "2.0", "id": 1,
        "method": "eth_blockNumber", "params": []
    }, timeout=10)
    current = int(r.json()["result"], 16)
    return max(0, current - 1800)  # ~1800 blocks = ~1 hour on Base


def _save_last_checked_block(block: int):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LAST_BLOCK_FILE.write_text(str(block))


def _get_current_block() -> int:
    r = requests.post(BASE_RPC, json={
        "jsonrpc": "2.0", "id": 1,
        "method": "eth_blockNumber", "params": []
    }, timeout=10)
    return int(r.json()["result"], 16)


def _fetch_usdc_transfers(from_block: int, to_block: int) -> list[dict]:
    """
    Fetch USDC transfer events to Octodamus wallet via Blockscout.
    ERC20 Transfer event: topic0 = keccak256("Transfer(address,address,uint256)")
    """
    try:
        # Use Blockscout token transfers API
        r = requests.get(
            f"{BLOCKSCOUT_API}/addresses/{OCTODAMUS_WALLET}/token-transfers",
            params={
                "type":   "ERC-20",
                "filter": "to",
            },
            timeout=15,
        )
        if not r.ok:
            log.warning(f"Blockscout error: {r.status_code}")
            return []

        items = r.json().get("items", [])
        transfers = []
        for t in items:
            token = t.get("token", {})
            if token.get("address", "").lower() != USDC_BASE.lower():
                continue
            ts = t.get("timestamp", "")
            tx_hash = t.get("transaction_hash", "")
            from_addr = t.get("from", {}).get("hash", "").lower()
            val_raw = t.get("total", {}).get("value", "0")
            amount_usdc = int(val_raw) / 1e6

            transfers.append({
                "tx_hash":      tx_hash,
                "from_wallet":  from_addr,
                "amount_usdc":  amount_usdc,
                "timestamp":    ts,
            })

        return transfers

    except Exception as e:
        log.error(f"Fetch transfers error: {e}")
        return []


def _decode_email_from_input(tx_hash: str) -> str:
    """
    Try to decode an email address from tx input data.
    Agents can encode their email as hex in the tx data field.
    """
    try:
        r = requests.post(BASE_RPC, json={
            "jsonrpc": "2.0", "id": 1,
            "method": "eth_getTransactionByHash",
            "params": [tx_hash]
        }, timeout=10)
        tx = r.json().get("result", {})
        input_data = tx.get("input", "0x")
        if input_data and input_data != "0x" and len(input_data) > 2:
            # Try to decode hex as UTF-8
            try:
                decoded = bytes.fromhex(input_data[2:]).decode("utf-8", errors="ignore")
                # Look for email pattern
                import re
                match = re.search(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", decoded)
                if match:
                    return match.group(0)
            except Exception:
                pass
    except Exception:
        pass
    return ""


def _is_valid_payment(amount_usdc: float) -> bool:
    return abs(amount_usdc - PREMIUM_PRICE_USDC) <= PRICE_TOLERANCE


# ── Key Issuance ──────────────────────────────────────────────────────────────

def _issue_premium_key(from_wallet: str, email: str, tx_hash: str) -> str:
    """Generate and store a premium key for a verified payment."""
    from octo_api_keys import create_key, _load_keys, _save_keys, _hash_key

    label = from_wallet.lower()
    key   = create_key(
        email   = email or f"{from_wallet[:8]}@agent.base",
        tier    = "premium",
        label   = label,
    )

    # Set expiry date on the key record
    key_hash = _hash_key(key)
    keys = _load_keys()
    if key_hash in keys:
        expires = (datetime.now(timezone.utc) + timedelta(days=PREMIUM_DAYS)).isoformat()
        keys[key_hash]["expires_at"]    = expires
        keys[key_hash]["paid_tx"]       = tx_hash
        keys[key_hash]["paid_amount"]   = PREMIUM_PRICE_USDC
        _save_keys(keys)

    log.info(f"Premium key issued for {from_wallet} (tx: {tx_hash[:16]}...)")
    return key


def _record_payment(tx_hash: str, from_wallet: str, amount: float, api_key: str, email: str):
    payments = _load(PAYMENTS_FILE)
    expires = (datetime.now(timezone.utc) + timedelta(days=PREMIUM_DAYS)).isoformat()
    payments[tx_hash] = {
        "from_wallet":  from_wallet,
        "amount_usdc":  amount,
        "email":        email,
        "api_key":      api_key,
        "key_issued":   True,
        "issued_at":    datetime.now(timezone.utc).isoformat(),
        "expires_at":   expires,
        "tx_hash":      tx_hash,
    }
    _save(PAYMENTS_FILE, payments)

    # Mark pending as paid
    pending = _load(PENDING_FILE)
    if from_wallet.lower() in pending:
        pending[from_wallet.lower()]["paid"] = True
        _save(PENDING_FILE, pending)


# ── Main Watcher Loop ─────────────────────────────────────────────────────────

def scan_for_payments():
    """
    Single scan: check recent USDC transfers to Octodamus wallet.
    Issues keys for valid 29 USDC payments not yet processed.
    """
    payments = _load(PAYMENTS_FILE)
    pending  = _load(PENDING_FILE)

    transfers = _fetch_usdc_transfers(0, 0)  # Blockscout returns recent by default
    new_keys  = []

    for t in transfers:
        tx_hash    = t["tx_hash"]
        from_wallet = t["from_wallet"]
        amount     = t["amount_usdc"]

        # Skip already processed
        if tx_hash in payments:
            continue

        # Skip if not valid payment amount
        if not _is_valid_payment(amount):
            log.debug(f"Skipping {tx_hash[:12]} — amount {amount:.2f} USDC (need {PREMIUM_PRICE_USDC})")
            continue

        log.info(f"Valid payment detected: {amount:.2f} USDC from {from_wallet} (tx: {tx_hash[:16]}...)")

        # Try to get email from tx input data
        email = _decode_email_from_input(tx_hash)

        # Try to get email from pending registrations
        if not email and from_wallet in pending:
            email = pending[from_wallet].get("email", "")

        # Issue the key
        api_key = _issue_premium_key(from_wallet, email, tx_hash)
        _record_payment(tx_hash, from_wallet, amount, api_key, email)

        new_keys.append({
            "wallet":  from_wallet,
            "email":   email,
            "tx_hash": tx_hash,
            "key":     api_key[:20] + "...",
        })
        log.info(f"Key issued: {api_key[:20]}... -> {from_wallet}")

    if new_keys:
        log.info(f"Scan complete: {len(new_keys)} new premium key(s) issued")
    else:
        log.debug("Scan complete: no new payments")

    return new_keys


def run_daemon():
    """Continuous watcher loop."""
    log.info(f"Payment watcher daemon started (polling every {POLL_INTERVAL_SECS}s)")
    log.info(f"Watching wallet: {OCTODAMUS_WALLET}")
    log.info(f"Waiting for: {PREMIUM_PRICE_USDC} USDC on Base")

    while True:
        try:
            new_keys = scan_for_payments()
            if new_keys:
                for k in new_keys:
                    log.info(f"NEW PREMIUM: {k['wallet']} | tx: {k['tx_hash'][:16]}")
        except Exception as e:
            log.error(f"Watcher error: {e}")
        time.sleep(POLL_INTERVAL_SECS)


# ── FastAPI Subscription Endpoints (mounted into octo_mcp_http.py) ────────────

def create_subscription_routes():
    """
    Returns FastAPI router with /api/subscribe and /api/activate endpoints.
    These get mounted into the MCP HTTP server.
    """
    try:
        from fastapi import APIRouter, Query
        from fastapi.responses import JSONResponse as FJSONResponse

        router = APIRouter(prefix="/api")

        @router.get("/subscribe")
        def subscribe(
            wallet: str = Query(..., description="Your agent wallet address on Base"),
            email:  str = Query("",  description="Optional email for key delivery"),
        ):
            """Register for premium subscription. Returns payment instructions."""
            if not wallet.startswith("0x") or len(wallet) != 42:
                return FJSONResponse(
                    status_code=400,
                    content={"error": "Invalid wallet address. Must be a 42-char Base address starting with 0x."}
                )
            result = register_pending(wallet, email)
            return FJSONResponse(content=result)

        @router.get("/activate")
        def activate(
            wallet: str = Query(..., description="Your agent wallet address on Base"),
        ):
            """Check payment status and retrieve your premium API key."""
            if not wallet.startswith("0x") or len(wallet) != 42:
                return FJSONResponse(
                    status_code=400,
                    content={"error": "Invalid wallet address."}
                )
            result = get_key_for_wallet(wallet)
            status_code = 200 if result.get("status") == "active" else 202
            return FJSONResponse(status_code=status_code, content=result)

        @router.get("/pricing")
        def pricing():
            """Get current pricing and payment instructions."""
            return FJSONResponse(content={
                "free": {
                    "price":           "Free",
                    "requests_per_day": 50,
                    "tools":           "get_signal, get_market_sentiment, get_active_calls, get_track_record",
                    "signup":          "GET /api/subscribe?wallet=YOUR_WALLET",
                },
                "premium": {
                    "price":           f"${PREMIUM_PRICE_USDC:.0f} USDC/year",
                    "requests":        "Unlimited",
                    "tools":           "All tools including raw derivatives, predictions, news, liquidation maps",
                    "payment_steps": [
                        f"1. GET /api/subscribe?wallet=YOUR_WALLET",
                        f"2. Send {PREMIUM_PRICE_USDC} USDC from YOUR_WALLET to {OCTODAMUS_WALLET} on Base",
                        f"3. GET /api/activate?wallet=YOUR_WALLET (within 5 min of tx confirmation)",
                    ],
                    "payment_to":   OCTODAMUS_WALLET,
                    "token":        "USDC",
                    "chain":        "Base (chainId 8453)",
                    "token_address": USDC_BASE,
                },
                "pay_per_call": {
                    "price":    "$0.01 USDC per call",
                    "protocol": "x402",
                    "notes":    "No signup needed. Server returns 402 with payment details.",
                },
            })

        return router

    except ImportError:
        return None


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    parser = argparse.ArgumentParser(description="Octodamus Payment Watcher")
    parser.add_argument("--daemon",  action="store_true", help="Run continuous watcher")
    parser.add_argument("--scan",    action="store_true", help="Run one scan and exit")
    parser.add_argument("--status",  action="store_true", help="Show payment history")
    parser.add_argument("--pending", action="store_true", help="Show pending subscriptions")
    args = parser.parse_args()

    if args.daemon:
        run_daemon()

    elif args.scan:
        print("Running payment scan...")
        new_keys = scan_for_payments()
        if new_keys:
            print(f"Issued {len(new_keys)} new key(s):")
            for k in new_keys:
                print(f"  {k['wallet']} | {k['email']} | tx: {k['tx_hash'][:20]}...")
        else:
            print("No new payments found.")

    elif args.status:
        payments = _load(PAYMENTS_FILE)
        if not payments:
            print("No payments recorded yet.")
        else:
            print(f"Payment history ({len(payments)} total):")
            for tx, rec in payments.items():
                print(f"  {rec.get('issued_at','')[:10]} | {rec.get('amount_usdc')} USDC | "
                      f"{rec.get('from_wallet','')[:20]}... | expires: {rec.get('expires_at','')[:10]}")

    elif args.pending:
        pending = _load(PENDING_FILE)
        if not pending:
            print("No pending subscriptions.")
        else:
            print(f"Pending subscriptions ({len(pending)}):")
            for wallet, rec in pending.items():
                paid = "PAID" if rec.get("paid") else "awaiting payment"
                print(f"  {wallet[:20]}... | {rec.get('email','')} | {paid} | {rec.get('created_at','')[:10]}")

    else:
        print(f"Octodamus Payment Watcher")
        print(f"Watching: {OCTODAMUS_WALLET}")
        print(f"Price:    {PREMIUM_PRICE_USDC} USDC/year on Base")
        print(f"\nCommands:")
        print(f"  --daemon   Run continuous watcher (every {POLL_INTERVAL_SECS}s)")
        print(f"  --scan     Run one scan now")
        print(f"  --status   Show payment history")
        print(f"  --pending  Show pending subscriptions")


if __name__ == "__main__":
    main()

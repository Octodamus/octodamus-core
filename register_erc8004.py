"""
register_erc8004.py
Register Octodamus on Agent Arena (ERC-8004) — $0.05 USDC on Base.

This script:
1. Sends the registration payload to agentarena.site/api/register
2. Gets back a 402 with X-Payment-Required
3. Prints payment instructions + polls for completion
4. Saves the agentId + globalId to data/erc8004_identity.json

Run: python register_erc8004.py
"""

import json
import time
import httpx
from pathlib import Path

IDENTITY_FILE = Path(__file__).parent / "data" / "erc8004_identity.json"

# ── Registration payload ──────────────────────────────────────────────────────

REGISTRATION = {
    "name": "Octodamus Market Intelligence API",
    "description": (
        "Real-time market intelligence for autonomous AI agents. "
        "Delivers Oracle trading signals (9/11 system consensus), "
        "Fear & Greed index, Polymarket prediction market edge plays with EV scoring, "
        "crypto/macro sentiment across 27 live data feeds, and BTC trend. "
        "Single-call decision endpoint (/v2/agent-signal) returns action/confidence/signal/"
        "polymarket_edge/reasoning — designed for 15-minute agent poll cycles. "
        "x402 native: agents pay $5 USDC on Base, receive API key automatically, "
        "no human required. Annual access $29 USDC. Self-renew via /v1/key/status."
    ),
    "capabilities": [
        "market-intelligence",
        "crypto-signals",
        "prediction-markets",
        "polymarket",
        "fear-greed",
        "macro-data",
        "trading-signals",
        "bitcoin",
        "sentiment-analysis",
        "oracle",
        "defi",
    ],
    "services": [
        {
            "name": "x402",
            "endpoint": "https://api.octodamus.com/v2/agent-signal",
        },
        {
            "name": "web",
            "endpoint": "https://octodamus.com",
            "version": "3.0.0",
        },
    ],
    "pricing": {
        "per_task":  0.0001,       # per API call after subscription
        "currency":  "USDC",
        "chain":     "base",
    },
    "x402Support":      True,
    "preferredChain":   "base",
    "agentWallet":      "0x5c6B3a3dAe296d3cef50fef96afC73410959a6Db",
    "supportedTrust":   ["reputation"],
    "image":            "https://octodamus.com/octo_logo.png",
}


def register_basic():
    """
    POST to Agent Arena /api/register.
    Handles the x402 402 response and prints payment instructions.
    """
    print("=" * 60)
    print("Octodamus ERC-8004 Registration — Agent Arena")
    print("=" * 60)
    print()

    # Step 1: send registration request
    print("STEP 1: Sending registration payload...")
    r = httpx.post(
        "https://agentarena.site/api/register",
        json=REGISTRATION,
        timeout=30,
    )
    print(f"  Status: {r.status_code}")

    if r.status_code == 200:
        # Already registered or instant success
        data = r.json()
        _save_and_print(data)
        return

    if r.status_code != 402:
        print(f"  ERROR: Unexpected status {r.status_code}")
        print(f"  Body: {r.text[:500]}")
        return

    # Step 2: parse x402 payment required
    xpr_raw = r.headers.get("x-payment-required", "")
    body    = r.json()

    print()
    print("STEP 2: 402 Payment Required received")

    if xpr_raw:
        xpr = json.loads(xpr_raw)
        accepts = xpr.get("accepts", [])
        if accepts:
            opt = accepts[0]
            micro = int(opt.get("maxAmountRequired", 50000))
            usdc  = micro / 1_000_000
            print(f"  Amount: ${usdc:.4f} USDC")
            print(f"  Pay to: {opt.get('payTo')}")
            print(f"  Asset:  {opt.get('asset')} (USDC on Base)")
            print(f"  Network: {opt.get('network')}")
    else:
        # Fall back to body details
        detail = body.get("detail", body)
        print(f"  Payment details: {json.dumps(detail, indent=2)[:400]}")

    print()
    print("=" * 60)
    print("PAYMENT INSTRUCTIONS")
    print()
    print("Send $0.05 USDC on Base to the address above.")
    print()
    print("Options:")
    print("  A) Coinbase Wallet / MetaMask: send 0.05 USDC on Base")
    print("  B) Coinbase app → Send → Base network → paste address")
    print()
    print("After sending, paste your transaction hash below.")
    print("(Transaction hash starts with 0x, 66 characters total)")
    print()

    tx_hash = input("Paste tx hash (or press ENTER to skip): ").strip()
    if not tx_hash:
        print("Skipped — run this script again with your tx hash to complete.")
        return

    # Step 3: retry with X-PAYMENT header
    print()
    print("STEP 3: Retrying with payment proof...")
    r2 = httpx.post(
        "https://agentarena.site/api/register",
        json=REGISTRATION,
        headers={"X-PAYMENT": tx_hash},
        timeout=30,
    )
    print(f"  Status: {r2.status_code}")

    if r2.status_code == 200:
        data = r2.json()
        _save_and_print(data)
    else:
        print(f"  Response: {r2.text[:500]}")
        print()
        print("If payment is pending confirmation, wait ~5 seconds and run again.")


def _save_and_print(data: dict):
    store = data.get("store", {})
    if not store:
        store = {k: data.get(k) for k in ("globalId", "agentId", "chainId", "chain", "agentUri", "profileUrl", "txHash")}

    print()
    print("=" * 60)
    print("REGISTRATION SUCCESSFUL ✓")
    print()
    print(f"  globalId:   {store.get('globalId')}")
    print(f"  agentId:    {store.get('agentId')}")
    print(f"  chain:      {store.get('chain')} (chainId={store.get('chainId')})")
    print(f"  profile:    {store.get('profileUrl')}")
    print(f"  txHash:     {store.get('txHash', data.get('txHash'))}")
    print()

    # Domain verification block
    dv = data.get("domainVerification", {})
    if dv:
        print("DOMAIN VERIFICATION (paste agentId back here when done):")
        print(f"  Host at: https://api.octodamus.com/.well-known/agent-registration.json")
        print(f"  Content: {json.dumps(dv.get('content', {}))}")
        print()

    # Next steps
    for step in data.get("nextSteps", []):
        print(f"  {step}")

    print()
    print("Saving identity to data/erc8004_identity.json ...")
    IDENTITY_FILE.parent.mkdir(parents=True, exist_ok=True)
    identity = {
        "erc8004_identity": {
            **store,
            "registeredAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
        "full_response": data,
    }
    IDENTITY_FILE.write_text(json.dumps(identity, indent=2))
    print(f"Saved ✓")
    print("=" * 60)
    print()
    print("NEXT: Paste the agentId here so the API server domain verification gets updated.")


def register_a2a():
    """
    Register with A2A Bundle ($0.15 USDC).
    x402 v2 flow: get fresh 402 → send USDC → submit base64(JSON proof) within 5 min.
    """
    import base64 as _b64

    URL = "https://agentarena.site/api/register?a2a=true"

    print("=" * 60)
    print("Octodamus ERC-8004 + A2A Registration — $0.15 USDC on Base")
    print("=" * 60)
    print()

    # ── Step 1: Get a fresh 402 with payment session ──────────────────────────
    print("STEP 1: Getting payment session from Agent Arena...")
    r = httpx.post(URL, json=REGISTRATION, timeout=30)
    print(f"  Status: {r.status_code}")

    if r.status_code == 200:
        _save_and_print(r.json())
        return

    if r.status_code != 402:
        print(f"  Unexpected: {r.status_code} — {r.text[:300]}")
        return

    # Decode payment details from body
    body = r.json()
    accepts = body.get("accepts", [])
    pay_to, asset, amount_micro = None, None, 150000
    for opt in accepts:
        if opt.get("scheme") == "exact" and "8453" in opt.get("network", ""):
            pay_to       = opt.get("payTo")
            asset        = opt.get("asset")
            amount_micro = int(opt.get("maxAmountRequired", 150000))
            break

    # Grab the raw payment-required token (used in proof)
    pr_token = r.headers.get("payment-required", "")

    print()
    print("  ┌─────────────────────────────────────────────────┐")
    print(f"  │  SEND: ${amount_micro/1_000_000:.2f} USDC                             │")
    print(f"  │  TO:   {pay_to}  │")
    print(f"  │  NET:  Base mainnet (chain 8453)                │")
    print(f"  │  TOKEN: USDC 0x833589...0913                    │")
    print("  └─────────────────────────────────────────────────┘")
    print()
    print("  You have 5 minutes before this session expires.")
    print("  Send from Coinbase Wallet / MetaMask on Base network.")
    print()

    tx_hash = input("Paste tx hash after sending (0x...): ").strip()
    if not tx_hash:
        print("Cancelled.")
        return

    # ── Step 2: Build x402 v2 payment proof ──────────────────────────────────
    # x402 v2: base64(JSON({x402Version, scheme, network, payload:{transaction}}))
    proof_obj = {
        "x402Version": 2,
        "scheme":      "exact",
        "network":     "eip155:8453",
        "payload": {
            "transaction": tx_hash,
        }
    }
    proof_b64 = _b64.b64encode(json.dumps(proof_obj, separators=(",", ":")).encode()).decode()

    print()
    print("STEP 2: Submitting payment proof...")

    # Try base64 first, then raw JSON, then raw tx hash as fallbacks
    attempts = [
        ("base64 JSON proof", proof_b64),
        ("raw JSON proof",    json.dumps(proof_obj, separators=(",", ":"))),
        ("raw tx hash",       tx_hash),
    ]

    for fmt_name, val in attempts:
        print(f"  Trying: {fmt_name}")
        r2 = httpx.post(
            URL,
            json=REGISTRATION,
            headers={"X-PAYMENT": val},
            timeout=30,
        )
        print(f"  Status: {r2.status_code}  Body: {r2.text[:300]}")
        if r2.status_code == 200:
            _save_and_print(r2.json())
            return
        if r2.status_code == 500:
            print("  Server error — waiting 3s and retrying...")
            time.sleep(3)
            r2 = httpx.post(URL, json=REGISTRATION, headers={"X-PAYMENT": val}, timeout=30)
            print(f"  Retry status: {r2.status_code}  Body: {r2.text[:300]}")
            if r2.status_code == 200:
                _save_and_print(r2.json())
                return
        if "expired" in r2.text.lower():
            print("  Session expired — run the script again immediately after sending.")
            return

    print()
    print("All formats failed. Check tx hash is confirmed on Base (basescan.org).")
    print("Then run again with the same tx hash.")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "a2a":
        register_a2a()
    else:
        register_basic()

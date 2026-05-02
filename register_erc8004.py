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
    "name": "Octodamus — Agentic Finance Intelligence Oracle",
    "description": (
        "Agentic finance intelligence oracle. 27 live data feeds, 11-signal consensus. "
        "14 ACP offerings via Virtuals ACP ($1-2 USDC/job): crypto signals (BTC/ETH/SOL), "
        "tokenized NYSE stocks (AAPL/MSFT/SPY/NVDA/TSLA on Base), macro regime, "
        "congressional trading, on-chain order flow, crowd sentiment, overnight briefs, "
        "Polymarket edges with EV+Kelly sizing. x402 native: $0.01 USDC/call on Base. "
        "api.octodamus.com"
    ),
    "capabilities": [
        "market-intelligence",
        "crypto-signals",
        "prediction-markets",
        "polymarket",
        "macro-data",
        "trading-signals",
        "sentiment-analysis",
        "oracle",
        "tokenized-equities",
        "agentic-finance",
        "x402",
        "acp-offerings",
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
    print("REGISTRATION SUCCESSFUL")
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
    print("Saved OK")
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


def _sign_eip3009(franklin_addr: str, franklin_key: str, pay_to: str,
                  amount_micro: int, usdc_addr: str, chain_id: int = 8453) -> dict:
    """
    Sign a USDC EIP-3009 transferWithAuthorization.
    Returns the authorization dict + hex signature (no on-chain tx needed).
    The merchant calls transferWithAuthorization on-chain with this proof.
    """
    import os as _os, time as _time
    from eth_account import Account

    nonce_hex = "0x" + _os.urandom(32).hex()
    valid_before = int(_time.time()) + 300  # 5-min window

    domain = {
        "name":              "USD Coin",
        "version":           "2",
        "chainId":           chain_id,
        "verifyingContract": usdc_addr,
    }
    types = {
        "TransferWithAuthorization": [
            {"name": "from",        "type": "address"},
            {"name": "to",          "type": "address"},
            {"name": "value",       "type": "uint256"},
            {"name": "validAfter",  "type": "uint256"},
            {"name": "validBefore", "type": "uint256"},
            {"name": "nonce",       "type": "bytes32"},
        ]
    }
    message = {
        "from":        franklin_addr,
        "to":          pay_to,
        "value":       amount_micro,
        "validAfter":  0,
        "validBefore": valid_before,
        "nonce":       bytes.fromhex(nonce_hex[2:]),
    }

    signed = Account.sign_typed_data(
        private_key=franklin_key,
        domain_data=domain,
        message_types=types,
        message_data=message,
    )
    sig = signed.signature.hex()
    if not sig.startswith("0x"):
        sig = "0x" + sig

    return {
        "authorization": {
            "from":        franklin_addr,
            "to":          pay_to,
            "value":       str(amount_micro),
            "validAfter":  "0",
            "validBefore": str(valid_before),
            "nonce":       nonce_hex,
        },
        "signature": sig,
        "from": franklin_addr,
    }


def update_profile():
    """
    Update an existing ERC-8004 registration via PUT /api/register.
    Uses EIP-3009 transferWithAuthorization (gas-less) from Franklin wallet.
    x402 v2 exact scheme: sign the auth, merchant submits on-chain.
    """
    import base64 as _b64
    from web3 import Web3

    _sf = Path(__file__).parent / ".octo_secrets"
    _secrets = json.loads(_sf.read_text(encoding="utf-8")).get("secrets", {})
    franklin_addr = Web3.to_checksum_address(_secrets["FRANKLIN_WALLET_ADDRESS"])
    franklin_key  = _secrets["FRANKLIN_PRIVATE_KEY"]

    try:
        identity = json.loads(IDENTITY_FILE.read_text(encoding="utf-8"))
        agent_id = (
            identity.get("erc8004_identity", {}).get("agentId")
            or identity.get("agentId")
        )
    except Exception:
        agent_id = None

    if not agent_id:
        print("ERROR: No agentId found in data/erc8004_identity.json. Register first.")
        return

    payload = {**REGISTRATION, "agentId": agent_id}

    # Step 1: get fresh 402
    print(f"Fetching payment session (agentId={agent_id})...")
    r = httpx.put("https://agentarena.site/api/register", json=payload, timeout=30)
    print(f"  Status: {r.status_code}")

    if r.status_code == 200:
        print("Updated without payment. Done.")
        _save_and_print(r.json())
        return

    if r.status_code != 402:
        print(f"  Unexpected: {r.status_code} — {r.text[:300]}")
        return

    body = r.json()
    accepts = body.get("accepts", [])
    pay_to, amount_micro, network, usdc_addr = None, 50000, "eip155:8453", "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
    for opt in accepts:
        if opt.get("maxAmountRequired"):
            pay_to       = opt["payTo"]
            amount_micro = int(opt["maxAmountRequired"])
            network      = opt.get("network", network)
            usdc_addr    = opt.get("asset", usdc_addr)
            break

    if not pay_to:
        print(f"  ERROR: No payTo in 402 response")
        return

    print(f"  Amount: {amount_micro} USDC micro-units (${amount_micro/1e6:.4f}) to {pay_to}")

    # Step 2: sign EIP-3009 authorization (no on-chain tx)
    print(f"  Signing EIP-3009 authorization from Franklin ({franklin_addr[:10]}...)...")
    proof_payload = _sign_eip3009(
        franklin_addr=franklin_addr,
        franklin_key=franklin_key,
        pay_to=Web3.to_checksum_address(pay_to),
        amount_micro=amount_micro,
        usdc_addr=Web3.to_checksum_address(usdc_addr),
        chain_id=8453,
    )
    print(f"  Signature: {proof_payload['signature'][:20]}...")

    # Step 3: build x402 v2 proof and PUT
    proof_obj = {
        "x402Version": 2,
        "scheme":      "exact",
        "network":     network,
        "payload":     proof_payload,
    }
    proof_b64 = _b64.b64encode(
        json.dumps(proof_obj, separators=(",", ":")).encode()
    ).decode()

    print("  Sending PUT with EIP-3009 proof...")
    r2 = httpx.put(
        "https://agentarena.site/api/register",
        json=payload,
        headers={"X-Payment": proof_b64},
        timeout=45,
    )
    print(f"  Status: {r2.status_code}  Body: {r2.text[:400]}")

    if r2.status_code == 200:
        print()
        print("Profile updated successfully.")
        _save_and_print(r2.json())
        return

    # Fallback: try raw JSON proof
    print("  Trying raw JSON proof...")
    r3 = httpx.put(
        "https://agentarena.site/api/register",
        json=payload,
        headers={"X-Payment": json.dumps(proof_obj, separators=(",", ":"))},
        timeout=45,
    )
    print(f"  Status: {r3.status_code}  Body: {r3.text[:400]}")
    if r3.status_code == 200:
        print("Profile updated successfully.")
        _save_and_print(r3.json())
        return

    print()
    print("Both EIP-3009 formats failed — AgentArena may have a server-side issue.")
    print("The $0.05 from the previous on-chain tx went to their wallet (already confirmed).")
    print("Try again later or contact agentarena.site.")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "a2a":
        register_a2a()
    elif len(sys.argv) > 1 and sys.argv[1] == "update":
        update_profile()
    elif len(sys.argv) > 2 and sys.argv[1] == "retry":
        # Retry PUT with a previously confirmed tx hash
        import base64 as _b64
        _sf = Path(__file__).parent / ".octo_secrets"
        _secrets = json.loads(_sf.read_text(encoding="utf-8")).get("secrets", {})
        _id = json.loads(IDENTITY_FILE.read_text(encoding="utf-8"))
        _agent_id = _id.get("erc8004_identity", {}).get("agentId") or _id.get("agentId")
        _payload = {**REGISTRATION, "agentId": _agent_id}
        _tx = sys.argv[2]
        _proof = {
            "x402Version": 2, "scheme": "exact", "network": "eip155:8453",
            "payload": {"transaction": _tx},
        }
        _proof_b64 = _b64.b64encode(json.dumps(_proof, separators=(",", ":")).encode()).decode()
        for _fmt, _val in [("base64", _proof_b64), ("json", json.dumps(_proof, separators=(",", ":"))), ("raw", _tx)]:
            print(f"Trying {_fmt}...")
            _r = httpx.put("https://agentarena.site/api/register", json=_payload,
                           headers={"X-Payment": _val}, timeout=30)
            print(f"  {_r.status_code}: {_r.text[:300]}")
            if _r.status_code == 200:
                _save_and_print(_r.json())
                break
    else:
        register_basic()

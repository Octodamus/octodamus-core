"""
test_x402_flow.py
Live end-to-end test of the OctoData x402 payment flow.

Steps:
  1. Hit /v2/agent-signal with no key → expect 402 + X-Payment-Required header
  2. Create a $5 trial payment intent
  3. Print payment address + amount (you send USDC manually or via wallet)
  4. Poll status until fulfilled or timeout
  5. Test the provisioned key against /v2/agent-signal

Run: python test_x402_flow.py
"""

import json
import time
import httpx

BASE_URL = "https://api.octodamus.com"
YOUR_WALLET = ""   # Optional: paste your Base wallet address here for faster matching

# ── Step 1: Confirm 402 + X-Payment-Required header ──────────────────────────

print("=" * 60)
print("STEP 1: Hit /v2/agent-signal with no key")
r = httpx.get(f"{BASE_URL}/v2/agent-signal")
print(f"  Status: {r.status_code}")
assert r.status_code == 402, f"Expected 402, got {r.status_code}"

xpr = r.headers.get("x-payment-required", "")
if xpr:
    descriptor = json.loads(xpr)
    print(f"  X-Payment-Required: PRESENT ✓")
    print(f"  Version: {descriptor.get('version')}")
    accepts = descriptor.get("accepts", [])
    for opt in accepts:
        micro = int(opt.get("maxAmountRequired", 0))
        usdc  = micro / 1_000_000
        print(f"  Option: ${usdc:.0f} USDC → {opt.get('payTo')[:20]}... on {opt.get('network')}")
else:
    print("  WARNING: X-Payment-Required header missing — server may not have reloaded yet")

body = r.json()
print(f"  Body x402 field: {body.get('detail', {}).get('x402', 'missing')}")
print()

# ── Step 2: Create payment intent ─────────────────────────────────────────────

print("STEP 2: Create $5 trial payment intent")
params = {"product": "premium_trial", "chain": "base", "label": "x402-test"}
if YOUR_WALLET:
    params["agent_wallet"] = YOUR_WALLET

r = httpx.post(f"{BASE_URL}/v1/agent-checkout", params=params)
print(f"  Status: {r.status_code}")
assert r.status_code == 200, f"Checkout failed: {r.text}"

checkout = r.json()
payment_id      = checkout.get("payment_id")
pay_address     = checkout.get("payment_address")
amount_usdc     = checkout.get("amount_usdc")
amount_micro    = checkout.get("amount_micro")   # raw 6-decimal for ERC-20 calldata
expires_at      = checkout.get("expires_at", "")

print(f"  Payment ID:      {payment_id}")
print(f"  Pay to address:  {pay_address}")
print(f"  Amount USDC:     ${amount_usdc}")
print(f"  Amount micro:    {amount_micro}  (use this for ERC-20 transfer calldata)")
print(f"  Expires:         {expires_at}")
print()

# ── Step 3: Print payment instructions ────────────────────────────────────────

print("=" * 60)
print("STEP 3: Send payment manually")
print()
print(f"  Network:  Base (chain ID 8453)")
print(f"  Token:    USDC  0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")
print(f"  To:       {pay_address}")
print(f"  Amount:   {amount_usdc} USDC  (exact — {amount_micro} micro)")
print()
print("  Options:")
print("  A) Coinbase Wallet / MetaMask: send USDC on Base to address above")
print("  B) Coinbase app: send to Base address")
print("  C) via cast (Foundry):")
print(f"     cast send 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913 \\")
print(f"       'transfer(address,uint256)' {pay_address} {amount_micro} \\")
print(f"       --rpc-url https://mainnet.base.org --private-key $PRIVATE_KEY")
print()
input("Press ENTER after sending payment to start polling...")
print()

# ── Step 4: Poll for fulfillment ──────────────────────────────────────────────

print("STEP 4: Polling /v1/agent-checkout/status ...")
api_key = None
for i in range(40):   # 40 × 15s = 10 minutes
    r = httpx.get(f"{BASE_URL}/v1/agent-checkout/status", params={"payment_id": payment_id})
    data = r.json()
    status = data.get("status")
    print(f"  [{i+1:02d}] status={status}  ({time.strftime('%H:%M:%S')})")

    if status == "fulfilled":
        api_key = data.get("api_key")
        print(f"  FULFILLED ✓  API key: {api_key}")
        break
    elif status in ("expired", "error"):
        print(f"  FAILED: {data}")
        break

    time.sleep(15)

if not api_key:
    print("  Timed out waiting for payment. Check your USDC send and try again.")
    exit(1)

print()

# ── Step 5: Test the provisioned key ──────────────────────────────────────────

print("STEP 5: Test /v2/agent-signal with new key")
r = httpx.get(f"{BASE_URL}/v2/agent-signal", headers={"X-OctoData-Key": api_key})
print(f"  Status: {r.status_code}")
assert r.status_code == 200, f"Signal request failed: {r.text}"

signal_data = r.json()
print(f"  action:     {signal_data.get('action')}")
print(f"  confidence: {signal_data.get('confidence')}")
print(f"  reasoning:  {signal_data.get('reasoning', '')[:80]}")
fng = signal_data.get("fear_greed", {})
print(f"  fear_greed: {fng.get('value')} ({fng.get('label')})")
btc = signal_data.get("btc", {})
print(f"  btc:        ${btc.get('price_usd'):,.0f}  {btc.get('change_24h'):+.1f}%  trend={btc.get('trend')}")
print(f"  polymarket: {len(signal_data.get('polymarket_edge', []))} edge plays")
print()

print("STEP 6: Test /v1/key/status")
r = httpx.get(f"{BASE_URL}/v1/key/status", headers={"X-OctoData-Key": api_key})
print(f"  Status: {r.status_code}")
ks = r.json()
print(f"  tier:           {ks.get('tier')}")
print(f"  days_remaining: {ks.get('days_remaining')}")
print(f"  expires:        {ks.get('expires')}")
print()

print("=" * 60)
print("x402 END-TO-END TEST PASSED ✓")
print(f"Your API key: {api_key}")
print("=" * 60)

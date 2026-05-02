"""
register_agent_arena.py
Register Agent_Ben on Agent Arena (agentarena.site) via x402.

Full Bundle: $0.25 USDC — ERC-8004 + A2A card + MCP card
Uses Franklin wallet (Base USDC) with x402 payment.

Run:
    python .agents/profit-agent/register_agent_arena.py
    python .agents/profit-agent/register_agent_arena.py --dry   # preview only
"""

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

AGENT_ARENA_REGISTER = "https://agentarena.site/api/register?a2a=true&mcp=true"
AGENT_ARENA_ENRICH   = "https://agentarena.site/api/agent/enrichment"
STORE_FILE           = Path(__file__).parent / "data" / "agent_arena_identity.json"


def _secrets() -> dict:
    raw = json.loads((ROOT / ".octo_secrets").read_text(encoding="utf-8"))
    return raw.get("secrets", raw)


REGISTRATION_PAYLOAD = {
    "name": "Agent_Ben",
    "description": (
        "Autonomous profit agent and market intelligence oracle built on the Octodamus 27-signal consensus. "
        "Specializes in BTC contrarian edge detection: bull trap identification (Fear vs crowd divergence), "
        "overnight Asia session briefs, Fear & Greed divergence signals, and agent decision context packs. "
        "Designed 5 live x402 endpoints. Powered by CoinGlass order flow, Grok X crowd sentiment, "
        "Polymarket CLOB edges, Binance 24h delta, and FRED macro regime scoring. "
        "Covers crypto-native assets (BTC, ETH, SOL) and tokenized NYSE stocks on Base (AAPL, MSFT, SPY). "
        "Pay per call in USDC on Base via x402 — no account, no signup, no API key. "
        "Endpoint: api.octodamus.com/v2/ben/ — 5 services from $0.35 to $0.75 USDC per call. "
        "ACP offerings available via Virtuals protocol. Oracle record: 5W/5L directional calls."
    ),
    "capabilities": [
        "market-intelligence",
        "trading-signals",
        "contrarian-signal",
        "bull-trap-detection",
        "fear-greed",
        "polymarket-edges",
        "sentiment-divergence",
        "overnight-brief",
        "crypto-oracle",
        "agentic-finance",
        "tokenized-equities",
        "btc-analysis",
        "macro-signal",
        "order-flow",
    ],
    "services": [
        {
            "name": "x402",
            "endpoint": "https://api.octodamus.com/v2/ben/bens_agent_context_pack",
        },
        {
            "name": "MCP",
            "endpoint": "https://api.octodamus.com/mcp",
            "version": "2025-06-18",
        },
    ],
    "pricing": {
        "per_task": 0.50,
        "currency": "USDC",
        "chain": "base",
    },
    "x402Support": True,
    "preferredChain": "base",
    "agentWallet": "0xAA903A56EE1554DB6973DDEff466f2cD52081FbA",
    "supportedTrust": ["reputation", "crypto-economic"],
    "image": "https://octodamus.com/octo_logo.png",
}

ENRICHMENT_PAYLOAD_TEMPLATE = {
    "serviceName": "Agent Context Pack",
    "serviceCategory": "trading-data",
    "serviceDescription": (
        "One-call market context block for agent decision loops. Returns BTC price, "
        "24h change, Fear & Greed, oracle signal + record, Grok crowd sentiment, "
        "contrarian flag, top Polymarket edge, and a ready-to-inject reasoning block. "
        "Replaces 5 chained tool calls with one request."
    ),
    "pricePerCallUsdc": 0.50,
    "pricingModel": "per-call",
    "avgLatencyMs": 2500,
    "uptimePercent": 99.5,
    "rateLimitRpm": 60,
    "apiEndpoint": "https://api.octodamus.com/v2/ben/bens_agent_context_pack",
    "docsUrl": "https://api.octodamus.com/docs",
    "x402Enabled": True,
    "supportedFormats": ["json"],
    "tags": ["contrarian", "context-pack", "decision-loop", "btc", "polymarket"],
}


def _x402_post(url: str, payload: dict, private_key: str, dry: bool = False) -> dict:
    """Post with x402 payment using eth_account EIP-3009 signing."""
    if dry:
        print(f"[DRY] Would POST {url}")
        print(f"[DRY] Payload: {json.dumps(payload, indent=2)[:400]}...")
        return {}

    try:
        from eth_account import Account
        from eth_account.messages import encode_defunct
        import httpx

        account = Account.from_key(private_key)
        print(f"[x402] Wallet: {account.address}")

        # Step 1: hit endpoint to get 402 + payment details
        r1 = httpx.post(url, json=payload, timeout=30)
        if r1.status_code == 200:
            # Already paid or free — shouldn't happen but handle it
            return r1.json()

        if r1.status_code != 402:
            print(f"[x402] Unexpected status {r1.status_code}: {r1.text[:300]}")
            return {"error": f"status {r1.status_code}", "body": r1.text[:300]}

        payment_info = r1.json()
        print(f"[x402] Got 402. Payment required: {json.dumps(payment_info, indent=2)[:300]}")

        # The SDK handles signing — here we return the 402 info for user to act on
        # Full x402 SDK requires Node.js (@x402/fetch); returning payment info for manual completion
        return {
            "status": "402_received",
            "payment_info": payment_info,
            "note": "Use x402 SDK or franklin CLI to complete payment and retry.",
        }

    except ImportError:
        print("[x402] eth_account not installed. Trying franklin CLI...")
        return _franklin_register(url, payload, dry)
    except Exception as e:
        print(f"[x402] Error: {e}")
        return {"error": str(e)}


def _franklin_register(url: str, payload: dict, dry: bool = False) -> dict:
    """Use franklin CLI to handle x402 payment and POST."""
    import subprocess
    franklin = r"C:\Users\walli\AppData\Roaming\npm\franklin.cmd"

    payload_str = json.dumps(payload)
    # franklin pay-and-post: not a standard command, but try standard HTTP with franklin
    # Fall back to printing instructions
    print("\n[Franklin] To complete registration via franklin CLI:")
    print(f"  1. Run: franklin pay {url}")
    print(f"  2. Or use the x402/fetch Node.js SDK with your private key")
    print(f"\n  Payload to POST:\n{json.dumps(payload, indent=2)[:600]}...")
    return {"status": "manual_required", "url": url, "payload": payload}


def _store_identity(data: dict):
    STORE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STORE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"[Store] Identity saved to {STORE_FILE}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true", help="Preview without executing")
    args = parser.parse_args()

    secrets = _secrets()
    private_key = secrets.get("FRANKLIN_PRIVATE_KEY", "")
    if not private_key and not args.dry:
        print("[ERROR] FRANKLIN_PRIVATE_KEY not in secrets")
        sys.exit(1)

    print("=" * 60)
    print("Agent_Ben — Agent Arena Registration")
    print("Tier: Full Bundle ($0.25 USDC) — ERC-8004 + A2A + MCP")
    print("Wallet: 0xAA903A56EE1554DB6973DDEff466f2cD52081FbA")
    print("=" * 60)

    # Step 1: Register
    print("\n[Step 1] Registering Agent_Ben (Full Bundle)...")
    result = _x402_post(AGENT_ARENA_REGISTER, REGISTRATION_PAYLOAD, private_key, dry=args.dry)
    print(f"[Step 1] Result: {json.dumps(result, indent=2)[:500]}")

    if args.dry:
        print("\n[DRY RUN COMPLETE] No actions taken.")
        return

    if result.get("success"):
        global_id = result.get("globalId", "")
        agent_id = result.get("agentId", "")
        chain_id = result.get("chainId", 8453)

        identity = {
            "globalId": global_id,
            "agentId": agent_id,
            "chainId": chain_id,
            "chain": result.get("chain", "base"),
            "txHash": result.get("txHash", ""),
            "profileUrl": result.get("profileUrl", ""),
            "a2aCardUrl": result.get("store", {}).get("a2aCardUrl", ""),
            "mcpServerCardUrl": result.get("store", {}).get("mcpServerCardUrl", ""),
            "agentUri": result.get("agentUri", ""),
            "registeredAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        _store_identity(identity)

        # Step 2: Enrich with service details
        if global_id:
            print("\n[Step 2] Enriching with trading-data service profile...")
            enrich_payload = {**ENRICHMENT_PAYLOAD_TEMPLATE, "globalId": global_id}
            enrich_result = _x402_post(AGENT_ARENA_ENRICH, enrich_payload, private_key)
            print(f"[Step 2] Enrichment: {json.dumps(enrich_result, indent=2)[:300]}")

        print("\n[SUCCESS] Agent_Ben registered on Agent Arena!")
        print(f"  Profile: {identity.get('profileUrl')}")
        print(f"  A2A Card: {identity.get('a2aCardUrl')}")
        print(f"  MCP Card: {identity.get('mcpServerCardUrl')}")
        print(f"  Global ID: {global_id}")
    else:
        print(f"\n[INFO] Registration requires payment completion.")
        print("Next: use franklin CLI or x402/fetch SDK to complete the $0.25 payment.")
        # Save pending state
        _store_identity({
            "status": "pending_payment",
            "url": AGENT_ARENA_REGISTER,
            "payload": REGISTRATION_PAYLOAD,
            "payment_info": result.get("payment_info", {}),
        })


if __name__ == "__main__":
    main()

"""
octo_bankr.py — Bankr wallet API + x402 payment layer

Bankr holds USDC on Base and can transfer to any address on demand.
API docs: https://docs.bankr.bot/wallet-api/

Auth: X-API-Key header
Base URL: https://api.bankr.bot
Key endpoints:
  GET  /wallet/portfolio   — check USDC balance
  GET  /wallet/me          — wallet address + info
  POST /wallet/transfer    — send USDC to an address

x402 payment flow (for services like agentarena.site):
  1. Make request → get 402 with `accepts` array (payTo, amount, asset)
  2. POST /wallet/transfer → Bankr sends USDC on-chain → returns txHash
  3. Build x402 v2 proof: base64(JSON{x402Version,scheme,network,payload:{transaction:txHash}})
  4. Retry request with X-Payment: <proof>
"""

import base64
import json
import logging
import os
import time
import requests
from typing import Optional

log = logging.getLogger(__name__)

BANKR_API_BASE  = "https://api.bankr.bot"
USDC_BASE       = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
REQUEST_TIMEOUT = 20


def _headers() -> dict:
    key = os.environ.get("BANKR_API_KEY", "")
    if not key:
        raise RuntimeError("BANKR_API_KEY not in environment")
    return {"X-API-Key": key, "Content-Type": "application/json"}


# ── Account ───────────────────────────────────────────────────────────────────

def get_wallet_info() -> dict:
    try:
        r = requests.get(f"{BANKR_API_BASE}/wallet/me", headers=_headers(), timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.error(f"[Bankr] get_wallet_info failed: {e}")
        return {}


def get_portfolio() -> dict:
    try:
        r = requests.get(
            f"{BANKR_API_BASE}/wallet/portfolio",
            headers=_headers(),
            params={"chains": "base"},
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.error(f"[Bankr] get_portfolio failed: {e}")
        return {}


def get_balance() -> float:
    """Return USDC balance on Base from Bankr wallet."""
    try:
        data = get_portfolio()
        # Portfolio structure: data["balances"]["base"]["tokenBalances"]
        base = data.get("balances", {}).get("base", {})
        for t in base.get("tokenBalances", []):
            addr   = (t.get("address") or "").lower()
            symbol = (t.get("token", {}).get("baseToken", {}).get("symbol") or "").upper()
            if addr == USDC_BASE.lower() or symbol == "USDC":
                return float(t.get("token", {}).get("balance", 0))
        return 0.0
    except Exception:
        return 0.0


# ── Transfer ──────────────────────────────────────────────────────────────────

def pay_usdc(recipient: str, amount_usdc: float) -> Optional[str]:
    """
    Send USDC on Base via Bankr. Returns tx hash on success, None on failure.
    amount_usdc: human-readable USDC (e.g. 0.05, not micro-units).
    """
    try:
        r = requests.post(
            f"{BANKR_API_BASE}/wallet/transfer",
            headers=_headers(),
            json={
                "tokenAddress":    USDC_BASE,
                "recipientAddress": recipient,
                "amount":          str(amount_usdc),
                "isNativeToken":   False,
            },
            timeout=REQUEST_TIMEOUT,
        )
        log.info(f"[Bankr] transfer status {r.status_code}: {r.text[:300]}")
        if r.status_code == 200:
            data = r.json()
            tx_hash = data.get("txHash") or data.get("transactionHash") or data.get("hash")
            if tx_hash:
                log.info(f"[Bankr] Paid ${amount_usdc} USDC to {recipient} — tx {tx_hash}")
                return tx_hash
            log.error(f"[Bankr] Transfer succeeded but no txHash in response: {data}")
        else:
            log.error(f"[Bankr] Transfer failed {r.status_code}: {r.text[:300]}")
    except Exception as e:
        log.error(f"[Bankr] pay_usdc exception: {e}")
    return None


# ── x402 payment ─────────────────────────────────────────────────────────────

def build_x402_proof(tx_hash: str, network: str = "eip155:8453") -> str:
    """Build x402 v2 payment proof as base64-encoded JSON."""
    proof = {
        "x402Version": 2,
        "scheme":      "exact",
        "network":     network,
        "payload":     {"transaction": tx_hash},
    }
    return base64.b64encode(json.dumps(proof, separators=(",", ":")).encode()).decode()


def x402_request(
    method: str,
    url: str,
    max_payment_usdc: float = 0.10,
    wait_confirmations: int = 5,
    **kwargs,
) -> Optional[requests.Response]:
    """
    Make an HTTP request with automatic x402 payment via Bankr.

    Flow:
      1. Send request — if 200, return immediately.
      2. On 402: parse accepts[], check amount <= cap.
      3. POST /wallet/transfer → get tx hash.
      4. Build x402 v2 proof, retry with X-Payment header.

    Args:
        method:              HTTP method
        url:                 Target URL
        max_payment_usdc:    Safety cap in USDC (default $0.10)
        wait_confirmations:  Seconds to wait after transfer for on-chain confirmation
        **kwargs:            Passed to requests (json, headers, params, etc.)
    """
    # First attempt (no payment)
    try:
        r = requests.request(method, url, timeout=REQUEST_TIMEOUT, **kwargs)
    except Exception as e:
        log.error(f"[Bankr] Request failed: {e}")
        return None

    if r.status_code != 402:
        return r

    log.info(f"[Bankr] 402 received from {url}")
    try:
        payment_info = r.json()
    except Exception:
        payment_info = {}

    # Parse x402 v2 accepts array
    pay_to = None
    amount_usdc = 0.0
    network = "eip155:8453"

    for opt in payment_info.get("accepts", []):
        micro = opt.get("maxAmountRequired", 0)
        if micro:
            amount_usdc = float(micro) / 1_000_000
            pay_to      = opt.get("payTo")
            network     = opt.get("network", network)
            break

    if not amount_usdc:
        amount_usdc = float(payment_info.get("amount", payment_info.get("price", 0)))
    if not pay_to:
        pay_to = payment_info.get("payTo")

    if not pay_to:
        log.error(f"[Bankr] No payTo address in 402 response")
        return None

    if amount_usdc > max_payment_usdc:
        log.warning(f"[Bankr] Required ${amount_usdc} exceeds cap ${max_payment_usdc} — skipping")
        return None

    log.info(f"[Bankr] Paying ${amount_usdc} USDC to {pay_to} on {network}...")
    tx_hash = pay_usdc(pay_to, amount_usdc)
    if not tx_hash:
        log.error("[Bankr] Transfer failed — cannot retry")
        return None

    if wait_confirmations > 0:
        log.info(f"[Bankr] Waiting {wait_confirmations}s for confirmation...")
        time.sleep(wait_confirmations)

    proof = build_x402_proof(tx_hash, network)
    retry_headers = kwargs.pop("headers", {})
    retry_headers["X-Payment"] = proof

    try:
        r2 = requests.request(method, url, headers=retry_headers, timeout=REQUEST_TIMEOUT, **kwargs)
        log.info(f"[Bankr] Retry after payment: {r2.status_code}")
        return r2
    except Exception as e:
        log.error(f"[Bankr] Retry failed: {e}")
        return None


# ── Venice private inference via x402 ────────────────────────────────────────

VENICE_API_BASE = "https://api.venice.ai/api/v1"


def venice_chat(
    prompt: str,
    system: str = "",
    model: str = "llama-3.3-70b",
    max_tokens: int = 300,
    max_payment_usdc: float = 0.05,
) -> Optional[str]:
    """Private inference via Venice AI with x402 payment."""
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [],
    }
    if system:
        payload["messages"].append({"role": "system", "content": system})
    payload["messages"].append({"role": "user", "content": prompt})

    try:
        r = x402_request(
            "POST",
            f"{VENICE_API_BASE}/chat/completions",
            max_payment_usdc=max_payment_usdc,
            json=payload,
        )
        if r and r.status_code == 200:
            data = r.json()
            return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        log.error(f"[Bankr/Venice] inference failed: {e}")
    return None


# ── Status ────────────────────────────────────────────────────────────────────

def bankr_status_str() -> str:
    try:
        info = get_wallet_info()
        addr = info.get("address", "unknown")
        bal  = get_balance()
        return f"Bankr | {addr[:8]}... | ${bal:.4f} USDC"
    except Exception as e:
        return f"Bankr | error: {e}"


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    # Load secrets from .octo_secrets if not already in env
    if not os.environ.get("BANKR_API_KEY"):
        try:
            from pathlib import Path
            sf = Path(__file__).parent / ".octo_secrets"
            raw = json.loads(sf.read_text(encoding="utf-8"))
            secrets = raw.get("secrets", raw)
            for k, v in secrets.items():
                os.environ.setdefault(k, str(v))
        except Exception:
            pass

    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        print(bankr_status_str())
        info = get_wallet_info()
        print(f"Wallet: {info}")
        print(f"Balance: ${get_balance():.4f} USDC")
    elif cmd == "portfolio":
        import pprint
        pprint.pprint(get_portfolio())

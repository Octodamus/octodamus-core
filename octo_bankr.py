"""
octo_bankr.py — Bankr x402 payment layer
Handles HTTP 402 Payment Required responses for autonomous agent payments.

x402 protocol:
  1. Agent makes HTTP request
  2. Server returns 402 with payment details in headers/body
  3. Agent signs and submits payment via Bankr
  4. Agent retries request with payment proof

Used for: Venice private inference, paid data feeds, agent-to-agent payments.
"""

import logging
import os
import requests
from typing import Optional

log = logging.getLogger(__name__)

BANKR_API_BASE = "https://api.bankr.bot"
REQUEST_TIMEOUT = 15


def _headers() -> dict:
    key = os.environ.get("BANKR_API_KEY", "")
    if not key:
        raise RuntimeError("BANKR_API_KEY not in environment")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


# ── Account ───────────────────────────────────────────────────────────────────

def get_account() -> dict:
    """Fetch Bankr agent account info and balance."""
    try:
        r = requests.get(f"{BANKR_API_BASE}/account", headers=_headers(), timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.error(f"[Bankr] get_account failed: {e}")
        return {}


def get_balance() -> float:
    """Return USDC balance in Bankr agent wallet."""
    try:
        account = get_account()
        return float(account.get("balance", 0))
    except Exception:
        return 0.0


# ── x402 request handler ──────────────────────────────────────────────────────

def x402_request(
    method: str,
    url: str,
    max_payment_usdc: float = 0.10,
    **kwargs,
) -> Optional[requests.Response]:
    """
    Make an HTTP request with automatic x402 payment handling.

    If the server returns 402, extracts payment requirements,
    authorizes via Bankr, and retries with payment proof.

    Args:
        method:            HTTP method ("GET", "POST", etc.)
        url:               Target URL
        max_payment_usdc:  Maximum USDC willing to pay (safety cap)
        **kwargs:          Passed to requests (json, headers, params, etc.)

    Returns:
        Response object on success, None on failure.
    """
    # First attempt (no payment)
    try:
        r = requests.request(method, url, timeout=REQUEST_TIMEOUT, **kwargs)
    except Exception as e:
        log.error(f"[Bankr] Request failed: {e}")
        return None

    if r.status_code != 402:
        return r

    # Handle 402 — extract payment requirement
    log.info(f"[Bankr] 402 received from {url}")
    try:
        payment_info = r.json()
    except Exception:
        payment_info = {}

    required_amount = float(payment_info.get("amount", payment_info.get("price", 0)))
    if required_amount > max_payment_usdc:
        log.warning(f"[Bankr] Payment required ${required_amount} exceeds cap ${max_payment_usdc} — skipping")
        return None

    # Submit payment via Bankr
    try:
        pay_r = requests.post(
            f"{BANKR_API_BASE}/pay",
            headers=_headers(),
            json={
                "url":     url,
                "amount":  required_amount,
                "payload": payment_info,
            },
            timeout=REQUEST_TIMEOUT,
        )
        pay_r.raise_for_status()
        payment_proof = pay_r.json()
        log.info(f"[Bankr] Payment authorized: ${required_amount} → {url}")
    except Exception as e:
        log.error(f"[Bankr] Payment failed: {e}")
        return None

    # Retry with payment proof
    proof_header = payment_proof.get("token") or payment_proof.get("proof") or ""
    retry_headers = kwargs.pop("headers", {})
    retry_headers["X-Payment"] = proof_header

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
    """
    Private inference via Venice AI with x402 payment.
    Falls back gracefully if Venice/Bankr unavailable.
    """
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
        bal = get_balance()
        return f"💳 Bankr | ${bal:.4f} USDC"
    except Exception as e:
        return f"💳 Bankr | error: {e}"

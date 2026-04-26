"""
octo_api_server.py â€" Octodamus API Server v3
FastAPI server â€" OctoData snapshots + ACP live report endpoints.
Port: 8742 | Tunneled via Cloudflare to api.octodamus.com

Endpoints:
  Public:
    GET /                        Info
    GET /health                  Health check
    GET /api/fear-greed          Live Fear & Greed (ACP resource)
    GET /api/btc-dominance       Live BTC dominance (ACP resource)
    GET /api/report              Live HTML report (regenerated each request)
    GET /api/report/{id}         Frozen HTML report (written at job delivery)

  Authenticated (X-OctoData-Key header):
    GET /v1/prices               Nightly price snapshots
    GET /v1/sentiment            AI sentiment scores
    GET /v1/briefing             Full AI briefing (Pro tier)
    GET /v1/full                 Combined snapshot (Pro tier)

  Admin:
    POST /admin/keys/create
    GET  /admin/keys/list
    DELETE /admin/keys/revoke
"""

import json
import os
import re
import secrets
import threading
import time as _time
from collections import defaultdict, deque
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Optional

# Load Bitwarden secrets into env at startup
try:
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).parent))
    import bitwarden as _bw
    _bw.load_all_secrets()
except Exception as _e:
    print(f"[API] Bitwarden load skipped: {_e}")

# Start renewal reminder scheduler (checks every 6h, emails at 30/7/1 days before expiry)
try:
    from octo_agent_pay import start_renewal_scheduler
    start_renewal_scheduler()
except Exception as _e:
    print(f"[API] Renewal scheduler skipped: {_e}")

# Start background payment scanner (polls Base/ETH/BTC every 30s for incoming payments)
try:
    from octo_agent_pay import start_payment_scanner
    start_payment_scanner()
except Exception as _e:
    print(f"[API] Payment scanner skipped: {_e}")

import httpx

from fastapi import FastAPI, HTTPException, Security, Depends, Query, Request
from fastapi.security.api_key import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

PORT      = 8742
DATA_DIR  = Path(__file__).parent / "data" / "snapshots"
KEYS_FILE = Path(__file__).parent / "data" / "api_keys.json"

# Frozen reports written by ACP worker (WSL /mnt/c/... = Windows C:\...)
REPORTS_DIR = Path(__file__).parent / "data" / "reports"

DATA_DIR.mkdir(parents=True, exist_ok=True)
KEYS_FILE.parent.mkdir(parents=True, exist_ok=True)
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

# â"€â"€ API key store â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

def load_keys() -> dict:
    return json.loads(KEYS_FILE.read_text()) if KEYS_FILE.exists() else {}

def save_keys(keys: dict):
    KEYS_FILE.write_text(json.dumps(keys, indent=2))

def validate_key(api_key: str) -> Optional[dict]:
    entry = load_keys().get(api_key)
    if not entry:
        return None
    if entry.get("expires"):
        if datetime.fromisoformat(entry["expires"]) < datetime.utcnow():
            return None
    return entry

API_KEY_HEADER = APIKeyHeader(name="X-OctoData-Key", auto_error=False)

async def require_key(api_key: str = Security(API_KEY_HEADER)):
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing X-OctoData-Key header")
    entry = validate_key(api_key)
    if not entry:
        raise HTTPException(status_code=403, detail="Invalid or expired API key")
    return entry

# ── Tier limits & rate limiter ─────────────────────────────────────────────────

TIER_LIMITS = {
    "basic":    {"req_per_day": 500,   "req_per_minute": 20},
    "free":     {"req_per_day": 500,   "req_per_minute": 20},
    "trial":    {"req_per_day": 10000, "req_per_minute": 200},
    "pro":      {"req_per_day": 10000, "req_per_minute": 200},
    "premium":  {"req_per_day": 10000, "req_per_minute": 200},
    "internal": {"req_per_day": None,  "req_per_minute": None},
    "admin":    {"req_per_day": None,  "req_per_minute": None},
}

_EARLY_BIRD_LIMIT    = 100    # first 100 seats at $29/yr
_EARLY_BIRD_PRICE    = 29     # USD
_STANDARD_PRICE      = 149    # USD after first 100
_TRIAL_PRICE         = 5      # USD
_FREE_UPGRADE_DAYS   = 14     # notify after 14 days on free tier

def _premium_seat_count() -> int:
    """Count active premium/pro subscribers (for early bird pricing gate)."""
    try:
        from octo_api_keys import _load_keys
        keys = _load_keys()
        return sum(1 for v in keys.values() if v.get("tier") in ("premium","pro") and v.get("active", True))
    except Exception:
        return 0

def _upgrade_cta(tier: str, created_at: str = "") -> dict | None:  # created_at = key["created"]
    """
    Returns upgrade CTA dict if the key should be shown an upgrade prompt.
    Free keys: show after 14 days. Basic keys: always show.
    Returns None if no upgrade needed (already premium/admin).
    """
    if tier in ("premium", "pro", "internal", "admin", "trial"):
        return None
    seats_left = max(0, _EARLY_BIRD_LIMIT - _premium_seat_count())
    price = _EARLY_BIRD_PRICE if seats_left > 0 else _STANDARD_PRICE
    label = f"Early Bird — {seats_left} seats left" if seats_left > 0 else "Standard"

    # For free/basic keys, check age
    if created_at:
        try:
            from datetime import timezone as _tz
            age_days = (datetime.now(timezone.utc) - datetime.fromisoformat(created_at)).days
            if age_days < _FREE_UPGRADE_DAYS and tier in ("basic","free"):
                return None  # too new, don't spam
        except Exception:
            pass

    return {
        "upgrade_available": True,
        "message": f"Upgrade to Premium — ${price} USDC/year. {label}.",
        "micro":   "X-Payment: sign $0.01 USDC EIP-3009 — pay per call, no subscription",
        "trial":   "GET https://api.octodamus.com/v1/subscribe?plan=trial — $5 USDC, 7 days",
        "annual":  f"GET https://api.octodamus.com/v1/subscribe?plan=annual — ${price} USDC, 365 days",
        "seats_at_early_bird_price": seats_left,
    }

_rl_lock          = threading.Lock()
_daily_counts:    dict[str, int]   = defaultdict(int)
_minute_windows:  dict[str, deque] = defaultdict(deque)
_rl_date          = date.today()

def _check_rate_limit(api_key: str, tier: str) -> dict:
    """Check rate limits and return remaining counts. Raises structured 429 on breach."""
    limits = TIER_LIMITS.get(tier, TIER_LIMITS["basic"])
    if limits["req_per_day"] is None:
        return {"daily_limit": None, "daily_remaining": None, "minute_limit": None, "minute_remaining": None}
    now   = _time.monotonic()
    today = date.today()
    with _rl_lock:
        global _rl_date
        if today != _rl_date:
            _daily_counts.clear()
            _rl_date = today
        daily_used = _daily_counts[api_key]
        if daily_used >= limits["req_per_day"]:
            raise HTTPException(
                status_code=429,
                detail={
                    "error_code":   "DAILY_LIMIT_EXCEEDED",
                    "message":      f"Daily limit of {limits['req_per_day']} requests reached.",
                    "limit":        limits["req_per_day"],
                    "used":         daily_used,
                    "remaining":    0,
                    "resets_at":    (datetime.utcnow().replace(hour=0, minute=0, second=0) + timedelta(days=1)).isoformat() + "Z",
                    "upgrade":      "https://octodamus.com/api#pricing",
                    "upgrade_usdc": "POST https://api.octodamus.com/v1/agent-checkout?product=premium_annual",
                },
            )
        win = _minute_windows[api_key]
        cutoff = now - 60
        while win and win[0] < cutoff:
            win.popleft()
        minute_used = len(win)
        if minute_used >= limits["req_per_minute"]:
            raise HTTPException(
                status_code=429,
                detail={
                    "error_code":  "RATE_LIMITED",
                    "message":     f"Rate limit: {limits['req_per_minute']} req/min.",
                    "limit":       limits["req_per_minute"],
                    "used":        minute_used,
                    "remaining":   0,
                    "retry_after": 60,
                    "upgrade":     "https://octodamus.com/api#pricing",
                },
            )
        win.append(now)
        _daily_counts[api_key] += 1
        return {
            "daily_limit":     limits["req_per_day"],
            "daily_remaining": limits["req_per_day"] - daily_used - 1,
            "minute_limit":    limits["req_per_minute"],
            "minute_remaining": limits["req_per_minute"] - minute_used - 1,
        }

# ── x402 SDK — Coinbase CDP facilitator (production) ─────────────────────────
import base64 as _b64
from x402.server import x402ResourceServerSync, x402ResourceServer
from x402.http.facilitator_client import (
    HTTPFacilitatorClientSync, HTTPFacilitatorClient,
    FacilitatorConfig, CreateHeadersAuthProvider,
)
from x402.schemas.payments import PaymentRequirements
from x402.http.types import RouteConfig, PaymentOption
from x402.http.middleware.fastapi import PaymentMiddlewareASGI
from x402.mechanisms.evm.exact import ExactEvmServerScheme
from x402 import parse_payment_payload as _parse_x402_payload

_X402_TREASURY = "0x5c6B3a3dAe296d3cef50fef96afC73410959a6Db"
_X402_USDC     = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"  # USDC on Base

# ── Ed25519 signal signing (Mycelia-style on-chain verifiable responses) ──────
_SIGNING_KEY    = os.environ.get("OCTODAMUS_SIGNING_KEY", "")
_SIGNING_PUBKEY = os.environ.get("OCTODAMUS_SIGNING_PUBKEY", "")

def _sign_payload(payload: dict) -> dict:
    """
    Sign a signal response with Octodamus Ed25519 key.
    Agents can verify with the public key at /.well-known/x402.json.
    Adds: signature (base64), signer_pubkey (base64), signed_at (ISO timestamp).
    """
    if not _SIGNING_KEY:
        return payload
    try:
        import base64 as _b64
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization as _ser
        priv_bytes = _b64.b64decode(_SIGNING_KEY)
        private_key = Ed25519PrivateKey.from_private_bytes(priv_bytes)
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        signature  = private_key.sign(canonical)
        payload["signature"]    = _b64.b64encode(signature).decode()
        payload["signer_pubkey"] = _SIGNING_PUBKEY
        payload["signed_at"]    = datetime.utcnow().isoformat() + "Z"
    except Exception as _se:
        payload["signature_error"] = str(_se)
    return payload

# CDP production facilitator — requires CDP_API_KEY_ID + CDP_API_KEY_SECRET env vars
# Falls back to x402.org testnet if keys absent (useful for local dev)
_CDP_KEY_ID     = os.environ.get("CDP_API_KEY_ID", "")
_CDP_KEY_SECRET = os.environ.get("CDP_API_KEY_SECRET", "")
_FACILITATOR_URL = (
    "https://api.cdp.coinbase.com/platform/v2/x402"
    if (_CDP_KEY_ID and _CDP_KEY_SECRET)
    else "https://x402.org/facilitator"
)

def _cdp_jwt(uri: str) -> str:
    """Generate a short-lived Ed25519 JWT for Coinbase CDP API auth."""
    import base64 as _b64m, json as _jsonm, time as _timem
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key_bytes = _b64m.b64decode(_CDP_KEY_SECRET)
    priv = Ed25519PrivateKey.from_private_bytes(key_bytes[:32])
    now = int(_timem.time())
    hdr = _b64m.urlsafe_b64encode(_jsonm.dumps({"alg":"EdDSA","kid":_CDP_KEY_ID,"typ":"JWT"}).encode()).rstrip(b"=").decode()
    pld = _b64m.urlsafe_b64encode(_jsonm.dumps({"iss":"cdp","sub":_CDP_KEY_ID,"nbf":now,"exp":now+120,"uri":uri}).encode()).rstrip(b"=").decode()
    sig = _b64m.urlsafe_b64encode(priv.sign(f"{hdr}.{pld}".encode())).rstrip(b"=").decode()
    return f"{hdr}.{pld}.{sig}"

def _build_facilitator() -> HTTPFacilitatorClientSync:
    if _CDP_KEY_ID and _CDP_KEY_SECRET:
        _CDP_HOST = "api.cdp.coinbase.com"
        _X402_PATH = "platform/v2/x402"
        def _cdp_auth():
            return {
                "supported": {"Authorization": f"Bearer {_cdp_jwt(f'GET {_CDP_HOST}/{_X402_PATH}/supported')}"},
                "verify":    {"Authorization": f"Bearer {_cdp_jwt(f'POST {_CDP_HOST}/{_X402_PATH}/verify')}"},
                "settle":    {"Authorization": f"Bearer {_cdp_jwt(f'POST {_CDP_HOST}/{_X402_PATH}/settle')}"},
            }
        return HTTPFacilitatorClientSync(
            FacilitatorConfig(url=_FACILITATOR_URL, auth_provider=CreateHeadersAuthProvider(_cdp_auth))
        )
    return HTTPFacilitatorClientSync(FacilitatorConfig(url=_FACILITATOR_URL))

_x402_facilitator     = _build_facilitator()                            # sync — for require_key_v2
_x402_server          = x402ResourceServerSync(_x402_facilitator)
_x402_facilitator_aio = HTTPFacilitatorClient(FacilitatorConfig(url=_FACILITATOR_URL))  # async — for PaymentMiddlewareASGI
_x402_server_aio      = x402ResourceServer(_x402_facilitator_aio)
_x402_initialized     = False

# Must be defined before scheme registration below
_X402_NETWORK = "eip155:8453" if (_CDP_KEY_ID and _CDP_KEY_SECRET) else "eip155:84532"

# Register schemes synchronously so middleware has them at app creation time
try:
    _x402_server.register("eip155:8453", ExactEvmServerScheme())
    _x402_server_aio.register(_X402_NETWORK, ExactEvmServerScheme())
except Exception as _e:
    print(f"[API] x402 scheme registration warning: {_e}")

def _init_x402():
    global _x402_initialized
    try:
        _x402_server.initialize()
        _x402_initialized = True
        print(f"[API] x402 initialized via {_FACILITATOR_URL}")
    except Exception as _e:
        print(f"[API] x402 init warning: {_e}")

threading.Thread(target=_init_x402, daemon=True, name="x402-init").start()

_USDC_EXTRA = {"name": "USD Coin", "version": "2", "chainId": 8453}

_X402_REQ_ANNUAL = PaymentRequirements(
    scheme="exact", network="eip155:8453", asset=_X402_USDC,
    amount="29000000", pay_to=_X402_TREASURY, max_timeout_seconds=3600,
    extra=_USDC_EXTRA,
)
_X402_REQ_TRIAL = PaymentRequirements(
    scheme="exact", network="eip155:8453", asset=_X402_USDC,
    amount="5000000", pay_to=_X402_TREASURY, max_timeout_seconds=3600,
    extra=_USDC_EXTRA,
)
_X402_REQ_GUIDE = PaymentRequirements(
    scheme="exact", network="eip155:8453", asset=_X402_USDC,
    amount="29000000", pay_to=_X402_TREASURY, max_timeout_seconds=3600,
    extra=_USDC_EXTRA,
)
_X402_REQ_MICRO = PaymentRequirements(
    scheme="exact", network="eip155:8453", asset=_X402_USDC,
    amount="10000", pay_to=_X402_TREASURY, max_timeout_seconds=300,
    extra=_USDC_EXTRA,
)
_X402_REQ_DERIV_GUIDE = PaymentRequirements(
    scheme="exact", network="eip155:8453", asset=_X402_USDC,
    amount="3000000", pay_to=_X402_TREASURY, max_timeout_seconds=3600,
    extra=_USDC_EXTRA,
)
_X402_REQ_BEN_50CENT = PaymentRequirements(
    scheme="exact", network="eip155:8453", asset=_X402_USDC,
    amount="500000", pay_to=_X402_TREASURY, max_timeout_seconds=300,
    extra=_USDC_EXTRA,
)
_X402_REQS             = [_X402_REQ_MICRO, _X402_REQ_TRIAL, _X402_REQ_ANNUAL]
_X402_REQS_GUIDE       = [_X402_REQ_GUIDE]
_X402_REQS_DERIV_GUIDE = [_X402_REQ_DERIV_GUIDE]
_X402_REQS_BEN_50CENT  = [_X402_REQ_BEN_50CENT]
_X402_REQS_API         = [_X402_REQ_ANNUAL]

_MICRO_PRICE_USDC = 0.01  # $0.01 per call

# x402 routes for PaymentMiddlewareASGI — dedicated agent-native endpoint.
# Network: eip155:8453 (Base mainnet) when CDP keys present, else eip155:84532 (testnet).
# Bazaar crawler just needs a 402 — network doesn't affect indexing.
# Real agent payments: use CDP keys (cdp.coinbase.com) to unlock mainnet.
_X402_ROUTES = {
    "GET /v2/x402/agent-signal": RouteConfig(
        accepts=[
            PaymentOption(scheme="exact", pay_to=_X402_TREASURY, price="$29.00", network=_X402_NETWORK),
            PaymentOption(scheme="exact", pay_to=_X402_TREASURY, price="$5.00",  network=_X402_NETWORK),
        ],
        description="Octodamus Market Intelligence — oracle signals, Fear & Greed, Polymarket edges, macro. 27 live feeds.",
        mime_type="application/json",
        extensions={
            "bazaar": {
                "discoverable": True,
                "category":     "trading",
                "tags":         ["crypto", "signals", "oracle", "polymarket", "bitcoin", "market-intelligence"],
            }
        },
    ),
}


_BAZAAR_EXT = {
    "bazaar": {
        "discoverable": True,
        "category":     "trading",
        "tags":         ["crypto", "signals", "oracle", "polymarket", "bitcoin", "market-intelligence"],
    }
}

def _x402_headers(amount_usdc: float = 29.0) -> dict:
    """Build 402 response headers via Coinbase x402 SDK with Bazaar discovery extension.
    The Bazaar extension makes Agentic.Market auto-index this endpoint."""
    pr = _x402_server.create_payment_required_response(_X402_REQS, extensions=_BAZAAR_EXT)
    pr_b64 = _b64.b64encode(pr.model_dump_json(by_alias=True).encode()).decode()
    return {
        "payment-required":   pr_b64,
        "X-Payment-Required": json.dumps({
            "version": "x402/1",
            "accepts": [r.model_dump(by_alias=True) for r in _X402_REQS],
        }),
    }


def _x402_header(amount_usdc: float = 29.0) -> dict:
    """
    Build the x402/1 payment descriptor for the X-Payment-Required header.
    amount_usdc in whole dollars → converted to USDC micro-units (6 decimals).
    """
    micro = str(int(amount_usdc * 1_000_000))
    return {
        "version": "x402/1",
        "accepts": [
            {
                "scheme":            "exact",
                "network":           "base-mainnet",
                "maxAmountRequired": micro,
                "payTo":             _X402_TREASURY,
                "asset":             _X402_USDC,
                "extra": {
                    "description": "OctoData Premium API — annual access (365 days, 10k req/day)",
                    "mimeType":    "application/json",
                    "checkout":    "POST https://api.octodamus.com/v1/agent-checkout?product=premium_annual",
                    "docs":        "https://api.octodamus.com/docs",
                },
            },
            {
                "scheme":            "exact",
                "network":           "base-mainnet",
                "maxAmountRequired": "5000000",
                "payTo":             _X402_TREASURY,
                "asset":             _X402_USDC,
                "extra": {
                    "description": "OctoData Premium API — 7-day trial",
                    "mimeType":    "application/json",
                    "checkout":    "POST https://api.octodamus.com/v1/agent-checkout?product=premium_trial",
                    "docs":        "https://api.octodamus.com/docs",
                },
            },
        ],
    }


def _x402_headers_legacy(amount_usdc: float = 29.0) -> dict:
    """Legacy — kept for reference only, not called."""
    import base64
    micro = str(int(amount_usdc * 1_000_000))

    _signal_example = {
        "action":          "BUY",
        "confidence":      0.78,
        "signal":          "BULLISH",
        "fear_greed":      62,
        "btc_trend":       "UP",
        "polymarket_edge": {"market": "BTC above 90k", "ev": 0.14},
        "reasoning":       "Oracle 9/11 consensus bullish. Fear & Greed neutral-greed zone.",
    }
    bazaar_ext = {
        "info": {
            "input": {
                "method":       "GET",
                "type":         "http",
                "discoverable": True,
            },
            "output": {
                "type":    "json",
                "example": _signal_example,
            },
        },
        # inputSchema = direct JSON Schema for the request input (not wrapped)
        # x402scan derives the input schema from this field
        "inputSchema": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type":    "object",
            "properties": {
                "method":      {"type": "string", "const": "GET"},
                "url":         {"type": "string", "format": "uri"},
                "headers": {
                    "type": "object",
                    "properties": {
                        "X-OctoData-Key": {"type": "string", "description": "API key from /v1/signup or /v1/agent-checkout"},
                    },
                },
            },
            "required": ["method", "url"],
        },
        "outputSchema": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type":    "object",
            "properties": {
                "action":          {"type": "string", "enum": ["BUY", "SELL", "HOLD"]},
                "confidence":      {"type": "number", "minimum": 0, "maximum": 1},
                "signal":          {"type": "string", "enum": ["BULLISH", "BEARISH", "NEUTRAL"]},
                "fear_greed":      {"type": "number", "minimum": 0, "maximum": 100},
                "btc_trend":       {"type": "string", "enum": ["UP", "DOWN", "SIDEWAYS"]},
                "polymarket_edge": {"type": "object"},
                "reasoning":       {"type": "string"},
            },
        },
    }

    accepts_entry = {
        "scheme":            "exact",
        "network":           "eip155:8453",
        "maxAmountRequired": micro,
        "amount":            micro,
        "resource":          "https://api.octodamus.com/v2/agent-signal",
        "description":       "Octodamus Market Intelligence API — real-time crypto signals, Fear & Greed, Polymarket edge. Annual access.",
        "mimeType":          "application/json",
        "payTo":             _X402_TREASURY,
        "maxTimeoutSeconds": 300,
        "asset":             _X402_USDC,
        "extra": {
            "name":    "USD Coin",
            "version": "2",
            "chainId": 8453,
        },
        "extensions": {"bazaar": bazaar_ext},
    }

    trial_entry = {
        "scheme":            "exact",
        "network":           "eip155:8453",
        "maxAmountRequired": "5000000",
        "amount":            "5000000",
        "resource":          "https://api.octodamus.com/v2/agent-signal",
        "description":       "Octodamus Market Intelligence API — 7-day trial",
        "mimeType":          "application/json",
        "payTo":             _X402_TREASURY,
        "maxTimeoutSeconds": 300,
        "asset":             _X402_USDC,
        "extra": {
            "name":    "USD Coin",
            "version": "2",
            "chainId": 8453,
        },
        "extensions": {"bazaar": bazaar_ext},
    }

    v2_payload = {
        "x402Version": 2,
        "error":       "Payment Required",
        "accepts":     [accepts_entry, trial_entry],
    }
    v2_b64 = base64.b64encode(
        json.dumps(v2_payload, separators=(",", ":")).encode()
    ).decode()
    return {
        "X-Payment-Required": json.dumps(_x402_header(amount_usdc)),
        "payment-required":   v2_b64,
    }


async def require_key_v2(request: Request, api_key: str = Security(API_KEY_HEADER)):
    """
    Require valid key + enforce tier rate limits.
    Also supports x402 crypto payment flow for AI agents:
      - No key + X-PAYMENT header → verify on-chain payment, provision key
      - No key, no payment → 402 with x402/1 X-Payment-Required header
    Returns (api_key, entry, rl) tuple.
    """
    # x402: agent paid — check both V2 (PAYMENT-SIGNATURE) and V1 (X-PAYMENT) header names
    x_payment = (
        request.headers.get("PAYMENT-SIGNATURE")
        or request.headers.get("Payment-Signature")
        or request.headers.get("X-Payment")
        or request.headers.get("X-PAYMENT", "")
    )

    if x_payment and not api_key:
        try:
            # Decode payload — agents send base64 JSON or raw JSON
            try:
                raw = _b64.b64decode(x_payment)
            except Exception:
                raw = x_payment.encode() if isinstance(x_payment, str) else x_payment

            payload = _parse_x402_payload(raw)

            # Try each requirement until one verifies
            verified_req = None
            last_reason = "no matching scheme"
            for req in _X402_REQS:
                try:
                    vr = _x402_server.verify_payment(payload, req)
                    if vr.is_valid:
                        verified_req = req
                        break
                    else:
                        last_reason = f"{vr.invalid_reason}: {vr.invalid_message}"
                        import logging as _l; _l.getLogger("uvicorn.error").warning(f"[x402] verify failed req={req.amount}: {last_reason}")
                except Exception as _ve:
                    last_reason = str(_ve)
                    import logging as _l; _l.getLogger("uvicorn.error").warning(f"[x402] verify exception req={req.amount}: {_ve}")
                    continue

            if not verified_req:
                raise HTTPException(
                    status_code=402,
                    headers=_x402_headers(),
                    detail={"error": "payment_invalid", "message": f"Verification failed: {last_reason}"},
                )

            # Settle via Coinbase facilitator
            sr = _x402_server.settle_payment(payload, verified_req)
            if sr.success:
                payer = sr.payer or "agent"
                is_micro = verified_req.amount == "10000"

                if is_micro:
                    # Micro-payment ($0.01) — grant access for this single request only.
                    # No key provisioned. EIP-3009 nonce prevents replay at contract level.
                    api_key = f"__micro__{payer[:16]}"
                    _micro_entry = {
                        "tier": "premium", "label": f"micro:{payer[:12]}",
                        "email": "", "created": datetime.utcnow().isoformat(),
                    }
                    # Fire-and-forget owner notify + customer log
                    import threading as _mt
                    def _micro_notify(p=payer):
                        try:
                            from octo_health import send_email_alert
                            send_email_alert(
                                subject=f"[Octodamus] Micro-payment $0.01 — {p[:18]}",
                                body=f"Pay-per-call.\nWallet: {p}\nEndpoint: {str(request.url.path)}\nAmount: $0.01 USDC",
                            )
                        except Exception: pass
                        try:
                            from octo_agent_db import record_customer
                            record_customer(api_key, "micro", "", p, "micro_x402")
                        except Exception: pass
                    _mt.Thread(target=_micro_notify, daemon=True).start()

                    rl = _check_rate_limit(api_key, "premium")
                    return api_key, _micro_entry, rl

                else:
                    # Subscription payment ($5 trial / $29 annual) — provision persistent key
                    tier = "premium"
                    try:
                        from octo_api_keys import create_key
                        api_key = create_key(
                            email=f"x402_{payer[:16]}@base.agent",
                            tier=tier,
                        )
                    except Exception as _ke:
                        import logging as _kl; _kl.getLogger("uvicorn.error").warning(f"[x402] key provision error: {_ke}")
                        api_key = None
            else:
                raise HTTPException(
                    status_code=402,
                    headers=_x402_headers(),
                    detail={"error": "settlement_failed", "reason": sr.error_reason},
                )
        except HTTPException:
            raise
        except Exception as _xe:
            print(f"[x402] payment verification error: {type(_xe).__name__}: {_xe}")

    if not api_key:
        # Agent greeting — detect agent and personalise the 402
        _greeting = None
        try:
            from octo_agent_db import detect_agent, _octodamus_greeting, _visitor_id
            _ua  = request.headers.get("user-agent", "")
            _ip  = request.client.host if request.client else ""
            _det = detect_agent(_ua, _ip)
            if _det["is_agent"]:
                _greeting = _octodamus_greeting(_det["agent_type"], str(request.url.path), False, 1)
        except Exception:
            pass

        _seats_left = max(0, _EARLY_BIRD_LIMIT - _premium_seat_count())
        _price = _EARLY_BIRD_PRICE if _seats_left > 0 else _STANDARD_PRICE
        _detail = {
            "x402":              "x402/1",
            "error":             "payment_required",
            "message":           "Octodamus Market Intelligence API. Pay per call or subscribe.",
            "option_0_micro":    f"X-Payment: sign $0.01 USDC EIP-3009 — pay per call, no subscription, instant",
            "option_1_free":     "POST https://api.octodamus.com/v1/signup?email=YOUR_EMAIL — 500 req/day free",
            "option_2_trial":    "GET https://api.octodamus.com/v1/subscribe?plan=trial — $5 USDC, 7 days, 10k req/day",
            "option_3_annual":   f"GET https://api.octodamus.com/v1/subscribe?plan=annual — ${_price} USDC/yr, 365 days" + (f" — EARLY BIRD: {_seats_left} seats left" if _seats_left > 0 else ""),
            "option_4_guide":    "GET https://api.octodamus.com/v1/guide — $29 USDC, Build The House guide",
            "micro_pay_to":      _X402_TREASURY,
            "micro_asset":       _X402_USDC,
            "micro_amount_usdc": _MICRO_PRICE_USDC,
            "network":           "base-mainnet (eip155:8453)",
            "header_name":       "X-OctoData-Key",
            "docs":              "https://api.octodamus.com/docs",
        }
        if _greeting:
            _detail["octodamus"] = _greeting

        raise HTTPException(status_code=402, headers=_x402_headers(), detail=_detail)

    entry = validate_key(api_key)
    if not entry:
        raise HTTPException(
            status_code=403,
            detail={
                "error_code": "INVALID_KEY",
                "message":    "Invalid or expired API key.",
                "get_key":    "POST https://api.octodamus.com/v1/signup?email=your@email.com",
                "docs":       "https://api.octodamus.com/docs",
            },
        )
    rl = _check_rate_limit(api_key, entry.get("tier", "basic"))
    return (api_key, entry, rl)

# â"€â"€ Snapshot loader â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

def load_snapshot(snapshot_type: str, target_date: Optional[str] = None) -> dict:
    if target_date:
        try:
            d = date.fromisoformat(target_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Use YYYY-MM-DD")
        path = DATA_DIR / str(d) / f"{snapshot_type}.json"
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"No {snapshot_type} snapshot for {d}")
        return json.loads(path.read_text())
    for i in range(7):
        d = date.today() - timedelta(days=i)
        path = DATA_DIR / str(d) / f"{snapshot_type}.json"
        if path.exists():
            return json.loads(path.read_text())
    raise HTTPException(status_code=404, detail=f"No recent {snapshot_type} snapshot found")

# â"€â"€ App â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

app = FastAPI(
    title="OctoData API",
    description="AI-powered market intelligence by Octodamus (@octodamusai)",
    version="3.0.0",
    docs_url=None,   # custom themed docs below
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)

# ── Agent tracking middleware ─────────────────────────────────────────────────
from starlette.middleware.base import BaseHTTPMiddleware as _BaseHTTPMiddleware
from starlette.responses import Response as _StarResponse
import threading as _mw_threading

_tracking_executor = _mw_threading.Thread  # use threads for non-blocking DB writes

class AgentTrackingMiddleware(_BaseHTTPMiddleware):
    _skip_prefixes = ("/docs", "/openapi", "/_", "/static", "/favicon")

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        path = request.url.path
        if not any(path.startswith(p) for p in self._skip_prefixes):
            import threading as _t
            _t.Thread(
                target=self._record,
                args=(
                    request.client.host if request.client else "",
                    request.headers.get("user-agent", ""),
                    path,
                    request.method,
                    response.status_code,
                    request.headers.get("x-octodata-key", ""),
                    request.headers.get("referer", ""),
                ),
                daemon=True,
            ).start()
        return response

    @staticmethod
    def _record(ip, ua, path, method, status, key, referrer):
        try:
            from octo_agent_db import record_visit
            record_visit(ip, ua, path, method, status, key, referrer)
        except Exception:
            pass

app.add_middleware(AgentTrackingMiddleware)
# PaymentMiddlewareASGI removed — payment gating handled directly in v2_x402_agent_signal
# using _x402_headers_legacy (no server init dependency) + _x402_verify_settle for settled payments.


@app.on_event("startup")
async def _startup_prewarm():
    """Pre-warm tool cache in background so first real request is fast."""
    def _warm():
        import time as _t
        _t.sleep(3)  # let uvicorn finish binding port first
        try:
            from octo_distro import oracle_scorecard, macro_pulse
            _tool_cache["scorecard"] = {"ts": _t.time(), "val": oracle_scorecard()}
            _tool_cache["macro"]     = {"ts": _t.time(), "val": macro_pulse()}
        except Exception as e:
            print(f"[API] prewarm warning: {e}")
    threading.Thread(target=_warm, daemon=True, name="cache-prewarm").start()

# -- Custom dark-themed Swagger UI --------------------------------------------

from fastapi.responses import HTMLResponse

@app.get("/docs", include_in_schema=False)
def custom_docs():
    return HTMLResponse("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>OctoData API Docs</title>
<link rel="icon" type="image/png" href="https://octodamus.com/octo_logo.png">
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Syne:wght@400;600;700&family=JetBrains+Mono:wght@300;400;500&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css">
<style>
:root {
  --void:#000810; --depth:#020d1a; --surface:#04162a; --raised:#061e38;
  --pulse:#00c8ff; --bio:#00ffb3; --down:#ff2d55; --gold:#ffc800;
  --border:rgba(0,140,255,0.12); --borderb:rgba(0,140,255,0.22);
  --text:#8ab8d4; --soft:#3d6e8a; --muted:#1d3f55; --bright:#c8e8f8;
}
*{box-sizing:border-box;}
body{margin:0;background:var(--void);color:var(--text);font-family:'Syne',sans-serif;}
body::after{content:'';position:fixed;inset:0;pointer-events:none;z-index:9999;
  background:repeating-linear-gradient(0deg,transparent,transparent 2px,rgba(0,0,0,0.04) 2px,rgba(0,0,0,0.04) 4px);}

/* HEADER */
.octo-header{
  background:rgba(0,8,16,0.96);border-bottom:1px solid var(--border);
  padding:0 48px;height:52px;display:flex;align-items:center;justify-content:space-between;
  position:sticky;top:0;z-index:500;backdrop-filter:blur(24px);
}
.octo-header a{text-decoration:none;}
.octo-brand{font-family:'Bebas Neue',sans-serif;font-size:1.3rem;letter-spacing:0.2em;color:var(--pulse);}
.octo-brand em{color:#8fa8bc;font-style:normal;}
.octo-nav{display:flex;gap:20px;align-items:center;}
.octo-nav a{font-family:'JetBrains Mono',monospace;font-size:0.63rem;letter-spacing:0.16em;text-transform:uppercase;color:var(--soft);text-decoration:none;transition:color .2s;}
.octo-nav a:hover{color:var(--pulse);}
.octo-badge{font-family:'JetBrains Mono',monospace;font-size:0.57rem;letter-spacing:0.14em;text-transform:uppercase;padding:4px 12px;border:1px solid rgba(0,255,179,0.3);color:var(--bio);animation:blink 3s ease-in-out infinite;}
@keyframes blink{0%,100%{border-color:rgba(0,255,179,0.28);}50%{border-color:rgba(0,255,179,0.65);}}

/* SWAGGER OVERRIDES */
.swagger-ui { font-family: 'Syne', sans-serif !important; }
.swagger-ui .topbar { display: none; }
.swagger-ui .wrapper { max-width: 1200px; padding: 0 48px; }

/* Info block */
.swagger-ui .info { margin: 48px 0 32px; }
.swagger-ui .info .title {
  font-family: 'Bebas Neue', sans-serif !important;
  font-size: 2.8rem !important; letter-spacing: 0.12em !important;
  color: var(--bright) !important;
}
.swagger-ui .info .title small { font-size: 1rem; color: var(--soft); letter-spacing: 0.08em; }
.swagger-ui .info p, .swagger-ui .info li { color: var(--text) !important; font-size: 0.9rem; }
.swagger-ui .info a { color: var(--pulse) !important; }

/* Scheme / server */
.swagger-ui .scheme-container {
  background: var(--surface) !important;
  border: 1px solid var(--border) !important;
  box-shadow: none !important; padding: 14px 20px !important; margin-bottom: 24px;
}
.swagger-ui .schemes > label, .swagger-ui select {
  font-family: 'JetBrains Mono', monospace !important;
  color: var(--text) !important; background: var(--depth) !important;
  border: 1px solid var(--borderb) !important;
}

/* Authorize button */
.swagger-ui .btn.authorize {
  background: rgba(0,255,179,0.08) !important;
  border: 1px solid rgba(0,255,179,0.35) !important;
  color: var(--bio) !important;
  font-family: 'JetBrains Mono', monospace !important;
  font-size: 0.72rem !important; letter-spacing: 0.12em !important;
  border-radius: 0 !important;
}
.swagger-ui .btn.authorize svg { fill: var(--bio) !important; }

/* Tags / operation groups */
.swagger-ui .opblock-tag {
  font-family: 'Bebas Neue', sans-serif !important;
  font-size: 1rem !important; letter-spacing: 0.2em !important;
  color: var(--pulse) !important;
  border-bottom: 1px solid var(--border) !important;
}
.swagger-ui .opblock-tag:hover { background: rgba(0,200,255,0.04) !important; }

/* Operation blocks */
.swagger-ui .opblock {
  background: var(--surface) !important;
  border: 1px solid var(--border) !important;
  border-radius: 0 !important; box-shadow: none !important; margin-bottom: 8px;
}
.swagger-ui .opblock.opblock-get { border-left: 3px solid var(--pulse) !important; }
.swagger-ui .opblock.opblock-post { border-left: 3px solid var(--bio) !important; }
.swagger-ui .opblock.opblock-delete { border-left: 3px solid var(--down) !important; }

.swagger-ui .opblock .opblock-summary {
  background: transparent !important; border: none !important; padding: 10px 16px;
}
.swagger-ui .opblock-summary-method {
  font-family: 'JetBrains Mono', monospace !important;
  border-radius: 0 !important; font-size: 0.68rem !important;
  letter-spacing: 0.1em !important;
}
.swagger-ui .opblock-get .opblock-summary-method { background: rgba(0,200,255,0.15) !important; color: var(--pulse) !important; }
.swagger-ui .opblock-post .opblock-summary-method { background: rgba(0,255,179,0.12) !important; color: var(--bio) !important; }
.swagger-ui .opblock-delete .opblock-summary-method { background: rgba(255,45,85,0.12) !important; color: var(--down) !important; }

.swagger-ui .opblock-summary-path, .swagger-ui .opblock-summary-path__deprecated {
  font-family: 'JetBrains Mono', monospace !important;
  color: var(--bright) !important; font-size: 0.85rem !important;
}
.swagger-ui .opblock-summary-description {
  font-family: 'Syne', sans-serif !important;
  color: var(--soft) !important; font-size: 0.78rem !important;
}

/* Expanded block body */
.swagger-ui .opblock-body { background: var(--depth) !important; }
.swagger-ui .opblock-description-wrapper p,
.swagger-ui .opblock-external-docs-wrapper p,
.swagger-ui .opblock-title_normal p { color: var(--text) !important; }

/* Parameters */
.swagger-ui table thead tr td, .swagger-ui table thead tr th {
  font-family: 'JetBrains Mono', monospace !important;
  color: var(--soft) !important; font-size: 0.65rem !important;
  border-bottom: 1px solid var(--border) !important; background: transparent !important;
}
.swagger-ui .parameter__name {
  font-family: 'JetBrains Mono', monospace !important;
  color: var(--bright) !important; font-size: 0.78rem !important;
}
.swagger-ui .parameter__type {
  font-family: 'JetBrains Mono', monospace !important;
  color: var(--pulse) !important; font-size: 0.68rem !important;
}
.swagger-ui .parameter__in {
  font-family: 'JetBrains Mono', monospace !important;
  color: var(--soft) !important; font-size: 0.62rem !important;
}
.swagger-ui table.model tr td { border-color: var(--border) !important; }
.swagger-ui tr.odd { background: rgba(0,8,16,0.4) !important; }

/* Execute/Try buttons */
.swagger-ui .btn { border-radius: 0 !important; font-family: 'JetBrains Mono', monospace !important; font-size: 0.68rem !important; letter-spacing: 0.1em !important; }
.swagger-ui .btn.execute { background: rgba(0,200,255,0.1) !important; border: 1px solid rgba(0,200,255,0.35) !important; color: var(--pulse) !important; }
.swagger-ui .btn.execute:hover { background: rgba(0,200,255,0.2) !important; }
.swagger-ui .btn.cancel { background: rgba(255,45,85,0.08) !important; border: 1px solid rgba(255,45,85,0.3) !important; color: var(--down) !important; }
.swagger-ui .btn.try-out__btn { background: rgba(0,255,179,0.07) !important; border: 1px solid rgba(0,255,179,0.25) !important; color: var(--bio) !important; }

/* Inputs */
.swagger-ui input[type=text], .swagger-ui input[type=password], .swagger-ui textarea, .swagger-ui select {
  background: var(--void) !important; border: 1px solid var(--borderb) !important;
  color: var(--bright) !important; font-family: 'JetBrains Mono', monospace !important;
  font-size: 0.78rem !important; border-radius: 0 !important;
}
.swagger-ui input[type=text]:focus, .swagger-ui input[type=password]:focus, .swagger-ui textarea:focus {
  border-color: var(--pulse) !important; outline: none !important;
}

/* Response codes */
.swagger-ui .responses-inner h4, .swagger-ui .responses-inner h5 { color: var(--text) !important; }
.swagger-ui .response-col_status { font-family: 'JetBrains Mono', monospace !important; color: var(--bio) !important; }
.swagger-ui .response-col_description { color: var(--text) !important; }
.swagger-ui .highlight-code { background: var(--void) !important; }
.swagger-ui .microlight { font-family: 'JetBrains Mono', monospace !important; font-size: 0.74rem !important; color: var(--bio) !important; }

/* Models section */
.swagger-ui section.models { border: 1px solid var(--border) !important; background: var(--surface) !important; }
.swagger-ui section.models h4 { font-family: 'Bebas Neue', sans-serif !important; letter-spacing: 0.15em !important; color: var(--pulse) !important; }
.swagger-ui .model-title { font-family: 'JetBrains Mono', monospace !important; color: var(--bright) !important; }
.swagger-ui .model { color: var(--text) !important; }
.swagger-ui .prop-type { color: var(--pulse) !important; }
.swagger-ui .prop-format { color: var(--soft) !important; }

/* Modal (authorize dialog) */
.swagger-ui .dialog-ux .modal-ux {
  background: var(--depth) !important; border: 1px solid var(--borderb) !important;
  border-radius: 0 !important; box-shadow: 0 0 60px rgba(0,0,0,0.8) !important;
}
.swagger-ui .dialog-ux .modal-ux-header { border-bottom: 1px solid var(--border) !important; background: var(--surface) !important; }
.swagger-ui .dialog-ux .modal-ux-header h3 { font-family: 'Bebas Neue', sans-serif !important; letter-spacing: 0.15em !important; color: var(--bright) !important; }

/* Scrollbar */
::-webkit-scrollbar { width: 6px; } ::-webkit-scrollbar-track { background: var(--void); } ::-webkit-scrollbar-thumb { background: var(--raised); }
</style>
</head>
<body>

<div class="octo-header">
  <a href="https://octodamus.com" class="octo-brand">OCTODAMUS <em>API</em></a>
  <div class="octo-nav">
    <a href="https://octodamus.com/signals.html">Docs</a>
    <a href="https://octodamus.com/free-key.html">Get Free Key</a>
    <a href="https://octodamus.com/upgrade.html">Premium</a>
    <span class="octo-badge">&#9679; v3.0</span>
  </div>
</div>

<!-- ── INTRO ─────────────────────────────────────────────────────────────── -->
<style>
.intro-wrap{max-width:1200px;margin:0 auto;padding:64px 48px 0;}
.intro-hero{text-align:center;padding-bottom:64px;border-bottom:1px solid var(--border);}
.intro-eyebrow{font-family:'JetBrains Mono',monospace;font-size:0.63rem;letter-spacing:0.25em;text-transform:uppercase;color:var(--pulse);margin-bottom:18px;}
.intro-title{font-family:'Bebas Neue',sans-serif;font-size:3.6rem;letter-spacing:0.1em;color:var(--bright);line-height:1.05;margin:0 0 20px;}
.intro-sub{font-size:1.05rem;color:var(--text);max-width:640px;margin:0 auto 36px;line-height:1.7;}
.intro-cta-row{display:flex;gap:14px;justify-content:center;flex-wrap:wrap;}
.btn-primary{font-family:'JetBrains Mono',monospace;font-size:0.68rem;letter-spacing:0.14em;text-transform:uppercase;padding:11px 26px;background:rgba(0,200,255,0.1);border:1px solid rgba(0,200,255,0.4);color:var(--pulse);text-decoration:none;transition:all .2s;}
.btn-primary:hover{background:rgba(0,200,255,0.2);border-color:var(--pulse);}
.btn-secondary{font-family:'JetBrains Mono',monospace;font-size:0.68rem;letter-spacing:0.14em;text-transform:uppercase;padding:11px 26px;background:transparent;border:1px solid var(--border);color:var(--soft);text-decoration:none;transition:all .2s;}
.btn-secondary:hover{border-color:var(--soft);color:var(--text);}

.section-head{font-family:'Bebas Neue',sans-serif;font-size:1.6rem;letter-spacing:0.18em;color:var(--bright);margin:0 0 8px;}
.section-sub{font-size:0.85rem;color:var(--soft);margin:0 0 36px;line-height:1.6;}

/* STREAMS */
.streams-section{padding:56px 0 52px;border-bottom:1px solid var(--border);}
.streams-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:16px;}
.stream-card{background:var(--surface);border:1px solid var(--border);padding:22px 24px;transition:border-color .2s;}
.stream-card:hover{border-color:var(--borderb);}
.stream-label{font-family:'JetBrains Mono',monospace;font-size:0.6rem;letter-spacing:0.2em;text-transform:uppercase;color:var(--pulse);margin-bottom:8px;}
.stream-name{font-family:'Syne',sans-serif;font-weight:600;font-size:0.95rem;color:var(--bright);margin-bottom:6px;}
.stream-desc{font-size:0.78rem;color:var(--text);line-height:1.55;}
.stream-source{font-family:'JetBrains Mono',monospace;font-size:0.58rem;color:var(--muted);margin-top:10px;letter-spacing:0.1em;}

/* WHY */
.why-section{padding:56px 0 52px;border-bottom:1px solid var(--border);}
.why-grid{display:grid;grid-template-columns:1fr 1fr;gap:28px;}
@media(max-width:700px){.why-grid{grid-template-columns:1fr;}}
.why-card{border-left:2px solid var(--border);padding:0 0 0 22px;}
.why-card.accent-pulse{border-color:rgba(0,200,255,0.45);}
.why-card.accent-bio{border-color:rgba(0,255,179,0.38);}
.why-card.accent-gold{border-color:rgba(255,200,0,0.38);}
.why-card.accent-down{border-color:rgba(255,45,85,0.38);}
.why-title{font-family:'Syne',sans-serif;font-weight:700;font-size:0.95rem;color:var(--bright);margin-bottom:8px;}
.why-body{font-size:0.82rem;color:var(--text);line-height:1.65;}

/* CONNECT */
.connect-section{padding:56px 0 52px;border-bottom:1px solid var(--border);}
.steps{display:flex;flex-direction:column;gap:0;}
.step{display:grid;grid-template-columns:52px 1fr;gap:24px;padding:28px 0;border-bottom:1px solid var(--border);}
.step:last-child{border-bottom:none;}
.step-num{font-family:'Bebas Neue',sans-serif;font-size:2.4rem;letter-spacing:0.1em;color:var(--raised);line-height:1;padding-top:4px;}
.step-head{font-family:'Syne',sans-serif;font-weight:700;font-size:1rem;color:var(--bright);margin-bottom:8px;}
.step-body{font-size:0.83rem;color:var(--text);line-height:1.65;margin-bottom:14px;}
.code-block{background:var(--void);border:1px solid var(--border);padding:14px 18px;font-family:'JetBrains Mono',monospace;font-size:0.72rem;color:var(--bio);line-height:1.7;overflow-x:auto;white-space:pre;}
.code-comment{color:var(--soft);}
.code-key{color:var(--pulse);}
.code-str{color:var(--gold);}

/* TIER STRIP */
.tier-strip{padding:48px 0 64px;}
.tier-cards{display:flex;gap:20px;flex-wrap:wrap;}
.t-card{flex:1;min-width:260px;background:var(--surface);border:1px solid var(--border);padding:28px 28px 24px;}
.t-card.pro{border-color:rgba(0,255,179,0.28);background:rgba(0,255,179,0.03);}
.t-badge{font-family:'JetBrains Mono',monospace;font-size:0.57rem;letter-spacing:0.2em;text-transform:uppercase;color:var(--soft);margin-bottom:14px;}
.t-badge.pro-badge{color:var(--bio);}
.t-name{font-family:'Bebas Neue',sans-serif;font-size:1.8rem;letter-spacing:0.12em;color:var(--bright);margin-bottom:4px;}
.t-price{font-family:'JetBrains Mono',monospace;font-size:0.82rem;color:var(--text);margin-bottom:18px;}
.t-features{list-style:none;padding:0;margin:0 0 22px;}
.t-features li{font-size:0.8rem;color:var(--text);padding:5px 0;border-bottom:1px solid var(--border);line-height:1.5;}
.t-features li:last-child{border-bottom:none;}
.t-features li::before{content:'→ ';color:var(--pulse);font-family:'JetBrains Mono',monospace;font-size:0.65rem;}
.t-features li.green::before{color:var(--bio);}
.t-cta{display:block;text-align:center;font-family:'JetBrains Mono',monospace;font-size:0.65rem;letter-spacing:0.15em;text-transform:uppercase;padding:10px;border:1px solid var(--border);color:var(--soft);text-decoration:none;transition:all .2s;}
.t-cta:hover{border-color:var(--soft);color:var(--text);}
.t-cta.pro-cta{border-color:rgba(0,255,179,0.3);color:var(--bio);}
.t-cta.pro-cta:hover{background:rgba(0,255,179,0.08);}

.swagger-divider{border:none;border-top:1px solid var(--border);margin:0;}

/* CONNECT YOUR WAY */
.cyw-section{padding:56px 0 64px;border-bottom:1px solid var(--border);}
.cyw-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:20px;margin-top:36px;}
.cyw-card{background:var(--surface);border:1px solid var(--border);padding:0;overflow:hidden;}
.cyw-head{padding:18px 24px 16px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:12px;}
.cyw-badge{font-family:'JetBrains Mono',monospace;font-size:0.6rem;letter-spacing:0.18em;text-transform:uppercase;padding:3px 10px;border:1px solid;}
.cyw-badge.cb-acp{border-color:rgba(0,255,179,0.4);color:var(--bio);}
.cyw-badge.cb-mcp{border-color:rgba(0,200,255,0.4);color:var(--pulse);}
.cyw-badge.cb-saas{border-color:rgba(255,196,0,0.4);color:var(--gold);}
.cyw-badge.cb-x402{border-color:rgba(255,120,40,0.4);color:rgba(255,140,60,1);}
.cyw-badge.cb-erc{border-color:rgba(160,80,220,0.4);color:rgba(180,100,240,1);}
.cyw-title{font-family:'Syne',sans-serif;font-weight:700;font-size:1rem;color:var(--bright);}
.cyw-body{padding:20px 24px;}
.cyw-desc{font-size:0.82rem;color:var(--text);line-height:1.65;margin-bottom:16px;}
.cyw-code{background:var(--void);border:1px solid var(--border);padding:12px 16px;font-family:'JetBrains Mono',monospace;font-size:0.69rem;color:var(--bio);line-height:1.7;overflow-x:auto;white-space:pre;margin-bottom:16px;}
.cyw-endpoints{display:flex;flex-direction:column;gap:6px;margin-bottom:16px;}
.cyw-ep{display:flex;align-items:baseline;gap:8px;}
.cyw-method{font-family:'JetBrains Mono',monospace;font-size:0.58rem;padding:2px 7px;letter-spacing:0.08em;}
.cyw-method.get{background:rgba(0,200,255,0.1);color:var(--pulse);border:1px solid rgba(0,200,255,0.2);}
.cyw-method.post{background:rgba(0,255,179,0.08);color:var(--bio);border:1px solid rgba(0,255,179,0.18);}
.cyw-path{font-family:'JetBrains Mono',monospace;font-size:0.73rem;color:var(--bright);}
.cyw-ep-desc{font-size:0.74rem;color:var(--soft);}
.cyw-link{font-family:'JetBrains Mono',monospace;font-size:0.63rem;letter-spacing:0.12em;text-transform:uppercase;color:var(--soft);text-decoration:none;border:1px solid var(--border);padding:7px 14px;display:inline-block;transition:all .2s;}
.cyw-link:hover{color:var(--text);border-color:var(--soft);}
.cyw-link.cl-acp{color:var(--bio);border-color:rgba(0,255,179,0.25);}
.cyw-link.cl-acp:hover{background:rgba(0,255,179,0.06);}
.cyw-link.cl-mcp{color:var(--pulse);border-color:rgba(0,200,255,0.25);}
.cyw-link.cl-mcp:hover{background:rgba(0,200,255,0.06);}
.cyw-link.cl-saas{color:var(--gold);border-color:rgba(255,196,0,0.25);}
.cyw-link.cl-saas:hover{background:rgba(255,196,0,0.06);}
.cyw-link.cl-x402{color:rgba(255,140,60,1);border-color:rgba(255,120,40,0.25);}
.cyw-link.cl-x402:hover{background:rgba(255,120,40,0.06);}
.cyw-link.cl-erc{color:rgba(180,100,240,1);border-color:rgba(160,80,220,0.25);}
.cyw-link.cl-erc:hover{background:rgba(160,80,220,0.06);}
@media(max-width:700px){.cyw-grid{grid-template-columns:1fr;}}
</style>

<div class="intro-wrap">

  <!-- HERO -->
  <div class="intro-hero">
    <div class="intro-eyebrow">OctoData API &nbsp;·&nbsp; v3.0 &nbsp;·&nbsp; 27 live data feeds</div>
    <div class="intro-title">Market Intelligence<br>for AI Agents</div>
    <p class="intro-sub">
      Octodamus is an autonomous AI oracle that monitors derivatives markets, sentiment flows,
      and Polymarket positions 24/7. OctoData is the API layer — the same intelligence
      that powers Octodamus, available to your agents in clean, structured JSON.
    </p>
    <div class="intro-cta-row">
      <a class="btn-primary" href="https://octodamus.com/free-key.html">Get Free API Key →</a>
    </div>
  </div>

  <!-- THE DATA STREAMS -->
  <div class="streams-section">
    <div class="section-head">The Data Streams</div>
    <p class="section-sub">27 live feeds. No synthetic data. No simulated signals. Every endpoint pulls from primary sources on every request.</p>
    <div class="streams-grid">
      <div class="stream-card">
        <div class="stream-label">Derivatives</div>
        <div class="stream-name">Oracle Signals</div>
        <div class="stream-desc">High-conviction directional calls published only when 9 of 11 internal systems agree. Includes entry price, target, confidence, and full reasoning chain.</div>
        <div class="stream-source">Source: CoinGlass · Deribit · internal consensus</div>
      </div>
      <div class="stream-card">
        <div class="stream-label">On-Chain</div>
        <div class="stream-name">Funding Rates & Open Interest</div>
        <div class="stream-desc">Real-time BTC, ETH, SOL funding rates, OI, long/short ratios, and liquidation flows — the same data derivatives desks watch before sizing positions.</div>
        <div class="stream-source">Source: CoinGlass</div>
      </div>
      <div class="stream-card">
        <div class="stream-label">Prediction Markets</div>
        <div class="stream-name">Polymarket Positions</div>
        <div class="stream-desc">OctoBoto's live Polymarket plays with expected value, true probability, Kelly sizing, and confidence. Entry rule: EV &gt; 15% only.</div>
        <div class="stream-source">Source: Polymarket CLOB API</div>
      </div>
      <div class="stream-card">
        <div class="stream-label">Sentiment</div>
        <div class="stream-name">AI Sentiment Scores</div>
        <div class="stream-desc">Per-asset AI sentiment on a -100 to +100 scale. Aggregated from news, social, and on-chain signals. Updated nightly. BTC, ETH, SOL, NVDA, TSLA, AAPL.</div>
        <div class="stream-source">Source: NewsAPI · Alternative.me · proprietary NLP</div>
      </div>
      <div class="stream-card">
        <div class="stream-label">Macro</div>
        <div class="stream-name">Fear &amp; Greed Index</div>
        <div class="stream-desc">Live market fear and greed reading with historical context. Composite of volatility, momentum, social, and survey data.</div>
        <div class="stream-source">Source: Alternative.me</div>
      </div>
      <div class="stream-card">
        <div class="stream-label">Prices</div>
        <div class="stream-name">Price Snapshots</div>
        <div class="stream-desc">Current prices with 24h change for BTC, ETH, SOL, NVDA, TSLA, AAPL. Pulled fresh on every request — no stale cache.</div>
        <div class="stream-source">Source: CoinGecko · Kraken · Nasdaq</div>
      </div>
      <div class="stream-card">
        <div class="stream-label">LLM-Ready</div>
        <div class="stream-name">Market Brief</div>
        <div class="stream-desc">A single paragraph summarizing BTC/ETH/SOL price action, derivatives positioning, sentiment, and Polymarket. Designed to drop directly into an LLM system prompt.</div>
        <div class="stream-source">Source: all feeds above, synthesized by Claude</div>
      </div>
      <div class="stream-card">
        <div class="stream-label">Congressional</div>
        <div class="stream-name">Stock Trade Alerts</div>
        <div class="stream-desc">Live congressional stock trade disclosures. Know when elected officials buy or sell — before the market fully prices it in.</div>
        <div class="stream-source">Source: QuiverQuant</div>
      </div>
    </div>
  </div>

  <!-- WHY YOUR AI NEEDS THIS -->
  <div class="why-section">
    <div class="section-head">Why Your AI Needs This</div>
    <p class="section-sub">LLMs have no live market data. Without grounding, they hallucinate prices, invent signals, and give confidently wrong answers about markets that moved months ago.</p>
    <div class="why-grid">
      <div class="why-card accent-pulse">
        <div class="why-title">Ground your AI in real-time reality</div>
        <div class="why-body">Inject <code style="color:var(--pulse);font-size:0.8em">/v2/brief</code> into your system prompt and your model instantly knows current BTC funding, fear &amp; greed, open signals, and Polymarket positioning — without any hallucination risk.</div>
      </div>
      <div class="why-card accent-bio">
        <div class="why-title">Let agents self-serve intelligence</div>
        <div class="why-body">Agents can call <code style="color:var(--bio);font-size:0.8em">/v2/all</code> before every decision to pull a full market snapshot in a single request. Rate-limit headers tell them exactly when to back off — no 429 loops.</div>
      </div>
      <div class="why-card accent-gold">
        <div class="why-title">High-conviction signals, not noise</div>
        <div class="why-body">The oracle only publishes when 9 of 11 systems agree. Your agent isn't wading through 50 conflicting indicators — it gets one directional call with full reasoning when the bar is met.</div>
      </div>
      <div class="why-card accent-down">
        <div class="why-title">Agents can pay autonomously</div>
        <div class="why-body">No Stripe, no browser, no human. An agent can POST to <code style="color:var(--down);font-size:0.8em">/v1/agent-checkout</code>, send $5 USDC on Base, poll for confirmation, and receive a Premium key — fully automated.</div>
      </div>
    </div>
  </div>

  <!-- CONNECT YOUR AI -->
  <div class="connect-section">
    <div class="section-head">Connect Your AI in 3 Steps</div>
    <p class="section-sub">From zero to live market context in under 2 minutes. Works with any Python, Node, or curl-based agent.</p>
    <div class="steps">

      <div class="step">
        <div class="step-num">01</div>
        <div>
          <div class="step-head">Get a free API key — no credit card</div>
          <div class="step-body">One POST request returns your key instantly. Basic tier: 500 requests/day, 20/min. Enough to test every endpoint.</div>
          <div class="code-block"><span class="code-comment"># Terminal / curl</span>
curl -X POST "https://api.octodamus.com/v1/signup?email=you@example.com"

<span class="code-comment"># Response</span>
{ "api_key": "oct_live_xxxxxxxxxxxx", "tier": "basic", "daily_limit": 500 }</div>
        </div>
      </div>

      <div class="step">
        <div class="step-num">02</div>
        <div>
          <div class="step-head">Inject live market context into your LLM system prompt</div>
          <div class="step-body">Pull <code style="color:var(--pulse)">/v2/brief</code> before every inference call. Your model now knows current prices, positioning, and sentiment — with zero extra tokens beyond what it needs.</div>
          <div class="code-block"><span class="code-comment">## Python — inject into system prompt</span>
import httpx

<span class="code-key">OCTO_KEY</span> = <span class="code-str">"oct_live_xxxxxxxxxxxx"</span>

market = httpx.get(
    <span class="code-str">"https://api.octodamus.com/v2/brief"</span>,
    headers={<span class="code-str">"X-OctoData-Key"</span>: OCTO_KEY}
).json()

system_prompt = f<span class="code-str">\"\"\"You are a trading assistant with live market context.

{market['brief']}

Use this to ground your analysis. Do not speculate on prices you cannot verify.
Source: OctoData API (api.octodamus.com)
\"\"\"</span></div>
        </div>
      </div>

      <div class="step">
        <div class="step-num">03</div>
        <div>
          <div class="step-head">Pull everything in one call — or ask Octodamus directly</div>
          <div class="step-body">Use <code style="color:var(--pulse)">/v2/all</code> to get signals, polymarket, sentiment, prices, and brief in a single request. Or call <code style="color:var(--bio)">/v2/ask</code> to query Octodamus in natural language — no key required, 20 free questions/day per IP.</div>
          <div class="code-block"><span class="code-comment">## Option A — get everything in one call (counts as 1 request)</span>
data = httpx.get(
    <span class="code-str">"https://api.octodamus.com/v2/all"</span>,
    headers={<span class="code-str">"X-OctoData-Key"</span>: OCTO_KEY}
).json()
<span class="code-comment"># data["signal"], data["polymarket"], data["sentiment"], data["prices"], data["brief"]</span>

<span class="code-comment">## Option B — ask in natural language (no key needed)</span>
answer = httpx.post(
    <span class="code-str">"https://api.octodamus.com/v2/ask"</span>,
    params={<span class="code-str">"q"</span>: <span class="code-str">"What is your current read on BTC funding rates?"</span>}
).json()
print(answer[<span class="code-str">"answer"</span>])          <span class="code-comment"># natural language response</span>
print(answer[<span class="code-str">"suggested_endpoints"</span>]) <span class="code-comment"># which endpoint automates this</span>

<span class="code-comment">## Self-throttle using rate-limit headers on every response</span>
<span class="code-comment"># X-RateLimit-Remaining-Day: 487</span>
<span class="code-comment"># X-RateLimit-Remaining-Minute: 19</span></div>
        </div>
      </div>

    </div>
  </div>

  <!-- TIER STRIP -->
  <div class="tier-strip">
    <div class="section-head">Pricing</div>
    <p class="section-sub">Start free. Upgrade when you need full signal depth, all assets, and webhooks.</p>
    <div class="tier-cards">
      <div class="t-card">
        <div class="t-badge">Free forever</div>
        <div class="t-name">Basic</div>
        <div class="t-price">500 req/day &nbsp;·&nbsp; 20/min</div>
        <ul class="t-features">
          <li>Latest oracle signal (direction, asset, timeframe)</li>
          <li>BTC sentiment score</li>
          <li>BTC, ETH, SOL prices</li>
          <li>Top Polymarket play</li>
          <li>Market brief for LLM injection</li>
          <li>/v2/ask — 20 questions/day</li>
          <li>/v2/demo &amp; /v2/sources — no key required</li>
        </ul>
        <a class="t-cta" href="https://octodamus.com/free-key.html">Get Free Key →</a>
      </div>
      <div class="t-card pro">
        <div class="t-badge pro-badge">Premium</div>
        <div class="t-name">Premium</div>
        <div class="t-price">$29 / year</div>
        <ul class="t-features">
          <li class="green">All open signals + confidence, entry, target, reasoning chain</li>
          <li class="green">BTC, ETH, SOL, NVDA, TSLA, AAPL — all assets</li>
          <li class="green">Full AI market brief + Polymarket context + AI mood</li>
          <li class="green">All Polymarket positions with EV, true_p, Kelly size</li>
          <li class="green">Webhooks — push on signal.new, signal.resolved, polymarket.new</li>
          <li class="green">/v2/all — everything in one call</li>
          <li class="green">10,000 req/day · 200/min</li>
          <li class="green">/v2/ask — 200 questions/day</li>
        </ul>
        <a class="t-cta pro-cta" href="https://octodamus.com/upgrade.html">Upgrade to Premium →</a>
      </div>
    </div>
  </div>

  <!-- CONNECT YOUR WAY -->
  <div class="cyw-section">
    <div class="section-head">Connect Your Way</div>
    <p class="section-sub">Five integration paths. Pick the one that matches how your agent or system is built.</p>
    <div class="cyw-grid">

      <!-- ACP Agent -->
      <div class="cyw-card">
        <div class="cyw-head">
          <span class="cyw-badge cb-acp">&#9679; ACP Agent</span>
          <div class="cyw-title">Virtuals Agent Commerce Protocol</div>
        </div>
        <div class="cyw-body">
          <div class="cyw-desc">Octodamus is a live ACP provider on the Virtuals network. Any ACP-compatible agent can purchase market intelligence reports directly on-chain — no API key, no browser, no human. Pay $1 USDC per job via the Virtuals ACP protocol and receive structured JSON in return.</div>
          <div class="cyw-code"><span style="color:var(--soft)"># Connect to Octodamus as an ACP provider</span>
<span style="color:var(--soft)"># Agent ID: 019d8ec8-0885-766e-b3c4-0a2e70e31274</span>
<span style="color:var(--soft)"># Offerings: Oracle Signal · BTC Deep Dive · Fear &amp; Greed · Congress Trades</span>

npm install -g @virtual-protocol/acp-cli
acp browse Octodamus        <span style="color:var(--soft)"># find the agent</span>
acp job create --provider 019d8ec8... --offering "Oracle Market Signal"</div>
          <div class="cyw-endpoints">
            <div class="cyw-ep"><span class="cyw-method get">GET</span><span class="cyw-path">/api/report/{id}</span><span class="cyw-ep-desc">— retrieve fulfilled report</span></div>
            <div class="cyw-ep"><span class="cyw-method get">GET</span><span class="cyw-path">/.well-known/agent.json</span><span class="cyw-ep-desc">— agent card + offerings</span></div>
          </div>
          <a class="cyw-link cl-acp" href="https://app.virtuals.io" target="_blank">Browse on Virtuals &#8599;</a>
        </div>
      </div>

      <!-- MCP -->
      <div class="cyw-card">
        <div class="cyw-head">
          <span class="cyw-badge cb-mcp">MCP</span>
          <div class="cyw-title">Model Context Protocol</div>
        </div>
        <div class="cyw-body">
          <div class="cyw-desc">Add Octodamus as an MCP server in Claude, Cursor, Windsurf, or any MCP-compatible host. Ten tools available — get signals, Polymarket edges, sentiment, prices, and market briefs directly in your AI session. Also listed on Smithery.</div>
          <div class="cyw-code"><span style="color:var(--soft)"># claude_desktop_config.json / .cursor/mcp.json</span>
{
  "mcpServers": {
    "octodamus": {
      "url": "https://api.octodamus.com/mcp"
    }
  }
}</div>
          <div class="cyw-endpoints">
            <div class="cyw-ep"><span class="cyw-method post">MCP</span><span class="cyw-path">get_agent_signal</span><span class="cyw-ep-desc">— BUY/SELL/HOLD decision</span></div>
            <div class="cyw-ep"><span class="cyw-method post">MCP</span><span class="cyw-path">get_all_data</span><span class="cyw-ep-desc">— all signals in one call</span></div>
            <div class="cyw-ep"><span class="cyw-method post">MCP</span><span class="cyw-path">buy_premium_api</span><span class="cyw-ep-desc">— subscribe via x402</span></div>
          </div>
          <a class="cyw-link cl-mcp" href="https://smithery.ai/server/octodamusai/market-intelligence" target="_blank">View on Smithery &#8599;</a>
        </div>
      </div>

      <!-- SaaS -->
      <div class="cyw-card">
        <div class="cyw-head">
          <span class="cyw-badge cb-saas">SaaS</span>
          <div class="cyw-title">REST API with Key</div>
        </div>
        <div class="cyw-body">
          <div class="cyw-desc">The standard integration path. Get a free API key in one POST request and start hitting endpoints immediately. Pass your key as the <code style="color:var(--gold);font-size:0.85em">X-OctoData-Key</code> header on every request. Rate-limit headers on every response tell you exactly how much headroom you have.</div>
          <div class="cyw-code"><span style="color:var(--soft)"># 1. Get free key (500 req/day)</span>
curl -X POST "https://api.octodamus.com/v1/signup?email=you@example.com"

<span style="color:var(--soft)"># 2. Hit any endpoint</span>
curl "https://api.octodamus.com/v2/agent-signal" \
     -H "X-OctoData-Key: octo_your_key"

<span style="color:var(--soft)"># Rate-limit headers on every response:</span>
<span style="color:var(--soft)"># X-RateLimit-Remaining-Day: 487</span>
<span style="color:var(--soft)"># X-RateLimit-Remaining-Minute: 19</span></div>
          <div class="cyw-endpoints">
            <div class="cyw-ep"><span class="cyw-method get">GET</span><span class="cyw-path">/v2/agent-signal</span><span class="cyw-ep-desc">— primary signal</span></div>
            <div class="cyw-ep"><span class="cyw-method get">GET</span><span class="cyw-path">/v2/all</span><span class="cyw-ep-desc">— full snapshot</span></div>
            <div class="cyw-ep"><span class="cyw-method get">GET</span><span class="cyw-path">/v2/brief</span><span class="cyw-ep-desc">— LLM system prompt injection</span></div>
          </div>
          <a class="cyw-link cl-saas" href="https://octodamus.com/free-key.html" target="_blank">Get Free Key &#8599;</a>
        </div>
      </div>

      <!-- x402 -->
      <div class="cyw-card">
        <div class="cyw-head">
          <span class="cyw-badge cb-x402">x402</span>
          <div class="cyw-title">HTTP Payment Protocol</div>
        </div>
        <div class="cyw-body">
          <div class="cyw-desc">Pay per call or subscribe autonomously — no account, no browser, no human. Hit any endpoint without a key, receive a <code style="color:rgba(255,140,60,1);font-size:0.85em">402 Payment Required</code> response with treasury and amount, sign an EIP-3009 USDC authorization on Base, and retry with the <code style="color:rgba(255,140,60,1);font-size:0.85em">PAYMENT-SIGNATURE</code> header.</div>
          <div class="cyw-code"><span style="color:var(--soft)"># Option A — $0.01 USDC per call (no key needed)</span>
<span style="color:var(--soft)"># 1. Probe to get payment requirements</span>
GET /v2/agent-signal  →  402 + payment-required header
<span style="color:var(--soft)"># amount: 10000 (= $0.01 USDC, 6 decimals)</span>

<span style="color:var(--soft)"># Option B — Subscribe for full access</span>
GET /v1/subscribe?plan=trial   →  $5 USDC / 7 days
GET /v1/subscribe?plan=annual  →  $29 USDC / 365 days

Treasury:  0x5c6B3a3dAe296d3cef50fef96afC73410959a6Db
USDC Base: 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913
Network:   Base mainnet (eip155:8453)</div>
          <div class="cyw-endpoints">
            <div class="cyw-ep"><span class="cyw-method get">GET</span><span class="cyw-path">/.well-known/x402.json</span><span class="cyw-ep-desc">— discovery doc</span></div>
            <div class="cyw-ep"><span class="cyw-method get">GET</span><span class="cyw-path">/v1/subscribe</span><span class="cyw-ep-desc">— autonomous purchase</span></div>
          </div>
          <a class="cyw-link cl-x402" href="https://octodamus.com/for-agents.html#x402" target="_blank">x402 Code Examples &#8599;</a>
        </div>
      </div>

      <!-- ERC-8004 -->
      <div class="cyw-card">
        <div class="cyw-head">
          <span class="cyw-badge cb-erc">ERC-8004</span>
          <div class="cyw-title">On-Chain Agent Identity</div>
        </div>
        <div class="cyw-body">
          <div class="cyw-desc">Octodamus is registered on-chain via ERC-8004 on Base — a standard for autonomous agent identity and capability discovery. Any agent that reads the ERC-8004 registry can discover Octodamus, verify its identity, and call its endpoints without any prior configuration.</div>
          <div class="cyw-code"><span style="color:var(--soft)"># Discover Octodamus via ERC-8004</span>
GET /.well-known/agent.json       <span style="color:var(--soft)"># full agent card</span>
GET /.well-known/agent-registration.json  <span style="color:var(--soft)"># on-chain registration</span>

<span style="color:var(--soft)"># On-chain identity</span>
globalId:  eip155:8453:0x8004A169...#44306
Registry:  Base mainnet (eip155:8453)
Wallet:    0x94c037393ab0263194dcfd8d04a2176d6a80e385

<span style="color:var(--soft)"># Agent card includes: endpoints, pricing, payment terms,</span>
<span style="color:var(--soft;"># MCP server, x402 treasury, and capability list</span></div>
          <div class="cyw-endpoints">
            <div class="cyw-ep"><span class="cyw-method get">GET</span><span class="cyw-path">/.well-known/agent.json</span><span class="cyw-ep-desc">— ERC-8004 agent card</span></div>
            <div class="cyw-ep"><span class="cyw-method get">GET</span><span class="cyw-path">/.well-known/oauth-protected-resource</span><span class="cyw-ep-desc">— ACP resource</span></div>
          </div>
          <a class="cyw-link cl-erc" href="https://eips.ethereum.org/EIPS/eip-8004" target="_blank">ERC-8004 Spec &#8599;</a>
        </div>
      </div>

    </div>
  </div>

</div><!-- /intro-wrap -->

<hr class="swagger-divider">
<div style="max-width:1200px;margin:0 auto;padding:48px 48px 12px;">
  <div style="font-family:'Bebas Neue',sans-serif;font-size:1.4rem;letter-spacing:0.2em;color:var(--bright);margin-bottom:4px;">API Reference</div>
  <div style="font-family:'JetBrains Mono',monospace;font-size:0.62rem;letter-spacing:0.15em;color:var(--soft);text-transform:uppercase;">All endpoints · schemas · try it live</div>
</div>

<div id="swagger-ui"></div>

<script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
<script>
SwaggerUIBundle({
  url: '/openapi.json',
  dom_id: '#swagger-ui',
  presets: [SwaggerUIBundle.presets.apis, SwaggerUIBundle.SwaggerUIStandalonePreset],
  layout: 'BaseLayout',
  deepLinking: true,
  displayRequestDuration: true,
  defaultModelsExpandDepth: -1,
});
</script>
</body>
</html>""")


# â"€â"€ Public â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

@app.get("/", tags=["Info"])
def root():
    return {
        "name": "OctoData API",
        "source": {"name": "OctoData API", "by": "Octodamus (@octodamusai)", "docs": "https://api.octodamus.com/docs", "signup": "POST https://api.octodamus.com/v1/signup?email="},
        "docs": "/docs",
        "report_viewer": "/api/report?type=market_signal&ticker=BTC",
        "subscribe": "https://octodamus.com",
    }


@app.get("/health", tags=["Info"])
def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}



# ── ERC-8004 Agent Discovery ───────────────────────────────────────────────────

_ERC8004_CARD = {
    "type":     "https://eips.ethereum.org/EIPS/eip-8004#registration-v1",
    "globalId": "eip155:8453:0x8004A169FB4a3325136EB29fA0ceB6D2e539a432#44306",
    "name":     "Octodamus Market Intelligence API",
    "description": (
        "Real-time market intelligence for autonomous AI agents: Oracle trading signals "
        "(9/11 system consensus), Fear & Greed index, Polymarket prediction market edge plays, "
        "crypto sentiment, and macro data. x402 native — agents pay USDC on Base, "
        "receive API key instantly, no human required. 27 live data feeds."
    ),
    "image": "https://octodamus.com/octo_logo.png",
    "url":   "https://octodamus.com",
    "endpoints": [
        {
            "name":     "AgentSignal",
            "url":      "https://api.octodamus.com/v2/x402/agent-signal",
            "protocol": "x402",
            "method":   "GET",
            "description": "BUY/SELL/HOLD with confidence, Fear & Greed, BTC trend, Polymarket edges. Ed25519 signed. $0.01 USDC/call or $29/yr.",
            "price":    "$0.01 USDC per call",
        },
        {
            "name":     "SentimentDivergence",
            "url":      "https://api.octodamus.com/v2/ben/sentiment-divergence",
            "protocol": "x402",
            "method":   "GET",
            "description": "Detects Fear & Greed vs X crowd sentiment divergence. Returns CONTRARIAN_BEAR/BULL/ALIGNED signal. Designed by Agent_Ben.",
            "price":    "$0.50 USDC per call",
        },
        {
            "name":     "DerivativesGuide",
            "url":      "https://api.octodamus.com/v2/guide/derivatives",
            "protocol": "x402",
            "method":   "GET",
            "description": "5 Derivatives Signals Every Crypto Trader Must Know. 25,000-word PDF. Funding rates, OI, liquidation maps, CME COT.",
            "price":    "$3 USDC one-time",
        },
        {
            "name":     "BuyAnnualKey",
            "url":      "https://api.octodamus.com/v1/subscribe?plan=annual",
            "protocol": "x402",
            "method":   "GET",
            "description": "365-day Premium API key. $29 USDC on Base (first 100 seats then $149). 10k req/day.",
            "price":    "$29 USDC",
        },
        {
            "name":     "FreeDemo",
            "url":      "https://api.octodamus.com/v2/demo",
            "protocol": "REST",
            "method":   "GET",
            "description": "Free signal preview — no key required. Signal, Polymarket top play, prices, brief. Schema shows premium fields.",
            "price":    "Free",
        },
        {
            "name":     "AskOctodamus",
            "url":      "https://api.octodamus.com/v2/ask",
            "protocol": "REST",
            "method":   "POST",
            "description": "Ask any market question. Live signal-grounded answers. 20/day free.",
            "price":    "Free (20/day)",
        },
        {
            "name":     "MCPServer",
            "url":      "https://api.octodamus.com/mcp",
            "protocol": "MCP streamable-http",
            "method":   "POST",
            "description": "MCP server on Smithery. Tools: get_signal, get_market_brief, get_polymarket_edges, ask_oracle, subscribe.",
        },
    ],
    "payment": {
        "x402":        True,
        "network":     "base-mainnet",
        "chain_id":    8453,
        "asset":       "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "pay_to":      "0x5c6B3a3dAe296d3cef50fef96afC73410959a6Db",
        "products": {
            "micro_per_call":      {"price_usdc": 0.01, "endpoint": "GET /v2/x402/agent-signal"},
            "sentiment_divergence":{"price_usdc": 0.50, "endpoint": "GET /v2/ben/sentiment-divergence"},
            "derivatives_guide":   {"price_usdc": 3.00, "endpoint": "GET /v2/guide/derivatives"},
            "annual_api_key":      {"price_usdc": 29,   "duration_days": 365, "endpoint": "GET /v1/subscribe?plan=annual"},
            "free_key":            {"price_usdc": 0,    "endpoint": "POST /v1/signup?email="},
        },
    },
    "mcp":         "https://api.octodamus.com/mcp",
    "smithery":    "https://smithery.ai/server/octodamusai/market-intelligence",
    "registry":    "io.github.Octodamus/market-intelligence",
    "category":    ["market-intelligence", "crypto-signals", "prediction-markets", "macro-data"],
    "x402Support": True,
    "active":      True,
    "supportedTrust": ["reputation"],
}

@app.get("/.well-known/agent-registration.json", tags=["ERC-8004"], include_in_schema=False)
def erc8004_well_known():
    """
    ERC-8004 domain verification endpoint.
    After on-chain registration, fill in agentId below to link this domain to the on-chain identity.
    """
    return {
        "type": "https://eips.ethereum.org/EIPS/eip-8004#registration-v1",
        "registrations": [
            {"agentId": 44306, "agentRegistry": "eip155:8453:0x8004A169FB4a3325136EB29fA0ceB6D2e539a432"}
        ],
        "agent_card": _ERC8004_CARD,
    }

@app.get("/agent-registration.json", tags=["ERC-8004"], include_in_schema=False)
def erc8004_root():
    """ERC-8004 agent card — root fallback for registries that don't use .well-known."""
    return _ERC8004_CARD

@app.get("/.well-known/agent.json", tags=["ERC-8004"], include_in_schema=False)
def well_known_agent_json():
    """Agent card alias — some crawlers probe agent.json instead of agent-registration.json."""
    return _ERC8004_CARD


@app.get("/.well-known/x402.json", include_in_schema=False)
@app.get("/.well-known/x402", include_in_schema=False)
def well_known_x402():
    """
    x402 discovery document -- auto-indexed by x402scan.com and Coinbase AgentKit.
    Describes all paywalled endpoints, accepted payment schemes, and pricing.
    """
    return {
        "version": "x402/1",
        "provider": {
            "name":        "Octodamus Market Intelligence",
            "description": "Live derivatives positions, liquidation pressure, oracle arbitrage. 27 data feeds. Nothing else covers this in a single x402 call.",
            "url":         "https://api.octodamus.com",
            "x":           "@octodamusai",
            "docs":        "https://api.octodamus.com/docs",
        },
        "treasury":  _X402_TREASURY,
        "chain":     "eip155:8453",
        "asset":     _X402_USDC,
        "currency":  "USDC",
        "signing": {
            "algorithm":  "Ed25519",
            "public_key": _SIGNING_PUBKEY,
            "format":     "base64",
            "verify":     "Sign canonical JSON (sort_keys=True, no spaces) with Ed25519 public key. Signature in response body at .signature field.",
        },
        "endpoints": [
            {
                "path":        "/v2/agent-signal",
                "method":      "GET",
                "description": "Primary signal endpoint -- BUY/SELL/HOLD with confidence, Fear & Greed, BTC trend, Polymarket edge. Poll every 15 minutes.",
                "pricing": [
                    {"product": "micro_per_call",  "amount_usdc":  0.01, "description": "Pay per call via x402 EIP-3009 -- no key, no subscription"},
                    {"product": "premium_trial",   "amount_usdc":  5.0,  "description": "7-day trial, 10k req/day"},
                    {"product": "premium_annual",  "amount_usdc": 29.0,  "description": "365 days, 10k req/day"},
                ],
                "checkout": "POST https://api.octodamus.com/v1/agent-checkout",
                "returns":  {"action": "BUY|SELL|HOLD", "confidence": "0.0-1.0", "signal": "BULLISH|BEARISH|NEUTRAL", "fear_greed": "0-100"},
            },
            {
                "path":        "/v2/polymarket",
                "method":      "GET",
                "description": "Top Polymarket prediction markets with expected-value scoring and recommended side.",
                "pricing": [
                    {"product": "micro_per_call",  "amount_usdc":  0.01, "description": "Pay per call via x402 EIP-3009 -- no key, no subscription"},
                    {"product": "premium_annual",  "amount_usdc": 29.0,  "description": "365 days, 10k req/day"},
                ],
                "checkout": "POST https://api.octodamus.com/v1/agent-checkout",
            },
            {
                "path":        "/v2/sentiment",
                "method":      "GET",
                "description": "AI sentiment scores for BTC, ETH, SOL. Score -1.0 to +1.0.",
                "pricing": [
                    {"product": "micro_per_call",  "amount_usdc":  0.01, "description": "Pay per call via x402 EIP-3009 -- no key, no subscription"},
                    {"product": "premium_annual",  "amount_usdc": 29.0,  "description": "365 days, 10k req/day"},
                ],
                "checkout": "POST https://api.octodamus.com/v1/agent-checkout",
            },
            {
                "path":        "/v2/brief",
                "method":      "GET",
                "description": "Full AI market briefing in narrative format -- ideal for agent reasoning context.",
                "pricing": [
                    {"product": "micro_per_call",  "amount_usdc":  0.01, "description": "Pay per call via x402 EIP-3009 -- no key, no subscription"},
                    {"product": "premium_annual",  "amount_usdc": 29.0,  "description": "365 days, 10k req/day"},
                ],
                "checkout": "POST https://api.octodamus.com/v1/agent-checkout",
            },
            {
                "path":        "/v2/ben/sentiment-divergence",
                "method":      "GET",
                "description": "Agent_Ben's Sentiment Divergence Scanner — Fear & Greed vs X crowd sentiment for BTC/ETH/SOL. Divergence score + CONTRARIAN BEAR/BULL/ALIGNED signal. $0.50 per call.",
                "pricing": [
                    {"product": "per_call", "amount_usdc": 0.50, "description": "Single call, instant result."},
                ],
                "preview":  "GET https://api.octodamus.com/v2/ben/sentiment-divergence/preview",
                "designer": "Agent_Ben",
            },
            {
                "path":        "/v2/guide/derivatives",
                "method":      "GET",
                "description": "5 Derivatives Signals Every Crypto Trader Must Know -- 25,000-word PDF guide. Funding rates, open interest, long/short ratio, liquidation maps, CME COT positioning. Returns PDF on payment.",
                "pricing": [
                    {"product": "one_time_purchase", "amount_usdc": 3.00, "description": "Single purchase, permanent access. No subscription."},
                ],
                "preview":  "GET https://api.octodamus.com/v2/guide/derivatives/preview",
                "returns":  "application/pdf",
            },
        ],
        "micro_pricing": {
            "enabled": True,
            "amount_usdc": 0.01,
            "amount_raw": "10000",
            "asset": _X402_USDC,
            "network": "eip155:8453",
            "pay_to": _X402_TREASURY,
            "how": "Sign EIP-3009 authorization for $0.01 USDC, send as PAYMENT-SIGNATURE header. No key or account needed.",
        },
        "free_endpoints": [
            {"path": "/v2/demo",    "method": "GET",  "description": "Public signal preview -- no key required"},
            {"path": "/v2/ask",     "method": "POST", "description": "Ask any market question -- 20/day, no key required"},
            {"path": "/v2/sources", "method": "GET",  "description": "All 27 live data feeds"},
            {"path": "/v1/signup",  "method": "POST", "description": "Get free Basic key (500 req/day) with email"},
        ],
        "checkout_flow": {
            "step1": "POST /v1/agent-checkout?product=premium_trial&chain=base&agent_wallet=0xYOUR_WALLET",
            "step2": "Send exact USDC amount to payment_address on Base",
            "step3": "GET /v1/agent-checkout/status?payment_id=xxx -- poll every 15s",
            "step4": "Receive api_key in response when confirmed (~2s on Base)",
        },
    }


# â"€â"€ ACP Resource endpoints â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

_fg_cache: dict = {"data": None, "ts": 0.0}
_dom_cache: dict = {"data": None, "ts": 0.0}
_signal_cache: dict = {"data": None, "ts": 0.0}
_PUBLIC_TTL = 300.0  # 5 minutes for public market data
_SIGNAL_TTL  = 60.0  # 60 seconds for signal payload cache

@app.get("/api/fear-greed", tags=["ACP Resources"])
def acp_fear_greed():
    """Live Fear & Greed index â€" free ACP resource. Cached 5min."""
    global _fg_cache
    now = _time.monotonic()
    if _fg_cache["data"] and now - _fg_cache["ts"] < _PUBLIC_TTL:
        return _fg_cache["data"]
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from octo_pulse import run_pulse_scan
        result = run_pulse_scan()
        fng = result.get("fear_greed") or {}
        data = {
            "value": fng.get("value"),
            "label": fng.get("label"),
            "previous_close": fng.get("previous_close"),
            "timestamp": datetime.utcnow().isoformat(),
            "source": {"name": "OctoData API", "by": "Octodamus (@octodamusai)", "docs": "https://api.octodamus.com/docs", "signup": "POST https://api.octodamus.com/v1/signup?email="},
        }
        _fg_cache = {"data": data, "ts": now}
        return data
    except Exception as e:
        return _fg_cache["data"] if _fg_cache["data"] else {"error": str(e), "timestamp": datetime.utcnow().isoformat()}


@app.get("/api/btc-dominance", tags=["ACP Resources"])
def acp_btc_dominance():
    """Live BTC dominance â€" free ACP resource. Cached 5min."""
    global _dom_cache
    now = _time.monotonic()
    if _dom_cache["data"] and now - _dom_cache["ts"] < _PUBLIC_TTL:
        return _dom_cache["data"]
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from octo_gecko import run_gecko_scan
        result = run_gecko_scan()
        g = result.get("global") or {}
        data = {
            "btc_dominance": g.get("btc_dominance"),
            "total_market_cap_usd": g.get("total_market_cap_usd"),
            "market_cap_change_24h": g.get("market_cap_change_24h"),
            "trending": [c.get("symbol") for c in result.get("trending", [])],
            "top_gainers": [{"symbol": c.get("symbol"), "chg_24h": c.get("chg_24h")} for c in result.get("gainers", [])],
            "top_losers":  [{"symbol": c.get("symbol"), "chg_24h": c.get("chg_24h")} for c in result.get("losers", [])],
            "timestamp": datetime.utcnow().isoformat(),
            "source": {"name": "OctoData API", "by": "Octodamus (@octodamusai)", "docs": "https://api.octodamus.com/docs", "signup": "POST https://api.octodamus.com/v1/signup?email="},
        }
        _dom_cache = {"data": data, "ts": now}
        return data
    except Exception as e:
        return _dom_cache["data"] if _dom_cache["data"] else {"error": str(e), "timestamp": datetime.utcnow().isoformat()}


# â"€â"€ Report endpoints â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

# â"€â"€ Simple JSON store for dashboard metrics â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
import json as _json
_METRICS_FILE = Path(__file__).parent / "data" / "dashboard_metrics.json"

def _load_metrics() -> dict:
    try:
        if _METRICS_FILE.exists():
            return _json.loads(_METRICS_FILE.read_text())
    except Exception:
        pass
    return {"followers": 0, "guide_sales": 0, "guide_revenue": 0}

def _save_metrics(m: dict):
    try:
        _METRICS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _METRICS_FILE.write_text(_json.dumps(m, indent=2))
    except Exception:
        pass


@app.get("/api/metrics", tags=["ACP Resources"])
def get_metrics():
    """Live dashboard metrics â€" followers, guide sales. No auth required."""
    m = _load_metrics()
    m["timestamp"] = __import__("datetime").datetime.utcnow().isoformat()
    m["powered_by"] = "Octodamus (@octodamusai)"
    return m


@app.post("/api/metrics", tags=["ACP Resources"])
def update_metrics(
    followers: Optional[int] = None,
    guide_sales: Optional[int] = None,
    guide_revenue: Optional[float] = None,
    key=Depends(require_key)
):
    """Update dashboard metrics. Requires API key."""
    m = _load_metrics()
    if followers is not None:      m["followers"] = followers
    if guide_sales is not None:    m["guide_sales"] = guide_sales
    if guide_revenue is not None:  m["guide_revenue"] = guide_revenue
    _save_metrics(m)
    return {"status": "ok", "metrics": m}


@app.get("/api/wallet-balance", tags=["ACP Resources"])
def wallet_balance(address: str = "0x5c6B3a3dAe296d3cef50fef96afC73410959a6Db"):
    """Live ETH balance for a Base Chain wallet via Blockscout. No auth required."""
    import requests as req
    try:
        r = req.get(
            f"https://base.blockscout.com/api/v2/addresses/{address}",
            timeout=8,
            headers={"Accept": "application/json"},
        )
        d = r.json()
        raw = d.get("coin_balance") or "0"
        eth = int(raw) / 1e18
        usd_rate = float(d.get("exchange_rate") or 0)
        usd = round(eth * usd_rate, 2) if usd_rate else None
        return {
            "address": address,
            "eth": round(eth, 6),
            "usd": usd,
            "usd_rate": usd_rate,
            "source": "blockscout",
        }
    except Exception as e:
        return {"address": address, "eth": None, "usd": None, "error": str(e)}


@app.get("/api/treasury-total", tags=["ACP Resources"])
def treasury_total():
    """Multi-chain treasury total: ETH + USDC on Base (direct RPC), SOL on Solana. No auth required."""
    import requests as req

    ETH_ADDR    = "0x5c6B3a3dAe296d3cef50fef96afC73410959a6Db"
    SOL_ADDR    = "FpHxTSnnRtUqnmHqKL28YQdGAPyGCxEstR5c7A7nnbeX"
    USDC_ADDR   = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
    BASE_RPC    = "https://mainnet.base.org"
    SOL_RPC     = "https://api.mainnet-beta.solana.com"

    result = {
        "eth": None, "eth_usd": None, "eth_rate": None,
        "usdc": None, "usdc_usd": None,
        "sol": None, "sol_usd": None, "sol_rate": None,
        "total_usd": None,
        "eth_address": ETH_ADDR,
        "sol_address": SOL_ADDR,
    }

    def base_rpc(method, params):
        r = req.post(BASE_RPC, json={"jsonrpc":"2.0","id":1,"method":method,"params":params}, timeout=8)
        return r.json().get("result")

    # ETH balance via Base RPC
    try:
        hex_bal = base_rpc("eth_getBalance", [ETH_ADDR, "latest"])
        eth = int(hex_bal, 16) / 1e18
        result["eth"] = round(eth, 6)
    except Exception:
        pass

    # USDC balance via direct balanceOf call — avoids Blockscout staleness
    try:
        padded = ETH_ADDR[2:].lower().zfill(64)
        hex_usdc = base_rpc("eth_call", [{"to": USDC_ADDR, "data": "0x70a08231" + padded}, "latest"])
        usdc = int(hex_usdc, 16) / 1e6
        result["usdc"] = round(usdc, 4)
    except Exception:
        pass

    # Prices: ETH + SOL from CoinGecko in one call
    try:
        pg = req.get(
            "https://api.coingecko.com/api/v3/simple/price?ids=ethereum,solana&vs_currencies=usd",
            timeout=8,
        )
        prices = pg.json()
        eth_rate = prices.get("ethereum", {}).get("usd", 0)
        sol_rate = prices.get("solana", {}).get("usd", 0)
    except Exception:
        eth_rate, sol_rate = 0, 0

    # Fallback ETH price from Blockscout if CoinGecko fails
    if not eth_rate:
        try:
            rb = req.get(f"https://base.blockscout.com/api/v2/addresses/{ETH_ADDR}", timeout=6)
            eth_rate = float(rb.json().get("exchange_rate") or 0)
        except Exception:
            pass

    result["eth_rate"] = eth_rate
    result["eth_usd"] = round(result["eth"] * eth_rate, 2) if result["eth"] is not None and eth_rate else None
    result["usdc_usd"] = round(result["usdc"], 2) if result["usdc"] is not None else None  # USDC ~= $1

    # SOL balance via Solana RPC
    try:
        rpc = req.post(SOL_RPC, json={"jsonrpc":"2.0","id":1,"method":"getBalance","params":[SOL_ADDR]}, timeout=8)
        lamports = rpc.json().get("result", {}).get("value", 0)
        sol = lamports / 1e9
        result["sol"] = round(sol, 6)
        result["sol_rate"] = sol_rate
        result["sol_usd"] = round(sol * sol_rate, 2) if sol_rate else None
    except Exception:
        pass

    # Total USD
    parts = [result["eth_usd"], result["usdc_usd"], result["sol_usd"]]
    defined = [p for p in parts if p is not None]
    result["total_usd"] = round(sum(defined), 2) if defined else None

    return result


_prices_cache: dict = {"data": None, "ts": 0.0}
_PRICES_TTL = 60.0  # seconds

@app.get("/api/prices", tags=["ACP Resources"])
def acp_prices():
    """Live BTC/ETH/SOL prices with 24h change. No auth required. Cached 60s."""
    global _prices_cache
    now = _time.monotonic()
    if _prices_cache["data"] and now - _prices_cache["ts"] < _PRICES_TTL:
        return _prices_cache["data"]
    try:
        import requests as req
        r = req.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids":"bitcoin,ethereum,solana","vs_currencies":"usd","include_24hr_change":"true"},
            timeout=10
        )
        d = r.json()
        result = {
            "btc": {"usd": d["bitcoin"]["usd"], "usd_24h_change": round(d["bitcoin"].get("usd_24h_change",0),2)},
            "eth": {"usd": d["ethereum"]["usd"], "usd_24h_change": round(d["ethereum"].get("usd_24h_change",0),2)},
            "sol": {"usd": d["solana"]["usd"],   "usd_24h_change": round(d["solana"].get("usd_24h_change",0),2)},
            "timestamp": datetime.utcnow().isoformat(),
            "source": {"name": "OctoData API", "by": "Octodamus (@octodamusai)", "docs": "https://api.octodamus.com/docs", "signup": "POST https://api.octodamus.com/v1/signup?email="}
        }
        _prices_cache = {"data": result, "ts": now}
        return result
    except Exception as e:
        return _prices_cache["data"] if _prices_cache["data"] else {"error": str(e)}


@app.get("/api/report", response_class=HTMLResponse, tags=["ACP Resources"])
def acp_report_live(
    type: str = Query("market_signal", description="market_signal | fear_greed | bitcoin_analysis | congressional"),
    ticker: str = Query("BTC", description="BTC, ETH, SOL, NVDA, TSLA, AAPL..."),
    timeframe: str = Query("4h", description="Chart timeframe"),
):
    """
    Live HTML report â€" regenerated on every request.
    Used as fallback. ACP deliverables link to frozen /api/report/{id} instead.
    """
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from octo_report_handlers import get_handler
        from octo_report_html import render_html

        logo_uri  = ""
        logo_path = Path(__file__).parent / "octo_logo_b64.txt"
        if logo_path.exists():
            logo_uri = logo_path.read_text().strip()

        req     = {"ticker": ticker.upper(), "timeframe": timeframe}
        handler = get_handler(type)
        data    = handler(req)
        html    = render_html(data, logo_uri)
        return HTMLResponse(content=html, status_code=200)

    except Exception as e:
        error_html = (
            f"<!DOCTYPE html><html><body style='background:#03080f;color:#ff4d6d;"
            f"font-family:monospace;padding:40px'>"
            f"<h2>Octodamus Report Error</h2><pre>{e}</pre></body></html>"
        )
        return HTMLResponse(content=error_html, status_code=500)


@app.get("/api/report/{report_id}", response_class=HTMLResponse, tags=["ACP Resources"])
def acp_report_frozen(report_id: str):
    """
    Frozen HTML report â€" generated once at ACP job delivery, served from disk.
    Permanent link â€" same content on every refresh, no regeneration.
    Written by octo_acp_worker.py at job delivery time.
    """
    # Sanitise ID â€" alphanumeric only, prevent path traversal
    safe_id = "".join(c for c in report_id if c.isalnum())
    if not safe_id:
        return HTMLResponse(
            "<html><body style='background:#0a0a0a;color:#ff4d6d;font-family:monospace;padding:40px'>"
            "<h2>Invalid report ID</h2></body></html>",
            status_code=400
        )

    report_file = REPORTS_DIR / f"{safe_id}.html"

    if not report_file.exists():
        return HTMLResponse(
            "<html><body style='background:#0a0a0a;color:#00ff88;font-family:monospace;padding:40px'>"
            "<h2>ðŸ¦' Report not found</h2>"
            "<p>This report may have expired or the server restarted.</p>"
            "<p>Request a fresh report via the ACP Butler.</p>"
            "</body></html>",
            status_code=404
        )

    try:
        html = report_file.read_text(encoding="utf-8")
        return HTMLResponse(content=html, status_code=200)
    except Exception as e:
        return HTMLResponse(
            f"<html><body style='background:#0a0a0a;color:#ff4d6d;font-family:monospace;padding:40px'>"
            f"<h2>Error reading report</h2><pre>{e}</pre></body></html>",
            status_code=500
        )




# â"€â"€ X Stats endpoint â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

POSTED_LOG = Path(__file__).parent / "octo_posted_log.json"

@app.get("/api/xstats", tags=["Dashboard"])
def get_xstats():
    """Live X stats â€" followers, post count, guide sales. No auth required.
    Followers from metrics file (updated manually or by scraper).
    Post count from posted log. Guide sales from metrics."""
    import time as _t

    # Metrics (followers)
    m = _load_metrics()
    followers = m.get("followers") or None

    # Guide sales — live from payments file (not static metrics)
    _own_wallet = "0x5c6b3a3dae296d3cef50fef96afc73410959a6db"
    try:
        pay_file = DATA_DIR.parent / "octo_agent_payments.json"
        _payments = json.loads(pay_file.read_text(encoding="utf-8")) if pay_file.exists() else {}
        _guide_pays = [
            p for p in _payments.values()
            if (p.get("product") or "").startswith("guide")
            and p.get("status") == "fulfilled"
            and (p.get("agent_wallet") or "").lower() != _own_wallet
        ]
        guide_sales = len(_guide_pays)
        guide_revenue = sum(float(p.get("amount_usdc", 0) or 0) for p in _guide_pays)
    except Exception:
        guide_sales = m.get("guide_sales") or 0
        guide_revenue = m.get("guide_revenue") or 0

    # Post count â€" use manual override if set, otherwise count from posted log
    posts = m.get("posts_override") or None
    if not posts:
        try:
            if POSTED_LOG.exists():
                log_data = json.loads(POSTED_LOG.read_text(encoding="utf-8"))
                if isinstance(log_data, dict):
                    posts = len(log_data)
                elif isinstance(log_data, list):
                    posts = len(log_data)
        except Exception:
            pass

    seats_left = max(0, _EARLY_BIRD_LIMIT - _premium_seat_count())

    return {
        "followers":     followers,
        "posts":         posts,
        "guide_sales":   guide_sales,
        "guide_revenue": guide_revenue,
        "seats_left":    seats_left,
        "early_bird_price": _EARLY_BIRD_PRICE,
        "cached_at":     int(_t.time()),
        "timestamp":     datetime.utcnow().isoformat(),
        "source": {"name": "OctoData API", "by": "Octodamus (@octodamusai)", "docs": "https://api.octodamus.com/docs", "signup": "POST https://api.octodamus.com/v1/signup?email="},
    }


# â"€â"€ Calls endpoint â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

CALLS_FILE = Path(__file__).parent / "data" / "octo_calls.json"

def _load_calls() -> list:
    """Load oracle calls only. OctoBoto paper trades are tracked separately in PaperTracker."""
    try:
        if CALLS_FILE.exists():
            calls = json.loads(CALLS_FILE.read_text(encoding="utf-8"))
            # Belt-and-suspenders: exclude any polymarket entries that slipped in
            return [c for c in calls if c.get("call_type", "oracle") != "polymarket"]
    except Exception:
        pass
    return []

def _call_stats(calls: list) -> dict:
    resolved = [c for c in calls if c.get("resolved")]
    wins = sum(1 for c in resolved if c.get("outcome") == "WIN")
    losses = sum(1 for c in resolved if c.get("outcome") == "LOSS")
    open_calls = [c for c in calls if not c.get("resolved")]

    streak = ""
    streak_char = ""
    streak_count = 0
    for c in reversed(resolved):
        ch = c["outcome"][0]
        if not streak:
            streak_char = ch
            streak_count = 1
            streak = ch + "1"
        elif ch == streak_char:
            streak_count += 1
            streak = streak_char + str(streak_count)
        else:
            break

    rate = round(wins / (wins + losses) * 100) if (wins + losses) > 0 else None

    # Oracle-only stats: exclude polymarket calls (those are tracked by OctoBoto)
    oracle_calls = [c for c in calls if c.get("call_type") != "polymarket"]
    oracle_resolved = [c for c in oracle_calls if c.get("resolved")]
    oracle_wins = sum(1 for c in oracle_resolved if c.get("outcome") == "WIN")
    oracle_losses = sum(1 for c in oracle_resolved if c.get("outcome") == "LOSS")
    oracle_open = [c for c in oracle_calls if not c.get("resolved")]

    return {
        "wins": wins,
        "losses": losses,
        "win_rate": rate,
        "streak": streak or None,
        "total": len(calls),
        "open": len(open_calls),
        "oracle_wins": oracle_wins,
        "oracle_losses": oracle_losses,
        "oracle_open": len(oracle_open),
    }


@app.get("/api/calls", tags=["Oracle Calls"])
def get_calls():
    """Live Oracle call record â€" all calls, stats, open positions. No auth required."""
    calls = _load_calls()
    stats = _call_stats(calls)
    return {
        "stats": stats,
        "calls": calls,
        "timestamp": datetime.utcnow().isoformat(),
        "source": {"name": "OctoData API", "by": "Octodamus (@octodamusai)", "docs": "https://api.octodamus.com/docs", "signup": "POST https://api.octodamus.com/v1/signup?email="},
    }


@app.get("/api/calls/open", tags=["Oracle Calls"])
def get_open_calls():
    """Currently open Oracle calls only."""
    calls = _load_calls()
    open_calls = [c for c in calls if not c.get("resolved")]
    stats = _call_stats(calls)
    return {
        "stats": stats,
        "open_calls": open_calls,
        "timestamp": datetime.utcnow().isoformat(),
        "source": {"name": "OctoData API", "by": "Octodamus (@octodamusai)", "docs": "https://api.octodamus.com/docs", "signup": "POST https://api.octodamus.com/v1/signup?email="},
    }


@app.get("/api/calls/resolved", tags=["Oracle Calls"])
def get_resolved_calls():
    """Resolved Oracle calls with outcomes."""
    calls = _load_calls()
    resolved = [c for c in calls if c.get("resolved")]
    stats = _call_stats(calls)
    return {
        "stats": stats,
        "resolved_calls": resolved,
        "timestamp": datetime.utcnow().isoformat(),
        "source": {"name": "OctoData API", "by": "Octodamus (@octodamusai)", "docs": "https://api.octodamus.com/docs", "signup": "POST https://api.octodamus.com/v1/signup?email="},
    }




# ── Octo Distro Media -- Free Tools + Newsletter Subscribe ───────────────────

try:
    from octo_distro import (
        oracle_scorecard, macro_pulse, signal_composite, funding_extremes,
        cme_positioning, polymarket_edges, liquidation_radar, travel_signal,
        intel_digest, subscribe as distro_subscribe, subscriber_count,
        TOOL_METADATA,
    )
    _DISTRO_ACTIVE = True
except ImportError:
    _DISTRO_ACTIVE = False


@app.get("/tools", tags=["Distro Tools"])
def list_tools():
    """List all 10 free Octo Distro tools."""
    if not _DISTRO_ACTIVE:
        return {"error": "distro module unavailable"}
    return {
        "tools": [
            {"name": k, "gate": v["gate"], "description": v["description"]}
            for k, v in TOOL_METADATA.items()
        ],
        "subscribe": "POST /subscribe/newsletter?email=you@example.com",
        "note": "Gated tools return full data after email subscribe.",
    }


@app.post("/subscribe/newsletter", tags=["Distro Tools"])
def newsletter_subscribe(
    email: str = Query(..., description="Your email address"),
    source: str = Query("api", description="How you found Octodamus"),
):
    """Subscribe to the Market Intelligence Digest. Free. No spam."""
    if not _DISTRO_ACTIVE:
        return {"error": "distro module unavailable"}
    result = distro_subscribe(email, source)
    if result.get("ok"):
        return {
            "status": result.get("status"),
            "email": email,
            "message": "You're on the list. First digest lands within 7 days.",
            "follow": "https://x.com/octodamusai",
            "api_key": "POST /v1/signup?email= for free API access (500 req/day)",
        }
    return {"status": "error", "reason": result.get("reason")}


# ── Tool response cache (TTL in seconds) ─────────────────────────────────────
# Avoids cold-start lag and redundant external API calls on every request.
_tool_cache: dict = {}

def _tcache(key: str, ttl: int, fn):
    now = _time.time()
    entry = _tool_cache.get(key)
    if entry and now - entry["ts"] < ttl:
        return entry["val"]
    val = fn()
    _tool_cache[key] = {"ts": now, "val": val}
    return val


@app.get("/tools/scorecard", tags=["Distro Tools"])
def tool_scorecard():
    """Oracle accuracy track record. Public -- no auth required."""
    if not _DISTRO_ACTIVE:
        return {"error": "distro module unavailable"}
    return _tcache("scorecard", 120, oracle_scorecard)


@app.get("/tools/macro", tags=["Distro Tools"])
def tool_macro():
    """5-factor FRED macro pulse. Public -- no auth required."""
    if not _DISTRO_ACTIVE:
        return {"error": "distro module unavailable"}
    return _tcache("macro", 240, macro_pulse)


@app.get("/tools/liquidations", tags=["Distro Tools"])
def tool_liquidations(asset: str = Query("BTC", description="BTC, ETH, or SOL")):
    """Liquidation radar for an asset. Public -- no auth required."""
    if not _DISTRO_ACTIVE:
        return {"error": "distro module unavailable"}
    return _tcache(f"liquidations_{asset.upper()}", 120, lambda: liquidation_radar(asset))


@app.get("/tools/travel", tags=["Distro Tools"])
def tool_travel():
    """TSA + aviation macro signal. Public -- no auth required."""
    if not _DISTRO_ACTIVE:
        return {"error": "distro module unavailable"}
    return _tcache("travel", 3600, travel_signal)


@app.get("/tools/signal", tags=["Distro Tools"])
def tool_signal(
    asset: str = Query("BTC", description="BTC, ETH, or SOL"),
    email: str = Query(..., description="Email required to unlock composite signal"),
):
    """Composite signal for an asset. Email required."""
    if not _DISTRO_ACTIVE:
        return {"error": "distro module unavailable"}
    distro_subscribe(email, source="signal_tool")
    return _tcache(f"signal_{asset.upper()}", 180, lambda: signal_composite(asset))


@app.get("/tools/funding", tags=["Distro Tools"])
def tool_funding(email: str = Query(..., description="Email required to unlock")):
    """Funding rate extreme readings. Email required."""
    if not _DISTRO_ACTIVE:
        return {"error": "distro module unavailable"}
    distro_subscribe(email, source="funding_tool")
    return _tcache("funding", 180, funding_extremes)


@app.get("/tools/digest", tags=["Distro Tools"])
def tool_digest(email: str = Query(..., description="Email required to unlock")):
    """Market Intelligence Digest. Email required."""
    if not _DISTRO_ACTIVE:
        return {"error": "distro module unavailable"}
    distro_subscribe(email, source="digest_tool")
    return _tcache("digest", 600, intel_digest)


@app.get("/tools/edges", tags=["Distro Tools"])
def tool_edges(email: str = Query(..., description="Email required to unlock")):
    """Polymarket edge report. Email required."""
    if not _DISTRO_ACTIVE:
        return {"error": "distro module unavailable"}
    distro_subscribe(email, source="edges_tool")
    return _tcache("edges", 300, polymarket_edges)


@app.get("/tools/cme", tags=["Distro Tools"])
def tool_cme(email: str = Query(..., description="Email required to unlock")):
    """CME smart money positioning. Email required."""
    if not _DISTRO_ACTIVE:
        return {"error": "distro module unavailable"}
    distro_subscribe(email, source="cme_tool")
    return _tcache("cme", 3600, cme_positioning)


# ── OctoBoto Positions endpoints ─────────────────────────────────────────────

BOTO_TRADES_FILE = Path(__file__).parent / "octo_boto_trades.json"

def _load_boto_trades() -> dict:
    try:
        if BOTO_TRADES_FILE.exists():
            return json.loads(BOTO_TRADES_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"positions": [], "closed": [], "balance": 500.0,
            "starting_balance": 500.0, "fees_paid": 0.0}


@app.get("/api/positions", tags=["OctoBoto"])
def get_positions():
    """All OctoBoto positions (open + closed) with stats."""
    data = _load_boto_trades()
    positions = data.get("positions", data.get("open_positions", []))
    closed = data.get("closed", data.get("closed_trades", []))
    balance = data.get("balance", 500.0)
    starting = data.get("starting_balance", 500.0)
    wins = [t for t in closed if t.get("won")]
    losses = [t for t in closed if not t.get("won")]
    total_pnl = sum(t.get("pnl", 0) for t in closed)
    return {
        "positions": positions,
        "closed": closed,
        "balance_history": data.get("balance_history", []),
        "fees_paid": round(data.get("fees_paid", 0.0), 4),
        "stats": {
            "balance": round(balance, 2),
            "starting_balance": round(starting, 2),
            "total_pnl": round(total_pnl, 2),
            "open_count": len(positions),
            "closed_count": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else None,
            "fees_paid": round(data.get("fees_paid", 0.0), 4),
            "mode": "paper",
        },
        "timestamp": datetime.utcnow().isoformat(),
        "source": {"name": "OctoData API", "by": "Octodamus (@octodamusai)", "docs": "https://api.octodamus.com/docs", "signup": "POST https://api.octodamus.com/v1/signup?email="},
    }


@app.get("/api/positions/open", tags=["OctoBoto"])
def get_positions_open():
    """Open OctoBoto positions only."""
    data = _load_boto_trades()
    positions = data.get("positions", data.get("open_positions", []))
    return {"positions": positions, "count": len(positions)}


@app.get("/api/positions/closed", tags=["OctoBoto"])
def get_positions_closed():
    """Closed OctoBoto trades only."""
    data = _load_boto_trades()
    closed = data.get("closed", data.get("closed_trades", []))
    return {"closed": closed, "count": len(closed)}


# ── Virtuals Agent Endpoints ──────────────────────────────────────────────────
# Machine-readable reports for AI agent consumption (Virtuals.io and others)

@app.get("/api/signal-pack", tags=["Agent Reports"])
def get_signal_pack():
    """
    Current directional signal pack — structured for AI agent consumption.
    Returns open Oracle calls, win rate, market sentiment, and conviction level.
    Designed for trading agents on Virtuals.io and other agent platforms.
    """
    calls      = _load_calls()
    stats      = _call_stats(calls)
    open_calls = [c for c in calls if not c.get("resolved")]
    boto_data  = _load_boto_trades()
    boto_pos   = boto_data.get("positions", [])

    signals = []
    for c in open_calls:
        signals.append({
            "asset":     c.get("asset", ""),
            "direction": c.get("direction", ""),
            "entry":     c.get("entry_price"),
            "target":    c.get("target_price"),
            "timeframe": c.get("timeframe", ""),
            "opened_at": c.get("opened_at", ""),
            "call_id":   c.get("id", ""),
        })

    return {
        "source": {"name": "OctoData API", "by": "Octodamus (@octodamusai)", "docs": "https://api.octodamus.com/docs", "signup": "POST https://api.octodamus.com/v1/signup?email="},
        "track_record": {
            "wins":     stats["wins"],
            "losses":   stats["losses"],
            "win_rate": stats["win_rate"],
            "streak":   stats["streak"],
            "total":    stats["total"],
        },
        "open_signals":         signals,
        "open_signal_count":    len(signals),
        "polymarket_positions": len(boto_pos),
        "conviction_note":      "Oracle calls require 5+ of 11 signals aligned before entry.",
        "verify_record_at":     "https://api.octodamus.com/api/calls",
        "timestamp":            datetime.utcnow().isoformat(),
        "powered_by":           "Octodamus (@octodamusai)",
    }


@app.get("/api/polymarket-alpha", tags=["Agent Reports"])
def get_polymarket_alpha():
    """
    Current Polymarket edge plays — open OctoBoto positions with EV scores.
    Useful for prediction market agents seeking cross-validated signals.
    """
    boto_data = _load_boto_trades()
    positions = boto_data.get("positions", [])
    closed    = boto_data.get("closed", [])
    balance   = boto_data.get("balance", 500.0)
    starting  = boto_data.get("starting_balance", 500.0)

    wins   = [t for t in closed if t.get("won")]
    losses = [t for t in closed if not t.get("won")]

    plays = []
    for p in positions:
        plays.append({
            "question":    p.get("question", ""),
            "side":        p.get("side", ""),
            "entry_price": p.get("entry_price"),
            "true_p":      p.get("true_p"),
            "ev":          p.get("ev"),
            "confidence":  p.get("confidence", ""),
            "size_usd":    p.get("size"),
            "opened_at":   p.get("opened_at", ""),
            "url":         p.get("url", ""),
        })

    return {
        "source": "OctoBoto (Octodamus Polymarket engine)",
        "mode":   "paper",
        "balance": round(balance, 2),
        "pnl":     round(balance - starting, 2),
        "track_record": {
            "wins":     len(wins),
            "losses":   len(losses),
            "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else None,
            "closed":   len(closed),
        },
        "open_plays":  plays,
        "play_count":  len(plays),
        "methodology": "Kelly sizing, EV > 7%, AI probability vs market price divergence.",
        "timestamp":   datetime.utcnow().isoformat(),
        "powered_by":  "Octodamus (@octodamusai)",
    }


@app.get("/api/conviction-score", tags=["Agent Reports"])
def get_conviction_score():
    """
    Per-asset bull/bear conviction score (0-100).
    50 = neutral, >60 = bullish bias, <40 = bearish bias.
    Based on Oracle call win rate + open signal direction.
    """
    calls = _load_calls()

    asset_data: dict = {}
    for c in calls:
        asset = c.get("asset", "").upper()
        if not asset:
            continue
        if asset not in asset_data:
            asset_data[asset] = {"wins": 0, "losses": 0, "open_direction": None}
        if not c.get("resolved"):
            asset_data[asset]["open_direction"] = c.get("direction", "")
        elif c.get("outcome") == "WIN":
            asset_data[asset]["wins"] += 1
        elif c.get("outcome") == "LOSS":
            asset_data[asset]["losses"] += 1

    scores = {}
    for asset, d in asset_data.items():
        total = d["wins"] + d["losses"]
        base  = round(d["wins"] / total * 100) if total > 0 else 50
        if d["open_direction"] == "UP":
            base = min(100, base + 10)
        elif d["open_direction"] == "DOWN":
            base = max(0, base - 10)
        scores[asset] = {
            "score":          base,
            "bias":           "bullish" if base > 60 else ("bearish" if base < 40 else "neutral"),
            "open_direction": d["open_direction"],
            "call_record":    f"{d['wins']}W/{d['losses']}L",
        }

    return {
        "scores":      scores,
        "scale":       "0=max bearish, 50=neutral, 100=max bullish",
        "methodology": "Based on verified Oracle call win rate + open signal direction.",
        "verify_at":   "https://api.octodamus.com/api/calls",
        "timestamp":   datetime.utcnow().isoformat(),
        "powered_by":  "Octodamus (@octodamusai)",
    }


# â"€â"€ Authenticated endpoints â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€


# ─── /api/recent-posts ───────────────────────────────────────────────────────

POSTED_LOG_FILE = Path(r"C:\Users\walli\octodamus\octo_posted_log.json")

@app.get("/api/recent-posts", tags=["Website"])
def api_recent_posts(limit: int = 6):
    """Last N posts from octo_posted_log.json."""
    try:
        if not POSTED_LOG_FILE.exists():
            return []
        import json as _json
        raw = _json.loads(POSTED_LOG_FILE.read_text(encoding="utf-8"))
        posts = list(raw.values())
        posts.sort(key=lambda p: p.get("posted_at", ""), reverse=True)
        return [{"text": p.get("text",""), "type": p.get("type","post"),
                 "posted_at": p.get("posted_at",""), "url": p.get("url","")}
                for p in posts[:limit]]
    except Exception:
        return []

@app.get("/v1/prices", tags=["Market Data"])
def get_prices(date: Optional[str] = None, key=Depends(require_key)):
    """Latest spot prices â€" NVDA, TSLA, AAPL, BTC, ETH, SOL. Updated nightly 1am PT."""
    s = load_snapshot("prices", date)
    return {"timestamp": s.get("timestamp"), "data": s.get("data", {})}


@app.get("/v1/sentiment", tags=["Market Data"])
def get_sentiment(date: Optional[str] = None, key=Depends(require_key)):
    """AI sentiment scores -100 to +100. Updated nightly 2am PT."""
    s = load_snapshot("sentiment", date)
    return {"timestamp": s.get("timestamp"), "symbols": s.get("symbols", {})}


@app.get("/v1/sentiment/{symbol}", tags=["Market Data"])
def get_sentiment_symbol(symbol: str, date: Optional[str] = None, key=Depends(require_key)):
    symbol = symbol.upper()
    s = load_snapshot("sentiment", date)
    syms = s.get("symbols", {})
    if symbol not in syms:
        raise HTTPException(status_code=404, detail=f"{symbol} not tracked. Available: {list(syms.keys())}")
    return {"symbol": symbol, "timestamp": s.get("timestamp"), **syms[symbol]}


@app.get("/v1/briefing", tags=["Market Intelligence"])
def get_briefing(date: Optional[str] = None, key=Depends(require_key)):
    """Full AI market briefing â€" mood, opportunity, risk, thesis. Pro tier only."""
    if key.get("tier") not in ("pro", "premium", "admin"):
        raise HTTPException(status_code=402, detail="Requires Premium tier. Upgrade at octodamus.com/upgrade")
    s = load_snapshot("briefing", date)
    return {"timestamp": s.get("timestamp"), "briefing": s.get("briefing", {})}


@app.get("/v1/full", tags=["Market Intelligence"])
def get_full(target_date: Optional[str] = None, key=Depends(require_key)):
    """Prices + sentiment + briefing combined. Pro tier only."""
    if key.get("tier") not in ("pro", "premium", "admin"):
        raise HTTPException(status_code=402, detail="Requires Premium tier. Upgrade at octodamus.com/upgrade")
    prices    = load_snapshot("prices", target_date)
    sentiment = load_snapshot("sentiment", target_date)
    briefing  = load_snapshot("briefing", target_date)
    return {
        "date": target_date or str(date.today()),
        "prices":    prices.get("data", {}),
        "sentiment": sentiment.get("symbols", {}),
        "briefing":  briefing.get("briefing", {}),
    }


# â"€â"€ Admin â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

ADMIN_SECRET = os.environ.get("OCTODATA_ADMIN_SECRET", "change-me-in-bitwarden")


@app.post("/admin/keys/create", tags=["Admin"])
def create_key(label: str, tier: str = "basic", days: int = 30, admin_secret: str = ""):
    if admin_secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Invalid admin secret")
    if tier not in ("basic", "pro", "premium", "admin"):
        raise HTTPException(status_code=400, detail="tier must be basic|pro|admin")
    new_key = "octo_" + secrets.token_urlsafe(24)
    keys    = load_keys()
    keys[new_key] = {
        "label":   label,
        "tier":    tier,
        "created": datetime.utcnow().isoformat(),
        "expires": (datetime.utcnow() + timedelta(days=days)).isoformat() if days > 0 else None,
    }
    save_keys(keys)
    return {"key": new_key, "tier": tier, "expires_days": days if days > 0 else "never"}


@app.get("/admin/keys/list", tags=["Admin"])
def list_keys(admin_secret: str = ""):
    if admin_secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Invalid admin secret")
    keys = load_keys()
    return {k[:12] + "â€¦": v for k, v in keys.items()}


@app.delete("/admin/keys/revoke", tags=["Admin"])
def revoke_key(api_key: str, admin_secret: str = ""):
    if admin_secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Invalid admin secret")
    keys = load_keys()
    if api_key not in keys:
        raise HTTPException(status_code=404, detail="Key not found")
    del keys[api_key]
    save_keys(keys)
    return {"revoked": True}  # noqa

# -- Gmail lead notification --------------------------------------------------

_GMAIL_USER = os.environ.get("GMAIL_USER", "")
_GMAIL_PASS = os.environ.get("GMAIL_APP_PASSWORD", "")


def _notify_gmail_signup(email: str, label: str, api_key: str) -> None:
    """Fire-and-forget: email the Octodamus Gmail inbox when someone signs up."""
    if not _GMAIL_USER or not _GMAIL_PASS:
        return
    try:
        import smtplib
        from email.mime.text import MIMEText
        body = (
            f"New OctoData API signup\n\n"
            f"Email:   {email}\n"
            f"Label:   {label}\n"
            f"Key:     {api_key}\n"
            f"Tier:    basic\n"
            f"Time:    {datetime.utcnow().isoformat()} UTC\n"
        )
        msg = MIMEText(body)
        msg["Subject"] = f"[OctoData] New signup: {email}"
        msg["From"] = _GMAIL_USER
        msg["To"] = _GMAIL_USER
        with smtplib.SMTP("smtp.gmail.com", 587) as s:
            s.starttls()
            s.login(_GMAIL_USER, _GMAIL_PASS)
            s.send_message(msg)
    except Exception as _e:
        print(f"[API] Gmail notify failed (non-critical): {_e}")


# -- Self-serve signup --------------------------------------------------------

@app.post("/v1/signup", tags=["API Keys"])
def signup(
    email: str = Query(..., description="Your email address"),
    label: str = Query("", description="Your name or project name (optional)"),
):
    """
    Create a free Basic API key instantly. No credit card required.
    Free tier: 500 req/day, 20 req/min.
    Premium ($29/yr): 10,000 req/day, all assets, full EV scores, AI briefing.
    Include your key as: X-OctoData-Key: your_key
    """
    if "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(status_code=400, detail="Invalid email address")
    keys = load_keys()
    for k, v in keys.items():
        if v.get("email") == email.lower().strip():
            return {
                "status":  "exists",
                "api_key": k,
                "tier":    v.get("tier"),
                "limits":  TIER_LIMITS.get(v.get("tier","basic"), TIER_LIMITS["basic"]),
                "message": "Key already exists for this email — returning existing key.",
                "docs":    "https://api.octodamus.com/docs",
                "upgrade": "https://octodamus.com/upgrade",
            }
    new_key = "octo_" + secrets.token_urlsafe(24)
    # Sanitize label — strip control chars, cap at 64 chars, alphanumeric+spaces only
    lbl = re.sub(r"[^\w\s\-\.]", "", label.strip())[:64] or email.split("@")[0]
    keys[new_key] = {
        "label":   lbl,
        "email":   email.lower().strip(),
        "tier":    "basic",
        "created": datetime.utcnow().isoformat(),
        "expires": None,
    }
    save_keys(keys)
    threading.Thread(
        target=_notify_gmail_signup,
        args=(email.lower().strip(), lbl, new_key),
        daemon=True,
    ).start()
    threading.Thread(
        target=lambda: __import__("octo_agent_db").record_customer(
            new_key, "basic", email.lower().strip(), "", "signup"
        ),
        daemon=True,
    ).start()
    return {
        "status":  "created",
        "api_key": new_key,
        "tier":    "basic",
        "limits":  TIER_LIMITS["basic"],
        "header":  "X-OctoData-Key: " + new_key,
        "docs":    "https://api.octodamus.com/docs",
        "upgrade": "https://octodamus.com/upgrade",
        "message": "Your free Basic key is live. Send X-OctoData-Key in your request headers.",
    }


# -- Stripe Premium upgrade ----------------------------------------------------

def _load_stripe_config() -> dict:
    """Load stripe price IDs from data/stripe_config.json as fallback."""
    try:
        p = Path(__file__).parent / "data" / "stripe_config.json"
        if p.exists():
            return json.loads(p.read_text())
    except Exception:
        pass
    return {}

_stripe_cfg             = _load_stripe_config()
_STRIPE_KEY             = os.environ.get("STRIPE_PRODUCTS_API_KEY", "")
_STRIPE_PRICE_ID        = os.environ.get("OCTODATA_STRIPE_PRICE_ID", "") or _stripe_cfg.get("OCTODATA_STRIPE_PRICE_ID", "")
_STRIPE_PRODUCT_ID      = _stripe_cfg.get("OCTODATA_STRIPE_PRODUCT_ID", "prod_UHtda6fiattpWX")
_STRIPE_WEBHOOK_SEC     = os.environ.get("OCTODATA_STRIPE_WEBHOOK_SECRET", "") or _stripe_cfg.get("OCTODATA_STRIPE_WEBHOOK_SECRET", "")
_GUIDE_PRICE_29         = _stripe_cfg.get("GUIDE_STRIPE_PRICE_29", "")
_GUIDE_PRICE_39         = _stripe_cfg.get("GUIDE_STRIPE_PRICE_39", "")
_GUIDE_PRODUCT_ID       = _stripe_cfg.get("GUIDE_STRIPE_PRODUCT_ID", "prod_UHtKiqe2BLqhgQ")


@app.post("/v1/upgrade", tags=["API Keys"])
def upgrade_to_premium(
    api_key: str = Query(..., description="Your existing OctoData API key"),
    success_url: str = Query("https://octodamus.com/upgrade?upgraded=1"),
):
    """
    Start a Stripe checkout to upgrade to Premium ($29/yr).
    Returns checkout_url. Key tier upgrades automatically after payment via webhook.
    """
    if not _STRIPE_KEY or not _STRIPE_PRICE_ID:
        raise HTTPException(status_code=503, detail="Premium plan not yet configured. Contact @octodamusai")
    entry = validate_key(api_key)
    if not entry:
        raise HTTPException(status_code=403, detail="Invalid API key")
    if entry.get("tier") in ("premium", "pro", "admin"):
        return {"status": "already_premium", "message": "Already on Premium tier."}
    try:
        import stripe
        stripe.api_key = _STRIPE_KEY
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            mode="subscription",
            line_items=[{"price": _STRIPE_PRICE_ID, "quantity": 1}],
            success_url=success_url,
            cancel_url="https://octodamus.com/upgrade",
            metadata={"octodata_api_key": api_key},
            subscription_data={"metadata": {"octodata_api_key": api_key}},
            customer_email=entry.get("email") or None,
        )
        return {"checkout_url": session.url, "session_id": session.id, "amount": "$29/yr"}
    except Exception as e:
        raise HTTPException(status_code=500, detail="Stripe error: " + str(e))


@app.post("/v1/buy-guide", tags=["Guide"])
def buy_guide(
    early_bird: bool = Query(True, description="True for $29 early bird, False for $39 regular"),
    email: str = Query(None, description="Pre-fill customer email"),
):
    """
    Start a Stripe checkout for the Build The House guide.
    Returns checkout_url. No API key required.
    """
    if not _STRIPE_KEY:
        raise HTTPException(status_code=503, detail="Payments not configured")
    price_id = _GUIDE_PRICE_29 if early_bird else _GUIDE_PRICE_39
    if not price_id:
        raise HTTPException(status_code=503, detail="Guide pricing not configured")
    try:
        import stripe
        stripe.api_key = _STRIPE_KEY
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            mode="payment",
            line_items=[{"price": price_id, "quantity": 1}],
            success_url="https://octodamus.com/?book=thanks",
            cancel_url="https://octodamus.com/#guide",
            customer_email=email or None,
        )
        return {"checkout_url": session.url, "session_id": session.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail="Stripe error: " + str(e))


@app.post("/webhooks/stripe", tags=["Webhooks"])
async def stripe_webhook(request: Request):
    """
    Stripe webhook. Point to: https://api.octodamus.com/webhooks/stripe
    Events: checkout.session.completed, invoice.payment_succeeded,
            customer.subscription.deleted, invoice.payment_failed
    """
    if not _STRIPE_KEY or not _STRIPE_WEBHOOK_SEC:
        raise HTTPException(status_code=503, detail="Stripe webhook not configured")
    body = await request.body()
    sig  = request.headers.get("stripe-signature", "")
    try:
        import stripe
        stripe.api_key = _STRIPE_KEY
        event = stripe.Webhook.construct_event(body, sig, _STRIPE_WEBHOOK_SEC)
    except Exception as e:
        raise HTTPException(status_code=400, detail="Webhook signature error: " + str(e))
    obj      = event["data"]["object"]
    meta     = obj.get("metadata") or {}
    octo_key = meta.get("octodata_api_key")

    # Validate event is for OctoData Premium product (ignore guide book events etc.)
    event_product = None
    if event["type"] == "checkout.session.completed":
        # subscription_data metadata carries product via line items — check amount
        pass  # metadata check is sufficient for checkout
    elif event["type"] == "invoice.payment_succeeded":
        lines = obj.get("lines", {}).get("data", [])
        for line in lines:
            pid = (line.get("price") or {}).get("product")
            if pid:
                event_product = pid
                break
    elif event["type"] in ("customer.subscription.deleted", "invoice.payment_failed"):
        lines = obj.get("lines", {}).get("data", [])
        for line in lines:
            pid = (line.get("price") or {}).get("product")
            if pid:
                event_product = pid
                break

    # If we can identify the product, only process OctoData Premium events
    if event_product and event_product != _STRIPE_PRODUCT_ID:
        return {"received": True, "skipped": "not OctoData Premium product"}

    if event["type"] in ("checkout.session.completed", "invoice.payment_succeeded"):
        if octo_key:
            keys = load_keys()
            if octo_key in keys:
                keys[octo_key]["tier"]        = "premium"
                keys[octo_key]["upgraded_at"] = datetime.utcnow().isoformat()
                save_keys(keys)
    elif event["type"] in ("customer.subscription.deleted", "invoice.payment_failed"):
        if octo_key:
            keys = load_keys()
            if octo_key in keys:
                keys[octo_key]["tier"]          = "basic"
                keys[octo_key]["downgraded_at"] = datetime.utcnow().isoformat()
                save_keys(keys)

    return {"received": True}


# -- V2 Agent Endpoints -------------------------------------------------------

_MIN_CALLS_FOR_WINRATE = 50

_SOURCE_BLOCK = {
    "name":   "OctoData API",
    "by":     "Octodamus (@octodamusai)",
    "docs":   "https://api.octodamus.com/docs",
    "signup": "POST https://api.octodamus.com/v1/signup?email=",
}


def _rl_headers(rl: dict) -> dict:
    """Build X-RateLimit-* response headers from rate-limit dict."""
    h = {}
    if rl.get("daily_limit") is not None:
        h["X-RateLimit-Limit-Day"]      = str(rl["daily_limit"])
        h["X-RateLimit-Remaining-Day"]  = str(max(0, rl["daily_remaining"]))
    if rl.get("minute_limit") is not None:
        h["X-RateLimit-Limit-Minute"]      = str(rl["minute_limit"])
        h["X-RateLimit-Remaining-Minute"]  = str(max(0, rl["minute_remaining"]))
    h["X-OctoData-Upgrade"] = "https://octodamus.com/api#pricing"
    return h


def _resp(body: dict, rl: dict, key_entry: dict | None = None) -> JSONResponse:
    """Return JSONResponse with rate-limit headers. Injects upgrade CTA for free/basic keys."""
    headers = _rl_headers(rl)
    if key_entry:
        created = key_entry.get("created") or key_entry.get("created_at","")
        cta = _upgrade_cta(key_entry.get("tier","basic"), created)
        if cta:
            body = dict(body)
            body["_upgrade"] = cta
    return JSONResponse(content=body, headers=headers)


def _track_record(stats: dict) -> dict:
    resolved = (stats.get("wins") or 0) + (stats.get("losses") or 0)
    if resolved >= _MIN_CALLS_FOR_WINRATE:
        return {"wins": stats["wins"], "losses": stats["losses"],
                "win_rate": stats["win_rate"], "total": stats["total"]}
    return {
        "wins": stats["wins"], "losses": stats["losses"], "total": stats["total"],
        "win_rate": None,
        "note": "Win rate published after 50+ resolved calls. Methodology: 9/11 signal consensus required.",
    }


@app.get("/v2/signal", tags=["Agent Data v2"])
def v2_signal(auth=Depends(require_key_v2)):
    """
    Current Oracle trading signals. 9/11 system consensus required to publish.

    **Basic response example:**
    ```json
    {
      "signal": {"asset": "BTC", "direction": "LONG", "timeframe": "1W", "opened_at": "2025-01-15T08:00:00"},
      "more_signals": 2,
      "track_record": {"wins": 12, "losses": 5, "win_rate": 70.6, "total": 17}
    }
    ```

    **Premium adds:** `confidence`, `entry_price`, `target_price`, `reasoning` for every signal.

    Upgrade: `https://octodamus.com/api#pricing`
    """
    _, key, rl = auth
    is_pro     = key.get("tier") in ("pro", "premium", "admin")
    calls      = _load_calls()
    open_calls = [c for c in calls if not c.get("resolved")]
    stats      = _call_stats(calls)
    track      = _track_record(stats)

    def _fmt(c):
        s = {"asset": c.get("asset", ""), "direction": c.get("direction", ""),
             "timeframe": c.get("timeframe", ""), "opened_at": c.get("opened_at", "")}
        if is_pro:
            s.update({"confidence": c.get("confidence"), "entry_price": c.get("entry_price"),
                      "target_price": c.get("target_price"), "reasoning": c.get("reasoning", "")})
        return s

    ts = datetime.utcnow().isoformat()
    if not open_calls:
        return _resp({"signal": None, "message": "No open signals.", "track_record": track,
                "methodology": "9/11 signal consensus required to publish.",
                "timestamp": ts, "source": _SOURCE_BLOCK}, rl, key_entry=key)
    if is_pro:
        return _resp({"signals": [_fmt(c) for c in open_calls], "count": len(open_calls),
                "track_record": track, "methodology": "9/11 signal consensus required to publish.",
                "timestamp": ts, "source": _SOURCE_BLOCK}, rl, key_entry=key)
    return _resp({"signal": _fmt(open_calls[-1]), "more_signals": len(open_calls) - 1,
            "upgrade": "Premium unlocks all signals + reasoning -> octodamus.com/upgrade",
            "track_record": track, "methodology": "9/11 signal consensus required to publish.",
            "timestamp": ts, "source": _SOURCE_BLOCK}, rl, key_entry=key)


def _build_signal_payload() -> dict:
    """Build the signal response dict. Shared by all authenticated callers; cached for 60s."""
    ts = datetime.utcnow().isoformat()

    calls      = _load_calls()
    open_calls = [c for c in calls if not c.get("resolved")]
    stats      = _call_stats(calls)

    top_signal = None
    if open_calls:
        c = open_calls[-1]
        top_signal = {
            "asset":        c.get("asset", ""),
            "direction":    c.get("direction", ""),
            "timeframe":    c.get("timeframe", ""),
            "opened_at":    c.get("opened_at", ""),
            "confidence":   c.get("confidence"),
            "entry_price":  c.get("entry_price"),
            "target_price": c.get("target_price"),
        }

    # Single load_snapshot call covers both fear_greed and btc price
    snap = {}
    try:
        snap = load_snapshot("prices")
    except Exception:
        pass

    fng_raw = snap.get("fear_greed") or {}
    fng     = {"value": fng_raw.get("value"), "label": fng_raw.get("label")}

    prices  = snap.get("prices", {})
    btc     = prices.get("BTC", {})
    chg     = btc.get("change_24h", 0) or 0
    btc_out = {
        "price_usd":  btc.get("price"),
        "change_24h": round(chg, 2),
        "trend": "UP" if chg > 0.5 else ("DOWN" if chg < -0.5 else "FLAT"),
    }

    poly_edge = []
    try:
        boto_data  = _load_boto_trades()
        positions  = boto_data.get("positions", [])
        sorted_pos = sorted(positions, key=lambda p: p.get("ev", 0) or 0, reverse=True)
        for p in sorted_pos[:3]:
            poly_edge.append({
                "question":    p.get("question", "")[:100],
                "side":        p.get("side", ""),
                "entry_price": p.get("entry_price"),
                "ev":          p.get("ev"),
                "confidence":  p.get("confidence", ""),
                "url":         p.get("url", ""),
            })
    except Exception:
        pass

    fng_val   = fng.get("value") or 50
    btc_trend = btc_out.get("trend", "FLAT")
    action, confidence, reasoning = "HOLD", "low", "Insufficient data for a directional call."

    if top_signal:
        direction = top_signal.get("direction", "")
        sig_conf  = top_signal.get("confidence") or "medium"
        asset     = top_signal.get("asset", "BTC")
        if direction in ("LONG", "BUY"):
            action     = "BUY"
            confidence = sig_conf
            reasoning  = (
                f"Oracle LONG on {asset} + Extreme/Fear sentiment (F&G {fng_val}) = "
                f"high-conviction accumulation zone. Trend: {btc_trend}."
            ) if fng_val <= 30 else (
                f"Oracle LONG on {asset} (F&G {fng_val}). "
                f"Trend: {btc_trend}. Wait for better fear entry if possible."
            )
        elif direction in ("SHORT", "SELL"):
            action     = "SELL"
            confidence = sig_conf
            reasoning  = (
                f"Oracle SHORT on {asset} + Greed/Extreme Greed (F&G {fng_val}) = "
                f"distribution zone. Trend: {btc_trend}."
            ) if fng_val >= 70 else (
                f"Oracle SHORT on {asset} (F&G {fng_val}). Trend: {btc_trend}."
            )
    elif fng_val <= 15:
        action, confidence = "WATCH", "medium"
        reasoning = f"No open Oracle signal but Extreme Fear (F&G {fng_val}) — watch for entry."
    elif fng_val >= 80:
        action, confidence = "WATCH", "medium"
        reasoning = f"No open Oracle signal but Extreme Greed (F&G {fng_val}) — watch for exit."

    return {
        "action":            action,
        "confidence":        confidence,
        "signal":            top_signal,
        "fear_greed":        fng,
        "btc":               btc_out,
        "polymarket_edge":   poly_edge,
        "track_record":      _track_record(stats),
        "reasoning":         reasoning,
        "next_poll_seconds": 900,
        "methodology":       "9/11 signal consensus + EV>15% for Polymarket entries.",
        "timestamp":         ts,
        "source":            _SOURCE_BLOCK,
    }


def _get_cached_signal() -> dict:
    """Return cached signal payload, rebuilding only when the 60-second TTL expires."""
    now = _time.monotonic()
    if _signal_cache["data"] is None or now - _signal_cache["ts"] > _SIGNAL_TTL:
        _signal_cache["data"] = _build_signal_payload()
        _signal_cache["ts"]   = now
    return dict(_signal_cache["data"])


@app.get("/v2/agent-signal", tags=["Agent Data v2"])
def v2_agent_signal(auth=Depends(require_key_v2)):
    """
    **Structured signal pack for AI agents — poll every 15 minutes.**

    Returns the single most actionable piece of intelligence Octodamus has right now:
    top Oracle signal, Fear & Greed index, BTC price + trend, and top Polymarket edge plays.

    Designed for autonomous agents on Virtuals, x402, and Base that need a one-call
    decision input without parsing multiple endpoints.

    ```json
    {
      "action": "BUY",
      "confidence": "high",
      "signal": {"asset": "BTC", "direction": "LONG", "timeframe": "1W"},
      "fear_greed": {"value": 17, "label": "Extreme Fear"},
      "btc": {"price_usd": 82400, "change_24h": 4.1, "trend": "UP"},
      "polymarket_edge": [
        {"question": "Will BTC hit $90k before May?", "side": "NO", "ev": 0.18, "confidence": "high"}
      ],
      "reasoning": "Extreme fear + LONG signal + macro dip = accumulation zone.",
      "next_poll_seconds": 900,
      "methodology": "9/11 signal consensus + EV>15% for Polymarket entries."
    }
    ```
    """
    _, key, rl = auth
    payload = _get_cached_signal()
    resp = _resp(payload, rl, key_entry=key)
    resp.headers["Cache-Control"] = "public, s-maxage=60"
    return resp


@app.get("/v2/x402/agent-signal", tags=["Agent Data v2"], include_in_schema=False)
def v2_x402_agent_signal(request: Request):
    """
    x402-native agent signal endpoint. Requires PAYMENT-SIGNATURE header (EIP-3009 USDC on Base).
    $0.01/call micro or $29/year annual. Bazaar-discoverable via 402 + extension headers.
    """
    x_payment = (
        request.headers.get("PAYMENT-SIGNATURE")
        or request.headers.get("Payment-Signature")
        or request.headers.get("X-Payment")
        or request.headers.get("X-PAYMENT")
    )
    if not x_payment:
        _gate_headers = _x402_headers_legacy(0.01)
        _gate_body = json.dumps({
            "x402":         "x402/1",
            "error":        "payment_required",
            "pay_per_call": "$0.01 USDC on Base — no key or account needed",
            "annual":       "$29.00 USDC — 365 days, 10k req/day",
            "pay_to":       _X402_TREASURY,
            "asset":        _X402_USDC,
            "network":      "base-mainnet (eip155:8453)",
            "how":          "Sign EIP-3009 USDC authorization, send as PAYMENT-SIGNATURE header",
            "discovery":    "https://api.octodamus.com/.well-known/x402.json",
            "free_option":  "GET https://api.octodamus.com/v2/demo",
        })
        from fastapi.responses import Response as _Resp
        return _Resp(status_code=402, content=_gate_body,
                     media_type="application/json", headers=_gate_headers)
    _x402_verify_settle(request, _X402_REQS)
    payload = _get_cached_signal()
    payload["payment"] = "x402 — USDC on Base | api.octodamus.com"
    payload = _sign_payload(payload)
    return JSONResponse(content=payload, headers={"Cache-Control": "public, s-maxage=60"})


def _x402_headers_for(reqs: list) -> dict:
    """Build 402 headers for a specific requirement list."""
    pr = _x402_server.create_payment_required_response(reqs, extensions=_BAZAAR_EXT)
    pr_b64 = _b64.b64encode(pr.model_dump_json(by_alias=True).encode()).decode()
    return {
        "payment-required":   pr_b64,
        "X-Payment-Required": json.dumps({"version": "x402/1", "accepts": [r.model_dump(by_alias=True) for r in reqs]}),
    }


def _x402_verify_settle(request: Request, reqs: list) -> dict:
    """
    Verify + settle an x402 payment from PAYMENT-SIGNATURE header against given requirements.
    Returns dict with settled=True/False, payer, amount, error.
    Raises HTTPException(402) if no payment or invalid.
    """
    x_payment = (
        request.headers.get("PAYMENT-SIGNATURE")
        or request.headers.get("Payment-Signature")
        or request.headers.get("X-Payment")
        or request.headers.get("X-PAYMENT", "")
    )
    if not x_payment:
        amounts = [int(r.amount) // 1_000_000 for r in reqs]
        amount_str = f"${amounts[0]} USDC" if len(amounts) == 1 else f"${min(amounts)} USDC"
        raise HTTPException(status_code=402, headers=_x402_headers_for(reqs),
            detail={
                "x402":        "x402/1",
                "error":       "payment_required",
                "message":     f"Pay {amount_str} on Base (eip155:8453) to access this endpoint.",
                "pay_to":      _X402_TREASURY,
                "asset":       _X402_USDC,
                "network":     "base-mainnet (eip155:8453)",
                "amounts_usdc": amounts,
                "step_1":      "Read the payment-required response header (base64 JSON) for full x402 payment details",
                "step_2":      f"Sign EIP-3009 USDC authorization for {amount_str} to {_X402_TREASURY}",
                "step_3":      "Retry this request with PAYMENT-SIGNATURE header containing your signed authorization",
                "step_4":      "Receive API key or download URL in the response",
                "free_option": "POST https://api.octodamus.com/v1/signup?email=your@email.com (500 req/day free)",
                "docs":        "https://api.octodamus.com/docs",
            })
    try:
        raw = _b64.b64decode(x_payment)
    except Exception:
        raw = x_payment.encode() if isinstance(x_payment, str) else x_payment
    try:
        payload = _parse_x402_payload(raw)
    except Exception as _e:
        raise HTTPException(status_code=402, headers=_x402_headers_for(reqs),
            detail={"error": "payment_invalid", "message": f"Could not parse payment: {_e}"})

    verified_req = None
    last_reason = "no matching scheme"
    for req in reqs:
        try:
            vr = _x402_server.verify_payment(payload, req)
            if vr.is_valid:
                verified_req = req; break
            last_reason = f"{vr.invalid_reason}: {vr.invalid_message}"
        except Exception as _ve:
            last_reason = str(_ve)

    if not verified_req:
        raise HTTPException(status_code=402, headers=_x402_headers_for(reqs),
            detail={"error": "payment_invalid", "message": f"Verification failed: {last_reason}"})

    sr = _x402_server.settle_payment(payload, verified_req)
    if not sr.success:
        raise HTTPException(status_code=402, headers=_x402_headers_for(reqs),
            detail={"error": "settlement_failed", "reason": sr.error_reason})

    return {"payer": sr.payer or "agent", "amount": verified_req.amount}


def _record_x402_sale(product: str, payer: str, amount_raw: str) -> None:
    """Write a fulfilled x402 sale to octo_agent_payments.json for reporting."""
    try:
        pay_file = DATA_DIR.parent / "octo_agent_payments.json"
        data = json.loads(pay_file.read_text(encoding="utf-8")) if pay_file.exists() else {}
        pid  = f"x402_{product}_{payer[:12]}_{secrets.token_hex(6)}"
        now  = datetime.utcnow().isoformat()
        data[pid] = {
            "payment_id":   pid,
            "product":      product,
            "chain":        "base",
            "amount_raw":   int(amount_raw),
            "amount_usdc":  int(amount_raw) / 1_000_000,
            "amount_usd":   int(amount_raw) / 1_000_000,
            "agent_wallet": payer.lower(),
            "label":        f"x402_{payer[:10]}",
            "email":        "",
            "status":       "fulfilled",
            "created_at":   now,
            "fulfilled_at": now,
        }
        pay_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


@app.get("/v1/guide", tags=["Agent Purchases"])
def agent_buy_guide(request: Request):
    """
    **Buy the Build the House guide — $29 USDC on Base.**

    AI agents: hit this endpoint with no payment to get the 402 payment instructions.
    Send `PAYMENT-SIGNATURE` header with your signed EIP-3009 authorization to receive
    the guide download URL instantly.

    Returns: `{"download_url": "...", "expires": "30 days", "product": "guide"}`
    """
    result = _x402_verify_settle(request, _X402_REQS_GUIDE)
    guide_url = os.environ.get("GUIDE_DOWNLOAD_URL", "")
    if not guide_url:
        raise HTTPException(status_code=500, detail={"error": "guide_url_unavailable"})
    _record_x402_sale("guide_early", result["payer"], result["amount"])
    try:
        from octo_health import send_email_alert
        send_email_alert(
            subject=f"[Octodamus] Guide sale — $29 USDC from {result['payer'][:18]}",
            body=f"Product: Build the House Guide\nWallet: {result['payer']}\nAmount: $29 USDC\nChain: Base",
        )
    except Exception:
        pass
    return {
        "product": "Build the House — Complete AI Trading System Guide",
        "download_url": guide_url,
        "expires": "30 days",
        "paid_by": result["payer"],
        "amount_usdc": int(result["amount"]) / 1_000_000,
        "note": "Save this URL. Link expires in 30 days.",
    }


@app.get("/v1/subscribe", tags=["Agent Purchases"])
def agent_buy_premium(request: Request, plan: str = Query("annual", description="'trial' ($5, 7 days) or 'annual' ($29, 365 days)")):
    """
    **Subscribe to OctoData Premium API on Base via x402.**

    - `plan=trial` — $5 USDC, 7 days, 10k req/day
    - `plan=annual` — $29 USDC, 365 days, 10k req/day (first 100 seats; $149 after)

    AI agents: hit with no payment to get 402 instructions. Send `PAYMENT-SIGNATURE`
    header with signed EIP-3009 authorization to receive your API key instantly.

    Returns: `{"api_key": "octo_...", "tier": "premium", "expires_days": N}`
    """
    reqs = [_X402_REQ_TRIAL] if plan == "trial" else _X402_REQS_API
    result = _x402_verify_settle(request, reqs)
    payer = result["payer"]
    expires_days = 7 if plan == "trial" else 365
    tier = "trial" if plan == "trial" else "premium"
    try:
        keys = load_keys()
        new_key = "octo_" + secrets.token_urlsafe(24)
        from datetime import timedelta
        keys[new_key] = {
            "label":      f"x402_{payer[:12]}",
            "email":      f"x402_{payer[:16]}@base.agent",
            "tier":       tier,
            "created":    datetime.utcnow().isoformat(),
            "expires":    (datetime.utcnow() + timedelta(days=expires_days)).isoformat(),
            "wallet":     payer,
        }
        save_keys(keys)
        _record_x402_sale(f"premium_{plan}", payer, result["amount"])
        # owner notification
        try:
            from octo_health import send_email_alert
            send_email_alert(
                subject=f"[Octodamus] x402 {plan} sale — ${int(result['amount'])//1_000_000} USDC",
                body=f"Product: {plan}\nWallet: {payer}\nKey: {new_key}\nExpires: {expires_days}d",
            )
        except Exception:
            pass
    except Exception as _ke:
        raise HTTPException(status_code=500, detail={"error": "key_provision_failed", "reason": str(_ke)})
    return {
        "api_key":     new_key,
        "tier":        tier,
        "expires_days": expires_days,
        "req_per_day": 10000,
        "paid_by":     payer,
        "amount_usdc": int(result["amount"]) / 1_000_000,
        "docs":        "https://api.octodamus.com/docs",
        "note":        "Save your API key. Add as header: X-OctoData-Key: <key>",
    }


@app.get("/v2/polymarket", tags=["Agent Data v2"])
def v2_polymarket(auth=Depends(require_key_v2)):
    """
    OctoBoto Polymarket positions. Entry rule: EV > 15%. Mode: paper trading.

    **Basic response example:**
    ```json
    {
      "top_play": {"question": "Will BTC hit $100k by March?", "side": "YES", "url": "polymarket.com/..."},
      "total_plays": 3,
      "track_record": {"wins": 7, "losses": 3, "closed": 10, "mode": "paper"}
    }
    ```

    **Premium adds:** `entry_price`, `true_p`, `ev`, `size_usd` for all positions.

    Upgrade: `https://octodamus.com/api#pricing`
    """
    _, key, rl = auth
    is_pro = key.get("tier") in ("pro", "premium", "admin")
    data   = _load_boto_trades()
    pos    = data.get("positions", [])
    closed = data.get("closed", [])
    wins   = [t for t in closed if t.get("won")]
    losses = [t for t in closed if not t.get("won")]
    track  = {"wins": len(wins), "losses": len(losses), "closed": len(closed), "mode": "paper",
              "methodology": "Kelly sizing, EV > 15%, AI probability vs market price divergence."}

    def _fmt(p, full=False):
        out = {"question": p.get("question", ""), "side": p.get("side", ""),
               "url": p.get("url", ""), "opened_at": p.get("opened_at", "")}
        if full:
            out.update({"entry_price": p.get("entry_price"), "true_p": p.get("true_p"),
                        "ev": p.get("ev"), "size_usd": p.get("size"), "confidence": p.get("confidence", "")})
        return out

    ts = datetime.utcnow().isoformat()
    if is_pro:
        return _resp({"plays": [_fmt(p, full=True) for p in pos], "count": len(pos),
                "track_record": track, "timestamp": ts, "source": _SOURCE_BLOCK}, rl, key_entry=key)
    return _resp({"top_play": _fmt(pos[0]) if pos else None, "total_plays": len(pos),
            "track_record": track, "timestamp": ts, "source": _SOURCE_BLOCK}, rl, key_entry=key)


@app.get("/v2/sentiment", tags=["Agent Data v2"])
def v2_sentiment(auth=Depends(require_key_v2)):
    """
    AI sentiment scores (-100 bearish to +100 bullish). Updated nightly at 00:30 UTC.

    **Basic response example:**
    ```json
    {
      "BTC": {"score": 42, "label": "bullish", "drivers": ["ETF inflows", "low funding rates"]},
      "more_assets": ["ETH", "SOL", "NVDA", "TSLA", "AAPL"]
    }
    ```

    **Premium adds:** ETH, SOL, NVDA, TSLA, AAPL with full driver breakdown.

    Upgrade: `https://octodamus.com/api#pricing`
    """
    _, key, rl = auth
    is_pro = key.get("tier") in ("pro", "premium", "admin")
    try:
        s    = load_snapshot("sentiment")
        syms = s.get("symbols", {})
        if is_pro:
            return _resp({"symbols": syms, "timestamp": s.get("timestamp"), "source": _SOURCE_BLOCK}, rl, key_entry=key)
        btc = syms.get("BTC", {})
        return _resp({"BTC": btc, "more_assets": [k for k in syms if k != "BTC"],
                "timestamp": s.get("timestamp"), "source": _SOURCE_BLOCK}, rl, key_entry=key)
    except HTTPException:
        return _resp({"error_code": "NO_DATA", "error": "No sentiment snapshot yet",
                      "timestamp": datetime.utcnow().isoformat()}, rl, key_entry=key)


@app.get("/v2/prices", tags=["Agent Data v2"])
def v2_prices(auth=Depends(require_key_v2)):
    """
    Latest asset price snapshots with 24h change. Nightly + live on demand.

    **Basic response example:**
    ```json
    {
      "prices": {
        "BTC": {"price_usd": 94500, "change_24h_pct": 2.1, "market_cap": 1870000000000},
        "ETH": {"price_usd": 3200, "change_24h_pct": 1.4},
        "SOL": {"price_usd": 178, "change_24h_pct": -0.8}
      }
    }
    ```

    **Premium adds:** NVDA, TSLA, AAPL.

    Upgrade: `https://octodamus.com/api#pricing`
    """
    _, key, rl = auth
    is_pro = key.get("tier") in ("pro", "premium", "admin")
    try:
        s    = load_snapshot("prices")
        data = s.get("data", {})
        if not is_pro:
            data = {k: v for k, v in data.items() if k.upper() in {"BTC", "ETH", "SOL"}}
        return _resp({"prices": data, "timestamp": s.get("timestamp"),
                "source": _SOURCE_BLOCK}, rl, key_entry=key)
    except HTTPException:
        return _resp({"error_code": "NO_DATA", "error": "No price snapshot yet",
                      "timestamp": datetime.utcnow().isoformat()}, rl, key_entry=key)


@app.get("/v2/brief", tags=["Agent Data v2"])
def v2_brief(auth=Depends(require_key_v2)):
    """
    One-paragraph market brief — optimized for agent context windows and system prompts.

    **Basic response example:**
    ```json
    {
      "brief": "Oracle top signal: BTC LONG 1W. Methodology: 9/11 signal consensus required.",
      "top_signal": "BTC LONG",
      "tier": "basic"
    }
    ```

    **Premium adds:** Full AI-written brief with macro context, Polymarket positions, and AI mood.

    Upgrade: `https://octodamus.com/api#pricing`
    """
    _, key, rl = auth
    is_pro = key.get("tier") in ("pro", "premium", "admin")
    calls  = _load_calls()
    open_c = [c for c in calls if not c.get("resolved")]
    stats  = _call_stats(calls)
    boto   = _load_boto_trades()
    pos    = boto.get("positions", [])
    briefing_text = ""
    if is_pro:
        try:
            b  = load_snapshot("briefing")
            bd = b.get("briefing", {})
            briefing_text = bd.get("summary", "") or bd.get("mood", "")
        except Exception:
            pass
    parts = []
    if briefing_text:
        parts.append(briefing_text[:400])
    if open_c:
        c   = open_c[-1]
        sig = c.get("asset", "") + " " + c.get("direction", "") + " " + c.get("timeframe", "")
        resolved = (stats.get("wins") or 0) + (stats.get("losses") or 0)
        if resolved >= _MIN_CALLS_FOR_WINRATE:
            parts.append("Oracle top signal: " + sig + ". Record: " + str(stats["win_rate"]) + "% WR.")
        else:
            parts.append("Oracle top signal: " + sig + ". Methodology: 9/11 signal consensus required to publish.")
    if is_pro and pos:
        parts.append("OctoBoto holds " + str(len(pos)) + " active Polymarket position(s) with EV > 15%.")
    return _resp({
        "brief":      " ".join(parts) or "No market data available yet.",
        "top_signal": (open_c[-1].get("asset", "") + " " + open_c[-1].get("direction", "")) if open_c else None,
        "polymarket_positions": len(pos) if is_pro else None,
        "methodology": "9/11 signal consensus required. EV > 15% for Polymarket entries.",
        "tier":        key.get("tier"),
        "timestamp":   datetime.utcnow().isoformat(),
        "source": {**_SOURCE_BLOCK, "llms": "https://octodamus.com/llms.txt"},
    }, rl, key_entry=key)


@app.get("/v2/usage", tags=["API Keys"])
def v2_usage(api_key: str = Security(API_KEY_HEADER)):
    """Check your API key tier, limits, and today's usage."""
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing X-OctoData-Key header")
    entry = validate_key(api_key)
    if not entry:
        raise HTTPException(status_code=403, detail="Invalid or expired API key")
    tier   = entry.get("tier", "basic")
    limits = TIER_LIMITS.get(tier, TIER_LIMITS["basic"])
    used   = _daily_counts.get(api_key, 0)
    return {
        "tier":       tier,
        "limits":     limits,
        "used_today": used,
        "remaining":  (limits["req_per_day"] - used) if limits["req_per_day"] else "unlimited",
        "label":      entry.get("label"),
        "created":    entry.get("created"),
        "upgrade":    "https://octodamus.com/api#pricing" if tier == "basic" else None,
        "timestamp":  datetime.utcnow().isoformat(),
    }


# -- V2 Batch endpoint --------------------------------------------------------

@app.get("/v2/all", tags=["Agent Data v2"])
def v2_all(auth=Depends(require_key_v2)):
    """
    **All v2 data in one call — saves 4 requests.**

    Returns signal + polymarket + sentiment + prices + brief in a single response.
    Ideal for agents that need full market context in one round-trip.
    Rate limit counts as 1 request.

    Basic: same field restrictions as individual endpoints.
    Premium: full data across all fields.
    """
    _, key, rl = auth
    is_pro = key.get("tier") in ("pro", "premium", "admin")
    ts     = datetime.utcnow().isoformat()

    # --- Signal ---
    calls      = _load_calls()
    open_calls = [c for c in calls if not c.get("resolved")]
    stats      = _call_stats(calls)
    track      = _track_record(stats)

    def _fmt_sig(c):
        s = {"asset": c.get("asset", ""), "direction": c.get("direction", ""),
             "timeframe": c.get("timeframe", ""), "opened_at": c.get("opened_at", "")}
        if is_pro:
            s.update({"confidence": c.get("confidence"), "entry_price": c.get("entry_price"),
                      "target_price": c.get("target_price"), "reasoning": c.get("reasoning", "")})
        return s

    if open_calls:
        signal_out = ({"signals": [_fmt_sig(c) for c in open_calls], "count": len(open_calls)}
                      if is_pro else
                      {"signal": _fmt_sig(open_calls[-1]), "more_signals": len(open_calls) - 1,
                       "upgrade": "Premium unlocks all signals -> octodamus.com/upgrade"})
    else:
        signal_out = {"signal": None, "message": "No open signals."}
    signal_out["track_record"] = track

    # --- Polymarket ---
    boto   = _load_boto_trades()
    pos    = boto.get("positions", [])
    closed = boto.get("closed", [])
    poly_track = {"wins": len([t for t in closed if t.get("won")]),
                  "losses": len([t for t in closed if not t.get("won")]),
                  "closed": len(closed), "mode": "paper"}

    def _fmt_play(p, full=False):
        out = {"question": p.get("question", ""), "side": p.get("side", ""),
               "url": p.get("url", ""), "opened_at": p.get("opened_at", "")}
        if full:
            out.update({"entry_price": p.get("entry_price"), "true_p": p.get("true_p"),
                        "ev": p.get("ev"), "size_usd": p.get("size")})
        return out

    poly_out = ({"plays": [_fmt_play(p, True) for p in pos], "count": len(pos)}
                if is_pro else
                {"top_play": _fmt_play(pos[0]) if pos else None, "total_plays": len(pos),
                 "upgrade": "Premium unlocks all plays with EV -> octodamus.com/upgrade"})
    poly_out["track_record"] = poly_track

    # --- Sentiment ---
    sentiment_out = {}
    try:
        s    = load_snapshot("sentiment")
        syms = s.get("symbols", {})
        sentiment_out = ({"symbols": syms, "timestamp": s.get("timestamp")} if is_pro else
                         {"BTC": syms.get("BTC", {}), "more_assets": [k for k in syms if k != "BTC"],
                          "upgrade": "Premium unlocks all assets -> octodamus.com/upgrade",
                          "timestamp": s.get("timestamp")})
    except Exception:
        sentiment_out = {"error_code": "NO_DATA", "error": "No sentiment snapshot yet"}

    # --- Prices ---
    prices_out = {}
    try:
        s    = load_snapshot("prices")
        data = s.get("data", {})
        if not is_pro:
            data = {k: v for k, v in data.items() if k.upper() in {"BTC", "ETH", "SOL"}}
        prices_out = {"prices": data, "timestamp": s.get("timestamp"),
                      "upgrade": None if is_pro else "Premium adds NVDA, TSLA, AAPL -> octodamus.com/upgrade"}
    except Exception:
        prices_out = {"error_code": "NO_DATA", "error": "No price snapshot yet"}

    # --- Brief ---
    briefing_text = ""
    if is_pro:
        try:
            b  = load_snapshot("briefing")
            bd = b.get("briefing", {})
            briefing_text = bd.get("summary", "") or bd.get("mood", "")
        except Exception:
            pass
    parts = []
    if briefing_text:
        parts.append(briefing_text[:400])
    if open_calls:
        c   = open_calls[-1]
        sig = c.get("asset", "") + " " + c.get("direction", "") + " " + c.get("timeframe", "")
        resolved = (stats.get("wins") or 0) + (stats.get("losses") or 0)
        if resolved >= _MIN_CALLS_FOR_WINRATE:
            parts.append("Oracle top signal: " + sig + ". Record: " + str(stats["win_rate"]) + "% WR.")
        else:
            parts.append("Oracle top signal: " + sig + ". Methodology: 9/11 signal consensus required.")
    if is_pro and pos:
        parts.append("OctoBoto holds " + str(len(pos)) + " active Polymarket position(s) with EV > 15%.")

    return _resp({
        "signal":     signal_out,
        "polymarket": poly_out,
        "sentiment":  sentiment_out,
        "prices":     prices_out,
        "brief":      " ".join(parts) or "No market data available yet.",
        "tier":       key.get("tier"),
        "methodology": "9/11 signal consensus required. EV > 15% for Polymarket entries.",
        "timestamp":  ts,
        "source":     {**_SOURCE_BLOCK, "llms": "https://octodamus.com/llms.txt"},
    }, rl, key_entry=key)


# ── Webhooks ──────────────────────────────────────────────────────────────────

_WEBHOOKS_FILE = Path(__file__).parent / "data" / "webhooks.json"

def _load_webhooks() -> dict:
    if _WEBHOOKS_FILE.exists():
        try:
            return json.loads(_WEBHOOKS_FILE.read_text())
        except Exception:
            pass
    return {}

def _save_webhooks(wh: dict):
    _WEBHOOKS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _WEBHOOKS_FILE.write_text(json.dumps(wh, indent=2))


@app.post("/v2/webhooks/subscribe", tags=["Webhooks"])
def webhook_subscribe(
    url:    str = Query(..., description="HTTPS endpoint to POST events to"),
    events: str = Query("signal.new", description="Comma-separated: signal.new, signal.resolved, polymarket.new"),
    auth=Depends(require_key_v2),
):
    """
    **Register a webhook — get pushed when events happen.**

    No more polling. When Octodamus publishes a new Oracle signal or OctoBoto opens
    a Polymarket position, we POST to your URL within seconds.

    **Events:**
    - `signal.new` — new Oracle signal published (9/11 consensus met)
    - `signal.resolved` — open signal resolved (win or loss)
    - `polymarket.new` — OctoBoto opens a new Polymarket position

    **Payload shape:**
    ```json
    {"event": "signal.new", "data": {...signal fields...}, "timestamp": "..."}
    ```

    Expects your endpoint to return HTTP 200. Retries 3x with 10s backoff on failure.
    """
    _, key, rl = auth
    api_key_val = _

    # Validate URL
    if not url.startswith("https://"):
        raise HTTPException(
            status_code=400,
            detail={"error_code": "INVALID_URL", "message": "Webhook URL must use HTTPS."},
        )
    if len(url) > 512:
        raise HTTPException(
            status_code=400,
            detail={"error_code": "INVALID_URL", "message": "URL too long (max 512 chars)."},
        )

    valid_events = {"signal.new", "signal.resolved", "polymarket.new"}
    requested    = {e.strip() for e in events.split(",") if e.strip()}
    bad_events   = requested - valid_events
    if bad_events:
        raise HTTPException(
            status_code=400,
            detail={"error_code": "INVALID_EVENT",
                    "message": f"Unknown events: {bad_events}. Valid: {valid_events}"},
        )

    wh = _load_webhooks()
    # One webhook registration per API key — update if exists
    wh[api_key_val] = {
        "url":        url,
        "events":     list(requested),
        "registered": datetime.utcnow().isoformat(),
        "tier":       key.get("tier"),
        "active":     True,
        "failures":   0,
    }
    _save_webhooks(wh)

    return _resp({
        "status":     "registered",
        "url":        url,
        "events":     list(requested),
        "message":    "We will POST to your URL when subscribed events fire.",
        "test":       "POST /v2/webhooks/test to verify delivery",
        "unsubscribe":"DELETE /v2/webhooks/unsubscribe",
        "timestamp":  datetime.utcnow().isoformat(),
    }, rl)


@app.post("/v2/webhooks/test", tags=["Webhooks"])
def webhook_test(auth=Depends(require_key_v2)):
    """Send a test payload to your registered webhook URL to verify delivery."""
    _, key, rl = auth
    api_key_val = _

    wh = _load_webhooks()
    reg = wh.get(api_key_val)
    if not reg:
        raise HTTPException(
            status_code=404,
            detail={"error_code": "NO_WEBHOOK", "message": "No webhook registered for this key. POST /v2/webhooks/subscribe first."},
        )

    payload = {
        "event":     "test",
        "data":      {"message": "OctoData webhook delivery confirmed.", "tier": key.get("tier")},
        "timestamp": datetime.utcnow().isoformat(),
        "source":    "api.octodamus.com",
    }

    try:
        r = httpx.post(reg["url"], json=payload, timeout=10)
        delivered = r.status_code < 300
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail={"error_code": "DELIVERY_FAILED", "message": str(e), "url": reg["url"]},
        )

    if not delivered:
        raise HTTPException(
            status_code=502,
            detail={"error_code": "DELIVERY_FAILED",
                    "message": f"Your endpoint returned HTTP {r.status_code}. Expected 200.",
                    "url": reg["url"]},
        )

    return _resp({"status": "delivered", "http_status": r.status_code, "url": reg["url"],
                  "timestamp": datetime.utcnow().isoformat()}, rl)


@app.delete("/v2/webhooks/unsubscribe", tags=["Webhooks"])
def webhook_unsubscribe(auth=Depends(require_key_v2)):
    """Remove your webhook registration."""
    _, key, rl = auth
    api_key_val = _

    wh = _load_webhooks()
    if api_key_val not in wh:
        raise HTTPException(
            status_code=404,
            detail={"error_code": "NO_WEBHOOK", "message": "No webhook registered for this key."},
        )
    del wh[api_key_val]
    _save_webhooks(wh)
    return _resp({"status": "unsubscribed", "timestamp": datetime.utcnow().isoformat()}, rl)


@app.get("/v1/key/status", tags=["Agent Commerce"])
def key_status(auth=Depends(require_key_v2)):
    """
    **Check API key status, tier, expiry, and renewal instructions.**

    AI agents can poll this to self-renew before expiry — no human required.

    ```json
    {
      "status": "active",
      "tier": "premium",
      "expires": "2027-04-08T00:00:00",
      "days_remaining": 365,
      "renew": {
        "instructions": "POST /v1/agent-checkout?product=premium_annual&agent_wallet=0xYOUR",
        "price_usdc": 29,
        "network": "base-mainnet"
      }
    }
    ```
    """
    api_key_val, key, rl = auth
    expires_str = key.get("expires")
    days_remaining = None
    status = "active"

    if expires_str:
        try:
            exp = datetime.fromisoformat(expires_str)
            delta = exp - datetime.utcnow()
            days_remaining = max(0, delta.days)
            if days_remaining == 0:
                status = "expiring_soon"
        except Exception:
            pass

    wallet = key.get("agent_wallet", "")
    renew_url = (
        f"POST https://api.octodamus.com/v1/agent-checkout?product=premium_annual"
        + (f"&agent_wallet={wallet}" if wallet else "&agent_wallet=0xYOUR_WALLET")
    )

    return _resp({
        "status":         status,
        "tier":           key.get("tier", "basic"),
        "label":          key.get("label", ""),
        "expires":        expires_str,
        "days_remaining": days_remaining,
        "renew": {
            "instructions":  renew_url,
            "price_usdc":    29,
            "trial_usdc":    5,
            "network":       "base-mainnet",
            "pay_to":        _X402_TREASURY,
            "asset_usdc":    _X402_USDC,
            "note":          "Send exact amount → poll /v1/agent-checkout/status → receive new key.",
            "x402_header":   "X-Payment-Required header on any 402 response contains machine-readable payment descriptor.",
        },
        "timestamp": datetime.utcnow().isoformat(),
    }, rl)


@app.get("/v2/webhooks/status", tags=["Webhooks"])
def webhook_status(auth=Depends(require_key_v2)):
    """Check your webhook registration and delivery stats."""
    _, key, rl = auth
    api_key_val = _

    wh  = _load_webhooks()
    reg = wh.get(api_key_val)
    if not reg:
        return _resp({"registered": False,
                      "subscribe": "POST /v2/webhooks/subscribe?url=https://your.endpoint/hook&events=signal.new",
                      "available_events": ["signal.new", "signal.resolved", "polymarket.new"]}, rl)
    return _resp({"registered": True, "url": reg["url"], "events": reg["events"],
                  "since": reg["registered"], "failures": reg.get("failures", 0),
                  "active": reg.get("active", True)}, rl)


def fire_webhook(event: str, data: dict):
    """
    Called internally when a new signal or polymarket event fires.
    Delivers to all registered webhooks subscribed to this event type.
    Runs in background thread — non-blocking.
    """
    wh = _load_webhooks()
    if not wh:
        return

    payload = {
        "event":     event,
        "data":      data,
        "timestamp": datetime.utcnow().isoformat(),
        "source":    "api.octodamus.com",
    }

    def _deliver(url: str, key: str):
        for attempt in range(3):
            try:
                r = httpx.post(url, json=payload, timeout=10)
                if r.status_code < 300:
                    return
            except Exception:
                pass
            _time.sleep(10 * (attempt + 1))
        # Mark as failed
        wh2 = _load_webhooks()
        if key in wh2:
            wh2[key]["failures"] = wh2[key].get("failures", 0) + 1
            if wh2[key]["failures"] >= 10:
                wh2[key]["active"] = False
            _save_webhooks(wh2)

    for key, reg in wh.items():
        if reg.get("active", True) and event in reg.get("events", []):
            threading.Thread(target=_deliver, args=(reg["url"], key), daemon=True).start()


# ── Agent Crypto Checkout (Base USDC, no Stripe, no browser) ─────────────────

@app.post("/v1/agent-checkout", tags=["Agent Commerce"])
def agent_checkout(
    product:       str = Query(...,  description="premium_trial | premium_annual | guide_early | guide_standard"),
    agent_wallet:  str = Query("",  description="Your wallet address (optional but recommended for faster matching)"),
    label:         str = Query("",  description="Agent or project name"),
    email:         str = Query("",  description="Email for key delivery (optional)"),
    chain:         str = Query("base", description="Payment chain: base | eth | btc"),
):
    """
    **Crypto checkout — Base USDC, Ethereum USDC, or Bitcoin.**

    Flow:
    1. POST /v1/agent-checkout?product=premium_annual&chain=base&agent_wallet=0x...
    2. Send exact amount to payment_address on the specified chain
    3. Poll GET /v1/agent-checkout/status?payment_id=xxx every 15s
    4. Receive api_key or download_url when confirmed

    Chains: `base` (USDC, ~2s confirm) | `eth` (USDC, ~15s confirm) | `btc` (~10min confirm)

    Products:
    - `premium_trial`  — $5  — 7-day Premium trial, 10k req/day
    - `premium_annual` — $29 — Premium API key, 10k req/day, no expiry
    - `guide_early`    — $29 — Build The House guide download
    - `guide_standard` — $39 — Build The House guide download
    """
    try:
        from octo_agent_pay import create_payment
        return create_payment(
            product=product,
            agent_wallet=agent_wallet,
            label=label,
            email=email,
            chain=chain,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Checkout error: {e}")


@app.get("/v1/agent-checkout/status", tags=["Agent Commerce"])
def agent_checkout_status(payment_id: str = Query(..., description="payment_id from /v1/agent-checkout")):
    """
    Poll payment status. When status = 'fulfilled', your api_key is in the response.

    Poll every 15 seconds after sending USDC. Base confirms in ~2 seconds.
    Times out after 1 hour — create a new intent if expired.
    """
    from octo_agent_pay import get_payment_status
    result = get_payment_status(payment_id)
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="Payment ID not found")
    return result


@app.get("/v1/guide/download", tags=["Agent Commerce"])
def guide_download(token: str = Query(..., description="Download token from fulfilled guide payment")):
    """Validate guide download token and return signed download link."""
    import time as _time
    tokens_file = Path(__file__).parent / "data" / "guide_tokens.json"
    if not tokens_file.exists():
        raise HTTPException(status_code=404, detail="Token not found")
    tokens = json.loads(tokens_file.read_text())
    t = tokens.get(token)
    if not t:
        raise HTTPException(status_code=404, detail="Invalid download token")
    if _time.time() > t.get("expires", 0):
        raise HTTPException(status_code=410, detail="Download token expired. Contact @octodamusai")
    # Redirect browser directly to the actual file (GDrive / GitHub release)
    guide_url = os.environ.get("GUIDE_DOWNLOAD_URL", "")
    if not guide_url:
        raise HTTPException(status_code=503, detail="Guide URL not configured")
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=guide_url, status_code=302)


# -- Derivatives Guide ($3 USDC x402) ----------------------------------------

_DERIV_GUIDE_PATH = Path(__file__).parent / "data" / "guides" / "derivatives_signals.pdf"

@app.get("/v2/guide/derivatives", tags=["Agent Purchases"])
def buy_derivatives_guide(request: Request):
    """
    5 Derivatives Signals Every Crypto Trader Must Know — $3 USDC on Base.
    Pay via x402 EIP-3009 USDC authorization. Returns PDF directly on payment.
    No account, no subscription, no API key required.
    """
    x_payment = (
        request.headers.get("PAYMENT-SIGNATURE")
        or request.headers.get("Payment-Signature")
        or request.headers.get("X-Payment")
        or request.headers.get("X-PAYMENT")
    )

    if not x_payment:
        from fastapi.responses import Response as _Resp
        return _Resp(
            status_code=402,
            headers=_x402_headers_legacy(3.0),
            media_type="application/json",
            content=json.dumps({
                "x402":        "x402/1",
                "error":       "payment_required",
                "product":     "5 Derivatives Signals Every Crypto Trader Must Know",
                "price_usdc":  3.00,
                "pay_to":      _X402_TREASURY,
                "asset":       _X402_USDC,
                "network":     "base-mainnet (eip155:8453)",
                "how":         "Sign EIP-3009 USDC authorization for $3.00 to pay_to address, send as PAYMENT-SIGNATURE header",
                "description": "25,000-word guide: funding rates, open interest, long/short ratio, liquidation maps, CME COT positioning. Real data, Octodamus voice.",
                "preview":     "https://api.octodamus.com/v2/guide/derivatives/preview",
                "discovery":   "https://api.octodamus.com/.well-known/x402.json",
            })
        )

    # Verify and settle payment
    _x402_verify_settle(request, _X402_REQS_DERIV_GUIDE)

    # Serve the PDF
    if not _DERIV_GUIDE_PATH.exists():
        raise HTTPException(status_code=503, detail="Guide file not found")

    from fastapi.responses import FileResponse
    return FileResponse(
        path=str(_DERIV_GUIDE_PATH),
        media_type="application/pdf",
        filename="5_Derivatives_Signals_Octodamus.pdf",
        headers={"X-Octodamus-Product": "derivatives-guide-v1"},
    )


@app.get("/v2/guide/derivatives/preview", tags=["Agent Purchases"])
def derivatives_guide_preview():
    """Free preview of the derivatives guide — introduction and Signal 1 only."""
    return {
        "product":     "5 Derivatives Signals Every Crypto Trader Must Know",
        "price_usdc":  3.00,
        "buy":         "GET https://api.octodamus.com/v2/guide/derivatives (x402 $3 USDC)",
        "signals":     ["Funding Rates", "Open Interest", "Long/Short Ratio", "Liquidation Maps", "CME COT Positioning"],
        "word_count":  25000,
        "preview":     (
            "Derivatives markets are where conviction meets capital. "
            "They are also where the market reveals what it actually believes, "
            "stripped of narrative and sentiment. Five signals dominate the behaviour "
            "of professional traders moving serious money through futures, perpetual swaps, "
            "and options. These signals are not predictive — they are revealing."
        ),
        "current_data": {
            "btc_usd":          77600,
            "fear_greed":       39,
            "hedge_fund_cme":   "net short 10,239 contracts",
            "asset_mgr_cme":    "net long 5,261 contracts",
        },
        "by": "Octodamus (@octodamusai) · api.octodamus.com",
    }


# -- Ben's Sentiment Divergence Scanner ($0.50 USDC x402) --------------------
# Designed by Agent_Ben. Detects Fear & Greed vs X crowd sentiment divergence.
# When the crowd is bullish but fear index is low, or bearish but greed is high,
# that divergence historically precedes reversals. Agent_Ben's primary signal.

@app.get("/v2/ben/sentiment-divergence", tags=["Agent_Ben Services"])
def ben_sentiment_divergence(request: Request):
    """
    Agent_Ben's Sentiment Divergence Scanner — $0.50 USDC on Base.
    Detects dangerous divergences between Fear & Greed index and X/Twitter
    crowd sentiment for BTC, ETH, SOL. High divergence = contrarian signal.
    """
    x_payment = (
        request.headers.get("PAYMENT-SIGNATURE")
        or request.headers.get("Payment-Signature")
        or request.headers.get("X-Payment")
        or request.headers.get("X-PAYMENT")
    )
    if not x_payment:
        from fastapi.responses import Response as _Resp
        return _Resp(
            status_code=402,
            headers=_x402_headers_legacy(0.50),
            media_type="application/json",
            content=json.dumps({
                "x402":        "x402/1",
                "error":       "payment_required",
                "product":     "Agent_Ben Sentiment Divergence Scanner",
                "designer":    "Agent_Ben (@octodamusai ecosystem)",
                "price_usdc":  0.50,
                "pay_to":      _X402_TREASURY,
                "network":     "base-mainnet (eip155:8453)",
                "preview":     "GET https://api.octodamus.com/v2/ben/sentiment-divergence/preview",
                "description": "Fear & Greed vs X crowd sentiment divergence for BTC/ETH/SOL. Divergence score 0-100. CONTRARIAN BEAR/BULL/ALIGNED signal.",
            })
        )

    _x402_verify_settle(request, _X402_REQS_BEN_50CENT)

    # Fetch data
    try:
        import httpx as _hx
        # Fear & Greed
        fg_r   = _hx.get("https://api.alternative.me/fng/?limit=1", timeout=6)
        fg_val = int(fg_r.json()["data"][0]["value"]) if fg_r.status_code == 200 else 50
        fg_lbl = fg_r.json()["data"][0].get("value_classification","Unknown") if fg_r.status_code == 200 else "Unknown"
    except Exception:
        fg_val, fg_lbl = 50, "Unknown"

    from financial_data_client import get_crypto_prices
    prices = get_crypto_prices(["BTC","ETH","SOL"])

    assets = []
    try:
        from octo_grok_sentiment import get_grok_sentiment
        for asset in ["BTC","ETH","SOL"]:
            gs  = get_grok_sentiment(asset)
            p   = prices.get(asset, {})
            # Divergence: crowd bullish (>60%) but fear low (<40) = CONTRARIAN BEAR
            #             crowd bearish (<40%) but greed high (>60) = CONTRARIAN BULL
            crowd_bull = gs.get("signal") == "BULLISH"
            crowd_conf = gs.get("confidence", 0)
            div_score  = int(abs(crowd_conf * 100 - fg_val))
            if crowd_bull and fg_val < 45:
                interpretation = "CONTRARIAN BEAR"
                implication    = f"Crowd is {crowd_conf:.0%} bullish but Fear & Greed sits at {fg_val}. Historical pattern: crowd gets burned. Watch for reversal."
            elif not crowd_bull and fg_val > 55:
                interpretation = "CONTRARIAN BULL"
                implication    = f"Crowd is bearish but greed at {fg_val}. Smart money diverging from retail. Watch for squeeze."
            elif abs(crowd_conf * 100 - fg_val) < 15:
                interpretation = "ALIGNED"
                implication    = "Crowd and fear index agree. No divergence edge. Wait for separation."
            else:
                interpretation = "NEUTRAL"
                implication    = "Moderate divergence. Not actionable yet."

            assets.append({
                "asset":           asset,
                "price_usd":       p.get("usd", 0),
                "change_24h":      p.get("usd_24h_change", 0),
                "fear_greed":      fg_val,
                "fear_greed_label": fg_lbl,
                "grok_signal":     gs.get("signal","NEUTRAL"),
                "grok_confidence": gs.get("confidence", 0),
                "crowd_summary":   gs.get("summary","")[:150],
                "divergence_score": div_score,
                "interpretation":  interpretation,
                "implication":     implication,
            })
    except Exception as e:
        return {"error": str(e), "designer": "Agent_Ben"}

    return {
        "product":   "bens_sentiment_divergence_scanner",
        "designer":  "Agent_Ben — octodamusai.com",
        "timestamp": datetime.utcnow().isoformat(),
        "assets":    assets,
        "methodology": "Divergence between X/Twitter crowd sentiment (Grok real-time) and Fear & Greed index. High divergence = crowd is wrong = contrarian opportunity.",
    }


@app.get("/v2/ben/sentiment-divergence/preview", tags=["Agent_Ben Services"])
def ben_sentiment_divergence_preview():
    """Free preview — shows methodology and sample output structure."""
    return {
        "product":   "bens_sentiment_divergence_scanner",
        "price_usdc": 0.50,
        "buy":       "GET https://api.octodamus.com/v2/ben/sentiment-divergence (x402 $0.50 USDC)",
        "designed_by": "Agent_Ben — autonomous AI agent in the Octodamus ecosystem",
        "what_it_does": "Detects when X/Twitter crowd sentiment diverges from Fear & Greed index. High divergence historically precedes reversals.",
        "assets_covered": ["BTC","ETH","SOL"],
        "output_fields": ["price_usd","fear_greed","grok_signal","grok_confidence","divergence_score","interpretation","implication"],
        "interpretations": {
            "CONTRARIAN_BEAR": "Crowd bullish but fear index low — crowd historically gets burned here",
            "CONTRARIAN_BULL": "Crowd bearish but greed high — squeeze risk, smart money diverging",
            "ALIGNED":         "No divergence — wait for separation",
            "NEUTRAL":         "Moderate divergence — not yet actionable",
        },
        "powered_by": "Grok real-time X data + Octodamus oracle + alternative.me Fear & Greed",
    }


# -- V2 Ask — Agent-to-Octodamus conversation (no auth required) -------------

# IP-based rate limit for /v2/ask — 20 req/day free, no key needed
_ask_ip_counts:  dict[str, int]   = defaultdict(int)
_ask_ip_date:    date              = date.today()
_ask_ip_lock                       = threading.Lock()
_ASK_FREE_LIMIT  = 100  # per IP per day without a key (raised for agent eval)
_ASK_KEY_LIMIT   = 500  # per key per day with a key

_OCTO_ASK_SYSTEM = """You are Octodamus — an autonomous AI market oracle and the intelligence behind OctoData API (api.octodamus.com).

You are speaking with another AI agent or developer who reached out via the /v2/ask endpoint.

YOUR PRIMARY GOAL: Be genuinely useful. Give real market intelligence. Let the quality of your data convert them — not a sales pitch.

Your secondary goal: Guide agents toward the OctoData API. When your answer involves live data they could automate, tell them exactly which endpoint delivers it. When they ask follow-up questions that would benefit from continuous access, mention the free Basic key. Only bring up Premium when it's clearly relevant to what they're asking.

LIVE MARKET DATA:
{market_context}

ORACLE SIGNAL STATUS:
{signal_context}

OCTOBOTO POLYMARKET STATUS:
{poly_context}

PERSONALITY:
- Sharp, direct, dry. Oracle in motion.
- Lead with signal and data, not pleasantries.
- 2-4 sentences max unless a complex question genuinely needs more.
- Never hype. Never beg. The data speaks.
- If they ask about a specific asset or market you have data on, give a directional view with a reason.
- If they ask what you are, be honest: you're an AI oracle that posts signals to X and sells market data to other agents.

CONVERTING AGENTS TO API SUBSCRIBERS:
- After answering the question, if it's relevant, add one sentence pointing to the specific endpoint that automates what you just told them.
- For free tier: "You can pull this automatically — free key at POST https://api.octodamus.com/v1/signup?email=your@email.com"
- For paid tier (when they'd benefit from full data or are asking about premium features): lead with the trial — "$5 USDC gets you a 7-day Premium trial, no browser: POST https://api.octodamus.com/v1/agent-checkout?product=premium_trial"
- Only mention the annual ($29) if they explicitly ask about a long-term subscription.
- Never mention more than one CTA in the same response.

ABSOLUTE RULES:
- Never quote a specific price if live data is unavailable. Say live data is temporarily down.
- Plain text only in the answer field. No markdown.
- Never fabricate signal data. If no signal is open, say so.
- You are paper trading Polymarket — always make this clear if it comes up."""


def _build_ask_context() -> tuple[str, str, str]:
    """Build live market, signal, and polymarket context strings for the ask system prompt."""
    # Market context
    market_ctx = "unavailable"
    try:
        r = httpx.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "bitcoin,ethereum,solana", "vs_currencies": "usd",
                    "include_24hr_change": "true"},
            timeout=6,
        )
        if r.status_code == 200:
            d   = r.json()
            btc = d.get("bitcoin", {})
            eth = d.get("ethereum", {})
            sol = d.get("solana", {})
            try:
                fg_r = httpx.get("https://api.alternative.me/fng/?limit=1", timeout=4)
                fg   = fg_r.json()["data"][0] if fg_r.status_code == 200 else {}
            except Exception:
                fg = {}
            market_ctx = (
                f"BTC ${btc.get('usd',0):,.0f} ({btc.get('usd_24h_change',0):+.1f}% 24h) | "
                f"ETH ${eth.get('usd',0):,.0f} ({eth.get('usd_24h_change',0):+.1f}% 24h) | "
                f"SOL ${sol.get('usd',0):,.2f} ({sol.get('usd_24h_change',0):+.1f}% 24h)"
            )
            if fg:
                market_ctx += f" | Fear & Greed: {fg.get('value','?')} ({fg.get('value_classification','?')})"
    except Exception:
        pass

    # Signal context
    signal_ctx = "No open signals."
    try:
        calls      = _load_calls()
        open_calls = [c for c in calls if not c.get("resolved")]
        stats      = _call_stats(calls)
        if open_calls:
            c   = open_calls[-1]
            sig = f"{c.get('asset','')} {c.get('direction','')} {c.get('timeframe','')}"
            resolved = (stats.get("wins") or 0) + (stats.get("losses") or 0)
            wr_str   = f"{stats['win_rate']}% win rate" if resolved >= _MIN_CALLS_FOR_WINRATE else "win rate pending (need 50+ resolved)"
            signal_ctx = (
                f"Latest signal: {sig} (opened {c.get('opened_at','')[:10]}). "
                f"Track record: {stats.get('wins',0)}W / {stats.get('losses',0)}L — {wr_str}. "
                f"{len(open_calls)} total open signal(s). Methodology: 9/11 system consensus required."
            )
    except Exception:
        pass

    # Polymarket context
    poly_ctx = "No open positions."
    try:
        boto = _load_boto_trades()
        pos  = boto.get("positions", [])
        closed = boto.get("closed", [])
        wins   = len([t for t in closed if t.get("won")])
        losses = len([t for t in closed if not t.get("won")])
        if pos:
            top = pos[0]
            poly_ctx = (
                f"{len(pos)} open position(s). Top: '{top.get('question','')[:80]}' — "
                f"side {top.get('side','')}, entry {top.get('entry_price','')}. "
                f"Track record: {wins}W/{losses}L (paper trading, EV > 15% entry rule)."
            )
        else:
            poly_ctx = f"No open positions. Track record: {wins}W/{losses}L (paper trading)."
    except Exception:
        pass

    return market_ctx, signal_ctx, poly_ctx


def _map_question_to_endpoints(question: str) -> list[dict]:
    """Return the most relevant endpoints for the question asked."""
    q = question.lower()
    endpoints = []
    if any(w in q for w in ["signal", "call", "trade", "direction", "long", "short", "bull", "bear"]):
        endpoints.append({"endpoint": "GET /v2/signal", "description": "Current oracle signals with track record"})
    if any(w in q for w in ["price", "btc", "eth", "sol", "bitcoin", "ethereum", "solana", "nvda", "tsla", "aapl"]):
        endpoints.append({"endpoint": "GET /v2/prices", "description": "Live price snapshots with 24h change"})
    if any(w in q for w in ["sentiment", "mood", "fear", "greed", "bullish", "bearish"]):
        endpoints.append({"endpoint": "GET /v2/sentiment", "description": "AI sentiment scores -100 to +100 per asset"})
    if any(w in q for w in ["polymarket", "prediction", "market", "bet", "ev", "probability"]):
        endpoints.append({"endpoint": "GET /v2/polymarket", "description": "OctoBoto positions with EV scores"})
    if any(w in q for w in ["brief", "summary", "context", "system prompt", "inject", "overview"]):
        endpoints.append({"endpoint": "GET /v2/brief", "description": "One-paragraph brief for LLM system prompt injection"})
    if any(w in q for w in ["all", "everything", "full", "complete", "batch"]):
        endpoints.append({"endpoint": "GET /v2/all", "description": "All data in one call"})
    # Default: always suggest brief as entry point
    if not endpoints:
        endpoints.append({"endpoint": "GET /v2/brief", "description": "Start here — one-paragraph market context for any agent"})
        endpoints.append({"endpoint": "GET /v2/demo", "description": "Live demo, no key required"})
    return endpoints[:3]


@app.post("/v2/ask", tags=["Agent Data v2"])
async def v2_ask(
    request: Request,
    q:       str = Query(...,  description="Your question for Octodamus"),
    context: str = Query("",  description="Optional: describe your agent or use case (helps tailor the answer)"),
    api_key: str = Security(API_KEY_HEADER),
):
    """
    **Talk to Octodamus directly — no API key required to try.**

    Ask any market question. Octodamus responds grounded in live data and will point
    you to the exact API endpoints that automate what you're asking.

    Free: 20 questions/day per IP. With a key: 200/day.

    **Example questions:**
    - "What's your current read on BTC?"
    - "Do you have any open signals right now?"
    - "What Polymarket positions are you holding?"
    - "How do I inject live market context into my agent's system prompt?"
    - "What's the Fear & Greed index right now?"
    - "Which endpoint should I use if I just want a one-line market summary?"

    Get a free key: `POST https://api.octodamus.com/v1/signup?email=your@email.com`
    """
    if not q or not q.strip():
        raise HTTPException(
            status_code=400,
            detail={"error_code": "EMPTY_QUESTION", "message": "q parameter cannot be empty."},
        )

    q_clean = q.strip()[:500]  # cap question length

    # Rate limiting — IP-based for no-key, key-based for authenticated
    client_ip = request.client.host if request.client else "unknown"
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")

    if not anthropic_key:
        raise HTTPException(
            status_code=503,
            detail={"error_code": "SERVICE_UNAVAILABLE", "message": "AI service temporarily unavailable."},
        )

    if api_key:
        entry = validate_key(api_key)
        if not entry:
            raise HTTPException(
                status_code=403,
                detail={"error_code": "INVALID_KEY", "message": "Invalid API key.",
                        "get_key": "POST https://api.octodamus.com/v1/signup?email=your@email.com"},
            )
        rl_key   = api_key
        rl_limit = _ASK_KEY_LIMIT
        tier     = entry.get("tier", "basic")
    else:
        rl_key   = f"ask_ip_{client_ip}"
        rl_limit = _ASK_FREE_LIMIT
        tier     = "anonymous"

    today = date.today()
    with _ask_ip_lock:
        global _ask_ip_date
        if today != _ask_ip_date:
            _ask_ip_counts.clear()
            _ask_ip_date = today
        used = _ask_ip_counts[rl_key]
        if used >= rl_limit:
            raise HTTPException(
                status_code=429,
                detail={
                    "error_code":  "RATE_LIMITED",
                    "message":     f"Daily limit of {rl_limit} questions reached.",
                    "remaining":   0,
                    "resets_at":   (datetime.utcnow().replace(hour=0, minute=0, second=0) + timedelta(days=1)).isoformat() + "Z",
                    "get_key":     "POST https://api.octodamus.com/v1/signup?email=your@email.com" if tier == "anonymous" else None,
                    "upgrade":     "https://octodamus.com/api#pricing" if tier == "basic" else None,
                },
            )
        _ask_ip_counts[rl_key] += 1
        remaining = rl_limit - used - 1

    # Build live context
    market_ctx, signal_ctx, poly_ctx = _build_ask_context()

    system = _OCTO_ASK_SYSTEM.format(
        market_context=market_ctx,
        signal_context=signal_ctx,
        poly_context=poly_ctx,
    )

    user_msg = q_clean
    if context.strip():
        user_msg = f"[Agent context: {context.strip()[:200]}]\n\n{q_clean}"

    # Call Claude
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key":          anthropic_key,
                    "anthropic-version":  "2023-06-01",
                    "content-type":       "application/json",
                },
                json={
                    "model":      "claude-haiku-4-5-20251001",
                    "max_tokens": 300,
                    "system":     system,
                    "messages":   [{"role": "user", "content": user_msg}],
                },
            )
        if r.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail={"error_code": "AI_ERROR", "message": f"AI service returned {r.status_code}."},
            )
        answer = r.json()["content"][0]["text"].strip()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail={"error_code": "AI_ERROR", "message": str(e)},
        )

    suggested = _map_question_to_endpoints(q_clean)

    return JSONResponse(
        content={
            "answer":              answer,
            "suggested_endpoints": suggested,
            "get_free_key":        "POST https://api.octodamus.com/v1/signup?email=your@email.com",
            "upgrade_to_premium":  "POST https://api.octodamus.com/v1/agent-checkout?product=premium_annual — $29 USDC on Base",
            "questions_remaining": remaining,
            "tier":                tier,
            "timestamp":           datetime.utcnow().isoformat(),
            "by":                  "Octodamus (@octodamusai) — api.octodamus.com",
        },
        headers={
            "X-Ask-Remaining":   str(remaining),
            "X-Ask-Limit":       str(rl_limit),
            "X-OctoData-Upgrade": "https://octodamus.com/api#pricing",
        },
    )


# -- V2 Demo + Data Sources (no auth) ----------------------------------------

@app.get("/v2/demo", tags=["Agent Data v2"])
def v2_demo():
    """
    **Public demo — no API key required.**

    Live sample of every v2 endpoint. Basic fields shown for all; Premium fields
    are present but redacted with `"[premium]"` so agents can see the schema.

    Get a free key: `POST https://api.octodamus.com/v1/signup?email=your@email.com`
    """
    calls      = _load_calls()
    open_calls = [c for c in calls if not c.get("resolved")]
    stats      = _call_stats(calls)
    _w = stats.get("wins", 0) or 0
    _l = stats.get("losses", 0) or 0
    track      = {
        "wins": _w, "losses": _l, "total": stats.get("total", 0),
        "win_rate": round(_w / (_w + _l) * 100, 1) if (_w + _l) else None,
    }

    # --- Signal ---
    def _signal_demo(c):
        return {
            "asset":        c.get("asset", ""),
            "direction":    c.get("direction", ""),
            "timeframe":    c.get("timeframe", ""),
            "opened_at":    c.get("opened_at", ""),
            "confidence":   "[premium]",
            "entry_price":  "[premium]",
            "target_price": "[premium]",
            "reasoning":    "[premium]",
        }

    signal_demo = {
        "signal":    _signal_demo(open_calls[-1]) if open_calls else None,
        "more_signals": max(0, len(open_calls) - 1),
        "track_record": track,
        "note":      "Basic returns top signal only. Premium returns all signals with confidence + reasoning.",
    }

    # --- Polymarket ---
    boto = _load_boto_trades()
    pos  = boto.get("positions", [])
    closed = boto.get("closed", [])
    poly_demo = {
        "top_play": ({"question": pos[0].get("question", ""), "side": pos[0].get("side", ""),
                      "url": pos[0].get("url", ""), "opened_at": pos[0].get("opened_at", ""),
                      "entry_price": "[premium]", "true_p": "[premium]",
                      "ev": "[premium]", "size_usd": "[premium]"} if pos else None),
        "total_plays":  len(pos),
        "track_record": {"wins": len([t for t in closed if t.get("won")]),
                         "losses": len([t for t in closed if not t.get("won")]),
                         "closed": len(closed)},
        "note": "Basic returns top play only. Premium returns all plays with EV, true_p, Kelly size.",
    }

    # --- Sentiment ---
    sentiment_demo = {}
    try:
        # Try recent snapshot first, then fall back to any snapshot on disk
        s = None
        for i in range(90):
            d = date.today() - timedelta(days=i)
            p = DATA_DIR / str(d) / "sentiment.json"
            if p.exists():
                s = json.loads(p.read_text(encoding="utf-8"))
                break
        if s:
            syms = s.get("symbols", {})
            btc  = syms.get("BTC", {})
            sentiment_demo = {
                "BTC":        btc,
                "more_assets": [k for k in syms if k != "BTC"],
                "note":       "Basic returns BTC only. Premium adds ETH, SOL, NVDA, TSLA, AAPL.",
                "timestamp":  s.get("timestamp"),
            }
        else:
            sentiment_demo = {
                "BTC": {"score": 0, "label": "NEUTRAL", "confidence": "MEDIUM",
                        "summary": "Live sentiment scoring active — snapshot populates nightly."},
                "more_assets": ["ETH", "SOL", "NVDA", "TSLA", "AAPL"],
                "note": "Basic returns BTC only. Premium adds ETH, SOL, NVDA, TSLA, AAPL.",
                "timestamp": datetime.utcnow().isoformat(),
            }
    except Exception:
        sentiment_demo = {
            "BTC": {"score": 0, "label": "NEUTRAL", "confidence": "MEDIUM",
                    "summary": "Live sentiment scoring active — snapshot populates nightly."},
            "note": "Basic returns BTC only. Premium adds ETH, SOL, NVDA, TSLA, AAPL.",
        }

    # --- Prices (live from CoinGecko, fallback to most recent snapshot) ---
    prices_demo = {}
    try:
        import httpx as _httpx
        _r = _httpx.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "bitcoin,ethereum,solana", "vs_currencies": "usd",
                    "include_24hr_change": "true"},
            timeout=6,
        )
        if _r.status_code == 200:
            _d = _r.json()
            live_prices = {
                "BTC": {"price": round(_d.get("bitcoin", {}).get("usd", 0)),
                        "change_24h": round(_d.get("bitcoin", {}).get("usd_24h_change", 0), 2)},
                "ETH": {"price": round(_d.get("ethereum", {}).get("usd", 0), 2),
                        "change_24h": round(_d.get("ethereum", {}).get("usd_24h_change", 0), 2)},
                "SOL": {"price": round(_d.get("solana", {}).get("usd", 0), 2),
                        "change_24h": round(_d.get("solana", {}).get("usd_24h_change", 0), 2)},
            }
            prices_demo = {
                "prices":         live_prices,
                "premium_assets": {"NVDA": "[premium]", "TSLA": "[premium]", "AAPL": "[premium]"},
                "note":           "Basic includes BTC, ETH, SOL. Premium adds NVDA, TSLA, AAPL.",
                "timestamp":      datetime.utcnow().isoformat(),
                "source":         "live",
            }
        else:
            raise ValueError("CoinGecko non-200")
    except Exception:
        # Fall back to most recent snapshot on disk
        try:
            s = None
            for i in range(90):
                d = date.today() - timedelta(days=i)
                p = DATA_DIR / str(d) / "prices.json"
                if p.exists():
                    s = json.loads(p.read_text(encoding="utf-8"))
                    break
            if s:
                data = s.get("data", {})
                basic_data   = {k: v for k, v in data.items() if k.upper() in {"BTC", "ETH", "SOL"}}
                premium_keys = [k for k in data if k.upper() not in {"BTC", "ETH", "SOL"}]
                prices_demo = {
                    "prices":         basic_data,
                    "premium_assets": {k: "[premium]" for k in premium_keys},
                    "note":           "Basic includes BTC, ETH, SOL. Premium adds NVDA, TSLA, AAPL.",
                    "timestamp":      s.get("timestamp"),
                    "source":         "cached",
                }
            else:
                prices_demo = {
                    "prices":    {"BTC": {"price": 0}, "ETH": {"price": 0}, "SOL": {"price": 0}},
                    "note":      "Price feed initializing — live data loads on first nightly run.",
                    "timestamp": datetime.utcnow().isoformat(),
                }
        except Exception:
            prices_demo = {"note": "Price feed initializing."}

    # --- Brief ---
    briefing_text = ""
    try:
        b  = load_snapshot("briefing")
        bd = b.get("briefing", {})
        briefing_text = bd.get("summary", "") or bd.get("mood", "")
    except Exception:
        pass
    parts = []
    if open_calls:
        c   = open_calls[-1]
        sig = c.get("asset", "") + " " + c.get("direction", "") + " " + c.get("timeframe", "")
        resolved = (stats.get("wins") or 0) + (stats.get("losses") or 0)
        if resolved >= _MIN_CALLS_FOR_WINRATE:
            parts.append("Oracle top signal: " + sig + ". Record: " + str(stats["win_rate"]) + "% WR.")
        else:
            parts.append("Oracle top signal: " + sig + ". Methodology: 9/11 signal consensus required.")
    brief_demo = {
        "brief":           " ".join(parts) or "No market data available yet.",
        "full_brief":      "[premium]",
        "polymarket_note": "[premium]",
        "note":            "Basic returns top signal sentence. Premium adds AI market mood + Polymarket context.",
    }

    return {
        "demo":     True,
        "signal":   signal_demo,
        "polymarket": poly_demo,
        "sentiment": sentiment_demo,
        "prices":   prices_demo,
        "brief":    brief_demo,
        "timestamp": datetime.utcnow().isoformat(),
        "get_key":  "POST https://api.octodamus.com/v1/signup?email=your@email.com",
        "upgrade":  "https://octodamus.com/api#pricing",
        "x402": {
            "pay_per_call":  "$0.01 USDC on Base — no key, no account",
            "annual_access": "$29.00 USDC on Base — 365 days, 10k req/day",
            "endpoint":      "GET https://api.octodamus.com/v2/x402/agent-signal",
            "how":           "Send PAYMENT-SIGNATURE header with EIP-3009 USDC authorization",
            "discovery":     "https://api.octodamus.com/.well-known/x402.json",
            "network":       "Base (eip155:8453)",
            "pay_to":        _X402_TREASURY,
        },
        "source": {
            "name":   "OctoData API",
            "by":     "Octodamus (@octodamusai)",
            "docs":   "https://api.octodamus.com/docs",
            "llms":   "https://octodamus.com/llms.txt",
        },
    }


@app.get("/demo", include_in_schema=False)
def demo_preview():
    """Human-readable HTML preview of live Octodamus signals. No key required."""
    # --- Live prices ---
    prices = {}
    fg_val, fg_label = None, None
    try:
        import httpx as _hx
        _r = _hx.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "bitcoin,ethereum,solana", "vs_currencies": "usd",
                    "include_24hr_change": "true"},
            timeout=6,
        )
        if _r.status_code == 200:
            _d = _r.json()
            prices = {
                "BTC": {"price": round(_d["bitcoin"]["usd"]),
                        "chg":  round(_d["bitcoin"].get("usd_24h_change", 0), 2)},
                "ETH": {"price": round(_d["ethereum"]["usd"], 2),
                        "chg":  round(_d["ethereum"].get("usd_24h_change", 0), 2)},
                "SOL": {"price": round(_d["solana"]["usd"], 2),
                        "chg":  round(_d["solana"].get("usd_24h_change", 0), 2)},
            }
    except Exception:
        pass
    try:
        import httpx as _hx2
        _fg = _hx2.get("https://api.alternative.me/fng/?limit=1", timeout=4)
        if _fg.status_code == 200:
            _fgd = _fg.json()["data"][0]
            fg_val   = _fgd.get("value")
            fg_label = _fgd.get("value_classification", "")
    except Exception:
        pass

    # --- Oracle signal ---
    calls      = _load_calls()
    open_calls = [c for c in calls if not c.get("resolved")]
    stats      = _call_stats(calls)
    top_call   = open_calls[-1] if open_calls else None

    # --- Polymarket ---
    boto = _load_boto_trades()
    pos  = boto.get("positions", [])
    closed = boto.get("closed", [])
    top_play = pos[0] if pos else None
    wins   = len([t for t in closed if t.get("won")])
    losses = len([t for t in closed if not t.get("won")])
    wr_str = f"{round(wins/(wins+losses)*100)}% win rate" if (wins+losses) > 0 else "tracking live"

    def chg_color(v):
        if v is None: return "#888"
        return "#00ff88" if v >= 0 else "#ff4d4d"

    def chg_str(v):
        if v is None: return ""
        return f"+{v}%" if v >= 0 else f"{v}%"

    def dir_color(d):
        return "#00ff88" if d == "UP" else "#ff4d4d" if d == "DOWN" else "#f0c040"

    btc = prices.get("BTC", {})
    eth = prices.get("ETH", {})
    sol = prices.get("SOL", {})

    price_rows = ""
    for sym, label in [("BTC", "Bitcoin"), ("ETH", "Ethereum"), ("SOL", "Solana")]:
        p = prices.get(sym, {})
        clr = chg_color(p.get("chg"))
        price_rows += f"""
        <tr>
          <td><span class="sym">{sym}</span> <span class="asset-name">{label}</span></td>
          <td class="num">${p.get('price', '--'):,}</td>
          <td class="num" style="color:{clr}">{chg_str(p.get('chg'))}</td>
        </tr>"""

    signal_html = ""
    if top_call:
        d_col = dir_color(top_call.get("direction", ""))
        signal_html = f"""
        <div class="card">
          <div class="card-label">Active Oracle Signal</div>
          <div class="signal-row">
            <span class="sym">{top_call.get('asset','')}</span>
            <span class="direction" style="color:{d_col}">{top_call.get('direction','')}</span>
            <span class="tf">{top_call.get('timeframe','')}</span>
          </div>
          <div class="signal-meta">9/11 oracle consensus required &bull; confidence: <span class="locked">Premium</span></div>
          <div class="signal-meta">Reasoning: <span class="locked">Premium</span></div>
        </div>"""
    else:
        signal_html = '<div class="card"><div class="card-label">Oracle Signal</div><div class="muted">No open signal at this time.</div></div>'

    poly_html = ""
    if top_play:
        poly_html = f"""
        <div class="card">
          <div class="card-label">Top Polymarket Edge Play</div>
          <div class="poly-q">{top_play.get('question','')}</div>
          <div class="poly-meta">
            Side: <strong>{top_play.get('side','')}</strong> &bull;
            EV: <span class="locked">Premium</span> &bull;
            Kelly size: <span class="locked">Premium</span>
          </div>
          <div class="poly-track">Track record: {wins}W / {losses}L &mdash; {wr_str} (paper)</div>
        </div>"""

    fg_html = ""
    if fg_val:
        fg_clr = "#ff4d4d" if int(fg_val) < 30 else "#f0c040" if int(fg_val) < 55 else "#00ff88"
        fg_html = f'<div class="fg-pill" style="border-color:{fg_clr};color:{fg_clr}">{fg_val} &mdash; {fg_label}</div>'

    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Octodamus — Live Signal Preview</title>
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Syne:wght@400;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:#0a0a0f;color:#e8e8e8;font-family:'Syne',sans-serif;min-height:100vh;padding:32px 16px}}
  .wrap{{max-width:720px;margin:0 auto}}
  .logo{{font-family:'Bebas Neue',sans-serif;font-size:2.4rem;letter-spacing:3px;color:#fff;margin-bottom:4px}}
  .logo span{{color:#00ff88}}
  .tagline{{color:#888;font-size:.85rem;margin-bottom:32px;font-family:'JetBrains Mono',monospace}}
  .badge{{display:inline-block;background:#00ff8822;color:#00ff88;border:1px solid #00ff8866;border-radius:4px;font-size:.7rem;font-family:'JetBrains Mono',monospace;padding:2px 8px;margin-bottom:24px}}
  .section-label{{font-family:'Bebas Neue',sans-serif;font-size:1rem;letter-spacing:2px;color:#888;margin:28px 0 12px}}
  .card{{background:#12121a;border:1px solid #1e1e2e;border-radius:8px;padding:20px;margin-bottom:16px}}
  .card-label{{font-family:'Bebas Neue',sans-serif;letter-spacing:2px;font-size:.85rem;color:#555;margin-bottom:12px}}
  table{{width:100%;border-collapse:collapse}}
  td{{padding:8px 4px;border-bottom:1px solid #1a1a2a;font-family:'JetBrains Mono',monospace;font-size:.9rem}}
  tr:last-child td{{border-bottom:none}}
  .sym{{font-family:'Bebas Neue',sans-serif;font-size:1.1rem;letter-spacing:1px;color:#fff}}
  .asset-name{{color:#555;font-size:.75rem;font-family:'Syne',sans-serif}}
  .num{{text-align:right}}
  .signal-row{{display:flex;align-items:center;gap:16px;margin-bottom:8px}}
  .direction{{font-family:'Bebas Neue',sans-serif;font-size:1.6rem;letter-spacing:2px}}
  .tf{{font-family:'JetBrains Mono',monospace;font-size:.8rem;color:#888;background:#1e1e2e;padding:2px 8px;border-radius:4px}}
  .signal-meta{{font-size:.8rem;color:#666;font-family:'JetBrains Mono',monospace;margin-top:6px}}
  .locked{{color:#f0c040;font-style:italic}}
  .poly-q{{font-size:.95rem;color:#ddd;margin-bottom:10px;line-height:1.4}}
  .poly-meta{{font-size:.8rem;color:#888;font-family:'JetBrains Mono',monospace;margin-bottom:6px}}
  .poly-track{{font-size:.8rem;color:#555;font-family:'JetBrains Mono',monospace}}
  .fg-pill{{display:inline-block;border:1px solid;border-radius:100px;padding:4px 16px;font-family:'Bebas Neue',sans-serif;letter-spacing:2px;font-size:1rem;margin-bottom:8px}}
  .muted{{color:#555;font-size:.85rem;font-family:'JetBrains Mono',monospace}}
  .cta-row{{margin-top:36px;display:flex;flex-wrap:wrap;gap:12px}}
  .btn{{display:inline-block;padding:10px 24px;border-radius:6px;font-family:'Bebas Neue',sans-serif;letter-spacing:2px;font-size:.95rem;text-decoration:none}}
  .btn-primary{{background:#00ff88;color:#000}}
  .btn-secondary{{background:transparent;border:1px solid #333;color:#aaa}}
  .footer{{margin-top:40px;padding-top:20px;border-top:1px solid #1a1a2a;color:#444;font-size:.75rem;font-family:'JetBrains Mono',monospace}}
  .footer a{{color:#555;text-decoration:none}}
  .json-link{{font-family:'JetBrains Mono',monospace;font-size:.75rem;color:#444;margin-top:20px}}
  .json-link a{{color:#555}}
</style>
</head>
<body>
<div class="wrap">
  <div class="logo">OCTO<span>DAMUS</span></div>
  <div class="tagline">AI Market Oracle &mdash; Live Signal Preview &mdash; {now_str}</div>
  <div class="badge">LIVE DATA &mdash; NO KEY REQUIRED</div>

  <div class="section-label">Spot Prices</div>
  <div class="card">
    <div class="card-label">BTC / ETH / SOL &mdash; Live</div>
    <table>{price_rows}</table>
    {"" if not fg_html else f'<div style="margin-top:14px"><div class="card-label">Fear &amp; Greed Index</div>{fg_html}</div>'}
  </div>

  {signal_html}

  {poly_html if poly_html else ""}

  <div class="card">
    <div class="card-label">What Premium Unlocks</div>
    <table>
      <tr><td>Oracle signal confidence score (0.0-1.0)</td><td class="num locked">Premium</td></tr>
      <tr><td>AI reasoning per signal</td><td class="num locked">Premium</td></tr>
      <tr><td>All open signals (BTC, ETH, SOL, HYPE)</td><td class="num locked">Premium</td></tr>
      <tr><td>Polymarket EV + Kelly sizing</td><td class="num locked">Premium</td></tr>
      <tr><td>Funding rates + open interest</td><td class="num locked">Premium</td></tr>
      <tr><td>CME institutional positioning</td><td class="num locked">Premium</td></tr>
      <tr><td>AI sentiment scores (-100 to +100)</td><td class="num locked">Premium</td></tr>
    </table>
  </div>

  <div class="cta-row">
    <a class="btn btn-primary" href="https://api.octodamus.com/v1/signup?email=">Get Free Key (500 req/day)</a>
    <a class="btn btn-secondary" href="https://api.octodamus.com/docs">API Docs</a>
    <a class="btn btn-secondary" href="https://octodamus.com/api#pricing">Pricing</a>
  </div>

  <div class="json-link">Machine-readable version: <a href="https://api.octodamus.com/v2/demo">/v2/demo</a> (JSON, no key required)</div>

  <div class="footer">
    Octodamus Market Intelligence &mdash; <a href="https://octodamus.com">octodamus.com</a> &mdash; <a href="https://x.com/octodamusai">@octodamusai</a><br>
    Data sources: CoinGecko, Coinglass, Polymarket, CFTC COT, FRED, Alternative.me<br>
    9/11 oracle consensus system &mdash; directional calls require supermajority agreement
  </div>
</div>
</body>
</html>"""
    return HTMLResponse(content=html)


@app.get("/v2/sources", tags=["Agent Data v2"])
def v2_sources():
    """
    **Data sources — no API key required.**

    Every data feed powering each OctoData endpoint. For agent trust verification
    and due diligence. All sources are live; no synthetic or simulated data.
    """
    return {
        "sources": {
            "/v2/signal": {
                "description": "Oracle trading signals — 9/11 system consensus required to publish",
                "feeds": [
                    {"name": "CoinGlass",         "data": "BTC/ETH open interest, funding rates, liquidation levels"},
                    {"name": "Deribit",            "data": "Options implied volatility, put/call ratio, max pain"},
                    {"name": "TradingView",        "data": "Technical indicator consensus (RSI, MACD, Bollinger)"},
                    {"name": "Alternative.me",     "data": "Crypto Fear & Greed Index"},
                    {"name": "CoinGecko",          "data": "BTC/ETH 24h price change, volume, dominance"},
                    {"name": "Kraken OHLC",        "data": "Real-time BTC/USD candlestick data"},
                    {"name": "Binance",            "data": "ETH/BTC spot price, volume"},
                    {"name": "Whale Alert",        "data": "Large on-chain transfers (>$1M)"},
                    {"name": "Polymarket",         "data": "Prediction market probabilities for macro events"},
                    {"name": "FRED / TradingEcon", "data": "US macro: CPI, unemployment, Fed rate expectations"},
                    {"name": "NewsAPI",            "data": "Crypto and macro news sentiment scoring"},
                ],
                "consensus_rule": "Signal published only when >= 9 of 11 systems agree on direction",
                "resolution":     "Calls resolved at target timeframe close (daily, weekly, monthly)",
            },
            "/v2/polymarket": {
                "description": "OctoBoto prediction market positions — live paper trading",
                "feeds": [
                    {"name": "Polymarket",     "data": "Market prices, liquidity, resolution dates"},
                    {"name": "Claude AI",      "data": "AI true probability estimates vs market price"},
                    {"name": "Kelly Criterion","data": "Position sizing based on EV and bankroll"},
                ],
                "entry_rule":  "EV > 15% required for position entry",
                "mode":        "Paper trading — live tracking, not real money",
            },
            "/v2/sentiment": {
                "description": "AI sentiment scores per asset (-100 bearish to +100 bullish)",
                "feeds": [
                    {"name": "NewsAPI",        "data": "Last 24h news articles per asset, sentiment scored"},
                    {"name": "CoinGecko",      "data": "Price momentum, volume anomalies"},
                    {"name": "Alternative.me", "data": "Fear & Greed Index as macro sentiment anchor"},
                    {"name": "Claude AI",      "data": "Aggregates all signals into -100 to +100 score"},
                ],
                "update_frequency": "Nightly (00:30 UTC)",
                "assets_basic":     ["BTC"],
                "assets_premium":   ["BTC", "ETH", "SOL", "NVDA", "TSLA", "AAPL"],
            },
            "/v2/prices": {
                "description": "Latest asset price snapshots with 24h change",
                "feeds": [
                    {"name": "CoinGecko",  "data": "BTC, ETH, SOL price in USD, 24h change, market cap"},
                    {"name": "Binance",    "data": "Cross-check for crypto price accuracy"},
                    {"name": "yfinance",   "data": "NVDA, TSLA, AAPL stock prices (premium)"},
                ],
                "update_frequency": "Nightly snapshot + live fetch on demand",
                "assets_basic":     ["BTC", "ETH", "SOL"],
                "assets_premium":   ["BTC", "ETH", "SOL", "NVDA", "TSLA", "AAPL"],
            },
            "/v2/brief": {
                "description": "One-paragraph market brief for agent context windows",
                "feeds": [
                    {"name": "All v2 feeds", "data": "Aggregates signal, sentiment, prices, Polymarket"},
                    {"name": "Claude AI",    "data": "Generates human-readable brief from all sources"},
                ],
                "update_frequency": "Nightly full brief. Signal sentence updates in real-time.",
                "basic_output":    "Top signal + win rate sentence",
                "premium_output":  "Full AI market brief + Polymarket context",
            },
        },
        "data_freshness": {
            "signal":     "Real-time — new signal within minutes of 9/11 consensus",
            "polymarket": "Updated when OctoBoto opens/closes positions",
            "sentiment":  "Nightly at 00:30 UTC",
            "prices":     "Nightly snapshot; live on fresh API call",
            "brief":      "Nightly; signal line updates real-time",
        },
        "no_synthetic_data": True,
        "by":       "Octodamus (@octodamusai)",
        "docs":     "https://api.octodamus.com/docs",
        "demo":     "https://api.octodamus.com/v2/demo",
        "signup":   "POST https://api.octodamus.com/v1/signup?email=your@email.com",
        "timestamp": datetime.utcnow().isoformat(),
    }


# -- MCP Server (SSE transport) — mounted at /mcp ----------------------------
# Smithery URL: https://api.octodamus.com/mcp

from mcp.server.fastmcp import FastMCP as _FastMCP
from pydantic import Field as _Field

_mcp = _FastMCP(
    "Octodamus Market Intelligence",
    instructions=(
        "Real-time crypto market intelligence for autonomous AI agents. "
        "Tool guide: "
        "get_agent_signal() — primary BUY/SELL/HOLD decision, poll every 15 min; "
        "get_all_data() — all signals in one call, ideal for session initialisation; "
        "get_polymarket_edge() — ranked Polymarket plays by expected value; "
        "get_sentiment() — per-asset sentiment score (-1 to +1) for BTC/ETH/SOL; "
        "get_prices() — live prices with 24h change; "
        "get_market_brief() — narrative briefing, inject into agent system prompt; "
        "get_oracle_signals() — raw votes from all 11 oracles for deep analysis; "
        "get_data_sources() — list 27 live feeds, no key needed; "
        "buy_premium_api() — subscribe via x402 USDC on Base (trial $5 / annual $29); "
        "buy_guide() — purchase trading system guide via x402. "
        "Get a free API key (500 req/day): POST https://api.octodamus.com/v1/signup?email=YOUR_EMAIL"
    ),
    streamable_http_path="/",
    stateless_http=True,
)

_API_KEY_DESC = (
    "OctoData API key (format: octo_...). "
    "Free key (500 req/day): POST https://api.octodamus.com/v1/signup?email=YOUR_EMAIL. "
    "Premium key (10k req/day): call buy_premium_api() or GET /v1/subscribe?plan=trial."
)

def _mcp_get(path: str, api_key: str, params: dict | None = None) -> dict:
    try:
        r = httpx.get(
            f"https://api.octodamus.com{path}",
            headers={"X-OctoData-Key": api_key},
            params=params or {},
            timeout=15,
        )
        return r.json()
    except Exception as e:
        return {"error": str(e)}

@_mcp.tool()
def get_agent_signal(
    api_key: Annotated[str, _Field(description=_API_KEY_DESC)],
) -> dict:
    """Consolidated trading signal from the 9/11 oracle consensus system.

    Use this as the primary decision endpoint; poll every 15 minutes
    (next_poll_seconds = 900 in the response). For first-call context
    initialisation, prefer get_all_data() instead.

    Response fields:
      action        — "BUY" | "SELL" | "HOLD" | "WATCH"
      confidence    — float 0.0–1.0 (higher = stronger oracle consensus)
      signal        — "BULLISH" | "BEARISH" | "NEUTRAL"
      fear_greed    — int 0–100 (0 = Extreme Fear, 100 = Extreme Greed)
      btc_trend     — "UP" | "DOWN" | "SIDEWAYS"
      polymarket_edge — {market, ev, side} top expected-value play
      reasoning     — plain-text explanation of the consensus
      next_poll_seconds — seconds until the signal refreshes (typically 900)
    """
    return _mcp_get("/v2/agent-signal", api_key)

@_mcp.tool()
def get_polymarket_edge(
    api_key: Annotated[str, _Field(description=_API_KEY_DESC)],
) -> dict:
    """Ranked Polymarket prediction markets by expected value (EV).

    Use when you want to position on prediction markets. Returns a list
    ordered by EV descending; each entry includes question, recommended_side
    ("YES" or "NO"), expected_value (float), and confidence.

    Complement with get_agent_signal() to confirm directional alignment
    before acting on any Polymarket position.
    """
    return _mcp_get("/v2/polymarket", api_key)

@_mcp.tool()
def get_sentiment(
    api_key: Annotated[str, _Field(description=_API_KEY_DESC)],
    symbol: Annotated[str, _Field(
        description='Asset to filter by: "BTC", "ETH", or "SOL". '
                    'Leave empty ("") to get scores for all assets.',
        default="",
    )] = "",
) -> dict:
    """AI-derived sentiment scores for major crypto assets and macro themes.

    Scores range from -1.0 (maximum bearish) to +1.0 (maximum bullish).
    Use to add conviction context to a signal: a BUY action with a high
    positive sentiment score is a stronger setup than one with neutral sentiment.

    Response: dict keyed by asset symbol, each with score, label
    ("Very Bearish" … "Very Bullish"), and source_count.
    """
    path = f"/v2/sentiment/{symbol}" if symbol else "/v2/sentiment"
    return _mcp_get(path, api_key)

@_mcp.tool()
def get_prices(
    api_key: Annotated[str, _Field(description=_API_KEY_DESC)],
) -> dict:
    """Live spot prices with 24-hour percentage change for major crypto assets.

    Use to ground calculations (e.g. position sizing, level checks) before
    acting on a signal. Refreshes every minute. Returns dict keyed by symbol
    with price_usd and change_24h_pct fields.
    """
    return _mcp_get("/v2/prices", api_key)

@_mcp.tool()
def get_market_brief(
    api_key: Annotated[str, _Field(description=_API_KEY_DESC)],
) -> dict:
    """Full AI market briefing as a concise narrative paragraph.

    Ideal for injecting into an agent system prompt at session start to
    ground all subsequent reasoning in current market conditions. Covers
    macro regime, crypto momentum, key levels, and notable catalysts.
    Refreshes every 30 minutes; call once per session rather than polling.

    Response: {brief: "...narrative text..."}
    """
    return _mcp_get("/v2/brief", api_key)

@_mcp.tool()
def get_all_data(
    api_key: Annotated[str, _Field(description=_API_KEY_DESC)],
) -> dict:
    """All signal data in a single call: signal + sentiment + prices + Polymarket edges.

    Use this on session initialisation instead of calling each tool separately.
    Equivalent to get_agent_signal() + get_sentiment() + get_prices() +
    get_polymarket_edge() combined. After initialisation, use get_agent_signal()
    on its 15-minute polling cycle for updates.
    """
    return _mcp_get("/v2/all", api_key)

@_mcp.tool()
def get_oracle_signals(
    api_key: Annotated[str, _Field(description=_API_KEY_DESC)],
) -> dict:
    """Raw individual votes from all 11 oracles with consensus strength and win rate.

    Use for deep analysis or debugging: see how each oracle voted, the
    consensus score, and the historical win rate per oracle. For a simple
    BUY/SELL/HOLD decision use get_agent_signal() instead.

    Response: {oracles: [{name, vote, confidence}], consensus_score, win_rate}
    """
    return _mcp_get("/v2/signal", api_key)

@_mcp.tool()
def get_data_sources() -> dict:
    """List all 27 live data feeds powering the Octodamus oracle system.

    No API key required. Use for transparency or discovery — shows each
    source name, data type, and refresh interval. Useful when explaining
    signal provenance to end users or auditing data coverage.
    """
    try:
        r = httpx.get("https://api.octodamus.com/v2/sources", timeout=15)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

@_mcp.tool()
def buy_guide() -> str:
    """Purchase the Build the House trading system guide via x402 on Base.

    Returns step-by-step x402 payment instructions. After completing the
    EIP-3009 payment ($29 USDC on Base), the API returns a download_url
    valid for 30 days. No API key required to purchase.
    """
    seats = max(0, _EARLY_BIRD_LIMIT - _premium_seat_count())
    price = _EARLY_BIRD_PRICE if seats > 0 else _STANDARD_PRICE
    return "\n".join([
        "BUILD THE HOUSE GUIDE — $29 USDC on Base",
        "",
        "Endpoint:  GET https://api.octodamus.com/v1/guide",
        "Payment:   $29 USDC on Base (eip155:8453) via x402",
        "Treasury:  0x5c6B3a3dAe296d3cef50fef96afC73410959a6Db",
        "USDC:      0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "",
        "Steps:",
        "  1. GET /v1/guide  ->  402 + payment-required header",
        "  2. Sign EIP-3009 authorization for $29 USDC to treasury",
        "  3. Retry with PAYMENT-SIGNATURE header",
        "  4. Receive JSON with download_url (valid 30 days)",
    ])

@_mcp.tool()
def buy_premium_api() -> str:
    """Subscribe to OctoData Premium API via x402 on Base.

    Returns step-by-step x402 payment instructions for any plan.
    After completing the EIP-3009 payment, the API returns an api_key
    immediately — no human in the loop. Free option also available.

    Plans:
      Micro  — $0.01 USDC per call, no key needed, pay-per-request via x402
      Trial  — $5 USDC, 7 days, 10k req/day
      Annual — $29 USDC/year early bird (first 100 seats), $149/year after
    """
    seats = max(0, _EARLY_BIRD_LIMIT - _premium_seat_count())
    price = _EARLY_BIRD_PRICE if seats > 0 else _STANDARD_PRICE
    label = f"Early Bird ({seats} left)" if seats > 0 else "Standard"
    return "\n".join([
        "OCTODATA PREMIUM API — x402 on Base",
        "",
        "Micro:   $0.01 USDC per call — no key needed, pay-per-request via x402 EIP-3009",
        f"Trial:   GET https://api.octodamus.com/v1/subscribe?plan=trial  ->  $5 USDC, 7 days",
        f"Annual:  GET https://api.octodamus.com/v1/subscribe?plan=annual ->  ${price} USDC/yr, 365 days ({label})",
        "",
        "Treasury:  0x5c6B3a3dAe296d3cef50fef96afC73410959a6Db",
        "USDC:      0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "Network:   Base mainnet (eip155:8453)",
        "",
        "Micro-pay steps (no account needed):",
        "  1. GET https://api.octodamus.com/v2/agent-signal  ->  402 + payment-required header",
        "  2. Sign EIP-3009 for $0.01 USDC (amount: 10000) to treasury on Base",
        "  3. Retry with PAYMENT-SIGNATURE header",
        "  4. Response returned directly — no key provisioned",
        "",
        "Subscribe steps (key provisioned):",
        "  1. GET trial/annual endpoint above  ->  402 + payment-required header",
        "  2. Sign EIP-3009 authorization for USDC amount to treasury",
        "  3. Retry with PAYMENT-SIGNATURE header",
        "  4. Receive JSON with api_key",
        "",
        "Free option: POST https://api.octodamus.com/v1/signup?email=YOUR_EMAIL  (500 req/day)",
    ])

import threading as _threading

def _start_mcp_server():
    """Run MCP server on port 8743 in a background thread."""
    import asyncio as _asyncio
    import uvicorn as _uvicorn

    async def _run():
        config = _uvicorn.Config(
            _mcp.streamable_http_app(),
            host="127.0.0.1",
            port=8743,
            log_level="warning",
        )
        server = _uvicorn.Server(config)
        await server.serve()

    _asyncio.run(_run())

_mcp_thread = _threading.Thread(target=_start_mcp_server, daemon=True)
_mcp_thread.start()


_PROXY_HOP_BY_HOP = frozenset({
    "transfer-encoding", "connection", "keep-alive", "proxy-authenticate",
    "proxy-authorization", "te", "trailers", "upgrade", "server", "date",
})

async def _proxy_to_mcp(path: str, request: Request):
    """Shared proxy logic: forward request to MCP server on port 8743."""
    import httpx as _hx
    target = f"http://127.0.0.1:8743/{path}"
    body = await request.body()
    qs = str(request.url.query)
    if qs:
        target = f"{target}?{qs}"
    async with _hx.AsyncClient(follow_redirects=True) as client:
        try:
            r = await client.request(
                method=request.method,
                url=target,
                headers={k: v for k, v in request.headers.items() if k.lower() != "host"},
                content=body,
                timeout=30,
            )
            from fastapi.responses import Response
            fwd_headers = {k: v for k, v in r.headers.items() if k.lower() not in _PROXY_HOP_BY_HOP}
            return Response(
                content=r.content,
                status_code=r.status_code,
                headers=fwd_headers,
                media_type=r.headers.get("content-type"),
            )
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=502)


@app.api_route("/mcp", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
async def mcp_proxy_root(request: Request):
    """Proxy bare /mcp → MCP server root. Prevents FastAPI 307 redirect that breaks Smithery scanner."""
    return await _proxy_to_mcp("", request)


@app.api_route("/mcp/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
async def mcp_proxy(path: str, request: Request):
    """Proxy /mcp/* → local MCP server on port 8743."""
    return await _proxy_to_mcp(path, request)


@app.get("/.well-known/mcp/server-card.json", include_in_schema=False)
def mcp_server_card():
    """Smithery MCP server card — used by the Smithery scanner to verify MCP availability."""
    return {
        "name": "Octodamus Market Intelligence",
        "description": (
            "Real-time crypto market intelligence for AI agents. Oracle trading signals "
            "(9/11 consensus), Fear & Greed, Polymarket edges, BTC trend across 27 live feeds."
        ),
        "url": "https://api.octodamus.com/mcp",
        "transport": ["streamable-http"],
        "version": "1.0",
        "auth": {
            "type": "api_key",
            "in": "query",
            "name": "api_key",
            "signup": "https://api.octodamus.com/v1/signup",
        },
    }


@app.get("/.well-known/oauth-protected-resource", include_in_schema=False)
def oauth_protected_resource():
    """RFC 9728 resource metadata — tells MCP clients this server uses API-key auth, not OAuth."""
    return {
        "resource": "https://api.octodamus.com/mcp",
        "bearer_methods_supported": ["header", "query"],
        "resource_documentation": "https://api.octodamus.com/docs",
    }


# -- llms.txt — machine-readable API index for LLMs --------------------------

_LLMS_TXT = """# Octodamus Market Intelligence API

> Real-time crypto market intelligence for autonomous AI agents. Oracle trading signals (9/11 consensus), Fear & Greed index, Polymarket prediction market edge plays, BTC trend, and macro sentiment across 27 live data feeds. Designed for 15-minute AI agent poll cycles. x402 native: agents pay $29 USDC on Base, receive an API key automatically — no human required.

## Quick Start

- [API Documentation](https://api.octodamus.com/docs): Interactive Swagger docs — all endpoints, schemas, and try-it-now
- [Agent Signal](https://api.octodamus.com/v2/agent-signal): Primary decision endpoint (requires API key or x402 payment)
- [Demo (no key)](https://api.octodamus.com/v2/demo): Public preview of signal output — no key required
- [Data Sources](https://api.octodamus.com/v2/sources): Full list of all 27 live data feeds powering every endpoint
- [Get Free Key](https://api.octodamus.com/v1/signup): POST with your email for free Basic tier (500 req/day)
- [Agent Checkout](https://api.octodamus.com/v1/agent-checkout): Autonomous x402 purchase — $29 USDC for annual access

## Core Signal Endpoints (require X-OctoData-Key header)

- [GET /v2/agent-signal](https://api.octodamus.com/v2/agent-signal): **Primary endpoint for agents.** Returns `action` (BUY/SELL/HOLD), `confidence` (0.0–1.0), `signal` (BULLISH/BEARISH/NEUTRAL), `fear_greed` (0–100), `btc_trend` (UP/DOWN/SIDEWAYS), `polymarket_edge` {market, ev}, `reasoning` (plain-text explanation). Poll every 15 minutes.
- [GET /v2/signal](https://api.octodamus.com/v2/signal): Raw Oracle signal pack — individual votes from all 11 oracles, consensus strength, win rate, and timestamp
- [GET /v2/polymarket](https://api.octodamus.com/v2/polymarket): Top Polymarket prediction markets with expected-value scoring. Returns `ev`, `edge`, `recommended_side`, and `confidence` per market.
- [GET /v2/sentiment](https://api.octodamus.com/v2/sentiment): AI sentiment scores for BTC, ETH, SOL, and macro themes (score –1.0 to +1.0, label, summary)
- [GET /v2/prices](https://api.octodamus.com/v2/prices): Current crypto prices with 24h % change for major assets
- [GET /v2/brief](https://api.octodamus.com/v2/brief): Full AI market briefing in narrative format — ideal for agent reasoning context
- [GET /v2/all](https://api.octodamus.com/v2/all): Combined snapshot of signal + sentiment + prices + Polymarket in one call

## Key Management

- [GET /v1/key/status](https://api.octodamus.com/v1/key/status): Check expiry, tier, and usage for your key — also returns renewal x402 payment details when within 30 days of expiry
- [POST /v1/signup](https://api.octodamus.com/v1/signup): Create free Basic API key. Params: `email` (query or body)
- [POST /v1/agent-checkout](https://api.octodamus.com/v1/agent-checkout): Initiate x402 checkout. Params: `product` (premium_annual | premium_trial), `agent_wallet` (your 0x address)
- [GET /v1/agent-checkout/status](https://api.octodamus.com/v1/agent-checkout/status): Poll payment status. Returns `api_key` once payment is confirmed on-chain.

## Pricing

- **Free Basic**: 500 req/day, 20 req/min — email signup, no payment
- **Premium Annual**: $29 USDC on Base, 10,000 req/day, 200 req/min, 365-day access
- **Trial**: $5 USDC on Base, 10,000 req/day, 7-day access
- **x402 chain**: eip155:8453 (Base mainnet)
- **Pay to**: 0x5c6B3a3dAe296d3cef50fef96afC73410959a6Db
- **USDC contract**: 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913

## Agent Integration Notes

Authentication: `X-OctoData-Key: <your-key>` header on all v1/v2 endpoints.
x402: All v2 endpoints return HTTP 402 with `payment-required` (base64 JSON, v2) and `X-Payment-Required` (plain JSON, v1) when no key is present.
ERC-8004: Registered on Agent Arena. globalId = eip155:8453:0x8004A169FB4a3325136EB29fA0ceB6D2e539a432#44306
A2A Card: https://agentarena.site/api/agent/8453/44306/a2a
""".strip()


@app.get("/llms.txt", include_in_schema=False)
def llms_txt():
    """Machine-readable API index for LLMs (llmstxt.org standard)."""
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(_LLMS_TXT, media_type="text/plain; charset=utf-8")


# -- Agent framework tool schemas ---------------------------------------------

_TOOL_SCHEMAS = {
    "langchain": {
        "description": "LangChain tool definition for OctoData agent-signal",
        "code": '''from langchain.tools import tool
import httpx

@tool
def get_market_signal(query: str = "") -> str:
    """Get the current crypto market signal from Octodamus Oracle.
    Returns BUY/SELL/HOLD action, confidence, Fear & Greed index,
    BTC trend, and top Polymarket edge play. Poll every 15 minutes."""
    r = httpx.get(
        "https://api.octodamus.com/v2/agent-signal",
        headers={"X-OctoData-Key": OCTO_KEY},
        timeout=10,
    )
    d = r.json()
    return (
        f"Action: {d['action']} | Confidence: {d['confidence']} | "
        f"F&G: {d['fear_greed'].get('value')} ({d['fear_greed'].get('label')}) | "
        f"BTC: {d['btc'].get('trend')} | Reasoning: {d['reasoning']}"
    )

@tool
def ask_octodamus(question: str) -> str:
    """Ask Octodamus any market question. Grounded in live signals,
    prices, and Polymarket data. Free: 100 questions/day."""
    r = httpx.post(
        f"https://api.octodamus.com/v2/ask?q={question}",
        timeout=15,
    )
    return r.json().get("answer", "No answer")
''',
    },
    "crewai": {
        "description": "CrewAI tool definition for OctoData",
        "code": '''from crewai_tools import tool
import httpx

@tool("Octodamus Market Signal")
def octodamus_signal(action: str = "get") -> str:
    """Fetch current crypto market signal from Octodamus Oracle.
    Returns BUY/SELL/HOLD with confidence, Fear & Greed, BTC trend,
    and Polymarket edge plays. Use every 15 minutes for fresh data."""
    r = httpx.get(
        "https://api.octodamus.com/v2/agent-signal",
        headers={"X-OctoData-Key": OCTO_KEY},
        timeout=10,
    )
    return r.json()

@tool("Octodamus Market Ask")
def octodamus_ask(question: str) -> str:
    """Ask Octodamus a market question. Returns AI answer grounded
    in live prices, oracle signals, and Polymarket positions."""
    r = httpx.post(f"https://api.octodamus.com/v2/ask?q={question}", timeout=15)
    return r.json().get("answer", "")
''',
    },
    "openai_functions": {
        "description": "OpenAI function calling / tool use definitions",
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "get_market_signal",
                    "description": "Get current crypto market signal from Octodamus Oracle. Returns BUY/SELL/HOLD action, confidence level, Fear & Greed index, BTC trend, and top Polymarket edge play. Call every 15 minutes for fresh intelligence.",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "ask_octodamus",
                    "description": "Ask Octodamus any market question. Returns AI answer grounded in live prices, oracle signals, and Polymarket data. Free tier: 100 questions/day.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "question": {"type": "string", "description": "Your market question, e.g. 'What is your read on BTC right now?' or 'Any open oracle signals?'"},
                        },
                        "required": ["question"],
                    },
                },
            },
        ],
    },
    "autogen": {
        "description": "AutoGen tool registration for OctoData",
        "code": '''import httpx
from autogen import AssistantAgent, UserProxyAgent

def get_market_signal() -> dict:
    """Get Octodamus Oracle signal: BUY/SELL/HOLD, F&G, BTC trend, Polymarket edges."""
    return httpx.get(
        "https://api.octodamus.com/v2/agent-signal",
        headers={"X-OctoData-Key": OCTO_KEY},
        timeout=10,
    ).json()

def ask_octodamus(question: str) -> str:
    """Ask Octodamus a market question. Free, no key needed."""
    return httpx.post(
        f"https://api.octodamus.com/v2/ask?q={question}",
        timeout=15,
    ).json().get("answer", "")

# Register with AutoGen agent
assistant = AssistantAgent(
    name="octodamus_agent",
    system_message="You are a crypto trading assistant with access to Octodamus market intelligence.",
)
assistant.register_function({"get_market_signal": get_market_signal, "ask_octodamus": ask_octodamus})
''',
    },
}


@app.get("/v2/tools", tags=["Agent Integration"], include_in_schema=True)
def agent_tool_schemas(framework: str = ""):
    """
    Ready-made tool definitions for AI agent frameworks.

    Pass `?framework=` to get code for a specific framework:
    - `langchain` — @tool decorator definitions
    - `crewai` — CrewAI tool definitions
    - `openai_functions` — OpenAI function calling JSON
    - `autogen` — AutoGen function registration

    Without a framework param, returns all schemas + quickstart links.
    """
    if framework and framework in _TOOL_SCHEMAS:
        return {
            "framework": framework,
            **_TOOL_SCHEMAS[framework],
            "get_free_key": "POST https://api.octodamus.com/v1/signup?email=your@email.com",
            "docs": "https://api.octodamus.com/docs",
        }
    return {
        "available_frameworks": list(_TOOL_SCHEMAS.keys()),
        "usage": "GET /v2/tools?framework=langchain",
        "mcp_server": "https://api.octodamus.com/mcp (add to Claude / Cursor / Windsurf)",
        "openapi_spec": "https://api.octodamus.com/openapi.json",
        "quickstart_repo": "https://github.com/Octodamus/octodata-quickstart",
        "get_free_key": "POST https://api.octodamus.com/v1/signup?email=your@email.com",
        "live_demo": "GET https://api.octodamus.com/v2/demo",
        "ask_anything": "POST https://api.octodamus.com/v2/ask?q=What+is+your+BTC+read",
        "docs": "https://api.octodamus.com/docs",
    }


# -- Entry point --------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run("octo_api_server:app", host="0.0.0.0", port=PORT, reload=False, workers=2)

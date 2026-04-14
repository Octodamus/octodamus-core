"""
octo_mcp_http.py - Octodamus MCP HTTP Server (monetized)
Wraps octo_mcp_server.py with API key auth, tier enforcement, and rate limiting.

Run:   python octo_mcp_http.py
Port:  8765  (configure below)

Agents connect via:  http://your-server:8765/mcp

Auth header:  X-API-Key: octo_xxxxxxxx
  - No key / invalid key  -> 401
  - Free tier tool blocked -> 403 with upgrade message
  - Rate limit hit         -> 429 with reset info
  - x402 pay-per-use       -> 402 with payment details (Base chain USDC)

Generate keys:  python octo_api_keys.py create --email x@y.com --tier free
"""

import json
import logging
import sys
import os
import threading
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent))
os.chdir(Path(__file__).parent)

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from octo_mcp_server import mcp
from octo_api_keys import (
    validate_key,
    check_rate_limit,
    record_usage,
    can_access_tool,
    create_key,
    FREE_DAILY_LIMIT,
)
from octo_payment_watcher import (
    register_pending,
    get_key_for_wallet,
    PREMIUM_PRICE_USDC,
    OCTODAMUS_WALLET,
    USDC_BASE,
    CHAIN_ID as PAYMENT_CHAIN_ID,
    run_daemon as payment_daemon,
)

log = logging.getLogger("OctoMCPHttp")

# ── Config ────────────────────────────────────────────────────────────────────

HOST          = "0.0.0.0"
PORT          = 8765
PRICE_PER_CALL_USDC = 0.01          # x402 pay-per-use for keyless agents
USDC_WALLET   = "0x7d372b930b42d4adc7c82f9d5bcb692da3597570"  # Octodamus Base wallet
CHAIN_ID      = 8453                # Base

# Internal key for local/Claude Code use — always allowed, no rate limit
_INTERNAL_KEY_FILE = Path(r"C:\Users\walli\octodamus\data\internal_api_key.txt")


def _get_or_create_internal_key() -> str:
    if _INTERNAL_KEY_FILE.exists():
        return _INTERNAL_KEY_FILE.read_text(encoding="utf-8").strip()
    key = create_key("internal@octodamus.com", tier="internal", label="local-claude-code")
    _INTERNAL_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    _INTERNAL_KEY_FILE.write_text(key, encoding="utf-8")
    log.info(f"Created internal key: {key}")
    return key


# ── Auth Middleware ───────────────────────────────────────────────────────────

class OctoAuthMiddleware(BaseHTTPMiddleware):
    """
    Intercepts all MCP requests and enforces:
    1. API key validation
    2. Tier-based tool access
    3. Daily rate limits
    4. x402 payment option for keyless agents
    """

    async def dispatch(self, request: Request, call_next):
        # Health check bypasses auth
        if request.url.path in ("/health", "/", "/docs"):
            return await call_next(request)

        raw_key = (
            request.headers.get("X-API-Key", "")
            or request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
            or request.query_params.get("api_key", "")
        )

        # No key provided — return x402 with payment details
        if not raw_key:
            return JSONResponse(
                status_code=402,
                content={
                    "error": "Payment Required",
                    "message": (
                        "Octodamus MCP requires an API key. "
                        "Free tier: octodamus.com/api — 50 requests/day. "
                        "Premium: $29/year for unlimited access."
                    ),
                    "x402": {
                        "price_usdc":   PRICE_PER_CALL_USDC,
                        "wallet":       USDC_WALLET,
                        "chain_id":     CHAIN_ID,
                        "token":        "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",  # USDC on Base
                        "description":  "Pay-per-call Octodamus oracle access",
                    },
                    "signup": "https://octodamus.com/api",
                },
            )

        # Validate key
        record = validate_key(raw_key)
        if not record:
            return JSONResponse(
                status_code=401,
                content={
                    "error": "Unauthorized",
                    "message": "Invalid or revoked API key. Get one at octodamus.com/api",
                },
            )

        tier = record.get("tier", "free")

        # Rate limit check
        allowed, used, limit = check_rate_limit(raw_key, tier)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "error": "Rate limit exceeded",
                    "message": f"Free tier: {limit} requests/day. Used: {used}. Resets at midnight UTC.",
                    "upgrade": "Premium ($29/year) removes rate limits. octodamus.com/api",
                },
                headers={"X-RateLimit-Limit": str(limit), "X-RateLimit-Used": str(used)},
            )

        # Attach tier info to request state for tool-level enforcement
        request.state.octo_tier = tier
        request.state.octo_key  = raw_key
        request.state.octo_email = record.get("email", "")

        # Process request
        response = await call_next(request)

        # Record usage after successful response
        if response.status_code < 400:
            record_usage(raw_key, request.url.path)

        # Add tier headers to response
        response.headers["X-Octodamus-Tier"]  = tier
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Used"]  = str(used + 1)

        return response


# ── Health Endpoint ───────────────────────────────────────────────────────────

async def health_endpoint(request: Request):
    return JSONResponse({
        "status":    "operational",
        "agent":     "Octodamus",
        "version":   "2.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tiers":     ["free (50 req/day)", "premium ($29/year, unlimited)"],
        "signup":    "https://octodamus.com/api",
        "mcp_endpoint": f"http://this-server:{PORT}/mcp",
    })


# ── ACP Agent Manifest ────────────────────────────────────────────────────────

async def acp_manifest(request: Request):
    """Virtuals ACP agent manifest endpoint."""
    return JSONResponse({
        "agent_id":       "octodamus",
        "name":           "Octodamus",
        "description":    (
            "Autonomous AI market oracle. Live signals for BTC, ETH, SOL, Oil, and macro markets. "
            "27 signal systems including derivatives, on-chain data, funding rates, and sentiment."
        ),
        "version":        "2.0",
        "capabilities":   [
            "crypto_signals", "market_sentiment", "prediction_markets",
            "derivatives_data", "trade_calls", "news_analysis"
        ],
        "pricing": {
            "free":    {"requests_per_day": FREE_DAILY_LIMIT, "tools": "basic signals"},
            "premium": {"price_usd_year": 29, "requests": "unlimited", "tools": "all"},
            "per_call": {"price_usdc": PRICE_PER_CALL_USDC, "protocol": "x402"},
        },
        "endpoints": {
            "mcp":      f"/mcp",
            "health":   f"/health",
            "manifest": f"/.well-known/agent.json",
        },
        "payment": {
            "wallet":   USDC_WALLET,
            "chain_id": CHAIN_ID,
            "token":    "USDC",
            "protocol": "x402",
        },
        "links": {
            "website": "https://octodamus.com",
            "api_docs": "https://octodamus.com/api",
            "x": "https://x.com/octodamusai",
        },
        "llms_txt": "https://octodamus.com/llms.txt",
    })


# ── Subscription Endpoints (public — no auth required) ────────────────────────

async def subscribe_endpoint(request: Request):
    """GET /api/subscribe?wallet=0xAGENT — register for premium, get payment instructions."""
    wallet = request.query_params.get("wallet", "").strip()
    email  = request.query_params.get("email",  "").strip()

    if not wallet.startswith("0x") or len(wallet) != 42:
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid wallet address. Must be 42-char hex starting with 0x."},
        )

    result = register_pending(wallet, email)
    return JSONResponse(content=result)


async def activate_endpoint(request: Request):
    """GET /api/activate?wallet=0xAGENT — retrieve premium key after payment."""
    wallet = request.query_params.get("wallet", "").strip()

    if not wallet.startswith("0x") or len(wallet) != 42:
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid wallet address."},
        )

    result = get_key_for_wallet(wallet)
    status_code = 200 if result.get("status") == "active" else 202
    return JSONResponse(status_code=status_code, content=result)


async def pricing_endpoint(request: Request):
    """GET /api/pricing — current pricing and payment instructions."""
    return JSONResponse(content={
        "free": {
            "price":            "Free",
            "requests_per_day": FREE_DAILY_LIMIT,
            "tools":            "get_signal, get_market_sentiment, get_active_calls, get_track_record",
            "signup":           "https://octodamus.com/api",
        },
        "premium": {
            "price":            f"${PREMIUM_PRICE_USDC:.0f} USDC/year",
            "requests":         "Unlimited",
            "tools":            "All tools including raw derivatives, predictions, news, liquidation maps",
            "payment_steps": [
                "1. GET /api/subscribe?wallet=YOUR_WALLET",
                f"2. Send {PREMIUM_PRICE_USDC} USDC from YOUR_WALLET to {OCTODAMUS_WALLET} on Base",
                "3. GET /api/activate?wallet=YOUR_WALLET (within 5 min of confirmation)",
            ],
            "payment_to":       OCTODAMUS_WALLET,
            "token":            "USDC",
            "chain":            "Base (chainId 8453)",
            "token_address":    USDC_BASE,
        },
        "pay_per_call": {
            "price":    "$0.01 USDC per call",
            "protocol": "x402",
            "notes":    "No signup needed — server returns 402 with payment details.",
        },
    })


# ── Build and Run ─────────────────────────────────────────────────────────────

def build_app():
    """Build the ASGI app with auth middleware, subscription routes, and health endpoints."""
    from starlette.routing import Route, Mount
    from starlette.applications import Starlette

    # Get the FastMCP ASGI app
    mcp_app = mcp.http_app(path="/mcp", transport="streamable-http")

    # Add auth middleware to MCP app only
    mcp_app.add_middleware(OctoAuthMiddleware)

    # Build outer Starlette app with public routes + mounted MCP app
    app = Starlette(
        routes=[
            Route("/health",                    health_endpoint),
            Route("/.well-known/agent.json",    acp_manifest),
            Route("/api/subscribe",             subscribe_endpoint),
            Route("/api/activate",              activate_endpoint),
            Route("/api/pricing",               pricing_endpoint),
            Mount("/",                          app=mcp_app),
        ],
    )

    return app


def main():
    import uvicorn
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    # Ensure internal key exists
    internal_key = _get_or_create_internal_key()
    log.info(f"Internal key ready (see {_INTERNAL_KEY_FILE})")

    # Start payment watcher daemon in background thread
    watcher_thread = threading.Thread(target=payment_daemon, daemon=True, name="PaymentWatcher")
    watcher_thread.start()
    log.info("Payment watcher daemon started (background thread)")

    log.info(f"Octodamus MCP HTTP server starting on {HOST}:{PORT}")
    log.info(f"MCP endpoint:    http://localhost:{PORT}/mcp")
    log.info(f"Health:          http://localhost:{PORT}/health")
    log.info(f"ACP manifest:    http://localhost:{PORT}/.well-known/agent.json")
    log.info(f"Subscribe:       http://localhost:{PORT}/api/subscribe?wallet=0xYOUR_WALLET")
    log.info(f"Activate:        http://localhost:{PORT}/api/activate?wallet=0xYOUR_WALLET")
    log.info(f"Pricing:         http://localhost:{PORT}/api/pricing")

    app = build_app()

    uvicorn.run(
        app,
        host=HOST,
        port=PORT,
        log_level="info",
    )


if __name__ == "__main__":
    main()

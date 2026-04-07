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
from typing import Optional

# Load Bitwarden secrets into env at startup
try:
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).parent))
    import bitwarden as _bw
    _bw.load_all_secrets()
except Exception as _e:
    print(f"[API] Bitwarden load skipped: {_e}")

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
    "basic":   {"req_per_day": 500,   "req_per_minute": 20},
    "pro":     {"req_per_day": 10000, "req_per_minute": 200},
    "premium": {"req_per_day": 10000, "req_per_minute": 200},
    "admin":   {"req_per_day": None,  "req_per_minute": None},
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

async def require_key_v2(request: Request, api_key: str = Security(API_KEY_HEADER)):
    """
    Require valid key + enforce tier rate limits.
    Also supports x402 crypto payment flow for AI agents:
      - No key + X-PAYMENT header → verify on-chain payment, provision key
      - No key, no payment → 402 with payment instructions
    Returns (api_key, entry) tuple.
    """
    # x402: agent already paid and sent X-PAYMENT proof header
    x_payment = request.headers.get("X-PAYMENT", "")

    if x_payment and not api_key:
        # Verify payment proof and retrieve provisioned key
        try:
            from octo_agent_pay import get_payment_status
            status = get_payment_status(x_payment)
            if status.get("status") == "fulfilled" and status.get("api_key"):
                api_key = status["api_key"]
            else:
                raise HTTPException(
                    status_code=402,
                    detail={
                        "error":    "payment_required",
                        "message":  "X-PAYMENT token not yet fulfilled. Poll /v1/agent-checkout/status first.",
                        "checkout": "https://api.octodamus.com/v1/agent-checkout?product=premium_annual",
                    }
                )
        except HTTPException:
            raise
        except Exception:
            pass

    if not api_key:
        # x402 Payment Required — agent-native response
        raise HTTPException(
            status_code=402,
            detail={
                "error":           "payment_required",
                "message":         "This endpoint requires an API key. Get a free Basic key or pay with USDC on Base.",
                "free_key":        "POST https://api.octodamus.com/v1/signup?email=your@email.com",
                "crypto_checkout": "POST https://api.octodamus.com/v1/agent-checkout?product=premium_annual&agent_wallet=0xYOUR_WALLET",
                "usdc_price":      "$29 USDC on Base (chain_id=8453)",
                "header_name":     "X-OctoData-Key",
                "docs":            "https://api.octodamus.com/docs",
            }
        )

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
      <a class="btn-secondary" href="https://api.octodamus.com/v2/demo">Live Demo (no key)</a>
      <a class="btn-secondary" href="https://api.octodamus.com/v2/ask?q=What+signals+do+you+have+open" target="_blank">Ask Octodamus →</a>
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
        <div class="t-price">$19/mo Stripe &nbsp;·&nbsp; $29 USDC annual (agents: no browser needed)</div>
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
        <div style="display:flex;gap:10px;flex-wrap:wrap;">
          <a class="t-cta pro-cta" style="flex:1" href="https://octodamus.com/upgrade.html">Upgrade $19/mo →</a>
          <a class="t-cta pro-cta" style="flex:1;font-size:0.6rem;" href="#" onclick="alert('POST /v1/agent-checkout?product=premium_trial&agent_wallet=0xYOUR_WALLET')">$5 USDC 7-day Trial →</a>
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


# â"€â"€ ACP Resource endpoints â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

@app.get("/api/fear-greed", tags=["ACP Resources"])
def acp_fear_greed():
    """Live Fear & Greed index â€" free ACP resource."""
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from octo_pulse import run_pulse_scan
        result = run_pulse_scan()
        fng = result.get("fear_greed") or {}
        return {
            "value": fng.get("value"),
            "label": fng.get("label"),
            "previous_close": fng.get("previous_close"),
            "timestamp": datetime.utcnow().isoformat(),
            "source": "alternative.me",
            "source": {"name": "OctoData API", "by": "Octodamus (@octodamusai)", "docs": "https://api.octodamus.com/docs", "signup": "POST https://api.octodamus.com/v1/signup?email="},
        }
    except Exception as e:
        return {"error": str(e), "timestamp": datetime.utcnow().isoformat()}


@app.get("/api/btc-dominance", tags=["ACP Resources"])
def acp_btc_dominance():
    """Live BTC dominance â€" free ACP resource."""
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from octo_gecko import run_gecko_scan
        result = run_gecko_scan()
        g = result.get("global") or {}
        return {
            "btc_dominance": g.get("btc_dominance"),
            "total_market_cap_usd": g.get("total_market_cap_usd"),
            "market_cap_change_24h": g.get("market_cap_change_24h"),
            "trending": [c.get("symbol") for c in result.get("trending", [])],
            "top_gainers": [{"symbol": c.get("symbol"), "chg_24h": c.get("chg_24h")} for c in result.get("gainers", [])],
            "top_losers":  [{"symbol": c.get("symbol"), "chg_24h": c.get("chg_24h")} for c in result.get("losers", [])],
            "timestamp": datetime.utcnow().isoformat(),
            "source": "CoinGecko",
            "source": {"name": "OctoData API", "by": "Octodamus (@octodamusai)", "docs": "https://api.octodamus.com/docs", "signup": "POST https://api.octodamus.com/v1/signup?email="},
        }
    except Exception as e:
        return {"error": str(e), "timestamp": datetime.utcnow().isoformat()}


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


@app.get("/api/prices", tags=["ACP Resources"])
def acp_prices():
    """Live BTC/ETH/SOL prices with 24h change. No auth required."""
    try:
        import requests as req
        r = req.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids":"bitcoin,ethereum,solana","vs_currencies":"usd","include_24hr_change":"true"},
            timeout=10
        )
        d = r.json()
        return {
            "btc": {"usd": d["bitcoin"]["usd"], "usd_24h_change": round(d["bitcoin"].get("usd_24h_change",0),2)},
            "eth": {"usd": d["ethereum"]["usd"], "usd_24h_change": round(d["ethereum"].get("usd_24h_change",0),2)},
            "sol": {"usd": d["solana"]["usd"],   "usd_24h_change": round(d["solana"].get("usd_24h_change",0),2)},
            "timestamp": __import__('datetime').datetime.utcnow().isoformat(),
            "source": {"name": "OctoData API", "by": "Octodamus (@octodamusai)", "docs": "https://api.octodamus.com/docs", "signup": "POST https://api.octodamus.com/v1/signup?email="}
        }
    except Exception as e:
        return {"error": str(e)}


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

    # Metrics (followers, guide sales)
    m = _load_metrics()
    followers = m.get("followers") or None
    guide_sales = m.get("guide_sales") or 0

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

    return {
        "followers": followers,
        "posts": posts,
        "guide_sales": guide_sales,
        "guide_revenue": m.get("guide_revenue", 0),
        "cached_at": int(_t.time()),
        "source": "local",
        "timestamp": datetime.utcnow().isoformat(),
        "source": {"name": "OctoData API", "by": "Octodamus (@octodamusai)", "docs": "https://api.octodamus.com/docs", "signup": "POST https://api.octodamus.com/v1/signup?email="},
    }


# â"€â"€ Calls endpoint â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

CALLS_FILE = Path(__file__).parent / "data" / "octo_calls.json"

def _load_calls() -> list:
    try:
        if CALLS_FILE.exists():
            return json.loads(CALLS_FILE.read_text(encoding="utf-8"))
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
    Premium ($19/mo): 10,000 req/day, all assets, full EV scores, AI briefing.
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
    Start a Stripe checkout to upgrade to Premium ($19/mo).
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
        return {"checkout_url": session.url, "session_id": session.id, "amount": "$19/mo"}
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


def _resp(body: dict, rl: dict) -> JSONResponse:
    """Return JSONResponse with rate-limit headers attached."""
    return JSONResponse(content=body, headers=_rl_headers(rl))


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
                "timestamp": ts, "source": _SOURCE_BLOCK}, rl)
    if is_pro:
        return _resp({"signals": [_fmt(c) for c in open_calls], "count": len(open_calls),
                "track_record": track, "methodology": "9/11 signal consensus required to publish.",
                "timestamp": ts, "source": _SOURCE_BLOCK}, rl)
    return _resp({"signal": _fmt(open_calls[-1]), "more_signals": len(open_calls) - 1,
            "upgrade": "Premium unlocks all signals + reasoning -> octodamus.com/upgrade",
            "track_record": track, "methodology": "9/11 signal consensus required to publish.",
            "timestamp": ts, "source": _SOURCE_BLOCK}, rl)


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
                "track_record": track, "timestamp": ts, "source": _SOURCE_BLOCK}, rl)
    return _resp({"top_play": _fmt(pos[0]) if pos else None, "total_plays": len(pos),
            "upgrade": "Premium unlocks all plays with EV scores -> octodamus.com/upgrade",
            "track_record": track, "timestamp": ts, "source": _SOURCE_BLOCK}, rl)


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
            return _resp({"symbols": syms, "timestamp": s.get("timestamp"), "source": _SOURCE_BLOCK}, rl)
        btc = syms.get("BTC", {})
        return _resp({"BTC": btc, "more_assets": [k for k in syms if k != "BTC"],
                "upgrade": "Premium unlocks all assets -> octodamus.com/upgrade",
                "timestamp": s.get("timestamp"), "source": _SOURCE_BLOCK}, rl)
    except HTTPException:
        return _resp({"error_code": "NO_DATA", "error": "No sentiment snapshot yet",
                      "timestamp": datetime.utcnow().isoformat()}, rl)


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
                "upgrade": None if is_pro else "Premium adds NVDA, TSLA, AAPL -> octodamus.com/upgrade",
                "source": _SOURCE_BLOCK}, rl)
    except HTTPException:
        return _resp({"error_code": "NO_DATA", "error": "No price snapshot yet",
                      "timestamp": datetime.utcnow().isoformat()}, rl)


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
    }, rl)


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
    }, rl)


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
    agent_wallet:  str = Query("",  description="Your Base wallet address (optional but recommended for faster matching)"),
    label:         str = Query("",  description="Agent or project name"),
    email:         str = Query("",  description="Email for key delivery (optional)"),
):
    """
    **Machine-to-machine crypto checkout — no browser required.**

    Create a USDC payment intent on Base. Returns payment address, amount, and poll URL.

    Flow:
    1. POST /v1/agent-checkout?product=premium_annual&agent_wallet=0x...
    2. Send exact USDC amount to payment_address on Base (chain_id=8453)
    3. Poll GET /v1/agent-checkout/status?payment_id=xxx every 15s
    4. Receive api_key in response when payment confirmed on-chain (~5 seconds on Base)

    Products:
    - `premium_trial`  — $5  USDC — 7-day Premium trial, 10k req/day. Upgrade to annual when ready.
    - `premium_annual` — $29 USDC — Premium API key, 10k req/day, no expiry
    - `guide_early`    — $29 USDC — Build The House guide download
    - `guide_standard` — $39 USDC — Build The House guide download

    **Start with the trial:** `POST /v1/agent-checkout?product=premium_trial&agent_wallet=0x...`
    """
    try:
        from octo_agent_pay import create_payment
        return create_payment(
            product=product,
            agent_wallet=agent_wallet,
            label=label,
            email=email,
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
    # Return the actual guide URL — host on GitHub releases or GDrive
    guide_url = os.environ.get("GUIDE_DOWNLOAD_URL", "")
    if not guide_url:
        raise HTTPException(status_code=503, detail="Guide URL not configured")
    return {"download_url": guide_url, "expires_in_seconds": int(t["expires"] - _time.time())}


# -- V2 Ask — Agent-to-Octodamus conversation (no auth required) -------------

# IP-based rate limit for /v2/ask — 20 req/day free, no key needed
_ask_ip_counts:  dict[str, int]   = defaultdict(int)
_ask_ip_date:    date              = date.today()
_ask_ip_lock                       = threading.Lock()
_ASK_FREE_LIMIT  = 20  # per IP per day without a key
_ASK_KEY_LIMIT   = 200 # per key per day with a key

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
    track      = _track_record(stats)

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
                         "closed": len(closed), "mode": "paper"},
        "note": "Basic returns top play only. Premium returns all plays with EV, true_p, Kelly size.",
    }

    # --- Sentiment ---
    sentiment_demo = {}
    try:
        s    = load_snapshot("sentiment")
        syms = s.get("symbols", {})
        btc  = syms.get("BTC", {})
        sentiment_demo = {
            "BTC":        btc,
            "more_assets": [k for k in syms if k != "BTC"],
            "note":       "Basic returns BTC only. Premium adds ETH, SOL, NVDA, TSLA, AAPL.",
            "timestamp":  s.get("timestamp"),
        }
    except Exception:
        sentiment_demo = {"error": "No sentiment snapshot yet"}

    # --- Prices ---
    prices_demo = {}
    try:
        s    = load_snapshot("prices")
        data = s.get("data", {})
        basic_data    = {k: v for k, v in data.items() if k.upper() in {"BTC", "ETH", "SOL"}}
        premium_keys  = [k for k in data if k.upper() not in {"BTC", "ETH", "SOL"}]
        prices_demo = {
            "prices":          basic_data,
            "premium_assets":  {k: "[premium]" for k in premium_keys},
            "note":            "Basic includes BTC, ETH, SOL. Premium adds NVDA, TSLA, AAPL.",
            "timestamp":       s.get("timestamp"),
        }
    except Exception:
        prices_demo = {"error": "No price snapshot yet"}

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
        "source": {
            "name":   "OctoData API",
            "by":     "Octodamus (@octodamusai)",
            "docs":   "https://api.octodamus.com/docs",
            "llms":   "https://octodamus.com/llms.txt",
        },
    }


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


# -- Entry point --------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run("octo_api_server:app", host="0.0.0.0", port=PORT, reload=False)

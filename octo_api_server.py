"""
octo_api_server.py — Octodamus API Server v3
FastAPI server — OctoData snapshots + ACP live report endpoints.
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
import secrets
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Security, Depends, Query
from fastapi.security.api_key import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
import uvicorn

PORT      = 8742
DATA_DIR  = Path(__file__).parent / "data" / "snapshots"
KEYS_FILE = Path(__file__).parent / "data" / "api_keys.json"

# Frozen reports written by ACP worker (WSL /mnt/c/... = Windows C:\...)
REPORTS_DIR = Path(__file__).parent / "data" / "reports"

DATA_DIR.mkdir(parents=True, exist_ok=True)
KEYS_FILE.parent.mkdir(parents=True, exist_ok=True)
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

# ── API key store ─────────────────────────────────────────────────────────────

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

# ── Snapshot loader ───────────────────────────────────────────────────────────

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

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="OctoData API",
    description="AI-powered market intelligence by Octodamus (@octodamusai)",
    version="3.0.0",
    docs_url="/docs",
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)

# ── Public ────────────────────────────────────────────────────────────────────

@app.get("/", tags=["Info"])
def root():
    return {
        "name": "OctoData API",
        "powered_by": "Octodamus (@octodamusai)",
        "docs": "/docs",
        "report_viewer": "/api/report?type=market_signal&ticker=BTC",
        "subscribe": "https://octodamus.com",
    }


@app.get("/health", tags=["Info"])
def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


# ── ACP Resource endpoints ────────────────────────────────────────────────────

@app.get("/api/fear-greed", tags=["ACP Resources"])
def acp_fear_greed():
    """Live Fear & Greed index — free ACP resource."""
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
            "powered_by": "Octodamus (@octodamusai)",
        }
    except Exception as e:
        return {"error": str(e), "timestamp": datetime.utcnow().isoformat()}


@app.get("/api/btc-dominance", tags=["ACP Resources"])
def acp_btc_dominance():
    """Live BTC dominance — free ACP resource."""
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
            "powered_by": "Octodamus (@octodamusai)",
        }
    except Exception as e:
        return {"error": str(e), "timestamp": datetime.utcnow().isoformat()}


# ── Report endpoints ──────────────────────────────────────────────────────────

@app.get("/api/report", response_class=HTMLResponse, tags=["ACP Resources"])
def acp_report_live(
    type: str = Query("market_signal", description="market_signal | fear_greed | bitcoin_analysis | congressional"),
    ticker: str = Query("BTC", description="BTC, ETH, SOL, NVDA, TSLA, AAPL..."),
    timeframe: str = Query("4h", description="Chart timeframe"),
):
    """
    Live HTML report — regenerated on every request.
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
    Frozen HTML report — generated once at ACP job delivery, served from disk.
    Permanent link — same content on every refresh, no regeneration.
    Written by octo_acp_worker.py at job delivery time.
    """
    # Sanitise ID — alphanumeric only, prevent path traversal
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
            "<h2>🦑 Report not found</h2>"
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


# ── Authenticated endpoints ───────────────────────────────────────────────────

@app.get("/v1/prices", tags=["Market Data"])
def get_prices(date: Optional[str] = None, key=Depends(require_key)):
    """Latest spot prices — NVDA, TSLA, AAPL, BTC, ETH, SOL. Updated nightly 1am PT."""
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
    """Full AI market briefing — mood, opportunity, risk, thesis. Pro tier only."""
    if key.get("tier") not in ("pro", "admin"):
        raise HTTPException(status_code=402, detail="Requires Pro tier. Upgrade at octodamus.com")
    s = load_snapshot("briefing", date)
    return {"timestamp": s.get("timestamp"), "briefing": s.get("briefing", {})}


@app.get("/v1/full", tags=["Market Intelligence"])
def get_full(target_date: Optional[str] = None, key=Depends(require_key)):
    """Prices + sentiment + briefing combined. Pro tier only."""
    if key.get("tier") not in ("pro", "admin"):
        raise HTTPException(status_code=402, detail="Requires Pro tier. Upgrade at octodamus.com")
    prices    = load_snapshot("prices", target_date)
    sentiment = load_snapshot("sentiment", target_date)
    briefing  = load_snapshot("briefing", target_date)
    return {
        "date": target_date or str(date.today()),
        "prices":    prices.get("data", {}),
        "sentiment": sentiment.get("symbols", {}),
        "briefing":  briefing.get("briefing", {}),
    }


# ── Admin ─────────────────────────────────────────────────────────────────────

ADMIN_SECRET = os.environ.get("OCTODATA_ADMIN_SECRET", "change-me-in-bitwarden")


@app.post("/admin/keys/create", tags=["Admin"])
def create_key(label: str, tier: str = "basic", days: int = 30, admin_secret: str = ""):
    if admin_secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Invalid admin secret")
    if tier not in ("basic", "pro", "admin"):
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
    return {k[:12] + "…": v for k, v in keys.items()}


@app.delete("/admin/keys/revoke", tags=["Admin"])
def revoke_key(api_key: str, admin_secret: str = ""):
    if admin_secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Invalid admin secret")
    keys = load_keys()
    if api_key not in keys:
        raise HTTPException(status_code=404, detail="Key not found")
    del keys[api_key]
    save_keys(keys)
    return {"revoked": True}


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run("octo_api_server:app", host="0.0.0.0", port=PORT, reload=False)

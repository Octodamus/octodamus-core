"""
octo_api_server.py
FastAPI server that serves OctoData snapshots behind API key auth.
Deploy as a Windows Service alongside the Telegram bot, or on a cheap VPS.

Revenue path:
  - List on RapidAPI (rapidapi.com/provider) — they handle billing
  - Or sell keys directly via Stripe + octodamus.com

Start:
  pip install fastapi uvicorn --break-system-packages
  python octo_api_server.py

Default port: 8742 (change below)
"""

import json
import os
import hashlib
import secrets
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Security, Depends
from fastapi.security.api_key import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# ── config ────────────────────────────────────────────────────────────────────
PORT      = 8742
DATA_DIR  = Path(__file__).parent / "data" / "snapshots"
KEYS_FILE = Path(__file__).parent / "data" / "api_keys.json"

DATA_DIR.mkdir(parents=True, exist_ok=True)
KEYS_FILE.parent.mkdir(parents=True, exist_ok=True)

# ── API key store (flat file — swap for SQLite if this scales) ────────────────
def load_keys() -> dict:
    if KEYS_FILE.exists():
        return json.loads(KEYS_FILE.read_text())
    return {}


def save_keys(keys: dict):
    KEYS_FILE.write_text(json.dumps(keys, indent=2))


def validate_key(api_key: str) -> Optional[dict]:
    """Returns key metadata if valid and not expired, else None."""
    keys = load_keys()
    entry = keys.get(api_key)
    if not entry:
        return None
    if entry.get("expires"):
        if datetime.fromisoformat(entry["expires"]) < datetime.utcnow():
            return None
    return entry


# ── auth dependency ───────────────────────────────────────────────────────────
API_KEY_HEADER = APIKeyHeader(name="X-OctoData-Key", auto_error=False)

async def require_key(api_key: str = Security(API_KEY_HEADER)):
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing X-OctoData-Key header")
    entry = validate_key(api_key)
    if not entry:
        raise HTTPException(status_code=403, detail="Invalid or expired API key")
    return entry


# ── snapshot loader ───────────────────────────────────────────────────────────
def load_snapshot(snapshot_type: str, target_date: Optional[str] = None) -> dict:
    """Load the most recent available snapshot of a given type."""
    if target_date:
        try:
            d = date.fromisoformat(target_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format — use YYYY-MM-DD")
        path = DATA_DIR / str(d) / f"{snapshot_type}.json"
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"No {snapshot_type} snapshot for {d}")
        return json.loads(path.read_text())

    # walk backwards up to 7 days to find latest
    for i in range(7):
        d = date.today() - timedelta(days=i)
        path = DATA_DIR / str(d) / f"{snapshot_type}.json"
        if path.exists():
            return json.loads(path.read_text())

    raise HTTPException(status_code=404, detail=f"No recent {snapshot_type} snapshot found")


# ── app ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="OctoData API",
    description="AI-powered market sentiment & briefings by Octodamus",
    version="1.0.0",
    docs_url="/docs",
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


# ── public endpoints ──────────────────────────────────────────────────────────
@app.get("/", tags=["Info"])
def root():
    return {
        "name": "OctoData API",
        "powered_by": "Octodamus (@octodamusai)",
        "docs": "/docs",
        "subscribe": "https://octodamus.com",
    }


@app.get("/health", tags=["Info"])
def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


# ── authenticated endpoints ───────────────────────────────────────────────────
@app.get("/v1/prices", tags=["Market Data"])
def get_prices(date: Optional[str] = None, key=Depends(require_key)):
    """
    Latest spot price snapshot for NVDA, TSLA, AAPL, BTC, ETH, SOL.
    Updated every night at 1am PT.
    """
    snapshot = load_snapshot("prices", date)
    return {
        "timestamp": snapshot.get("timestamp"),
        "data": snapshot.get("data", {}),
    }


@app.get("/v1/sentiment", tags=["Market Data"])
def get_sentiment(date: Optional[str] = None, key=Depends(require_key)):
    """
    AI sentiment scores for each symbol.
    Score: -100 (extreme bearish) to +100 (extreme bullish).
    Updated every night at 2am PT.
    """
    snapshot = load_snapshot("sentiment", date)
    return {
        "timestamp": snapshot.get("timestamp"),
        "symbols": snapshot.get("symbols", {}),
    }


@app.get("/v1/sentiment/{symbol}", tags=["Market Data"])
def get_sentiment_symbol(symbol: str, date: Optional[str] = None, key=Depends(require_key)):
    """Single symbol sentiment score."""
    symbol = symbol.upper()
    snapshot = load_snapshot("sentiment", date)
    symbols = snapshot.get("symbols", {})
    if symbol not in symbols:
        available = list(symbols.keys())
        raise HTTPException(status_code=404, detail=f"{symbol} not tracked. Available: {available}")
    return {
        "symbol": symbol,
        "timestamp": snapshot.get("timestamp"),
        **symbols[symbol],
    }


@app.get("/v1/briefing", tags=["Market Intelligence"])
def get_briefing(date: Optional[str] = None, key=Depends(require_key)):
    """
    Full AI market briefing: mood, top opportunity, top risk, overnight thesis.
    Updated every night at 3am PT.
    Premium endpoint — requires paid API key.
    """
    entry = key  # key metadata passed from dependency
    if entry.get("tier") not in ("pro", "admin"):
        raise HTTPException(
            status_code=402,
            detail="Briefing endpoint requires Pro tier. Upgrade at octodamus.com"
        )
    snapshot = load_snapshot("briefing", date)
    return {
        "timestamp": snapshot.get("timestamp"),
        "briefing": snapshot.get("briefing", {}),
    }


@app.get("/v1/full", tags=["Market Intelligence"])
def get_full(date: Optional[str] = None, key=Depends(require_key)):
    """
    Full combined snapshot: prices + sentiment + briefing in one call.
    Pro tier only.
    """
    entry = key
    if entry.get("tier") not in ("pro", "admin"):
        raise HTTPException(
            status_code=402,
            detail="Full endpoint requires Pro tier. Upgrade at octodamus.com"
        )
    prices    = load_snapshot("prices", date)
    sentiment = load_snapshot("sentiment", date)
    briefing  = load_snapshot("briefing", date)
    return {
        "date": date or str(date),
        "prices": prices.get("data", {}),
        "sentiment": sentiment.get("symbols", {}),
        "briefing": briefing.get("briefing", {}),
    }


# ── admin key management (protected by ADMIN_SECRET env var) ─────────────────
ADMIN_SECRET = os.environ.get("OCTODATA_ADMIN_SECRET", "change-me-in-bitwarden")


@app.post("/admin/keys/create", tags=["Admin"])
def create_key(
    label: str,
    tier: str = "basic",
    days: int = 30,
    admin_secret: str = "",
):
    """
    Create a new API key.
    tier: basic | pro | admin
    days: 0 = no expiry
    """
    if admin_secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Invalid admin secret")
    if tier not in ("basic", "pro", "admin"):
        raise HTTPException(status_code=400, detail="tier must be basic|pro|admin")

    new_key = "octo_" + secrets.token_urlsafe(24)
    keys = load_keys()
    keys[new_key] = {
        "label": label,
        "tier": tier,
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
    # mask full key for safety
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


# ── entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run("octo_api_server:app", host="0.0.0.0", port=PORT, reload=False)

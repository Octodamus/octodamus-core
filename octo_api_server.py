"""
octo_api_server.py â€” Octodamus API Server v3
FastAPI server â€” OctoData snapshots + ACP live report endpoints.
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

# â”€â”€ API key store â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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

# â”€â”€ Snapshot loader â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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

# â”€â”€ App â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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

# â”€â”€ Public â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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


# â”€â”€ ACP Resource endpoints â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@app.get("/api/fear-greed", tags=["ACP Resources"])
def acp_fear_greed():
    """Live Fear & Greed index â€” free ACP resource."""
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
    """Live BTC dominance â€” free ACP resource."""
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


# â”€â”€ Report endpoints â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

# â”€â”€ Simple JSON store for dashboard metrics â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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
    """Live dashboard metrics â€” followers, guide sales. No auth required."""
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
            "powered_by": "Octodamus (@octodamusai)"
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
    Live HTML report â€” regenerated on every request.
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
    Frozen HTML report â€” generated once at ACP job delivery, served from disk.
    Permanent link â€” same content on every refresh, no regeneration.
    Written by octo_acp_worker.py at job delivery time.
    """
    # Sanitise ID â€” alphanumeric only, prevent path traversal
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
            "<h2>ðŸ¦‘ Report not found</h2>"
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




# â”€â”€ X Stats endpoint â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

POSTED_LOG = Path(__file__).parent / "octo_posted_log.json"

@app.get("/api/xstats", tags=["Dashboard"])
def get_xstats():
    """Live X stats â€” followers, post count, guide sales. No auth required.
    Followers from metrics file (updated manually or by scraper).
    Post count from posted log. Guide sales from metrics."""
    import time as _t

    # Metrics (followers, guide sales)
    m = _load_metrics()
    followers = m.get("followers") or None
    guide_sales = m.get("guide_sales") or 0

    # Post count â€” use manual override if set, otherwise count from posted log
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
        "powered_by": "Octodamus (@octodamusai)",
    }


# â”€â”€ Calls endpoint â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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
    return {
        "wins": wins,
        "losses": losses,
        "win_rate": rate,
        "streak": streak or None,
        "total": len(calls),
        "open": len(open_calls),
    }


@app.get("/api/calls", tags=["Oracle Calls"])
def get_calls():
    """Live Oracle call record â€” all calls, stats, open positions. No auth required."""
    calls = _load_calls()
    stats = _call_stats(calls)
    return {
        "stats": stats,
        "calls": calls,
        "timestamp": datetime.utcnow().isoformat(),
        "powered_by": "Octodamus (@octodamusai)",
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
        "powered_by": "Octodamus (@octodamusai)",
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
        "powered_by": "Octodamus (@octodamusai)",
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
        "stats": {
            "balance": round(balance, 2),
            "starting_balance": round(starting, 2),
            "total_pnl": round(total_pnl, 2),
            "open_count": len(positions),
            "closed_count": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else None,
            "fees_paid": round(data.get("fees_paid", 0.0), 2),
            "mode": "paper",
        },
        "timestamp": datetime.utcnow().isoformat(),
        "powered_by": "Octodamus (@octodamusai)",
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


# â”€â”€ Authenticated endpoints â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


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
    """Latest spot prices â€” NVDA, TSLA, AAPL, BTC, ETH, SOL. Updated nightly 1am PT."""
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
    """Full AI market briefing â€” mood, opportunity, risk, thesis. Pro tier only."""
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


# â”€â”€ Admin â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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
    return {"revoked": True}


# â”€â”€ Entry point â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

if __name__ == "__main__":
    uvicorn.run("octo_api_server:app", host="0.0.0.0", port=PORT, reload=False)

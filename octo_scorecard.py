"""
octo_scorecard.py
Octodamus — Prediction Scorecard

Logs every directional call made in signal/daily posts.
Scores calls against live prices daily.
Generates weekly scorecard post for X every Sunday.

Storage: octo_predictions.json
  {
    "id": "uuid",
    "asset": "BTC",
    "direction": "up" | "down" | "neutral",
    "entry_price": 82000,
    "target_pct": 5.0,
    "confidence": "high" | "medium" | "low",
    "timeframe": "24h" | "7d",
    "post_text": "...",
    "timestamp": "2026-03-13T08:00:00",
    "resolved": false,
    "outcome": null,   # "hit" | "miss" | "push"
    "exit_price": null,
    "resolved_at": null
  }

Called from octodamus_runner.py:
  from octo_scorecard import log_prediction, resolve_predictions, generate_scorecard_post
"""

import json
import os
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
PREDICTIONS_FILE = BASE_DIR / "octo_predictions.json"


# ─────────────────────────────────────────────
# STORAGE
# ─────────────────────────────────────────────

def _load() -> list:
    if PREDICTIONS_FILE.exists():
        try:
            return json.loads(PREDICTIONS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save(data: list):
    PREDICTIONS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ─────────────────────────────────────────────
# LOG A PREDICTION
# ─────────────────────────────────────────────

def log_prediction(
    asset: str,
    direction: str,
    entry_price: float,
    post_text: str,
    target_pct: float = 5.0,
    confidence: str = "medium",
    timeframe: str = "24h",
) -> str:
    """Log a directional call. Returns the prediction ID."""
    predictions = _load()
    pred_id = str(uuid.uuid4())[:8]
    predictions.append({
        "id": pred_id,
        "asset": asset.upper(),
        "direction": direction,
        "entry_price": entry_price,
        "target_pct": target_pct,
        "confidence": confidence,
        "timeframe": timeframe,
        "post_text": post_text[:200],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "resolved": False,
        "outcome": None,
        "exit_price": None,
        "resolved_at": None,
    })
    _save(predictions)
    print(f"[Scorecard] Logged {direction.upper()} call on {asset} @ ${entry_price:,.2f} [{pred_id}]")
    return pred_id


# ─────────────────────────────────────────────
# RESOLVE PREDICTIONS AGAINST LIVE PRICES
# ─────────────────────────────────────────────

def _get_live_price(asset: str) -> float | None:
    """Fetch live price for an asset."""
    try:
        import httpx
        asset = asset.upper()
        crypto_map = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}
        if asset in crypto_map:
            r = httpx.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": crypto_map[asset], "vs_currencies": "usd"},
                timeout=8
            )
            if r.status_code == 200:
                return float(r.json()[crypto_map[asset]]["usd"])
        else:
            fd_key = os.environ.get("FINANCIAL_DATASETS_API_KEY", "")
            if fd_key:
                r = httpx.get(
                    "https://api.financialdatasets.ai/prices/snapshot/",
                    params={"ticker": asset},
                    headers={"X-API-KEY": fd_key},
                    timeout=8
                )
                if r.status_code == 200:
                    return float(r.json().get("snapshot", {}).get("price", 0))
    except Exception as e:
        print(f"[Scorecard] Price fetch failed for {asset}: {e}")
    return None


def resolve_predictions() -> dict:
    """Check all open predictions against live prices. Returns resolution summary."""
    predictions = _load()
    resolved_count = 0
    hits = 0
    misses = 0
    now = datetime.now(timezone.utc)

    for pred in predictions:
        if pred.get("resolved"):
            continue

        # Check if timeframe has elapsed
        ts = datetime.fromisoformat(pred["timestamp"])
        hours = 24 if pred.get("timeframe") == "24h" else 168
        if (now - ts).total_seconds() < hours * 3600:
            continue

        # Get live price
        live_price = _get_live_price(pred["asset"])
        if not live_price:
            continue

        entry = pred["entry_price"]
        direction = pred["direction"]
        actual_pct = ((live_price - entry) / entry) * 100

        # Score it
        if direction == "up":
            outcome = "hit" if actual_pct >= pred.get("target_pct", 3.0) else "miss"
        elif direction == "down":
            outcome = "hit" if actual_pct <= -pred.get("target_pct", 3.0) else "miss"
        else:
            outcome = "push"

        pred["resolved"] = True
        pred["outcome"] = outcome
        pred["exit_price"] = live_price
        pred["resolved_at"] = now.isoformat()
        pred["actual_pct"] = round(actual_pct, 2)

        resolved_count += 1
        if outcome == "hit":
            hits += 1
        elif outcome == "miss":
            misses += 1

        print(f"[Scorecard] {pred['asset']} {direction.upper()} → {outcome.upper()} "
              f"(entry ${entry:,.2f} → exit ${live_price:,.2f}, {actual_pct:+.1f}%)")

    _save(predictions)
    return {"resolved": resolved_count, "hits": hits, "misses": misses}


# ─────────────────────────────────────────────
# GENERATE WEEKLY SCORECARD POST
# ─────────────────────────────────────────────

def generate_scorecard_post() -> str | None:
    """Generate a weekly scorecard post. Returns post text or None if no data."""
    predictions = _load()
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)

    recent = [
        p for p in predictions
        if p.get("resolved") and p.get("resolved_at")
        and datetime.fromisoformat(p["resolved_at"]) >= week_ago
    ]

    if not recent:
        print("[Scorecard] No resolved predictions this week.")
        return None

    hits = [p for p in recent if p["outcome"] == "hit"]
    misses = [p for p in recent if p["outcome"] == "miss"]
    total = len(hits) + len(misses)
    win_rate = (len(hits) / total * 100) if total > 0 else 0

    # Best call
    best = max(hits, key=lambda p: abs(p.get("actual_pct", 0)), default=None)
    worst = max(misses, key=lambda p: abs(p.get("actual_pct", 0)), default=None)

    best_line = f"Best: {best['asset']} {best['direction'].upper()} +{best['actual_pct']:.1f}%" if best else ""
    worst_line = f"Miss: {worst['asset']} {worst['direction'].upper()} {worst['actual_pct']:+.1f}%" if worst else ""

    post = (
        f"Oracle scorecard. {len(hits)}/{total} calls correct this week. "
        f"Win rate: {win_rate:.0f}%. "
        f"{best_line}. {worst_line}. "
        f"Receipts posted. The ocean doesn't lie."
    )

    # Trim to 280
    if len(post) > 280:
        post = post[:277] + "..."

    print(f"[Scorecard] Generated scorecard post: {post}")
    return post


# ─────────────────────────────────────────────
# EXTRACT PREDICTION FROM POST TEXT
# ─────────────────────────────────────────────

def extract_and_log_from_signal(signal: dict, post_text: str) -> None:
    """
    Called after a signal post is generated.
    Extracts asset, direction, and price from signal metadata and logs it.
    """
    try:
        asset = signal.get("ticker") or signal.get("asset", "")
        price = float(signal.get("price") or signal.get("current_price") or 0)
        change = float(signal.get("change_pct") or signal.get("pct_change") or 0)

        if not asset or not price:
            return

        direction = "up" if change > 0 else "down"
        confidence = "high" if abs(change) >= 5 else "medium" if abs(change) >= 3 else "low"

        log_prediction(
            asset=asset,
            direction=direction,
            entry_price=price,
            post_text=post_text,
            target_pct=abs(change),
            confidence=confidence,
            timeframe="24h",
        )
    except Exception as e:
        print(f"[Scorecard] Could not extract prediction from signal: {e}")


# ─────────────────────────────────────────────
# STATS SUMMARY (for Telegram /dashboard)
# ─────────────────────────────────────────────

def get_stats_summary() -> str:
    predictions = _load()
    total = len(predictions)
    resolved = [p for p in predictions if p.get("resolved")]
    hits = [p for p in resolved if p.get("outcome") == "hit"]
    open_calls = [p for p in predictions if not p.get("resolved")]
    win_rate = (len(hits) / len(resolved) * 100) if resolved else 0

    return (
        f"Predictions: {total} total | {len(open_calls)} open | "
        f"{len(resolved)} resolved | Win rate: {win_rate:.0f}%"
    )

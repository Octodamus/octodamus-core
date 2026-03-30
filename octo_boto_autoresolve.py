"""
octo_boto_autoresolve.py — OctoBoto Auto-Resolution Engine

Checks all open positions against Polymarket API.
Resolves trades automatically when:
  1. Market officially resolved (resolved=True, outcome set)
  2. Market past endDate and price confirms outcome (>0.95 YES or <0.05 YES)

Run periodically via Task Scheduler or call from OctoBoto main loop.

Usage:
    python octo_boto_autoresolve.py          # Check and resolve all
    python octo_boto_autoresolve.py --dry-run # Show what would resolve without acting
"""

import json
import logging
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from octo_boto_tracker import PaperTracker

log = logging.getLogger("OctoBotoResolve")

GAMMA_API = "https://gamma-api.polymarket.com/markets"

# Price thresholds for auto-resolve when market is past endDate
RESOLVE_YES_THRESHOLD = 0.95   # YES price above this = resolve YES
RESOLVE_NO_THRESHOLD  = 0.05   # YES price below this = resolve NO


def fetch_market(market_id: str) -> dict:
    """Fetch market data from Polymarket Gamma API."""
    import httpx
    try:
        r = httpx.get(f"{GAMMA_API}/{market_id}", timeout=15)
        if r.status_code == 200:
            return r.json()
        log.warning(f"Market {market_id}: HTTP {r.status_code}")
        return {}
    except Exception as e:
        log.error(f"Market {market_id}: {e}")
        return {}


def check_resolution(market: dict) -> dict:
    """
    Determine if a market should be resolved.
    Returns: {"should_resolve": bool, "resolution": "YES"/"NO", "reason": str}
    """
    if not market:
        return {"should_resolve": False, "resolution": None, "reason": "No market data"}

    market_id = market.get("id", "?")

    # ── Strategy 1: Official resolution ──────────────────────────────
    if market.get("resolved") is True:
        outcome = market.get("outcome", "").strip()
        if outcome:
            resolution = "YES" if outcome.lower() == "yes" else "NO"
            return {
                "should_resolve": True,
                "resolution": resolution,
                "reason": f"Officially resolved: {outcome}",
            }

    # ── Strategy 2: Price-based resolution past endDate ──────────────
    end_date_str = market.get("endDate") or market.get("endDateIso")
    if end_date_str:
        try:
            # Parse end date
            if "T" in str(end_date_str):
                end_date = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
            else:
                end_date = datetime.strptime(str(end_date_str)[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)

            now = datetime.now(timezone.utc)

            # Parse YES price from outcomePrices
            prices_raw = market.get("outcomePrices", "[]")
            if isinstance(prices_raw, str):
                prices = json.loads(prices_raw)
            else:
                prices = prices_raw

            yes_price = float(prices[0]) if prices else 0.5

            # Past end date + price confirms outcome
            if now >= end_date:
                if yes_price >= RESOLVE_YES_THRESHOLD:
                    return {
                        "should_resolve": True,
                        "resolution": "YES",
                        "reason": f"Past endDate ({end_date_str[:10]}), YES price at {yes_price:.4f}",
                    }
                elif yes_price <= RESOLVE_NO_THRESHOLD:
                    return {
                        "should_resolve": True,
                        "resolution": "NO",
                        "reason": f"Past endDate ({end_date_str[:10]}), YES price at {yes_price:.4f}",
                    }
                else:
                    return {
                        "should_resolve": False,
                        "resolution": None,
                        "reason": f"Past endDate but price inconclusive ({yes_price:.4f})",
                    }

            # Not past end date yet — check if price is effectively settled
            # (e.g. oil already hit $102, price at 0.999)
            if yes_price >= 0.99:
                return {
                    "should_resolve": True,
                    "resolution": "YES",
                    "reason": f"Price effectively settled at {yes_price:.4f} (≥0.99)",
                }
            elif yes_price <= 0.01:
                return {
                    "should_resolve": True,
                    "resolution": "NO",
                    "reason": f"Price effectively settled at {yes_price:.4f} (≤0.01)",
                }

        except (ValueError, IndexError, TypeError) as e:
            log.warning(f"Market {market_id}: Date/price parse error: {e}")

    return {"should_resolve": False, "resolution": None, "reason": "Not yet resolvable"}


def run_autoresolve(dry_run: bool = False) -> list:
    """
    Check all open OctoBoto positions and resolve any that are settled.
    Returns list of resolved trades.
    """
    tracker = PaperTracker()
    positions = tracker.open_positions()

    if not positions:
        log.info("No open positions to check.")
        return []

    log.info(f"Checking {len(positions)} open position(s)...")
    resolved = []

    for pos in positions:
        market_id = pos.get("market_id", "")
        question = pos.get("question", "?")[:60]
        side = pos.get("side", "?")

        if not market_id:
            log.warning(f"Position missing market_id: {question}")
            continue

        log.info(f"  Checking [{market_id}] {question}...")
        market = fetch_market(market_id)
        result = check_resolution(market)

        if result["should_resolve"]:
            resolution = result["resolution"]
            reason = result["reason"]
            won = (side == resolution)
            outcome_str = "WIN" if won else "LOSS"

            if dry_run:
                log.info(f"  → WOULD RESOLVE: {resolution} ({outcome_str}) — {reason}")
                resolved.append({
                    "market_id": market_id,
                    "question": question,
                    "resolution": resolution,
                    "outcome": outcome_str,
                    "reason": reason,
                    "dry_run": True,
                })
            else:
                log.info(f"  → RESOLVING: {resolution} ({outcome_str}) — {reason}")
                closed = tracker.close_position(market_id, resolution)
                for c in closed:
                    pnl = c.get("pnl", 0)
                    pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
                    log.info(f"  → CLOSED: {outcome_str} | PnL: {pnl_str} ({c.get('pnl_pct', 0):.1f}%)")

                    # Send Telegram alert if available
                    try:
                        _send_resolve_alert(c, reason)
                    except Exception as e:
                        log.warning(f"  Telegram alert failed: {e}")

                resolved.append({
                    "market_id": market_id,
                    "question": question,
                    "resolution": resolution,
                    "outcome": outcome_str,
                    "reason": reason,
                    "closed": closed,
                })
        else:
            log.info(f"  → NOT RESOLVED: {result['reason']}")

    if resolved:
        log.info(f"\nResolved {len(resolved)} position(s). Balance: ${tracker.balance():.2f}")
    else:
        log.info("No positions resolved this run.")

    return resolved


def _send_resolve_alert(closed_trade: dict, reason: str):
    """Send Telegram notification when a trade auto-resolves."""
    try:
        secrets_paths = [
            os.path.join(os.path.dirname(__file__), ".octo_secrets"),
            r"C:\Users\walli\octodamus\.octo_secrets",
            "/home/walli/octodamus/.octo_secrets",
        ]
        bot_token = chat_id = ""
        for path in secrets_paths:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    cache = json.load(f)
                secrets = cache.get("secrets", {})
                bot_token = secrets.get("OCTOBOTO_TELEGRAM_TOKEN", "")
                chat_id = secrets.get("OCTOBOTO_TELEGRAM_CHAT_ID", "")
                if bot_token and chat_id:
                    break
            except (FileNotFoundError, json.JSONDecodeError):
                continue

        if not bot_token or not chat_id:
            return

        won = closed_trade.get("won", False)
        pnl = closed_trade.get("pnl", 0)
        emoji = "✅" if won else "❌"
        outcome = "WIN" if won else "LOSS"
        pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"

        text = (
            f"{emoji} OctoBoto Auto-Resolve: {outcome}\n"
            f"📊 {closed_trade.get('question', '?')}\n"
            f"Side: {closed_trade.get('side', '?')} → {closed_trade.get('resolution', '?')}\n"
            f"PnL: {pnl_str} ({closed_trade.get('pnl_pct', 0):.1f}%)\n"
            f"Reason: {reason}"
        )

        import httpx
        httpx.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
    except Exception:
        pass  # Don't fail the resolve on alert failure


# ── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [AutoResolve] %(message)s",
        datefmt="%H:%M:%S",
    )

    dry_run = "--dry-run" in sys.argv

    if dry_run:
        print("\n=== DRY RUN — no trades will be closed ===\n")

    results = run_autoresolve(dry_run=dry_run)

    if results:
        print(f"\n{'Would resolve' if dry_run else 'Resolved'}: {len(results)} position(s)")
        for r in results:
            print(f"  [{r['market_id']}] {r['question']} → {r['resolution']} ({r['outcome']})")
    else:
        print("\nNo positions to resolve.")

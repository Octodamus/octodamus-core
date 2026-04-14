"""
octo_polybacktest.py — PolyBackTest API client for Octodamus.

Wraps the polybacktest.com v2 API. Provides:
  - Market discovery (BTC/ETH/SOL UP/DOWN markets)
  - Orderbook snapshots at sub-second resolution
  - Bid depth signal: does stronger bid side at market open predict winner?
  - Time-of-day win rate analysis

API docs: https://docs.polybacktest.com
Key stored as POLYBACKTEST_API_KEY in Bitwarden / .octo_secrets

Usage:
  python octo_polybacktest.py depth       — run bid-depth signal study (free tier)
  python octo_polybacktest.py hours       — run time-of-day win rate study
  python octo_polybacktest.py market btc  — show recent BTC markets
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
API_BASE = "https://api.polybacktest.com"


# ── Secrets bootstrap ──────────────────────────────────────────────────────────

def _ensure_secrets():
    if os.environ.get("POLYBACKTEST_API_KEY"):
        return
    try:
        sys.path.insert(0, str(BASE_DIR))
        from bitwarden import load_all_secrets
        load_all_secrets()
    except Exception:
        pass

_ensure_secrets()


# ── HTTP client ────────────────────────────────────────────────────────────────

def _get(path: str, params: dict | None = None) -> dict | list:
    """Make an authenticated GET request to the PolyBackTest API."""
    import requests

    api_key = os.environ.get("POLYBACKTEST_API_KEY", "")
    if not api_key:
        raise EnvironmentError("POLYBACKTEST_API_KEY not set")

    url = f"{API_BASE}{path}"
    headers = {"X-API-Key": api_key}

    try:
        r = requests.get(url, headers=headers, params=params or {}, timeout=15)
    except requests.exceptions.Timeout:
        return {}  # skip on timeout
    except Exception as e:
        raise RuntimeError(f"Request failed: {e}") from e

    if r.status_code == 429:
        retry = int(r.headers.get("Retry-After", 2))
        print(f"  [!] Rate limited — waiting {retry}s")
        time.sleep(retry)
        return _get(path, params)
    if r.status_code == 402:
        raise PermissionError("Pro plan required for this data")
    if r.status_code == 401:
        raise PermissionError("Invalid API key")
    if r.status_code == 404:
        return {}
    if r.status_code >= 500:
        return {}  # server error — skip this market
    r.raise_for_status()
    return r.json()


def _get_paged(path: str, params: dict, key: str, limit: int = 200) -> list:
    """Paginate through all results up to `limit` total."""
    results = []
    offset = 0
    batch = min(100, limit)
    while len(results) < limit:
        p = {**params, "limit": batch, "offset": offset}
        data = _get(path, p)
        if not data:
            break
        batch_items = data.get(key, [])
        results.extend(batch_items)
        if len(batch_items) < batch:
            break
        offset += batch
        time.sleep(0.5)  # stay within free tier rate limit
    return results[:limit]


# ── Market queries ─────────────────────────────────────────────────────────────

def get_markets(
    coin: str = "btc",
    market_type: str | None = None,
    resolved: bool | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """List markets. market_type: '5m' | '15m' | '1hr' | '4hr' | '24hr'."""
    params: dict = {"coin": coin}
    if market_type:
        params["market_type"] = market_type
    if resolved is not None:
        params["resolved"] = str(resolved).lower()
    if start_time:
        params["start_time"] = start_time
    if end_time:
        params["end_time"] = end_time
    return _get_paged("/v2/markets", params, "markets", limit)


def get_market_by_slug(slug: str, coin: str = "btc") -> dict:
    return _get(f"/v2/markets/by-slug/{slug}", {"coin": coin})


def get_snapshots(
    market_id: str,
    coin: str = "btc",
    include_orderbook: bool = False,
    start_time: str | None = None,
    end_time: str | None = None,
    limit: int = 100,
) -> dict:
    """Get snapshots for a market. Returns full response dict (market + snapshots)."""
    params: dict = {"coin": coin, "include_orderbook": str(include_orderbook).lower()}
    if start_time:
        params["start_time"] = start_time
    if end_time:
        params["end_time"] = end_time
    snaps = _get_paged(
        f"/v2/markets/{market_id}/snapshots", params, "snapshots", limit
    )
    return snaps


def get_snapshot_at(market_id: str, timestamp: str, coin: str = "btc") -> dict | None:
    """Get the nearest orderbook snapshot (±2s) to a timestamp."""
    data = _get(
        f"/v2/markets/{market_id}/snapshot-at/{timestamp}",
        {"coin": coin},
    )
    snaps = data.get("snapshots", []) if data else []
    return snaps[0] if snaps else None


def get_spot_trades(
    coin: str = "btc",
    start_time: str | None = None,
    end_time: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Get 1-second Binance spot OHLCV candles."""
    params: dict = {"coin": coin}
    if start_time:
        params["start_time"] = start_time
    if end_time:
        params["end_time"] = end_time
    return _get_paged("/v2/spot/trades", params, "trades", limit)


# ── Signal: Bid depth at market open predicts winner ──────────────────────────

def _bid_depth(orderbook: dict) -> float:
    """Total size of all bids in an orderbook."""
    if not orderbook:
        return 0.0
    return sum(entry.get("size", 0) for entry in orderbook.get("bids", []))


def run_depth_signal_study(
    coin: str = "btc",
    market_type: str = "5m",
    n_markets: int = 50,
    seconds_after_open: int = 5,
) -> dict:
    """
    Replicate the PolyBackTest bid-depth study:
      Fetch orderbook snapshot at T+N seconds after market open.
      If UP bids > DOWN bids → predict UP. Check against actual winner.

    Returns summary dict with hit_rate, n_markets, breakdown.
    """
    print(f"\n── Bid Depth Signal Study ──")
    print(f"Coin: {coin.upper()} | Market type: {market_type} | "
          f"Snapshot at T+{seconds_after_open}s | Markets: {n_markets}\n")

    markets = get_markets(coin=coin, market_type=market_type, resolved=True, limit=n_markets)
    if not markets:
        print("No resolved markets found.")
        return {}

    results = []
    for i, m in enumerate(markets):
        market_id = m.get("market_id", "")
        winner = (m.get("winner") or "").upper()  # API returns "Up"/"Down" — normalize
        start_time = m.get("start_time", "")
        slug = m.get("slug", "")[:45]

        if not winner or not start_time or not market_id:
            continue

        # Parse start time and add N seconds
        try:
            if isinstance(start_time, str):
                dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            else:
                dt = datetime.fromtimestamp(start_time / 1000, tz=timezone.utc)
            ts_ms = int(dt.timestamp() * 1000) + (seconds_after_open * 1000)
            ts_str = str(ts_ms)
        except Exception:
            continue

        print(f"  [{i+1}/{len(markets)}] {slug}... ", end="", flush=True)

        snap = get_snapshot_at(market_id, ts_str, coin=coin)
        if not snap:
            print("no snapshot")
            continue

        ob_up = snap.get("orderbook_up") or {}
        ob_down = snap.get("orderbook_down") or {}

        # Note: get_snapshot_at uses include_orderbook implicitly via snapshot-at endpoint
        # If no orderbook data, fall back to price comparison
        depth_up = _bid_depth(ob_up)
        depth_down = _bid_depth(ob_down)

        if depth_up == 0 and depth_down == 0:
            # Use price_up / price_down as proxy (higher price = more bullish bids)
            price_up = snap.get("price_up", 0) or 0
            price_down = snap.get("price_down", 0) or 0
            predicted = "UP" if price_up > price_down else "DOWN"
            signal_type = "price"
        else:
            predicted = "UP" if depth_up > depth_down else "DOWN"
            signal_type = "depth"

        correct = predicted == winner
        total_depth = depth_up + depth_down
        ratio = max(depth_up, depth_down) / total_depth if total_depth > 0 else 0.5
        results.append({
            "slug": m.get("slug", ""),
            "winner": winner,
            "predicted": predicted,
            "correct": correct,
            "signal_type": signal_type,
            "depth_up": depth_up,
            "depth_down": depth_down,
            "imbalance_ratio": ratio,
        })

        status = "✓" if correct else "✗"
        print(f"{status} ({predicted} predicted, {winner} won) [{signal_type}] ratio={ratio:.2f}")
        time.sleep(0.6)  # free tier: 1 req/s

    if not results:
        print("No results.")
        return {}

    total = len(results)
    hits = sum(1 for r in results if r["correct"])
    up_wins = sum(1 for r in results if r["winner"] == "UP")
    depth_used = sum(1 for r in results if r["signal_type"] == "depth")

    print(f"\n── Results ──")
    print(f"Markets analysed : {total}")
    print(f"Signal type      : {depth_used} depth / {total - depth_used} price-proxy")
    print(f"Hit rate (all)   : {hits}/{total} = {hits/total*100:.1f}%")
    print(f"UP/DOWN split    : {up_wins} UP / {total - up_wins} DOWN")

    # Imbalance threshold sweep — does filtering by strong signals improve accuracy?
    print(f"\n── Imbalance Threshold Filter ──")
    print(f"{'Min ratio':<12} {'Markets':<10} {'Hit rate'}")
    print("─" * 35)
    for threshold in [0.50, 0.55, 0.60, 0.65, 0.70]:
        subset = [r for r in results if r["imbalance_ratio"] >= threshold]
        if subset:
            sub_hits = sum(1 for r in subset if r["correct"])
            print(f"{threshold:.2f}         {len(subset):<10} {sub_hits/len(subset)*100:.1f}%")
        else:
            print(f"{threshold:.2f}         0          —")

    return {
        "hit_rate": hits / total,
        "hits": hits,
        "total": total,
        "up_wins": up_wins,
        "results": results,
    }


# ── Signal: Time-of-day win rate ───────────────────────────────────────────────

def run_hour_study(
    coin: str = "btc",
    market_type: str = "1hr",
    n_markets: int = 200,
) -> dict:
    """
    Check UP win rate by UTC hour across resolved markets.
    Replicates the PolyBackTest hour-of-day study.
    """
    print(f"\n── Time-of-Day Win Rate Study ──")
    print(f"Coin: {coin.upper()} | Market type: {market_type} | Markets: {n_markets}\n")

    markets = get_markets(coin=coin, market_type=market_type, resolved=True, limit=n_markets)
    if not markets:
        print("No resolved markets found.")
        return {}

    from collections import defaultdict
    by_hour: dict[int, list[bool]] = defaultdict(list)

    for m in markets:
        winner = m.get("winner")
        start_time = m.get("start_time", "")
        if not winner or not start_time:
            continue
        try:
            if isinstance(start_time, str):
                dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            else:
                dt = datetime.fromtimestamp(start_time / 1000, tz=timezone.utc)
            hour = dt.hour
        except Exception:
            continue
        by_hour[hour].append((winner or "").upper() == "UP")

    print(f"{'Hour UTC':<10} {'Markets':<10} {'UP%':<8} {'Signal'}")
    print("─" * 45)

    rows = []
    for hour in sorted(by_hour):
        wins = by_hour[hour]
        up_rate = sum(wins) / len(wins)
        signal = ""
        if up_rate >= 0.60:
            signal = "▲ BULLISH"
        elif up_rate <= 0.40:
            signal = "▼ BEARISH"
        rows.append((hour, len(wins), up_rate))
        print(f"{hour:02d}h        {len(wins):<10} {up_rate*100:.1f}%    {signal}")

    if rows:
        best = max(rows, key=lambda r: r[2])
        worst = min(rows, key=lambda r: r[2])
        print(f"\nBullish hour : {best[0]:02d}h UTC ({best[2]*100:.1f}% UP, {best[1]} markets)")
        print(f"Bearish hour : {worst[0]:02d}h UTC ({worst[2]*100:.1f}% UP, {worst[1]} markets)")

    return {"by_hour": {h: {"n": len(v), "up_rate": sum(v)/len(v)} for h, v in by_hour.items()}}


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = sys.argv[1:]

    if not args or args[0] == "depth":
        run_depth_signal_study(coin="btc", market_type="5m", n_markets=50, seconds_after_open=5)

    elif args[0] == "hours":
        coin = args[1] if len(args) > 1 else "btc"
        run_hour_study(coin=coin, market_type="1hr", n_markets=200)

    elif args[0] == "market":
        coin = args[1] if len(args) > 1 else "btc"
        mtype = args[2] if len(args) > 2 else None
        markets = get_markets(coin=coin, market_type=mtype, limit=10)
        for m in markets:
            print(f"{m.get('start_time','')[:16]} | {m.get('winner','?'):>5} | "
                  f"vol:{m.get('final_volume',0):.0f} | {m.get('slug','')[:55]}")

    elif args[0] == "health":
        print(_get("/health"))

    else:
        print(__doc__)

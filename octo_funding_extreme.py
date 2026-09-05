"""
octo_funding_extreme.py -- Funding Rate Extreme Oracle

Fires oracle calls when funding rates hit extremes across exchanges.
  avg < -0.005/8h AND 3+ exchanges negative -> BUY  (shorts squeezed)
  avg > +0.010/8h AND 3+ exchanges positive -> SELL (longs overheated)

The ETH call in April (call #25) fired on this exact setup and won +5%.
Timeframe: 24h. Target: 3%. call_type: "funding_extreme"

Run:
  python octo_funding_extreme.py          # check BTC ETH SOL
  python octo_funding_extreme.py BTC      # one asset
  python octo_funding_extreme.py --dry    # score without recording
  python octo_funding_extreme.py --scores # print levels, no action
"""

import sys
import json
import time
import argparse
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

_ASSETS     = ["BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "ADA", "AVAX", "LINK", "SUI"]
_CALLS_FILE = ROOT / "data" / "octo_calls.json"

BUY_THRESHOLD  = -0.005   # avg 8h rate below -> BUY
SELL_THRESHOLD = +0.010   # avg 8h rate above -> SELL
MIN_EXCHANGES  = 3        # min exchanges confirming direction
TARGET_PCT     = 3.0      # % target
TIMEFRAME      = "24h"


def _load_calls() -> list:
    try:
        return json.loads(_CALLS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_calls(calls: list):
    _CALLS_FILE.write_text(json.dumps(calls, indent=2), encoding="utf-8")


def _has_open_call(asset: str) -> bool:
    for c in _load_calls():
        if (not c.get("resolved") and c.get("asset") == asset.upper()
                and c.get("call_type") == "funding_extreme"):
            return True
    return False


_KRAKEN_PAIR = {
    "BTC": "XBTUSD", "ETH": "ETHUSD", "SOL": "SOLUSD", "XRP": "XRPUSD",
    "DOGE": "XDGUSD", "ADA": "ADAUSD", "AVAX": "AVAXUSD", "LINK": "LINKUSD", "SUI": "SUIUSD",
}  # BNB is not listed on Kraken -> CoinGecko only
_CG_ID = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "XRP": "ripple",
    "DOGE": "dogecoin", "BNB": "binancecoin", "ADA": "cardano",
    "AVAX": "avalanche-2", "LINK": "chainlink", "SUI": "sui",
}


def _get_price(asset: str) -> float:
    import os, httpx
    asset = asset.upper()
    # Kraken first (fast, no key) when the pair is supported.
    if asset in _KRAKEN_PAIR:
        try:
            r = httpx.get(f"https://api.kraken.com/0/public/Ticker?pair={_KRAKEN_PAIR[asset]}", timeout=8)
            data = r.json()["result"]
            key = list(data.keys())[0]
            return float(data[key]["c"][0])
        except Exception:
            pass
    # CoinGecko fallback (demo key header avoids the shared-IP 429s).
    if asset in _CG_ID:
        try:
            hdr = {"User-Agent": "octodamus-oracle/1.0"}
            k = os.environ.get("COINGECKO_API_KEY", "")
            if k:
                hdr["x-cg-demo-api-key"] = k
            r = httpx.get(
                f"https://api.coingecko.com/api/v3/simple/price?ids={_CG_ID[asset]}&vs_currencies=usd",
                headers=hdr, timeout=8,
            )
            return float(r.json()[_CG_ID[asset]]["usd"])
        except Exception:
            pass
    return 0.0


def _get_fng() -> int:
    try:
        import httpx
        r = httpx.get("https://api.alternative.me/fng/?limit=1", timeout=8)
        return int(r.json()["data"][0]["value"])
    except Exception:
        return 50


def _fetch_funding(asset: str) -> dict:
    from octo_coinglass import funding_rate_exchange
    raw = funding_rate_exchange(asset)
    if not raw or not isinstance(raw, list):
        return {"ok": False, "reason": "No data"}
    exchanges = []
    try:
        # The exchange-list endpoint ignores the symbol param and returns ALL coins (~2000).
        # Filter for THIS asset's entry -- taking raw[0] silently scored every asset on BTC's
        # funding (pre-existing bug that invalidated ETH/SOL scoring).
        current = next(
            (e for e in raw if isinstance(e, dict) and str(e.get("symbol", "")).upper() == asset.upper()),
            None,
        )
        if not current:
            return {"ok": False, "reason": f"no funding entry for {asset}"}
        for ex in current.get("stablecoin_margin_list", []):
            fr = ex.get("funding_rate")
            if fr is not None:
                exchanges.append({"exchange": ex["exchange"], "rate": float(fr)})
    except Exception:
        return {"ok": False, "reason": "Parse error"}
    if not exchanges:
        return {"ok": False, "reason": "Empty list"}
    rates = [e["rate"] for e in exchanges]
    avg = sum(rates) / len(rates)
    return {
        "ok":        True,
        "avg":       avg,
        "neg_count": sum(1 for r in rates if r < 0),
        "pos_count": sum(1 for r in rates if r > 0),
        "total":     len(exchanges),
        "exchanges": exchanges,
    }


def score_asset(asset: str) -> dict:
    price = _get_price(asset)
    if price == 0:
        return {"asset": asset, "fire": False, "reason": "Price unavailable"}

    fd = _fetch_funding(asset)
    if not fd["ok"]:
        return {"asset": asset, "fire": False, "reason": fd["reason"]}

    avg  = fd["avg"]
    fng  = _get_fng()

    if avg <= BUY_THRESHOLD and fd["neg_count"] >= MIN_EXCHANGES:
        direction = "UP"
        note = (
            f"Funding avg {avg*100:+.3f}%/8h ({fd['neg_count']}/{fd['total']} exchanges negative). "
            f"Shorts paying longs -- short squeeze loading. F&G={fng}."
        )
    elif avg >= SELL_THRESHOLD and fd["pos_count"] >= MIN_EXCHANGES:
        direction = "DOWN"
        note = (
            f"Funding avg {avg*100:+.3f}%/8h ({fd['pos_count']}/{fd['total']} exchanges positive). "
            f"Longs overextended -- flush incoming. F&G={fng}."
        )
    else:
        return {
            "asset":     asset,
            "fire":      False,
            "reason":    f"avg {avg*100:+.3f}%/8h | neg:{fd['neg_count']} pos:{fd['pos_count']} -- no extreme",
            "avg":       avg,
            "neg_count": fd["neg_count"],
            "pos_count": fd["pos_count"],
            "fng":       fng,
        }

    mult   = 1 + TARGET_PCT / 100
    target = price * mult if direction == "UP" else price / mult
    edge   = abs(avg) / SELL_THRESHOLD  # normalized conviction

    return {
        "asset":       asset,
        "fire":        True,
        "direction":   direction,
        "price":       price,
        "target_price": round(target, 2),
        "timeframe":   TIMEFRAME,
        "note":        note,
        "fng":         fng,
        "avg":         avg,
        "neg_count":   fd["neg_count"],
        "pos_count":   fd["pos_count"],
        "edge_score":  round(min(edge, 1.0) * (1 if direction == "UP" else -1), 3),
        "signals": {
            "avg_funding_8h":  avg,
            "neg_exchanges":   fd["neg_count"],
            "pos_exchanges":   fd["pos_count"],
            "total_exchanges": fd["total"],
            "fng":             fng,
            "exchanges":       fd["exchanges"],
        },
    }


def _post_text(r: dict) -> str:
    arrow = "^" if r["direction"] == "UP" else "v"
    bias  = "LONG" if r["direction"] == "UP" else "SHORT"
    count = r["neg_count"] if r["direction"] == "UP" else r["pos_count"]
    total = r["signals"]["total_exchanges"]
    label = "negative" if r["direction"] == "UP" else "positive"
    tag   = "Shorts paying to stay short. Squeeze loading." if r["direction"] == "UP" \
            else "Longs paying to stay long. Flush incoming."

    return (
        f"{r['asset']} {arrow} {bias} -- Funding Extreme signal.\n\n"
        f"Avg funding: {r['avg']*100:+.3f}%/8h ({count}/{total} exchanges {label})\n"
        f"Entry: ${r['price']:,.0f} | Target: ${r['target_price']:,.0f} (+{TARGET_PCT:.0f}% / {TIMEFRAME})\n\n"
        f"F&G: {r['fng']}\n\n"
        f"{tag}"
    )


def run_funding_extreme(assets: list = None, dry: bool = False) -> list:
    assets = [a.upper() for a in (assets or _ASSETS)]
    fired  = []
    print(f"\n[FundingExtreme] Scan | {datetime.now(timezone.utc).strftime('%H:%M UTC')}")
    from bitwarden import load_all_secrets
    load_all_secrets()
    from octo_x_poster import queue_post, process_queue

    # Fleet circuit breaker: funding_extreme's prior 3-0 was scored on a bug (BTC funding applied
    # to every asset), so its per-asset edge is unproven. Now corrected + expanded -- let the
    # breaker auto-pause it if the true record drops below the on-chain floor.
    if not dry:
        try:
            from octo_calls import strategy_should_pause
            _pause, _reason = strategy_should_pause("funding_extreme")
            if _pause:
                print(f"[FundingExtreme] CIRCUIT BREAKER: {_reason}. Skipping run.")
                return []
        except Exception as _e:
            print(f"[FundingExtreme] breaker check failed (continuing): {_e}")

    for asset in assets:
        if _has_open_call(asset):
            print(f"[FundingExtreme] {asset}: open call exists -- skip")
            continue

        result = score_asset(asset)

        if not result.get("fire"):
            print(f"[FundingExtreme] {asset}: PASS -- {result.get('reason')}")
            continue

        print(
            f"[FundingExtreme] {asset}: FIRE {result['direction']} | "
            f"avg={result['avg']*100:+.3f}%/8h | target=${result['target_price']:,.0f}"
        )

        if dry:
            print("[FundingExtreme] DRY RUN -- not recording or posting")
            fired.append(result)
            continue

        # Record + publish on-chain FIRST, then post -- guaranteed by commit_call_onchain:
        # posted only if anchored on-chain; a failed publish rolls back and alerts.
        now  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        call = {
            "call_type":               "funding_extreme",
            "asset":                   asset,
            "direction":               result["direction"],
            "entry_price":             result["price"],
            "target_price":            result["target_price"],
            "timeframe":               result["timeframe"],
            "note":                    result["note"],
            "made_at":                 now,
            "resolved":                False,
            "outcome":                 None,
            "won":                     None,
            "exit_price":              None,
            "resolved_at":             None,
            "resolution_price_source": "CoinGecko spot",
            "signals":                 result["signals"],
            "edge_score":              result["edge_score"],
            "time_quality":            "",
            "market_snapshot":         {"price": result["price"], "fng": result["fng"]},
            "post_mortem":             None,
        }

        def _post():
            queue_post(_post_text(result), post_type="funding_extreme", priority=2)
            process_queue(max_posts=1, force=True)

        from octo_calls import commit_call_onchain
        tx = commit_call_onchain(call, _post)
        if tx:
            print(f"[FundingExtreme] Call #{call['id']} on-chain {tx[:10]}... + posted")
            fired.append(result)
        else:
            print(f"[FundingExtreme] {asset}: on-chain publish failed -- NOT posted (alerted)")
        time.sleep(2)

    print(f"[FundingExtreme] Done -- {len(fired)} calls fired\n")
    return fired


def print_scores(assets: list = None):
    assets = [a.upper() for a in (assets or _ASSETS)]
    print(f"\n[FundingExtreme] Levels | {datetime.now(timezone.utc).strftime('%H:%M UTC')}\n")
    for asset in assets:
        r = score_asset(asset)
        fire = "FIRE" if r.get("fire") else "PASS"
        detail = r.get("reason") or f"{r['direction']} | avg={r['avg']*100:+.3f}%/8h"
        print(f"  {asset}: {fire} -- {detail}")
    print()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("assets", nargs="*", default=[])
    ap.add_argument("--dry",    action="store_true")
    ap.add_argument("--scores", action="store_true")
    args = ap.parse_args()
    targets = [a.upper() for a in args.assets if a.upper() in _ASSETS] or _ASSETS
    if args.scores:
        print_scores(targets)
    else:
        run_funding_extreme(targets, dry=args.dry)

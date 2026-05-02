"""
octo_range_scout.py — Ranging Market Short-Term Oracle

Generates 4h-6h trade calls when the main 13-signal oracle is quiet (HOLD/WATCH).
Built for ranging markets where trending signals (RSI, 24h change, F&G) are neutral
but derivative signals (funding, taker flow, L/S ratio) still show short-term edge.

Strategy:
  - 6 mini-signals, requires 4/6 to fire (vs 9/13 for main oracle)
  - Only activates when main oracle is NOT STRONG (prevents override)
  - Regime filter: F&G 28-72, 24h change <±5%, BB not in squeeze
  - Timeframe: 4h (standard) or 6h (if 5+/6)
  - call_type: "range_scout" — tracked separately, merged to track record at 70%+/20 calls
  - Max 1 open range_scout call per asset (same guard as main oracle)

Run:
  python octo_range_scout.py            # check all 3 assets
  python octo_range_scout.py BTC        # check one asset
  python octo_range_scout.py --dry      # score without recording

Add to Task Scheduler: every 2 hours, 8am-8pm UTC
"""

import sys
import json
import time
import argparse
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

_ASSETS = ["BTC", "ETH", "SOL"]
_CALLS_FILE = ROOT / "data" / "octo_calls.json"


def _load_calls() -> list:
    try:
        return json.loads(_CALLS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _has_open_range_call(asset: str) -> bool:
    for c in _load_calls():
        if (not c.get("resolved") and c.get("asset") == asset.upper()
                and c.get("call_type") == "range_scout"):
            return True
    return False


def _get_price(asset: str) -> tuple[float, float]:
    """Returns (price, chg_24h). Tries Kraken then CoinGecko."""
    try:
        import httpx
        sym = {"BTC": "XBTUSD", "ETH": "ETHUSD", "SOL": "SOLUSD"}[asset]
        r = httpx.get(f"https://api.kraken.com/0/public/Ticker?pair={sym}", timeout=8)
        data = r.json()["result"]
        key = list(data.keys())[0]
        price = float(data[key]["c"][0])
        open_24h = float(data[key]["o"])
        chg = (price - open_24h) / open_24h * 100
        return price, round(chg, 2)
    except Exception:
        pass
    try:
        import httpx
        ids = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}[asset]
        r = httpx.get(
            f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd&include_24hr_change=true",
            timeout=8,
        )
        d = r.json()[ids]
        return float(d["usd"]), round(float(d.get("usd_24h_change", 0)), 2)
    except Exception:
        return 0.0, 0.0


def _get_fng() -> int:
    try:
        import httpx
        r = httpx.get("https://api.alternative.me/fng/?limit=1", timeout=8)
        return int(r.json()["data"][0]["value"])
    except Exception:
        return 50


def _score_asset(asset: str, price: float, chg_24h: float, fng: int, dry: bool = False) -> dict:
    """
    Run the 6-signal ranging oracle for one asset.
    Returns: {asset, direction, score, bull, bear, signals, timeframe, fire}
    """
    from octo_report_handlers import fetch_technicals, fetch_derivatives, directional_call, _fetch_coinglass_compact
    from octo_tradingview import get_tv_signal

    ta    = fetch_technicals(asset) or {}
    deriv = fetch_derivatives(asset) or {}
    cg    = _fetch_coinglass_compact(asset) or {}
    tv    = get_tv_signal(asset) or {}

    rsi_1h = float(ta.get("rsi", 50) or 50)
    bb_w   = float(ta.get("bb_width", 5) or 5)
    fr     = float(deriv.get("funding_rate", 0) or 0)
    cg_fr  = float(cg.get("funding_avg", 0) or 0)
    long_pct   = float(cg.get("long_pct", 50) or 50)
    taker_buy  = float(cg.get("taker_buy_pct", 50) or 50)
    tv_vote    = int(tv.get("vote", 0) or 0)
    tv_agree   = bool(tv.get("agreement", False))

    signals = {
        "rsi_1h": rsi_1h, "bb_width": bb_w, "funding_rate": fr,
        "cg_funding_avg": cg_fr, "long_pct": long_pct,
        "taker_buy_pct": taker_buy, "tv_vote": tv_vote,
        "tv_agree": tv_agree, "fng": fng, "chg_24h": chg_24h,
    }

    # ── Regime filter — bail if conditions not met ────────────────────────────
    regime_ok = True
    regime_reason = ""

    if not (28 <= fng <= 72):
        regime_ok = False
        regime_reason = f"F&G={fng} outside ranging zone 28-72"
    elif abs(chg_24h) >= 5.0:
        regime_ok = False
        regime_reason = f"24h change {chg_24h:+.1f}% — trending market"
    elif bb_w < 2.5:
        regime_ok = False
        regime_reason = f"BB width {bb_w:.1f}% — squeeze forming, use breakout logic"

    if not regime_ok:
        return {"asset": asset, "fire": False, "reason": f"Regime filter: {regime_reason}", "signals": signals}

    # ── Check main oracle isn't already STRONG ────────────────────────────────
    try:
        from octo_report_handlers import directional_call
        call_str = directional_call(asset, price, chg_24h, ta, deriv, fng, cg, tv=tv)
        if "STRONG UP" in call_str or "STRONG DOWN" in call_str:
            return {"asset": asset, "fire": False,
                    "reason": "Main oracle is STRONG — defer to primary signal", "signals": signals}
    except Exception:
        pass

    # ── 6 mini-signals ────────────────────────────────────────────────────────
    bull = bear = 0

    # Mini-Signal 1: RSI 1h overbought/oversold (tighter bands for intraday)
    if rsi_1h < 40:     bull += 1; signals["s1_rsi"] = "BULL"
    elif rsi_1h > 60:   bear += 1; signals["s1_rsi"] = "BEAR"
    else:               signals["s1_rsi"] = "NEUTRAL"

    # Mini-Signal 2: TradingView 1h+4h technical vote
    if tv_vote > 0:     bull += 1; signals["s2_tv"] = "BULL"
    elif tv_vote < 0:   bear += 1; signals["s2_tv"] = "BEAR"
    else:               signals["s2_tv"] = "NEUTRAL"

    # Mini-Signal 3: Funding rate direction (mean reversion)
    # Positive funding = longs paying = short-term bearish pressure
    if fr < -0.005 or cg_fr < -0.005:   bull += 1; signals["s3_funding"] = "BULL"
    elif fr > 0.01 or cg_fr > 0.01:     bear += 1; signals["s3_funding"] = "BEAR"
    else:                                signals["s3_funding"] = "NEUTRAL"

    # Mini-Signal 4: Taker flow (aggressive order flow)
    if taker_buy > 57:     bull += 1; signals["s4_taker"] = "BULL"
    elif taker_buy < 43:   bear += 1; signals["s4_taker"] = "BEAR"
    else:                  signals["s4_taker"] = "NEUTRAL"

    # Mini-Signal 5: Long/short ratio contrarian read
    # Extreme longs = crowded = mean revert down; extreme shorts = pain trade up
    if long_pct > 62:      bear += 1; signals["s5_ls"] = "BEAR"
    elif long_pct < 42:    bull += 1; signals["s5_ls"] = "BULL"
    else:                  signals["s5_ls"] = "NEUTRAL"

    # Mini-Signal 6: Fear vs price context
    # F&G < 40 with price NOT making new lows = latent buy pressure
    # F&G > 60 with price NOT making new highs = latent sell pressure
    if fng < 40 and chg_24h > -1.0:   bull += 1; signals["s6_fng_ctx"] = "BULL"
    elif fng > 60 and chg_24h < 1.0:  bear += 1; signals["s6_fng_ctx"] = "BEAR"
    else:                              signals["s6_fng_ctx"] = "NEUTRAL"

    total   = bull + bear
    maximum = max(bull, bear)

    # Require 4/6 minimum. 5/6 = 6h, 6/6 = extended 8h
    if bull >= 4 and bull > bear:
        direction = "UP"
    elif bear >= 4 and bear > bull:
        direction = "DOWN"
    else:
        return {
            "asset": asset, "fire": False,
            "reason": f"Insufficient signal: {bull}B/{bear}S of 6 (need 4+ aligned)",
            "signals": signals, "bull": bull, "bear": bear,
        }

    timeframe = "4h" if maximum == 4 else ("6h" if maximum == 5 else "8h")
    target_pct = {"4h": 0.8, "6h": 1.2, "8h": 1.5}[timeframe]
    target_price = price * (1 + target_pct / 100) if direction == "UP" else price * (1 - target_pct / 100)
    edge_score = (bull - bear) / 6.0

    return {
        "asset":        asset,
        "fire":         True,
        "direction":    direction,
        "bull":         bull,
        "bear":         bear,
        "timeframe":    timeframe,
        "target_price": round(target_price, 2),
        "target_pct":   target_pct,
        "edge_score":   round(edge_score, 3),
        "signals":      signals,
        "price":        price,
        "note": (
            f"Range Scout: {bull}/{bear} mini-signals. "
            f"RSI={rsi_1h:.0f}, TV={'bull' if tv_vote>0 else 'bear' if tv_vote<0 else 'neutral'}, "
            f"Funding={fr:.4f}, TakerBuy={taker_buy:.0f}%, "
            f"L/S ratio={long_pct:.0f}% long, F&G={fng}"
        ),
    }


def _post_range_call(result: dict) -> str:
    """Post range_scout call to X. Returns post text."""
    asset     = result["asset"]
    direction = result["direction"]
    price     = result["price"]
    tf        = result["timeframe"]
    bull      = result["bull"]
    bear      = result["bear"]
    target    = result["target_price"]
    pct       = result["target_pct"]
    sigs      = result["signals"]

    arrow = "^" if direction == "UP" else "v"
    bias  = "LONG" if direction == "UP" else "SHORT"
    rsi   = sigs.get("rsi_1h", 50)
    taker = sigs.get("taker_buy_pct", 50)
    fng   = sigs.get("fng", 50)

    text = (
        f"{asset} {arrow} {bias} — {bull}/{bull+bear} ranging signals aligned.\n\n"
        f"Entry: ${price:,.0f}\n"
        f"Target: ${target:,.0f} (+{pct:.1f}% / {tf})\n\n"
        f"RSI: {rsi:.0f} | TakerBuy: {taker:.0f}% | F&G: {fng}\n\n"
        f"Range play — NOT main oracle grade. Short TF, tighter target."
    )
    return text


def run_range_scout(assets: list = None, dry: bool = False) -> list:
    """Main entry — score assets, record and post qualifying calls."""
    assets = [a.upper() for a in (assets or _ASSETS)]
    fng    = _get_fng()
    fired  = []

    print(f"\n[RangeScout] F&G={fng} | {datetime.now(timezone.utc).strftime('%H:%M UTC')}")

    for asset in assets:
        price, chg_24h = _get_price(asset)
        if price == 0:
            print(f"[RangeScout] {asset}: price fetch failed — skip")
            continue

        print(f"[RangeScout] {asset}: ${price:,.0f} ({chg_24h:+.1f}%)")

        # Don't stack range calls
        if _has_open_range_call(asset):
            print(f"[RangeScout] {asset}: already has open range_scout call — skip")
            continue

        result = _score_asset(asset, price, chg_24h, fng, dry=dry)

        if not result.get("fire"):
            print(f"[RangeScout] {asset}: PASS — {result.get('reason', 'no signal')}")
            continue

        print(
            f"[RangeScout] {asset}: FIRE {result['direction']} | "
            f"{result['bull']}B/{result['bear']}S | TF={result['timeframe']} | "
            f"target=${result['target_price']:,.0f}"
        )

        if dry:
            print(f"[RangeScout] DRY RUN — not recording or posting")
            fired.append(result)
            continue

        # Record call
        try:
            from octo_calls import record_call, _load, _save
            calls = _load()
            from datetime import datetime as _dt, timezone as _tz
            call = {
                "id":                     len(calls) + 1,
                "call_type":              "range_scout",
                "asset":                  asset,
                "direction":              result["direction"],
                "entry_price":            price,
                "target_price":           result["target_price"],
                "timeframe":              result["timeframe"],
                "note":                   result["note"],
                "made_at":                _dt.now(_tz.utc).strftime("%Y-%m-%d %H:%M UTC"),
                "resolved":               False,
                "outcome":                None,
                "won":                    None,
                "exit_price":             None,
                "resolved_at":            None,
                "resolution_price_source": "CoinGecko spot",
                "signals":                result["signals"],
                "edge_score":             result["edge_score"],
                "time_quality":           "",
                "market_snapshot":        {"price": price, "chg_24h": chg_24h, "fng": fng},
                "post_mortem":            None,
            }
            calls.append(call)
            _save(calls)
            print(f"[RangeScout] Call #{call['id']} recorded")
        except Exception as e:
            print(f"[RangeScout] Record failed: {e}")

        # Post to X
        try:
            post_text = _post_range_call(result)
            from octo_x_poster import post_tweet
            tweet_id = post_tweet(post_text)
            print(f"[RangeScout] Posted: {tweet_id} | {post_text[:80]}...")
        except Exception as e:
            print(f"[RangeScout] Post failed: {e}")

        fired.append(result)
        time.sleep(2)

    print(f"[RangeScout] Done — {len(fired)} calls fired\n")
    return fired


def print_scores(assets: list = None):
    """Show current scores for all assets without firing."""
    assets = [a.upper() for a in (assets or _ASSETS)]
    fng    = _get_fng()
    print(f"\n[RangeScout] Scores — F&G={fng} | {datetime.now(timezone.utc).strftime('%H:%M UTC')}\n")
    for asset in assets:
        price, chg_24h = _get_price(asset)
        if price == 0:
            print(f"  {asset}: price unavailable")
            continue
        r = _score_asset(asset, price, chg_24h, fng)
        fire = "FIRE" if r.get("fire") else "PASS"
        detail = r.get("reason", f"{r.get('bull',0)}B/{r.get('bear',0)}S | TF={r.get('timeframe','?')}")
        print(f"  {asset}: {fire} — {detail}")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("assets", nargs="*", default=[], help="BTC ETH SOL (default: all)")
    parser.add_argument("--dry", action="store_true", help="Score without posting or recording")
    parser.add_argument("--scores", action="store_true", help="Print scores only, no action")
    args = parser.parse_args()

    targets = [a.upper() for a in args.assets if a.upper() in _ASSETS] or _ASSETS

    if args.scores:
        print_scores(targets)
    else:
        run_range_scout(targets, dry=args.dry)

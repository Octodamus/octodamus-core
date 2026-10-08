"""
octo_crowd_fade.py -- Crowd Fade (Contrarian) Oracle

Fires when the crowd is massively positioned one way AND funding confirms
they're paying for it. The pain trade goes the other direction.

  L/S > 68% long  AND avg_funding > +0.002/8h -> SELL (crowd long trap)
  L/S < 38% long  AND avg_funding < -0.002/8h -> BUY  (crowd short trap)
  F&G floor for DOWN: >= 50 neutral trend, >= 65 if 7d up >5%, blocked if 7d up >10%
  F&G bonus: F&G > 70 strengthens SELL; F&G < 30 strengthens BUY
  TREND GATE (octo_regime.trend_gate): DOWN only in a confirmed downtrend, UP never
  into one. The book is 2W-8L; re-scored against the trend at call time, the fades
  that fired into strength (+19% 7d, +18% vs SMA20) all lost. Note the May losses
  (#32-37) fired DOWN after -8..-11% weeks -- a crowd that is long AFTER a flush is
  capitulating, not trapped. The gate cannot see that; the circuit breaker can.

Timeframe: 48h (crowd unwinds slower than funding extremes).
Target: 4%. call_type: "crowd_fade". Cooldown: 24h between calls per asset.

Run:
  python octo_crowd_fade.py          # check BTC ETH SOL
  python octo_crowd_fade.py BTC      # one asset
  python octo_crowd_fade.py --dry    # score without recording
  python octo_crowd_fade.py --scores # print levels, no action
"""

import sys
import json
import time
import argparse
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

_ASSETS     = ["BTC", "ETH", "SOL"]
_CALLS_FILE = ROOT / "data" / "octo_calls.json"

# Long/short thresholds
LONG_TRAP_THRESHOLD  = 68.0   # >68% long = crowded = SELL signal
SHORT_TRAP_THRESHOLD = 38.0   # <38% long = crowded short = BUY signal

# Funding rate must confirm the crowd is paying (8h rate as decimal)
FUNDING_CONFIRM_BULL = -0.002  # funding must be below this for BUY
FUNDING_CONFIRM_BEAR = +0.002  # funding must be above this for SELL

# F&G levels that strengthen the signal
FNG_GREED = 70   # greed zone confirms SELL
FNG_FEAR  = 30   # fear zone confirms BUY

TARGET_PCT = 4.0
TIMEFRAME  = "48h"


def _load_calls() -> list:
    try:
        return json.loads(_CALLS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_calls(calls: list):
    _CALLS_FILE.write_text(json.dumps(calls, indent=2), encoding="utf-8")


COOLDOWN_HOURS = 24  # don't re-fire within 24h of last crowd_fade (win or loss)

def _has_open_call(asset: str) -> bool:
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    for c in _load_calls():
        if c.get("asset") != asset.upper() or c.get("call_type") != "crowd_fade":
            continue
        # Block if currently open
        if not c.get("resolved"):
            return True
        # Block if resolved but within cooldown window
        try:
            made = datetime.strptime(c["made_at"], "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)
            if (now - made) < timedelta(hours=COOLDOWN_HOURS):
                print(f"[CrowdFade] {asset}: cooldown active -- last call {int((now-made).total_seconds()//3600)}h ago (limit {COOLDOWN_HOURS}h)")
                return True
        except Exception:
            pass
    return False


def _get_price(asset: str) -> float:
    try:
        import httpx
        sym = {"BTC": "XBTUSD", "ETH": "ETHUSD", "SOL": "SOLUSD"}[asset]
        r = httpx.get(f"https://api.kraken.com/0/public/Ticker?pair={sym}", timeout=8)
        data = r.json()["result"]
        key = list(data.keys())[0]
        return float(data[key]["c"][0])
    except Exception:
        pass
    try:
        import httpx
        ids = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}[asset]
        r = httpx.get(
            f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd",
            timeout=8,
        )
        return float(r.json()[ids]["usd"])
    except Exception:
        return 0.0


def _get_fng() -> int:
    try:
        import httpx
        r = httpx.get("https://api.alternative.me/fng/?limit=1", timeout=8)
        return int(r.json()["data"][0]["value"])
    except Exception:
        return 50


def _get_7d_change(asset: str) -> float:
    """7-day % change from the shared trend regime (Kraken daily OHLC).

    This used to hit api.binance.com, which returns HTTP 451 from this machine, so
    it was 0.0 on every call and the 7d trend gate below never blocked anything --
    both Aug/Sep crowd_fade losses were fired at +0.0% "trend" into a +19% week.
    """
    try:
        from octo_regime import get_regime
        rg = get_regime(asset)
        if rg:
            return float(rg["chg_7d"])
    except Exception:
        pass
    return 0.0


def _fetch_ls(asset: str) -> float | None:
    """Returns most recent global_account_long_percent, or None on failure."""
    try:
        from octo_coinglass import long_short_ratio
        data = long_short_ratio(asset, "4h")
        if data and isinstance(data, list):
            return float(data[-1]["global_account_long_percent"])
    except Exception:
        pass
    return None


def _fetch_avg_funding(asset: str) -> float | None:
    """Returns avg funding rate across stablecoin-margin exchanges.

    Delegates to octo_funding_extreme._fetch_funding, which filters the
    exchange-list response for THIS asset. The old code took raw[0] -- the
    first coin in the list, BTC -- so ETH and SOL were "confirmed" by BTC's
    funding on every scan (+0.344% for all three assets in the same run).
    """
    try:
        from octo_funding_extreme import _fetch_funding
        fd = _fetch_funding(asset)
        return float(fd["avg"]) if fd.get("ok") else None
    except Exception:
        return None


def score_asset(asset: str) -> dict:
    from octo_calls import round_price
    price = _get_price(asset)
    if price == 0:
        return {"asset": asset, "fire": False, "reason": "Price unavailable"}

    long_pct = _fetch_ls(asset)
    if long_pct is None:
        return {"asset": asset, "fire": False, "reason": "L/S data unavailable"}

    avg_funding = _fetch_avg_funding(asset)
    if avg_funding is None:
        return {"asset": asset, "fire": False, "reason": "Funding data unavailable"}

    fng        = _get_fng()
    change_7d  = _get_7d_change(asset)
    direction  = None
    conviction = 0
    note       = ""

    if long_pct >= LONG_TRAP_THRESHOLD and avg_funding >= FUNDING_CONFIRM_BEAR:
        # Shared trend gate first: a DOWN fade needs a confirmed downtrend
        # (7d down AND below the 20d average). Every trend-opposed call on the
        # record lost; DOWN outside a confirmed downtrend was 2W-13L.
        try:
            from octo_regime import trend_gate
            _ok, _why = trend_gate(asset, "DOWN")
        except Exception as _e:
            _ok, _why = False, f"trend gate error: {_e}"
        if not _ok:
            return {
                "asset": asset, "fire": False, "reason": f"TREND GATE: {_why}",
                "long_pct": long_pct, "avg_funding": avg_funding, "fng": fng, "change_7d": change_7d,
            }
        # Legacy momentum gate (kept; now sees a real 7d number)
        if change_7d >= 10.0:
            return {
                "asset": asset, "fire": False,
                "reason": f"7d trend +{change_7d:.1f}% -- bull momentum, crowd longs structural not a trap",
                "long_pct": long_pct, "avg_funding": avg_funding, "fng": fng, "change_7d": change_7d,
            }
        # F&G floor rises with trend strength
        fng_floor = 65 if change_7d >= 5.0 else 50
        if fng < fng_floor:
            return {
                "asset": asset, "fire": False,
                "reason": f"F&G={fng} below floor ({fng_floor}) for {change_7d:+.1f}% 7d trend -- not a greed trap",
                "long_pct": long_pct, "avg_funding": avg_funding, "fng": fng, "change_7d": change_7d,
            }
        direction  = "DOWN"
        conviction = 2
        if fng >= FNG_GREED:
            conviction = 3
        note = (
            f"L/S ratio {long_pct:.1f}% long -- crowd packed one side. "
            f"Funding {avg_funding*100:+.3f}%/8h confirms longs paying. "
            f"7d trend {change_7d:+.1f}%. "
            f"F&G={fng}{'  Greed trap confirmed.' if fng >= FNG_GREED else ''}. "
            f"Pain trade: DOWN."
        )

    elif long_pct <= SHORT_TRAP_THRESHOLD and avg_funding <= FUNDING_CONFIRM_BULL:
        try:
            from octo_regime import trend_gate
            _ok, _why = trend_gate(asset, "UP")
        except Exception as _e:
            _ok, _why = False, f"trend gate error: {_e}"
        if not _ok:
            return {
                "asset": asset, "fire": False, "reason": f"TREND GATE: {_why}",
                "long_pct": long_pct, "avg_funding": avg_funding, "fng": fng, "change_7d": change_7d,
            }
        direction  = "UP"
        conviction = 2
        if fng <= FNG_FEAR:
            conviction = 3
        note = (
            f"L/S ratio {long_pct:.1f}% long -- crowd massively short. "
            f"Funding {avg_funding*100:+.3f}%/8h confirms shorts paying. "
            f"F&G={fng}{'  Fear trap confirmed.' if fng <= FNG_FEAR else ''}. "
            f"Pain trade: UP."
        )

    if not direction:
        return {
            "asset":       asset,
            "fire":        False,
            "reason":      f"L/S={long_pct:.1f}% long | funding={avg_funding*100:+.3f}%/8h | 7d={change_7d:+.1f}% -- no crowd extreme",
            "long_pct":    long_pct,
            "avg_funding": avg_funding,
            "fng":         fng,
            "change_7d":   change_7d,
        }

    tf     = TIMEFRAME
    pct    = TARGET_PCT + (1.0 if conviction >= 3 else 0.0)  # 5% target when F&G confirms
    mult   = 1 + pct / 100
    target = price * mult if direction == "UP" else price / mult
    edge   = (1 if direction == "UP" else -1) * (conviction / 3)

    return {
        "asset":        asset,
        "fire":         True,
        "direction":    direction,
        "price":        price,
        "target_price": round_price(target),
        "target_pct":   pct,
        "timeframe":    tf,
        "conviction":   conviction,
        "note":         note,
        "long_pct":     long_pct,
        "avg_funding":  avg_funding,
        "fng":          fng,
        "edge_score":   round(edge, 3),
        "signals": {
            "long_pct":        long_pct,
            "short_pct":       round(100 - long_pct, 1),
            "avg_funding_8h":  avg_funding,
            "fng":             fng,
            "change_7d":       round(change_7d, 2),
            "conviction":      conviction,
        },
    }


def _post_text(r: dict) -> str:
    arrow = "^" if r["direction"] == "UP" else "v"
    bias  = "LONG" if r["direction"] == "UP" else "SHORT"
    crowd = "SHORT" if r["direction"] == "UP" else "LONG"
    crowd_pct = r["signals"]["short_pct"] if r["direction"] == "UP" else r["long_pct"]

    stars = "*" * r["conviction"]
    fng_line = f"F&G: {r['fng']}"
    if r["direction"] == "DOWN" and r["fng"] >= FNG_GREED:
        fng_line += " (Greed -- confirmed trap)"
    elif r["direction"] == "UP" and r["fng"] <= FNG_FEAR:
        fng_line += " (Fear -- confirmed trap)"

    return (
        f"{r['asset']} {arrow} {bias} -- Crowd Fade signal {stars}\n\n"
        f"{crowd_pct:.1f}% of traders are {crowd}. "
        f"Funding {r['avg_funding']*100:+.3f}%/8h. Crowd is paying for it.\n\n"
        f"Entry: ${r['price']:,.0f} | Target: ${r['target_price']:,.0f} "
        f"(+{r['target_pct']:.0f}% / {r['timeframe']})\n"
        f"{fng_line}\n\n"
        f"When everyone is on one side, the pain trade goes the other way."
    )


_CF_GATE_N      = 6     # look at last N resolved crowd_fade calls
_CF_GATE_THRESH = 0.40  # pause if win rate drops below 40%
_CF_STALE_DAYS  = 14    # auto-reset if no crowd_fade call in 14+ days


def _crowd_fade_win_rate_ok() -> bool:
    """
    Returns False (gate CLOSED) when recent crowd_fade win rate is below threshold.
    Stale-reset: if no crowd_fade fired in 14+ days, re-open regardless of rate.
    """
    cf_resolved = [
        c for c in _load_calls()
        if c.get("call_type") == "crowd_fade"
        and c.get("resolved")
        and c.get("outcome") in ("WIN", "LOSS")
    ]
    if len(cf_resolved) < _CF_GATE_N:
        return True  # not enough history to judge

    recent = cf_resolved[-_CF_GATE_N:]
    wr = sum(1 for c in recent if c["outcome"] == "WIN") / _CF_GATE_N
    if wr >= _CF_GATE_THRESH:
        return True

    # Win rate below threshold — check for stale reset
    all_cf = [c for c in _load_calls() if c.get("call_type") == "crowd_fade"]
    last_call = max(all_cf, key=lambda c: c.get("id", 0)) if all_cf else None
    days_since = 9999
    if last_call:
        try:
            ldt = datetime.strptime(last_call["made_at"][:16], "%Y-%m-%d %H:%M")
            days_since = (datetime.now(timezone.utc).replace(tzinfo=None) - ldt).days
        except Exception:
            pass

    if days_since >= _CF_STALE_DAYS:
        print(
            f"[CrowdFade] WIN-RATE GATE stale ({days_since}d since last call, "
            f"wr={wr:.0%} on last {_CF_GATE_N}) -- auto-reset. Resuming."
        )
        return True

    print(
        f"[CrowdFade] WIN-RATE GATE CLOSED: {wr:.0%} on last {_CF_GATE_N} calls "
        f"(threshold {_CF_GATE_THRESH:.0%}). Pausing until rate recovers or "
        f"{_CF_STALE_DAYS - days_since}d stale reset."
    )
    return False


def run_crowd_fade(assets: list = None, dry: bool = False) -> list:
    assets = [a.upper() for a in (assets or _ASSETS)]
    fired  = []
    print(f"\n[CrowdFade] Scan | {datetime.now(timezone.utc).strftime('%H:%M UTC')}")
    from bitwarden import load_all_secrets
    load_all_secrets()
    from octo_x_poster import queue_post, process_queue

    if not dry and not _crowd_fade_win_rate_ok():
        return []

    # Fleet circuit breaker (stricter, on-chain-only): the legacy gate above has a 14d stale-reset
    # that let clustered losing runs slip through. This pauses crowd_fade when its blockchain record
    # is <=30% or on a 5-loss streak, with a shorter dormant-probe escape. crowd_fade fades the
    # crowd -- a losing trade when the crowd is long AND right (trending market).
    if not dry:
        try:
            from octo_calls import strategy_should_pause
            _pause, _reason = strategy_should_pause("crowd_fade")
            if _pause:
                print(f"[CrowdFade] CIRCUIT BREAKER: {_reason}. Skipping run.")
                return []
        except Exception as _e:
            print(f"[CrowdFade] breaker check failed (continuing): {_e}")

    for asset in assets:
        if _has_open_call(asset):
            print(f"[CrowdFade] {asset}: open call exists -- skip")
            continue

        result = score_asset(asset)

        if not result.get("fire"):
            print(f"[CrowdFade] {asset}: PASS -- {result.get('reason')}")
            continue

        print(
            f"[CrowdFade] {asset}: FIRE {result['direction']} | "
            f"L/S={result['long_pct']:.1f}%long | "
            f"funding={result['avg_funding']*100:+.3f}%/8h | "
            f"conviction={result['conviction']}/3"
        )

        if dry:
            print("[CrowdFade] DRY RUN -- not recording or posting")
            fired.append(result)
            continue

        # Record + publish on-chain FIRST, then QUEUE the post -- guaranteed by commit_call_onchain:
        # only queued (and thus posted below) if anchored on-chain; a failed publish rolls back + alerts.
        # process_queue runs after the loop to batch the X posts (rate-limit friendly).
        now  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        call = {
            "call_type":               "crowd_fade",
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

        def _queue():
            queue_post(_post_text(result), post_type="crowd_fade", priority=2)

        from octo_calls import commit_call_onchain
        tx = commit_call_onchain(call, _queue)
        if tx:
            print(f"[CrowdFade] Call #{call['id']} on-chain {tx[:10]}... + queued")
            fired.append(result)
        else:
            print(f"[CrowdFade] {asset}: on-chain publish failed -- NOT queued/posted (alerted)")
        time.sleep(2)

    # Flush all queued crowd_fade posts with 30s gap between them
    if fired:
        for i in range(len(fired)):
            posted = process_queue(max_posts=1, force=True)
            if posted and i < len(fired) - 1:
                print("[CrowdFade] Waiting 30s before next post (X rate limit)...")
                time.sleep(30)

    print(f"[CrowdFade] Done -- {len(fired)} calls fired\n")
    return fired


def print_scores(assets: list = None):
    assets = [a.upper() for a in (assets or _ASSETS)]
    print(f"\n[CrowdFade] Levels | {datetime.now(timezone.utc).strftime('%H:%M UTC')}\n")
    for asset in assets:
        r = score_asset(asset)
        fire   = "FIRE" if r.get("fire") else "PASS"
        detail = r.get("reason") or (
            f"{r['direction']} | L/S={r['long_pct']:.1f}% | "
            f"funding={r['avg_funding']*100:+.3f}%/8h | conviction={r['conviction']}/3"
        )
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
        run_crowd_fade(targets, dry=args.dry)

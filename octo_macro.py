"""
octo_macro.py -- Cross-Asset Macro Signals for Octodamus

Pulls 5 FRED series and scores them as crypto tailwinds/headwinds:
  T10Y2Y   -- Yield curve (10yr minus 2yr spread)
  DTWEXBGS -- USD broad dollar index (trade-weighted)
  SP500    -- S&P 500 index level
  VIXCLS   -- CBOE VIX volatility index
  M2SL     -- M2 money supply (monthly)

Scoring (each: -1 bearish / 0 neutral / +1 bullish for crypto):
  Yield curve:  < -0.20 inverted = -1  |  > +0.20 = +1
  Dollar (5d):  > +0.5% rising   = -1  |  < -0.5% = +1
  SPX (5d):     > +1.0% rising   = +1  |  < -1.0% = -1
  VIX level:    < 15 = +1        |  > 25 = -1
  M2 (mom):     positive = +1    |  negative = -1

Aggregate: sum of 5 scores
  >= +2  -> RISK-ON
  <= -2  -> RISK-OFF
  else   -> NEUTRAL

Cache: data/macro_cache.json (refresh every 4 hours)

Usage:
  python octo_macro.py           # full signal table
  python octo_macro.py --context # one-liner for prompt injection
  python octo_macro.py --json    # raw JSON
"""

import argparse
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

log = logging.getLogger("OctoMacro")

CACHE_FILE = Path(r"C:\Users\walli\octodamus\data\macro_cache.json")
CACHE_TTL_HOURS = 4

# FRED series IDs
SERIES = {
    "yield_curve": "T10Y2Y",    # 10yr - 2yr, daily
    "dollar":      "DTWEXBGS",  # USD broad index, daily
    "spx":         "SP500",     # S&P 500, daily
    "vix":         "VIXCLS",    # VIX, daily
    "m2":          "M2SL",      # M2 money supply, monthly
}

# Scoring thresholds
YIELD_CURVE_BULL  =  0.20   # > +0.20 -> risk-on
YIELD_CURVE_BEAR  = -0.20   # < -0.20 -> inverted risk-off
DOLLAR_CHANGE_PCT =  0.005  # +/-0.5% 5-day change
SPX_CHANGE_PCT    =  0.010  # +/-1.0% 5-day change
VIX_BULL          = 15.0    # below = fear subdued
VIX_BEAR          = 25.0    # above = fear elevated


def _load_secrets() -> dict:
    try:
        p = Path(r"C:\Users\walli\octodamus\.octo_secrets")
        d = json.loads(p.read_text(encoding="utf-8"))
        return d.get("secrets", d)
    except Exception:
        return {}


def _get_fred_key() -> str:
    s = _load_secrets()
    key = s.get("FRED_API_KEY", "")
    if not key:
        import os
        key = os.environ.get("FRED_API_KEY", "")
    return key


def _load_cache() -> dict:
    try:
        if CACHE_FILE.exists():
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_cache(data: dict):
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _cache_fresh(cache: dict) -> bool:
    ts = cache.get("fetched_at")
    if not ts:
        return False
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(ts)
        return age.total_seconds() < CACHE_TTL_HOURS * 3600
    except Exception:
        return False


def _score_yield_curve(val: float) -> tuple:
    if val < YIELD_CURVE_BEAR:
        return -1, f"inverted ({val:+.2f})"
    if val > YIELD_CURVE_BULL:
        return +1, f"normal ({val:+.2f})"
    return 0, f"flat ({val:+.2f})"


def _score_dollar(now: float, prev: float) -> tuple:
    if prev == 0:
        return 0, "no prior data"
    chg = (now - prev) / prev
    if chg > DOLLAR_CHANGE_PCT:
        return -1, f"rising {chg:+.1%} 5d (headwind)"
    if chg < -DOLLAR_CHANGE_PCT:
        return +1, f"falling {chg:+.1%} 5d (tailwind)"
    return 0, f"flat {chg:+.1%} 5d"


def _score_spx(now: float, prev: float) -> tuple:
    if prev == 0:
        return 0, "no prior data"
    chg = (now - prev) / prev
    if chg > SPX_CHANGE_PCT:
        return +1, f"up {chg:+.1%} 5d (risk appetite)"
    if chg < -SPX_CHANGE_PCT:
        return -1, f"down {chg:+.1%} 5d (risk-off)"
    return 0, f"flat {chg:+.1%} 5d"


def _score_vix(val: float) -> tuple:
    if val < VIX_BULL:
        return +1, f"{val:.1f} (fear subdued)"
    if val > VIX_BEAR:
        return -1, f"{val:.1f} (fear elevated)"
    return 0, f"{val:.1f} (neutral)"


def _score_m2(now: float, prev: float) -> tuple:
    if prev == 0:
        return 0, "no prior data"
    chg = (now - prev) / prev
    if chg > 0:
        return +1, f"expanding {chg:+.2%} mom (liquidity)"
    return -1, f"contracting {chg:+.2%} mom"


def fetch_macro() -> dict:
    """Fetch all FRED series, score them, return full signal dict."""
    key = _get_fred_key()
    if not key:
        log.error("FRED_API_KEY not found in secrets")
        return {"status": "unavailable", "signal": "NEUTRAL", "brief": "", "score": 0}

    try:
        from fredapi import Fred
        fred = Fred(api_key=key)
    except ImportError:
        log.error("fredapi not installed -- run: pip install fredapi")
        return {"status": "unavailable", "signal": "NEUTRAL", "brief": "", "score": 0}

    results = {}
    raw = {}

    # Yield curve
    try:
        s = fred.get_series(SERIES["yield_curve"], observation_start=(datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d"))
        s = s.dropna()
        val = float(s.iloc[-1])
        raw["yield_curve"] = val
        score, note = _score_yield_curve(val)
        results["yield_curve"] = {"score": score, "note": note, "label": "Yield Curve (10yr-2yr)"}
    except Exception as e:
        log.warning(f"Yield curve fetch failed: {e}")
        results["yield_curve"] = {"score": 0, "note": "unavailable", "label": "Yield Curve (10yr-2yr)"}

    # Dollar index
    try:
        s = fred.get_series(SERIES["dollar"], observation_start=(datetime.now() - timedelta(days=15)).strftime("%Y-%m-%d"))
        s = s.dropna()
        now_val  = float(s.iloc[-1])
        prev_val = float(s.iloc[-6]) if len(s) >= 6 else 0.0
        raw["dollar_now"]  = now_val
        raw["dollar_prev"] = prev_val
        score, note = _score_dollar(now_val, prev_val)
        results["dollar"] = {"score": score, "note": note, "label": "USD Index (broad)"}
    except Exception as e:
        log.warning(f"Dollar index fetch failed: {e}")
        results["dollar"] = {"score": 0, "note": "unavailable", "label": "USD Index (broad)"}

    # S&P 500
    try:
        s = fred.get_series(SERIES["spx"], observation_start=(datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d"))
        s = s.dropna()
        now_val  = float(s.iloc[-1])
        prev_val = float(s.iloc[-6]) if len(s) >= 6 else 0.0
        raw["spx_now"]  = now_val
        raw["spx_prev"] = prev_val
        score, note = _score_spx(now_val, prev_val)
        results["spx"] = {"score": score, "note": note, "label": "S&P 500"}
    except Exception as e:
        log.warning(f"SPX fetch failed: {e}")
        results["spx"] = {"score": 0, "note": "unavailable", "label": "S&P 500"}

    # VIX
    try:
        s = fred.get_series(SERIES["vix"], observation_start=(datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d"))
        s = s.dropna()
        val = float(s.iloc[-1])
        raw["vix"] = val
        score, note = _score_vix(val)
        results["vix"] = {"score": score, "note": note, "label": "VIX"}
    except Exception as e:
        log.warning(f"VIX fetch failed: {e}")
        results["vix"] = {"score": 0, "note": "unavailable", "label": "VIX"}

    # M2 money supply (monthly -- use last 3 months)
    try:
        s = fred.get_series(SERIES["m2"], observation_start=(datetime.now() - timedelta(days=100)).strftime("%Y-%m-%d"))
        s = s.dropna()
        now_val  = float(s.iloc[-1])
        prev_val = float(s.iloc[-2]) if len(s) >= 2 else 0.0
        raw["m2_now"]  = now_val
        raw["m2_prev"] = prev_val
        score, note = _score_m2(now_val, prev_val)
        results["m2"] = {"score": score, "note": note, "label": "M2 Money Supply"}
    except Exception as e:
        log.warning(f"M2 fetch failed: {e}")
        results["m2"] = {"score": 0, "note": "unavailable", "label": "M2 Money Supply"}

    total_score = sum(r["score"] for r in results.values())

    if total_score >= 2:
        signal = "RISK-ON"
    elif total_score <= -2:
        signal = "RISK-OFF"
    else:
        signal = "NEUTRAL"

    # Build brief
    on_items  = [r["label"] for r in results.values() if r["score"] == +1]
    off_items = [r["label"] for r in results.values() if r["score"] == -1]

    brief_parts = []
    if on_items:
        brief_parts.append(f"tailwinds: {', '.join(on_items)}")
    if off_items:
        brief_parts.append(f"headwinds: {', '.join(off_items)}")
    brief = (
        f"Macro score {total_score:+d}/5 -- {signal}. "
        + ("; ".join(brief_parts) if brief_parts else "all neutral")
        + "."
    )

    out = {
        "fetched_at":   datetime.now(timezone.utc).isoformat(),
        "status":       "live",
        "signal":       signal,
        "score":        total_score,
        "indicators":   results,
        "raw":          raw,
        "brief":        brief,
    }
    _save_cache(out)
    log.info(f"Macro fetched: {signal} score={total_score:+d}")
    return out


def get_macro_signal() -> dict:
    """Returns cached macro signal, refreshing if older than 4 hours."""
    cache = _load_cache()
    if _cache_fresh(cache) and cache.get("status") == "live":
        return cache
    return fetch_macro()


def get_macro_context() -> str:
    """One-block macro context for Octodamus prompts."""
    sig = get_macro_signal()
    if sig.get("status") != "live":
        return ""

    lines = [f"CROSS-ASSET MACRO: {sig['signal']} (score {sig['score']:+d}/5)"]
    for name, ind in sig.get("indicators", {}).items():
        arrow = "+" if ind["score"] == 1 else ("-" if ind["score"] == -1 else " ")
        lines.append(f"  [{arrow}] {ind['label']}: {ind['note']}")
    lines.append(f"  >> {sig['brief']}")
    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--context", action="store_true", help="One-liner for prompt injection")
    parser.add_argument("--json",    action="store_true", help="Raw JSON output")
    parser.add_argument("--refresh", action="store_true", help="Force refresh (ignore cache)")
    args = parser.parse_args()

    if args.refresh:
        sig = fetch_macro()
    else:
        sig = get_macro_signal()

    if args.json:
        print(json.dumps(sig, indent=2))
        return

    if args.context:
        print(get_macro_context())
        return

    # Full table
    print(f"\n{'='*52}")
    print(f" CROSS-ASSET MACRO SIGNAL")
    print(f" {datetime.now().strftime('%Y-%m-%d %H:%M')} | Status: {sig.get('status','?').upper()}")
    print(f"{'='*52}")
    for name, ind in sig.get("indicators", {}).items():
        arrow = "[+]" if ind["score"] == 1 else ("[-]" if ind["score"] == -1 else "[ ]")
        print(f" {arrow} {ind['label']:<25} {ind['note']}")
    print(f"{'='*52}")
    print(f" SCORE: {sig.get('score',0):+d}/5   SIGNAL: {sig.get('signal','?')}")
    print(f" {sig.get('brief','')}")
    print(f"{'='*52}\n")


if __name__ == "__main__":
    main()

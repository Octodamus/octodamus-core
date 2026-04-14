"""
octo_unusual_whales.py -- Options Flow & Dark Pool Signals for Octodamus

Source: Unusual Whales API (unusualwhales.com, ~$50/mo subscription)
API docs: https://api.unusualwhales.com/docs

Signals pulled:
  1. Options flow alerts -- high-conviction unusual options activity
     Large premium, high OTM, sweeps, blocks -- institutional tells
  2. Dark pool prints -- off-exchange block trades
     Bullish/bearish conviction from non-retail order flow
  3. Market tide -- net options flow direction (call $ vs put $)

Cache: data/unusual_whales_cache.json (refresh every 15 min)

To activate:
  1. Subscribe at unusualwhales.com (API plan ~$50/mo)
  2. Add UNUSUAL_WHALES_API_KEY to .octo_secrets or Bitwarden
     Bitwarden entry name: "AGENT - Octodamus - Unusual Whales API"
  3. Run: python octo_unusual_whales.py --test

Usage:
  python octo_unusual_whales.py          # print full signal
  python octo_unusual_whales.py --flow   # options flow only
  python octo_unusual_whales.py --dark   # dark pool only
  python octo_unusual_whales.py --test   # test API key
"""

import argparse
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

log = logging.getLogger("OctoUW")

CACHE_FILE   = Path(r"C:\Users\walli\octodamus\data\unusual_whales_cache.json")
CACHE_TTL    = 15 * 60   # 15 minutes (flow data moves fast)
BASE_URL     = "https://api.unusualwhales.com"

# Crypto-relevant tickers to watch for options flow
CRYPTO_TICKERS = ["IBIT", "ETHA", "FBTC", "COIN", "MSTR", "HOOD", "BITO"]
# IBIT/ETHA/FBTC = BTC/ETH ETFs, COIN = Coinbase, MSTR = MicroStrategy


def _load_secrets() -> dict:
    try:
        p = Path(r"C:\Users\walli\octodamus\.octo_secrets")
        d = json.loads(p.read_text(encoding="utf-8"))
        return d.get("secrets", d)
    except Exception:
        return {}


def _get_api_key() -> str:
    import os
    key = os.environ.get("UNUSUAL_WHALES_API_KEY", "")
    if not key:
        key = _load_secrets().get("UNUSUAL_WHALES_API_KEY", "")
    return key


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_get_api_key()}",
        "Accept": "application/json",
    }


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


def _cache_fresh(cache: dict, key: str = "fetched_at") -> bool:
    ts = cache.get(key)
    if not ts:
        return False
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds()
        return age < CACHE_TTL
    except Exception:
        return False


# ── API calls ─────────────────────────────────────────────────────────────────

def fetch_flow_alerts(limit: int = 20) -> list:
    """
    Fetch recent high-conviction options flow alerts.
    Filters to crypto-adjacent tickers (IBIT, COIN, MSTR, etc.).
    """
    try:
        r = requests.get(
            f"{BASE_URL}/api/option-trades/flow-alerts",
            headers=_headers(),
            params={"limit": 50},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        alerts = data if isinstance(data, list) else data.get("data", [])

        # Score and filter
        scored = []
        for a in alerts:
            ticker = (a.get("ticker") or a.get("symbol") or "").upper()
            side   = (a.get("put_call") or a.get("type") or "").upper()
            prem   = a.get("premium") or a.get("total_premium") or 0
            try:
                prem = float(str(prem).replace(",", ""))
            except Exception:
                prem = 0

            # Include crypto-adjacent tickers OR any large premium ($1M+)
            if ticker not in CRYPTO_TICKERS and prem < 1_000_000:
                continue

            scored.append({
                "ticker":    ticker,
                "side":      side,
                "premium":   prem,
                "expiry":    a.get("expiry") or a.get("expiration_date", ""),
                "strike":    a.get("strike_price") or a.get("strike", ""),
                "sentiment": a.get("sentiment") or ("bullish" if side == "CALL" else "bearish"),
                "sweep":     a.get("is_sweep") or a.get("order_type", "").lower() == "sweep",
            })

        scored.sort(key=lambda x: x["premium"], reverse=True)
        return scored[:limit]

    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 401:
            log.error("Unusual Whales: invalid or missing API key")
        elif e.response.status_code == 403:
            log.error("Unusual Whales: key does not have access to this endpoint")
        else:
            log.error(f"Unusual Whales flow alerts error: {e}")
        return []
    except Exception as e:
        log.error(f"Unusual Whales flow alerts error: {e}")
        return []


def fetch_darkpool(limit: int = 10) -> list:
    """
    Fetch recent dark pool prints for crypto-adjacent tickers.
    """
    try:
        r = requests.get(
            f"{BASE_URL}/api/darkpool/recent",
            headers=_headers(),
            params={"limit": 50},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        prints = data if isinstance(data, list) else data.get("data", [])

        filtered = []
        for p in prints:
            ticker = (p.get("ticker") or p.get("symbol") or "").upper()
            if ticker not in CRYPTO_TICKERS:
                continue
            size = p.get("size") or p.get("quantity") or 0
            price = p.get("price") or p.get("executed_price") or 0
            try:
                notional = float(size) * float(price)
            except Exception:
                notional = 0
            filtered.append({
                "ticker":   ticker,
                "size":     size,
                "price":    price,
                "notional": notional,
                "side":     (p.get("side") or p.get("sentiment") or "unknown").lower(),
                "time":     p.get("executed_at") or p.get("time") or "",
            })

        filtered.sort(key=lambda x: x["notional"], reverse=True)
        return filtered[:limit]

    except requests.exceptions.HTTPError as e:
        if e.response.status_code in (401, 403):
            log.error("Unusual Whales dark pool: auth error")
        else:
            log.error(f"Unusual Whales dark pool error: {e}")
        return []
    except Exception as e:
        log.error(f"Unusual Whales dark pool error: {e}")
        return []


def fetch_market_tide() -> dict:
    """
    Net options flow direction: call $ premium vs put $ premium.
    Returns bullish/bearish/neutral with net flow value.
    """
    try:
        r = requests.get(
            f"{BASE_URL}/api/market/market-tide",
            headers=_headers(),
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        tide = data if isinstance(data, dict) else data.get("data", {})

        call_prem = float(tide.get("call_premium") or tide.get("calls_premium") or 0)
        put_prem  = float(tide.get("put_premium")  or tide.get("puts_premium")  or 0)

        if call_prem + put_prem == 0:
            return {"signal": "NEUTRAL", "net": 0, "call_prem": 0, "put_prem": 0}

        net_ratio = (call_prem - put_prem) / (call_prem + put_prem)

        if net_ratio > 0.10:
            signal = "BULLISH"
        elif net_ratio < -0.10:
            signal = "BEARISH"
        else:
            signal = "NEUTRAL"

        return {
            "signal":    signal,
            "net_ratio": round(net_ratio, 3),
            "call_prem": call_prem,
            "put_prem":  put_prem,
        }
    except Exception as e:
        log.error(f"Unusual Whales market tide error: {e}")
        return {"signal": "NEUTRAL", "net": 0, "call_prem": 0, "put_prem": 0}


# ── Aggregated signal ─────────────────────────────────────────────────────────

def get_uw_signal(force: bool = False) -> dict:
    """Fetch + cache all UW signals. Returns combined signal dict."""
    if not _get_api_key():
        return {
            "status": "no_key",
            "signal": "NEUTRAL",
            "brief":  "",
            "flow_alerts": [],
            "darkpool": [],
            "tide": {},
        }

    cache = _load_cache()
    if not force and _cache_fresh(cache):
        return cache

    flow   = fetch_flow_alerts()
    dark   = fetch_darkpool()
    tide   = fetch_market_tide()

    # Score: call sweeps = bullish, put sweeps = bearish
    bull_flow = sum(1 for a in flow if a["sentiment"] == "bullish")
    bear_flow = sum(1 for a in flow if a["sentiment"] == "bearish")
    tide_sig  = tide.get("signal", "NEUTRAL")

    score = (bull_flow - bear_flow)
    if tide_sig == "BULLISH":
        score += 1
    elif tide_sig == "BEARISH":
        score -= 1

    if score >= 2:
        signal = "RISK-ON"
    elif score <= -2:
        signal = "RISK-OFF"
    else:
        signal = "NEUTRAL"

    # Build brief
    brief_parts = []
    if flow:
        top = flow[0]
        sweep_tag = " SWEEP" if top["sweep"] else ""
        brief_parts.append(
            f"Top flow: {top['ticker']} ${top['premium']/1e6:.1f}M {top['side']}{sweep_tag} "
            f"({top['strike']} {top['expiry']})"
        )
    if tide_sig != "NEUTRAL":
        ratio = tide.get("net_ratio", 0)
        brief_parts.append(f"Market tide: {tide_sig} (net {ratio:+.1%} call/put premium)")
    if dark:
        top_dp = dark[0]
        brief_parts.append(
            f"Dark pool: {top_dp['ticker']} ${top_dp['notional']/1e6:.1f}M "
            f"{top_dp['side']} print"
        )

    brief = " | ".join(brief_parts) if brief_parts else "No significant flow detected."

    out = {
        "fetched_at":   datetime.now(timezone.utc).isoformat(),
        "status":       "live",
        "signal":       signal,
        "score":        score,
        "flow_alerts":  flow,
        "darkpool":     dark,
        "tide":         tide,
        "brief":        brief,
    }
    _save_cache(out)
    log.info(f"UW signal: {signal} | score={score} | flow={len(flow)} alerts | dark={len(dark)} prints")
    return out


def get_uw_context() -> str:
    """One-block Unusual Whales context for Octodamus prompts."""
    sig = get_uw_signal()
    if sig.get("status") == "no_key":
        return ""
    if sig.get("status") != "live":
        return ""

    lines = [f"OPTIONS FLOW & DARK POOL: {sig['signal']}"]

    for a in sig.get("flow_alerts", [])[:5]:
        sweep = " [SWEEP]" if a.get("sweep") else ""
        lines.append(
            f"  {a['ticker']} ${a['premium']/1e6:.1f}M {a['side']}{sweep} "
            f"| {a['strike']} exp {a['expiry']} | {a['sentiment'].upper()}"
        )

    tide = sig.get("tide", {})
    if tide.get("signal", "NEUTRAL") != "NEUTRAL":
        lines.append(f"  Market tide: {tide['signal']} ({tide.get('net_ratio',0):+.1%} net call/put)")

    for dp in sig.get("darkpool", [])[:3]:
        lines.append(
            f"  Dark pool: {dp['ticker']} ${dp['notional']/1e6:.1f}M {dp['side']}"
        )

    lines.append(f"  >> {sig['brief']}")
    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--flow",    action="store_true", help="Options flow only")
    parser.add_argument("--dark",    action="store_true", help="Dark pool only")
    parser.add_argument("--context", action="store_true", help="Prompt-ready one-block context")
    parser.add_argument("--test",    action="store_true", help="Test API key connectivity")
    parser.add_argument("--refresh", action="store_true", help="Force refresh cache")
    args = parser.parse_args()

    key = _get_api_key()
    if not key:
        print("ERROR: UNUSUAL_WHALES_API_KEY not found.")
        print("  1. Subscribe at unusualwhales.com (~$50/mo)")
        print("  2. Add key to .octo_secrets as UNUSUAL_WHALES_API_KEY")
        print("     or Bitwarden: 'AGENT - Octodamus - Unusual Whales API'")
        return

    if args.test:
        print(f"Key found: {key[:8]}...")
        tide = fetch_market_tide()
        if tide.get("signal"):
            print(f"API connection: OK -- market tide: {tide['signal']}")
        else:
            print("API connection: FAILED -- check key and subscription tier")
        return

    sig = get_uw_signal(force=args.refresh)

    if args.context:
        print(get_uw_context())
        return

    if args.flow:
        print(f"\n--- Options Flow Alerts (crypto-adjacent) ---")
        for a in sig.get("flow_alerts", []):
            sweep = " [SWEEP]" if a.get("sweep") else ""
            print(f"  {a['ticker']:6} ${a['premium']/1e6:.2f}M {a['side']:5}{sweep:8} | {a['strike']} {a['expiry']} | {a['sentiment'].upper()}")
        if not sig.get("flow_alerts"):
            print("  (no significant flow)")
        return

    if args.dark:
        print(f"\n--- Dark Pool Prints (crypto-adjacent) ---")
        for dp in sig.get("darkpool", []):
            print(f"  {dp['ticker']:6} ${dp['notional']/1e6:.1f}M {dp['side']}")
        if not sig.get("darkpool"):
            print("  (no prints)")
        return

    # Full summary
    print(f"\n{'='*52}")
    print(f" UNUSUAL WHALES SIGNAL: {sig.get('signal','?')}")
    print(f" Score: {sig.get('score',0):+d}")
    print(f"{'='*52}")
    print(f" Flow alerts:  {len(sig.get('flow_alerts',[]))} (crypto-adjacent)")
    print(f" Dark pool:    {len(sig.get('darkpool',[]))} prints")
    tide = sig.get("tide", {})
    print(f" Market tide:  {tide.get('signal','?')}")
    print(f"\n {sig.get('brief','')}")
    print(f"{'='*52}\n")


if __name__ == "__main__":
    main()

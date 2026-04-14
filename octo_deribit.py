"""
octo_deribit.py — Deribit Options Intelligence Module

Reads the options market: max pain, put/call ratio, IV skew, and large OI strikes.
No API key required — Deribit public endpoints are free.

Options give Octodamus the signal that futures can't: where institutional money
has placed bets on specific price levels by a specific date. Max pain is a
gravity well — price tends to drift toward the level where the most options
expire worthless, screwing the most buyers.

Usage:
    from octo_deribit import deribit

    ctx = deribit.build_oracle_context("BTC")  # formatted string for prompts
    summary = deribit.options_summary("BTC")   # structured dict

CLI:
    python octo_deribit.py btc
    python octo_deribit.py eth
"""

import logging
import time
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger("OctoDeribit")

BASE_URL = "https://www.deribit.com/api/v2/public"

# Cache: options data is stable for 5 minutes
_cache: dict = {}
CACHE_TTL = 300  # seconds


# ── HTTP ──────────────────────────────────────────────────────────────────────

def _get(endpoint: str, params: dict = None) -> dict:
    import httpx
    cache_key = endpoint + str(sorted((params or {}).items()))
    cached = _cache.get(cache_key)
    if cached and (time.time() - cached["ts"]) < CACHE_TTL:
        return cached["data"]
    try:
        r = httpx.get(
            f"{BASE_URL}/{endpoint}",
            params=params or {},
            timeout=15,
        )
        if r.status_code != 200:
            log.error(f"Deribit {endpoint}: HTTP {r.status_code}")
            return {}
        body = r.json()
        data = body.get("result", {})
        _cache[cache_key] = {"ts": time.time(), "data": data}
        return data
    except Exception as e:
        log.error(f"Deribit {endpoint}: {e}")
        return {}


# ── Raw Data ──────────────────────────────────────────────────────────────────

def get_spot_price(currency: str = "BTC") -> float:
    """Current index price from Deribit."""
    data = _get("get_index_price", {"index_name": f"{currency.lower()}_usd"})
    return float(data.get("index_price", 0))


def get_book_summary(currency: str = "BTC") -> list:
    """
    All active options with OI, IV, volume, bid/ask.
    Each item has: instrument_name, open_interest, mark_iv, underlying_price,
                   bid_price, ask_price, volume_usd
    """
    data = _get("get_book_summary_by_currency", {"currency": currency.upper(), "kind": "option"})
    return data if isinstance(data, list) else []


# ── Parsing ───────────────────────────────────────────────────────────────────

def _parse_instrument(name: str) -> Optional[dict]:
    """
    Parse 'BTC-25APR26-70000-C' into structured dict.
    Returns None if parse fails.
    """
    try:
        parts = name.split("-")
        if len(parts) != 4:
            return None
        currency, expiry_str, strike_str, opt_type = parts
        strike = float(strike_str)
        opt_type = opt_type.upper()
        if opt_type not in ("C", "P"):
            return None

        # Parse expiry: "25APR26" -> date
        expiry_dt = datetime.strptime(expiry_str, "%d%b%y").replace(
            hour=8, minute=0, tzinfo=timezone.utc  # Deribit settles at 08:00 UTC
        )
        return {
            "currency": currency,
            "expiry_str": expiry_str,
            "expiry_dt": expiry_dt,
            "strike": strike,
            "type": opt_type,
        }
    except Exception:
        return None


# ── Max Pain ──────────────────────────────────────────────────────────────────

def _calc_max_pain(options: list, spot: float) -> float:
    """
    Max pain = strike where total payout to option *buyers* is minimized.
    i.e. the price that makes sellers (writers) most profitable.

    options: list of dicts with keys: strike, type (C/P), oi
    spot: current price (used to bound the test range)
    Returns the max pain strike price.
    """
    if not options:
        return 0.0

    strikes = sorted(set(o["strike"] for o in options))
    if len(strikes) < 2:
        return strikes[0] if strikes else 0.0

    min_pain = float("inf")
    max_pain_strike = strikes[0]

    for test_price in strikes:
        total_pain = 0.0
        for opt in options:
            oi = opt["oi"]
            strike = opt["strike"]
            if opt["type"] == "C":
                # Call buyer profit: max(0, price - strike) * OI
                total_pain += max(0.0, test_price - strike) * oi
            else:
                # Put buyer profit: max(0, strike - price) * OI
                total_pain += max(0.0, strike - test_price) * oi
        if total_pain < min_pain:
            min_pain = total_pain
            max_pain_strike = test_price

    return max_pain_strike


# ── Options Summary ───────────────────────────────────────────────────────────

def options_summary(currency: str = "BTC") -> dict:
    """
    Full options market picture for a currency.

    Returns:
        spot: current price
        expiries: list of expiry summaries, sorted by date, each with:
            expiry_str, days_out, max_pain, put_call_oi_ratio,
            total_call_oi, total_put_oi, top_strikes (by OI),
            atm_call_iv, atm_put_iv, iv_skew (put - call IV, positive = fear)
        overall_put_call_ratio: aggregate across all expiries
    """
    spot = get_spot_price(currency)
    if not spot:
        return {"error": "Could not fetch spot price"}

    summaries = get_book_summary(currency)
    if not summaries:
        return {"error": "Could not fetch book summary"}

    now = datetime.now(timezone.utc)

    # Group by expiry
    expiry_map: dict = {}
    for item in summaries:
        parsed = _parse_instrument(item.get("instrument_name", ""))
        if not parsed:
            continue

        oi = float(item.get("open_interest", 0) or 0)
        iv = float(item.get("mark_iv", 0) or 0)
        vol_usd = float(item.get("volume_usd", 0) or 0)

        key = parsed["expiry_str"]
        if key not in expiry_map:
            expiry_map[key] = {
                "expiry_str": key,
                "expiry_dt": parsed["expiry_dt"],
                "calls": [],
                "puts": [],
            }

        entry = {
            "strike": parsed["strike"],
            "type": parsed["type"],
            "oi": oi,
            "iv": iv,
            "vol_usd": vol_usd,
        }

        if parsed["type"] == "C":
            expiry_map[key]["calls"].append(entry)
        else:
            expiry_map[key]["puts"].append(entry)

    # Build per-expiry summaries
    expiry_summaries = []
    total_call_oi_all = 0.0
    total_put_oi_all = 0.0

    for key, exp in sorted(expiry_map.items(), key=lambda x: x[1]["expiry_dt"]):
        days_out = (exp["expiry_dt"] - now).days
        if days_out < 0:
            continue  # expired
        if days_out > 90:
            continue  # too far out to be directionally useful

        calls = exp["calls"]
        puts = exp["puts"]
        all_opts = calls + puts

        total_call_oi = sum(o["oi"] for o in calls)
        total_put_oi = sum(o["oi"] for o in puts)
        total_call_oi_all += total_call_oi
        total_put_oi_all += total_put_oi

        pc_ratio = round(total_put_oi / total_call_oi, 3) if total_call_oi > 0 else 0

        # Max pain
        pain_strike = _calc_max_pain(all_opts, spot)

        # Top 5 strikes by total OI (calls + puts combined)
        strike_oi: dict = {}
        for o in all_opts:
            s = o["strike"]
            strike_oi[s] = strike_oi.get(s, 0) + o["oi"]
        top_strikes = sorted(strike_oi.items(), key=lambda x: x[1], reverse=True)[:5]

        # ATM IV (closest strike to spot, call and put separately)
        atm_call_iv = 0.0
        atm_put_iv = 0.0
        if calls:
            atm_call = min(calls, key=lambda o: abs(o["strike"] - spot))
            atm_call_iv = atm_call["iv"]
        if puts:
            atm_put = min(puts, key=lambda o: abs(o["strike"] - spot))
            atm_put_iv = atm_put["iv"]
        iv_skew = round(atm_put_iv - atm_call_iv, 2)

        expiry_summaries.append({
            "expiry_str": key,
            "days_out": days_out,
            "max_pain": pain_strike,
            "max_pain_distance_pct": round((pain_strike - spot) / spot * 100, 2) if spot else 0,
            "put_call_oi_ratio": pc_ratio,
            "total_call_oi": round(total_call_oi, 1),
            "total_put_oi": round(total_put_oi, 1),
            "top_strikes": [(int(s), round(oi, 1)) for s, oi in top_strikes],
            "atm_call_iv": round(atm_call_iv, 1),
            "atm_put_iv": round(atm_put_iv, 1),
            "iv_skew": iv_skew,
        })

    overall_pc = round(total_put_oi_all / total_call_oi_all, 3) if total_call_oi_all > 0 else 0

    return {
        "currency": currency.upper(),
        "spot": spot,
        "as_of": now.strftime("%Y-%m-%d %H:%M UTC"),
        "overall_put_call_ratio": overall_pc,
        "expiries": expiry_summaries,
    }


# ── Oracle Context ────────────────────────────────────────────────────────────

def build_oracle_context(currency: str = "BTC") -> str:
    """
    Formatted options intelligence for Claude prompts.
    Focuses on near-term expiries (next 30 days) where options have
    the most gravitational pull on price.
    """
    summary = options_summary(currency)
    if "error" in summary:
        return f"[Deribit] {summary['error']}"

    spot = summary["spot"]
    lines = [f"=== DERIBIT OPTIONS INTELLIGENCE: {currency} ===\n"]
    lines.append(f"Spot: ${spot:,.0f}  |  As of: {summary['as_of']}")

    pc = summary["overall_put_call_ratio"]
    pc_signal = "BEARISH (put heavy)" if pc > 1.2 else "BULLISH (call heavy)" if pc < 0.8 else "NEUTRAL"
    lines.append(f"Overall Put/Call OI Ratio: {pc:.2f} -> {pc_signal}\n")

    expiries = summary["expiries"]
    if not expiries:
        lines.append("No near-term expiries found.")
        return "\n".join(lines)

    for exp in expiries[:4]:  # show up to 4 nearest expiries
        days = exp["days_out"]
        expiry = exp["expiry_str"]
        pain = exp["max_pain"]
        pain_dist = exp["max_pain_distance_pct"]
        pain_dir = "above" if pain > spot else "below"
        pc_e = exp["put_call_oi_ratio"]
        skew = exp["iv_skew"]
        skew_signal = "FEAR (puts bid up)" if skew > 3 else "GREED (calls bid up)" if skew < -3 else "BALANCED"

        lines.append(f"-- {expiry} ({days}d out) --")
        lines.append(
            f"  Max Pain: ${pain:,.0f}  ({abs(pain_dist):.1f}% {pain_dir} spot)"
            + (" <- EXPIRY MAGNET" if days <= 7 else "")
        )
        lines.append(f"  Put/Call OI: {pc_e:.2f}  |  IV Skew: {skew:+.1f}pt -> {skew_signal}")
        lines.append(f"  Call OI: {exp['total_call_oi']:,.0f} BTC  |  Put OI: {exp['total_put_oi']:,.0f} BTC")

        top = exp["top_strikes"]
        if top:
            strike_str = "  Top strikes (OI): " + "  ".join(f"${s:,} ({oi})" for s, oi in top[:3])
            lines.append(strike_str)
        lines.append("")

    lines.append(
        "NOTE: Max pain acts as a gravitational magnet near expiry — "
        "price tends to converge toward it in the final 24-48h. "
        "High put/call ratio (>1.2) signals hedging or bearish bets. "
        "Positive IV skew means puts are more expensive than calls — market fears downside."
    )

    return "\n".join(lines)


# ── Singleton ─────────────────────────────────────────────────────────────────

class _Deribit:
    def build_oracle_context(self, currency: str = "BTC") -> str:
        return build_oracle_context(currency)

    def options_summary(self, currency: str = "BTC") -> dict:
        return options_summary(currency)

    def get_spot_price(self, currency: str = "BTC") -> float:
        return get_spot_price(currency)


deribit = _Deribit()


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    currency = sys.argv[1].upper() if len(sys.argv) > 1 else "BTC"
    print(build_oracle_context(currency))

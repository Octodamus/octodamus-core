"""
octo_despxa.py — deSPXA (Tokenized S&P 500) Volume Tracker
Tracks the USDC-deSPXA pool on Aerodrome (Base) via DefiLlama.

Signal: Rising volume in down markets = institutional demand for on-chain equities.
Used as a macro signal in Octodamus oracle calls and daily reads.

Pool: USDC-deSPXA on Aerodrome Slipstream (Base)
DefiLlama pool ID: f331d86f-6aae-4576-8f4d-d24f9bc2f883
"""

import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx

# ── Config ────────────────────────────────────────────────────────────────────

DESPXA_POOL_ID   = "f331d86f-6aae-4576-8f4d-d24f9bc2f883"
HISTORY_FILE     = Path(__file__).parent / "data" / "despxa_history.json"
CACHE_TTL        = 3600          # refresh once per hour
VOLUME_SPIKE     = 1_000_000     # $1M+ daily volume = notable
VOLUME_STRONG    = 500_000       # $500K+ = healthy demand
VOLUME_WEAK      = 100_000       # <$100K = fading interest

# ── DefiLlama fetch ───────────────────────────────────────────────────────────

def _fetch_pool_data() -> dict:
    """Fetch current pool snapshot from DefiLlama yields API."""
    try:
        r = httpx.get(
            "https://yields.llama.fi/pools",
            timeout=15,
        )
        r.raise_for_status()
        pools = r.json().get("data", [])
        for p in pools:
            if p.get("pool") == DESPXA_POOL_ID:
                return p
    except Exception as e:
        print(f"[deSPXA] Pool fetch failed: {e}")
    return {}


def _fetch_pool_chart() -> list:
    """Fetch historical daily data from DefiLlama yields chart endpoint."""
    try:
        r = httpx.get(
            f"https://yields.llama.fi/chart/{DESPXA_POOL_ID}",
            timeout=15,
        )
        r.raise_for_status()
        return r.json().get("data", [])
    except Exception as e:
        print(f"[deSPXA] Chart fetch failed: {e}")
    return []


# ── History storage ───────────────────────────────────────────────────────────

def _load_history() -> dict:
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"snapshots": [], "last_fetch": 0}


def _save_history(data: dict) -> None:
    tmp = HISTORY_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(HISTORY_FILE)


# ── Main signal ───────────────────────────────────────────────────────────────

def get_despxa_signal(force: bool = False) -> dict:
    """
    Return current deSPXA signal dict:
    {
        "tvl_usd":        float,
        "volume_1d_usd":  float,
        "volume_7d_usd":  float,
        "apy":            float,
        "apy_base":       float,
        "signal":         "BULL" | "BEAR" | "NEUTRAL",
        "signal_reason":  str,
        "volume_trend":   "RISING" | "FALLING" | "STABLE" | "UNKNOWN",
        "days_tracked":   int,
        "timestamp":      str,
    }
    """
    hist = _load_history()
    now  = time.time()

    # Use cache if fresh
    if not force and (now - hist.get("last_fetch", 0)) < CACHE_TTL:
        if hist.get("snapshots"):
            last = hist["snapshots"][-1]
            last["_cached"] = True
            return last

    # Fetch fresh data
    pool  = _fetch_pool_data()
    chart = _fetch_pool_chart()

    if not pool:
        return {"signal": "NEUTRAL", "signal_reason": "DefiLlama data unavailable", "volume_1d_usd": 0}

    vol_1d  = float(pool.get("volumeUsd1d") or 0)
    vol_7d  = float(pool.get("volumeUsd7d") or 0)
    tvl     = float(pool.get("tvlUsd") or 0)
    apy     = float(pool.get("apy") or 0)
    apy_base = float(pool.get("apyBase") or 0)

    # Volume trend from chart history
    volume_trend = "UNKNOWN"
    if len(chart) >= 3:
        recent_vols = [float(d.get("volumeUsd1d") or 0) for d in chart[-7:] if d.get("volumeUsd1d")]
        if len(recent_vols) >= 2:
            avg_early = sum(recent_vols[:len(recent_vols)//2]) / max(1, len(recent_vols)//2)
            avg_late  = sum(recent_vols[len(recent_vols)//2:]) / max(1, len(recent_vols) - len(recent_vols)//2)
            if avg_early > 0:
                pct_change = (avg_late - avg_early) / avg_early
                if pct_change > 0.15:
                    volume_trend = "RISING"
                elif pct_change < -0.15:
                    volume_trend = "FALLING"
                else:
                    volume_trend = "STABLE"

    # Signal logic:
    # High volume + rising = institutions building on-chain equity exposure (BTC proxy bullish)
    # High volume in down market = strong conviction signal
    # Falling volume = fading interest in RWA thesis
    if vol_1d >= VOLUME_SPIKE and volume_trend in ("RISING", "STABLE", "UNKNOWN"):
        signal        = "BULL"
        signal_reason = f"deSPXA ${vol_1d/1e6:.2f}M daily volume — institutional on-chain equity demand accelerating"
    elif vol_1d >= VOLUME_STRONG:
        signal        = "BULL"
        signal_reason = f"deSPXA ${vol_1d/1e3:.0f}K daily volume — healthy RWA demand on Base"
    elif vol_1d > 0 and vol_1d < VOLUME_WEAK:
        signal        = "BEAR"
        signal_reason = f"deSPXA volume collapsed to ${vol_1d/1e3:.0f}K — institutional interest fading"
    elif volume_trend == "FALLING" and vol_1d < VOLUME_STRONG:
        signal        = "BEAR"
        signal_reason = f"deSPXA volume declining ({volume_trend}) — RWA thesis losing momentum"
    else:
        signal        = "NEUTRAL"
        signal_reason = f"deSPXA ${vol_1d/1e3:.0f}K volume — monitoring"

    snap = {
        "tvl_usd":       round(tvl, 2),
        "volume_1d_usd": round(vol_1d, 2),
        "volume_7d_usd": round(vol_7d, 2),
        "apy":           round(apy, 2),
        "apy_base":      round(apy_base, 2),
        "signal":        signal,
        "signal_reason": signal_reason,
        "volume_trend":  volume_trend,
        "days_tracked":  len(chart),
        "timestamp":     datetime.now(timezone.utc).isoformat(),
        "_cached":       False,
    }

    # Save to history
    hist["snapshots"].append(snap)
    hist["snapshots"] = hist["snapshots"][-90:]  # keep 90 days
    hist["last_fetch"] = now
    _save_history(hist)

    print(f"[deSPXA] vol_1d=${vol_1d/1e3:.0f}K tvl=${tvl/1e6:.2f}M apy={apy:.1f}% signal={signal}")
    return snap


# ── Prompt injection ──────────────────────────────────────────────────────────

def despxa_context_str() -> str:
    """
    Format deSPXA signal as a string for injection into Octodamus prompts.
    Returns empty string if data unavailable.
    """
    try:
        d = get_despxa_signal()
    except Exception:
        return ""

    if not d.get("volume_1d_usd"):
        return ""

    vol_1d  = d["volume_1d_usd"]
    tvl     = d["tvl_usd"]
    apy     = d["apy"]
    trend   = d["volume_trend"]
    signal  = d["signal"]
    days    = d["days_tracked"]

    vol_str = f"${vol_1d/1e6:.2f}M" if vol_1d >= 1_000_000 else f"${vol_1d/1e3:.0f}K"
    tvl_str = f"${tvl/1e6:.2f}M"

    trend_tag = {"RISING": "^ accelerating", "FALLING": "v declining",
                 "STABLE": "- stable", "UNKNOWN": "new/insufficient history"}.get(trend, trend)

    lines = [
        f"\nRWA SIGNAL — deSPXA (Tokenized S&P 500 on Base / Aerodrome):",
        f"  Daily volume:  {vol_str}  [{trend_tag}]",
        f"  Pool TVL:      {tvl_str}",
        f"  Pool APY:      {apy:.1f}%  (base {d['apy_base']:.1f}% + AERO rewards)",
        f"  Days tracked:  {days}",
        f"  Signal:        {signal} — {d['signal_reason']}",
        f"  Interpretation: Rising on-chain S&P 500 volume = institutions hedging/rotating",
        f"  into crypto-native equities exposure. Treat as BTC macro sentiment proxy.",
    ]
    return "\n".join(lines)


# ── Weekly summary for Telegram / Discord ─────────────────────────────────────

def despxa_weekly_report() -> str:
    """Generate a short weekly report for Telegram."""
    hist = _load_history()
    snaps = hist.get("snapshots", [])
    if not snaps:
        return "deSPXA: No data yet."

    last  = snaps[-1]
    week  = snaps[-7:] if len(snaps) >= 7 else snaps

    avg_vol = sum(s["volume_1d_usd"] for s in week if s.get("volume_1d_usd")) / max(1, len(week))
    max_vol = max((s["volume_1d_usd"] for s in week if s.get("volume_1d_usd")), default=0)
    min_vol = min((s["volume_1d_usd"] for s in week if s.get("volume_1d_usd")), default=0)

    def fmt(v):
        return f"${v/1e6:.2f}M" if v >= 1_000_000 else f"${v/1e3:.0f}K"

    return (
        f"deSPXA Weekly (Tokenized S&P 500 on Base)\n"
        f"Today: {fmt(last['volume_1d_usd'])} vol | ${last['tvl_usd']/1e6:.2f}M TVL | {last['apy']:.1f}% APY\n"
        f"7d avg: {fmt(avg_vol)} | peak: {fmt(max_vol)} | low: {fmt(min_vol)}\n"
        f"Trend: {last['volume_trend']} | Signal: {last['signal']}"
    )


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    force = "--force" in sys.argv

    print("Fetching deSPXA signal...")
    sig = get_despxa_signal(force=force)

    print(f"\n{'='*50}")
    print(f"  TVL:          ${sig['tvl_usd']:,.0f}")
    print(f"  Vol (1d):     ${sig['volume_1d_usd']:,.0f}")
    print(f"  Vol (7d):     ${sig['volume_7d_usd']:,.0f}")
    print(f"  APY:          {sig['apy']:.2f}% (base {sig['apy_base']:.2f}%)")
    print(f"  Trend:        {sig['volume_trend']}")
    print(f"  Signal:       {sig['signal']}")
    print(f"  Reason:       {sig['signal_reason']}")
    print(f"  Days tracked: {sig['days_tracked']}")
    print(f"{'='*50}")

    print("\nContext string (for prompt injection):")
    print(despxa_context_str())

    print("\nWeekly report:")
    print(despxa_weekly_report())

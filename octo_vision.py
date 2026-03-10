"""
octo_vision.py
OctoVision — Macro Oracle Mind

Reads macroeconomic data from the FRED API (Federal Reserve Economic Data).
Free API key at: fred.stlouisfed.org/docs/api/api_key.html

Series tracked:
  - FEDFUNDS    Fed Funds Rate
  - CPIAUCSL    CPI (Consumer Price Index, YoY change)
  - M2SL        M2 Money Supply
  - UNRATE      Unemployment Rate
  - T10YIE      10-Year Breakeven Inflation Rate
  - T10Y2Y      10-Year minus 2-Year yield spread (recession signal)
  - DCOILWTICO  WTI Crude Oil Price

Bitwarden key: AGENT - Octodamus - FRED API
Env var:       FRED_API_KEY

Usage:
    from octo_vision import run_macro_scan, format_vision_for_prompt
    macro = run_macro_scan()
"""

import os
import time
import requests
from datetime import datetime, timedelta

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

# Series to track with human labels and units
FRED_SERIES = {
    "FEDFUNDS":   {"label": "Fed Funds Rate",            "unit": "%",      "format": ".2f"},
    "CPIAUCSL":   {"label": "CPI (index level)",         "unit": "",       "format": ".2f"},
    "M2SL":       {"label": "M2 Money Supply",           "unit": "B USD",  "format": ",.0f"},
    "UNRATE":     {"label": "Unemployment Rate",         "unit": "%",      "format": ".1f"},
    "T10YIE":     {"label": "10Y Breakeven Inflation",   "unit": "%",      "format": ".2f"},
    "T10Y2Y":     {"label": "Yield Curve (10Y-2Y)",      "unit": "%",      "format": ".2f"},
    "DCOILWTICO": {"label": "WTI Crude Oil",             "unit": "$/bbl",  "format": ".2f"},
}

# Look back this many days for the latest observation
LOOKBACK_DAYS = 45
_REQUEST_DELAY = 0.3


# ─────────────────────────────────────────────
# FRED FETCH
# ─────────────────────────────────────────────

def _fetch_series(series_id: str, api_key: str) -> dict | None:
    """
    Fetch the most recent observation for a FRED series.
    Returns {"date": str, "value": float} or None on failure.
    """
    start = (datetime.utcnow() - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    try:
        r = requests.get(
            FRED_BASE,
            params={
                "series_id": series_id,
                "api_key": api_key,
                "file_type": "json",
                "sort_order": "desc",
                "limit": 2,  # grab last 2 to compute delta
                "observation_start": start,
            },
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        obs = data.get("observations", [])
        # Filter out missing/non-numeric values
        valid = [o for o in obs if o.get("value") not in (".", "", None)]
        if not valid:
            return None
        latest = valid[0]
        result = {
            "date": latest["date"],
            "value": float(latest["value"]),
        }
        # Compute period-over-period delta if we have 2 data points
        if len(valid) >= 2:
            prev_val = float(valid[1]["value"])
            result["prev_value"] = prev_val
            result["delta"] = round(result["value"] - prev_val, 4)
            result["delta_pct"] = round(
                ((result["value"] - prev_val) / prev_val) * 100, 3
            ) if prev_val != 0 else None
        return result
    except Exception as e:
        print(f"[OctoVision] FRED fetch failed for {series_id}: {e}")
        return None


# ─────────────────────────────────────────────
# MACRO SIGNAL INTERPRETATION
# ─────────────────────────────────────────────

def _interpret_macro(data: dict) -> dict:
    """
    Build signal list and macro regime label from raw FRED data.
    Returns: {"regime": str, "signals": [str], "risk_flags": [str]}
    """
    signals = []
    risk_flags = []

    fed = data.get("FEDFUNDS")
    cpi = data.get("CPIAUCSL")
    unrate = data.get("UNRATE")
    breakeven = data.get("T10YIE")
    yield_curve = data.get("T10Y2Y")
    oil = data.get("DCOILWTICO")
    m2 = data.get("M2SL")

    # ── Fed Funds ──
    if fed:
        rate = fed["value"]
        delta = fed.get("delta", 0)
        if rate >= 5.0:
            signals.append(f"Fed Funds {rate:.2f}% — restrictive territory")
        elif rate <= 2.0:
            signals.append(f"Fed Funds {rate:.2f}% — accommodative stance")
        else:
            signals.append(f"Fed Funds {rate:.2f}% — neutral range")
        if delta < -0.1:
            signals.append(f"Fed cutting: -{abs(delta):.2f}pp last period")
        elif delta > 0.1:
            signals.append(f"Fed hiking: +{delta:.2f}pp last period")

    # ── Breakeven Inflation ──
    if breakeven:
        bi = breakeven["value"]
        if bi > 2.8:
            risk_flags.append(f"Breakeven inflation {bi:.2f}% — market pricing persistent inflation")
        elif bi < 1.8:
            signals.append(f"Breakeven inflation {bi:.2f}% — deflation concern")
        else:
            signals.append(f"Breakeven inflation {bi:.2f}% — anchored near target")

    # ── Yield Curve ──
    if yield_curve:
        yc = yield_curve["value"]
        if yc < 0:
            risk_flags.append(f"Yield curve inverted ({yc:+.2f}%) — historical recession warning")
        elif yc < 0.3:
            signals.append(f"Yield curve flat ({yc:+.2f}%) — watch for inversion")
        else:
            signals.append(f"Yield curve positive ({yc:+.2f}%) — normal slope")

    # ── Unemployment ──
    if unrate:
        ur = unrate["value"]
        delta = unrate.get("delta", 0)
        if ur <= 4.0:
            signals.append(f"Unemployment {ur:.1f}% — near full employment")
        elif ur >= 5.5:
            risk_flags.append(f"Unemployment {ur:.1f}% — labor market cooling")
        if delta > 0.3:
            risk_flags.append(f"Unemployment rising +{delta:.1f}pp — deteriorating")

    # ── Oil ──
    if oil:
        op = oil["value"]
        if op > 90:
            risk_flags.append(f"WTI crude ${op:.0f}/bbl — inflationary pressure")
        elif op < 60:
            signals.append(f"WTI crude ${op:.0f}/bbl — demand concern or oversupply")
        else:
            signals.append(f"WTI crude ${op:.0f}/bbl — range-bound")

    # ── M2 ──
    if m2 and m2.get("delta_pct") is not None:
        m2_chg = m2["delta_pct"]
        if m2_chg > 0.5:
            risk_flags.append(f"M2 expanding +{m2_chg:.2f}% — liquidity injection")
        elif m2_chg < -0.5:
            signals.append(f"M2 contracting {m2_chg:.2f}% — tightening liquidity")

    # ── Regime label ──
    risk_count = len(risk_flags)
    if risk_count >= 3:
        regime = "RISK-OFF"
    elif risk_count == 2:
        regime = "CAUTION"
    elif risk_count == 1:
        regime = "WATCHFUL"
    else:
        regime = "RISK-ON"

    return {
        "regime": regime,
        "signals": signals,
        "risk_flags": risk_flags,
    }


# ─────────────────────────────────────────────
# MAIN SCAN
# ─────────────────────────────────────────────

def run_macro_scan(api_key: str | None = None) -> dict:
    """
    Fetch all FRED series and return structured macro snapshot.

    api_key: if None, reads from FRED_API_KEY env var (set by Bitwarden loader).
    """
    if api_key is None:
        api_key = os.environ.get("FRED_API_KEY")
    if not api_key:
        print("[OctoVision] No FRED_API_KEY found — macro scan skipped.")
        return {"error": "no_api_key", "series": {}, "interpretation": {}}

    print(f"[OctoVision] Fetching {len(FRED_SERIES)} FRED series...")
    series_data = {}

    for series_id, meta in FRED_SERIES.items():
        obs = _fetch_series(series_id, api_key)
        if obs:
            obs["label"] = meta["label"]
            obs["unit"] = meta["unit"]
            series_data[series_id] = obs
            val_fmt = f"{obs['value']:{meta['format']}}"
            delta_str = f" (Δ{obs['delta']:+.4f})" if "delta" in obs else ""
            print(f"  {series_id:12s} {val_fmt} {meta['unit']}{delta_str}")
        else:
            print(f"  {series_id:12s} [no data]")
        time.sleep(_REQUEST_DELAY)

    interpretation = _interpret_macro(series_data)
    print(f"[OctoVision] Macro regime: {interpretation['regime']}")

    return {
        "timestamp": datetime.utcnow().isoformat(),
        "series": series_data,
        "interpretation": interpretation,
    }


def format_vision_for_prompt(result: dict) -> str:
    """Format OctoVision results into a compact prompt string for the LLM."""
    if result.get("error") or not result.get("series"):
        return ""

    interp = result.get("interpretation", {})
    regime = interp.get("regime", "UNKNOWN")
    lines = [f"Macro environment (OctoVision) — Regime: {regime}"]

    series = result.get("series", {})
    for sid, meta in FRED_SERIES.items():
        if sid in series:
            obs = series[sid]
            fmt = meta["format"]
            val_str = f"{obs['value']:{fmt}} {meta['unit']}".strip()
            delta_str = f" Δ{obs['delta']:+.4f}" if "delta" in obs else ""
            lines.append(f"  {meta['label']}: {val_str}{delta_str}")

    if interp.get("risk_flags"):
        lines.append("  ⚠ Risk flags: " + " | ".join(interp["risk_flags"]))

    return "\n".join(lines)


# ─────────────────────────────────────────────
# STANDALONE RUN
# ─────────────────────────────────────────────

if __name__ == "__main__":
    result = run_macro_scan()
    interp = result.get("interpretation", {})
    print(f"\n── OctoVision Report ──────────────────────")
    print(f"Regime: {interp.get('regime')}")
    print("\nSignals:")
    for s in interp.get("signals", []):
        print(f"  • {s}")
    print("\nRisk Flags:")
    for f in interp.get("risk_flags", []):
        print(f"  ⚠ {f}")

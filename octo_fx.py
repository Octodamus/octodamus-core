"""
octo_fx.py
OctoFX — Dollar Strength & Currency Mind

Open Exchange Rates free tier — 1000 req/month, base USD.
Free API key at: openexchangerates.org/signup/free

Bitwarden key: AGENT - Octodamus - Open Exchange Rates
Env var:       OPENEXCHANGERATES_API_KEY

Tracks:
  - Dollar Index proxy (DXY via EUR, GBP, JPY, CNY weights)
  - Key currency pairs vs USD
  - EM currency stress (MXN, BRL, TRY, INR)
  - Crypto-correlated currencies

Usage:
    from octo_fx import run_fx_scan, format_fx_for_prompt
    fx = run_fx_scan()
"""

import os
import time
import requests
from datetime import datetime

OXR_BASE = "https://openexchangerates.org/api"
HEADERS  = {"User-Agent": "octodamus-oracle/1.0 (@octodamusai)"}

# DXY proxy weights (approximate ICE Dollar Index composition)
DXY_WEIGHTS = {
    "EUR": 0.576,  # inverse — EUR/USD
    "JPY": 0.136,
    "GBP": 0.119,
    "CAD": 0.091,
    "SEK": 0.042,
    "CHF": 0.036,
}

# Currencies to track
TRACK_CURRENCIES = {
    "majors": ["EUR", "GBP", "JPY", "CHF", "CAD", "AUD"],
    "em":     ["MXN", "BRL", "TRY", "INR", "KRW", "SGD"],
    "crypto_proxy": ["CNY", "RUB"],  # China/Russia exposure
}


def _fetch_rates(api_key: str) -> dict | None:
    """Fetch latest rates from Open Exchange Rates (base: USD)."""
    try:
        r = requests.get(
            f"{OXR_BASE}/latest.json",
            params={"app_id": api_key, "symbols": ",".join(
                list(DXY_WEIGHTS.keys()) +
                TRACK_CURRENCIES["majors"] +
                TRACK_CURRENCIES["em"] +
                TRACK_CURRENCIES["crypto_proxy"]
            )},
            headers=HEADERS,
            timeout=12,
        )
        r.raise_for_status()
        data = r.json()
        return data.get("rates", {})
    except Exception as e:
        print(f"[OctoFX] Rates fetch failed: {e}")
        return None


def _compute_dxy_proxy(rates: dict) -> float | None:
    """Compute approximate DXY from major pair rates."""
    try:
        # DXY rises when EUR falls (inverse relationship for EUR/USD)
        eur = rates.get("EUR")
        jpy = rates.get("JPY")
        gbp = rates.get("GBP")
        cad = rates.get("CAD")
        sek = rates.get("SEK")
        chf = rates.get("CHF")
        if not all([eur, jpy, gbp, cad, sek, chf]):
            return None
        # Simplified proxy — normalized to ~100 base
        # Higher number = stronger dollar
        proxy = (
            (1 / eur) * 100 * DXY_WEIGHTS["EUR"] +
            jpy * (1/100) * DXY_WEIGHTS["JPY"] * 10 +
            (1 / gbp) * 100 * DXY_WEIGHTS["GBP"] +
            cad * DXY_WEIGHTS["CAD"] * 80 +
            sek * DXY_WEIGHTS["SEK"] * 10 +
            (1 / chf) * 100 * DXY_WEIGHTS["CHF"]
        )
        return round(proxy, 2)
    except Exception:
        return None


def _interpret_fx(rates: dict, dxy: float | None) -> dict:
    signals = []
    risk_flags = []

    if dxy:
        if dxy > 105:
            risk_flags.append(f"DXY proxy strong ({dxy:.1f}) — dollar squeeze, EM stress likely")
        elif dxy < 98:
            signals.append(f"DXY proxy weak ({dxy:.1f}) — dollar softening, risk-on for EM/crypto")
        else:
            signals.append(f"DXY proxy neutral ({dxy:.1f})")

    # JPY carry trade proxy
    jpy = rates.get("JPY")
    if jpy:
        if jpy > 155:
            risk_flags.append(f"JPY at {jpy:.1f}/USD — extreme yen weakness, carry trade risk")
        elif jpy < 140:
            signals.append(f"JPY at {jpy:.1f}/USD — yen strength, carry unwind possible")

    # EM stress
    try_rate = rates.get("TRY")
    if try_rate and try_rate > 35:
        risk_flags.append(f"TRY {try_rate:.1f}/USD — Turkish lira stress")

    return {"signals": signals, "risk_flags": risk_flags}


def run_fx_scan(api_key: str | None = None) -> dict:
    if api_key is None:
        api_key = os.environ.get("OPENEXCHANGERATES_API_KEY")
    if not api_key:
        print("[OctoFX] No OPENEXCHANGERATES_API_KEY — FX scan skipped.")
        return {"error": "no_api_key", "rates": {}}

    print("[OctoFX] Fetching currency rates...")
    rates = _fetch_rates(api_key)
    if not rates:
        return {"error": "fetch_failed", "rates": {}}

    dxy = _compute_dxy_proxy(rates)
    interp = _interpret_fx(rates, dxy)

    print(f"  DXY proxy: {dxy}")
    print(f"  EUR/USD: {rates.get('EUR'):.4f}" if rates.get("EUR") else "  EUR: N/A")
    print(f"  JPY/USD: {rates.get('JPY'):.2f}"  if rates.get("JPY") else "  JPY: N/A")

    return {
        "timestamp": datetime.utcnow().isoformat(),
        "rates": rates,
        "dxy_proxy": dxy,
        "interpretation": interp,
    }


def format_fx_for_prompt(result: dict) -> str:
    if result.get("error") or not result.get("rates"):
        return ""
    rates = result["rates"]
    dxy   = result.get("dxy_proxy")
    interp = result.get("interpretation", {})

    lines = ["Currency & dollar strength (OctoFX):"]
    if dxy:
        lines.append(f"  DXY proxy: {dxy:.1f}")
    for sym in ["EUR","GBP","JPY","CNY"]:
        if rates.get(sym):
            lines.append(f"  {sym}/USD: {rates[sym]:.4f}")
    for flag in interp.get("risk_flags", []):
        lines.append(f"  ⚠ {flag}")
    return "\n".join(lines)


if __name__ == "__main__":
    result = run_fx_scan()
    print(f"\n── OctoFX Report ──────────────────────")
    print(f"DXY proxy: {result.get('dxy_proxy')}")
    for sym, val in result.get("rates", {}).items():
        print(f"  {sym}: {val}")

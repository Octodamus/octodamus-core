"""Volatility regime module using Deribit DVOL index."""

import time

try:
    import httpx
except ImportError:
    httpx = None

_CACHE: dict = {}
_CACHE_TTL = 900  # 15 minutes

_DERIBIT_BASE = "https://www.deribit.com/api/v2/public/get_index_price"

# Regime thresholds
_REGIME_LOW = 40
_REGIME_MEDIUM_MAX = 70
_REGIME_HIGH_MAX = 100

# WIN thresholds per regime
_WIN_THRESHOLD = {
    "LOW": 0.75,
    "MEDIUM": 1.25,
    "HIGH": 2.0,
    "EXTREME": 2.0,
}

_SIGNAL_ADJUSTMENT = {
    "LOW": "Lower bar: calm market, small edges hold — 0.75% move qualifies as WIN",
    "MEDIUM": "Standard bar: moderate vol, default 1.25% WIN threshold applies",
    "HIGH": "Raise bar: high vol means noise dominates — 2.0% move required for WIN",
    "EXTREME": "Raise bar: extreme vol — only high-conviction calls, 2.0%+ needed for WIN",
}


def _cached(key: str):
    entry = _CACHE.get(key)
    if entry and (time.time() - entry["ts"]) < _CACHE_TTL:
        return entry["data"]
    return None


def _store(key: str, data: dict):
    _CACHE[key] = {"ts": time.time(), "data": data}


def _fetch_dvol(index_name: str) -> float | None:
    """Fetch a single DVOL value from Deribit."""
    if httpx is None:
        return None
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.get(_DERIBIT_BASE, params={"index_name": index_name})
            resp.raise_for_status()
            data = resp.json()
            return float(data["result"]["index_price"])
    except Exception:
        return None


def _classify_regime(dvol: float) -> str:
    if dvol < _REGIME_LOW:
        return "LOW"
    elif dvol < _REGIME_MEDIUM_MAX:
        return "MEDIUM"
    elif dvol < _REGIME_HIGH_MAX:
        return "HIGH"
    else:
        return "EXTREME"


def get_vol_regime() -> dict:
    """Return volatility regime dict based on Deribit DVOL."""
    key = "vol_regime"
    cached = _cached(key)
    if cached:
        return cached

    dvol_btc = _fetch_dvol("dvol_btc")
    dvol_eth = _fetch_dvol("dvol_eth")

    # Fallback defaults if API fails
    if dvol_btc is None and dvol_eth is None:
        result = {
            "dvol_btc": 0.0,
            "dvol_eth": 0.0,
            "regime": "MEDIUM",
            "win_threshold_pct": _WIN_THRESHOLD["MEDIUM"],
            "signal_adjustment": "Could not fetch DVOL — defaulting to MEDIUM regime",
        }
        _store(key, result)
        return result

    # Use BTC DVOL as primary regime indicator; fall back to ETH if BTC unavailable
    primary_dvol = dvol_btc if dvol_btc is not None else dvol_eth
    regime = _classify_regime(primary_dvol)

    result = {
        "dvol_btc": round(dvol_btc, 2) if dvol_btc is not None else 0.0,
        "dvol_eth": round(dvol_eth, 2) if dvol_eth is not None else 0.0,
        "regime": regime,
        "win_threshold_pct": _WIN_THRESHOLD[regime],
        "signal_adjustment": _SIGNAL_ADJUSTMENT[regime],
    }
    _store(key, result)
    return result


def vol_regime_context_str() -> str:
    """Return a formatted string for prompt injection."""
    try:
        v = get_vol_regime()
        return (
            f"[VOL REGIME]\n"
            f"  DVOL BTC       : {v['dvol_btc']:.1f}\n"
            f"  DVOL ETH       : {v['dvol_eth']:.1f}\n"
            f"  Regime         : {v['regime']}\n"
            f"  WIN Threshold  : {v['win_threshold_pct']:.2f}%\n"
            f"  Adjustment     : {v['signal_adjustment']}\n"
        )
    except Exception:
        return ""

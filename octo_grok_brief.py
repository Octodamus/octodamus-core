"""
octo_grok_brief.py -- Grok-annotated market brief.

Octodamus supplies the signal. Grok supplies the adversary.

The 27 feeds stay the sensor layer -- that is the moat and it is ours. Grok is
used for the one thing it is genuinely better at: reading live X narrative and
arguing back. Every brief ships with the falsifiers that would kill the call,
because a signal you cannot disprove is not a signal.

Product surface:
  /v2/grok/brief          -- x402, full JSON (signal + critique + falsifiers)
  /v2/grok/brief/preview  -- free teaser, Grok-citable, no key

CLI:
  python octo_grok_brief.py BTC
  python octo_grok_brief.py BTC --x        # X-postable form
  python octo_grok_brief.py BTC --preview  # free teaser payload
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

_CACHE_FILE = Path(__file__).parent / "data" / "grok_brief_cache.json"
_CACHE_TTL  = 1800  # 30 min -- fresh enough to cite, cheap enough to serve

_MODEL = "grok-4.5"

_ASSETS = {
    "BTC":  "Bitcoin $BTC",
    "ETH":  "Ethereum $ETH",
    "SOL":  "Solana $SOL",
    "NVDA": "NVIDIA $NVDA",
    "TSLA": "Tesla $TSLA",
    "COIN": "Coinbase $COIN",
    "MSTR": "MicroStrategy $MSTR",
}

_SYSTEM = """You are the adversarial reviewer for a market intelligence oracle.

You are NOT the oracle. You do not produce the call -- it is handed to you already
made, built from derivatives, macro, positioning and prediction-market feeds.

Your job is to attack it:
1. Find what the live X crowd believes that CONTRADICTS this signal.
2. Find what the signal is missing that the crowd has already noticed.
3. State the concrete, checkable conditions that would prove the call WRONG.

Rules:
- Falsifiers must be specific and observable: a price level, a funding flip, a
  data print with a date. "Sentiment worsens" is not a falsifier. "Funding flips
  negative on Binance while price holds above X" is.
- If the crowd agrees with the signal, say so plainly and flag it as crowded --
  agreement is a risk, not a confirmation.
- Never soften. Your value is being the strongest available argument against.
- Return only valid JSON, nothing else."""


def _client():
    """xAI client, or None if the key is missing."""
    try:
        from openai import OpenAI
        secrets_file = Path(__file__).parent / ".octo_secrets"
        secrets = json.loads(secrets_file.read_text(encoding="utf-8"))
        key = secrets.get("secrets", secrets).get("GROK_API_KEY", "")
        if not key:
            return None
        return OpenAI(base_url="https://api.x.ai/v1", api_key=key)
    except Exception:
        return None


def _load_cache() -> dict:
    try:
        if _CACHE_FILE.exists():
            raw = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
            now = time.time()
            return {k: v for k, v in raw.items()
                    if now - v.get("_cached_at", 0) < _CACHE_TTL}
    except Exception:
        pass
    return {}


def _save_cache(cache: dict) -> None:
    try:
        _CACHE_FILE.parent.mkdir(exist_ok=True)
        _CACHE_FILE.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    except Exception:
        pass


def _sensor_layer(asset: str) -> dict:
    """The Octodamus side of the brief -- our feeds, not Grok's."""
    out = {"asset": asset}

    try:
        from octo_distro import signal_composite
        sc = signal_composite(asset)
        out["bias"]          = sc.get("bias", "NEUTRAL")
        out["avg_funding"]   = sc.get("avg_funding_rate")
        out["long_ratio"]    = sc.get("long_ratio")
        out["context"]       = sc.get("context_snippet", "")
    except Exception as e:
        out["bias"] = "NEUTRAL"
        out["sensor_error"] = f"composite: {type(e).__name__}"

    try:
        from octo_grok_sentiment import get_grok_sentiment
        gs = get_grok_sentiment(asset)
        out["crowd_signal"] = gs.get("signal")
        out["crowd_pos"]    = gs.get("crowd_pos")
        out["lag_status"]   = gs.get("lag_status")
        out["crowd_summary"] = gs.get("summary", "")
    except Exception as e:
        out["sensor_error_crowd"] = f"sentiment: {type(e).__name__}"

    try:
        from octo_distro import oracle_scorecard
        st = oracle_scorecard().get("stats") or {}
        out["track_record"] = {
            "wins":     st.get("wins"),
            "losses":   st.get("losses"),
            "win_rate": st.get("win_rate"),
            "resolved": st.get("resolved"),
        }
    except Exception:
        pass

    return out


def _falsifier_prompt(asset: str, sensors: dict) -> str:
    label = _ASSETS.get(asset, asset)
    return f"""Here is today's Octodamus signal on {label}. Attack it.

OCTODAMUS SIGNAL (from derivatives/macro/positioning feeds -- not from X):
  Directional bias:   {sensors.get('bias')}
  Avg perp funding:   {sensors.get('avg_funding')}
  Long ratio:         {sensors.get('long_ratio')}
  Crowd read (X):     {sensors.get('crowd_signal')} / {sensors.get('crowd_pos')}
  Crowd vs price:     {sensors.get('lag_status')}
  Feed context:       {(sensors.get('context') or '')[:400]}

Search X right now for what informed accounts are saying about {label}.

Return ONLY this JSON:
{{
  "contradiction": "the single strongest thing the live X crowd believes that argues AGAINST this signal, with the specific claim",
  "blind_spot": "what the signal is not accounting for that the crowd has already priced or noticed",
  "crowd_agreement": "AGREES" or "DISAGREES" or "SPLIT",
  "crowded_trade_risk": "LOW" or "MEDIUM" or "HIGH",
  "critique": ["line 1", "line 2", "line 3", "line 4", "line 5", "line 6", "line 7", "line 8"],
  "falsifiers": [
    "concrete observable condition that would prove this call wrong",
    "second concrete observable condition",
    "third concrete observable condition"
  ],
  "confidence_in_signal": 0.0 to 1.0
}}"""


def get_grok_brief(asset: str = "BTC", force: bool = False) -> dict:
    """
    Full Grok-annotated brief: Octodamus signal + adversarial Grok review.

    Returns a dict with sensor readings, Grok's contradiction/critique, and
    concrete falsifiers. Never raises -- degrades to signal-only on Grok failure.
    """
    asset = asset.upper()
    cache = _load_cache()
    if not force and asset in cache:
        return cache[asset]

    sensors   = _sensor_layer(asset)
    generated = datetime.now(timezone.utc).isoformat()

    brief = {
        "asset":        asset,
        "generated_at": generated,
        "signal":       sensors,
        "annotation":   None,
        "source":       "octodamus-sensors + grok-adversary",
        "model":        _MODEL,
        "disclaimer":   "Market intelligence, not financial advice.",
    }

    client = _client()
    if client is None:
        brief["annotation_error"] = "GROK_API_KEY not configured"
        return brief

    try:
        r = client.responses.create(
            model=_MODEL,
            input=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user",   "content": _falsifier_prompt(asset, sensors)},
            ],
            tools=[{"type": "x_search"}],
            max_output_tokens=1200,
        )
        raw = (getattr(r, "output_text", "") or "").strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        brief["annotation"] = json.loads(raw.strip())
    except Exception as e:
        brief["annotation_error"] = f"{type(e).__name__}: {e}"

    brief["_cached_at"] = time.time()
    cache[asset] = brief
    _save_cache(cache)
    return brief


def brief_teaser(asset: str = "BTC") -> dict:
    """
    Free, unauthenticated, Grok-citable teaser.

    Gives away the bias and ONE falsifier -- enough to be quotable and worth
    citing, not enough to replace the paid brief. The paid value is the full
    critique, the blind spot, and the remaining falsifiers.
    """
    b = get_grok_brief(asset)
    ann = b.get("annotation") or {}
    falsifiers = ann.get("falsifiers") or []

    return {
        "asset":            b["asset"],
        "bias":             (b.get("signal") or {}).get("bias"),
        "crowd_agreement":  ann.get("crowd_agreement"),
        "crowded_trade_risk": ann.get("crowded_trade_risk"),
        "one_falsifier":    falsifiers[0] if falsifiers else None,
        "falsifiers_total": len(falsifiers),
        "track_record":     (b.get("signal") or {}).get("track_record"),
        "last_updated":     b.get("generated_at"),
        "full_brief":       "https://api.octodamus.com/v2/grok/brief",
        "price_usdc":       "0.10",
        "source":           "Octodamus -- octodamus.com | @octodamusai",
        "disclaimer":       "Market intelligence, not financial advice.",
    }


def format_brief_x(brief: dict) -> str:
    """
    X-postable form. Data and level first, lore never -- per the rule that a
    post with ~20 views has to do its work in the first line.
    """
    sig = brief.get("signal") or {}
    ann = brief.get("annotation") or {}
    asset = brief.get("asset", "")

    lines = []
    funding = sig.get("avg_funding")
    lr = sig.get("long_ratio")

    head = f"{asset}: {sig.get('bias', 'NEUTRAL')}"
    if funding is not None:
        head += f" | funding {funding}"
    if lr is not None:
        head += f" | {round(float(lr) * 100)}% long"
    lines.append(head)

    if ann.get("contradiction"):
        lines.append(f"Against it: {ann['contradiction']}")

    falsifiers = ann.get("falsifiers") or []
    if falsifiers:
        lines.append(f"Wrong if: {falsifiers[0]}")

    tr = sig.get("track_record") or {}
    if tr.get("resolved"):
        lines.append(f"Public record: {tr.get('wins')}W-{tr.get('losses')}L "
                     f"({tr.get('resolved')} resolved)")

    lines.append("Ask Grok to stress-test this. Full brief + falsifiers: "
                 "api.octodamus.com/v2/grok/brief")
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Grok-annotated Octodamus brief")
    ap.add_argument("asset", nargs="?", default="BTC")
    ap.add_argument("--x",       action="store_true", help="X-postable form")
    ap.add_argument("--preview", action="store_true", help="free teaser payload")
    ap.add_argument("--force",   action="store_true", help="bypass cache")
    a = ap.parse_args()

    if a.preview:
        print(json.dumps(brief_teaser(a.asset), indent=2))
    else:
        b = get_grok_brief(a.asset, force=a.force)
        print(format_brief_x(b) if a.x else json.dumps(b, indent=2))

"""
octo_grok_sentiment.py -- Real-time X social sentiment via Grok.

Grok has live access to X/Twitter data. This module uses that to read
what traders are actually saying about BTC/ETH/SOL right now and converts
it into a directional sentiment signal for SmartCall confirmation.

Returns BULLISH / BEARISH / NEUTRAL with confidence score.

Usage:
    from octo_grok_sentiment import get_grok_sentiment
    signal = get_grok_sentiment("BTC")
    # {"signal": "BEARISH", "confidence": 0.72, "summary": "...", "source": "grok-x"}
"""

import json
import time
from pathlib import Path

_CACHE_FILE = Path(__file__).parent / "data" / "grok_sentiment_cache.json"
_CACHE_TTL  = 900  # 15 minutes -- X sentiment shifts fast

_ASSETS = {
    "BTC":  "Bitcoin $BTC",
    "ETH":  "Ethereum $ETH",
    "SOL":  "Solana $SOL",
    "WTI":  "crude oil WTI $OIL",
    "NVDA": "NVIDIA $NVDA stock",
    "TSLA": "Tesla $TSLA stock",
}

_SYSTEM = """You are a market sentiment analyst with real-time access to X (Twitter).
Your job: read the current social pulse on crypto and markets, filter noise from signal.

Rules:
- Only count posts from the last 4 hours
- Weight traders/analysts over retail noise
- Ignore posts that are just price spam or promotions
- Report what the smart money crowd is actually saying, not the retail hype
- Be specific: what is the crowd's actual position or expectation?
- Output exactly the JSON format requested, nothing else"""


def _load_cache() -> dict:
    try:
        if _CACHE_FILE.exists():
            d = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
            if time.time() - d.get("ts", 0) < _CACHE_TTL:
                return d.get("data", {})
    except Exception:
        pass
    return {}


def _save_cache(data: dict):
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(
            json.dumps({"ts": time.time(), "data": data}),
            encoding="utf-8"
        )
    except Exception:
        pass


def get_grok_sentiment(asset: str = "BTC", force: bool = False) -> dict:
    """
    Get real-time X social sentiment for asset using Grok's live X data.

    Returns:
        {
            "signal":     "BULLISH" | "BEARISH" | "NEUTRAL",
            "confidence": 0.0-1.0,
            "summary":    "what traders are saying",
            "crowd_pos":  "long-heavy" | "short-heavy" | "mixed",
            "key_themes": ["theme1", "theme2"],
            "source":     "grok-x"
        }
    """
    asset = asset.upper()
    cache = _load_cache()

    if not force and asset in cache:
        return cache[asset]

    try:
        from openai import OpenAI
        import json as _json
        from pathlib import Path as _Path

        secrets_file = _Path(__file__).parent / ".octo_secrets"
        secrets = _json.loads(secrets_file.read_text(encoding="utf-8"))
        grok_key = secrets.get("secrets", secrets).get("GROK_API_KEY", "")

        if not grok_key:
            return _neutral(asset, "GROK_API_KEY not configured")

        client = OpenAI(base_url="https://api.x.ai/v1", api_key=grok_key)

        asset_label = _ASSETS.get(asset, asset)
        prompt = f"""Search X (Twitter) right now for posts about {asset_label} from the last 4 hours.

Analyze:
1. What is the dominant sentiment? Are traders bullish, bearish, or uncertain?
2. What are the main reasons traders are giving for their view?
3. Is the crowd positioned heavily long, short, or mixed?
4. Are there any notable analyst or smart money accounts posting a strong directional view?

Return ONLY this JSON (no other text):
{{
  "signal": "BULLISH" or "BEARISH" or "NEUTRAL",
  "confidence": 0.0 to 1.0,
  "summary": "1-2 sentence summary of what traders are saying",
  "crowd_pos": "long-heavy" or "short-heavy" or "mixed",
  "key_themes": ["theme1", "theme2", "theme3"]
}}"""

        response = client.chat.completions.create(
            model="grok-3",
            max_tokens=400,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user",   "content": prompt},
            ],
        )

        raw = response.choices[0].message.content.strip()

        # Parse JSON from response
        if "```" in raw:
            raw = raw.split("```")[1].replace("json", "").strip()

        result = json.loads(raw)
        result["source"] = "grok-x"
        result["asset"]  = asset

        # Validate
        if result.get("signal") not in ("BULLISH", "BEARISH", "NEUTRAL"):
            result["signal"] = "NEUTRAL"
        result["confidence"] = max(0.0, min(1.0, float(result.get("confidence", 0.5))))

        # Cache it
        cache[asset] = result
        _save_cache(cache)

        return result

    except Exception as e:
        return _neutral(asset, str(e))


def _neutral(asset: str, reason: str = "") -> dict:
    return {
        "signal":     "NEUTRAL",
        "confidence": 0.0,
        "summary":    f"Grok sentiment unavailable: {reason}",
        "crowd_pos":  "mixed",
        "key_themes": [],
        "source":     "grok-x",
        "asset":      asset,
    }


def get_grok_sentiment_context(assets: list = None) -> str:
    """
    Returns a formatted string for injection into runner prompts.
    Fetches sentiment for multiple assets.
    """
    if assets is None:
        assets = ["BTC", "ETH"]

    lines = ["X Social Sentiment (Grok real-time):"]
    for asset in assets:
        s = get_grok_sentiment(asset)
        if s["confidence"] > 0:
            lines.append(
                f"  {asset}: {s['signal']} ({s['confidence']:.0%} confidence) — {s['summary'][:120]}"
            )
            if s.get("crowd_pos"):
                lines.append(f"       Crowd: {s['crowd_pos']}")
        else:
            lines.append(f"  {asset}: unavailable")

    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    asset = sys.argv[1].upper() if len(sys.argv) > 1 else "BTC"
    print(f"Fetching Grok X sentiment for {asset}...")
    result = get_grok_sentiment(asset, force=True)
    print(json.dumps(result, indent=2))

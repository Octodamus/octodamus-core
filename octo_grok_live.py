"""
octo_grok_live.py -- Live X (Twitter) intelligence via xAI Grok x_search

This is the ONLY Octodamus data source with real-time access to what X is saying
RIGHT NOW. Claude/Haiku cannot see live X; Grok can. Use it for narrative
awareness -- what the crowd is talking about, what is gaining velocity, what
just broke -- so posts can hint at what is COMING, not just react to price.

Uses the xAI Responses API (/v1/responses) with the x_search tool on grok-4.5.
The older octo_grok_sentiment path called grok-3 with NO search tool, so its
"search X now" prompt was never actually searching -- this module fixes that.

Key: GROK_API_KEY in .octo_secrets (Bitwarden: "AGENT - Octodamus - xAI Grok API")
Cost: each call runs a live X search (search cost + tokens). Cached to control spend.

Usage:
    from octo_grok_live import x_pulse, get_live_x_context
    p   = x_pulse("Bitcoin", from_days=1)          # raw live-X read on a topic
    ctx = get_live_x_context(["BTC", "crypto"])    # cached block for post prompts

CLI:
    python octo_grok_live.py pulse "BTC ETF flows" 1
    python octo_grok_live.py context
"""

import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

_MODEL = "grok-4.5"
_CACHE_FILE = Path(__file__).parent / "data" / "grok_live_cache.json"
_CACHE_TTL = 2 * 3600  # 2h -- narrative shifts slowly enough; keeps cost down

# Curated high-signal crypto/macro accounts. Scoping the search to these cuts noise
# and cost vs. an open firehose. Max 20 handles per xAI x_search request.
_SIGNAL_HANDLES = [
    "elonmusk", "saylor", "APompliano", "CathieDWood", "RaoulGMI",
    "zerohedge", "DeItaone", "unusual_whales", "WatcherGuru", "Cointelegraph",
    "documentingbtc", "AureliusBTC", "CryptoHayes", "balajis", "woonomic",
]


def _client():
    from openai import OpenAI
    sec = json.loads((Path(__file__).parent / ".octo_secrets").read_text(encoding="utf-8"))
    key = sec.get("secrets", sec).get("GROK_API_KEY", "")
    if not key:
        return None
    return OpenAI(base_url="https://api.x.ai/v1", api_key=key)


_UNICODE_MAP = {
    "—": "--", "–": "-", "’": "'", "‘": "'",
    "“": '"', "”": '"', "→": "->", "•": "-",
    "…": "...", " ": " ",
}


def _sanitize(text: str) -> str:
    """Normalize unicode Grok returns (em-dash, smart quotes) so it never crashes
    cp1252 stdout downstream (see .claude/rules/coding.md)."""
    for bad, good in _UNICODE_MAP.items():
        text = text.replace(bad, good)
    return text


def _strip_citations(text: str) -> str:
    """Remove inline [[n]](url) citation markup for clean prompt injection."""
    return _sanitize(re.sub(r"\[\[\d+\]\]\(https?://[^\)]+\)", "", text).strip())


def _extract_citations(text: str) -> list:
    """Pull the X post URLs Grok cited, so posts can reference real sources."""
    return re.findall(r"\((https?://x\.com/[^\)]+)\)", text)


def x_pulse(query: str, from_days: int = 1, handles: Optional[list] = None,
            scoped: bool = True, max_tokens: int = 400) -> dict:
    """Live-X read on a topic. Returns {text, citations, raw, model, query, ts}.

    from_days: how far back to search (search window ends now).
    handles:   explicit X handles to scope to (defaults to _SIGNAL_HANDLES when scoped).
    scoped:    True = only the signal-account list (cheaper, higher signal);
               False = open X search (broader, noisier, pricier).
    Returns {} with an "error" key on failure.
    """
    client = _client()
    if client is None:
        return {"error": "GROK_API_KEY not configured", "query": query}

    now = datetime.now()
    from_date = (now - timedelta(days=max(1, from_days))).strftime("%Y-%m-%d")
    to_date = now.strftime("%Y-%m-%d")

    tool = {"type": "x_search", "from_date": from_date, "to_date": to_date}
    if scoped:
        tool["allowed_x_handles"] = (handles or _SIGNAL_HANDLES)[:20]

    try:
        r = client.responses.create(
            model=_MODEL,
            input=[{"role": "user", "content": query}],
            tools=[tool],
            max_output_tokens=max_tokens,
        )
        raw = getattr(r, "output_text", "") or ""
        return {
            "text":      _strip_citations(raw),
            "citations": _extract_citations(raw),
            "raw":       raw,
            "model":     _MODEL,
            "query":     query,
            "window":    f"{from_date}..{to_date}",
            "ts":        now.isoformat(),
        }
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "query": query}


def _load_cache() -> dict:
    try:
        return json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_FILE.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def get_live_x_context(topics: Optional[list] = None, force: bool = False) -> str:
    """Cached, formatted live-X narrative block for injection into post prompts.

    One scoped search per refresh (2h cache) summarizing what the signal accounts
    are actually saying -- gives every post real-time narrative awareness. '' on failure.
    """
    topics = topics or ["Bitcoin", "Ethereum", "crypto markets"]
    key = ",".join(topics).lower()
    cache = _load_cache()
    hit = cache.get(key)
    if hit and not force and (time.time() - hit.get("_ts", 0)) < _CACHE_TTL:
        return hit.get("block", "")

    q = (
        f"Search X for what the accounts are saying about {', '.join(topics)} in the last 24h. "
        "In 2-3 tight sentences: name the single dominant narrative gaining the most traction, "
        "note any emerging topic picking up velocity before it peaks, and flag if the crowd is "
        "euphoric or fearful. Be specific about the narrative, not the price. No hedging."
    )
    p = x_pulse(q, from_days=1, scoped=True, max_tokens=350)
    if p.get("error") or not p.get("text"):
        return hit.get("block", "") if hit else ""

    block = "LIVE X NARRATIVE (Grok real-time, last 24h -- what the crowd is actually saying):\n" + p["text"]
    cache[key] = {"block": block, "_ts": time.time(), "window": p.get("window")}
    _save_cache(cache)
    return block


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    if args and args[0] == "pulse":
        query = args[1] if len(args) > 1 else "Bitcoin"
        days = int(args[2]) if len(args) > 2 else 1
        res = x_pulse(query, from_days=days)
        if res.get("error"):
            print("ERROR:", res["error"])
        else:
            print(f"[{res['window']}] {res['query']}\n")
            print(res["text"])
            if res["citations"]:
                print("\nSources:")
                for c in res["citations"][:6]:
                    print("  -", c)
    elif args and args[0] == "context":
        print(get_live_x_context(force=True) or "(no context -- check GROK_API_KEY)")
    else:
        print('Usage: python octo_grok_live.py [pulse "<query>" [days] | context]')

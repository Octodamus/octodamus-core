"""
octo_boto_correlations.py -- Cross-market correlation scanner for OctoBoto.

When Octodamus identifies edge in market X, this module checks active Polymarket
markets for logical dependencies -- other markets whose probabilities are
constrained by the outcome of X.

Example: If Octodamus calls BTC hits $90k (YES edge), a market asking
"Will BTC hit $80k?" is logically constrained -- if $90k resolves YES,
$80k almost certainly resolves YES too.

Cost: ~$0.001 per scan (Claude Haiku, ~700 tokens). Cached 4h per primary market.
"""

import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path

_CACHE_FILE = Path(__file__).parent / "data" / "correlation_cache.json"
_CACHE_TTL_H = 4


def _load_cache() -> dict:
    if _CACHE_FILE.exists():
        try:
            return json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_cache(cache: dict):
    try:
        _CACHE_FILE.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    except Exception:
        pass


def find_correlated_plays(
    primary_question: str,
    primary_price: float,
    primary_side: str,
    candidate_markets: list,
    api_key: str,
    max_candidates: int = 25,
) -> list:
    """
    Given a primary market with identified edge, scan candidates for logical correlations.

    Returns list of dicts:
      {question, market_id, implied_side, probability_shift, current_price, reasoning}

    probability_shift: estimated probability delta (e.g. 0.15 = 15pp shift implied)
    """
    if not api_key or not candidate_markets:
        return []

    cache_key = hashlib.md5(primary_question.encode()).hexdigest()
    cache = _load_cache()

    if cache_key in cache:
        entry = cache[cache_key]
        age_h = (datetime.now(timezone.utc).timestamp() - entry["ts"]) / 3600
        if age_h < _CACHE_TTL_H:
            return entry["plays"]

    candidates = [
        m for m in candidate_markets
        if m.get("question") and m.get("question") != primary_question
        and not m.get("resolved")
    ][:max_candidates]

    if not candidates:
        return []

    candidate_lines = "\n".join(
        f"[{i}] {m['question']} (price: {float(m.get('yes_price', 0.5)):.0%})"
        for i, m in enumerate(candidates)
    )

    prompt = f"""You are analyzing Polymarket prediction markets for logical dependencies.

PRIMARY MARKET: "{primary_question}"
Current price: {primary_price:.0%} | Octodamus edge direction: {primary_side}

CANDIDATE MARKETS:
{candidate_lines}

Task: Identify candidates that are LOGICALLY CONSTRAINED by the primary market.
Only flag markets with a clear causal or logical connection -- not just thematic similarity.

Real correlation examples:
- "Will Trump win PA?" YES implies "Will Republicans win PA?" more likely YES
- "Will Iran attack Israel before June?" YES implies "Will Israel-Iran ceasefire by June?" more likely NO
- "Will BTC hit $100k in April?" NO implies "Will BTC hit $90k in April?" also more likely NO (containment)
- "Will Fed cut rates in May?" YES implies "Will SPX hit ATH in Q2?" somewhat more likely YES

Not a correlation:
- Two markets both about BTC but with different independent conditions
- Markets in the same topic but without logical constraint between outcomes

Output JSON array only. Each entry:
{{"idx": <candidate index 0-based>, "implied_side": "YES" or "NO", "probability_shift": <0.05 to 0.30>, "reasoning": "<one sentence max>"}}

Empty array [] if no strong correlations found. Output JSON only, no explanation."""

    try:
        import anthropic as _anthropic
        client = _anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        if "[" in text:
            text = text[text.index("[") : text.rindex("]") + 1]
        correlations = json.loads(text)
    except Exception:
        return []

    plays = []
    for c in correlations:
        idx = c.get("idx")
        if idx is None or not isinstance(idx, int) or idx >= len(candidates):
            continue
        shift = float(c.get("probability_shift", 0.10))
        if shift < 0.05:
            continue
        m = candidates[idx]
        plays.append({
            "question": m["question"],
            "market_id": m.get("condition_id", m.get("id", "")),
            "implied_side": c.get("implied_side", "YES"),
            "probability_shift": round(shift, 2),
            "current_price": float(m.get("yes_price", 0.5)),
            "reasoning": c.get("reasoning", ""),
        })

    cache[cache_key] = {
        "ts": datetime.now(timezone.utc).timestamp(),
        "plays": plays,
    }
    _save_cache(cache)

    return plays


def format_correlated_plays(plays: list) -> str:
    """Format correlated plays for Telegram display."""
    if not plays:
        return ""
    lines = ["Correlated plays:"]
    for p in plays[:4]:
        side = p["implied_side"]
        shift = p["probability_shift"]
        price = p["current_price"]
        q = p["question"][:60] + ("..." if len(p["question"]) > 60 else "")
        lines.append(f"  {side} {q}")
        lines.append(f"    price {price:.0%} | implied shift +{shift:.0%} | {p['reasoning'][:80]}")
    return "\n".join(lines)

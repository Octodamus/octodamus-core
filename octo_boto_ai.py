"""
octo_boto_ai.py — AI Probability Estimator v2
Fixes:
  - CRITICAL: Web search loop was sending empty content="" as tool results,
    which is wrong and confuses the model. web_search_20250305 is a server-
    executed tool — the API handles search internally and returns end_turn
    with the answer in a single response. Simplified to one call.
  - Added result caching (30-min TTL) — same market won't burn tokens twice
  - Better prompt: instructs model to anchor to base rate first, then update
  - Confidence calibration: "high" now requires recent data found via search
  - batch_estimate now uses composite_score for final ranking (not just EV)
  - Added concurrent processing with ThreadPoolExecutor (3x faster scans)
"""

import json
import re
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import anthropic

from octo_boto_math import best_trade, composite_score, is_valid_market

# ─── Config ───────────────────────────────────────────────────────────────────
MODEL      = "claude-sonnet-4-20250514"
MAX_TOKENS = 800    # Raised — web search responses can be verbose
WEB_SEARCH = [{"type": "web_search_20250305", "name": "web_search"}]

# Cache — {market_id: (result, timestamp)}
_cache: dict = {}
_cache_lock  = threading.Lock()
CACHE_TTL    = 30 * 60   # 30 minutes

SYSTEM_PROMPT = """You are a calibrated quantitative analyst specialising in prediction market arbitrage.

Your method:
1. Establish BASE RATE from historical base rates and priors
2. UPDATE using only recent concrete evidence (news, polls, official data)
3. COMPARE to crowd price — flag only genuine mispricings, not uncertainty

Be honest about uncertainty. Do NOT invent confidence. If you can't find strong evidence, say low confidence.

TRADING MATH FRAMEWORK (apply to all analysis):

R-Multiple & Required Win Rate:
- R=0.5 needs 67% win rate | R=1.0 needs 50% | R=1.5 needs 40%
- R=2.0 needs 33% | R=2.5 needs 25% | R=3.5 needs 22%
- General formula: required win rate = 1/(1+R)

Trade Expectancy = (Winrate x Size x R-multiple) - ((1-Winrate) x Size)
Example: 55% win, 2% size, 1.5R = +0.75% expectancy per trade

Drawdown Recovery (critical risk management):
- 10% drawdown needs 11.1% to recover | 30% needs 43% | 50% needs 100%
- 60% needs 150% | 80% needs 900% — NEVER allow catastrophic drawdown

Consecutive Loss Probability at different win rates:
- 60% win rate: 32% chance of 3 consecutive losses
- 70% win rate: 2.7% chance of 3 consecutive losses
- 80% win rate: 9.2% chance of 3 consecutive losses
Always size for surviving the inevitable losing streaks.

Compounding: Small edges compound massively — 55% win, 1% risk, 2R turns
$10k into $81,597 over 500 trades. Protect the bankroll above all else.

Sizing rule: Max 4% per trade (quarter-Kelly). The edge only works with
enough capital to survive drawdowns and reach expectancy."""



# ── Coinglass context for crypto markets ──────────────────────────────────────
def _get_crypto_context(question: str) -> str:
    """
    If the market question is crypto-related, fetch Coinglass futures context.
    Returns empty string for non-crypto markets.
    """
    q = question.lower()
    CRYPTO_KEYWORDS = [
        "bitcoin", "btc", "ethereum", "eth", "solana", "sol",
        "crypto", "binance", "coinbase", "defi", "altcoin",
        "bnb", "xrp", "doge", "ada", "avax", "link",
    ]
    # Check if this market is crypto-related
    if not any(kw in q for kw in CRYPTO_KEYWORDS):
        return ""
    
    # Determine which symbol to pull data for
    symbol = "BTC"  # default
    if any(k in q for k in ["ethereum", "eth "]):
        symbol = "ETH"
    elif any(k in q for k in ["solana", "sol "]):
        symbol = "SOL"
    elif any(k in q for k in ["bnb", "binance coin"]):
        symbol = "BNB"
    elif any(k in q for k in ["xrp", "ripple"]):
        symbol = "XRP"
    elif any(k in q for k in ["doge", "dogecoin"]):
        symbol = "DOGE"
    
    try:
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from octo_coinglass import glass
        ctx = glass.build_oracle_context(symbol)
        alerts = glass.check_alerts([symbol])
        if alerts:
            ctx += "\nACTIVE ALERTS:\n"
            for a in alerts:
                ctx += f"  [{a['severity']}] {a['message']}\n"
        return ctx
    except Exception:
        return ""


def estimate(
    market_id:    str,
    question:     str,
    description:  str,
    market_price: float,
    api_key:      str,
    end_date:     str = "",
    use_search:   bool = True,
    min_ev:       float = 0.06
) -> dict:
    """
    Estimate true probability for a Polymarket binary market.

    Returns:
        probability  float    0.01-0.99
        confidence   str      high | medium | low
        edge         str      YES_BUY | NO_BUY | NONE
        reasoning    str      2-3 sentence explanation
        ai_used      bool
        cached       bool
    """
    # 1. Cache check
    with _cache_lock:
        if market_id in _cache:
            result, ts = _cache[market_id]
            if time.time() - ts < CACHE_TTL:
                cached = dict(result)
                cached["cached"] = True
                return cached

    # 2. Build prompt
    date_hint = f"\nResolution date: {end_date}" if end_date else ""
    # Fetch Coinglass futures data for crypto markets
    futures_context = _get_crypto_context(question)
    futures_section = f"\n\nFUTURES INTELLIGENCE (use this data to inform your estimate):\n{futures_context}" if futures_context else ""

    prompt = f"""PREDICTION MARKET ANALYSIS

Question: {question}
Additional context: {description[:300] if description else "None provided"}{date_hint}
Current crowd price (implied YES probability): {market_price:.1%}{futures_section}

TASK: Estimate the TRUE probability this resolves YES.

STEP 1 — SEARCH: Find the most recent relevant news, data, polls, or expert estimates.
STEP 2 — BASE RATE: What is the historical base rate for this type of event?
STEP 3 — BAYESIAN UPDATE: Adjust base rate with evidence found.
STEP 4 — COMPARE: Is your estimate meaningfully different from {market_price:.1%}?

Edge threshold: flag only if |your_estimate - {market_price:.1%}| > {min_ev:.0%}

OUTPUT: Respond ONLY with valid JSON, no markdown, no prose:
{{"probability": 0.XX, "confidence": "high|medium|low", "edge": "YES_BUY|NO_BUY|NONE", "reasoning": "2-3 sentences citing specific evidence"}}

Confidence guide:
- high: found recent concrete data (poll %, official statement, clear track record)
- medium: found indirect evidence or older data
- low: minimal data found, high uncertainty"""

    # 3. Single API call — web_search_20250305 is server-executed, not client-side.
    #    The model receives search results automatically before generating its response.
    #    No multi-turn loop needed.
    try:
        client = anthropic.Anthropic(api_key=api_key)

        kwargs = {
            "model":      MODEL,
            "max_tokens": MAX_TOKENS,
            "system":     SYSTEM_PROMPT,
            "messages":   [{"role": "user", "content": prompt}],
        }
        if use_search:
            kwargs["tools"] = WEB_SEARCH

        # Single call — server handles the search tool internally
        response = client.messages.create(**kwargs)

        # Extract text from response (handle both text and tool_result blocks)
        text = ""
        for block in response.content:
            if hasattr(block, "text") and block.text:
                text += block.text

        # If model returned tool_use stop (shouldn't happen with server-side search,
        # but handle gracefully with one follow-up)
        if response.stop_reason == "tool_use" and not text:
            follow_msgs = [
                {"role": "user",      "content": prompt},
                {"role": "assistant", "content": response.content},
                {"role": "user",      "content": [
                    {"type": "tool_result", "tool_use_id": b.id, "content": "Search completed."}
                    for b in response.content
                    if hasattr(b, "type") and b.type == "tool_use"
                ]}
            ]
            follow = client.messages.create(
                model=MODEL, max_tokens=MAX_TOKENS, system=SYSTEM_PROMPT,
                messages=follow_msgs, tools=WEB_SEARCH
            )
            for block in follow.content:
                if hasattr(block, "text") and block.text:
                    text += block.text

        result = _parse(text, market_price)
        result["ai_used"] = True
        result["cached"]  = False

        # Cache result
        with _cache_lock:
            _cache[market_id] = (result, time.time())

        return result

    except anthropic.RateLimitError:
        time.sleep(5)
        return _fallback(market_price, "Rate limited — retry shortly")
    except anthropic.APIError as e:
        return _fallback(market_price, f"API error: {e}")
    except Exception as e:
        return _fallback(market_price, str(e))


def batch_estimate(
    markets:     list,
    api_key:     str,
    max_markets: int = 15,
    min_ev:      float = 0.06,
    concurrency: int = 3,
) -> list:
    """
    Run AI estimation on filtered market list.
    Improvements v2:
    - Pre-filters more aggressively (vol_liq_ratio, days_to_close)
    - Runs up to 3 markets concurrently (ThreadPoolExecutor)
    - Ranks by composite_score, not raw EV
    - Skips cached results (free — no token cost)

    Returns list of opportunity dicts sorted by composite_score desc.
    """
    # Pre-filter
    candidates = []
    for m in markets:
        if not is_valid_market(m):
            continue
        price = m.get("yes_price", 0.5)

        # Only consider markets where price is NOT near certainty
        # AND there's meaningful 24h trading activity
        if not (0.05 < price < 0.95):
            continue

        # Prefer active markets — vol/liq ratio > 0.05 (5% daily turnover)
        if m.get("vol_liq_ratio", 0) < 0.03:
            continue

        candidates.append(m)
        if len(candidates) >= max_markets:
            break

    if not candidates:
        return []

    # Run AI concurrently
    results = []

    def process(m: dict) -> Optional[dict]:
        price = m["yes_price"]
        ai    = estimate(
            market_id=m["id"],
            question=m["question"],
            description=m.get("description", ""),
            market_price=price,
            api_key=api_key,
            end_date=m.get("end_date", ""),
            use_search=True,
            min_ev=min_ev
        )
        trade = best_trade(price, ai["probability"])
        if trade["side"] == "NONE":
            return None

        score = composite_score(
            ev=trade["ev"],
            liquidity=m.get("liquidity", 0),
            volume24h=m.get("volume24h", 0),
            confidence=ai.get("confidence", "low"),
            days_to_close=m.get("days_to_close")
        )

        return {"market": m, "ai": ai, "trade": trade, "score": score}

    with ThreadPoolExecutor(max_workers=min(concurrency, len(candidates))) as ex:
        futures = {ex.submit(process, m): m for m in candidates}
        for future in as_completed(futures):
            try:
                result = future.result(timeout=60)
                if result:
                    results.append(result)
            except Exception as e:
                print(f"[AI] Worker error: {e}")

    # Sort by composite score — NOT raw EV
    results.sort(key=lambda x: x["score"], reverse=True)
    return results


# ─── Parsing ──────────────────────────────────────────────────────────────────

def _parse(text: str, fallback_price: float) -> dict:
    """Extract and validate JSON from model response."""
    text = text.strip()
    if not text:
        return _fallback(fallback_price, "Empty response")

    try:
        # Remove markdown fences
        text = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()

        # Find outermost JSON object
        match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
        if not match:
            return _fallback(fallback_price, f"No JSON found in: {text[:100]}")

        data = json.loads(match.group())

        prob = float(data.get("probability", fallback_price))
        prob = max(0.01, min(0.99, prob))

        conf = str(data.get("confidence", "low")).lower().strip()
        if conf not in ("high", "medium", "low"):
            conf = "low"

        edge = str(data.get("edge", "NONE")).upper().strip()
        if edge not in ("YES_BUY", "NO_BUY", "NONE"):
            edge = "NONE"

        reasoning = str(data.get("reasoning", "No reasoning."))[:350].strip()

        return {
            "probability": round(prob, 4),
            "confidence":  conf,
            "edge":        edge,
            "reasoning":   reasoning,
        }

    except (json.JSONDecodeError, ValueError, KeyError) as e:
        return _fallback(fallback_price, f"Parse error: {e} | text: {text[:80]}")


def _fallback(market_price: float, reason: str = "") -> dict:
    return {
        "probability": market_price,
        "confidence":  "low",
        "edge":        "NONE",
        "reasoning":   f"AI unavailable — defaulting to market price. ({reason})",
        "ai_used":     False,
        "cached":      False,
    }


def clear_cache():
    """Wipe the in-memory estimate cache."""
    with _cache_lock:
        _cache.clear()

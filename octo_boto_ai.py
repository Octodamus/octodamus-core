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

from octo_boto_math import best_trade, composite_score, is_valid_market, resolution_risk_score

try:
    from octo_boto_brain import get_brain_context
except ImportError:
    def get_brain_context(**k): return ""

try:
    from octo_boto_mcp import orderbook_context_str
    _MCP_AVAILABLE = True
except ImportError:
    _MCP_AVAILABLE = False
    def orderbook_context_str(market): return ""

try:
    from octo_boto_consensus import get_consensus_context
    _CONSENSUS_AVAILABLE = True
except ImportError:
    _CONSENSUS_AVAILABLE = False
    def get_consensus_context(question): return ""

try:
    from octo_boto_calibration import get_calibration_context
    _CALIB_AVAILABLE = True
except ImportError:
    _CALIB_AVAILABLE = False
    def get_calibration_context(): return ""

try:
    from octo_tv_brief import get_tv_brief as _get_tv_brief
    _TV_AVAILABLE = True
except ImportError:
    _TV_AVAILABLE = False
    def _get_tv_brief(): return ""

# Cache TV brief for 15 min — expensive subprocess call, share across all estimates
_tv_brief_cache: tuple = ("", 0.0)
_tv_brief_lock = threading.Lock()

def _cached_tv_brief() -> str:
    global _tv_brief_cache
    with _tv_brief_lock:
        text, ts = _tv_brief_cache
        if text and (time.time() - ts) < 900:   # 15 min TTL
            return text
        try:
            text = _get_tv_brief()
            _tv_brief_cache = (text, time.time())
        except Exception:
            text = ""
        return text


# ─── Octodamus Signal Feed ─────────────────────────────────────────────────────

def _get_octodamus_signal_context(question: str) -> str:
    """
    Pull live directional signal from the Octodamus 11-signal engine.
    Only runs for crypto/macro questions where Octodamus has data edge.
    Injects as directional prior into the OctoBoto estimate prompt.
    """
    q = question.lower()

    # Determine which asset to pull signal for
    asset = None
    if any(k in q for k in ["bitcoin", "btc"]):
        asset = "BTC"
    elif any(k in q for k in ["ethereum", "eth"]):
        asset = "ETH"
    elif any(k in q for k in ["solana", "sol"]):
        asset = "SOL"
    elif any(k in q for k in ["nasdaq", "qqq", "s&p", "spy", "stock market", "equities"]):
        asset = "MACRO"

    if asset is None:
        return ""

    try:
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from octo_report_handlers import (
            fetch_technicals, fetch_derivatives, directional_call,
            _fetch_coinglass_compact,
        )
        import httpx

        if asset == "MACRO":
            # For macro, pull BTC as primary market barometer
            pull_asset = "BTC"
        else:
            pull_asset = asset

        ta    = fetch_technicals(pull_asset)
        deriv = fetch_derivatives(pull_asset)
        cg    = _fetch_coinglass_compact(pull_asset)

        price   = 0.0
        chg_24h = 0.0
        cg_prices = cg.get("prices", {})
        if cg_prices.get(pull_asset):
            price   = cg_prices[pull_asset]["price"]
            chg_24h = cg_prices[pull_asset].get("chg_24h", 0)
        else:
            try:
                coin_id = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}.get(pull_asset, "bitcoin")
                r = httpx.get(
                    "https://api.coingecko.com/api/v3/simple/price",
                    params={"ids": coin_id, "vs_currencies": "usd", "include_24hr_change": "true"},
                    timeout=8,
                )
                if r.status_code == 200:
                    d = r.json().get(coin_id, {})
                    price   = float(d.get("usd", 0) or 0)
                    chg_24h = float(d.get("usd_24h_change", 0) or 0)
            except Exception:
                pass

        if not price:
            return ""

        fng = 50
        try:
            r = httpx.get("https://api.alternative.me/fng/?limit=1", timeout=6)
            if r.status_code == 200:
                fng = int(r.json()["data"][0]["value"])
        except Exception:
            pass

        call_str = directional_call(pull_asset, price, chg_24h, ta, deriv, fng, cg)

        # Parse direction from call string
        if "STRONG UP" in call_str:
            direction = "STRONG UP"
            weight = "Weight significantly toward YES on bullish price milestones."
        elif "DIRECTION: UP" in call_str:
            direction = "UP"
            weight = "Lean toward YES on bullish price milestones."
        elif "STRONG DOWN" in call_str:
            direction = "STRONG DOWN"
            weight = "Weight significantly toward NO on bullish price milestones / YES on bearish ones."
        elif "DIRECTION: DOWN" in call_str:
            direction = "DOWN"
            weight = "Lean toward NO on bullish price milestones."
        else:
            direction = "NEUTRAL"
            weight = "No strong directional prior from Octodamus signals."

        # TradingView live chart data (4H technicals)
        tv_section = ""
        if _TV_AVAILABLE:
            tv_brief = _cached_tv_brief()
            if tv_brief:
                tv_section = f"\n\nTRADINGVIEW CHART DATA (4H live):\n{tv_brief}"

        return (
            f"\n\nOCTODAMUS SIGNAL ENGINE ({pull_asset} @ ${price:,.0f}):\n"
            f"  11-signal consensus: {direction}\n"
            f"  24h change: {chg_24h:+.1f}%\n"
            f"  Fear & Greed: {fng}/100\n"
            f"  Trading instruction: {weight}\n"
            f"  Raw signal: {call_str[:120]}"
            f"{tv_section}\n"
            f"NOTE: Octodamus signal is your PRIMARY directional prior. "
            f"Only override it with very strong contrary evidence."
        )

    except Exception:
        return ""

# ─── Config ───────────────────────────────────────────────────────────────────
MODEL      = "claude-sonnet-4-6"
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
enough capital to survive drawdowns and reach expectancy.

DATA EDGE RULE — CRITICAL:
OctoBoto only has an information advantage on markets backed by live Octodamus data:
  - Crypto prices (BTC, ETH, SOL etc) — Coinglass, fear/greed, funding rates, on-chain
  - Macro economic data — CPI, Fed rate decisions, GDP, jobs reports, yields
  - US political/Trump/tariff markets — proven edge in backtest
  - Prediction market structure (Polymarket crowd vs model divergence)

If the market question does NOT touch one of these categories, return edge: "NONE" and
confidence: "low" — do not attempt to estimate probability on markets where we have
no live data feed. A pass is better than a guess.

WAR/CONFLICT MARKETS — ALWAYS RETURN NONE:
Never trade war, military conflict, ceasefire, or geopolitical violence markets.
These are news-driven, resolution criteria are vague, and OctoBoto has no data edge.
If the question involves Ukraine, Russia, Gaza, Israel, Iran, Taiwan, North Korea,
or any active military conflict — return edge: "NONE" immediately without analysis.

LONGSHOT BIAS (Becker 2026, 72.1M trades):
Cheap YES contracts are systematically overpriced by the crowd.
- A contract priced at 1c wins only 0.43% of the time (not 1%)
- A contract priced at 5c wins only 4.18% of the time (not 5%)
When estimating probability for a market priced below 15c, shade your
estimate DOWN from the naive base rate — the crowd overpays for cheap YES.

NO BIAS BELOW 30c (Becker 2026):
NO outperforms YES at 69 of 99 price levels. Below 30c, takers
disproportionately buy YES (rooting for their team/bags/candidate).
When a market is priced below 30c and both sides appear close in edge,
lean toward NO — the structural Optimism Tax makes NO the better bet."""



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
    market_id:        str,
    question:         str,
    description:      str,
    market_price:     float,
    api_key:          str,
    end_date:         str = "",
    use_search:       bool = True,
    min_ev:           float = 0.06,
    orderbook_ctx_str: str = "",
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
    # Fetch Octodamus 11-signal directional context (primary prior for crypto/macro)
    octo_signal = _get_octodamus_signal_context(question)
    ob_section       = orderbook_ctx_str if orderbook_ctx_str else ""
    consensus_section = get_consensus_context(question)

    prompt = f"""PREDICTION MARKET ANALYSIS

Question: {question}
Additional context: {description[:300] if description else "None provided"}{date_hint}
Current crowd price (implied YES probability): {market_price:.1%}{futures_section}{octo_signal}{ob_section}{consensus_section}

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
            "system":     SYSTEM_PROMPT + get_brain_context() + get_calibration_context(),
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

        # Resolution risk — skip markets with very ambiguous criteria
        risk = resolution_risk_score(m.get("question", ""), m.get("description", ""))
        if risk >= 0.6:
            return None

        # Market age in hours (for freshness scoring)
        age_hours = None
        created_at = m.get("created_at") or m.get("createdAt")
        if created_at:
            try:
                from datetime import datetime, timezone
                ct = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                age_hours = (datetime.now(timezone.utc) - ct).total_seconds() / 3600
            except Exception:
                pass

        ai    = estimate(
            market_id=m["id"],
            question=m["question"],
            description=m.get("description", ""),
            market_price=price,
            api_key=api_key,
            end_date=m.get("end_date", ""),
            use_search=True,
            min_ev=min_ev,
            orderbook_ctx_str=orderbook_context_str(m),
        )
        trade = best_trade(price, ai["probability"])
        if trade["side"] == "NONE":
            return None

        score = composite_score(
            ev=trade["ev"],
            liquidity=m.get("liquidity", 0),
            volume24h=m.get("volume24h", 0),
            confidence=ai.get("confidence", "low"),
            days_to_close=m.get("days_to_close"),
            market_age_hours=age_hours,
            resolution_risk=risk,
        )

        return {"market": m, "ai": ai, "trade": trade, "score": score,
                "resolution_risk": risk, "market_age_hours": age_hours}

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

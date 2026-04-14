"""
octo_boto_consensus.py
Cross-platform market intelligence for OctoBoto.

Covers:
  #1 — Metaculus + Manifold + Kalshi price comparison (external crowd signal)
  #4 — GPT-4o-mini second opinion (multi-model consensus)
  #6 — Binance distance-to-milestone for crypto price markets

Kalshi is the highest-authority signal: US-regulated, real-money, professionally
arbitraged. A Kalshi vs Polymarket gap > 8% is strong evidence of Polymarket mispricing.

Import:
  from octo_boto_consensus import get_consensus_context, gpt_second_opinion, consensus_str
"""

import re
import json
import time
import threading
import httpx

METACULUS_API    = "https://www.metaculus.com/api/questions/"
MANIFOLD_API     = "https://api.manifold.markets/v0/search-markets"
KALSHI_PUBLIC_API = "https://api.elections.kalshi.com/trade-api/v2"   # no auth required

# Min word-overlap ratio to accept a question match
MATCH_THRESHOLD = 0.35


# ── Similarity ────────────────────────────────────────────────────────────────

def _token_overlap(a: str, b: str) -> float:
    """Word-level Jaccard similarity — fast and good enough for question matching."""
    stop = {"the", "a", "an", "is", "will", "by", "to", "in", "of", "at", "for",
            "on", "be", "this", "that", "or", "and", "it", "as", "with", "from"}
    a_tok = {t for t in re.findall(r'\w+', a.lower()) if t not in stop and len(t) > 2}
    b_tok = {t for t in re.findall(r'\w+', b.lower()) if t not in stop and len(t) > 2}
    if not a_tok or not b_tok:
        return 0.0
    return len(a_tok & b_tok) / max(len(a_tok), len(b_tok))


# ── Metaculus ─────────────────────────────────────────────────────────────────

def _query_metaculus(question: str) -> list:
    """Return list of {source, title, probability, similarity} for matching open questions."""
    try:
        r = httpx.get(
            METACULUS_API,
            params={"search": question[:120], "limit": 5, "type": "forecast", "status": "open"},
            headers={"Accept": "application/json"},
            timeout=8,
        )
        if r.status_code != 200:
            return []

        results = []
        for q in r.json().get("results", []):
            title = q.get("title", "")
            sim   = _token_overlap(question, title)
            if sim < MATCH_THRESHOLD:
                continue
            # Community median probability
            cp   = (q.get("community_prediction") or {})
            full = (cp.get("full") or {})
            prob = full.get("q2") or full.get("mean")
            if prob is not None:
                results.append({
                    "source":      "Metaculus",
                    "title":       title,
                    "probability": float(prob),
                    "similarity":  round(sim, 2),
                })
        return results
    except Exception:
        return []


# ── Manifold ──────────────────────────────────────────────────────────────────

def _query_manifold(question: str) -> list:
    """Return list of {source, title, probability, similarity} for matching binary markets."""
    try:
        r = httpx.get(
            MANIFOLD_API,
            params={"term": question[:120], "limit": 5, "filter": "open"},
            timeout=8,
        )
        if r.status_code != 200:
            return []

        results = []
        for m in r.json():
            if m.get("outcomeType") != "BINARY":
                continue
            title = m.get("question", "")
            sim   = _token_overlap(question, title)
            if sim < MATCH_THRESHOLD:
                continue
            prob = m.get("probability")
            if prob is not None:
                results.append({
                    "source":      "Manifold",
                    "title":       title,
                    "probability": float(prob),
                    "similarity":  round(sim, 2),
                })
        return results
    except Exception:
        return []


# ── Kalshi ───────────────────────────────────────────────────────────────────
#
# Kalshi is US-regulated (CFTC), real money, professionally arbitraged.
# Public API base: api.elections.kalshi.com (no auth required for market data)
#
# Strategies:
#   1. THRESHOLD MATCH — extract price/pct threshold from Polymarket question,
#      find matching Kalshi T-market in the right series. Direct price comparison.
#   2. DISTRIBUTION CONTEXT — for BTC questions, pull the full KXBTC distribution
#      and show where professional money prices the nearest level.
#   3. MACRO DIRECT — CPI/Fed/NFP questions mapped to known Kalshi series.
#   4. GENERAL FALLBACK — token-overlap on general open markets.

_KALSHI_CACHE_TTL = 15 * 60   # 15 min cache per series
_kalshi_series_cache: dict = {}  # {series_ticker: (markets_list, timestamp)}
_kalshi_lock = threading.Lock()

# Map question keywords → Kalshi series ticker
_SERIES_KEYWORDS: list[tuple[list[str], str]] = [
    (["bitcoin", "btc"],                                          "KXBTC"),
    (["ethereum", "eth"],                                         "KXETH"),
    (["federal reserve", "fed rate", "rate cut", "rate hike",
      "funds rate", "fomc", "interest rate decision"],           "KXFED"),
    (["cpi", "consumer price index", "inflation rate",
      "core cpi", "headline cpi"],                               "KXCPI"),
    (["non-farm", "nonfarm", "nfp", "payroll",
      "jobs report", "unemployment rate"],                       "KXNFP"),
    (["s&p 500", "s&p500", "spx", "spy", "nasdaq",
      "qqq", "stock market close"],                              "KXSPY"),
]


def _detect_series(question: str) -> str | None:
    """Return the best Kalshi series ticker for this question, or None."""
    q = question.lower()
    for keywords, series in _SERIES_KEYWORDS:
        if any(k in q for k in keywords):
            return series
    return None


def _fetch_kalshi_series(series_ticker: str) -> list:
    """
    Fetch all open markets for a Kalshi series, with 15-min cache per series.
    Returns list of raw market dicts.
    """
    with _kalshi_lock:
        cached = _kalshi_series_cache.get(series_ticker)
        if cached:
            markets, ts = cached
            if time.time() - ts < _KALSHI_CACHE_TTL:
                return markets

    try:
        r = httpx.get(
            f"{KALSHI_PUBLIC_API}/markets",
            params={"status": "open", "series_ticker": series_ticker, "limit": 200},
            headers={"Accept": "application/json"},
            timeout=10,
        )
        if r.status_code == 200:
            markets = r.json().get("markets", [])
        else:
            markets = []
    except Exception:
        markets = []

    with _kalshi_lock:
        _kalshi_series_cache[series_ticker] = (markets, time.time())
    return markets


# Note: The unfiltered Kalshi endpoint returns sports betting parlays (NBA, MLB, etc.)
# which are useless for prediction market analysis. We ONLY query known series:
# KXBTC, KXETH, KXFED, KXCPI, KXNFP, KXSPY.
# No general fallback — garbage in, garbage out.


def _kalshi_price(m: dict) -> float | None:
    """Extract YES probability from a raw Kalshi market dict (0–1 scale)."""
    # Prefer last traded price; fall back to bid/ask midpoint
    for field in ("last_price_dollars", "yes_ask_dollars"):
        val = m.get(field)
        if val is not None:
            try:
                p = float(val)
                if 0.01 <= p <= 0.99:
                    return round(p, 4)
            except (TypeError, ValueError):
                pass
    bid = m.get("yes_bid_dollars")
    ask = m.get("yes_ask_dollars")
    if bid is not None and ask is not None:
        try:
            p = (float(bid) + float(ask)) / 2
            if 0.01 <= p <= 0.99:
                return round(p, 4)
        except (TypeError, ValueError):
            pass
    return None


def _extract_threshold(question: str) -> float | None:
    """
    Extract the primary numeric threshold from a Polymarket question.
    Handles: $84,000 / $84k / 84000 / 0.5% / 4.25% / 80K
    Returns float or None.
    """
    q = question.replace(",", "")
    # Dollar amounts with optional k/K suffix: $80k, $80,000, $84500
    for raw, suffix in re.findall(r'\$\s*([\d]+(?:\.\d+)?)([kKmM]?)', q):
        try:
            val = float(raw)
            if suffix.lower() == "k":
                val *= 1_000
            elif suffix.lower() == "m":
                val *= 1_000_000
            if val > 100:   # Looks like a price, not a percentage
                return val
        except ValueError:
            pass
    # Percentage: 4.25%, 0.5%, -0.3%
    for raw in re.findall(r'(-?\d+(?:\.\d+)?)\s*%', q):
        try:
            return float(raw)
        except ValueError:
            pass
    return None


def _kalshi_threshold_match(markets: list, threshold: float, above: bool = True) -> dict | None:
    """
    Find the best T-market (threshold contract) in a Kalshi series that
    covers the given threshold.

    Kalshi T-tickers look like: KXBTC-26APR1214-T81799.99 (above $81,800)
    We find the T-market whose strike is nearest to our target threshold.

    above=True  → we want P(value ABOVE threshold) [YES = above]
    above=False → we want P(value BELOW threshold)
    """
    best = None
    best_dist = float("inf")

    for m in markets:
        ticker = m.get("ticker", "")
        # Only look at T-threshold markets
        t_match = re.search(r'-T([\d.]+)$', ticker)
        if not t_match:
            continue
        try:
            strike = float(t_match.group(1))
        except ValueError:
            continue

        dist = abs(strike - threshold)
        if dist < best_dist:
            price = _kalshi_price(m)
            if price is not None:
                best_dist = dist
                best = {
                    "ticker":    ticker,
                    "strike":    strike,
                    "price":     price,   # P(above strike)
                    "distance":  dist,
                    "close_time": m.get("close_time", ""),
                }

    return best


def _kalshi_btc_distribution_context(question: str) -> str:
    """
    For BTC/ETH price questions: fetch the KXBTC/KXETH series and find
    the market closest to the threshold in the question.

    Returns formatted context showing what professional Kalshi traders
    price the nearest price level at.
    """
    series = _detect_series(question)
    if series not in ("KXBTC", "KXETH", "KXSOL"):
        return ""

    threshold = _extract_threshold(question)
    if threshold is None:
        return ""

    markets = _fetch_kalshi_series(series)
    if not markets:
        return ""

    match = _kalshi_threshold_match(markets, threshold, above=True)
    if not match:
        return ""

    asset = series[2:]   # "BTC", "ETH", "SOL"
    close_date = ""
    if match["close_time"]:
        try:
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(match["close_time"].replace("Z", "+00:00"))
            close_date = f" (closes {dt.strftime('%b %d')})"
        except Exception:
            pass

    lines = [
        f"\nKALSHI {asset} PRICE DISTRIBUTION (CFTC-regulated, real money):",
        f"  Nearest threshold contract: ${match['strike']:,.0f}{close_date}",
        f"  Kalshi P({asset} > ${match['strike']:,.0f}): {match['price']:.0%}",
        f"  Strike distance from question threshold (${threshold:,.0f}): ${abs(match['distance']):,.0f}",
    ]

    if match["distance"] < threshold * 0.05:   # within 5% of target
        lines.append(f"  ✅ Very close match — use this as your primary price anchor.")
    else:
        lines.append(f"  ⚠️  Nearest contract is ${abs(match['distance']):,.0f} away — directional signal only.")

    lines.append(
        "  Kalshi's daily/weekly price contracts are professionally arbitraged. "
        "Weight heavily vs Polymarket."
    )
    return "\n".join(lines)


def _kalshi_macro_direct_match(question: str) -> str:
    """
    For CPI/Fed/NFP questions: find matching Kalshi series market by threshold.
    Returns formatted context or empty string.
    """
    series = _detect_series(question)
    if series not in ("KXFED", "KXCPI", "KXNFP"):
        return ""

    threshold = _extract_threshold(question)
    if threshold is None:
        return ""

    markets = _fetch_kalshi_series(series)
    if not markets:
        return ""

    match = _kalshi_threshold_match(markets, threshold, above=True)
    if not match:
        return ""

    series_labels = {"KXFED": "Fed Rate", "KXCPI": "CPI", "KXNFP": "Payrolls"}
    label = series_labels.get(series, series)

    lines = [
        f"\nKALSHI {label} MARKET (CFTC-regulated, real money):",
        f"  Threshold: {threshold:+.2f}% | Strike: {match['strike']:+.2f}%",
        f"  Kalshi P(above {match['strike']:+.2f}%): {match['price']:.0%}",
        f"  ✅ Direct Kalshi match — highest-confidence external prior available.",
        f"  Weight this heavily. Kalshi macro markets are arbitraged by institutions.",
    ]
    return "\n".join(lines)


def _query_kalshi(question: str) -> list:
    """
    Kalshi lookup — series-targeted only.
    Only queries KXBTC, KXETH, KXFED, KXCPI, KXNFP, KXSPY.
    Never queries the general endpoint (returns useless sports parlays).

    Returns list with at most 1 result.
    Kalshi authority="HIGH" — real money, CFTC-regulated.
    """
    series = _detect_series(question)
    if not series:
        return []   # No matching series — don't query Kalshi at all

    markets = _fetch_kalshi_series(series)
    if not markets:
        return []

    # 1. Threshold match (most accurate — direct price-level comparison)
    threshold = _extract_threshold(question)
    if threshold is not None:
        match = _kalshi_threshold_match(markets, threshold)
        if match and match["distance"] < abs(threshold) * 0.15:
            return [{
                "source":      "Kalshi",
                "title":       f"{series} | strike {match['strike']:,.2f}",
                "probability": match["price"],
                "similarity":  1.0,
                "authority":   "HIGH",
                "match_type":  "threshold",
            }]

    # 2. Token-overlap within the series (fallback for same-series questions
    #    that don't have a clean numeric threshold, e.g. "Will Fed cut in June?")
    results = []
    for m in markets:
        title = m.get("yes_sub_title") or m.get("title") or m.get("ticker", "")
        sim = _token_overlap(question, title)
        if sim >= MATCH_THRESHOLD:
            price = _kalshi_price(m)
            if price:
                results.append({
                    "source":      "Kalshi",
                    "title":       title[:80],
                    "probability": price,
                    "similarity":  round(sim, 2),
                    "authority":   "HIGH",
                    "match_type":  "series_overlap",
                })

    results.sort(key=lambda x: x["similarity"], reverse=True)
    return results[:1]



# ── Binance distance-to-milestone (#6) ───────────────────────────────────────

_CRYPTO_SYMBOLS = {
    "bitcoin": ("BTC", "BTCUSDT"),
    "btc":     ("BTC", "BTCUSDT"),
    "ethereum": ("ETH", "ETHUSDT"),
    "eth":     ("ETH", "ETHUSDT"),
    "solana":  ("SOL", "SOLUSDT"),
    "sol":     ("SOL", "SOLUSDT"),
}

_THRESHOLD_RANGES = {
    "BTC": (10_000, 500_000),
    "ETH": (500,    50_000),
    "SOL": (10,     10_000),
}


def _binance_distance_signal(question: str) -> str:
    """
    For crypto price milestone markets, compare current Binance price to resolution threshold.
    Returns formatted context string or empty string.
    """
    q = question.lower()

    asset = symbol = None
    for keyword, (a, s) in _CRYPTO_SYMBOLS.items():
        if keyword in q:
            asset, symbol = a, s
            break
    if not asset:
        return ""

    # Extract numeric threshold(s) from the question
    lo, hi = _THRESHOLD_RANGES[asset]
    threshold = None
    for raw in re.findall(r'\$?([\d,]+)(?:[kK])?', question):
        try:
            val = float(raw.replace(",", ""))
            # Handle '80k' style (already stripped 'k' in pattern — handle separately)
            if lo <= val <= hi:
                threshold = val
                break
        except ValueError:
            continue
    # Also try '80k' pattern explicitly
    if threshold is None:
        for raw in re.findall(r'\$?([\d]+)[kK]', question):
            try:
                val = float(raw) * 1000
                if lo <= val <= hi:
                    threshold = val
                    break
            except ValueError:
                continue

    if threshold is None:
        return ""

    try:
        r = httpx.get(
            "https://api.binance.com/api/v3/ticker/24hr",
            params={"symbol": symbol},
            timeout=6,
        )
        if r.status_code != 200:
            return ""
        data     = r.json()
        current  = float(data["lastPrice"])
        chg_24h  = float(data["priceChangePercent"])
        dist_pct = (threshold - current) / current * 100
        direction = "above" if dist_pct > 0 else "below"

        lines = [f"\nBINANCE PRICE CONTEXT ({asset}):"]
        lines.append(f"  Current:    ${current:,.0f}  ({chg_24h:+.1f}% 24h)")
        lines.append(f"  Threshold:  ${threshold:,.0f}")
        lines.append(f"  Distance:   {abs(dist_pct):.1f}% {direction} resolution price")

        if abs(dist_pct) < 3:
            lines.append("  At threshold — tiny move resolves this. Price is highly sensitive.")
        elif abs(dist_pct) > 25:
            lines.append(f"  Far from threshold — requires {abs(dist_pct):.0f}% move. Low probability unless strong catalyst.")

        # Days-needed estimate from 24h move rate (rough)
        if chg_24h and abs(dist_pct) > 0:
            daily_rate = abs(chg_24h)
            if daily_rate > 0.5:
                days_est = abs(dist_pct) / daily_rate
                lines.append(f"  At current {daily_rate:.1f}%/day pace: ~{days_est:.0f} days to threshold.")

        return "\n".join(lines)
    except Exception:
        return ""


# ── Main context builder ──────────────────────────────────────────────────────

def get_consensus_context(question: str) -> str:
    """
    Multi-source consensus context for AI estimate prompt.

    Sources (in authority order):
    1. Kalshi threshold match   — same event, same threshold, direct price comparison
    2. Kalshi distribution      — BTC/ETH: nearest price level probability
    3. Kalshi macro direct      — CPI/Fed/NFP: exact threshold from regulated market
    4. Metaculus + Manifold     — community forecasting platforms
    5. Binance distance         — for crypto price milestone markets

    Kalshi always listed first and weighted highest.
    """
    # Kalshi — three strategies, take whichever fires
    kalshi_matches       = _query_kalshi(question)
    kalshi_distribution  = _kalshi_btc_distribution_context(question)
    kalshi_macro         = _kalshi_macro_direct_match(question)

    external_matches = _query_metaculus(question) + _query_manifold(question)
    all_matches      = kalshi_matches + external_matches
    binance          = _binance_distance_signal(question)

    if not all_matches and not binance and not kalshi_distribution and not kalshi_macro:
        return ""

    lines = []

    if all_matches:
        seen, best = set(), []
        for m in sorted(all_matches,
                        key=lambda x: (x.get("authority") == "HIGH", x["similarity"]),
                        reverse=True):
            if m["source"] not in seen:
                best.append(m)
                seen.add(m["source"])

        lines.append("\nCROSS-PLATFORM CONSENSUS:")
        for m in best:
            auth_tag = " ⭐ [CFTC-regulated, real money]" if m.get("authority") == "HIGH" else ""
            match_type = m.get("match_type", "")
            type_tag = " [threshold match]" if match_type == "threshold" else ""
            lines.append(
                f"  {m['source']}: {m['probability']:.0%}{auth_tag}{type_tag} — "
                f"\"{m['title'][:65]}\" (sim: {m['similarity']:.0%})"
            )

        # Gap alert between Kalshi and others
        kalshi_m = next((m for m in best if m["source"] == "Kalshi"), None)
        others   = [m for m in best if m["source"] != "Kalshi"]
        if kalshi_m and others:
            max_gap = max(abs(kalshi_m["probability"] - m["probability"]) for m in others)
            if max_gap > 0.08:
                lines.append(
                    f"  ⚠️  KALSHI GAP {max_gap:.0%} vs other platforms — "
                    f"Kalshi is CFTC-regulated. Weight it heavily."
                )
        elif len(best) >= 2:
            probs = [m["probability"] for m in best]
            if max(probs) - min(probs) > 0.10:
                lines.append(f"  WARNING: {max(probs)-min(probs):.0%} spread — platforms disagree.")

        lines.append(
            "  Authority: Kalshi (real money, regulated) > Metaculus > Manifold."
        )

    # Kalshi structural signals (separate from cross-platform table)
    if kalshi_distribution:
        lines.append(kalshi_distribution)

    if kalshi_macro:
        lines.append(kalshi_macro)

    if binance:
        lines.append(binance)

    return "\n".join(lines)


# ── GPT-4o-mini second opinion (#4) ──────────────────────────────────────────

def gpt_second_opinion(question: str, market_price: float, openai_key: str) -> dict | None:
    """
    Get second-opinion probability estimate for multi-model consensus.
    Primary: GPT-4o-mini. Fallback: Venice private inference via Bankr x402.
    Returns {probability: float, reasoning: str} or None on failure.
    """
    prompt = (
        f"You are a calibrated prediction market analyst.\n\n"
        f"Question: {question}\n"
        f"Current Polymarket price (crowd YES probability): {market_price:.1%}\n\n"
        f"Estimate the TRUE probability this resolves YES. "
        f"Base rate first, then update on evidence. "
        f"Respond ONLY with valid JSON: "
        f'{"probability": 0.XX, "reasoning": "1-2 sentences"}'
    )

    # Primary: OpenAI GPT-4o-mini
    if openai_key:
        try:
            import openai
            client = openai.OpenAI(api_key=openai_key)
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=150,
                temperature=0.2,
            )
            text = resp.choices[0].message.content.strip()
            text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text, flags=re.MULTILINE)
            return json.loads(text)
        except Exception:
            pass  # Fall through to Venice

    # Fallback: Venice private inference via Bankr x402
    try:
        from octo_bankr import venice_chat
        text = venice_chat(prompt, max_tokens=150)
        if text:
            text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text, flags=re.MULTILINE)
            return json.loads(text)
    except Exception:
        pass

    return None


def consensus_str(claude_p: float, gpt_result: dict | None) -> str:
    """Format multi-model consensus for display in scan output."""
    if not gpt_result:
        return ""
    gpt_p = float(gpt_result.get("probability", 0))
    diff  = abs(claude_p - gpt_p)
    lines = [
        f"\n  GPT-4o-mini cross-check: {gpt_p:.0%} (Claude: {claude_p:.0%}, gap: {diff:.0%})"
    ]
    if diff < 0.05:
        lines.append("  Models AGREE — high conviction.")
    elif diff < 0.12:
        lines.append("  Models broadly agree — proceed.")
    else:
        lines.append("  Models DISAGREE — reduce size or skip.")
    return "\n".join(lines)


def get_kalshi_price(question: str) -> dict | None:
    """
    Return Kalshi's YES probability for a matching market, or None if no match found.

    Used by the triple-lock gate to check Kalshi alignment before entry.
    Returns: {probability: float, title: str, similarity: float} or None
    """
    matches = _query_kalshi(question)
    if not matches:
        return None
    return matches[0]


def kalshi_confirms_edge(
    question: str,
    trade_side: str,
    polymarket_price: float,
    min_gap: float = 0.06,
) -> dict:
    """
    Check whether Kalshi's price confirms or contradicts the proposed trade.

    Returns:
        confirmed   — True if Kalshi agrees with the edge direction
        contradicts — True if Kalshi disagrees (strong block signal)
        kalshi_p    — Kalshi probability (None if not found)
        gap         — abs(kalshi_p - polymarket_price)
        note        — human-readable explanation
    """
    k = get_kalshi_price(question)
    if k is None:
        return {"confirmed": False, "contradicts": False, "kalshi_p": None, "gap": 0.0, "note": "No Kalshi match"}

    kp  = k["probability"]
    gap = abs(kp - polymarket_price)

    if trade_side == "YES":
        # We think YES is underpriced (kp > polymarket_price confirms)
        confirmed   = kp > polymarket_price and gap >= min_gap
        contradicts = kp < polymarket_price and gap >= min_gap
    else:
        # We think NO is underpriced = YES is overpriced (kp < polymarket_price confirms)
        confirmed   = kp < polymarket_price and gap >= min_gap
        contradicts = kp > polymarket_price and gap >= min_gap

    if confirmed:
        note = f"Kalshi {kp:.0%} confirms {trade_side} edge vs Polymarket {polymarket_price:.0%} ({gap:.0%} gap)"
    elif contradicts:
        note = f"Kalshi {kp:.0%} CONTRADICTS {trade_side} — Polymarket {polymarket_price:.0%} ({gap:.0%} gap)"
    else:
        note = f"Kalshi {kp:.0%} — gap {gap:.0%} below threshold, no strong signal"

    return {
        "confirmed":   confirmed,
        "contradicts": contradicts,
        "kalshi_p":    kp,
        "gap":         round(gap, 4),
        "note":        note,
        "title":       k["title"],
        "similarity":  k["similarity"],
    }

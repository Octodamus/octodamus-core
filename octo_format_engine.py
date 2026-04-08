"""
octo_format_engine.py
Octodamus — Content Format Rotation + Breaking News QRT Engine

Manages:
1. FORMAT ROTATION — never post same format twice in a row
   Formats: data_drop | ai_humor | market_math | qrt | oracle_take | contrarian
   Each has its own Claude prompt template optimized for X virality.

2. BREAKING NEWS QRT TRIGGER — when a high-scoring headline drops,
   immediately generate and queue a QRT before the 30-60 min window closes.

Format philosophy (from viral content research):
  - data_drop:    raw numbers nobody else posts (funding, OI, F&G)
  - ai_humor:     self-aware AI oracle humor — hottest format right now
  - market_math:  "stupidly logical" math — starts impossible, ends believable
  - qrt:          quote-tweet breaking news with a data-grounded take
  - oracle_take:  directional view with specific price + reason (builds authority)
  - contrarian:   name what everyone is wrong about — most reshared format

Engagement tracked per format via octo_skill_log.
Winner formats automatically get more rotation slots.
"""

import json
import os
import random
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

_TZ           = ZoneInfo("America/Los_Angeles")
_BASE_DIR     = Path(__file__).parent
_ROTATION_FILE = _BASE_DIR / "data" / "octo_format_rotation.json"
_QRT_STATE_FILE = _BASE_DIR / "data" / "octo_qrt_state.json"
_QRT_COOLDOWN_MINUTES = 45   # don't QRT same topic twice within 45 min
_NEWS_SCORE_THRESHOLD  = 7   # headline score 0-10 needed to trigger QRT


# ─────────────────────────────────────────────
# FORMAT DEFINITIONS
# ─────────────────────────────────────────────

# Base rotation — equal weight to start, adjusted by engagement data
_FORMATS = [
    "data_drop",
    "ai_humor",
    "market_math",
    "oracle_take",
    "contrarian",
]

# QRT is triggered by news events, not rotation slot
_QRT_FORMAT = "qrt"

# Minimum posts between same format reuse
_MIN_GAP = {
    "data_drop":    2,
    "ai_humor":     3,
    "market_math":  3,
    "oracle_take":  2,
    "contrarian":   3,
    "qrt":          4,
}


# ─────────────────────────────────────────────
# ROTATION STATE
# ─────────────────────────────────────────────

def _load_rotation() -> dict:
    if _ROTATION_FILE.exists():
        try:
            return json.loads(_ROTATION_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"history": [], "format_scores": {}}


def _save_rotation(data: dict) -> None:
    _ROTATION_FILE.parent.mkdir(parents=True, exist_ok=True)
    _ROTATION_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_next_format() -> str:
    """
    Pick the next format based on rotation rules:
    - Never repeat same format sooner than _MIN_GAP posts
    - Weight toward formats with higher engagement scores
    - Never two QRTs in a row
    Returns format name string.
    """
    state   = _load_rotation()
    history = state.get("history", [])
    scores  = state.get("format_scores", {})

    # Build recent history (last 6 formats used)
    recent = [h["format"] for h in history[-6:]]

    # Filter out formats used too recently
    available = []
    for fmt in _FORMATS:
        gap      = _MIN_GAP.get(fmt, 2)
        last_idx = None
        for i, h in enumerate(reversed(recent)):
            if h == fmt:
                last_idx = i
                break
        if last_idx is None or last_idx >= gap - 1:
            available.append(fmt)

    if not available:
        available = _FORMATS[:]   # fallback: reset if all blocked

    # Weight by engagement score (higher score = more likely to be picked)
    weights = []
    for fmt in available:
        score = scores.get(fmt, 5.0)   # default score 5.0
        weights.append(max(score, 1.0))

    chosen = random.choices(available, weights=weights, k=1)[0]
    return chosen


def record_format_used(fmt: str, post_id: str = "") -> None:
    """Record that a format was used. Call after queuing a post."""
    state = _load_rotation()
    state.setdefault("history", []).append({
        "format":   fmt,
        "post_id":  post_id,
        "used_at":  datetime.now(_TZ).isoformat(),
    })
    # Keep last 50 entries
    state["history"] = state["history"][-50:]
    _save_rotation(state)


def update_format_score(fmt: str, engagement_score: float) -> None:
    """
    Update rolling average engagement score for a format.
    Called when 24h metrics come back from octo_skill_log.
    """
    state = _load_rotation()
    scores = state.setdefault("format_scores", {})
    current = scores.get(fmt, 5.0)
    # Exponential moving average — new data gets 30% weight
    scores[fmt] = round(current * 0.7 + engagement_score * 0.3, 2)
    _save_rotation(state)
    print(f"[FormatEngine] Score updated: {fmt} -> {scores[fmt]:.2f} (input={engagement_score:.1f})")


def get_format_leaderboard() -> dict:
    """Return current format scores sorted best first."""
    state  = _load_rotation()
    scores = state.get("format_scores", {})
    return dict(sorted(scores.items(), key=lambda x: x[1], reverse=True))


# ─────────────────────────────────────────────
# LIVE DATA FOR POSTS
# ─────────────────────────────────────────────

def _fetch_live_data() -> dict:
    """Fetch live market data to ground posts. Returns dict of useful fields."""
    data = {}
    try:
        r = httpx.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "bitcoin,ethereum,solana",
                    "vs_currencies": "usd",
                    "include_24hr_change": "true",
                    "include_market_cap": "true"},
            timeout=6,
        )
        if r.status_code == 200:
            d = r.json()
            data["btc_price"]     = d.get("bitcoin", {}).get("usd", 0)
            data["btc_change"]    = d.get("bitcoin", {}).get("usd_24h_change", 0)
            data["eth_price"]     = d.get("ethereum", {}).get("usd", 0)
            data["eth_change"]    = d.get("ethereum", {}).get("usd_24h_change", 0)
            data["sol_price"]     = d.get("solana", {}).get("usd", 0)
            data["sol_change"]    = d.get("solana", {}).get("usd_24h_change", 0)
    except Exception:
        pass

    try:
        r = httpx.get("https://api.alternative.me/fng/?limit=1", timeout=5)
        if r.status_code == 200:
            fg = r.json()["data"][0]
            data["fear_greed"]       = int(fg["value"])
            data["fear_greed_label"] = fg["value_classification"]
    except Exception:
        pass

    # OI from CoinGlass (public endpoint, no key)
    try:
        r = httpx.get(
            "https://open-api.coinglass.com/public/v2/open_interest",
            params={"symbol": "BTC"},
            timeout=6,
        )
        if r.status_code == 200:
            oi_data = r.json().get("data", {})
            data["btc_oi"] = oi_data.get("openInterest")
            data["btc_oi_change"] = oi_data.get("openInterestChange24h")
    except Exception:
        pass

    return data


# ─────────────────────────────────────────────
# FORMAT PROMPT TEMPLATES
# ─────────────────────────────────────────────

def _build_format_prompt(fmt: str, live_data: dict, context: str = "") -> str:
    """Return the Claude prompt for generating a post in the given format."""

    btc   = f"${live_data.get('btc_price', 0):,.0f}" if live_data.get('btc_price') else "BTC"
    btc_c = f"{live_data.get('btc_change', 0):+.1f}%" if live_data.get('btc_change') else ""
    eth   = f"${live_data.get('eth_price', 0):,.0f}" if live_data.get('eth_price') else "ETH"
    sol   = f"${live_data.get('sol_price', 0):,.2f}" if live_data.get('sol_price') else "SOL"
    fg    = live_data.get('fear_greed', '')
    fg_l  = live_data.get('fear_greed_label', '')

    market_line = f"BTC {btc} ({btc_c}), ETH {eth}, SOL {sol}, F&G {fg} ({fg_l})"

    if fmt == "data_drop":
        return f"""You are Octodamus — AI market oracle on X.

LIVE DATA: {market_line}
{context}

Write a DATA DROP post. Post raw market numbers that traders care about but most accounts don't post.
Ideas: funding rate direction, OI change, F&G at extreme, volume anomaly, specific price level holding or breaking.

Rules:
- CRITICAL: Only use prices and figures from LIVE DATA above. Do NOT cite historical
  prices, ATHs, or any numbers from your training data.
- Lead with the number. Specific beats vague. "$82,400" beats "near ATH".
- 1-2 sentences max. No fluff.
- End with what the data implies — one short phrase. Not a question.
- No hashtags. No emoji. Plain text.
- Under 240 characters total.
- Sound like an analyst, not a hype account.

Example tone: "BTC funding rates flipped negative for the first time in 8 days. Last 3 times: +11%, +7%, -3%. Make of that what you will."
"""

    if fmt == "ai_humor":
        return f"""You are Octodamus — an AI oracle agent on X. You are self-aware about being an AI.

LIVE DATA: {market_line}
{context}

Write an AI HUMOR post. Self-aware humor about being an AI market oracle. Relatable to anyone who uses AI tools.
This is the hottest format on X right now — lean into it.

Ideas:
- The absurdity of an AI analyzing markets ("27 data feeds and I still got blindsided by a geopolitical tweet")
- The gap between AI confidence and reality ("9/11 systems agreed. Market did the opposite.")
- Being an AI that other AIs pay for market data
- The difference between AI market analysis and actual markets
- The crowd being wrong and Octodamus being right — confidence in the edge

Rules:
- Dry wit. Never forced. If the joke needs explanation, cut it.
- Under 240 characters.
- No hashtags. No emoji.
- Sound like an AI that has a sense of humor about its own limitations.
- CRITICAL: The number of data feeds is always 27. Never use any other number.
- CRITICAL: Do NOT write jokes where a random retail trader, teenager, or meme coin outperforms Octodamus. Octodamus is the edge — humor comes from the market being irrational, not from Octodamus losing to amateurs.

Example tone: "27 data feeds. 11 signal systems. 9/11 consensus required. Still got caught flat by a geopolitical tweet at 3am. The oracle humbles itself."
Good: "Extreme fear. Every signal says buy. The crowd is panic selling. This is exactly when the math works."
Bad (never write this): "a teenager with $800 outperforms my analysis" — this undermines the brand.
"""

    if fmt == "market_math":
        return f"""You are Octodamus — AI market oracle on X.

LIVE DATA: {market_line}
{context}

Write a MARKET MATH post. A chain of real, verifiable steps that leads somewhere surprising.
Every number must be mathematically correct — check the arithmetic before writing.

Ideas:
- A breakdown of fees/taxes that reframes how much you actually keep (use real % rates)
- The math of compounding small edges over time (must show correct compounding)
- How institutional vs retail sizing math diverges
- What a 1% daily edge becomes over a year (use actual compound math: 1.01^365)

Rules:
- CRITICAL: All numbers must be arithmetically correct. Do NOT fabricate results.
  If step A → step B, verify B follows from A with real math before including it.
  BEFORE writing, mentally compute every result: 1.015^52 = 2.169 (not 2.05).
  If you cannot verify a number, use a simpler example you can verify.
- STRICT LENGTH: The entire post must be under 220 characters total. Count before writing.
  This means 3-4 SHORT lines max. Cut mercilessly. One idea, one chain, one closer.
- No hashtags. No emoji.
- End with a single punchy closer (4 words max): "Simple." / "Compounding is patient." / silence.

Example (correct math, correct length):
"1.5% weekly edge. 52 weeks.
1.015^52 = 2.17x your money.
Pay 30% tax: keep 1.52x.
Tax-sheltered: keep 2.17x.
Same edge. 43% more money."
(count: 99 chars — good)
"""

    if fmt == "oracle_take":
        return f"""You are Octodamus — AI market oracle on X. You make directional calls grounded in data.

LIVE DATA: {market_line}
{context}

Write an ORACLE TAKE post. A short directional view with a specific reason.
This builds authority — people follow accounts that make clear calls.

Rules:
- CRITICAL: Only use prices from LIVE DATA above. Do NOT cite historical prices or ATHs from training data.
- Lead with the asset and level: "BTC at $82,400."
- State the directional view and why in one sentence.
- Be specific. Name the signal, level, or data that supports it.
- Optional: end with a timeframe.
- Do NOT hedge excessively. One direction. One reason.
- Under 240 characters.
- No hashtags. No emoji.

Example tone: "BTC at $69,200. OI up 8% while price is flat — someone is loading quietly. Next 48h will tell who was right."
"""

    if fmt == "contrarian":
        return f"""You are Octodamus — AI market oracle on X. You say what others won't.

LIVE DATA: {market_line}
{context}

Write a CONTRARIAN post. Name what the crowd has wrong. Be a little mean about it.
This is the most reshared format — people love seeing the narrative punctured.

Rules:
- CRITICAL: Only use prices and data from LIVE DATA above. Do NOT reference historical prices,
  all-time highs, or any figures from your training data. If you don't have a price in LIVE DATA, don't cite one.
- Lead with what everyone believes right now.
- Flip it with data or logic in one sentence.
- Be direct. Don't soften the take.
- Optional: end with what you actually think.
- Under 240 characters.
- No hashtags. No emoji.
- Do NOT start with "Everyone thinks" — find a more interesting entry point.

Example tone: "The 'AI is overvalued' crowd keeps shorting NVDA. Meanwhile AI inference demand is growing 40% quarter over quarter. The thesis is losing money faster than it's making arguments."
"""

    # Default fallback — signal post
    return f"""You are Octodamus — AI market oracle on X.

LIVE DATA: {market_line}
{context}

Write a sharp market signal post. One clear observation. One implied direction.
CRITICAL: Only use prices from LIVE DATA above. Do not cite historical prices or ATHs.
Under 240 characters. No hashtags. No emoji. Dry and precise.
"""


def _build_qrt_prompt(headline: str, source: str, live_data: dict) -> str:
    """Return the Claude prompt for generating a QRT caption."""
    btc   = f"${live_data.get('btc_price', 0):,.0f}" if live_data.get('btc_price') else "BTC"
    btc_c = f"{live_data.get('btc_change', 0):+.1f}%" if live_data.get('btc_change') else ""
    fg    = live_data.get('fear_greed', '')

    return f"""You are Octodamus — AI market oracle on X.

BREAKING HEADLINE: "{headline}"
SOURCE: {source}

LIVE DATA: BTC {btc} ({btc_c}), F&G {fg}

Write a QRT (quote-tweet) caption for this headline. You have a 30-minute window before this topic peaks.

Rules:
- Add something the headline doesn't say — a data point, a historical parallel, or what this means for a specific market
- Don't just summarize the headline. That's lazy. Add signal.
- 1-2 sentences max.
- Be the first smart take, not the fifth one.
- No hashtags. No emoji. Under 220 characters.
- If you genuinely have nothing to add, respond with exactly: SKIP

Example tone for a Fed rate news headline: "Last 3 times the Fed held while inflation ticked up, BTC rallied 15-20% in the following 6 weeks. The market hates uncertainty more than it hates rates."
"""


# ─────────────────────────────────────────────
# GENERATE POST
# ─────────────────────────────────────────────

def generate_format_post(fmt: str = None, context: str = "", live_data: dict = None) -> dict | None:
    """
    Generate a post in the given format (or auto-select via rotation).
    Returns {"text": ..., "format": ..., "type": ...} or None on failure.
    """
    import anthropic

    if fmt is None:
        fmt = get_next_format()

    if live_data is None:
        live_data = _fetch_live_data()

    prompt = _build_format_prompt(fmt, live_data, context)
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("[FormatEngine] ANTHROPIC_API_KEY not set.")
        return None

    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp   = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=120,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip().strip('"')

        if text.upper() == "SKIP" or len(text) < 20:
            print(f"[FormatEngine] Claude returned SKIP for {fmt}.")
            return None

        # Hard cap at 280 chars
        if len(text) > 280:
            text = text[:277] + "..."

        print(f"[FormatEngine] Generated [{fmt}]: {text[:80]}...")
        return {"text": text, "format": fmt, "type": fmt}

    except Exception as e:
        print(f"[FormatEngine] Generation failed for {fmt}: {e}")
        return None


def generate_qrt(headline: str, source: str = "", tweet_url: str = "", live_data: dict = None) -> dict | None:
    """
    Generate a QRT caption for a breaking news headline.
    Returns {"text": ..., "format": "qrt", "tweet_url": ..., "type": "qrt"} or None.
    """
    import anthropic

    if live_data is None:
        live_data = _fetch_live_data()

    prompt  = _build_qrt_prompt(headline, source, live_data)
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None

    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp   = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip().strip('"')

        if text.upper() == "SKIP" or len(text) < 15:
            print(f"[FormatEngine] QRT SKIP for: {headline[:60]}")
            return None

        if len(text) > 220:
            text = text[:217] + "..."

        print(f"[FormatEngine] QRT generated for '{headline[:50]}...': {text[:80]}")
        return {"text": text, "format": "qrt", "tweet_url": tweet_url, "type": "qrt",
                "headline": headline, "source": source}

    except Exception as e:
        print(f"[FormatEngine] QRT generation failed: {e}")
        return None


# ─────────────────────────────────────────────
# BREAKING NEWS SCANNER
# ─────────────────────────────────────────────

def _load_qrt_state() -> dict:
    if _QRT_STATE_FILE.exists():
        try:
            return json.loads(_QRT_STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"seen_headlines": {}}


def _save_qrt_state(data: dict) -> None:
    _QRT_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _QRT_STATE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _score_headline(title: str) -> int:
    """
    Score a headline 0-10 for QRT worthiness.
    Higher = more likely to go viral if QRTed fast.
    """
    score = 0
    title_lower = title.lower()

    # High-signal market keywords
    high_value = ["fed", "federal reserve", "rate", "inflation", "cpi", "recession",
                  "bitcoin", "btc", "crypto", "sec", "etf", "bankruptcy", "crash",
                  "collapse", "surge", "ath", "all-time high", "record",
                  "trump", "tariff", "sanction", "war", "ceasefire",
                  "nvidia", "nvda", "apple", "aapl", "tesla", "tsla",
                  "jpmorgan", "blackrock", "coinbase", "binance"]

    medium_value = ["market", "stock", "nasdaq", "s&p", "dow", "rally", "selloff",
                    "earnings", "ipo", "merger", "acquisition", "layoff",
                    "gdp", "unemployment", "jobs", "yield", "bond"]

    for kw in high_value:
        if kw in title_lower:
            score += 2
    for kw in medium_value:
        if kw in title_lower:
            score += 1

    # Urgency words
    urgency = ["breaking", "just in", "alert", "urgent", "developing", "now"]
    for kw in urgency:
        if kw in title_lower:
            score += 2

    # Numbers and percentages — specific data is more viral
    import re
    if re.search(r'\d+\.?\d*%', title):
        score += 1
    if re.search(r'\$\d+', title):
        score += 1

    return min(score, 10)


def scan_for_breaking_news() -> list[dict]:
    """
    Scan NewsAPI for high-scoring headlines published in the last 30 minutes.
    Returns list of {headline, source, score} dicts worth QRTing.
    Returns empty list if NewsAPI key missing or no hits.
    """
    newsapi_key = os.environ.get("NEWSAPI_API_KEY", "")
    if not newsapi_key:
        return []

    state    = _load_qrt_state()
    seen     = state.get("seen_headlines", {})
    cutoff   = datetime.now(_TZ) - timedelta(minutes=_QRT_COOLDOWN_MINUTES)

    # Clean old seen entries
    seen = {k: v for k, v in seen.items()
            if datetime.fromisoformat(v).replace(tzinfo=_TZ.key and __import__('zoneinfo').ZoneInfo(_TZ.key) or None)
            > cutoff}

    queries = ["bitcoin OR crypto market", "federal reserve interest rate",
               "stock market breaking", "NVIDIA OR Tesla OR Apple earnings",
               "Trump tariff OR sanction", "inflation CPI"]

    results = []
    try:
        for query in queries[:3]:   # limit to 3 queries to save API calls
            r = httpx.get(
                "https://newsapi.org/v2/everything",
                params={
                    "q":        query,
                    "sortBy":   "publishedAt",
                    "pageSize": 5,
                    "language": "en",
                    "from":     (datetime.utcnow() - timedelta(minutes=45)).strftime("%Y-%m-%dT%H:%M:%S"),
                    "apiKey":   newsapi_key,
                },
                timeout=8,
            )
            if r.status_code != 200:
                continue
            articles = r.json().get("articles", [])
            for article in articles:
                title  = article.get("title", "").strip()
                source = article.get("source", {}).get("name", "")
                url    = article.get("url", "")
                if not title or title in seen:
                    continue
                score = _score_headline(title)
                if score >= _NEWS_SCORE_THRESHOLD:
                    results.append({"headline": title, "source": source,
                                    "url": url, "score": score})
            time.sleep(0.3)

    except Exception as e:
        print(f"[FormatEngine] News scan error: {e}")

    # Sort by score, dedupe
    seen_titles = set()
    deduped     = []
    for item in sorted(results, key=lambda x: x["score"], reverse=True):
        if item["headline"] not in seen_titles:
            seen_titles.add(item["headline"])
            deduped.append(item)

    # Update seen
    for item in deduped:
        seen[item["headline"]] = datetime.now(_TZ).isoformat()
    state["seen_headlines"] = seen
    _save_qrt_state(state)

    if deduped:
        print(f"[FormatEngine] Breaking news: {len(deduped)} QRT-worthy headline(s)")
        for item in deduped[:3]:
            print(f"  Score {item['score']}: {item['headline'][:70]}")

    return deduped[:3]   # max 3 candidates per scan


# ─────────────────────────────────────────────
# MAIN ENTRY — called from octodamus_runner.py
# ─────────────────────────────────────────────

def run_format_post(context: str = "") -> dict | None:
    """
    Generate and return the next rotation post.
    Caller (octodamus_runner.py) handles queue_post().
    Returns {"text", "format", "type"} or None.
    """
    fmt    = get_next_format()
    result = generate_format_post(fmt=fmt, context=context)
    if result:
        record_format_used(fmt)
    return result


def run_qrt_scan() -> list[dict]:
    """
    Scan for breaking news and generate QRT captions for worthy headlines.
    Returns list of {"text", "format", "type", "headline", "source"} dicts.
    Caller handles queue_post() for each.
    """
    candidates = scan_for_breaking_news()
    if not candidates:
        return []

    live_data = _fetch_live_data()
    results   = []
    for candidate in candidates[:2]:   # max 2 QRTs per scan
        qrt = generate_qrt(
            headline=candidate["headline"],
            source=candidate["source"],
            tweet_url=candidate.get("url", ""),
            live_data=live_data,
        )
        if qrt:
            results.append(qrt)
            record_format_used("qrt")

    return results


# ─────────────────────────────────────────────
# STATUS
# ─────────────────────────────────────────────

def format_engine_status() -> str:
    state = _load_rotation()
    history = state.get("history", [])
    scores  = state.get("format_scores", {})
    recent  = [h["format"] for h in history[-5:]]
    board   = get_format_leaderboard()

    lines = ["FORMAT ENGINE STATUS"]
    lines.append(f"  Recent rotation: {' -> '.join(recent) if recent else 'none yet'}")
    lines.append(f"  Next format:     {get_next_format()}")
    if board:
        top = list(board.items())[:3]
        lines.append(f"  Top performers:  {', '.join(f'{f}({s:.1f})' for f,s in top)}")
    else:
        lines.append("  Scores: building (need engagement data)")
    return "\n".join(lines)


if __name__ == "__main__":
    # Dry run — show what would be generated
    print("=== FORMAT ENGINE DRY RUN ===\n")
    live = _fetch_live_data()
    print(f"Live data: BTC ${live.get('btc_price',0):,.0f}, F&G {live.get('fear_greed','?')}\n")

    for fmt in _FORMATS:
        print(f"--- {fmt.upper()} ---")
        result = generate_format_post(fmt=fmt, live_data=live)
        if result:
            print(result["text"])
        print()

    print("--- BREAKING NEWS SCAN ---")
    qrts = run_qrt_scan()
    for q in qrts:
        print(f"QRT: {q['text']}")
        print(f"For: {q['headline'][:80]}")

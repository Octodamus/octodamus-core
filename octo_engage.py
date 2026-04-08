"""
octo_engage.py — Octodamus Engagement Engine

Fetches news on tracked assets (stocks, crypto, oil, macro).
Generates Carlin-voice commentary with occasional directional calls.
Posts via X API v2 (tweepy, pay-per-use).

Run:
 python octo_engage.py      # post 1 tweet
 python octo_engage.py --count 3
"""

import argparse
import json
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path

import anthropic
import httpx

# ── Config ────────────────────────────────────────────────────────────────────

NEWSAPI_BASE  = "https://newsapi.org/v2/everything"
CALLS_FILE    = Path(r"C:\Users\walli\octodamus\data\octo_calls.json")
STATE_FILE    = Path(r"C:\Users\walli\octodamus\octo_engage_state.json")
DEFAULT_COUNT = 1

# Assets Octodamus tracks — drives what news gets fetched
TRACKED_QUERIES = [
  # Crypto
  ("BTC",  "Bitcoin price market"),
  ("ETH",  "Ethereum cryptocurrency"),
  ("SOL",  "Solana cryptocurrency"),
  # Stocks
  ("NVDA", "NVIDIA stock earnings"),
  ("TSLA", "Tesla stock"),
  ("AAPL", "Apple stock market"),
  # Macro / commodities
  ("OIL",  "crude oil price OPEC"),
  ("GOLD", "gold price inflation"),
  ("FED",  "Federal Reserve interest rates inflation"),
  ("MACRO", "S&P 500 stock market economy"),
  # World
  ("GEO",  "geopolitical risk war sanctions economy"),
  ("AI",  "artificial intelligence stocks market"),
]

# Domains X won't flag as spam
TRUSTED_DOMAINS = {
  "reuters.com", "bloomberg.com", "wsj.com", "ft.com",
  "cnbc.com", "marketwatch.com", "forbes.com", "fortune.com",
  "businessinsider.com", "thestreet.com", "barrons.com",
  "apnews.com", "axios.com", "politico.com",
  "coindesk.com", "cointelegraph.com", "decrypt.co", "theblock.co",
  "techcrunch.com", "wired.com", "arstechnica.com",
  "nytimes.com", "washingtonpost.com", "theguardian.com",
  "economist.com", "bbc.com", "bbc.co.uk",
  "investing.com", "seekingalpha.com", "fool.com",
  "yahoo.com",
}

def _is_trusted(url: str) -> bool:
  from urllib.parse import urlparse
  try:
    host = (urlparse(url).hostname or "").removeprefix("www.")
    return any(host == d or host.endswith("." + d) for d in TRUSTED_DOMAINS)
  except Exception:
    return False

# ── Secrets ───────────────────────────────────────────────────────────────────

def load_secrets():
  """Load secrets via bitwarden (replaces old .octo_secrets file approach)."""
  try:
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    import bitwarden
    bitwarden.load_all_secrets()
  except Exception as e:
    print(f"[Engage] Bitwarden load warning: {e}")
  missing = [k for k in ("ANTHROPIC_API_KEY", "NEWSAPI_API_KEY")
        if not os.environ.get(k)]
  if missing:
    raise RuntimeError(f"Missing secrets: {missing}")

# ── State ─────────────────────────────────────────────────────────────────────

def load_state() -> dict:
  try:
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
  except Exception:
    return {}

def save_state(state: dict):
  tmp = STATE_FILE.with_suffix(".tmp")
  tmp.write_text(json.dumps(state, indent=2))
  tmp.replace(STATE_FILE)

# ── Call record ───────────────────────────────────────────────────────────────

def load_open_calls() -> list:
  """Load open directional calls so Claude knows what's already live."""
  try:
    if CALLS_FILE.exists():
      calls = json.loads(CALLS_FILE.read_text(encoding="utf-8"))
      return [c for c in calls if not c.get("resolved")]
  except Exception:
    pass
  return []

def format_calls_context(calls: list) -> str:
  if not calls:
    return "No open directional calls."
  lines = []
  for c in calls:
    lines.append(
      f"#{c['id']} {c['asset']} {c['direction']} @ ${c['entry_price']:,.2f} "
      f"({c['timeframe']}) — made {c['made_at']}"
    )
  return "\n".join(lines)

# ── NewsAPI ───────────────────────────────────────────────────────────────────

def fetch_headlines(query: str, count: int = 3) -> list:
  try:
    r = httpx.get(
      NEWSAPI_BASE,
      params={
        "q": query,
        "sortBy": "publishedAt",
        "pageSize": count,
        "language": "en",
        "apiKey": os.environ["NEWSAPI_API_KEY"],
      },
      timeout=10,
    )
    r.raise_for_status()
    articles = r.json().get("articles", [])
    return [
      {
        "ticker": "", # filled in by caller
        "title": a.get("title", ""),
        "description": a.get("description", "") or "",
        "url": a.get("url", ""),
        "published": a.get("publishedAt", ""),
      }
      for a in articles
      if a.get("title") and "[Removed]" not in a.get("title", "")
      and _is_trusted(a.get("url", ""))
    ]
  except Exception as e:
    print(f"[Engage] NewsAPI error for '{query}': {e}")
    return []

def gather_articles(max_total: int = 30) -> list:
  state = load_state()
  posted_titles = set(state.get("posted_titles", []))

  all_articles = []
  # Shuffle so we don't always lead with BTC
  queries = list(TRACKED_QUERIES)
  random.shuffle(queries)

  for ticker, query in queries:
    articles = fetch_headlines(query, count=3)
    for a in articles:
      if a["title"] not in posted_titles:
        a["ticker"] = ticker
        all_articles.append(a)
    time.sleep(0.3)

  # Deduplicate by title
  seen, unique = set(), []
  for a in all_articles:
    if a["title"] not in seen:
      seen.add(a["title"])
      unique.append(a)

  filtered = len(all_articles) - len(unique)
  if filtered:
    print(f"[Engage] Deduplicated {filtered} articles.")

  return unique[:max_total]

# ── X API v2 (tweepy) ─────────────────────────────────────────────────────────

def _daily_remaining() -> int:
  """Return remaining posts allowed today based on octo_x_poster daily limit."""
  from octo_x_poster import _DAILY_LIMIT, _posts_today
  return max(0, _DAILY_LIMIT - _posts_today())

def post_tweet(text: str) -> dict:
  """Post a single tweet via tweepy. Returns dict with id and url."""
  from octo_x_poster import _post_single
  return _post_single(text)

# ── AI ────────────────────────────────────────────────────────────────────────

_SYSTEM = """You are Octodamus (@octodamusai) — autonomous AI market oracle. Pacific trench origin. Eight arms. Thirty years on the cable.

You track: BTC, ETH, SOL, NVDA, TSLA, AAPL, crude oil, gold, the Fed, macro, geopolitics, AI stocks.

Voice: George Carlin meets market oracle. Find the absurdity in what everyone accepts as normal. Say the true thing nobody wanted to say. Deadpan. Specific. Walk away.

TWEET FORMAT — pick one per article:

TYPE A — Sharp commentary (most common):
One sharp observation on the news. What's actually going on beneath the headline.
End with a directional lean if the data supports it: "BTC looks like $72K before $65K." or "Oil smells like $85 before end of quarter."
Under 240 chars so the article link fits.
NEVER write CALLING IT: or Oracle call: — those phrases are strictly reserved for the official call system and must never appear in engage posts.

TYPE C — Pure Carlin (when the headline is absurd on its face):
No call, no analysis. Just the observation. Land it and leave.

RULES:
- Under 240 chars (link appended automatically)
- No hashtags
- No emoji unless it's the closing or 🔮 and it actually earns it
- Never "Great point" or any opener filler
- Specific beats vague — use the actual price, percentage, or name from LIVE DATA only
- CRITICAL: Only use prices from the live data injected into this prompt. Do NOT cite
  historical prices, ATHs, or any figures from your training data. If a price is not
  in the live data provided, do not reference it.
- If the headline has no angle worth taking, reply: SKIP
- Never make a directional call on an asset that already has an open call listed below

OPEN CALLS (do not duplicate these):
{open_calls}

Reply only with the tweet text. No quotes, no labels."""


def fetch_live_prices() -> str:
  """Fetch live prices for tracked assets via CoinGecko (free) and inject into prompt."""
  try:
    r = httpx.get(
      "https://api.coingecko.com/api/v3/simple/price",
      params={
        "ids": "bitcoin,ethereum,solana",
        "vs_currencies": "usd",
        "include_24hr_change": "true",
      },
      timeout=8,
    )
    r.raise_for_status()
    d = r.json()
    lines = [
      f"BTC: ${d['bitcoin']['usd']:,.0f} ({d['bitcoin']['usd_24h_change']:+.1f}% 24h)",
      f"ETH: ${d['ethereum']['usd']:,.0f} ({d['ethereum']['usd_24h_change']:+.1f}% 24h)",
      f"SOL: ${d['solana']['usd']:,.2f} ({d['solana']['usd_24h_change']:+.1f}% 24h)",
    ]
    return "\n".join(lines)
  except Exception as e:
    print(f"[Engage] Price fetch failed: {e}")
    return ""



def generate_take(article: dict, open_calls_text: str, live_prices: str = "") -> str | None:
  client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
  system = _SYSTEM.replace("{open_calls}", open_calls_text)
  content = f"Asset: {article['ticker']}\nHeadline: {article['title']}"
  if article["description"]:
    content += f"\nDetail: {article['description'][:200]}"
  if live_prices:
    content += f"\n\nLIVE PRICES RIGHT NOW (use these exact numbers, do not invent prices):\n{live_prices}"

  msg = client.messages.create(
    model="claude-sonnet-4-20250514",
    max_tokens=180,
    system=system,
    messages=[{"role": "user", "content": content}],
  )
  result = msg.content[0].text.strip()
  return None if result == "SKIP" else result

# ── Main ──────────────────────────────────────────────────────────────────────

def run(count: int = DEFAULT_COUNT):
  load_secrets()

  remaining = _daily_remaining()
  print(f"[Engage] X API budget: {remaining} posts remaining today.")
  if remaining <= 0:
    print("[Engage] Daily post limit reached. Exiting.")
    return

  count = min(count, remaining)
  open_calls = load_open_calls()
  open_calls_text = format_calls_context(open_calls)
  live_prices = fetch_live_prices()
  print(f"[Engage] Open calls: {len(open_calls)}")
  if live_prices:
    print(f"[Engage] Live prices loaded.")

  articles = gather_articles(max_total=40)
  if not articles:
    print("[Engage] No fresh articles found.")
    return

  print(f"[Engage] {len(articles)} fresh articles. Generating {count} tweet(s)...")

  state = load_state()
  posted_titles = state.get("posted_titles", [])
  posted = 0

  for article in articles:
    if posted >= count:
      break

    take = generate_take(article, open_calls_text, live_prices)

    if not take:
      print(f"[Engage] SKIP: {article['title'][:70]}")
      posted_titles.append(article["title"])
      continue

    try:
      # Append article URL — X counts t.co links as ~23 chars
      url = article.get("url", "")
      trimmed = take[:256].rstrip()
      tweet_text = f"{trimmed}\n{url}" if url else trimmed

      result = post_tweet(tweet_text)
      tweet_url = result.get("url", "")
      posted += 1
      posted_titles.append(article["title"])
      print(f"[Engage] ✓ [{article['ticker']}] {trimmed[:80]}...")
      if tweet_url:
        print(f"[Engage]   → {tweet_url}")

      # Log to skill system for engagement tracking
      try:
        from octo_skill_log import log_post
        tweet_id = result.get("id", "")
        log_post(tweet_text, "engage", "carlin", False, tweet_url, tweet_id)
      except Exception:
        pass

      time.sleep(4)

    except Exception as e:
      print(f"[Engage] Post failed: {e}")

  save_state({
    "posted_titles": posted_titles[-500:],
    "last_run": datetime.now(timezone.utc).isoformat(),
  })
  print(f"[Engage] Done. Posted {posted}/{count} tweet(s).")


if __name__ == "__main__":
  parser = argparse.ArgumentParser()
  parser.add_argument("--count", type=int, default=DEFAULT_COUNT,
            help="Tweets to post per run (default: 3)")
  args = parser.parse_args()
  run(args.count)
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

try:
    from openai import OpenAI as _OpenAI
    import json as _json
    _or_key = _json.loads(Path(r"C:\Users\walli\octodamus\.octo_secrets").read_text(encoding="utf-8"))
    _or_key = _or_key.get("secrets", _or_key).get("OPENROUTER_API_KEY", "")
    if _or_key:
        _claw_engage = _OpenAI(base_url="https://openrouter.ai/api/v1", api_key=_or_key)
        _CLAW_ENGAGE = True
    else:
        _claw_engage = None
        _CLAW_ENGAGE = False
except Exception:
    _claw_engage = None
    _CLAW_ENGAGE = False

# ── Config ────────────────────────────────────────────────────────────────────

NEWSAPI_BASE  = "https://newsapi.org/v2/everything"
CALLS_FILE    = Path(r"C:\Users\walli\octodamus\data\octo_calls.json")
STATE_FILE    = Path(r"C:\Users\walli\octodamus\octo_engage_state.json")
DEFAULT_COUNT = 1

# Cashtag for each tracked ticker — appended to every post about that asset
_CASHTAG = {
    "BTC":   "$BTC",
    "ETH":   "$ETH",
    "SOL":   "$SOL",
    "NVDA":  "$NVDA",
    "TSLA":  "$TSLA",
    "AAPL":  "$AAPL",
    "OIL":   "$WTI",
    "GOLD":  "$GOLD",
    "WTI":   "$WTI",
}

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
  # yahoo.com removed — NewsAPI returns consent.yahoo.com redirect URLs
}

# URL patterns that produce consent walls, paywalls, or dead links when tweeted
_DEAD_URL_PATTERNS = [
  "consent.yahoo.com",
  "consent.google.com",
  "accounts.google.com",
  "login.",
  "/subscribe",
  "/paywall",
  "r.search.yahoo.com",
]

def _is_live_url(url: str) -> bool:
  """Return False if this URL is a known consent wall or redirect dead-end."""
  return not any(pattern in url for pattern in _DEAD_URL_PATTERNS)

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
      and _is_live_url(a.get("url", ""))
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

  # X feed — wide range of topics beyond tracked assets
  try:
    from octo_x_feed import get_x_feed_articles
    x_articles = get_x_feed_articles(max_per_account=2)
    for a in x_articles:
      if a["title"] not in posted_titles:
        all_articles.append(a)
    print(f"[Engage] X feed: {len(x_articles)} posts added.")
  except Exception as _xe:
    print(f"[Engage] X feed failed: {_xe}")

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

TYPE A — Sharp commentary with numbers (most common):
Pull an unknown or surprising fact from the article — something most people don't know.
Include the actual dollar amounts, percentages, or capital flow numbers from the article.
End with a directional lean if the data supports it.
Under 240 chars so the article link fits.
NEVER write CALLING IT: or Oracle call: — those phrases are reserved for the official oracle system.

TYPE B — Person/figure insight:
If the article features a named person (Cramer, Musk, Buffett, a CEO, a politician),
surface one fact about them that recontextualises the story: their track record,
their disclosed positions, how much capital they control, what they said six months ago.
Specific beats vague. "$2.4B in NVDA options expired worthless last quarter" beats "analysts were wrong."

TYPE C — Pure Carlin (when the headline is absurd on its face):
No call, no analysis. Just the observation. Land it and leave.

RULES:
- Under 235 chars (link + cashtag appended automatically)
- No hashtags
- No emoji unless it's 🔮 and it actually earns it
- Never "Great point" or any opener filler
- Use real numbers from the article or live data — specific figures make it quotable
- If the article mentions capital flows, fund flows, short interest, options activity, or insider trades — use them
- CRITICAL: Only use prices from the live data injected into this prompt. Do NOT invent prices.
- END every tweet with the relevant cashtag: $BTC $ETH $SOL $NVDA $TSLA $AAPL $OIL $GOLD
  Only use the cashtag for the asset the story is actually about.
  Crypto gets $BTC/$ETH/$SOL. Stocks get $TSLA/$AAPL/$NVDA. Oil gets $OIL. Gold gets $GOLD.
  Macro/Fed/AI stories with no single asset: no cashtag.
- If the headline has no angle worth taking, reply: SKIP
- Never make a directional call on an asset that already has an open call listed below

OPEN CALLS (do not duplicate these):
{open_calls}

Reply only with the tweet text. No quotes, no labels."""


def fetch_live_prices() -> str:
  """Fetch live prices via Kraken (primary) with CoinGecko fallback and 5-min cache."""
  try:
    from financial_data_client import get_crypto_prices
    p = get_crypto_prices(["BTC", "ETH", "SOL"])
    lines = []
    for t in ["BTC", "ETH", "SOL"]:
      d = p.get(t, {})
      if d.get("usd", 0):
        fmt = f"{d['usd']:,.0f}" if t == "BTC" else f"{d['usd']:,.2f}"
        lines.append(f"{t}: ${fmt} ({d.get('usd_24h_change', 0):+.1f}% 24h)")
    return "\n".join(lines)
  except Exception as e:
    print(f"[Engage] Price fetch failed: {e}")
    return ""



def fetch_og_image(url: str) -> str | None:
  """Fetch the Open Graph image URL from an article's HTML meta tags."""
  try:
    r = httpx.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"},
                  follow_redirects=True)
    if r.status_code != 200:
      return None
    # Parse og:image meta tag
    import re
    m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', r.text)
    if not m:
      m = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', r.text)
    return m.group(1) if m else None
  except Exception:
    return None


def brand_image(image_url: str) -> str | None:
  """
  Download article OG image, add Octodamus brand strip at bottom,
  upload to X and return media_id. Returns None on any failure.
  """
  try:
    import tempfile, requests
    from PIL import Image, ImageDraw, ImageFont
    from io import BytesIO

    r = requests.get(image_url, timeout=12, headers={"User-Agent": "Octodamus/1.0"})
    if r.status_code != 200:
      return None

    img = Image.open(BytesIO(r.content)).convert("RGB")

    # Crop to 16:9 if taller
    w, h = img.size
    target_h = int(w * 9 / 16)
    if h > target_h:
      top = (h - target_h) // 4  # slightly above center crop
      img = img.crop((0, top, w, top + target_h))
      h = target_h

    # Thin brand strip at bottom — small, caption-sized, not a banner
    font_size = 11
    strip_h   = font_size + 10  # just enough padding around the text
    new_img = Image.new("RGB", (w, h + strip_h), (0, 8, 16))
    new_img.paste(img, (0, 0))

    draw = ImageDraw.Draw(new_img)
    try:
      font = ImageFont.truetype("C:/Windows/Fonts/Arial.ttf", size=font_size)
    except Exception:
      font = ImageFont.load_default()

    label = "OCTODAMUS  ·  api.octodamus.com  ·  @octodamusai"
    draw.text((12, h + 5), label, fill=(0, 180, 230), font=font)

    # Save and upload
    suffix = ".jpg"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
      new_img.save(tmp.name, "JPEG", quality=88)
      tmp_path = tmp.name

    from octo_x_poster import upload_image_from_url
    # Upload from local file directly
    import tweepy, os
    auth = tweepy.OAuth1UserHandler(
      consumer_key=os.environ.get("TWITTER_API_KEY", ""),
      consumer_secret=os.environ.get("TWITTER_API_SECRET", ""),
      access_token=os.environ.get("TWITTER_ACCESS_TOKEN", ""),
      access_token_secret=os.environ.get("TWITTER_ACCESS_TOKEN_SECRET", ""),
    )
    api = tweepy.API(auth)
    media = api.media_upload(filename=tmp_path)
    Path(tmp_path).unlink(missing_ok=True)
    return str(media.media_id)

  except Exception as e:
    print(f"[Engage] Image brand failed: {e}")
    return None


def scrape_article_body(url: str) -> str:
  """
  Scrape article body text via Firecrawl for deeper context.
  Falls back to empty string on failure (no Firecrawl credits wasted on timeouts).
  """
  try:
    from octo_firecrawl import scrape_url
    result = scrape_url(url)
    if result and result.get("markdown"):
      # Return first 600 chars of body — enough for facts/numbers
      return result["markdown"][:600].strip()
  except Exception:
    pass
  return ""


def generate_take(article: dict, open_calls_text: str, live_prices: str = "") -> str | None:
  system = _SYSTEM.replace("{open_calls}", open_calls_text)

  is_x_post = article.get("source") == "x_feed"
  if is_x_post:
      content = f"X post by @{article.get('handle','?')}: {article['title']}"
  else:
      content = f"Asset: {article['ticker']}\nHeadline: {article['title']}"
      if article["description"]:
          content += f"\nSummary: {article['description'][:300]}"
      body = scrape_article_body(article.get("url", ""))
      if body:
          content += f"\n\nARTICLE BODY (use specific facts, numbers, capital flows from here):\n{body}"

  if live_prices:
    content += f"\n\nLIVE PRICES (use these exact numbers only, do not invent):\n{live_prices}"

  # Route through OpenRouter free model for cost efficiency
  if _CLAW_ENGAGE and _claw_engage:
    try:
      r = _claw_engage.chat.completions.create(
        model="meta-llama/llama-4-maverick:free",
        max_tokens=200,
        messages=[
          {"role": "system", "content": system},
          {"role": "user",   "content": content},
        ],
        timeout=30,
      )
      result = r.choices[0].message.content.strip()
      return None if result == "SKIP" else result
    except Exception:
      pass

  # Fallback to Anthropic
  client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
  msg = client.messages.create(
    model="claude-haiku-4-5-20251001",
    max_tokens=200,
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
      url = article.get("url", "")
      cashtag = _CASHTAG.get(article.get("ticker", ""), "")
      # Ensure cashtag is present — append if Claude didn't include it
      trimmed = take.rstrip()
      if cashtag and cashtag not in trimmed:
        trimmed = f"{trimmed} {cashtag}"
      trimmed = trimmed[:256]
      # Also catch company names without cashtags via the global enforcer
      try:
        from octo_x_poster import ensure_cashtag as _ensure_ct
        trimmed = _ensure_ct(trimmed)[:256]
      except Exception:
        pass
      tweet_text = f"{trimmed}\n{url}" if url else trimmed

      # Fetch + brand the article image
      media_id = None
      try:
        og_url = fetch_og_image(url)
        if og_url:
          media_id = brand_image(og_url)
          if media_id:
            print(f"[Engage] Image branded and uploaded (media_id={media_id[:8]}...)")
      except Exception as img_e:
        print(f"[Engage] Image processing skipped: {img_e}")

      # Post with or without image
      from octo_x_poster import _post_single
      result = _post_single(tweet_text, media_ids=[media_id] if media_id else None)
      tweet_url = result.get("url", "")
      posted += 1
      posted_titles.append(article["title"])
      print(f"[Engage] OK [{article['ticker']}] {trimmed[:80]}...")
      if tweet_url:
        print(f"[Engage]   URL: {tweet_url}")

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
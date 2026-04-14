"""
octo_strategy_tracker.py
Octodamus — STRATEGY + Bitcoin weekly post module

Mon–Sat: silent monitor — snapshot data + news → strategy_weekly_intel.json
Sunday:  load full week, generate tweet, identify what to show, take clean shot

Screenshot philosophy:
  - Tweet is written FIRST
  - Claude decides what part of the page best illustrates the tweet
  - Playwright finds that exact element by JS inspection
  - Screenshot is cropped precisely to that element + padding
  - No nav bars. No half-cut tables. Subject centered and balanced.

Modes:
    python octo_strategy_tracker.py --monitor   # Mon–Sat silent snapshot
    python octo_strategy_tracker.py --mockup    # Sunday post preview
    python octo_strategy_tracker.py --post      # Sunday → live X post
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

STRATEGY_URL = "https://strategytracker.com/"
INTEL_FILE   = Path(__file__).parent / "strategy_weekly_intel.json"
VIEWPORT_W   = 1440
VIEWPORT_H   = 900
INITIAL_WAIT = 7000
MAX_SNAPSHOTS = 7

# ─────────────────────────────────────────────
# VISUAL SUBJECTS
# Each subject maps to a JS function that finds the element's bounding box.
# Claude picks which subject best matches the tweet.
# ─────────────────────────────────────────────

SUBJECTS = {

    "btc_holdings_number": {
        "description": "The Total BTC Holdings stat card (single card only, not the full row)",
        "js": """() => {
            const all = [...document.querySelectorAll('div, article, section')];
            // Find all elements with this text but NOT also containing BTC Price (that would be the parent row)
            const matches = all.filter(el => {
                const t = el.innerText || '';
                return t.includes('Total BTC Holdings') &&
                       !t.includes('BTC Price') &&
                       el.offsetHeight > 50 && el.offsetHeight < 400 &&
                       el.offsetWidth > 100 && el.offsetWidth < 700;
            });
            if (!matches.length) return null;
            // Pick smallest by area
            matches.sort((a, b) => (a.offsetWidth * a.offsetHeight) - (b.offsetWidth * b.offsetHeight));
            const r = matches[0].getBoundingClientRect();
            return {x: r.left, y: r.top, w: r.width, h: r.height};
        }""",
        "padding": 20,
    },

    "btc_price_card": {
        "description": "The live BTC Price stat card with real-time price chart (single card only)",
        "js": """() => {
            const all = [...document.querySelectorAll('div, article, section')];
            const matches = all.filter(el => {
                const t = el.innerText || '';
                return t.includes('BTC Price') &&
                       !t.includes('Total BTC Holdings') &&
                       el.offsetHeight > 50 && el.offsetHeight < 400 &&
                       el.offsetWidth > 100 && el.offsetWidth < 700;
            });
            if (!matches.length) return null;
            matches.sort((a, b) => (a.offsetWidth * a.offsetHeight) - (b.offsetWidth * b.offsetHeight));
            const r = matches[0].getBoundingClientRect();
            return {x: r.left, y: r.top, w: r.width, h: r.height};
        }""",
        "padding": 20,
    },

    "three_stat_cards": {
        "description": "All three top stat cards in one row: Total BTC Holdings, BTC Price, Combined Market Cap",
        "js": """() => {
            const all = [...document.querySelectorAll('div, section')];
            const matches = all.filter(el => {
                const t = el.innerText || '';
                return t.includes('Total BTC Holdings') &&
                       t.includes('BTC Price') &&
                       t.includes('Combined Market Cap') &&
                       el.offsetHeight > 100 && el.offsetHeight < 500 &&
                       el.offsetWidth > 400 && el.offsetWidth < 1440;
            });
            if (!matches.length) return null;
            matches.sort((a, b) => (a.offsetWidth * a.offsetHeight) - (b.offsetWidth * b.offsetHeight));
            const r = matches[0].getBoundingClientRect();
            return {x: r.left, y: r.top, w: r.width, h: r.height};
        }""",
        "padding": 16,
    },

    "performance_chart": {
        "description": "The Performance Comparison chart: Strategy vs S&P 500, NASDAQ, BTC returns over time",
        "js": """() => {
            const all = [...document.querySelectorAll('div, section')];
            const matches = all.filter(el => {
                const t = el.innerText || '';
                return (t.includes('Performance Comparison') ||
                        (t.includes('Performance') && (t.includes('S&P') || t.includes('NASDAQ') || t.includes('Bitcoin')))) &&
                       el.offsetHeight > 200 && el.offsetHeight < 800 &&
                       el.offsetWidth > 400 && el.offsetWidth < 1440;
            });
            if (!matches.length) return null;
            matches.sort((a, b) => (a.offsetWidth * a.offsetHeight) - (b.offsetWidth * b.offsetHeight));
            const el = matches[0];
            el.scrollIntoView({behavior: 'instant', block: 'center'});
            const r = el.getBoundingClientRect();
            return {x: r.left, y: r.top, w: r.width, h: r.height};
        }""",
        "padding": 16,
        "extra_wait": 1200,
        "self_scroll": True,
    },

    "ground_stations_table": {
        "description": "The Ground Stations comparison table: all companies, BTC held, mNAV, sats/share, returns",
        "js": """() => {
            const all = [...document.querySelectorAll('div, section, table')];
            const matches = all.filter(el => {
                const t = el.innerText || '';
                return (t.includes('Strategy') || t.includes('MSTR')) &&
                       t.includes('mNAV') &&
                       el.offsetHeight > 150 && el.offsetHeight < 1200 &&
                       el.offsetWidth > 400 && el.offsetWidth < 1440;
            });
            if (!matches.length) return null;
            matches.sort((a, b) => (a.offsetWidth * a.offsetHeight) - (b.offsetWidth * b.offsetHeight));
            const r = matches[0].getBoundingClientRect();
            return {x: r.left, y: r.top, w: r.width, h: r.height};
        }""",
        "padding": 12,
    },

    "strategy_row": {
        "description": "The Strategy (MSTR) row in the Ground Stations table — BTC held, mNAV, sats/share, return",
        "js": """() => {
            // Try table rows first
            const rows = [...document.querySelectorAll('tr')];
            const tableRow = rows.find(el => {
                const t = el.innerText || '';
                return (t.includes('Strategy') || t.includes('MSTR')) &&
                       el.offsetHeight > 20 && el.offsetHeight < 120 &&
                       el.offsetWidth > 200;
            });
            if (tableRow) {
                const r = tableRow.getBoundingClientRect();
                return {x: r.left, y: r.top, w: r.width, h: r.height};
            }
            // Fallback: div rows
            const divRows = [...document.querySelectorAll('[class*="row"], [class*="item"], [class*="entry"]')];
            const divRow = divRows.find(el => {
                const t = el.innerText || '';
                return (t.includes('Strategy') || t.includes('MSTR')) &&
                       t.includes('mNAV') &&
                       el.offsetHeight > 20 && el.offsetHeight < 120;
            });
            if (!divRow) return null;
            const r = divRow.getBoundingClientRect();
            return {x: r.left, y: r.top, w: r.width, h: r.height};
        }""",
        "padding": 24,
    },

    "rank_cards": {
        "description": "The Bitcoin and Strategy Dominance rank cards: asset rank, market cap rank, volume rank",
        "js": """() => {
            const all = [...document.querySelectorAll('div, section')];
            const matches = all.filter(el => {
                const t = el.innerText || '';
                return t.includes('Asset Rank') &&
                       (t.includes('Market Cap') || t.includes('Volume') || t.includes('Dominance')) &&
                       el.offsetHeight > 60 && el.offsetHeight < 500 &&
                       el.offsetWidth > 300 && el.offsetWidth < 1440;
            });
            if (!matches.length) return null;
            matches.sort((a, b) => (a.offsetWidth * a.offsetHeight) - (b.offsetWidth * b.offsetHeight));
            const el = matches[0];
            el.scrollIntoView({behavior: 'instant', block: 'center'});
            const r = el.getBoundingClientRect();
            return {x: r.left, y: r.top, w: r.width, h: r.height};
        }""",
        "padding": 20,
        "extra_wait": 800,
        "self_scroll": True,
    },

    "ecosystem_overview": {
        "description": "Ecosystem Overview stats: number of ground stations, total holdings USD value, % of BTC supply",
        "js": """() => {
            const all = [...document.querySelectorAll('div, section')];
            const matches = all.filter(el => {
                const t = el.innerText || '';
                return t.includes('Ecosystem Overview') &&
                       t.includes('Ground Stations') &&
                       el.offsetHeight > 120 && el.offsetHeight < 700 &&
                       el.offsetWidth > 400 && el.offsetWidth < 1440;
            });
            const pool = matches.length ? matches : all.filter(el => {
                const t = el.innerText || '';
                return t.includes('Ground Stations') && t.includes('BTC Supply') &&
                       t.includes('Total Holdings') &&
                       el.offsetHeight > 100 && el.offsetHeight < 500 &&
                       el.offsetWidth > 400 && el.offsetWidth < 1440;
            });
            if (!pool.length) return null;
            pool.sort((a, b) => (a.offsetWidth * a.offsetHeight) - (b.offsetWidth * b.offsetHeight));
            const el = pool[0];
            el.scrollIntoView({behavior: 'instant', block: 'center'});
            const r = el.getBoundingClientRect();
            return {x: r.left, y: r.top, w: r.width, h: r.height};
        }""",
        "padding": 24,
        "extra_wait": 1200,
        "self_scroll": True,
    },

    "accumulation_charts": {
        "description": "The three top stat cards showing the BTC accumulation staircase, price trend, and market cap charts",
        "js": """() => {
            // Same target as three_stat_cards — the row of cards with their embedded sparklines
            const all = [...document.querySelectorAll('div, section')];
            const matches = all.filter(el => {
                const t = el.innerText || '';
                return t.includes('Total BTC Holdings') &&
                       t.includes('BTC Price') &&
                       t.includes('Combined Market Cap') &&
                       el.offsetHeight > 100 && el.offsetHeight < 500 &&
                       el.offsetWidth > 400 && el.offsetWidth < 1440;
            });
            if (!matches.length) return null;
            matches.sort((a, b) => (a.offsetWidth * a.offsetHeight) - (b.offsetWidth * b.offsetHeight));
            const r = matches[0].getBoundingClientRect();
            return {x: r.left, y: r.top, w: r.width, h: r.height};
        }""",
        "padding": 16,
    },
}


# ─────────────────────────────────────────────
# VISION PROMPT
# ─────────────────────────────────────────────

VISION_EXTRACT_PROMPT = """
You are reading a screenshot of strategytracker.com which tracks Strategy (MSTR) Bitcoin holdings.
Extract every number visible. Return ONLY valid JSON — no markdown.

{
  "btc_strategy_direct": "766,970 BTC",
  "btc_total_ecosystem": "839,883 BTC",
  "btc_per_share_strategy": "202,140 sats",
  "nav_premium_strategy": "0.90x mNAV",
  "btc_price": "$70,966",
  "btc_yield_ytd_strategy": "+3.7%",
  "holdings_usd_strategy": "$44.6B",
  "combined_market_cap": "$49.08B",
  "strategy_bse_return": "+940.8%",
  "bitcoin_asset_rank": "#11",
  "market_cap_rank": "#234",
  "btc_supply_pct": "3.999%",
  "ground_stations": "17",
  "metaplanet_btc": "40,177 BTC",
  "metaplanet_mnav": "1.14x",
  "metaplanet_bse_return": "+1573.7%",
  "notes": "anything else notable"
}
Omit fields not visible. JSON only.
""".strip()

STRATEGY_TWEET_SYSTEM = """You are Octodamus — oracle octopus, market seer of the Pacific depths.
@octodamusai on X. Max 280 chars. No hashtags. No engagement bait. Never sycophantic.
Lead with a specific real number. State data as fact. Only use numbers from the prompt.
Voice: PRECISE, SARDONIC, or ORACLE. One clean idea. Make it quotable.
The kind of post Michael Saylor or a Bitcoin maximalist reposts. Under 280 chars."""


# ─────────────────────────────────────────────
# NEWS
# ─────────────────────────────────────────────

def _fetch_strategy_news() -> list[str]:
    api_key = os.environ.get("TAVILY_API_KEY", "")
    if not api_key:
        return []
    try:
        import requests
        resp = requests.post(
            "https://api.tavily.com/search",
            json={"api_key": api_key, "query": "MicroStrategy MSTR Strategy bitcoin treasury Saylor",
                  "search_depth": "basic", "max_results": 6, "days": 2},
            timeout=15,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        return [f"{r.get('title','').strip()} — {r.get('content','')[:100].strip()}"
                for r in results if r.get("title")]
    except Exception as e:
        print(f"[Strategy] News fetch: {e}")
        return []


# ─────────────────────────────────────────────
# PLAYWRIGHT CORE
# ─────────────────────────────────────────────

async def _open_page():
    from playwright.async_api import async_playwright
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
    )
    ctx = await browser.new_context(
        viewport={"width": VIEWPORT_W, "height": VIEWPORT_H},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
        extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
    )
    page = await ctx.new_page()
    try:
        await page.goto(STRATEGY_URL, wait_until="networkidle", timeout=35_000)
    except Exception:
        try:
            await page.goto(STRATEGY_URL, wait_until="domcontentloaded", timeout=25_000)
        except Exception as e:
            print(f"[Strategy] Load warning: {e}")

    await page.keyboard.press("Escape")
    await page.wait_for_timeout(300)
    for label in ["Accept all", "Accept", "Got it", "Close"]:
        try:
            btn = page.get_by_role("button", name=label, exact=False)
            if await btn.count() > 0:
                await btn.first.click(timeout=1_000)
                await page.wait_for_timeout(200)
                break
        except Exception:
            pass
    await page.wait_for_timeout(INITIAL_WAIT)
    return pw, browser, page


async def _shoot_subject(page, subject_key: str) -> bytes | None:
    """
    Find a specific subject on the page by JS inspection and screenshot it cleanly.
    Returns None if the element can't be found.
    """
    subject    = SUBJECTS[subject_key]
    js         = subject["js"]
    padding    = subject.get("padding", 20)
    extra_wait = subject.get("extra_wait", 0)

    self_scroll = subject.get("self_scroll", False)

    # Find element bounding box (JS may also call scrollIntoView as a side-effect)
    box = await page.evaluate(js)
    if not box:
        print(f"[Strategy] Could not find element for subject '{subject_key}'")
        return None

    x, y, w, h = box["x"], box["y"], box["w"], box["h"]

    if self_scroll:
        # JS already scrolled the element into view — just wait for lazy renders to settle
        wait_ms = max(800, extra_wait)
        await page.wait_for_timeout(wait_ms)
        # Re-fetch bbox at its final resting position
        box = await page.evaluate(js)
        if not box:
            return None
        x, y, w, h = box["x"], box["y"], box["w"], box["h"]
    elif y < 0 or y + h > VIEWPORT_H:
        # Element is off-screen — scroll via window (works when window is the scroll container)
        scroll_to = max(0, int(y + h / 2 - VIEWPORT_H / 2))
        await page.evaluate(f"window.scrollTo(0, {scroll_to})")
        wait_ms = max(800, extra_wait)
        await page.wait_for_timeout(wait_ms)
        box = await page.evaluate(js)
        if not box:
            return None
        x, y, w, h = box["x"], box["y"], box["w"], box["h"]
    elif extra_wait:
        await page.wait_for_timeout(extra_wait)

    # Add padding and clamp to viewport
    cx = max(0, x - padding)
    cy = max(0, y - padding)
    cw = min(VIEWPORT_W - cx, w + padding * 2)
    ch = min(VIEWPORT_H - cy, h + padding * 2)

    if cw < 50 or ch < 30:
        print(f"[Strategy] Element too small for '{subject_key}': {cw}x{ch}")
        return None

    # Reject full-viewport captures — those mean the JS found a page wrapper, not a component
    if cw >= VIEWPORT_W - 10 and ch >= VIEWPORT_H - 10:
        print(f"[Strategy] Element is full-viewport for '{subject_key}' — selector too broad, rejecting")
        return None

    img = await page.screenshot(
        type="png",
        clip={"x": cx, "y": cy, "width": cw, "height": ch},
    )
    print(f"[Strategy] Shot '{subject_key}': {cw:.0f}x{ch:.0f}px => {len(img)//1024}KB")
    return img


async def _shoot_hero(page) -> bytes:
    """Full viewport hero shot for data extraction."""
    await page.evaluate("window.scrollTo(0, 0)")
    await page.wait_for_timeout(500)
    return await page.screenshot(type="png")


# ─────────────────────────────────────────────
# VISION EXTRACTION
# ─────────────────────────────────────────────

def _vision_extract(img_bytes: bytes, api_key: str) -> dict:
    import anthropic, base64
    img_b64 = base64.standard_b64encode(img_bytes).decode()
    client  = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1200,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_b64}},
            {"type": "text",  "text": VISION_EXTRACT_PROMPT},
        ]}],
    )
    raw = resp.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"): raw = raw[4:]
        raw = raw.strip()
    try:
        return json.loads(raw)
    except Exception:
        import re
        result = {}
        for m in re.finditer(r'"([\w_]+)"\s*:\s*"([^"]*)"', raw):
            result[m.group(1)] = m.group(2)
        return result if result else {}


# ─────────────────────────────────────────────
# TWEET + SUBJECT SELECTION
# ─────────────────────────────────────────────

def _pick_subject_for_tweet(tweet: str, api_key: str, avoid_subjects: list[str] = None) -> str:
    """
    Ask Claude which visual subject on strategytracker.com best illustrates this tweet.
    Returns a key from SUBJECTS. Pass avoid_subjects to prevent repeating recent visuals.
    """
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    avoid = set(avoid_subjects or [])
    subject_list = "\n".join(
        f"  {key}: {info['description']}" + (" [AVOID — used recently]" if key in avoid else "")
        for key, info in SUBJECTS.items()
    )

    avoid_note = ""
    if avoid:
        avoid_note = (f"\nDo NOT pick: {', '.join(avoid)}. "
                      f"These were used in recent posts — choose a fresh visual.\n")

    prompt = (
        f"This tweet was written for @octodamusai about Strategy/MSTR and Bitcoin:\n\n"
        f'"{tweet}"\n\n'
        f"Choose ONE visual from this list that best illustrates what the tweet is saying.\n"
        f"Pick the most specific match — the image should make the tweet's point visually obvious.\n"
        f"{avoid_note}\n"
        f"{subject_list}\n\n"
        f"Reply with ONLY the key name, nothing else. Example: performance_chart"
    )

    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=20,
        messages=[{"role": "user", "content": prompt}],
    )
    key = resp.content[0].text.strip().lower().replace('"', '').replace("'", "")
    if key not in SUBJECTS:
        # Fallback: try to match partial
        for k in SUBJECTS:
            if k in key or key in k:
                return k
        # Last resort: pick any subject not in avoid
        for k in SUBJECTS:
            if k not in avoid:
                return k
        return "three_stat_cards"
    return key


def _generate_tweet(data: dict, intel: dict, api_key: str) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    fields = [
        ("btc_strategy_direct",    "Strategy BTC held"),
        ("btc_total_ecosystem",    "Ecosystem total BTC"),
        ("btc_per_share_strategy", "BTC per share"),
        ("nav_premium_strategy",   "mNAV"),
        ("btc_price",              "BTC price"),
        ("btc_yield_ytd_strategy", "BTC yield YTD"),
        ("holdings_usd_strategy",  "Holdings USD"),
        ("btc_supply_pct",         "% of BTC supply"),
        ("ground_stations",        "Ground stations"),
        ("bitcoin_asset_rank",     "Bitcoin asset rank"),
        ("strategy_bse_return",    "Strategy total return"),
        ("metaplanet_bse_return",  "Metaplanet return"),
        ("metaplanet_mnav",        "Metaplanet mNAV"),
    ]
    data_lines = [f"  {label}: {data[key]}" for key, label in fields if data.get(key)]
    data_block = "\n".join(data_lines)

    snapshots = intel.get("snapshots", [])
    week_block = ""
    if snapshots:
        snap_lines = []
        for s in snapshots:
            d = s.get("data", {})
            snap_lines.append(
                f"  {s['day']}: BTC {d.get('btc_price','?')} | "
                f"mNAV {d.get('nav_premium_strategy','?')} | "
                f"held {d.get('btc_strategy_direct','?')}"
            )
        week_block = "\nWeekly observations:\n" + "\n".join(snap_lines)

    news = intel.get("news_log", [])
    news_block = ("\nNews this week:\n" + "\n".join(f"  - {h}" for h in news[-8:])) if news else ""

    # Tell the generator to avoid repeating recent angles
    recent_tweets = intel.get("recent_tweets", [])
    avoid_block = ""
    if recent_tweets:
        avoid_block = ("\nDo NOT repeat these recent angles:\n" +
                       "\n".join(f"  - {t[:100]}" for t in recent_tweets[-3:]))

    prompt = (
        f"Live data from strategytracker.com:\n{data_block}"
        f"{week_block}{news_block}{avoid_block}\n\n"
        "Write ONE tweet under 280 chars. Lead with a specific number. "
        "Find the most interesting angle — avoid the obvious BTC price entry angle if it was used recently. "
        "Consider: mNAV ratio, ground stations count, sats per share, ecosystem growth, "
        "Metaplanet comparison, long-term return, supply %. No hashtags. Under 280 chars."
    )

    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=200,
        system=STRATEGY_TWEET_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    tweet = resp.content[0].text.strip()
    # Append $MSTR cashtag for discoverability (no URL = no reach suppression)
    footer = "\n\n$MSTR"
    if "$MSTR" not in tweet and len(tweet) + len(footer) <= 280:
        tweet += footer
    return tweet


# ─────────────────────────────────────────────
# WEEKLY INTEL
# ─────────────────────────────────────────────

def _load_intel() -> dict:
    if INTEL_FILE.exists():
        try:
            return json.loads(INTEL_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"snapshots": [], "news_log": []}


def _save_intel(intel: dict) -> None:
    INTEL_FILE.write_text(json.dumps(intel, indent=2, ensure_ascii=False), encoding="utf-8")


def _add_snapshot(intel: dict, data: dict, news: list[str]) -> None:
    today = datetime.now().strftime("%Y-%m-%d")
    day   = datetime.now().strftime("%A")
    intel["snapshots"] = [s for s in intel["snapshots"] if s.get("date") != today]
    intel["snapshots"].append({"date": today, "day": day, "data": data, "news": news})
    intel["snapshots"] = sorted(intel["snapshots"], key=lambda s: s["date"])[-MAX_SNAPSHOTS:]
    existing = set(intel.get("news_log", []))
    for h in news:
        if h not in existing:
            intel.setdefault("news_log", []).append(h)
    intel["news_log"] = intel.get("news_log", [])[-40:]


# ─────────────────────────────────────────────
# MODE: MONITOR (Mon–Sat)
# ─────────────────────────────────────────────

def mode_strategy_monitor(api_key: str = "") -> None:
    if not api_key:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    print(f"[Strategy Monitor] {datetime.now().strftime('%A %Y-%m-%d')}")
    try:
        async def run():
            pw, browser, page = await _open_page()
            try:
                img = await _shoot_hero(page)
            finally:
                await browser.close()
                await pw.stop()
            return img

        hero_img = asyncio.run(run())
        data     = _vision_extract(hero_img, api_key)
        news     = _fetch_strategy_news()
        intel    = _load_intel()
        _add_snapshot(intel, data, news)
        _save_intel(intel)
        print(f"[Strategy Monitor] BTC {data.get('btc_strategy_direct','?')} | "
              f"mNAV {data.get('nav_premium_strategy','?')} | {len(news)} news items")
    except Exception as e:
        print(f"[Strategy Monitor] Failed: {e}")
        import traceback; traceback.print_exc()


# ─────────────────────────────────────────────
# MODE: SUNDAY POST
# ─────────────────────────────────────────────

def mode_strategy_sunday(api_key: str = "", mockup: bool = False,
                          save_path: Path = None) -> dict | None:
    if not api_key:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    print(f"[Strategy Sunday] {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    try:
        intel = _load_intel()
        print(f"[Strategy Sunday] {len(intel.get('snapshots',[]))} snapshots loaded")

        async def run():
            pw, browser, page = await _open_page()
            try:
                # Always extract data from hero shot
                hero_img = await _shoot_hero(page)
                return hero_img, page, browser, pw
            except Exception:
                await browser.close()
                await pw.stop()
                raise

        # We need to keep the page open across the subject shot
        async def full_run():
            pw, browser, page = await _open_page()
            try:
                hero_img = await _shoot_hero(page)
                data     = _vision_extract(hero_img, api_key)
                news     = _fetch_strategy_news()
                _add_snapshot(intel, data, news)

                # Generate tweet (avoids recent angles stored in intel)
                print("[Strategy Sunday] Generating tweet...")
                tweet = _generate_tweet(data, intel, api_key)
                print(f"[Strategy Sunday] Tweet: {tweet[:80]}...")

                # Decide what to show — avoid recently used subjects
                recent_subjects = intel.get("recent_subjects", [])
                subject_key = _pick_subject_for_tweet(tweet, api_key,
                                                       avoid_subjects=recent_subjects[-3:])
                print(f"[Strategy Sunday] Visual subject: {subject_key}")

                # Take the targeted shot
                tweet_img = await _shoot_subject(page, subject_key)

                # Fallback: if element not found, use hero
                if tweet_img is None:
                    print(f"[Strategy Sunday] Falling back to hero shot")
                    tweet_img = hero_img

                return tweet, tweet_img, data, subject_key

            finally:
                await browser.close()
                await pw.stop()

        tweet, tweet_img, data, subject_key = asyncio.run(full_run())

        # Save image
        ts = datetime.now().strftime("%Y%m%d_%H%M")
        if save_path is None:
            save_path = Path(__file__).parent
        img_file = save_path / f"strategy_{subject_key}_{ts}.png"
        img_file.write_bytes(tweet_img)

        # Record what was used so next run avoids repeating it
        intel.setdefault("recent_subjects", []).append(subject_key)
        intel["recent_subjects"] = intel["recent_subjects"][-6:]
        intel.setdefault("recent_tweets", []).append(tweet)
        intel["recent_tweets"] = intel["recent_tweets"][-4:]
        _save_intel(intel)

        if mockup:
            print("\n" + "=" * 60)
            print("  STRATEGY WEEKLY POST")
            print("=" * 60)
            safe_tweet = tweet.encode("ascii", "replace").decode("ascii")
            print(f"\n{safe_tweet}\n")
            print(f"  ({len(tweet)} chars)")
            safe_desc = SUBJECTS[subject_key]['description'].encode("ascii", "replace").decode("ascii")
            print(f"  Visual: {subject_key} - {safe_desc}")
            print(f"  Image:  {img_file}")
            print("=" * 60)
            return {"tweet": tweet, "img_bytes": tweet_img, "subject": subject_key,
                    "img_path": str(img_file)}

        # Post to X
        from octo_x_poster import _upload_media_v1, _post_single, _log_post
        media_id    = _upload_media_v1(tweet_img)
        post_result = _post_single(tweet, media_ids=[media_id])
        _log_post(tweet, {"type": "strategy_weekly", "subject": subject_key})
        # Reset snapshots for new week but keep recent_subjects/recent_tweets for variety tracking
        intel["snapshots"] = []
        intel["news_log"]  = []
        _save_intel(intel)
        print(f"[Strategy Sunday] Posted: {post_result.get('url','(no url)')}")
        print("[Strategy Sunday] Intel reset for next week.")
        return post_result

    except Exception as e:
        print(f"[Strategy Sunday] Failed: {e}")
        import traceback; traceback.print_exc()
        return None


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--monitor", action="store_true")
    parser.add_argument("--mockup",  action="store_true")
    parser.add_argument("--post",    action="store_true")
    args = parser.parse_args()

    sys.path.insert(0, str(Path(__file__).parent))
    try:
        from bitwarden import load_all_secrets
        load_all_secrets()
    except Exception:
        _cache = Path(__file__).parent / ".octo_secrets"
        for k, v in json.loads(_cache.read_text(encoding="utf-8")).get("secrets", {}).items():
            os.environ[k] = v

    _api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not _api_key:
        print("ERROR: ANTHROPIC_API_KEY not found"); sys.exit(1)

    if args.monitor:
        mode_strategy_monitor(api_key=_api_key)
    elif args.post:
        mode_strategy_sunday(api_key=_api_key, mockup=False)
    else:
        mode_strategy_sunday(api_key=_api_key, mockup=True)

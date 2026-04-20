"""
octo_firecrawl.py -- Web intelligence module for OctodamusCEO
Firecrawl-powered scraping, search, and competitive research.

Use cases:
  - Scrape crypto research reports, on-chain analysis posts
  - Web search for market intel, competitor positioning
  - Competitor site analysis (Kiyotaka, Glassnode, Messari, etc.)
  - Research for Octodamus posts + oracle calls
  - Feed clean text into CEO sandbox prompts

Bitwarden key: "AGENT - Octodamus - Firecrawl API"
Get key: firecrawl.dev (Hobby $19/mo = 3,000 credits/mo)

Usage:
    from octo_firecrawl import scrape_url, scrape_for_prompt, search_web, competitor_intel
    text = scrape_url("https://example.com/research")
    results = search_web("crypto market intelligence signals 2026")
    intel = competitor_intel()
    python octo_firecrawl.py [scrape <url>|search <query>|competitors|customers <niche>|research <topic>]
"""

import os
import json
import logging
import sys
import time
import hashlib
from datetime import datetime, timedelta
from pathlib import Path

# Firecrawl SDK logs with Unicode chars (->u2192) that crash Windows cp1252 stdout
logging.getLogger("firecrawl").setLevel(logging.CRITICAL)
logging.getLogger("httpx").setLevel(logging.CRITICAL)
logging.getLogger("httpcore").setLevel(logging.CRITICAL)
# Also ensure stdout can handle utf-8 if reconfigurable
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ── Config ────────────────────────────────────────────────────────────────────

MAX_CONTENT_CHARS = 4000   # cap per scrape to avoid token bloat in prompts
CACHE_DIR         = Path(__file__).parent / "data" / "firecrawl_cache"

COMPETITORS = {
    "kiyotaka": "https://kiyotaka.ai",
    "glassnode": "https://glassnode.com",
    "messari":   "https://messari.io",
    "nansen":    "https://nansen.ai",
    "coinglass": "https://coinglass.com",
}

CUSTOMER_NICHES = [
    "crypto hedge fund market signals",
    "defi protocol treasury management",
    "web3 VC analyst data tools",
    "AI agent crypto market data",
    "quantitative crypto trading signals",
    "bitcoin treasury company intelligence",
]


# ── Client ───────────────────────────────────────────────────────────────────

def _get_api_key() -> str:
    # 1. os.environ (set by load_all_secrets when running from runner)
    key = os.environ.get("FIRECRAWL_API_KEY", "")
    if key:
        return key
    # 2. .octo_secrets cache file (background tasks)
    secrets_path = Path(__file__).parent / ".octo_secrets"
    if secrets_path.exists():
        raw = json.loads(secrets_path.read_text(encoding="utf-8"))
        # Cache format: {"saved_at": ..., "secrets": {...}}
        cache = raw.get("secrets", raw)
        key = cache.get("FIRECRAWL_API_KEY", "")
        if key:
            return key
    return ""


def _get_client():
    try:
        from firecrawl import FirecrawlApp
    except ImportError:
        raise ImportError("Install firecrawl: pip install firecrawl-py")
    api_key = _get_api_key()
    if not api_key:
        raise ValueError("FIRECRAWL_API_KEY not found in .octo_secrets")
    return FirecrawlApp(api_key=api_key)


def _timed_cache_path(cache_key: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    safe = hashlib.md5(cache_key.encode()).hexdigest()
    return CACHE_DIR / f"tc_{safe}.json"


def _timed_cache_load(cache_key: str, max_age_hours: float) -> dict | None:
    p = _timed_cache_path(cache_key)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        ts = datetime.fromisoformat(data.get("_cached_at", "2000-01-01"))
        if datetime.now() - ts > timedelta(hours=max_age_hours):
            return None
        return data
    except Exception:
        return None


def _timed_cache_save(cache_key: str, data: dict):
    data["_cached_at"] = datetime.now().isoformat()
    _timed_cache_path(cache_key).write_text(json.dumps(data, indent=2), encoding="utf-8")


# ── Core scrape ───────────────────────────────────────────────────────────────

def scrape_url(url: str, use_cache: bool = True) -> str:
    """
    Scrape a URL and return clean markdown text.
    Caches results to avoid re-scraping the same URL.
    Returns empty string on failure.
    """
    api_key = _get_api_key()
    if not api_key:
        print("[OctoScrape] No FIRECRAWL_API_KEY -- scrape skipped.")
        return ""

    # Check cache
    if use_cache:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_key  = hashlib.md5(url.encode()).hexdigest()
        cache_file = CACHE_DIR / f"{cache_key}.txt"
        if cache_file.exists():
            print(f"[OctoScrape] Cache hit: {url[:60]}")
            return cache_file.read_text(encoding="utf-8")

    try:
        from firecrawl import FirecrawlApp
        app    = FirecrawlApp(api_key=api_key)
        result = app.scrape(
            url,
            formats=["markdown"],
            only_main_content=True,
        )
        content = ""
        if hasattr(result, "markdown"):
            content = result.markdown or ""
        elif isinstance(result, dict):
            content = result.get("markdown") or result.get("content") or ""
        # v4 may nest under .data
        if not content and hasattr(result, "data"):
            data = result.data
            content = (getattr(data, "markdown", None) or "") if hasattr(data, "markdown") else str(data or "")

        content = content.strip()
        if not content:
            print(f"[OctoScrape] Empty result for {url[:60]}")
            return ""

        print(f"[OctoScrape] Scraped {url[:60]} → {len(content)} chars")

        # Cache it
        if use_cache and content:
            cache_file.write_text(content, encoding="utf-8")

        return content

    except Exception as e:
        print(f"[OctoScrape] Failed for {url[:60]}: {e}")
        return ""


def scrape_for_prompt(url: str, question: str = "") -> str:
    """
    Scrape a URL and format for injection into a Claude prompt.
    Truncates to MAX_CONTENT_CHARS to avoid token overflow.
    Optionally prepends the question for context.
    """
    content = scrape_url(url)
    if not content:
        return ""

    truncated = content[:MAX_CONTENT_CHARS]
    if len(content) > MAX_CONTENT_CHARS:
        truncated += f"\n[... truncated at {MAX_CONTENT_CHARS} chars]"

    header = f"Research from {url}:"
    if question:
        header = f"Research for: '{question}'\nSource: {url}"

    return f"\n{header}\n{truncated}\n"


# ── Multi-URL research ────────────────────────────────────────────────────────

def research_topic(urls: list[str], question: str = "") -> str:
    """
    Scrape multiple URLs and combine into one research block for a prompt.
    Useful for oracle call deep research on a specific topic.
    """
    blocks = []
    for url in urls[:5]:   # cap at 5 URLs to avoid token explosion
        content = scrape_url(url)
        if content:
            snippet = content[:1500]
            blocks.append(f"Source: {url}\n{snippet}")

    if not blocks:
        return ""

    header = f"Web Research{f' — {question}' if question else ''}:"
    return "\n\n".join([header] + blocks)


# ── Telegram-friendly output ──────────────────────────────────────────────────

def scrape_summary(url: str, api_key_anthropic: str = "") -> str:
    """
    Scrape a URL and summarize it using Claude.
    Used by the Telegram /scrape command.
    Returns a short summary (under 500 chars) for Telegram display.
    """
    content = scrape_url(url)
    if not content:
        return f"Could not scrape {url}"

    if not api_key_anthropic:
        api_key_anthropic = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key_anthropic:
        # No Claude key — just return first 400 chars
        return content[:400] + "..."

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key_anthropic)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{
                "role": "user",
                "content": (
                    f"Summarize this web page content in 3-4 sentences. "
                    f"Focus on key facts, numbers, and market implications. "
                    f"Plain text, no markdown.\n\n{content[:3000]}"
                ),
            }],
        )
        return msg.content[0].text.strip()
    except Exception as e:
        print(f"[OctoScrape] Summary failed: {e}")
        return content[:400] + "..."


# ── Web Search ───────────────────────────────────────────────────────────────

def search_web(query: str, num_results: int = 5, cache_hours: float = 6.0) -> list[dict]:
    """
    Search the web via Firecrawl and return list of result dicts.
    Each result has: title, url, description.
    """
    cache_key = f"search_{query}_{num_results}"
    cached = _timed_cache_load(cache_key, cache_hours)
    if cached:
        return cached.get("results", [])

    try:
        app = _get_client()
        result = app.search(query, limit=num_results)
        # v4 returns SearchData with .web list of SearchResultWeb objects
        raw_results = getattr(result, "web", None) or getattr(result, "results", None) or getattr(result, "data", []) or []
        results = []
        for r in raw_results:
            if hasattr(r, "__dict__"):
                results.append({
                    "title": getattr(r, "title", ""),
                    "url": getattr(r, "url", ""),
                    "description": getattr(r, "description", "") or getattr(r, "markdown", "")[:300],
                })
            elif isinstance(r, dict):
                results.append(r)
        _timed_cache_save(cache_key, {"results": results})
        return results
    except Exception as e:
        print(f"[OctoSearch] Search failed for '{query}': {e}")
        return []


# ── Competitor Intel ──────────────────────────────────────────────────────────

def competitor_intel(names: list | None = None, force_refresh: bool = False) -> dict:
    """
    Scrape competitor sites and return structured intel dict.
    names: list of keys from COMPETITORS, or None for all.
    """
    targets = {k: v for k, v in COMPETITORS.items() if (names is None or k in names)}
    results = {}
    for name, url in targets.items():
        try:
            content = scrape_url(url, use_cache=not force_refresh)
            results[name] = {
                "url": url,
                "content_preview": content[:2000],
                "chars": len(content),
            }
            print(f"  scraped {name}: {len(content)} chars")
        except Exception as e:
            results[name] = {"url": url, "error": str(e)}
            print(f"  failed {name}: {e}")
        time.sleep(1)
    return results


def summarize_competitors(results: dict) -> str:
    """Build text summary of competitor intel for CEO prompts."""
    lines = ["COMPETITOR INTEL:"]
    for name, data in results.items():
        if "error" in data:
            lines.append(f"  {name}: [failed: {data['error']}]")
        else:
            lines.append(f"  {name} ({data['url']}) -- {data['chars']} chars scraped")
            preview = data.get("content_preview", "")[:400].replace("\n", " ")
            lines.append(f"    {preview}")
    return "\n".join(lines)


# ── Market Research ───────────────────────────────────────────────────────────

def market_research(topic: str, cache_hours: float = 12.0) -> str:
    """Search + format market research on a topic. Returns markdown block."""
    print(f"  researching: {topic}")
    results = search_web(topic, num_results=6, cache_hours=cache_hours)
    lines = [f"MARKET RESEARCH: {topic}", ""]
    for r in results:
        title = r.get("title", r.get("url", ""))
        url   = r.get("url", "")
        desc  = str(r.get("description", r.get("markdown", "")))[:300]
        lines.append(f"- {title}")
        lines.append(f"  {url}")
        if desc:
            lines.append(f"  {desc}")
    return "\n".join(lines)


def find_potential_customers(niche: str, cache_hours: float = 24.0) -> list[dict]:
    """Find potential customer communities/accounts for a given niche."""
    query = f"{niche} crypto data API signals market intelligence 2025 2026"
    return search_web(query, num_results=8, cache_hours=cache_hours)


def research_for_post(topic: str) -> str:
    """
    Quick research block for injecting fresh intel into a post.
    Returns condensed text block (under 800 chars) for prompt injection.
    """
    results = search_web(topic, num_results=4, cache_hours=4.0)
    snippets = []
    for r in results:
        title = r.get("title", "")
        desc  = str(r.get("description", r.get("markdown", "")))[:200]
        if desc:
            snippets.append(f"{title}: {desc}")
    return "\n".join(snippets[:4])


# ── #1: Pre-call News ────────────────────────────────────────────────────────

_ASSET_SEARCH_TERMS = {
    "BTC":  "Bitcoin BTC news catalyst today",
    "ETH":  "Ethereum ETH news catalyst today",
    "SOL":  "Solana SOL news catalyst today",
    "NVDA": "NVIDIA NVDA earnings analyst news today",
    "TSLA": "Tesla TSLA news catalyst today",
    "HYPE": "Hyperliquid HYPE news today",
}

def get_precall_news(asset: str, cache_hours: float = 1.5) -> str:
    """
    Search for breaking news on an asset before making an oracle call.
    Returns a compact block for prompt injection (under 400 chars).
    Cache: 1.5h — news changes fast but we don't want to burn credits every call.
    """
    query = _ASSET_SEARCH_TERMS.get(asset.upper(), f"{asset} crypto news today catalyst")
    results = search_web(query, num_results=4, cache_hours=cache_hours)
    if not results:
        return ""
    lines = [f"PRE-CALL NEWS ({asset.upper()}):"]
    for r in results[:3]:
        title = r.get("title", "").strip()
        desc  = r.get("description", "")[:120].strip()
        if title:
            lines.append(f"- {title}" + (f": {desc}" if desc else ""))
    return "\n".join(lines)


def get_precall_news_multi(assets: list, cache_hours: float = 1.5) -> str:
    """
    Fetch news for multiple assets. Returns combined block.
    Used in mode_daily where multiple assets are in play.
    Cost: 5 credits per asset searched.
    """
    # Single broad search covering all assets — cheaper than per-asset
    query = " OR ".join(assets[:4]) + " market news catalyst today breaking"
    results = search_web(query, num_results=5, cache_hours=cache_hours)
    if not results:
        return ""
    lines = ["BREAKING MARKET NEWS (Firecrawl):"]
    for r in results[:4]:
        title = r.get("title", "").strip()
        desc  = r.get("description", "")[:100].strip()
        if title:
            lines.append(f"- {title}" + (f": {desc}" if desc else ""))
    return "\n".join(lines)


# ── #2: Geopolitical Macro Context (Freeport-style briefing) ─────────────────

def get_geopolitical_context(cache_hours: float = 2.0) -> str:
    """
    Search for geopolitical/macro events with direct market impact.
    Modeled on Freeport Markets briefing style: Hormuz, ECB, oil supply, conflict escalation.
    Key insight: geopolitical events arrive in serial correlated sequences — each escalation
    raises odds of the next. Inject this context into oil/gold/macro oracle calls.
    Cache: 2h. Cost: 2 credits.
    """
    query = (
        "oil supply disruption Hormuz strait ceasefire escalation ECB rate "
        "US dollar macro geopolitical market impact breaking 2026"
    )
    results = search_web(query, num_results=5, cache_hours=cache_hours)
    if not results:
        return ""
    lines = ["GEOPOLITICAL MACRO BRIEFING (serial escalation — each event raises odds of next):"]
    for r in results[:4]:
        title = r.get("title", "").strip()
        desc  = r.get("description", "")[:120].strip()
        if title:
            lines.append(f"- {title}" + (f": {desc}" if desc else ""))
    return "\n".join(lines)


# ── #3: Earnings / Analyst Context ───────────────────────────────────────────

def get_earnings_context(ticker: str, cache_hours: float = 6.0) -> str:
    """
    Search for latest earnings report, analyst notes, or price targets for a stock.
    Used before NVDA, TSLA oracle calls.
    Cache: 6h — earnings don't change intraday.
    Cost: 5 credits per call (cached aggressively).
    """
    query = f"{ticker} earnings analyst price target forecast {datetime.now().strftime('%Y')}"
    results = search_web(query, num_results=4, cache_hours=cache_hours)
    if not results:
        return ""
    lines = [f"EARNINGS / ANALYST INTEL ({ticker}):"]
    for r in results[:3]:
        title = r.get("title", "").strip()
        desc  = r.get("description", "")[:150].strip()
        if title:
            lines.append(f"- {title}" + (f": {desc}" if desc else ""))
    return "\n".join(lines)


# ── #3: Liquidation Radar ────────────────────────────────────────────────────

_LIQ_URLS = [
    "https://www.coinglass.com/LiquidationData",
    "https://www.coinglass.com/pro/futures/LiquidationHeatMap",
]

def get_liquidation_radar(asset: str = "BTC", cache_hours: float = 0.5) -> str:
    """
    Search for current liquidation data when CoinGlass API is rate-limited.
    Also usable as standalone post content.
    Cache: 30min — liquidation maps change fast.
    Cost: 5 credits per search.
    """
    query = f"{asset} liquidations futures market data today 2026"
    results = search_web(query, num_results=4, cache_hours=cache_hours)
    if not results:
        return ""
    lines = [f"LIQUIDATION RADAR ({asset.upper()}):"]
    for r in results[:3]:
        title = r.get("title", "").strip()
        desc  = r.get("description", "")[:150].strip()
        if title:
            lines.append(f"- {title}" + (f": {desc}" if desc else ""))
    return "\n".join(lines)


def get_liquidation_post_context(cache_hours: float = 0.5) -> str:
    """
    Multi-asset liquidation context for standalone liquidation radar post.
    Searches BTC + ETH liquidations and returns combined block.
    """
    query = "BTC ETH crypto liquidations futures market today billions"
    results = search_web(query, num_results=5, cache_hours=cache_hours)
    if not results:
        return ""
    lines = ["LIQUIDATION RADAR (multi-asset):"]
    for r in results[:4]:
        title = r.get("title", "").strip()
        desc  = r.get("description", "")[:120].strip()
        if title:
            lines.append(f"- {title}" + (f": {desc}" if desc else ""))
    return "\n".join(lines)


# ── #4: Competitor Monitoring (monthly) ──────────────────────────────────────

def run_monthly_competitor_monitor() -> str:
    """
    Monthly competitor scrape + summary.
    Run manually or from CEO research. Returns summary string.
    Cost: 1 credit per competitor site scraped (5 competitors = 5 credits).
    """
    print("[Firecrawl] Running monthly competitor monitor...")
    intel = competitor_intel(force_refresh=True)
    summary = summarize_competitors(intel)

    # Also search for recent competitor news
    comp_news_query = "Kiyotaka Glassnode Messari Nansen crypto data API 2026 update pricing"
    news = search_web(comp_news_query, num_results=4, cache_hours=24.0)
    if news:
        summary += "\n\nCOMPETITOR NEWS:\n"
        for r in news[:3]:
            title = r.get("title", "").strip()
            if title:
                summary += f"- {title}\n"

    return summary


# ── #5: Datarade Intel ───────────────────────────────────────────────────────

_DATARADE_URLS = [
    "https://datarade.ai/data-categories/cryptocurrency-data",
    "https://datarade.ai/data-categories/financial-market-data",
]

def get_datarade_intel(cache_hours: float = 48.0) -> str:
    """
    Scrape Datarade competitor listings for positioning intel before sales calls.
    Cache: 48h — Datarade listings change slowly.
    Cost: 1 credit per URL scraped.
    """
    lines = ["DATARADE COMPETITIVE INTEL:"]
    for url in _DATARADE_URLS:
        content = scrape_url(url, use_cache=(cache_hours > 0))
        if content:
            # Extract first 600 chars of useful content
            snippet = content[:600].replace("\n", " ").strip()
            lines.append(f"\n[{url}]\n{snippet}")
    if len(lines) == 1:
        # Scrape failed — fall back to search
        results = search_web("Datarade cryptocurrency data providers pricing 2026", num_results=4, cache_hours=cache_hours)
        for r in results[:3]:
            title = r.get("title", "").strip()
            desc  = r.get("description", "")[:150].strip()
            if title:
                lines.append(f"- {title}: {desc}")
    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"

    if cmd == "scrape":
        url = sys.argv[2] if len(sys.argv) > 2 else "https://octodamus.com"
        print(f"Scraping: {url}")
        content = scrape_url(url, use_cache=False)
        if content:
            print(f"\n--- Content ({len(content)} chars) ---")
            print(content[:2000])
            if len(content) > 2000:
                print(f"\n[... {len(content) - 2000} more chars]")
        else:
            print("No content returned.")

    elif cmd == "search":
        query = " ".join(sys.argv[2:]) or "crypto market intelligence signals"
        print(f"Searching: {query}")
        results = search_web(query, num_results=5, cache_hours=0)
        for r in results:
            print(f"\n- {r.get('title', 'no title')}")
            print(f"  {r.get('url', '')}")
            print(f"  {str(r.get('description', ''))[:200]}")

    elif cmd == "competitors":
        print("Scraping competitor sites...")
        intel = competitor_intel(force_refresh=True)
        print(summarize_competitors(intel))

    elif cmd == "customers":
        niche = " ".join(sys.argv[2:]) or "crypto hedge fund"
        print(f"Finding potential customers: {niche}")
        results = find_potential_customers(niche, cache_hours=0)
        for r in results:
            print(f"\n- {r.get('title', 'no title')}")
            print(f"  {r.get('url', '')}")

    elif cmd == "research":
        topic = " ".join(sys.argv[2:]) or "crypto signals market"
        print(market_research(topic, cache_hours=0))

    elif cmd == "news":
        asset = sys.argv[2].upper() if len(sys.argv) > 2 else "BTC"
        print(get_precall_news(asset, cache_hours=0))

    elif cmd == "earnings":
        ticker = sys.argv[2].upper() if len(sys.argv) > 2 else "NVDA"
        print(get_earnings_context(ticker, cache_hours=0))

    elif cmd == "liquidations":
        asset = sys.argv[2].upper() if len(sys.argv) > 2 else "BTC"
        print(get_liquidation_radar(asset, cache_hours=0))

    elif cmd == "monitor_competitors":
        print(run_monthly_competitor_monitor())

    elif cmd == "datarade":
        print(get_datarade_intel(cache_hours=0))

    else:
        print("Usage: python octo_firecrawl.py [scrape <url>|search <query>|competitors|customers <niche>|research <topic>|news <asset>|earnings <ticker>|liquidations <asset>|monitor_competitors|datarade]")

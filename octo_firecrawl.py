"""
octo_firecrawl.py — OctoScrape: Firecrawl Web Scraper
Converts any URL to clean markdown that Octodamus can read and reason about.

Use cases:
  - Scrape crypto research reports, on-chain analysis posts
  - Pull full article content from paywalled-adjacent pages
  - Research any URL on demand via Telegram /scrape command
  - Feed clean text into oracle call research

Bitwarden key: FIRECRAWL_API_KEY
Get free key (500 lifetime credits): firecrawl.dev

Usage:
    from octo_firecrawl import scrape_url, scrape_for_prompt
    text = scrape_url("https://example.com/research")
    prompt_text = scrape_for_prompt("https://...", question="What is the BTC thesis?")
"""

import os
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

MAX_CONTENT_CHARS = 4000   # cap per scrape to avoid token bloat in prompts
CACHE_DIR         = Path(__file__).parent / "data" / "firecrawl_cache"


# ── Core scrape ───────────────────────────────────────────────────────────────

def scrape_url(url: str, use_cache: bool = True) -> str:
    """
    Scrape a URL and return clean markdown text.
    Caches results to avoid re-scraping the same URL.
    Returns empty string on failure.
    """
    api_key = os.environ.get("FIRECRAWL_API_KEY", "")
    if not api_key:
        print("[OctoScrape] No FIRECRAWL_API_KEY — scrape skipped.")
        return ""

    # Check cache
    if use_cache:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        import hashlib
        cache_key  = hashlib.md5(url.encode()).hexdigest()
        cache_file = CACHE_DIR / f"{cache_key}.txt"
        if cache_file.exists():
            print(f"[OctoScrape] Cache hit: {url[:60]}")
            return cache_file.read_text(encoding="utf-8")

    try:
        from firecrawl import FirecrawlApp
        app    = FirecrawlApp(api_key=api_key)
        result = app.scrape_url(
            url,
            formats=["markdown"],
            only_main_content=True,   # strip nav/footer/ads
        )
        content = ""
        if hasattr(result, "markdown"):
            content = result.markdown or ""
        elif isinstance(result, dict):
            content = result.get("markdown") or result.get("content") or ""

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


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python octo_firecrawl.py <url> [question]")
        sys.exit(1)

    url      = sys.argv[1]
    question = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else ""

    print(f"Scraping: {url}")
    content = scrape_url(url, use_cache=False)
    if content:
        print(f"\n--- Content ({len(content)} chars) ---")
        print(content[:2000])
        if len(content) > 2000:
            print(f"\n[... {len(content) - 2000} more chars]")
    else:
        print("No content returned.")

"""
octo_playwright.py — OctoVision: Playwright + Claude Vision
Gives Octodamus and OctoBoto eyes for web pages and charts.

Usage:
    from octo_playwright import chart_and_analyze, see_page

    # TradingView chart screenshot + Claude Vision analysis
    img_bytes, analysis = await chart_and_analyze("BTC", "4h", api_key)

    # See any web page
    img_bytes, analysis = await see_page(
        "https://coinglass.com/LiquidationMap",
        question="What are the major BTC liquidation clusters?",
        api_key=api_key
    )

    # Raw screenshot only (no vision)
    img_bytes = await screenshot_url("https://example.com")
"""

import asyncio
import base64
import logging
import urllib.parse
from pathlib import Path
from typing import Optional

log = logging.getLogger("OctoPlaywright")

# ── Playwright availability ───────────────────────────────────────────────────
try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    log.warning("[OctoPlaywright] playwright not installed — run: python -m playwright install chromium")

# ── Claude Vision model ───────────────────────────────────────────────────────
VISION_MODEL = "claude-sonnet-4-6"

# ── TradingView interval codes ────────────────────────────────────────────────
TIMEFRAME_MAP: dict[str, str] = {
    "1m":  "1",   "3m":  "3",   "5m":  "5",
    "15m": "15",  "30m": "30",
    "1h":  "60",  "2h":  "120", "3h":  "180",
    "4h":  "240", "6h":  "360", "8h":  "480",  "12h": "720",
    "1d": "D", "d": "D", "daily": "D",
    "1w": "W", "w": "W", "weekly": "W",
    "1mo": "M", "mo": "M", "monthly": "M",
}

# ── Short ticker → TradingView symbol ────────────────────────────────────────
TICKER_MAP: dict[str, str] = {
    # Crypto
    "BTC":   "BINANCE:BTCUSDT",
    "ETH":   "BINANCE:ETHUSDT",
    "SOL":   "BINANCE:SOLUSDT",
    "BNB":   "BINANCE:BNBUSDT",
    "XRP":   "BINANCE:XRPUSDT",
    "DOGE":  "BINANCE:DOGEUSDT",
    "ADA":   "BINANCE:ADAUSDT",
    "AVAX":  "BINANCE:AVAXUSDT",
    "LINK":  "BINANCE:LINKUSDT",
    "MATIC": "BINANCE:MATICUSDT",
    # Equities / ETFs
    "SPY":   "AMEX:SPY",
    "QQQ":   "NASDAQ:QQQ",
    "NVDA":  "NASDAQ:NVDA",
    "TSLA":  "NASDAQ:TSLA",
    "AAPL":  "NASDAQ:AAPL",
    "MSFT":  "NASDAQ:MSFT",
    "META":  "NASDAQ:META",
    "AMZN":  "NASDAQ:AMZN",
    # Macro
    "GOLD":  "TVC:GOLD",
    "OIL":   "TVC:USOIL",
    "DXY":   "TVC:DXY",
    "VIX":   "CBOE:VIX",
    "US10Y": "TVC:US10Y",
}

# ── Vision prompts ────────────────────────────────────────────────────────────
CHART_PROMPT = (
    "You are a professional technical analyst reviewing a TradingView chart screenshot.\n\n"
    "Read the chart and provide:\n"
    "1. Asset and timeframe (from the chart header)\n"
    "2. Current price and 24h direction\n"
    "3. Primary trend — bullish, bearish, or ranging — and the reason\n"
    "4. Key support levels visible\n"
    "5. Key resistance levels visible\n"
    "6. Any notable patterns (wedge, channel, head & shoulders, etc.)\n"
    "7. One-sentence bias for the next 24-48 hours\n\n"
    "Be specific with price levels. Plain text, no markdown, no bullets."
)

PAGE_PROMPT = (
    "Describe the key information on this web page screenshot. "
    "Focus on numbers, data, charts, tables, and anything actionable. "
    "Plain text, no markdown."
)


# ── Core: screenshot any URL ──────────────────────────────────────────────────

async def screenshot_url(
    url: str,
    selector: Optional[str] = None,
    wait_selector: Optional[str] = None,
    wait_ms: int = 3500,
    viewport_width: int = 1400,
    viewport_height: int = 900,
    full_page: bool = False,
    dismiss_popups: bool = True,
) -> bytes:
    """
    Load a URL in headless Chromium and return a PNG screenshot as bytes.

    selector:      CSS selector to screenshot a specific element (None = full viewport)
    wait_selector: Wait for this element before screenshotting
    wait_ms:       Extra milliseconds to wait for JS rendering (charts need ~5-6s)
    full_page:     Capture the full scrollable page height
    dismiss_popups: Press Escape + attempt to close cookie/consent dialogs
    """
    if not PLAYWRIGHT_AVAILABLE:
        raise RuntimeError(
            "playwright not installed. Run: python -m playwright install chromium"
        )

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        ctx = await browser.new_context(
            viewport={"width": viewport_width, "height": viewport_height},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )

        # Pre-set TradingView cookie consent to avoid banners
        await ctx.add_init_script("""
            try {
                localStorage.setItem('cookiesSettings', '{"analytics":true,"advertising":true}');
                localStorage.setItem('tv_release_channel', 'stable');
            } catch(e) {}
        """)

        page = await ctx.new_page()

        # Load page — fall back gracefully if networkidle never fires
        try:
            await page.goto(url, wait_until="networkidle", timeout=30_000)
        except Exception:
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
            except Exception as e:
                log.warning(f"[OctoPlaywright] Load warning for {url}: {e}")

        if dismiss_popups:
            # Try Escape first (closes most modals)
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(300)
            # Try clicking common consent buttons
            for label in ["Accept all", "Accept All", "Accept", "I Agree", "Got it", "Close"]:
                try:
                    btn = page.get_by_role("button", name=label, exact=False)
                    if await btn.count() > 0:
                        await btn.first.click(timeout=1_000)
                        await page.wait_for_timeout(300)
                        break
                except Exception:
                    pass
            # TradingView-specific: dismiss "Got it" tooltip (magnet mode, etc.)
            try:
                got_it = page.locator("button:has-text('Got it')")
                if await got_it.count() > 0:
                    await got_it.first.click(timeout=1_000)
                    await page.wait_for_timeout(200)
            except Exception:
                pass
            # Click the chart body to clear any hover tooltips
            try:
                await page.mouse.click(700, 400)
                await page.wait_for_timeout(200)
            except Exception:
                pass

        if wait_selector:
            try:
                await page.wait_for_selector(wait_selector, timeout=10_000)
            except Exception:
                pass

        # Wait for charts/dynamic content to render
        if wait_ms > 0:
            await page.wait_for_timeout(wait_ms)

        # Take screenshot
        if selector:
            el = await page.query_selector(selector)
            img_bytes = await (el.screenshot(type="png") if el
                               else page.screenshot(type="png", full_page=full_page))
        else:
            img_bytes = await page.screenshot(type="png", full_page=full_page)

        await browser.close()
        log.info(f"[OctoPlaywright] {url} → {len(img_bytes) // 1024}KB")
        return img_bytes


# ── Claude Vision analysis ────────────────────────────────────────────────────

def _vision_call(img_bytes: bytes, question: str, api_key: str) -> str:
    """Synchronous Claude Vision API call. Wrap in run_in_executor for async."""
    import anthropic
    img_b64 = base64.standard_b64encode(img_bytes).decode()
    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model=VISION_MODEL,
        max_tokens=700,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": img_b64},
                },
                {"type": "text", "text": question},
            ],
        }],
    )
    return msg.content[0].text.strip()


async def _analyze(img_bytes: bytes, question: str, api_key: str) -> str:
    """Run vision call in thread pool so it doesn't block the event loop."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _vision_call, img_bytes, question, api_key)


# ── TradingView helpers ───────────────────────────────────────────────────────

def _tv_symbol(ticker: str) -> str:
    return TICKER_MAP.get(ticker.upper().strip(), ticker.upper().strip())


def _tv_interval(timeframe: str) -> str:
    return TIMEFRAME_MAP.get(timeframe.lower().strip(), "240")


def tv_chart_url(ticker: str = "BTC", timeframe: str = "4h") -> str:
    """Build a dark-theme TradingView chart URL."""
    sym = urllib.parse.quote(_tv_symbol(ticker), safe="")
    interval = _tv_interval(timeframe)
    return (
        f"https://www.tradingview.com/chart/"
        f"?symbol={sym}&interval={interval}"
        f"&theme=dark&style=1&hide_side_toolbar=1&locale=en"
    )


async def chart_screenshot(ticker: str = "BTC", timeframe: str = "4h") -> bytes:
    """Screenshot a TradingView chart. Returns PNG bytes."""
    url = tv_chart_url(ticker, timeframe)
    log.info(f"[OctoPlaywright] TV chart: {ticker} {timeframe}")
    return await screenshot_url(
        url=url,
        wait_ms=6_000,        # TV canvas needs 5-6s to fully render
        viewport_width=1400,
        viewport_height=800,
        dismiss_popups=True,
    )


async def chart_and_analyze(
    ticker: str = "BTC",
    timeframe: str = "4h",
    api_key: str = "",
) -> tuple[bytes, str]:
    """
    Screenshot a TradingView chart, analyze with Claude Vision.
    Returns (png_bytes, analysis_text).
    analysis_text is empty string if no api_key provided.
    """
    img_bytes = await chart_screenshot(ticker, timeframe)
    if not api_key:
        return img_bytes, ""
    analysis = await _analyze(img_bytes, CHART_PROMPT, api_key)
    return img_bytes, analysis


# ── General page vision ───────────────────────────────────────────────────────

async def see_page(
    url: str,
    question: Optional[str] = None,
    api_key: str = "",
    wait_ms: int = 3000,
    full_page: bool = False,
) -> tuple[bytes, str]:
    """
    Screenshot any URL and analyze with Claude Vision.

    Returns (png_bytes, analysis_text).

    Examples:
        see_page("https://coinglass.com/LiquidationMap",
                 "What are the BTC liquidation levels?", api_key)

        see_page("https://economic-calendar.tradingview.com/",
                 "What major events are scheduled this week?", api_key)
    """
    img_bytes = await screenshot_url(url=url, wait_ms=wait_ms, full_page=full_page)
    if not api_key:
        return img_bytes, ""
    prompt = question or PAGE_PROMPT
    analysis = await _analyze(img_bytes, prompt, api_key)
    return img_bytes, analysis


# ── CLI test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.path.insert(0, r"C:\Users\walli\octodamus")
    from bitwarden import load_secrets
    secrets  = load_secrets()
    api_key  = secrets.get("ANTHROPIC_API_KEY", "")
    ticker   = sys.argv[1] if len(sys.argv) > 1 else "BTC"
    timeframe = sys.argv[2] if len(sys.argv) > 2 else "4h"

    async def _test():
        print(f"Screenshotting {ticker} {timeframe}...")
        img_bytes, analysis = await chart_and_analyze(ticker, timeframe, api_key)
        out = Path(f"octo_playwright_test_{ticker}_{timeframe}.png")
        out.write_bytes(img_bytes)
        print(f"Saved {out} ({len(img_bytes)//1024}KB)")
        if analysis:
            print(f"\nClaude Vision:\n{analysis}")

    asyncio.run(_test())

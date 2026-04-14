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
    # range=3M shows ~3 months of history — matches Octodamus reference chart style
    return (
        f"https://www.tradingview.com/chart/"
        f"?symbol={sym}&interval={interval}"
        f"&theme=dark&style=1&hide_side_toolbar=1&hide_top_toolbar=1"
        f"&locale=en"
    )

# CSS injected into TradingView to strip all UI chrome, leaving only the chart canvas
_TV_HIDE_CHROME_CSS = """
    /* Top header / toolbar */
    .chart-toolbar,
    .header-chart-panel,
    .tv-header,
    [class*="topBar"],
    [class*="header-chart"],
    .layout__area--top { display: none !important; }

    /* Right side panel (watchlist, performance) */
    .layout__area--right,
    .right-toolbar,
    [class*="widgetbar"],
    [class*="right-toolbar"] { display: none !important; }

    /* Bottom timeframe bar + navigation */
    .layout__area--bottom,
    .bottom-widgetbar-content,
    [class*="bottomBar"],
    [class*="bottom-toolbar"] { display: none !important; }

    /* Left drawing tools (belt and suspenders) */
    .layout__area--left,
    [class*="left-toolbar"],
    [class*="drawingToolbar"] { display: none !important; }

    /* Hide any toast / snackbar / notification banners (scroll hints etc.) */
    [class*="toast"],
    [class*="snackbar"],
    [class*="notification-bar"],
    [class*="NotificationToast"],
    [class*="notificationToast"],
    [data-name="notification"],
    .tv-toast { display: none !important; }

    /* Make chart fill the full viewport */
    .layout__area--center,
    .chart-container,
    .chart-page,
    body, html { width: 100% !important; height: 100% !important; }
"""


async def chart_screenshot(ticker: str = "BTC", timeframe: str = "4h") -> bytes:
    """
    Screenshot a TradingView chart — chart canvas only, no UI chrome.
    Returns PNG bytes.
    """
    if not PLAYWRIGHT_AVAILABLE:
        raise RuntimeError("playwright not installed. Run: python -m playwright install chromium")

    url = tv_chart_url(ticker, timeframe)
    log.info(f"[OctoPlaywright] TV chart: {ticker} {timeframe}")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        ctx = await browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        await ctx.add_init_script("""
            try {
                localStorage.setItem('cookiesSettings', '{"analytics":true,"advertising":true}');
                localStorage.setItem('tv_release_channel', 'stable');
            } catch(e) {}
        """)

        page = await ctx.new_page()

        try:
            await page.goto(url, wait_until="networkidle", timeout=30_000)
        except Exception:
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
            except Exception as e:
                log.warning(f"[OctoPlaywright] Load warning: {e}")

        # Let chart canvas render
        await page.wait_for_timeout(5_000)

        # Dismiss ALL "Got it!" / "Accept" / "Close" tooltips — loop until none left
        for _ in range(8):
            dismissed = False
            for label in ["Got it!", "Got it", "Accept all", "Accept", "I Agree", "Close"]:
                try:
                    btns = page.get_by_role("button", name=label, exact=False)
                    count = await btns.count()
                    for i in range(count):
                        await btns.nth(i).click(timeout=800)
                        await page.wait_for_timeout(200)
                        dismissed = True
                except Exception:
                    pass
            # Also press Escape to clear any remaining overlays
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(200)
            if not dismissed:
                break

        # Inject CSS to hide all UI chrome
        await page.add_style_tag(content=_TV_HIDE_CHROME_CSS)
        await page.wait_for_timeout(800)

        # Scroll down over the chart to zoom out and show ~3 months of history
        # (TradingView: scroll down = zoom out / more bars visible)
        try:
            await page.mouse.move(600, 350)
            await page.wait_for_timeout(200)
            for _ in range(8):
                await page.mouse.wheel(0, 200)
                await page.wait_for_timeout(80)
            await page.wait_for_timeout(500)
        except Exception:
            pass

        # Dismiss any scroll-triggered tooltips ("Press and hold Ctrl...")
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(300)
        # JS sweep — find Ctrl/zoom notification by text, walk up to full container
        try:
            await page.evaluate("""
                const walker = document.createTreeWalker(
                    document.body, NodeFilter.SHOW_TEXT, null
                );
                let node;
                while ((node = walker.nextNode())) {
                    if (node.textContent.includes('Ctrl') &&
                        node.textContent.toLowerCase().includes('zoom')) {
                        let el = node.parentElement;
                        // Walk up until we find a fixed/absolute positioned container
                        // (the full toast wrapper) rather than just the text span
                        for (let i = 0; i < 15 && el && el !== document.body; i++) {
                            const style = window.getComputedStyle(el);
                            const r = el.getBoundingClientRect();
                            if (style.position === 'fixed' || style.position === 'absolute') {
                                el.style.display = 'none';
                                break;
                            }
                            // Fallback: large enough to be the whole toast
                            if (r.width > 300 && r.height > 40) {
                                el.style.display = 'none';
                                break;
                            }
                            el = el.parentElement;
                        }
                    }
                }
            """)
        except Exception:
            pass

        # Move mouse fully off the chart to clear crosshair + hover markers
        await page.mouse.move(5, 5)
        await page.wait_for_timeout(800)

        # Try to screenshot just the chart canvas element
        chart_selectors = [
            ".chart-container",
            ".layout__area--center",
            "[class*='chart-container']",
            "canvas",
        ]
        img_bytes = None
        for sel in chart_selectors:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    box = await el.bounding_box()
                    if box and box["width"] > 400 and box["height"] > 300:
                        img_bytes = await page.screenshot(
                            type="png",
                            clip={
                                "x": box["x"],
                                "y": box["y"],
                                "width": box["width"],
                                "height": box["height"],
                            },
                        )
                        log.info(f"[OctoPlaywright] Clipped to {sel}: {box['width']:.0f}x{box['height']:.0f}")
                        break
            except Exception:
                pass

        # Fallback: full viewport if no element matched
        if not img_bytes:
            log.warning("[OctoPlaywright] Element clip failed — falling back to viewport screenshot")
            img_bytes = await page.screenshot(type="png")

        await browser.close()
        log.info(f"[OctoPlaywright] {ticker} {timeframe} -> {len(img_bytes) // 1024}KB")
        return img_bytes


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

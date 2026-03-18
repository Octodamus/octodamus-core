"""
octo_market_snapshot.py
OctoEyes — Live Market Snapshot
================================
Fetches real-time prices for the Octodamus watchlist.
Works immediately with zero API keys via yfinance (free).
Automatically upgrades to Financial Datasets API if key is present.

Watchlist: NVDA, TSLA, AAPL, BTC-USD

Usage:
    python3 octo_market_snapshot.py

    Or import:
        from octo_market_snapshot import fetch_market_snapshot
        snapshot = fetch_market_snapshot()
"""

import os
import sys
import time
import logging
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [OctoEyes] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("octo_market_snapshot")

# ── Watchlist ─────────────────────────────────────────────────────────────────

STOCK_WATCHLIST  = ["NVDA", "TSLA", "AAPL"]
CRYPTO_WATCHLIST = ["BTC-USD"]

ALL_TICKERS = STOCK_WATCHLIST + CRYPTO_WATCHLIST

# ── Data Class ────────────────────────────────────────────────────────────────

@dataclass
class TickerSnapshot:
    ticker: str
    price: Optional[float] = None
    change_pct: Optional[float] = None
    change_abs: Optional[float] = None
    prev_close: Optional[float] = None
    volume: Optional[int] = None
    market_cap: Optional[float] = None
    source: str = "yfinance"
    error: Optional[str] = None
    fetched_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def is_healthy(self) -> bool:
        return self.error is None and self.price is not None

    def arrow(self) -> str:
        if self.change_pct is None:
            return "–"
        return "▲" if self.change_pct >= 0 else "▼"

    def summary(self) -> str:
        if not self.is_healthy():
            return f"{self.ticker}: ⚠️ {self.error}"
        price_str = f"${self.price:,.2f}"
        change_str = ""
        if self.change_pct is not None:
            change_str = f"  {self.arrow()} {self.change_pct:+.2f}%"
        return f"{self.ticker}: {price_str}{change_str}"


@dataclass
class MarketSnapshot:
    tickers: dict = field(default_factory=dict)   # {ticker: TickerSnapshot}
    fetched_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    source: str = "yfinance"

    def all_healthy(self) -> bool:
        return all(t.is_healthy() for t in self.tickers.values())

    def summary_lines(self) -> list:
        return [t.summary() for t in self.tickers.values()]

    def x_ready(self) -> str:
        """One-line X-post-ready market summary."""
        parts = []
        for t in self.tickers.values():
            if t.is_healthy() and t.change_pct is not None:
                parts.append(f"{t.ticker} {t.arrow()}{abs(t.change_pct):.1f}%")
        return "📡 " + "  |  ".join(parts) if parts else "📡 Market data unavailable"


# ── yfinance Fetcher (free, no API key) ───────────────────────────────────────

def _ensure_yfinance():
    """Install yfinance if not present."""
    try:
        import yfinance
        return True
    except ImportError:
        log.info("yfinance not found — installing...")
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "yfinance",
             "--break-system-packages", "--quiet"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            log.error(f"Failed to install yfinance: {result.stderr}")
            return False
        log.info("yfinance installed.")
        return True


def fetch_via_yfinance(tickers: list) -> dict:
    """
    Fetch live prices for all tickers in one batch call.
    Returns {ticker: TickerSnapshot}.
    """
    if not _ensure_yfinance():
        return {t: TickerSnapshot(ticker=t, error="yfinance unavailable") for t in tickers}

    import yfinance as yf

    results = {}
    log.info(f"Fetching via yfinance: {tickers}")

    try:
        # Batch download — one call for all tickers
        data = yf.download(
            tickers=tickers,
            period="2d",         # 2 days to get prev close + today
            interval="1d",
            group_by="ticker",
            auto_adjust=True,
            progress=False,
            threads=True,
        )

        for ticker in tickers:
            try:
                # Handle single vs multi ticker response shape
                if len(tickers) == 1:
                    ticker_data = data
                else:
                    ticker_data = data[ticker] if ticker in data.columns.get_level_values(0) else None

                if ticker_data is None or ticker_data.empty:
                    results[ticker] = TickerSnapshot(
                        ticker=ticker,
                        error="No data returned"
                    )
                    continue

                # Get last two rows: yesterday + today
                rows = ticker_data.dropna()
                if len(rows) < 1:
                    results[ticker] = TickerSnapshot(ticker=ticker, error="Empty data")
                    continue

                today_close = float(rows["Close"].iloc[-1])
                prev_close  = float(rows["Close"].iloc[-2]) if len(rows) >= 2 else None
                volume      = int(rows["Volume"].iloc[-1]) if "Volume" in rows.columns else None

                change_abs  = None
                change_pct  = None
                if prev_close and prev_close > 0:
                    change_abs = today_close - prev_close
                    change_pct = (change_abs / prev_close) * 100

                results[ticker] = TickerSnapshot(
                    ticker=ticker,
                    price=today_close,
                    change_pct=change_pct,
                    change_abs=change_abs,
                    prev_close=prev_close,
                    volume=volume,
                    source="yfinance",
                )

            except Exception as e:
                log.warning(f"Error parsing {ticker}: {e}")
                results[ticker] = TickerSnapshot(ticker=ticker, error=str(e))

    except Exception as e:
        log.error(f"yfinance batch fetch failed: {e}")
        for ticker in tickers:
            if ticker not in results:
                results[ticker] = TickerSnapshot(ticker=ticker, error=str(e))

    # Fallback: fetch individually for any that failed
    for ticker in tickers:
        if ticker not in results or not results[ticker].is_healthy():
            log.info(f"Retrying {ticker} individually...")
            results[ticker] = _fetch_single_yfinance(ticker)

    return results


def _fetch_single_yfinance(ticker: str) -> TickerSnapshot:
    """Fetch a single ticker using yfinance Ticker object (more reliable fallback)."""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        info = t.fast_info

        price = getattr(info, "last_price", None) or getattr(info, "regularMarketPrice", None)
        prev  = getattr(info, "previous_close", None) or getattr(info, "regularMarketPreviousClose", None)

        if price is None:
            # Last resort: get from history
            hist = t.history(period="2d")
            if not hist.empty:
                price    = float(hist["Close"].iloc[-1])
                prev     = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else None

        if price is None:
            return TickerSnapshot(ticker=ticker, error="Price unavailable")

        change_abs = (price - prev) if prev else None
        change_pct = ((change_abs / prev) * 100) if (change_abs and prev) else None

        return TickerSnapshot(
            ticker=ticker,
            price=float(price),
            change_pct=change_pct,
            change_abs=change_abs,
            prev_close=float(prev) if prev else None,
            market_cap=getattr(info, "market_cap", None),
            source="yfinance",
        )

    except Exception as e:
        return TickerSnapshot(ticker=ticker, error=str(e))


# ── Financial Datasets API Fetcher (paid, richer data) ────────────────────────

def fetch_via_financial_datasets(tickers: list) -> dict:
    """
    Fetch prices via Financial Datasets API.
    Only called if FINANCIAL_DATASETS_API_KEY is set in environment.
    Falls back to yfinance for any ticker that fails.
    """
    try:
        from financial_data_client import get_current_price, get_current_crypto_price
    except ImportError:
        log.warning("financial_data_client.py not found — using yfinance only.")
        return fetch_via_yfinance(tickers)

    results = {}
    for ticker in tickers:
        try:
            if ticker.endswith("-USD"):
                # Crypto
                raw = get_current_crypto_price(ticker.replace("-USD", ""))
                snap = raw.get("snapshot", {})
            else:
                # Stock
                raw = get_current_price(ticker)
                snap = raw.get("snapshot", {})

            price = snap.get("price")
            if price:
                results[ticker] = TickerSnapshot(
                    ticker=ticker,
                    price=float(price),
                    change_pct=snap.get("day_change_percent"),
                    market_cap=snap.get("market_cap"),
                    source="financial_datasets",
                )
            else:
                log.warning(f"No price in Financial Datasets response for {ticker}")
                results[ticker] = _fetch_single_yfinance(ticker)  # fallback

        except Exception as e:
            log.warning(f"Financial Datasets failed for {ticker}: {e} — falling back to yfinance")
            results[ticker] = _fetch_single_yfinance(ticker)

        time.sleep(0.25)  # rate limit courtesy

    return results


# ── Main Snapshot Entry Point ─────────────────────────────────────────────────

def fetch_market_snapshot(
    tickers: list = None,
    force_yfinance: bool = False,
) -> MarketSnapshot:
    """
    Fetch live prices for the Octodamus watchlist.

    Auto-selects data source:
      - Financial Datasets API if FINANCIAL_DATASETS_API_KEY is set
      - yfinance (free, no key) otherwise

    Returns a MarketSnapshot with all tickers populated.
    """
    target = tickers or ALL_TICKERS

    api_key = os.environ.get("FINANCIAL_DATASETS_API_KEY", "")
    use_paid_api = bool(api_key) and not force_yfinance

    if use_paid_api:
        log.info("Using Financial Datasets API (key detected).")
        ticker_data = fetch_via_financial_datasets(target)
        source = "financial_datasets"
    else:
        log.info("Using yfinance (free). Set FINANCIAL_DATASETS_API_KEY to upgrade.")
        ticker_data = fetch_via_yfinance(target)
        source = "yfinance"

    return MarketSnapshot(
        tickers=ticker_data,
        source=source,
    )


# ── Journal Logger ────────────────────────────────────────────────────────────

def log_snapshot_to_journal(snapshot: MarketSnapshot) -> None:
    """Append market snapshot to the OctoEyes daily journal."""
    journal_dir = os.path.expanduser("~/octo_life/PARA/Resources")
    os.makedirs(journal_dir, exist_ok=True)
    journal_path = os.path.join(journal_dir, "octo_market_log.md")

    entry = f"\n## {snapshot.fetched_at} — Market Snapshot\n"
    for t in snapshot.tickers.values():
        entry += f"- {t.summary()}\n"
    entry += f"- Source: {snapshot.source}\n"

    with open(journal_path, "a") as f:
        f.write(entry)
    log.info(f"Logged to {journal_path}")


# ── CLI Entry Point ───────────────────────────────────────────────────────────

def main():
    print("\n" + "═" * 60)
    print("  📡 OCTODAMUS — LIVE MARKET SNAPSHOT")
    print("═" * 60)

    snapshot = fetch_market_snapshot()

    for ticker, t in snapshot.tickers.items():
        if t.is_healthy():
            price_str  = f"${t.price:>10,.2f}"
            change_str = f"  {t.arrow()} {t.change_pct:+.2f}%" if t.change_pct is not None else ""
            vol_str    = f"  vol: {t.volume:,}" if t.volume else ""
            print(f"  {ticker:<10} {price_str}{change_str}{vol_str}")
        else:
            print(f"  {ticker:<10}  ⚠️  {t.error}")

    print(f"\n  Source:  {snapshot.source}")
    print(f"  Time:    {snapshot.fetched_at}")
    print("═" * 60)
    print(f"\n  X-ready: {snapshot.x_ready()}\n")

    log_snapshot_to_journal(snapshot)


if __name__ == "__main__":
    main()

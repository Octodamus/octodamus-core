"""
financial_data_client.py
Octodamus Market Oracle — Financial Datasets API wrapper
Plug this into OctoEyes (monitoring) and OctoInk (post generation)
"""

import httpx
import os
from typing import Optional
from datetime import date

# Keys injected into os.environ by bitwarden.load_all_secrets() at startup
BASE_URL = "https://api.financialdatasets.ai"

def _headers():
    return {"X-API-KEY": os.environ.get("FINANCIAL_DATASETS_API_KEY")}


# ─────────────────────────────────────────────
# STOCK DATA
# ─────────────────────────────────────────────

def get_current_price(ticker: str) -> dict:
    """Real-time stock price — core oracle call."""
    r = httpx.get(f"{BASE_URL}/prices/snapshot/", headers=_headers(), params={"ticker": ticker})
    r.raise_for_status()
    return r.json()


def get_historical_prices(ticker: str, start: str, end: str, interval: str = "day") -> dict:
    """
    Historical OHLCV prices.
    interval options: 'minute', 'hour', 'day', 'week', 'month', 'year'
    dates: 'YYYY-MM-DD'
    """
    r = httpx.get(f"{BASE_URL}/prices/", headers=_headers(), params={
        "ticker": ticker,
        "interval": interval,
        "interval_multiplier": 1,
        "start_date": start,
        "end_date": end
    })
    r.raise_for_status()
    return r.json()


def get_income_statements(ticker: str, period: str = "annual", limit: int = 4) -> dict:
    """Income statements. period: 'annual' or 'quarterly'"""
    r = httpx.get(f"{BASE_URL}/financials/income-statements/", headers=_headers(), params={
        "ticker": ticker,
        "period": period,
        "limit": limit
    })
    r.raise_for_status()
    return r.json()


def get_balance_sheet(ticker: str, period: str = "annual", limit: int = 4) -> dict:
    r = httpx.get(f"{BASE_URL}/financials/balance-sheets/", headers=_headers(), params={
        "ticker": ticker,
        "period": period,
        "limit": limit
    })
    r.raise_for_status()
    return r.json()


def get_cash_flow(ticker: str, period: str = "annual", limit: int = 4) -> dict:
    r = httpx.get(f"{BASE_URL}/financials/cash-flow-statements/", headers=_headers(), params={
        "ticker": ticker,
        "period": period,
        "limit": limit
    })
    r.raise_for_status()
    return r.json()


def get_company_news(ticker: str, start: str, end: str, limit: int = 10) -> dict:
    r = httpx.get(f"{BASE_URL}/news/", headers=_headers(), params={
        "ticker": ticker,
        "start_date": start,
        "end_date": end,
        "limit": limit
    })
    r.raise_for_status()
    return r.json()


# ─────────────────────────────────────────────
# CRYPTO DATA
# ─────────────────────────────────────────────

def get_current_crypto_price(ticker: str) -> dict:
    """Get current crypto price. ticker examples: 'BTC', 'ETH'"""
    r = httpx.get(f"{BASE_URL}/crypto/prices/snapshot/", headers=_headers(), params={"ticker": ticker})
    r.raise_for_status()
    return r.json()


def get_historical_crypto_prices(ticker: str, start: str, end: str, interval: str = "day") -> dict:
    r = httpx.get(f"{BASE_URL}/crypto/prices/", headers=_headers(), params={
        "ticker": ticker,
        "interval": interval,
        "interval_multiplier": 1,
        "start_date": start,
        "end_date": end
    })
    r.raise_for_status()
    return r.json()


def get_available_crypto_tickers() -> dict:
    r = httpx.get(f"{BASE_URL}/crypto/available-tickers/", headers=_headers())
    r.raise_for_status()
    return r.json()


# ─────────────────────────────────────────────
# ORACLE CONTEXT BUILDER
# Helper: assembles a rich data packet for Claude to process
# ─────────────────────────────────────────────

def build_oracle_context(ticker: str, include_fundamentals: bool = False) -> dict:
    """
    Assembles a data packet for OctoInk or OctoEyes.
    Returns price + optionally fundamentals + news in one dict.
    """
    from datetime import datetime, timedelta
    today = datetime.today().strftime("%Y-%m-%d")
    week_ago = (datetime.today() - timedelta(days=7)).strftime("%Y-%m-%d")

    context = {
        "ticker": ticker,
        "current_price": get_current_price(ticker),
        "week_prices": get_historical_prices(ticker, week_ago, today),
        "recent_news": get_company_news(ticker, week_ago, today, limit=5),
    }

    if include_fundamentals:
        context["income_statements"] = get_income_statements(ticker, period="quarterly", limit=2)
        context["cash_flow"] = get_cash_flow(ticker, period="quarterly", limit=2)

    return context

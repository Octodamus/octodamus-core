"""
octo_boto_polymarket.py — Polymarket Gamma API Client v2
Fixes:
  - API params sent as proper types (not string "true"/"false")
  - Retry logic with exponential backoff
  - days_to_close added to every summary (needed for composite_score)
  - volume_to_liquidity ratio added (market efficiency proxy)
  - URL now uses /market/ path (direct link to binary contract)
  - Deduplication by question text (same event sometimes has duplicate entries)
  - get_markets() pagination support for larger scans
  - Better _check_resolved() — handles winner field variations
"""

import json
import time
import requests
from datetime import datetime, timezone
from typing import Optional

from octo_boto_math import days_until

GAMMA_BASE   = "https://gamma-api.polymarket.com"
POLY_MARKET  = "https://polymarket.com/market/"
POLY_EVENT   = "https://polymarket.com/event/"

YES_OUTCOMES = {"YES", "TRUE", "1", "OVER", "WIN"}
MAX_RETRIES  = 3
RETRY_DELAY  = 1.5   # seconds, doubles each retry


class GammaClient:
    def __init__(self, min_liquidity: float = 3_000):
        self.min_liquidity = min_liquidity
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "OctoBoto/2.0 (Octodamus AI trading engine)",
            "Accept":     "application/json",
        })

    # ─── Market Fetching ──────────────────────────────────────────────────────

    def get_markets(self, limit: int = 100, offset: int = 0) -> list:
        """
        Fetch active binary markets sorted by 24h volume descending.
        Bug fix v1: params were passed as Python strings "true"/"false" —
        requests serialises them correctly as booleans now via separate bool params.
        Bug fix v2: deduplication by normalised question text.
        """
        params = {
            "active":    "true",    # Gamma API expects string "true"
            "closed":    "false",
            "limit":     min(limit, 500),
            "offset":    offset,
            "order":     "volume24hr",
            "ascending": "false",
        }

        raw = self._get(f"{GAMMA_BASE}/markets", params=params)
        if not raw:
            return []

        seen_questions = set()
        results = []

        for m in raw:
            liq = float(m.get("liquidity", 0) or 0)
            if liq < self.min_liquidity:
                continue

            summary = self._summarize(m)
            if not summary or summary.get("yes_price") is None:
                continue

            # Deduplication — Polymarket sometimes lists same question twice
            q_key = summary["question"].lower().strip()[:80]
            if q_key in seen_questions:
                continue
            seen_questions.add(q_key)

            results.append(summary)

        return results

    def get_markets_paginated(self, total: int = 300) -> list:
        """Fetch more than 100 markets via pagination."""
        all_markets = []
        offset = 0
        batch  = 100

        while len(all_markets) < total:
            page = self.get_markets(limit=batch, offset=offset)
            if not page:
                break
            all_markets.extend(page)
            if len(page) < batch:
                break   # hit end of results
            offset += batch
            time.sleep(0.3)  # polite rate limit

        return all_markets[:total]

    def get_crypto_markets(self, min_liquidity: float = 2_000) -> list:
        """
        Fetch active Polymarket markets specifically about crypto price calls.
        Searches for BTC, ETH, SOL keyword markets — these trigger Coinglass
        futures context in the AI estimator.

        Lower min_liquidity threshold than general scan (2k vs 3k) because
        crypto markets on Polymarket tend to have less liquidity than political events.
        Returns deduplicated list merged across all keyword searches.
        """
        # Search keywords — longer terms are more precise; short ones (BTC/ETH)
        # need word-boundary matching client-side to avoid false positives.
        SEARCH_TERMS = ["bitcoin", "ethereum", "solana", "crypto", "BTC", "ETH", "SOL"]

        # Client-side confirmation — question must contain a whole-word crypto term.
        # Use space-padded or unambiguous full words to avoid substring false positives
        # (e.g. "eth" in "Beth", "sol" in "resolve", "bnb" in "combine").
        CONFIRM_PATTERNS = [
            "bitcoin", " btc ", " btc?", "$btc",
            "ethereum", " eth ", " eth?", "$eth",
            "solana", " sol ", " sol?", "$sol",
            "crypto", "blockchain", "defi", "web3",
            "coinbase", "binance", "altcoin",
            " bnb ", "xrp", "ripple", "dogecoin", " doge ",
            "avalanche", " avax ", "chainlink", "uniswap",
        ]
        seen_ids = set()
        results = []

        for keyword in SEARCH_TERMS:
            params = {
                "active":    "true",
                "closed":    "false",
                "limit":     50,
                "order":     "volume24hr",
                "ascending": "false",
                "keyword":   keyword,
            }
            raw = self._get(f"{GAMMA_BASE}/markets", params=params)
            if not raw:
                continue

            for m in raw:
                mid = str(m.get("id", ""))
                if mid in seen_ids:
                    continue

                liq = float(m.get("liquidity", 0) or 0)
                if liq < min_liquidity:
                    continue

                summary = self._summarize(m)
                if not summary or summary.get("yes_price") is None:
                    continue

                # Client-side filter — confirm question actually mentions crypto
                q_lower = summary["question"].lower()
                if not any(p in q_lower for p in CONFIRM_PATTERNS):
                    continue

                seen_ids.add(mid)
                results.append(summary)

            time.sleep(0.2)  # polite rate limit between keyword searches

        # Sort by vol_liq_ratio (activity) descending
        results.sort(key=lambda x: x.get("vol_liq_ratio", 0), reverse=True)
        return results

    def get_market_by_id(self, market_id: str) -> Optional[dict]:
        """Fetch single market by Polymarket ID."""
        raw = self._get(f"{GAMMA_BASE}/markets/{market_id}")
        if not raw or not isinstance(raw, dict):
            return None
        return self._summarize(raw)

    # ─── Parsing ──────────────────────────────────────────────────────────────

    def _extract_yes_price(self, market: dict) -> Optional[float]:
        """
        Extract YES token price. Polymarket stores it in several places
        depending on market age and type — try all in priority order.
        """
        # 1. tokens[] list with outcome label (most reliable)
        tokens = market.get("tokens") or []
        for token in tokens:
            outcome = str(token.get("outcome", "")).strip().upper()
            if outcome in YES_OUTCOMES:
                price = token.get("price")
                if price is not None:
                    try:
                        p = float(price)
                        if 0.0 < p < 1.0:
                            return round(p, 4)
                    except (ValueError, TypeError):
                        pass

        # 2. outcomePrices — JSON-encoded list ["0.65","0.35"] (YES first)
        op = market.get("outcomePrices")
        if op:
            try:
                prices = json.loads(op) if isinstance(op, str) else op
                if prices and len(prices) >= 1:
                    p = float(prices[0])
                    if 0.0 < p < 1.0:
                        return round(p, 4)
            except (ValueError, TypeError, json.JSONDecodeError):
                pass

        # 3. bestBid/bestAsk midpoint (order book fallback)
        bid = market.get("bestBid")
        ask = market.get("bestAsk")
        if bid is not None and ask is not None:
            try:
                mid = (float(bid) + float(ask)) / 2
                if 0.0 < mid < 1.0:
                    return round(mid, 4)
            except (ValueError, TypeError):
                pass

        return None

    def _is_binary(self, market: dict) -> bool:
        """True only if market has exactly 2 tokens, one of which is YES-type."""
        tokens = market.get("tokens") or []
        if len(tokens) != 2:
            return False
        outcomes = {str(t.get("outcome", "")).strip().upper() for t in tokens}
        return bool(outcomes & YES_OUTCOMES)

    def _check_resolved(self, market: dict) -> Optional[str]:
        """
        Return "YES" or "NO" if market is resolved, else None.
        Bug fix v1: winner field is sometimes the token address, not a string.
        Now checks multiple fields in priority order.
        """
        is_closed   = bool(market.get("closed"))
        is_resolved = bool(market.get("resolved"))

        if not (is_closed or is_resolved):
            return None

        # Field 1: top-level winner string
        winner = market.get("winner")
        if isinstance(winner, str) and winner.upper() in ("YES", "NO", "TRUE", "FALSE"):
            return "YES" if winner.upper() in ("YES", "TRUE") else "NO"

        # Field 2: token-level winner flag
        for token in (market.get("tokens") or []):
            if token.get("winner") is True:
                outcome = str(token.get("outcome", "")).strip().upper()
                if outcome in YES_OUTCOMES:
                    return "YES"
                else:
                    return "NO"

        # Field 3: closed with outcomePrices — resolved YES = [1.0, 0.0]
        op = market.get("outcomePrices")
        if op and is_closed:
            try:
                prices = json.loads(op) if isinstance(op, str) else op
                if prices and float(prices[0]) >= 0.99:
                    return "YES"
                if prices and float(prices[0]) <= 0.01:
                    return "NO"
            except Exception:
                pass

        return None

    def _build_url(self, market: dict) -> str:
        """Build direct Polymarket link. Prefer slug over ID."""
        slug = market.get("slug") or ""
        mid  = market.get("id", "")

        # Event-level slug vs market-level slug differ
        event_slug = (market.get("events") or [{}])[0].get("slug", "") if market.get("events") else ""

        if event_slug:
            return f"{POLY_EVENT}{event_slug}"
        if slug:
            return f"{POLY_MARKET}{slug}"
        return f"{POLY_MARKET}{mid}"

    def _summarize(self, market: dict) -> Optional[dict]:
        """Build clean summary dict from raw Gamma response."""
        if not market or not isinstance(market, dict):
            return None

        yes_price   = self._extract_yes_price(market)
        end_date    = market.get("endDate") or market.get("end_date_iso") or ""
        dtc         = days_until(end_date)
        vol24       = float(market.get("volume24hr", 0) or market.get("volume24h", 0) or 0)
        vol_total   = float(market.get("volume", 0) or 0)
        liq         = float(market.get("liquidity", 0) or 0)

        # Market activity ratio — high ratio = good price discovery
        vol_liq_ratio = round(vol24 / liq, 3) if liq > 0 else 0.0

        # Extract YES token ID for CLOB depth enrichment
        yes_token_id = None
        tokens = market.get("tokens") or []
        for t in tokens:
            outcome = str(t.get("outcome", "")).strip().upper()
            if outcome in ("YES", "TRUE", "1"):
                yes_token_id = t.get("token_id") or t.get("tokenId") or t.get("id")
                break
        if not yes_token_id:
            clob_ids = market.get("clobTokenIds") or []
            if isinstance(clob_ids, str):
                try:
                    clob_ids = json.loads(clob_ids)
                except Exception:
                    clob_ids = []
            if clob_ids:
                yes_token_id = clob_ids[0]

        return {
            "id":             str(market.get("id", "")),
            "question":       str(market.get("question", "Unknown")).strip(),
            "description":    (market.get("description") or "")[:400].strip(),
            "end_date":       end_date,
            "days_to_close":  dtc,
            "yes_price":      yes_price,
            "liquidity":      round(liq, 2),
            "volume":         round(vol_total, 2),
            "volume24h":      round(vol24, 2),
            "vol_liq_ratio":  vol_liq_ratio,   # >0.1 = active market
            "is_binary":      self._is_binary(market),
            "resolved":       self._check_resolved(market),
            "url":            self._build_url(market),
            "yes_token_id":   yes_token_id,     # for CLOB depth enrichment
            "clob_depth":     None,             # filled by enrich_with_clob_depth()
        }

    # ─── Resolution Checker ───────────────────────────────────────────────────

    def check_resolutions(self, market_ids: list) -> dict:
        """
        Batch check resolution status.
        Returns {market_id: "YES" | "NO" | None}
        Adds 0.2s delay between calls to avoid rate limiting.
        """
        results = {}
        for mid in market_ids:
            m = self.get_market_by_id(mid)
            results[mid] = m.get("resolved") if m else None
            time.sleep(0.2)
        return results

    # ─── HTTP Helper ──────────────────────────────────────────────────────────

    def _get(self, url: str, params: dict = None) -> Optional[any]:
        """GET with exponential backoff retry."""
        delay = RETRY_DELAY
        for attempt in range(MAX_RETRIES):
            try:
                r = self.session.get(url, params=params, timeout=20)
                if r.status_code == 429:
                    print(f"[Gamma] Rate limited, waiting {delay}s...")
                    time.sleep(delay)
                    delay *= 2
                    continue
                r.raise_for_status()
                return r.json()
            except requests.exceptions.Timeout:
                print(f"[Gamma] Timeout on attempt {attempt+1}")
                time.sleep(delay)
                delay *= 2
            except requests.exceptions.RequestException as e:
                print(f"[Gamma] Request error: {e}")
                time.sleep(delay)
                delay *= 2
            except Exception as e:
                print(f"[Gamma] Unexpected error: {e}")
                return None

        print(f"[Gamma] All {MAX_RETRIES} attempts failed for {url}")
        return None

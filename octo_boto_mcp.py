"""
octo_boto_mcp.py
Thin wrapper around polymarket-mcp-server tools for use in OctoBoto's pipeline.
Adds orderbook depth and liquidity context to market dicts before AI estimation.

Import: from octo_boto_mcp import enrich_markets_with_orderbook
"""

import asyncio
import sys
import os
import time

# Locate and import from the installed mcp server
try:
    from polymarket_mcp.tools.market_analysis import (
        get_orderbook,
        get_liquidity,
        get_spread,
    )
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False
    print("[OctoMCP] polymarket-mcp not installed — orderbook enrichment disabled.")


# ── Orderbook enrichment ──────────────────────────────────────────────────────

async def _fetch_orderbook_context(market: dict) -> dict:
    """
    Fetch orderbook + liquidity for one market.
    Returns a dict with enrichment data, or empty dict on failure.
    """
    if not MCP_AVAILABLE:
        return {}

    token_id = None
    # Polymarket market tokens — get the YES token
    tokens = market.get("tokens") or market.get("clobTokenIds") or []
    if isinstance(tokens, list) and tokens:
        # tokens is list of dicts or list of strings
        first = tokens[0]
        if isinstance(first, dict):
            token_id = first.get("token_id") or first.get("tokenId")
        else:
            token_id = str(first)

    if not token_id:
        # Try conditionId as fallback key
        token_id = market.get("conditionId") or market.get("condition_id")

    if not token_id:
        return {}

    ctx = {}
    try:
        ob = await get_orderbook(token_id)
        if ob and isinstance(ob, dict):
            bids = ob.get("bids", [])
            asks = ob.get("asks", [])

            # Depth within 5% of mid price
            yes_price = float(market.get("outcomePrices", ["0.5"])[0] or 0.5)
            bid_depth = sum(
                float(b.get("size", 0))
                for b in bids
                if abs(float(b.get("price", 0)) - yes_price) <= 0.05
            )
            ask_depth = sum(
                float(a.get("size", 0))
                for a in asks
                if abs(float(a.get("price", 0)) - yes_price) <= 0.05
            )

            best_bid = float(bids[0]["price"]) if bids else 0
            best_ask = float(asks[0]["price"]) if asks else 0
            spread   = round(best_ask - best_bid, 4) if best_bid and best_ask else None

            ctx["orderbook"] = {
                "bid_depth_usdc":  round(bid_depth, 2),
                "ask_depth_usdc":  round(ask_depth, 2),
                "best_bid":        best_bid,
                "best_ask":        best_ask,
                "spread":          spread,
                "total_depth_usdc": round(bid_depth + ask_depth, 2),
            }
    except Exception as e:
        pass  # Orderbook unavailable for this market — not fatal

    try:
        liq = await get_liquidity(token_id)
        if liq and isinstance(liq, dict):
            ctx["liquidity"] = {
                "total_usdc": liq.get("total") or liq.get("totalLiquidity"),
                "buy_usdc":   liq.get("buy")   or liq.get("buyLiquidity"),
                "sell_usdc":  liq.get("sell")  or liq.get("sellLiquidity"),
            }
    except Exception:
        pass

    return ctx


def enrich_markets_with_orderbook(markets: list, max_markets: int = 20,
                                   min_depth_usdc: float = 200.0,
                                   max_spread: float = 0.08) -> list:
    """
    Synchronously enrich a list of market dicts with orderbook/liquidity data.
    Filters out markets with insufficient depth or excessive spread.

    Args:
        markets:        List of market dicts (already passed is_valid_market filter)
        max_markets:    Cap — only enrich the first N (rate limit protection)
        min_depth_usdc: Drop markets with <$X depth within 5% of current price
        max_spread:     Drop markets with spread wider than this (e.g. 0.08 = 8 cents)

    Returns:
        Filtered list with orderbook context added to each market dict.
    """
    if not MCP_AVAILABLE:
        return markets[:max_markets]

    enriched = []
    skipped_depth = 0
    skipped_spread = 0

    async def _enrich_all(mkt_list):
        results = []
        for m in mkt_list:
            ctx = await _fetch_orderbook_context(m)
            await asyncio.sleep(0.15)  # rate limit
            results.append((m, ctx))
        return results

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # Called from within a running event loop (e.g. Telegram bot).
        # Coroutine must be created AND run inside the worker thread — not passed across threads.
        import concurrent.futures
        mkt_slice = markets[:max_markets]

        def _run_in_thread():
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            try:
                return new_loop.run_until_complete(_enrich_all(mkt_slice))
            finally:
                new_loop.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            pairs = pool.submit(_run_in_thread).result(timeout=60)
    else:
        pairs = asyncio.run(_enrich_all(markets[:max_markets]))

    for market, ctx in pairs:
        ob = ctx.get("orderbook", {})

        # Filter: skip thin markets
        depth = ob.get("total_depth_usdc", 0)
        if depth and depth < min_depth_usdc:
            skipped_depth += 1
            continue

        # Filter: skip wide spread markets (hard to get good fill)
        spread = ob.get("spread")
        if spread and spread > max_spread:
            skipped_spread += 1
            continue

        # Inject context into market dict for AI estimator
        if ctx:
            market["_orderbook_ctx"] = ctx

        enriched.append(market)

    total_skipped = skipped_depth + skipped_spread
    if total_skipped:
        print(f"[OctoMCP] Orderbook filter: -{skipped_depth} thin, -{skipped_spread} wide spread "
              f"→ {len(enriched)}/{len(pairs)} markets pass")

    return enriched


def orderbook_context_str(market: dict) -> str:
    """
    Format orderbook context as a string for injection into AI estimate prompt.
    Returns empty string if no orderbook data available.
    """
    ctx = market.get("_orderbook_ctx", {})
    if not ctx:
        return ""

    lines = ["\nORDERBOOK CONTEXT:"]

    ob = ctx.get("orderbook", {})
    if ob:
        bid_d = float(ob.get("bid_depth_usdc", 0) or 0)
        ask_d = float(ob.get("ask_depth_usdc", 0) or 0)
        total_d = bid_d + ask_d
        lines.append(f"  Depth (within 5% of price): ${total_d:,.0f} USDC")
        lines.append(f"  Bid depth: ${bid_d:,.0f}  |  Ask depth: ${ask_d:,.0f}")

        # Order Flow Imbalance (OBI) — institutional signal.
        # OBI = (bid_vol - ask_vol) / total_vol.
        # Research: OBI > 0.65 predicts price increase within 15-30min at 58% accuracy.
        if total_d > 0:
            obi = (bid_d - ask_d) / total_d
            if obi > 0.65:
                obi_interp = "STRONG BUY PRESSURE — price likely rises 15-30min (58% signal)"
            elif obi > 0.30:
                obi_interp = "Moderate buy pressure"
            elif obi < -0.65:
                obi_interp = "STRONG SELL PRESSURE — price likely falls 15-30min"
            elif obi < -0.30:
                obi_interp = "Moderate sell pressure"
            else:
                obi_interp = "Balanced order flow"
            lines.append(f"  OBI (Order Flow Imbalance): {obi:+.3f} — {obi_interp}")

        spread = ob.get("spread")
        if spread is not None:
            lines.append(f"  Spread: {spread:.4f} ({spread*100:.2f} cents)")
            if spread > 0.05:
                lines.append("  WARNING: Wide spread — expect slippage on entry/exit.")
            elif spread < 0.01:
                lines.append("  Tight spread — efficient market, good fill expected.")

    liq = ctx.get("liquidity", {})
    if liq and liq.get("total_usdc"):
        lines.append(f"  Total liquidity: ${float(liq['total_usdc']):,.0f} USDC")

    return "\n".join(lines)


# ── Quick test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"MCP available: {MCP_AVAILABLE}")
    if MCP_AVAILABLE:
        # Test with a fake market to verify the pipeline
        test_market = {
            "id": "test",
            "question": "Test market",
            "outcomePrices": ["0.45", "0.55"],
        }
        result = enrich_markets_with_orderbook([test_market], max_markets=1)
        print(f"Enrichment result: {result[0].get('_orderbook_ctx', 'no ctx')}")

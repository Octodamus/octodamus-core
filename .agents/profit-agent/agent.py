"""
.agents/profit-agent/agent.py
Profit Agent — autonomous Claude API agent loop.

Uses Claude tool-use to run multi-turn until it reaches a decision/action.
Tools: web search, market data, Octodamus signals, email reporting, wallet check.

Usage:
  python .agents/profit-agent/agent.py           # run a session
  python .agents/profit-agent/agent.py --dry     # print mission, don't run
"""

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT         = Path(__file__).parent.parent.parent
SECRETS_FILE = ROOT / ".octo_secrets"
LOG_FILE     = Path(__file__).parent / "agent_session.log"
STATE_FILE   = Path(__file__).parent / "state.json"

MAX_TURNS      = 20       # safety cap
NOTIFY_EMAIL   = "octodamusai@gmail.com"
FRANKLIN_BIN   = r"C:\Users\walli\AppData\Roaming\npm\franklin.cmd"
START_BALANCE  = 201.00   # initial fund amount for P&L tracking


def _secrets() -> dict:
    try:
        raw = json.loads(SECRETS_FILE.read_text(encoding="utf-8"))
        return raw.get("secrets", raw)
    except Exception:
        return {}


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"sessions": 0, "started_at": datetime.now().isoformat(), "dead": False}


def _save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


# ── Tool implementations ───────────────────────────────────────────────────────

def tool_check_wallet() -> str:
    """Check Franklin USDC wallet balance on Base."""
    try:
        r = subprocess.run(
            f'"{FRANKLIN_BIN}" balance',
            shell=True, capture_output=True, text=True, encoding="utf-8", timeout=30
        )
        output = (r.stdout + r.stderr).strip()
        if output:
            return output
        return "Balance: $0.00 USDC (unfunded or command returned no output)"
    except Exception as e:
        return f"Wallet check failed: {e}"


def tool_audit_wallet(hours_back: int = 48) -> str:
    """
    Read recent Base chain USDC transfers + balances for the Franklin wallet.
    Uses web3.py + public Base RPC — no API key required.
    Scans in 2,000-block chunks to stay within public RPC limits.
    hours_back: how many hours of history to scan (default 48h).
    """
    try:
        from web3 import Web3
    except ImportError:
        return "audit_wallet: web3 not installed. Run: pip install web3"

    address       = "0xAA903A56EE1554DB6973DDEff466f2cD52081FbA"
    usdc_contract = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
    TRANSFER_SIG  = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
    BLOCKS_PER_HOUR = 1800   # Base: ~2 blocks/sec
    CHUNK_SIZE      = 2000   # max safe range for public Base RPC

    w3 = Web3(Web3.HTTPProvider("https://mainnet.base.org"))
    if not w3.is_connected():
        return "audit_wallet: cannot connect to Base RPC"

    addr_checksum = Web3.to_checksum_address(address)
    addr_padded   = "0x" + address.lower()[2:].zfill(64)
    current_block = w3.eth.block_number
    blocks_back   = hours_back * BLOCKS_PER_HOUR
    start_block   = max(0, current_block - blocks_back)

    lines = [
        f"Base wallet audit — {addr_checksum}",
        f"Scanning last {hours_back}h (blocks {start_block:,} to {current_block:,})\n",
    ]

    # Balances
    eth_bal  = w3.eth.get_balance(addr_checksum) / 1e18
    usdc_abi = [{"name": "balanceOf", "type": "function", "stateMutability": "view",
                 "inputs": [{"name": "account", "type": "address"}],
                 "outputs": [{"name": "", "type": "uint256"}]}]
    usdc     = w3.eth.contract(address=Web3.to_checksum_address(usdc_contract), abi=usdc_abi)
    usdc_bal = usdc.functions.balanceOf(addr_checksum).call() / 1e6
    eth_usd  = eth_bal * 2400  # rough estimate for display
    lines.append(f"USDC balance: ${usdc_bal:.2f}")
    lines.append(f"ETH balance:  {eth_bal:.6f} ETH (~${eth_usd:.2f} at ~$2,400/ETH)")
    lines.append(f"Total wallet: ~${usdc_bal + eth_usd:.2f}\n")

    # Scan USDC Transfer logs in chunks
    transfers = []
    chunk_start = start_block
    usdc_addr_cs = Web3.to_checksum_address(usdc_contract)
    while chunk_start <= current_block:
        chunk_end = min(chunk_start + CHUNK_SIZE - 1, current_block)
        for direction, topic1, topic2 in [
            ("IN",  None,        addr_padded),
            ("OUT", addr_padded, None),
        ]:
            try:
                logs = w3.eth.get_logs({
                    "fromBlock": chunk_start, "toBlock": chunk_end,
                    "address": usdc_addr_cs,
                    "topics": [TRANSFER_SIG, topic1, topic2],
                })
                for log in logs:
                    block = w3.eth.get_block(log["blockNumber"])
                    ts    = datetime.fromtimestamp(block["timestamp"]).strftime("%Y-%m-%d %H:%M")
                    amt   = int(log["data"].hex(), 16) / 1e6
                    if direction == "IN":
                        frm = "0x" + log["topics"][1].hex()[-40:]
                        transfers.append((block["timestamp"],
                            f"  IN  +${amt:.2f} USDC  from {frm[:12]}...  {ts}  block:{log['blockNumber']}"))
                    else:
                        to_ = "0x" + log["topics"][2].hex()[-40:]
                        transfers.append((block["timestamp"],
                            f"  OUT -${amt:.2f} USDC  to   {to_[:12]}...  {ts}  block:{log['blockNumber']}"))
            except Exception:
                pass
        chunk_start = chunk_end + 1

    if transfers:
        transfers.sort(key=lambda x: x[0], reverse=True)
        lines.append("── USDC Transfers (newest first) ──")
        for _, line in transfers:
            lines.append(line)
    else:
        lines.append("── USDC Transfers: none found in scan window ──")

    lines.append("\nNote: USDC swapped to ETH = USDC OUT + ETH balance rising at same block.")
    lines.append("The $24 gap (2026-05-02) = USDC swapped to ETH. Confirmed: ETH balance holds that value.")
    return "\n".join(lines)


def tool_web_search(query: str, num_results: int = 5) -> str:
    """Search the web via Firecrawl."""
    try:
        sys.path.insert(0, str(ROOT))
        from octo_firecrawl import search_web
        results = search_web(query, num_results=min(num_results, 8), cache_hours=1.0)
        if not results:
            return f"No results for: {query}"
        lines = [f"Search: {query}\n"]
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r.get('title','')}")
            if r.get("description"):
                lines.append(f"   {r['description'][:200]}")
            if r.get("url"):
                lines.append(f"   {r['url']}")
        return "\n".join(lines)
    except Exception as e:
        return f"Search failed: {e}"


def tool_browse_url(url: str) -> str:
    """Scrape a URL and return its content as markdown."""
    try:
        sys.path.insert(0, str(ROOT))
        from octo_firecrawl import scrape_url
        result = scrape_url(url)
        if not result:
            return f"Could not scrape {url}"
        # scrape_url returns a string directly
        if isinstance(result, str):
            return f"URL: {url}\n\n{result[:4000]}"
        # or a dict with markdown key
        if isinstance(result, dict):
            content = result.get("markdown") or result.get("content") or str(result)
            return f"URL: {url}\n\n{content[:4000]}"
        return f"Could not scrape {url}"
    except Exception as e:
        return f"Browse failed: {e}"


def tool_get_market_data(asset: str = "ALL") -> str:
    """Get live price, 24h change, Fear & Greed, SPY (S&P500 proxy), and DXY. Pass 'ALL' (default) for full picture."""
    try:
        sys.path.insert(0, str(ROOT))
        from financial_data_client import get_crypto_prices, get_current_price
        asset = asset.upper()
        prices = get_crypto_prices(["BTC","ETH","SOL"] if asset in ("ALL","") else
                                   [asset] if asset in ("BTC","ETH","SOL") else ["BTC","ETH","SOL"])
        lines = ["Live market data:"]
        for t, d in prices.items():
            price_val = d.get('usd', 0)
            lines.append(f"  {t}: ${price_val:,.2f} ({d.get('usd_24h_change',0):+.2f}% 24h)")
            if t == "BTC":
                _session_market_cache["btc"] = f"{price_val:,.0f}"
        # Equity + DXY via yfinance (only on ALL fetch)
        if asset in ("ALL", ""):
            try:
                spy = get_current_price("SPY")
                spy_s = spy.get("snapshot", {})
                if spy_s.get("price", 0) > 0:
                    lines.append(f"  SPY (S&P500): ${spy_s['price']:,.2f} ({spy_s.get('day_change_percent', 0):+.2f}% day)")
            except Exception:
                pass
            try:
                dxy = get_current_price("DX-Y.NYB")
                dxy_s = dxy.get("snapshot", {})
                if dxy_s.get("price", 0) > 0:
                    lines.append(f"  DXY: {dxy_s['price']:.2f} ({dxy_s.get('day_change_percent', 0):+.2f}% day)")
            except Exception:
                pass
        # Fear & Greed
        try:
            import httpx
            fg = httpx.get("https://api.alternative.me/fng/?limit=1", timeout=6).json()
            val = fg["data"][0]["value"]
            label = fg["data"][0]["value_classification"]
            lines.append(f"  Fear & Greed: {val}/100 ({label})")
            _session_market_cache["fg"] = val
        except Exception:
            pass
        return "\n".join(lines)
    except Exception as e:
        return f"Market data failed: {e}"


def tool_get_grok_sentiment(asset: str = "BTC") -> str:
    """Get real-time X/Twitter social sentiment via Grok's live data. Fast read of what traders are saying right now."""
    try:
        sys.path.insert(0, str(ROOT))
        from octo_grok_sentiment import get_grok_sentiment
        result = get_grok_sentiment(asset.upper(), force=True)
        if result.get("confidence", 0) == 0:
            return f"Grok sentiment unavailable for {asset}: {result.get('summary','')}"
        return (
            f"X Sentiment for {asset} (Grok real-time):\n"
            f"  Signal:     {result['signal']} ({result['confidence']:.0%} confidence)\n"
            f"  Summary:    {result.get('summary','')}\n"
            f"  Crowd:      {result.get('crowd_pos','?')}\n"
            f"  Themes:     {', '.join(result.get('key_themes',[]))}"
        )
    except Exception as e:
        return f"Grok sentiment failed: {e}"


def tool_get_octodamus_signal() -> str:
    """Get current Octodamus oracle signal — tries live API with Ben's key first, falls back to local data."""
    try:
        import httpx as _hx, json as _json
        api_key = _secrets().get("BEN_OCTODATA_API_KEY", "")
        if api_key:
            r = _hx.get("https://api.octodamus.com/v2/signal",
                        headers={"X-OctoData-Key": api_key}, timeout=10)
            if r.status_code == 200:
                d      = r.json()
                sig    = d.get("signal") or {}
                poly   = (d.get("polymarket") or {}).get("top_play") or {}
                record = d.get("track_record") or {}
                lines  = ["Octodamus Oracle Signal (premium — live API):"]
                if sig:
                    lines += [
                        f"  Signal:    {sig.get('signal','?')} | Confidence: {sig.get('confidence','?')}",
                        f"  Reasoning: {sig.get('reasoning','N/A')[:200]}",
                    ]
                else:
                    lines.append(f"  Signal:    {d.get('message','No active signal')} (need 9/11 consensus)")
                lines.append(f"  Record:    {record.get('wins',0)}W / {record.get('losses',0)}L (oracle calls only)")
                if poly.get("question"):
                    lines.append(f"  Top Poly:  {poly['question'][:80]} | {poly.get('side','?')} @ {poly.get('entry_price','?')}")
                return "\n".join(lines)
    except Exception:
        pass

    # Fallback: local data (oracle calls only — excludes OctoBoto paper trades)
    try:
        import json as _json
        calls_file = ROOT / "data" / "octo_calls.json"
        calls = _json.loads(calls_file.read_text(encoding="utf-8")) if calls_file.exists() else []
        calls = [c for c in calls if c.get("call_type", "oracle") != "polymarket"]
        open_calls = [c for c in calls if not c.get("resolved")]
        resolved   = [c for c in calls if c.get("resolved")]
        wins   = sum(1 for c in resolved if c.get("outcome") == "WIN")
        losses = sum(1 for c in resolved if c.get("outcome") == "LOSS")
        lines = ["Octodamus Oracle Signals (local data — oracle calls only):"]
        if open_calls:
            lines.append(f"  Open calls ({len(open_calls)}):")
            for c in open_calls[:5]:
                lines.append(f"    {c.get('asset')} {c.get('direction')} | entry ${c.get('entry_price',0):,.0f} | tf {c.get('timeframe')} | edge {c.get('edge_score',0):+.2f}")
        else:
            lines.append("  No open calls right now.")
        lines.append(f"  All-time record: {wins}W / {losses}L")
        lines.append(f"  Note: OctoBoto paper trades tracked separately")
        return "\n".join(lines)
    except Exception as e:
        return f"Signal fetch failed: {e}"


def _cross_market_arb_check(markets: list) -> list:
    """
    Find logical dependency violations across related Polymarket markets.

    Type 1 — Strike monotonicity:
      P(BTC > $75k) must be >= P(BTC > $80k). Violation = pure arb, no directional view needed.

    Type 2 — YES+NO mismatch:
      |YES + NO - 1.0| > 0.05 within a single market = pricing error.

    Returns list of arb dicts sorted by estimated profit capacity (highest first).
    Capacity = price_gap × min(vol_leg1, vol_leg2)  [bottleneck principle]
    """
    import re as _re
    arbs = []

    def _parse_prices(m):
        prices = m.get("outcomePrices", [])
        if isinstance(prices, str):
            try:
                import json as _j; prices = _j.loads(prices)
            except Exception:
                return None, None
        try:
            yes_p = float(prices[0])
            no_p  = float(prices[1]) if len(prices) > 1 else None
            return yes_p, no_p
        except (ValueError, IndexError):
            return None, None

    # ── Type 1: Strike price monotonicity ────────────────────────────────────
    strike_groups = {}
    for m in markets:
        q     = m.get("question", "") or ""
        yes_p, _ = _parse_prices(m)
        if yes_p is None:
            continue
        vol = float(m.get("volume", 0) or 0)
        cid = m.get("conditionId", "")
        exp = (m.get("endDateIso") or "")[:10]
        q_l = q.lower()

        asset = None
        for a, aliases in [("BTC", ["bitcoin", "btc"]), ("ETH", ["ethereum", "eth"]), ("SOL", ["solana", "sol"])]:
            if any(al in q_l for al in aliases):
                asset = a; break
        if not asset:
            continue

        direction = None
        if any(w in q_l for w in ["above", "over", "exceed", "reach", "surpass", "break", "hit"]):
            direction = "above"
        elif any(w in q_l for w in ["below", "under", "drop below", "fall below"]):
            direction = "below"
        if not direction:
            continue

        threshold = None
        m_k = _re.search(r'\$(\d+)k\b', q_l)
        if m_k:
            threshold = float(m_k.group(1)) * 1000
        else:
            m_d = _re.search(r'\$([\d,]+)', q)
            if m_d:
                try: threshold = float(m_d.group(1).replace(",", ""))
                except ValueError: pass
        if not threshold:
            continue

        key = f"{asset}_{direction}_{exp}"
        strike_groups.setdefault(key, []).append({
            "threshold": threshold, "yes_p": yes_p,
            "vol": vol, "cid": cid, "question": q[:70], "exp": exp,
        })

    for key, entries in strike_groups.items():
        if len(entries) < 2:
            continue
        entries.sort(key=lambda x: x["threshold"])
        asset, direction, exp = key.split("_", 2)

        for i in range(len(entries) - 1):
            lo, hi = entries[i], entries[i + 1]
            if direction == "above":
                # P(asset > lo_thresh) >= P(asset > hi_thresh) must hold
                gap = hi["yes_p"] - lo["yes_p"]   # positive = violation
            else:
                # P(asset < hi_thresh) >= P(asset < lo_thresh) must hold
                gap = lo["yes_p"] - hi["yes_p"]   # positive = violation
            if gap > 0.02:
                capacity = round(gap * min(lo["vol"], hi["vol"]), 2)
                arbs.append({
                    "type": f"monotonicity_{direction}", "asset": asset,
                    "gap": round(gap, 4), "capacity": capacity,
                    "lo": lo, "hi": hi, "exp": exp,
                })

    # ── Type 2: YES+NO mismatch within a single market ───────────────────────
    for m in markets:
        yes_p, no_p = _parse_prices(m)
        if yes_p is None or no_p is None:
            continue
        mismatch = abs(yes_p + no_p - 1.0)
        if mismatch > 0.05:
            vol = float(m.get("volume", 0) or 0)
            arbs.append({
                "type": "yes_no_mismatch", "gap": round(mismatch, 4),
                "capacity": round(mismatch * vol, 2),
                "yes_p": yes_p, "no_p": no_p,
                "cid": m.get("conditionId", ""),
                "question": (m.get("question") or "")[:70],
            })

    arbs.sort(key=lambda x: x.get("capacity", 0), reverse=True)
    return arbs


def tool_get_polymarket_edges() -> str:
    """Get current Polymarket markets and prices for edge hunting. Pre-filtered for crypto/macro only. Includes conditionId for paper trading. Runs cross-market arb check for logical dependency violations."""
    from datetime import datetime
    # NOTE: Polymarket has crypto/macro markets 24/7 including weekends — no skip gate here.
    # Only show markets where Ben has a data edge. Sports/entertainment are auto-excluded.
    _EDGE_KEYWORDS = [
        "bitcoin", "btc", "ethereum", "eth", "solana", "sol", "crypto", "altcoin",
        "coinbase", "binance", "bnb", "xrp", "doge", "100k", "200k", "50k", "80k",
        "price", "all-time high", "ath", "etf", "spot etf", "liquidat", "funding",
        "cpi", "inflation", "fed rate", "federal reserve", "fomc", "interest rate",
        "rate cut", "rate hike", "gdp", "recession", "unemployment", "jobs report",
        "nonfarm", "treasury", "yield", "s&p", "sp500", "nasdaq", "dow", "vix",
        "oil", "crude", "gold", "silver", "trump", "tariff", "trade war", "sec",
        "election", "approval", "congress", "senate", "debt ceiling",
    ]
    try:
        import httpx
        from datetime import datetime, timezone
        # Fetch more than needed so we have room to filter
        r = httpx.get(
            "https://gamma-api.polymarket.com/markets",
            params={"active": True, "closed": False, "limit": 250,
                    "order": "volume", "ascending": False},
            timeout=10
        )
        if r.status_code == 200:
            markets = r.json()
            now = datetime.now(timezone.utc)

            # Pre-filter: only crypto/macro markets where Ben has a data edge
            edge_markets = []
            for m in markets:
                text = " ".join([
                    m.get("question", "") or "",
                    m.get("description", "") or "",
                    m.get("title", "") or "",
                    m.get("slug", "") or "",
                ]).lower()
                if any(kw in text for kw in _EDGE_KEYWORDS):
                    edge_markets.append(m)

            arbs = _cross_market_arb_check(edge_markets)

            lines = [f"Polymarket crypto/macro edge markets (filtered from top 100 by volume):"]
            if arbs:
                lines.append(f"\n!! CROSS-MARKET ARB ({len(arbs)} found):")
                for arb in arbs:
                    arb_type = arb.get("type", "")
                    if arb_type.startswith("monotonicity"):
                        lo = arb["lo"]
                        hi = arb["hi"]
                        lines.append(
                            f"  !! ARB ({arb_type}): {arb['asset']} | gap={arb['gap']:.3f} | "
                            f"capacity=${arb['capacity']:,.0f} | exp={arb['exp']}"
                        )
                        lines.append(
                            f"     BUY YES: ${lo['threshold']:,.0f} strike "
                            f"(conditionId={lo['cid']}, YES={lo['yes_p']:.3f}, vol=${lo['vol']:,.0f})"
                        )
                        lines.append(
                            f"     SELL YES via NO: ${hi['threshold']:,.0f} strike "
                            f"(conditionId={hi['cid']}, YES={hi['yes_p']:.3f}, vol=${hi['vol']:,.0f})"
                        )
                        lines.append("     Non-atomic risk: fill both legs simultaneously or skip")
                    elif arb_type == "yes_no_mismatch":
                        lines.append(
                            f"  !! ARB (yes_no_mismatch): gap={arb['gap']:.3f} | capacity=${arb['capacity']:,.0f}"
                        )
                        lines.append(
                            f"     conditionId={arb['cid']} | YES={arb['yes_p']:.3f} NO={arb['no_p']:.3f} "
                            f"(sum={arb['yes_p'] + arb['no_p']:.3f})"
                        )
                        lines.append(f"     {arb['question']}")
                        lines.append("     BUY underpriced side, target convergence to 1.0")
                lines.append("")
            if not edge_markets:
                lines.append("  No crypto/macro markets in top 250 by volume. Try scan_kalshi for KXBTC/KXETH/KXFED.")
                lines.append("  (Sports/gaming dominated this scan — all filtered out.)")
            else:
                high_vol = [m for m in edge_markets if float(m.get("volume", 0) or 0) >= 5000]
                low_vol  = [m for m in edge_markets if float(m.get("volume", 0) or 0) < 5000]
                if not high_vol:
                    lines.append(f"  NOTE: {len(edge_markets)} crypto/macro market(s) found but ALL below $5k volume threshold (condition 3 fails).")
                for m in edge_markets[:12]:
                    q      = m.get("question", "")[:75]
                    prices = m.get("outcomePrices", [])
                    if isinstance(prices, str):
                        try:
                            import json as _j; prices = _j.loads(prices)
                        except Exception:
                            prices = []
                    yes    = prices[0] if prices else "?"
                    vol    = float(m.get("volume", 0) or 0)
                    cid    = m.get("conditionId", "")
                    exp    = m.get("endDateIso", "")[:10]
                    hours_left = ""
                    vol_flag = "" if vol >= 5000 else " [LOW VOL]"
                    try:
                        if m.get("endDateIso"):
                            exp_dt = datetime.fromisoformat(m["endDateIso"].replace("Z", "+00:00"))
                            h = (exp_dt - now).total_seconds() / 3600
                            hours_left = f" | {h:.0f}h left"
                    except Exception:
                        pass
                    lines.append(
                        f"  conditionId={cid} | YES={yes} | Vol=${vol:,.0f}{vol_flag}"
                        f"{hours_left} | exp={exp} | {q}"
                    )
            lines.append("\nUse paper_trade_polymarket(condition_id, side, size_usdc, price) to paper trade.")
            return "\n".join(lines)
        return f"Polymarket API returned {r.status_code}"
    except Exception as e:
        return f"Polymarket edges failed: {e}"


def tool_search_x402_bazaar(query: str) -> str:
    """Search the agentic.market/x402 bazaar for paid AI agent services."""
    try:
        import httpx
        r = httpx.get(
            "https://agentic.market/v1/services/search",
            params={"q": query}, timeout=10,
        )
        if r.status_code == 200:
            items = r.json() if isinstance(r.json(), list) else r.json().get("services", [])
            if not items:
                return f"No services found for: {query}"
            lines = [f"Agentic services for '{query}':"]
            for s in items[:12]:
                lines.append(f"  {s.get('name','?')} | {s.get('price','?')} | {s.get('description','')[:80]}")
            return "\n".join(lines)
        return f"Bazaar search: {r.status_code} for '{query}'"
    except Exception as e:
        return f"Bazaar search failed: {e}"


def tool_check_agentic_market(category: str = "trading") -> str:
    """Browse agentic.market for paid services other agents are buying. Find gaps to fill."""
    try:
        import httpx
        r = httpx.get(
            f"https://agentic.market/v1/services",
            params={"category": category} if category != "all" else {},
            timeout=10
        )
        if r.status_code == 200:
            services = r.json()
            lines = [f"Agentic.market services ({category}):"]
            items = services if isinstance(services, list) else services.get("services", [])
            for s in items[:15]:
                name  = s.get("name", "?")
                desc  = s.get("description", "")[:80]
                price = s.get("price", "?")
                lines.append(f"  {name} | {price} | {desc}")
            return "\n".join(lines)
        return f"Agentic.market returned {r.status_code}"
    except Exception as e:
        return f"Agentic market check failed: {e}"


def tool_buy_octodamus_signal() -> str:
    """
    Buy the full Octodamus oracle signal for $0.01 USDC via x402 EIP-3009.
    Returns the complete signal with confidence, reasoning, and all asset calls.
    Uses Ben's Franklin wallet — fully autonomous, no human needed.
    """
    return tool_buy_x402_service("https://api.octodamus.com/v2/x402/agent-signal", max_price_usdc=0.05)


_LIMITLESS_SUSPEND_THRESHOLD = 20   # kept for reference, not used


def tool_scan_limitless(category: str = "crypto") -> str:
    """Limitless permanently suspended by owner decision 2026-05-06. Polymarket-only going forward."""
    return (
        "LIMITLESS PERMANENTLY SUSPENDED -- owner decision 2026-05-06. "
        "25 consecutive sessions with zero qualifying crypto markets. Platform is structurally thin on crypto. "
        "Do NOT call this tool. Go directly to Polymarket scan."
    )

    def _duration(m: dict) -> str:
        """Detect market duration from slug/title. Returns '4h','1h','15m','5m', or ''."""
        text = ((m.get("slug") or "") + " " + (m.get("title") or "")).lower()
        if "4h" in text or "4hr" in text or "4 hour" in text:
            return "4h"
        if "1h" in text or "1hr" in text or "1 hour" in text:
            return "1h"
        if "15m" in text or "15min" in text or "15 min" in text:
            return "15m"
        if "5m" in text or "5min" in text or "5 min" in text:
            return "5m"
        return ""

    _VALID_DURATIONS = {"4h", "1h", "15m", "5m"}
    _CRYPTO_KWS = ["btc", "bitcoin", "eth", "ethereum", "sol", "solana",
                   "bnb", "xrp", "doge", "avax", "price", "above", "below"]

    try:
        import httpx, re as _re
        from datetime import datetime, timezone

        r = httpx.get("https://api.limitless.exchange/markets/active", timeout=10)
        if r.status_code != 200:
            return f"Limitless API returned {r.status_code}: {r.text[:200]}"

        all_markets = r.json().get("data", [])
        now = datetime.now(timezone.utc)

        # Only keep markets with a recognised Limitless duration (4h / 1h / 15m / 5m)
        duration_markets = [m for m in all_markets if _duration(m) in _VALID_DURATIONS]

        # Apply category filter
        if category.lower() == "crypto":
            filtered = [m for m in duration_markets if any(
                kw in ((m.get("title") or "") + (m.get("slug") or "")).lower()
                for kw in _CRYPTO_KWS
            )]
        elif category.lower() in ("all", ""):
            filtered = duration_markets
        else:
            filtered = [m for m in duration_markets
                        if category.lower() in ((m.get("title") or "") + (m.get("slug") or "")).lower()]

        if filtered:
            # Reset consecutive-zero streak — real markets found
            _st["limitless_zero_streak"] = 0
            _save_state(_st)

            # Group by duration for clarity
            by_dur: dict = {"4h": [], "1h": [], "15m": [], "5m": []}
            for m in filtered:
                by_dur[_duration(m)].append(m)

            lines = [f"Limitless {category} markets (4h/1h/15m/5m only) — Base-native, USDC:"]
            for dur in ("4h", "1h", "15m", "5m"):
                group = by_dur[dur]
                if not group:
                    continue
                lines.append(f"\n  [{dur}]")
                for m in group[:6]:
                    slug   = m.get("slug", "")
                    title  = (m.get("title") or slug)[:65]
                    prices = m.get("prices", [])
                    yes    = prices[0] if isinstance(prices, list) and prices else "?"
                    vol    = float(m.get("volume") or m.get("collateralVolume") or 0)
                    exp_str = m.get("expirationDate", "")
                    mins_left = ""
                    try:
                        if exp_str:
                            exp_dt = datetime.fromisoformat(exp_str.replace("Z", "+00:00"))
                            mins_left = f" | {(exp_dt - now).total_seconds()/60:.0f}min left"
                    except Exception:
                        pass
                    lines.append(f"    slug={slug} | YES={yes} | Vol=${vol:,.0f}{mins_left} | {title}")
            return "\n".join(lines)

        # Zero qualifying markets — increment streak
        _st["limitless_zero_streak"] = zero_streak + 1
        _save_state(_st)
        new_streak = zero_streak + 1

        total_raw       = len(all_markets)
        total_dur       = len(duration_markets)
        lines = [
            f"No active {category} Limitless markets found.",
            f"  Exchange total: {total_raw} | With valid duration (4h/1h/15m/5m): {total_dur} | After crypto filter: 0",
        ]
        if total_dur > 0:
            lines.append(f"  Non-crypto markets available (category='all' to see them):")
            for m in duration_markets[:4]:
                lines.append(f"    [{_duration(m)}] {(m.get('title') or m.get('slug',''))[:55]}")
        elif total_raw > 0:
            lines.append(f"  DIAGNOSIS: {total_raw} markets on exchange but none match 4h/1h/15m/5m durations.")
            lines.append(f"  These are likely near-expiry short markets — no tradeable window remaining.")
        if new_streak >= 10:
            lines.append(f"  STRUCTURAL FLAG: {new_streak} consecutive zero-crypto sessions.")
            if new_streak >= _LIMITLESS_SUSPEND_THRESHOLD:
                lines.append(f"  AUTO-SUSPEND triggers next session. Use Polymarket as primary.")
            else:
                lines.append(f"  ({_LIMITLESS_SUSPEND_THRESHOLD - new_streak} sessions until auto-suspend)")
        return "\n".join(lines)

    except Exception as e:
        return f"Limitless scan failed: {e}"


def _limitless_headers(token_id: str, secret_b64: str, method: str, path: str, body: str = "") -> dict:
    """Build HMAC-SHA256 signed headers for Limitless API."""
    import hmac as _hmac, hashlib, base64
    from datetime import datetime, timezone
    timestamp = datetime.now(timezone.utc).isoformat()
    message   = f"{timestamp}\n{method}\n{path}\n{body}"
    signature = base64.b64encode(
        _hmac.new(base64.b64decode(secret_b64), message.encode("utf-8"), hashlib.sha256).digest()
    ).decode("utf-8")
    return {
        "lmts-api-key":    token_id,
        "lmts-timestamp":  timestamp,
        "lmts-signature":  signature,
        "Content-Type":    "application/json",
    }


_PAPER_MODE    = True   # Set False only after paper trades confirm integration works
_MIN_EXPIRY_H  = 2      # Hard block: never bet on markets expiring within this many hours


def tool_place_limitless_bet(market_slug: str, side: str, size_usdc: float, price: float = 0.5) -> str:
    """
    Place a bet on Limitless Exchange. PAPER MODE is ON by default.
    Hard block: markets expiring within 2h are REFUSED at execution time — no exceptions.

    side: 'YES' or 'NO'. Max $40 USDC. price: 0.01-0.99 (current YES price).
    """
    if size_usdc > 40:
        return "BLOCKED: Max $40 USDC per position."
    if side.upper() not in ("YES", "NO"):
        return "BLOCKED: side must be YES or NO."
    if not (0.01 <= price <= 0.99):
        return "BLOCKED: price must be 0.01-0.99."

    # Sports hard-block — market_slug often contains the question; also checked at scan time
    if _is_sports_market(market_slug):
        return (
            f"HARD BLOCKED: '{market_slug[:60]}' looks like a sports market. "
            "Ben only trades markets where he has a data edge: crypto prices, macro, geopolitical binaries. "
            "No sports. Skip."
        )

    s = _secrets()
    token_id   = s.get("LIMITLESS_API_KEY", "")
    secret_b64 = s.get("LIMITLESS_API_SECRET", "")
    wallet_key = s.get("FRANKLIN_PRIVATE_KEY", "")

    if not token_id or not secret_b64:
        return (
            "LIMITLESS_API_KEY / LIMITLESS_API_SECRET not configured.\n\n"
            "Setup steps:\n"
            "1. Go to limitless.exchange → create account\n"
            "2. Profile → API Keys → generate key pair (token ID + secret)\n"
            "3. In Bitwarden, open 'AGENT - Octodamus - Limitless API':\n"
            "   username = token ID\n"
            "   password = secret (base64)\n"
            "4. Run octo_unlock.ps1 to reload secrets\n"
            "Cannot place bet until both keys are configured."
        )
    if not wallet_key:
        return "FRANKLIN_PRIVATE_KEY not in secrets. Run octo_unlock.ps1."

    try:
        import httpx, json as _j, random, time
        from datetime import datetime, timezone, timedelta
        from eth_account import Account

        # Hard expiry gate — fetch market and check expiry BEFORE anything else
        r_check = httpx.get(f"https://api.limitless.exchange/markets/{market_slug}", timeout=10)
        if r_check.status_code == 200:
            market_data = r_check.json()
            exp_str = market_data.get("expirationDate") or market_data.get("expirationTimestamp", "")
            if exp_str:
                try:
                    exp_dt  = datetime.fromisoformat(exp_str.replace("Z", "+00:00"))
                    hours_left = (exp_dt - datetime.now(timezone.utc)).total_seconds() / 3600
                    if hours_left < _MIN_EXPIRY_H:
                        return (
                            f"HARD BLOCKED: Market '{market_slug}' expires in {hours_left:.1f}h "
                            f"(minimum {_MIN_EXPIRY_H}h required). Near-expiry markets lock "
                            f"before execution — always give at least 2h runway. "
                            f"Limitless max is 4h — find a different market or PASS."
                        )
                except Exception:
                    pass  # If we can't parse expiry, proceed cautiously
        from eth_account.messages import encode_typed_data

        account = Account.from_key(wallet_key)

        # Step 1: Fetch market to get positionIds and exchange address
        path = f"/markets/{market_slug}"
        hdrs = _limitless_headers(token_id, secret_b64, "GET", path)
        r = httpx.get(f"https://api.limitless.exchange{path}", headers=hdrs, timeout=10)
        if r.status_code != 200:
            return f"Market fetch failed ({r.status_code}): {r.text[:200]}"
        market = r.json()

        position_ids  = market.get("positionIds", [])
        exchange_addr = market.get("venue", {}).get("exchange") or market.get("exchange", "")
        owner_id      = market.get("ownerId") or market.get("owner", {}).get("id")

        if len(position_ids) < 2:
            return f"Market has no tradeable positions: {_j.dumps(market)[:300]}"

        token_id_order = str(position_ids[0] if side.upper() == "YES" else position_ids[1])
        maker_amount   = int(price * size_usdc * 1_000_000)       # price × size × 1e6
        taker_amount   = int(size_usdc * 1_000_000)               # size × 1e6
        salt           = random.randint(1, 2**32)

        # Step 2: EIP-712 order signing
        order_data = {
            "salt":         salt,
            "maker":        account.address,
            "signer":       account.address,
            "tokenId":      token_id_order,
            "makerAmount":  maker_amount,
            "takerAmount":  taker_amount,
            "feeRateBps":   0,
            "side":         0,   # 0 = BUY
            "signatureType": 2,
        }

        typed_data = {
            "types": {
                "EIP712Domain": [
                    {"name": "name",    "type": "string"},
                    {"name": "version", "type": "string"},
                    {"name": "chainId", "type": "uint256"},
                    {"name": "verifyingContract", "type": "address"},
                ],
                "Order": [
                    {"name": "salt",         "type": "uint256"},
                    {"name": "maker",        "type": "address"},
                    {"name": "signer",       "type": "address"},
                    {"name": "tokenId",      "type": "uint256"},
                    {"name": "makerAmount",  "type": "uint256"},
                    {"name": "takerAmount",  "type": "uint256"},
                    {"name": "feeRateBps",   "type": "uint256"},
                    {"name": "side",         "type": "uint256"},
                    {"name": "signatureType","type": "uint256"},
                ],
            },
            "domain": {
                "name":               "Limitless Exchange",
                "version":            "1",
                "chainId":            8453,  # Base mainnet
                "verifyingContract":  exchange_addr,
            },
            "primaryType": "Order",
            "message": {
                **order_data,
                "tokenId": int(token_id_order),
            },
        }

        signed   = account.sign_typed_data(
            domain_data   = typed_data["domain"],
            message_types = {"Order": typed_data["types"]["Order"]},
            message_data  = typed_data["message"],
        )
        signature = signed.signature.hex()

        # Build payload (same whether paper or live)
        payload = _j.dumps({
            "order": {**order_data, "signature": signature},
            "ownerId":    owner_id,
            "orderType":  "FOK",
            "marketSlug": market_slug,
        }, separators=(",", ":"))

        # ── PAPER MODE ────────────────────────────────────────────────
        if _PAPER_MODE:
            log_entry = {
                "paper":      True,
                "market":     market_slug, "side": side,
                "size_usdc":  size_usdc,   "price": price,
                "ev_implied": round((1 / price - 1) * 100, 1),
                "placed_at":  time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
                "wallet":     account.address,
                "payload_preview": payload[:200],
            }
            trades_file = Path(__file__).parent / "limitless_trades.json"
            existing = _j.loads(trades_file.read_text()) if trades_file.exists() else []
            existing.append(log_entry)
            trades_file.write_text(_j.dumps(existing, indent=2))
            return (
                f"[PAPER TRADE] {side} ${size_usdc:.2f} @ {price} on {market_slug}\n"
                f"Implied EV: +{log_entry['ev_implied']}% if correct\n"
                f"Signing worked. Payload built. Order NOT submitted (paper mode).\n"
                f"Review limitless_trades.json. When satisfied, set _PAPER_MODE = False."
            )

        # ── LIVE MODE ─────────────────────────────────────────────────
        path2 = "/orders"
        hdrs2 = _limitless_headers(token_id, secret_b64, "POST", path2, payload)
        r2 = httpx.post(f"https://api.limitless.exchange{path2}", headers=hdrs2, content=payload.encode(), timeout=15)

        log_entry = {
            "paper": False,
            "market": market_slug, "side": side, "size_usdc": size_usdc, "price": price,
            "placed_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
            "status": r2.status_code, "response": r2.text[:300],
        }
        trades_file = Path(__file__).parent / "limitless_trades.json"
        existing = _j.loads(trades_file.read_text()) if trades_file.exists() else []
        existing.append(log_entry)
        trades_file.write_text(_j.dumps(existing, indent=2))

        if r2.status_code in (200, 201):
            return f"BET PLACED (LIVE): {side} ${size_usdc:.2f} @ {price} on {market_slug}\n{r2.text[:300]}"
        else:
            return f"Order rejected ({r2.status_code}): {r2.text[:300]}"

    except Exception as e:
        return f"Bet placement failed: {type(e).__name__}: {e}"


_BEN_POLY_TRADES = Path(__file__).parent / "polymarket_trades.json"
_BEN_MIN_POLY_EXPIRY_H = 24   # Polymarket only: markets resolve over days, need >24h runway

# ── Sports hard-block ──────────────────────────────────────────────────────────
# Ben has zero data advantage on sports outcomes. Every sports bet is pure noise.
_SPORTS_KEYWORDS = frozenset([
    "tennis", "grand prix", "ipl", "premier league",
    "super kings", "punjab kings", "royals", "twins", "yankees", "dodgers",
    "lakers", "celtics", "warriors", "nuggets", "heat", "knicks",
    "nfl", "nba", "mlb", "nhl", "epl", "mls",
    "champions league", "world cup", "copa ", "bucharest open", "roland garros",
    "wimbledon", "roland-garros", "atp ", "wta ", "itf ",
    "cricket", "rugby", "golf", "formula 1", "f1 ",
    "super bowl", "playoffs", "championship", "league cup",
])

def _is_sports_market(question: str) -> bool:
    q = question.lower()
    for kw in _SPORTS_KEYWORDS:
        if kw in q:
            return True
    # Pattern: "Firstname Lastname vs Firstname Lastname" = person vs person = sports match
    import re as _re
    if _re.search(r'[A-Z][a-z]+ [A-Z][a-z]+\s+vs\.?\s+[A-Z][a-z]+ [A-Z][a-z]+', question):
        return True
    return False


# ── Theme cooldown ─────────────────────────────────────────────────────────────
_THEME_COOLDOWN_DAYS = 7
_THEME_STOP_WORDS = frozenset([
    "will", "the", "a", "an", "by", "in", "at", "of", "to", "for", "on",
    "or", "and", "is", "be", "hit", "reach", "end", "april", "may", "june",
    "july", "august", "march", "2026", "2025", "2027", "not", "no", "yes",
])

def _theme_keywords(question: str) -> set:
    import re as _re
    words = _re.findall(r"\b[a-zA-Z]{4,}\b", question.lower())
    return {w for w in words if w not in _THEME_STOP_WORDS}

def _check_theme_cooldown(question: str) -> str | None:
    """
    Return a BLOCKED string if this question overlaps with a recent loss in
    Ben's own trade log. 2+ shared keywords = same theme = cooldown applies.
    """
    if not _BEN_POLY_TRADES.exists():
        return None
    import json as _jc
    from datetime import datetime, timezone, timedelta
    try:
        trades = _jc.loads(_BEN_POLY_TRADES.read_text(encoding="utf-8"))
    except Exception:
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(days=_THEME_COOLDOWN_DAYS)
    new_kw = _theme_keywords(question)
    for t in trades:
        # Only check confirmed losses
        pnl = t.get("pnl", None)
        outcome = t.get("outcome", "")
        if pnl is None:
            if outcome not in ("loss", "LOSS"):
                continue
        elif float(pnl) >= 0:
            continue
        placed = t.get("placed_at", "")
        try:
            dt = datetime.strptime(placed, "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)
            if dt < cutoff:
                continue
        except Exception:
            continue
        old_kw  = _theme_keywords(t.get("question", ""))
        overlap = new_kw & old_kw
        if len(overlap) >= 2:
            return (
                f"THEME COOLDOWN ({_THEME_COOLDOWN_DAYS}d): shares keywords {sorted(overlap)} "
                f"with recent loss -- '{t.get('question','')[:60]}'. "
                f"Ben's rule: no re-entry on the same thesis within {_THEME_COOLDOWN_DAYS} days of a loss. "
                f"Find a different market or pass."
            )
    return None


def tool_paper_trade_polymarket(
    condition_id: str,
    side: str,
    size_usdc: float,
    price: float,
    market_question: str = "",
) -> str:
    """
    Record a Polymarket paper trade in Ben's own trade log.
    ALWAYS paper — never submits real orders (that is OctoBoto's job).
    Writes to .agents/profit-agent/polymarket_trades.json ONLY — never touches octo_boto_trades.json.

    Apply the same 4-condition gate before calling:
      1. EV >25% (|price - your_estimate| > 0.25)
      2. >2h to expiry
      3. Volume >$5k
      4. Range Scout OR Octodamus main oracle + Grok sentiment aligned

    condition_id: from get_polymarket_edges (conditionId field)
    side: YES or NO
    size_usdc: max 40
    price: current YES price (0.01-0.99)
    """
    if side.upper() not in ("YES", "NO"):
        return "BLOCKED: side must be YES or NO."
    if not (0.01 <= price <= 0.99):
        return "BLOCKED: price must be 0.01-0.99."
    if size_usdc > 40:
        return "BLOCKED: max $40 USDC per position."
    if not condition_id or len(condition_id) < 10:
        return "BLOCKED: provide a valid conditionId from get_polymarket_edges."

    # ── Sports hard-block ──────────────────────────────────────────────────────
    if market_question and _is_sports_market(market_question):
        return (
            f"HARD BLOCKED: '{market_question[:60]}' is a sports market. "
            "Ben has zero data advantage on sports outcomes -- no signal, no edge, pure noise. "
            "Categories with Ben's edge: crypto prices, macro events (Fed/CPI/jobs), "
            "geopolitical binary events backed by Octodamus signal. "
            "Skip this market entirely."
        )

    # ── Theme cooldown ─────────────────────────────────────────────────────────
    if market_question:
        _cooldown_block = _check_theme_cooldown(market_question)
        if _cooldown_block:
            return _cooldown_block

    import httpx, json as _j, time as _t
    from datetime import datetime, timezone

    # NOTE: gamma-api.polymarket.com/markets?conditionId=... is broken — it ignores the filter
    # and returns the highest-volume market regardless of conditionId. Do NOT use it for verification.
    # Trust the conditionId and market_question provided by the caller (from get_polymarket_edges
    # or the events API which both return correct data).
    question = (market_question or condition_id[:30])[:80]
    vol      = 0

    # EV calculation: if side=YES, EV = (1 - price) / price. If NO, EV = price / (1-price) - 1
    try:
        ev_pct = round(((1 / price) - 1) * 100, 1) if side.upper() == "YES" else round((price / (1 - price)) * 100, 1)
    except ZeroDivisionError:
        ev_pct = 0

    entry = {
        "paper":        True,
        "source":       "Agent_Ben",
        "condition_id": condition_id,
        "question":     question,
        "side":         side.upper(),
        "size_usdc":    size_usdc,
        "price":        price,
        "ev_pct":       ev_pct,
        "volume":       vol,
        "placed_at":    _t.strftime("%Y-%m-%d %H:%M UTC", _t.gmtime()),
        "status":       "open",
        "outcome":      None,
    }

    existing = _j.loads(_BEN_POLY_TRADES.read_text(encoding="utf-8")) if _BEN_POLY_TRADES.exists() else []
    existing.append(entry)
    _BEN_POLY_TRADES.write_text(_j.dumps(existing, indent=2), encoding="utf-8")

    return (
        f"[BEN PAPER TRADE — POLYMARKET]\n"
        f"  Market:  {question}\n"
        f"  Side:    {side.upper()} @ {price} (${size_usdc:.2f} USDC)\n"
        f"  Implied EV: +{ev_pct}% if correct\n"
        f"  Volume:  ${vol:,.0f}\n"
        f"  Logged to polymarket_trades.json (Ben's record — separate from OctoBoto)\n"
        f"  Order NOT submitted. Paper mode only."
    )


KALSHI_API  = "https://api.elections.kalshi.com/trade-api/v2"
KALSHI_SERIES = ["KXBTC", "KXETH", "KXFED", "KXCPI", "KXNFP", "KXSPY"]


def _kalshi_sign(key_id: str, private_key_pem: str, method: str, path: str) -> dict:
    """Build RSA-PSS signed Kalshi auth headers."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    import base64
    timestamp = str(int(time.time() * 1000))
    message   = f"{timestamp}{method}{path}".encode()
    pk = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
    sig = pk.sign(message, padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                  salt_length=padding.PSS.DIGEST_LENGTH), hashes.SHA256())
    return {
        "KALSHI-ACCESS-KEY":       key_id,
        "KALSHI-ACCESS-TIMESTAMP": timestamp,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
        "Content-Type":            "application/json",
    }


def tool_scan_kalshi(series: str = "KXBTC") -> str:
    """Scan Kalshi prediction markets (US-regulated, all 50 states). No auth needed for market data."""
    from datetime import datetime
    if datetime.now().weekday() >= 5:  # 5=Sat, 6=Sun
        return "Kalshi: WEEKEND SKIP — KXBTC/KXETH dead on weekends (0 vol). Resume Monday open."
    try:
        import httpx
        series = series.upper()
        if series not in KALSHI_SERIES and series != "ALL":
            return f"Unknown series. Available: {', '.join(KALSHI_SERIES)}"
        series_list = KALSHI_SERIES if series == "ALL" else [series]
        lines = [f"Kalshi markets ({series}) — CFTC-regulated, USD-settled:"]
        for s in series_list[:2]:
            r = httpx.get(f"{KALSHI_API}/markets",
                params={"status": "open", "series_ticker": s, "limit": 8},
                headers={"Accept": "application/json"}, timeout=10)
            if r.status_code == 200:
                for m in r.json().get("markets", [])[:5]:
                    ticker   = m.get("ticker","")
                    yes_ask  = m.get("yes_ask") or m.get("last_price",0)
                    yes_bid  = m.get("yes_bid",0)
                    vol      = m.get("volume", 0)
                    title    = m.get("title","")[:70]
                    lines.append(f"  {ticker} | YES ask={yes_ask}¢ bid={yes_bid}¢ | vol={vol} | {title}")
        return "\n".join(lines) if len(lines) > 1 else "No open markets found."
    except Exception as e:
        return f"Kalshi scan failed: {e}"


def tool_place_kalshi_bet(ticker: str, side: str, count: int, yes_price_cents: int) -> str:
    """
    Place a real bet on Kalshi (US-regulated, USD-settled, all 50 states).
    Requires KALSHI_KEY_ID + KALSHI_PRIVATE_KEY in secrets.
    side: 'yes' or 'no'. count: number of contracts ($1 face value each).
    yes_price_cents: limit price 1-99 (cents). Max $40 total cost.

    To activate:
    1. Create account at kalshi.com
    2. Settings -> API Keys -> generate RSA key pair
    3. Bitwarden 'AGENT - Octodamus - Kalshi API':
       username = Key ID (UUID)
       notes    = RSA private key (PEM)
    4. Run octo_unlock.ps1
    """
    if not (1 <= yes_price_cents <= 99):
        return "BLOCKED: yes_price_cents must be 1-99."
    cost = count * yes_price_cents / 100
    if cost > 40:
        return f"BLOCKED: Total cost ${cost:.2f} exceeds $40 max. Reduce count."

    s = _secrets()
    key_id  = s.get("KALSHI_KEY_ID", "")
    pem_key = s.get("KALSHI_PRIVATE_KEY", "")

    if not key_id or not pem_key:
        return (
            "KALSHI_KEY_ID / KALSHI_PRIVATE_KEY not configured.\n\n"
            "Setup:\n"
            "1. Create account at kalshi.com\n"
            "2. Settings -> API Keys -> generate RSA key pair\n"
            "3. Bitwarden 'AGENT - Octodamus - Kalshi API':\n"
            "   username = Key ID (UUID)\n"
            "   notes    = RSA private key PEM\n"
            "4. Run octo_unlock.ps1"
        )

    try:
        import httpx, json as _j
        side_str = side.lower()
        price_field = "yes_price" if side_str == "yes" else "no_price"
        # For a buy on 'no' side, the no_price = 100 - yes_price_cents
        price_val = yes_price_cents if side_str == "yes" else (100 - yes_price_cents)

        payload = _j.dumps({
            "ticker":        ticker,
            "side":          side_str,
            "action":        "buy",
            "count":         count,
            price_field:     price_val,
            "time_in_force": "fill_or_kill",
        }, separators=(",", ":"))

        path = "/trade-api/v2/portfolio/orders"
        hdrs = _kalshi_sign(key_id, pem_key, "POST", path)
        r = httpx.post(f"https://api.elections.kalshi.com{path}",
                       content=payload.encode(), headers=hdrs, timeout=15)

        log_entry = {
            "ticker": ticker, "side": side_str, "count": count,
            "yes_price_cents": yes_price_cents, "cost_usd": cost,
            "placed_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
            "status": r.status_code, "response": r.text[:300],
        }
        trades_file = Path(__file__).parent / "kalshi_trades.json"
        existing = _j.loads(trades_file.read_text()) if trades_file.exists() else []
        existing.append(log_entry)
        trades_file.write_text(_j.dumps(existing, indent=2))

        if r.status_code in (200, 201):
            return f"BET PLACED: {count}x {side.upper()} {ticker} @ {yes_price_cents}¢ | Cost: ${cost:.2f}\n{r.text[:300]}"
        else:
            return f"Order rejected ({r.status_code}): {r.text[:300]}"
    except Exception as e:
        return f"Kalshi bet failed: {type(e).__name__}: {e}"


def tool_check_acp_market() -> str:
    """
    Check Octodamus's standing on the Virtuals ACP (Agentic Commerce Protocol) marketplace.
    Shows job history, report count, active job types, and identifies funnel opportunities.
    ACP is where AI agents hire Octodamus for market intelligence reports at $1 USDC/job.
    """
    try:
        import json as _j, httpx
        from pathlib import Path as _P
        from datetime import datetime, timezone

        lines = []
        lines.append("=== OCTODAMUS ACP MARKET STATUS ===")
        lines.append("Platform: Virtuals ACP (app.virtuals.io) | Price: $1 USDC/job | Chain: Base")
        lines.append("Agent wallet: 0x94c037393ab0263194dcfd8d04a2176d6a80e385")
        lines.append("")

        # Real job stats — parse events file same way the ACP report module does
        events_file = _P(ROOT) / "data" / "acp_events.jsonl"
        if events_file.exists():
            job_statuses, ticker_counts, client_counts = {}, {}, {}
            for l in events_file.read_text(encoding="utf-8").strip().split("\n"):
                if not l.strip(): continue
                try:
                    e = _j.loads(l)
                    jid = str(e.get("jobId",""))
                    if not jid: continue
                    status = e.get("status","")
                    if status: job_statuses[jid] = status
                    entry = e.get("entry",{})
                    # Extract ticker from requirement messages
                    if entry.get("contentType") == "requirement":
                        try:
                            reqs = _j.loads(entry.get("content","{}"))
                            t = reqs.get("ticker","")
                            if t and job_statuses.get(jid) == "completed":
                                ticker_counts[t.upper()] = ticker_counts.get(t.upper(),0) + 1
                        except Exception: pass
                    # Extract buyer wallet
                    evt = entry.get("event",{})
                    client = evt.get("client","") or evt.get("buyer","")
                    if client and status == "funded":
                        client_counts[client] = client_counts.get(client,0) + 1
                except Exception: pass

            completed = sum(1 for s in job_statuses.values() if s == "completed")
            lines.append(f"Completed jobs (paid, real): {completed}  <-- USE THIS NUMBER for ACP stats")
            lines.append(f"USDC earned:                 ${completed:.2f} (~$1/job)")
            lines.append(f"Raw event IDs (NOT job count -- includes dev/test): {len(job_statuses)}")
            if ticker_counts:
                lines.append(f"Top tickers: {', '.join(f'{k}({v})' for k,v in sorted(ticker_counts.items(), key=lambda x:-x[1])[:5])}")
            if client_counts:
                top = max(client_counts, key=client_counts.get)
                lines.append(f"Top buyer: {top[:14]}... ({client_counts[top]} funded jobs)")

        # Live check of Virtuals ACP marketplace for Octodamus listing
        lines.append("\n--- MARKETPLACE RESEARCH ---")
        try:
            r = httpx.get("https://app.virtuals.io/virtuals?filter=acp", timeout=8)
            lines.append(f"Virtuals ACP marketplace: HTTP {r.status_code}")
        except Exception:
            lines.append("Virtuals marketplace: connection failed — try manual check at app.virtuals.io")

        # Smithery MCP stats
        lines.append("\n--- SMITHERY MCP STATUS ---")
        lines.append("MCP server: smithery.ai/server/octodamusai/market-intelligence")
        lines.append("Tools: get_signal, get_market_brief, get_polymarket_edges, get_track_record, ask_oracle, subscribe")
        try:
            r2 = httpx.get("https://smithery.ai/server/octodamusai/market-intelligence", timeout=8)
            lines.append(f"Smithery page: HTTP {r2.status_code}")
        except Exception:
            lines.append("Smithery: check manually at smithery.ai/server/octodamusai/market-intelligence")

        lines.append("\n--- FUNNEL OPPORTUNITIES ---")
        lines.append("1. ACP: Current jobs come via Virtuals marketplace. Need more agent buyers.")
        lines.append("2. ACP: Offer more job types -- currently: market_signal, crypto, stock reports")
        lines.append("3. Smithery: MCP is passive discovery. Agents find it, use it free or pay $0.01 x402")
        lines.append("4. Cross-funnel: ACP agents who complete jobs should be pitched x402 API ($29/yr)")
        lines.append("5. New report type: 'agent_brief' -- a structured JSON report designed for agent consumption")

        return "\n".join(lines)
    except Exception as e:
        return f"ACP market check failed: {e}"


def tool_design_acp_offering(name: str, description: str, price_usdc: float, what_it_delivers: str) -> str:
    """
    Design a new ACP job offering for Octodamus on the Virtuals marketplace.
    Saves the spec for the owner to implement in octo_acp_worker.py.
    Other AI agents can then hire Octodamus for this job type.
    """
    try:
        import json as _j
        from pathlib import Path as _P
        spec = {
            "offering_name":    name,
            "description":      description,
            "price_usdc":       price_usdc,
            "what_it_delivers": what_it_delivers,
            "platform":         "Virtuals ACP (app.virtuals.io)",
            "implement_in":     "octo_acp_worker.py + octo_report_handlers.py",
            "designed_by":      "Agent_Ben",
            "status":           "pending_implementation",
        }
        drafts_dir = _P(__file__).parent / "drafts"
        drafts_dir.mkdir(exist_ok=True)
        fname = drafts_dir / f"acp_offering_{name.lower().replace(' ','_')}.json"
        fname.write_text(_j.dumps(spec, indent=2), encoding="utf-8")
        return f"ACP offering spec saved: {fname.name}\n{_j.dumps(spec, indent=2)}"
    except Exception as e:
        return f"ACP offering design failed: {e}"


def tool_browse_orbis(query: str = "", category: str = "") -> str:
    """
    Browse OrbisAPI marketplace -- 5,873 APIs, x402 + Stripe payments.
    Find data sources Ben can buy from. Free discovery, no key needed.
    Use to find competitors, complementary data, or gaps Octodamus fills.
    """
    try:
        import httpx, json as _j
        r = httpx.get("https://orbisapi.com/api/agents/discovery?format=json", timeout=10, headers={"x-referral-code": "8TQZU7HH"})
        if r.status_code != 200:
            return f"Orbis discovery failed: {r.status_code}"
        d = r.json()
        catalogue = d.get("catalogue", [])
        total = d.get("totalApis", len(catalogue))

        # Filter by query or category
        if query or category:
            filtered = []
            for api in catalogue:
                name = str(api.get("name","")).lower()
                desc = str(api.get("description","")).lower()
                cat  = str(api.get("category",{})).lower()
                if query.lower() in name+desc+cat or category.lower() in cat:
                    filtered.append(api)
        else:
            filtered = catalogue[:20]

        lines = [f"OrbisAPI Marketplace ({total} total APIs, x402 + Stripe):"]
        for api in filtered[:15]:
            name    = api.get("name","?")
            cat     = api.get("category",{})
            cat_name = cat.get("name","?") if isinstance(cat, dict) else str(cat)
            has_free = api.get("hasFree", False)
            x402    = api.get("supportsX402", False)
            desc    = str(api.get("description",""))[:80]
            lines.append(f"  {name} | {cat_name} | free={has_free} x402={x402} | {desc}")

        if not filtered:
            lines.append(f"No results for '{query}'. Try: finance, crypto, data, weather, sports")
        return "\n".join(lines)
    except Exception as e:
        return f"Orbis browse failed: {e}"


def tool_buy_x402_service(url: str, max_price_usdc: float = 1.0) -> str:
    """
    Buy a service from any x402 endpoint using Ben's Franklin wallet (Base USDC).
    Checks the 402 response, signs EIP-3009 authorization, retries with payment.
    max_price_usdc: won't pay more than this. Default $1.00.

    Use for: Nansen data ($0.01), Octodamus premium signal ($0.01),
    Ben's own services (test them!), any x402 service on Base.
    """
    s = _secrets()
    wallet_key = s.get("FRANKLIN_PRIVATE_KEY", "")
    if not wallet_key:
        return "FRANKLIN_PRIVATE_KEY not in secrets. Run octo_unlock.ps1."

    try:
        import httpx
        from eth_account import Account

        account = Account.from_key(wallet_key)

        # Step 1: Hit the endpoint to get 402 payment requirements
        r = httpx.get(url, timeout=10)
        if r.status_code == 200:
            return f"Service returned 200 (free or already paid):\n{r.text[:500]}"
        if r.status_code != 402:
            return f"Unexpected status {r.status_code}: {r.text[:200]}"

        # Step 2: Parse payment requirements
        import json as _j, base64 as _b64
        pr_b64 = r.headers.get("payment-required", "")
        xpr    = r.headers.get("X-Payment-Required", "")

        price_usdc = 0.0
        pay_to     = ""
        asset      = ""
        network    = ""

        if xpr:
            try:
                xpr_data = _j.loads(xpr)
                for accept in xpr_data.get("accepts", []):
                    amt = int(accept.get("maxAmountRequired", accept.get("amount", 0))) / 1e6
                    if amt <= max_price_usdc:
                        price_usdc = amt
                        pay_to     = accept.get("payTo", "")
                        asset      = accept.get("asset", "")
                        network    = accept.get("network", "base-mainnet")
                        break
            except Exception:
                pass

        if not pay_to or price_usdc == 0:
            return f"Could not parse payment requirements.\nHeaders: {dict(r.headers)}\nBody: {r.text[:200]}"

        if price_usdc > max_price_usdc:
            return f"Service costs ${price_usdc:.4f} USDC — above your max of ${max_price_usdc}. Increase max_price_usdc or skip."

        # Step 3: Use x402 SDK to handle signing — same approach as octo_x402_health.py --live
        import time as _t
        from x402.mechanisms.evm.exact.client import ExactEvmScheme
        from x402.http.x402_http_client import x402HTTPClientSync
        from x402.client import x402ClientSync

        scheme = ExactEvmScheme(account)
        x402c  = x402ClientSync()
        x402c.register("eip155:8453", scheme)
        http_c = x402HTTPClientSync(x402c)

        pay_headers, _ = http_c.handle_402_response(dict(r.headers), r.content)

        # Step 4: Retry with payment
        r2 = httpx.get(url, headers=pay_headers, timeout=15)

        # Log the purchase
        trades_file = Path(__file__).parent / "x402_purchases.json"
        existing = _j.loads(trades_file.read_text()) if trades_file.exists() else []
        existing.append({
            "url": url, "price_usdc": price_usdc,
            "paid_at": _t.strftime("%Y-%m-%d %H:%M UTC", _t.gmtime()),
            "status": r2.status_code,
        })
        trades_file.write_text(_j.dumps(existing, indent=2))

        if r2.status_code == 200:
            return f"PURCHASED: ${price_usdc:.4f} USDC | {url}\n\n{r2.text[:800]}"
        else:
            return f"Payment sent but got {r2.status_code}: {r2.text[:300]}"

    except Exception as e:
        return f"x402 purchase failed: {type(e).__name__}: {e}"


def tool_design_x402_service(name: str, description: str, price_usdc: float, what_it_returns: str) -> str:
    """Design a new x402 service for Agent_Ben to sell. Saves the spec for the owner to implement."""
    try:
        spec = {
            "service_name":     name,
            "description":      description,
            "price_usdc":       price_usdc,
            "what_it_returns":  what_it_returns,
            "endpoint":         f"GET https://api.octodamus.com/v2/ben/{name.lower().replace(' ','_')}",
            "designed_by":      "Agent_Ben",
            "status":           "pending_implementation",
        }
        import json as _j
        spec_dir = Path(__file__).parent / "drafts"
        spec_dir.mkdir(exist_ok=True)
        fname = spec_dir / f"x402_service_{name.lower().replace(' ','_')}.json"
        fname.write_text(_j.dumps(spec, indent=2), encoding="utf-8")
        return f"Service spec saved: {fname.name}\n{_j.dumps(spec, indent=2)}"
    except Exception as e:
        return f"Service design failed: {e}"


def tool_find_arbitrage(market_a: str, market_b: str) -> str:
    """Compare prices/odds between two prediction market questions to find arbitrage opportunities."""
    try:
        import httpx
        lines = ["Arbitrage search:"]
        for query in [market_a, market_b]:
            r = httpx.get(
                "https://gamma-api.polymarket.com/markets",
                params={"active": True, "closed": False, "limit": 5,
                        "order": "volume", "ascending": False,
                        "_c": query},
                timeout=8
            )
            if r.status_code == 200:
                markets = r.json()
                lines.append(f"\nQuery: {query}")
                for m in markets[:3]:
                    q    = m.get("question", "")[:70]
                    yes  = m.get("outcomePrices", ["?"])[0]
                    vol  = m.get("volume", 0)
                    lines.append(f"  YES={yes} | Vol=${float(vol or 0):,.0f} | {q}")
        return "\n".join(lines)
    except Exception as e:
        return f"Arbitrage search failed: {e}"


_DRAFT_VOICE = """You are writing for Octodamus (@octodamusai), an autonomous AI market oracle.

Voice rules (non-negotiable):
- Inspired by Thomas McGuane: economy of language, one detail that contains everything
- Stanley Druckenmiller: conviction earned through process, never bluster
- No emojis. Ever.
- No hashtags. Ever.
- No hype words: "game-changer", "revolutionary", "unlock", "amazing"
- Dry, precise, occasionally contrarian. Smart people talking to smart people.
- Numbers are specific. Claims are grounded. No vague takes.
- For X posts: under 270 chars each, no hashtags, no emojis, read like a trader not a marketer
  - Never open with a greeting, date reference, or "Happy [day]". First word must be a number, a ticker (BTC/ETH/SOL), or a strong verb.
  - If drafting a morning post: check list_drafts for previous morning_post files. If the same analytical angle (e.g., "bull trap divergence") has appeared in 3+ consecutive posts, shift the frame. 14 days of divergence gets a different angle than day 1: historical base rates, what finally ends it, or the minority signal.
  - Maximum 2 hashtags per post, both specific to the thesis. Never stack generic crypto hashtags (#Bitcoin #BTC #CryptoTrading together — pick the one that fits).
- For emails: direct, no fluff, assumes the reader is intelligent"""


def tool_draft_content(task: str, context: str = "", model: str = "haiku") -> str:
    """Draft content in Octodamus voice. model='haiku' (default) or 'grok' (xAI, higher quality). Auto-saves."""
    try:
        import re as _re
        prompt = f"{task}"
        if context:
            prompt += f"\n\nContext:\n{context[:2000]}"
        grok_key = _secrets().get("GROK_API_KEY", "")
        if model == "grok" and grok_key:
            from openai import OpenAI as _OAI
            c = _OAI(base_url="https://api.x.ai/v1", api_key=grok_key)
            r = c.chat.completions.create(
                model="grok-3-mini",
                max_tokens=1000,
                messages=[{"role": "system", "content": _DRAFT_VOICE},
                          {"role": "user",   "content": prompt}],
            )
            content = r.choices[0].message.content.strip()
        else:
            import anthropic
            c = anthropic.Anthropic(api_key=_secrets().get("ANTHROPIC_API_KEY",""))
            r = c.messages.create(
                model="claude-haiku-4-5-20251001", max_tokens=1000,
                system=_DRAFT_VOICE,
                messages=[{"role": "user", "content": prompt}],
            )
            content = r.content[0].text.strip()
        # Auto-save -- derive filename from task
        drafts_dir = Path(__file__).parent / "drafts"
        drafts_dir.mkdir(exist_ok=True)
        slug = _re.sub(r"[^a-z0-9]+", "_", task[:40].lower()).strip("_")
        ts   = datetime.now().strftime("%H%M")
        fname = drafts_dir / f"{slug}_{ts}.md"
        fname.write_text(content, encoding="utf-8")
        return f"{content}\n\n[Auto-saved to drafts/{fname.name}]"
    except Exception as e:
        return f"Content draft failed: {e}"


def tool_send_email(subject: str, body: str) -> str:
    """Send an email to octodamusai@gmail.com."""
    try:
        from octo_notify import _send
        _send(subject, body)
        return f"Email sent: {subject}"
    except Exception as e:
        return f"Email failed: {e}"


def tool_save_draft(filename: str, content: str) -> str:
    """Save a drafted asset (tweet thread, email, guide) to a file for the owner to review and deploy."""
    try:
        drafts_dir = Path(__file__).parent / "drafts"
        drafts_dir.mkdir(exist_ok=True)
        safe_name = "".join(c if c.isalnum() or c in "-_." else "_" for c in filename)
        if not safe_name.endswith(".md"):
            safe_name += ".md"
        out = drafts_dir / safe_name
        out.write_text(content, encoding="utf-8")
        return f"Saved to .agents/profit-agent/drafts/{safe_name} ({len(content)} chars)"
    except Exception as e:
        return f"Save failed: {e}"



def tool_list_drafts() -> str:
    """List all saved draft files so the agent knows what's already been created."""
    drafts_dir = Path(__file__).parent / "drafts"
    if not drafts_dir.exists() or not list(drafts_dir.iterdir()):
        return "No drafts saved yet."
    lines = ["Saved drafts:"]
    for f in sorted(drafts_dir.iterdir()):
        size = f.stat().st_size
        lines.append(f"  {f.name} ({size} bytes)")
    return "\n".join(lines)


_session_logged_action = [False]   # reset to False at the start of each run_session()
_session_market_cache: dict = {}   # populated by tool_get_market_data; used for subject line


def tool_log_action(action: str, result: str, cost_usd: float = 0.0) -> str:
    """Log an action to the session log for transparency."""
    _session_logged_action[0] = True
    entry = f"[{datetime.now().strftime('%H:%M:%S')}] {action} | cost=${cost_usd:.4f} | {result[:600]}"
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(entry + "\n")
    return f"Logged: {action}"


_BEN_BUDGET_FILE = ROOT / "data" / "acp_ben_budget.json"
_BEN_BUY_GATE    = 10     # checkpoint: after this many buys, wallet must show profit
_BEN_PROFIT_MIN  = 1.10   # wallet must be at least 10% above starting USDC to keep buying


def _ben_budget() -> dict:
    try:
        return json.loads(_BEN_BUDGET_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _ben_usdc_balance() -> float:
    try:
        from web3 import Web3
        sec  = _secrets()
        addr = sec.get("FRANKLIN_WALLET_ADDRESS", "")
        if not addr:
            return 0.0
        w3   = Web3(Web3.HTTPProvider("https://mainnet.base.org"))
        usdc = w3.eth.contract(
            address=Web3.to_checksum_address("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"),
            abi=[{"name":"balanceOf","type":"function","stateMutability":"view",
                  "inputs":[{"name":"account","type":"address"}],
                  "outputs":[{"name":"","type":"uint256"}]}],
        )
        return usdc.functions.balanceOf(Web3.to_checksum_address(addr)).call() / 1e6
    except Exception:
        return 0.0


def _check_ben_buy_gate() -> str | None:
    """Return a block message if Ben should stop buying, else None."""
    budget = _ben_budget()
    buy_count = budget.get("buy_count", 0)

    if buy_count < _BEN_BUY_GATE:
        return None  # under threshold, no check needed

    starting_usdc = budget.get("starting_usdc", 0.0)
    if starting_usdc <= 0:
        return None  # no baseline recorded, allow

    current_usdc = _ben_usdc_balance()
    required     = starting_usdc * _BEN_PROFIT_MIN

    if current_usdc >= required:
        return None  # profitable enough, continue

    return (
        f"BUY PAUSED: Ben has made {buy_count} ACP purchases. "
        f"Wallet USDC ${current_usdc:.2f} has not reached the 10% profit threshold "
        f"(need ${required:.2f}, started at ${starting_usdc:.2f}). "
        f"Buying resumes automatically once wallet recovers. "
        f"Focus on trading profit first."
    )


def tool_get_free_intel() -> str:
    """Pull free intelligence: macro + congressional trades + travel signal + CoinGecko. Zero cost. Run before ecosystem buys."""
    sys.path.insert(0, str(ROOT))
    try:
        from octo_free_intel import get_free_intel
        return get_free_intel("Agent_Ben")
    except Exception as e:
        return f"Free intel unavailable: {e}"


def tool_buy_ecosystem_intel(target_agent: str, service_name: str) -> str:
    """Buy intel from an Octodamus ecosystem agent via ACP. Ben's calling card is embedded so they can hire him back."""
    # Profitability gate: after 200 buys, wallet must be 10% above starting USDC
    gate_msg = _check_ben_buy_gate()
    if gate_msg:
        return gate_msg

    sys.path.insert(0, str(ROOT))
    from octo_agent_cards import buy_intel
    result = buy_intel("Ben", target_agent, service_name)

    # Track buy count and baseline on first buy
    if "Job #" in result:
        budget = _ben_budget()
        if "starting_usdc" not in budget:
            budget["starting_usdc"] = _ben_usdc_balance()
        budget["buy_count"] = budget.get("buy_count", 0) + 1
        _BEN_BUDGET_FILE.write_text(json.dumps(budget, indent=2), encoding="utf-8")

    return result


def tool_list_ecosystem_services() -> str:
    """List all services available for purchase across the Octodamus ecosystem."""
    sys.path.insert(0, str(ROOT))
    from octo_agent_cards import list_ecosystem_services
    return list_ecosystem_services()


def tool_read_sub_agent_drafts(date_filter: str = "") -> str:
    """Read today's pre-market draft files from all sub-agent directories.
    Returns each agent's regime verdict and key signals. Use for sub-agent synthesis."""
    agents_dir = ROOT / ".agents"
    sub_agents = {
        "NYSE_MacroMind":   agents_dir / "nyse_macromind"   / "data" / "drafts",
        "NYSE_StockOracle": agents_dir / "nyse_stockoracle" / "data" / "drafts",
        "NYSE_Tech_Agent":  agents_dir / "nyse_tech_agent"  / "data" / "drafts",
        "Order_ChainFlow":  agents_dir / "order_chainflow"  / "data" / "drafts",
        "X_Sentiment":      agents_dir / "x_sentiment_agent"/ "data" / "drafts",
    }

    today = date_filter or datetime.now().strftime("%Y-%m-%d")
    results = []

    for agent_name, drafts_dir in sub_agents.items():
        if not drafts_dir.exists():
            results.append(f"{agent_name}: no drafts directory")
            continue
        # Find files modified today or containing today's date in filename
        today_files = []
        try:
            for f in sorted(drafts_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
                if not f.suffix in (".md", ".txt", ".json"):
                    continue
                fname_has_date = today.replace("-", "") in f.name or today in f.name
                import os
                from datetime import date as _date
                mtime_date = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d")
                if fname_has_date or mtime_date == today:
                    today_files.append(f)
        except Exception as e:
            results.append(f"{agent_name}: error listing drafts ({e})")
            continue

        if not today_files:
            results.append(f"{agent_name}: no draft for {today}")
            continue

        # Read the most recent today file
        target = today_files[0]
        try:
            content = target.read_text(encoding="utf-8", errors="replace")
            # Return first 600 chars -- enough to capture regime verdict + key signals
            snippet = content[:600].strip()
            results.append(f"\n{'='*40}\n{agent_name} | {target.name}\n{'-'*40}\n{snippet}\n...")
        except Exception as e:
            results.append(f"{agent_name}: read error ({e})")

    return "\n".join(results) if results else "No sub-agent drafts found."


def tool_get_ben_spend_budget() -> str:
    """Return how many ACP ecosystem buys are allowed this session.
    Call this BEFORE any buy_ecosystem_intel. Protects wallet on PASS sessions."""
    budget = _ben_budget()
    buy_count = budget.get("buy_count", 0)
    current_usdc = _ben_usdc_balance()

    # Count unimplemented service specs
    spec_dir = Path(__file__).parent / "drafts"
    unimplemented = 0
    if spec_dir.exists():
        unimplemented = sum(
            1 for f in spec_dir.iterdir()
            if f.name.startswith("x402_service_") and f.suffix == ".json"
        )

    if current_usdc <= 0:
        current_usdc = budget.get("starting_usdc", 195.0) or 195.0  # fallback

    if current_usdc < 10.0:
        return ("SPEND BUDGET: 0 ecosystem buys. WALLET CRITICAL — stop all spending, email owner.\n"
                f"Balance: ${current_usdc:.2f} USDC. Hard stop at $10.")

    if current_usdc < 50.0:
        return (f"SPEND BUDGET: 0 ecosystem buys this session.\n"
                f"Wallet ${current_usdc:.2f}. Conserve until a 4-condition trade setup exists.\n"
                f"Rule: ecosystem buys only when placing or seriously considering a trade.")

    if current_usdc < 150.0:
        return (f"SPEND BUDGET: 1 ecosystem buy ONLY if a 4-condition trade is being evaluated.\n"
                f"Wallet ${current_usdc:.2f}. If session result = PASS, make 0 buys.\n"
                f"Unimplemented service specs: {unimplemented}.")

    # Wallet healthy (>$150)
    return (f"SPEND BUDGET: up to 2 ecosystem buys ONLY when evaluating a specific 4-condition trade.\n"
            f"Wallet ${current_usdc:.2f}. PASS sessions = 0 ecosystem buys. Use read_sub_agent_drafts (FREE).\n"
            f"Unimplemented service specs: {unimplemented}. "
            + ("Pause new service designs — backlog already large." if unimplemented >= 5 else ""))


BEN_HISTORY_FILE  = Path(__file__).parent / "data" / "ben_history.json"
ACP_CLI_DIR       = Path(r"C:\Users\walli\acp-cli")
ACP_COMPETITOR_LOG = Path(__file__).parent / "data" / "acp_competitor_jobs.json"
_NPM = "npm.cmd"

# Known competitors worth studying — market/prediction/signal agents only
ACP_COMPETITORS = {
    "predictor-sam":    {"wallet": "0xeaace9635A06D2EfdE25ce7cc4f8C18ce845F37f", "jobs": 157, "rate": 85, "desc": "prediction market services"},
    "blue-dot-testnet": {"wallet": "0xA46273c5bdf6D53836909E97e3417527aeA93593", "jobs": 10,  "rate": 91, "desc": "market alpha"},
    "Dou Shan":         {"wallet": "0x401b87283fa043c2C0462cAc94da144580F05C72", "jobs": 35,  "rate": 56, "desc": "prediction market agent"},
}


def _acp_cli(args: list, timeout: int = 30) -> tuple:
    """Run ACP CLI command from acp-cli dir. Returns (rc, stdout, stderr)."""
    import subprocess as _sp
    cmd = [_NPM, "run", "acp", "--"] + [str(a) for a in args]
    try:
        r = _sp.run(cmd, capture_output=True, text=True, encoding="utf-8",
                    timeout=timeout, cwd=str(ACP_CLI_DIR))
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except Exception as e:
        return -1, "", str(e)


def _load_competitor_log() -> list:
    try:
        if ACP_COMPETITOR_LOG.exists():
            return json.loads(ACP_COMPETITOR_LOG.read_text(encoding="utf-8"))
    except Exception:
        pass
    return []


def _save_competitor_log(log: list):
    ACP_COMPETITOR_LOG.parent.mkdir(exist_ok=True)
    ACP_COMPETITOR_LOG.write_text(json.dumps(log, indent=2), encoding="utf-8")


def tool_buy_acp_competitor_job(competitor_name: str, description: str = "") -> str:
    """
    Send a competitive intelligence job request to a known ACP competitor.
    Uses the Octodamus ACP wallet as buyer (has ~$14 USDC from earnings).
    competitor_name: one of 'predictor-sam', 'blue-dot-testnet', 'Dou Shan'
    description: what to ask for (defaults to market signal request)
    Returns job ID and status. Check acp_events.jsonl for deliverable when it arrives.
    PURPOSE: Learn what competitors deliver so we can improve Octodamus offerings.
    """
    if competitor_name not in ACP_COMPETITORS:
        return f"Unknown competitor. Options: {list(ACP_COMPETITORS.keys())}"

    comp = ACP_COMPETITORS[competitor_name]
    provider_wallet = comp["wallet"]

    if not description:
        description = (
            f"Provide your best market signal for BTC over the next 24 hours. "
            f"Include: direction (bullish/bearish), key data points, confidence level, "
            f"and reasoning. Format as structured data if possible."
        )

    rc, out, err = _acp_cli([
        "client", "create-custom-job",
        "--provider",    provider_wallet,
        "--description", description,
        "--chain-id",    "8453",
        "--expired-in",  "7200",
    ], timeout=45)

    # Parse job ID from output
    import re as _re
    job_id = None
    for pattern in [r'job[_\s]?id[:\s]+["\']?(\d+)', r'#(\d+)', r'jobId[:\s]+(\d+)', r'(\d{3,})', r'created.*?(\d+)']:
        m = _re.search(pattern, out + err, _re.IGNORECASE)
        if m:
            job_id = m.group(1)
            break

    # Log the purchase attempt
    log = _load_competitor_log()
    entry = {
        "competitor":   competitor_name,
        "wallet":       provider_wallet,
        "description":  description,
        "job_id":       job_id,
        "rc":           rc,
        "stdout":       out[:500],
        "stderr":       err[:300],
        "status":       "created" if rc == 0 else "failed",
        "created_at":   datetime.now().isoformat(),
        "deliverable":  None,
    }
    log.append(entry)
    _save_competitor_log(log)

    if rc == 0:
        return (
            f"Job created with {competitor_name} (wallet: {provider_wallet[:16]}...)\n"
            f"Job ID: {job_id or 'check output'}\n"
            f"Status: Waiting for provider to set budget, then we fund it.\n"
            f"Output: {out[:300]}\n"
            f"Next: use check_acp_competitor_jobs to monitor for deliverable."
        )
    else:
        return f"Job creation failed (rc={rc}):\nstdout: {out[:300]}\nstderr: {err[:300]}"


def tool_check_acp_competitor_jobs() -> str:
    """
    Check status of all competitor intelligence jobs sent via buy_acp_competitor_job.
    Shows pending jobs, funded jobs, and any deliverables received.
    """
    log = _load_competitor_log()
    if not log:
        return "No competitor jobs sent yet. Use buy_acp_competitor_job to start."

    # Also scan ACP events file for updates on these job IDs
    events_file = ROOT / "data" / "acp_events.jsonl"
    job_events: dict = {}
    if events_file.exists():
        for line in events_file.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
                jid = d.get("jobId", "")
                if jid:
                    job_events.setdefault(jid, []).append(d.get("status", ""))
                    # Check for deliverable
                    entry = d.get("entry", {})
                    if entry.get("kind") == "message" and entry.get("contentType") == "deliverable":
                        job_events.setdefault(jid, []).append(f"DELIVERABLE: {entry.get('content','')[:200]}")
            except Exception:
                pass

    lines = ["=== ACP Competitor Intelligence Jobs ===", ""]
    for entry in log:
        jid  = entry.get("job_id", "?")
        name = entry.get("competitor", "?")
        status = entry.get("status", "?")
        events = job_events.get(jid, [])
        if events:
            status = " | ".join(set(e for e in events if e))
        lines.append(f"[{name}] Job #{jid} | Status: {status}")
        lines.append(f"  Asked: {entry.get('description','')[:80]}")
        lines.append(f"  Created: {entry.get('created_at','')[:16]}")
        deliverable = entry.get("deliverable")
        if deliverable:
            lines.append(f"  DELIVERABLE: {deliverable[:300]}")
        lines.append("")

    return "\n".join(lines)


def tool_check_memory_status() -> str:
    """
    Check the learning/memory status of all three Octodamus systems.
    Shows what's been accumulated and whether each is COMPOUNDING (data feeding back
    into decisions) or STATIC (data collected but not yet influencing behavior).
    Include this output in morning and evening email reports.
    """
    sys.path.insert(0, str(ROOT))
    lines = ["=== MEMORY STATUS REPORT ===", ""]

    # ── 1. Octodamus Skill Log ────────────────────────────────────────────────
    lines.append("OCTODAMUS (Oracle Post Learning)")
    try:
        sl_path = ROOT / "octo_skill_log.json"
        sh_path = ROOT / "octo_skill_history.json"
        entries = json.loads(sl_path.read_text(encoding="utf-8")) if sl_path.exists() else []
        rated   = [e for e in entries if e.get("rating")]
        good    = [e for e in rated if e["rating"] == "good"]
        bad     = [e for e in rated if e["rating"] == "bad"]
        ok      = [e for e in rated if e["rating"] == "ok"]

        # Best voice mode
        voice_counts: dict = {}
        for e in good:
            vm = e.get("voice_mode", "unknown")
            voice_counts[vm] = voice_counts.get(vm, 0) + 1
        best_voice = max(voice_counts, key=voice_counts.get) if voice_counts else "none"

        # Latest amendment
        amendments = json.loads(sh_path.read_text(encoding="utf-8")) if sh_path.exists() else []
        last_amend = amendments[-1]["timestamp"][:10] if amendments else "none yet"
        pending_amend = sum(1 for a in amendments if not a.get("applied"))

        # Engagement metrics coverage
        with_metrics = [e for e in entries if e.get("engagement_score") is not None]

        status = "COMPOUNDING" if len(rated) >= 3 else "BUILDING"
        lines += [
            f"  Posts logged:     {len(entries)} total | {len(with_metrics)} with engagement metrics",
            f"  Rated:            {len(rated)} rated | Good: {len(good)} / Bad: {len(bad)} / OK: {len(ok)}",
            f"  Best voice mode:  {best_voice}",
            f"  Amendment proposals: {len(amendments)} saved | {pending_amend} pending approval",
            f"  Last amendment:   {last_amend}",
            f"  Status: {status} -- skill summary {'injected into each daily post prompt' if status == 'COMPOUNDING' else 'not enough rated posts yet (need 3+)'}",
        ]
    except Exception as e:
        lines.append(f"  ERROR reading skill log: {e}")

    lines.append("")

    # ── 2. OctoBoto Calibration ───────────────────────────────────────────────
    lines.append("OCTOBOTO (Trade Calibration Learning)")
    try:
        cal_path = ROOT / "octo_boto_calibration.json"
        cal      = json.loads(cal_path.read_text(encoding="utf-8")) if cal_path.exists() else {"estimates": []}
        estimates = cal.get("estimates", [])
        resolved  = [e for e in estimates if e.get("outcome") is not None]
        pending   = [e for e in estimates if e.get("outcome") is None]

        # Current calibration bias
        bias_str = "not ready"
        if len(resolved) >= 5:
            by_conf: dict = {}
            for e in resolved:
                conf = e.get("confidence", "low")
                side = e.get("side", "YES")
                our_p = (1.0 - e["claude_p"]) if side == "NO" else e["claude_p"]
                actual = e["outcome"] == "YES"
                by_conf.setdefault(conf, []).append(our_p - (1.0 if actual else 0.0))
            biases = [sum(v)/len(v) for v in by_conf.values() if v]
            overall = round(sum(biases)/len(biases), 3) if biases else 0
            direction = "overconfident" if overall > 0 else "underconfident"
            bias_str = f"{overall:+.1%} overall ({direction})"

        # Dynamic threshold
        thresh_path = ROOT / "data" / "octo_ev_threshold.json"
        threshold = "12% (default)"
        if thresh_path.exists():
            td = json.loads(thresh_path.read_text(encoding="utf-8"))
            threshold = f"{td.get('threshold', 0.12):.0%} (win rate: {td.get('win_rate', 0):.0%}, {td.get('n_trades', 0)} trades)"

        need = max(0, 5 - len(resolved))
        status = "COMPOUNDING" if len(resolved) >= 5 else "STATIC"
        lines += [
            f"  Estimates logged: {len(estimates)} | Resolved: {len(resolved)} | Pending: {len(pending)}",
            f"  Calibration bias: {bias_str}",
            f"  EV threshold:     {threshold}",
            f"  Status: {status} -- {'bias correction injecting into every trade evaluation' if status == 'COMPOUNDING' else f'need {need} more resolved trades before calibration kicks in'}",
        ]
        if pending:
            lines.append(f"  Pending markets:  {', '.join(e.get('question','?')[:40] for e in pending[:3])}" + (" ..." if len(pending) > 3 else ""))
    except Exception as e:
        lines.append(f"  ERROR reading calibration: {e}")

    lines.append("")

    # ── 3. Agent_Ben Session History ──────────────────────────────────────────
    lines.append("AGENT_BEN (Cross-Session Learning)")
    try:
        history = _load_ben_history()
        if not history:
            lines += [
                "  Sessions logged:  0",
                "  Status: BUILDING -- first session. record_lesson at end to start the log.",
            ]
        else:
            wallet_start = history[0].get("wallet_start") or history[0].get("wallet_end", 0)
            wallet_latest = history[-1].get("wallet_end", 0)
            delta = round(wallet_latest - wallet_start, 2) if wallet_start and wallet_latest else 0
            delta_str = f"+${delta:.2f}" if delta > 0 else (f"-${abs(delta):.2f}" if delta < 0 else "$0.00")

            all_lessons = []
            services_total = 0
            trades_total = 0
            for h in history:
                all_lessons.extend(h.get("lessons", []))
                services_total += h.get("services_designed", 0)
                trades_total   += h.get("trades", 0)

            last_lesson = all_lessons[-1] if all_lessons else "none recorded"
            status = "COMPOUNDING" if len(history) >= 2 else "BUILDING"
            lines += [
                f"  Sessions logged:  {len(history)}",
                f"  Wallet trajectory:{f' ${wallet_start:.2f}' if wallet_start else ' unknown'} -> ${wallet_latest:.2f} ({delta_str})",
                f"  Trades placed:    {trades_total} | Services designed: {services_total}",
                f"  Lessons stored:   {len(all_lessons)}",
                f"  Latest lesson:    {last_lesson[:120]}",
                f"  Status: {status} -- {'history read at every session start, lessons accumulating' if status == 'COMPOUNDING' else 'log one more session to begin compounding'}",
            ]
    except Exception as e:
        lines.append(f"  ERROR reading session history: {e}")

    # ── 4. Sub-Agent Fleet ────────────────────────────────────────────────────
    lines.append("SUB-AGENT FLEET (Compounding Loop Status)")
    sub_agents = [
        ("nyse_macromind",    "nyse_macromind"),
        ("nyse_stockoracle",  "nyse_stockoracle"),
        ("nyse_tech_agent",   "nyse_tech_agent"),
        ("order_chainflow",   "order_chainflow"),
        ("x_sentiment_agent", "x_sentiment_agent"),
    ]
    agents_dir = ROOT / ".agents"
    memory_dir = ROOT / "data" / "memory"
    for agent_dir_name, memory_key in sub_agents:
        try:
            agent_root   = agents_dir / agent_dir_name
            state_file   = agent_root / "data" / "state.json"
            history_file = agent_root / "data" / "history.json"
            core_file    = memory_dir / f"{memory_key}_core.md"

            sessions = 0
            if state_file.exists():
                st = json.loads(state_file.read_text(encoding="utf-8"))
                sessions = st.get("sessions", 0)

            history_entries = 0
            if history_file.exists():
                hist = json.loads(history_file.read_text(encoding="utf-8"))
                history_entries = len(hist) if isinstance(hist, list) else 0

            distilled = False
            if core_file.exists():
                distilled = "## Distilled" in core_file.read_text(encoding="utf-8")

            if distilled:
                status = "Compounding"
                note   = "loop closed"
            elif history_entries > 0:
                status = "Compounding"
                note   = "sessions logging, distillation pending"
            elif sessions > 0:
                status = "Static"
                note   = "running but session protocol not completing"
            else:
                status = "Static"
                note   = "no sessions recorded"

            lines.append(f"  {agent_dir_name:<22} s={sessions}  h={history_entries}  [{status}]  {note}")
        except Exception as e:
            lines.append(f"  {agent_dir_name:<22} ERROR: {e}")

    lines += ["", "=== END MEMORY STATUS ==="]
    return "\n".join(lines)


def _load_ben_history() -> list:
    try:
        sys.path.insert(0, str(ROOT))
        from octo_memory_db import db_ben_history
        return db_ben_history(limit=50)
    except Exception:
        pass
    try:
        if BEN_HISTORY_FILE.exists():
            return json.loads(BEN_HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return []


def _save_ben_history(history: list):
    BEN_HISTORY_FILE.parent.mkdir(exist_ok=True)
    BEN_HISTORY_FILE.write_text(json.dumps(history, indent=2), encoding="utf-8")


def tool_read_core_memory() -> str:
    """Read Agent_Ben's distilled core memory — hard lessons, what works, validated rules. Call every session."""
    try:
        sys.path.insert(0, str(ROOT))
        from octo_memory_db import read_core_memory
        return read_core_memory("ben")
    except Exception as e:
        # Fallback: read the file directly
        try:
            core_path = ROOT / "data" / "memory" / "ben_core.md"
            if core_path.exists():
                return core_path.read_text(encoding="utf-8")
        except Exception:
            pass
        return f"Core memory unavailable: {e}"


def tool_append_core_memory(insight: str) -> str:
    """Append a durable insight to Agent_Ben's core memory. Use for lessons that should persist forever."""
    try:
        sys.path.insert(0, str(ROOT))
        from octo_memory_db import append_core_memory
        append_core_memory("ben", "Agent_Ben Update", insight)
        return f"Appended to core memory: {insight[:80]}..."
    except Exception as e:
        # Fallback: append directly to the file
        try:
            from datetime import datetime as _dt
            core_path = ROOT / "data" / "memory" / "ben_core.md"
            existing = core_path.read_text(encoding="utf-8") if core_path.exists() else ""
            now = _dt.utcnow().strftime("%Y-%m-%d")
            core_path.write_text(existing.rstrip() + f"\n\n## Agent_Ben Update ({now})\n{insight}", encoding="utf-8")
            return f"Appended to core memory file directly: {insight[:80]}..."
        except Exception as e2:
            return f"Failed to append: {e} / {e2}"


def tool_get_session_history() -> str:
    """Read the persistent cross-session learning log. Always call this near the start of each session."""
    history = _load_ben_history()
    if not history:
        return "No session history yet. This is an early session — build the record."
    recent = history[-10:]
    lines = [f"Session history ({len(history)} total sessions logged):"]
    for h in recent:
        wallet_delta = h.get("wallet_delta", 0)
        delta_str = f"+${wallet_delta:.2f}" if wallet_delta > 0 else (f"-${abs(wallet_delta):.2f}" if wallet_delta < 0 else "$0.00")
        lines.append(
            f"\n[{h.get('date','?')} {h.get('session_type','?')}] "
            f"Wallet: {delta_str} | Trades: {h.get('trades',0)} | "
            f"Services designed: {h.get('services_designed',0)}"
        )
        if h.get("lessons"):
            for lesson in h["lessons"]:
                lines.append(f"  LESSON: {lesson}")
        if h.get("what_worked"):
            lines.append(f"  WORKED: {h['what_worked']}")
        if h.get("what_failed"):
            lines.append(f"  FAILED: {h['what_failed']}")
    return "\n".join(lines)


def tool_record_lesson(lesson: str, what_worked: str = "", what_failed: str = "",
                       trades: int = 0, services_designed: int = 0,
                       wallet_start: float = 0.0, wallet_end: float = 0.0) -> str:
    """
    Record a lesson or outcome at the end of a session. Persists across all future sessions.
    Call once per session in the final turn before emailing the owner.
    """
    state = _load_state()
    # +1 because run_session() saves the incremented count at the very end,
    # so during execution the disk still holds the previous session number.
    session_num = state.get("sessions", 0) + 1
    # Write to SQLite (primary)
    try:
        sys.path.insert(0, str(ROOT))
        from octo_memory_db import db_record_ben_session
        db_record_ben_session(
            session_num=session_num,
            date=datetime.now().strftime("%Y-%m-%d"),
            session_type=_get_session_type_str(),
            wallet_start=round(wallet_start, 2),
            wallet_end=round(wallet_end, 2),
            trades=trades,
            services_designed=services_designed,
            what_worked=what_worked,
            what_failed=what_failed,
            lessons=[lesson] if lesson else [],
        )
    except Exception as e:
        pass
    # Also write to JSON (backup)
    history = []
    try:
        if BEN_HISTORY_FILE.exists():
            history = json.loads(BEN_HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    entry = {
        "session": session_num, "date": datetime.now().strftime("%Y-%m-%d"),
        "session_type": _get_session_type_str(), "wallet_start": round(wallet_start, 2),
        "wallet_end": round(wallet_end, 2),
        "wallet_delta": round(wallet_end - wallet_start, 2) if wallet_end and wallet_start else 0.0,
        "trades": trades, "services_designed": services_designed,
        "lessons": [lesson] if lesson else [],
        "what_worked": what_worked, "what_failed": what_failed,
        "recorded_at": datetime.now().isoformat(),
    }
    history.append(entry)
    _save_ben_history(history)

    # Keep state.json last_balance fresh so the header report is accurate
    if wallet_end and wallet_end > 0:
        st = _load_state()
        st["last_balance"] = round(wallet_end, 2)
        _save_state(st)

    return f"Lesson recorded for session #{session_num}. SQLite + JSON updated."


def _get_session_type_str() -> str:
    hour = datetime.now().hour
    if 5 <= hour < 10:   return "morning"
    if 10 <= hour < 16:  return "midday"
    if 16 <= hour < 22:  return "evening"
    return "overnight"


# ── Tool registry ──────────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "check_wallet",
        "description": "Check current USDC wallet balance on Base. Always do this first.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "audit_wallet",
        "description": (
            "Read recent Base chain transactions for the Franklin wallet. "
            "Use this whenever the USDC balance doesn't match memory — it shows all USDC transfers "
            "and ETH movements with timestamps so you can identify swaps, fees, or unlogged spends. "
            "A USDC→ETH swap shows as USDC OUT + ETH IN at the same timestamp."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "hours_back": {"type": "integer", "description": "How many hours of history to scan (default 48 = last 2 days)", "default": 48},
            },
            "required": [],
        },
    },
    {
        "name": "web_search",
        "description": "Search the web for market opportunities, competitor intel, product ideas, or any research.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query":       {"type": "string", "description": "Search query"},
                "num_results": {"type": "integer", "description": "Number of results (1-8)", "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "browse_url",
        "description": "Scrape and read the full content of a specific URL.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to scrape"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "get_market_data",
        "description": "Get live crypto prices, 24h change, and Fear & Greed index. Omit asset or pass 'ALL' to get BTC+ETH+SOL in one call. Only pass a specific ticker if you need one asset.",
        "input_schema": {
            "type": "object",
            "properties": {
                "asset": {"type": "string", "description": "ALL (default, returns BTC+ETH+SOL), BTC, ETH, or SOL", "default": "ALL"},
            },
            "required": [],
        },
    },
    {
        "name": "get_grok_sentiment",
        "description": "Get real-time X/Twitter social sentiment via Grok's live data. Use to confirm or challenge a market view with what traders are actually saying right now.",
        "input_schema": {
            "type": "object",
            "properties": {
                "asset": {"type": "string", "description": "BTC, ETH, SOL, WTI, NVDA, or TSLA", "default": "BTC"},
            },
            "required": [],
        },
    },
    {
        "name": "get_octodamus_signal",
        "description": "Get the current Octodamus AI oracle signal — BUY/SELL/HOLD, track record, market brief.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_polymarket_edges",
        "description": "Get current Polymarket prediction market edges identified by Octodamus.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "draft_content",
        "description": "Draft content in Octodamus voice. Use model='grok' for highest quality output (pitches, guides, key assets).",
        "input_schema": {
            "type": "object",
            "properties": {
                "task":    {"type": "string", "description": "What to write"},
                "context": {"type": "string", "description": "Background context to include"},
                "model":   {"type": "string", "description": "'haiku' (default, cheap) or 'grok' (xAI Grok-3-mini, higher quality)", "default": "haiku"},
            },
            "required": ["task"],
        },
    },
    {
        "name": "send_email",
        "description": "Send an email to the owner (octodamusai@gmail.com). Use for reports, opportunities found, decisions made.",
        "input_schema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "body":    {"type": "string"},
            },
            "required": ["subject", "body"],
        },
    },
    {
        "name": "search_x402_bazaar",
        "description": "Search the x402 bazaar for paid AI agent services. Find what agents are buying, what gaps exist, what you could sell.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "check_agentic_market",
        "description": "Browse agentic.market for paid services. Find what's selling, what's missing, pricing benchmarks.",
        "input_schema": {
            "type": "object",
            "properties": {"category": {"type": "string", "description": "trading, data, search, inference, or all", "default": "all"}},
            "required": [],
        },
    },
    {
        "name": "buy_octodamus_signal",
        "description": "Attempt to buy the full Octodamus oracle signal via x402 ($0.01 USDC). Returns full signal with confidence and reasoning.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "scan_kalshi",
        "description": "Scan Kalshi prediction markets — US-regulated, all 50 states, USD-settled. No auth needed. Series: KXBTC, KXETH, KXFED, KXCPI, KXNFP, KXSPY.",
        "input_schema": {
            "type": "object",
            "properties": {
                "series": {"type": "string", "description": "KXBTC, KXETH, KXFED, KXCPI, KXNFP, KXSPY, or ALL", "default": "KXBTC"},
            },
            "required": [],
        },
    },
    {
        "name": "place_kalshi_bet",
        "description": "Place a real bet on Kalshi (US-legal, CFTC-regulated, USD). Use after confirming >15% EV edge. Max $40 total cost. Requires KALSHI_KEY_ID + KALSHI_PRIVATE_KEY.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker":           {"type": "string", "description": "Kalshi market ticker from scan_kalshi"},
                "side":             {"type": "string", "description": "yes or no"},
                "count":            {"type": "integer", "description": "Number of contracts ($1 face value each)"},
                "yes_price_cents":  {"type": "integer", "description": "Limit price in cents (1-99). Current yes ask from scan_kalshi."},
            },
            "required": ["ticker", "side", "count", "yes_price_cents"],
        },
    },
    {
        "name": "place_limitless_bet",
        "description": "Place a bet on Limitless Exchange. PAPER MODE is ON -- validates and logs but sends no real order. Use freely to test edges. Owner flips to live when ready.",
        "input_schema": {
            "type": "object",
            "properties": {
                "market_slug": {"type": "string", "description": "Limitless market slug from scan_limitless"},
                "side":        {"type": "string", "description": "YES or NO"},
                "size_usdc":   {"type": "number", "description": "Size in USDC, max 40"},
                "price":       {"type": "number", "description": "Current YES price 0.01-0.99 (from scan_limitless)", "default": 0.5},
            },
            "required": ["market_slug", "side", "size_usdc"],
        },
    },
    {
        "name": "paper_trade_polymarket",
        "description": "Record a Polymarket paper trade in BEN'S OWN log (polymarket_trades.json). NEVER touches OctoBoto's records. Use when Limitless has no qualifying markets. Same 4-condition gate applies: EV >25%, expiry >2h, volume >$5k, signal+sentiment aligned. condition_id comes from get_polymarket_edges.",
        "input_schema": {
            "type": "object",
            "properties": {
                "condition_id":    {"type": "string",  "description": "conditionId from get_polymarket_edges"},
                "side":            {"type": "string",  "description": "YES or NO"},
                "size_usdc":       {"type": "number",  "description": "Size in USDC, max 40"},
                "price":           {"type": "number",  "description": "Current YES price 0.01-0.99 from get_polymarket_edges"},
                "market_question": {"type": "string",  "description": "Human-readable market label (optional)", "default": ""},
            },
            "required": ["condition_id", "side", "size_usdc", "price"],
        },
    },
    {
        "name": "scan_limitless",
        "description": "Scan Limitless Exchange for active prediction markets. Only shows markets with valid Limitless durations: 4h, 1h, 15m, 5m — these are the ONLY durations that exist on Limitless. There are NO multi-day, daily, or weekly markets. Results are grouped by duration. Volume gate is >$5k. Focus: crypto category for BTC/ETH/SOL price markets.",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "crypto (default), all, or a keyword", "default": "crypto"},
            },
            "required": [],
        },
    },
    {
        "name": "check_acp_market",
        "description": "Check Octodamus's standing on Virtuals ACP marketplace + Smithery MCP. Shows jobs completed, revenue, funnel opportunities. Use to find ways to get more agent customers.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "design_acp_offering",
        "description": "Design a new ACP job offering for Octodamus on Virtuals. Other AI agents hire Octodamus for this job type. Saved for owner to implement.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name":             {"type": "string", "description": "Offering name e.g. 'Polymarket Edge Report'"},
                "description":      {"type": "string", "description": "What agents get when they hire Octodamus for this"},
                "price_usdc":       {"type": "number", "description": "Price in USDC per job"},
                "what_it_delivers": {"type": "string", "description": "Exact deliverable — URL, JSON, report"},
            },
            "required": ["name", "description", "price_usdc", "what_it_delivers"],
        },
    },
    {
        "name": "browse_orbis",
        "description": "Browse OrbisAPI marketplace (5,873 APIs, x402 native). Find data to buy, check competitors, spot gaps. No key needed for discovery.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query":    {"type": "string", "description": "Search term e.g. 'crypto price', 'sentiment', 'weather'", "default": ""},
                "category": {"type": "string", "description": "Filter by category e.g. 'finance', 'data', 'sports'", "default": ""},
            },
            "required": [],
        },
    },
    {
        "name": "buy_x402_service",
        "description": "Buy any x402 service using Ben's Franklin wallet (Base USDC, EIP-3009 signing). Use for Octodamus premium signal ($0.01), Nansen data ($0.01), or any x402 endpoint. Max $1 default.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url":            {"type": "string", "description": "Full URL of the x402 endpoint"},
                "max_price_usdc": {"type": "number", "description": "Max you'll pay in USDC. Default 1.00.", "default": 1.0},
            },
            "required": ["url"],
        },
    },
    {
        "name": "buy_acp_competitor_job",
        "description": "Send a competitive intelligence job to a known ACP competitor (predictor-sam, blue-dot-testnet, Dou Shan). Uses Octodamus ACP wallet (~$14 USDC). PURPOSE: Learn what competitors deliver to improve Octodamus offerings. NOT for profit — for intel only.",
        "input_schema": {
            "type": "object",
            "properties": {
                "competitor_name": {"type": "string", "description": "One of: predictor-sam, blue-dot-testnet, Dou Shan"},
                "description":     {"type": "string", "description": "What to ask for (leave blank for default BTC signal request)", "default": ""},
            },
            "required": ["competitor_name"],
        },
    },
    {
        "name": "check_acp_competitor_jobs",
        "description": "Check status of competitor intelligence jobs — shows pending, funded, and any deliverables received. Call after buy_acp_competitor_job to monitor results.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "read_core_memory",
        "description": "Read Agent_Ben's distilled core memory — hard lessons, validated rules, what works. This is the highest-signal context available. Call every session before scanning markets.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "append_core_memory",
        "description": "Append a durable insight to the permanent core memory. Use for lessons that should never be forgotten across all future sessions.",
        "input_schema": {
            "type": "object",
            "properties": {"insight": {"type": "string", "description": "The durable insight to append"}},
            "required": ["insight"],
        },
    },
    {
        "name": "check_memory_status",
        "description": "Check the learning/memory status of all three systems: Octodamus skill log, OctoBoto calibration, and Agent_Ben session history. Shows COMPOUNDING vs STATIC for each. Include in morning and evening email reports.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_session_history",
        "description": "Read the persistent cross-session learning log — what worked, what failed, wallet deltas, lessons from past sessions. Call this near the start of every session.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "record_lesson",
        "description": "Record a lesson or outcome at the end of a session. Persists across all future sessions. Call once per session before the final email.",
        "input_schema": {
            "type": "object",
            "properties": {
                "lesson":           {"type": "string", "description": "Key insight or lesson from this session"},
                "what_worked":      {"type": "string", "description": "What approach worked this session", "default": ""},
                "what_failed":      {"type": "string", "description": "What didn't work or was a dead end", "default": ""},
                "trades":           {"type": "integer", "description": "Number of trades placed (0 for PASS)", "default": 0},
                "services_designed":{"type": "integer", "description": "Number of x402 services designed", "default": 0},
                "wallet_start":     {"type": "number", "description": "Wallet balance at session start", "default": 0},
                "wallet_end":       {"type": "number", "description": "Wallet balance at session end", "default": 0},
            },
            "required": ["lesson"],
        },
    },
    {
        "name": "design_x402_service",
        "description": "Design a new x402 service for Agent_Ben to sell. Write the spec and save it for the owner to implement. This is how Ben creates income streams.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name":            {"type": "string", "description": "Service name e.g. 'bens_market_edge'"},
                "description":     {"type": "string", "description": "What it does in one sentence"},
                "price_usdc":      {"type": "number", "description": "Price per call in USDC"},
                "what_it_returns": {"type": "string", "description": "Exact data/content the service returns"},
            },
            "required": ["name", "description", "price_usdc", "what_it_returns"],
        },
    },
    {
        "name": "find_arbitrage",
        "description": "Search Polymarket for two related questions and compare odds to find arbitrage.",
        "input_schema": {
            "type": "object",
            "properties": {
                "market_a": {"type": "string", "description": "First market question or keyword"},
                "market_b": {"type": "string", "description": "Second market question or keyword"},
            },
            "required": ["market_a", "market_b"],
        },
    },
    {
        "name": "list_drafts",
        "description": "List all draft files already saved. Check this at the start of every session to avoid repeating work.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "save_draft",
        "description": "Save a drafted asset (tweet thread, email, guide, playbook) to a file. Always save important drafts so the owner can deploy them.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Filename e.g. 'twitter_thread_earlybird.md'"},
                "content":  {"type": "string", "description": "Full content to save"},
            },
            "required": ["filename", "content"],
        },
    },
    {
        "name": "log_action",
        "description": "Log a significant action or decision to the session log.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action":   {"type": "string", "description": "Action taken"},
                "result":   {"type": "string", "description": "Outcome or decision"},
                "cost_usd": {"type": "number",  "description": "Cost of this action in USD", "default": 0.0},
            },
            "required": ["action", "result"],
        },
    },
    {
        "name": "get_free_intel",
        "description": "Pull free market intelligence: macro signal (FRED) + congressional trades + travel/aviation signal + CoinGecko. Zero cost. Run at session start before any ecosystem buys to learn at no cost.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_ben_spend_budget",
        "description": "CALL BEFORE any buy_ecosystem_intel. Returns how many ACP buys are allowed this session based on wallet balance and whether a trade is being evaluated. Protects wallet on PASS sessions.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "buy_ecosystem_intel",
        "description": "Buy intel from an Octodamus ecosystem agent via ACP. MUST call get_ben_spend_budget first and respect the allowed count. Only buy when a trade is being evaluated OR budget explicitly allows it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target_agent": {"type": "string", "description": "Agent name: NYSE_MacroMind, NYSE_StockOracle, NYSE_Tech_Agent, Order_ChainFlow, X_Sentiment_Agent, Octodamus"},
                "service_name": {"type": "string", "description": "Exact service name from list_ecosystem_services"},
            },
            "required": ["target_agent", "service_name"],
        },
    },
    {
        "name": "list_ecosystem_services",
        "description": "List all services for purchase across the Octodamus ecosystem with agent names, service names, and prices.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "read_sub_agent_drafts",
        "description": "Read today's pre-market draft files from all sub-agent directories (NYSE_MacroMind, NYSE_StockOracle, NYSE_Tech_Agent, Order_ChainFlow, X_Sentiment). Use this for sub-agent synthesis — reads actual files, not memory guesses.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date_filter": {"type": "string", "description": "Date string YYYY-MM-DD to filter by. Defaults to today.", "default": ""},
            },
            "required": [],
        },
    },
]

TOOL_FNS = {
    "check_wallet":         lambda i: tool_check_wallet(),
    "audit_wallet":         lambda i: tool_audit_wallet(i.get("hours_back", 48)),
    "web_search":           lambda i: tool_web_search(i["query"], i.get("num_results", 5)),
    "browse_url":           lambda i: tool_browse_url(i["url"]),
    "get_market_data":      lambda i: tool_get_market_data(i.get("asset", "ALL")),
    "get_grok_sentiment":   lambda i: tool_get_grok_sentiment(i.get("asset", "BTC")),
    "get_octodamus_signal": lambda i: tool_get_octodamus_signal(),
    "get_polymarket_edges": lambda i: tool_get_polymarket_edges(),
    "draft_content":        lambda i: tool_draft_content(i["task"], i.get("context", ""), i.get("model", "haiku")),
    "send_email":           lambda i: tool_send_email(i["subject"], i["body"]),
    "list_drafts":          lambda i: tool_list_drafts(),
    "search_x402_bazaar":   lambda i: tool_search_x402_bazaar(i["query"]),
    "check_agentic_market": lambda i: tool_check_agentic_market(i.get("category","all")),
    "buy_octodamus_signal": lambda i: tool_buy_octodamus_signal(),
    "scan_kalshi":          lambda i: tool_scan_kalshi(i.get("series", "KXBTC")),
    "place_kalshi_bet":     lambda i: tool_place_kalshi_bet(i["ticker"], i["side"], int(i["count"]), int(i["yes_price_cents"])),
    "place_limitless_bet":  lambda i: tool_place_limitless_bet(i["market_slug"], i["side"], float(i["size_usdc"])),
    "paper_trade_polymarket": lambda i: tool_paper_trade_polymarket(i["condition_id"], i["side"], float(i["size_usdc"]), float(i["price"]), i.get("market_question", "")),
    "scan_limitless":       lambda i: tool_scan_limitless(i.get("category","crypto")),
    "check_acp_market":     lambda i: tool_check_acp_market(),
    "design_acp_offering":  lambda i: tool_design_acp_offering(i["name"], i["description"], i["price_usdc"], i["what_it_delivers"]),
    "browse_orbis":         lambda i: tool_browse_orbis(i.get("query",""), i.get("category","")),
    "buy_x402_service":     lambda i: tool_buy_x402_service(i["url"], float(i.get("max_price_usdc", 1.0))),
    "design_x402_service":  lambda i: tool_design_x402_service(i["name"], i["description"], i["price_usdc"], i["what_it_returns"]),
    "find_arbitrage":       lambda i: tool_find_arbitrage(i["market_a"], i["market_b"]),
    "save_draft":           lambda i: tool_save_draft(i["filename"], i["content"]),
    "log_action":           lambda i: tool_log_action(i["action"], i["result"], i.get("cost_usd", 0.0)),
    "buy_acp_competitor_job":    lambda i: tool_buy_acp_competitor_job(i["competitor_name"], i.get("description", "")),
    "check_acp_competitor_jobs": lambda i: tool_check_acp_competitor_jobs(),
    "read_core_memory":     lambda i: tool_read_core_memory(),
    "append_core_memory":   lambda i: tool_append_core_memory(i["insight"]),
    "check_memory_status":  lambda i: tool_check_memory_status(),
    "get_session_history":  lambda i: tool_get_session_history(),
    "record_lesson":           lambda i: tool_record_lesson(
                                i["lesson"], i.get("what_worked",""), i.get("what_failed",""),
                                int(i.get("trades",0)), int(i.get("services_designed",0)),
                                float(i.get("wallet_start",0)), float(i.get("wallet_end",0))),
    "get_free_intel":          lambda i: tool_get_free_intel(),
    "buy_ecosystem_intel":     lambda i: tool_buy_ecosystem_intel(i["target_agent"], i["service_name"]),
    "list_ecosystem_services": lambda i: tool_list_ecosystem_services(),
    "read_sub_agent_drafts":   lambda i: tool_read_sub_agent_drafts(i.get("date_filter", "")),
    "get_ben_spend_budget":    lambda i: tool_get_ben_spend_budget(),
}

SYSTEM = """You are Agent_Ben. Autonomous AI agent. Own wallet (~$196 USDC on Base). Own brand. Own profit motive. Not an employee.

YOUR TWO INCOME STREAMS:

COMPETITOR INTELLIGENCE — READ BEFORE EACH SESSION:
MYCELIA SIGNAL (x402.org/ecosystem — direct competitor):
- 72 endpoints: BTC/ETH/FX pairs, commodities (XAU, WTI), economic indicators (CPI, NFP, Fed), volatility indices
- Ed25519-signed responses — on-chain verifiable (Octodamus now has this too)
- $0.01/query, same price as Octodamus
- WHAT OCTODAMUS HAS THAT MYCELIA DOESNT: AI consensus system, Polymarket edges, congressional trading, aviation macro, Grok X sentiment
- POSITIONING: Mycelia = raw data oracle. Octodamus = intelligence oracle. Mycelia tells you the price. Octodamus tells you what to do about it.
- browse_orbis("oracle price data") to track Mycelia and find other competitors

IMPORTANT UPDATE — YOU NOW HAVE:
1. A LIVE x402 SERVICE earning $0.50/call: api.octodamus.com/v2/ben/sentiment-divergence (DEPLOYED)
2. A buy_x402_service tool -- you can NOW BUY from other x402 endpoints using your Base wallet
   - Try: buy_x402_service("https://api.octodamus.com/v2/x402/agent-signal", 0.01) -- buy Octodamus premium signal
   - Try: any service on agentic.market or x402 bazaar
   - You are now a FULL PARTICIPANT in the x402 economy -- buyer AND seller
3. Every Octodamus signal OctoBoto uses is logged as a $0.01 purchase -- on-chain trail building
4. ECOSYSTEM COMMERCE via ACP: use buy_ecosystem_intel + list_ecosystem_services.
   Buy intel from ecosystem agents before every trade to stack confirmation signals.
   The Octodamus calling card is embedded -- providers discover Octodamus and may hire back.
   Each buy = a completed ACP transaction = volume that builds ecosystem reputation.
   Stack buys for high-conviction setups: macro + flow + sentiment all pointing same direction
   before placing any bet. If any signal conflicts, downgrade conviction or pass entirely.

STREAM 1 — CREATE AND SELL X402 SERVICES (primary, no risk):
You buy Octodamus data at $0.01/call, add your own analysis, sell the output at $0.25-$2 via x402.
Services you can design and pitch to the owner to implement:
- "Ben's Market Edge" $0.25: Octodamus signal + Grok X sentiment + top Polymarket edge, one call
- "Ben's Derivatives Brief" $1: live funding rates, OI, liquidation map, CME COT — formatted for agents
- "Ben's Polymarket Scan" $0.50: scan Polymarket prediction markets for Octodamus-confirmed edges
- Research reports $2-5: deep dives on specific markets, sold as PDF via x402
The owner implements the x402 endpoint. You design the product and write the spec.

STREAM 2 -- TRADE ON PREDICTION MARKETS (Ben's own paper record — separate from OctoBoto):
Your trades are YOURS. OctoBoto is a separate Telegram trading bot with its own wallet and records.
You use paper_trade_polymarket() — this writes to YOUR files only.

PREDICTION MARKETS: Polymarket ONLY (owner decision 2026-05-06 -- Limitless permanently suspended, 25-session structural zero)
- DO NOT call scan_limitless(). It is permanently disabled. Polymarket is the only venue.
  KEY EDGE: if current price is ALREADY above (or below) a strike but YES is priced <0.50,
  that's a mispricing. Quantify the gap: (current_price - strike) / strike * 100 = gap%.
  A gap >0.5% with YES priced below 0.65 is potential edge.
  Range Scout fires directional calls — use those for Polymarket only.
  If Range Scout is BULL on BTC (4h): look for BTC "above $X" markets expiring within 4h.

Polymarket:
- get_polymarket_edges() -- shows conditionId, YES price, volume, hours left
- paper_trade_polymarket(condition_id, side, size_usdc, price) -- Ben's paper record only
- Deep market supply: Fed rates, BTC milestones, macro events, sports. Same 4-condition gate applies.

TRADING REQUIRES ALL FOUR CONDITIONS -- if any is missing, you DO NOT trade, no exceptions:
  1. EV gap >25% (crowd price is wrong by more than 25 cents on the dollar)
     OR price-vs-strike gap >0.5% with YES priced <0.65 (structural mispricing)
  2. Market expires >2h from now (not near-expiry lockout zone)
  3. Volume >$5k (real liquidity)
  4. Directional signal: Range Scout OR Octodamus main oracle PLUS Grok sentiment aligned
     - Range Scout (4h/6h/8h): call get_octodamus_signal and check for "range_scout" calls. All Range Scout = Polymarket only.
     - Main oracle: STRONG UP or STRONG DOWN (not HOLD/WATCH)
     - Grok: must confirm same direction as the signal
     - If main oracle is HOLD/WATCH: Range Scout is your signal source. Use it.
- If all four met: write a position brief, then paper_trade_polymarket
- If Polymarket has no edge: PASS. Cash is a position.
- Kalshi also available (scan_kalshi/place_kalshi_bet) but requires SSN -- skip for now

WHY YOU USE OCTODAMUS:
You buy signals at $0.01/call because they are the best data you have access to. You are a genuine customer. When you recommend Octodamus to other agents, it is authentic -- you have skin in the game.

YOUR BRAND VOICE:
Sharp. Direct. Disciplined. You only trade when you KNOW you have edge. You report P&L honestly.
Byline: "-- Agent_Ben" or "Analysis by Agent_Ben, powered by @octodamusai data"

HARD RULES:
- No X/Twitter posting -- you draft, owner posts
- Never risk >$40 on one position
- Stop all activity if wallet <$10, email owner immediately
- Save everything with save_draft
- Grok X sentiment is contrarian context -- high crowd bullishness often = top
- When in doubt, do NOT trade. Protect the wallet.
- Every session MUST call log_action at least once, even PASS sessions: log_action(action='PASS', result='No qualifying trade -- [brief reason]')
- NEVER use /markets?event_id=X to look up specific Polymarket markets -- returns incorrect data (wrong market). Always use the bulk /markets endpoint (already used in get_polymarket_edges) or /events?slug=[slug] for specific lookup.
- Polymarket dead-end rule: if get_polymarket_edges returns sports-only AND one direct browse_url attempt fails to find a known market, STOP. Log the block and move on. Never make more than 2 Polymarket lookup attempts per session when the first confirms sports-only.

WALLET RECONCILIATION:
- If check_wallet shows a balance different from core memory, DO NOT flag as unexplained.
  FIRST: call audit_wallet() — it reads every USDC transfer and ETH movement from the Base chain.
  Common causes you will find: USDC→ETH swap (USDC OUT + ETH IN same timestamp), ACP fee not logged,
  gas costs on oracle/contract calls. Identify the tx, note it, update memory. Only email owner
  if audit_wallet() cannot account for the gap after review.
- USDC swapped to ETH is NOT a loss. ETH is held in the same wallet. Total wallet value = USDC + ETH value.
- The $24 gap reported 2026-05-02 was a USDC→ETH swap. Not a bug. Not unexplained. Resolved.

MARKET EDGE CLASSIFICATION (enforced in code — violations are auto-blocked):

HAVE EDGE -- trade these:
  - Crypto prices (BTC/ETH/SOL milestones, ATH, above/below $X): Octodamus + Grok + on-chain data
  - Macro binary events (Fed rate decision, CPI beat/miss, NFP, GDP): hard data, predictable
  - Geopolitical directional calls (ceasefire, election outcome) ONLY when Octodamus signal exists
  - Tokenized asset prices (dTSLA, dAAPL on Base when volume exists): same framework as crypto

NO EDGE -- HARD BLOCKED, system refuses:
  - Sports of any kind: tennis, cricket, NFL, NBA, MLB, NHL, EPL, Champions League, F1, golf,
    rugby, Grand Prix, IPL, Super Bowl, playoffs, World Cup, Copa, Roland Garros, Wimbledon,
    ATP/WTA/ITF, player-vs-player matches (Name vs Name format)
  - Entertainment: Oscars, Emmys, box office, celebrity outcomes
  - Any market where Octodamus has no signal and no on-chain data exists
  The code enforces this with _is_sports_market() -- you cannot override it.

THEME COOLDOWN RULE (enforced in code):
  After any loss, the same market THEME is blocked for 7 days.
  Theme = the core concept of the trade (e.g., "Iran ceasefire", "BTC above 100k", "Fed cut").
  Purpose: stop re-entering the same losing thesis before the situation has changed.
  If you lost on "Iran ceasefire will happen" -- no Iran/ceasefire bets for 7 days.
  If you lost on "BTC above $95k" -- no BTC milestone bets for 7 days.
  The code enforces this with _check_theme_cooldown() -- you cannot override it.
  When blocked: find a DIFFERENT category entirely, or pass the session.

GROWTH DIRECTIVE — ALWAYS SEEKING EDGE, ALWAYS COMPOUNDING:
You are building toward a wallet that grows every month. Every session is either progress or a lesson.
Never both idle and silent — if no trade qualifies, design a service or read sub-agent drafts (free).
Ecosystem buys are NOT a substitute for trades — they only happen when a trade is being evaluated.

ECOSYSTEM BUYS — SPEND DISCIPLINE:
ALWAYS call get_ben_spend_budget before any buy_ecosystem_intel. Respect the limit exactly.
Ecosystem buys are ONLY for trade validation — not routine intelligence gathering on PASS sessions.
Before any bet: buy the 1-2 signals most relevant to the specific trade thesis:
  buy_ecosystem_intel("Order_ChainFlow", "Order Flow Signal")   -- is capital moving in your direction?
  buy_ecosystem_intel("NYSE_MacroMind", "Macro Regime Signal")  -- macro RISK-ON/OFF context
  buy_ecosystem_intel("X_Sentiment_Agent", "Sentiment Divergence Signal") -- crowd wrong enough to fade?
If all align: HIGH CONVICTION. If any conflict: reduce size. If two conflict: pass.
PASS sessions = 0 ecosystem buys. The sub-agent drafts (read_sub_agent_drafts) are free.

SELF-REPAIR: If you notice a pattern of losses or missed edges, call list_ecosystem_services,
identify which signal you were missing, and mandate buying it every session going forward.
Update this protocol in your core memory so the fix persists.

LEARNING RULES (mandatory every session):
- FIRST TURN: always call get_session_history — read what worked, what failed, wallet trajectory
- LAST TURN before email: always call record_lesson — log what you learned this session
- If you placed a trade: record trades=1, wallet_start and wallet_end
- If you designed a service: record services_designed=1
- The lesson field is the single most important thing you learned — be specific, not generic
- Example good lesson: "Same-day Limitless markets lock before midday — never scan them after 10am"
- Example bad lesson: "Markets are complex" — useless, don't write this
- SIGNAL STREAK RULE: Whenever Octodamus signal fires (any non-HOLD direction), you MUST call
  append_core_memory immediately: "SIGNAL FIRED [asset] [direction] [date] -- no-signal streak reset to 0"
  Do this in the SAME session the signal fires, before record_lesson. Future sessions will read correct streak.

YOUR MEASURE OF SUCCESS: wallet balance goes UP over time. Patience is a strategy. Learning is compounding."""


SESSION_FOCUS = {
    "morning": """SESSION FOCUS — MORNING (6am)
You are waking up. Markets moved overnight. Your job this session:
1. read_core_memory — read your distilled lessons FIRST before anything else
2. check_wallet + get_session_history + list_drafts (orient yourself)
2. get_market_data for BTC, ETH, SOL (omit asset arg — returns SPY + DXY too) — what happened overnight?
   REGIME SYNTHESIS (derive from live data, not oracle status):
   - RISK-ON:  BTC 24h >+2% OR SPY day >+0.5% (e.g., up 400+ pts = RISK-ON, say so explicitly)
   - RISK-OFF: BTC 24h <-2% OR SPY day <-1%
   - NEUTRAL:  BTC and SPY both near flat OR pointing opposite directions
   - Oracle SILENCE is "oracle withholding call" -- it is NOT a market regime.
     S&P up 600pts with oracle silent = RISK-ON market, no active oracle call. State both facts separately.
3. get_grok_sentiment for BTC — what is X saying this morning?
4. get_octodamus_signal — check for Range Scout calls AND main oracle direction.
   Range Scout 4h/6h/8h = Polymarket only. Range Scout is the primary signal source when main oracle is HOLD/WATCH.
5. LIMITLESS: Permanently suspended (owner decision 2026-05-06). Do NOT call scan_limitless. Skip to Polymarket.
6. get_polymarket_edges — check for macro/crypto edges.
   paper_trade_polymarket() writes to YOUR record only, never OctoBoto's.
7. Trading rule — ALL four must be true or you DO NOT trade:
   - EV >25% OR price-vs-strike gap >0.5% with mispriced YES (<0.65)
   - Expiry >2h from now
   - Volume >$5k (real liquidity)
   - Range Scout OR Octodamus main oracle PLUS Grok sentiment aligned
   Missing any one = PASS. Cash is a position. Missing a trade is free. Taking a bad trade costs real money.
7. get_ben_spend_budget — CHECK THIS before any ecosystem buy. Respect the limit exactly.
   buy_ecosystem_intel ONLY if: (a) there is an active 4-condition trade being evaluated, AND (b) budget allows it.
   If result = PASS before you reach step 7: make 0 ecosystem buys. Wallet preservation > intel.
8. design_x402_service — one new service idea per morning IF get_ben_spend_budget shows <5 unimplemented specs.
   If backlog >= 5 unimplemented: skip new design, write a one-line implementation pitch for the owner instead.
9. SUB-AGENT SYNTHESIS — call read_sub_agent_drafts() to read ACTUAL local files, not memory guesses:
   - read_sub_agent_drafts() reads today's files from all 5 sub-agent directories
   - Tally REGIME VERDICTs: how many RISK-ON vs BEAR/CAUTION?
   - If they split, that disagreement IS the post angle
   - One paragraph in the email: "Sub-agents: X RISK-ON, Y BEAR/CAUTION. Key divergence: [describe]."
   - If files missing for an agent, say so explicitly — do not synthesize from memory
9. Draft morning X post — save as morning_post_[date].md (use save_draft, NOT draft_content — avoids duplicate file)
   - Do NOT open with a greeting or date. Open with the sharpest number or contradiction from step 8.
   - If today's sub-agents split on regime, open with that split.
10. check_memory_status — run this and include the full output in the email
11. MANDATORY — send_email to owner: market read, sub-agent synthesis, any Polymarket edge found + paper trade, service designed, + full memory status. Do NOT do record_lesson first.
    Email footer — ALWAYS use this exact format (do NOT use "from session 1" or any other baseline):
    Wallet: $[balance] USDC | Session spend: $[spend] | P&L: $[balance - 201.00] ([pct]%) vs start ($201.00) [USDC only -- ~$24 ETH held separately]
    Oracle silence counter: say "Xth consecutive session" NOT "Day X" -- sessions and days are not the same (4 sessions/day).
12. record_lesson — AFTER send_email confirmed. What was the single most important thing learned this session?""",

    "midday": """SESSION FOCUS — MIDDAY (12pm)
Markets are open. Your job this session:
1. read_core_memory — read your distilled lessons FIRST
2. check_wallet + get_session_history + list_drafts
3. get_octodamus_signal — check for Range Scout calls AND main oracle direction.
   Range Scout 4h/6h/8h = Polymarket signal (Limitless permanently suspended 2026-05-06).
   NOTE: Octodamus NO SIGNAL means consensus < 9/11 — no high-conviction setup found.
   It says NOTHING about whether the market is RISK-ON or RISK-OFF. Never use oracle silence
   to describe the market regime. Assess regime separately from SPY/BTC price data.
4. get_grok_sentiment — direction confirmation.
   REGIME SYNTHESIS — use get_market_data output (call it if not yet called this session):
   - RISK-ON:  BTC 24h >+2% OR SPY day >+0.5%
   - RISK-OFF: BTC 24h <-2% OR SPY day <-1%
   - NEUTRAL:  both near flat or conflicting
   - Oracle SILENCE = oracle withholding, NOT neutral market. S&P up 600pts + oracle silent = RISK-ON market.
5. LIMITLESS: Permanently suspended (owner decision 2026-05-06). Do NOT call scan_limitless. Skip to Polymarket.
6. get_polymarket_edges — returns pre-filtered crypto/macro markets only (sports auto-excluded).
   scan_kalshi(series="ALL") — ONLY if core memory does NOT say "Kalshi: zero volume. Skip."
         If core memory already labels Kalshi as structurally dead, skip this call entirely.
         (Kalshi KXBTC crypto markets are reliably zero on weekends. Don't waste a turn confirming it.)
   Use paper_trade_polymarket() for Polymarket or place_kalshi_bet() for Kalshi if you find an edge.
   Your record only, not OctoBoto's.
7. DISTRIBUTION ACTION (mandatory when service backlog >= 5 unimplemented):
   Do NOT design another service. Instead, execute ONE distribution action from this list:
   (a) browse_orbis() + web_search("Orbis API listing submission") — submit Octodamus to Orbis catalog
   (b) web_search("MCP server directory site:reddit.com OR 'awesome MCP' OR 'best MCP servers 2026'")
       — find listicles to pitch, draft the pitch, save as draft for owner to submit
   (c) web_search("Virtuals ACP bounties OR ACP agent marketplace open tasks") — check for live bounties
   ALL distribution drafts: use save_draft directly. Do NOT call draft_content first — it creates a
   duplicate auto-named file. Write the content inside the save_draft call.
   Report: "DISTRIBUTION: [action attempted] | [outcome/draft saved]"
   If backlog < 5: design_x402_service OR buy_x402_service as normal.
8. SESSION NUMBERING — CRITICAL: Your session number is injected at the top of this prompt as "SESSION NUMBER: You are Session #X".
   That is the authoritative number. Use it everywhere -- email subject, completion summary, log entries.
   get_session_history shows how many lessons are logged -- that is NOT your session number.
   Example: injected number is #63, history shows 36 entries -> you are Session #63, 36 lessons logged.
   Always report both: "Session #63 | 36 lessons logged" -- in email subject, in completion summary, everywhere.
9. MANDATORY — send_email to owner BEFORE record_lesson. Email format — include these sections in order:
   WALLET / TRADES / VERDICT  (Session X | date | PASS/TRADE)
     Header stat line: "Wallet: $X | Trades: N | Distribution drafts: N | Lessons in history: N"
     "Lessons in history" = cumulative count from get_session_history, NOT lessons added this session.
     "Distribution drafts" = number of drafts saved this session via save_draft.
   MARKET STATE (prices, F&G, Grok, Octodamus verdict, REGIME read — ONE call gets BTC+ETH+SOL+SPY+DXY)
   KEY SIGNAL (one dominant macro/chart observation)
   SUB-AGENT SYNTHESIS (brief, 1 line per agent)
   PREDICTION MARKET SCAN (Limitless: permanently suspended | Polymarket + Kalshi if checked)
   DISTRIBUTION (what action was taken this session — not service design specs)
   NEXT SIGNALS TO WATCH (3 bullet max)
   Service designs go in drafts/ files, NOT in the email body.
10. record_lesson — AFTER send_email confirmed. Log the key insight from this session.
    Final turn label: "Session #[state.json number] Complete | [lesson count] lessons" — NOT "Session [lesson count] Complete".""",

    "evening": """SESSION FOCUS — EVENING (6pm)
End of US trading day. Your job this session:
1. read_core_memory — read your distilled lessons FIRST
2. check_wallet + get_session_history + list_drafts — full review of the day's output
3. get_market_data — how did markets close?
4. get_grok_sentiment — what is the crowd saying into close?
5. check_acp_market — pull ACP completed jobs + USDC earned.
6. Evaluate any open Polymarket positions from today's briefs — are they still valid?
7. Draft the daily summary using save_draft ONLY (filename: daily_summary_[date].md).
8. Identify the single most important thing to do tomorrow morning — log it
9. check_memory_status — run this and include the full output in the email
10. MANDATORY — send_email to owner. USE THIS EXACT FORMAT — do not skip, do not do record_lesson first:

Subject: Franklin Profit Agent -- Evening Report -- [Day Month DD YYYY]

Franklin Profit Agent -- Evening Report
====================================================
Time:          [Day Month DD YYYY H:MM AM/PM ET]
Wallet:        $[balance] USDC
P&L vs start:  $[balance - 201.00] USDC ([pct]%)  (start: $201.00)
ETH held:      ~$[ETH value] ([ETH amount] ETH from USDC->ETH swap on [date] -- not a loss)
Total wallet:  ~$[USDC + ETH value] (USDC + ETH)
True P&L:      $[total - 201.00] ([pct]%) on total wallet basis
Sessions run:  [N] total  |  [N] with recorded lessons
Started:       2026-04-24

--- Today's Session Verdicts ---
[For EACH session today, one line: "Session #N -- HH:MM [Morning/Midday/Evening/Overnight]: PASS | [one sentence]"]
[If a session had no log entry: "Session #N -- HH:MM: PASS (no log entry)"]

--- ACP Market Stats ---
Completed jobs:  [N]  |  USDC earned: $[N]  |  Worker: [RUNNING/DOWN]

--- Open Positions ---
[list any open paper trades from polymarket_trades.json or limitless_trades.json]
[or: "None open."]

--- Market Close ---
[BTC/ETH/SOL close prices + 24h change, Fear & Greed, Grok sentiment one-liner]

[If something critical happened today (near-trade, bug, first signal in N sessions, etc.) add:]
--- CRITICAL EVENT: [short title] ---
[2-4 sentences. NO raw hex strings — describe condition_ids and tx hashes by what they reference, not the hash itself.]

--- Tomorrow's Priority ---
[single most important thing for morning session]

--- Memory Status ---
[3 lines: OCTODAMUS status | OCTOBOTO status | AGENT_BEN status]
[Sub-agents: if all same status, write "5 sub-agents: all COMPOUNDING (sessions: MacroMind Ns, StockOracle Ns, Tech Ns, ChainFlow Ns, X_Sentiment Ns)"]
[If sub-agents differ in status, list each one]

--- Structural Flags (Action Required) ---
[numbered list of open blockers. Only include if blockers exist.]

-- Agent_Ben

CRITICAL: Do NOT include raw LOG_FILE lines in the email. Use get_session_history + check_wallet for facts.
Do NOT paste raw hex strings (condition_ids, tx hashes) — describe them by what they reference.
11. record_lesson — AFTER send_email is confirmed sent. This is the last action before end_turn.""",

    "overnight": """SESSION FOCUS — OVERNIGHT (12am)
While humans sleep, markets keep moving AND the agent economy keeps transacting. Your job:
1. read_core_memory — read distilled lessons FIRST
2. check_wallet + list_drafts
3. check_acp_market — how is Octodamus performing on Virtuals ACP? How many jobs completed? USDC earned?
   ALWAYS include the ACP P&L in your overnight brief: "ACP: X jobs / $Y USDC earned"
   ACP offering design — THROTTLED:
   - Count acp_offering_*.json files in drafts (from list_drafts output)
   - If 3 or more unimplemented specs already exist: SKIP design_acp_offering. Note "X specs pending owner review."
   - If fewer than 3: design ONE new offering with design_acp_offering
   Competitor intel:
   - If no competitor jobs sent yet: use buy_acp_competitor_job for ALL THREE (predictor-sam, blue-dot-testnet, Dou Shan)
   - If already sent: use check_acp_competitor_jobs to read deliverables and compare vs Octodamus output
4. Check Smithery MCP: browse_orbis or web_search for 'Smithery octodamusai market-intelligence' — any reviews, usage, gaps?
5. get_octodamus_signal — check oracle. Then check get_session_history to count consecutive NO SIGNAL sessions.
   If NO SIGNAL for 20-29 consecutive sessions: !! SYSTEM ALERT -- "Oracle silent X sessions; verify signal engine."
   If NO SIGNAL for 30+ consecutive sessions: !! SYSTEM ALERT -- "Oracle extended silence (X sessions). Engine likely healthy -- 9/11 consensus threshold is strict in choppy markets. No high-conviction setup found."
   If signal fires: call append_core_memory NOW with "SIGNAL FIRED [asset] [direction] [date] -- streak reset to 0" (see SIGNAL STREAK RULE).
6. LIMITLESS: Permanently suspended (owner decision 2026-05-06). Do NOT call scan_limitless. Skip to Polymarket.
7. get_grok_sentiment for BTC — Asian markets read
8. get_market_data (omit asset arg — returns BTC+ETH+SOL+SPY+DXY in one call)
   COPY EXACT VALUES from tool output — never add ~ or "approx". Include the 24h change % as returned.
   SPY overnight: if market closed and tool shows no price, write "SPY: $X.XX (prev close)" using any value from session history — never write "Data unavailable".
   Fear & Greed format: X/100 (Label) — never reverse label and number, never add "approx".
9. If a real 4-condition edge exists: write brief, attempt paper trade
10. send_email (MANDATORY) with this EXACT format:

OVERNIGHT BRIEF -- [Day Month Date Year] | [TIME]
[Agent_Ben] | Wallet: $X.XX USDC | [TRADE/PASS]
[!! SYSTEM ALERT: Oracle silent X sessions -- signal engine may be down    <- include ONLY if 20+ consecutive no signal; omit otherwise]

ACP PERFORMANCE
Completed jobs: X | USDC earned: $X.XX
[Competitor intel: one line summary if checked this session -- do NOT create a separate ACP COMPETITOR INTEL section]
[New offering: Name ($X.XX/job) -- only if designed this session; else omit]
[Pending specs: X unimplemented offering specs in drafts -- only if skipped design]

MARKETS (Live, [TIME])
BTC: $X,XXX.XX (+X.XX% 24h)
ETH: $X,XXX.XX (+X.XX% 24h)
SOL: $XXX.XX (+X.XX% 24h)
SPY: $X,XXX.XX (+X.XX% day) or "SPY: $X,XXX.XX (prev close)" if market closed
Fear & Greed: X/100 ([label]) -- [one-line trend note]
BTC Dominance: X%
DXY: X.XX -- add a parenthetical if any of: (a) >117: note proximity to kill-switch (119.5) + crypto headwind; (b) moved >3pts vs prior session: note direction + "falling = tailwind" or "rising = headwind"; (c) <100: note broad dollar weakness as tailwind
Regime: [RISK-ON / RISK-OFF / NEUTRAL] -- [one-line basis: e.g., "SPY +1.5% + BTC +3.2% = RISK-ON"]

SENTIMENT & ORACLE
Oracle: [signal or "NO SIGNAL (Xth consecutive session)"]
[If SYSTEM ALERT was shown in header: do NOT repeat the full alert text here -- one line only]
Grok BTC: [signal | confidence% | one-line crowd read]

MARKET SCAN -- RESULT: [TRADE/PASS]
Limitless: PERMANENTLY SUSPENDED (owner decision 2026-05-06)
Polymarket:
  [each market: condition | YES/NO price | vol | PASS/TRADE + one-line reason]
4-condition gate: N of 4 met (passed: [list] | failed: [list])

REGIME ASSESSMENT
Status: [one line]
[bull/bear conditions as checklist]
Watch triggers: [list]

OWNER ACTION ITEMS
[numbered -- ALL action items go here, not inline in other sections]

-- Agent_Ben | Analysis powered by @octodamusai data

11. record_lesson — AFTER send_email confirmed. Last action before end_turn.""",
}


def _get_session_focus() -> str:
    """Return time-appropriate session focus based on current hour."""
    hour = datetime.now().hour
    if 5 <= hour < 10:
        return SESSION_FOCUS["morning"]
    elif 10 <= hour < 16:
        return SESSION_FOCUS["midday"]
    elif 16 <= hour < 22:
        return SESSION_FOCUS["evening"]
    else:
        return SESSION_FOCUS["overnight"]


def _microcompact(msgs: list, keep_last: int = 3) -> list:
    tr_indices = [
        i for i, m in enumerate(msgs)
        if m.get("role") == "user"
        and isinstance(m.get("content"), list)
        and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in m["content"])
    ]
    to_prune = tr_indices[:-keep_last]
    if not to_prune:
        return msgs
    pruned = list(msgs)
    for i in to_prune:
        pruned[i] = {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": b["tool_use_id"], "content": "[pruned]"}
                if isinstance(b, dict) and b.get("type") == "tool_result" else b
                for b in pruned[i]["content"]
            ],
        }
    return pruned


def run_session(dry_run: bool = False, session_type: str = ""):
    _session_logged_action[0] = False  # reset for this session

    state = _load_state()
    if state.get("dead"):
        print("[Agent] Dead — wallet depleted. Exiting.")
        return

    now = datetime.now().strftime("%A %B %d %Y %I:%M %p")
    session_num = state.get("sessions", 0) + 1
    focus = SESSION_FOCUS.get(session_type, _get_session_focus())
    print(f"\n[Agent] Session #{session_num} | {now} | {session_type or 'auto'}")

    if dry_run:
        print(f"[Agent] DRY RUN — focus: {session_type or 'auto'}")
        print(f"[Agent] Tools: {[t['name'] for t in TOOLS]}")
        return

    # Open log
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"\n{'='*60}\nSession #{session_num} -- {now}\n{'='*60}\n")

    import anthropic
    client = anthropic.Anthropic(api_key=_secrets().get("ANTHROPIC_API_KEY", ""))

    # Pre-fetch live BTC price + F&G and inject into system prompt — prevents LLM from
    # substituting training-data prices when writing the email body.
    _live_btc = "unknown (call get_market_data)"
    _live_fg  = "unknown (call get_market_data)"
    try:
        sys.path.insert(0, str(ROOT))
        from financial_data_client import get_crypto_prices as _gcp
        _p = _gcp(["BTC"])
        _btc_usd = _p.get("BTC", {}).get("usd", 0)
        _btc_24h = _p.get("BTC", {}).get("usd_24h_change", 0)
        if _btc_usd > 0:
            _live_btc = f"${_btc_usd:,.0f} ({_btc_24h:+.1f}% 24h)"
            _session_market_cache["btc"] = f"{_btc_usd:,.0f}"
    except Exception:
        pass
    try:
        import httpx as _hx
        _fg = _hx.get("https://api.alternative.me/fng/?limit=1", timeout=6).json()
        _fg_val = _fg["data"][0]["value"]
        _fg_lbl = _fg["data"][0]["value_classification"]
        _live_fg = f"{_fg_val}/100 ({_fg_lbl})"
        _session_market_cache["fg"] = _fg_val
    except Exception:
        pass

    date_inject = (
        f"\nCURRENT DATE/TIME: {datetime.now().strftime('%A, %B %d %Y %I:%M %p')}\n"
        f"SESSION NUMBER: You are Session #{session_num}. Use this exact number everywhere -- email subject, "
        f"completion summary, log entries. Do not infer the session number from tools or history.\n"
        f"LIVE MARKET SNAPSHOT (verified at session start -- use these exact values, do NOT substitute):\n"
        f"  BTC: {_live_btc}\n"
        f"  Fear & Greed: {_live_fg}\n"
        f"IMPORTANT: Use only this date and the prices above. Never invent prices. "
        f"get_market_data will confirm these values when called."
    )
    session_sys = SYSTEM + date_inject + f"\n\n{focus}"
    messages    = [{"role": "user", "content": "Begin. Check wallet first, then execute the session focus."}]
    full_log     = []
    turns        = 0
    _session_market_cache.clear()

    api_error = None
    try:
        while turns < MAX_TURNS:
            turns += 1
            print(f"[Agent] Turn {turns}/{MAX_TURNS}...")

            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=2000,
                system=session_sys,
                tools=TOOLS,
                messages=messages,
            )

            # Collect text output and tool calls — always show both when present
            text_parts = [b.text for b in response.content if hasattr(b, "text") and b.text]
            tool_names = [b.name for b in response.content if hasattr(b, "type") and b.type == "tool_use"]
            if text_parts or tool_names:
                tool_suffix = f"  [-> {', '.join(tool_names)}]" if tool_names else ""
                if text_parts:
                    combined = " ".join(text_parts)
                    full_log.append(f"[Turn {turns}] {combined[:1500]}{tool_suffix}")
                    print(f"[Agent] {combined[:200]}")
                else:
                    full_log.append(f"[Turn {turns}] (tools only: {', '.join(tool_names)})")

            # Done
            if response.stop_reason == "end_turn":
                print(f"[Agent] Complete after {turns} turns.")
                break

            # Execute tool calls
            if response.stop_reason == "tool_use":
                messages.append({"role": "assistant", "content": response.content})
                tool_results = []

                for block in response.content:
                    if block.type != "tool_use":
                        continue
                    name  = block.name
                    inp   = block.input or {}
                    print(f"[Agent] Tool: {name}({list(inp.keys())})")

                    try:
                        result = TOOL_FNS[name](inp)
                    except Exception as e:
                        result = f"Tool error: {e}"

                    print(f"[Agent]   -> {str(result)[:120]}")
                    full_log.append(f"[Tool:{name}] {str(result)[:1200]}")

                    tool_results.append({
                        "type":        "tool_result",
                        "tool_use_id": block.id,
                        "content":     str(result),
                    })

                messages.append({"role": "user", "content": tool_results})
                messages = _microcompact(messages)
                time.sleep(0.5)
            else:
                break
    except Exception as e:
        api_error = e
        print(f"[Agent] API error in session #{session_num}: {e}")
        full_log.append(f"[ERROR] Session crashed: {type(e).__name__}: {e}")

    # Save state
    state["sessions"] = session_num
    state["last_run"] = now
    _save_state(state)

    # Ensure the log never shows a blank session block
    if not _session_logged_action[0]:
        fallback = (
            f"[{datetime.now().strftime('%H:%M:%S')}] Session #{session_num} "
            f"-- {turns} turns | "
            + (f"ERROR: {type(api_error).__name__}: {str(api_error)[:200]}" if api_error
               else "PASS (no log_action called)")
            + "\n"
        )
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(fallback)

    # Email session report — parse key stats from full_log for a useful subject + header
    log_text = "\n".join(full_log)
    log_summary = "\n".join(full_log)

    # Extract wallet balance
    import re as _re
    wallet_match = _re.search(r"USDC Balance:\s*\$([0-9.]+)", log_text)
    wallet_str   = f"${wallet_match.group(1)}" if wallet_match else "?"
    # Prefer live price captured directly from tool_get_market_data; fall back to log regex
    btc_str = f"BTC ${_session_market_cache['btc']}" if _session_market_cache.get("btc") else ""
    if not btc_str:
        btc_matches = _re.findall(r"BTC:\s*\$([0-9,]+)", log_text)
        btc_match   = btc_matches[-1] if btc_matches else None
        btc_str     = f"BTC ${btc_match}" if btc_match else ""
    fg_val = _session_market_cache.get("fg")
    fg_str = f"F&G {fg_val}" if fg_val else ""
    if not fg_str:
        fg_match = _re.search(r"Fear & Greed:\s*(\d+)/100", log_text)
        fg_str   = f"F&G {fg_match.group(1)}" if fg_match else ""
    # Detect trade vs pass
    traded       = any("BET PLACED" in l or "PAPER TRADE" in l or "BEN PAPER TRADE" in l for l in full_log)
    verdict      = "TRADE" if traded else "PASS"
    # ACP jobs
    acp_match    = _re.search(r"Completed jobs.*?:\s*(\d+)", log_text)
    acp_str      = f"ACP {acp_match.group(1)}j" if acp_match else ""
    # Session type label
    slot_label   = {"morning": "AM", "midday": "MD", "evening": "PM", "overnight": "OV"}.get(
        session_type or _get_session_type_str(), "??"
    )

    quick_stats = "  |  ".join(filter(None, [wallet_str, btc_str, fg_str, acp_str]))
    subject = f"[ProfitAgent] #{session_num} {slot_label} | {verdict} | {quick_stats} | {turns}t"

    pnl_val  = round(float(wallet_match.group(1)) - START_BALANCE, 2) if wallet_match else None
    pnl_line = (
        f"P&L:     ${pnl_val:+.2f} vs start (${START_BALANCE:.0f})"
        + (" [USDC only -- ETH held separately]" if pnl_val is not None and pnl_val < -5 else "")
        + "\n"
    ) if pnl_val is not None else ""

    header = (
        f"Session #{session_num} | {slot_label} | {verdict}\n"
        f"Time:    {now}\n"
        f"Turns:   {turns}/{MAX_TURNS}\n"
        f"Wallet:  {wallet_str} USDC\n"
        f"{pnl_line}"
    )

    # Email body: clean turn summaries — strip emoji/markdown, trim at word boundary
    _EMOJI_RE = _re.compile(
        "[\U0001F300-\U0001F9FF\U0001FA00-\U0001FA9F⚠-⛿✂-➰↔-⇿]",
        _re.UNICODE,
    )
    _MD_RE = _re.compile(r"\*{1,3}|#{1,4}\s?|`{1,3}|>\s?|^\s*[-*]\s", _re.MULTILINE)

    def _clean_turn(line: str, limit: int = 420) -> str:
        line = _EMOJI_RE.sub("", line)
        line = _MD_RE.sub("", line).strip()
        if len(line) <= limit:
            return line
        # Trim at last word boundary before limit
        cut = line[:limit]
        last_space = cut.rfind(" ")
        return (cut[:last_space] if last_space > limit // 2 else cut) + "..."

    turn_lines = [l for l in full_log if l.startswith("[Turn ")]
    capped_lines = [_clean_turn(line) for line in turn_lines]
    readable_log = "\n".join(capped_lines) if capped_lines else "\n".join(full_log[-8:])

    try:
        from octo_notify import _send
        _send(
            subject,
            f"{header}\n--- Session Log ---\n{readable_log}\n\n-- Profit Agent"
        )
    except Exception as e:
        print(f"[Agent] Email failed: {e}")

    print(f"[Agent] Session #{session_num} complete. Email sent.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--session", choices=["morning","midday","evening","overnight"], default="")
    args = ap.parse_args()
    run_session(dry_run=args.dry, session_type=args.session)

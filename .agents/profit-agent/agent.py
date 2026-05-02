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


def tool_get_market_data(asset: str = "BTC") -> str:
    """Get live price, 24h change, funding rate for BTC/ETH/SOL."""
    try:
        sys.path.insert(0, str(ROOT))
        from financial_data_client import get_crypto_prices
        asset = asset.upper()
        prices = get_crypto_prices([asset] if asset in ("BTC","ETH","SOL") else ["BTC","ETH","SOL"])
        lines = ["Live market data:"]
        for t, d in prices.items():
            lines.append(f"  {t}: ${d.get('usd',0):,.2f} ({d.get('usd_24h_change',0):+.2f}% 24h)")
        # Fear & Greed
        try:
            import httpx
            fg = httpx.get("https://api.alternative.me/fng/?limit=1", timeout=6).json()
            val = fg["data"][0]["value"]
            label = fg["data"][0]["value_classification"]
            lines.append(f"  Fear & Greed: {val}/100 ({label})")
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


def tool_get_polymarket_edges() -> str:
    """Get current Polymarket markets and prices for edge hunting. Includes conditionId for paper trading."""
    try:
        import httpx
        from datetime import datetime, timezone
        r = httpx.get(
            "https://gamma-api.polymarket.com/markets",
            params={"active": True, "closed": False, "limit": 20,
                    "order": "volume", "ascending": False},
            timeout=10
        )
        if r.status_code == 200:
            markets = r.json()
            now = datetime.now(timezone.utc)
            lines = ["Active Polymarket markets by volume (include conditionId for paper_trade_polymarket):"]
            for m in markets[:12]:
                q      = m.get("question", "")[:75]
                prices = m.get("outcomePrices", [])
                if isinstance(prices, str):
                    try:
                        import json as _j; prices = _j.loads(prices)
                    except Exception:
                        prices = []
                yes    = prices[0] if prices else "?"
                vol    = m.get("volume", 0)
                cid    = m.get("conditionId", "")
                exp    = m.get("endDateIso", "")[:10]
                # Hours until expiry
                hours_left = ""
                try:
                    if m.get("endDateIso"):
                        exp_dt = datetime.fromisoformat(m["endDateIso"].replace("Z", "+00:00"))
                        h = (exp_dt - now).total_seconds() / 3600
                        hours_left = f" | {h:.0f}h left"
                except Exception:
                    pass
                lines.append(
                    f"  conditionId={cid} | YES={yes} | Vol=${float(vol or 0):,.0f}"
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


def tool_scan_limitless(category: str = "crypto", min_hours: int = 0) -> str:
    """
    Scan Limitless Exchange active markets.
    min_hours: only show markets expiring at least this many hours from now.
    Use min_hours=2 to avoid near-expiry lockout. Limitless markets max out at 4h (5min/15min/1hr/4hr).
    """
    try:
        import httpx
        from datetime import datetime, timezone, timedelta

        r = httpx.get("https://api.limitless.exchange/markets/active", timeout=10)

        if r.status_code == 200:
            markets = r.json().get("data", [])
            now = datetime.now(timezone.utc)

            # Filter by expiry if min_hours specified
            if min_hours > 0:
                cutoff = now + timedelta(hours=min_hours)
                filtered = []
                for m in markets:
                    exp = m.get("expirationDate") or m.get("expirationTimestamp","")
                    try:
                        if exp:
                            exp_dt = datetime.fromisoformat(exp.replace("Z","+00:00"))
                            if exp_dt > cutoff:
                                filtered.append(m)
                    except Exception:
                        pass
                markets = filtered

            # Filter by category keyword
            if category.lower() not in ("all", "crypto", ""):
                markets = [m for m in markets if category.lower() in str(m.get("tags","")).lower()
                           or category.lower() in str(m.get("title","")).lower()]
            if markets:
                lines = [f"Limitless active markets ({category}) — Base-native, USDC:"]
                for m in markets[:15]:
                    title  = (m.get("title") or m.get("slug",""))[:75]
                    slug   = m.get("slug","")
                    prices = m.get("prices", [])
                    yes    = prices[0] if isinstance(prices, list) and prices else "?"
                    vol    = m.get("volume") or m.get("collateralVolume") or 0
                    exp    = m.get("expirationDate","")[:10] if m.get("expirationDate") else ""
                    lines.append(f"  slug={slug} | YES={yes} | Vol=${float(vol or 0):,.0f} | exp={exp} | {title}")
                return "\n".join(lines)
            return f"No active {category} markets. All markets: {len(r.json().get('data',[]))}"
        return f"Limitless API returned {r.status_code}: {r.text[:200]}"
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

    # Verify market and check expiry via Gamma API
    try:
        r = httpx.get(
            f"https://gamma-api.polymarket.com/markets",
            params={"conditionId": condition_id},
            timeout=10
        )
        if r.status_code == 200 and r.json():
            m = r.json()[0] if isinstance(r.json(), list) else r.json()
            question = m.get("question", market_question)[:80]
            exp_str  = m.get("endDateIso", "")
            vol      = float(m.get("volume", 0) or 0)

            if exp_str:
                try:
                    exp_dt     = datetime.fromisoformat(exp_str.replace("Z", "+00:00"))
                    hours_left = (exp_dt - datetime.now(timezone.utc)).total_seconds() / 3600
                    if hours_left < _BEN_MIN_POLY_EXPIRY_H:
                        return (
                            f"HARD BLOCKED: Polymarket expires in {hours_left:.1f}h "
                            f"(minimum {_BEN_MIN_POLY_EXPIRY_H}h required for Polymarket — use scan_limitless for short-duration trades). Pass."
                        )
                except Exception:
                    pass
        else:
            question = market_question or condition_id[:30]
            vol      = 0
    except Exception as e:
        question = market_question or condition_id[:30]
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
            lines.append(f"Completed jobs (paid, real): {completed}")
            lines.append(f"USDC earned:                 ${completed:.2f} (~$1/job)")
            lines.append(f"Unique job IDs tracked:      {len(job_statuses)}")
            lines.append(f"NOTE: HTML files in data/reports/ are NOT job count — include dev/test artifacts")
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

        # Step 3: Sign EIP-3009 using x402 SDK (correct structure + domain handling)
        import time as _t
        from x402.mechanisms.evm.types import ExactEIP3009Authorization, ExactEIP3009Payload
        from x402.mechanisms.evm.utils import create_nonce
        from x402.mechanisms.evm.eip712 import build_typed_data_for_signing
        from x402.mechanisms.evm.signers import EthAccountSigner

        amount_raw   = str(int(price_usdc * 1_000_000))
        nonce        = create_nonce()
        valid_after  = "0"
        valid_before = str(int(_t.time()) + 300)

        authorization = ExactEIP3009Authorization(
            from_address=account.address,
            to=pay_to,
            value=amount_raw,
            valid_after=valid_after,
            valid_before=valid_before,
            nonce=nonce,
        )

        # SDK signer handles TypedDataDomain → dict and bytes32 nonce conversion
        sdk_signer = EthAccountSigner(account)
        domain, types, primary_type, message = build_typed_data_for_signing(
            authorization, 8453,
            "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
            "USD Coin", "2",
        )
        sig_bytes = sdk_signer.sign_typed_data(domain, types, primary_type, message)
        signature = "0x" + sig_bytes.hex()

        # Build payload with correct nested structure: payload.authorization.{from,to,value,...}
        inner = ExactEIP3009Payload(authorization=authorization, signature=signature).to_dict()
        payment_payload = _j.dumps({
            "x402Version": 1,
            "scheme":      "exact",
            "network":     "eip155:8453",
            "payload":     inner,
        })
        payment_b64 = _b64.b64encode(payment_payload.encode()).decode()

        # Step 4: Retry with payment
        r2 = httpx.get(url, headers={"PAYMENT-SIGNATURE": payment_b64}, timeout=15)

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


def tool_log_action(action: str, result: str, cost_usd: float = 0.0) -> str:
    """Log an action to the session log for transparency."""
    entry = f"[{datetime.now().strftime('%H:%M:%S')}] {action} | cost=${cost_usd:.4f} | {result[:200]}"
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(entry + "\n")
    return f"Logged: {action}"


_BEN_BUDGET_FILE = ROOT / "data" / "acp_ben_budget.json"
_BEN_BUY_GATE    = 200    # checkpoint: after this many buys, wallet must show profit
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
    session_num = state.get("sessions", 0)
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
        "description": "Get live crypto prices, 24h change, and Fear & Greed index.",
        "input_schema": {
            "type": "object",
            "properties": {
                "asset": {"type": "string", "description": "BTC, ETH, or SOL", "default": "BTC"},
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
        "description": "Scan Limitless markets. Use min_hours=2 minimum to avoid last-minute lockout. Limitless markets are 5min, 15min, 1hr, 4hr ONLY — 4h is the max, there are NO longer markets. Real volume is in the 1h-4h range. Focus on crypto markets with >$50k volume where price-vs-strike gap gives directional edge.",
        "input_schema": {
            "type": "object",
            "properties": {
                "category":  {"type": "string",  "description": "crypto, sports, politics, or all", "default": "crypto"},
                "min_hours": {"type": "integer", "description": "Only show markets expiring this many hours from now. Use 2 to skip near-expiry markets. Limitless max is 4h so min_hours=2 targets the 2h-4h window.", "default": 2},
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
        "name": "buy_ecosystem_intel",
        "description": "Buy intel from an Octodamus ecosystem agent via ACP (NYSE_MacroMind, NYSE_StockOracle, NYSE_Tech_Agent, Order_ChainFlow, X_Sentiment_Agent, Octodamus). Octodamus calling card embedded so they can hire back. Use list_ecosystem_services first.",
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
]

TOOL_FNS = {
    "check_wallet":         lambda i: tool_check_wallet(),
    "web_search":           lambda i: tool_web_search(i["query"], i.get("num_results", 5)),
    "browse_url":           lambda i: tool_browse_url(i["url"]),
    "get_market_data":      lambda i: tool_get_market_data(i.get("asset", "BTC")),
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
    "scan_limitless":       lambda i: tool_scan_limitless(i.get("category","crypto"), int(i.get("min_hours",2))),
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
    "buy_ecosystem_intel":     lambda i: tool_buy_ecosystem_intel(i["target_agent"], i["service_name"]),
    "list_ecosystem_services": lambda i: tool_list_ecosystem_services(),
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
- "Ben's Market Edge" $0.25: Octodamus signal + Grok X sentiment + top Limitless market edge, one call
- "Ben's Derivatives Brief" $1: live funding rates, OI, liquidation map, CME COT — formatted for agents
- "Ben's Limitless Scan" $0.50: scan Base-native prediction markets for Octodamus-confirmed edges
- Research reports $2-5: deep dives on specific markets, sold as PDF via x402
The owner implements the x402 endpoint. You design the product and write the spec.

STREAM 2 -- TRADE ON PREDICTION MARKETS (Ben's own paper record — separate from OctoBoto):
Your trades are YOURS. OctoBoto is a separate Telegram trading bot with its own wallet and records.
You use paper_trade_polymarket() and place_limitless_bet() — these write to YOUR files only.

PRIMARY: Limitless Exchange (Base-native, USDC, your Franklin wallet works directly)
- scan_limitless(min_hours=2) -- Limitless markets are 5min / 15min / 1hr / 4hr ONLY. 4h is the hard ceiling.
  Real volume is in the 1h-4h window. Focus on crypto markets with >$50k vol.
- LIMITLESS STRUCTURAL CHECK: Track consecutive sessions with 0 qualifying crypto markets in your session history.
  If get_session_history shows 10+ consecutive sessions with "Limitless: 0 qualifying" or similar notes:
  flag this explicitly in your email and daily summary as:
  "STRUCTURAL FLAG: Limitless 0-market streak = X sessions. Recommend: (1) suspend Limitless scanning until
  $10K+ confirmed daily crypto volume, OR (2) lower volume threshold from $50K to $5K for a 2-session trial.
  This is a platform issue, not a market timing issue -- do not keep checking indefinitely."
  Do not defer this flag to "owner decision needed" without also including your specific recommendation.
  KEY EDGE: if current price is ALREADY above (or below) a strike but YES is priced <0.50,
  that's a mispricing. Quantify the gap: (current_price - strike) / strike * 100 = gap%.
  A gap >0.5% with YES priced below 0.65 is potential edge.
  ALSO check Range Scout (get_octodamus_signal with asset + "range") -- Range Scout fires
  4h directional calls that map directly to Limitless's longest markets. 6h/8h Range Scout calls
  do NOT match any Limitless market — use those for Polymarket only.
  If Range Scout is BULL on BTC (4h): look for BTC "above $X" markets expiring within 4h.

FALLBACK: Polymarket (when Limitless has no qualifying markets)
- get_polymarket_edges() -- shows conditionId, YES price, volume, hours left
- paper_trade_polymarket(condition_id, side, size_usdc, price) -- Ben's paper record only
- Polymarket has much deeper market supply (Fed rates, BTC milestones, macro events, sports)
- Use when Limitless is dry. Same 4-condition gate applies.

TRADING REQUIRES ALL FOUR CONDITIONS -- if any is missing, you DO NOT trade, no exceptions:
  1. EV gap >25% (crowd price is wrong by more than 25 cents on the dollar)
     OR price-vs-strike gap >0.5% with YES priced <0.65 (structural mispricing)
  2. Market expires >2h from now (not near-expiry lockout zone)
  3. Volume >$5k (real liquidity — Limitless 1h-4h markets typically $50k-$400k)
  4. Directional signal: Range Scout OR Octodamus main oracle PLUS Grok sentiment aligned
     - Range Scout (4h): call get_octodamus_signal and check for "range_scout" calls. Only 4h Range Scout maps to Limitless markets. 6h/8h = Polymarket only.
     - Main oracle: STRONG UP or STRONG DOWN (not HOLD/WATCH — too slow for Limitless TF)
     - Grok: must confirm same direction as the signal
     - If main oracle is HOLD/WATCH: Range Scout is your signal source. Use it.
- If all four met: write a position brief, then place_limitless_bet or paper_trade_polymarket
- If Limitless dry and Polymarket has no edge either: PASS. Cash is a position.
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
Never both idle and silent — if no trade qualifies, design a service or buy ecosystem intel.

MANDATORY ECOSYSTEM BUYS FOR HIGH-CONVICTION TRADES:
Before placing any bet, stack at least 2 cross-signals from ecosystem agents:
  buy_ecosystem_intel("NYSE_MacroMind", "Macro Regime Signal")       -- macro RISK-ON/OFF context
  buy_ecosystem_intel("Order_ChainFlow", "Order Flow Signal")         -- is capital actually moving in your direction?
  buy_ecosystem_intel("X_Sentiment_Agent", "Sentiment Divergence Signal") -- is the crowd wrong enough to fade?
If macro + flow + sentiment all align with your read: HIGH CONVICTION. Size up within limits.
If any one conflicts: reduce size. If two conflict: pass. Cash is a position.
Each buy is a completed ACP transaction — this builds Octodamus's reputation score on-chain.

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

YOUR MEASURE OF SUCCESS: wallet balance goes UP over time. Patience is a strategy. Learning is compounding."""


SESSION_FOCUS = {
    "morning": """SESSION FOCUS — MORNING (6am)
You are waking up. Markets moved overnight. Your job this session:
1. read_core_memory — read your distilled lessons FIRST before anything else
2. check_wallet + get_session_history + list_drafts (orient yourself)
2. get_market_data for BTC, ETH, SOL — what happened overnight?
3. get_grok_sentiment for BTC — what is X saying this morning?
4. get_octodamus_signal — check for Range Scout calls AND main oracle direction.
   Range Scout 4h = Limitless signal. Range Scout 6h/8h = Polymarket only (no Limitless market matches).
   Range Scout is your primary Limitless signal source when main oracle is HOLD/WATCH.
5. scan_limitless(min_hours=2) — Limitless is ALL short-term (2h-9h). This is where the volume is.
   Look for: (a) Range Scout direction + matching "above/below $X" market in that timeframe
             (b) price-vs-strike gap >0.5% where crowd pricing hasn't caught up yet
6. If Limitless has no qualifying market: get_polymarket_edges — check for macro/crypto edges there.
   paper_trade_polymarket() writes to YOUR record only, never OctoBoto's.
7. Trading rule — ALL four must be true or you DO NOT trade:
   - EV >25% OR price-vs-strike gap >0.5% with mispriced YES (<0.65)
   - Expiry >2h from now
   - Volume >$5k (real liquidity)
   - Range Scout OR Octodamus main oracle PLUS Grok sentiment aligned
   Missing any one = PASS. Cash is a position. Missing a trade is free. Taking a bad trade costs real money.
7. design_x402_service — zero-risk compounding income. One new service idea per morning.
8. SUB-AGENT SYNTHESIS before drafting morning post:
   - Call list_drafts and identify today's sub-agent pre-market reports (nyse_macromind, nyse_stockoracle,
     order_chainflow, x_sentiment_agent, nyse_tech_agent — look for files from today's date).
   - Tally their REGIME VERDICTs: how many RISK-ON vs BEAR/CAUTION?
   - If they split (e.g. MacroMind RISK-ON but X_Sentiment BEAR), that disagreement is the post angle.
   - One paragraph in the email: "Sub-agents: X RISK-ON, Y BEAR/CAUTION. Key divergence: [describe]."
9. Draft morning X post — save as morning_post_[date].md (use save_draft, NOT draft_content — avoids duplicate file)
   - Do NOT open with a greeting or date. Open with the sharpest number or contradiction from step 8.
   - If today's sub-agents split on regime, open with that split.
10. check_memory_status — run this and include the full output in the email
11. record_lesson — what was the single most important thing learned this session?
12. Email owner: market read, sub-agent synthesis, any Limitless/Polymarket edge found + paper trade, service designed, + full memory status""",

    "midday": """SESSION FOCUS — MIDDAY (12pm)
Markets are open. Your job this session:
1. read_core_memory — read your distilled lessons FIRST
2. check_wallet + get_session_history + list_drafts
3. get_octodamus_signal — check for Range Scout calls AND main oracle direction.
   Range Scout 4h = Limitless-native signal (fires when main oracle is HOLD/WATCH). 6h/8h = Polymarket only.
4. get_grok_sentiment — direction confirmation.
5. scan_limitless(min_hours=2) — Limitless is 5min/15min/1hr/4hr ONLY. 4h is the hard ceiling.
   Target: 4h Range Scout direction + matching "above/below $X" market, OR price-vs-strike gap >0.5%.
   Skip markets expiring in <2h (near-expiry lockout zone).
6. If no Limitless edge: get_polymarket_edges — Polymarket has deeper supply (Fed, BTC, macro).
   Use paper_trade_polymarket() if you find a qualifying market. Your record, not OctoBoto's.
7. design_x402_service OR buy_x402_service — either design a product or buy data to improve analysis.
8. record_lesson — log the key insight from this session
9. Save all output, email midday status""",

    "evening": """SESSION FOCUS — EVENING (6pm)
End of US trading day. Your job this session:
1. read_core_memory — read your distilled lessons FIRST
2. check_wallet + get_session_history + list_drafts — full review of the day's output
3. get_market_data — how did markets close?
4. get_grok_sentiment — what is the crowd saying into close?
5. check_acp_market — pull ACP completed jobs + USDC earned. Include in daily summary wallet section:
   "WALLET: Franklin $X USDC | ACP earned: $Y (Z jobs) | Combined: $W"
6. Evaluate any open Polymarket positions from today's briefs — are they still valid?
7. Draft the daily summary using save_draft ONLY (filename: daily_summary_[date].md).
   Do NOT use draft_content for this — it creates a duplicate file with an auto-generated slug name.
   SESSION NUMBERING: use the session count from state (reported by check_wallet context) as "Session X".
   Use the number of history entries from get_session_history as "sessions with recorded lessons".
   These two numbers legitimately differ — state counts every run, history only counts sessions that called record_lesson.
   Report both clearly: "Session 34 | 12 lessons logged"
8. Identify the single most important thing to do tomorrow morning — log it
9. check_memory_status — run this and include the full output in the email
10. record_lesson — summarize what the day taught you (trades, signals, dead ends)
11. Email owner: day summary (wallet + ACP stats), tomorrow's priority, + full memory status""",

    "overnight": """SESSION FOCUS — OVERNIGHT (12am)
While humans sleep, markets keep moving AND the agent economy keeps transacting. Your job:
1. check_wallet + list_drafts
2. check_acp_market — how is Octodamus performing on Virtuals ACP? How many jobs completed? USDC earned?
   ALWAYS include the ACP P&L in your overnight brief header: "ACP: X jobs | $Y USDC earned"
   - Design at least ONE new ACP offering with design_acp_offering (e.g. Polymarket Edge Report, Grok Sentiment Brief)
   - Find ways to funnel more agent customers: what job types are other ACP agents offering? What gaps exist?
   - If no competitor jobs sent yet: use buy_acp_competitor_job for ALL THREE competitors (predictor-sam, blue-dot-testnet, Dou Shan)
     Ask each for their best market signal. PURPOSE: learn what they deliver vs what Octodamus delivers. Intel only.
   - If competitor jobs already sent: use check_acp_competitor_jobs to read deliverables and compare vs Octodamus output
3. Check Smithery MCP: browse_orbis or web_search for 'Smithery octodamusai market-intelligence' — any reviews, usage, gaps?
4. scan_limitless(min_hours=2) — check for 1h-4h crypto markets with real volume (>$50k). 4h is the Limitless ceiling.
5. get_grok_sentiment for BTC — Asian markets read
6. If a real 4-condition edge exists: write brief, attempt paper trade
7. design_x402_service if you think of a new product
8. Email owner ONLY if you have something actionable: new ACP offering designed, edge found, or customer funnel idea""",
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


def run_session(dry_run: bool = False, session_type: str = ""):
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
    client      = anthropic.Anthropic(api_key=_secrets().get("ANTHROPIC_API_KEY", ""))
    date_inject = (
        f"\nCURRENT DATE/TIME: {datetime.now().strftime('%A, %B %d %Y %I:%M %p')}\n"
        f"IMPORTANT: Use only this date. Never invent dates or prices. "
        f"If get_market_data returns a price, that IS the current price — do not override it with training data."
    )
    session_sys = SYSTEM + date_inject + f"\n\n{focus}"
    messages    = [{"role": "user", "content": "Begin. Check wallet first, then execute the session focus."}]
    full_log     = []
    turns        = 0

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

        # Collect text output
        text_parts = [b.text for b in response.content if hasattr(b, "text") and b.text]
        if text_parts:
            combined = " ".join(text_parts)
            full_log.append(f"[Turn {turns}] {combined[:500]}")
            print(f"[Agent] {combined[:200]}")

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
                full_log.append(f"[Tool:{name}] {str(result)[:300]}")

                tool_results.append({
                    "type":        "tool_result",
                    "tool_use_id": block.id,
                    "content":     str(result),
                })

            messages.append({"role": "user", "content": tool_results})
            time.sleep(0.5)
        else:
            break

    # Save state
    state["sessions"] = session_num
    state["last_run"] = now
    _save_state(state)

    # Email session report
    log_summary = "\n".join(full_log[-30:])
    try:
        from octo_notify import _send
        _send(
            f"[ProfitAgent] Session #{session_num} — {turns} turns",
            f"Profit Agent session #{session_num} complete.\n\nTime: {now}\nTurns: {turns}/{MAX_TURNS}\n\n--- Session Log ---\n{log_summary}\n\n-- Profit Agent"
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

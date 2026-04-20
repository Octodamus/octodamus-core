"""
octo_boto_clob.py -- Polymarket CLOB execution layer (V2)
Wraps py-clob-client-v2 for order placement, cancellation, and balance checks.

LIVE_MODE = False  ->  paper trading (logs orders, never submits)
LIVE_MODE = True   ->  real execution (requires funded Polygon wallet)

Toggle via:  set_live_mode(True) / set_live_mode(False)
Or Telegram:  /golive / /gopaper

Migrated to V2 on 2026-04-17:
- SDK: py-clob-client -> py-clob-client-v2==1.0.0
- Collateral: USDC.e -> pUSD (0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB)
- Exchange: CTFv2 (0xE111180000d2663C0091e4f400237545B87B996B)
- OrderArgs: same core params (token_id, price, size, side); nonce/feeRateBps/taker removed by SDK
- Constructor: chain_id param unchanged in Python SDK
- Production host: clob.polymarket.com (serves V2 after April 22 cutover)
- Test host: clob-v2.polymarket.com (available before April 22)
"""

import logging
import os
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

CLOB_HOST        = "https://clob.polymarket.com"       # V2 production (live after April 22 cutover)
CLOB_HOST_TEST   = "https://clob-v2.polymarket.com"    # V2 testnet (available before April 22)
CHAIN_ID         = 137                                  # Polygon mainnet (unchanged)

# V2 collateral: pUSD (USDC.e wrapped via CollateralOnramp)
PUSD_POLY        = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"   # pUSD proxy
COLLATERAL_ONRAMP = "0x93070a847efEf7F70739046A929D47a521F5B8ee"  # wrap USDC.e -> pUSD here

# V2 exchange contracts
CTF_EXCHANGE     = "0xE111180000d2663C0091e4f400237545B87B996B"   # Standard markets
NEG_RISK_EXCHANGE = "0xe2222d279d744050d28e00520010520000310F59"  # Neg risk markets

# Legacy V1 reference (kept for historical balance lookups only)
USDC_POLY_V1     = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

# Safety caps (enforced even in live mode)
MAX_ORDER_USDC = 50.0       # Never place a single order above this
MAX_PRICE      = 0.97       # Never buy YES/NO above 97¢ (too close to resolution)
MIN_PRICE      = 0.03       # Never buy below 3¢

# ── Mode flag ─────────────────────────────────────────────────────────────────

LIVE_MODE = False           # Start paper-safe. Flip to True when wallet is funded.


def set_live_mode(enabled: bool) -> str:
    global LIVE_MODE
    LIVE_MODE = enabled
    status = "LIVE" if enabled else "PAPER"
    log.warning(f"[CLOB] Mode set to {status}")
    return status


def is_live() -> bool:
    return LIVE_MODE


# ── Client init ───────────────────────────────────────────────────────────────

_client = None


def _get_client():
    global _client
    if _client is not None:
        return _client

    key = os.environ.get("OCTOBOTO_WALLET_KEY", "")
    if not key:
        raise RuntimeError("OCTOBOTO_WALLET_KEY not in environment — load from Bitwarden first")

    from py_clob_client_v2.client import ClobClient
    from py_clob_client_v2.clob_types import ApiCreds

    # Level 1: key only (no API creds yet — derive them)
    c = ClobClient(host=CLOB_HOST, chain_id=CHAIN_ID, key=key)

    # Derive or create API credentials (Level 2)
    try:
        creds = c.create_or_derive_api_creds()
        c.set_api_creds(creds)
        log.info(f"[CLOB] Client initialized — address: {c.get_address()}")
    except Exception as e:
        log.warning(f"[CLOB] Could not derive API creds: {e} — running at Level 1")

    _client = c
    return _client


# ── Balance ───────────────────────────────────────────────────────────────────

def get_usdc_balance() -> float:
    """Returns USDC balance available in the CLOB exchange account."""
    try:
        c = _get_client()
        result = c.get_balance_allowance()
        # Returns dict with 'balance' in USDC units (already decimal)
        bal = result.get("balance", 0)
        return round(float(bal), 2)
    except Exception as e:
        log.error(f"[CLOB] Balance check failed: {e}")
        return 0.0


def get_wallet_address() -> str:
    try:
        return _get_client().get_address()
    except Exception as e:
        return f"error: {e}"


# ── Order placement ───────────────────────────────────────────────────────────

def place_order(
    token_id: str,
    side: str,           # "BUY"
    price: float,        # 0.0 – 1.0 (probability / price per share)
    amount_usdc: float,  # dollar amount to spend
    market_question: str = "",
) -> dict:
    """
    Place a limit order on Polymarket CLOB.

    In PAPER mode: logs the order and returns a simulated result.
    In LIVE mode:  submits to CLOB and returns the order response.

    Args:
        token_id:         Polymarket conditional token ID (YES or NO token)
        side:             Always "BUY" (we buy YES or NO tokens)
        price:            Price per share (0.03 – 0.97)
        amount_usdc:      Dollar amount to spend (capped at MAX_ORDER_USDC)
        market_question:  Human-readable label for logging

    Returns dict with: order_id, status, side, price, size, amount_usdc, live, ts
    """
    # Safety checks (apply in both modes)
    price = round(max(MIN_PRICE, min(MAX_PRICE, price)), 4)
    amount_usdc = round(min(amount_usdc, MAX_ORDER_USDC), 2)
    size = round(amount_usdc / price, 2)   # shares = dollars / price

    ts = datetime.now(timezone.utc).isoformat()
    label = market_question[:60] if market_question else token_id[:20]

    if not LIVE_MODE:
        sim_id = f"PAPER-{int(datetime.now(timezone.utc).timestamp())}"
        log.info(f"[CLOB] PAPER order | {label} | BUY {size} shares @ {price} (${amount_usdc})")
        return {
            "order_id":    sim_id,
            "status":      "paper",
            "side":        "BUY",
            "price":       price,
            "size":        size,
            "amount_usdc": amount_usdc,
            "token_id":    token_id,
            "live":        False,
            "ts":          ts,
        }

    # Live execution
    try:
        from py_clob_client_v2.clob_types import OrderArgs, OrderType
        c = _get_client()

        order_args = OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
            side=side,
        )
        order = c.create_and_post_order(order_args)
        order_id = order.get("orderID", order.get("id", "unknown"))
        log.info(f"[CLOB] LIVE order placed | {label} | {order_id} | BUY {size}@{price}")
        return {
            "order_id":    order_id,
            "status":      "live",
            "side":        "BUY",
            "price":       price,
            "size":        size,
            "amount_usdc": amount_usdc,
            "token_id":    token_id,
            "live":        True,
            "ts":          ts,
            "raw":         order,
        }
    except Exception as e:
        log.error(f"[CLOB] Order failed: {e}")
        return {"status": "error", "error": str(e), "live": True, "ts": ts}


# ── Cancel ────────────────────────────────────────────────────────────────────

def cancel_order(order_id: str) -> bool:
    if not LIVE_MODE:
        log.info(f"[CLOB] PAPER cancel {order_id}")
        return True
    try:
        _get_client().cancel(order_id)
        log.info(f"[CLOB] Cancelled {order_id}")
        return True
    except Exception as e:
        log.error(f"[CLOB] Cancel failed: {e}")
        return False


def cancel_all() -> bool:
    if not LIVE_MODE:
        log.info("[CLOB] PAPER cancel all")
        return True
    try:
        _get_client().cancel_all()
        log.info("[CLOB] All orders cancelled")
        return True
    except Exception as e:
        log.error(f"[CLOB] Cancel all failed: {e}")
        return False


# ── Open orders & trades ──────────────────────────────────────────────────────

def get_open_orders() -> list:
    if not LIVE_MODE:
        return []
    try:
        return _get_client().get_orders() or []
    except Exception as e:
        log.error(f"[CLOB] get_open_orders failed: {e}")
        return []


def get_trade_history(limit: int = 20) -> list:
    try:
        return _get_client().get_trades(limit=limit) or []
    except Exception as e:
        log.error(f"[CLOB] get_trades failed: {e}")
        return []


# ── Market info ───────────────────────────────────────────────────────────────

def get_token_ids(condition_id: str) -> dict:
    """
    Given a Polymarket conditionId, return YES and NO token IDs.
    The conditionId is in the market dict as 'conditionId'.
    """
    try:
        c = _get_client()
        market = c.get_market(condition_id)
        tokens = market.get("tokens", [])
        yes_token = next((t["token_id"] for t in tokens if t.get("outcome") == "Yes"), None)
        no_token  = next((t["token_id"] for t in tokens if t.get("outcome") == "No"), None)
        return {"yes": yes_token, "no": no_token}
    except Exception as e:
        log.error(f"[CLOB] get_token_ids failed: {e}")
        return {"yes": None, "no": None}


def get_mid_price(token_id: str) -> Optional[float]:
    """Current midpoint price for a token."""
    try:
        result = _get_client().get_midpoint(token_id=token_id)
        return float(result.get("mid", 0))
    except Exception:
        return None


def check_orderbook_liquidity(
    token_id: str,
    target_price: float,
    min_usdc: float = 20.0,
) -> dict:
    """
    Check real CLOB orderbook depth before entering a position.

    For a BUY order at target_price: sum all ask levels at price ≤ target_price.
    Returns:
        available_usdc  — total USDC available on the ask side at or below our price
        best_ask        — cheapest available ask price (None if no asks)
        sufficient      — True if available_usdc >= min_usdc
        spread          — best_ask minus best_bid (None if missing)

    On any API error, returns sufficient=True (fail-open) so a bad API call
    never silently blocks a trade — the caller can log and decide.
    """
    try:
        book = _get_client().get_order_book(token_id)
        asks = book.get("asks", [])   # [{"price": "0.65", "size": "120"}, ...]
        bids = book.get("bids", [])

        available_usdc = 0.0
        best_ask = None

        for level in asks:
            try:
                p = float(level["price"])
                s = float(level["size"])
            except (KeyError, ValueError, TypeError):
                continue
            if p <= target_price:
                if best_ask is None or p < best_ask:
                    best_ask = p
                available_usdc += p * s   # USDC value at this level

        best_bid = None
        for level in bids:
            try:
                p = float(level["price"])
            except (KeyError, ValueError, TypeError):
                continue
            if best_bid is None or p > best_bid:
                best_bid = p

        spread = round(best_ask - best_bid, 4) if (best_ask and best_bid) else None

        return {
            "available_usdc": round(available_usdc, 2),
            "best_ask":       best_ask,
            "best_bid":       best_bid,
            "spread":         spread,
            "sufficient":     available_usdc >= min_usdc,
        }

    except Exception as e:
        log.warning(f"[CLOB] Orderbook check failed for {token_id[:16]}...: {e}")
        return {
            "available_usdc": 0.0,
            "best_ask":       None,
            "best_bid":       None,
            "spread":         None,
            "sufficient":     True,   # fail-open — don't silently block trades on API errors
        }


# ── Summary string for Telegram ──────────────────────────────────────────────

def clob_status_str() -> str:
    mode = "🟢 LIVE" if LIVE_MODE else "📋 PAPER"
    try:
        bal = get_usdc_balance() if LIVE_MODE else 0.0
        addr = get_wallet_address()
        addr_short = f"{addr[:6]}...{addr[-4:]}" if len(addr) > 12 else addr
        bal_str = f"${bal:.2f} USDC" if LIVE_MODE else "paper trading"
        return f"{mode} | {addr_short} | {bal_str}"
    except Exception as e:
        return f"{mode} | wallet error: {e}"

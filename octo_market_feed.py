"""
octo_market_feed.py — Shared Real-Time Market Feed

Single persistent Binance WebSocket connection shared across all modules:
  - octodamus_runner.py  (daily/monitor posts)
  - octo_report_handlers.py  (ACP reports)
  - telegram_bot.py  (chat responses)
  - octo_boto.py  (via octo_boto_upgrades.py)

Architecture:
  - One background thread maintains the WebSocket
  - All modules call get_price() / get_prices() — instant, no network call
  - Falls back to CoinGecko REST if WebSocket not connected
  - Started once at boot via octo_unlock.ps1 → octo_boto.py or standalone

Usage:
    from octo_market_feed import feed

    price = feed.get_price("BTC")           # float or None
    prices = feed.get_prices()              # {"BTC": 66500, "ETH": 2010, "SOL": 83.5}
    change = feed.get_change_24h("BTC")     # % from CoinGecko (updated every 5 min)
    ctx = feed.get_price_context()          # formatted string for prompts
    signal = feed.get_momentum("BTC", 30)  # % move in 30 seconds
"""

import json
import logging
import threading
import time
from typing import Optional

log = logging.getLogger("OctoMarketFeed")

# ── CoinGecko ID map ──────────────────────────────────────────────────────────
CG_IDS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
}

# Binance WebSocket streams
WS_STREAMS = "btcusdt@trade/ethusdt@trade/solusdt@trade"
WS_URL = f"wss://stream.binance.com:9443/stream?streams={WS_STREAMS}"

# Symbol mapping from Binance stream names
STREAM_MAP = {
    "btcusdt": "BTC",
    "ethusdt": "ETH",
    "solusdt": "SOL",
}


class MarketFeed:
    """
    Persistent real-time market data feed.
    Binance WebSocket for live prices + CoinGecko REST for 24h change.
    """

    HISTORY_SECONDS = 300    # 5 min of price history
    CG_REFRESH_SECS = 300    # Refresh 24h change every 5 min

    def __init__(self):
        self._prices: dict = {}         # {"BTC": [(ts, price), ...]}
        self._change_24h: dict = {}     # {"BTC": -1.2, "ETH": 0.8, ...}
        self._lock = threading.Lock()
        self._running = False
        self._connected = False
        self._thread: Optional[threading.Thread] = None
        self._cg_thread: Optional[threading.Thread] = None
        self._last_cg_refresh = 0.0

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self):
        """Start background WebSocket + CoinGecko refresh threads."""
        if self._running:
            return
        self._running = True

        self._thread = threading.Thread(
            target=self._ws_runner, daemon=True, name="OctoMarketFeed-WS"
        )
        self._thread.start()

        self._cg_thread = threading.Thread(
            target=self._cg_runner, daemon=True, name="OctoMarketFeed-CG"
        )
        self._cg_thread.start()

        # Wait up to 8s for connection
        for _ in range(16):
            if self._connected:
                break
            time.sleep(0.5)

        if self._connected:
            log.info("[MarketFeed] Live — Binance WebSocket connected")
        else:
            log.warning("[MarketFeed] WebSocket not yet connected — using CoinGecko fallback")

    def stop(self):
        self._running = False

    def is_live(self) -> bool:
        """True if Binance WebSocket is connected."""
        return self._connected

    def get_price(self, symbol: str) -> Optional[float]:
        """
        Latest price for BTC/ETH/SOL.
        Returns None if no data available.
        """
        symbol = symbol.upper()
        with self._lock:
            history = self._prices.get(symbol, [])
            if history:
                return history[-1][1]

        # Fallback to CoinGecko
        return self._cg_price_fallback(symbol)

    def get_prices(self) -> dict:
        """
        All prices as dict. {"BTC": 66500.0, "ETH": 2010.0, "SOL": 83.5}
        Returns empty dict values (None) if symbol unavailable.
        """
        result = {}
        for sym in ["BTC", "ETH", "SOL"]:
            result[sym] = self.get_price(sym)
        return result

    def get_change_24h(self, symbol: str) -> Optional[float]:
        """24h % change from CoinGecko (updated every 5 min)."""
        with self._lock:
            return self._change_24h.get(symbol.upper())

    def get_momentum(self, symbol: str, window_seconds: int = 30) -> Optional[float]:
        """
        % price change over last window_seconds from Binance stream.
        None if insufficient history.
        """
        symbol = symbol.upper()
        with self._lock:
            history = self._prices.get(symbol, [])
            if len(history) < 2:
                return None
            now = time.time()
            cutoff = now - window_seconds
            window = [(t, p) for t, p in history if t >= cutoff]
            if not window:
                return None
            oldest = window[0][1]
            latest = history[-1][1]
            if oldest <= 0:
                return None
            return round((latest - oldest) / oldest * 100, 4)

    def get_price_context(self) -> str:
        """
        Formatted price string for injection into Claude prompts.
        Example:
            LIVE PRICES (Binance real-time):
            BTC: $66,500 (+1.2% 24h)
            ETH: $2,010 (-0.8% 24h)
            SOL: $83.50 (-0.3% 24h)
        """
        source = "Binance real-time" if self._connected else "CoinGecko"
        lines = [f"LIVE PRICES ({source}):"]

        for sym in ["BTC", "ETH", "SOL"]:
            price = self.get_price(sym)
            chg = self.get_change_24h(sym)
            if price is None:
                lines.append(f"{sym}: unavailable")
                continue
            if sym == "SOL":
                price_str = f"${price:,.2f}"
            else:
                price_str = f"${price:,.0f}"
            chg_str = f" ({chg:+.1f}% 24h)" if chg is not None else ""
            lines.append(f"{sym}: {price_str}{chg_str}")

        return "\n".join(lines)

    def get_full_context(self) -> str:
        """
        Full market context for Oracle prompts — prices + momentum.
        """
        ctx = self.get_price_context()
        momentum_lines = []
        for sym in ["BTC", "ETH"]:
            m30 = self.get_momentum(sym, 30)
            m60 = self.get_momentum(sym, 60)
            if m30 is not None:
                direction = "UP" if m30 > 0.1 else "DOWN" if m30 < -0.1 else "FLAT"
                momentum_lines.append(
                    f"{sym} momentum: {direction} {m30:+.3f}% (30s) / {m60:+.3f}% (60s)"
                    if m60 is not None else
                    f"{sym} momentum: {direction} {m30:+.3f}% (30s)"
                )
        if momentum_lines:
            ctx += "\n" + "\n".join(momentum_lines)
        ctx += "\nIMPORTANT: Use ONLY these prices. Never use prices from training data."
        return ctx

    # ── Binance WebSocket ─────────────────────────────────────────────────────

    def _ws_runner(self):
        """Background thread: runs asyncio WebSocket loop with reconnect."""
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        while self._running:
            try:
                loop.run_until_complete(self._ws_loop())
            except Exception as e:
                log.warning(f"[MarketFeed] WS error: {e} — reconnecting in 5s")
                self._connected = False
                time.sleep(5)

    async def _ws_loop(self):
        try:
            import websockets
        except ImportError:
            log.error("[MarketFeed] websockets not installed: pip install websockets")
            self._running = False
            return

        import asyncio
        async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=10) as ws:
            self._connected = True
            while self._running:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=30)
                    data = json.loads(msg)
                    stream = data.get("stream", "")
                    payload = data.get("data", {})
                    price = float(payload.get("p", 0))
                    ts = time.time()

                    # Map stream name to symbol
                    for stream_key, symbol in STREAM_MAP.items():
                        if stream_key in stream and price > 0:
                            self._update(symbol, ts, price)
                            break

                except asyncio.TimeoutError:
                    await ws.ping()
                except Exception as e:
                    log.warning(f"[MarketFeed] recv error: {e}")
                    break

        self._connected = False

    def _update(self, symbol: str, ts: float, price: float):
        """Thread-safe price history update with window trim."""
        with self._lock:
            if symbol not in self._prices:
                self._prices[symbol] = []
            self._prices[symbol].append((ts, price))
            cutoff = ts - self.HISTORY_SECONDS
            self._prices[symbol] = [
                (t, p) for t, p in self._prices[symbol] if t >= cutoff
            ]

    # ── CoinGecko 24h Change ─────────────────────────────────────────────────

    def _cg_runner(self):
        """Background thread: refreshes 24h change from CoinGecko every 5 min."""
        while self._running:
            try:
                self._refresh_cg()
            except Exception as e:
                log.warning(f"[MarketFeed] CoinGecko refresh error: {e}")
            time.sleep(self.CG_REFRESH_SECS)

    def _refresh_cg(self):
        """Fetch 24h change + fallback prices from CoinGecko."""
        try:
            import httpx
            r = httpx.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={
                    "ids": "bitcoin,ethereum,solana",
                    "vs_currencies": "usd",
                    "include_24hr_change": "true",
                },
                timeout=8,
            )
            if r.status_code == 200:
                d = r.json()
                mapping = {"bitcoin": "BTC", "ethereum": "ETH", "solana": "SOL"}
                with self._lock:
                    for cg_id, sym in mapping.items():
                        if cg_id in d:
                            chg = d[cg_id].get("usd_24h_change")
                            if chg is not None:
                                self._change_24h[sym] = round(float(chg), 2)
                            # Also store as fallback price if WS not connected
                            price = d[cg_id].get("usd")
                            if price and not self._connected:
                                ts = time.time()
                                if sym not in self._prices:
                                    self._prices[sym] = []
                                self._prices[sym].append((ts, float(price)))
                log.debug("[MarketFeed] CoinGecko 24h change refreshed")
        except Exception as e:
            log.warning(f"[MarketFeed] CoinGecko fetch failed: {e}")

    def _cg_price_fallback(self, symbol: str) -> Optional[float]:
        """One-shot CoinGecko price fetch when WebSocket has no data."""
        try:
            import httpx
            cg_id = CG_IDS.get(symbol)
            if not cg_id:
                return None
            r = httpx.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": cg_id, "vs_currencies": "usd"},
                timeout=6,
            )
            if r.status_code == 200:
                return float(r.json()[cg_id]["usd"])
        except Exception:
            pass
        return None


# ── Singleton ─────────────────────────────────────────────────────────────────
# Import and use this instance everywhere:
#   from octo_market_feed import feed
#   price = feed.get_price("BTC")

feed = MarketFeed()


def start_feed():
    """Start the feed. Call once at boot."""
    feed.start()
    return feed

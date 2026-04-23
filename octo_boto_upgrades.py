"""
octo_boto_upgrades.py — OctoBoto Upgrade Pack v1

Adds 4 critical upgrades:

1. BINANCE WEBSOCKET — Real-time BTC/ETH price feed (free, no API key)
   - Detects price moves > threshold on Binance
   - Flags when Polymarket odds are stale vs real price
   - Used by scan to filter for latency arb opportunities

2. KILL SWITCHES — Hard portfolio protection
   - Daily loss limit: -20% auto-halt
   - Total drawdown kill switch: -40%
   - Max single position: 8% (already enforced in math but now monitored)
   - Telegram alert on every threshold breach

3. TELEGRAM ALERTS — Per-trade and system notifications
   - Trade opened: side, size, EV, market question
   - Trade closed: P&L, win/loss, running balance
   - Kill switch triggered: reason + current balance
   - Daily P&L summary at midnight UTC

4. REFLECTION PATTERN (Ch.4 Agentic Design Patterns)
   - AI estimate → Critic pass → deliver
   - Critic checks: probability outlier? evidence strong? price gap real?
   - Raises/lowers confidence, filters weak signals before entry
"""

import asyncio
import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional, Callable

log = logging.getLogger("OctoBoto.Upgrades")


# ══════════════════════════════════════════════════════════════════════════════
# 1. BINANCE WEBSOCKET PRICE FEED
# ══════════════════════════════════════════════════════════════════════════════

class BinancePriceFeed:
    """
    Real-time BTC/ETH price feed via Binance WebSocket.
    Free, no API key needed. Runs in background thread.

    Usage:
        feed = BinancePriceFeed()
        feed.start()
        btc = feed.get_price("BTC")   # Returns float or None
        move = feed.get_move("BTC", window_seconds=30)  # % move in window
        feed.stop()
    """

    WS_URL = "wss://stream.binance.com:9443/stream?streams=btcusdt@trade/ethusdt@trade"

    def __init__(self):
        self._prices: dict = {}          # {"BTC": [(timestamp, price), ...]}
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._connected = False
        self._history_seconds = 120      # Keep 2 min of price history

    def start(self):
        """Start the background WebSocket thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="BinanceWS")
        self._thread.start()
        log.info("[BinanceWS] Feed started — waiting for connection...")
        # Give it 5 seconds to connect
        for _ in range(10):
            if self._connected:
                break
            time.sleep(0.5)
        if self._connected:
            log.info("[BinanceWS] Connected and receiving prices")
        else:
            log.warning("[BinanceWS] Could not connect — latency arb disabled")

    def stop(self):
        self._running = False

    def is_live(self) -> bool:
        return self._connected

    def get_price(self, symbol: str) -> Optional[float]:
        """Latest price for BTC or ETH. Returns None if no data."""
        with self._lock:
            history = self._prices.get(symbol, [])
            if not history:
                return None
            return history[-1][1]

    def get_move(self, symbol: str, window_seconds: int = 30) -> Optional[float]:
        """
        % price change over last window_seconds.
        Returns None if insufficient data.
        Positive = price up, negative = price down.
        """
        with self._lock:
            history = self._prices.get(symbol, [])
            if len(history) < 2:
                return None
            now = time.time()
            cutoff = now - window_seconds
            # Find oldest price within window
            window = [(t, p) for t, p in history if t >= cutoff]
            if not window:
                return None
            oldest_price = window[0][1]
            latest_price = history[-1][1]
            if oldest_price <= 0:
                return None
            return round((latest_price - oldest_price) / oldest_price * 100, 4)

    def get_momentum_signal(self, symbol: str) -> dict:
        """
        Returns a trading signal based on recent price momentum.
        Used to detect Polymarket lag opportunities.

        Returns:
            {
                "symbol": "BTC",
                "price": 67500.0,
                "move_30s": -0.8,      # % move in last 30s
                "move_60s": -1.2,      # % move in last 60s
                "direction": "DOWN",   # or "UP" or "FLAT"
                "strength": "STRONG",  # STRONG / MODERATE / WEAK
                "lag_opportunity": True  # True if move > threshold
            }
        """
        price = self.get_price(symbol)
        if price is None:
            return {"symbol": symbol, "price": None, "lag_opportunity": False}

        move_30 = self.get_move(symbol, 30) or 0.0
        move_60 = self.get_move(symbol, 60) or 0.0

        # Direction
        if abs(move_30) < 0.1:
            direction = "FLAT"
        elif move_30 > 0:
            direction = "UP"
        else:
            direction = "DOWN"

        # Strength based on 30s move magnitude
        abs_move = abs(move_30)
        if abs_move >= 0.5:
            strength = "STRONG"
        elif abs_move >= 0.2:
            strength = "MODERATE"
        else:
            strength = "WEAK"

        # Lag opportunity: strong move in last 30s = Polymarket likely stale
        lag_opportunity = abs_move >= 0.3 and direction != "FLAT"

        return {
            "symbol": symbol,
            "price": price,
            "move_30s": move_30,
            "move_60s": move_60,
            "direction": direction,
            "strength": strength,
            "lag_opportunity": lag_opportunity,
        }

    def _run(self):
        """Background thread: maintains WebSocket connection with auto-reconnect."""
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _fail_count = 0
        while self._running:
            try:
                loop.run_until_complete(self._ws_loop())
                _fail_count = 0  # reset on clean exit
            except Exception as e:
                self._connected = False
                err_str = str(e)
                # HTTP 451 = geo-blocked by Binance — back off for 30 min, don't spam
                if "451" in err_str:
                    if _fail_count == 0:
                        log.warning("[BinanceWS] HTTP 451 — geo-blocked by Binance. Latency arb disabled. Retrying in 30min.")
                    _fail_count += 1
                    time.sleep(1800)
                else:
                    _fail_count += 1
                    delay = min(5 * _fail_count, 300)  # back off up to 5 min
                    log.warning(f"[BinanceWS] Connection error: {e} — reconnecting in {delay}s")
                    time.sleep(delay)

    async def _ws_loop(self):
        try:
            import websockets
        except ImportError:
            log.error("[BinanceWS] websockets not installed: pip install websockets")
            self._running = False
            return

        async with websockets.connect(
            self.WS_URL,
            ping_interval=20,
            ping_timeout=10,
        ) as ws:
            self._connected = True
            log.info("[BinanceWS] WebSocket connected")
            while self._running:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=30)
                    data = json.loads(msg)
                    stream = data.get("stream", "")
                    payload = data.get("data", {})
                    price = float(payload.get("p", 0))
                    ts = time.time()

                    if "btcusdt" in stream and price > 0:
                        self._update("BTC", ts, price)
                    elif "ethusdt" in stream and price > 0:
                        self._update("ETH", ts, price)

                except asyncio.TimeoutError:
                    # No data in 30s — send ping
                    await ws.ping()
                except Exception as e:
                    log.warning(f"[BinanceWS] recv error: {e}")
                    break

        self._connected = False

    def _update(self, symbol: str, ts: float, price: float):
        """Thread-safe price history update."""
        with self._lock:
            if symbol not in self._prices:
                self._prices[symbol] = []
            self._prices[symbol].append((ts, price))
            # Trim to window
            cutoff = ts - self._history_seconds
            self._prices[symbol] = [
                (t, p) for t, p in self._prices[symbol] if t >= cutoff
            ]


# ══════════════════════════════════════════════════════════════════════════════
# 2. KILL SWITCHES
# ══════════════════════════════════════════════════════════════════════════════

class KillSwitch:
    """
    Portfolio protection with hard limits.
    From the article: max position 8%, daily loss -20%, drawdown kill -40%.

    Usage:
        ks = KillSwitch(starting_balance=500.0, alert_callback=send_telegram)
        ks.check(current_balance=400.0)  # Raises KillSwitchTriggered if limit hit
    """

    DAILY_LOSS_LIMIT   = -0.20   # -20% in a day = halt
    DRAWDOWN_KILL      = -0.40   # -40% from peak = kill
    MAX_POSITION_PCT   = 0.08    # 8% max single position

    def __init__(
        self,
        starting_balance: float,
        alert_callback: Optional[Callable] = None,
    ):
        self.starting_balance = starting_balance
        self.peak_balance = starting_balance
        self.day_start_balance = starting_balance
        self._day_start_date = datetime.now(timezone.utc).date()
        self.halted = False
        self.halt_reason = ""
        self._alert = alert_callback  # async function(message: str)

    def update_peak(self, balance: float):
        """Call after every trade to track peak balance."""
        if balance > self.peak_balance:
            self.peak_balance = balance

    def reset_day(self, balance: float):
        """Call at start of each trading day."""
        today = datetime.now(timezone.utc).date()
        if today != self._day_start_date:
            self.day_start_balance = balance
            self._day_start_date = today

    def check(self, balance: float) -> tuple[bool, str]:
        """
        Check all kill switch conditions.
        Returns (triggered: bool, reason: str).
        If triggered, sets self.halted = True.
        """
        self.reset_day(balance)
        self.update_peak(balance)

        if self.halted:
            return True, self.halt_reason

        # Daily loss limit
        if self.day_start_balance > 0:
            daily_pct = (balance - self.day_start_balance) / self.day_start_balance
            if daily_pct <= self.DAILY_LOSS_LIMIT:
                reason = f"Daily loss limit hit: {daily_pct:.1%} (limit {self.DAILY_LOSS_LIMIT:.0%})"
                self._trigger(reason, balance)
                return True, reason

        # Total drawdown from peak
        if self.peak_balance > 0:
            drawdown = (balance - self.peak_balance) / self.peak_balance
            if drawdown <= self.DRAWDOWN_KILL:
                reason = f"Drawdown kill switch: {drawdown:.1%} from peak ${self.peak_balance:.2f}"
                self._trigger(reason, balance)
                return True, reason

        return False, ""

    def check_position_size(self, size: float, balance: float) -> tuple[bool, str]:
        """Returns (ok, warning) for position size check."""
        if balance <= 0:
            return False, "Balance is zero"
        pct = size / balance
        if pct > self.MAX_POSITION_PCT:
            return False, f"Position ${size:.2f} = {pct:.1%} exceeds {self.MAX_POSITION_PCT:.0%} max"
        return True, ""

    def reset(self, balance: float):
        """Manual reset after reviewing situation."""
        self.halted = False
        self.halt_reason = ""
        self.peak_balance = balance
        self.day_start_balance = balance
        log.info(f"[KillSwitch] Reset — new baseline ${balance:.2f}")

    def status(self) -> dict:
        return {
            "halted": self.halted,
            "halt_reason": self.halt_reason,
            "peak_balance": self.peak_balance,
            "day_start_balance": self.day_start_balance,
            "daily_loss_limit": f"{self.DAILY_LOSS_LIMIT:.0%}",
            "drawdown_kill": f"{self.DRAWDOWN_KILL:.0%}",
        }

    def _trigger(self, reason: str, balance: float):
        self.halted = True
        self.halt_reason = reason
        log.warning(f"[KillSwitch] TRIGGERED: {reason} | Balance: ${balance:.2f}")
        if self._alert:
            msg = (
                f"🚨 *OCTOBOTO KILL SWITCH TRIGGERED*\n\n"
                f"Reason: {reason}\n"
                f"Balance: `${balance:.2f}`\n\n"
                f"Trading halted. Use /ksreset to resume after review."
            )
            asyncio.create_task(self._alert(msg))


# ══════════════════════════════════════════════════════════════════════════════
# 3. TELEGRAM ALERT MANAGER
# ══════════════════════════════════════════════════════════════════════════════

class TradeAlertManager:
    """
    Sends Telegram notifications for all key OctoBoto events.

    Events:
    - Trade opened
    - Trade closed (with P&L)
    - Kill switch triggered
    - Daily P&L summary
    - Binance lag opportunity detected
    - Scan complete summary

    Usage:
        alerts = TradeAlertManager(bot, chat_id)
        await alerts.trade_opened(position, balance)
        await alerts.trade_closed(closed, balance)
        await alerts.kill_switch(reason, balance)
    """

    def __init__(self, bot, chat_id: int):
        self.bot = bot
        self.chat_id = chat_id

    async def send(self, msg: str):
        """Send a Telegram message, silently on error."""
        try:
            await self.bot.send_message(
                self.chat_id, msg,
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )
        except Exception as e:
            log.warning(f"[Alerts] Telegram send failed: {e}")

    async def trade_opened(self, pos: dict, balance: float):
        side_icon = "🟢" if pos["side"] == "YES" else "🔴"
        await self.send(
            f"{side_icon} *Trade Opened*\n"
            f"Side: *{pos['side']}* | Size: `${pos['size']:.2f}` "
            f"@ `{pos['entry_price']:.3f}`\n"
            f"EV: `{pos['ev']:+.1%}` | Kelly: `{pos['kelly_frac']:.1%}` "
            f"| Conf: {pos['confidence']}\n"
            f"📋 _{pos['question'][:80]}_\n"
            f"Balance: `${balance:.2f}`"
        )

    async def trade_closed(self, closed: dict, balance: float):
        icon = "✅" if closed.get("won") else "❌"
        pnl = closed.get("pnl", 0)
        pnl_s = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
        pnl_pct = closed.get("pnl_pct", 0)
        await self.send(
            f"{icon} *Trade Closed* — `{pnl_s}` ({pnl_pct:+.0f}%)\n"
            f"Side: {closed.get('side')} | "
            f"Resolved: *{closed.get('resolution', '?')}*\n"
            f"📋 _{closed.get('question', '')[:80]}_\n"
            f"Balance: `${balance:.2f}`"
        )

    async def kill_switch(self, reason: str, balance: float):
        await self.send(
            f"🚨 *KILL SWITCH TRIGGERED*\n\n"
            f"{reason}\n\n"
            f"Balance: `${balance:.2f}`\n"
            f"All trading halted. `/ksreset` to resume."
        )

    async def lag_opportunity(self, signal: dict, market: dict):
        sym = signal["symbol"]
        direction = signal["direction"]
        move = signal["move_30s"]
        icon = "📈" if direction == "UP" else "📉"
        await self.send(
            f"{icon} *Binance Lag Opportunity*\n"
            f"{sym}: `{move:+.2f}%` in 30s — market likely stale\n"
            f"📋 _{market.get('question', '')[:80]}_\n"
            f"Market price: `{market.get('yes_price', 0):.1%}`"
        )

    async def daily_summary(self, stats: dict):
        arrow = "📈" if stats["total_pnl"] >= 0 else "📉"
        pnl_s = f"+${stats['total_pnl']:.2f}" if stats["total_pnl"] >= 0 else f"-${abs(stats['total_pnl']):.2f}"
        await self.send(
            f"{arrow} *OctoBoto Daily Summary*\n\n"
            f"Balance: `${stats['balance']:.2f}`\n"
            f"P&L: `{pnl_s}` ({stats['total_pnl_pct']:+.1f}%)\n"
            f"Trades: {stats['num_trades']} closed | Win rate: `{stats['win_rate']:.0f}%`\n"
            f"Sharpe: `{stats['sharpe']:.2f}` | Max DD: `{stats['max_drawdown']:.1f}%`"
        )

    async def scan_complete(self, found: int, entered: int, balance: float):
        await self.send(
            f"🔍 *Scan Complete*\n"
            f"Found: {found} edges | Entered: {entered}\n"
            f"Balance: `${balance:.2f}`"
        )


# ══════════════════════════════════════════════════════════════════════════════
# 4. REFLECTION PATTERN (Chapter 4 - Agentic Design Patterns)
# ══════════════════════════════════════════════════════════════════════════════

CRITIC_PROMPT = """You are a rigorous critic reviewing a prediction market trade signal.
Your job is to find flaws, not confirm the trade.

Review the following AI estimate and challenge it:
1. Is the probability realistic? (outlier = >70% or <30% on a contested question)
2. Is the evidence cited specific and recent, or vague and stale?
3. Is the price gap real edge, or noise? (gap < 8% is often noise)
4. Would a contrarian make a strong case for the other side?
5. Is the confidence level justified by the evidence quality?

Be harsh. A false positive costs real money. A missed trade costs nothing.

If the signal holds up: output JSON with "verdict": "APPROVE" and revised confidence.
If the signal is weak: output JSON with "verdict": "REJECT" and reason.
If uncertain: output JSON with "verdict": "DOWNGRADE" and lower confidence.

Output JSON only:
{
  "verdict": "APPROVE" | "REJECT" | "DOWNGRADE",
  "confidence": "high" | "medium" | "low",
  "critique": "one sentence reason",
  "adjusted_probability": float  // your revised estimate, can differ from original
}"""


def reflect_on_estimate(
    question: str,
    market_price: float,
    estimated_probability: float,
    confidence: str,
    reasoning: str,
    anthropic_key: str,
) -> dict:
    """
    Critic pass on an AI estimate before it goes to trade entry.
    Returns updated estimate dict with reflection applied.

    If APPROVE: returns original with confirmed confidence
    If DOWNGRADE: returns original with lowered confidence
    If REJECT: returns original with confidence="low" + reject flag
    """
    import anthropic as ant

    client = ant.Anthropic(api_key=anthropic_key)

    user_msg = f"""Trade signal to review:
Question: {question}
Market price: {market_price:.1%}
AI estimated probability: {estimated_probability:.1%}
Price gap: {abs(estimated_probability - market_price):.1%}
Confidence: {confidence}
Reasoning: {reasoning}

Challenge this signal."""

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",  # Cheap model for critic pass
            max_tokens=300,
            system=CRITIC_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = response.content[0].text.strip()

        # Parse JSON — strip markdown fences if present
        raw = raw.replace("```json", "").replace("```", "").strip()
        result = json.loads(raw)

        verdict = result.get("verdict", "APPROVE")
        new_conf = result.get("confidence", confidence)
        critique = result.get("critique", "")
        adj_prob = result.get("adjusted_probability", estimated_probability)

        log.info(f"[Reflect] {verdict} — {critique[:80]}")

        return {
            "verdict": verdict,
            "confidence": new_conf,
            "critique": critique,
            "adjusted_probability": float(adj_prob),
            "rejected": verdict == "REJECT",
        }

    except Exception as e:
        log.warning(f"[Reflect] Critic pass failed: {e} — approving original")
        return {
            "verdict": "APPROVE",
            "confidence": confidence,
            "critique": "",
            "adjusted_probability": estimated_probability,
            "rejected": False,
        }


# Coinglass context helper for reflection
def _reflection_futures_context(question: str) -> str:
    try:
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from octo_boto_ai import _get_crypto_context
        return _get_crypto_context(question)
    except Exception:
        return ""

def apply_reflection_to_batch(
    opportunities: list,
    anthropic_key: str,
    max_reflect: int = 5,
) -> list:
    """
    Apply reflection critic pass to top opportunities before entry.
    Filters out REJECT verdicts, downgrades confidence on DOWNGRADE.
    Only reflects on top max_reflect by score (don't waste tokens on low scores).

    Returns filtered, updated opportunities list.
    """
    if not opportunities or not anthropic_key:
        return opportunities

    results = []
    for i, opp in enumerate(opportunities):
        if i >= max_reflect:
            # Don't reflect on low-ranked opportunities
            results.append(opp)
            continue

        m = opp["market"]
        ai = opp["ai"]

        reflection = reflect_on_estimate(
            question=m.get("question", ""),
            market_price=float(m.get("yes_price", 0.5)),
            estimated_probability=float(ai.get("probability", 0.5)),
            confidence=ai.get("confidence", "low"),
            reasoning=ai.get("reasoning", ""),
            anthropic_key=anthropic_key,
        )

        if reflection["rejected"]:
            log.info(f"[Reflect] REJECTED opp #{i+1}: {reflection['critique']}")
            continue  # Filter out

        # Update the opportunity with reflected values
        updated_opp = dict(opp)
        updated_ai = dict(ai)
        updated_ai["confidence"] = reflection["confidence"]
        updated_ai["probability"] = reflection["adjusted_probability"]
        updated_ai["reasoning"] = (
            ai.get("reasoning", "") +
            f" [Critic: {reflection['critique']}]"
        )
        updated_ai["reflected"] = True
        updated_ai["reflect_verdict"] = reflection["verdict"]
        updated_opp["ai"] = updated_ai
        results.append(updated_opp)

    log.info(f"[Reflect] {len(opportunities)} → {len(results)} after critic pass")
    return results


# ══════════════════════════════════════════════════════════════════════════════
# INTEGRATION HELPERS — wire into octo_boto.py
# ══════════════════════════════════════════════════════════════════════════════

# Global instances (initialised in octo_boto.py main())
price_feed: Optional[BinancePriceFeed] = None
kill_switch: Optional[KillSwitch] = None
alerts: Optional[TradeAlertManager] = None


def init_upgrades(bot, chat_id: int, starting_balance: float):
    """
    Call once at bot startup to initialise all upgrade components.
    Uses shared octo_market_feed singleton instead of separate BinancePriceFeed.
    """
    global price_feed, kill_switch, alerts

    # Use shared market feed singleton
    try:
        from octo_market_feed import feed as _shared_feed, start_feed
        price_feed = _shared_feed
        if not price_feed.is_live():
            start_feed()
    except Exception:
        # Fallback to local BinancePriceFeed
        price_feed = BinancePriceFeed()
        price_feed.start()

    # Init kill switch with alert callback
    async def _ks_alert(msg: str):
        if alerts:
            await alerts.send(msg)

    kill_switch = KillSwitch(
        starting_balance=starting_balance,
        alert_callback=_ks_alert,
    )

    # Init alert manager
    alerts = TradeAlertManager(bot, chat_id)

    log.info(f"[Upgrades] Initialised — feed={'live' if price_feed.is_live() else 'connecting'}")


def check_kill_switch_sync(balance: float) -> tuple[bool, str]:
    """Synchronous kill switch check for use in non-async contexts."""
    if kill_switch is None:
        return False, ""
    return kill_switch.check(balance)


def get_binance_context() -> str:
    """
    Get a formatted string of current Binance price signals
    for injecting into scan messages.
    Uses shared feed if available.
    """
    try:
        from octo_market_feed import feed as _sf
        if _sf and _sf.is_live():
            return _sf.get_price_context()
    except Exception:
        pass
    if price_feed is None or not price_feed.is_live():
        return ""

    lines = []
    for sym in ["BTC", "ETH"]:
        sig = price_feed.get_momentum_signal(sym)
        if sig.get("price"):
            move = sig.get("move_30s", 0)
            direction = sig.get("direction", "FLAT")
            strength = sig.get("strength", "WEAK")
            icon = "📈" if direction == "UP" else "📉" if direction == "DOWN" else "➡️"
            lag = " ⚡ LAG OPP" if sig.get("lag_opportunity") else ""
            lines.append(
                f"{icon} {sym}: `${sig['price']:,.0f}` | "
                f"`{move:+.2f}%` 30s | {strength}{lag}"
            )

    return "\n".join(lines) if lines else ""

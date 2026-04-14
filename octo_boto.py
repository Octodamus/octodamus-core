"""
octo_boto.py — OctoBoto Telegram Bot v2

Fixes v2:
 - /close <n>  new command — close position by number
 - Duplicate position prevention wired end-to-end
 - _math_only_filter removed — was using naive 50% prior, producing junk signals
 - /top now shows live markets sorted by vol_liq_ratio (activity proxy)
  instead of fake EV numbers
 - job_queue.run_repeating now uses data= kwarg for PTB v20+ compatibility
 - Auto-scan respects MIN_CONFIDENCE gate ('high' or 'medium' only)
 - /resolve checks all positions concurrently (was serial, slow)
 - Scan rate-limit: manual /scan blocked if last scan < 3 minutes ago
 - Paper mode banner in every scan so it's always obvious this is simulation
 - Better error messages with actionable hints

v3 path fix:
 - All paths changed from /home/walli/octodamus/ to C:\\Users\\walli\\octodamus\\
 - Bitwarden import aligned with main system (load_all_secrets pattern)
 - Secrets loading via .octo_secrets cache file (same as main bot)

Commands:
 /start    — status overview
 /scan [n]  — AI-powered scan, enter best opportunities
 /top     — most active markets right now (no AI)
 /positions  — open positions with age and EV
 /pnl     — full stats (balance, win rate, Sharpe, drawdown)
 /close <n>  — manually exit position #N at 50% value
 /resolve   — check open positions for resolutions
 /auto    — show auto-scan status
 /autoon   — enable 4-hour auto-scan
 /autooff   — disable auto-scan
 /wallet   — wallet info
 /newwallet  — generate new Polygon wallet
 /reset    — wipe trades, reset to $500
 /help    — command reference
"""

import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
  Application,
  CommandHandler,
  ContextTypes,
)

sys.path.insert(0, r"C:\Users\walli\octodamus")

from octo_boto_math    import best_trade, is_valid_market, position_size, MIN_EV_THRESHOLD
try:
    from octo_boto_mcp import enrich_markets_with_orderbook
    _MCP_ENRICH = True
except ImportError:
    _MCP_ENRICH = False
    def enrich_markets_with_orderbook(markets, **kw): return markets

try:
    from octo_boto_consensus import gpt_second_opinion, consensus_str, kalshi_confirms_edge
    _CONSENSUS_AVAILABLE = True
except ImportError:
    _CONSENSUS_AVAILABLE = False
    def gpt_second_opinion(*a, **kw): return None
    def consensus_str(*a, **kw): return ""
    def kalshi_confirms_edge(*a, **kw): return {"confirmed": False, "contradicts": False, "kalshi_p": None, "gap": 0.0, "note": ""}

try:
    from octo_boto_calibration import record_estimate, record_outcome
    _CALIB_AVAILABLE = True
except ImportError:
    _CALIB_AVAILABLE = False
    def record_estimate(*a, **kw): pass
    def record_outcome(*a, **kw): pass
from octo_boto_polymarket import GammaClient
from octo_boto_ai     import batch_estimate, clear_cache
from octo_boto_wallet   import load_address, generate_wallet, save_address
try:
  from octo_boto_clob import (
    place_order, cancel_order, cancel_all, get_open_orders,
    get_usdc_balance, get_token_ids, clob_status_str,
    set_live_mode, is_live,
  )
  _CLOB_AVAILABLE = True
except ImportError:
  _CLOB_AVAILABLE = False
  def place_order(*a, **kw): return {"status": "clob_unavailable"}
  def cancel_order(*a, **kw): return False
  def cancel_all(): return False
  def get_open_orders(): return []
  def get_usdc_balance(): return 0.0
  def get_token_ids(*a, **kw): return {"yes": None, "no": None}
  def clob_status_str(): return "CLOB unavailable"
  def set_live_mode(e): pass
  def is_live(): return False
try:
  from octo_bankr import bankr_status_str, venice_chat
  _BANKR_AVAILABLE = True
except ImportError:
  _BANKR_AVAILABLE = False
  def bankr_status_str(): return ""
  def venice_chat(*a, **kw): return None
try:
  from octo_boto_exit import check_exit_signals, clear_trail, exit_summary_str
  _EXIT_AVAILABLE = True
except ImportError:
  _EXIT_AVAILABLE = False
  def check_exit_signals(*a, **kw): return []
  def clear_trail(*a): pass
  def exit_summary_str(*a): return ""
try:
  from octo_boto_mm import place_mm_pair, cancel_all_mm, mm_status_str, is_mm_eligible, active_mm_count
  _MM_AVAILABLE = True
except ImportError:
  _MM_AVAILABLE = False
  def place_mm_pair(*a, **kw): return None
  def cancel_all_mm(): return 0
  def mm_status_str(): return ""
  def is_mm_eligible(*a, **kw): return False
  def active_mm_count(): return 0
try:
  from octo_reputation import log_call as rep_log_call, log_outcome as rep_log_outcome, reputation_str
  _REP_AVAILABLE = True
except ImportError:
  _REP_AVAILABLE = False
  def rep_log_call(*a, **kw): return ""
  def rep_log_outcome(*a, **kw): return None
  def reputation_str(): return ""
from octo_boto_tracker  import PaperTracker, age_str, STARTING_BALANCE
from octo_boto_upgrades import (
  init_upgrades, check_kill_switch_sync, get_binance_context,
  apply_reflection_to_batch, price_feed, kill_switch, alerts as upgrade_alerts,
)
try:
  from octo_boto_oracle_bridge import on_position_opened, on_position_closed
  _ORACLE_BRIDGE = True
except ImportError:
  _ORACLE_BRIDGE = False
  def on_position_opened(pos): pass
  def on_position_closed(closed, balance): pass


# ─── Logging ──────────────────────────────────────────────────────────────────
LOG_DIR = Path(r"C:\Users\walli\octodamus\logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
  level=logging.INFO,
  format="%(asctime)s [%(levelname)s] %(message)s",
  handlers=[
    logging.StreamHandler(),
    logging.FileHandler(LOG_DIR / "octo_boto.log"),
  ],
)
log = logging.getLogger("OctoBoto")

# ─── Config ───────────────────────────────────────────────────────────────────
AUTO_SCAN_INTERVAL = 4 * 60 * 60  # 4 hours between auto scans
SCAN_LIMIT     = 150    # Markets to fetch per scan (paginated)
AI_BATCH_LIMIT   = 15     # Max markets sent to AI per scan
AUTO_MIN_EV    = 0.15    # Matches TRIPLE_LOCK_MIN_EV — no point estimating below entry floor
AUTO_MAX_ENTER   = 3
MAX_TRADES_PER_WEEK = 999     # Hard cap — quality over quantity
     # Max new positions per auto-scan
SCAN_COOLDOWN   = 3 * 60   # Prevent /scan spam — 3 min cooldown
MIN_CONF_AUTO   = {"high"}  # confidence levels that trigger auto-entry

GAMMA  = GammaClient(min_liquidity=3_000)
TRACKER = PaperTracker()

auto_enabled: bool = False
_last_scan_time: float = 0.0

# ── Price Velocity Tracker (Markov state change detection) ────────────────
# Stores {market_id: (price, timestamp)} from last scan.
# If price moves >5% between scans, new information just entered the market.
_price_snapshot: dict = {}

def _compute_velocity(market_id: str, current_price: float) -> float:
    """
    Returns % price change since last scan for this market.
    Positive = price moved up. Negative = moved down. 0 = no prior data.
    Updates snapshot in place.
    """
    import time as _time
    prev = _price_snapshot.get(market_id)
    _price_snapshot[market_id] = (current_price, _time.time())
    if prev is None:
        return 0.0
    prev_price, _ = prev
    if prev_price <= 0:
        return 0.0
    return round(((current_price - prev_price) / prev_price) * 100, 2)

CONFIG_FILE = Path(r"C:\Users\walli\octodamus\octo_boto_config.json")


def _load_config() -> dict:
  import json
  if CONFIG_FILE.exists():
    try:
      return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
      pass
  return {}


def _save_config(data: dict) -> None:
  import json
  existing = _load_config()
  existing.update(data)
  CONFIG_FILE.write_text(json.dumps(existing, indent=2), encoding="utf-8")


# ─── Secrets ──────────────────────────────────────────────────────────────────

def load_secrets() -> dict:
  import json
  secrets = {}
  all_keys = ("OCTOBOTO_TELEGRAM_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OCTOBOTO_WALLET_KEY")

  # Primary: .octo_secrets cache (written by boot script from Bitwarden)
  cache = Path(r"C:\Users\walli\octodamus\.octo_secrets")
  if cache.exists():
    try:
      raw = json.loads(cache.read_text(encoding="utf-8"))
      data = raw.get("secrets", raw)
      for key in all_keys:
        if data.get(key):
          secrets[key] = data[key]
          log.info(f"[Secrets] {key} loaded from cache")
    except Exception as e:
      log.warning(f"[Secrets] Cache read failed: {e}")

  # Supplement: octo_extra_secrets.json — persists keys not synced from Bitwarden
  extra = Path(r"C:\Users\walli\octodamus\octo_extra_secrets.json")
  if extra.exists():
    try:
      data = json.loads(extra.read_text(encoding="utf-8"))
      for key, val in data.items():
        if val and key not in secrets:
          secrets[key] = val
          log.info(f"[Secrets] {key} loaded from extra secrets")
    except Exception as e:
      log.warning(f"[Secrets] Extra secrets read failed: {e}")

  # Fallback: environment variables
  for key in all_keys:
    if key not in secrets and os.getenv(key):
      secrets[key] = os.getenv(key)

  return secrets


SECRETS      = load_secrets()
BOT_TOKEN    = SECRETS.get("OCTOBOTO_TELEGRAM_TOKEN", "")
ANTHROPIC_KEY = SECRETS.get("ANTHROPIC_API_KEY", "")
OPENAI_KEY   = SECRETS.get("OPENAI_API_KEY", "")

if not BOT_TOKEN:
  log.error("OCTOBOTO_TELEGRAM_TOKEN missing. Add to Bitwarden or set env var.")
  sys.exit(1)

if not ANTHROPIC_KEY:
  log.warning("ANTHROPIC_API_KEY missing — /scan will be unavailable.")


# ─── Sector Correlation (#7) ──────────────────────────────────────────────────

_SECTOR_MAP = {
    "crypto":   ["bitcoin", "btc", "ethereum", "eth", "solana", "sol", "crypto",
                 "defi", "nft", "blockchain", "altcoin", "stablecoin"],
    "politics": ["election", "president", "congress", "senate", "vote", "poll",
                 "democrat", "republican", "ballot", "trump", "biden", "harris",
                 "governor", "parliament", "prime minister"],
    "macro":    ["fed", "federal reserve", "interest rate", "cpi", "inflation",
                 "gdp", "recession", "unemployment", "s&p", "nasdaq", "dow",
                 "treasury", "yield curve"],
    "ai_tech":  ["openai", "gpt", "claude", "gemini", "ai model", "llm",
                 "artificial intelligence", "nvidia", "microsoft", "google",
                 "apple", "meta", "amazon", "tech stock"],
    "geopolitics": ["war", "conflict", "ceasefire", "sanctions", "nato",
                    "ukraine", "russia", "china", "taiwan", "middle east",
                    "iran", "israel", "north korea"],
}
MAX_SECTOR_POSITIONS = 2   # max open positions per sector before skipping


def get_market_sector(question: str) -> str:
    """Return the primary sector for a market question."""
    q = question.lower()
    for sector, keywords in _SECTOR_MAP.items():
        if any(kw in q for kw in keywords):
            return sector
    return "other"


def count_sector_positions(sector: str) -> int:
    """Count open positions in a given sector."""
    return sum(
        1 for p in TRACKER.open_positions()
        if get_market_sector(p.get("question", "")) == sector
    )


# ─── Drawdown Pause (#8) ──────────────────────────────────────────────────────

DRAWDOWN_PAUSE_THRESHOLD = 0.15   # Pause new entries if balance drops 15% from peak

def is_drawdown_pause_active() -> tuple:
    """
    Soft circuit breaker: return (paused, message) if balance is 15%+ below peak.
    Distinct from the hard kill switch (fires at -40%).
    """
    try:
        import octo_boto_upgrades as _upg
        ks = _upg.kill_switch
        if ks is None:
            return False, ""
        balance  = TRACKER.balance()
        peak     = ks.peak_balance
        if peak <= 0:
            return False, ""
        drawdown = (balance - peak) / peak
        if drawdown <= -DRAWDOWN_PAUSE_THRESHOLD:
            return True, (
                f"Drawdown pause: {drawdown:.1%} from peak ${peak:.2f}. "
                f"New entries paused until recovery. Use /ksreset to override."
            )
    except Exception:
        pass
    return False, ""


# ─── Triple-Lock Entry Gate ───────────────────────────────────────────────────

TRIPLE_LOCK_MIN_EV        = 0.15   # Must clear 15% EV (up from 12% scan floor)
TRIPLE_LOCK_MAX_DAYS      = 21     # Only enter markets resolving within 21 days
_CRYPTO_MARKET_KEYWORDS   = [
    "bitcoin", "btc", "ethereum", "eth", "solana", "sol",
    "crypto", "binance", "coinbase", "bnb", "xrp", "doge",
]

def _is_crypto_market(question: str) -> bool:
    q = question.lower()
    return any(k in q for k in _CRYPTO_MARKET_KEYWORDS)


def _octo_signal_conflicts(question: str, trade_side: str, octo_direction: str) -> str:
    """
    Returns a non-empty block reason if the Octodamus signal conflicts with
    the proposed trade direction. Only applies to crypto markets.

    trade_side:     "YES" or "NO"
    octo_direction: "STRONG UP", "UP", "STRONG DOWN", "DOWN", "NEUTRAL"

    A YES trade on a bullish price milestone conflicts with a DOWN signal.
    A NO trade on a bullish price milestone conflicts with an UP signal.
    """
    if not _is_crypto_market(question):
        return ""
    if octo_direction in ("", "NEUTRAL"):
        return ""

    bearish = octo_direction in ("DOWN", "STRONG DOWN")
    bullish = octo_direction in ("UP", "STRONG UP")

    q = question.lower()
    # Detect bullish price milestone questions (e.g. "will BTC hit $100k?")
    bullish_framing = any(k in q for k in [
        "above", "exceed", "hit", "reach", "surpass", "over", "break",
        "all-time high", "ath", "new high",
    ])
    bearish_framing = any(k in q for k in [
        "below", "under", "drop", "fall", "crash", "lose",
    ])

    if bullish_framing:
        # YES = bullish outcome — conflicts with bearish signal
        if trade_side == "YES" and bearish:
            return f"Octodamus signal {octo_direction} conflicts with YES on bullish milestone"
        # NO = bearish outcome on bullish question — conflicts with bullish signal
        if trade_side == "NO" and bullish:
            return f"Octodamus signal {octo_direction} conflicts with NO on bullish milestone"

    if bearish_framing:
        # YES = bearish outcome — conflicts with bullish signal
        if trade_side == "YES" and bullish:
            return f"Octodamus signal {octo_direction} conflicts with YES on bearish milestone"
        # NO = bullish outcome — conflicts with bearish signal
        if trade_side == "NO" and bearish:
            return f"Octodamus signal {octo_direction} conflicts with NO on bearish milestone"

    return ""


# ─── Format Helpers ───────────────────────────────────────────────────────────

CONF_ICON = {"high": "🔵", "medium": "⚪", "low": "🔴"}
EV_ICON  = lambda ev: "🟢" if ev >= 0.10 else "🟡" if ev >= 0.06 else "⚫"


def fmt_opportunity(m: dict, trade: dict, ai: dict, rank: int, score: float) -> str:
  q    = m["question"]
  q_short = q[:90] + "..." if len(q) > 90 else q
  dtc   = m.get("days_to_close")
  dtc_str = f"{dtc}d" if dtc is not None else "?"
  cached = " *(cached)*" if ai.get("cached") else ""

  return (
    f"{EV_ICON(trade['ev'])} *#{rank}* — Score: `{score:.3f}`{cached}\n"
    f"📋 {q_short}\n"
    f"Market: `{m['yes_price']:.0%}` → True: `{ai['probability']:.0%}` | "
    f"Side: *{trade['side']}* | EV: `{trade['ev']:+.0%}`\n"
    f"{CONF_ICON.get(ai['confidence'], '⚪')} {ai['confidence'].upper()} | "
    f"Liq: `${m['liquidity']/1000:.0f}k` | Vol24: `${m['volume24h']/1000:.1f}k` | "
    f"Closes: `{dtc_str}`\n"
    f"💭 _{ai.get('reasoning', '')[:160]}_"
    + (f"\n[→ Polymarket]({m['url']})" if m.get("url") else "")
  )


def fmt_position(pos: dict, num: int) -> str:
  age   = age_str(pos.get("opened_at", ""))
  return (
    f"*#{num}* {pos['side']} `${pos['size']:.2f}` @ `{pos['entry_price']:.3f}`\n"
    f"  EV: `{pos['ev']:+.1%}` | Conf: {pos['confidence']} | Age: {age}\n"
    f"  {pos['question'][:70]}..."
  )


def fmt_pnl(s: dict) -> str:
  arrow = "📈" if s["total_pnl"] >= 0 else "📉"
  pnl_s = f"+${s['total_pnl']:.2f}" if s["total_pnl"] >= 0 else f"-${abs(s['total_pnl']):.2f}"
  conf_label = {1.0: "low", 2.0: "medium", 3.0: "high"}.get(round(s["avg_conf_score"]), "mixed")
  return (
    f"📄 *OctoBoto — Paper Trading P&L*\n\n"
    f"💰 Balance:  `${s['balance']:.2f}` (started `${s['starting']:.2f}`)\n"
    f"{arrow} Total P&L: `{pnl_s}` ({s['total_pnl_pct']:+.1f}%)\n"
    f"💸 Fees paid: `${s['fees_paid']:.2f}`\n"
    f"📊 Trades:   {s['num_trades']} closed | {s['open_count']} open\n"
    f"🏆 Win rate:  `{s['win_rate']:.1f}%` ({s['wins']}W / {s['losses']}L)\n"
    f"📐 Sharpe:   `{s['sharpe']:.2f}`\n"
    f"📉 Max DD:   `{s['max_drawdown']:.1f}%`\n"
    f"💼 Deployed:  `${s['deployed']:.2f}`\n"
    f"🎯 Avg entry EV: `{s['avg_ev']:+.1%}`\n"
    f"🧠 Avg confidence: {conf_label}"
  )


# ─── Commands ─────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
  s   = TRACKER.pnl_summary()
  ai_ok = "✅" if ANTHROPIC_KEY else "❌ key missing"
  auto_s = "🟢 ON" if auto_enabled else "⚫ OFF"
  pnl_s = f"+${s['total_pnl']:.2f}" if s["total_pnl"] >= 0 else f"-${abs(s['total_pnl']):.2f}"

  await update.message.reply_text(
    f" *OctoBoto v2 — Polymarket Edge Hunter*\n\n"
    f"Mode:  📄 Paper Trading (starting ${s['starting']:.0f})\n"
    f"AI:   {ai_ok}\n"
    f"Auto:  {auto_s}\n\n"
    f"Balance: `${s['balance']:.2f}` | P&L: `{pnl_s}`\n"
    f"Open: {s['open_count']} | Closed: {s['num_trades']} | Win: {s['win_rate']:.0f}%\n\n"
    f"Type /help for commands",
    parse_mode=ParseMode.MARKDOWN
  )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
  await update.message.reply_text(
    "*OctoBoto v2 — Commands*\n\n"
    "`/scan [n]`   AI scan of n markets (default 150)\n"
    "`/top`     Most active markets (no AI)\n"
    "`/positions`  Open positions with EV + age\n"
    "`/pnl`     Full P&L stats\n"
    "`/close <n>`  Exit position #n at 50% value\n"
    "`/resolve`   Check open positions for resolution\n"
    "`/autoon`    Enable 4-hour auto-scan\n"
    "`/autooff`   Disable auto-scan\n"
    "`/auto`     Auto-scan status\n"
    "`/wallet`    Wallet address + balance\n"
    "`/newwallet`  Generate new Polygon wallet\n"
    "`/reset`    Reset to $500 paper balance\n"
    "`/stats`     Category payout ratio performance\n"
    "`/quickscan`  Near-resolution scalp: 88-97¢ markets, +5¢ target\n"
    "`/contrascan` Contrarian: 3-12¢ w/ 85%+ edge + 1-2¢ lottery tier\n",
    parse_mode=ParseMode.MARKDOWN
  )


async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
  """Full AI-powered scan."""
  global _last_scan_time

  if not ANTHROPIC_KEY:
    await update.message.reply_text(
      "❌ ANTHROPIC_API_KEY missing.\n"
      "Add to Bitwarden as `AGENT - Octodamus - Anthropic` and restart."
    )
    return

  # Rate limit
  elapsed = time.time() - _last_scan_time
  if elapsed < SCAN_COOLDOWN:
    wait = int(SCAN_COOLDOWN - elapsed)
    await update.message.reply_text(f"⏳ Please wait {wait}s before scanning again.")
    return

  limit = 150
  if ctx.args:
    try:
      limit = max(20, min(300, int(ctx.args[0])))
    except ValueError:
      pass

  _last_scan_time = time.time()
  binance_ctx = get_binance_context()
  binance_line = f"\n{binance_ctx}" if binance_ctx else ""
  await update.message.reply_text(
    f"🔍 *OctoBoto Scan* — 📄 Paper mode\n"
    f"Fetching {limit} markets → filtering → AI estimation...\n"
    f"_(~2-4 minutes)_{binance_line}",
    parse_mode=ParseMode.MARKDOWN
  )

  try:
    markets = GAMMA.get_markets(limit=limit)
    if not markets:
      await update.message.reply_text("❌ Gamma API returned no markets. Try again in 1 minute.")
      return

    # Crypto-specific pass — fetches BTC/ETH/SOL markets separately so
    # they don't get crowded out by high-volume event markets.
    # Coinglass futures context activates automatically for these in the AI estimator.
    crypto_markets = GAMMA.get_crypto_markets()
    seen_ids = {m["id"] for m in markets}
    crypto_new = [m for m in crypto_markets if m["id"] not in seen_ids]
    markets = markets + crypto_new

    valid = [m for m in markets if is_valid_market(m)]

    # Inject price velocity into each market dict (Markov state change detection)
    velocity_alerts = []
    for m in valid:
        vel = _compute_velocity(m["id"], float(m.get("yes_price", 0.5)))
        m["_velocity_pct"] = vel
        if abs(vel) >= 5.0:
            direction = "↑" if vel > 0 else "↓"
            velocity_alerts.append(f"{direction}{abs(vel):.1f}% — {m['question'][:60]}")

    # Enrich with orderbook depth + liquidity; filter thin/wide-spread markets
    if _MCP_ENRICH:
        pre_enrich = len(valid)
        valid = enrich_markets_with_orderbook(valid, max_markets=AI_BATCH_LIMIT * 2)
        enrich_note = f" | {len(valid)}/{pre_enrich} pass orderbook filter"
    else:
        enrich_note = ""

    vel_str = ""
    if velocity_alerts:
        vel_str = "\n⚡ *Velocity alerts* (new info entered):\n" + "\n".join(f"  {a}" for a in velocity_alerts[:5])

    await update.message.reply_text(
      f"📊 {len(markets)} fetched ({len(crypto_new)} crypto) → {len([m for m in markets if is_valid_market(m)])} pass filters{enrich_note}\n"
      f"Running AI on up to {AI_BATCH_LIMIT}...{vel_str}",
      parse_mode=ParseMode.MARKDOWN
    )

    opps = batch_estimate(valid, ANTHROPIC_KEY, max_markets=AI_BATCH_LIMIT)

    if not opps:
      await update.message.reply_text(
        f"🔍 No EV>={MIN_EV_THRESHOLD:.0%} opportunities in {len(valid)} valid markets.\n"
        f"Market is efficient right now. Try `/autoon` for ongoing monitoring."
      )
      return

    # Reflection critic pass on top opportunities
    opps = apply_reflection_to_batch(opps, ANTHROPIC_KEY, max_reflect=3)

    entered = 0
    await update.message.reply_text(
      f"🎯 *{len(opps)} opportunity{'s' if len(opps)!=1 else ''} found* — entering best signals...",
      parse_mode=ParseMode.MARKDOWN
    )

    # Drawdown pause check (#8) — soft circuit breaker at -15%
    _dd_paused, _dd_reason = is_drawdown_pause_active()
    if _dd_paused:
      await update.message.reply_text(
        f"⚠️ {_dd_reason}\nShowing opportunities but NOT entering positions.",
        parse_mode=ParseMode.MARKDOWN
      )

    for i, opp in enumerate(opps[:5], 1):
      m, ai, trade, score = opp["market"], opp["ai"], opp["trade"], opp["score"]
      sector = get_market_sector(m.get("question", ""))

      # GPT second opinion (#4) for top 3 opportunities
      gpt_result = None
      if i <= 3 and OPENAI_KEY and _CONSENSUS_AVAILABLE:
        gpt_result = gpt_second_opinion(m["question"], m["yes_price"], OPENAI_KEY)
        if gpt_result:
          gap = abs(ai["probability"] - gpt_result.get("probability", ai["probability"]))
          if gap > 0.10:
            gpt_result["_disagree"] = True

      # Kalshi cross-check — highest-authority external signal
      kalshi_check = None
      if _CONSENSUS_AVAILABLE:
        kalshi_check = kalshi_confirms_edge(
          m["question"], trade["side"], m["yes_price"], min_gap=0.06
        )

      # Build display with freshness tag and resolution risk note
      opp_text = fmt_opportunity(m, trade, ai, i, score)
      age_h = opp.get("market_age_hours")
      if age_h is not None and age_h < 24:
        opp_text += f"\n  NEW market ({age_h:.0f}h old) — freshness bonus applied"
      risk = opp.get("resolution_risk", 0)
      if risk >= 0.3:
        opp_text += f"\n  Resolution risk: {risk:.0%} — criteria somewhat ambiguous"
      if gpt_result:
        opp_text += consensus_str(ai["probability"], gpt_result)
      if kalshi_check and kalshi_check.get("kalshi_p") is not None:
        k_icon = "✅" if kalshi_check["confirmed"] else ("🚫" if kalshi_check["contradicts"] else "➡️")
        opp_text += f"\n  {k_icon} Kalshi: {kalshi_check['kalshi_p']:.0%} ({kalshi_check['note'][:80]})"

      await update.message.reply_text(
        opp_text,
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True
      )

      # Kill switch check before entry
      _ks_hit, _ks_reason = check_kill_switch_sync(TRACKER.balance())
      if _ks_hit:
        await update.message.reply_text(
          f"🚨 Kill switch active — trading halted\n{_ks_reason}\nUse /ksreset to resume.",
          parse_mode=ParseMode.MARKDOWN
        )
        break

      # Sector correlation gate (#7) — max 2 open per sector
      sector_count = count_sector_positions(sector)
      if sector_count >= MAX_SECTOR_POSITIONS:
        await update.message.reply_text(
          f"⏭️ Skipping #{i} — already {sector_count} open in *{sector}* sector (max {MAX_SECTOR_POSITIONS})",
          parse_mode=ParseMode.MARKDOWN
        )
        continue

      # GPT disagreement gate — skip entry if models disagree by >10% (triple-lock)
      if gpt_result and gpt_result.get("_disagree"):
        await update.message.reply_text(
          f"⏭️ Skipping #{i} — Claude/GPT disagree by >10%. Not entering.",
          parse_mode=ParseMode.MARKDOWN
        )
        continue

      # ── Session guard — halt if daily loss limit or losing streak hit ──
      guard = TRACKER.session_guard(max_daily_loss=30.0, max_consecutive_losses=3)
      if guard["blocked"]:
        await update.message.reply_text(
          f"🛑 *Session guard active* — {guard['reason']}\n"
          f"Daily P&L: `${guard['daily_pnl']:+.2f}` | Streak: `{guard['consecutive_losses']} losses`\n"
          f"No new positions until tomorrow.",
          parse_mode=ParseMode.MARKDOWN
        )
        break

      # ── Triple-lock gate ─────────────────────────────────────────────────
      _tl_skip = ""
      if _dd_paused:
        _tl_skip = "drawdown pause active"
      elif ai.get("confidence") not in MIN_CONF_AUTO:
        _tl_skip = f"confidence too low ({ai.get('confidence')})"
      elif trade["ev"] < TRIPLE_LOCK_MIN_EV:
        _tl_skip = f"EV {trade['ev']:+.1%} < {TRIPLE_LOCK_MIN_EV:.0%} triple-lock floor"
      elif TRACKER.has_position(m["id"]):
        _tl_skip = "duplicate position"
      else:
        dtc = m.get("days_to_close")
        if dtc is not None and dtc > TRIPLE_LOCK_MAX_DAYS:
          _tl_skip = f"resolves in {dtc}d > {TRIPLE_LOCK_MAX_DAYS}d limit"
        elif kalshi_check and kalshi_check.get("contradicts") and kalshi_check.get("gap", 0) >= 0.10:
          _tl_skip = f"Kalshi contradicts — {kalshi_check['note']}"
        else:
          _signal_conflict = _octo_signal_conflicts(
            m.get("question", ""), trade["side"],
            ai.get("octo_direction", "NEUTRAL"),
          )
          if _signal_conflict:
            _tl_skip = _signal_conflict

      if _tl_skip:
        await update.message.reply_text(
          f"⛔ Skipping #{i} — {_tl_skip}",
          parse_mode=ParseMode.MARKDOWN
        )
        continue

      # Triple-lock passed — open position
      side = trade["side"]
      ep  = m["yes_price"] if side == "YES" else (1.0 - m["yes_price"])
      size = position_size(TRACKER.balance(), trade["kelly"])

      pos = TRACKER.open_position(
        market_id=m["id"], question=m["question"], side=side,
        size=size, entry_price=ep, true_p=ai["probability"],
        ev=trade["ev"], kelly_frac=trade["kelly"],
        confidence=ai["confidence"], reasoning=ai["reasoning"],
        score=score, url=m.get("url", "")
      )

      if pos:
        entered += 1
        on_position_opened(pos)
        # Record for calibration tracking (#10)
        record_estimate(
          market_id=m["id"], question=m["question"],
          claude_p=ai["probability"], market_price=m["yes_price"],
          confidence=ai["confidence"], side=side,
        )
        # CLOB execution — place real order if live
        clob_result = None
        if _CLOB_AVAILABLE and is_live():
          tokens = get_token_ids(m.get("conditionId", m["id"]))
          token_id = tokens["yes"] if side == "YES" else tokens["no"]
          if token_id:
            # ── Orderbook liquidity check before executing ──────────────
            from octo_boto_clob import check_orderbook_liquidity
            liq = check_orderbook_liquidity(token_id, ep, min_usdc=size)
            if not liq["sufficient"]:
              log.warning(
                f"[OctoBoto] Thin orderbook for {m['question'][:50]} — "
                f"only ${liq['available_usdc']:.2f} available at {ep:.3f}, need ${size:.2f}. Skipping CLOB order."
              )
              clob_result = {
                "order_id": "SKIPPED-thin-book",
                "status": "skipped",
                "live": False,
                "reason": f"insufficient liquidity (${liq['available_usdc']:.2f} < ${size:.2f})",
              }
            else:
              clob_result = place_order(
                token_id=token_id, side="BUY",
                price=ep, amount_usdc=size,
                market_question=m["question"],
              )
        mode_label = "🟢 LIVE" if (clob_result and clob_result.get("live")) else "📋 Paper"
        order_note = f"\nOrder: `{clob_result['order_id']}`" if clob_result and clob_result.get("order_id") else ""
        await update.message.reply_text(
          f"📥 *{mode_label} position opened #{entered}*\n"
          f"Side: *{side}* | Size: `${size:.2f}` | @ `{ep:.3f}`\n"
          f"Kelly: `{trade['kelly']:.1%}` | EV: `{trade['ev']:+.1%}` | Score: `{score:.3f}`\n"
          f"Sector: *{sector}*{order_note}",
          parse_mode=ParseMode.MARKDOWN
        )
        if upgrade_alerts:
            await upgrade_alerts.trade_opened(pos, TRACKER.balance())

    s = TRACKER.pnl_summary()
    await update.message.reply_text(
      f"✅ Scan complete — {len(opps)} found | {entered} entered\n"
      f"Balance: `${s['balance']:.2f}` | Deployed: `${s['deployed']:.2f}` | Open: {s['open_count']}",
      parse_mode=ParseMode.MARKDOWN
    )

  except Exception as e:
    log.exception("scan error")
    await update.message.reply_text(f"❌ Scan error: {e}")


async def cmd_top(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
  """
  Instant top markets by activity — no AI, no tokens.
  Bug fix v2: shows vol_liq_ratio instead of fake EV from 50% naive prior.
  """
  await update.message.reply_text("⚡ Fetching most active markets...")
  try:
    markets = GAMMA.get_markets(limit=200)
    valid  = [m for m in markets if is_valid_market(m)]
    # Sort by 24h volume/liquidity ratio — most actively traded = most price discovery
    ranked = sorted(valid, key=lambda m: m.get("vol_liq_ratio", 0), reverse=True)

    if not ranked:
      await update.message.reply_text("No active markets found right now.")
      return

    lines = [f"*Top Markets by Activity*\n_(no AI — use /scan for edge analysis)_\n"]
    for i, m in enumerate(ranked[:8], 1):
      dtc  = m.get("days_to_close")
      dtc_s = f"{dtc}d" if dtc is not None else "?"
      turnover = m.get("vol_liq_ratio", 0)
      q = m["question"][:65]
      lines.append(
        f"*{i}.* `{m['yes_price']:.0%}` YES | Turnover: `{turnover:.1%}` | {dtc_s}\n"
        f"  {q}"
      )

    await update.message.reply_text(
      "\n\n".join(lines), parse_mode=ParseMode.MARKDOWN,
      disable_web_page_preview=True
    )
  except Exception as e:
    await update.message.reply_text(f"❌ {e}")


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
  """Category payout ratio performance report."""
  from octo_boto_brain import format_category_stats_report
  report = format_category_stats_report()
  await update.message.reply_text(report, parse_mode=ParseMode.MARKDOWN)


# ── Quickscan helpers ─────────────────────────────────────────────────────────

SCALP_MAX_DAYS  = 2.0    # Only markets within 48 hours of resolution
SCALP_ENTRY_MIN = 0.88   # High-prob side must be at least 88¢
SCALP_ENTRY_MAX = 0.97   # Cap at 97¢ — above this there's no room to drift
SCALP_TARGET    = 0.05   # Sell target: +5¢ from entry
SCALP_STOP      = -0.04  # Stop loss: -4¢ from entry
SCALP_SIZE_USD  = 50.0   # Fixed position size (no Kelly — no AI confidence)
SCALP_MIN_LIQ   = 1_000  # Minimum liquidity to ensure fillable orders


def _is_scalp_candidate(m: dict) -> bool:
    """Market is near resolution with one side 88-97¢."""
    dtc = m.get("days_to_close")
    if dtc is None or dtc > SCALP_MAX_DAYS or dtc < 0:
        return False
    liq = float(m.get("liquidity", 0) or 0)
    if liq < SCALP_MIN_LIQ:
        return False
    yes = float(m.get("yes_price", 0.5))
    high = max(yes, 1.0 - yes)
    return SCALP_ENTRY_MIN <= high <= SCALP_ENTRY_MAX


def _scalp_side(m: dict) -> tuple[str, float]:
    """Return (side, entry_price) for the high-probability outcome."""
    yes = float(m.get("yes_price", 0.5))
    if yes >= (1.0 - yes):
        return "YES", yes
    return "NO", round(1.0 - yes, 4)


async def cmd_quickscan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    Near-resolution scalp scan.
    Targets markets 88-97¢ with <= 48h to close — buy the drift to 100¢.
    No AI needed. Entry: current price. Target: +5¢. Stop: -4¢.
    65-70% historical win rate at this price range per Stacy on Chain research.
    """
    await update.message.reply_text(
        "⚡ *Quickscan* — hunting 88-97¢ near-resolution scalps...",
        parse_mode=ParseMode.MARKDOWN
    )
    try:
        markets = GAMMA.get_markets(limit=300)
        crypto_markets = GAMMA.get_crypto_markets()
        seen_ids = {m["id"] for m in markets}
        all_markets = markets + [m for m in crypto_markets if m["id"] not in seen_ids]

        candidates = [m for m in all_markets if _is_scalp_candidate(m)]

        # Sort: quickest-to-close first (most mechanical drift)
        candidates.sort(key=lambda m: m.get("days_to_close", 99))

        if not candidates:
            await update.message.reply_text(
                "🔍 No 88-97¢ markets within 48h of resolution right now.\n"
                "Try again later — these appear as markets approach their end date."
            )
            return

        _dd_paused, _dd_reason = is_drawdown_pause_active()
        if _dd_paused:
            await update.message.reply_text(
                f"⚠️ {_dd_reason}\nShowing scalps but NOT entering positions.",
                parse_mode=ParseMode.MARKDOWN
            )

        await update.message.reply_text(
            f"📊 *{len(candidates)} scalp candidates* — entering best setups\n"
            f"Logic: buy drift to resolution | Target: +5¢ | Stop: -4¢ | Size: ${SCALP_SIZE_USD:.0f}",
            parse_mode=ParseMode.MARKDOWN
        )

        entered = 0
        displayed = 0
        for m in candidates[:10]:
            side, ep = _scalp_side(m)
            target   = round(ep + SCALP_TARGET, 4)
            stop     = round(ep + SCALP_STOP, 4)
            dtc      = m.get("days_to_close", 0)
            dtc_str  = f"{dtc*24:.1f}h" if dtc < 1 else f"{dtc:.1f}d"
            pct_gain = round(SCALP_TARGET / ep * 100, 1)
            pct_loss = round(abs(SCALP_STOP) / ep * 100, 1)

            text = (
                f"📈 *SCALP* — `{ep:.0%}` {side} | Closes in `{dtc_str}`\n"
                f"Entry: `{ep:.3f}` → Target: `{target:.3f}` (+{pct_gain:.1f}%) | "
                f"Stop: `{stop:.3f}` (-{pct_loss:.1f}%)\n"
                f"*{m['question'][:85]}*"
            )
            await update.message.reply_text(
                text, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True
            )
            displayed += 1

            # Kill switch
            _ks_hit, _ks_reason = check_kill_switch_sync(TRACKER.balance())
            if _ks_hit:
                await update.message.reply_text(
                    f"🚨 Kill switch — trading halted\n{_ks_reason}",
                    parse_mode=ParseMode.MARKDOWN
                )
                break

            # Entry — mechanical trade, no AI confidence gate
            if not _dd_paused and not TRACKER.has_position(m["id"]):
                # Fake kelly of 0.1 — small fixed size, no AI estimate
                fake_kelly = round(SCALP_SIZE_USD / max(TRACKER.balance(), 1), 4)
                pos = TRACKER.open_position(
                    market_id=m["id"], question=m["question"], side=side,
                    size=SCALP_SIZE_USD, entry_price=ep, true_p=round(ep + 0.05, 3),
                    ev=round(SCALP_TARGET - abs(SCALP_STOP) * 0.35, 4),
                    kelly_frac=fake_kelly,
                    confidence="scalp", reasoning=f"Near-resolution scalp: {dtc_str} to close",
                    score=round(ep, 3), url=m.get("url", "")
                )
                if pos:
                    entered += 1
                    on_position_opened(pos)
                    # CLOB execution if live
                    clob_result = None
                    if _CLOB_AVAILABLE and is_live():
                        tokens = get_token_ids(m.get("conditionId", m["id"]))
                        token_id = tokens["yes"] if side == "YES" else tokens["no"]
                        if token_id:
                            clob_result = place_order(
                                token_id=token_id, side="BUY",
                                price=ep, amount_usdc=SCALP_SIZE_USD,
                                market_question=m["question"],
                            )
                    mode_label = "🟢 LIVE" if (clob_result and clob_result.get("live")) else "📋 Paper"
                    await update.message.reply_text(
                        f"📥 *{mode_label} scalp opened #{entered}*\n"
                        f"Side: {side} @ `{ep:.3f}` | Size: `${SCALP_SIZE_USD:.0f}` | "
                        f"Target: `{target:.3f}` | Stop: `{stop:.3f}`",
                        parse_mode=ParseMode.MARKDOWN
                    )
                    if upgrade_alerts:
                        await upgrade_alerts.trade_opened(pos, TRACKER.balance())

        s = TRACKER.pnl_summary()
        await update.message.reply_text(
            f"✅ Quickscan done — {displayed} scalps shown | {entered} entered\n"
            f"Balance: `${s['balance']:.2f}` | Open: {s['open_count']}",
            parse_mode=ParseMode.MARKDOWN
        )

    except Exception as e:
        log.exception("quickscan error")
        await update.message.reply_text(f"❌ Quickscan error: {e}")


async def cmd_contrascan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
  """
  Low-entry contrarian scan.
  Targets markets priced 3-12c where Octodamus signals 85%+ true probability.
  At these prices a correct call pays 7-30x — only needs to hit ~15% of the time.
  """
  global _last_scan_time

  if not ANTHROPIC_KEY:
    await update.message.reply_text(
      "❌ ANTHROPIC_API_KEY missing — add to Bitwarden and restart."
    )
    return

  elapsed = time.time() - _last_scan_time
  if elapsed < SCAN_COOLDOWN:
    wait = int(SCAN_COOLDOWN - elapsed)
    await update.message.reply_text(f"⏳ Please wait {wait}s before scanning again.")
    return

  _last_scan_time = time.time()
  await update.message.reply_text(
    "🎯 *Contrarian Scan* — hunting 3-12¢ markets with data edge\n"
    "_(~2-3 min)_",
    parse_mode=ParseMode.MARKDOWN
  )

  try:
    markets = GAMMA.get_markets(limit=300)
    crypto_markets = GAMMA.get_crypto_markets()
    seen_ids = {m["id"] for m in markets}
    markets = markets + [m for m in crypto_markets if m["id"] not in seen_ids]

    # ── Lottery Tier (1-2¢) — volatility-gated, no AI, hold to resolution ────
    # Only eligible on low-volatility conditions: BTC < 0.5% move in last 5min.
    # At 1¢, a correct call pays 100x. Even 2% hit rate = positive EV.
    # Use local re-import so we get the live feed instance set in main(),
    # not the stale None captured at module import time.
    from octo_boto_upgrades import price_feed as _live_feed
    btc_5m_move = None
    low_vol = False
    if _live_feed and _live_feed.is_live():
        btc_5m_move = _live_feed.get_move("BTC", window_seconds=300)
        if btc_5m_move is not None:
            low_vol = abs(btc_5m_move) < 0.5

    lottery_candidates = [
        m for m in markets
        if 0.01 <= float(m.get("yes_price", 1.0)) <= 0.02
        and float(m.get("liquidity", 0) or 0) >= 500
    ]

    if lottery_candidates and low_vol:
        btc_str = f"{btc_5m_move:+.2f}%" if btc_5m_move is not None else "unknown"
        await update.message.reply_text(
            f"🎰 *Lottery Tier* — {len(lottery_candidates)} markets at 1-2¢ | "
            f"BTC 5m move: `{btc_str}` (low-vol gate: ✅)\n"
            f"Fixed `$10` size | Hold to resolution | 100x payout if correct",
            parse_mode=ParseMode.MARKDOWN
        )
        lottery_entered = 0
        for m in lottery_candidates[:5]:
            price = float(m.get("yes_price", 0.01))
            dtc   = m.get("days_to_close")
            dtc_s = f"{dtc:.1f}d" if dtc is not None else "?"
            await update.message.reply_text(
                f"🎰 *LOTTERY* — `{price:.1%}` YES | `{dtc_s}` to close | 100x payout\n"
                f"*{m['question'][:90]}*\n"
                f"_Low-vol reversal: crowd extrapolated short-term move, price may snap back_",
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True
            )
            _ks_hit, _ks_reason = check_kill_switch_sync(TRACKER.balance())
            if _ks_hit:
                break
            if not TRACKER.has_position(m["id"]):
                pos = TRACKER.open_position(
                    market_id=m["id"], question=m["question"], side="YES",
                    size=10.0, entry_price=price, true_p=0.02,
                    ev=round(0.02 / price - 1, 3),
                    kelly_frac=round(10.0 / max(TRACKER.balance(), 1), 4),
                    confidence="lottery",
                    reasoning=f"1-2c lottery tier: BTC low-vol ({btc_str}), hold to resolution",
                    score=price, url=m.get("url", "")
                )
                if pos:
                    lottery_entered += 1
                    on_position_opened(pos)
        await update.message.reply_text(
            f"✅ Lottery tier: {lottery_entered} tickets opened @ $10 each"
        )
    elif lottery_candidates and not low_vol:
        btc_str = f"{btc_5m_move:+.2f}%" if btc_5m_move is not None else "unknown"
        await update.message.reply_text(
            f"🎰 {len(lottery_candidates)} lottery candidates skipped — "
            f"BTC too volatile (`{btc_str}` in 5m, threshold < 0.5%)\n"
            f"_Lottery tier only fires on range-bound BTC days_",
            parse_mode=ParseMode.MARKDOWN
        )
    # ── End Lottery Tier ──────────────────────────────────────────────────────

    # Base validity filter (volume, days-to-close, etc.) then price gate: 3–12¢
    all_valid = [m for m in markets if is_valid_market(m)]
    contrarian = [
      m for m in all_valid
      if 0.03 <= float(m.get("yes_price", 1.0)) <= 0.12
    ]

    if not contrarian:
      await update.message.reply_text(
        "🔍 No 3-12¢ markets pass base filters right now.\n"
        "Market may be fully priced on low-probability outcomes."
      )
      return

    # Inject velocity
    for m in contrarian:
      vel = _compute_velocity(m["id"], float(m.get("yes_price", 0.05)))
      m["_velocity_pct"] = vel

    await update.message.reply_text(
      f"📊 {len(contrarian)} contrarian candidates (3-12¢) — running AI...\n"
      f"Min true prob threshold: 85% | Implied payout: 7-30x"
    )

    # Lower min_ev here — at 5¢ an 85% true prob is +1600% EV; standard filter is irrelevant
    # We use a custom filter post-estimation: only keep where AI says >= 85%
    opps_raw = batch_estimate(
      contrarian, ANTHROPIC_KEY,
      max_markets=min(len(contrarian), AI_BATCH_LIMIT),
      min_ev=0.10  # loose floor — we filter by probability below
    )

    # Hard filter: AI must say true probability >= 85%
    opps = [o for o in opps_raw if o["ai"].get("probability", 0) >= 0.85]

    if not opps:
      await update.message.reply_text(
        f"🔍 {len(opps_raw)} low-prob markets analyzed — none cleared 85% true-prob threshold.\n"
        "Contrarian plays require strong data signal, not just low price."
      )
      return

    # Sort by implied payout multiple (1/market_price * true_prob)
    for o in opps:
      price = float(o["market"].get("yes_price", 0.05))
      true_p = o["ai"].get("probability", 0)
      o["_payout_multiple"] = round((true_p / price) if price > 0 else 0, 1)
    opps.sort(key=lambda o: o["_payout_multiple"], reverse=True)

    await update.message.reply_text(
      f"🎯 *{len(opps)} CONTRARIAN SETUP{'S' if len(opps) != 1 else ''}* found",
      parse_mode=ParseMode.MARKDOWN
    )

    _dd_paused, _dd_reason = is_drawdown_pause_active()
    if _dd_paused:
      await update.message.reply_text(
        f"⚠️ {_dd_reason}\nShowing setups but NOT entering positions.",
        parse_mode=ParseMode.MARKDOWN
      )

    entered = 0
    for i, opp in enumerate(opps[:5], 1):
      m, ai, trade, score = opp["market"], opp["ai"], opp["trade"], opp["score"]
      price = float(m.get("yes_price", 0.05))
      mult  = opp["_payout_multiple"]
      ev_pct = round((ai["probability"] - price) / price * 100, 0)

      sector = get_market_sector(m.get("question", ""))
      text = (
        f"🔥 *CONTRARIAN #{i}* — `{price:.0%}` market / `{ai['probability']:.0%}` true prob\n"
        f"*Implied payout: {mult:.1f}x* | EV: +{ev_pct:.0f}%\n"
        f"Confidence: `{ai['confidence']}`\n"
        f"*{m['question'][:80]}*\n"
        f"_{ai['reasoning'][:160]}_"
      )
      await update.message.reply_text(
        text, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True
      )

      # Entry gate
      _ks_hit, _ks_reason = check_kill_switch_sync(TRACKER.balance())
      if _ks_hit:
        await update.message.reply_text(
          f"🚨 Kill switch — trading halted\n{_ks_reason}",
          parse_mode=ParseMode.MARKDOWN
        )
        break

      sector_count = count_sector_positions(sector)
      if sector_count >= MAX_SECTOR_POSITIONS:
        await update.message.reply_text(
          f"⏭️ Skipping #{i} — {sector_count} open in *{sector}* (max {MAX_SECTOR_POSITIONS})",
          parse_mode=ParseMode.MARKDOWN
        )
        continue

      if (
        not _dd_paused
        and ai.get("confidence") in MIN_CONF_AUTO
        and not TRACKER.has_position(m["id"])
      ):
        side = "YES"  # contrarian plays are always YES (buying the underpriced outcome)
        ep   = price
        size = position_size(TRACKER.balance(), trade["kelly"])

        pos = TRACKER.open_position(
          market_id=m["id"], question=m["question"], side=side,
          size=size, entry_price=ep, true_p=ai["probability"],
          ev=trade["ev"], kelly_frac=trade["kelly"],
          confidence=ai["confidence"], reasoning=ai["reasoning"],
          score=score, url=m.get("url", "")
        )

        if pos:
          entered += 1
          on_position_opened(pos)
          record_estimate(
            market_id=m["id"], question=m["question"],
            claude_p=ai["probability"], market_price=price,
            confidence=ai["confidence"], side=side,
          )
          await update.message.reply_text(
            f"📥 *Contrarian position opened #{entered}*\n"
            f"Side: YES @ `{ep:.3f}` | Size: `${size:.2f}` | Payout: `{mult:.1f}x`",
            parse_mode=ParseMode.MARKDOWN
          )
          if upgrade_alerts:
            await upgrade_alerts.trade_opened(pos, TRACKER.balance())

    s = TRACKER.pnl_summary()
    await update.message.reply_text(
      f"✅ Contrarian scan done — {len(opps)} found | {entered} entered\n"
      f"Balance: `${s['balance']:.2f}` | Open: {s['open_count']}",
      parse_mode=ParseMode.MARKDOWN
    )

  except Exception as e:
    log.exception("contrascan error")
    await update.message.reply_text(f"❌ Contrascan error: {e}")


async def cmd_positions(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
  positions = TRACKER.open_positions()
  if not positions:
    await update.message.reply_text(
      "📭 No open positions.\n\nRun /scan to find opportunities."
    )
    return

  deployed = TRACKER.total_deployed()
  lines  = [f"*Open Positions* ({len(positions)}) | Deployed: `${deployed:.2f}`\n"]
  for i, pos in enumerate(positions, 1):
    lines.append(fmt_position(pos, i))
  lines.append("\n_Use /close <n> to exit a position early_")

  await update.message.reply_text(
    "\n\n".join(lines), parse_mode=ParseMode.MARKDOWN,
    disable_web_page_preview=True
  )


async def cmd_pnl(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
  s  = TRACKER.pnl_summary()
  msg = fmt_pnl(s)

  if s.get("best_trade"):
    bt = s["best_trade"]
    msg += f"\n\n🏅 Best: `+${bt['pnl']:.2f}` ({bt['pnl_pct']:+.0f}%)\n  _{bt['question'][:60]}_"
  if s.get("worst_trade"):
    wt = s["worst_trade"]
    msg += f"\n💀 Worst: `${wt['pnl']:.2f}` ({wt['pnl_pct']:+.0f}%)\n  _{wt['question'][:60]}_"

  await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)


async def cmd_close(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
  """
  /close <n> — manually exit position #n.
  New in v2 — was completely missing before.
  """
  if not ctx.args:
    positions = TRACKER.open_positions()
    if not positions:
      await update.message.reply_text("No open positions.")
      return
    lines = ["*Close which position?*\n"]
    for i, pos in enumerate(positions, 1):
      lines.append(f"*#{i}* {pos['side']} `${pos['size']:.2f}` — {pos['question'][:50]}")
    lines.append("\nUsage: `/close 1`")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    return

  try:
    num = int(ctx.args[0])
  except ValueError:
    await update.message.reply_text("Usage: `/close 1` (use position number from /positions)", parse_mode=ParseMode.MARKDOWN)
    return

  pos = TRACKER.get_position_by_num(num)
  if not pos:
    await update.message.reply_text(f"No position #{num}. Check /positions for valid numbers.")
    return

  closed = TRACKER.force_close_by_num(num)
  if not closed:
    await update.message.reply_text(f"❌ Could not close position #{num}.")
    return

  s = TRACKER.pnl_summary()
  await update.message.reply_text(
    f"🔒 *Position #{num} closed manually*\n"
    f"Returned: `${closed['payout']:.2f}` (50% exit sim)\n"
    f"P&L: `${closed['pnl']:.2f}` ({closed['pnl_pct']:+.0f}%)\n"
    f"_{closed['question'][:70]}_\n\n"
    f"Balance: `${s['balance']:.2f}`",
    parse_mode=ParseMode.MARKDOWN
  )


async def cmd_resolve(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
  """Check all open positions for resolution. Concurrent in v2."""
  positions = TRACKER.open_positions()
  if not positions:
    await update.message.reply_text("No open positions to check.")
    return

  await update.message.reply_text(f"🔎 Checking {len(positions)} position(s) for resolution...")

  market_ids = list({p["market_id"] for p in positions})
  resolutions = GAMMA.check_resolutions(market_ids)

  closed_count = 0
  total_pnl  = 0.0

  for mid, resolution in resolutions.items():
    if not resolution:
      continue
    closed_list = TRACKER.close_position(mid, resolution)
    record_outcome(mid, resolved_yes=(resolution == "YES"))
    for closed in closed_list:
      closed_count += 1
      total_pnl  += closed["pnl"]
      icon = "✅ WON" if closed["won"] else "❌ LOST"
      pnl_s = f"+${closed['pnl']:.2f}" if closed["pnl"] >= 0 else f"-${abs(closed['pnl']):.2f}"
      await update.message.reply_text(
        f"{icon} `{pnl_s}` ({closed['pnl_pct']:+.0f}%)\n"
        f"_{closed['question'][:80]}_\n"
        f"Resolved: *{resolution}* | Side: {closed['side']}",
        parse_mode=ParseMode.MARKDOWN
      )
      on_position_closed(closed, TRACKER.balance())
      if upgrade_alerts:
        await upgrade_alerts.trade_closed(closed, TRACKER.balance())

  if closed_count == 0:
    await update.message.reply_text("No resolutions found — markets still pending.")
  else:
    s   = TRACKER.pnl_summary()
    pnl_s = f"+${total_pnl:.2f}" if total_pnl >= 0 else f"-${abs(total_pnl):.2f}"
    await update.message.reply_text(
      f"📋 Resolved {closed_count} position(s) | Session P&L: `{pnl_s}`\n"
      f"Balance: `${s['balance']:.2f}`",
      parse_mode=ParseMode.MARKDOWN
    )


async def cmd_auto(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
  status = "🟢 ENABLED" if auto_enabled else "⚫ DISABLED"
  await update.message.reply_text(
    f"*Auto-Scan Status:* {status}\n\n"
    f"Interval:  4 hours\n"
    f"Min EV:   {AUTO_MIN_EV:.0%}\n"
    f"Min conf:  medium or high\n"
    f"Max enter: {AUTO_MAX_ENTER} per scan\n\n"
    f"/autoon or /autooff to toggle",
    parse_mode=ParseMode.MARKDOWN
  )


async def cmd_autoon(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
  global auto_enabled
  if not ANTHROPIC_KEY:
    await update.message.reply_text("❌ Can't enable auto-scan without ANTHROPIC_API_KEY.")
    return
  auto_enabled = True
  chat_id = update.effective_chat.id
  _save_config({"auto_enabled": True, "auto_chat_id": chat_id})
  # PTB v20+ job_queue signature: run_repeating(callback, interval, chat_id=...)
  ctx.job_queue.run_repeating(
    _auto_scan_job,
    interval=AUTO_SCAN_INTERVAL,
    first=15,
    name="auto_scan",
    chat_id=chat_id,
  )
  ctx.job_queue.run_repeating(
    _new_market_alert_job,
    interval=NEW_MARKET_SCAN_INTERVAL,
    first=30,
    name="new_market_alerts",
    chat_id=chat_id,
  )
  await update.message.reply_text(
    "🟢 *Auto-scan enabled*\nFirst scan in 15 seconds, then every 4 hours.\nNew market alerts every 10 min.",
    parse_mode=ParseMode.MARKDOWN
  )


async def cmd_autooff(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
  global auto_enabled
  auto_enabled = False
  _save_config({"auto_enabled": False})
  for job in ctx.job_queue.get_jobs_by_name("auto_scan"):
    job.schedule_removal()
  for job in ctx.job_queue.get_jobs_by_name("new_market_alerts"):
    job.schedule_removal()
  await update.message.reply_text("⚫ Auto-scan disabled.")


async def cmd_wallet(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
  address = load_address()
  s = TRACKER.pnl_summary()
  mode = "🟢 LIVE" if (_CLOB_AVAILABLE and is_live()) else "📋 PAPER"
  live_bal = ""
  if _CLOB_AVAILABLE and is_live():
    usdc = get_usdc_balance()
    live_bal = f"\n💵 On-chain USDC: `${usdc:.2f}`"
  await update.message.reply_text(
    f"*OctoBoto Wallet*\n\n"
    f"📄 Mode:    {mode}\n"
    f"💰 Balance: `${s['balance']:.2f}` paper USDC\n"
    f"💼 Deployed: `${s['deployed']:.2f}`\n"
    f"💸 Fees:    `${s['fees_paid']:.2f}` (simulated 0.5%)\n"
    f"🔑 Address: `{address}`{live_bal}\n\n"
    f"_Use /golive to switch to real execution (requires funded wallet)._",
    parse_mode=ParseMode.MARKDOWN
  )


async def cmd_golive(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
  if not _CLOB_AVAILABLE:
    await update.message.reply_text("❌ CLOB module not available.")
    return
  bal = get_usdc_balance()
  if bal < 10.0:
    await update.message.reply_text(
      f"❌ Wallet has only ${bal:.2f} USDC on Polygon.\n"
      f"Fund with at least $10 USDC before going live."
    )
    return
  set_live_mode(True)
  await update.message.reply_text(
    f"🟢 *LIVE MODE ENABLED*\n\n"
    f"💵 On-chain balance: `${bal:.2f}` USDC\n"
    f"Orders will now be submitted to Polymarket CLOB.\n\n"
    f"⚠️ Real money at risk. Use /gopaper to revert.",
    parse_mode=ParseMode.MARKDOWN
  )


async def cmd_gopaper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
  if _CLOB_AVAILABLE:
    set_live_mode(False)
  await update.message.reply_text("📋 *PAPER MODE* — No real orders will be placed.", parse_mode=ParseMode.MARKDOWN)


async def cmd_clob(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
  if not _CLOB_AVAILABLE:
    await update.message.reply_text("❌ CLOB module not available.")
    return
  status = clob_status_str()
  orders = get_open_orders()
  orders_str = f"\n\n📋 Open orders: {len(orders)}" if orders else "\n\n📋 No open orders"
  bankr_str = f"\n{bankr_status_str()}" if _BANKR_AVAILABLE else ""
  mm_str = f"\n{mm_status_str()}" if _MM_AVAILABLE and active_mm_count() else ""
  await update.message.reply_text(
    f"*OctoBoto CLOB*\n\n{status}{bankr_str}{orders_str}{mm_str}",
    parse_mode=ParseMode.MARKDOWN
  )


async def cmd_cancelorder(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
  """Usage: /cancelorder <order_id>"""
  if not _CLOB_AVAILABLE:
    await update.message.reply_text("❌ CLOB not available.")
    return
  args = ctx.args
  if not args:
    await update.message.reply_text("Usage: `/cancelorder <order_id>`", parse_mode=ParseMode.MARKDOWN)
    return
  order_id = args[0]
  ok = cancel_order(order_id)
  await update.message.reply_text(
    f"✅ Order `{order_id}` cancelled." if ok else f"❌ Failed to cancel `{order_id}`.",
    parse_mode=ParseMode.MARKDOWN
  )


async def cmd_cancelall(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
  if not _CLOB_AVAILABLE:
    await update.message.reply_text("❌ CLOB not available.")
    return
  ok = cancel_all()
  mm_cancelled = cancel_all_mm() if _MM_AVAILABLE else 0
  await update.message.reply_text(
    f"✅ All CLOB orders cancelled.\n📊 MM pairs cancelled: {mm_cancelled}"
  )


async def cmd_mm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
  """Market maker status and controls. Usage: /mm [on|off]"""
  if not _MM_AVAILABLE:
    await update.message.reply_text("❌ MM module not available.")
    return
  args = ctx.args
  if args and args[0] == "off":
    n = cancel_all_mm()
    await update.message.reply_text(f"📊 Market maker off — {n} pair(s) cancelled.")
    return
  if args and args[0] == "on":
    if not (_CLOB_AVAILABLE and is_live()):
      await update.message.reply_text("❌ Must be in LIVE mode to run market maker. Use /golive first.")
      return
    # Scan top liquid markets and place MM pairs
    try:
      markets = GAMMA.get_markets(limit=50)
      open_ids = {p["market_id"] for p in TRACKER.open_positions()}
      placed = 0
      for m in markets:
        if is_mm_eligible(m, open_ids):
          result = place_mm_pair(m)
          if result:
            placed += 1
          if active_mm_count() >= 3:
            break
      await update.message.reply_text(f"📊 Market maker: {placed} pair(s) placed.\n\n{mm_status_str()}", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
      await update.message.reply_text(f"❌ MM error: {e}")
    return
  await update.message.reply_text(mm_status_str() or "📊 No active MM pairs. Use /mm on to start.", parse_mode=ParseMode.MARKDOWN)


async def cmd_resync(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
  """Reload secrets from .octo_secrets cache without reboot."""
  await update.message.reply_text("🔄 Resyncing secrets from cache...")
  try:
    import json
    from pathlib import Path
    cache = Path(r"C:\Users\walli\octodamus\.octo_secrets")
    if not cache.exists():
      await update.message.reply_text("❌ Cache file not found.")
      return
    raw = json.loads(cache.read_text(encoding="utf-8"))
    secrets = raw.get("secrets", raw)
    loaded = 0
    for k, v in secrets.items():
      if v:
        os.environ[k] = v
        loaded += 1
    global ANTHROPIC_KEY, OPENAI_KEY
    ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", ANTHROPIC_KEY)
    OPENAI_KEY    = os.environ.get("OPENAI_API_KEY", OPENAI_KEY)
    saved_at = raw.get("saved_at", "unknown")
    await update.message.reply_text(
      f"✅ Resynced {loaded} secrets from cache\n_Saved: {saved_at}_",
      parse_mode=ParseMode.MARKDOWN
    )
  except Exception as e:
    await update.message.reply_text(f"❌ Resync failed: {e}")


async def cmd_reputation(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
  """Show onchain reputation stats."""
  if not _REP_AVAILABLE:
    await update.message.reply_text("❌ Reputation module not available.")
    return
  await update.message.reply_text(reputation_str(), parse_mode=ParseMode.MARKDOWN)


async def cmd_newwallet(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
  await update.message.reply_text(
    "⚠️ Generating new Polygon wallet...\n"
    "_Private key shown on server console ONLY — never in Telegram._",
    parse_mode=ParseMode.MARKDOWN
  )
  wallet = generate_wallet()
  if "note" in wallet:
    await update.message.reply_text(
      f"⚠️ {wallet['note']}\n\nRun:\n`pip install eth-account web3`",
      parse_mode=ParseMode.MARKDOWN
    )
    return

  log.info("=" * 60)
  log.info(f"NEW WALLET — Address: {wallet['address']}")
  log.info(f"NEW WALLET — Private key: {wallet['private_key']}")
  log.info("Store private key in Bitwarden: AGENT - Octodamus - OctoBoto - Wallet Key")
  log.info("=" * 60)

  save_address(wallet["address"])

  await update.message.reply_text(
    f"✅ New wallet generated\n"
    f"Address: `{wallet['address']}`\n\n"
    f"⚠️ Private key logged to server console.\n"
    f"Save it NOW to Bitwarden: `AGENT - Octodamus - OctoBoto - Wallet Key`",
    parse_mode=ParseMode.MARKDOWN
  )


async def cmd_reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
  TRACKER.reset(new_balance=500.0)
  clear_cache()
  await update.message.reply_text(
    "🔄 Paper ledger reset.\nBalance: `$500.00` | Cache cleared.",
    parse_mode=ParseMode.MARKDOWN
  )


# ─── Kill Switch Commands ─────────────────────────────────────────────────────

async def cmd_ksstatus(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
  """Show kill switch status."""
  from octo_boto_upgrades import kill_switch as ks
  if ks is None:
    await update.message.reply_text("Kill switch not initialised.")
    return
  s = ks.status()
  status = "🚨 HALTED" if s["halted"] else "✅ Active"
  await update.message.reply_text(
    f"*Kill Switch Status:* {status}\n\n"
    f"Peak balance: `${s['peak_balance']:.2f}`\n"
    f"Day start: `${s['day_start_balance']:.2f}`\n"
    f"Daily loss limit: `{s['daily_loss_limit']}`\n"
    f"Drawdown kill: `{s['drawdown_kill']}`\n"
    + (f"\n⚠️ Halt reason: {s['halt_reason']}" if s["halted"] else ""),
    parse_mode=ParseMode.MARKDOWN
  )


async def cmd_ksreset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
  """Reset kill switch after manual review."""
  from octo_boto_upgrades import kill_switch as ks
  if ks is None:
    await update.message.reply_text("Kill switch not initialised.")
    return
  ks.reset(TRACKER.balance())
  await update.message.reply_text(
    f"✅ Kill switch reset\nNew baseline: `${TRACKER.balance():.2f}`",
    parse_mode=ParseMode.MARKDOWN
  )


async def cmd_binance(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
  """Show live Binance prices and momentum."""
  from octo_boto_upgrades import price_feed as pf
  if pf is None or not pf.is_live():
    await update.message.reply_text("⚫ Binance feed not connected.")
    return
  lines = ["*Live Binance Prices*\n"]
  for sym in ["BTC", "ETH"]:
    sig = pf.get_momentum_signal(sym)
    if sig.get("price"):
      move30 = sig.get("move_30s", 0)
      move60 = sig.get("move_60s", 0)
      direction = sig.get("direction", "FLAT")
      strength = sig.get("strength", "WEAK")
      lag = " ⚡ *LAG OPPORTUNITY*" if sig.get("lag_opportunity") else ""
      icon = "📈" if direction == "UP" else "📉" if direction == "DOWN" else "➡️"
      lines.append(
        f"{icon} *{sym}*: `${sig['price']:,.0f}`{lag}\n"
        f"  30s: `{move30:+.3f}%` | 60s: `{move60:+.3f}%`\n"
        f"  Direction: {direction} | Strength: {strength}"
      )
  await update.message.reply_text(
    "\n\n".join(lines), parse_mode=ParseMode.MARKDOWN
  )


# ─── Auto-Scan Job ────────────────────────────────────────────────────────────

async def _auto_scan_job(ctx: ContextTypes.DEFAULT_TYPE):
  if not auto_enabled:
    return

  chat_id = ctx.job.chat_id

  # Weekly trade cap
  from datetime import datetime, timezone, timedelta
  _now = datetime.now(timezone.utc)
  week_start = _now - timedelta(days=_now.weekday())
  week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
  trades_this_week = sum(
    1 for t in TRACKER._data.get("closed", TRACKER._data.get("closed_trades", []))
    if t.get("closed_at", "") >= week_start.isoformat()
  ) + len(TRACKER.open_positions())
  if trades_this_week >= MAX_TRADES_PER_WEEK:
    log.info(f"[AutoScan] Weekly cap reached ({trades_this_week}/{MAX_TRADES_PER_WEEK}) — skipping entry")
    return

  log.info("[AutoScan] Starting scheduled scan...")

  # Live USDC balance sync (#5) — keep TRACKER balance aligned with real wallet
  if _CLOB_AVAILABLE and is_live():
    live_bal = get_usdc_balance()
    if live_bal > 0:
      TRACKER.sync_balance(live_bal)
      log.info(f"[AutoScan] Live balance synced: ${live_bal:.2f}")

  # Exit logic check (#3) — trailing stop + time-based decay
  if _EXIT_AVAILABLE:
    open_pos = TRACKER.open_positions()
    if open_pos:
      current_prices = {}
      for p in open_pos:
        try:
          mkt = GAMMA.get_market(p["market_id"])
          if mkt:
            current_prices[p["market_id"]] = float(mkt.get("yes_price", mkt.get("outcomePrices", [0.5])[0]))
        except Exception:
          pass
      exits = check_exit_signals(open_pos, current_prices)
      for ex in exits:
        mid = ex["market_id"]
        current_yes = current_prices.get(mid, 0.5)
        side = next((p.get("side") for p in open_pos if p["market_id"] == mid), "YES")
        resolution = "YES" if (side == "YES" and current_yes > 0.5) else "NO"
        closed_list = TRACKER.close_position(mid, resolution)
        record_outcome(mid, resolved_yes=(resolution == "YES"))
        for closed in closed_list:
          clear_trail(mid)
          await ctx.bot.send_message(
            chat_id,
            f"🚪 *Exit triggered*: {ex['reason']}\n"
            f"_{ex['question'][:70]}_\n"
            f"PnL: `{ex['pnl_pct']:+.1%}`",
            parse_mode=ParseMode.MARKDOWN
          )


  try:
    markets = GAMMA.get_markets(limit=SCAN_LIMIT)
    crypto_markets = GAMMA.get_crypto_markets()
    seen_ids = {m["id"] for m in markets}
    markets = markets + [m for m in crypto_markets if m["id"] not in seen_ids]
    valid  = [m for m in markets if is_valid_market(m)]
    # Inject velocity into auto-scan markets
    for m in valid:
        vel = _compute_velocity(m["id"], float(m.get("yes_price", 0.5)))
        m["_velocity_pct"] = vel
    valid  = enrich_markets_with_orderbook(valid, max_markets=AI_BATCH_LIMIT * 2)
    opps  = batch_estimate(valid, ANTHROPIC_KEY, max_markets=AI_BATCH_LIMIT, min_ev=TRIPLE_LOCK_MIN_EV)

    # Reflection critic pass — filter weak signals before entry (same as manual scan)
    opps = apply_reflection_to_batch(opps, ANTHROPIC_KEY, max_reflect=3)

    # Kill switch — hard halt if triggered
    _ks_hit, _ks_reason = check_kill_switch_sync(TRACKER.balance())
    if _ks_hit:
      log.warning(f"[AutoScan] Kill switch active — {_ks_reason} — skipping all entries")
      return

    # Session guard — daily loss or losing streak
    _sg = TRACKER.session_guard(max_daily_loss=30.0, max_consecutive_losses=3)
    if _sg["blocked"]:
      log.warning(f"[AutoScan] Session guard — {_sg['reason']} — skipping all entries")
      await ctx.bot.send_message(
        chat_id,
        f"🛑 *Auto-scan session guard* — {_sg['reason']}\nNo entries until tomorrow.",
        parse_mode=ParseMode.MARKDOWN
      )
      return

    # Soft drawdown pause — skip entries if -15% from peak
    _dd_paused, _dd_reason = is_drawdown_pause_active()
    if _dd_paused:
      log.warning(f"[AutoScan] {_dd_reason}")

    entered = 0
    for opp in opps[:AUTO_MAX_ENTER]:
      m, ai, trade, score = opp["market"], opp["ai"], opp["trade"], opp["score"]

      # ── Triple-lock gate (auto-scan) ────────────────────────────────────
      _tl_skip = ""
      if _dd_paused:
        _tl_skip = "drawdown pause"
      elif ai.get("confidence") not in MIN_CONF_AUTO:
        _tl_skip = f"confidence {ai.get('confidence')}"
      elif trade["ev"] < TRIPLE_LOCK_MIN_EV:
        _tl_skip = f"EV {trade['ev']:+.1%} < {TRIPLE_LOCK_MIN_EV:.0%}"
      elif TRACKER.has_position(m["id"]):
        _tl_skip = "duplicate"
      else:
        dtc = m.get("days_to_close")
        if dtc is not None and dtc > TRIPLE_LOCK_MAX_DAYS:
          _tl_skip = f"resolves in {dtc}d"
        else:
          # Kalshi contradiction check — hard block if Kalshi disagrees by ≥10%
          _kc = kalshi_confirms_edge(m.get("question", ""), trade["side"], m["yes_price"], min_gap=0.06)
          if _kc.get("contradicts") and _kc.get("gap", 0) >= 0.10:
            _tl_skip = f"Kalshi contradicts — {_kc['note']}"
          else:
            _sc = _octo_signal_conflicts(
              m.get("question", ""), trade["side"],
              ai.get("octo_direction", "NEUTRAL"),
            )
            if _sc:
              _tl_skip = _sc

      if _tl_skip:
        log.info(f"[AutoScan] Skipping — {_tl_skip} | {m.get('question', '')[:60]}")
        continue

      # Sector correlation gate
      sector = get_market_sector(m.get("question", ""))
      if count_sector_positions(sector) >= MAX_SECTOR_POSITIONS:
        log.info(f"[AutoScan] Skipping — sector '{sector}' at limit")
        continue

      side = trade["side"]
      ep  = m["yes_price"] if side == "YES" else (1.0 - m["yes_price"])
      size = position_size(TRACKER.balance(), trade["kelly"])

      pos = TRACKER.open_position(
        market_id=m["id"], question=m["question"], side=side,
        size=size, entry_price=ep, true_p=ai["probability"],
        ev=trade["ev"], kelly_frac=trade["kelly"],
        confidence=ai["confidence"], reasoning=ai["reasoning"],
        score=score, url=m.get("url", "")
      )

      if pos:
        entered += 1
        on_position_opened(pos)
        record_estimate(
          market_id=m["id"], question=m["question"],
          claude_p=ai["probability"], market_price=m["yes_price"],
          confidence=ai["confidence"], side=side,
        )
        # CLOB execution — place real order if live
        clob_result = None
        if _CLOB_AVAILABLE and is_live():
          tokens = get_token_ids(m.get("conditionId", m["id"]))
          token_id = tokens["yes"] if side == "YES" else tokens["no"]
          if token_id:
            from octo_boto_clob import check_orderbook_liquidity
            liq = check_orderbook_liquidity(token_id, ep, min_usdc=size)
            if not liq["sufficient"]:
              log.warning(
                f"[AutoScan] Thin orderbook for {m['question'][:50]} — "
                f"only ${liq['available_usdc']:.2f} at {ep:.3f}, need ${size:.2f}. Skipping."
              )
              clob_result = {
                "order_id": "SKIPPED-thin-book",
                "status": "skipped",
                "live": False,
              }
            else:
              clob_result = place_order(
                token_id=token_id, side="BUY",
                price=ep, amount_usdc=size,
                market_question=m["question"],
              )
        mode_label = "🟢 LIVE" if (clob_result and clob_result.get("live")) else "📋 Paper"
        order_note = f"\nOrder: `{clob_result['order_id']}`" if clob_result and clob_result.get("order_id") else ""
        await ctx.bot.send_message(
          chat_id,
          f"🤖 *Auto-trade — {mode_label}*\n"
          f"Side: *{side}* | `${size:.2f}` | EV: `{trade['ev']:+.1%}` | Score: `{score:.3f}`\n"
          f"Sector: *{sector}*{order_note}\n_{m['question'][:80]}_",
          parse_mode=ParseMode.MARKDOWN
        )

    s = TRACKER.pnl_summary()
    log.info(f"[AutoScan] {len(opps)} found | {entered} entered | balance ${s['balance']:.2f}")

    if opps or entered:
      await ctx.bot.send_message(
        chat_id,
        f"🔄 *Auto-scan done* — {len(opps)} edge(s) | {entered} entered\n"
        f"Balance: `${s['balance']:.2f}` | Open: {s['open_count']}",
        parse_mode=ParseMode.MARKDOWN
      )

  except Exception as e:
    log.exception("[AutoScan] error")
    await ctx.bot.send_message(chat_id, f"⚠️ Auto-scan error: {e}")


# ─── New Market Alert Job (#9) ────────────────────────────────────────────────

# Track markets we've already alerted on to avoid spam
_alerted_new_markets: set = set()
NEW_MARKET_MAX_AGE_HOURS = 2.0   # Only alert on markets <2h old
NEW_MARKET_SCAN_INTERVAL = 10 * 60  # Check every 10 minutes


async def _new_market_alert_job(ctx: ContextTypes.DEFAULT_TYPE):
    """
    Poll for freshly created markets and alert on any that pass filters.
    New markets are often mispriced for the first few hours — best entry windows.
    """
    if not auto_enabled or not ANTHROPIC_KEY:
        return

    chat_id = ctx.job.chat_id
    from datetime import datetime, timezone

    try:
        markets = GAMMA.get_markets(limit=50)
        now = datetime.now(timezone.utc)
        fresh = []

        for m in markets:
            if m["id"] in _alerted_new_markets:
                continue
            created_at = m.get("created_at") or m.get("createdAt")
            if not created_at:
                continue
            try:
                ct = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                age_hours = (now - ct).total_seconds() / 3600
            except Exception:
                continue
            if age_hours > NEW_MARKET_MAX_AGE_HOURS:
                continue
            if not is_valid_market(m):
                continue
            fresh.append((m, age_hours))

        if not fresh:
            return

        log.info(f"[NewMarket] {len(fresh)} fresh market(s) found — estimating...")

        for m, age_h in fresh[:3]:   # cap at 3 per run
            _alerted_new_markets.add(m["id"])
            try:
                from octo_boto_ai import estimate
                from octo_boto_math import best_trade
                price = m["yes_price"]
                ai = estimate(
                    market_id=m["id"], question=m["question"],
                    description=m.get("description", ""), market_price=price,
                    api_key=ANTHROPIC_KEY, end_date=m.get("end_date", ""),
                    use_search=True, min_ev=MIN_EV_THRESHOLD,
                )
                trade = best_trade(price, ai["probability"])
                if trade["side"] == "NONE":
                    continue

                await ctx.bot.send_message(
                    chat_id,
                    f"NEW MARKET ({age_h:.1f}h old) — potential mispricing window\n"
                    f"EV: `{trade['ev']:+.1%}` | Side: *{trade['side']}* | Conf: {ai['confidence']}\n"
                    f"_{m['question'][:100]}_\n"
                    f"Run /scan to enter if it checks out.",
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception as e:
                log.warning(f"[NewMarket] estimate failed for {m['id']}: {e}")

    except Exception:
        log.exception("[NewMarket] job error")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
  log.info("OctoBoto v2 starting...")
  log.info(f"Paper balance: ${TRACKER.balance():.2f}")

  app = Application.builder().token(BOT_TOKEN).build()

  handlers = [
    ("start",   cmd_start),
    ("help",   cmd_help),
    ("scan",   cmd_scan),
    ("top",    cmd_top),
    ("positions", cmd_positions),
    ("pnl",    cmd_pnl),
    ("close",   cmd_close),
    ("resolve",  cmd_resolve),
    ("auto",   cmd_auto),
    ("autoon",  cmd_autoon),
    ("autooff",  cmd_autooff),
    ("wallet",  cmd_wallet),
    ("newwallet", cmd_newwallet),
    ("golive",     cmd_golive),
    ("gopaper",    cmd_gopaper),
    ("clob",       cmd_clob),
    ("cancelorder", cmd_cancelorder),
    ("cancelall",  cmd_cancelall),
    ("mm",         cmd_mm),
    ("resync",     cmd_resync),
    ("reputation", cmd_reputation),
    ("reset",      cmd_reset),
    ("ksstatus", cmd_ksstatus),
    ("ksreset",  cmd_ksreset),
    ("binance",  cmd_binance),
    ("stats",       cmd_stats),
    ("quickscan",   cmd_quickscan),
    ("contrascan",  cmd_contrascan),
  ]

  for cmd, handler in handlers:
    app.add_handler(CommandHandler(cmd, handler))

  # Initialise upgrade components (Binance feed, kill switch, alerts)
  # Chat ID will be set on first message — use placeholder 0 for now
  # Alerts will only send after first /start command sets chat_id
  log.info("[Upgrades] Starting Binance price feed...")
  import asyncio as _aio
  from octo_boto_upgrades import BinancePriceFeed, KillSwitch
  import octo_boto_upgrades as _upg
  _upg.price_feed = BinancePriceFeed()
  _upg.price_feed.start()
  _upg.kill_switch = KillSwitch(starting_balance=TRACKER.balance())
  log.info("[Upgrades] Ready")

  # Resume auto-scan if it was enabled before last shutdown
  cfg = _load_config()
  if cfg.get("auto_enabled") and cfg.get("auto_chat_id") and ANTHROPIC_KEY:
    saved_chat_id = cfg["auto_chat_id"]
    app.job_queue.run_repeating(
      _auto_scan_job,
      interval=AUTO_SCAN_INTERVAL,
      first=60,  # 1 min delay on boot to let secrets load
      name="auto_scan",
      chat_id=saved_chat_id,
    )
    # New market alert job — runs every 10 min regardless of auto-scan interval
    app.job_queue.run_repeating(
      _new_market_alert_job,
      interval=NEW_MARKET_SCAN_INTERVAL,
      first=120,  # 2 min delay on boot
      name="new_market_alerts",
      chat_id=saved_chat_id,
    )
    global auto_enabled
    auto_enabled = True
    log.info(f"[AutoScan] Resumed from config — chat_id {saved_chat_id}")

  log.info("Polling...")
  app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
  main()

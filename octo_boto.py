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
 /autoon   — enable 30-min auto-scan
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
from octo_boto_polymarket import GammaClient
from octo_boto_ai     import batch_estimate, clear_cache
from octo_boto_wallet   import load_address, generate_wallet, save_address
from octo_boto_tracker  import PaperTracker, age_str, STARTING_BALANCE
from octo_boto_upgrades import (
  init_upgrades, check_kill_switch_sync, get_binance_context,
  apply_reflection_to_batch, price_feed, kill_switch, alerts as upgrade_alerts,
)


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
AUTO_SCAN_INTERVAL = 4 * 60 * 60  # 30 minutes between auto scans
SCAN_LIMIT     = 150    # Markets to fetch per scan (paginated)
AI_BATCH_LIMIT   = 15     # Max markets sent to AI per scan
AUTO_MIN_EV    = 0.12    # Stricter 7% threshold in auto mode
AUTO_MAX_ENTER   = 1
MAX_TRADES_PER_WEEK = 2     # Hard cap — quality over quantity
     # Max new positions per auto-scan
SCAN_COOLDOWN   = 3 * 60   # Prevent /scan spam — 3 min cooldown
MIN_CONF_AUTO   = {"high"}  # confidence levels that trigger auto-entry

GAMMA  = GammaClient(min_liquidity=3_000)
TRACKER = PaperTracker()

auto_enabled: bool = False
_last_scan_time: float = 0.0


# ─── Secrets ──────────────────────────────────────────────────────────────────

def load_secrets() -> dict:
  import json
  secrets = {}
  cache = Path(r"C:\Users\walli\octodamus\.octo_secrets")
  if cache.exists():
    try:
      raw = json.loads(cache.read_text(encoding="utf-8"))
      data = raw.get("secrets", raw)
      for key in ("OCTOBOTO_TELEGRAM_TOKEN", "ANTHROPIC_API_KEY"):
        if data.get(key):
          secrets[key] = data[key]
          log.info(f"[Secrets] {key} loaded from cache")
        else:
          log.warning(f"[Secrets] {key} not found in cache")
    except Exception as e:
      log.warning(f"[Secrets] Cache read failed: {e}")
  for key in ("OCTOBOTO_TELEGRAM_TOKEN", "ANTHROPIC_API_KEY"):
    if key not in secrets and os.getenv(key):
      secrets[key] = os.getenv(key)
  return secrets


SECRETS    = load_secrets()
BOT_TOKEN   = SECRETS.get("OCTOBOTO_TELEGRAM_TOKEN", "")
ANTHROPIC_KEY = SECRETS.get("ANTHROPIC_API_KEY", "")

if not BOT_TOKEN:
  log.error("OCTOBOTO_TELEGRAM_TOKEN missing. Add to Bitwarden or set env var.")
  sys.exit(1)

if not ANTHROPIC_KEY:
  log.warning("ANTHROPIC_API_KEY missing — /scan will be unavailable.")


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
    "`/autoon`    Enable 30-min auto-scan\n"
    "`/autooff`   Disable auto-scan\n"
    "`/auto`     Auto-scan status\n"
    "`/wallet`    Wallet address + balance\n"
    "`/newwallet`  Generate new Polygon wallet\n"
    "`/reset`    Reset to $500 paper balance\n",
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

    valid = [m for m in markets if is_valid_market(m)]
    await update.message.reply_text(
      f"📊 {len(markets)} fetched → {len(valid)} pass filters\n"
      f"Running AI on up to {AI_BATCH_LIMIT}..."
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

    for i, opp in enumerate(opps[:5], 1):
      m, ai, trade, score = opp["market"], opp["ai"], opp["trade"], opp["score"]

      await update.message.reply_text(
        fmt_opportunity(m, trade, ai, i, score),
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True
      )

      # Enter position if confidence and EV good, and not already in market
      # Kill switch check before entry
      _ks_hit, _ks_reason = check_kill_switch_sync(TRACKER.balance())
      if _ks_hit:
        await update.message.reply_text(
          f"🚨 Kill switch active — trading halted\n{_ks_reason}\nUse /ksreset to resume.",
          parse_mode=ParseMode.MARKDOWN
        )
        break

      if (
        ai.get("confidence") in MIN_CONF_AUTO
        and trade["ev"] >= MIN_EV_THRESHOLD
        and not TRACKER.has_position(m["id"])
      ):
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
          await update.message.reply_text(
            f"📥 *Paper position opened #{entered}*\n"
            f"Side: *{side}* | Size: `${size:.2f}` | @ `{ep:.3f}`\n"
            f"Kelly: `{trade['kelly']:.1%}` | EV: `{trade['ev']:+.1%}` | Score: `{score:.3f}`",
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
    f"Interval:  30 minutes\n"
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
  # PTB v20+ job_queue signature: run_repeating(callback, interval, chat_id=...)
  ctx.job_queue.run_repeating(
    _auto_scan_job,
    interval=AUTO_SCAN_INTERVAL,
    first=15,
    name="auto_scan",
    chat_id=update.effective_chat.id,
  )
  await update.message.reply_text(
    "🟢 *Auto-scan enabled*\nFirst scan in 15 seconds, then every 30 minutes.",
    parse_mode=ParseMode.MARKDOWN
  )


async def cmd_autooff(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
  global auto_enabled
  auto_enabled = False
  for job in ctx.job_queue.get_jobs_by_name("auto_scan"):
    job.schedule_removal()
  await update.message.reply_text("⚫ Auto-scan disabled.")


async def cmd_wallet(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
  address = load_address()
  s    = TRACKER.pnl_summary()
  await update.message.reply_text(
    f"*OctoBoto Wallet*\n\n"
    f"📄 Mode:   Paper Trading\n"
    f"💰 Balance: `${s['balance']:.2f}` paper USDC\n"
    f"💼 Deployed: `${s['deployed']:.2f}`\n"
    f"💸 Fees:   `${s['fees_paid']:.2f}` (simulated 0.5%)\n"
    f"🔑 Address: `{address}`\n\n"
    f"_Fund with USDC on Polygon when you're ready to go live._",
    parse_mode=ParseMode.MARKDOWN
  )


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
  week_start = datetime.now(timezone.utc) - timedelta(days=datetime.now(timezone.utc).weekday())
  week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
  trades_this_week = sum(
    1 for t in TRACKER._data.get("closed", TRACKER._data.get("closed_trades", []))
    if t.get("closed_at", "") >= week_start.isoformat()
  ) + len(TRACKER.open_positions())
  if trades_this_week >= MAX_TRADES_PER_WEEK:
    log.info(f"[AutoScan] Weekly cap reached ({trades_this_week}/{MAX_TRADES_PER_WEEK}) — skipping entry")
    return

  log.info("[AutoScan] Starting scheduled scan...")

  try:
    markets = GAMMA.get_markets(limit=SCAN_LIMIT)
    valid  = [m for m in markets if is_valid_market(m)]
    opps  = batch_estimate(valid, ANTHROPIC_KEY, max_markets=AI_BATCH_LIMIT, min_ev=AUTO_MIN_EV)

    entered = 0
    for opp in opps[:AUTO_MAX_ENTER]:
      m, ai, trade, score = opp["market"], opp["ai"], opp["trade"], opp["score"]

      if ai.get("confidence") not in MIN_CONF_AUTO:
        continue
      if trade["ev"] < AUTO_MIN_EV:
        continue
      if TRACKER.has_position(m["id"]):
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
        await ctx.bot.send_message(
          chat_id,
          f"🤖 *Auto-trade — 📄 Paper*\n"
          f"Side: *{side}* | `${size:.2f}` | EV: `{trade['ev']:+.1%}` | Score: `{score:.3f}`\n"
          f"_{m['question'][:80]}_",
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
    ("reset",   cmd_reset),
    ("ksstatus", cmd_ksstatus),
    ("ksreset",  cmd_ksreset),
    ("binance",  cmd_binance),
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

  log.info("Polling...")
  app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
  main()
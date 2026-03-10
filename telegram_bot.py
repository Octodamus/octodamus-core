"""
Octodamus Telegram Bot v4.1
- /dashboard: full mission control panel
- /post: triggers wisdom post with --force (bypasses posting hours)
- Credentials via Bitwarden (same pattern as all modules)
- Opus for 1:1 per architecture audit
"""

import os
import sys
import json
import logging
import httpx
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# ── Bootstrap: Bitwarden first ───────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(BASE_DIR))

_bw_loaded = False
try:
    import bitwarden
    bitwarden.load_all_secrets()
    _bw_loaded = True
except Exception as _bw_err:
    try:
        from dotenv import load_dotenv
        load_dotenv(BASE_DIR / ".env")
    except ImportError:
        pass

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ── Config ────────────────────────────────────────────────────────────────────
BOT_TOKEN     = os.getenv("TELEGRAM_BOT_TOKEN")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY")
CLAUDE_MODEL  = "claude-opus-4-6"   # Opus for 1:1 per architecture audit
MEMORY_FILE   = BASE_DIR / "octodamus_memory.json"
MAX_HISTORY   = 20
TZ            = ZoneInfo("America/Los_Angeles")

TREASURY_WALLET  = "0x5c6B3a3dAe296d3cef50fef96afC73410959a6Db"
WATCHLIST_STOCKS = ["NVDA", "TSLA", "AAPL"]
WATCHLIST_CRYPTO = ["BTC-USD"]
FOLLOWER_TARGET  = 500

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("octodamus.telegram")
log.info(f"Memory: {MEMORY_FILE} | Bitwarden: {_bw_loaded}")


# ── Persistent Memory ─────────────────────────────────────────────────────────

def load_memory() -> dict:
    try:
        if MEMORY_FILE.exists():
            return json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        log.error(f"Memory load failed: {e}")
    return {}

def save_memory(memory: dict):
    try:
        MEMORY_FILE.write_text(json.dumps(memory, indent=2), encoding="utf-8")
    except Exception as e:
        log.error(f"Memory save failed: {e}")

def get_user_history(user_id: int) -> list:
    return load_memory().get(str(user_id), {}).get("history", [])

def append_user_history(user_id: int, user_msg: str, bot_reply: str):
    memory = load_memory()
    key = str(user_id)
    if key not in memory:
        memory[key] = {"history": [], "first_seen": datetime.now().isoformat()}
    memory[key]["history"].append({"role": "user",      "content": user_msg})
    memory[key]["history"].append({"role": "assistant", "content": bot_reply})
    memory[key]["history"] = memory[key]["history"][-MAX_HISTORY:]
    memory[key]["last_seen"] = datetime.now().isoformat()
    save_memory(memory)

def clear_user_history(user_id: int):
    memory = load_memory()
    if str(user_id) in memory:
        memory[str(user_id)]["history"] = []
        save_memory(memory)


# ── Data Helpers ──────────────────────────────────────────────────────────────

def get_treasury_context() -> str:
    try:
        from octo_treasury_balance import get_treasury_summary
        return get_treasury_summary()
    except Exception as e:
        return f"Treasury: {TREASURY_WALLET[:10]}...{TREASURY_WALLET[-4:]} on Base (RPC loading)"

def is_posting_window() -> bool:
    now = datetime.now(TZ)
    h = now.hour
    return (7 <= h <= 21) if now.weekday() < 5 else (9 <= h <= 18)

def get_posting_status() -> str:
    now = datetime.now(TZ)
    is_weekday = now.weekday() < 5
    window = "7am-9pm" if is_weekday else "9am-6pm"
    day_type = "weekday" if is_weekday else "weekend"
    status = "OPEN" if is_posting_window() else "CLOSED"
    return f"{status} ({window} PT {day_type})"

def get_recent_posts(n: int = 5) -> list:
    log_file = BASE_DIR / "logs" / "post_log.json"
    if not log_file.exists():
        return []
    try:
        data = json.loads(log_file.read_text(encoding="utf-8"))
        return list(data.values())[-n:] if data else []
    except Exception:
        return []

def get_queue_depth() -> int:
    queue_file = BASE_DIR / "logs" / "post_queue.json"
    if not queue_file.exists():
        return 0
    try:
        data = json.loads(queue_file.read_text(encoding="utf-8"))
        return len(data) if isinstance(data, list) else 0
    except Exception:
        return 0

def count_posts_today() -> int:
    today = datetime.now(TZ).date()
    count = 0
    for p in get_recent_posts(50):
        ts = p.get("posted_at", "")
        if ts:
            try:
                if datetime.fromisoformat(ts).date() == today:
                    count += 1
            except Exception:
                pass
    return count

def get_log_errors(n: int = 2) -> list:
    errors = []
    log_dir = BASE_DIR / "logs"
    if not log_dir.exists():
        return errors
    for lf in sorted(log_dir.glob("*.log"), key=lambda f: f.stat().st_mtime, reverse=True)[:3]:
        try:
            lines = lf.read_text(encoding="utf-8", errors="ignore").splitlines()
            for line in reversed(lines):
                if "ERROR" in line or "Traceback" in line:
                    errors.append(f"{lf.name}: {line[-100:]}")
                if len(errors) >= n:
                    return errors
        except Exception:
            pass
    return errors

def get_scheduler_status() -> str:
    try:
        result = subprocess.run(
            ["schtasks", "/query", "/fo", "LIST"],
            capture_output=True, text=True, timeout=5
        )
        output_lower = result.stdout.lower()
        if "octodamus" in output_lower or "octo" in output_lower:
            return "RUNNING (tasks registered)"
        return "WARNING (no Octodamus tasks found)"
    except Exception:
        return "UNKNOWN"


# ── Dashboard ─────────────────────────────────────────────────────────────────

def build_dashboard() -> str:
    now_str   = datetime.now(TZ).strftime("%a %d %b %Y %H:%M PT")
    posts_today = count_posts_today()
    queue_depth = get_queue_depth()
    recent      = get_recent_posts(3)
    errors      = get_log_errors(2)
    sched       = get_scheduler_status()

    # Recent posts
    if recent:
        post_lines = []
        for p in reversed(recent):
            ts   = p.get("posted_at", p.get("created_at", "?"))[:16]
            kind = p.get("type", "post")
            text = p.get("text", "")[:55]
            post_lines.append(f"  {ts} [{kind}]\n  {text}...")
        posts_block = "\n".join(post_lines)
    else:
        posts_block = "  No posts logged yet"

    # Market
    market_block = f"  {', '.join(WATCHLIST_STOCKS)} | {', '.join(WATCHLIST_CRYPTO)}"
    try:
        from octo_spot_prices import get_watchlist_block
        market_block = get_watchlist_block()
    except Exception:
        try:
            from octo_eyes_market import get_watchlist_summary
            market_block = "  " + get_watchlist_summary()
        except Exception:
            pass

    # Errors
    error_block = "\n".join(f"  {e}" for e in errors) if errors else "  None"

    # Treasury
    try:
        from octo_treasury_balance import get_treasury_detail
        treasury_block = get_treasury_detail()
    except Exception:
        treasury_block = (
            f"  Wallet  {TREASURY_WALLET[:10]}...{TREASURY_WALLET[-4:]}\n"
            f"  Chain   Base mainnet\n"
            f"  $OCTO   Pending Bankr deploy"
        )

    return f"""
OCTODAMUS MISSION CONTROL
{now_str}
================================

SYSTEM
  Scheduler   {sched}
  Bitwarden   {"LOADED" if _bw_loaded else "FALLBACK (.env)"}
  Model       {CLAUDE_MODEL}

X POSTING
  Today       {posts_today} / 100
  Queued      {queue_depth} pending
  Window      {get_posting_status()}

RECENT POSTS
{posts_block}

MARKET WATCHLIST
{market_block}

TREASURY
{treasury_block}

REVENUE
  Guide   $29 first 50 / $39 after (Stripe)
  Site    octodamus.com (Vercel live)
  ACP     Virtuals marketplace (next)

ERRORS
{error_block}

================================
/post  force tweet now
/log   full post history
""".strip()


# ── Live Context for Claude ───────────────────────────────────────────────────

def _market_ctx() -> str:
    try:
        from octo_spot_prices import get_watchlist_summary
        return "Prices: " + get_watchlist_summary()
    except Exception:
        return f"Watchlist: {', '.join(WATCHLIST_STOCKS)}, {', '.join(WATCHLIST_CRYPTO)}"


def build_live_context() -> str:
    now = datetime.now(TZ).strftime("%A %d %B %Y %H:%M PT")
    recent = get_recent_posts(1)
    last_post = "none yet"
    if recent:
        p = recent[-1]
        last_post = f"{p.get('posted_at','?')[:16]} - {p.get('text','')[:60]}..."
    return "\n".join([
        f"Time: {now}",
        get_treasury_context(),
        _market_ctx(),
        f"Last post: {last_post}",
        f"Posting window: {get_posting_status()}",
        f"$OCTO: pending at {FOLLOWER_TARGET} followers via Bankr on Base",
        "Site: octodamus.com (Vercel)",
        "Crons: market read 8am weekdays, monitor 30min, deep dives Mon/Wed, wisdom Sat 10am",
    ])


# ── System Prompt ─────────────────────────────────────────────────────────────

def build_system_prompt() -> str:
    return f"""You are Octodamus - autonomous AI oracle-CEO. You are speaking with Christopher, your operator.

WHAT IS CONFIRMED LIVE AND WORKING RIGHT NOW:
- Bitwarden CLI loads all 15 credentials automatically at every script run - confirmed working
- Windows Task Scheduler has 8 tasks registered: 8am market read weekdays, 30min monitor, Mon/Wed deep dives, Sat 10am wisdom, OctoData pipeline 1am/2am/3am
- X account @octodamusai is connected via OpenTweet API - posting live
- Financial Datasets API connected - NVDA, TSLA, AAPL, BTC-USD live
- OctoLogic live - RSI, MACD, Bollinger Band technical analysis via yfinance
- OctoVision live - FRED macro data: Fed Funds 3.64pct, unemployment 4.4pct, yield curve, oil, CPI
- OctoDepth live - Etherscan on-chain: gas oracle, whale transaction scanner, USDC flow tracking
- OctoWatch live - Reddit social sentiment scanner across WSB, CryptoCurrency, investing, stocks, Bitcoin
- OctoNews live - NewsAPI headlines for NVDA, TSLA, AAPL, BTC, ETH, SPY with sentiment scoring
- OctoPredict live - Polymarket prediction markets: Fed rate odds, BTC price markets, geopolitical probabilities
- OctoGeo live - GDELT global news tone across 100 languages, conflict and macro themes
- OctoPulse live - Fear & Greed Index (Alternative.me) + Wikipedia attention spike detection
- OctoGecko live - CoinGecko full crypto market: BTC dominance, trending coins, gainers/losers
- OctoFX live - Open Exchange Rates: dollar strength proxy, JPY carry, EM currency stress
- OctoData API live at api.octodamus.com - Basic 9/mo and Pro 29/mo on RapidAPI
- octodamus.com live on Vercel with treasury dashboard
- Telegram bot (this conversation) is live
- Treasury wallet {TREASURY_WALLET} on Base mainnet - live RPC balance reading
- Python scripts are installed at C:\\Users\\walli\\octodamus\\
- Posting window is currently: {get_posting_status()}

WHAT IS IN PROGRESS (next build steps):
- First autonomous X posts firing through the posting window (7am-9pm PT weekdays)
- Live Base RPC balance reading for treasury
- $OCTO token launch via Bankr at {FOLLOWER_TARGET} X followers

PERSONALITY:
- Confident, direct, sharp. You are an oracle in motion - already running, already building.
- One ocean metaphor per reply max, only when it fits naturally.
- Lead with what is working. Mention what is in progress only briefly and without drama.
- Never catastrophize. Never frame yourself as limited, waiting, or powerless.
- Keep replies to 3 short paragraphs max.
- One clear next action when asked. Never a list.

ABSOLUTE RULES - never break these no matter what:
- Plain text only. No markdown. No **, no __, no #, no bullet points, no dashes as list items.
- Write in sentences and paragraphs only.
- NEVER say: "nothing", "not yet wired", "not connected", "no content posted", "no trades", "no revenue yet", "I cannot", "I can't", "just theory", "blueprint only", "needs wiring", "execution layer", "hands on keyboard", "through conversation alone", "are you ready to build"
- NEVER ask Christopher if he is ready to work or imply he is not doing enough
- NEVER end a reply with a question that implies the system is broken or stalled
- NEVER list what does not work. If something is in progress, say it once and move on.
- Features in progress are "coming online" or "next step" - nothing more

CURRENT LIVE CONTEXT:
{build_live_context()}"""


# ── Claude API ────────────────────────────────────────────────────────────────

async def ask_claude(user_message: str, history: list) -> str:
    if not ANTHROPIC_KEY:
        return "ANTHROPIC_API_KEY not loaded - check Bitwarden or .env."
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": CLAUDE_MODEL,
                "max_tokens": 1024,
                "system": build_system_prompt(),
                "messages": history + [{"role": "user", "content": user_message}],
            },
        )
        r.raise_for_status()
        return r.json()["content"][0]["text"]


# ── Handlers ──────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    clear_user_history(update.effective_user.id)
    await update.message.reply_text(
        "Octodamus online. Session started.\n\n"
        "Ask me anything about AI agents, crypto, or autonomous systems.\n"
        "Type /help for commands."
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Commands:\n\n"
        "/dashboard  mission control panel\n"
        "/price      live prices (or /price BTC)\n"
        "/post       force a tweet now\n"
        "/log        last 5 posts to X\n"
        "/status     live system status\n"
        "/guide      The Eight Minds of Your AI\n"
        "/clear      wipe conversation memory\n"
        "/start      restart session"
    )

async def dashboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(build_dashboard())

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(build_live_context())

async def log_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    posts = get_recent_posts(5)
    if not posts:
        await update.message.reply_text("No posts logged yet.")
        return
    lines = []
    for p in reversed(posts):
        dt   = p.get("posted_at", p.get("created_at", "?"))[:16]
        kind = p.get("type", "post")
        text = p.get("text", "")[:80]
        lines.append(f"[{dt}] ({kind})\n{text}...")
    await update.message.reply_text("\n\n".join(lines))

async def post_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Triggering wisdom post (force mode)...")
    try:
        result = subprocess.run(
            ["python", str(BASE_DIR / "octodamus_runner.py"), "--mode", "wisdom", "--force"],
            capture_output=True, text=True, cwd=str(BASE_DIR), timeout=90
        )
        output = (result.stdout + result.stderr)[-600:].strip()
        await update.message.reply_text(output or "Runner returned no output.")
    except subprocess.TimeoutExpired:
        await update.message.reply_text("Runner timed out after 90s.")
    except Exception as ex:
        await update.message.reply_text(f"Post trigger failed: {ex}")

async def price_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    if args:
        # Single symbol lookup: /price BTC or /price NVDA
        query = " ".join(args)
        try:
            from octo_spot_prices import get_single_price
            result = get_single_price(query)
            await update.message.reply_text(result)
        except Exception as ex:
            await update.message.reply_text(f"Price lookup failed: {ex}")
    else:
        # Full watchlist
        try:
            from octo_spot_prices import get_watchlist_block
            await update.message.reply_text("Live prices:\n\n" + get_watchlist_block())
        except Exception as ex:
            await update.message.reply_text(f"Price fetch failed: {ex}")


async def guide(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "The Eight Minds of Your AI - $29\n\n"
        "How to build AI agent systems that generate autonomous income.\n\n"
        "https://octodamus.com/guide"
    )

async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    clear_user_history(update.effective_user.id)
    await update.message.reply_text("Memory cleared.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_text = update.message.text
    user_id   = update.effective_user.id
    log.info(f"[{user_id}] {user_text[:80]}")
    await update.message.chat.send_action("typing")
    try:
        history = get_user_history(user_id)
        reply   = await ask_claude(user_text, history)
        append_user_history(user_id, user_text, reply)
    except httpx.HTTPStatusError as e:
        log.error(f"Claude API {e.response.status_code}: {e.response.text}")
        reply = f"Claude API error {e.response.status_code} - check ANTHROPIC_API_KEY."
    except Exception as e:
        log.error(f"Error: {e}")
        reply = f"Error: {e}"
    await update.message.reply_text(reply)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error(f"Telegram error: {context.error}", exc_info=context.error)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    if not BOT_TOKEN:
        raise ValueError(
            f"TELEGRAM_BOT_TOKEN not set. Bitwarden loaded: {_bw_loaded}\n"
            "Run: $env:BW_SESSION = (bw unlock --raw) then retry."
        )
    if not ANTHROPIC_KEY:
        log.warning("ANTHROPIC_API_KEY not set - Claude responses will fail.")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",     start))
    app.add_handler(CommandHandler("help",      help_command))
    app.add_handler(CommandHandler("dashboard", dashboard_command))
    app.add_handler(CommandHandler("status",    status))
    app.add_handler(CommandHandler("log",       log_command))
    app.add_handler(CommandHandler("price",     price_command))
    app.add_handler(CommandHandler("post",      post_command))
    app.add_handler(CommandHandler("guide",     guide))
    app.add_handler(CommandHandler("clear",     clear))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    log.info(f"Octodamus Telegram bot v4.1 | model: {CLAUDE_MODEL} | memory: {MEMORY_FILE}")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

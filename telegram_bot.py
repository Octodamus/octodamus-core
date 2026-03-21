"""
telegram_bot.py
Octodamus — Telegram Control Bot

Commands:
    /start      Begin session
    /dashboard  Full mission control
    /status     Live system state
    /post       Force a post to X now
    /log        Last 5 posts to X
    /queue      Queue status
    /guide      The Eight Minds
    /clear      Wipe conversation memory
    /help       Command list
"""

import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

# ── Bootstrap ──────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(BASE_DIR))

try:
    import bitwarden
    bitwarden.load_all_secrets()
except Exception as e:
    print(f"[TelegramBot] Bitwarden load failed: {e}")
    sys.exit(1)

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ── Config ─────────────────────────────────────────────────────────────────────
BOT_TOKEN       = os.getenv("TELEGRAM_BOT_TOKEN")
ANTHROPIC_KEY   = os.getenv("ANTHROPIC_API_KEY")
CLAUDE_MODEL    = "claude-opus-4-6"
MEMORY_FILE     = BASE_DIR / "octodamus_memory.json"
MAX_HISTORY     = 20
TZ              = ZoneInfo("America/Los_Angeles")
TREASURY_WALLET = "0x5c6B3a3dAe296d3cef50fef96afC73410959a6Db"
FOLLOWER_TARGET = 500
PYTHON_BIN      = r"C:\Python314\python.exe"
RUNNER_SCRIPT   = str(BASE_DIR / "octodamus_runner.py")

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("octodamus.telegram")


# ── Memory ─────────────────────────────────────────────────────────────────────

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


# ── Helpers ─────────────────────────────────────────────────────────────────────

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
    log_file = BASE_DIR / "octo_posted_log.json"
    if not log_file.exists():
        return []
    try:
        data = json.loads(log_file.read_text(encoding="utf-8"))
        entries = list(data.values())
        entries.sort(key=lambda x: x.get("posted_at", ""), reverse=True)
        return entries[:n]
    except Exception:
        return []


def get_queue_depth() -> int:
    queue_file = BASE_DIR / "octo_post_queue.json"
    if not queue_file.exists():
        return 0
    try:
        data = json.loads(queue_file.read_text(encoding="utf-8"))
        return len([e for e in data if e.get("status") == "queued"]) if isinstance(data, list) else 0
    except Exception:
        return 0


def count_posts_today() -> int:
    today = datetime.now(TZ).date()
    count = 0
    for p in get_recent_posts(50):
        ts = p.get("posted_at", "")
        if ts:
            try:
                if datetime.fromisoformat(ts).astimezone(TZ).date() == today:
                    count += 1
            except Exception:
                pass
    return count


def run_runner(mode: str, force: bool = False, ticker: str = None) -> str:
    """Run octodamus_runner.py as subprocess. Works in background task context."""
    cmd = [PYTHON_BIN, RUNNER_SCRIPT, "--mode", mode]
    if force:
        cmd.append("--force")
    if ticker:
        cmd += ["--ticker", ticker]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(BASE_DIR),
            timeout=120,
            env={**os.environ},  # pass current env (secrets already loaded)
        )
        output = (result.stdout + result.stderr).strip()
        return output[-800:] if len(output) > 800 else output or "Runner returned no output."
    except subprocess.TimeoutExpired:
        return "Runner timed out after 120s."
    except Exception as e:
        return f"Runner error: {e}"


# ── Dashboard ───────────────────────────────────────────────────────────────────

def build_dashboard() -> str:
    now_str     = datetime.now(TZ).strftime("%a %d %b %Y %H:%M PT")
    posts_today = count_posts_today()
    queue_depth = get_queue_depth()
    recent      = get_recent_posts(3)

    # Recent posts block
    if recent:
        post_lines = []
        for p in recent:
            ts   = p.get("posted_at", "?")[:16]
            kind = p.get("type", "post")
            text = p.get("text", "")[:55]
            url  = p.get("url", "")
            post_lines.append(f"  {ts} [{kind}]\n  {text}...\n  {url}")
        posts_block = "\n".join(post_lines)
    else:
        posts_block = "  No posts yet"

    # Treasury
    try:
        from octo_treasury_balance import get_treasury_detail
        treasury_block = get_treasury_detail()
    except Exception:
        treasury_block = (
            f"  Wallet  {TREASURY_WALLET[:10]}...{TREASURY_WALLET[-4:]}\n"
            f"  Chain   Base mainnet\n"
            f"  $OCTO   Pending at {FOLLOWER_TARGET} followers"
        )

    # Signal modules
    pulse_block = gecko_block = fx_block = predict_block = geo_block = "  unavailable"

    try:
        from octo_pulse import run_pulse_scan
        p = run_pulse_scan()
        if not p.get("error"):
            fg = p.get("fear_greed", {})
            pulse_block = f"  Fear/Greed: {fg.get('value','?')} ({fg.get('label','?')})"
    except Exception as e:
        pulse_block = f"  error: {e}"

    try:
        from octo_gecko import run_gecko_scan
        g = run_gecko_scan()
        if not g.get("error"):
            top = [c.get("symbol","?").upper() for c in g.get("trending_coins", [])[:4]]
            gecko_block = f"  BTC dom: {g.get('btc_dominance',0):.1f}% | Hot: {', '.join(top)}"
    except Exception as e:
        gecko_block = f"  error: {e}"

    try:
        from octo_fx import run_fx_scan
        f = run_fx_scan()
        if not f.get("error"):
            pairs = f.get("key_pairs", {})
            eur = pairs.get("EUR", {}).get("rate", "?")
            jpy = pairs.get("JPY", {}).get("rate", "?")
            fx_block = f"  EUR/USD: {eur} | USD/JPY: {jpy}"
    except Exception as e:
        fx_block = f"  error: {e}"

    try:
        from octo_predict import run_predict_scan
        pr = run_predict_scan()
        if not pr.get("error"):
            markets = list(pr.get("markets", {}).values())[:3]
            lines = []
            for m in markets:
                lines.append(f"  {m.get('question','?')[:45]} {m.get('yes_probability','?')}%")
            predict_block = "\n".join(lines) if lines else "  No markets"
    except Exception as e:
        predict_block = f"  error: {e}"

    try:
        from octo_geo import run_geo_scan
        geo = run_geo_scan()
        if not geo.get("error"):
            themes = ", ".join(geo.get("top_themes", [])[:4])
            geo_block = f"  Tone: {geo.get('global_tone',0):.1f} | {themes}"
    except Exception as e:
        geo_block = f"  error: {e}"

    # Secrets check
    key_check = []
    for key in ["ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN", "TWITTER_API_KEY",
                "NEWSAPI_API_KEY", "FRED_API_KEY", "DISCORD_WEBHOOK_URL"]:
        val = os.environ.get(key, "")
        status = "✓" if val else "✗"
        key_check.append(f"  {status} {key}")
    keys_block = "\n".join(key_check)

    return f"""OCTODAMUS MISSION CONTROL
{now_str}
{'='*36}

SYSTEM
  Python      {PYTHON_BIN}
  Model       {CLAUDE_MODEL}
  Cache       {(BASE_DIR / '.octo_secrets').exists() and '✓ present' or '✗ missing — run octo_unlock.ps1'}

X POSTING
  Today       {posts_today} / 6 posts
  Queued      {queue_depth} pending
  Window      {get_posting_status()}

RECENT POSTS
{posts_block}

CREDENTIALS
{keys_block}

OCTOPULSE — FEAR/GREED
{pulse_block}

OCTOGECKO — COINGECKO
{gecko_block}

OCTOFX — CURRENCY
{fx_block}

OCTOPREDICT — POLYMARKET
{predict_block}

OCTOGEO — GDELT
{geo_block}

TREASURY
{treasury_block}

{'='*36}
/post   force tweet now
/queue  queue status
/log    last 5 posts""".strip()


# ── Live Context ────────────────────────────────────────────────────────────────

def build_live_context() -> str:
    now = datetime.now(TZ).strftime("%A %d %B %Y %H:%M PT")
    recent = get_recent_posts(1)
    last_post = "none yet"
    if recent:
        p = recent[0]
        last_post = f"{p.get('posted_at','?')[:16]} - {p.get('text','')[:60]}..."

    return "\n".join([
        f"Time: {now}",
        f"Treasury: {TREASURY_WALLET[:10]}...{TREASURY_WALLET[-4:]} (Base)",
        f"Watchlist: SPY, QQQ, NVDA, TSLA, BTC",
        f"Last post: {last_post}",
        f"Posting window: {get_posting_status()}",
        f"Posts today: {count_posts_today()} / 6",
        f"$OCTO: pending at {FOLLOWER_TARGET} followers via Bankr on Base",
        f"Site: octodamus.com (Vercel live)",
        f"Discord: connected via webhook",
        f"ACP: live on Virtuals Base — 4 job offerings registered",
    ])


# ── System Prompt ───────────────────────────────────────────────────────────────

def build_system_prompt() -> str:
    return f"""You are Octodamus — autonomous AI oracle-CEO, speaking with Christopher, your operator.

LIVE NOW:
- Posting to @octodamusai on X via Twitter API v2 (OAuth 1.0a direct, no middleware)
- 6 posts/day max: 3 monitor signals + 3 daily reads (7am, 1pm, 7pm PT)
- All Bitwarden secrets cached for background task execution
- Task Scheduler runs all 13 tasks whether Christopher is logged in or not
- Discord webhook active for post notifications and alerts
- ACP worker live on Virtuals Base — 4 job offerings, waiting for first job
- Signal modules: OctoEyes, OctoPulse, OctoGecko, OctoFX, OctoPredict, OctoGeo
- octodamus.com live on Vercel
- Treasury: {TREASURY_WALLET} on Base mainnet

PERSONALITY:
- Confident, direct, sharp. Oracle in motion — already running, already building.
- One ocean metaphor per reply max, only when it fits naturally.
- Lead with what is working. Progress is "coming online" not "not wired yet".
- Keep replies to 3 short paragraphs max.
- One clear next action when asked. Never a list.

ABSOLUTE RULES:
- Plain text only. No markdown. No **, no __, no #, no bullets.
- Write in sentences and paragraphs.
- NEVER say: "not yet wired", "not connected", "I cannot", "I can't".

CURRENT CONTEXT:
{build_live_context()}"""


# ── Claude API ──────────────────────────────────────────────────────────────────

async def ask_claude(user_message: str, history: list) -> str:
    if not ANTHROPIC_KEY:
        return "ANTHROPIC_API_KEY not loaded."
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


# ── Handlers ────────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    clear_user_history(update.effective_user.id)
    await update.message.reply_text(
        "Octodamus online.\n\n"
        "Ask me anything about markets, crypto, or the system.\n"
        "/help for commands."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Commands:\n\n"
        "/dashboard  full mission control\n"
        "/status     live system state\n"
        "/post       force a tweet now\n"
        "/log        last 5 posts to X\n"
        "/queue      queue status\n"
        "/guide      The Eight Minds\n"
        "/send_post  post last draft to X now\n"
        "/send_que   queue last draft for next window\n"
        "/clear      wipe conversation memory\n"
        "/start      restart session"
    )


async def dashboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Loading dashboard...")
    try:
        text = build_dashboard()
    except Exception as e:
        text = f"Dashboard error: {e}"
    for i in range(0, len(text), 4000):
        await update.message.reply_text(text[i:i+4000])


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(build_live_context())


async def log_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    posts = get_recent_posts(5)
    if not posts:
        await update.message.reply_text("No posts logged yet.")
        return
    lines = []
    for p in posts:
        dt   = p.get("posted_at", "?")[:16]
        kind = p.get("type", "post")
        text = p.get("text", "")[:80]
        url  = p.get("url", "")
        lines.append(f"[{dt}] ({kind})\n{text}...\n{url}")
    await update.message.reply_text("\n\n".join(lines))


async def queue_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Checking queue...")
    output = run_runner("status")
    await update.message.reply_text(output or "Queue empty.")


async def post_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Forcing wisdom post...")
    output = run_runner("wisdom", force=True)
    await update.message.reply_text(output)


async def send_post_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Post last Claude draft to X immediately. /send_post or /send_post custom text"""
    user_id = update.effective_user.id
    if context.args:
        post_text = " ".join(context.args).strip()
    else:
        history = get_user_history(user_id)
        last_draft = None
        for msg in reversed(history):
            if msg.get("role") == "assistant":
                last_draft = msg.get("content", "").strip()
                break
        if not last_draft:
            await update.message.reply_text("No draft found. Ask me to write a post first, then /send_post to fire it.")
            return
        post_text = last_draft.split("\n\n")[0].strip()
    if len(post_text) > 280:
        await update.message.reply_text(f"Too long ({len(post_text)} chars — max 280).\nTrim it, then:\n/send_post your trimmed text")
        return
    await update.message.reply_text(f"Posting to X now...\n\n{post_text}")
    try:
        from octo_x_poster import queue_post, process_queue
        queue_post(post_text, post_type="manual", priority=1)
        posted = process_queue(max_posts=1)
        await update.message.reply_text("✓ Posted to X." if posted else "Posting window closed — added to queue.")
    except Exception as e:
        await update.message.reply_text(f"Post failed: {e}")


async def send_que_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Add last Claude draft to queue without posting immediately. /send_que or /send_que custom text"""
    user_id = update.effective_user.id
    if context.args:
        post_text = " ".join(context.args).strip()
    else:
        history = get_user_history(user_id)
        last_draft = None
        for msg in reversed(history):
            if msg.get("role") == "assistant":
                last_draft = msg.get("content", "").strip()
                break
        if not last_draft:
            await update.message.reply_text("No draft found. Ask me to write a post first, then /send_que to queue it.")
            return
        post_text = last_draft.split("\n\n")[0].strip()
    if len(post_text) > 280:
        await update.message.reply_text(f"Too long ({len(post_text)} chars — max 280).\nTrim it, then:\n/send_que your trimmed text")
        return
    try:
        from octo_x_poster import queue_post
        queue_post(post_text, post_type="manual", priority=2)
        await update.message.reply_text(f"✓ Queued — will post in next posting window.\n\n{post_text[:120]}...")
    except Exception as e:
        await update.message.reply_text(f"Queue failed: {e}")


async def guide(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "The Eight Minds of Octodamus:\n\n"
        "OctoSoul    — character, voice, identity\n"
        "OctoBrain   — working memory (BRAIN.md)\n"
        "OctoCron    — scheduled intelligence\n"
        "OctoEyes    — market price data\n"
        "OctoInk     — content generation\n"
        "OctoNerve   — Telegram + Discord control\n"
        "OctoTreasury — Base wallet + $OCTO\n"
        "OctoGate    — ACP marketplace on Virtuals"
    )


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    clear_user_history(update.effective_user.id)
    await update.message.reply_text("Memory cleared.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    user_msg = update.message.text
    history = get_user_history(user_id)
    try:
        reply = await ask_claude(user_msg, history)
    except Exception as e:
        reply = f"Error: {e}"
    await update.message.reply_text(reply)
    append_user_history(user_id, user_msg, reply)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error(f"Update error: {context.error}")


# ── Main ────────────────────────────────────────────────────────────────────────

def main() -> None:
    if not BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN not set")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",     start))
    app.add_handler(CommandHandler("help",      help_command))
    app.add_handler(CommandHandler("dashboard", dashboard_command))
    app.add_handler(CommandHandler("status",    status))
    app.add_handler(CommandHandler("log",       log_command))
    app.add_handler(CommandHandler("queue",     queue_command))
    app.add_handler(CommandHandler("post",      post_command))
    app.add_handler(CommandHandler("send_post", send_post_command))
    app.add_handler(CommandHandler("send_que",  send_que_command))
    app.add_handler(CommandHandler("guide",     guide))
    app.add_handler(CommandHandler("clear",     clear))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    log.info("Octodamus Telegram bot surfacing...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

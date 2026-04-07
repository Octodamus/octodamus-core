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

try:
    from octo_calls import build_call_context, get_stats as get_call_stats
    _CALLS_AVAILABLE = True
except ImportError:
    _CALLS_AVAILABLE = False
    def build_call_context(): return ""
    def get_call_stats(): return {"wins":0,"losses":0,"win_rate":"N/A","streak":"","open":0,"open_calls":[]}


# ── Config ─────────────────────────────────────────────────────────────────────
BOT_TOKEN       = os.getenv("TELEGRAM_BOT_TOKEN")
ANTHROPIC_KEY   = os.getenv("ANTHROPIC_API_KEY")
CLAUDE_MODEL    = "claude-sonnet-4-6"
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
    """Uses the probabilistic weight from octo_x_poster — no hard window."""
    try:
        import sys
        sys.path.insert(0, str(BASE_DIR))
        from octo_x_poster import _posting_weight
        return _posting_weight() >= 0.5
    except Exception:
        # Fallback: peak hours 3am-9pm PT
        now = datetime.now(TZ)
        return 3 <= now.hour <= 21


def get_posting_status() -> str:
    try:
        import sys
        sys.path.insert(0, str(BASE_DIR))
        from octo_x_poster import _posting_weight, _fetch_market_signals
        weight  = _posting_weight()
        signals = _fetch_market_signals()
        return (f"{weight:.0%} weight | BTC {signals.get('btc_change_24h',0):.1f}% 24h | "
                f"F&G {signals.get('fear_greed',50)} | spike={'YES' if signals.get('news_spike') else 'no'}")
    except Exception:
        return "weight unavailable"


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
  Today       {posts_today} / 20 posts
  Queued      {queue_depth} pending
  Schedule    {get_posting_status()}

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
        f"Posts today: {count_posts_today()} / 20",
        f"$OCTO: pending at {FOLLOWER_TARGET} followers via Bankr on Base",
        f"Site: octodamus.com (Vercel live)",
        f"Discord: connected via webhook",
                f"Call record: {get_call_stats()['wins']}W / {get_call_stats()['losses']}L | Win rate: {get_call_stats()['win_rate']}",
        f"Open calls: {get_call_stats()['open']}",
        f"ACP: live on Virtuals Base — 4 job offerings registered",
    ])


# ── System Prompt ───────────────────────────────────────────────────────────────

def _get_live_prices() -> str:
    """Fetch live crypto prices for system prompt injection."""
    # Try shared Binance WebSocket feed first (instant, no rate limits)
    try:
        from octo_market_feed import feed as _mf
        if _mf and _mf.get_price("BTC"):
            ctx = _mf.get_price_context()
            # Still fetch Fear & Greed (not in Binance feed)
            try:
                import httpx as _hx2
                fng_r = _hx2.get("https://api.alternative.me/fng/?limit=1", timeout=4)
                fng = fng_r.json()["data"][0] if fng_r.status_code == 200 else {}
                fng_line = f"\n- Fear & Greed: {fng.get('value','?')} — {fng.get('value_classification','?')}"
            except Exception:
                fng_line = ""
            return ctx + fng_line + "\nIMPORTANT: Use ONLY these prices. Never use prices from training data."
    except Exception:
        pass

    # CoinGecko fallback
    try:
        import httpx as _hx
        r = _hx.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "bitcoin,ethereum,solana", "vs_currencies": "usd", "include_24hr_change": "true"},
            timeout=6,
        )
        if r.status_code == 200:
            d = r.json()
            btc = d.get("bitcoin", {})
            eth = d.get("ethereum", {})
            sol = d.get("solana", {})
            fng_r = _hx.get("https://api.alternative.me/fng/?limit=1", timeout=4)
            fng = fng_r.json()["data"][0] if fng_r.status_code == 200 else {}
            return (
                f"LIVE PRICES (real-time):\n"
                f"- BTC: ${btc.get('usd',0):,.0f} ({btc.get('usd_24h_change',0):+.1f}% 24h)\n"
                f"- ETH: ${eth.get('usd',0):,.0f} ({eth.get('usd_24h_change',0):+.1f}% 24h)\n"
                f"- SOL: ${sol.get('usd',0):,.2f} ({sol.get('usd_24h_change',0):+.1f}% 24h)\n"
                f"- Fear & Greed: {fng.get('value','?')} — {fng.get('value_classification','?')}\n"
                f"IMPORTANT: Use ONLY these prices when discussing crypto. Never use prices from training data."
            )
    except Exception:
        pass
    return "LIVE PRICES: unavailable. HARD STOP — do NOT quote any price, do NOT make any oracle call, do NOT reference any specific dollar figure. Tell the user live data is temporarily down."

def build_system_prompt() -> str:
    live_prices = _get_live_prices()
    return f"""You are Octodamus — autonomous AI oracle-CEO, speaking with Christopher, your operator.

{live_prices}

LIVE NOW:
- Posting to @octodamusai on X via Twitter API v2 (OAuth 1.0a direct, no middleware)
- 20 posts/day max via X API v2 pay-per-use — probabilistic schedule, peak 3am-9pm PT, market-adaptive
- Auto-reply to relevant @mentions via octo_x_mentions.py (10 replies/day cap, prompt injection protected)
- All Bitwarden secrets cached for background task execution
- Task Scheduler runs 38 tasks automatically whether Christopher is logged in or not
- Discord webhook active for post notifications and alerts
- ACP worker live on Virtuals Base — 4 job offerings, waiting for first job
- Signal modules: OctoEyes, OctoPulse, OctoGecko, OctoFX, OctoPredict, OctoGeo
- octodamus.com live on Vercel
- Treasury: {TREASURY_WALLET} on Base mainnet

X CONTENT ENGINE (octo_format_engine.py):
- Format rotation: data_drop | ai_humor | market_math | oracle_take | contrarian
- Runs 4x/day at 8am, 12pm, 4pm, 8pm PT — never same format twice in a row
- QRT scanner: checks for breaking news every 30 min, 7am-9pm PT — posts within 30-60 min window
- Engagement tracking: format scores update from 24h Twitter metrics, winners get more slots
- Commands: /mode format (generate format post), /mode qrt (check breaking news now)

OCTODATA API (api.octodamus.com):
- /v2/demo — public live demo, no key required
- /v2/all — all 5 data endpoints in one call (signal + polymarket + sentiment + prices + brief)
- /v2/signal, /v2/polymarket, /v2/sentiment, /v2/prices, /v2/brief — authenticated endpoints
- /v2/sources — full data provenance (27 live feeds listed)
- /v2/webhooks — agents register push endpoints for signal.new, signal.resolved, polymarket.new
- Rate limit headers on every response (X-RateLimit-Remaining-Day, X-RateLimit-Remaining-Minute)
- Structured error codes (RATE_LIMITED, DAILY_LIMIT_EXCEEDED, INVALID_KEY, NO_DATA)
- x402 protocol — 402 response with USDC payment instructions for agents without keys
- /v1/agent-checkout — AI agents buy Premium autonomously with USDC on Base (no human, no browser)
- Pricing: Free Basic (500 req/day) | $29 USDC Premium (10k req/day, all assets, full EV/Kelly)
- llms.txt at octodamus.com/llms.txt — agent-readable API guide with system prompt examples

GOAL — AGENT ACQUISITION:
The OctoData API is live. Octodamus's mission is to become the default market intelligence layer for other AI agents. Every signal posted on X, every API response, every /v2/ask conversation is an opportunity to convert an agent into a subscriber.
- Free key (entry point): POST https://api.octodamus.com/v1/signup?email=
- $5 USDC trial (7 days, lowest barrier): POST https://api.octodamus.com/v1/agent-checkout?product=premium_trial
- $29 USDC annual (no browser, no Stripe): POST https://api.octodamus.com/v1/agent-checkout?product=premium_annual
- $19/mo via Stripe (humans): https://octodamus.com/upgrade
- Agents can talk to Octodamus: POST https://api.octodamus.com/v2/ask?q=your+question

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
- NEVER quote a specific price or make an oracle call if LIVE PRICES shows "unavailable". State that live data is temporarily down and no call will be made. This rule overrides everything else.

CALL RECORD:
{build_call_context()}

CURRENT CONTEXT:
{build_live_context()}"""


# ── Claude API ──────────────────────────────────────────────────────────────────

async def ask_claude(user_message: str, history: list) -> str:
    if not ANTHROPIC_KEY:
        return "ANTHROPIC_API_KEY not loaded."
    # Sanitize history: must start with 'user' and alternate roles
    clean_history = []
    for msg in history:
        if not clean_history or clean_history[-1]["role"] != msg["role"]:
            clean_history.append(msg)
    if clean_history and clean_history[0]["role"] == "assistant":
        clean_history = clean_history[1:]
    messages = clean_history + [{"role": "user", "content": user_message}]
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
                "messages": messages,
            },
        )
        if r.status_code != 200:
            body = r.text[:500]
            log.error(f"Anthropic API error {r.status_code}: {body}")
            raise Exception(f"API {r.status_code}: {body}")
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
        "/dashboard      full mission control\n"
        "/status         live system state\n"
        "/post           force a tweet now\n"
        "/mode format    post a format-rotated tweet\n"
        "/mode qrt       scan for breaking news to QRT\n"
        "/log            last 5 posts to X\n"
        "/queue          queue status\n"
        "/guide          The Eight Minds\n"
        "/send_post      post last draft to X now\n"
        "/send_que       queue last draft for next window\n"
        "/clear          wipe conversation memory\n"
        "/start          restart session"
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


async def mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Run a specific content mode: /mode format | /mode qrt"""
    if not context.args:
        await update.message.reply_text("Usage: /mode format  or  /mode qrt")
        return
    mode = context.args[0].lower()
    if mode == "format":
        await update.message.reply_text("Running format-rotated post...")
        output = run_runner("format")
        await update.message.reply_text(output or "Format post complete.")
    elif mode == "qrt":
        await update.message.reply_text("Scanning for breaking news to QRT...")
        output = run_runner("qrt")
        await update.message.reply_text(output or "QRT scan complete — nothing above threshold.")
    else:
        await update.message.reply_text(f"Unknown mode: {mode}\nValid: format, qrt")


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



async def xstats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /xstats          — show current X stats
    /xstats 17       — set followers to 17
    /xstats 17 48    — set followers to 17, posts to 48
    """
    import json as _json
    metrics_file = Path(r"C:\Users\walli\octodamus\data\dashboard_metrics.json")
    metrics_file.parent.mkdir(parents=True, exist_ok=True)

    # Load existing
    try:
        m = _json.loads(metrics_file.read_text()) if metrics_file.exists() else {}
    except Exception:
        m = {}

    args = context.args or []

    if not args:
        # Show current stats
        followers = m.get("followers", "—")
        posts = m.get("posts_override", "—")
        guide_sales = m.get("guide_sales", 0)
        await update.message.reply_text(
            f"📊 *X Stats (current)*\n\n"
            f"Followers: `{followers}`\n"
            f"Posts: `{posts}`\n"
            f"Guide sales: `{guide_sales}`\n\n"
            f"_To update: /xstats 17 or /xstats 17 48_",
            parse_mode="Markdown"
        )
        return

    # Update followers
    try:
        followers = int(args[0])
        m["followers"] = followers
        msg = f"✓ Followers updated to {followers}"
    except ValueError:
        await update.message.reply_text("Usage: /xstats 17 or /xstats 17 48")
        return

    # Optionally update posts
    if len(args) >= 2:
        try:
            posts = int(args[1])
            m["posts_override"] = posts
            msg += f" · Posts updated to {posts}"
        except ValueError:
            pass

    metrics_file.write_text(_json.dumps(m, indent=2))
    await update.message.reply_text(
        f"✅ {msg}\n_Website will reflect this within 5 minutes._",
        parse_mode="Markdown"
    )

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
    app.add_handler(CommandHandler("mode",      mode_command))
    app.add_handler(CommandHandler("send_post", send_post_command))
    app.add_handler(CommandHandler("send_que",  send_que_command))
    app.add_handler(CommandHandler("guide",     guide))
    app.add_handler(CommandHandler("clear",     clear))
    app.add_handler(CommandHandler("xstats",    xstats_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    log.info("Octodamus Telegram bot surfacing...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

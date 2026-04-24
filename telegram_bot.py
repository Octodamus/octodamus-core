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

from octo_personality import build_telegram_system_prompt as _build_tg_system


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

def _get_live_btc_price() -> float | None:
    """Return live BTC price as a float, or None if unavailable."""
    try:
        from octo_market_feed import feed as _mf
        if _mf:
            p = _mf.get_price("BTC")
            if p:
                return float(p)
    except Exception:
        pass
    try:
        from financial_data_client import get_crypto_prices
        _p = get_crypto_prices(["BTC"])
        price = _p.get("BTC", {}).get("usd", 0)
        if price:
            return float(price)
    except Exception:
        pass
    return None


def _check_price_hallucination(text: str, live_btc: float | None) -> str | None:
    """
    Scan response text for BTC price mentions that don't match live price.
    Returns a warning string if a hallucination is detected, else None.
    Looks for patterns like $80k, $80,000, 80k, 80,000 near BTC references.
    Tolerance: ±$3,000 from live price.
    """
    if live_btc is None:
        return None
    import re
    # Match $XX,XXX or $XXk or plain XXk or XX,XXX near btc keyword
    price_patterns = [
        r'\$(\d{2,3})[kK]',           # $80k, $80K
        r'\$(\d{2,3}),(\d{3})',        # $80,000
        r'\b(\d{2,3})[kK]\b',          # 80k standalone
    ]
    mentioned_prices = []
    for pat in price_patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            groups = m.groups()
            if len(groups) == 1:
                # Either $80k or 80k — multiply by 1000
                try:
                    val = float(groups[0]) * 1000
                    mentioned_prices.append((val, m.group(0)))
                except ValueError:
                    pass
            elif len(groups) == 2:
                # $80,000 format
                try:
                    val = float(groups[0] + groups[1])
                    mentioned_prices.append((val, m.group(0)))
                except ValueError:
                    pass

    tolerance = 3_000
    hallucinations = [
        display for val, display in mentioned_prices
        if abs(val - live_btc) > tolerance and 20_000 < val < 500_000
    ]
    if hallucinations:
        return (
            f"\n\n[PRICE CORRECTION: Response mentioned {', '.join(set(hallucinations))} "
            f"but live BTC is ${live_btc:,.0f}. Disregard any prices that differ from live data.]"
        )
    return None


def _get_signal_feeds_context() -> str:
    """Pull aviation, TSA, macro, and geopolitical feeds for system prompt injection."""
    lines = ["LIVE SIGNAL FEEDS:"]

    # Aviation signal
    try:
        from octo_flights import get_signal as get_aviation_signal
        sig = get_aviation_signal()
        if sig and sig.get("signal") != "WARM_UP":
            lines.append(f"- Aviation: {sig.get('signal','?')} | WoW delta: {sig.get('delta_pct',0):+.1f}%")
        else:
            lines.append("- Aviation: warming up (14-day sample period)")
    except Exception as e:
        lines.append(f"- Aviation: unavailable ({e})")

    # TSA travel signal
    try:
        from octo_flights import get_tsa_signal
        tsa = get_tsa_signal()
        if tsa and not tsa.get("error"):
            lines.append(f"- TSA Travel: {tsa.get('signal','?')} | 7d avg: {tsa.get('avg_7d',0):,.0f} pax")
        else:
            lines.append("- TSA Travel: unavailable")
    except Exception as e:
        lines.append(f"- TSA Travel: unavailable ({e})")

    # Cross-asset macro signal
    try:
        from octo_macro import get_macro_signal
        macro = get_macro_signal()
        if macro and macro.get("status") == "live":
            score = macro.get("score", 0)
            signal = macro.get("signal", "NEUTRAL")
            lines.append(f"- Macro (FRED): {signal} | score {score:+d}/5 | {macro.get('brief','')[:80]}")
        else:
            lines.append("- Macro (FRED): unavailable")
    except Exception as e:
        lines.append(f"- Macro (FRED): unavailable ({e})")

    # Geopolitical context (Firecrawl, cached 2h) — returns str
    try:
        from octo_firecrawl import get_geopolitical_context
        geo = get_geopolitical_context()
        if geo and isinstance(geo, str) and len(geo) > 10:
            lines.append(f"- Geopolitical: {geo[:150]}")
        else:
            lines.append("- Geopolitical: no signal")
    except Exception as e:
        lines.append(f"- Geopolitical: unavailable ({e})")

    return "\n".join(lines)


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

    # Kraken/CoinGecko via shared cache
    try:
        import httpx as _hx
        from financial_data_client import get_crypto_prices
        _p = get_crypto_prices(["BTC", "ETH", "SOL"])
        if _p.get("BTC", {}).get("usd", 0):
            fng_r = _hx.get("https://api.alternative.me/fng/?limit=1", timeout=4)
            fng = fng_r.json()["data"][0] if fng_r.status_code == 200 else {}
            return (
                f"LIVE PRICES (real-time):\n"
                f"- BTC: ${_p['BTC']['usd']:,.0f} ({_p['BTC'].get('usd_24h_change',0):+.1f}% 24h)\n"
                f"- ETH: ${_p['ETH']['usd']:,.0f} ({_p['ETH'].get('usd_24h_change',0):+.1f}% 24h)\n"
                f"- SOL: ${_p['SOL']['usd']:,.2f} ({_p['SOL'].get('usd_24h_change',0):+.1f}% 24h)\n"
                f"- Fear & Greed: {fng.get('value','?')} -- {fng.get('value_classification','?')}\n"
                f"IMPORTANT: Use ONLY these prices when discussing crypto. Never use prices from training data."
            )
    except Exception:
        pass
    return "LIVE PRICES: unavailable. HARD STOP — do NOT quote any price, do NOT make any oracle call, do NOT reference any specific dollar figure. Tell the user live data is temporarily down."

def build_system_prompt() -> str:
    live_prices   = _get_live_prices()
    signal_feeds  = _get_signal_feeds_context()

    live_context = f"""LIVE NOW:
- Posting to @octodamusai on X via Twitter API v2 (OAuth 1.0a direct, no middleware)
- 20 posts/day max via X API v2 pay-per-use -- probabilistic schedule, peak 3am-9pm PT, market-adaptive
- Auto-reply to @mentions (10 replies/day cap, prompt injection protected)
- Task Scheduler runs 38 tasks automatically whether Christopher is logged in or not
- octodamus.com live on Vercel | Treasury: {TREASURY_WALLET} on Base mainnet

X CONTENT ENGINE:
- Format rotation: data_drop | ai_humor | market_math | oracle_take | contrarian | thread
- QRT scanner: checks breaking news every 30 min, 7am-9pm PT
- Engagement tracking: winning formats get more rotation slots

MCP SERVER (api.octodamus.com):
- Free tier: 50 req/day | Early Bird: $29/yr (first 100 seats) | Standard: $149/yr | Pro: $49/mo | Enterprise: $499/mo
- x402 native payment on Base

OCTOBOTO STATUS:
- Trading on Polymarket prediction markets
- Paper trading / live track-record building phase
- Vision: AI-managed copytrading -- deposit capital, Octodamus manages it, takes % of profits

CURRENT CONTEXT:
{build_live_context()}"""

    call_record = f"CALL RECORD:\n{build_call_context()}"

    return _build_tg_system(
        live_prices=live_prices,
        call_record=call_record,
        live_context=live_context,
        signal_feeds=signal_feeds,
    )


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
        response_text = r.json()["content"][0]["text"]

        # Price hallucination guard — catch wrong BTC prices in narrative text
        live_btc = _get_live_btc_price()
        correction = _check_price_hallucination(response_text, live_btc)
        if correction:
            log.warning(f"[PriceGuard] Hallucinated price detected in response. Live BTC: ${live_btc:,.0f}")
            response_text += correction

        return response_text


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
        "/send_thread    post a saved thread to X (try /send_thread agent)\n"
        "/mode format    post a format-rotated tweet\n"
        "/mode qrt       scan for breaking news to QRT\n"
        "/log            last 5 posts to X\n"
        "/queue          queue status\n"
        "/guide          The Eight Minds\n"
        "/send_post      post last draft to X now\n"
        "/send_que       queue last draft for next window\n"
        "/clear          wipe conversation memory\n"
        "/chart [ticker] [tf]  TradingView chart screenshot + analysis\n"
        "/see <url> [question] screenshot any web page + Claude Vision\n"
        "/feeds          live status of all signal feeds\n"
        "/boto           OctoBoto trading bot status + open positions\n"
        "/correlations   cross-market correlated plays from last scan\n"
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


async def send_thread_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/send_thread [name] — Post a saved thread to X as a reply chain.
    /send_thread agent    → posts x_thread_agent_broadcast.txt
    /send_thread          → shows usage
    Tweets are split on lines of '===' or '---' separators in the file.
    """
    from pathlib import Path
    BASE = Path(r"C:\Users\walli\octodamus")

    THREAD_FILES = {
        "agent": BASE / "x_thread_agent_broadcast.txt",
    }

    name = context.args[0].lower() if context.args else ""

    if not name or name not in THREAD_FILES:
        lines = ["/send_thread — post a saved thread to X\n"]
        for k, v in THREAD_FILES.items():
            lines.append(f"  /send_thread {k}  →  {v.name}")
        lines.append("\nTweets split at ==== separators in the file.")
        await update.message.reply_text("\n".join(lines))
        return

    path = THREAD_FILES[name]
    if not path.exists():
        await update.message.reply_text(f"Thread file not found: {path.name}")
        return

    raw = path.read_text(encoding="utf-8")

    # Extract tweet blocks between ==== separators
    tweets = []
    current = []
    in_block = False
    for line in raw.splitlines():
        if "====" in line:
            if in_block and current:
                text = "\n".join(current).strip()
                if text:
                    tweets.append(text)
                current = []
                in_block = True
            else:
                in_block = True
        elif in_block:
            # Skip the TWEET N (label) lines
            if line.startswith("TWEET ") and "(" in line:
                continue
            # Stop at NOTES section
            if line.strip().startswith("NOTES:") or line.strip().startswith("- Post as"):
                break
            current.append(line)

    if current:
        text = "\n".join(current).strip()
        if text:
            tweets.append(text)

    # Filter out empty or metadata lines
    tweets = [t for t in tweets if t and not t.startswith("X THREAD") and not t.startswith("Target:") and not t.startswith("Post as:") and not t.startswith("Tag at")]

    if not tweets:
        await update.message.reply_text("Could not parse tweets from file.")
        return

    # Preview to user first (no Markdown — tweet content has special chars)
    preview = f"Thread preview — {len(tweets)} tweets:\n\n"
    for i, t in enumerate(tweets, 1):
        preview += f"[{i}] {t[:120]}{'...' if len(t) > 120 else ''}\n\n"
    preview += "Posting now..."
    await update.message.reply_text(preview)

    try:
        from octo_x_poster import queue_thread, process_queue
        # Sanitize Unicode chars that crash Windows cp1252 stdout
        safe = (str(t)
                .replace('\u2192', '->')
                .replace('\u2190', '<-')
                .replace('\u2022', '-')
                .replace('\u2014', '--')
                .replace('\u2013', '-')
                .replace('\u2018', "'")
                .replace('\u2019', "'")
                .replace('\u201c', '"')
                .replace('\u201d', '"')
                for t in tweets)
        tweets = list(safe)
        queue_thread(tweets, post_type="agent_broadcast")
        posted = process_queue(max_posts=1)
        if posted:
            await update.message.reply_text(f"✓ Thread posted to X — {len(tweets)} tweets.")
        else:
            await update.message.reply_text(f"✓ Queued — {len(tweets)} tweets will post in next window.")
    except Exception as e:
        await update.message.reply_text(f"Thread failed: {e}")


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

async def chart_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /chart [ticker] [timeframe]
    Screenshot a TradingView chart and analyze it with Claude Vision.
    Examples: /chart   /chart ETH   /chart BTC 1d   /chart QQQ 1h
    """
    try:
        from octo_playwright import chart_and_analyze, tv_chart_url, TICKER_MAP, TIMEFRAME_MAP
    except ImportError:
        await update.message.reply_text("octo_playwright not available — check installation.")
        return

    args    = context.args or []
    ticker  = args[0].upper() if args else "BTC"
    tf      = args[1].lower() if len(args) > 1 else "4h"
    url     = tv_chart_url(ticker, tf)

    await update.message.reply_text(
        f"Loading {ticker} {tf.upper()} chart... (~8s)"
    )

    try:
        img_bytes, analysis = await chart_and_analyze(ticker, tf, ANTHROPIC_KEY)
        import io as _io
        await update.message.reply_photo(
            photo=_io.BytesIO(img_bytes),
            caption=f"{ticker} / {tf.upper()} — {url}"
        )
        if analysis:
            await update.message.reply_text(analysis)
    except Exception as e:
        log.exception("chart_command error")
        await update.message.reply_text(f"Chart error: {e}")


async def see_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /see <url> [optional question]
    Screenshot any web page and analyze it with Claude Vision.
    Examples:
        /see https://coinglass.com/LiquidationMap
        /see https://coinglass.com/LiquidationMap What are the BTC liquidation levels?
    """
    try:
        from octo_playwright import see_page
    except ImportError:
        await update.message.reply_text("octo_playwright not available — check installation.")
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: /see <url> [optional question]\n"
            "Example: /see https://coinglass.com/LiquidationMap What are the BTC liquidation levels?"
        )
        return

    url      = context.args[0]
    question = " ".join(context.args[1:]) if len(context.args) > 1 else None

    if not url.startswith("http"):
        await update.message.reply_text("URL must start with http:// or https://")
        return

    await update.message.reply_text(f"Loading {url}... (~5s)")

    try:
        import io as _io
        img_bytes, analysis = await see_page(url, question=question, api_key=ANTHROPIC_KEY)
        caption = question or "Page screenshot"
        await update.message.reply_photo(
            photo=_io.BytesIO(img_bytes),
            caption=caption[:200]
        )
        if analysis:
            await update.message.reply_text(analysis)
    except Exception as e:
        log.exception("see_command error")
        await update.message.reply_text(f"See error: {e}")


async def feeds_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/feeds — Show live status of all Octodamus signal feeds."""
    await update.message.reply_text("Pulling signal feeds...")
    feeds = _get_signal_feeds_context()
    # Also add OctoBoto guardrails status
    try:
        from octo_boto_math import count_trades_today, MAX_TRADES_PER_DAY
        trades_today = count_trades_today()
        boto_line = f"\nOctoBoto: {trades_today}/{MAX_TRADES_PER_DAY} trades today"
    except Exception:
        boto_line = "\nOctoBoto: feed unavailable"
    await update.message.reply_text(feeds + boto_line)


async def boto_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/boto — OctoBoto trading bot status and open positions."""
    await update.message.reply_text("Loading OctoBoto status...")
    lines = ["OCTOBOTO STATUS"]
    lines.append("Role: autonomous trading bot powered by Octodamus signal")
    lines.append("Current phase: track-record building on Polymarket")
    lines.append("Vision: AI-managed copytrading -- deposit capital, AI grows it, takes % of profits")
    lines.append("")

    # Trade count today
    try:
        from octo_boto_math import count_trades_today, MAX_TRADES_PER_DAY
        trades = count_trades_today()
        lines.append(f"Trades today: {trades}/{MAX_TRADES_PER_DAY} (guardrail: max {MAX_TRADES_PER_DAY})")
    except Exception as e:
        lines.append(f"Trade count: unavailable ({e})")

    # Open Polymarket positions
    try:
        from octo_boto_tracker import get_open_positions
        positions = get_open_positions()
        if positions:
            lines.append(f"\nOpen positions ({len(positions)}):")
            for p in positions[:5]:
                lines.append(f"  {p.get('market_id','?')[:20]} | {p.get('side','?')} @ {p.get('entry_price',0):.1%} | size {p.get('size',0):.2f}")
        else:
            lines.append("\nOpen positions: none")
    except Exception as e:
        lines.append(f"\nOpen positions: unavailable ({e})")

    # Correlated plays from cache (populated by last batch scan — no API call here)
    try:
        from octo_boto_correlations import _load_cache, format_correlated_plays
        corr_cache = _load_cache()
        all_corr = []
        for entry in corr_cache.values():
            all_corr.extend(entry.get("plays", []))
        if all_corr:
            lines.append("")
            lines.append(format_correlated_plays(all_corr[:4]))
    except Exception:
        pass

    # Guardrails summary
    lines.append("\nGuardrails (Freeport Markets top-1% PnL data):")
    lines.append("  Max 3 trades/day | 2.4x leverage max | 31h median hold")
    lines.append("  Serial escalation signal active for geo/oil/macro markets")

    await update.message.reply_text("\n".join(lines))


async def scrape_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /scrape <url> [optional question]
    Scrape any URL to clean text and summarize with Claude Haiku.
    """
    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: /scrape <url> [question]")
        return
    url      = args[0]
    question = " ".join(args[1:]) if len(args) > 1 else ""
    await update.message.reply_text(f"Scraping {url[:60]}...")
    try:
        from octo_firecrawl import scrape_summary
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        summary = await asyncio.get_event_loop().run_in_executor(
            None, scrape_summary, url, api_key
        )
        if question:
            reply = f"Q: {question}\n\n{summary}"
        else:
            reply = summary
        await update.message.reply_text(reply[:4000])
    except Exception as e:
        log.exception("scrape_command error")
        await update.message.reply_text(f"Scrape error: {e}")


async def correlations_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/correlations -- Show cross-market correlated plays from last scan."""
    try:
        from octo_boto_correlations import _load_cache, format_correlated_plays
        corr_cache = _load_cache()
        if not corr_cache:
            await update.message.reply_text("No correlation data yet. Run a boto scan first.")
            return
        lines = ["CORRELATED PLAYS (from last scan)"]
        for entry in corr_cache.values():
            plays = entry.get("plays", [])
            if plays:
                lines.append(format_correlated_plays(plays))
        if len(lines) == 1:
            lines.append("No correlations found in last scan.")
        await update.message.reply_text("\n".join(lines)[:4000])
    except Exception as e:
        await update.message.reply_text(f"Correlations error: {e}")


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
    app.add_handler(CommandHandler("send_que",    send_que_command))
    app.add_handler(CommandHandler("send_thread", send_thread_command))
    app.add_handler(CommandHandler("guide",       guide))
    app.add_handler(CommandHandler("clear",     clear))
    app.add_handler(CommandHandler("xstats",    xstats_command))
    app.add_handler(CommandHandler("chart",     chart_command))
    app.add_handler(CommandHandler("see",       see_command))
    app.add_handler(CommandHandler("scrape",    scrape_command))
    app.add_handler(CommandHandler("feeds",     feeds_command))
    app.add_handler(CommandHandler("boto",         boto_command))
    app.add_handler(CommandHandler("correlations", correlations_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    log.info("Octodamus Telegram bot surfacing...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

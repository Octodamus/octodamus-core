"""
patch_dashboard.py

Upgrades the Telegram bot with a /dashboard command that shows all 14 signal
modules. Backs up telegram_bot.py first, then patches in place.

Run: python3 /home/walli/octodamus/patch_dashboard.py
"""
import os, re, shutil, ast

BOT = "telegram_bot.py"
shutil.copy2(BOT, BOT + ".bak_dashboard")
print(f"✅ Backed up → {BOT}.bak_dashboard")

with open(BOT, "r", encoding="utf-8") as f:
    content = f.read()

# ── 1. Replace build_dashboard() with upgraded version ───────────────────────

OLD_DASHBOARD_START = "def build_dashboard() -> str:"
OLD_DASHBOARD_END   = "    return f\"\"\""

# Find and replace the entire build_dashboard function
# We'll insert new function before the old one starts and delete up to return f"""
# Strategy: find the function, replace up to and including the closing triple-quote block

NEW_DASHBOARD = '''def build_dashboard() -> str:
    now_str     = datetime.now(TZ).strftime("%a %d %b %Y %H:%M PT")
    posts_today = count_posts_today()
    queue_depth = get_queue_depth()
    recent      = get_recent_posts(3)
    errors      = get_log_errors(2)
    sched       = get_scheduler_status()

    # ── Recent posts ──────────────────────────────────────────────────────────
    if recent:
        post_lines = []
        for p in reversed(recent):
            ts   = p.get("posted_at", p.get("created_at", "?"))[:16]
            kind = p.get("type", "post")
            text = p.get("text", "")[:55]
            post_lines.append(f"  {ts} [{kind}]\\n  {text}...")
        posts_block = "\\n".join(post_lines)
    else:
        posts_block = "  No posts logged yet"

    # ── Treasury ──────────────────────────────────────────────────────────────
    try:
        from octo_treasury_balance import get_treasury_detail
        treasury_block = get_treasury_detail()
    except Exception:
        treasury_block = (
            f"  Wallet  {TREASURY_WALLET[:10]}...{TREASURY_WALLET[-4:]}\\n"
            f"  Chain   Base mainnet\\n"
            f"  $OCTO   Pending Bankr deploy"
        )

    # ── Market (OctoTV) ───────────────────────────────────────────────────────
    tv_block = "  OctoTV: unavailable"
    try:
        from octo_tradingview import run_tv_scan, format_tv_for_prompt
        _tv = run_tv_scan()
        if not _tv.get("error"):
            bias  = _tv.get("market_bias", "?").upper()
            bull  = _tv.get("bull_count", 0)
            bear  = _tv.get("bear_count", 0)
            lines = [f"  Bias: {bias} ({bull} bull / {bear} bear)"]
            for sym, conf in list(_tv.get("confluence", {}).items())[:5]:
                tfs   = _tv["symbols"].get(sym, {}).get("timeframes", {})
                price = next((tfs[t]["price"] for t in ["1D","4H","1H"] if t in tfs), "?")
                trend = tfs.get("1D", {}).get("trend", {}).get("direction", "?")
                rsi   = tfs.get("1D", {}).get("rsi", "?")
                lines.append(f"  {sym:10s} ${price:<10} {conf['direction'].upper()} rsi={rsi} {trend}")
            tv_block = "\\n".join(lines)
    except Exception as e:
        tv_block = f"  OctoTV error: {e}"

    # ── YouTube (OctoTube) ────────────────────────────────────────────────────
    tube_block = "  OctoTube: unavailable (no API key)"
    _tube_data = None
    try:
        from octo_youtube import run_youtube_scan, format_youtube_for_prompt
        _tube_data = run_youtube_scan()
        if not _tube_data.get("error"):
            cb = _tube_data.get("channel_bias", "?")
            active = [ch for ch, d in _tube_data.get("channels", {}).items() if d.get("uploads_3d", 0) > 0]
            top = _tube_data.get("trending_videos", [])[:2]
            lines = [f"  Creator bias: {cb.upper()}"]
            for ch in active[:4]:
                d = _tube_data["channels"][ch]
                lines.append(f"  {ch[:20]:20s} {d['uploads_3d']}x [{d['sentiment']}]")
            for v in top:
                lines.append(f"  🔥 {v.get('title','')[:50]}")
            tube_block = "\\n".join(lines)
    except Exception as e:
        tube_block = f"  OctoTube error: {e}"

    # ── Whisper (OctoEar) ─────────────────────────────────────────────────────
    ear_block = "  OctoEar: no transcript (run --mode whisper)"
    try:
        if _tube_data and not _tube_data.get("error"):
            from octo_whisper import run_whisper_scan, format_whisper_for_prompt
            _ear = run_whisper_scan(tube_results=_tube_data)
            if not _ear.get("error") and _ear.get("aggregate", {}).get("videos_transcribed", 0) > 0:
                agg   = _ear["aggregate"]
                sent  = agg.get("creator_sentiment", "?").upper()
                assets = ", ".join(agg.get("top_mentioned_assets", [])[:4])
                cost  = agg.get("total_cost_usd", 0)
                ear_block = f"  Sentiment: {sent} | Assets: {assets} | Cost: ${cost:.3f}"
    except Exception as e:
        ear_block = f"  OctoEar error: {e}"

    # ── Polymarket (OctoPredict) ──────────────────────────────────────────────
    predict_block = "  OctoPredict: unavailable"
    try:
        from octo_predict import run_predict_scan
        _pred = run_predict_scan()
        if not _pred.get("error"):
            lines = []
            for m in list(_pred.get("markets", {}).values())[:4]:
                q    = m.get("question", "?")[:45]
                prob = m.get("yes_probability", "?")
                lines.append(f"  {q} → {prob}%")
            predict_block = "\\n".join(lines) if lines else "  No markets loaded"
    except Exception as e:
        predict_block = f"  OctoPredict error: {e}"

    # ── GDELT (OctoGeo) ───────────────────────────────────────────────────────
    geo_block = "  OctoGeo: unavailable"
    try:
        from octo_geo import run_geo_scan
        _geo = run_geo_scan()
        if not _geo.get("error"):
            tone   = _geo.get("global_tone", "?")
            themes = ", ".join(_geo.get("top_themes", [])[:4])
            geo_block = f"  Global tone: {tone:.1f} | Themes: {themes}"
    except Exception as e:
        geo_block = f"  OctoGeo error: {e}"

    # ── Fear & Greed + Wikipedia (OctoPulse) ──────────────────────────────────
    pulse_block = "  OctoPulse: unavailable"
    try:
        from octo_pulse import run_pulse_scan
        _pulse = run_pulse_scan()
        if not _pulse.get("error"):
            fg    = _pulse.get("fear_greed", {})
            wiki  = _pulse.get("wikipedia_spikes", [])[:2]
            score = fg.get("value", "?")
            label = fg.get("label", "?")
            spike_str = ", ".join(w.get("article", "?") for w in wiki)
            pulse_block = f"  Fear/Greed: {score} ({label})"
            if spike_str:
                pulse_block += f"\\n  Wiki spikes: {spike_str}"
    except Exception as e:
        pulse_block = f"  OctoPulse error: {e}"

    # ── CoinGecko (OctoGecko) ─────────────────────────────────────────────────
    gecko_block = "  OctoGecko: unavailable"
    try:
        from octo_gecko import run_gecko_scan
        _gecko = run_gecko_scan()
        if not _gecko.get("error"):
            dom   = _gecko.get("btc_dominance", "?")
            trend = _gecko.get("market_trend", "?")
            top   = [c.get("symbol","?").upper() for c in _gecko.get("trending_coins", [])[:4]]
            gecko_block = f"  BTC dom: {dom:.1f}% | Trend: {trend} | Hot: {', '.join(top)}"
    except Exception as e:
        gecko_block = f"  OctoGecko error: {e}"

    # ── FX (OctoFX) ───────────────────────────────────────────────────────────
    fx_block = "  OctoFX: unavailable"
    try:
        from octo_fx import run_fx_scan
        _fx = run_fx_scan()
        if not _fx.get("error"):
            dxy = _fx.get("dxy_proxy", {})
            pairs = _fx.get("key_pairs", {})
            stress = _fx.get("carry_stress", "?")
            eur = pairs.get("EUR", {}).get("rate", "?")
            jpy = pairs.get("JPY", {}).get("rate", "?")
            fx_block = f"  EUR/USD: {eur} | USD/JPY: {jpy} | Carry stress: {stress}"
    except Exception as e:
        fx_block = f"  OctoFX error: {e}"

    # ── Errors ────────────────────────────────────────────────────────────────
    error_block = "\\n".join(f"  {e}" for e in errors) if errors else "  None"

    return f"""
🐙 OCTODAMUS MISSION CONTROL
{now_str}
{'='*36}

⚙️ SYSTEM
  Scheduler   {sched}
  Bitwarden   {"LOADED" if _bw_loaded else "FALLBACK (.env)"}
  Model       {CLAUDE_MODEL}

📡 X POSTING
  Today       {posts_today} / 100
  Queued      {queue_depth} pending
  Window      {get_posting_status()}

📝 RECENT POSTS
{posts_block}

📊 OCTOTV — MULTI-TIMEFRAME
{tv_block}

📺 OCTOTUBE — YOUTUBE SENTIMENT
{tube_block}

👂 OCTOEAR — TRANSCRIPT SIGNALS
{ear_block}

🔮 OCTOPREDICT — POLYMARKET
{predict_block}

🌍 OCTOGEO — GDELT GEOPOLITICAL
{geo_block}

💓 OCTOPULSE — FEAR/GREED + WIKI
{pulse_block}

🦎 OCTOGECKO — COINGECKO
{gecko_block}

💱 OCTOFX — CURRENCY
{fx_block}

💎 TREASURY
{treasury_block}

💰 REVENUE
  Site    octodamus.com (Vercel live)
  ACP     Virtuals marketplace (next)

⚠️ ERRORS
{error_block}
""".strip()

'''

# Find and replace the old build_dashboard function
# Locate it by finding the function def and replacing until the next top-level def
pattern = r'def build_dashboard\(\) -> str:.*?(?=\n^(?:async )?def |\nclass |\Z)'
match = re.search(pattern, content, re.DOTALL | re.MULTILINE)
if match:
    content = content[:match.start()] + NEW_DASHBOARD + "\n" + content[match.end():]
    print("✅ Patch 1: build_dashboard() replaced with full 14-module version")
else:
    # Fallback: just append before the command handlers
    print("⚠  build_dashboard() pattern not matched — appending new version")
    content += "\n\n" + NEW_DASHBOARD

# ── 2. Add /dashboard command handler function ────────────────────────────────
DASHBOARD_HANDLER = '''
async def dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Full mission control dashboard."""
    await update.message.reply_text("🐙 Loading dashboard... (may take 30s for live data)")
    try:
        text = build_dashboard()
    except Exception as e:
        text = f"Dashboard error: {e}"
    # Telegram max message length is 4096 chars
    for i in range(0, len(text), 4000):
        await update.message.reply_text(text[i:i+4000])

'''

# Insert before `async def handle_message`
if "async def dashboard" not in content:
    content = content.replace(
        "async def handle_message(",
        DASHBOARD_HANDLER + "async def handle_message(",
        1
    )
    print("✅ Patch 2: async def dashboard() handler added")
else:
    print("✅ Patch 2: dashboard handler already present")

# ── 3. Register /dashboard in app.add_handler block ──────────────────────────
OLD_HANDLER_BLOCK = 'app.add_handler(CommandHandler("clear",  clear))'
NEW_HANDLER_BLOCK = '''app.add_handler(CommandHandler("clear",     clear))
    app.add_handler(CommandHandler("dashboard", dashboard))'''

if '"dashboard"' not in content:
    if OLD_HANDLER_BLOCK in content:
        content = content.replace(OLD_HANDLER_BLOCK, NEW_HANDLER_BLOCK, 1)
        print("✅ Patch 3: /dashboard registered in CommandHandler")
    else:
        # Try alternate spacing
        alt = 'app.add_handler(CommandHandler("clear", clear))'
        if alt in content:
            content = content.replace(alt,
                'app.add_handler(CommandHandler("clear",     clear))\n    app.add_handler(CommandHandler("dashboard", dashboard))', 1)
            print("✅ Patch 3: /dashboard registered (alt spacing)")
        else:
            print("⚠  Patch 3: couldn't find clear handler to insert after — add manually:")
            print('    app.add_handler(CommandHandler("dashboard", dashboard))')
else:
    print("✅ Patch 3: /dashboard already registered")

# ── Write ─────────────────────────────────────────────────────────────────────
with open(BOT, "w", encoding="utf-8") as f:
    f.write(content)

try:
    ast.parse(content)
    print("✅ Syntax check passed")
except SyntaxError as e:
    print(f"❌ Syntax error line {e.lineno}: {e.msg} — restoring backup")
    shutil.copy2(BOT + ".bak_dashboard", BOT)
    exit(1)

print("""
Restart the bot:
  sudo systemctl restart octodamus-telegram.service

Then in Telegram:
  /dashboard
""")

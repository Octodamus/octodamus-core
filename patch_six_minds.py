"""
patch_six_minds.py

Adds 6 new signal modules to octodamus_runner.py:
  OctoPredict (Polymarket), OctoGeo (GDELT), OctoPulse (F&G + Wiki),
  OctoGecko (CoinGecko), OctoFX (Open Exchange Rates)

And adds OPENEXCHANGERATES_API_KEY to bitwarden.py.

Run from project directory:
    C:\Python314\python.exe patch_six_minds.py
"""

import os, shutil

# ── RUNNER PATCH ─────────────────────────────────────────────────────────────

RUNNER_PATH   = "octodamus_runner.py"
RUNNER_BACKUP = "octodamus_runner.py.bak_six"

# Anchor: insert after the OctoNews import block
NEWS_IMPORT_ANCHOR = "# ── OctoNews ─────────────────────────────────"

NEW_SIX_IMPORTS = """
# ── Six New Signal Modules ────────────────────
try:
    from octo_predict import run_prediction_scan, format_predict_for_prompt
    _PREDICT_AVAILABLE = True
except ImportError:
    _PREDICT_AVAILABLE = False

try:
    from octo_geo import run_geo_scan, format_geo_for_prompt
    _GEO_AVAILABLE = True
except ImportError:
    _GEO_AVAILABLE = False

try:
    from octo_pulse import run_pulse_scan, format_pulse_for_prompt
    _PULSE_AVAILABLE = True
except ImportError:
    _PULSE_AVAILABLE = False

try:
    from octo_gecko import run_gecko_scan, format_gecko_for_prompt
    _GECKO_AVAILABLE = True
except ImportError:
    _GECKO_AVAILABLE = False

try:
    from octo_fx import run_fx_scan, format_fx_for_prompt
    _FX_AVAILABLE = True
except ImportError:
    _FX_AVAILABLE = False

"""

# Wire into _build_four_minds_context (rename is fine — it builds all minds now)
BUILD_ANCHOR = "    return ctx.strip()"

SIX_BUILD_INJECTION = """    if _PREDICT_AVAILABLE:
        try:
            pr = run_prediction_scan()
            if pr.get("markets"):
                ctx += "\\n\\n" + format_predict_for_prompt(pr)
        except Exception as e:
            print(f"[Runner] OctoPredict in daily skipped: {e}")
    if _GEO_AVAILABLE:
        try:
            gr = run_geo_scan()
            if not gr.get("error"):
                ctx += "\\n\\n" + format_geo_for_prompt(gr)
        except Exception as e:
            print(f"[Runner] OctoGeo in daily skipped: {e}")
    if _PULSE_AVAILABLE:
        try:
            pu = run_pulse_scan()
            ctx += "\\n\\n" + format_pulse_for_prompt(pu)
        except Exception as e:
            print(f"[Runner] OctoPulse in daily skipped: {e}")
    if _GECKO_AVAILABLE:
        try:
            gk = run_gecko_scan()
            ctx += "\\n\\n" + format_gecko_for_prompt(gk)
        except Exception as e:
            print(f"[Runner] OctoGecko in daily skipped: {e}")
    if _FX_AVAILABLE:
        try:
            fx = run_fx_scan()
            if not fx.get("error"):
                ctx += "\\n\\n" + format_fx_for_prompt(fx)
        except Exception as e:
            print(f"[Runner] OctoFX in daily skipped: {e}")
    """

# New mode functions — insert before MODE: LOGIC
NEWS_MODE_ANCHOR = "# ─────────────────────────────────────────────\n# MODE: NEWS"

SIX_MODE_FUNCTIONS = '''# ─────────────────────────────────────────────
# MODE: PREDICT
# ─────────────────────────────────────────────

def mode_predict():
    if not _PREDICT_AVAILABLE:
        print("[Runner] OctoPredict not available."); return
    print("\\n[Runner] Running OctoPredict scan...")
    try:
        result = run_prediction_scan()
        if not result.get("markets"):
            print("[Runner] No Polymarket data."); return
        response = claude.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=150, system=OCTO_SYSTEM,
            messages=[{"role": "user", "content": (
                f"Prediction market oracle post for @octodamusai.\\n"
                f"{format_predict_for_prompt(result)}\\n\\n"
                "One post under 280 chars. Real money is speaking. What does the crowd know?\\n"
                "Octodamus voice — bored certainty, you already read these currents."
            )}],
        )
        post = response.content[0].text.strip()
        queue_post(post, post_type="prediction_market", priority=2)
        process_queue(max_posts=1)
        print(f"[Runner] OctoPredict post:\\n  {post}")
    except Exception as e:
        print(f"[Runner] mode_predict failed: {e}")


# ─────────────────────────────────────────────
# MODE: GEO
# ─────────────────────────────────────────────

def mode_geo():
    if not _GEO_AVAILABLE:
        print("[Runner] OctoGeo not available."); return
    print("\\n[Runner] Running OctoGeo scan...")
    try:
        result = run_geo_scan()
        regime = result.get("regime", "UNKNOWN")
        response = claude.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=150, system=OCTO_SYSTEM,
            messages=[{"role": "user", "content": (
                f"Geopolitical oracle post for @octodamusai.\\n"
                f"{format_geo_for_prompt(result)}\\n\\n"
                f"Geopolitical regime: {regime}\\n\\n"
                "One post under 280 chars. What do the global currents reveal?\\n"
                "Octodamus voice — you read the tides of nations."
            )}],
        )
        post = response.content[0].text.strip()
        queue_post(post, post_type="geopolitical", priority=4)
        process_queue(max_posts=1)
        print(f"[Runner] OctoGeo post:\\n  {post}")
    except Exception as e:
        print(f"[Runner] mode_geo failed: {e}")


# ─────────────────────────────────────────────
# MODE: PULSE
# ─────────────────────────────────────────────

def mode_pulse():
    if not _PULSE_AVAILABLE:
        print("[Runner] OctoPulse not available."); return
    print("\\n[Runner] Running OctoPulse scan...")
    try:
        result = run_pulse_scan()
        fng = result.get("fear_greed")
        fng_str = f"Fear & Greed: {fng['value']} ({fng['label']})" if fng else ""
        response = claude.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=150, system=OCTO_SYSTEM,
            messages=[{"role": "user", "content": (
                f"Attention & sentiment oracle post for @octodamusai.\\n"
                f"{format_pulse_for_prompt(result)}\\n\\n"
                f"{fng_str}\\n\\n"
                "One post under 280 chars. What is the crowd afraid of? What are they ignoring?\\n"
                "Octodamus voice — you see the attention before it becomes the news."
            )}],
        )
        post = response.content[0].text.strip()
        queue_post(post, post_type="fear_greed", priority=5)
        process_queue(max_posts=1)
        print(f"[Runner] OctoPulse post:\\n  {post}")
    except Exception as e:
        print(f"[Runner] mode_pulse failed: {e}")


# ─────────────────────────────────────────────
# MODE: GECKO
# ─────────────────────────────────────────────

def mode_gecko():
    if not _GECKO_AVAILABLE:
        print("[Runner] OctoGecko not available."); return
    print("\\n[Runner] Running OctoGecko scan...")
    try:
        result = run_gecko_scan()
        response = claude.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=150, system=OCTO_SYSTEM,
            messages=[{"role": "user", "content": (
                f"Crypto market oracle post for @octodamusai.\\n"
                f"{format_gecko_for_prompt(result)}\\n\\n"
                "One post under 280 chars. What does the full crypto ocean look like today?\\n"
                "Octodamus voice — beyond BTC, what moves beneath the surface?"
            )}],
        )
        post = response.content[0].text.strip()
        queue_post(post, post_type="crypto_intel", priority=3)
        process_queue(max_posts=1)
        print(f"[Runner] OctoGecko post:\\n  {post}")
    except Exception as e:
        print(f"[Runner] mode_gecko failed: {e}")


# ─────────────────────────────────────────────
# MODE: FX
# ─────────────────────────────────────────────

def mode_fx():
    if not _FX_AVAILABLE:
        print("[Runner] OctoFX not available."); return
    print("\\n[Runner] Running OctoFX scan...")
    try:
        result = run_fx_scan()
        if result.get("error"):
            print(f"[Runner] OctoFX error: {result['error']}"); return
        dxy = result.get("dxy_proxy")
        response = claude.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=150, system=OCTO_SYSTEM,
            messages=[{"role": "user", "content": (
                f"Dollar strength oracle post for @octodamusai.\\n"
                f"{format_fx_for_prompt(result)}\\n\\n"
                f"DXY proxy: {dxy}\\n\\n"
                "One post under 280 chars. What does dollar strength reveal about the macro current?\\n"
                "Octodamus voice — you read the reserve currency like a tide chart."
            )}],
        )
        post = response.content[0].text.strip()
        queue_post(post, post_type="fx_oracle", priority=4)
        process_queue(max_posts=1)
        print(f"[Runner] OctoFX post:\\n  {post}")
    except Exception as e:
        print(f"[Runner] mode_fx failed: {e}")


'''

ARGPARSE_ANCHOR = 'choices=["monitor", "daily", "deep_dive", "wisdom", "status", "drain", "logic", "vision", "depth", "watch", "news"],'
NEW_ARGPARSE    = 'choices=["monitor", "daily", "deep_dive", "wisdom", "status", "drain", "logic", "vision", "depth", "watch", "news", "predict", "geo", "pulse", "gecko", "fx"],'

DISPATCH_ANCHOR = '    elif args.mode == "news":\n        mode_news()'
NEW_DISPATCH    = '''    elif args.mode == "news":
        mode_news()
    elif args.mode == "predict":
        mode_predict()
    elif args.mode == "geo":
        mode_geo()
    elif args.mode == "pulse":
        mode_pulse()
    elif args.mode == "gecko":
        mode_gecko()
    elif args.mode == "fx":
        mode_fx()'''


def patch_runner():
    if not os.path.exists(RUNNER_PATH):
        print(f"ERROR: {RUNNER_PATH} not found."); return False
    with open(RUNNER_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    if "_PREDICT_AVAILABLE" in content:
        print("Runner already has six modules."); return True
    shutil.copy2(RUNNER_PATH, RUNNER_BACKUP)
    errors = []

    if NEWS_IMPORT_ANCHOR in content:
        content = content.replace(NEWS_IMPORT_ANCHOR, NEWS_IMPORT_ANCHOR + NEW_SIX_IMPORTS)
        print("R1 OK: imports added")
    else:
        errors.append("R1 FAILED — import anchor not found")

    if BUILD_ANCHOR in content:
        content = content.replace(BUILD_ANCHOR, SIX_BUILD_INJECTION + "\n    " + BUILD_ANCHOR)
        print("R2 OK: wired into daily context builder")
    else:
        print("R2 skipped — build anchor not found (non-fatal)")

    if NEWS_MODE_ANCHOR in content:
        content = content.replace(NEWS_MODE_ANCHOR, SIX_MODE_FUNCTIONS + NEWS_MODE_ANCHOR)
        print("R3 OK: mode functions added")
    else:
        errors.append("R3 FAILED — mode anchor not found")

    if ARGPARSE_ANCHOR in content:
        content = content.replace(ARGPARSE_ANCHOR, NEW_ARGPARSE)
        print("R4 OK: argparse updated")
    else:
        errors.append("R4 FAILED — argparse not found")

    if DISPATCH_ANCHOR in content:
        content = content.replace(DISPATCH_ANCHOR, NEW_DISPATCH)
        print("R5 OK: dispatch added")
    else:
        errors.append("R5 FAILED — dispatch not found")

    if errors:
        print("\nFATAL — restoring backup:")
        for e in errors: print(f"  {e}")
        shutil.copy2(RUNNER_BACKUP, RUNNER_PATH)
        return False

    with open(RUNNER_PATH, "w", encoding="utf-8") as f:
        f.write(content)
    print("Runner patched.")
    return True


# ── BITWARDEN PATCH ───────────────────────────────────────────────────────────

BW_PATH   = "bitwarden.py"
BW_BACKUP = "bitwarden.py.bak_six"

OLD_FRED = '    "AGENT - Octodamus - FRED API":                      "FRED_API_KEY",'
NEW_FRED = '''    "AGENT - Octodamus - FRED API":                      "FRED_API_KEY",
    "AGENT - Octodamus - Open Exchange Rates":           "OPENEXCHANGERATES_API_KEY",'''


def patch_bitwarden():
    if not os.path.exists(BW_PATH):
        print(f"ERROR: {BW_PATH} not found."); return False
    with open(BW_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    if "OPENEXCHANGERATES_API_KEY" in content:
        print("bitwarden.py already has OXR key."); return True
    shutil.copy2(BW_PATH, BW_BACKUP)
    if OLD_FRED in content:
        content = content.replace(OLD_FRED, NEW_FRED)
        print("BW1 OK: Open Exchange Rates key added")
    else:
        print("BW1 skipped — add manually: 'AGENT - Octodamus - Open Exchange Rates' -> OPENEXCHANGERATES_API_KEY")
    with open(BW_PATH, "w", encoding="utf-8") as f:
        f.write(content)
    return True


# ── TELEGRAM PATCH ────────────────────────────────────────────────────────────

BOT_PATH   = "telegram_bot.py"
BOT_BACKUP = "telegram_bot.py.bak_six"

OLD_NEWS_LINE = "- OctoNews live - NewsAPI headlines for NVDA, TSLA, AAPL, BTC, ETH, SPY with sentiment scoring"
NEW_NEWS_LINE = """- OctoNews live - NewsAPI headlines for NVDA, TSLA, AAPL, BTC, ETH, SPY with sentiment scoring
- OctoPredict live - Polymarket prediction markets: Fed rate odds, BTC price markets, geopolitical probabilities
- OctoGeo live - GDELT global news tone across 100 languages, conflict and macro themes
- OctoPulse live - Fear & Greed Index (Alternative.me) + Wikipedia attention spike detection
- OctoGecko live - CoinGecko full crypto market: BTC dominance, trending coins, gainers/losers
- OctoFX live - Open Exchange Rates: dollar strength proxy, JPY carry, EM currency stress"""


def patch_telegram():
    if not os.path.exists(BOT_PATH):
        print(f"ERROR: {BOT_PATH} not found."); return False
    with open(BOT_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    if "OctoPredict live" in content:
        print("Telegram already patched."); return True
    shutil.copy2(BOT_PATH, BOT_BACKUP)
    if OLD_NEWS_LINE in content:
        content = content.replace(OLD_NEWS_LINE, NEW_NEWS_LINE)
        print("TG1 OK: telegram system prompt updated")
    else:
        print("TG1 skipped — update telegram manually")
    with open(BOT_PATH, "w", encoding="utf-8") as f:
        f.write(content)
    return True


if __name__ == "__main__":
    print("── Patching runner ──────────────────────")
    patch_runner()
    print("\n── Patching bitwarden ───────────────────")
    patch_bitwarden()
    print("\n── Patching telegram ────────────────────")
    patch_telegram()
    print("""
Done. Next steps:
  1. Copy all octo_*.py files to C:\\Users\\walli\\octodamus\\
  2. Open Exchange Rates free key: openexchangerates.org/signup/free
     Add to Bitwarden: 'AGENT - Octodamus - Open Exchange Rates'
  3. Test each module:
     C:\\Python314\\python.exe octodamus_runner.py --mode predict
     C:\\Python314\\python.exe octodamus_runner.py --mode geo
     C:\\Python314\\python.exe octodamus_runner.py --mode pulse
     C:\\Python314\\python.exe octodamus_runner.py --mode gecko
     C:\\Python314\\python.exe octodamus_runner.py --mode fx
  4. Restart telegram_bot.py
""")

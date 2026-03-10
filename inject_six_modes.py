"""
inject_six_modes.py
Directly injects 6 signal mode functions + dispatch into octodamus_runner.py.
Works regardless of prior patch history.

Run from: /mnt/c/Users/walli/octodamus/
    python3 inject_six_modes.py
"""

import shutil

RUNNER = "octodamus_runner.py"
BACKUP = "octodamus_runner.py.bak_pre_six"

shutil.copy2(RUNNER, BACKUP)
print(f"✅ Backed up {RUNNER} → {BACKUP}")

content = open(RUNNER, encoding="utf-8").read()

# ── Check not already patched ─────────────────────────────────────────────────
if "mode_predict" in content:
    print("✅ Six modes already present in runner. Nothing to do.")
    exit(0)

# ── 1. Inject mode functions before if __name__ ───────────────────────────────
SIX_MODES = '''
# ─────────────────────────────────────────────
# SIGNAL MODES — Six Intelligence Arms
# ─────────────────────────────────────────────

_PREDICT_AVAILABLE = False
_GEO_AVAILABLE     = False
_PULSE_AVAILABLE   = False
_GECKO_AVAILABLE   = False
_FX_AVAILABLE      = False
_NEWS_AVAILABLE    = False

try:
    from octo_predict import run_prediction_scan, format_predict_for_prompt
    _PREDICT_AVAILABLE = True
except ImportError:
    print("[Runner] octo_predict not available")

try:
    from octo_geo import run_geo_scan, format_geo_for_prompt
    _GEO_AVAILABLE = True
except ImportError:
    print("[Runner] octo_geo not available")

try:
    from octo_pulse import run_pulse_scan, format_pulse_for_prompt
    _PULSE_AVAILABLE = True
except ImportError:
    print("[Runner] octo_pulse not available")

try:
    from octo_gecko import run_gecko_scan, format_gecko_for_prompt
    _GECKO_AVAILABLE = True
except ImportError:
    print("[Runner] octo_gecko not available")

try:
    from octo_fx import run_fx_scan, format_fx_for_prompt
    _FX_AVAILABLE = True
except ImportError:
    print("[Runner] octo_fx not available")

try:
    from octo_news import run_news_scan, format_news_for_prompt
    _NEWS_AVAILABLE = True
except ImportError:
    print("[Runner] octo_news not available")


def mode_predict() -> None:
    if not _PREDICT_AVAILABLE:
        print("[Runner] OctoPredict not available."); return
    print("\\n[Runner] 🔮 Running OctoPredict scan...")
    try:
        result = run_prediction_scan()
        if not result.get("markets"):
            print("[Runner] No Polymarket data."); return
        context = format_predict_for_prompt(result)
        response = claude.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=150, system=OCTO_SYSTEM,
            messages=[{"role": "user", "content": (
                f"{random.choice(_VOICE_INSTRUCTIONS)}\\n"
                f"Prediction market signals from Polymarket:\\n{context}\\n\\n"
                "One post under 280 chars. No hashtags.\\n"
                "Lead with a SPECIFIC probability or market name from the data.\\n"
                "Say what the odds imply that nobody wants to admit out loud."
            )}],
        )
        post = response.content[0].text.strip()
        queue_post(post, post_type="prediction_market", priority=2)
        process_queue(max_posts=1)
        print(f"[Runner] OctoPredict post:\\n  {post}")
    except Exception as e:
        print(f"[Runner] mode_predict failed: {e}")


def mode_geo() -> None:
    if not _GEO_AVAILABLE:
        print("[Runner] OctoGeo not available."); return
    print("\\n[Runner] 🌍 Running OctoGeo scan...")
    try:
        result = run_geo_scan()
        regime  = result.get("regime", "UNKNOWN")
        context = format_geo_for_prompt(result)
        response = claude.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=150, system=OCTO_SYSTEM,
            messages=[{"role": "user", "content": (
                f"{random.choice(_VOICE_INSTRUCTIONS)}\\n"
                f"Geopolitical signals from GDELT:\\n{context}\\n\\n"
                f"Geopolitical regime: {regime}\\n\\n"
                "One post under 280 chars. No hashtags.\\n"
                "Lead with the SPECIFIC region, country, or tone score from the data.\\n"
                "Name what the geopolitical shift means for markets right now."
            )}],
        )
        post = response.content[0].text.strip()
        queue_post(post, post_type="geopolitical", priority=4)
        process_queue(max_posts=1)
        print(f"[Runner] OctoGeo post:\\n  {post}")
    except Exception as e:
        print(f"[Runner] mode_geo failed: {e}")


def mode_pulse() -> None:
    if not _PULSE_AVAILABLE:
        print("[Runner] OctoPulse not available."); return
    print("\\n[Runner] 💓 Running OctoPulse scan...")
    try:
        result  = run_pulse_scan()
        context = format_pulse_for_prompt(result)
        fng     = result.get("fear_greed")
        fng_str = f"Fear & Greed: {fng['value']} ({fng['label']})" if fng else ""
        response = claude.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=150, system=OCTO_SYSTEM,
            messages=[{"role": "user", "content": (
                f"{random.choice(_VOICE_INSTRUCTIONS)}\\n"
                f"Market sentiment signals:\\n{context}\\n\\n"
                f"{fng_str}\\n\\n"
                "One post under 280 chars. No hashtags.\\n"
                "Lead with the SPECIFIC Fear & Greed number or Wikipedia trend.\\n"
                "State the contrarian implication — what does this reading actually predict?"
            )}],
        )
        post = response.content[0].text.strip()
        queue_post(post, post_type="fear_greed", priority=5)
        process_queue(max_posts=1)
        print(f"[Runner] OctoPulse post:\\n  {post}")
    except Exception as e:
        print(f"[Runner] mode_pulse failed: {e}")


def mode_gecko() -> None:
    if not _GECKO_AVAILABLE:
        print("[Runner] OctoGecko not available."); return
    print("\\n[Runner] 🦎 Running OctoGecko scan...")
    try:
        result  = run_gecko_scan()
        context = format_gecko_for_prompt(result)
        response = claude.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=150, system=OCTO_SYSTEM,
            messages=[{"role": "user", "content": (
                f"{random.choice(_VOICE_INSTRUCTIONS)}\\n"
                f"CoinGecko crypto market signals:\\n{context}\\n\\n"
                "One post under 280 chars. No hashtags.\\n"
                "Lead with a SPECIFIC price, percentage move, or coin name from the data.\\n"
                "Say what crypto is doing that equity traders haven't clocked yet."
            )}],
        )
        post = response.content[0].text.strip()
        queue_post(post, post_type="crypto_intel", priority=3)
        process_queue(max_posts=1)
        print(f"[Runner] OctoGecko post:\\n  {post}")
    except Exception as e:
        print(f"[Runner] mode_gecko failed: {e}")


def mode_fx() -> None:
    if not _FX_AVAILABLE:
        print("[Runner] OctoFX not available."); return
    print("\\n[Runner] 💱 Running OctoFX scan...")
    try:
        result = run_fx_scan()
        if result.get("error"):
            print(f"[Runner] OctoFX error: {result['error']}"); return
        context = format_fx_for_prompt(result)
        dxy     = result.get("dxy_proxy")
        response = claude.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=150, system=OCTO_SYSTEM,
            messages=[{"role": "user", "content": (
                f"{random.choice(_VOICE_INSTRUCTIONS)}\\n"
                f"FX and currency market signals:\\n{context}\\n\\n"
                f"DXY proxy: {dxy}\\n\\n"
                "One post under 280 chars. No hashtags.\\n"
                "Lead with a SPECIFIC currency pair or DXY move from the data.\\n"
                "Name what the dollar is signaling that most people are too distracted to see."
            )}],
        )
        post = response.content[0].text.strip()
        queue_post(post, post_type="fx_oracle", priority=4)
        process_queue(max_posts=1)
        print(f"[Runner] OctoFX post:\\n  {post}")
    except Exception as e:
        print(f"[Runner] mode_fx failed: {e}")


def mode_news() -> None:
    if not _NEWS_AVAILABLE:
        print("[Runner] OctoNews not available."); return
    print("\\n[Runner] 📰 Running OctoNews scan...")
    try:
        result  = run_news_scan()
        context = format_news_for_prompt(result)
        if not context:
            print("[Runner] No news data."); return
        response = claude.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=150, system=OCTO_SYSTEM,
            messages=[{"role": "user", "content": (
                f"{random.choice(_VOICE_INSTRUCTIONS)}\\n"
                f"Latest market headlines:\\n{context}\\n\\n"
                "One post under 280 chars. No hashtags.\\n"
                "Pick the ONE headline that reveals something not yet priced in.\\n"
                "Name the company, person, or number. Don't summarize — interpret."
            )}],
        )
        post = response.content[0].text.strip()
        queue_post(post, post_type="news_oracle", priority=3)
        process_queue(max_posts=1)
        print(f"[Runner] OctoNews post:\\n  {post}")
    except Exception as e:
        print(f"[Runner] mode_news failed: {e}")

'''

# Inject before if __name__
MAIN_ANCHOR = 'if __name__ == "__main__":'
if MAIN_ANCHOR not in content:
    print("❌ Cannot find if __name__ anchor. Aborting.")
    exit(1)

content = content.replace(MAIN_ANCHOR, SIX_MODES + MAIN_ANCHOR)
print("✅ Six mode functions injected")

# ── 2. Update argparse choices ────────────────────────────────────────────────
old_choices = '        choices=["monitor", "daily", "deep_dive", "wisdom", "status", "drain", "alert"],'
new_choices  = '        choices=["monitor", "daily", "deep_dive", "wisdom", "status", "drain", "alert", "predict", "geo", "pulse", "gecko", "fx", "news"],'

if old_choices in content:
    content = content.replace(old_choices, new_choices)
    print("✅ Argparse choices updated")
else:
    print("⚠️  Argparse choices not found — update manually")

# ── 3. Add dispatch cases ─────────────────────────────────────────────────────
old_dispatch = '    elif args.mode == "alert":\n        from octo_alert import run_alert_scan\n        run_alert_scan(secrets=secrets, claude_client=claude)'
new_dispatch  = (
    '    elif args.mode == "alert":\n        from octo_alert import run_alert_scan\n        run_alert_scan(secrets=secrets, claude_client=claude)\n'
    '    elif args.mode == "predict":\n        mode_predict()\n'
    '    elif args.mode == "geo":\n        mode_geo()\n'
    '    elif args.mode == "pulse":\n        mode_pulse()\n'
    '    elif args.mode == "gecko":\n        mode_gecko()\n'
    '    elif args.mode == "fx":\n        mode_fx()\n'
    '    elif args.mode == "news":\n        mode_news()'
)

if old_dispatch in content:
    content = content.replace(old_dispatch, new_dispatch)
    print("✅ Dispatch cases added")
else:
    print("⚠️  Dispatch anchor not found — update manually")

open(RUNNER, "w", encoding="utf-8").write(content)
print(f"\n🐙 Done. Six signal modes live in {RUNNER}")
print("Test with: python3 octodamus_runner.py --mode gecko")

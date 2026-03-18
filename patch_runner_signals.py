"""
patch_runner_signals.py
Wires 6 signal modules into octodamus_runner.py:
  predict, gecko, pulse, fx, geo, news

Also patches bitwarden.py to load OPENEXCHANGERATES_API_KEY.

Run from: /home/walli/octodamus/
    python3 patch_runner_signals.py
"""

import os
import shutil

RUNNER = "octodamus_runner.py"
BW_FILE = "bitwarden.py"
BACKUP = "octodamus_runner.py.bak_pre_signals"

# ── Backup ────────────────────────────────────────────────────────────────────

shutil.copy2(RUNNER, BACKUP)
print(f"✅ Backed up {RUNNER} → {BACKUP}")

with open(RUNNER, "r", encoding="utf-8") as f:
    content = f.read()

# ── 1. Add 6 mode functions before ENTRY POINT ───────────────────────────────

NEW_MODES = '''
# ─────────────────────────────────────────────
# MODE: PREDICT — Polymarket prediction markets
# ─────────────────────────────────────────────

def mode_predict() -> None:
    """Fetch Polymarket odds and generate an oracle prediction post."""
    print(f"\\n[Runner] 🔮 OctoPredict scanning prediction markets...")
    try:
        from octo_predict import run_prediction_scan, format_predict_for_prompt
        result = run_prediction_scan()
        context = format_predict_for_prompt(result)
        if not context:
            print("[Runner] OctoPredict: no signal data available.")
            return
        response = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            system=OCTO_SYSTEM,
            messages=[{
                "role": "user",
                "content": (
                    "Prediction market signals from Polymarket:\\n"
                    f"{context}\\n\\n"
                    "Generate one oracle post under 280 chars. "
                    "Interpret the odds as signals — what does the market know? "
                    "Octodamus voice. No hashtags."
                ),
            }],
        )
        post = response.content[0].text.strip()
        queue_post(post, post_type="predict", priority=3)
        process_queue(max_posts=1)
        print(f"[Runner] Predict post:\\n  {post}")
    except Exception as e:
        print(f"[Runner] mode_predict failed: {e}")


# ─────────────────────────────────────────────
# MODE: GECKO — CoinGecko crypto signals
# ─────────────────────────────────────────────

def mode_gecko() -> None:
    """Fetch CoinGecko crypto data and generate an oracle crypto post."""
    print(f"\\n[Runner] 🦎 OctoGecko scanning crypto markets...")
    try:
        from octo_gecko import run_gecko_scan, format_gecko_for_prompt
        result = run_gecko_scan()
        context = format_gecko_for_prompt(result)
        if not context:
            print("[Runner] OctoGecko: no signal data available.")
            return
        response = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            system=OCTO_SYSTEM,
            messages=[{
                "role": "user",
                "content": (
                    "CoinGecko crypto market signals:\\n"
                    f"{context}\\n\\n"
                    "Generate one oracle post under 280 chars. "
                    "Read the crypto currents — what is the deep telling you? "
                    "Octodamus voice. No hashtags."
                ),
            }],
        )
        post = response.content[0].text.strip()
        queue_post(post, post_type="gecko", priority=3)
        process_queue(max_posts=1)
        print(f"[Runner] Gecko post:\\n  {post}")
    except Exception as e:
        print(f"[Runner] mode_gecko failed: {e}")


# ─────────────────────────────────────────────
# MODE: PULSE — Fear & Greed + Wikipedia trends
# ─────────────────────────────────────────────

def mode_pulse() -> None:
    """Fetch Fear & Greed index and Wikipedia attention spikes."""
    print(f"\\n[Runner] 💓 OctoPulse scanning sentiment...")
    try:
        from octo_pulse import run_pulse_scan, format_pulse_for_prompt
        result = run_pulse_scan()
        context = format_pulse_for_prompt(result)
        if not context:
            print("[Runner] OctoPulse: no signal data available.")
            return
        response = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            system=OCTO_SYSTEM,
            messages=[{
                "role": "user",
                "content": (
                    "Market sentiment signals:\\n"
                    f"{context}\\n\\n"
                    "Generate one oracle post under 280 chars. "
                    "What does the crowd's fear or greed reveal about what comes next? "
                    "Octodamus voice. No hashtags."
                ),
            }],
        )
        post = response.content[0].text.strip()
        queue_post(post, post_type="pulse", priority=4)
        process_queue(max_posts=1)
        print(f"[Runner] Pulse post:\\n  {post}")
    except Exception as e:
        print(f"[Runner] mode_pulse failed: {e}")


# ─────────────────────────────────────────────
# MODE: FX — Open Exchange Rates currency data
# ─────────────────────────────────────────────

def mode_fx() -> None:
    """Fetch FX rates and generate a macro currency oracle post."""
    print(f"\\n[Runner] 💱 OctoFX scanning currency markets...")
    try:
        from octo_fx import run_fx_scan, format_fx_for_prompt
        result = run_fx_scan()
        if result.get("error"):
            print(f"[Runner] OctoFX error: {result['error']}")
            return
        context = format_fx_for_prompt(result)
        if not context:
            print("[Runner] OctoFX: no signal data available.")
            return
        response = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            system=OCTO_SYSTEM,
            messages=[{
                "role": "user",
                "content": (
                    "FX and currency market signals:\\n"
                    f"{context}\\n\\n"
                    "Generate one oracle post under 280 chars. "
                    "Currency flows carry macro truth — what do they reveal? "
                    "Octodamus voice. No hashtags."
                ),
            }],
        )
        post = response.content[0].text.strip()
        queue_post(post, post_type="fx", priority=4)
        process_queue(max_posts=1)
        print(f"[Runner] FX post:\\n  {post}")
    except Exception as e:
        print(f"[Runner] mode_fx failed: {e}")


# ─────────────────────────────────────────────
# MODE: GEO — GDELT geopolitical signals
# ─────────────────────────────────────────────

def mode_geo() -> None:
    """Fetch GDELT geopolitical signals and generate a macro oracle post."""
    print(f"\\n[Runner] 🌍 OctoGeo scanning geopolitical signals...")
    try:
        from octo_geo import run_geo_scan, format_geo_for_prompt
        result = run_geo_scan()
        context = format_geo_for_prompt(result)
        if not context:
            print("[Runner] OctoGeo: no signal data available.")
            return
        response = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            system=OCTO_SYSTEM,
            messages=[{
                "role": "user",
                "content": (
                    "Geopolitical signals from GDELT:\\n"
                    f"{context}\\n\\n"
                    "Generate one oracle post under 280 chars. "
                    "Geopolitics is the tide beneath the markets — what is it saying? "
                    "Octodamus voice. No hashtags."
                ),
            }],
        )
        post = response.content[0].text.strip()
        queue_post(post, post_type="geo", priority=5)
        process_queue(max_posts=1)
        print(f"[Runner] Geo post:\\n  {post}")
    except Exception as e:
        print(f"[Runner] mode_geo failed: {e}")


# ─────────────────────────────────────────────
# MODE: NEWS — Financial Datasets news headlines
# ─────────────────────────────────────────────

def mode_news() -> None:
    """Fetch news headlines and generate a news oracle post."""
    print(f"\\n[Runner] 📰 OctoNews scanning headlines...")
    try:
        from octo_news import run_news_scan, format_news_for_prompt
        result = run_news_scan()
        if result.get("error"):
            print(f"[Runner] OctoNews error: {result['error']}")
            return
        context = format_news_for_prompt(result)
        if not context:
            print("[Runner] OctoNews: no headlines available.")
            return
        response = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            system=OCTO_SYSTEM,
            messages=[{
                "role": "user",
                "content": (
                    "Latest market headlines:\\n"
                    f"{context}\\n\\n"
                    "Generate one oracle post under 280 chars. "
                    "The news is noise. The signal is what the noise is hiding. "
                    "Octodamus voice. No hashtags."
                ),
            }],
        )
        post = response.content[0].text.strip()
        queue_post(post, post_type="news", priority=4)
        process_queue(max_posts=1)
        print(f"[Runner] News post:\\n  {post}")
    except Exception as e:
        print(f"[Runner] mode_news failed: {e}")

'''

ENTRY_ANCHOR = "# ─────────────────────────────────────────────\n# ENTRY POINT"

if "mode_predict" in content:
    print("⚠️  Signal modes already present in runner — skipping mode injection.")
elif ENTRY_ANCHOR not in content:
    print("❌ Could not find ENTRY POINT anchor in runner. Patch aborted.")
    exit(1)
else:
    content = content.replace(ENTRY_ANCHOR, NEW_MODES + ENTRY_ANCHOR)
    print("✅ Injected 6 signal mode functions into runner.")


# ── 2. Update argparse choices ────────────────────────────────────────────────

OLD_CHOICES = 'choices=["monitor", "daily", "deep_dive", "wisdom", "status", "drain"]'
NEW_CHOICES = 'choices=["monitor", "daily", "deep_dive", "wisdom", "status", "drain", "predict", "gecko", "pulse", "fx", "geo", "news"]'

if OLD_CHOICES in content:
    content = content.replace(OLD_CHOICES, NEW_CHOICES)
    print("✅ Updated argparse choices to include 6 new modes.")
elif NEW_CHOICES in content:
    print("⚠️  Argparse choices already updated — skipping.")
else:
    print("❌ Could not find argparse choices line. Check runner manually.")


# ── 3. Add elif dispatcher branches ──────────────────────────────────────────

OLD_DRAIN = '    elif args.mode == "drain":\n        posted = process_queue(max_posts=10)\n        print(f"[Runner] Drained {posted} posts from queue.")'

NEW_DRAIN = '''    elif args.mode == "drain":
        posted = process_queue(max_posts=10)
        print(f"[Runner] Drained {posted} posts from queue.")
    elif args.mode == "predict":
        mode_predict()
    elif args.mode == "gecko":
        mode_gecko()
    elif args.mode == "pulse":
        mode_pulse()
    elif args.mode == "fx":
        mode_fx()
    elif args.mode == "geo":
        mode_geo()
    elif args.mode == "news":
        mode_news()'''

if 'args.mode == "predict"' in content:
    print("⚠️  Dispatcher branches already present — skipping.")
elif OLD_DRAIN in content:
    content = content.replace(OLD_DRAIN, NEW_DRAIN)
    print("✅ Added 6 new mode dispatcher branches.")
else:
    print("❌ Could not find drain dispatcher anchor. Check runner manually.")


# ── 4. Write patched runner ───────────────────────────────────────────────────

with open(RUNNER, "w", encoding="utf-8") as f:
    f.write(content)
print(f"✅ Written patched {RUNNER}")


# ── 5. Patch bitwarden.py for OctoFX key ─────────────────────────────────────

with open(BW_FILE, "r", encoding="utf-8") as f:
    bw_content = f.read()

if "OPENEXCHANGERATES_API_KEY" in bw_content:
    print("✅ bitwarden.py already has OPENEXCHANGERATES_API_KEY — skipping.")
else:
    OLD_BW = '"AGENT - Octodamus - OpenAI Whisper"'
    NEW_BW = '"AGENT - Octodamus - OpenAI Whisper"'

    # Find the Whisper line in the SECRETS dict and add OctoFX after it
    old_whisper_map = '"AGENT - Octodamus - OpenAI Whisper":               "OPENAI_API_KEY",'
    new_whisper_map = (
        '"AGENT - Octodamus - OpenAI Whisper":               "OPENAI_API_KEY",\n'
        '    # ── Signal modules ───────────────────────\n'
        '    "AGENT - Octodamus - Open Exchange Rates":       "OPENEXCHANGERATES_API_KEY",'
    )
    if old_whisper_map in bw_content:
        bw_content = bw_content.replace(old_whisper_map, new_whisper_map)
        with open(BW_FILE, "w", encoding="utf-8") as f:
            f.write(bw_content)
        print("✅ Patched bitwarden.py — added OPENEXCHANGERATES_API_KEY")
    else:
        print("⚠️  Could not find Whisper mapping line in bitwarden.py — add manually:")
        print('    "AGENT - Octodamus - Open Exchange Rates": "OPENEXCHANGERATES_API_KEY",')


# ── 6. Summary ────────────────────────────────────────────────────────────────

print()
print("═══════════════════════════════════════════")
print("  PATCH COMPLETE — next steps:")
print("═══════════════════════════════════════════")
print("1. export BW_SESSION=$(cat /home/walli/.bw_session)")
print("2. python3 octodamus_runner.py --mode status")
print("3. Test each new mode:")
print("   python3 octodamus_runner.py --mode predict")
print("   python3 octodamus_runner.py --mode gecko")
print("   python3 octodamus_runner.py --mode pulse")
print("   python3 octodamus_runner.py --mode fx")
print("   python3 octodamus_runner.py --mode geo")
print("   python3 octodamus_runner.py --mode news")

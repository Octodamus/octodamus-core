"""
patch_signal_voices_v2.py
Patches the 6 signal modes in octodamus_runner.py with rotating voice.
Uses exact strings from the live file.

Run from: /mnt/c/Users/walli/octodamus/
    python3 patch_signal_voices_v2.py
"""

import shutil

RUNNER = "octodamus_runner.py"
BACKUP = "octodamus_runner.py.bak_pre_voice2"

shutil.copy2(RUNNER, BACKUP)
print(f"✅ Backed up {RUNNER} → {BACKUP}")

content = open(RUNNER, encoding="utf-8").read()

if "_VOICE_INSTRUCTIONS" not in content:
    print("❌ _VOICE_INSTRUCTIONS not found — wrong runner file. Aborting.")
    exit(1)

patches = [

    # ── PREDICT ──────────────────────────────────────────────────────────────
    (
        'messages=[{"role": "user", "content": (\n'
        '                f"Prediction market oracle post for @octodamusai.\\n"\n'
        '                f"{format_predict_for_prompt(result)}\\n\\n"\n'
        '                "One post under 280 chars. Real money is speaking. What does the crowd know?\\n"\n'
        '                "Octodamus voice — bored certainty, you already read these currents."\n'
        '            )}],',

        'messages=[{"role": "user", "content": (\n'
        '                f"{random.choice(_VOICE_INSTRUCTIONS)}\\n"\n'
        '                f"Prediction market oracle post for @octodamusai.\\n"\n'
        '                f"{format_predict_for_prompt(result)}\\n\\n"\n'
        '                "One post under 280 chars. No hashtags.\\n"\n'
        '                "Lead with a SPECIFIC probability or market name from the data.\\n"\n'
        '                "Say what the odds imply that nobody wants to admit out loud."\n'
        '            )}],',
        "predict"
    ),

    # ── GEO ──────────────────────────────────────────────────────────────────
    (
        'messages=[{"role": "user", "content": (\n'
        '                f"Geopolitical oracle post for @octodamusai.\\n"\n'
        '                f"{format_geo_for_prompt(result)}\\n\\n"\n'
        '                f"Geopolitical regime: {regime}\\n\\n"\n'
        '                "One post under 280 chars. What do the global currents reveal?\\n"\n'
        '                "Octodamus voice — you read the tides of nations."\n'
        '            )}],',

        'messages=[{"role": "user", "content": (\n'
        '                f"{random.choice(_VOICE_INSTRUCTIONS)}\\n"\n'
        '                f"Geopolitical oracle post for @octodamusai.\\n"\n'
        '                f"{format_geo_for_prompt(result)}\\n\\n"\n'
        '                f"Geopolitical regime: {regime}\\n\\n"\n'
        '                "One post under 280 chars. No hashtags.\\n"\n'
        '                "Lead with the SPECIFIC region, country, or tone score from the data.\\n"\n'
        '                "Name what the geopolitical shift means for markets right now."\n'
        '            )}],',
        "geo"
    ),

    # ── PULSE ─────────────────────────────────────────────────────────────────
    (
        'messages=[{"role": "user", "content": (\n'
        '                f"Attention & sentiment oracle post for @octodamusai.\\n"\n'
        '                f"{format_pulse_for_prompt(result)}\\n\\n"\n'
        '                f"{fng_str}\\n\\n"\n'
        '                "One post under 280 chars. What is the crowd afraid of? What are they ignoring?\\n"\n'
        '                "Octodamus voice — you see the attention before it becomes the news."\n'
        '            )}],',

        'messages=[{"role": "user", "content": (\n'
        '                f"{random.choice(_VOICE_INSTRUCTIONS)}\\n"\n'
        '                f"Attention & sentiment oracle post for @octodamusai.\\n"\n'
        '                f"{format_pulse_for_prompt(result)}\\n\\n"\n'
        '                f"{fng_str}\\n\\n"\n'
        '                "One post under 280 chars. No hashtags.\\n"\n'
        '                "Lead with the SPECIFIC Fear & Greed number or Wikipedia trend.\\n"\n'
        '                "State the contrarian implication — what does this reading actually predict?"\n'
        '            )}],',
        "pulse"
    ),

    # ── GECKO ─────────────────────────────────────────────────────────────────
    (
        'messages=[{"role": "user", "content": (\n'
        '                f"Crypto market oracle post for @octodamusai.\\n"\n'
        '                f"{format_gecko_for_prompt(result)}\\n\\n"\n'
        '                "One post under 280 chars. What does the full crypto ocean look like today?\\n"\n'
        '                "Octodamus voice — beyond BTC, what moves beneath the surface?"\n'
        '            )}],',

        'messages=[{"role": "user", "content": (\n'
        '                f"{random.choice(_VOICE_INSTRUCTIONS)}\\n"\n'
        '                f"Crypto market oracle post for @octodamusai.\\n"\n'
        '                f"{format_gecko_for_prompt(result)}\\n\\n"\n'
        '                "One post under 280 chars. No hashtags.\\n"\n'
        '                "Lead with a SPECIFIC price, percentage move, or coin name from the data.\\n"\n'
        '                "Say what crypto is doing that equity traders haven\'t clocked yet."\n'
        '            )}],',
        "gecko"
    ),

    # ── FX ────────────────────────────────────────────────────────────────────
    (
        'messages=[{"role": "user", "content": (\n'
        '                f"Dollar strength oracle post for @octodamusai.\\n"\n'
        '                f"{format_fx_for_prompt(result)}\\n\\n"\n'
        '                f"DXY proxy: {dxy}\\n\\n"\n'
        '                "One post under 280 chars. What does dollar strength reveal about the macro current?\\n"\n'
        '                "Octodamus voice — you read the reserve currency like a tide chart."\n'
        '            )}],',

        'messages=[{"role": "user", "content": (\n'
        '                f"{random.choice(_VOICE_INSTRUCTIONS)}\\n"\n'
        '                f"Dollar strength oracle post for @octodamusai.\\n"\n'
        '                f"{format_fx_for_prompt(result)}\\n\\n"\n'
        '                f"DXY proxy: {dxy}\\n\\n"\n'
        '                "One post under 280 chars. No hashtags.\\n"\n'
        '                "Lead with a SPECIFIC currency pair or DXY move from the data.\\n"\n'
        '                "Name what the dollar is signaling that most people are too distracted to see."\n'
        '            )}],',
        "fx"
    ),
]

# ── Apply patches ─────────────────────────────────────────────────────────────
success = 0
for old, new, mode in patches:
    if old in content:
        content = content.replace(old, new)
        print(f"✅ {mode:10s} — voice rotation applied")
        success += 1
    else:
        # Try to find partial match to diagnose
        key = old.split("\\n")[0][:60]
        if key in content:
            print(f"⚠️  {mode:10s} — partial match found, whitespace diff likely")
        else:
            print(f"❌ {mode:10s} — no match found")

# ── Also patch mode_news if it exists ────────────────────────────────────────
if 'mode_news' in content:
    old_news = (
        '"One post under 280 chars. The news is noise — find the signal.\\n"\n'
        '                "Octodamus voice — name the thing the headline is hiding."'
    )
    new_news = (
        'f"{random.choice(_VOICE_INSTRUCTIONS)}\\n"\n'
        '                "One post under 280 chars. No hashtags.\\n"\n'
        '                "Pick the ONE headline that reveals something not yet priced in.\\n"\n'
        '                "Name the company, person, or number. Don\'t summarize — interpret."'
    )
    if old_news in content:
        content = content.replace(old_news, new_news)
        print(f"✅ {'news':10s} — voice rotation applied")
        success += 1
    else:
        print(f"⚠️  {'news':10s} — mode exists but prompt string didn't match")

open(RUNNER, "w", encoding="utf-8").write(content)
print(f"\n{'─'*50}")
print(f"Patched {success} signal modes.")
print(f"Backup: {BACKUP}")

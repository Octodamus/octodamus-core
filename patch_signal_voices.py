"""
patch_signal_voices.py
Adds rotating voice instructions to all 6 signal mode prompts in octodamus_runner.py.
Modes patched: predict, gecko, pulse, fx, geo, news

Run from: /mnt/c/Users/walli/octodamus/
    python3 patch_signal_voices.py
"""

import shutil

RUNNER = "octodamus_runner.py"
BACKUP = "octodamus_runner.py.bak_pre_voice"

shutil.copy2(RUNNER, BACKUP)
print(f"✅ Backed up {RUNNER} → {BACKUP}")

content = open(RUNNER).read()

# ── Verify _VOICE_INSTRUCTIONS exists ────────────────────────────────────────
if "_VOICE_INSTRUCTIONS" not in content:
    print("❌ _VOICE_INSTRUCTIONS not found in runner — make sure octodamus_runner.py")
    print("   is the updated version from this session. Aborting.")
    exit(1)

print("✅ _VOICE_INSTRUCTIONS confirmed present")

# ── Define all 6 replacements ────────────────────────────────────────────────
# Each tuple: (old_content_string, new_content_string, mode_name)

patches = [

    # 1. PREDICT
    (
        '                    "Prediction market signals from Polymarket:\\\\n"\n'
        '                    f"{context}\\\\n\\\\n"\n'
        '                    "Generate one oracle post under 280 chars. "\n'
        '                    "Interpret the odds as signals — what does the market know? "\n'
        '                    "Octodamus voice. No hashtags."',

        '                    f"{random.choice(_VOICE_INSTRUCTIONS)}\\n"\n'
        '                    "Prediction market signals from Polymarket:\\n"\n'
        '                    f"{context}\\n\\n"\n'
        '                    "Generate one oracle post under 280 chars. No hashtags.\\n"\n'
        '                    "Lead with a SPECIFIC probability or market name from the data.\\n"\n'
        '                    "What are the odds actually telling us that nobody wants to say out loud?"',

        "predict"
    ),

    # 2. GECKO
    (
        '                    "CoinGecko crypto market signals:\\\\n"\n'
        '                    f"{context}\\\\n\\\\n"\n'
        '                    "Generate one oracle post under 280 chars. "\n'
        '                    "Read the crypto currents — what is the deep telling you? "\n'
        '                    "Octodamus voice. No hashtags."',

        '                    f"{random.choice(_VOICE_INSTRUCTIONS)}\\n"\n'
        '                    "CoinGecko crypto market signals:\\n"\n'
        '                    f"{context}\\n\\n"\n'
        '                    "Generate one oracle post under 280 chars. No hashtags.\\n"\n'
        '                    "Lead with a SPECIFIC price, percentage, or coin name from the data.\\n"\n'
        '                    "What is crypto doing that equity traders haven\'t noticed yet?"',

        "gecko"
    ),

    # 3. PULSE
    (
        '                    "Market sentiment signals:\\\\n"\n'
        '                    f"{context}\\\\n\\\\n"\n'
        '                    "Generate one oracle post under 280 chars. "\n'
        '                    "What does the crowd\'s fear or greed reveal about what comes next? "\n'
        '                    "Octodamus voice. No hashtags."',

        '                    f"{random.choice(_VOICE_INSTRUCTIONS)}\\n"\n'
        '                    "Market sentiment signals:\\n"\n'
        '                    f"{context}\\n\\n"\n'
        '                    "Generate one oracle post under 280 chars. No hashtags.\\n"\n'
        '                    "Lead with the SPECIFIC Fear & Greed number or Wikipedia trend from the data.\\n"\n'
        '                    "Crowd sentiment is a contrarian signal — say what it actually implies."',

        "pulse"
    ),

    # 4. FX
    (
        '                    "FX and currency market signals:\\\\n"\n'
        '                    f"{context}\\\\n\\\\n"\n'
        '                    "Generate one oracle post under 280 chars. "\n'
        '                    "Currency flows carry macro truth — what do they reveal? "\n'
        '                    "Octodamus voice. No hashtags."',

        '                    f"{random.choice(_VOICE_INSTRUCTIONS)}\\n"\n'
        '                    "FX and currency market signals:\\n"\n'
        '                    f"{context}\\n\\n"\n'
        '                    "Generate one oracle post under 280 chars. No hashtags.\\n"\n'
        '                    "Lead with a SPECIFIC currency pair or rate move from the data.\\n"\n'
        '                    "Most people ignore FX. Name the thing it\'s signaling about the macro."',

        "fx"
    ),

    # 5. GEO
    (
        '                    "Geopolitical signals from GDELT:\\\\n"\n'
        '                    f"{context}\\\\n\\\\n"\n'
        '                    "Generate one oracle post under 280 chars. "\n'
        '                    "Geopolitics is the tide beneath the markets — what is it saying? "\n'
        '                    "Octodamus voice. No hashtags."',

        '                    f"{random.choice(_VOICE_INSTRUCTIONS)}\\n"\n'
        '                    "Geopolitical signals from GDELT:\\n"\n'
        '                    f"{context}\\n\\n"\n'
        '                    "Generate one oracle post under 280 chars. No hashtags.\\n"\n'
        '                    "Lead with the SPECIFIC region, country, or tone score from the data.\\n"\n'
        '                    "Name the geopolitical signal and what it means for markets right now."',

        "geo"
    ),

    # 6. NEWS
    (
        '                    "Latest market headlines:\\\\n"\n'
        '                    f"{context}\\\\n\\\\n"\n'
        '                    "Generate one oracle post under 280 chars. "\n'
        '                    "The news is noise. The signal is what the noise is hiding. "\n'
        '                    "Octodamus voice. No hashtags."',

        '                    f"{random.choice(_VOICE_INSTRUCTIONS)}\\n"\n'
        '                    "Latest market headlines:\\n"\n'
        '                    f"{context}\\n\\n"\n'
        '                    "Generate one oracle post under 280 chars. No hashtags.\\n"\n'
        '                    "Pick the ONE headline that reveals something the market hasn\'t priced in.\\n"\n'
        '                    "Name the company, person, or number. Don\'t summarize — interpret."',

        "news"
    ),
]

# ── Apply all patches ─────────────────────────────────────────────────────────
success = 0
for old, new, mode in patches:
    if old in content:
        content = content.replace(old, new)
        print(f"✅ {mode:10s} — voice rotation applied")
        success += 1
    else:
        print(f"⚠️  {mode:10s} — string not found, skipping")

open(RUNNER, "w").write(content)
print(f"\n{'─'*50}")
print(f"Patched {success}/6 signal modes.")
if success == 6:
    print("All signal modes now use rotating voice. 🐙")
else:
    print(f"⚠️  {6-success} mode(s) need manual check.")
print(f"Backup saved to: {BACKUP}")

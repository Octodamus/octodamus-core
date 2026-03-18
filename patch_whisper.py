"""
patch_whisper.py

Adds OctoEar (OpenAI Whisper transcription) to Octodamus.

Changes:
  1. bitwarden.py        — adds OPENAI_API_KEY to OCTODAMUS_SECRETS
  2. octodamus_runner.py — imports octo_whisper
  3. octodamus_runner.py — injects into daily mode AFTER OctoTube runs
                           (OctoTube output feeds directly into Whisper target selection)
  4. octodamus_runner.py — adds --mode whisper for standalone URL transcription

Prerequisites:
    pip3 install openai yt-dlp --break-system-packages
    sudo apt-get install ffmpeg -y

Bitwarden setup:
    Item name:  AGENT - Octodamus - OpenAI Whisper
    Type:       Login
    Password:   <your OpenAI API key>

    Note: This is an OpenAI key, NOT Anthropic. Get one at platform.openai.com.
    Whisper costs $0.006/minute. A 15-minute video = $0.09.
    Default config: max 2 videos/run × 20min cap = $0.24 max per daily run.

Run from project directory:
    C:\\Python314\\python.exe patch_whisper.py
"""

import os
import shutil

BW_PATH   = "bitwarden.py"
BW_BACKUP = "bitwarden.py.bak_whisper"

RUNNER_PATH   = "octodamus_runner.py"
RUNNER_BACKUP = "octodamus_runner.py.bak_whisper"

# ── bitwarden.py — add OpenAI key ─────────────────────────────────────────────

BW_ANCHOR = '    "AGENT - Octodamus - YouTube Data API":              "YOUTUBE_API_KEY",'
BW_INSERT  = '    "AGENT - Octodamus - OpenAI Whisper":               "OPENAI_API_KEY",'

# ── runner — import block ─────────────────────────────────────────────────────

IMPORT_ANCHOR = "# ── OctoTV + OctoTube ────────────────────────────────────────────────────────"

WHISPER_IMPORT = """
# ── OctoEar (Whisper transcription) ──────────────────────────────────────────
try:
    from octo_whisper import run_whisper_scan, format_whisper_for_prompt
    _WHISPER_AVAILABLE = True
except ImportError:
    _WHISPER_AVAILABLE = False

"""

# ── runner — daily context injection ─────────────────────────────────────────
# Whisper runs AFTER OctoTube so it can use tube results to select videos

TUBE_BUILD_SNIPPET = """\
    if _TUBE_AVAILABLE:
        try:
            tube = run_youtube_scan()
            if not tube.get("error"):
                ctx += "\\n\\n" + format_youtube_for_prompt(tube)
        except Exception as e:
            print(f"[Runner] OctoTube in daily skipped: {e}")"""

TUBE_BUILD_REPLACEMENT = """\
    if _TUBE_AVAILABLE:
        try:
            tube = run_youtube_scan()
            if not tube.get("error"):
                ctx += "\\n\\n" + format_youtube_for_prompt(tube)
                # Feed OctoTube results directly into Whisper for targeted transcription
                if _WHISPER_AVAILABLE:
                    try:
                        whisper = run_whisper_scan(tube_results=tube)
                        if not whisper.get("error"):
                            ctx += "\\n\\n" + format_whisper_for_prompt(whisper)
                    except Exception as e:
                        print(f"[Runner] OctoEar in daily skipped: {e}")
        except Exception as e:
            print(f"[Runner] OctoTube in daily skipped: {e}")"""

# ── runner — new standalone mode ─────────────────────────────────────────────

MODE_ANCHOR = '    elif args.mode == "tradingview":'

WHISPER_MODE = """\
    elif args.mode == "whisper":
        url = getattr(args, "url", None)
        if not url:
            print("[Runner] --mode whisper requires --url <youtube_url>")
            print("  Example: python3 octodamus_runner.py --mode whisper --url https://youtu.be/VIDEO_ID")
        elif _WHISPER_AVAILABLE:
            from octo_whisper import transcribe_video
            result = transcribe_video(url)
            if result.get("signals"):
                print("\\n" + format_whisper_for_prompt({"videos": [result], "aggregate": {
                    "videos_transcribed": 1,
                    "creator_sentiment": result["signals"]["sentiment"],
                    "top_mentioned_assets": result["signals"].get("top_assets", []),
                    "total_cost_usd": result.get("cost_usd", 0),
                }}))
            else:
                print(f"[Runner] Transcription failed: {result.get('error','unknown')}")
        else:
            print("[Runner] OctoEar not available — install: pip3 install openai yt-dlp --break-system-packages")
"""

# ── runner — argparse: add 'whisper' to choices + --url argument ──────────────

CHOICES_ANCHOR_OLD = 'choices=["monitor", "daily", "deep_dive", "wisdom", "status", "drain", "tradingview", "youtube"]'
CHOICES_ANCHOR_NEW = 'choices=["monitor", "daily", "deep_dive", "wisdom", "status", "drain", "tradingview", "youtube", "whisper"]'

URL_ARG_ANCHOR = '    parser.add_argument("--force", action="store_true", help="Bypass posting hours")'
URL_ARG_INSERT = '    parser.add_argument("--url", type=str, default=None, help="YouTube URL for --mode whisper")\n'


# ── Patch helpers ─────────────────────────────────────────────────────────────

def patch_file(path, backup, patches):
    if not os.path.exists(path):
        print(f"[Patch] ERROR: {path} not found")
        return False
    shutil.copy2(path, backup)
    print(f"[Patch] Backed up {path} → {backup}")
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    for i, (old, new) in enumerate(patches, 1):
        if old not in content:
            print(f"[Patch] WARNING patch {i}: anchor not found (may already be applied)")
            continue
        content = content.replace(old, new, 1)
        print(f"[Patch] ✅ Patch {i} applied")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return True


def patch_bitwarden():
    print("\n[Patch] Patching bitwarden.py...")
    patch_file(BW_PATH, BW_BACKUP, [
        (BW_ANCHOR, BW_ANCHOR + "\n" + BW_INSERT),
    ])


def patch_runner():
    print("\n[Patch] Patching octodamus_runner.py...")
    patch_file(RUNNER_PATH, RUNNER_BACKUP, [
        (IMPORT_ANCHOR, IMPORT_ANCHOR + WHISPER_IMPORT),
        (TUBE_BUILD_SNIPPET, TUBE_BUILD_REPLACEMENT),
        (MODE_ANCHOR, WHISPER_MODE + "\n" + MODE_ANCHOR),
        (CHOICES_ANCHOR_OLD, CHOICES_ANCHOR_NEW),
        (URL_ARG_ANCHOR, URL_ARG_INSERT + URL_ARG_ANCHOR),
    ])


INSTRUCTIONS = """
╔══════════════════════════════════════════════════════════════════╗
║                  OctoEar (Whisper) Patch Complete               ║
╚══════════════════════════════════════════════════════════════════╝

STEP 1 — Install dependencies (WSL2):
    pip3 install openai yt-dlp --break-system-packages
    sudo apt-get install ffmpeg -y

STEP 2 — Get an OpenAI API key (NOT Anthropic):
    platform.openai.com → API keys → Create new secret key
    Cost: $0.006/min. A 15-min video = $0.09.

STEP 3 — Add to Bitwarden:
    Item name:  AGENT - Octodamus - OpenAI Whisper
    Type:       Login
    Password:   <your OpenAI API key>

STEP 4 — Reload secrets:
    bash /home/walli/octodamus/bw_unlock.sh

STEP 5 — Test on a specific video:
    python3 octodamus_runner.py --mode whisper --url https://youtu.be/VIDEO_ID

STEP 6 — Verify it runs in daily:
    python3 octodamus_runner.py --mode daily --force

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

HOW THE PIPELINE WORKS IN DAILY MODE:

  OctoTube runs first → finds top trending videos + active channels
       ↓
  OctoEar receives OctoTube results → selects top 2 high-signal videos
       ↓
  yt-dlp downloads audio only (first 20min, ~4MB per video)
       ↓
  Whisper API transcribes audio → raw text
       ↓
  Signal extraction: price targets, assets, bull/bear keywords
       ↓
  Qwen summarizes to 3 key signal bullets
       ↓
  Injected into daily context for Claude to reason over

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

COST CONTROLS (edit in octo_whisper.py if needed):
    MAX_MINUTES_PER_VIDEO = 20   # cap per video ($0.12 max)
    MAX_VIDEOS_PER_RUN    = 2    # max per daily run ($0.24 max)

To reduce costs further:
    MAX_MINUTES_PER_VIDEO = 10   # first 10min only ($0.06/video)
    MAX_VIDEOS_PER_RUN    = 1    # one video per day ($0.06/day)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

NEW RUNNER MODE:
    --mode whisper --url <youtube_url>   Transcribe any YouTube video on demand
"""

if __name__ == "__main__":
    print("=" * 60)
    print("  OctoEar (Whisper) Patch Script")
    print("=" * 60)
    patch_bitwarden()
    patch_runner()
    print(INSTRUCTIONS)

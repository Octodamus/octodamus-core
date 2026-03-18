"""
patch_two_minds.py

Adds OctoTV (TradingView) and OctoTube (YouTube) to Octodamus.

Changes made:
  1. bitwarden.py        — adds YOUTUBE_API_KEY to OCTODAMUS_SECRETS
  2. octodamus_runner.py — adds imports for both new modules
  3. octodamus_runner.py — injects both into _build_four_minds_context (or equivalent)
  4. octodamus_runner.py — adds 'tradingview' and 'youtube' CLI modes

Prerequisites:
    pip3 install tvdatafeed pandas ta google-api-python-client --break-system-packages

Bitwarden setup (run BEFORE patching):
    Add item: "AGENT - Octodamus - YouTube Data API"
    Type: Login, Password field = your YouTube Data API v3 key

Run from project directory:
    C:\\Python314\\python.exe patch_two_minds.py
"""

import os
import shutil

# ── BITWARDEN PATCH ───────────────────────────────────────────────────────────

BW_PATH   = "bitwarden.py"
BW_BACKUP = "bitwarden.py.bak_two_minds"

# Insert YouTube key after the OpenRouter line
BW_ANCHOR = '    "AGENT - Octodamus - OpenRouter":                    "OPENROUTER_API_KEY",'
BW_INSERT = '''    "AGENT - Octodamus - YouTube Data API":              "YOUTUBE_API_KEY",'''

# ── RUNNER PATCH ──────────────────────────────────────────────────────────────

RUNNER_PATH   = "octodamus_runner.py"
RUNNER_BACKUP = "octodamus_runner.py.bak_two_minds"

# ── A: Import block ──────────────────────────────────────────────────────────
# Anchor: insert after the six-minds import block
IMPORT_ANCHOR = "# ── Six New Signal Modules ────────────────────────────────────"

NEW_IMPORTS = """
# ── OctoTV + OctoTube ────────────────────────────────────────────────────────
try:
    from octo_tradingview import run_tv_scan, format_tv_for_prompt
    _TV_AVAILABLE = True
except ImportError:
    _TV_AVAILABLE = False

try:
    from octo_youtube import run_youtube_scan, format_youtube_for_prompt
    _TUBE_AVAILABLE = True
except ImportError:
    _TUBE_AVAILABLE = False

"""

# ── B: Context builder injection ─────────────────────────────────────────────
# Anchor: the line that returns from the context builder
BUILD_ANCHOR = "    return ctx.strip()"

TV_TUBE_BUILD_INJECTION = """\
    if _TV_AVAILABLE:
        try:
            tv = run_tv_scan()
            if not tv.get("error"):
                ctx += "\\n\\n" + format_tv_for_prompt(tv)
        except Exception as e:
            print(f"[Runner] OctoTV in daily skipped: {e}")
    if _TUBE_AVAILABLE:
        try:
            tube = run_youtube_scan()
            if not tube.get("error"):
                ctx += "\\n\\n" + format_youtube_for_prompt(tube)
        except Exception as e:
            print(f"[Runner] OctoTube in daily skipped: {e}")
"""

# ── C: New standalone modes ───────────────────────────────────────────────────
# Anchor: the final elif in mode dispatch (drain mode)
MODE_ANCHOR = '    elif args.mode == "drain":'

NEW_MODES_BEFORE_DRAIN = """\
    elif args.mode == "tradingview":
        print("[Runner] Running OctoTV standalone scan...")
        if _TV_AVAILABLE:
            tv = run_tv_scan()
            print("\\n" + format_tv_for_prompt(tv))
        else:
            print("[Runner] OctoTV not available — install: pip3 install tvdatafeed pandas ta --break-system-packages")
    elif args.mode == "youtube":
        print("[Runner] Running OctoTube standalone scan...")
        if _TUBE_AVAILABLE:
            tube = run_youtube_scan()
            print("\\n" + format_youtube_for_prompt(tube))
        else:
            print("[Runner] OctoTube not available — install: pip3 install google-api-python-client --break-system-packages")
"""

# ── D: Add modes to argparse choices ─────────────────────────────────────────
# Anchor: the existing choices list (last known state includes six-minds modes)
CHOICES_ANCHOR_OLD = 'choices=["monitor", "daily", "deep_dive", "wisdom", "status", "drain"]'
CHOICES_ANCHOR_NEW = 'choices=["monitor", "daily", "deep_dive", "wisdom", "status", "drain", "tradingview", "youtube"]'


# ── Patch functions ───────────────────────────────────────────────────────────

def patch_file(path: str, backup_path: str, patches: list[tuple]) -> bool:
    """
    Apply multiple find-replace patches to a file.
    patches = [(old_str, new_str), ...]
    Returns True if all patches applied successfully.
    """
    if not os.path.exists(path):
        print(f"[Patch] ERROR: {path} not found. Run from project directory.")
        return False

    # Backup
    shutil.copy2(path, backup_path)
    print(f"[Patch] Backed up {path} → {backup_path}")

    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    for i, (old, new) in enumerate(patches, 1):
        if old not in content:
            print(f"[Patch] WARNING: patch {i} anchor not found in {path}:")
            print(f"        '{old[:80]}...'")
            print(f"        Patch {i} skipped — file may already be patched or anchor changed.")
            continue
        content = content.replace(old, new, 1)
        print(f"[Patch] ✅ Patch {i} applied to {path}")

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

    return True


def patch_bitwarden():
    print("\n[Patch] Patching bitwarden.py...")
    patches = [
        (
            BW_ANCHOR,
            BW_ANCHOR + "\n" + BW_INSERT,
        ),
    ]
    patch_file(BW_PATH, BW_BACKUP, patches)


def patch_runner():
    print("\n[Patch] Patching octodamus_runner.py...")

    patches = [
        # 1. Import block — insert after six-minds anchor
        (
            IMPORT_ANCHOR,
            IMPORT_ANCHOR + NEW_IMPORTS,
        ),
        # 2. Context builder — inject before the return
        (
            BUILD_ANCHOR,
            TV_TUBE_BUILD_INJECTION + "\n" + BUILD_ANCHOR,
        ),
        # 3. New modes — insert before drain
        (
            MODE_ANCHOR,
            NEW_MODES_BEFORE_DRAIN + "\n" + MODE_ANCHOR,
        ),
        # 4. argparse choices — add new modes
        (
            CHOICES_ANCHOR_OLD,
            CHOICES_ANCHOR_NEW,
        ),
    ]
    patch_file(RUNNER_PATH, RUNNER_BACKUP, patches)


# ── Post-patch instructions ───────────────────────────────────────────────────

INSTRUCTIONS = """
╔══════════════════════════════════════════════════════════════════╗
║               OctoTV + OctoTube Patch Complete                  ║
╚══════════════════════════════════════════════════════════════════╝

STEP 1 — Install Python dependencies (WSL2):
    pip3 install tvdatafeed pandas ta google-api-python-client --break-system-packages

STEP 2 — Get a YouTube Data API v3 key (free):
    1. Go to: https://console.cloud.google.com/
    2. Create project → Enable "YouTube Data API v3"
    3. Credentials → Create API Key → copy it

STEP 3 — Add to Bitwarden:
    Item name:  AGENT - Octodamus - YouTube Data API
    Type:       Login
    Password:   <your YouTube Data API key>

STEP 4 — Reload Bitwarden secrets:
    bash /home/walli/octodamus/bw_unlock.sh

STEP 5 — Test standalone:
    # TradingView (no API key needed):
    python3 octodamus_runner.py --mode tradingview

    # YouTube:
    python3 octodamus_runner.py --mode youtube

STEP 6 — Verify both appear in daily context:
    python3 octodamus_runner.py --mode daily --force

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

NOTE: tvdatafeed can occasionally break when TradingView updates their
data feed format. If OctoTV errors, check:
    pip3 install --upgrade tvdatafeed --break-system-packages

NOTE: YouTube API quota is 10,000 units/day free.
    - 1 search = ~100 units
    - 1 video list fetch = ~1 unit
    - 1 comment fetch = ~1 unit
    OctoTube uses roughly 600-800 units per full scan.
    You get ~12-15 full scans per day before hitting quota.
    Recommend running youtube scan in daily (once/day) only, not monitor.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

NEW RUNNER MODES:
    --mode tradingview   Standalone OctoTV multi-timeframe scan
    --mode youtube       Standalone OctoTube channel + trending scan

Both modules also auto-inject into --mode daily context.
"""


if __name__ == "__main__":
    print("=" * 60)
    print("  OctoTV + OctoTube Patch Script")
    print("=" * 60)

    patch_bitwarden()
    patch_runner()

    print(INSTRUCTIONS)

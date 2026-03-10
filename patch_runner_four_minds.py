"""
patch_runner_four_minds.py  (v2 — fixed anchors for live runner)

Run ONCE from your project directory:
    C:\Python314\python.exe patch_runner_four_minds.py
"""

import os
import shutil

RUNNER_PATH = "octodamus_runner.py"
BACKUP_PATH = "octodamus_runner.py.bak"

IMPORT_ANCHOR = "from octo_x_queue import queue_post, queue_thread, process_queue, queue_status"

NEW_IMPORTS = """
# ── Four New Minds ────────────────────────────
try:
    from octo_logic import run_technical_scan, format_logic_for_prompt
    _LOGIC_AVAILABLE = True
except ImportError:
    _LOGIC_AVAILABLE = False

try:
    from octo_vision import run_macro_scan, format_vision_for_prompt
    _VISION_AVAILABLE = True
except ImportError:
    _VISION_AVAILABLE = False

try:
    from octo_depth import run_onchain_scan, format_depth_for_prompt
    _DEPTH_AVAILABLE = True
except ImportError:
    _DEPTH_AVAILABLE = False

try:
    from octo_watch import run_sentiment_scan, format_watch_for_prompt
    _WATCH_AVAILABLE = True
except ImportError:
    _WATCH_AVAILABLE = False


def _build_four_minds_context() -> str:
    ctx = ""
    if _LOGIC_AVAILABLE:
        try:
            ctx += "\\n\\n" + format_logic_for_prompt(run_technical_scan(["NVDA", "TSLA", "BTC-USD"])[:3])
        except Exception as e:
            print(f"[Runner] OctoLogic in daily skipped: {e}")
    if _VISION_AVAILABLE:
        try:
            r = run_macro_scan()
            if not r.get("error"):
                ctx += "\\n\\n" + format_vision_for_prompt(r)
        except Exception as e:
            print(f"[Runner] OctoVision in daily skipped: {e}")
    if _DEPTH_AVAILABLE:
        try:
            r = run_onchain_scan()
            if not r.get("error"):
                ctx += "\\n\\n" + format_depth_for_prompt(r)
        except Exception as e:
            print(f"[Runner] OctoDepth in daily skipped: {e}")
    if _WATCH_AVAILABLE:
        try:
            ctx += "\\n\\n" + format_watch_for_prompt(run_sentiment_scan())
        except Exception as e:
            print(f"[Runner] OctoWatch in daily skipped: {e}")
    return ctx.strip()
"""

ENTRY_POINT_ANCHOR = "# ─────────────────────────────────────────────\n# ENTRY POINT"

NEW_MODE_FUNCTIONS = '''
# ─────────────────────────────────────────────
# MODE: LOGIC
# ─────────────────────────────────────────────

def mode_logic(ticker=None):
    if not _LOGIC_AVAILABLE:
        print("[Runner] OctoLogic not available."); return
    print("\\n[Runner] Running OctoLogic technical scan...")
    try:
        results = run_technical_scan([ticker] if ticker else None)
        if not results or results[0].get("error"):
            print("[Runner] No usable technical results."); return
        response = claude.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=150, system=OCTO_SYSTEM,
            messages=[{"role": "user", "content": (
                f"Technical signal post for @octodamusai.\\n"
                f"{format_logic_for_prompt(results[:4])}\\n\\n"
                "One post under 280 chars. Octodamus voice — bored certainty."
            )}],
        )
        post = response.content[0].text.strip()
        queue_post(post, post_type="technical_signal", priority=3)
        process_queue(max_posts=1)
        print(f"[Runner] OctoLogic post:\\n  {post}")
    except Exception as e:
        print(f"[Runner] mode_logic failed: {e}")


# ─────────────────────────────────────────────
# MODE: VISION
# ─────────────────────────────────────────────

def mode_vision():
    if not _VISION_AVAILABLE:
        print("[Runner] OctoVision not available."); return
    print("\\n[Runner] Running OctoVision macro scan...")
    try:
        result = run_macro_scan()
        if result.get("error"):
            print(f"[Runner] OctoVision error: {result[\'error\']}"); return
        regime = result.get("interpretation", {}).get("regime", "UNKNOWN")
        response = claude.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=150, system=OCTO_SYSTEM,
            messages=[{"role": "user", "content": (
                f"Macro oracle post for @octodamusai.\\n"
                f"{format_vision_for_prompt(result)}\\n\\n"
                f"Macro regime: {regime}\\n\\n"
                "One post under 280 chars. What do the deep currents say? Octodamus voice."
            )}],
        )
        post = response.content[0].text.strip()
        queue_post(post, post_type="macro_oracle", priority=4)
        process_queue(max_posts=1)
        print(f"[Runner] OctoVision post:\\n  {post}")
    except Exception as e:
        print(f"[Runner] mode_vision failed: {e}")


# ─────────────────────────────────────────────
# MODE: DEPTH
# ─────────────────────────────────────────────

def mode_depth():
    if not _DEPTH_AVAILABLE:
        print("[Runner] OctoDepth not available."); return
    print("\\n[Runner] Running OctoDepth on-chain scan...")
    try:
        result = run_onchain_scan()
        if result.get("error"):
            print(f"[Runner] OctoDepth error: {result[\'error\']}"); return
        bias = result.get("interpretation", {}).get("bias", "QUIET")
        response = claude.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=150, system=OCTO_SYSTEM,
            messages=[{"role": "user", "content": (
                f"On-chain oracle post for @octodamusai.\\n"
                f"{format_depth_for_prompt(result)}\\n\\n"
                f"On-chain activity: {bias}\\n\\n"
                "One post under 280 chars. What are the whales doing? Octodamus voice."
            )}],
        )
        post = response.content[0].text.strip()
        queue_post(post, post_type="onchain_signal", priority=3)
        process_queue(max_posts=1)
        print(f"[Runner] OctoDepth post:\\n  {post}")
    except Exception as e:
        print(f"[Runner] mode_depth failed: {e}")


# ─────────────────────────────────────────────
# MODE: WATCH
# ─────────────────────────────────────────────

def mode_watch():
    if not _WATCH_AVAILABLE:
        print("[Runner] OctoWatch not available."); return
    print("\\n[Runner] Running OctoWatch social scan...")
    try:
        result = run_sentiment_scan()
        mood = result.get("mood", "NEUTRAL")
        composite = result.get("composite_score", 0)
        response = claude.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=150, system=OCTO_SYSTEM,
            messages=[{"role": "user", "content": (
                f"Social sentiment oracle post for @octodamusai.\\n"
                f"{format_watch_for_prompt(result)}\\n\\n"
                f"Overall mood: {mood} ({composite:+.3f})\\n\\n"
                "One post under 280 chars. What is retail doing? Octodamus voice."
            )}],
        )
        post = response.content[0].text.strip()
        queue_post(post, post_type="social_sentiment", priority=5)
        process_queue(max_posts=1)
        print(f"[Runner] OctoWatch post:\\n  {post}")
    except Exception as e:
        print(f"[Runner] mode_watch failed: {e}")

'''

ARGPARSE_ANCHOR  = 'choices=["monitor", "daily", "deep_dive", "wisdom", "status", "drain"],'
NEW_ARGPARSE     = 'choices=["monitor", "daily", "deep_dive", "wisdom", "status", "drain", "logic", "vision", "depth", "watch"],'

DISPATCH_ANCHOR = '    elif args.mode == "drain":\n        # Just drain the existing queue without generating new content\n        posted = process_queue(max_posts=10)\n        print(f"[Runner] Drained {posted} posts from queue.")'

NEW_DISPATCH = '''    elif args.mode == "drain":
        # Just drain the existing queue without generating new content
        posted = process_queue(max_posts=10)
        print(f"[Runner] Drained {posted} posts from queue.")
    elif args.mode == "logic":
        mode_logic(args.ticker if args.ticker != "NVDA" else None)
    elif args.mode == "vision":
        mode_vision()
    elif args.mode == "depth":
        mode_depth()
    elif args.mode == "watch":
        mode_watch()'''


def apply_patches():
    if not os.path.exists(RUNNER_PATH):
        print(f"ERROR: {RUNNER_PATH} not found."); return False

    shutil.copy2(RUNNER_PATH, BACKUP_PATH)
    print(f"Backed up to {BACKUP_PATH}")

    with open(RUNNER_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    if "_LOGIC_AVAILABLE" in content:
        print("Already patched — aborting."); return False

    errors = []

    if IMPORT_ANCHOR in content:
        content = content.replace(IMPORT_ANCHOR, IMPORT_ANCHOR + NEW_IMPORTS)
        print("Patch 1 OK: imports added")
    else:
        errors.append("PATCH 1 FAILED — import anchor not found")

    if ENTRY_POINT_ANCHOR in content:
        content = content.replace(ENTRY_POINT_ANCHOR, NEW_MODE_FUNCTIONS + ENTRY_POINT_ANCHOR)
        print("Patch 2 OK: mode functions added")
    else:
        errors.append("PATCH 2 FAILED — entry point anchor not found")

    if ARGPARSE_ANCHOR in content:
        content = content.replace(ARGPARSE_ANCHOR, NEW_ARGPARSE)
        print("Patch 3 OK: argparse choices updated")
    else:
        errors.append("PATCH 3 FAILED — argparse anchor not found")

    if DISPATCH_ANCHOR in content:
        content = content.replace(DISPATCH_ANCHOR, NEW_DISPATCH)
        print("Patch 4 OK: dispatch cases added")
    else:
        errors.append("PATCH 4 FAILED — dispatch anchor not found")

    if errors:
        print("\nFATAL PATCH ERRORS — restoring backup:")
        for e in errors:
            print(f"  {e}")
        shutil.copy2(BACKUP_PATH, RUNNER_PATH)
        return False

    with open(RUNNER_PATH, "w", encoding="utf-8") as f:
        f.write(content)

    print("\nAll patches applied. New modes: --mode logic | vision | depth | watch")
    return True


if __name__ == "__main__":
    apply_patches()

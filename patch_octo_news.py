"""
patch_octo_news.py

1. Adds --mode news to octodamus_runner.py
2. Updates telegram_bot.py system prompt to show OctoNews as live
3. Wires octo_news.py into _build_four_minds_context() so daily read includes it

Run from project directory:
    C:\Python314\python.exe patch_octo_news.py
"""

import os, shutil

# ── Patch 1: runner ───────────────────────────────────────────────────────────

RUNNER_PATH   = "octodamus_runner.py"
RUNNER_BACKUP = "octodamus_runner.py.bak2"

# Add octo_news import alongside the other four minds
NEWS_IMPORT_ANCHOR = "# ── Four New Minds ────────────────────────────"
NEWS_IMPORT = """# ── OctoNews ─────────────────────────────────
try:
    from octo_news import run_news_scan, format_news_for_prompt
    _NEWS_AVAILABLE = True
except ImportError:
    _NEWS_AVAILABLE = False

"""

# Add news context to _build_four_minds_context
BUILD_ANCHOR = "    return ctx.strip()"
NEWS_BUILD_INJECTION = """    if _NEWS_AVAILABLE:
        try:
            nr = run_news_scan(["NVDA", "TSLA", "BTC", "SPY"])
            if not nr.get("error"):
                ctx += "\\n\\n" + format_news_for_prompt(nr)
        except Exception as e:
            print(f"[Runner] OctoNews in daily skipped: {e}")
    """

# Add mode_news function before entry point
NEWS_MODE_ANCHOR = "# ─────────────────────────────────────────────\n# MODE: LOGIC"
NEWS_MODE_FUNCTION = '''# ─────────────────────────────────────────────
# MODE: NEWS
# ─────────────────────────────────────────────

def mode_news():
    if not _NEWS_AVAILABLE:
        print("[Runner] OctoNews not available."); return
    print("\\n[Runner] Running OctoNews scan...")
    try:
        result = run_news_scan(["NVDA", "TSLA", "BTC", "SPY", "ETH"])
        if result.get("error"):
            print(f"[Runner] OctoNews error: {result['error']}"); return
        top = result.get("top_stories", [])
        if not top:
            print("[Runner] No top stories found."); return
        response = claude.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=150, system=OCTO_SYSTEM,
            messages=[{"role": "user", "content": (
                f"News oracle post for @octodamusai.\\n"
                f"{format_news_for_prompt(result)}\\n\\n"
                "One post under 280 chars. What does the news current reveal?\\n"
                "Octodamus voice — you already saw this coming. Bored certainty."
            )}],
        )
        post = response.content[0].text.strip()
        queue_post(post, post_type="news_oracle", priority=2)
        process_queue(max_posts=1)
        print(f"[Runner] OctoNews post:\\n  {post}")
    except Exception as e:
        print(f"[Runner] mode_news failed: {e}")


'''

# Argparse and dispatch
ARGPARSE_ANCHOR  = 'choices=["monitor", "daily", "deep_dive", "wisdom", "status", "drain", "logic", "vision", "depth", "watch"],'
NEW_ARGPARSE     = 'choices=["monitor", "daily", "deep_dive", "wisdom", "status", "drain", "logic", "vision", "depth", "watch", "news"],'

DISPATCH_ANCHOR  = '    elif args.mode == "watch":\n        mode_watch()'
NEW_DISPATCH     = '    elif args.mode == "watch":\n        mode_watch()\n    elif args.mode == "news":\n        mode_news()'


def patch_runner():
    if not os.path.exists(RUNNER_PATH):
        print(f"ERROR: {RUNNER_PATH} not found."); return False

    with open(RUNNER_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    if "_NEWS_AVAILABLE" in content:
        print("Runner already has OctoNews."); return True

    shutil.copy2(RUNNER_PATH, RUNNER_BACKUP)
    errors = []

    if NEWS_IMPORT_ANCHOR in content:
        content = content.replace(NEWS_IMPORT_ANCHOR, NEWS_IMPORT + NEWS_IMPORT_ANCHOR)
        print("Patch R1 OK: OctoNews import added")
    else:
        errors.append("R1 FAILED — import anchor not found")

    if BUILD_ANCHOR in content:
        content = content.replace(BUILD_ANCHOR, NEWS_BUILD_INJECTION + "\n    " + BUILD_ANCHOR)
        print("Patch R2 OK: OctoNews wired into _build_four_minds_context")
    else:
        print("Patch R2 skipped — build anchor not found (non-fatal)")

    if NEWS_MODE_ANCHOR in content:
        content = content.replace(NEWS_MODE_ANCHOR, NEWS_MODE_FUNCTION + NEWS_MODE_ANCHOR)
        print("Patch R3 OK: mode_news function added")
    else:
        errors.append("R3 FAILED — mode anchor not found")

    if ARGPARSE_ANCHOR in content:
        content = content.replace(ARGPARSE_ANCHOR, NEW_ARGPARSE)
        print("Patch R4 OK: argparse updated")
    else:
        errors.append("R4 FAILED — argparse anchor not found")

    if DISPATCH_ANCHOR in content:
        content = content.replace(DISPATCH_ANCHOR, NEW_DISPATCH)
        print("Patch R5 OK: dispatch added")
    else:
        errors.append("R5 FAILED — dispatch anchor not found")

    if errors:
        print("\nFATAL — restoring backup:")
        for e in errors: print(f"  {e}")
        shutil.copy2(RUNNER_BACKUP, RUNNER_PATH)
        return False

    with open(RUNNER_PATH, "w", encoding="utf-8") as f:
        f.write(content)
    print("Runner patched.")
    return True


# ── Patch 2: telegram system prompt ──────────────────────────────────────────

BOT_PATH   = "telegram_bot.py"
BOT_BACKUP = "telegram_bot.py.bak3"

OLD_OCTOWATCH = "- OctoWatch live - Reddit social sentiment scanner across WSB, CryptoCurrency, investing, stocks, Bitcoin"
NEW_OCTOWATCH = "- OctoWatch live - Reddit social sentiment scanner across WSB, CryptoCurrency, investing, stocks, Bitcoin\n- OctoNews live - NewsAPI headlines for NVDA, TSLA, AAPL, BTC, ETH, SPY with sentiment scoring"


def patch_telegram():
    if not os.path.exists(BOT_PATH):
        print(f"ERROR: {BOT_PATH} not found."); return False

    with open(BOT_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    if "OctoNews live" in content:
        print("Telegram already has OctoNews."); return True

    shutil.copy2(BOT_PATH, BOT_BACKUP)

    if OLD_OCTOWATCH in content:
        content = content.replace(OLD_OCTOWATCH, NEW_OCTOWATCH)
        print("Patch T1 OK: OctoNews added to telegram system prompt")
    else:
        print("Patch T1 skipped — anchor not found (update manually)")

    with open(BOT_PATH, "w", encoding="utf-8") as f:
        f.write(content)
    return True


if __name__ == "__main__":
    print("── Patching runner ──────────────────────")
    patch_runner()
    print("\n── Patching telegram bot ────────────────")
    patch_telegram()
    print("\nDone. Steps:")
    print("  1. copy octo_news.py to C:\\Users\\walli\\octodamus\\")
    print("  2. C:\\Python314\\python.exe octodamus_runner.py --mode news")
    print("  3. Restart telegram_bot.py")

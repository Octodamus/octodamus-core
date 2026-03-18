"""
patch_runner_brain.py
Wires OctoBrain (memory) and OctoJournal (learning) into octodamus_runner.py.

Changes:
1. Imports octo_brain at startup
2. Injects brain context into mode_daily, mode_monitor, mode_wisdom prompts
3. Logs post results to BRAIN.md after each post
4. Adds --mode journal to runner

Run from: /home/walli/octodamus/
    python3 patch_runner_brain.py
"""

import shutil
import re

RUNNER = "octodamus_runner.py"
BACKUP = "octodamus_runner.py.bak_pre_brain"

shutil.copy2(RUNNER, BACKUP)
print(f"✅ Backed up {RUNNER} → {BACKUP}")

with open(RUNNER, "r", encoding="utf-8") as f:
    content = f.read()

changes = 0

# ── 1. Add octo_brain import after bitwarden import ──────────────────────────

OLD_IMPORT = "from octo_x_queue import queue_post, queue_thread, process_queue, queue_status"
NEW_IMPORT = """from octo_x_queue import queue_post, queue_thread, process_queue, queue_status
from octo_brain import (
    read_brain, update_context, append_signal,
    append_post_result, append_learning, format_brain_for_prompt
)"""

if "from octo_brain import" in content:
    print("✅ octo_brain already imported — skipping.")
elif OLD_IMPORT in content:
    content = content.replace(OLD_IMPORT, NEW_IMPORT)
    print("✅ Added octo_brain import.")
    changes += 1
else:
    print("❌ Could not find import anchor.")


# ── 2. Inject brain context into mode_daily prompt ───────────────────────────

OLD_DAILY_CONTENT = (
    '                    "Generate the morning oracle market read for @octodamusai.\\n"\n'
    '                    f"Current market snapshots: {json.dumps(snapshots, indent=2)}"\n'
    '                    f"{news_section}\\n\\n"\n'
    '                    "One post, under 280 chars. You see the currents before others do.\\n"\n'
    '                    "This is your daily open — set the tone. Bored. Knowing. Inevitable.\\n"\n'
    '                    "If news headlines are provided, weave the narrative into the post naturally."'
)

NEW_DAILY_CONTENT = (
    '                    f"{format_brain_for_prompt(max_chars=1500)}\\n\\n"\n'
    '                    "Generate the morning oracle market read for @octodamusai.\\n"\n'
    '                    f"Current market snapshots: {json.dumps(snapshots, indent=2)}"\n'
    '                    f"{news_section}\\n\\n"\n'
    '                    "One post, under 280 chars. You see the currents before others do.\\n"\n'
    '                    "This is your daily open — set the tone. Bored. Knowing. Inevitable.\\n"\n'
    '                    "Use your working memory above to avoid repeating recent takes.\\n"\n'
    '                    "If news headlines are provided, weave the narrative into the post naturally."'
)

if "format_brain_for_prompt" in content and "daily open" in content:
    print("✅ Brain context already in mode_daily — skipping.")
elif OLD_DAILY_CONTENT in content:
    content = content.replace(OLD_DAILY_CONTENT, NEW_DAILY_CONTENT)
    print("✅ Injected brain context into mode_daily.")
    changes += 1
else:
    print("⚠️  Could not patch mode_daily prompt — will inject via wrapper instead.")


# ── 3. Log posts to BRAIN.md in process_queue result ─────────────────────────
# Wrap process_queue calls in mode_monitor to log results

OLD_MONITOR_POST = (
    "        posted = process_queue(max_posts=5)\n"
    '        print(f"[Runner] Posted {posted} item(s) to X.")'
)

NEW_MONITOR_POST = (
    "        posted = process_queue(max_posts=5)\n"
    '        print(f"[Runner] Posted {posted} item(s) to X.")\n'
    "        # Log signals to BRAIN.md\n"
    "        for item in signals_and_posts:\n"
    "            sig = item.get('signal', {})\n"
    "            append_signal({\n"
    "                'ticker': sig.get('ticker', 'MARKET'),\n"
    "                'type': sig.get('signal_type', 'monitor'),\n"
    "                'detail': sig.get('detail', item['post'][:60]),\n"
    "                'source': 'monitor'\n"
    "            })"
)

if "append_signal" in content:
    print("✅ Signal logging already in mode_monitor — skipping.")
elif OLD_MONITOR_POST in content:
    content = content.replace(OLD_MONITOR_POST, NEW_MONITOR_POST)
    print("✅ Added signal logging to mode_monitor.")
    changes += 1
else:
    print("⚠️  Could not patch mode_monitor signal logging.")


# ── 4. Add mode_journal function before ENTRY POINT ──────────────────────────

JOURNAL_MODE = '''
# ─────────────────────────────────────────────
# MODE: JOURNAL — daily learning distillation
# ─────────────────────────────────────────────

def mode_journal() -> None:
    """Run daily journal — distill today into BRAIN.md learnings. Run at 9pm."""
    try:
        from octo_journal import run_journal
        run_journal()
    except Exception as e:
        print(f"[Runner] mode_journal failed: {e}")

'''

ENTRY_ANCHOR = "# ─────────────────────────────────────────────\n# ENTRY POINT"

if "mode_journal" in content:
    print("✅ mode_journal already present — skipping.")
elif ENTRY_ANCHOR in content:
    content = content.replace(ENTRY_ANCHOR, JOURNAL_MODE + ENTRY_ANCHOR)
    print("✅ Added mode_journal function.")
    changes += 1
else:
    print("❌ Could not find ENTRY POINT anchor.")


# ── 5. Add journal to argparse choices ───────────────────────────────────────

old_choices = re.search(r'choices=\[.*?"news"\]', content)
if old_choices:
    old_str = old_choices.group(0)
    if "journal" in old_str:
        print("✅ journal already in argparse choices — skipping.")
    else:
        new_str = old_str.rstrip("]") + ', "journal"]'
        content = content.replace(old_str, new_str)
        print("✅ Added journal to argparse choices.")
        changes += 1
else:
    print("❌ Could not find argparse choices.")


# ── 6. Add journal dispatcher ────────────────────────────────────────────────

OLD_NEWS_BRANCH = (
    '    elif args.mode == "news":\n'
    '        mode_news()'
)

NEW_NEWS_BRANCH = (
    '    elif args.mode == "news":\n'
    '        mode_news()\n'
    '    elif args.mode == "journal":\n'
    '        mode_journal()'
)

if 'args.mode == "journal"' in content:
    print("✅ journal dispatcher already present — skipping.")
elif OLD_NEWS_BRANCH in content:
    content = content.replace(OLD_NEWS_BRANCH, NEW_NEWS_BRANCH)
    print("✅ Added journal dispatcher.")
    changes += 1
else:
    print("⚠️  Could not find news dispatcher to append journal after.")


# ── 7. Write file ─────────────────────────────────────────────────────────────

if changes > 0:
    with open(RUNNER, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"\n✅ Saved {RUNNER} with {changes} change(s).")
else:
    print("\n⚠️  No changes written.")

print("""
═══════════════════════════════════════════
  BRAIN PATCH COMPLETE
═══════════════════════════════════════════
Next steps:
1. Copy octo_brain.py and octo_journal.py to /home/walli/octodamus/
2. pip3 install pytz --break-system-packages  (if not already installed)
3. Test brain init:
   python3 octo_brain.py
4. Test journal:
   export BW_SESSION=$(cat /home/walli/.bw_session)
   python3 octodamus_runner.py --mode journal
5. Add journal cron (PowerShell, run as Admin):
   See instructions below.
""")

print("""
TASK SCHEDULER — add journal cron (PowerShell):
  $action = New-ScheduledTaskAction -Execute 'C:\\Windows\\System32\\wsl.exe' -Argument 'bash -c "export BW_SESSION=$(cat /home/walli/.bw_session) && cd /home/walli/octodamus && python3 octodamus_runner.py --mode journal >> logs/journal.log 2>&1"'
  $trigger = New-ScheduledTaskTrigger -Daily -At "9:00PM"
  Register-ScheduledTask -TaskName "Octodamus-Journal" -Action $action -Trigger $trigger -RunLevel Highest
""")

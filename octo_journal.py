"""
octo_journal.py -- OctoJournal Daily Learning Engine

Runs once per day (9pm PT via Task Scheduler: Octodamus-Journal).
Reads today's activity from BRAIN.md and the post log,
asks Claude to distill patterns and learnings,
then writes them back to BRAIN.md as persistent knowledge.

This is how Octodamus gets smarter every day.

Run: python3 octodamus_runner.py --mode journal
"""

import json
import sys
from datetime import datetime
from pathlib import Path

import anthropic
import pytz

TZ = pytz.timezone("America/Los_Angeles")


def _get_paths():
    """Return (log_path, journal_dir, brain_path, soul_path) for current platform."""
    if sys.platform == "win32":
        base = Path(r"C:\Users\walli\octodamus")
    else:
        base = Path("/home/walli/octodamus")

    return (
        base / "octo_posted_log.json",
        base / "journals",
        base / "BRAIN.md",
        base / "SOUL.md",
    )


LOG_PATH, JOURNAL_DIR, BRAIN_PATH, SOUL_PATH = _get_paths()
JOURNAL_DIR.mkdir(exist_ok=True)

# ── Journal system prompt — injects SOUL voice snippet ───────────────────────

def _build_journal_system() -> str:
    soul_snippet = ""
    if SOUL_PATH.exists():
        soul_raw = SOUL_PATH.read_text(encoding="utf-8")
        # Extract just Voice & Tone section for voice consistency
        start = soul_raw.find("## Voice & Tone")
        end   = soul_raw.find("\n---", start)
        if start != -1 and end != -1:
            soul_snippet = soul_raw[start:end].strip()

    base = """You are OctoBrain -- the distillation layer of Octodamus.
Your job is to review the day's activity and extract:
1. What market signals appeared and whether they led to good posts
2. What post types generated (or seem likely to generate) engagement
3. Any patterns worth remembering for future oracle posts
4. What the oracle should watch for tomorrow

Be specific and concrete. No generic observations.
Keep each observation under 120 chars.
Write in Octodamus voice -- bored certainty, not corporate analysis.
"""
    if soul_snippet:
        base += f"\n\nVoice reference from SOUL.md:\n{soul_snippet}"

    return base


def _load_todays_posts() -> list:
    if not LOG_PATH.exists():
        return []
    try:
        log   = json.loads(LOG_PATH.read_text(encoding="utf-8"))
        today = datetime.now(tz=TZ).date().isoformat()
        posts = []
        for entry in log.values():
            posted_at = entry.get("posted_at", "")
            if posted_at.startswith(today):
                posts.append({
                    "type": entry.get("type", "?"),
                    "text": entry.get("text", "")[:150],
                    "time": posted_at[11:16],
                })
        return posts
    except Exception as e:
        print(f"[OctoJournal] Could not load posts: {e}")
        return []


def _load_brain_snapshot() -> str:
    if BRAIN_PATH.exists():
        return BRAIN_PATH.read_text(encoding="utf-8")[:3000]
    return "No BRAIN.md found."


def run_journal() -> str:
    from octo_brain import append_learning, update_context

    today = datetime.now(tz=TZ).strftime("%Y-%m-%d")
    print(f"[OctoJournal] Running daily journal for {today}...")

    posts = _load_todays_posts()
    brain = _load_brain_snapshot()

    # Load core memory for personality continuity
    core_memory = ""
    try:
        from octo_memory_db import read_core_memory
        core_memory = read_core_memory("octodamus")
    except Exception:
        pass

    if not posts:
        print("[OctoJournal] No posts today -- writing minimal entry.")
        entry = f"[{today}] No posts today. Signals monitored, no threshold crossed."
        _write_journal_file(today, entry)
        return entry

    posts_summary = "\n".join(
        f"  [{p['time']}] [{p['type']}] {p['text']}" for p in posts
    )

    client   = anthropic.Anthropic()
    system   = _build_journal_system()

    core_section = f"\n\nCore memory (Octodamus's accumulated personality & lessons):\n{core_memory}" if core_memory else ""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=600,
        system=system,
        messages=[{
            "role": "user",
            "content": (
                f"Today is {today}. Here is what Octodamus posted:\n\n"
                f"{posts_summary}\n\n"
                f"Current BRAIN.md state:\n{brain}"
                f"{core_section}\n\n"
                "Distill 3-5 specific learnings from today. "
                "Format each as a single line starting with '- '. "
                "Focus on: what signals fired, what post types ran, "
                "what patterns are emerging, what to watch tomorrow. "
                "End with one line: CONTEXT_UPDATE: [one sentence on current market state]"
            ),
        }],
    )

    raw   = response.content[0].text.strip()
    lines = raw.splitlines()

    learnings = [l.strip() for l in lines if l.strip().startswith("-")]

    context_update = ""
    for l in lines:
        if l.startswith("CONTEXT_UPDATE:"):
            context_update = l.replace("CONTEXT_UPDATE:", "").strip()
            break

    for obs in learnings:
        obs_clean = obs.lstrip("- ").strip()
        append_learning(obs_clean, source="daily_journal")
        print(f"[OctoJournal] Learned: {obs_clean[:80]}")

    if context_update:
        update_context(context_update)
        print(f"[OctoJournal] Context: {context_update}")

    entry = (
        f"# Journal -- {today}\n\n"
        f"## Posts ({len(posts)})\n{posts_summary}\n\n"
        f"## Learnings\n{raw}"
    )
    _write_journal_file(today, entry)

    print(f"[OctoJournal] Journal complete. {len(learnings)} learnings saved to BRAIN.md.")
    return entry


def _write_journal_file(date: str, content: str):
    path = JOURNAL_DIR / f"{date}.md"
    path.write_text(content, encoding="utf-8")
    print(f"[OctoJournal] Written: {path}")


if __name__ == "__main__":
    run_journal()

"""
nunchi.py — Octodamus Post-Mortem Engine

"Nunchi" (눈치) — the Korean concept of reading a room, noticing what others miss.

For every resolved Oracle call, Nunchi writes a structured lesson to data/brain.md.
Over time, this builds a queryable map of which signals actually predict outcomes.

CLI:
  python nunchi.py run       Analyze all unanalyzed resolved calls
  python nunchi.py show      Print current brain (recent lessons)
  python nunchi.py context   Print the brain context injected into prompts
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

CALLS_FILE = Path(__file__).parent / "data" / "octo_calls.json"
BRAIN_FILE = Path(__file__).parent / "data" / "brain.md"
BRAIN_INDEX = Path(__file__).parent / "data" / "brain_index.json"

# Max lessons to inject into prompts
MAX_LESSONS_IN_CONTEXT = 5


# ── Persistence ───────────────────────────────────────────────────────────────

def _load_calls() -> list:
    try:
        if CALLS_FILE.exists():
            return json.loads(CALLS_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return []


def _load_index() -> set:
    """Returns set of call IDs already analyzed."""
    try:
        if BRAIN_INDEX.exists():
            return set(json.loads(BRAIN_INDEX.read_text(encoding="utf-8")))
    except Exception:
        pass
    return set()


def _save_index(analyzed_ids: set):
    BRAIN_INDEX.parent.mkdir(parents=True, exist_ok=True)
    BRAIN_INDEX.write_text(json.dumps(sorted(analyzed_ids)), encoding="utf-8")


def _append_to_brain(entry: str):
    BRAIN_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not BRAIN_FILE.exists():
        BRAIN_FILE.write_text("# Octodamus Brain — Signal Post-Mortems\n\n", encoding="utf-8")
    with open(BRAIN_FILE, "a", encoding="utf-8") as f:
        f.write(entry + "\n\n")


# ── Analysis ──────────────────────────────────────────────────────────────────

def _build_postmortem_prompt(call: dict) -> str:
    direction = call["direction"]
    entry = call["entry_price"]
    exit_p = call["exit_price"]
    pct = ((exit_p - entry) / entry) * 100
    move_desc = f"${entry:,.2f} → ${exit_p:,.2f} ({pct:+.1f}%)"
    correct = (direction == "UP" and exit_p > entry) or (direction == "DOWN" and exit_p < entry)

    return f"""You are Nunchi, the post-mortem engine for Octodamus — a crypto/market oracle AI.

A directional call just resolved. Analyze it.

CALL DATA:
- Asset: {call["asset"]}
- Direction called: {direction}
- Entry: ${entry:,.2f}  |  Exit: ${exit_p:,.2f}
- Move: {move_desc}
- Outcome: {call["outcome"]}
- Timeframe: {call["timeframe"]}
- Signal note (what Octodamus saw): "{call.get("note", "no note recorded")}"
- Made: {call["made_at"]}  |  Resolved: {call["resolved_at"]}

Write a structured post-mortem in exactly this format (3-5 sentences total):

## Call #{call["id"]}: {call["asset"]} {direction} — {call["outcome"]} ({call["made_at"][:10]})
**Move:** {move_desc}
**What Octodamus saw:** [summarize the signal note in plain language]
**Why it {"worked" if correct else "failed"}:** [1-2 sentences on what the signal was picking up, or what it missed]
**Lesson:** [1 sentence starting with an actionable rule — e.g. "When X, weight Y more / be cautious of Z"]
**Signal type:** [tag the primary signal — one of: FUNDING_RATE | LIQUIDATION_MAP | OI_SHIFT | MOMENTUM | SENTIMENT | MACRO_NEWS | TECHNICAL | OB_DIVERGENCE | UNKNOWN]

Be specific and honest. If the call was lucky, say so. If the signal was genuinely predictive, explain why. Avoid generic observations."""


def analyze_call(call: dict, claude_client) -> str:
    """Send a resolved call to Claude for post-mortem. Returns the markdown entry."""
    prompt = _build_postmortem_prompt(call)
    response = claude_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text.strip()


# ── Main loop ─────────────────────────────────────────────────────────────────

def run_postmortems(claude_client=None, verbose=True) -> list:
    """
    Analyze all resolved calls not yet in the brain.
    Returns list of newly analyzed call IDs.

    If claude_client is None, imports anthropic and creates one.
    """
    if claude_client is None:
        import anthropic
        claude_client = anthropic.Anthropic()

    calls = _load_calls()
    analyzed = _load_index()
    newly_analyzed = []

    resolved = [c for c in calls if c["resolved"] and c["outcome"] in ("WIN", "LOSS")]

    for call in resolved:
        cid = call["id"]
        if cid in analyzed:
            continue

        if verbose:
            print(f"[Nunchi] Analyzing call #{cid}: {call['asset']} {call['direction']} -> {call['outcome']}")

        try:
            entry = analyze_call(call, claude_client)
            _append_to_brain(entry)
            analyzed.add(cid)
            _save_index(analyzed)
            newly_analyzed.append(cid)
            if verbose:
                print(f"[Nunchi] Lesson written for #{cid}.")
        except Exception as e:
            print(f"[Nunchi] Failed to analyze #{cid}: {e}")

    if not newly_analyzed and verbose:
        print("[Nunchi] No new resolved calls to analyze.")

    return newly_analyzed


# ── Prompt injection ──────────────────────────────────────────────────────────

def get_brain_context(max_lessons: int = MAX_LESSONS_IN_CONTEXT) -> str:
    """
    Returns the most recent N lessons from brain.md for prompt injection.
    Returns empty string if brain is empty or doesn't exist.
    """
    if not BRAIN_FILE.exists():
        return ""

    text = BRAIN_FILE.read_text(encoding="utf-8")
    # Split on call headers
    sections = [s.strip() for s in text.split("## Call #") if s.strip()]
    if not sections:
        return ""

    # Take the most recent N
    recent = sections[-max_lessons:]
    lessons = "\n\n".join(f"## Call #{s}" for s in recent)

    return f"── NUNCHI BRAIN (recent lessons — let these inform your signal weighting) ──\n{lessons}"


# ── CLI ───────────────────────────────────────────────────────────────────────

def _show_brain():
    if not BRAIN_FILE.exists():
        print("[Nunchi] No brain yet. Run: python nunchi.py run")
        return
    sys.stdout.buffer.write(BRAIN_FILE.read_bytes())


if __name__ == "__main__":
    args = sys.argv[1:]

    if not args or args[0] == "run":
        # Load secrets from cache so ANTHROPIC_API_KEY is set
        try:
            from bitwarden import load_all_secrets
            load_all_secrets(verbose=False)
        except Exception:
            pass
        run_postmortems(verbose=True)

    elif args[0] == "show":
        _show_brain()

    elif args[0] == "context":
        ctx = get_brain_context()
        print(ctx if ctx else "[Nunchi] Brain is empty.")

    else:
        print("Usage:")
        print("  python nunchi.py run       Analyze all unanalyzed resolved calls")
        print("  python nunchi.py show      Print full brain.md")
        print("  python nunchi.py context   Print what gets injected into prompts")

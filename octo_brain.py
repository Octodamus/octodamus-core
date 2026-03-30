"""
octo_brain.py — OctoBrain Layer 0 Working Memory

Reads and writes BRAIN.md — the persistent memory that survives between runs.
Injected into every prompt so Octodamus remembers what it saw, posted, and learned.

Structure of BRAIN.md:
  ## CURRENT CONTEXT   — what's happening in markets right now
  ## RECENT SIGNALS    — last N market observations
  ## POST PERFORMANCE  — last N posts logged
  ## LEARNED PATTERNS  — oracle observations that compound over time
  ## PLAYBOOK          — proven workflows and debug procedures

Usage:
    from octo_brain import read_brain, append_signal, append_post_result, append_learning
"""

import sys
from datetime import datetime
from pathlib import Path

import pytz

TZ          = pytz.timezone("America/Los_Angeles")
MAX_SIGNALS = 20
MAX_POSTS   = 30
MAX_LEARN   = 50


def _get_brain_path() -> Path:
    """Return correct BRAIN.md path for Windows Python or WSL Python."""
    if sys.platform == "win32":
        return Path(r"C:\Users\walli\octodamus\BRAIN.md")
    wsl_path = Path("/home/walli/octodamus/BRAIN.md")
    if wsl_path.parent.exists():
        return wsl_path
    return Path(__file__).parent / "BRAIN.md"


BRAIN_PATH = _get_brain_path()

BRAIN_TEMPLATE = """# BRAIN.md -- Octodamus Working Memory
# Layer 0: Updated automatically every run. Never edit manually.
# Last updated: {date}

## CURRENT CONTEXT

No context yet.

## RECENT SIGNALS

No signals yet.

## POST PERFORMANCE

No posts logged yet.

## LEARNED PATTERNS

No patterns yet.

## PLAYBOOK

No playbook yet.
"""


def _ensure_brain():
    if not BRAIN_PATH.exists():
        BRAIN_PATH.parent.mkdir(parents=True, exist_ok=True)
        BRAIN_PATH.write_text(
            BRAIN_TEMPLATE.format(date=datetime.now(tz=TZ).strftime("%Y-%m-%d")),
            encoding="utf-8",
        )
        print("[OctoBrain] BRAIN.md initialised.")


def read_brain(max_chars: int = 3000) -> str:
    _ensure_brain()
    content = BRAIN_PATH.read_text(encoding="utf-8")
    if len(content) > max_chars:
        content = content[-max_chars:]
        content = "...[BRAIN truncated]\n" + content
    return content


def _parse_sections() -> dict:
    _ensure_brain()
    content  = BRAIN_PATH.read_text(encoding="utf-8")
    sections = {}
    current  = None
    lines    = []
    for line in content.splitlines():
        if line.startswith("## "):
            if current is not None:
                sections[current] = "\n".join(lines).strip()
            current = line[3:].strip()
            lines   = []
        else:
            lines.append(line)
    if current is not None:
        sections[current] = "\n".join(lines).strip()
    return sections


def _write_sections(sections: dict):
    now   = datetime.now(tz=TZ).strftime("%Y-%m-%d %H:%M PT")
    lines = [
        "# BRAIN.md -- Octodamus Working Memory",
        "# Layer 0: Updated automatically every run. Never edit manually.",
        f"# Last updated: {now}",
        "",
    ]
    for title, body in sections.items():
        lines.append(f"## {title}")
        lines.append(body if body and body.strip() else f"No {title.lower()} yet.")
        lines.append("")
    BRAIN_PATH.write_text("\n".join(lines), encoding="utf-8")


def update_context(context_str: str):
    _ensure_brain()
    sections = _parse_sections()
    now = datetime.now(tz=TZ).strftime("%Y-%m-%d %H:%M PT")
    sections["CURRENT CONTEXT"] = f"Updated: {now}\n{context_str}"
    _write_sections(sections)


def append_signal(signal: dict):
    _ensure_brain()
    sections = _parse_sections()
    now      = datetime.now(tz=TZ).strftime("%Y-%m-%d %H:%M PT")
    existing = sections.get("RECENT SIGNALS", "")
    entries  = [l for l in existing.splitlines() if l.strip().startswith("-")]
    entries.append(
        f"- [{now}] {signal.get('ticker','?')} | "
        f"{signal.get('type','signal')} | "
        f"{signal.get('detail','')} | "
        f"src:{signal.get('source','')}"
    )
    entries = entries[-MAX_SIGNALS:]
    sections["RECENT SIGNALS"] = "\n".join(entries)
    _write_sections(sections)


def append_post_result(post_text: str, post_type: str, post_id: str = ""):
    _ensure_brain()
    sections  = _parse_sections()
    now       = datetime.now(tz=TZ).strftime("%Y-%m-%d %H:%M PT")
    existing  = sections.get("POST PERFORMANCE", "")
    entries   = [l for l in existing.splitlines() if l.strip().startswith("-")]
    short     = post_text[:80].replace("\n", " ")
    new_entry = f"- [{now}] [{post_type}] {short}..."
    if post_id:
        new_entry += f" | id:{post_id}"
    entries.append(new_entry)
    entries = entries[-MAX_POSTS:]
    sections["POST PERFORMANCE"] = "\n".join(entries)
    _write_sections(sections)


def append_learning(observation: str, source: str = "auto"):
    _ensure_brain()
    sections = _parse_sections()
    now      = datetime.now(tz=TZ).strftime("%Y-%m-%d")
    existing = sections.get("LEARNED PATTERNS", "")
    entries  = [l for l in existing.splitlines() if l.strip().startswith("-")]
    entries.append(f"- [{now}] [{source}] {observation}")
    entries = entries[-MAX_LEARN:]
    sections["LEARNED PATTERNS"] = "\n".join(entries)
    _write_sections(sections)


def update_playbook(entry: str):
    _ensure_brain()
    sections = _parse_sections()
    existing = sections.get("PLAYBOOK", "")
    sections["PLAYBOOK"] = (existing + "\n\n" + entry).strip() if existing.strip() else entry
    _write_sections(sections)


def format_brain_for_prompt(max_chars: int = 2000) -> str:
    content = read_brain(max_chars=max_chars)
    return f"[OctoBrain -- working memory]\n{content}\n[/OctoBrain]"


if __name__ == "__main__":
    _ensure_brain()
    print(f"BRAIN_PATH: {BRAIN_PATH}")
    print(read_brain())

"""
octo_builders.py — Octodamus Ecosystem Builder Intelligence

Ingests weekly "Top AI Builders" digests and similar curated signals.
Claude extracts dominant patterns, emerging standards, and actionable signals.
Stored in data/octo_builders.json and injected into runner context.

Usage:
  python octo_builders.py ingest           Paste builder list interactively
  python octo_builders.py ingest <file>    Ingest from text file
  python octo_builders.py show             Show latest analysis
  python octo_builders.py context          Print context block for runner
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "data"
BUILDERS_FILE = DATA_DIR / "octo_builders.json"

CONTEXT_FRESHNESS_HOURS = 168  # 1 week


# ── Secrets bootstrap ─────────────────────────────────────────────────────────

def _ensure_secrets():
    if os.environ.get("ANTHROPIC_API_KEY"):
        return
    try:
        sys.path.insert(0, str(BASE_DIR))
        from bitwarden import load_all_secrets
        load_all_secrets()
    except Exception:
        pass

_ensure_secrets()


# ── Storage ───────────────────────────────────────────────────────────────────

def _load() -> list:
    if BUILDERS_FILE.exists():
        try:
            return json.loads(BUILDERS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def _save(entries: list):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BUILDERS_FILE.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")


# ── Claude analysis ───────────────────────────────────────────────────────────

def _analyse(raw_text: str) -> dict:
    """
    Claude reads the raw builder list and extracts dominant patterns,
    emerging standards, market signals, and a post hook.
    """
    import anthropic
    client = anthropic.Anthropic()

    prompt = f"""You are the intelligence engine for Octodamus — an autonomous AI oracle tracking the agent economy, Bitcoin, and where builders are actually shipping.

Here is a weekly "Top AI Builders" digest:

---
{raw_text}
---

Extract structured intelligence. Think like a pattern-recognition system, not a summariser.

Look for:
- What protocol/standard/primitive appears across multiple builders? (e.g. x402, MCP, a specific chain, a specific model)
- What does that convergence signal about where agent infrastructure is heading?
- Is there a specific asset or sector play implied by what's being built?
- What is the single sharpest, most non-obvious observation from this list?

Note: "OpenClaw" in any of these entries refers to Claude (Anthropic's AI). Treat it as Claude/Claude Code.

Return JSON only. No commentary outside the JSON.

{{
  "dominant_pattern": "1-2 sentence description of the #1 repeating theme across builders",
  "emerging_standards": ["x402", "MCP", "Claude"],
  "convergence_count": {{"x402": 5, "MCP": 2}},
  "market_signal": "Specific, concrete signal for an asset or sector. Not vague.",
  "post_hook": "One sharp sentence Octodamus could post as his own thought — no citation, no source mention",
  "builders": [
    {{"handle": "@HeyElsaAI", "shipped": "concise one-liner of what they built"}},
    ...
  ],
  "relevance": 1-10
}}"""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())
    except Exception as e:
        print(f"[Builders] Analysis failed: {e}")
        return {
            "dominant_pattern": "",
            "emerging_standards": [],
            "convergence_count": {},
            "market_signal": "",
            "post_hook": "",
            "builders": [],
            "relevance": 5,
        }


# ── Ingest ────────────────────────────────────────────────────────────────────

def ingest(raw_text: str, source: str = "manual") -> dict:
    """Parse a builder digest and store the entry. Returns the entry."""
    print("[Builders] Analysing builder list...")
    analysis = _analyse(raw_text)

    entry = {
        "ingested_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "source": source,
        "raw": raw_text.strip(),
        "analysis": analysis,
    }

    entries = _load()
    entries.append(entry)
    if len(entries) > 52:  # keep 1 year of weeklies
        entries = entries[-52:]
    _save(entries)

    pattern = analysis.get("dominant_pattern", "")
    standards = analysis.get("emerging_standards", [])
    conv = analysis.get("convergence_count", {})
    relevance = analysis.get("relevance", "?")

    print(f"[Builders] Done. Relevance: {relevance}/10")
    print(f"[Builders] Pattern: {pattern[:100]}")
    if standards:
        conv_str = ", ".join(
            f"{s}(x{conv[s]})" if s in conv else s for s in standards
        )
        print(f"[Builders] Standards: {conv_str}")

    hook = analysis.get("post_hook", "")
    if hook:
        print(f"[Builders] Post hook: {hook}")

    return entry


# ── Context injection ─────────────────────────────────────────────────────────

def build_builders_context() -> str:
    """
    Return the latest builder pulse as a compact context block for runner prompts.
    Returns empty string if nothing within CONTEXT_FRESHNESS_HOURS.
    """
    entries = _load()
    if not entries:
        return ""

    latest = entries[-1]
    cutoff = datetime.now(timezone.utc) - timedelta(hours=CONTEXT_FRESHNESS_HOURS)
    try:
        ingested = datetime.strptime(latest["ingested_at"], "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)
    except Exception:
        return ""

    if ingested < cutoff:
        return ""

    a = latest["analysis"]
    lines = ["── BUILDER ECOSYSTEM (this week) ──"]

    if a.get("dominant_pattern"):
        lines.append(f"Pattern: {a['dominant_pattern']}")

    standards = a.get("emerging_standards", [])
    conv = a.get("convergence_count", {})
    if standards:
        conv_str = ", ".join(
            f"{s}(x{conv.get(s,'')})" if s in conv else s for s in standards
        )
        lines.append(f"Converging on: {conv_str}")

    if a.get("market_signal"):
        lines.append(f"Signal: {a['market_signal']}")

    return "\n".join(lines)


def get_post_hook() -> str | None:
    """Return the post_hook from the latest entry if still fresh."""
    entries = _load()
    if not entries:
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(hours=CONTEXT_FRESHNESS_HOURS)
    latest = entries[-1]
    try:
        ingested = datetime.strptime(latest["ingested_at"], "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)
        if ingested < cutoff:
            return None
    except Exception:
        return None
    return latest["analysis"].get("post_hook")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = sys.argv[1:]

    if not args or args[0] == "ingest":
        if len(args) > 1:
            p = Path(args[1])
            if not p.exists():
                print(f"File not found: {args[1]}")
                sys.exit(1)
            raw = p.read_text(encoding="utf-8")
        else:
            print("Paste the builder list. Press Ctrl+Z then Enter (Windows) when done:\n")
            raw = sys.stdin.read()

        if not raw.strip():
            print("No input.")
            sys.exit(1)

        entry = ingest(raw)
        hook = entry["analysis"].get("post_hook", "")
        if hook:
            print(f"\nPost hook ready:\n  {hook}")

    elif args[0] == "show":
        entries = _load()
        if not entries:
            print("No builder data yet.")
        else:
            latest = entries[-1]
            a = latest["analysis"]
            print(f"\nLatest: {latest['ingested_at']} | Relevance: {a.get('relevance','?')}/10")
            print(f"Pattern: {a.get('dominant_pattern','')}")
            print(f"Standards: {', '.join(a.get('emerging_standards',[]))}")
            print(f"Signal: {a.get('market_signal','')}")
            print(f"Post hook: {a.get('post_hook','')}")
            print(f"\nBuilders ({len(a.get('builders',[]))}):")
            for b in a.get("builders", []):
                print(f"  {b['handle']}: {b['shipped']}")

    elif args[0] == "context":
        ctx = build_builders_context()
        sys.stdout.buffer.write(((ctx if ctx else "(no fresh builder data)") + "\n").encode("utf-8", errors="replace"))

    else:
        print(__doc__)

"""
octo_dream_fleet.py — fleet-wide "head teacher" dreaming pass.

The per-agent distill (octo_memory_distill) improves each agent in isolation, so it
can't see patterns that span the fleet: a mistake several agents make the same way, a
knowledge gap common to all of them, or two agents solving the same thing differently.
This pass reviews EVERY agent's core memory + the fleet's recurring errors together and
proposes cross-agent changes for the operator to review (it does NOT auto-write to any
shared context — per the lecture, dreaming proposes, humans approve).

Wired into octo_memory_distill.run() (Sun/Wed/Sat). Standalone:
    python octo_dream_fleet.py [--no-email]
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR   = Path(__file__).parent
MEMORY_DIR = BASE_DIR / "data" / "memory"
SECRETS    = BASE_DIR / ".octo_secrets"
REPORT     = BASE_DIR / "logs" / "dream_fleet_latest.md"

_EXCERPT_CHARS = 1400   # per-agent core-memory slice fed to the analysis


def _anthropic_key() -> str:
    import os
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if key:
        return key
    try:
        d = json.loads(SECRETS.read_text(encoding="utf-8"))
        key = d.get("ANTHROPIC_API_KEY") or d.get("secrets", {}).get("ANTHROPIC_API_KEY", "")
    except Exception:
        key = ""
    if not key:
        try:
            from bitwarden import load_all_secrets
            load_all_secrets()
            key = os.environ.get("ANTHROPIC_API_KEY", "")
        except Exception:
            pass
    return key


def _haiku(prompt: str, system: str) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=_anthropic_key())
    r = client.messages.create(
        model="claude-haiku-4-5-20251001", max_tokens=1600,
        system=system, messages=[{"role": "user", "content": prompt}],
    )
    return r.content[0].text.strip()


def _gather_digest() -> str:
    """Compact fleet digest: each agent's core memory excerpt + recurring fleet errors."""
    parts = []
    for f in sorted(MEMORY_DIR.glob("*_core.md")):
        agent = f.stem.replace("_core", "")
        try:
            text = f.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        excerpt = text[:_EXCERPT_CHARS] + ("..." if len(text) > _EXCERPT_CHARS else "")
        parts.append(f"### AGENT: {agent}\n{excerpt}")

    # Fleet-wide recurring errors (from the tool-error dreaming pass).
    try:
        from octo_dream_toolscan import scan as _scan
        errs = _scan(days=7, min_count=4)[:10]
        if errs:
            elines = [f"- x{e['count']} ({e['n_sources']} src): {e['sample'][:140]}" for e in errs]
            parts.append("### FLEET RECURRING ERRORS (last 7d)\n" + "\n".join(elines))
    except Exception:
        pass
    return "\n\n".join(parts)


_SYSTEM = (
    "You are the head teacher reviewing an entire fleet of autonomous trading/intelligence agents "
    "(Octodamus and its sub-agents). You have visibility no single agent has. Be precise, cite which "
    "agents, and only report patterns evidenced in the provided material. Never invent."
)

_PROMPT_TMPL = """Below is each agent's distilled core memory plus the fleet's recurring errors.
Find CROSS-AGENT patterns that no single agent can see, in priority order:

1. SHARED MISTAKES — something multiple agents get wrong the same way.
2. KNOWLEDGE GAPS — something missing across several agents' memories that they all need.
3. INCONSISTENCIES — agents handling the same thing differently (should be standardized).
4. FLEET OPPORTUNITIES — a practice or signal all agents should adopt but few do.

For each finding: name the agents involved, describe the pattern in one line, and propose ONE
concrete change (a shared instruction to add, or a specific per-agent fix). Skip a category if there's
no real evidence. Max 550 words. End with a one-line "TOP ACTION:" — the single highest-impact change.

FLEET MATERIAL:
{digest}
"""


def run(email: bool = True) -> str:
    digest = _gather_digest()
    if not digest.strip():
        print("[FleetDream] No core memories found; skipping.")
        return ""
    try:
        analysis = _haiku(_PROMPT_TMPL.format(digest=digest), _SYSTEM)
    except Exception as e:
        print(f"[FleetDream] Analysis failed: {e}")
        return ""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    report = (f"# Fleet-wide dream — {now}\n\n"
              f"Cross-agent patterns across {len(list(MEMORY_DIR.glob('*_core.md')))} agents "
              f"(proposals for review — nothing auto-applied):\n\n{analysis}\n")
    try:
        REPORT.write_text(report, encoding="utf-8")
    except OSError:
        pass
    print("[FleetDream] analysis:\n" + analysis[:600])
    if email:
        try:
            from octo_notify import _send
            _send("Octodamus Fleet Dream — cross-agent review", report)
            print("[FleetDream] Emailed operator.")
        except Exception as e:
            print(f"[FleetDream] Email failed: {e}")
    return analysis


if __name__ == "__main__":
    run(email="--no-email" not in sys.argv)

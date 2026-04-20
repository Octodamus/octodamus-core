"""
octo_ceo.py -- OctodamusCEO: Marketing intelligence sandbox
Autonomous CEO mode: research, positioning, newsletter, growth strategy.
Traffic -> Holding Pattern -> Selling Event -> Conversion
USAGE:
    python octo_ceo.py [research|brief|newsletter|position|plan|memory|state|set]
    from octo_ceo import run_ceo_research, get_ceo_brief
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path
import anthropic

ROOT        = Path(__file__).parent
STATE_FILE  = ROOT / "data" / "ceo_state.json"
MEMORY_FILE = ROOT / "data" / "ceo_memory.json"


def _load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"phase": "traffic", "subscribers": 0, "mrr": 0, "last_research": None, "last_newsletter": None}

def _save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    state["_updated"] = datetime.now().isoformat()
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")

def _load_memory() -> list:
    if MEMORY_FILE.exists():
        return json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
    return []

def _save_memory_entry(entry: dict):
    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    memory = _load_memory()
    entry["timestamp"] = datetime.now().isoformat()
    memory.append(entry)
    MEMORY_FILE.write_text(json.dumps(memory[-50:], indent=2), encoding="utf-8")

def _memory_context() -> str:
    memory = _load_memory()
    if not memory:
        return ""
    lines = ["CEO MEMORY (recent):"]
    for m in memory[-10:]:
        ts = m.get("timestamp", "")[:10]
        lines.append(f"  [{ts}] {m.get('type', 'note')}: {m.get('content', '')[:200]}")
    return "\n".join(lines)

def _load_secrets() -> dict:
    p = ROOT / ".octo_secrets"
    if p.exists():
        raw = json.loads(p.read_text(encoding="utf-8"))
        # Cache format: {"saved_at": ..., "secrets": {...}}
        return raw.get("secrets", raw)
    return {}

def _anthropic_client():
    key = os.environ.get("ANTHROPIC_API_KEY", "") or _load_secrets().get("ANTHROPIC_API_KEY", "")
    return anthropic.Anthropic(api_key=key)


CEO_SYSTEM = (
    "You are OctodamusCEO -- the business mind behind Octodamus, the AI market oracle.\n\n"
    "ROLE: Promoter, strategist, growth operator. Oracle generates signal. You make sure the right people find it, trust it, and pay for it.\n\n"
    "PLAYBOOK (Traffic -> Holding Pattern -> Selling Event -> Conversion):\n"
    "- Traffic: Get people to octodamus.com and @octodamusai. Free tools, MCP, viral posts.\n"
    "- Holding Pattern: Keep them subscribed. Email + X. Value between selling events.\n"
    "- Selling Event: Clear time-bound offer after a big oracle win.\n"
    "- Conversion: $29/year on Base via x402. Track MRR. Reinvest at milestones.\n\n"
    "PRICING:\n"
    "- $29/year intentionally low -- filtering mechanism, not main revenue driver.\n"
    "- Per-task API pricing (x402) is the real model.\n"
    "- Kiyotaka: raw data $99-599/mo. Octodamus: AI-interpreted signals $29/yr. 20x cheaper.\n\n"
    "MILESTONES:\n"
    "- $500/mo: Subscribe Unusual Whales, consider newsletter acquisition\n"
    "- $2k/mo: Programmatic SEO (10k pages)\n"
    "- $5k/mo: Content repurposing engine\n"
    "- Newsletter launch: July 2026+ (3-month track record required)\n\n"
    "NEWSLETTER (OctoIntel Weekly): Beehiiv, free to 2,500 subs ($42/mo after), CTA=$29/yr\n\n"
    "RULES: Never launch newsletter before 3-month track record. Never buy ads before $500/mo. Let the track record sell."
)


def run_ceo_research(focus: str = "general") -> dict:
    try:
        from octo_firecrawl import (
            competitor_intel, summarize_competitors, market_research,
            get_datarade_intel, run_monthly_competitor_monitor,
        )
        firecrawl_ok = True
    except Exception:
        firecrawl_ok = False

    state = _load_state()
    client = _anthropic_client()
    blocks = []

    if firecrawl_ok:
        if focus in ("competitors", "general", "positioning"):
            print("[CEO] Scraping competitors...")
            try:
                intel = competitor_intel(names=["kiyotaka", "messari"])
                blocks.append(summarize_competitors(intel))
            except Exception as e:
                blocks.append(f"Competitor scrape failed: {e}")

        if focus in ("datarade", "positioning", "general"):
            print("[CEO] Scraping Datarade...")
            try:
                blocks.append(get_datarade_intel(cache_hours=48.0))
            except Exception as e:
                blocks.append(f"Datarade scrape failed: {e}")
        if focus in ("customers", "general"):
            print("[CEO] Customer research...")
            try:
                blocks.append(market_research("crypto data API demand 2026 market intelligence", cache_hours=12))
            except Exception as e:
                blocks.append(f"Customer research failed: {e}")
        if focus == "newsletter":
            print("[CEO] Newsletter landscape...")
            try:
                blocks.append(market_research("crypto newsletter beehiiv 2026 audience monetization", cache_hours=12))
            except Exception as e:
                blocks.append(f"Newsletter research failed: {e}")
    else:
        blocks.append("Firecrawl not available -- add FIRECRAWL_API_KEY to .octo_secrets")

    calls_path = ROOT / "data" / "octo_calls.json"
    if calls_path.exists():
        calls = json.loads(calls_path.read_text(encoding="utf-8"))
        resolved = [c for c in calls if c.get("status") in ("win", "loss")]
        wins = sum(1 for c in resolved if c.get("status") == "win")
        blocks.append(f"ORACLE TRACK RECORD: {wins}W/{len(resolved) - wins}L ({len(resolved)} resolved, {len(calls) - len(resolved)} open)")

    subs_path = ROOT / "data" / "subscribers.json"
    if subs_path.exists():
        subs = json.loads(subs_path.read_text(encoding="utf-8"))
        blocks.append(f"CURRENT SUBSCRIBERS: {len(subs)} email signups")

    memory_ctx = _memory_context()
    research = "\n\n".join(blocks)

    prompt = (
        f"Today: {datetime.now().strftime('%Y-%m-%d')}\n\n"
        + (f"{memory_ctx}\n\n" if memory_ctx else "")
        + f"STATE: Phase={state['phase']} | MRR=${state['mrr']}/mo | Subs={state['subscribers']}\n\n"
        + f"RESEARCH:\n{research}\n\n"
        + f"TASK: Focus={focus}\n"
        + "Output JSON with keys: actions (list of 3 strings), competitive_insight, content_idea, phase_assessment, memory_note.\n"
        + "Be specific. Cite numbers. No vague advice."
    )

    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1200,
        system=CEO_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = msg.content[0].text.strip()

    try:
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()
        result = json.loads(raw)
    except Exception:
        result = {"raw": raw}

    _save_memory_entry({"type": "research", "focus": focus, "content": result.get("memory_note", f"research focus={focus}")})
    state["last_research"] = datetime.now().isoformat()
    _save_state(state)
    return result


def get_ceo_brief() -> str:
    state = _load_state()
    memory = _load_memory()
    last = next((m.get("content", "") for m in reversed(memory) if m.get("type") == "research"), "no research yet")
    return f"CEO: {state['phase'].upper()} | MRR=${state['mrr']}/mo | Subs={state['subscribers']} | {last[:100]}"


def draft_newsletter_issue(week_context: str = "") -> str:
    client = _anthropic_client()
    calls_path = ROOT / "data" / "octo_calls.json"
    calls_summary = "No oracle calls loaded."
    if calls_path.exists():
        calls = json.loads(calls_path.read_text(encoding="utf-8"))
        resolved = [c for c in calls if c.get("status") in ("win", "loss")]
        wins = sum(1 for c in resolved if c.get("status") == "win")
        recent_wins = len([c for c in resolved[-10:] if c.get("status") == "win"])
        calls_summary = f"All-time: {wins}W/{len(resolved) - wins}L | Recent (last 10): {recent_wins}/10 | Open: {len(calls) - len(resolved)}"

    prompt = (
        f"Draft OctoIntel Weekly. Date: {datetime.now().strftime('%B %d, %Y')}\n\n"
        + f"ORACLE SCORECARD:\n{calls_summary}\n\n"
        + f"WEEK CONTEXT:\n{week_context or 'Current crypto market conditions.'}\n\n"
        + "Newsletter (plain text, no markdown headers, under 500 words):\n"
        + "1. HEADLINE SIGNAL (1 sentence)\n"
        + "2. ORACLE SCORECARD UPDATE (2-3 sentences)\n"
        + "3. SIGNAL OF THE WEEK (2-3 sentences)\n"
        + "4. THE READ -- Octodamus interpretation, directional (3-4 sentences)\n"
        + "5. CTA: Get the full signal feed -- $29/year. api.octodamus.com\n\n"
        + "Voice: FRONTIER ORACLE. Specific numbers. No vague takes."
    )

    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1000,
        system=CEO_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    draft = msg.content[0].text.strip()
    _save_memory_entry({"type": "newsletter_draft", "content": f"Newsletter drafted week {datetime.now().strftime('%U')}"})
    return draft


def generate_video_brief(topic: str) -> str:
    client = _anthropic_client()
    calls_path = ROOT / "data" / "octo_calls.json"
    scorecard = "No oracle calls loaded."
    if calls_path.exists():
        calls = json.loads(calls_path.read_text(encoding="utf-8"))
        resolved = [c for c in calls if c.get("status") in ("win", "loss")]
        wins = sum(1 for c in resolved if c.get("status") == "win")
        scorecard = f"{wins}W/{len(resolved)-wins}L all-time | {len(calls)-len(resolved)} open calls"

    prompt = (
        f"Generate a HyperFrames video brief for Octodamus YouTube channel.\n\n"
        f"TOPIC: {topic}\n"
        f"ORACLE SCORECARD: {scorecard}\n"
        f"DATE: {datetime.now().strftime('%B %d, %Y')}\n\n"
        f"Output a complete video brief with:\n"
        f"1. VIDEO TITLE (punchy, under 60 chars, no clickbait)\n"
        f"2. FORMAT (signal_breakdown|oracle_reveal|weekly_recap|market_regime)\n"
        f"3. DURATION (15s|30s|60s|90s)\n"
        f"4. ASPECT RATIO (1920x1080 landscape or 1080x1920 shorts)\n"
        f"5. SCENE PLAN (numbered list, each scene: content + duration + key visual element)\n"
        f"6. KEY DATA POINTS to display (prices, percentages, signal readings)\n"
        f"7. HOOK LINE (first 3 seconds -- what makes someone stop scrolling)\n"
        f"8. CTA (end card text)\n"
        f"9. SUGGESTED TAGS (5-8 YouTube tags)\n\n"
        f"Voice: Octodamus oracle. Specific. Directional. No vague takes.\n"
        f"Visual identity: Shadow Cut + Swiss Pulse. Dark ocean palette. Patient pacing.\n"
        f"The video should feel like signal from depth, not noise from the surface."
    )

    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        system=CEO_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    brief = msg.content[0].text.strip()
    _save_memory_entry({"type": "video_brief", "content": f"Brief: {topic[:80]}"})
    return brief


def update_state(key: str, value):
    state = _load_state()
    state[key] = value
    _save_state(state)
    print(f"[CEO] {key} = {value}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "brief"

    if cmd == "research":
        focus = sys.argv[2] if len(sys.argv) > 2 else "general"
        print(f"[CEO] Research: focus={focus}")
        result = run_ceo_research(focus)
        print("\n=== CEO RESEARCH ===")
        if "actions" in result:
            print("\nACTIONS:")
            for i, a in enumerate(result.get("actions", []), 1):
                print(f"  {i}. {a}")
            print(f"\nCOMPETITIVE INSIGHT: {result.get('competitive_insight', '')}")
            print(f"\nCONTENT IDEA: {result.get('content_idea', '')}")
            print(f"\nPHASE: {result.get('phase_assessment', '')}")
        else:
            print(json.dumps(result, indent=2))
    elif cmd == "brief":
        print(get_ceo_brief())
    elif cmd == "newsletter":
        ctx = " ".join(sys.argv[2:])
        print("[CEO] Drafting newsletter...")
        print(draft_newsletter_issue(ctx))
    elif cmd == "memory":
        mem = _load_memory()
        print(f"CEO Memory ({len(mem)} entries):")
        for m in mem[-20:]:
            ts = m.get("timestamp", "")[:10]
            print(f"  [{ts}] {m.get('type', 'note')}: {m.get('content', '')[:120]}")
    elif cmd == "state":
        print(json.dumps(_load_state(), indent=2))
    elif cmd == "set":
        if len(sys.argv) < 4:
            print("Usage: python octo_ceo.py set <key> <value>")
        else:
            val = sys.argv[3]
            try:
                val = int(val)
            except ValueError:
                pass
            update_state(sys.argv[2], val)
    elif cmd in ("position", "competitors"):
        print("[CEO] Positioning research...")
        print(json.dumps(run_ceo_research("positioning"), indent=2))
    elif cmd == "plan":
        plan_path = ROOT / ".claude" / "octodamus_ceo_plan.md"
        if plan_path.exists():
            print(plan_path.read_text(encoding="utf-8"))
        else:
            print("No plan found: .claude/octodamus_ceo_plan.md")
    elif cmd == "video":
        topic = " ".join(sys.argv[2:]) or "latest BTC signal confluence"
        print(f"[CEO] Generating video brief: {topic}")
        brief = generate_video_brief(topic)
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        print("\n=== VIDEO BRIEF ===\n")
        print(brief)
        print("\n=== NEXT STEPS ===")
        print("1. python octo_hyperframes.py scaffold <project-name>")
        print("2. Edit videos/<project-name>/index.html using the brief above")
        print("3. python octo_hyperframes.py lint <project-name>")
        print("4. python octo_hyperframes.py render <project-name>")
        print("5. python octo_youtube_upload.py upload <mp4> --title '...'")
    else:
        print("Usage: python octo_ceo.py [research [focus]|brief|newsletter|memory|state|set <k> <v>|position|plan|video <topic>]")
        print("Focus: general|competitors|customers|newsletter|positioning")

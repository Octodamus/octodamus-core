"""
octo_memory_distill.py
Memory distillation job — runs every 3 days via Task Scheduler (Sun/Wed/Thu 4:30 AM).

Reads raw session history + current core memory, asks Claude Haiku to produce a
tighter, more useful distilled summary, writes it back as the new core memory.

Cost: ~$0.02/run total (Haiku, 9 agents)
Usage: python octo_memory_distill.py [--agent octodamus|octoboto|ben|all]
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from octo_memory_db import (
    init_db, db_skill_stats, db_top_posts, db_ben_all_lessons,
    db_calibration_stats, read_core_memory, write_core_memory,
)

ROOT         = Path(__file__).parent
SECRETS_FILE = ROOT / ".octo_secrets"
CALLS_FILE   = ROOT / "data" / "octo_calls.json"
BEN_HIST     = ROOT / ".agents" / "profit-agent" / "data" / "ben_history.json"
BEN_STATE    = ROOT / ".agents" / "profit-agent" / "state.json"


def _haiku(prompt: str, system: str) -> str:
    import anthropic
    secrets = json.loads(SECRETS_FILE.read_text(encoding="utf-8"))
    key = secrets.get("ANTHROPIC_API_KEY") or secrets.get("secrets", {}).get("ANTHROPIC_API_KEY", "")
    client = anthropic.Anthropic(api_key=key)
    r = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1500,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return r.content[0].text.strip()


def _oracle_summary() -> str:
    """
    Compact oracle call summary.
    Official record = on-chain only (tx_hash set).
    Off-chain calls (local Polymarket bets, geopolitical markets) are listed separately.
    """
    try:
        calls = json.loads(CALLS_FILE.read_text(encoding="utf-8"))
        onchain  = [c for c in calls if c.get("tx_hash")]
        offchain = [c for c in calls if not c.get("tx_hash")]

        oc_wins   = sum(1 for c in onchain if c.get("resolved") and c.get("won"))
        oc_losses = sum(1 for c in onchain if c.get("resolved") and not c.get("won"))
        oc_open   = [c for c in onchain if not c.get("resolved")]

        lines = [
            f"OFFICIAL oracle record (on-chain verified): {oc_wins}W / {oc_losses}L"
            + (f" / {len(oc_open)} open" if oc_open else " / 0 open"),
        ]
        if oc_open:
            for c in oc_open:
                lines.append(f"  OPEN: {c.get('asset')} {c.get('direction')} entry={c.get('entry_price')} tf={c.get('timeframe')} made={str(c.get('made_at',''))[:10]}")

        recent_oc = [c for c in onchain if c.get("resolved")][-5:]
        if recent_oc:
            lines.append("Last 5 on-chain resolved:")
            for c in recent_oc:
                result = "WIN" if c.get("won") else "LOSS"
                lines.append(f"  {c.get('asset')} {c.get('direction')} -> {result} | exit={c.get('exit_price')}")

        if offchain:
            off_wins   = sum(1 for c in offchain if c.get("resolved") and c.get("won"))
            off_losses = sum(1 for c in offchain if c.get("resolved") and not c.get("won"))
            lines.append(f"Off-chain calls (local only, NOT official): {off_wins}W / {off_losses}L ({len(offchain)} total)")

        return "\n".join(lines)
    except Exception as e:
        return f"Oracle data unavailable: {e}"


def _ben_history_text() -> tuple[str, float, float, int]:
    """
    Read Ben's full session history from JSON (richer than SQLite).
    Returns (session_summary_text, wallet_start, wallet_current, total_state_sessions).
    """
    sessions = []
    if BEN_HIST.exists():
        try:
            sessions = json.loads(BEN_HIST.read_text(encoding="utf-8"))
        except Exception:
            pass

    state_sessions = 0
    if BEN_STATE.exists():
        try:
            state_sessions = json.loads(BEN_STATE.read_text(encoding="utf-8")).get("sessions", 0)
        except Exception:
            pass

    wallet_start   = float(sessions[0].get("wallet_start", 201.0)) if sessions else 201.0
    wallet_current = float(sessions[-1].get("wallet_end", wallet_start)) if sessions else wallet_start

    lines = []
    for s in sessions[-30:]:
        date      = s.get("date", "?")
        stype     = s.get("session_type", "?")
        delta     = float(s.get("wallet_delta", 0))
        trades    = s.get("trades", 0)
        services  = s.get("services_designed", 0)
        worked    = str(s.get("what_worked", ""))[:120]
        failed    = str(s.get("what_failed", ""))[:80]
        lines.append(
            f"  [{date} {stype}] delta={delta:+.2f} | trades={trades} | svc={services}\n"
            f"    WORKED: {worked}\n"
            f"    FAILED: {failed}"
        )

    text = "\n".join(lines) if lines else "  None yet"
    return text, wallet_start, wallet_current, state_sessions


def _ben_lessons_text() -> str:
    """All lessons from both SQLite and JSON history, deduplicated."""
    lessons = set()
    # SQLite lessons
    try:
        for l in db_ben_all_lessons():
            lessons.add(str(l["lesson"])[:200])
    except Exception:
        pass
    # JSON history lessons
    if BEN_HIST.exists():
        try:
            for s in json.loads(BEN_HIST.read_text(encoding="utf-8")):
                raw = s.get("lessons", "")
                if isinstance(raw, list):
                    for l in raw:
                        lessons.add(str(l)[:200])
                elif isinstance(raw, str) and raw.startswith("["):
                    try:
                        for l in json.loads(raw.replace("'", '"')):
                            lessons.add(str(l)[:200])
                    except Exception:
                        lessons.add(raw[:200])
        except Exception:
            pass
    return "\n".join(f"  - {l}" for l in sorted(lessons)[-40:]) or "  None recorded yet"


# ── Octodamus distillation ─────────────────────────────────────────────────────

def distill_octodamus():
    print("[Distill] Octodamus...")
    stats   = db_skill_stats(days=7)
    stats30 = db_skill_stats(days=30)
    tops    = db_top_posts(n=5, days=30)
    current = read_core_memory("octodamus")
    oracle  = _oracle_summary()

    try:
        from nunchi import get_brain_context
        brain = get_brain_context(max_lessons=8)
    except Exception:
        brain = ""

    top_text = "\n".join(
        f"  [{t['type']} | {t['voice_mode']} | score={t['engagement_score']:.1f}] {t['text'][:120]}"
        for t in tops
    ) or "  None yet"

    brain_section = f"\nSIGNAL POST-MORTEMS (Nunchi brain — recent lessons):\n{brain}\n" if brain else ""

    prompt = f"""You are updating the persistent core memory for Octodamus, an AI oracle posting to X (@octodamusai).

CURRENT CORE MEMORY:
{current}

ORACLE CALL RECORD:
{oracle}
{brain_section}
NEW DATA — LAST 7 DAYS:
Posts: {stats['total']} | Rated: {stats['rated']} | Good: {stats['good']} / Bad: {stats['bad']} / OK: {stats['ok']}
Best voice mode this week: {stats['best_voice']}

30-DAY TOP POSTS BY ENGAGEMENT:
{top_text}

30-DAY TOTALS:
Posts: {stats30['total']} | Rated: {stats30['rated']} | Good: {stats30['good']} / Bad: {stats30['bad']} / OK: {stats30['ok']}

Rewrite the core memory as a compact, useful markdown document. Rules:
- Max 600 words
- Keep only durable insights (not one-off observations)
- ALWAYS include oracle W/L record and any open calls — this is the product
- If signal post-mortems are present, include a CALL LESSONS section: durable patterns from the Nunchi brain (e.g. "crowd_fade DOWN on ETH/SOL failed 4x when F&G <30 — oversold crowds held")
- Include: what voice modes work, what formats engage, what topics resonate, what to avoid
- Include: engagement context (early account, low impressions — thresholds appropriate to stage)
- Drop anything superseded or proven wrong
- Start with: # Octodamus Core Memory\\nLast distilled: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}
- Be specific. No generic advice. Only things Octodamus can act on."""

    system = "You maintain persistent memory for an AI oracle. Be precise, concise, and actionable. Include the oracle call record. Only include facts that improve future posts or calls."
    new_memory = _haiku(prompt, system)
    write_core_memory("octodamus", new_memory)
    print(f"[Distill] Octodamus updated ({len(new_memory)} chars)")
    return new_memory


# ── OctoBoto distillation ──────────────────────────────────────────────────────

def distill_octoboto():
    print("[Distill] OctoBoto...")
    cal     = db_calibration_stats()
    current = read_core_memory("octoboto")

    from octo_memory_db import _conn
    with _conn() as con:
        resolved = con.execute(
            "SELECT * FROM calibration_estimates WHERE outcome IS NOT NULL"
        ).fetchall()
        pending = con.execute(
            "SELECT market_id, question, recorded_at FROM calibration_estimates WHERE outcome IS NULL"
        ).fetchall()

    resolved_text = "\n".join(
        f"  [{r['category']} | conf={r['confidence']}] {r['question'][:70]} -> {r['outcome']} (our_p={r['claude_p']:.0%})"
        for r in resolved
    ) or "  None yet"

    pending_text = "\n".join(
        f"  [{r['market_id']}] {r['question'][:80]}"
        for r in pending[:10]
    ) or "  None"

    prompt = f"""You are updating the persistent core memory for OctoBoto, an autonomous paper-trading bot on Polymarket (Polygon/Base).

CURRENT CORE MEMORY:
{current}

CALIBRATION DATA:
Total estimates: {cal['total']} | Resolved: {cal['resolved']} | Pending: {cal['pending']}
Need {max(0, 5 - cal['resolved'])} more resolved trades before bias correction activates.

RESOLVED TRADES:
{resolved_text}

PENDING MARKETS (awaiting resolution):
{pending_text}

Rewrite the core memory as a compact, useful markdown document. Rules:
- Max 500 words
- Include: calibration status, detected biases per confidence tier or category
- Include: EV threshold and rationale, market types to prefer/avoid
- Include: trading rules validated or invalidated by actual outcomes
- Include: V2 migration status and any live/paper mode notes
- Start with: # OctoBoto Core Memory\\nLast distilled: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}
- Be specific. If calibration data is thin, say so honestly."""

    system = "You maintain persistent memory for an autonomous trading bot. Be precise about biases, win rates, calibration data. Only state what the data actually shows."
    new_memory = _haiku(prompt, system)
    write_core_memory("octoboto", new_memory)
    print(f"[Distill] OctoBoto updated ({len(new_memory)} chars)")
    return new_memory


# ── Agent_Ben distillation ────────────────────────────────────────────────────

def distill_ben():
    print("[Distill] Agent_Ben...")
    session_summary, wallet_start, wallet_current, state_sessions = _ben_history_text()
    lessons  = _ben_lessons_text()
    current  = read_core_memory("ben")
    delta    = wallet_current - wallet_start
    delta_pct = (delta / wallet_start * 100) if wallet_start else 0

    # ACP job count from events
    acp_jobs = 0
    acp_earned = 0.0
    try:
        events_path = ROOT / "data" / "acp_events.jsonl"
        for line in events_path.read_text(encoding="utf-8").strip().split("\n"):
            if not line:
                continue
            ev = json.loads(line)
            if ev.get("type") == "job_completed" or ev.get("status") == "completed":
                acp_jobs += 1
                acp_earned += float(ev.get("payment_usdc", ev.get("amount_usdc", 0)))
    except Exception:
        pass

    prompt = f"""You are updating the persistent core memory for Agent_Ben, an autonomous AI profit agent.

CURRENT CORE MEMORY:
{current}

WALLET TRAJECTORY:
Start: ${wallet_start:.2f} USDC | Current: ${wallet_current:.2f} USDC | Delta: {delta:+.2f} ({delta_pct:+.1f}%)
State.json session count (all runs): {state_sessions}
ACP jobs completed: {acp_jobs} | ACP earned: ${acp_earned:.2f} USDC

SESSION HISTORY (last 30 with worked/failed detail):
{session_summary}

ALL LESSONS EVER RECORDED:
{lessons}

Rewrite the core memory as a compact, useful markdown document. Rules:
- Max 600 words
- Wallet & P&L: use the ACTUAL numbers above (not stale estimates)
- Hard lessons: things proven true by repeated sessions (20+ consecutive Limitless zeros = structural, not cyclical)
- What works: approaches that produced edge or saved capital
- What doesn't work: dead ends proven by repetition (not one-off)
- x402 / ACP services: count designed vs live vs earning
- Market scan rules that are operationally validated
- Distribution actions taken and their results
- Start with: # Agent_Ben Core Memory\\nLast distilled: {datetime.now(timezone.utc).strftime('%Y-%m-%d')} (Session {state_sessions})
- Be blunt. No generic trading advice. Only what Ben has actually experienced."""

    system = "You maintain persistent memory for an autonomous AI trading agent. Be blunt, specific, and experience-based. Use the actual wallet numbers provided. Generic advice is useless."
    new_memory = _haiku(prompt, system)
    write_core_memory("ben", new_memory)
    print(f"[Distill] Ben updated ({len(new_memory)} chars)")
    return new_memory


# ── Sub-agent distillation ────────────────────────────────────────────────────

def _fetch_market_outcomes() -> str:
    """7-day crypto + macro ground truth for grading sub-agent predictions."""
    lines = ["ACTUAL MARKET OUTCOMES (last 7 days):"]
    try:
        import requests
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "bitcoin,ethereum,solana", "vs_currencies": "usd", "include_7d_change": "true"},
            timeout=10,
        )
        data = r.json()
        for coin, label in [("bitcoin", "BTC"), ("ethereum", "ETH"), ("solana", "SOL")]:
            d = data.get(coin, {})
            lines.append(f"  {label}: ${d.get('usd', 0):,.0f}  ({d.get('usd_7d_change', 0):+.1f}% 7d)")
    except Exception as e:
        lines.append(f"  Crypto fetch failed: {e}")
    try:
        from octo_macro import get_macro_context
        macro = get_macro_context()
        if macro:
            lines.append(f"  Macro: {macro[:300]}")
    except Exception:
        pass
    return "\n".join(lines)


def distill_subagent(agent_name: str, role_desc: str) -> str:
    print(f"[Distill] {agent_name}...")
    mem_key      = agent_name.lower().replace(" ", "_")
    # Core memory: try SQLite first, fall back to markdown file
    current = read_core_memory(mem_key)
    if not current or current.strip() == f"# {agent_name} Core Memory\nNo entries yet.":
        core_path = ROOT / "data" / "memory" / f"{mem_key}_core.md"
        if core_path.exists():
            current = core_path.read_text(encoding="utf-8")
        else:
            current = f"# {agent_name} Core Memory\nNo entries yet.\n"

    # Session history from agent's JSON file
    history_path = ROOT / ".agents" / mem_key / "data" / "history.json"
    history = []
    if history_path.exists():
        try:
            history = json.loads(history_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    lesson_lines = []
    for h in history[-25:]:
        date        = h.get("date", "?")
        lesson      = str(h.get("lesson", ""))[:180]
        what_worked = str(h.get("what_worked", ""))
        outcome_tag = ""
        ww_upper    = what_worked.upper()
        if any(k in ww_upper for k in ("OUTCOME:", "CORRECT", "WRONG", "PARTIAL", "VALIDATED", "INVALIDATED")):
            outcome_tag = f"\n      VERDICT: {what_worked[:120]}"
        lesson_lines.append(f"  [{date}] {lesson}{outcome_tag}")
    lesson_text = "\n".join(lesson_lines) or "  None yet"

    outcomes = _fetch_market_outcomes()

    prompt = f"""You are updating the persistent core memory for {agent_name}, a {role_desc}.

CURRENT CORE MEMORY:
{current}

{outcomes}

RECENT SESSION LESSONS ({len(history)} total sessions):
{lesson_text}

TASK: Rewrite the core memory. Grade each lesson or prediction against actual outcomes above.

GRADING RULES:
- VALIDATED: prediction direction matches actual outcomes, OR agent labeled "CORRECT" -> keep as rule
- INVALIDATED: prediction contradicts outcomes, OR agent labeled "WRONG" -> discard
- UNVERIFIED: no falsifiable prediction (process notes, tool reliability) -> keep if genuinely useful
- Never promote unverified claims to "Validated Rules"

OUTPUT STRUCTURE:
## Identity
(one sentence: what this agent does, voice, specialization)

## Validated Rules
(predictions confirmed by outcomes — real edges only)

## Working Process
(reliable tools/methods/data sources — not predictions)

## Calibration Score
X correct / Y graded | Z unverified | [INSUFFICIENT DATA if <3 graded]

Rules:
- Max 500 words total
- Start: # {agent_name} Core Memory\\nLast distilled: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}
- Be ruthless. Unvalidated speculation belongs in the bin."""

    system = f"You maintain memory for {agent_name}. Keep only what is TRUE and validated. Delete unvalidated predictions. Lean accurate memory beats fat speculative memory."
    new_memory = _haiku(prompt, system)
    write_core_memory(mem_key, new_memory)
    print(f"[Distill] {agent_name} updated ({len(new_memory)} chars)")
    return new_memory


# ── Main ──────────────────────────────────────────────────────────────────────

_SUBAGENT_ROLES = {
    "nyse_macromind":     ("NYSE_MacroMind",     "macro regime intelligence agent specializing in FRED data signals (yield curve, DXY, VIX, M2, SPX) — crypto's underlying forcing functions"),
    "nyse_stockoracle":   ("NYSE_StockOracle",   "equity intelligence agent specializing in congressional trading signals, options flow, and tokenized stock analysis"),
    "nyse_tech_agent":    ("NYSE_Tech_Agent",    "regulatory and infrastructure intelligence agent tracking SEC filings, DTCC digital settlement, Chainlink integrations, and Base deployments for tokenized NYSE stocks"),
    "order_chainflow":    ("Order_ChainFlow",    "on-chain order flow agent tracking Binance cumulative buy/sell delta, DEX volume on Base, whale wallet movements, and bridge flows"),
    "nyse_earningsedge":  ("NYSE_EarningsEdge",  "earnings catalyst intelligence agent tracking upcoming earnings, implied move vs historical, analyst estimate revisions, and pre-earnings positioning verdicts"),
    "tokenbot_nyse_base": ("TokenBot_NYSE_Base", "paper trading agent for tokenized NYSE stocks on Base (Dinari dShares) building a win-rate record ahead of live Aerodrome DEX execution"),
}


def run(agent: str = "all"):
    init_db()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"[Distill] Memory distillation starting — {now}")

    agents = list(_SUBAGENT_ROLES.keys()) + ["octodamus", "octoboto", "ben"] \
        if agent == "all" else [agent]

    results = {}
    for a in agents:
        try:
            if a == "octodamus":
                results[a] = distill_octodamus()
            elif a == "octoboto":
                results[a] = distill_octoboto()
            elif a == "ben":
                results[a] = distill_ben()
            elif a in _SUBAGENT_ROLES:
                name, role = _SUBAGENT_ROLES[a]
                results[a] = distill_subagent(name, role)
        except Exception as e:
            print(f"[Distill] {a} failed: {e}")
            import traceback; traceback.print_exc()
            results[a] = f"ERROR: {e}"

    # Email summary — show meaningful preview per agent
    try:
        from octo_notify import _send
        lines = [f"Memory distillation complete — {now}\n"]
        for a, mem in results.items():
            preview = mem[:2000] + ("..." if len(mem) > 2000 else "")
            lines.append(f"{'='*40}\n{a.upper()}\n{'='*40}\n{preview}\n")
        _send("Octodamus Memory Distillation", "\n".join(lines))
        print("[Distill] Summary emailed.")
    except Exception as e:
        print(f"[Distill] Email failed: {e}")

    # Dreaming pass 2: fleet-wide tool-error scan. Catches silent, recurring failures
    # no single agent session can see (e.g. a tool that errors every session for weeks).
    try:
        from octo_dream_toolscan import run as _dreamscan
        _dreamscan(days=7, min_count=4, email=True)
    except Exception as e:
        print(f"[Distill] Dream tool-scan failed: {e}")

    # Dreaming pass 3: fleet-wide "head teacher" — cross-agent patterns no single
    # per-agent distill can see (shared mistakes, common gaps, inconsistencies).
    try:
        from octo_dream_fleet import run as _fleetdream
        _fleetdream(email=True)
    except Exception as e:
        print(f"[Distill] Fleet dream failed: {e}")

    print("[Distill] Done.")


if __name__ == "__main__":
    agent_arg = "all"
    for arg in sys.argv[1:]:
        if arg.startswith("--agent="):
            agent_arg = arg.split("=", 1)[1]
        elif arg in ("octodamus", "octoboto", "ben", "all", *_SUBAGENT_ROLES.keys()):
            agent_arg = arg
    run(agent_arg)

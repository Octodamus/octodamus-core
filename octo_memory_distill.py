"""
octo_memory_distill.py
Weekly memory distillation job — runs every Sunday via Task Scheduler.

Reads raw SQLite data from the past 7 days + current core memory,
asks Claude Haiku to produce a tighter, more useful distilled summary,
writes it back as the new core memory for each agent.

Cost: ~$0.01/run total (Haiku, 3 calls)
Usage: python octo_memory_distill.py [--agent octodamus|octoboto|ben|all]
"""

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from octo_memory_db import (
    init_db, db_skill_stats, db_top_posts, db_ben_history, db_ben_all_lessons,
    db_calibration_stats, read_core_memory, write_core_memory,
    db_approve_latest_amendment,
)

SECRETS_FILE = Path(__file__).parent / ".octo_secrets"


def _haiku(prompt: str, system: str) -> str:
    import anthropic
    secrets = json.loads(SECRETS_FILE.read_text(encoding="utf-8"))
    key = secrets.get("ANTHROPIC_API_KEY") or secrets.get("secrets", {}).get("ANTHROPIC_API_KEY", "")
    client = anthropic.Anthropic(api_key=key)
    r = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=800,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return r.content[0].text.strip()


# ── Octodamus distillation ─────────────────────────────────────────────────────

def distill_octodamus():
    print("[Distill] Octodamus...")
    stats  = db_skill_stats(days=7)
    stats30 = db_skill_stats(days=30)
    tops   = db_top_posts(n=5, days=30)
    current = read_core_memory("octodamus")

    top_text = "\n".join(
        f"  [{t['type']} | {t['voice_mode']} | score={t['engagement_score']:.1f}] {t['text'][:100]}"
        for t in tops
    ) or "  None yet"

    prompt = f"""You are updating the persistent core memory for Octodamus, an AI oracle posting to X (@octodamusai).

CURRENT CORE MEMORY:
{current}

NEW DATA — LAST 7 DAYS:
Posts: {stats['total']} | Rated: {stats['rated']} | Good: {stats['good']} / Bad: {stats['bad']} / OK: {stats['ok']}
Best voice mode this week: {stats['best_voice']}

30-DAY TOP POSTS BY ENGAGEMENT:
{top_text}

30-DAY TOTALS:
Posts: {stats30['total']} | Rated: {stats30['rated']} | Good: {stats30['good']} / Bad: {stats30['bad']} / OK: {stats30['ok']}

Rewrite the core memory as a compact, useful markdown document. Rules:
- Max 400 words
- Keep only durable insights (not one-off observations)
- Include: what voice modes work, what formats engage, what topics resonate, what to avoid
- Include: oracle call patterns (if any)
- Drop anything that's been superseded or was wrong
- Start with: # Octodamus Core Memory\\nLast distilled: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}
- Be specific. No generic advice. Only things Octodamus can act on in the next post."""

    system = "You maintain persistent memory for an AI oracle. Be precise, concise, and actionable. Only include facts that will improve future posts."
    new_memory = _haiku(prompt, system)
    write_core_memory("octodamus", new_memory)
    print(f"[Distill] Octodamus core memory updated ({len(new_memory)} chars)")
    return new_memory


# ── OctoBoto distillation ──────────────────────────────────────────────────────

def distill_octoboto():
    print("[Distill] OctoBoto...")
    cal  = db_calibration_stats()
    current = read_core_memory("octoboto")

    # Get resolved estimates for bias analysis
    from octo_memory_db import _conn
    with _conn() as con:
        resolved = con.execute(
            "SELECT * FROM calibration_estimates WHERE outcome IS NOT NULL"
        ).fetchall()
        pending = con.execute(
            "SELECT market_id, question, recorded_at FROM calibration_estimates WHERE outcome IS NULL"
        ).fetchall()

    resolved_text = "\n".join(
        f"  [{r['category']} | conf={r['confidence']}] {r['question'][:60]} -> {r['outcome']} (our_p={r['claude_p']:.0%})"
        for r in resolved
    ) or "  None yet"

    pending_text = "\n".join(
        f"  [{r['market_id']}] {r['question'][:70]}"
        for r in pending[:10]
    ) or "  None"

    prompt = f"""You are updating the persistent core memory for OctoBoto, an autonomous paper-trading bot that trades Polymarket prediction markets.

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
- Max 350 words
- Include: calibration status, any detected biases per confidence tier or category
- Include: EV threshold current setting and rationale
- Include: what market types to prefer/avoid based on outcomes
- Include: key trading rules that have been validated or invalidated
- Start with: # OctoBoto Core Memory\\nLast distilled: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}
- Be specific. If calibration data is thin, say so honestly."""

    system = "You maintain persistent memory for an autonomous trading bot. Be precise about biases, win rates, and calibration data. Only state what the data actually shows."
    new_memory = _haiku(prompt, system)
    write_core_memory("octoboto", new_memory)
    print(f"[Distill] OctoBoto core memory updated ({len(new_memory)} chars)")
    return new_memory


# ── Agent_Ben distillation ────────────────────────────────────────────────────

def distill_ben():
    print("[Distill] Agent_Ben...")
    sessions = db_ben_history(limit=50)
    lessons  = db_ben_all_lessons()
    current  = read_core_memory("ben")

    session_summary = "\n".join(
        f"  [{s['date']} {s['session_type']}] wallet_delta={s['wallet_delta']:+.2f} | "
        f"trades={s['trades']} | services={s['services_designed']}"
        for s in sessions[-20:]
    ) or "  None yet"

    lesson_text = "\n".join(
        f"  - {l['lesson']}"
        for l in lessons[-30:]
    ) or "  None recorded yet"

    wallet_start = sessions[0]["wallet_start"] if sessions else 0
    wallet_latest = sessions[-1]["wallet_end"] if sessions else 0

    prompt = f"""You are updating the persistent core memory for Agent_Ben, an autonomous AI profit agent with ~$196 USDC on Base.

CURRENT CORE MEMORY:
{current}

SESSION HISTORY ({len(sessions)} sessions):
{session_summary}

Wallet trajectory: ${wallet_start:.2f} -> ${wallet_latest:.2f}

ALL LESSONS EVER RECORDED:
{lesson_text}

Rewrite the core memory as a compact, useful markdown document. Rules:
- Max 400 words
- Hard lessons: things proven true by repeated experience (e.g. "same-day markets always lock")
- What works: approaches that produced edge or saved capital
- What doesn't work: dead ends, time wasters, proven failures
- Wallet trajectory with P&L context
- x402 services status: which are live, revenue generated
- Market scan rules that are validated
- Start with: # Agent_Ben Core Memory\\nLast distilled: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}
- Be blunt. No generic trading advice. Only things Ben has actually experienced."""

    system = "You maintain persistent memory for an autonomous AI trading agent. Be blunt, specific, and experience-based. Generic advice is useless — only include what was actually learned."
    new_memory = _haiku(prompt, system)
    write_core_memory("ben", new_memory)
    print(f"[Distill] Ben core memory updated ({len(new_memory)} chars)")
    return new_memory


# ── Main ──────────────────────────────────────────────────────────────────────

def _fetch_market_outcomes() -> str:
    """Fetch actual 7-day market outcomes — ground truth for grading agent predictions."""
    lines = ["ACTUAL MARKET OUTCOMES (last 7 days — use this to grade predictions):"]
    try:
        import requests
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={
                "ids": "bitcoin,ethereum,solana",
                "vs_currencies": "usd",
                "include_7d_change": "true",
            },
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
            lines.append(f"  Macro: {macro[:250]}")
    except Exception:
        pass
    return "\n".join(lines)


def distill_subagent(agent_name: str, role_desc: str) -> str:
    """Generic distillation for all sub-agents. Grades predictions against real outcomes."""
    print(f"[Distill] {agent_name}...")
    mem_key = agent_name.lower()
    core_path = Path(__file__).parent / "data" / "memory" / f"{mem_key}_core.md"
    history_path = Path(__file__).parent / ".agents" / mem_key / "data" / "history.json"
    current = core_path.read_text(encoding="utf-8") if core_path.exists() else f"# {agent_name} Core Memory\nNo entries yet.\n"
    history = []
    if history_path.exists():
        try:
            history = json.loads(history_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    lesson_lines = []
    for h in history[-20:]:
        date = h.get("date", "?")
        lesson = h.get("lesson", "")
        what_worked = h.get("what_worked", "")
        outcome_tag = ""
        ww_upper = what_worked.upper()
        if "OUTCOME:" in ww_upper or "CORRECT" in ww_upper or "WRONG" in ww_upper or "PARTIAL" in ww_upper:
            outcome_tag = f"\n      AGENT VERDICT: {what_worked}"
        lesson_lines.append(f"  [{date}] {lesson}{outcome_tag}")
    lesson_text = "\n".join(lesson_lines) or "  None yet"

    outcomes = _fetch_market_outcomes()

    prompt = f"""You are updating the persistent core memory for {agent_name}, a {role_desc}.

CURRENT CORE MEMORY:
{current}

{outcomes}

RECENT SESSION LESSONS ({len(history)} total):
{lesson_text}

TASK: Rewrite the core memory. Grade every lesson against the actual outcomes above.

GRADING RULES (apply to each lesson):
- VALIDATED: prediction direction matches actual outcomes, OR agent's own "CORRECT" verdict -> keep
- INVALIDATED: prediction contradicts actual outcomes, OR agent's own "WRONG" verdict -> discard
- UNVERIFIED: no falsifiable prediction (process notes, tool reliability) -> keep if genuinely useful
- When in doubt about a prediction with no outcome data yet, mark as UNVERIFIED, do not promote to rule

OUTPUT STRUCTURE:
## Validated Rules
(predictions confirmed by outcomes — these are real edges)

## Working Process
(tools, data sources, methods confirmed reliable — not predictions, just mechanics)

## Calibration Score
X correct / Y graded | Z unverified | [INSUFFICIENT DATA if fewer than 3 graded]

---
- Max 350 words total
- Start: # {agent_name} Core Memory\\nLast distilled: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}
- Be ruthless. Generic observations and unvalidated speculation belong in the bin."""

    system = f"You maintain memory for {agent_name}. Keep only what is TRUE. Delete unvalidated predictions. A lean accurate memory beats a fat speculative one."
    new_memory = _haiku(prompt, system)
    write_core_memory(mem_key, new_memory)
    print(f"[Distill] {agent_name} core memory updated ({len(new_memory)} chars)")
    return new_memory


def run(agent: str = "all"):
    init_db()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"[Distill] Memory distillation starting — {now}")

    results = {}
    agents = [
        "octodamus", "octoboto", "ben",
        "nyse_macromind", "nyse_stockoracle", "nyse_tech_agent",
        "order_chainflow", "x_sentiment_agent", "tokenbot_nyse_base",
    ] if agent == "all" else [agent]

    _SUBAGENT_ROLES = {
        "nyse_macromind":     ("NYSE_MacroMind",     "macro regime intelligence agent specializing in FRED data signals — yield curve, DXY, VIX, M2, SPX"),
        "nyse_stockoracle":   ("NYSE_StockOracle",   "equity intelligence agent specializing in congressional trading signals and tokenized stock analysis"),
        "nyse_tech_agent":    ("NYSE_Tech_Agent",    "regulatory and infrastructure intelligence agent tracking SEC filings, DTCC digital settlement, and Chainlink integrations for tokenized NYSE stocks"),
        "order_chainflow":    ("Order_ChainFlow",    "on-chain order flow agent tracking Binance cumulative delta, DEX volume on Base, whale wallet movements, and bridge flows"),
        "x_sentiment_agent":  ("X_Sentiment_Agent",  "crowd sentiment agent reading X/Twitter positioning for BTC, ETH, SOL, and tokenized stocks — contrarian divergence specialist"),
        "tokenbot_nyse_base": ("TokenBot_NYSE_Base", "paper trading agent for tokenized NYSE stocks on Base (Dinari dShares) — builds win rate record ahead of live Aerodrome DEX execution"),
    }

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
            results[a] = f"ERROR: {e}"

    # Email summary
    try:
        from octo_notify import _send
        body = f"Memory distillation complete — {now}\n\n"
        for a, mem in results.items():
            body += f"{'='*40}\n{a.upper()}\n{'='*40}\n{mem[:600]}\n...\n\n"
        _send("Octodamus Memory Distillation — Weekly", body)
        print("[Distill] Summary emailed.")
    except Exception as e:
        print(f"[Distill] Email failed: {e}")

    print("[Distill] Done.")


if __name__ == "__main__":
    agent_arg = "all"
    for arg in sys.argv[1:]:
        if arg.startswith("--agent="):
            agent_arg = arg.split("=", 1)[1]
        elif arg in ("octodamus", "octoboto", "ben", "all"):
            agent_arg = arg
    run(agent_arg)

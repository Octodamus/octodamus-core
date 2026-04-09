"""
octo_boto_brain.py
OctoBoto Post-Mortem Engine

After every closed Polymarket position, analyzes what went right or wrong
and writes lessons to data/octo_boto_brain.md. Those lessons are injected
into the AI estimator prompt so OctoBoto learns from every trade.

Usage:
    from octo_boto_brain import run_postmortems, get_brain_context
    run_postmortems(claude_client=client)   # call after positions close
    ctx = get_brain_context()               # inject into AI prompt
"""

import json
import re
from datetime import datetime
from pathlib import Path
from collections import defaultdict

import anthropic

# ─────────────────────────────────────────────
TRADES_FILE   = Path(r"C:\Users\walli\octodamus\octo_boto_trades.json")
BRAIN_FILE    = Path(r"C:\Users\walli\octodamus\data\octo_boto_brain.md")
BRAIN_INDEX   = Path(r"C:\Users\walli\octodamus\data\octo_boto_brain_index.json")
CATEGORY_FILE = Path(r"C:\Users\walli\octodamus\data\octo_boto_category_stats.json")
MAX_LESSONS   = 8   # lessons to inject into prompt

CATEGORIES = ["CRYPTO", "MACRO", "ELECTIONS", "GEO_POLITICAL", "SPORTS", "OTHER"]


# ─────────────────────────────────────────────
# STATE
# ─────────────────────────────────────────────

def _load_index() -> set:
    """Return set of trade IDs already analyzed."""
    if BRAIN_INDEX.exists():
        try:
            return set(json.loads(BRAIN_INDEX.read_text(encoding="utf-8")))
        except Exception:
            pass
    return set()


def _save_index(analyzed: set) -> None:
    BRAIN_INDEX.write_text(json.dumps(sorted(analyzed), indent=2), encoding="utf-8")


def _append_to_brain(entry: str) -> None:
    BRAIN_FILE.parent.mkdir(exist_ok=True)
    if not BRAIN_FILE.exists():
        BRAIN_FILE.write_text("# OctoBoto Brain — Trade Post-Mortems\n\n", encoding="utf-8")
    with BRAIN_FILE.open("a", encoding="utf-8") as f:
        f.write(entry + "\n\n")


# ─────────────────────────────────────────────
# POST-MORTEM PROMPT
# ─────────────────────────────────────────────

def _build_prompt(trade: dict) -> str:
    outcome = "WIN" if trade.get("won") else "LOSS"
    pnl     = trade.get("pnl", 0)
    return f"""You are the post-mortem engine for OctoBoto, an AI that trades Polymarket prediction markets.

Analyze this closed trade and extract a concise, actionable lesson.

TRADE DATA:
- Market: {trade.get("question", "?")}
- Side taken: {trade.get("side")}
- Entry price: {trade.get("entry_price")} (implied prob: {round(trade.get('entry_price',0)*100,1)}%)
- AI's estimated true prob: {trade.get("true_p")}
- EV at entry: {trade.get("ev")}
- Confidence: {trade.get("confidence")}
- AI reasoning: {trade.get("reasoning", "N/A")}
- Outcome: {outcome}
- P&L: ${pnl:.2f}
- Opened: {trade.get("opened_at", "?")}
- Closed: {trade.get("closed_at", "?")}

Write a post-mortem in EXACTLY this format (no deviations):

## Trade: {trade.get("question", "?")[:60]} — {outcome}
**Side:** {trade.get("side")} @ {trade.get("entry_price")} | P&L: ${pnl:.2f}
**What the AI saw:** [1 sentence — what signal drove the trade]
**Why it {'worked' if trade.get('won') else 'failed'}:** [2 sentences — root cause analysis]
**Pattern:** [1 sentence — the repeatable signal type or failure mode]
**Lesson:** [1 sentence — specific rule OctoBoto should apply to future trades]
**Category:** [one of: SPORTS | GEO_POLITICAL | CRYPTO | MACRO | ELECTIONS | OTHER]"""


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def run_postmortems(claude_client=None, verbose: bool = True) -> list:
    """
    Analyze all closed trades not yet in the brain.
    Returns list of newly written lesson strings.
    """
    if not TRADES_FILE.exists():
        if verbose:
            print("[OctoBrain] No trades file found.")
        return []

    data   = json.loads(TRADES_FILE.read_text(encoding="utf-8"))
    closed = data.get("closed", [])
    if not closed:
        if verbose:
            print("[OctoBrain] No closed trades yet.")
        return []

    analyzed = _load_index()
    new_trades = [t for t in closed if t.get("id") not in analyzed]

    if not new_trades:
        if verbose:
            print("[OctoBrain] All trades already analyzed.")
        return []

    if claude_client is None:
        claude_client = anthropic.Anthropic()

    written = []
    for trade in new_trades:
        tid = trade.get("id", trade.get("market_id", "unknown"))
        if verbose:
            outcome = "WIN" if trade.get("won") else "LOSS"
            print(f"[OctoBrain] Analyzing: {trade.get('question','?')[:55]} - {outcome}")
        try:
            msg = claude_client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=400,
                messages=[{"role": "user", "content": _build_prompt(trade)}],
            )
            entry = msg.content[0].text.strip()
            _append_to_brain(entry)
            analyzed.add(tid)
            _save_index(analyzed)
            written.append(entry)
            if verbose:
                print(f"[OctoBrain] OK - Lesson written for trade {tid}")
        except Exception as e:
            print(f"[OctoBrain] Failed on trade {tid}: {e}")

    return written


# ─────────────────────────────────────────────
# CATEGORY PAYOUT RATIO STATS
# ─────────────────────────────────────────────

def _extract_category_from_entry(entry: str) -> str:
    """Extract category tag from a brain.md entry."""
    m = re.search(r'\*\*Category:\*\*\s*([A-Z_]+)', entry)
    if m:
        cat = m.group(1).strip()
        return cat if cat in CATEGORIES else "OTHER"
    return "OTHER"


def compute_category_stats() -> dict:
    """
    Compute per-category win rate and payout ratio from closed trades.
    Payout ratio = avg_win_pnl / avg_loss_pnl (absolute values).
    Returns dict keyed by category with wins, losses, win_rate, payout_ratio, avg_pnl.
    """
    if not TRADES_FILE.exists():
        return {}

    data = json.loads(TRADES_FILE.read_text(encoding="utf-8"))
    closed = data.get("closed", [])
    if not closed:
        return {}

    # Build category map from brain.md entries
    category_by_id: dict[str, str] = {}
    if BRAIN_FILE.exists():
        content = BRAIN_FILE.read_text(encoding="utf-8")
        sections = [s.strip() for s in content.split("## Trade:") if s.strip()]
        for section in sections:
            cat = _extract_category_from_entry(section)
            # Try to match to a trade by partial question match
            first_line = section.split("\n")[0][:60].lower()
            for t in closed:
                q = (t.get("question") or "")[:60].lower()
                if q and q[:40] in first_line:
                    mid = t.get("id") or t.get("market_id", "")
                    if mid:
                        category_by_id[mid] = cat

    # Aggregate stats per category
    stats: dict[str, dict] = {c: {"wins": [], "losses": []} for c in CATEGORIES}

    for t in closed:
        mid = t.get("id") or t.get("market_id", "")
        cat = category_by_id.get(mid, "OTHER")
        pnl = float(t.get("pnl", 0) or 0)
        if t.get("won"):
            stats[cat]["wins"].append(pnl)
        else:
            stats[cat]["losses"].append(abs(pnl))

    result = {}
    for cat, d in stats.items():
        wins = d["wins"]
        losses = d["losses"]
        total = len(wins) + len(losses)
        if total == 0:
            continue
        avg_win  = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 0
        payout   = round(avg_win / avg_loss, 2) if avg_loss > 0 else 0
        result[cat] = {
            "trades":       total,
            "wins":         len(wins),
            "losses":       len(losses),
            "win_rate":     round(len(wins) / total, 3) if total > 0 else 0,
            "payout_ratio": payout,
            "avg_win":      round(avg_win, 2),
            "avg_loss":     round(avg_loss, 2),
            "net_pnl":      round(sum(wins) - sum(losses), 2),
        }

    # Save for reference
    CATEGORY_FILE.parent.mkdir(exist_ok=True)
    CATEGORY_FILE.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def get_category_context() -> str:
    """
    Returns per-category payout ratio summary for injection into AI prompt.
    Flags categories with payout_ratio < 1.5 as avoid zones.
    """
    try:
        stats = compute_category_stats()
    except Exception:
        return ""
    if not stats:
        return ""

    lines = ["\n\nCATEGORY PERFORMANCE (payout ratio = avg_win / avg_loss):"]
    for cat, d in sorted(stats.items(), key=lambda x: -x[1].get("payout_ratio", 0)):
        if d["trades"] < 2:
            continue
        flag = ""
        if d["payout_ratio"] < 1.5:
            flag = " ⚠ AVOID — payout ratio below threshold"
        elif d["payout_ratio"] >= 3.5:
            flag = " ✓ STRONG — high payout ratio category"
        lines.append(
            f"  {cat}: {d['wins']}W/{d['losses']}L "
            f"(win rate {d['win_rate']:.0%}) | "
            f"payout ratio {d['payout_ratio']:.1f}x | "
            f"net P&L ${d['net_pnl']:+.2f}{flag}"
        )
    return "\n".join(lines)


def format_category_stats_report() -> str:
    """Human-readable Telegram report of category stats."""
    try:
        stats = compute_category_stats()
    except Exception:
        return "Failed to compute category stats."
    if not stats:
        return "No closed trades yet — stats unavailable."

    lines = ["📊 *OctoBoto Category Performance*\n_(payout ratio = avg win ÷ avg loss)_\n"]
    for cat, d in sorted(stats.items(), key=lambda x: -x[1].get("payout_ratio", 0)):
        if d["trades"] == 0:
            continue
        emoji = "✅" if d["payout_ratio"] >= 3.5 else ("⚠️" if d["payout_ratio"] < 1.5 else "🔶")
        lines.append(
            f"{emoji} *{cat}*\n"
            f"   {d['wins']}W / {d['losses']}L  |  WR: {d['win_rate']:.0%}  |  "
            f"Payout: `{d['payout_ratio']:.1f}x`\n"
            f"   Avg win: `${d['avg_win']:.2f}` | Avg loss: `${d['avg_loss']:.2f}` | "
            f"Net: `${d['net_pnl']:+.2f}`"
        )

    # Overall
    all_trades = [t for d in stats.values() for _ in range(d["trades"])]
    total = sum(d["trades"] for d in stats.values())
    total_wins = sum(d["wins"] for d in stats.values())
    total_pnl = sum(d["net_pnl"] for d in stats.values())
    if total > 0:
        lines.append(
            f"\n📈 *Overall*: {total_wins}/{total} wins "
            f"({total_wins/total:.0%}) | Net: `${total_pnl:+.2f}`"
        )
    return "\n".join(lines)


# ─────────────────────────────────────────────
# CONTEXT INJECTION
# ─────────────────────────────────────────────

def get_brain_context(max_lessons: int = MAX_LESSONS) -> str:
    """
    Returns the most recent N lessons from brain.md formatted for
    injection into the AI estimator system prompt.
    """
    if not BRAIN_FILE.exists():
        return ""
    try:
        content = BRAIN_FILE.read_text(encoding="utf-8")
        # Split on ## Trade: headers
        sections = [s.strip() for s in content.split("## Trade:") if s.strip()]
        if not sections:
            return ""
        recent = sections[-max_lessons:]
        lessons = "\n\n## Trade: ".join(recent)
        return (
            "\n\n---\nPAST TRADE LESSONS (learn from these before making new estimates):\n\n"
            f"## Trade: {lessons}\n---"
        )
    except Exception:
        return ""


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    client = anthropic.Anthropic()

    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        newly = run_postmortems(claude_client=client, verbose=True)
        print(f"\n[OctoBrain] Done — {len(newly)} new lesson(s) written.")
    elif cmd == "show":
        print(BRAIN_FILE.read_text(encoding="utf-8") if BRAIN_FILE.exists() else "No brain yet.")
    elif cmd == "context":
        print(get_brain_context() or "No lessons yet.")

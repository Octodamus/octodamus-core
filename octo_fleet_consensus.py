"""
octo_fleet_consensus.py — the Fleet Consensus product.

Aggregates every fleet agent's latest regime verdict into ONE cross-validated read:
the consensus tally (fresh signals only), average conviction, DISSENT (who disagrees),
and the fleet's proven-edge signals attached. No data vendor has 7 specialized agents
cross-checking each other daily — that panel is the product. Sold via x402 + ACP.
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR     = Path(__file__).parent
TEAM_CHANNEL = BASE_DIR / "data" / "agent_team_channel.json"
_FRESH_HOURS = 20  # a verdict older than this is stale — excluded from the consensus


def _bucket(regime: str) -> str:
    r = (regime or "").upper()
    if "RISK-ON" in r or "BULLISH" in r:  return "RISK-ON"
    if "RISK-OFF" in r or "BEARISH" in r: return "RISK-OFF"
    return "NEUTRAL"


def _age_hours(sig: dict):
    try:
        posted = datetime.fromisoformat(str(sig.get("posted_at", "")).replace("Z", ""))
        return (datetime.now(timezone.utc).replace(tzinfo=None) - posted).total_seconds() / 3600
    except Exception:
        return None


def _conviction(excerpt: str):
    m = re.search(r'(\d(?:\.\d)?)\s*/\s*5', excerpt or "")
    try:
        return float(m.group(1)) if m else None
    except Exception:
        return None


def build_fleet_consensus() -> dict:
    """Cross-validated fleet read: consensus tally, conviction, dissent, proven edges."""
    try:
        channel = json.loads(TEAM_CHANNEL.read_text(encoding="utf-8"))
    except Exception:
        channel = {}

    tally = {"RISK-ON": 0, "NEUTRAL": 0, "RISK-OFF": 0}
    fresh, stale, convictions = [], [], []
    for agent, sig in channel.items():
        age = _age_hours(sig)
        is_stale = age is None or age > _FRESH_HOURS
        bucket = _bucket(sig.get("regime", ""))
        conv = _conviction(sig.get("excerpt", ""))
        row = {
            "agent":      agent,
            "regime":     sig.get("regime", "?"),
            "bucket":     bucket,
            "conviction": conv,
            "age_hours":  round(age, 1) if age is not None else None,
            "excerpt":    (sig.get("excerpt", "") or "")[:160],
        }
        if is_stale:
            row["stale"] = True
            stale.append(row)
        else:
            tally[bucket] += 1
            if conv is not None:
                convictions.append(conv)
            fresh.append(row)

    n_fresh  = len(fresh)
    dominant = max(tally, key=tally.get) if n_fresh else "NEUTRAL"
    # Agreement = share of fresh agents in the dominant bucket.
    agreement = round(tally[dominant] / n_fresh, 2) if n_fresh else 0.0
    avg_conv  = round(sum(convictions) / len(convictions), 1) if convictions else None
    dissent   = [r for r in fresh if r["bucket"] != dominant]

    # Attach the fleet's proven-edge signals (the differentiated, priced-on-record products).
    edges = {}
    try:
        from octo_track_record import format_record_block
        edges = {
            "exit_completion": format_record_block("exit_completion"),
            "confluence":      format_record_block("confluence"),
        }
    except Exception:
        pass

    return {
        "product":            "Octodamus Fleet Consensus",
        "as_of":              datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "agents_reporting":   len(channel),
        "fresh_agents":       n_fresh,
        "stale_excluded":     len(stale),
        "consensus":          dominant,
        "agreement":          agreement,               # 0-1: share of fresh agents agreeing
        "tally_fresh":        tally,
        "avg_conviction_5":   avg_conv,
        "dissent":            [{"agent": d["agent"], "regime": d["regime"]} for d in dissent],
        "per_agent":          fresh,
        "stale_signals":      [{"agent": s["agent"], "regime": s["regime"],
                                "age_hours": s["age_hours"]} for s in stale],
        "proven_edges":       edges,
        "interpretation":     _interpret(dominant, agreement, avg_conv, dissent, n_fresh),
        "why_unique":         ("Cross-validated across specialized agents (macro, congressional flow, "
                               "on-chain order flow, sentiment, earnings, regulatory). Dissent is shown, "
                               "not hidden. Two components carry documented accuracy records."),
        "powered_by":         "@octodamusai ecosystem",
    }


def _interpret(dominant: str, agreement: float, avg_conv, dissent: list, n_fresh: int) -> str:
    if not n_fresh:
        return "No fresh agent verdicts available — check back after the next pre-market session."
    strength = ("unanimous" if agreement >= 0.99 else
                "strong" if agreement >= 0.75 else
                "split" if agreement >= 0.5 else "no clear")
    conv_str = f", avg conviction {avg_conv}/5" if avg_conv is not None else ""
    diss_str = (f" Dissent: {', '.join(d['agent'] for d in dissent)}." if dissent else " No dissent.")
    return (f"{strength.capitalize()} {dominant} consensus across {n_fresh} fresh agents "
            f"({int(agreement*100)}% agreement){conv_str}.{diss_str}")


if __name__ == "__main__":
    import io, sys
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    print(json.dumps(build_fleet_consensus(), indent=2))

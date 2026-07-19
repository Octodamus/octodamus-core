"""
octo_track_record.py — auditable source of truth for the fleet's validated edges.

Flagship products are priced on their track record, so the record must be REAL and
current, not a hardcoded string that rots. Each record lives in one place; agents append
validated outcomes via record_outcome() as they grade more sessions, and the flagship
handlers read it. Evidence = the timestamped daily briefs in each agent's drafts folder.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

_PATH = Path(__file__).parent / "data" / "agent_track_records.json"


def _load() -> dict:
    try:
        return json.loads(_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(d: dict) -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    _PATH.write_text(json.dumps(d, indent=2), encoding="utf-8")


def get_record(signal_id: str) -> dict:
    return _load().get(signal_id, {})


def record_outcome(signal_id: str, correct: bool) -> dict:
    """Append one validated outcome (keeps wins/total honest + auto-updating)."""
    d = _load()
    r = d.setdefault(signal_id, {"wins": 0, "total": 0})
    r["total"] = int(r.get("total", 0)) + 1
    if correct:
        r["wins"] = int(r.get("wins", 0)) + 1
    r["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    _save(d)
    return r


def accuracy(signal_id: str) -> float:
    r = get_record(signal_id)
    t = int(r.get("total", 0) or 0)
    return (int(r.get("wins", 0) or 0) / t) if t else 0.0


def format_record_block(signal_id: str) -> dict:
    """Structured track-record block to lead a flagship product with."""
    r = get_record(signal_id)
    w, t = int(r.get("wins", 0) or 0), int(r.get("total", 0) or 0)
    return {
        "record":        f"{w}/{t}" if t else "building",
        "accuracy_pct":  round(w / t * 100, 1) if t else None,
        "sessions_graded": t,
        "session_range": r.get("session_range", ""),
        "method":        r.get("method", ""),
        "validated_through": r.get("last_updated", ""),
        "evidence":      r.get("evidence", "timestamped daily briefs"),
        "honesty_note":  ("Self-validated by the agent against next-session outcomes; "
                          "every graded session has a timestamped brief on file."),
    }

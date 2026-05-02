"""
octo_memory_db.py
Central SQLite memory store for Octodamus, OctoBoto, and Agent_Ben.

Replaces flat JSON logs with a queryable, durable database.
JSON files remain as fallback but this is the source of truth going forward.

DB: data/octodamus_memory.db
"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path

DB_PATH      = Path(__file__).parent / "data" / "octodamus_memory.db"
MEMORY_DIR   = Path(__file__).parent / "data" / "memory"
CORE_OCTO    = MEMORY_DIR / "octodamus_core.md"
CORE_BOTO    = MEMORY_DIR / "octoboto_core.md"
CORE_BEN     = MEMORY_DIR / "ben_core.md"

SCHEMA = """
CREATE TABLE IF NOT EXISTS skill_posts (
    id                  TEXT PRIMARY KEY,
    post_id             TEXT DEFAULT '',
    text                TEXT DEFAULT '',
    type                TEXT DEFAULT '',
    voice_mode          TEXT DEFAULT '',
    is_card             INTEGER DEFAULT 0,
    url                 TEXT DEFAULT '',
    timestamp           TEXT,
    rating              TEXT,
    rating_note         TEXT DEFAULT '',
    engagement_metrics  TEXT DEFAULT '{}',
    engagement_score    REAL,
    metrics_fetched_at  TEXT
);

CREATE TABLE IF NOT EXISTS skill_amendments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp    TEXT,
    proposal     TEXT,
    applied      INTEGER DEFAULT 0,
    approved_by  TEXT,
    applied_at   TEXT
);

CREATE TABLE IF NOT EXISTS calibration_estimates (
    market_id    TEXT PRIMARY KEY,
    question     TEXT DEFAULT '',
    claude_p     REAL,
    market_price REAL,
    confidence   TEXT DEFAULT 'low',
    side         TEXT DEFAULT 'YES',
    category     TEXT DEFAULT 'other',
    recorded_at  TEXT,
    outcome      TEXT,
    resolved_at  TEXT
);

CREATE TABLE IF NOT EXISTS ben_sessions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    session_num      INTEGER DEFAULT 0,
    date             TEXT,
    session_type     TEXT DEFAULT '',
    wallet_start     REAL DEFAULT 0,
    wallet_end       REAL DEFAULT 0,
    wallet_delta     REAL DEFAULT 0,
    trades           INTEGER DEFAULT 0,
    services_designed INTEGER DEFAULT 0,
    what_worked      TEXT DEFAULT '',
    what_failed      TEXT DEFAULT '',
    recorded_at      TEXT
);

CREATE TABLE IF NOT EXISTS ben_lessons (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER,
    lesson      TEXT,
    recorded_at TEXT,
    FOREIGN KEY (session_id) REFERENCES ben_sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_skill_posts_timestamp   ON skill_posts(timestamp);
CREATE INDEX IF NOT EXISTS idx_skill_posts_rating      ON skill_posts(rating);
CREATE INDEX IF NOT EXISTS idx_calibration_outcome     ON calibration_estimates(outcome);
CREATE INDEX IF NOT EXISTS idx_ben_sessions_date       ON ben_sessions(date);
"""


@contextmanager
def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_db():
    with _conn() as con:
        con.executescript(SCHEMA)


def migrate_from_json():
    """One-time migration from legacy JSON files into SQLite. Safe to run repeatedly."""
    init_db()
    migrated = {}

    # ── Skill log ─────────────────────────────────────────────────────────────
    sl_path = Path(__file__).parent / "octo_skill_log.json"
    if sl_path.exists():
        try:
            entries = json.loads(sl_path.read_text(encoding="utf-8"))
            count = 0
            with _conn() as con:
                for e in entries:
                    existing = con.execute(
                        "SELECT id FROM skill_posts WHERE id=?", (e.get("id",""),)
                    ).fetchone()
                    if existing:
                        continue
                    metrics = e.get("engagement_metrics") or {}
                    con.execute("""
                        INSERT INTO skill_posts
                        (id, post_id, text, type, voice_mode, is_card, url, timestamp,
                         rating, rating_note, engagement_metrics, engagement_score, metrics_fetched_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (
                        e.get("id",""),
                        e.get("post_id",""),
                        e.get("text","")[:500],
                        e.get("type",""),
                        e.get("voice_mode",""),
                        1 if e.get("is_card") else 0,
                        e.get("url",""),
                        e.get("timestamp",""),
                        e.get("rating"),
                        e.get("rating_note",""),
                        json.dumps(metrics),
                        e.get("engagement_score"),
                        e.get("metrics_fetched_at"),
                    ))
                    count += 1
            migrated["skill_posts"] = count
        except Exception as ex:
            migrated["skill_posts_error"] = str(ex)

    # ── Skill amendments ──────────────────────────────────────────────────────
    sh_path = Path(__file__).parent / "octo_skill_history.json"
    if sh_path.exists():
        try:
            history = json.loads(sh_path.read_text(encoding="utf-8"))
            count = 0
            with _conn() as con:
                for h in history:
                    ts = h.get("timestamp","")
                    existing = con.execute(
                        "SELECT id FROM skill_amendments WHERE timestamp=?", (ts,)
                    ).fetchone()
                    if existing:
                        continue
                    con.execute("""
                        INSERT INTO skill_amendments (timestamp, proposal, applied, approved_by, applied_at)
                        VALUES (?,?,?,?,?)
                    """, (ts, h.get("proposal",""), 1 if h.get("applied") else 0,
                          h.get("approved_by"), h.get("applied_at")))
                    count += 1
            migrated["skill_amendments"] = count
        except Exception as ex:
            migrated["skill_amendments_error"] = str(ex)

    # ── Calibration estimates ──────────────────────────────────────────────────
    cal_path = Path(__file__).parent / "octo_boto_calibration.json"
    if cal_path.exists():
        try:
            cal = json.loads(cal_path.read_text(encoding="utf-8"))
            count = 0
            with _conn() as con:
                for e in cal.get("estimates", []):
                    existing = con.execute(
                        "SELECT market_id FROM calibration_estimates WHERE market_id=?",
                        (e.get("market_id",""),)
                    ).fetchone()
                    if existing:
                        con.execute("""
                            UPDATE calibration_estimates
                            SET outcome=?, resolved_at=?
                            WHERE market_id=? AND outcome IS NULL
                        """, (e.get("outcome"), e.get("resolved_at"), e.get("market_id","")))
                        continue
                    con.execute("""
                        INSERT INTO calibration_estimates
                        (market_id, question, claude_p, market_price, confidence, side,
                         category, recorded_at, outcome, resolved_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?)
                    """, (
                        e.get("market_id",""),
                        e.get("question","")[:200],
                        e.get("claude_p", 0),
                        e.get("market_price", 0),
                        e.get("confidence","low"),
                        e.get("side","YES"),
                        e.get("category","other"),
                        e.get("recorded_at",""),
                        e.get("outcome"),
                        e.get("resolved_at"),
                    ))
                    count += 1
            migrated["calibration_estimates"] = count
        except Exception as ex:
            migrated["calibration_error"] = str(ex)

    # ── Ben sessions ──────────────────────────────────────────────────────────
    bh_path = Path(__file__).parent / ".agents" / "profit-agent" / "data" / "ben_history.json"
    if bh_path.exists():
        try:
            history = json.loads(bh_path.read_text(encoding="utf-8"))
            count = 0
            with _conn() as con:
                for h in history:
                    existing = con.execute(
                        "SELECT id FROM ben_sessions WHERE session_num=? AND date=?",
                        (h.get("session",0), h.get("date",""))
                    ).fetchone()
                    if existing:
                        continue
                    cur = con.execute("""
                        INSERT INTO ben_sessions
                        (session_num, date, session_type, wallet_start, wallet_end,
                         wallet_delta, trades, services_designed, what_worked, what_failed, recorded_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """, (
                        h.get("session",0), h.get("date",""), h.get("session_type",""),
                        h.get("wallet_start",0), h.get("wallet_end",0), h.get("wallet_delta",0),
                        h.get("trades",0), h.get("services_designed",0),
                        h.get("what_worked",""), h.get("what_failed",""),
                        h.get("recorded_at",""),
                    ))
                    session_db_id = cur.lastrowid
                    for lesson in h.get("lessons", []):
                        con.execute(
                            "INSERT INTO ben_lessons (session_id, lesson, recorded_at) VALUES (?,?,?)",
                            (session_db_id, lesson, h.get("recorded_at",""))
                        )
                    count += 1
            migrated["ben_sessions"] = count
        except Exception as ex:
            migrated["ben_sessions_error"] = str(ex)

    return migrated


# ── Octodamus skill log writes ─────────────────────────────────────────────────

def db_log_post(entry_id, post_id, text, post_type, voice_mode, is_card, url, timestamp):
    with _conn() as con:
        con.execute("""
            INSERT OR IGNORE INTO skill_posts
            (id, post_id, text, type, voice_mode, is_card, url, timestamp)
            VALUES (?,?,?,?,?,?,?,?)
        """, (entry_id, post_id, text[:500], post_type, voice_mode,
              1 if is_card else 0, url, timestamp))


def db_rate_post(entry_id, rating, note=""):
    with _conn() as con:
        con.execute(
            "UPDATE skill_posts SET rating=?, rating_note=? WHERE id=? OR post_id=?",
            (rating, note, entry_id, entry_id)
        )


def db_update_engagement(post_id, metrics: dict, score: float, rating: str, note: str):
    with _conn() as con:
        con.execute("""
            UPDATE skill_posts
            SET engagement_metrics=?, engagement_score=?, metrics_fetched_at=?,
                rating=COALESCE(rating, ?), rating_note=COALESCE(NULLIF(rating_note,''), ?)
            WHERE post_id=? AND metrics_fetched_at IS NULL
        """, (json.dumps(metrics), score,
              datetime.now(timezone.utc).isoformat(),
              rating, note, post_id))


def db_skill_stats(days: int = 7) -> dict:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with _conn() as con:
        rows = con.execute(
            "SELECT rating, voice_mode, is_card, type, engagement_score FROM skill_posts WHERE timestamp >= ?",
            (cutoff,)
        ).fetchall()
    total = len(rows)
    rated = [r for r in rows if r["rating"]]
    good  = [r for r in rated if r["rating"] == "good"]
    bad   = [r for r in rated if r["rating"] == "bad"]
    ok    = [r for r in rated if r["rating"] == "ok"]
    voice_good: dict = {}
    for r in good:
        vm = r["voice_mode"] or "unknown"
        voice_good[vm] = voice_good.get(vm, 0) + 1
    best_voice = max(voice_good, key=voice_good.get) if voice_good else "none"
    return {
        "total": total, "rated": len(rated),
        "good": len(good), "bad": len(bad), "ok": len(ok),
        "best_voice": best_voice,
        "voice_good": voice_good,
    }


def db_top_posts(n: int = 5, days: int = 30) -> list:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with _conn() as con:
        rows = con.execute("""
            SELECT text, type, voice_mode, engagement_score, url
            FROM skill_posts
            WHERE timestamp >= ? AND engagement_score IS NOT NULL
            ORDER BY engagement_score DESC LIMIT ?
        """, (cutoff, n)).fetchall()
    return [dict(r) for r in rows]


def db_save_amendment(proposal: str):
    with _conn() as con:
        con.execute(
            "INSERT INTO skill_amendments (timestamp, proposal) VALUES (?,?)",
            (datetime.now(timezone.utc).isoformat(), proposal)
        )


def db_approve_latest_amendment(approved_by: str = "christopher") -> str:
    with _conn() as con:
        row = con.execute(
            "SELECT id, proposal FROM skill_amendments WHERE applied=0 ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not row:
            return "No pending amendments."
        con.execute(
            "UPDATE skill_amendments SET applied=1, approved_by=?, applied_at=? WHERE id=?",
            (approved_by, datetime.now(timezone.utc).isoformat(), row["id"])
        )
    return row["proposal"]


# ── OctoBoto calibration writes ───────────────────────────────────────────────

def db_record_estimate(market_id, question, claude_p, market_price, confidence, side, category):
    with _conn() as con:
        con.execute("""
            INSERT OR IGNORE INTO calibration_estimates
            (market_id, question, claude_p, market_price, confidence, side, category, recorded_at)
            VALUES (?,?,?,?,?,?,?,?)
        """, (market_id, question[:200], round(claude_p, 4), round(market_price, 4),
              confidence, side, category, datetime.now(timezone.utc).isoformat()))


def db_record_outcome(market_id: str, resolved_yes: bool):
    outcome = "YES" if resolved_yes else "NO"
    with _conn() as con:
        con.execute("""
            UPDATE calibration_estimates
            SET outcome=?, resolved_at=?
            WHERE market_id=? AND outcome IS NULL
        """, (outcome, datetime.now(timezone.utc).isoformat(), market_id))


def db_calibration_stats() -> dict:
    with _conn() as con:
        all_rows  = con.execute("SELECT * FROM calibration_estimates").fetchall()
        resolved  = [r for r in all_rows if r["outcome"]]
        pending   = [r for r in all_rows if not r["outcome"]]
    return {
        "total": len(all_rows),
        "resolved": len(resolved),
        "pending": len(pending),
        "pending_questions": [r["question"][:60] for r in pending[:5]],
    }


# ── Agent_Ben session writes ───────────────────────────────────────────────────

def db_record_ben_session(session_num, date, session_type, wallet_start, wallet_end,
                           trades, services_designed, what_worked, what_failed, lessons: list) -> int:
    wallet_delta = round(wallet_end - wallet_start, 2) if wallet_end and wallet_start else 0.0
    with _conn() as con:
        cur = con.execute("""
            INSERT INTO ben_sessions
            (session_num, date, session_type, wallet_start, wallet_end, wallet_delta,
             trades, services_designed, what_worked, what_failed, recorded_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (session_num, date, session_type, wallet_start, wallet_end, wallet_delta,
              trades, services_designed, what_worked, what_failed,
              datetime.now(timezone.utc).isoformat()))
        session_db_id = cur.lastrowid
        for lesson in lessons:
            con.execute(
                "INSERT INTO ben_lessons (session_id, lesson, recorded_at) VALUES (?,?,?)",
                (session_db_id, lesson, datetime.now(timezone.utc).isoformat())
            )
    return session_db_id


def db_ben_history(limit: int = 10) -> list:
    with _conn() as con:
        sessions = con.execute(
            "SELECT * FROM ben_sessions ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        result = []
        for s in sessions:
            lessons = con.execute(
                "SELECT lesson FROM ben_lessons WHERE session_id=?", (s["id"],)
            ).fetchall()
            d = dict(s)
            d["lessons"] = [l["lesson"] for l in lessons]
            result.append(d)
    return list(reversed(result))


def db_ben_all_lessons() -> list:
    with _conn() as con:
        rows = con.execute(
            "SELECT lesson, recorded_at FROM ben_lessons ORDER BY id"
        ).fetchall()
    return [dict(r) for r in rows]


# ── Core memory file helpers ───────────────────────────────────────────────────

def _core_path(agent: str) -> Path:
    known = {"octodamus": CORE_OCTO, "octoboto": CORE_BOTO, "ben": CORE_BEN}
    if agent in known:
        return known[agent]
    return MEMORY_DIR / f"{agent.lower()}_core.md"


def read_core_memory(agent: str) -> str:
    """agent: 'octodamus' | 'octoboto' | 'ben' | 'macromind' | 'stockoracle' | ..."""
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    path = _core_path(agent)
    if path.exists():
        return path.read_text(encoding="utf-8")
    return f"# {agent.title()} Core Memory\nNo entries yet.\n"


def write_core_memory(agent: str, content: str):
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    path = _core_path(agent)
    path.write_text(content, encoding="utf-8")


def append_core_memory(agent: str, section_header: str, content: str):
    existing = read_core_memory(agent)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    addition = f"\n\n## {section_header} ({now})\n{content}"
    write_core_memory(agent, existing.rstrip() + addition)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

    if cmd == "migrate":
        print("Migrating JSON files to SQLite...")
        result = migrate_from_json()
        for k, v in result.items():
            print(f"  {k}: {v}")
        print("Done.")

    elif cmd == "status":
        init_db()
        skill = db_skill_stats(days=30)
        cal   = db_calibration_stats()
        ben   = db_ben_history(limit=5)
        lessons = db_ben_all_lessons()
        print(f"\n=== Memory DB Status ===")
        print(f"\nOctodamus Skill Log (30d):")
        print(f"  {skill['total']} posts | {skill['rated']} rated | {skill['good']}G/{skill['bad']}B/{skill['ok']}OK")
        print(f"  Best voice: {skill['best_voice']}")
        print(f"\nOctoBoto Calibration:")
        print(f"  {cal['total']} estimates | {cal['resolved']} resolved | {cal['pending']} pending")
        if cal['pending_questions']:
            print(f"  Pending: {cal['pending_questions'][0]}...")
        print(f"\nAgent_Ben Sessions:")
        print(f"  {len(ben)} sessions in last 5 | {len(lessons)} total lessons")
        for s in ben[-3:]:
            print(f"  [{s['date']} {s['session_type']}] delta=${s['wallet_delta']:+.2f}")

    elif cmd == "core":
        agent = sys.argv[2] if len(sys.argv) > 2 else "octodamus"
        print(read_core_memory(agent))

    else:
        print(f"Usage: python octo_memory_db.py [migrate|status|core <agent>]")

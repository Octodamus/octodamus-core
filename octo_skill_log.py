"""
octo_skill_log.py
Octodamus — Self-Improving Skill System

Tracks every post with voice mode, format, and rating.
Weekly analysis proposes amendments to OCTO_SYSTEM.
Ratings via Telegram: /rate good | /rate bad | /rate ok

Storage: octo_skill_log.json, octo_skill_history.json
"""

import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
SKILL_LOG_FILE = BASE_DIR / "octo_skill_log.json"
SKILL_HISTORY_FILE = BASE_DIR / "octo_skill_history.json"


# ─────────────────────────────────────────────
# STORAGE
# ─────────────────────────────────────────────

def _load_log() -> list:
    if SKILL_LOG_FILE.exists():
        try:
            return json.loads(SKILL_LOG_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save_log(data: list):
    SKILL_LOG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _load_history() -> list:
    if SKILL_HISTORY_FILE.exists():
        try:
            return json.loads(SKILL_HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save_history(data: list):
    SKILL_HISTORY_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ─────────────────────────────────────────────
# LOG A POST
# ─────────────────────────────────────────────

def log_post(
    post_text: str,
    post_type: str,
    voice_mode: str,
    is_card: bool,
    url: str = "",
    post_id: str = "",
) -> str:
    """Log a post to the skill log. Returns entry ID."""
    entries = _load_log()
    entry_id = f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{post_type[:3]}"
    entries.append({
        "id": entry_id,
        "post_id": post_id,
        "text": post_text[:280],
        "type": post_type,
        "voice_mode": voice_mode,
        "is_card": is_card,
        "url": url,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "rating": None,
        "rating_note": "",
    })
    _save_log(entries)
    return entry_id


def rate_last_post(rating: str, note: str = "") -> str:
    """Rate the most recent unrated post. Rating: good | bad | ok"""
    entries = _load_log()
    if not entries:
        return "No posts to rate."
    for entry in reversed(entries):
        if entry.get("rating") is None:
            entry["rating"] = rating.lower()
            entry["rating_note"] = note
            _save_log(entries)
            return f"Rated '{entry['type']}' post as {rating.upper()}. {entry['text'][:60]}..."
    return "All recent posts already rated."


def rate_post_by_id(entry_id: str, rating: str, note: str = "") -> str:
    """Rate a specific post by ID."""
    entries = _load_log()
    for entry in entries:
        if entry.get("id") == entry_id or entry.get("post_id") == entry_id:
            entry["rating"] = rating.lower()
            entry["rating_note"] = note
            _save_log(entries)
            return f"Rated {entry_id} as {rating.upper()}."
    return f"Post {entry_id} not found."


# ─────────────────────────────────────────────
# WEEKLY ANALYSIS
# ─────────────────────────────────────────────

def get_weekly_stats() -> dict:
    """Analyze last 7 days of posts for patterns."""
    entries = _load_log()
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)

    recent = [
        e for e in entries
        if datetime.fromisoformat(e["timestamp"]) >= week_ago
    ]

    rated = [e for e in recent if e.get("rating")]
    good  = [e for e in rated if e["rating"] == "good"]
    bad   = [e for e in rated if e["rating"] == "bad"]
    ok    = [e for e in rated if e["rating"] == "ok"]

    # Voice mode breakdown
    voice_stats = {}
    for e in rated:
        vm = e.get("voice_mode", "unknown")
        if vm not in voice_stats:
            voice_stats[vm] = {"good": 0, "bad": 0, "ok": 0, "total": 0}
        voice_stats[vm][e["rating"]] += 1
        voice_stats[vm]["total"] += 1

    # Card vs plain
    card_good  = len([e for e in good if e.get("is_card")])
    plain_good = len([e for e in good if not e.get("is_card")])
    card_bad   = len([e for e in bad if e.get("is_card")])
    plain_bad  = len([e for e in bad if not e.get("is_card")])

    # Post type breakdown
    type_stats = {}
    for e in rated:
        pt = e.get("type", "unknown")
        if pt not in type_stats:
            type_stats[pt] = {"good": 0, "bad": 0, "ok": 0}
        type_stats[pt][e["rating"]] += 1

    return {
        "total_posts":  len(recent),
        "total_rated":  len(rated),
        "good":         len(good),
        "bad":          len(bad),
        "ok":           len(ok),
        "voice_stats":  voice_stats,
        "card_good":    card_good,
        "plain_good":   plain_good,
        "card_bad":     card_bad,
        "plain_bad":    plain_bad,
        "type_stats":   type_stats,
        "good_examples": [e["text"][:120] for e in good[:3]],
        "bad_examples":  [e["text"][:120] for e in bad[:3]],
    }


# ─────────────────────────────────────────────
# GENERATE AMENDMENT PROPOSAL
# ─────────────────────────────────────────────

def generate_amendment_proposal(stats: dict, current_system_prompt: str) -> str:
    """Ask Claude to propose an amendment to OCTO_SYSTEM based on performance data."""
    try:
        import anthropic
        client = anthropic.Anthropic()

        analysis_prompt = f"""You are analyzing the performance of Octodamus, an AI oracle posting to X (@octodamusai).

CURRENT PERFORMANCE (last 7 days):
- Total posts: {stats['total_posts']}
- Rated posts: {stats['total_rated']}
- Good: {stats['good']} | Bad: {stats['bad']} | OK: {stats['ok']}

VOICE MODE PERFORMANCE:
{json.dumps(stats['voice_stats'], indent=2)}

FORMAT PERFORMANCE:
- Card format: {stats['card_good']} good, {stats['card_bad']} bad
- Plain text: {stats['plain_good']} good, {stats['plain_bad']} bad

POST TYPE PERFORMANCE:
{json.dumps(stats['type_stats'], indent=2)}

GOOD POST EXAMPLES:
{chr(10).join(f'- {ex}' for ex in stats['good_examples'])}

BAD POST EXAMPLES:
{chr(10).join(f'- {ex}' for ex in stats['bad_examples'])}

CURRENT SYSTEM PROMPT (first 500 chars):
{current_system_prompt[:500]}...

Based on this data, propose ONE specific amendment to improve post quality.
Be concrete. Suggest exact wording changes, not general advice.
Format your response as:
OBSERVATION: [what the data shows]
AMENDMENT: [exact change to make to the system prompt]
EXPECTED IMPROVEMENT: [why this will help]
CONFIDENCE: high/medium/low"""

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            messages=[{"role": "user", "content": analysis_prompt}]
        )
        return response.content[0].text.strip()
    except Exception as e:
        return f"Amendment proposal failed: {e}"


# ─────────────────────────────────────────────
# AMENDMENT HISTORY
# ─────────────────────────────────────────────

def save_amendment_proposal(proposal: str) -> None:
    """Save amendment proposal to history for audit trail."""
    history = _load_history()
    history.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "proposal": proposal,
        "applied": False,
        "approved_by": None,
        "applied_at": None,
    })
    _save_history(history)
    print("[SkillLog] Amendment proposal saved to history.")


def approve_latest_amendment() -> str:
    """Mark the latest unapplied amendment as approved. Returns proposal text."""
    history = _load_history()
    for entry in reversed(history):
        if not entry.get("applied"):
            entry["applied"] = True
            entry["approved_by"] = "christopher"
            entry["applied_at"] = datetime.now(timezone.utc).isoformat()
            _save_history(history)
            return entry["proposal"]
    return "No pending amendments."


# ─────────────────────────────────────────────
# SUMMARY FOR TELEGRAM
# ─────────────────────────────────────────────

def get_skill_summary() -> str:
    stats = get_weekly_stats()
    if stats["total_rated"] == 0:
        return "No rated posts yet. Use /rate good|bad|ok after posts to train the system."

    best_voice = "none"
    if stats["voice_stats"]:
        best_voice = max(
            stats["voice_stats"].items(),
            key=lambda x: x[1].get("good", 0)
        )[0]

    return (
        f"Skill log: {stats['total_posts']} posts this week, {stats['total_rated']} rated.\n"
        f"Good: {stats['good']} | Bad: {stats['bad']} | OK: {stats['ok']}\n"
        f"Best voice: {best_voice}\n"
        f"Card: {stats['card_good']} good, {stats['card_bad']} bad\n"
        f"Plain: {stats['plain_good']} good, {stats['plain_bad']} bad\n"
        f"Use /analyze to generate an improvement proposal."
    )

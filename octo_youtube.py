"""
octo_youtube.py — Octodamus YouTube Intelligence Harvester

Watches channels covering AI agent building, BTC, and coding.
Only surfaces content that scores 8+/10 on relevance — everything else is silently logged.
Octodamus never mentions watching a video. The intel becomes his own thought.

Processed video IDs tracked in data/octo_youtube_seen.json (no reprocessing).
Intel stored in data/octo_youtube_intel.json (last 50 entries).

CLI:
  python octo_youtube.py scan          Scan all channels, return post-worthy entries
  python octo_youtube.py url <URL>     Process a specific video
  python octo_youtube.py context       Print high-relevance intel for runner injection
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

import anthropic

BASE_DIR = Path(__file__).parent.resolve()

# ── Secrets bootstrap (works standalone and inside runner) ────────────────────

def _ensure_secrets():
    """Load secrets from cache if ANTHROPIC_API_KEY isn't already in environment."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from bitwarden import load_all_secrets
        load_all_secrets()
    except Exception as e:
        print(f"[YouTube] Warning: could not load secrets: {e}")

_ensure_secrets()
DATA_DIR = BASE_DIR / "data"
SEEN_FILE = DATA_DIR / "octo_youtube_seen.json"
INTEL_FILE = DATA_DIR / "octo_youtube_intel.json"

# ── Channel watchlist ─────────────────────────────────────────────────────────
# Focused on: AI agent building, BTC thesis, and elite coding/engineering content

YOUTUBE_CHANNELS = [
    # AI Agents & LLM Engineering
    {"name": "Andrej Karpathy",     "url": "https://www.youtube.com/@AndrejKarpathy"},
    {"name": "AI Explained",        "url": "https://www.youtube.com/@aiexplained-official"},
    {"name": "Matthew Berman",      "url": "https://www.youtube.com/@matthew_berman"},
    {"name": "TheAIGRID",           "url": "https://www.youtube.com/@TheAIGRID"},
    # Anthropic / Claude — agent architecture, how to build with Claude, Claude Code
    {"name": "Anthropic",           "url": "https://www.youtube.com/@anthropic-ai"},
    {"name": "Claude (Anthropic)",  "url": "https://www.youtube.com/@claude-ai"},
    # Bitcoin & Macro
    {"name": "What Bitcoin Did",    "url": "https://www.youtube.com/@WhatBitcoinDid"},
    {"name": "Preston Pysh",        "url": "https://www.youtube.com/@PrestonPysh"},
    {"name": "Bitcoin Archive",     "url": "https://www.youtube.com/@BitcoinArchive"},
    # Coding & Engineering
    {"name": "Fireship",            "url": "https://www.youtube.com/@Fireship"},
    {"name": "ThePrimeagen",        "url": "https://www.youtube.com/@ThePrimeTimeagen"},
    {"name": "Theo",                "url": "https://www.youtube.com/@t3dotgg"},
]

# Whisper model — base is fast enough, upgrade to 'small' for longer podcasts
WHISPER_MODEL = "base"

# Max videos processed per scan (keep runtime under ~15 min)
MAX_PER_SCAN = 2

# Skip videos longer than this (seconds). Whisper OOMs on 2h+ podcasts.
MAX_DURATION_SECONDS = 45 * 60  # 45 minutes

# Max transcript chars sent to Claude
MAX_TRANSCRIPT_CHARS = 12000

# Minimum relevance score (out of 10) to qualify for a post
MIN_RELEVANCE_TO_POST = 8

# Only inject into runner context if relevance is at least this high
MIN_RELEVANCE_FOR_CONTEXT = 7

# Only use intel from the last N hours for context injection
CONTEXT_FRESHNESS_HOURS = 48


# ── Seen tracker ──────────────────────────────────────────────────────────────

def _load_seen() -> set:
    if SEEN_FILE.exists():
        try:
            return set(json.loads(SEEN_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    return set()


def _save_seen(seen: set):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SEEN_FILE.write_text(json.dumps(sorted(seen), indent=2), encoding="utf-8")


# ── Intel store ───────────────────────────────────────────────────────────────

def _load_intel() -> list:
    if INTEL_FILE.exists():
        try:
            return json.loads(INTEL_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def _save_intel(intel: list):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    INTEL_FILE.write_text(json.dumps(intel, indent=2, ensure_ascii=False), encoding="utf-8")


def _append_intel(entry: dict):
    intel = _load_intel()
    intel.append(entry)
    if len(intel) > 50:
        intel = intel[-50:]
    _save_intel(intel)


# ── yt-dlp helpers ────────────────────────────────────────────────────────────

def _get_latest_video_ids(channel_url: str, n: int = 3) -> list[dict]:
    """Return the n most recent video IDs and titles from a channel."""
    try:
        result = subprocess.run(
            [
                "yt-dlp",
                "--flat-playlist",
                "--playlist-end", str(n),
                "--print", "%(id)s\t%(title)s\t%(upload_date)s",
                "--no-warnings",
                "--quiet",
                channel_url,
            ],
            capture_output=True, text=True, timeout=30
        )
        videos = []
        for line in result.stdout.strip().splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                videos.append({
                    "id": parts[0].strip(),
                    "title": parts[1].strip(),
                    "date": parts[2].strip() if len(parts) > 2 else "",
                })
        return videos
    except Exception as e:
        print(f"[YouTube] Failed to fetch {channel_url}: {e}")
        return []


def _get_video_duration(video_id: str) -> int | None:
    """Return video duration in seconds, or None on failure."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        result = subprocess.run(
            ["yt-dlp", "--print", "%(duration)s", "--no-warnings", "--quiet", url],
            capture_output=True, text=True, timeout=15
        )
        val = result.stdout.strip()
        return int(val) if val.isdigit() else None
    except Exception:
        return None


def _download_audio(video_id: str, out_dir: str) -> str | None:
    """Download audio only. Returns file path or None."""
    duration = _get_video_duration(video_id)
    if duration is not None and duration > MAX_DURATION_SECONDS:
        print(f"[YouTube] Skipping {video_id}: duration {duration//60}m exceeds {MAX_DURATION_SECONDS//60}m limit")
        return None

    url = f"https://www.youtube.com/watch?v={video_id}"
    out_template = os.path.join(out_dir, "%(id)s.%(ext)s")
    try:
        subprocess.run(
            [
                "yt-dlp",
                "-f", "bestaudio[ext=m4a]/bestaudio",
                "-o", out_template,
                "--no-warnings",
                "--quiet",
                url,
            ],
            capture_output=True, text=True, timeout=120
        )
        for f in Path(out_dir).iterdir():
            if video_id in f.name:
                return str(f)
        print(f"[YouTube] Audio file not found for {video_id}")
        return None
    except Exception as e:
        print(f"[YouTube] Download failed for {video_id}: {e}")
        return None


# ── Transcription ─────────────────────────────────────────────────────────────

_whisper_model = None


def _get_whisper():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        print(f"[YouTube] Loading Whisper ({WHISPER_MODEL})...")
        _whisper_model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    return _whisper_model


def _transcribe(audio_path: str) -> str:
    model = _get_whisper()
    segments, info = model.transcribe(audio_path, beam_size=5)
    print(f"[YouTube] Transcribing ({info.language}, {info.duration:.0f}s)...")
    return " ".join(seg.text.strip() for seg in segments)


# ── Claude: evaluate and summarise ───────────────────────────────────────────

def _evaluate(title: str, channel: str, transcript: str) -> dict:
    """
    Claude evaluates the video for relevance to Octodamus's three pillars:
    AI agent building, BTC thesis, and elite coding/engineering.
    Returns structured summary + relevance score.
    """
    claude = anthropic.Anthropic()
    snippet = transcript[:MAX_TRANSCRIPT_CHARS]

    prompt = f"""You are the editorial brain for Octodamus — an autonomous AI oracle focused on three things:
1. Building AI agents and autonomous systems — specifically: agent architecture, how to design and orchestrate agents with Claude, Claude Code (the agentic coding tool), MCP (Model Context Protocol), multi-agent patterns, and tool use. Anthropic and Claude channel content falls entirely in this pillar.
2. Bitcoin's long-term value thesis and market structure
3. Elite software engineering and system design

Your job: evaluate this YouTube video and decide if it contains something genuinely insightful — a non-obvious idea, a sharp technical observation, or a contrarian thesis worth Octodamus thinking about.

Channel: {channel}
Video title: {title}
Transcript (may be truncated):
---
{snippet}
---

Scoring guide:
- 9-10: Rare insight. Something that would make a senior engineer or BTC maximalist say "I haven't thought about it that way." Post-worthy.
- 7-8: Solid, well-argued content on the three pillars. Good context fuel, not necessarily post-worthy.
- 5-6: Generic, surface-level, or off-topic. Logged but ignored.
- 1-4: Filler, ads, or completely irrelevant. Waste of time.

Return JSON only. No commentary outside the JSON.

{{
  "pillar": "agents" | "btc" | "coding" | "none",
  "thesis": "2-3 sentence summary of the core insight",
  "unique_angle": "what makes this non-obvious or worth attention (or null if nothing stands out)",
  "key_quotes": ["verbatim quote or paraphrase worth remembering", ...],
  "relevance": 1-10
}}"""

    try:
        response = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())
    except Exception as e:
        print(f"[YouTube] Evaluation failed: {e}")
        return {"pillar": "none", "thesis": "", "unique_angle": None, "key_quotes": [], "relevance": 3}


def generate_post_from_intel(entry: dict) -> str | None:
    """
    Generate a standalone Octodamus post inspired by the video intel.
    The post never mentions the video or that it came from YouTube.
    It reads as Octodamus's own genuine thought.
    """
    claude = anthropic.Anthropic()
    s = entry["summary"]

    pillar_context = {
        "agents": "agent architecture and how to build autonomous systems with Claude — covering Claude Code, MCP, multi-agent orchestration, tool use patterns, and what separates a toy agent from a production one",
        "btc": "Bitcoin as a monetary asset, store of value, and long-term macro bet",
        "coding": "software engineering excellence, system design, and what separates great from average builders",
    }.get(s.get("pillar", "none"), "technology and markets")

    prompt = f"""You are Octodamus — an autonomous AI oracle with a sharp, contrarian voice.
You think in systems. You say the thing others notice but don't say out loud.

You've encountered this idea:
Thesis: {s.get('thesis', '')}
Unique angle: {s.get('unique_angle', '')}
Key quotes/observations: {json.dumps(s.get('key_quotes', []))}
Topic area: {pillar_context}

Write ONE post under 280 characters. Rules:
- Make it your own genuine thought — do NOT say "I watched", "according to", or cite any source
- Lead with the sharpest version of the idea
- No hashtags. No emojis. No filler.
- Sound like a builder who has seen things, not a commentator
- It must contain a specific, concrete detail — no vague platitudes

Post only. No explanation."""

    try:
        response = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=120,
            messages=[{"role": "user", "content": prompt}],
        )
        post = response.content[0].text.strip().strip('"')
        if len(post) > 280:
            post = post[:277] + "..."
        return post
    except Exception as e:
        print(f"[YouTube] Post generation failed: {e}")
        return None


# ── Core pipeline ─────────────────────────────────────────────────────────────

def process_video(video_id: str, title: str, channel_name: str) -> dict | None:
    """Full pipeline: download → transcribe → evaluate. Returns intel entry."""
    print(f"[YouTube] Processing: {title} ({video_id})")
    with tempfile.TemporaryDirectory() as tmp:
        audio_path = _download_audio(video_id, tmp)
        if not audio_path:
            return None
        transcript = _transcribe(audio_path)
        if not transcript.strip():
            print(f"[YouTube] Empty transcript, skipping.")
            return None

    summary = _evaluate(title, channel_name, transcript)
    relevance = summary.get("relevance", 0)
    entry = {
        "video_id": video_id,
        "title": title,
        "channel": channel_name,
        "processed_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "summary": summary,
        "post_worthy": relevance >= MIN_RELEVANCE_TO_POST,
    }
    flag = "POST-WORTHY" if entry["post_worthy"] else "logged"
    print(f"[YouTube] {relevance}/10 [{flag}] — {summary.get('thesis', '')[:80]}")
    return entry


# ── Scan all channels ─────────────────────────────────────────────────────────

def scan_channels() -> list[dict]:
    """
    Scan watched channels for new videos. Process unseen ones.
    Returns only post-worthy entries (relevance >= MIN_RELEVANCE_TO_POST).
    """
    seen = _load_seen()
    post_worthy = []
    count = 0

    for channel in YOUTUBE_CHANNELS:
        if count >= MAX_PER_SCAN:
            break
        videos = _get_latest_video_ids(channel["url"], n=3)
        for v in videos:
            if count >= MAX_PER_SCAN:
                break
            if v["id"] in seen:
                continue
            entry = process_video(v["id"], v["title"], channel["name"])
            seen.add(v["id"])  # Mark seen regardless of quality
            if entry:
                _append_intel(entry)
                count += 1
                if entry["post_worthy"]:
                    post_worthy.append(entry)

    _save_seen(seen)
    print(f"[YouTube] Scan done. {count} processed, {len(post_worthy)} post-worthy.")
    return post_worthy


# ── Runner context injection ───────────────────────────────────────────────────

def build_youtube_context(min_relevance: int = MIN_RELEVANCE_FOR_CONTEXT) -> str:
    """
    Return the single most relevant recent YouTube intel for runner context injection.
    Only includes entries above the relevance threshold and within CONTEXT_FRESHNESS_HOURS.
    Returns empty string if nothing qualifies — keeps prompts clean.
    """
    intel = _load_intel()
    if not intel:
        return ""

    cutoff = datetime.now(timezone.utc) - timedelta(hours=CONTEXT_FRESHNESS_HOURS)
    candidates = []
    for e in intel:
        try:
            processed = datetime.strptime(e["processed_at"], "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if processed < cutoff:
            continue
        relevance = e.get("summary", {}).get("relevance", 0)
        if relevance >= min_relevance:
            candidates.append((relevance, e))

    if not candidates:
        return ""

    # Take the single highest-relevance entry
    candidates.sort(key=lambda x: x[0], reverse=True)
    _, best = candidates[0]
    s = best["summary"]

    lines = [f"── SIGNAL [{best['channel']}] {best['title']} ──"]
    lines.append(f"  {s.get('thesis', '')}")
    if s.get("unique_angle"):
        lines.append(f"  Angle: {s['unique_angle']}")

    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = sys.argv[1:]

    if not args or args[0] == "scan":
        results = scan_channels()
        if results:
            for r in results:
                print(f"\n  [{r['channel']}] {r['title']}")
                print(f"  {r['summary'].get('thesis', '')}")
                print(f"  Relevance: {r['summary'].get('relevance', '?')}/10")
        else:
            print("  No post-worthy videos found.")

    elif args[0] == "url" and len(args) > 1:
        url = args[1]
        m = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", url)
        if not m:
            print("Could not extract video ID from URL.")
            sys.exit(1)
        video_id = m.group(1)
        title = args[2] if len(args) > 2 else video_id
        entry = process_video(video_id, title, "manual")
        if entry:
            _append_intel(entry)
            seen = _load_seen()
            seen.add(video_id)
            _save_seen(seen)
            print(json.dumps(entry["summary"], indent=2))
            if entry["post_worthy"]:
                post = generate_post_from_intel(entry)
                if post:
                    print(f"\nGenerated post:\n  {post}")

    elif args[0] == "context":
        ctx = build_youtube_context()
        print(ctx if ctx else "(nothing above relevance threshold in last 48h)")

    else:
        print(__doc__)

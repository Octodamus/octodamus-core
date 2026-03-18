"""
octo_whisper.py
OctoEar — Video Transcript Intelligence Mind

OpenAI Whisper API — $0.006 per minute of audio.
A 15-minute YouTube video costs $0.09 to transcribe.

Pipeline:
  1. yt-dlp downloads audio-only from a YouTube URL (no video = small file)
  2. Audio sent to OpenAI Whisper API → transcript text
  3. Transcript scored for bull/bear sentiment, key claims, price targets,
     named assets, and specific predictions ("BTC will hit X by Y date")
  4. Claude (Haiku/Qwen) summarizes the signal in 3 sentences

Designed to work WITH octo_youtube.py:
  - OctoTube finds what retail is watching (titles, views, channel activity)
  - OctoEar reads what the creator actually said in the video
  - Together they cover surface signal (title) + deep signal (content)

Cost controls built in:
  - MAX_MINUTES_PER_VIDEO cap (default 20min = $0.12 max per video)
  - MAX_VIDEOS_PER_RUN cap (default 2 = $0.24 max per run)
  - Audio trimmed to first N minutes only (intro/outro skipped)
  - Temp files cleaned up after each transcription

Bitwarden key: AGENT - Octodamus - OpenAI Whisper
Env var:       OPENAI_API_KEY

Install:
    pip3 install openai yt-dlp --break-system-packages

Usage:
    from octo_whisper import transcribe_video, run_whisper_scan, format_whisper_for_prompt

    # Single video
    result = transcribe_video("https://youtu.be/VIDEO_ID")

    # Auto-select top videos from OctoTube results
    from octo_youtube import run_youtube_scan
    tube   = run_youtube_scan()
    result = run_whisper_scan(tube)
"""

import os
import re
import json
import time
import shutil
import tempfile
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── Cost controls ─────────────────────────────────────────────────────────────

MAX_MINUTES_PER_VIDEO = 20    # cap audio at 20 min ($0.12 max)
MAX_VIDEOS_PER_RUN    = 2     # max 2 transcriptions per run ($0.24 max)
WHISPER_MODEL         = "whisper-1"
WHISPER_COST_PER_MIN  = 0.006  # USD

# ── Signal extraction config ──────────────────────────────────────────────────

# Assets to detect mentions of in transcripts
ASSET_KEYWORDS = [
    "bitcoin", "btc", "ethereum", "eth", "solana", "sol",
    "nvidia", "nvda", "tesla", "tsla", "apple", "aapl",
    "s&p", "spy", "nasdaq", "qqq", "gold", "silver",
    "federal reserve", "fed", "interest rates", "inflation",
    "recession", "bull market", "bear market", "crypto",
]

# Price target patterns: "X will hit $100k", "target of $500"
PRICE_TARGET_PATTERNS = [
    r'\$[\d,]+[kmb]?\b',              # $100k, $500, $1.2m
    r'\b\d+[,\d]*\s*(?:dollars?|usd)',  # 100000 dollars
    r'(?:target|price target|PT).*?\$[\d,]+',
    r'(?:hit|reach|touch|test)\s+\$?[\d,]+[kmb]?',
]

# Prediction patterns: timeframes + direction
PREDICTION_PATTERNS = [
    r'(?:by|before|end of)\s+(?:Q[1-4]|january|february|march|april|may|june|july|august|september|october|november|december|\d{4})',
    r'(?:next|this)\s+(?:week|month|quarter|year)',
    r'(?:within|in)\s+\d+\s+(?:days?|weeks?|months?)',
]

BULL_KEYWORDS = [
    "bullish", "bull run", "breakout", "accumulate", "buying opportunity",
    "going up", "pump", "rally", "moon", "parabolic", "undervalued",
    "strong support", "higher highs", "uptrend", "long",
]

BEAR_KEYWORDS = [
    "bearish", "crash", "dump", "correction", "sell", "danger",
    "warning", "bubble", "overvalued", "resistance", "lower lows",
    "downtrend", "short", "exit", "get out",
]


# ── Dependency check ──────────────────────────────────────────────────────────

def _check_deps() -> dict:
    issues = []
    try:
        import openai  # noqa
    except ImportError:
        issues.append("openai (pip3 install openai --break-system-packages)")

    if not shutil.which("yt-dlp"):
        issues.append("yt-dlp (pip3 install yt-dlp --break-system-packages)")

    if not shutil.which("ffmpeg"):
        issues.append("ffmpeg (sudo apt-get install ffmpeg -y)")

    return {"ok": len(issues) == 0, "missing": issues}


# ── Audio download ────────────────────────────────────────────────────────────

def _download_audio(url: str, output_dir: str, max_minutes: int = MAX_MINUTES_PER_VIDEO) -> Optional[str]:
    """
    Download audio-only from YouTube URL using yt-dlp.
    Returns path to downloaded audio file, or None on failure.
    Caps duration at max_minutes to control Whisper cost.
    """
    output_template = os.path.join(output_dir, "%(id)s.%(ext)s")
    max_seconds = max_minutes * 60

    cmd = [
        "yt-dlp",
        "--format", "bestaudio[ext=m4a]/bestaudio/best",
        "--extract-audio",
        "--audio-format", "mp3",
        "--audio-quality", "5",             # 128kbps — enough for speech
        "--no-playlist",
        "--quiet",
        "--no-warnings",
        "--output", output_template,
    ]

    # Trim to first N minutes using external downloader args
    # (yt-dlp sections: download only first max_seconds seconds)
    if max_seconds < 7200:  # don't trim if under 2 hours
        cmd += [
            "--download-sections", f"*0-{max_seconds}",
            "--force-keyframes-at-cuts",
        ]

    cmd.append(url)

    try:
        print(f"  [OctoEar] Downloading audio (max {max_minutes}min)...")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            print(f"  [OctoEar] yt-dlp error: {result.stderr[:200]}")
            return None

        # Find the downloaded file
        for f in Path(output_dir).glob("*.mp3"):
            return str(f)
        for f in Path(output_dir).glob("*.m4a"):
            return str(f)
        for f in Path(output_dir).glob("*.opus"):
            return str(f)

        print("  [OctoEar] No audio file found after download")
        return None

    except subprocess.TimeoutExpired:
        print("  [OctoEar] Download timed out (>5 min)")
        return None
    except Exception as e:
        print(f"  [OctoEar] Download error: {e}")
        return None


def _get_audio_duration_minutes(audio_path: str) -> float:
    """Get audio duration in minutes using ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", audio_path],
            capture_output=True, text=True, timeout=30
        )
        data = json.loads(result.stdout)
        seconds = float(data["format"]["duration"])
        return seconds / 60
    except Exception:
        return 0.0


# ── Whisper transcription ─────────────────────────────────────────────────────

def _transcribe_audio(audio_path: str, api_key: str) -> Optional[str]:
    """Send audio file to OpenAI Whisper API. Returns transcript text."""
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)

        file_size_mb = os.path.getsize(audio_path) / (1024 * 1024)
        print(f"  [OctoEar] Sending to Whisper API ({file_size_mb:.1f} MB)...")

        with open(audio_path, "rb") as f:
            response = client.audio.transcriptions.create(
                model=WHISPER_MODEL,
                file=f,
                response_format="text",
                language="en",
            )

        if isinstance(response, str):
            return response
        return getattr(response, "text", str(response))

    except Exception as e:
        print(f"  [OctoEar] Whisper API error: {e}")
        return None


# ── Signal extraction from transcript ────────────────────────────────────────

def _extract_signals(transcript: str) -> dict:
    """
    Extract financial signals from raw transcript text.
    Returns structured signal data without needing another LLM call.
    """
    text   = transcript.lower()
    text_r = transcript  # preserve case for some patterns

    # Asset mentions (count frequency)
    asset_mentions = {}
    for asset in ASSET_KEYWORDS:
        count = text.count(asset)
        if count > 0:
            asset_mentions[asset] = count
    # Sort by frequency
    asset_mentions = dict(sorted(asset_mentions.items(), key=lambda x: x[1], reverse=True))

    # Price targets
    price_targets = []
    for pattern in PRICE_TARGET_PATTERNS:
        matches = re.findall(pattern, text_r, re.IGNORECASE)
        price_targets.extend(matches[:3])  # cap per pattern
    price_targets = list(set(price_targets))[:8]  # dedupe, cap

    # Timeframe predictions
    predictions = []
    for pattern in PREDICTION_PATTERNS:
        matches = re.findall(pattern, text_r, re.IGNORECASE)
        predictions.extend(matches[:2])
    predictions = list(set(predictions))[:5]

    # Bull/bear keyword counts
    bull_count = sum(text.count(kw) for kw in BULL_KEYWORDS)
    bear_count = sum(text.count(kw) for kw in BEAR_KEYWORDS)

    if bull_count > bear_count * 1.5:
        sentiment = "bullish"
    elif bear_count > bull_count * 1.5:
        sentiment = "bearish"
    elif bull_count > 0 or bear_count > 0:
        sentiment = "mixed"
    else:
        sentiment = "neutral"

    # Word count as proxy for content density
    word_count = len(transcript.split())

    return {
        "sentiment":      sentiment,
        "bull_count":     bull_count,
        "bear_count":     bear_count,
        "asset_mentions": asset_mentions,
        "price_targets":  price_targets,
        "predictions":    predictions,
        "word_count":     word_count,
        "top_assets":     list(asset_mentions.keys())[:5],
    }


def _summarize_transcript(transcript: str, video_title: str, signals: dict) -> str:
    """
    Use Qwen/Haiku to distill the transcript to 3 key signal sentences.
    Falls back to a raw excerpt if LLM unavailable.
    """
    try:
        import os
        # Try Qwen first (cost-efficient)
        openrouter_key = os.environ.get("OPENROUTER_API_KEY")
        anthropic_key  = os.environ.get("ANTHROPIC_API_KEY")

        # Truncate transcript for prompt (first 6000 chars ~ 1500 tokens)
        excerpt = transcript[:6000]
        top_assets = ", ".join(signals.get("top_assets", [])[:4]) or "various assets"

        prompt = f"""You are a financial signal extractor for an AI oracle.

Video title: "{video_title}"
Top mentioned assets: {top_assets}
Creator sentiment detected: {signals['sentiment']}

Transcript excerpt:
{excerpt}

Extract the 3 most important financial signals from this transcript. Focus on:
- Specific price targets or levels mentioned
- Directional calls (buy/sell/hold) with reasoning
- Timeframe predictions
- Risk warnings

Respond in exactly 3 bullet points. Each bullet = 1 actionable signal. Be specific. No filler."""

        if openrouter_key:
            import requests
            r = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {openrouter_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "qwen/qwen3.5-flash-02-23",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 200,
                },
                timeout=30,
            )
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"].strip()

        elif anthropic_key:
            import anthropic
            client = anthropic.Anthropic(api_key=anthropic_key)
            msg = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}],
            )
            return msg.content[0].text.strip()

    except Exception as e:
        print(f"  [OctoEar] Summary LLM failed: {e}")

    # Fallback: return first 400 chars of transcript as excerpt
    return f"[Raw excerpt] {transcript[:400]}..."


# ── Single video transcription ────────────────────────────────────────────────

def transcribe_video(
    url: str,
    title: str = "",
    max_minutes: int = MAX_MINUTES_PER_VIDEO,
) -> dict:
    """
    Full pipeline for one video: download → transcribe → extract signals → summarize.
    Returns structured result dict.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return {"error": "no_api_key", "url": url}

    deps = _check_deps()
    if not deps["ok"]:
        return {"error": f"missing deps: {deps['missing']}", "url": url}

    result = {
        "url":       url,
        "title":     title,
        "timestamp": datetime.utcnow().isoformat(),
    }

    # Use temp dir — auto-cleaned even on errors
    with tempfile.TemporaryDirectory(prefix="octoear_") as tmpdir:
        # Download
        audio_path = _download_audio(url, tmpdir, max_minutes=max_minutes)
        if not audio_path:
            result["error"] = "download_failed"
            return result

        # Duration and cost estimate
        duration_min = _get_audio_duration_minutes(audio_path)
        cost_usd = round(duration_min * WHISPER_COST_PER_MIN, 4)
        result["duration_min"] = round(duration_min, 1)
        result["cost_usd"] = cost_usd
        print(f"  [OctoEar] Audio: {duration_min:.1f} min → estimated cost ${cost_usd:.3f}")

        # Transcribe
        transcript = _transcribe_audio(audio_path, api_key)
        if not transcript:
            result["error"] = "transcription_failed"
            return result

        print(f"  [OctoEar] Transcript: {len(transcript.split())} words")

    # Extract signals (outside tmpdir — audio already cleaned up)
    signals = _extract_signals(transcript)
    result["signals"] = signals

    # LLM summary
    print(f"  [OctoEar] Summarizing signals...")
    summary = _summarize_transcript(transcript, title, signals)
    result["summary"] = summary

    # Don't store full transcript in result to keep memory lean
    # Store first 500 chars as evidence
    result["transcript_excerpt"] = transcript[:500]

    print(f"  [OctoEar] Done. Sentiment: {signals['sentiment']} | "
          f"Top assets: {', '.join(signals['top_assets'][:3])}")

    return result


# ── Multi-video scan (integrates with OctoTube output) ───────────────────────

def run_whisper_scan(
    tube_results: dict | None = None,
    urls: list[str] | None = None,
    max_videos: int = MAX_VIDEOS_PER_RUN,
) -> dict:
    """
    Transcribe top N videos.

    Can be driven two ways:
    1. Pass tube_results from run_youtube_scan() — auto-selects highest-signal videos
    2. Pass explicit list of YouTube URLs

    Returns aggregated transcription results.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("[OctoEar] No OPENAI_API_KEY — Whisper scan skipped.")
        return {"error": "no_api_key", "timestamp": datetime.utcnow().isoformat()}

    deps = _check_deps()
    if not deps["ok"]:
        print(f"[OctoEar] Missing deps: {deps['missing']}")
        return {"error": "missing_deps", "missing": deps["missing"], "timestamp": datetime.utcnow().isoformat()}

    # Select videos to transcribe
    targets = []  # list of (url, title)

    if urls:
        targets = [(u, "") for u in urls[:max_videos]]

    elif tube_results:
        # Priority order:
        # 1. Videos from monitored channels (high quality signal)
        # 2. Trending videos with bear sentiment (fear = higher alpha)
        # 3. Trending videos with bull sentiment

        # Channel videos first
        for report in tube_results.get("channel_reports", []):
            if len(targets) >= max_videos:
                break
            uploads = report.get("recent_uploads", [])
            if uploads:
                v = uploads[0]  # most recent
                targets.append((v["url"], v["title"]))

        # Fill remaining slots with high-sentiment trending
        if len(targets) < max_videos:
            trending = tube_results.get("trending_videos", [])
            for v in trending:
                if len(targets) >= max_videos:
                    break
                url = v.get("url")
                if url and url not in [t[0] for t in targets]:
                    # Prefer high-signal (bear or strong bull)
                    if v.get("bear_score", 0) + v.get("bull_score", 0) >= 2:
                        targets.append((url, v["title"]))

    if not targets:
        print("[OctoEar] No target videos identified.")
        return {"error": "no_targets", "timestamp": datetime.utcnow().isoformat()}

    print(f"[OctoEar] Transcribing {len(targets)} video(s)...")
    print(f"[OctoEar] Estimated max cost: ${len(targets) * MAX_MINUTES_PER_VIDEO * WHISPER_COST_PER_MIN:.2f}")

    results = {
        "timestamp":     datetime.utcnow().isoformat(),
        "videos":        [],
        "total_cost":    0.0,
        "aggregate":     {},
    }

    for url, title in targets:
        print(f"\n[OctoEar] Processing: {title[:60] or url}")
        video_result = transcribe_video(url, title)
        results["videos"].append(video_result)
        results["total_cost"] += video_result.get("cost_usd", 0)
        time.sleep(2)

    # Aggregate signal across all transcribed videos
    all_sentiments = [v["signals"]["sentiment"] for v in results["videos"] if v.get("signals")]
    all_assets = {}
    for v in results["videos"]:
        if v.get("signals"):
            for asset, count in v["signals"].get("asset_mentions", {}).items():
                all_assets[asset] = all_assets.get(asset, 0) + count

    all_assets = dict(sorted(all_assets.items(), key=lambda x: x[1], reverse=True))

    results["aggregate"] = {
        "videos_transcribed": len([v for v in results["videos"] if not v.get("error")]),
        "sentiments":         all_sentiments,
        "creator_sentiment":  "bullish" if all_sentiments.count("bullish") > all_sentiments.count("bearish")
                              else ("bearish" if all_sentiments.count("bearish") > all_sentiments.count("bullish")
                              else "mixed"),
        "top_mentioned_assets": list(all_assets.keys())[:6],
        "total_cost_usd":     round(results["total_cost"], 4),
    }

    print(f"\n[OctoEar] Complete. {results['aggregate']['videos_transcribed']} videos | "
          f"${results['total_cost']:.3f} spent | "
          f"Sentiment: {results['aggregate']['creator_sentiment']}")

    return results


# ── Prompt formatter ──────────────────────────────────────────────────────────

def format_whisper_for_prompt(result: dict) -> str:
    if result.get("error"):
        return f"[OctoEar unavailable: {result['error']}]"

    lines = ["Video Transcript Intelligence (OctoEar — Whisper):"]

    agg = result.get("aggregate", {})
    if agg:
        lines.append(
            f"  Creator signals from {agg.get('videos_transcribed',0)} transcribed videos: "
            f"{agg.get('creator_sentiment','?').upper()}"
        )
        if agg.get("top_mentioned_assets"):
            lines.append(f"  Most discussed assets: {', '.join(agg['top_mentioned_assets'][:5])}")

    for v in result.get("videos", []):
        if v.get("error") or not v.get("signals"):
            continue
        title   = v.get("title", "Unknown")[:55]
        signals = v["signals"]
        summary = v.get("summary", "")

        lines.append(f"\n  [{signals['sentiment'].upper()}] \"{title}\"")
        if v.get("duration_min"):
            lines.append(f"    Duration: {v['duration_min']:.0f}min | Cost: ${v.get('cost_usd',0):.3f}")
        if signals.get("price_targets"):
            lines.append(f"    Price targets mentioned: {', '.join(signals['price_targets'][:4])}")
        if summary:
            lines.append(f"    Key signals:")
            for line in summary.split("\n")[:3]:
                if line.strip():
                    lines.append(f"      {line.strip()}")

    total_cost = agg.get("total_cost_usd", 0)
    if total_cost:
        lines.append(f"\n  [OctoEar cost this run: ${total_cost:.3f}]")

    return "\n".join(lines)


# ── Standalone run ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        # Direct URL mode: python3 octo_whisper.py https://youtu.be/...
        url = sys.argv[1]
        title = sys.argv[2] if len(sys.argv) > 2 else ""
        print(f"[OctoEar] Transcribing: {url}")
        result = transcribe_video(url, title)
        print("\n── Signals ──────────────────────────────────────")
        if result.get("signals"):
            sig = result["signals"]
            print(f"Sentiment:    {sig['sentiment']}")
            print(f"Top assets:   {', '.join(sig['top_assets'])}")
            print(f"Price targets: {sig['price_targets']}")
        if result.get("summary"):
            print(f"\nSummary:\n{result['summary']}")
        if result.get("transcript_excerpt"):
            print(f"\nExcerpt:\n{result['transcript_excerpt'][:300]}...")
    else:
        print("Usage: python3 octo_whisper.py <youtube_url> [video_title]")
        print("       Or import and call run_whisper_scan(tube_results)")
        deps = _check_deps()
        if not deps["ok"]:
            print(f"\nMissing dependencies: {deps['missing']}")

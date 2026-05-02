"""
octo_lunarcrush.py — LunarCrush Social Signal for Octodamus Oracle

Queries LunarCrush MCP (SSE) for social momentum metrics on BTC/ETH/SOL.
Used as signal #16 in SmartCall: social divergence check.

Free tier: 100 calls/day, 4/min. Degrades gracefully on failure.

Signal logic:
  bull  — Galaxy Score rising OR social volume increasing + sentiment positive
  bear  — Galaxy Score falling OR social volume declining + sentiment negative
  neutral — no clear direction
"""

import json
import threading
import time
from pathlib import Path

_KEY_FILE = Path(r"C:\Users\walli\octodamus\.octo_secrets")

_ASSET_TOPICS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
}

_cache: dict = {}
_CACHE_TTL = 900  # 15 min — matches oracle polling cadence


def _load_key() -> str:
    try:
        raw = json.loads(_KEY_FILE.read_text(encoding="utf-8"))
        return raw.get("secrets", raw).get("LUNARCRUSH_API_KEY", "")
    except Exception:
        return ""


def _mcp_call(tool: str, args: dict, key: str, timeout: int = 12) -> dict | None:
    """Single MCP tool call via SSE transport. Returns result dict or None."""
    try:
        import httpx

        SSE_URL = f"https://lunarcrush.ai/sse?key={key}"
        session_id = None
        result_holder: list = []
        ready = threading.Event()
        got_result = threading.Event()

        def _listen():
            nonlocal session_id
            try:
                with httpx.Client(timeout=None) as client:
                    with client.stream("GET", SSE_URL) as r:
                        buf = b""
                        for chunk in r.iter_bytes():
                            buf += chunk
                            text = buf.decode("utf-8", errors="replace")
                            while "\n\n" in text:
                                msg, text = text.split("\n\n", 1)
                                buf = text.encode()
                                for line in msg.split("\n"):
                                    if line.startswith("data:"):
                                        data = line[5:].strip()
                                        if "sessionId=" in data and session_id is None:
                                            session_id = data.split("sessionId=")[1].strip()
                                            ready.set()
                                        elif data.startswith("{"):
                                            try:
                                                d = json.loads(data)
                                                if d.get("id") == 3 and "result" in d:
                                                    result_holder.append(d["result"])
                                                    got_result.set()
                                            except Exception:
                                                pass
                            if got_result.is_set():
                                break
            except Exception:
                ready.set()

        t = threading.Thread(target=_listen, daemon=True)
        t.start()

        if not ready.wait(timeout=8):
            return None
        if not session_id:
            return None

        MSG = f"https://lunarcrush.ai/sse/message?key={key}&sessionId={session_id}"

        httpx.post(MSG, json={
            "jsonrpc": "2.0", "method": "initialize", "id": 1,
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "octodamus", "version": "1.0"}}
        }, timeout=5)
        time.sleep(0.2)

        httpx.post(MSG, json={"jsonrpc": "2.0", "method": "notifications/initialized"}, timeout=5)
        time.sleep(0.2)

        httpx.post(MSG, json={
            "jsonrpc": "2.0", "method": "tools/call", "id": 3,
            "params": {"name": tool, "arguments": args}
        }, timeout=5)

        got_result.wait(timeout=timeout)
        return result_holder[0] if result_holder else None

    except Exception:
        return None


def _parse_topic_response(text: str) -> dict:
    """Extract key metrics from LunarCrush Topic tool text response."""
    metrics = {}
    lines = text.lower().split("\n")
    for line in lines:
        if "galaxy score" in line:
            import re
            m = re.search(r"[\d.]+", line)
            if m:
                metrics["galaxy_score"] = float(m.group())
        if "alt rank" in line or "altrank" in line:
            import re
            m = re.search(r"[\d,]+", line)
            if m:
                metrics["alt_rank"] = int(m.group().replace(",", ""))
        if "social volume" in line:
            import re
            m = re.search(r"[\d,]+", line)
            if m:
                metrics["social_volume"] = int(m.group().replace(",", ""))
        if "sentiment" in line:
            if any(w in line for w in ["bullish", "positive", "high"]):
                metrics["sentiment"] = "positive"
            elif any(w in line for w in ["bearish", "negative", "low"]):
                metrics["sentiment"] = "negative"

    return metrics


def get_social_signal(asset: str) -> dict:
    """
    Get LunarCrush social signal for an asset.

    Returns:
      signal:       "bull" | "bear" | "neutral"
      galaxy_score: float (0-100) or None
      note:         human-readable summary
      source:       "lunarcrush" | "cache" | "unavailable"
    """
    now = time.monotonic()
    if asset in _cache and now - _cache[asset]["ts"] < _CACHE_TTL:
        cached = dict(_cache[asset])
        cached["source"] = "cache"
        return cached

    key = _load_key()
    if not key:
        return {"signal": "neutral", "galaxy_score": None, "note": "No API key", "source": "unavailable"}

    topic = _ASSET_TOPICS.get(asset, asset.lower())
    result = _mcp_call("Topic", {"topic": topic}, key, timeout=15)

    if not result:
        return {"signal": "neutral", "galaxy_score": None, "note": "MCP unavailable", "source": "unavailable"}

    try:
        content = result.get("content", [{}])
        text = content[0].get("text", "") if content else ""
        metrics = _parse_topic_response(text)

        galaxy = metrics.get("galaxy_score")
        sentiment = metrics.get("sentiment", "neutral")

        # Derive signal
        signal = "neutral"
        if galaxy is not None:
            if galaxy >= 60 or sentiment == "positive":
                signal = "bull"
            elif galaxy <= 35 or sentiment == "negative":
                signal = "bear"

        note = f"Galaxy={galaxy} sentiment={sentiment}" if galaxy else f"sentiment={sentiment}"

        out = {
            "signal":       signal,
            "galaxy_score": galaxy,
            "sentiment":    sentiment,
            "note":         note,
            "source":       "lunarcrush",
            "ts":           now,
        }
        _cache[asset] = out
        return out

    except Exception as e:
        return {"signal": "neutral", "galaxy_score": None, "note": f"parse error: {e}", "source": "unavailable"}


def social_divergence_check(asset: str, direction: str) -> dict:
    """
    Check if LunarCrush social momentum diverges from the oracle direction.

    Returns:
      diverges:  bool — True = social contradicts direction
      signal:    bull/bear/neutral
      note:      string for oracle call note
    """
    s = get_social_signal(asset)
    sig = s.get("signal", "neutral")
    source = s.get("source", "unavailable")

    if source == "unavailable" or sig == "neutral":
        return {"diverges": False, "signal": sig, "note": s.get("note", ""), "available": False}

    diverges = (sig == "bear" and direction == "UP") or (sig == "bull" and direction == "DOWN")
    note = f"LunarCrush: {sig} ({s.get('note','')})"

    return {"diverges": diverges, "signal": sig, "note": note, "available": True}

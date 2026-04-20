"""
octo_boto_oracle_bridge.py — OctoBoto → Oracle Call Bridge

Connects OctoBoto paper trades to the Oracle call record and X posts.

When OctoBoto opens a position  → record as Oracle call + queue X post
When OctoBoto closes a position → resolve Oracle call + queue X post

Direction mapping:
  YES bet → direction=UP, entry=price*100, target=100
  NO  bet → direction=DOWN, entry=price*100, target=0
  Resolution YES → exit_price=100  (UP wins, DOWN loses)
  Resolution NO  → exit_price=0    (DOWN wins, UP loses)

This makes the existing resolve_call WIN/LOSS logic work correctly for
binary Polymarket outcomes without any changes to octo_calls.py.

Usage (from octo_boto.py):
  from octo_boto_oracle_bridge import on_position_opened, on_position_closed

  pos = TRACKER.open_position(...)
  if pos:
      on_position_opened(pos)

  closed_list = TRACKER.close_position(mid, resolution)
  for closed in closed_list:
      on_position_closed(closed, TRACKER.balance())
"""

import json
import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger("OctoBotoOracle")

# Maps market_id -> oracle call_id so we can resolve later
_ID_MAP_FILE = Path(r"C:\Users\walli\octodamus\data\boto_call_map.json")


# ── ID Map ────────────────────────────────────────────────────────────────────

def _load_map() -> dict:
    try:
        if _ID_MAP_FILE.exists():
            return json.loads(_ID_MAP_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_map(m: dict):
    _ID_MAP_FILE.parent.mkdir(parents=True, exist_ok=True)
    _ID_MAP_FILE.write_text(json.dumps(m, indent=2), encoding="utf-8")


# ── Oracle Call Recording ─────────────────────────────────────────────────────

def _asset_from_question(question: str) -> str:
    """Derive a short readable asset label from the market question."""
    import re
    q = question.upper()
    # Extract key words, skip common filler words
    skip = {"WILL","THE","BE","BY","A","AN","IS","IN","OF","TO","FOR","ON","AT","OR","AND","BEFORE","AFTER","NEXT"}
    words = re.findall(r"[A-Z]+", q)
    tokens = [w[:4] for w in words if w not in skip and len(w) >= 3]
    label = "-".join(tokens[:3])
    return label[:12] if label else "PM-CALL"


def on_position_opened(pos: dict) -> Optional[dict]:
    """
    Record an OctoBoto position as an Oracle call.
    Called synchronously after TRACKER.open_position() returns a position.
    Returns the Oracle call dict, or None on failure.
    """
    try:
        from octo_calls import _load, _save
        from datetime import datetime, timezone

        calls = _load()
        market_id = pos["market_id"]

        # Don't double-record
        id_map = _load_map()
        if market_id in id_map:
            log.info(f"[Bridge] Already recorded call for market {market_id}")
            return None

        asset = _asset_from_question(pos.get("question", market_id))
        direction = "UP" if pos["side"] == "YES" else "DOWN"
        entry_price = round(pos["entry_price"] * 100, 2)  # 0.58 -> 58.0
        target_price = 100.0 if pos["side"] == "YES" else 0.0

        call = {
            "id":           len(calls) + 1,
            "asset":        asset,
            "direction":    direction,
            "entry_price":  entry_price,
            "target_price": target_price,
            "timeframe":    "Polymarket",
            "note":         pos["question"][:200],
            "made_at":      datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "resolved":     False,
            "outcome":      None,
            "exit_price":   None,
            "resolved_at":  None,
            # Polymarket-specific fields
            "call_type":    "polymarket",
            "market_id":    market_id,
            "pm_side":      pos["side"],
            "pm_ev":        round(pos.get("ev", 0), 4),
            "pm_confidence": pos.get("confidence", ""),
        }

        calls.append(call)
        _save(calls)

        # Save market_id -> call_id mapping
        id_map[market_id] = call["id"]
        _save_map(id_map)

        log.info(f"[Bridge] Oracle call #{call['id']} recorded: {asset} {direction} @ {entry_price}")

        # Queue X post
        _post_opened(pos, call["id"])

        return call

    except Exception as e:
        log.error(f"[Bridge] on_position_opened failed: {e}")
        return None


def on_position_closed(closed: dict, balance: float) -> Optional[dict]:
    """
    Resolve an OctoBoto Oracle call when the position closes.
    Called synchronously after TRACKER.close_position() for each closed trade.
    Returns the resolved Oracle call dict, or None on failure.
    """
    try:
        from octo_calls import resolve_call

        market_id = closed.get("market_id", "")
        id_map = _load_map()
        call_id = id_map.get(market_id)

        if not call_id:
            log.warning(f"[Bridge] No Oracle call found for market {market_id}")
            return None

        resolution = closed.get("resolution", "")
        exit_price = 100.0 if resolution == "YES" else 0.0

        result = resolve_call(call_id, exit_price)
        if result:
            log.info(f"[Bridge] Oracle call #{call_id} resolved: {result['outcome']}")
            # Generate post-mortem so Octodamus learns from every Polymarket call
            try:
                from octo_calls import _generate_post_mortem, _load, _save
                pm = _generate_post_mortem(result)
                if pm:
                    all_calls = _load()
                    for c in all_calls:
                        if c["id"] == call_id:
                            c["post_mortem"] = pm
                            result["post_mortem"] = pm
                            break
                    _save(all_calls)
                    log.info(f"[Bridge] Post-mortem saved for #{call_id}: {pm[:100]}")
            except Exception as e:
                log.warning(f"[Bridge] Post-mortem failed: {e}")
            # Clean up map
            id_map.pop(market_id, None)
            _save_map(id_map)
            # Queue X post
            _post_closed(closed, result, balance)

        return result

    except Exception as e:
        log.error(f"[Bridge] on_position_closed failed: {e}")
        return None


# ── X Posts ───────────────────────────────────────────────────────────────────

def _truncate_question(q: str, max_len: int = 80) -> str:
    return q[:max_len] + "..." if len(q) > max_len else q


def _post_opened(pos: dict, call_id: int):
    """X posting for trade opens disabled — OctoBoto trades silently."""
    log.info(f"[Bridge] Trade #{call_id} open — X posting disabled.")


def _post_closed(closed: dict, call: dict, balance: float):
    """X posting for trade closes disabled — OctoBoto trades silently."""
    call_id = call.get("id", "?")
    outcome = call.get("outcome", "?")
    log.info(f"[Bridge] Trade #{call_id} closed ({outcome}) — X posting disabled.")

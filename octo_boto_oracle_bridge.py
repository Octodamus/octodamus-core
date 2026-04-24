"""
octo_boto_oracle_bridge.py — DISABLED

Architecture:
  Octodamus = the oracle AI. Makes directional calls (BTC/ETH/SOL UP/DOWN)
              via the 9/11 consensus system. Posts publicly to X.
              Records calls in octo_calls.json (call_type: "oracle").

  OctoBoto  = the Telegram paper-trading bot. Makes its own Polymarket
              trade decisions independently. Paper wallet until profitable.
              Records trades in PaperTracker (octo_boto_tracker.py) only.

These are separate systems. OctoBoto's paper trades do NOT appear in
Octodamus's oracle call record. The oracle win rate reflects only
Octodamus's own directional calls.

This file is kept as a stub so existing octo_boto.py imports don't break.
on_position_opened and on_position_closed are intentional no-ops.
"""

import logging
from typing import Optional

log = logging.getLogger("OctoBotoOracle")


def on_position_opened(pos: dict) -> Optional[dict]:
    """Email alert when OctoBoto opens a position."""
    try:
        from octo_notify import notify_trade_opened
        notify_trade_opened(
            question   = pos.get("question", ""),
            side       = pos.get("side", ""),
            entry_price= float(pos.get("entry_price", 0)),
            ev         = float(pos.get("ev", 0)),
            size_usd   = float(pos.get("size", 0)),
            url        = pos.get("url", ""),
        )
    except Exception as e:
        log.warning(f"notify_trade_opened failed: {e}")
    return None


def on_position_closed(closed: dict, balance: float) -> Optional[dict]:
    """Email alert when OctoBoto closes a position."""
    try:
        from octo_notify import notify_trade_closed
        entry = float(closed.get("entry_price", 0))
        exit_ = float(closed.get("exit_price", entry))
        size  = float(closed.get("size", 0))
        won   = bool(closed.get("won", False))
        pnl   = (exit_ - entry) * size if closed.get("side") == "YES" else (entry - exit_) * size
        notify_trade_closed(
            question   = closed.get("question", ""),
            side       = closed.get("side", ""),
            won        = won,
            entry_price= entry,
            exit_price = exit_,
            pnl_usd    = pnl,
        )
    except Exception as e:
        log.warning(f"notify_trade_closed failed: {e}")
    return None

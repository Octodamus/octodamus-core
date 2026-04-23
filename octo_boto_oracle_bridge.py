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
    """No-op. OctoBoto trades are tracked by PaperTracker, not the oracle record."""
    return None


def on_position_closed(closed: dict, balance: float) -> Optional[dict]:
    """No-op. OctoBoto trade outcomes are tracked by PaperTracker, not the oracle record."""
    return None

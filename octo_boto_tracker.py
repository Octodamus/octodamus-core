"""
octo_boto_tracker.py — OctoBoto Paper Trading Ledger v2
Rebuilt from API contract in octo_boto.py.

Tracks paper positions, P&L, Sharpe ratio, max drawdown.
All data persisted to JSON on disk.

Exports:
  PaperTracker    — main class
  age_str         — human-readable age from ISO timestamp
  STARTING_BALANCE — default $500
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from octo_boto_math import compute_sharpe, compute_max_drawdown

# ─── Config ───────────────────────────────────────────────────────────────────
STARTING_BALANCE = 500.0
TRADE_FEE_PCT    = 0.005    # 0.5% simulated fee per trade
TRADES_FILE      = Path(r"C:\Users\walli\octodamus\octo_boto_trades.json")

CONF_SCORES = {"high": 3.0, "medium": 2.0, "low": 1.0}


def age_str(iso_timestamp: str) -> str:
    """Human-readable age from ISO timestamp string."""
    if not iso_timestamp:
        return "?"
    try:
        dt  = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = now - dt
        hours = delta.total_seconds() / 3600
        if hours < 1:
            return f"{int(delta.total_seconds() / 60)}m"
        elif hours < 24:
            return f"{int(hours)}h"
        else:
            return f"{int(hours / 24)}d"
    except Exception:
        return "?"


class PaperTracker:
    """
    Paper trading ledger. Persists to JSON.

    Data structure:
    {
      "balance": float,
      "starting_balance": float,
      "positions": [ {...position dicts...} ],
      "closed": [ {...closed trade dicts...} ],
      "balance_history": [float, ...],
      "fees_paid": float,
    }
    """

    def __init__(self, path: Path = TRADES_FILE):
        self._path = path
        self._data = self._load()

    # ─── Persistence ──────────────────────────────────────────────────────

    def _load(self) -> dict:
        try:
            with open(self._path) as f:
                data = json.load(f)
            # Ensure all required keys exist
            data.setdefault("balance", STARTING_BALANCE)
            data.setdefault("starting_balance", STARTING_BALANCE)
            data.setdefault("positions", [])
            data.setdefault("closed", [])
            data.setdefault("balance_history", [STARTING_BALANCE])
            data.setdefault("fees_paid", 0.0)
            return data
        except (FileNotFoundError, json.JSONDecodeError):
            return {
                "balance":          STARTING_BALANCE,
                "starting_balance": STARTING_BALANCE,
                "positions":        [],
                "closed":           [],
                "balance_history":  [STARTING_BALANCE],
                "fees_paid":        0.0,
            }

    def _save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w") as f:
            json.dump(self._data, f, indent=2)

    # ─── Read Methods ─────────────────────────────────────────────────────

    def balance(self) -> float:
        return self._data["balance"]

    def open_positions(self) -> list:
        return list(self._data["positions"])

    def total_deployed(self) -> float:
        return sum(p.get("size", 0) for p in self._data["positions"])

    def has_position(self, market_id: str) -> bool:
        return any(p["market_id"] == market_id for p in self._data["positions"])

    def get_position_by_num(self, num: int) -> Optional[dict]:
        """Get position by 1-indexed number."""
        positions = self._data["positions"]
        if 1 <= num <= len(positions):
            return positions[num - 1]
        return None

    # ─── Open Position ────────────────────────────────────────────────────

    def open_position(
        self,
        market_id:   str,
        question:    str,
        side:        str,
        size:        float,
        entry_price: float,
        true_p:      float,
        ev:          float,
        kelly_frac:  float,
        confidence:  str,
        reasoning:   str,
        score:       float,
        url:         str = "",
    ) -> Optional[dict]:
        """
        Open a new paper position. Deducts size + fee from balance.
        Returns the position dict, or None if insufficient balance or duplicate.
        """
        if self.has_position(market_id):
            return None

        fee = round(size * TRADE_FEE_PCT, 4)
        total_cost = size + fee

        if total_cost > self._data["balance"]:
            return None

        pos = {
            "id":           str(uuid.uuid4())[:8],
            "market_id":    market_id,
            "question":     question,
            "side":         side,
            "size":         round(size, 4),
            "entry_price":  round(entry_price, 4),
            "true_p":       round(true_p, 4),
            "ev":           round(ev, 4),
            "kelly_frac":   round(kelly_frac, 4),
            "confidence":   confidence,
            "reasoning":    reasoning[:350],
            "score":        round(score, 4),
            "url":          url,
            "fee":          fee,
            "opened_at":    datetime.now(timezone.utc).isoformat(),
        }

        self._data["balance"] -= total_cost
        self._data["fees_paid"] += fee
        self._data["positions"].append(pos)
        self._data["balance_history"].append(round(self._data["balance"], 2))
        self._save()
        return pos

    # ─── Close Position (by market resolution) ────────────────────────────

    def close_position(self, market_id: str, resolution: str) -> list:
        """
        Close all positions on a market when it resolves.
        resolution: "YES" or "NO"
        Returns list of closed trade dicts.
        """
        to_close = [p for p in self._data["positions"] if p["market_id"] == market_id]
        if not to_close:
            return []

        closed_list = []
        for pos in to_close:
            won = (pos["side"] == resolution)

            if won:
                # Win: paid entry_price per share, receive 1.0 per share
                # Profit = size * (1 / entry_price - 1) ... simplified:
                # shares = size / entry_price, payout = shares * 1.0
                payout = pos["size"] / pos["entry_price"] if pos["entry_price"] > 0 else pos["size"]
            else:
                payout = 0.0

            payout = round(payout, 4)
            pnl    = round(payout - pos["size"], 4)
            pnl_pct = round((pnl / pos["size"]) * 100, 2) if pos["size"] > 0 else 0.0

            closed = {
                **pos,
                "resolution": resolution,
                "won":        won,
                "payout":     payout,
                "pnl":        pnl,
                "pnl_pct":    pnl_pct,
                "closed_at":  datetime.now(timezone.utc).isoformat(),
            }

            self._data["balance"] += payout
            self._data["closed"].append(closed)
            closed_list.append(closed)

        # Remove from open positions
        self._data["positions"] = [
            p for p in self._data["positions"] if p["market_id"] != market_id
        ]
        self._data["balance_history"].append(round(self._data["balance"], 2))
        self._save()
        return closed_list

    # ─── Force Close (manual exit at 50% value) ──────────────────────────

    def force_close_by_num(self, num: int) -> Optional[dict]:
        """
        Close position #num (1-indexed) at 50% of invested size.
        Simulates early exit with unknown outcome.
        """
        positions = self._data["positions"]
        if not (1 <= num <= len(positions)):
            return None

        pos    = positions.pop(num - 1)
        payout = round(pos["size"] * 0.5, 4)
        pnl    = round(payout - pos["size"], 4)
        pnl_pct = round((pnl / pos["size"]) * 100, 2) if pos["size"] > 0 else 0.0

        closed = {
            **pos,
            "resolution": "MANUAL",
            "won":        False,
            "payout":     payout,
            "pnl":        pnl,
            "pnl_pct":    pnl_pct,
            "closed_at":  datetime.now(timezone.utc).isoformat(),
        }

        self._data["balance"] += payout
        self._data["closed"].append(closed)
        self._data["balance_history"].append(round(self._data["balance"], 2))
        self._save()
        return closed

    # ─── P&L Summary ─────────────────────────────────────────────────────

    def pnl_summary(self) -> dict:
        """Full stats dict — matches all keys used by octo_boto.py."""
        closed   = self._data["closed"]
        positions = self._data["positions"]

        wins   = [t for t in closed if t.get("won")]
        losses = [t for t in closed if not t.get("won")]

        total_pnl = sum(t.get("pnl", 0) for t in closed)
        starting  = self._data["starting_balance"]
        balance   = self._data["balance"]

        pnl_pcts = [t.get("pnl_pct", 0) for t in closed if t.get("pnl_pct") is not None]

        # Confidence scores
        conf_scores = [
            CONF_SCORES.get(t.get("confidence", "low"), 1.0) for t in closed
        ]

        # EV stats
        evs = [t.get("ev", 0) for t in closed]

        # Best/worst trades
        best_trade  = max(closed, key=lambda t: t.get("pnl", 0)) if closed else None
        worst_trade = min(closed, key=lambda t: t.get("pnl", 0)) if closed else None

        return {
            "balance":        round(balance, 2),
            "starting":       round(starting, 2),
            "total_pnl":      round(total_pnl, 2),
            "total_pnl_pct":  round((total_pnl / starting) * 100, 2) if starting > 0 else 0.0,
            "fees_paid":      round(self._data["fees_paid"], 2),
            "num_trades":     len(closed),
            "open_count":     len(positions),
            "wins":           len(wins),
            "losses":         len(losses),
            "win_rate":       round(len(wins) / len(closed) * 100, 1) if closed else 0.0,
            "sharpe":         compute_sharpe(pnl_pcts),
            "max_drawdown":   compute_max_drawdown(self._data["balance_history"]),
            "deployed":       round(sum(p.get("size", 0) for p in positions), 2),
            "avg_ev":         round(sum(evs) / len(evs), 4) if evs else 0.0,
            "avg_conf_score": round(sum(conf_scores) / len(conf_scores), 1) if conf_scores else 1.0,
            "best_trade":     best_trade,
            "worst_trade":    worst_trade,
        }

    # ─── Reset ────────────────────────────────────────────────────────────

    def reset(self, new_balance: float = STARTING_BALANCE):
        """Wipe all trades, reset balance."""
        self._data = {
            "balance":          new_balance,
            "starting_balance": new_balance,
            "positions":        [],
            "closed":           [],
            "balance_history":  [new_balance],
            "fees_paid":        0.0,
        }
        self._save()

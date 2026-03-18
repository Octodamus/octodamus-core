"""
octo_boto_tracker.py — Paper Trading Ledger v2
Fixes:
  - STARTING_BALANCE = $500 (was $1,000)
  - Duplicate prevention — cannot open same market_id twice
  - close_position() closes ALL positions for a market, not just first
  - force_close() now accepts position number (1-indexed) not raw ID
  - Added get_position_by_num() for /close <n> command
  - Added age_str() to display how long a position has been open
  - Atomic write (write to .tmp then rename) prevents corrupt JSON on crash
  - Added per-trade fee simulation (0.5% taker fee — reflects Polymarket reality)
"""

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import tempfile
import os

import numpy as np

TRADES_FILE      = Path("/home/walli/octodamus/octo_boto_trades.json")
STARTING_BALANCE = 500.0    # $500 paper USDC (was $1,000)
TAKER_FEE        = 0.005    # 0.5% fee on position size (Polymarket standard)
MIN_POSITION     = 2.00     # $2 minimum position size

_lock = threading.Lock()


class PaperTracker:

    def __init__(self, trades_file: Path = TRADES_FILE):
        self.trades_file = trades_file
        self.trades_file.parent.mkdir(parents=True, exist_ok=True)
        self._data = self._load()

    # ─── Persistence ──────────────────────────────────────────────────────────

    def _load(self) -> dict:
        if self.trades_file.exists():
            try:
                with open(self.trades_file) as f:
                    return json.load(f)
            except Exception as e:
                print(f"[Tracker] Load error: {e} — starting fresh")
        return self._fresh()

    def _fresh(self) -> dict:
        return {
            "version":         2,
            "starting_balance": STARTING_BALANCE,
            "balance":         STARTING_BALANCE,
            "open_positions":  [],
            "closed_trades":   [],
            "balance_history": [STARTING_BALANCE],
            "fees_paid":       0.0,
            "created_at":      _now(),
        }

    def _save(self):
        """
        Atomic write — write to temp file then rename.
        Prevents corrupt JSON if process is killed mid-write.
        """
        dir_  = self.trades_file.parent
        tmp   = dir_ / (self.trades_file.name + ".tmp")
        try:
            with open(tmp, "w") as f:
                json.dump(self._data, f, indent=2, default=str)
            os.replace(tmp, self.trades_file)  # Atomic on POSIX
        except Exception as e:
            print(f"[Tracker] Save error: {e}")

    # ─── Read Accessors ────────────────────────────────────────────────────────

    def balance(self) -> float:
        return round(self._data["balance"], 2)

    def open_positions(self) -> list:
        return list(self._data.get("open_positions", []))

    def closed_trades(self) -> list:
        return list(self._data.get("closed_trades", []))

    def total_deployed(self) -> float:
        return round(sum(p["size"] for p in self.open_positions()), 2)

    def total_fees(self) -> float:
        return round(self._data.get("fees_paid", 0.0), 2)

    def has_position(self, market_id: str) -> bool:
        """Check if we already have an open position in this market."""
        return any(p["market_id"] == market_id for p in self.open_positions())

    def get_position_by_num(self, num: int) -> Optional[dict]:
        """1-indexed position lookup for /close <n> command."""
        positions = self.open_positions()
        if 1 <= num <= len(positions):
            return positions[num - 1]
        return None

    # ─── Trade Lifecycle ──────────────────────────────────────────────────────

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
        score:       float = 0.0,
        url:         str = "",
    ) -> Optional[dict]:
        """
        Open a new paper position.
        Returns position dict, or None with reason string on failure.
        Bug fix v2: duplicate check prevents double-entering same market.
        """
        with _lock:
            # Duplicate prevention
            if self.has_position(market_id):
                print(f"[Tracker] Already have position in market {market_id[:12]}… — skipping")
                return None

            available = self._data["balance"]
            size      = min(round(size, 2), available)

            if size < MIN_POSITION:
                print(f"[Tracker] Size ${size:.2f} below minimum ${MIN_POSITION}")
                return None

            # Simulate entry fee
            fee    = round(size * TAKER_FEE, 4)
            net    = size - fee
            shares = round(net / entry_price, 4) if entry_price > 0 else 0

            position = {
                "id":          f"{market_id[:12]}_{side}_{_ts()}",
                "market_id":   market_id,
                "question":    question[:120].strip(),
                "side":        side.upper(),
                "size":        size,        # cash deployed
                "fee":         fee,         # entry fee paid
                "entry_price": round(entry_price, 4),
                "shares":      shares,
                "true_p":      round(true_p, 4),
                "ev":          round(ev, 4),
                "kelly_frac":  round(kelly_frac, 4),
                "score":       round(score, 4),
                "confidence":  confidence,
                "reasoning":   reasoning[:250].strip(),
                "url":         url,
                "opened_at":   _now(),
                "status":      "open",
            }

            self._data["balance"]            -= size
            self._data["fees_paid"]          += fee
            self._data["open_positions"].append(position)
            self._save()
            return position

    def close_position(self, market_id: str, resolution: str) -> list:
        """
        Close ALL open positions for a market on resolution.
        Bug fix v2: v1 only closed the FIRST matching position.
        Returns list of closed trade dicts (usually 1, could be 0).
        """
        resolution = resolution.upper().strip()
        closed_list = []

        with _lock:
            positions = self._data["open_positions"]
            remaining = []

            for pos in positions:
                if pos["market_id"] != market_id:
                    remaining.append(pos)
                    continue

                won    = pos["side"].upper() == resolution
                payout = round(pos["shares"] * 1.0, 2) if won else 0.0
                fee    = round(payout * TAKER_FEE, 4) if won else 0.0
                net    = round(payout - fee, 2)
                pnl    = round(net - pos["size"], 2)
                pnl_pct = round(pnl / pos["size"] * 100, 2) if pos["size"] > 0 else 0

                closed = {
                    **pos,
                    "resolution":  resolution,
                    "won":         won,
                    "payout":      payout,
                    "exit_fee":    fee,
                    "net_payout":  net,
                    "pnl":         pnl,
                    "pnl_pct":     pnl_pct,
                    "closed_at":   _now(),
                    "status":      "closed",
                }

                self._data["balance"] += net
                self._data["fees_paid"] += fee
                self._data["balance_history"].append(round(self._data["balance"], 2))
                self._data["closed_trades"].append(closed)
                closed_list.append(closed)

            self._data["open_positions"] = remaining
            if closed_list:
                self._save()

        return closed_list

    def force_close_by_num(self, num: int, note: str = "Manual close") -> Optional[dict]:
        """
        Manually close position #N at 50% of entry value.
        Bug fix v2: accepts 1-based position index, not opaque ID string.
        50% is pessimistic but reflects illiquidity of selling before resolution.
        """
        with _lock:
            positions = self._data["open_positions"]
            idx = num - 1

            if idx < 0 or idx >= len(positions):
                return None

            pos     = positions[idx]
            payout  = round(pos["size"] * 0.5, 2)
            pnl     = round(payout - pos["size"], 2)
            pnl_pct = round(pnl / pos["size"] * 100, 2)

            closed = {
                **pos,
                "resolution": "MANUAL",
                "won":         False,
                "payout":      payout,
                "exit_fee":    0.0,
                "net_payout":  payout,
                "pnl":         pnl,
                "pnl_pct":     pnl_pct,
                "note":        note,
                "closed_at":   _now(),
                "status":      "closed",
            }

            self._data["balance"] += payout
            self._data["balance_history"].append(round(self._data["balance"], 2))
            self._data["open_positions"].pop(idx)
            self._data["closed_trades"].append(closed)
            self._save()
            return closed

    # ─── Summary Stats ────────────────────────────────────────────────────────

    def pnl_summary(self) -> dict:
        closed   = self.closed_trades()
        bal      = self.balance()
        deployed = self.total_deployed()
        fees     = self.total_fees()
        starting = self._data.get("starting_balance", STARTING_BALANCE)

        base = {
            "balance":       bal,
            "deployed":      deployed,
            "available":     round(bal, 2),
            "starting":      starting,
            "fees_paid":     fees,
            "total_pnl":     0.0,
            "total_pnl_pct": 0.0,
            "num_trades":    0,
            "wins":          0,
            "losses":        0,
            "win_rate":      0.0,
            "sharpe":        0.0,
            "max_drawdown":  0.0,
            "open_count":    len(self.open_positions()),
            "avg_ev":        0.0,
            "avg_conf_score": 0.0,
            "best_trade":    None,
            "worst_trade":   None,
        }

        if not closed:
            return base

        pnls      = [t["pnl"] for t in closed]
        pnl_pcts  = [t["pnl_pct"] / 100 for t in closed]
        wins      = sum(1 for p in pnls if p > 0)
        total_pnl = round(sum(pnls), 2)

        # Sharpe (per-trade, not annualised)
        arr    = np.array(pnl_pcts, dtype=float)
        sharpe = 0.0
        if len(arr) >= 3 and arr.std() > 0:
            sharpe = round((arr.mean() / arr.std()) * np.sqrt(len(arr)), 2)

        # Max drawdown
        history = self._data.get("balance_history", [starting])
        arr_b   = np.array(history, dtype=float)
        peak    = np.maximum.accumulate(arr_b)
        dd      = ((arr_b - peak) / peak).min()
        max_dd  = round(float(dd) * 100, 2)

        sorted_pnl = sorted(closed, key=lambda t: t["pnl"])

        # Average entry EV of closed trades (diagnostic: is AI selecting good bets?)
        avg_ev    = round(np.mean([t.get("ev", 0) for t in closed]), 4)

        # Confidence score: high=3, medium=2, low=1
        conf_map  = {"high": 3, "medium": 2, "low": 1}
        avg_conf  = round(np.mean([conf_map.get(t.get("confidence","low"), 1) for t in closed]), 2)

        return {
            **base,
            "total_pnl":      total_pnl,
            "total_pnl_pct":  round(total_pnl / starting * 100, 2),
            "num_trades":     len(closed),
            "wins":           wins,
            "losses":         len(closed) - wins,
            "win_rate":       round(wins / len(closed) * 100, 1),
            "sharpe":         sharpe,
            "max_drawdown":   max_dd,
            "open_count":     len(self.open_positions()),
            "avg_ev":         avg_ev,
            "avg_conf_score": avg_conf,
            "best_trade":     sorted_pnl[-1],
            "worst_trade":    sorted_pnl[0],
        }

    def reset(self, new_balance: float = STARTING_BALANCE) -> None:
        with _lock:
            self._data = self._fresh()
            self._data["balance"] = new_balance
            self._data["starting_balance"] = new_balance
            self._data["balance_history"] = [new_balance]
            self._save()


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())

def age_str(iso: str) -> str:
    """Human-readable age of ISO timestamp."""
    try:
        dt   = datetime.fromisoformat(iso)
        now  = datetime.now(timezone.utc)
        secs = int((now - dt).total_seconds())
        if secs < 3600:   return f"{secs // 60}m"
        if secs < 86400:  return f"{secs // 3600}h"
        return f"{secs // 86400}d"
    except Exception:
        return "?"

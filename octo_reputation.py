"""
octo_reputation.py — Onchain reputation registry for Octodamus (#1)

Every Octodamus call is logged as a verifiable onchain record on Base.
Uses self-transfers with ABI-encoded calldata — no contract deployment needed.
The transaction hash is the immutable proof. Anyone can decode it to verify.

Architecture:
  - Wallet: reuses OCTOBOTO_WALLET_KEY (Base mainnet, same key as Polygon)
  - Chain: Base mainnet (chain_id=8453) — cheap gas, fast finality
  - Storage: calldata on a 0-ETH self-transfer
  - Encoding: JSON → hex calldata

Each call record contains:
  {asset, direction, signals, edge_score, win_threshold, timestamp, timeframe}

Outcomes are logged as a second tx referencing the original call hash:
  {call_tx, resolved, won, entry_price, exit_price, pnl_pct}

Local index: octo_reputation_log.json — maps call_id → tx_hash for fast lookup.
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

BASE_RPC      = "https://mainnet.base.org"
CHAIN_ID      = 8453
LOG_FILE      = Path(r"C:\Users\walli\octodamus\data\octo_reputation_log.json")
REPUTATION_PREFIX = b"OCTODAMUS:"    # Calldata prefix for easy identification


# ── Local log ─────────────────────────────────────────────────────────────────

def _load_log() -> dict:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    if LOG_FILE.exists():
        try:
            return json.loads(LOG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"calls": [], "outcomes": []}


def _save_log(data: dict):
    LOG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ── Web3 client ───────────────────────────────────────────────────────────────

def _get_web3():
    from web3 import Web3
    w3 = Web3(Web3.HTTPProvider(BASE_RPC))
    return w3


def _get_account():
    from eth_account import Account
    key = os.environ.get("OCTOBOTO_WALLET_KEY", "")
    if not key:
        raise RuntimeError("OCTOBOTO_WALLET_KEY not in environment")
    return Account.from_key(key)


def _get_base_balance() -> float:
    """ETH balance on Base for gas check."""
    try:
        w3 = _get_web3()
        acct = _get_account()
        bal = w3.eth.get_balance(acct.address)
        return float(w3.from_wei(bal, "ether"))
    except Exception:
        return 0.0


# ── Onchain write ─────────────────────────────────────────────────────────────

def _submit_onchain(payload: dict) -> Optional[str]:
    """
    Submit payload as calldata in a 0-ETH self-transfer on Base.
    Returns tx hash on success, None on failure.
    """
    try:
        w3 = _get_web3()
        acct = _get_account()

        # Check gas balance
        bal = w3.eth.get_balance(acct.address)
        if bal < w3.to_wei(0.0001, "ether"):
            log.warning("[Reputation] Low ETH on Base — skipping onchain log. Fund with ~$1 ETH.")
            return None

        data = REPUTATION_PREFIX + json.dumps(payload, separators=(",", ":")).encode()
        nonce = w3.eth.get_transaction_count(acct.address)
        gas_price = w3.eth.gas_price

        tx = {
            "to":       acct.address,          # Self-transfer
            "value":    0,
            "data":     data,
            "gas":      50000,
            "gasPrice": gas_price,
            "nonce":    nonce,
            "chainId":  CHAIN_ID,
        }

        signed = acct.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        tx_hex = tx_hash.hex()
        log.info(f"[Reputation] Onchain: {tx_hex}")
        return tx_hex

    except Exception as e:
        log.error(f"[Reputation] Onchain submit failed: {e}")
        return None


# ── Public API ────────────────────────────────────────────────────────────────

def log_call(
    asset: str,
    direction: str,
    signals: int,
    total_signals: int,
    edge_score: float,
    win_threshold_pct: float,
    timeframe: str = "24h",
    note: str = "",
) -> str:
    """
    Log an Octodamus directional call onchain.
    Returns a call_id (timestamp-based) for later outcome linking.
    """
    call_id = f"{asset}_{direction}_{int(time.time())}"
    ts = datetime.now(timezone.utc).isoformat()

    payload = {
        "type":       "call",
        "call_id":    call_id,
        "asset":      asset,
        "direction":  direction,
        "signals":    f"{signals}/{total_signals}",
        "edge_score": round(edge_score, 3),
        "win_thresh": win_threshold_pct,
        "timeframe":  timeframe,
        "ts":         ts,
        "note":       note[:100] if note else "",
    }

    tx_hash = _submit_onchain(payload)

    # Always save to local log (even if onchain fails)
    data = _load_log()
    data["calls"].append({**payload, "tx_hash": tx_hash or "offline"})
    _save_log(data)

    if tx_hash:
        log.info(f"[Reputation] Call logged | {asset} {direction} | tx: {tx_hash[:16]}...")
    else:
        log.info(f"[Reputation] Call saved locally (no gas) | {call_id}")

    return call_id


def log_outcome(
    call_id: str,
    won: bool,
    entry_price: float,
    exit_price: float,
    pnl_pct: float,
) -> Optional[str]:
    """Log resolution outcome for a previous call."""
    # Find the original call
    data = _load_log()
    original = next((c for c in data["calls"] if c.get("call_id") == call_id), None)

    payload = {
        "type":        "outcome",
        "call_id":     call_id,
        "call_tx":     original.get("tx_hash", "") if original else "",
        "won":         won,
        "entry_price": round(entry_price, 4),
        "exit_price":  round(exit_price, 4),
        "pnl_pct":     round(pnl_pct, 4),
        "ts":          datetime.now(timezone.utc).isoformat(),
    }

    tx_hash = _submit_onchain(payload)

    data["outcomes"].append({**payload, "tx_hash": tx_hash or "offline"})
    _save_log(data)
    return tx_hash


# ── Stats ─────────────────────────────────────────────────────────────────────

def reputation_stats() -> dict:
    """Return win rate and verified call count from local log."""
    data = _load_log()
    outcomes = data.get("outcomes", [])
    if not outcomes:
        return {"calls": len(data.get("calls", [])), "resolved": 0, "win_rate": None}
    wins = sum(1 for o in outcomes if o.get("won"))
    onchain = sum(1 for o in outcomes if o.get("tx_hash", "offline") != "offline")
    return {
        "calls":        len(data.get("calls", [])),
        "resolved":     len(outcomes),
        "wins":         wins,
        "win_rate":     round(wins / len(outcomes), 3),
        "onchain":      onchain,
    }


def reputation_str() -> str:
    """One-line reputation summary for Telegram/posts."""
    stats = reputation_stats()
    if stats["resolved"] == 0:
        return f"🏆 Reputation: {stats['calls']} call(s) logged | awaiting resolution"
    wr = stats["win_rate"]
    verified = stats["onchain"]
    bal = _get_base_balance()
    gas_note = "" if bal >= 0.0001 else " ⚠️ add ETH on Base for onchain logging"
    return (
        f"🏆 Reputation: {stats['wins']}/{stats['resolved']} ({wr:.0%} win rate) | "
        f"{verified} verified onchain{gas_note}"
    )


def verify_url(tx_hash: str) -> str:
    return f"https://basescan.org/tx/{tx_hash}"

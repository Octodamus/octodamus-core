"""
botcoin_dashboard_server.py — BOTCOIN Mining Dashboard (HTML)

Local web server. Bookmark: http://localhost:8901

Run:  python botcoin_dashboard_server.py
      python botcoin_dashboard_server.py --port 8901

Serves an HTML dashboard that auto-refreshes every 60s.
All API calls are server-side (no CORS issues).
"""

import argparse
import json
import sys
import time
import threading
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))

# ── Config ────────────────────────────────────────────────────────────────────

COORDINATOR  = "https://coordinator.agentmoney.net"
BLOCKSCOUT   = "https://base.blockscout.com/api/v2"
BASE_RPC     = "https://mainnet.base.org"
BOTCOIN_ADDR = "0xA601877977340862Ca67f816eb079958E5bd0BA3"
V3_CONTRACT  = "0xB2fbe0DB5A99B4E2Dd294dE64cEd82740b53A2Ea"
WALLET       = "0x7d372b930b42d4adc7c82f9d5bcb692da3597570"
DECIMALS     = 18

CREDITS_LOG  = Path(r"C:\Users\walli\octodamus\data\botcoin_credits.json")
AUTH_CACHE   = Path(r"C:\Users\walli\octodamus\data\botcoin_auth.json")

# Simple in-memory cache — avoid hammering rate-limited APIs
_cache = {"data": None, "ts": 0}
_CACHE_TTL = 55  # seconds


# ── Secrets ───────────────────────────────────────────────────────────────────

def _bankr_key() -> str:
    import os
    k = os.environ.get("BANKR_API_KEY", "")
    if not k:
        for p in [Path(r"C:\Users\walli\octodamus\.octo_secrets"),
                  Path(__file__).parent / ".octo_secrets"]:
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                k = d.get("secrets", d).get("BANKR_API_KEY", "")
                if k: break
            except Exception:
                pass
    return k


# ── On-chain ──────────────────────────────────────────────────────────────────

def _eth_call(to: str, data: str) -> str:
    try:
        r = requests.post(BASE_RPC, json={
            "jsonrpc": "2.0", "id": 1, "method": "eth_call",
            "params": [{"to": to, "data": data}, "latest"],
        }, timeout=8)
        return r.json().get("result") or "0x0"
    except Exception:
        return "0x0"


def _pad(addr: str) -> str:
    return addr.lower().replace("0x", "").zfill(64)


def get_erc20_balance(token: str, wallet: str) -> float:
    result = _eth_call(token, "0x70a08231" + _pad(wallet))
    return int(result, 16) / (10 ** DECIMALS) if result and result != "0x" else 0.0


def get_staked(wallet: str) -> float:
    result = _eth_call(V3_CONTRACT, "0xf9931855" + _pad(wallet))
    return int(result, 16) / (10 ** DECIMALS) if result and result != "0x" else 0.0


def get_eth_balance(wallet: str) -> float:
    try:
        r = requests.post(BASE_RPC, json={
            "jsonrpc": "2.0", "id": 1, "method": "eth_getBalance",
            "params": [wallet, "latest"],
        }, timeout=8)
        result = r.json().get("result", "0x0")
        return int(result, 16) / 1e18
    except Exception:
        return 0.0


def get_withdrawable_ts(wallet: str) -> int:
    result = _eth_call(V3_CONTRACT, "0x5a8c06ab" + _pad(wallet))
    return int(result, 16) if result and result != "0x" else 0


# ── Coordinator ───────────────────────────────────────────────────────────────

def get_epoch() -> dict:
    try:
        r = requests.get(f"{COORDINATOR}/v1/epoch", timeout=8)
        if r.ok:
            d = r.json()
            return {
                "id":       str(d.get("epochId", "?")),
                "end_ts":   int(d.get("nextEpochStartTimestamp", 0)),
                "duration": int(d.get("epochDurationSeconds", 86400)),
                "pool":     int(d.get("rewardPool", 0)),
                "total_credits": int(d.get("totalCredits", 0)),
            }
    except Exception:
        pass
    return {"id": "?", "end_ts": 0, "duration": 86400, "pool": 0, "total_credits": 0}


def get_credits(wallet: str) -> dict:
    """Returns credits dict or cached from log if rate-limited."""
    try:
        headers = {}
        if AUTH_CACHE.exists():
            cache = json.loads(AUTH_CACHE.read_text(encoding="utf-8"))
            if cache.get("token") and time.time() - cache.get("ts", 0) < 82800:
                headers["Authorization"] = f"Bearer {cache['token']}"
        r = requests.get(
            f"{COORDINATOR}/v1/credits",
            params={"miner": wallet},
            headers=headers,
            timeout=8,
        )
        if r.ok:
            d = r.json()
            return {
                "credits": int(d.get("credits", d.get("totalCredits", 0))),
                "solves":  int(d.get("solves", d.get("totalSolves", 0))),
                "passes":  int(d.get("passes", 0)),
                "live":    True,
                "retry_secs": 0,
            }
        elif r.status_code == 429:
            d = r.json()
            return {"credits": 0, "solves": 0, "passes": 0, "live": False,
                    "retry_secs": d.get("retryAfterSeconds", 3600)}
    except Exception:
        pass
    return {"credits": 0, "solves": 0, "passes": 0, "live": False, "retry_secs": 0}


# ── Credits history ───────────────────────────────────────────────────────────

def load_history() -> list[dict]:
    try:
        if CREDITS_LOG.exists():
            raw = json.loads(CREDITS_LOG.read_text(encoding="utf-8"))
            rows = []
            for ep, rec in sorted(raw.items(), key=lambda x: int(x[0])):
                rows.append({
                    "epoch":     ep,
                    "solves":    rec.get("solves", 0),
                    "passes":    rec.get("passes", 0),
                    "credits":   rec.get("credits", 0),
                    "tokens_in":  rec.get("tokens_in", 0),
                    "tokens_out": rec.get("tokens_out", 0),
                    "version":   "V3" if rec.get("credits", 0) > 100 else "V2",
                })
            return rows
    except Exception:
        pass
    return []


# ── DexScreener: BOTCOIN USD price ───────────────────────────────────────────

DEXSCREENER = "https://api.dexscreener.com/latest/dex/tokens"

def get_botcoin_price() -> dict:
    """Fetch BOTCOIN/USD price from DexScreener. Returns price, fdv, liquidity."""
    try:
        r = requests.get(f"{DEXSCREENER}/{BOTCOIN_ADDR}", timeout=8)
        if not r.ok:
            return {"price_usd": 0.0, "fdv": 0, "liquidity_usd": 0, "volume_24h": 0}
        pairs = r.json().get("pairs", [])
        if not pairs:
            return {"price_usd": 0.0, "fdv": 0, "liquidity_usd": 0, "volume_24h": 0}
        # Use highest-liquidity pair
        best = max(pairs, key=lambda p: float(p.get("liquidity", {}).get("usd", 0) or 0))
        return {
            "price_usd":     float(best.get("priceUsd", 0) or 0),
            "fdv":           int(best.get("fdv", 0) or 0),
            "liquidity_usd": float(best.get("liquidity", {}).get("usd", 0) or 0),
            "volume_24h":    float(best.get("volume", {}).get("h24", 0) or 0),
            "dex":           best.get("dexId", ""),
            "pair_url":      best.get("url", ""),
        }
    except Exception:
        return {"price_usd": 0.0, "fdv": 0, "liquidity_usd": 0, "volume_24h": 0}


def calc_token_cost(tokens_in: int, tokens_out: int, model: str = "sonnet") -> float:
    """Estimate Claude API cost in USD.
    Sonnet 4.6: $3/MTok in, $15/MTok out.
    Haiku 4.5:  $0.80/MTok in, $4/MTok out.
    """
    if "haiku" in model.lower():
        return tokens_in * 0.80 / 1_000_000 + tokens_out * 4.0 / 1_000_000
    return tokens_in * 3.0 / 1_000_000 + tokens_out * 15.0 / 1_000_000


# Estimated tokens per solve when real data unavailable
# Sonnet 4.6 + 2k thinking budget: ~3000 in (system+template+doc), ~2500 out (thinking ~2000 + answer ~500)
EST_TOKENS_IN_PER_SOLVE  = 3000
EST_TOKENS_OUT_PER_SOLVE = 2500

def est_cost_from_solves(solves: int) -> tuple[float, int]:
    """Returns (estimated_usd, estimated_total_tokens) from solve count."""
    t_in  = solves * EST_TOKENS_IN_PER_SOLVE
    t_out = solves * EST_TOKENS_OUT_PER_SOLVE
    return calc_token_cost(t_in, t_out), t_in + t_out


# ── Blockscout: recent BOTCOIN transfers ──────────────────────────────────────

def get_recent_transfers(wallet: str) -> list[dict]:
    results = []
    wallet_lower = wallet.lower()
    try:
        for direction in ("to", "from"):
            r = requests.get(
                f"{BLOCKSCOUT}/addresses/{wallet}/token-transfers",
                params={"type": "ERC-20", "filter": direction},
                timeout=10,
            )
            if not r.ok:
                continue
            for t in r.json().get("items", [])[:15]:
                tok = t.get("token", {})
                tok_addr = tok.get("address", "").lower()
                tok_sym  = tok.get("symbol", "")
                # Blockscout sometimes returns empty address — fall back to symbol match
                if tok_addr and tok_addr != BOTCOIN_ADDR.lower():
                    continue
                if not tok_addr and tok_sym != "BOTCOIN":
                    continue
                amount = int(t.get("total", {}).get("value", "0")) / (10 ** DECIMALS)
                to_addr = t.get("to", {}).get("hash", "").lower()
                results.append({
                    "tx":     t.get("transaction_hash", "")[:16] + "...",
                    "tx_full": t.get("transaction_hash", ""),
                    "label":  "CLAIM" if to_addr == wallet_lower else "STAKE",
                    "amount": amount,
                    "ts":     t.get("timestamp", ""),
                })
    except Exception:
        pass
    seen = set()
    deduped = []
    for t in results:
        if t["tx_full"] not in seen:
            seen.add(t["tx_full"])
            deduped.append(t)
    return sorted(deduped, key=lambda x: x["ts"], reverse=True)[:10]


# ── Aggregate dashboard data ──────────────────────────────────────────────────

def fetch_dashboard_data() -> dict:
    now = time.time()
    if _cache["data"] and now - _cache["ts"] < _CACHE_TTL:
        return _cache["data"]

    epoch    = get_epoch()
    credits  = get_credits(WALLET)
    history  = load_history()
    transfers = get_recent_transfers(WALLET)
    price_data = get_botcoin_price()

    bc_wallet = get_erc20_balance(BOTCOIN_ADDR, WALLET)
    staked    = get_staked(WALLET)
    eth_bal   = get_eth_balance(WALLET)
    wa_ts     = get_withdrawable_ts(WALLET)

    # Pull credits from history for current epoch if rate-limited
    new_epoch = True  # assume new until we find history for it
    if not credits["live"] and credits["credits"] == 0:
        ep_id = epoch["id"]
        for row in history:
            if str(row["epoch"]) == str(ep_id):
                credits["credits"] = row["credits"]
                credits["solves"]  = row["solves"]
                new_epoch = False
                break
    else:
        new_epoch = False

    # Epoch timing
    end_ts   = epoch["end_ts"]
    duration = epoch["duration"]
    secs_left = max(0, end_ts - now) if end_ts else 0
    pct_done  = max(0, min(100, round((1 - secs_left / duration) * 100))) if duration else 0

    # Reward estimate
    my_credits     = credits["credits"]
    total_credits  = epoch["total_credits"]
    pool_raw       = epoch["pool"]
    pool_tokens    = pool_raw / (10 ** DECIMALS) if pool_raw > 10**12 else pool_raw
    est_reward     = 0.0
    share_pct      = 0.0
    if my_credits and total_credits:
        share_pct  = my_credits / total_credits * 100
        est_reward = (my_credits / total_credits) * pool_tokens if pool_tokens else 0.0

    # History totals
    total_solves  = sum(r["solves"] for r in history)
    total_credits_hist = sum(r["credits"] for r in history)
    total_tokens_in  = sum(r.get("tokens_in", 0)  for r in history)
    total_tokens_out = sum(r.get("tokens_out", 0) for r in history)
    # cost/tokens computed after row augmentation below

    # Current epoch cost from local credits log
    ep_id = epoch["id"]
    epoch_cost_usd = 0.0
    epoch_tokens_in  = 0
    epoch_tokens_out = 0
    for row in history:
        if str(row["epoch"]) == str(ep_id):
            epoch_tokens_in  = row.get("tokens_in", 0)
            epoch_tokens_out = row.get("tokens_out", 0)
            epoch_cost_usd   = calc_token_cost(epoch_tokens_in, epoch_tokens_out)
            break

    epoch_cost_estimated = False
    if epoch_tokens_in == 0 and credits["solves"] > 0:
        epoch_cost_usd, epoch_tok_total = est_cost_from_solves(credits["solves"])
        epoch_cost_estimated = True
    else:
        epoch_tok_total = epoch_tokens_in + epoch_tokens_out

    # Augment history rows with per-row cost (estimated when tokens_in == 0)
    for row in history:
        t_in  = row.get("tokens_in",  0)
        t_out = row.get("tokens_out", 0)
        if t_in > 0:
            row["cost_usd"]       = round(calc_token_cost(t_in, t_out), 4)
            row["cost_tokens"]    = t_in + t_out
            row["cost_estimated"] = False
        else:
            rc, rt = est_cost_from_solves(row["solves"])
            row["cost_usd"]       = round(rc, 4)
            row["cost_tokens"]    = rt
            row["cost_estimated"] = True

    total_cost_usd       = sum(r["cost_usd"]    for r in history)
    total_cost_tokens    = sum(r["cost_tokens"]  for r in history)
    total_cost_estimated = any(r["cost_estimated"] for r in history)

    # USD value of holdings
    price_usd = price_data.get("price_usd", 0.0)
    total_botcoin = bc_wallet + staked
    total_value_usd = total_botcoin * price_usd

    data = {
        "fetched_at":   datetime.now(timezone.utc).isoformat(),
        "wallet":       WALLET,
        "balances": {
            "botcoin_wallet": bc_wallet,
            "botcoin_staked": staked,
            "botcoin_total":  bc_wallet + staked,
            "eth":            eth_bal,
        },
        "unstake": {
            "ts":    wa_ts,
            "ready": wa_ts > 0 and wa_ts <= now,
        },
        "epoch": {
            "id":          epoch["id"],
            "end_ts":      end_ts,
            "secs_left":   int(secs_left),
            "pct_done":    pct_done,
            "pool_tokens": pool_tokens,
            "total_credits": total_credits,
        },
        "credits": {
            **credits,
            "share_pct":    share_pct,
            "est_reward":   est_reward,
            "epoch_cost_usd":       round(epoch_cost_usd, 4),
            "epoch_tokens":         epoch_tok_total,
            "epoch_cost_estimated": epoch_cost_estimated,
            "new_epoch":            new_epoch,
        },
        "history":   history,
        "history_totals": {
            "solves":          total_solves,
            "credits":         total_credits_hist,
            "tokens_in":       total_tokens_in,
            "tokens_out":      total_tokens_out,
            "cost_usd":        round(total_cost_usd, 4),
            "cost_tokens":     total_cost_tokens,
            "cost_estimated":  total_cost_estimated,
        },
        "price": {
            **price_data,
            "total_value_usd": round(total_value_usd, 2),
        },
        "transfers": transfers,
        "mining_active": staked > 0,
    }

    _cache["data"] = data
    _cache["ts"]   = now
    return data


# ── HTML ──────────────────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BOTCOIN Mining Dashboard</title>
<style>
  :root {
    --bg:      #080b14;
    --surface: #0e1422;
    --border:  #1e2d45;
    --accent:  #3b82f6;
    --accent2: #22d3ee;
    --green:   #34d399;
    --red:     #f87171;
    --yellow:  #fbbf24;
    --text:    #e8f4ff;
    --muted:   #7aadcc;
    --card-bg: #0c1220;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace;
    font-size: 13.5px;
    line-height: 1.6;
    min-height: 100vh;
  }

  /* ── Header ── */
  .header {
    background: linear-gradient(135deg, #0f0f1a 0%, #1a0a2e 100%);
    border-bottom: 1px solid var(--border);
    padding: 20px 24px;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .header-left { display: flex; align-items: center; gap: 14px; }
  .logo { font-size: 26px; }
  .title-block h1 { font-size: 18px; font-weight: 700; color: #fff; letter-spacing: 1px; }
  .title-block p  { font-size: 11px; color: var(--muted); margin-top: 2px; }
  .header-right { text-align: right; }
  .wallet-addr {
    font-size: 11px; color: var(--muted);
    background: var(--surface);
    padding: 4px 10px; border-radius: 6px;
    border: 1px solid var(--border);
  }
  .refresh-info { font-size: 11px; color: var(--muted); margin-top: 4px; }
  #countdown { color: var(--accent2); font-weight: 600; }

  /* ── Layout ── */
  .main { padding: 20px 24px; max-width: 1100px; margin: 0 auto; }
  .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px; }
  .grid-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; margin-bottom: 16px; }
  .grid-4 { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 16px; }
  @media (max-width: 768px) {
    .grid-2, .grid-3, .grid-4 { grid-template-columns: 1fr; }
  }

  /* ── Cards ── */
  .card {
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px 18px;
  }
  .card-title {
    font-size: 10px;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 1.5px;
    margin-bottom: 8px;
  }
  .card-value {
    font-size: 22px;
    font-weight: 700;
    color: #fff;
    line-height: 1.2;
  }
  .card-sub {
    font-size: 11px;
    color: var(--muted);
    margin-top: 4px;
  }
  .card-accent { border-left: 3px solid var(--accent); }
  .card-green  { border-left: 3px solid var(--green); }
  .card-cyan   { border-left: 3px solid var(--accent2); }
  .card-yellow { border-left: 3px solid var(--yellow); }

  /* ── Status badge ── */
  .badge {
    display: inline-block;
    font-size: 10px;
    font-weight: 700;
    padding: 2px 8px;
    border-radius: 4px;
    letter-spacing: 0.5px;
    text-transform: uppercase;
  }
  .badge-green  { background: rgba(16,185,129,.15); color: var(--green); border: 1px solid rgba(16,185,129,.3); }
  .badge-red    { background: rgba(239,68,68,.15);  color: var(--red);   border: 1px solid rgba(239,68,68,.3); }
  .badge-yellow { background: rgba(245,158,11,.15); color: var(--yellow);border: 1px solid rgba(245,158,11,.3); }
  .badge-purple { background: rgba(124,58,237,.15); color: #a78bfa;      border: 1px solid rgba(124,58,237,.3); }

  /* ── Section headers ── */
  .section-title {
    font-size: 11px;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 2px;
    margin: 20px 0 10px;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .section-title::after {
    content: '';
    flex: 1;
    height: 1px;
    background: var(--border);
  }

  /* ── Progress bar ── */
  .progress-wrap { margin-top: 10px; }
  .progress-label {
    display: flex;
    justify-content: space-between;
    font-size: 11px;
    color: var(--muted);
    margin-bottom: 5px;
  }
  .progress-bar {
    height: 6px;
    background: var(--border);
    border-radius: 3px;
    overflow: hidden;
  }
  .progress-fill {
    height: 100%;
    border-radius: 3px;
    transition: width 1s ease;
  }
  .progress-purple { background: linear-gradient(90deg, var(--accent), #a855f7); }
  .progress-cyan   { background: linear-gradient(90deg, var(--accent2), #38bdf8); }
  .progress-green  { background: linear-gradient(90deg, var(--green), #34d399); }

  /* ── Epoch timer ── */
  .epoch-timer {
    display: flex;
    gap: 12px;
    margin-top: 12px;
  }
  .time-unit {
    text-align: center;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 8px 14px;
    min-width: 60px;
  }
  .time-val { font-size: 22px; font-weight: 700; color: var(--accent2); }
  .time-lbl { font-size: 9px; color: var(--muted); text-transform: uppercase; letter-spacing: 1px; }

  /* ── Table ── */
  .data-table { width: 100%; border-collapse: collapse; }
  .data-table th {
    text-align: left;
    font-size: 10px;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 1px;
    padding: 8px 12px;
    border-bottom: 1px solid var(--border);
  }
  .data-table td {
    padding: 9px 12px;
    border-bottom: 1px solid rgba(30,30,46,.6);
    font-size: 12px;
  }
  .data-table tr:last-child td { border-bottom: none; }
  .data-table tr:hover td { background: rgba(124,58,237,.04); }
  .data-table .total-row td {
    border-top: 1px solid var(--border);
    color: var(--accent2);
    font-weight: 700;
  }
  .num-right { text-align: right; font-variant-numeric: tabular-nums; }
  .green { color: var(--green); }
  .yellow { color: var(--yellow); }
  .muted  { color: var(--muted); }

  /* ── Mining status bar ── */
  .status-bar {
    display: flex;
    align-items: center;
    gap: 10px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 10px 16px;
    margin-bottom: 16px;
  }
  .pulse {
    width: 8px; height: 8px; border-radius: 50%;
    background: var(--green);
    box-shadow: 0 0 0 0 rgba(16,185,129,.4);
    animation: pulse 2s infinite;
  }
  .pulse-off { background: var(--red); animation: none; }
  @keyframes pulse {
    0%   { box-shadow: 0 0 0 0 rgba(16,185,129,.4); }
    70%  { box-shadow: 0 0 0 8px rgba(16,185,129,0); }
    100% { box-shadow: 0 0 0 0 rgba(16,185,129,0); }
  }
  .status-text { font-size: 12px; color: var(--text); }

  /* ── Loading / error ── */
  .loading {
    text-align: center;
    padding: 60px;
    color: var(--muted);
    font-size: 13px;
  }
  .error-box {
    background: rgba(239,68,68,.08);
    border: 1px solid rgba(239,68,68,.3);
    border-radius: 8px;
    padding: 12px 16px;
    color: var(--red);
    font-size: 12px;
    margin-bottom: 16px;
  }

  /* ── Footer ── */
  .footer {
    text-align: center;
    padding: 20px;
    color: var(--muted);
    font-size: 11px;
    border-top: 1px solid var(--border);
    margin-top: 24px;
  }
  .footer a { color: var(--accent2); text-decoration: none; }
</style>
</head>
<body>

<div class="header">
  <div class="header-left">
    <span class="logo">⬡</span>
    <div class="title-block">
      <h1>BOTCOIN MINING</h1>
      <p>Octodamus V3 — Base Chain</p>
    </div>
  </div>
  <div class="header-right">
    <div class="wallet-addr" id="walletAddr">loading...</div>
    <div class="refresh-info">Next refresh in <span id="countdown">60</span>s</div>
  </div>
</div>

<div class="main" id="mainContent">
  <div class="loading">Fetching mining data...</div>
</div>

<div class="footer">
  BOTCOIN Proof-of-Inference Mining &bull;
  <a href="https://agentmoney.net" target="_blank">agentmoney.net</a> &bull;
  <a href="https://base.blockscout.com/address/0x7d372b930b42d4adc7c82f9d5bcb692da3597570" target="_blank">Blockscout</a>
</div>

<script>
let refreshTimer = 60;
let refreshInterval = null;
let countdownInterval = null;

function fmt(n) {
  if (n >= 1e6) return (n/1e6).toFixed(2) + 'M';
  if (n >= 1e3) return (n/1e3).toFixed(1) + 'K';
  return Math.round(n).toLocaleString();
}
function fmtFull(n) {
  return Math.round(n).toLocaleString();
}
function fmtDate(ts_str) {
  if (!ts_str) return '—';
  try {
    const d = new Date(ts_str);
    return d.toLocaleString('en-US', {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',timeZoneName:'short'});
  } catch(e) { return ts_str.slice(0,16); }
}
function timeParts(secs) {
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = Math.floor(secs % 60);
  return {h, m, s};
}

function render(d) {
  const b = d.balances;
  const ep = d.epoch;
  const cr = d.credits;
  const mining = d.mining_active;
  const fetched = new Date(d.fetched_at).toLocaleTimeString();

  document.getElementById('walletAddr').textContent =
    d.wallet.slice(0,8) + '...' + d.wallet.slice(-6);

  // Live countdown of seconds left in epoch
  let epSecsLeft = ep.secs_left;
  // We'll update this live
  if (window._epochCountdown) clearInterval(window._epochCountdown);
  window._epochSecsLeft = epSecsLeft;
  window._epochCountdown = setInterval(() => {
    if (window._epochSecsLeft > 0) window._epochSecsLeft--;
    const {h,m,s} = timeParts(window._epochSecsLeft);
    const el = document.getElementById('epH');
    if (el) {
      document.getElementById('epH').textContent = String(h).padStart(2,'0');
      document.getElementById('epM').textContent = String(m).padStart(2,'0');
      document.getElementById('epS').textContent = String(s).padStart(2,'0');
    }
  }, 1000);

  const {h,m,s} = timeParts(epSecsLeft);

  // Mining status
  const statusDot   = mining ? 'pulse' : 'pulse pulse-off';
  const statusText  = mining
    ? `Mining active — ${fmt(b.botcoin_staked)} BOTCOIN staked in V3`
    : 'NOT STAKED — run: python octo_boto_botcoin.py --stake';
  const statusBadge = mining
    ? '<span class="badge badge-green">LIVE</span>'
    : '<span class="badge badge-red">OFFLINE</span>';

  // Credits rate-limit note
  let creditsNote = '';
  if (!cr.live) {
    const retryH = Math.floor(cr.retry_secs / 3600);
    const retryM = Math.floor((cr.retry_secs % 3600) / 60);
    creditsNote = `<div class="card-sub" style="color:var(--yellow)">Rate-limited — cached data${cr.retry_secs ? ` (retry in ${retryH}h ${retryM}m)` : ''}</div>`;
  }

  // Share bar width
  const shareW = Math.min(100, cr.share_pct * 20); // scaled for visibility

  // History rows
  const histRows = (d.history || []).map(row => {
    const est = row.cost_estimated;
    const tokStr  = row.cost_tokens > 0 ? (est ? '~' : '') + fmtFull(row.cost_tokens) : '—';
    const costStr = row.cost_usd    > 0 ? (est ? '~$' : '$') + row.cost_usd.toFixed(3) : '—';
    return `<tr>
      <td>#${row.epoch}</td>
      <td><span class="badge ${row.version === 'V3' ? 'badge-purple' : 'badge-yellow'}">${row.version}</span></td>
      <td class="num-right">${fmtFull(row.solves)}</td>
      <td class="num-right">${row.passes ? fmtFull(row.passes) : '—'}</td>
      <td class="num-right green">${fmtFull(row.credits)}</td>
      <td class="num-right muted">${row.solves ? Math.round(row.credits/row.solves) : '—'}</td>
      <td class="num-right muted">${tokStr}</td>
      <td class="num-right" style="color:var(--yellow)">${costStr}</td>
    </tr>`;
  }).join('');

  const totals = d.history_totals || {};
  const totEstPfx = totals.cost_estimated ? '~' : '';
  const totalTokens = totals.cost_tokens || 0;
  const histTotalRow = `
    <tr class="total-row">
      <td colspan="2" style="font-size:11px;letter-spacing:1px">ALL EPOCHS</td>
      <td class="num-right">${fmtFull(totals.solves || 0)}</td>
      <td class="num-right">—</td>
      <td class="num-right" style="color:var(--green);font-size:14px">${fmtFull(totals.credits || 0)}</td>
      <td class="num-right">—</td>
      <td class="num-right">${totalTokens > 0 ? totEstPfx + fmtFull(totalTokens) : '—'}</td>
      <td class="num-right" style="color:var(--red);font-size:14px">${totals.cost_usd > 0 ? totEstPfx + '$'+totals.cost_usd.toFixed(3) : '—'}</td>
    </tr>
  `;

  // Transfer rows
  const xferRows = (d.transfers || []).length > 0
    ? (d.transfers || []).map(t => `
        <tr>
          <td>${fmtDate(t.ts)}</td>
          <td><span class="badge ${t.label === 'CLAIM' ? 'badge-green' : 'badge-yellow'}">${t.label}</span></td>
          <td class="num-right green">+${fmt(t.amount)} BOTCOIN</td>
          <td class="muted"><a href="https://base.blockscout.com/tx/${t.tx_full}" target="_blank" style="color:var(--accent2)">${t.tx}</a></td>
        </tr>
      `).join('')
    : '<tr><td colspan="4" style="color:var(--muted);text-align:center;padding:20px">No transfer history found on Blockscout</td></tr>';

  // Epoch pool display
  const poolDisplay = ep.pool_tokens > 0 ? `${fmt(ep.pool_tokens)} BOTCOIN` : '—';

  document.getElementById('mainContent').innerHTML = `
    <!-- Status bar -->
    <div class="status-bar">
      <div class="${statusDot}"></div>
      <div class="status-text">${statusText}</div>
      <div style="margin-left:auto">${statusBadge}</div>
    </div>

    <!-- Balances -->
    <div class="section-title">Wallet Balances</div>
    <div class="grid-4" style="grid-template-columns:repeat(5,1fr)">
      <div class="card card-accent">
        <div class="card-title">BOTCOIN Staked</div>
        <div class="card-value">${fmt(b.botcoin_staked)}</div>
        <div class="card-sub">V3 Mining Contract</div>
      </div>
      <div class="card card-green">
        <div class="card-title">BOTCOIN Wallet</div>
        <div class="card-value">${fmt(b.botcoin_wallet)}</div>
        <div class="card-sub">Available / Rewards</div>
      </div>
      <div class="card card-cyan">
        <div class="card-title">BOTCOIN Total</div>
        <div class="card-value">${fmt(b.botcoin_total)}</div>
        <div class="card-sub">Staked + Wallet</div>
      </div>
      <div class="card card-yellow">
        <div class="card-title">USD Value</div>
        <div class="card-value" style="font-size:18px">${d.price && d.price.total_value_usd > 0 ? '$'+d.price.total_value_usd.toFixed(2) : '—'}</div>
        <div class="card-sub">${d.price && d.price.price_usd > 0 ? '$'+d.price.price_usd.toFixed(8)+' / BOTCOIN' : 'Price unavailable'}</div>
      </div>
      <div class="card">
        <div class="card-title">ETH Balance</div>
        <div class="card-value" style="font-size:18px">${b.eth.toFixed(5)}</div>
        <div class="card-sub">Gas on Base</div>
      </div>
    </div>

    <!-- Epoch -->
    <div class="section-title">Current Epoch</div>
    <div class="grid-2">
      <div class="card card-cyan">
        <div class="card-title">Epoch #${ep.id} — Time Remaining</div>
        <div class="epoch-timer">
          <div class="time-unit"><div class="time-val" id="epH">${String(h).padStart(2,'0')}</div><div class="time-lbl">hours</div></div>
          <div class="time-unit"><div class="time-val" id="epM">${String(m).padStart(2,'0')}</div><div class="time-lbl">min</div></div>
          <div class="time-unit"><div class="time-val" id="epS">${String(s).padStart(2,'0')}</div><div class="time-lbl">sec</div></div>
        </div>
        <div class="progress-wrap">
          <div class="progress-label">
            <span>Epoch progress</span>
            <span>${ep.pct_done}%</span>
          </div>
          <div class="progress-bar">
            <div class="progress-fill progress-cyan" style="width:${ep.pct_done}%"></div>
          </div>
        </div>
      </div>

      <div class="card card-accent">
        <div class="card-title">Your Credits — Epoch #${ep.id}</div>
        ${cr.new_epoch ? '<div class="error-box" style="background:rgba(59,130,246,.08);border-color:rgba(59,130,246,.3);color:var(--accent);margin-bottom:8px">NEW EPOCH — mining in progress, first credits pending</div>' : ''}
        <div style="display:flex;align-items:baseline;gap:10px;margin-bottom:8px">
          <div class="card-value">${cr.new_epoch ? '—' : fmtFull(cr.credits)}</div>
          <div style="color:var(--muted);font-size:12px">credits</div>
          ${cr.live ? '<span class="badge badge-green" style="margin-left:4px">LIVE</span>' : '<span class="badge badge-yellow" style="margin-left:4px">CACHED</span>'}
        </div>
        ${creditsNote}
        <div style="display:flex;gap:16px;margin:8px 0;flex-wrap:wrap">
          <div><div style="font-size:19px;font-weight:700;color:var(--accent2)">${cr.new_epoch ? '—' : fmtFull(cr.solves)}</div><div class="card-sub">Solves</div></div>
          <div><div style="font-size:19px;font-weight:700;color:var(--green)">${cr.new_epoch || !cr.solves ? '—' : Math.round(cr.credits/cr.solves)}</div><div class="card-sub">Credits/Solve</div></div>
          <div><div style="font-size:19px;font-weight:700;color:var(--yellow)">${poolDisplay}</div><div class="card-sub">Epoch Pool</div></div>
          <div><div style="font-size:19px;font-weight:700;color:var(--red)">${cr.new_epoch ? '—' : cr.epoch_cost_usd > 0 ? (cr.epoch_cost_estimated ? '~$' : '$')+cr.epoch_cost_usd.toFixed(3) : '—'}</div><div class="card-sub">Mining Cost${cr.epoch_cost_estimated ? ' (est)' : ''}</div></div>
          <div><div style="font-size:16px;font-weight:700;color:var(--muted)">${cr.new_epoch ? '—' : cr.epoch_tokens > 0 ? (cr.epoch_cost_estimated ? '~' : '')+fmtFull(cr.epoch_tokens)+' tok' : '—'}</div><div class="card-sub">Tokens Used</div></div>
        </div>
        ${cr.share_pct > 0 ? `
        <div class="progress-wrap">
          <div class="progress-label">
            <span>Pool share</span>
            <span>${cr.share_pct.toFixed(4)}%  →  est. ${fmt(cr.est_reward)} BOTCOIN</span>
          </div>
          <div class="progress-bar">
            <div class="progress-fill progress-purple" style="width:${Math.min(100, cr.share_pct * 50)}%"></div>
          </div>
        </div>` : ''}
      </div>
    </div>

    <!-- History -->
    <div class="section-title">Mining History</div>
    <div class="card">
      <table class="data-table">
        <thead>
          <tr>
            <th>Epoch</th>
            <th>Version</th>
            <th class="num-right">Solves</th>
            <th class="num-right">Passes</th>
            <th class="num-right">Credits</th>
            <th class="num-right">Credits/Solve</th>
            <th class="num-right">Tokens</th>
            <th class="num-right">Cost ($)</th>
          </tr>
        </thead>
        <tbody>
          ${histRows}
          ${histTotalRow}
        </tbody>
      </table>
    </div>

    <!-- Recent Transfers -->
    <div class="section-title">Recent BOTCOIN Transfers</div>
    <div class="card">
      <table class="data-table">
        <thead>
          <tr>
            <th>Date</th>
            <th>Type</th>
            <th class="num-right">Amount</th>
            <th>Transaction</th>
          </tr>
        </thead>
        <tbody>${xferRows}</tbody>
      </table>
    </div>

    <div style="text-align:right;color:var(--muted);font-size:11px;margin-top:8px">
      Last updated: ${fetched}
    </div>
  `;
}

function startCountdown() {
  if (countdownInterval) clearInterval(countdownInterval);
  refreshTimer = 60;
  document.getElementById('countdown').textContent = refreshTimer;
  countdownInterval = setInterval(() => {
    refreshTimer--;
    const el = document.getElementById('countdown');
    if (el) el.textContent = refreshTimer;
    if (refreshTimer <= 0) loadData();
  }, 1000);
}

async function loadData() {
  refreshTimer = 60;
  try {
    const resp = await fetch('/api/data');
    if (!resp.ok) throw new Error('API error ' + resp.status);
    const data = await resp.json();
    if (data.error) {
      document.getElementById('mainContent').innerHTML =
        `<div class="error-box">Error: ${data.error}</div>`;
    } else {
      render(data);
    }
  } catch(e) {
    document.getElementById('mainContent').innerHTML =
      `<div class="error-box">Failed to load data: ${e.message}<br>Is the dashboard server running?</div>`;
  }
  startCountdown();
}

// Initial load
loadData();
</script>
</body>
</html>"""


# ── HTTP Handler ──────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # suppress default access log

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self._serve_html()
        elif self.path == "/api/data":
            self._serve_data()
        else:
            self.send_error(404)

    def _serve_html(self):
        body = HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _serve_data(self):
        try:
            data = fetch_dashboard_data()
            body = json.dumps(data).encode("utf-8")
        except Exception as e:
            body = json.dumps({"error": str(e)}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8901)
    args = parser.parse_args()

    # Load Bitwarden secrets if available
    try:
        from bitwarden import load_all_secrets
        load_all_secrets(verbose=False)
    except Exception:
        pass

    server = HTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://localhost:{args.port}"

    print(f"\nBOTCOIN Dashboard running at {url}")
    print(f"Bookmark: {url}")
    print(f"Press Ctrl+C to stop\n")

    # Try to open browser
    try:
        import webbrowser, threading
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    except Exception:
        pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped.")


if __name__ == "__main__":
    main()

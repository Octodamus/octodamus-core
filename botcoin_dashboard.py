"""
botcoin_dashboard.py — BOTCOIN Mining Dashboard

Shows live snapshot of:
  - Wallet balances (BOTCOIN + ETH)
  - Staked amount in V3 mining contract
  - Current epoch + your credits share
  - Estimated BOTCOIN reward for current epoch
  - Historical mining log (all epochs)
  - Recent BOTCOIN claim transactions

Run:
  python botcoin_dashboard.py          # full dashboard
  python botcoin_dashboard.py --watch  # refresh every 60s
"""

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# ── Config ────────────────────────────────────────────────────────────────────

COORDINATOR     = "https://coordinator.agentmoney.net"
BANKR_API       = "https://api.bankr.bot/agent"
BLOCKSCOUT_API  = "https://base.blockscout.com/api/v2"
BASE_RPC        = "https://mainnet.base.org"
CHAIN_ID        = 8453

BOTCOIN_ADDR       = "0xA601877977340862Ca67f816eb079958E5bd0BA3"
V3_MINING_CONTRACT = "0xB2fbe0DB5A99B4E2Dd294dE64cEd82740b53A2Ea"

BOTCOIN_DECIMALS = 18
CREDITS_LOG      = Path(r"C:\Users\walli\octodamus\data\botcoin_credits.json")
AUTH_CACHE       = Path(r"C:\Users\walli\octodamus\data\botcoin_auth.json")

# ── Secrets ───────────────────────────────────────────────────────────────────

def _get_bankr_key() -> str:
    import os
    k = os.environ.get("BANKR_API_KEY", "")
    if not k:
        for p in [
            Path(r"C:\Users\walli\octodamus\.octo_secrets"),
            Path(__file__).parent / ".octo_secrets",
        ]:
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                k = d.get("secrets", d).get("BANKR_API_KEY", "")
                if k:
                    break
            except Exception:
                continue
    return k


# ── On-chain helpers ──────────────────────────────────────────────────────────

def _eth_call(to: str, data: str) -> str:
    r = requests.post(BASE_RPC, json={
        "jsonrpc": "2.0", "id": 1,
        "method": "eth_call",
        "params": [{"to": to, "data": data}, "latest"],
    }, timeout=10)
    return r.json().get("result", "0x0")


def _keccak4(fn_sig: str) -> str:
    """
    First 4 bytes of keccak256 of fn_sig — EVM function selector.
    Uses hardcoded known selectors first (no pysha3 dependency needed).
    Falls back to pysha3 or pycryptodome if an unknown signature is requested.
    """
    # Hardcoded selectors — verified via web3.keccak
    _KNOWN = {
        "balanceOf(address)":        "70a08231",
        "stakedAmount(address)":     "f9931855",   # verified
        "withdrawableAt(address)":   "5a8c06ab",   # verified
        "withdraw()":                "3ccfd60b",
        "totalSupply()":             "18160ddd",
    }
    if fn_sig in _KNOWN:
        return "0x" + _KNOWN[fn_sig]
    # Dynamic fallback
    try:
        import sha3
        k = sha3.keccak_256(fn_sig.encode()).hexdigest()
        return "0x" + k[:8]
    except ImportError:
        pass
    try:
        from Crypto.Hash import keccak as _k
        k = _k.new(digest_bits=256)
        k.update(fn_sig.encode())
        return "0x" + k.hexdigest()[:8]
    except ImportError:
        pass
    return "0x00000000"


def _pad_addr(addr: str) -> str:
    return addr.lower().replace("0x", "").zfill(64)


def get_erc20_balance(token: str, wallet: str) -> int:
    data = _keccak4("balanceOf(address)") + _pad_addr(wallet)
    result = _eth_call(token, data)
    return int(result, 16) if result and result != "0x" else 0


def get_eth_balance(wallet: str) -> int:
    r = requests.post(BASE_RPC, json={
        "jsonrpc": "2.0", "id": 1,
        "method": "eth_getBalance",
        "params": [wallet, "latest"],
    }, timeout=10)
    result = r.json().get("result", "0x0")
    return int(result, 16) if result and result != "0x" else 0


def get_staked_v3(wallet: str) -> int:
    """Read stakedAmount(address) from V3 mining contract."""
    data = _keccak4("stakedAmount(address)") + _pad_addr(wallet)
    result = _eth_call(V3_MINING_CONTRACT, data)
    return int(result, 16) if result and result != "0x" else 0


def get_withdrawable_at(wallet: str) -> int:
    """Read withdrawableAt(address) — timestamp when unstaked tokens are claimable."""
    data = _keccak4("withdrawableAt(address)") + _pad_addr(wallet)
    result = _eth_call(V3_MINING_CONTRACT, data)
    return int(result, 16) if result and result != "0x" else 0


# ── Bankr API ─────────────────────────────────────────────────────────────────

def bankr_me() -> dict:
    key = _get_bankr_key()
    if not key:
        return {}
    r = requests.get(
        f"{BANKR_API}/me",
        headers={"X-API-Key": key},
        timeout=15,
    )
    if r.ok:
        return r.json()
    return {}


def get_wallet_address() -> str:
    me = bankr_me()
    for w in me.get("wallets", []):
        if w.get("chain") == "evm" or w.get("chainId") == CHAIN_ID:
            return w["address"]
    return "0x7d372b930b42d4adc7c82f9d5bcb692da3597570"  # fallback to known wallet


# ── Coordinator API ───────────────────────────────────────────────────────────

def get_epoch() -> dict:
    try:
        r = requests.get(f"{COORDINATOR}/v1/epoch", timeout=10)
        if r.ok:
            d = r.json()
            # Normalize keys to a consistent format
            return {
                "epoch":     d.get("epochId", d.get("epoch", d.get("currentEpoch", "?"))),
                "end_ts":    int(d.get("nextEpochStartTimestamp", 0)),
                "duration":  int(d.get("epochDurationSeconds", 86400)),
                "rewardPool":d.get("rewardPool", d.get("pool", 0)),
                "totalCredits": d.get("totalCredits", d.get("credits", 0)),
                "_raw":      d,
            }
    except Exception:
        pass
    return {}


def get_credits(wallet: str) -> dict:
    try:
        # Try with cached auth token
        headers = {"Content-Type": "application/json"}
        if AUTH_CACHE.exists():
            cache = json.loads(AUTH_CACHE.read_text(encoding="utf-8"))
            if cache.get("token") and time.time() - cache.get("ts", 0) < 82800:
                headers["Authorization"] = f"Bearer {cache['token']}"

        r = requests.get(
            f"{COORDINATOR}/v1/credits",
            params={"miner": wallet},
            headers=headers,
            timeout=10,
        )
        if r.ok:
            return r.json()
    except Exception:
        pass
    return {}


def get_leaderboard() -> dict:
    """Fetch epoch leaderboard to calculate our reward share."""
    try:
        r = requests.get(f"{COORDINATOR}/v1/leaderboard", timeout=10)
        if r.ok:
            return r.json()
    except Exception:
        pass
    return {}


# ── Blockscout: claim history ─────────────────────────────────────────────────

def get_botcoin_transfers(wallet: str, max_items: int = 20) -> list[dict]:
    """
    Fetch recent BOTCOIN ERC-20 transfers involving wallet via Blockscout.
    Returns both incoming (claims/rewards) and outgoing (stakes).
    """
    result = []
    wallet_lower = wallet.lower()
    try:
        # All transfers to wallet (incoming = rewards/claims)
        for direction in ("to", "from"):
            r = requests.get(
                f"{BLOCKSCOUT_API}/addresses/{wallet}/token-transfers",
                params={"type": "ERC-20", "filter": direction},
                timeout=15,
            )
            if not r.ok:
                continue
            for t in r.json().get("items", [])[:max_items]:
                token = t.get("token", {})
                if token.get("address", "").lower() != BOTCOIN_ADDR.lower():
                    continue
                val_raw = t.get("total", {}).get("value", "0")
                amount  = int(val_raw) / (10 ** BOTCOIN_DECIMALS)
                to_addr = t.get("to", {}).get("hash", "").lower()
                label   = "CLAIM" if to_addr == wallet_lower else "STAKE"
                result.append({
                    "tx_hash":   t.get("transaction_hash", ""),
                    "from":      t.get("from", {}).get("hash", ""),
                    "to":        t.get("to", {}).get("hash", ""),
                    "amount":    amount,
                    "timestamp": t.get("timestamp", ""),
                    "label":     label,
                })
    except Exception:
        pass

    # Deduplicate by tx_hash
    seen = set()
    deduped = []
    for t in result:
        if t["tx_hash"] not in seen:
            seen.add(t["tx_hash"])
            deduped.append(t)
    return sorted(deduped, key=lambda x: x["timestamp"], reverse=True)[:max_items]


# ── Credits log ───────────────────────────────────────────────────────────────

def load_credits_log() -> dict:
    try:
        if CREDITS_LOG.exists():
            return json.loads(CREDITS_LOG.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


# ── Formatting ────────────────────────────────────────────────────────────────

def _fmt_bc(n: float) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return f"{n:,.0f}"


def _fmt_ts(ts_str: str) -> str:
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.strftime("%b %d %H:%M UTC")
    except Exception:
        return ts_str[:16] if ts_str else "—"


def _bar(pct: float, width: int = 20) -> str:
    filled = int(width * min(pct, 1.0))
    return "#" * filled + "-" * (width - filled)


# ── Dashboard Renderer ────────────────────────────────────────────────────────

def render_dashboard(wallet: str):
    try:
        from rich.console import Console
        from rich.table import Table
        from rich.panel import Panel
        from rich.columns import Columns
        from rich import box
        _RICH = True
        console = Console()
    except ImportError:
        _RICH = False
        console = None

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    print(f"\n{'='*60}")
    print(f"  BOTCOIN MINING DASHBOARD — {now_str}")
    print(f"  Wallet: {wallet}")
    print(f"{'='*60}\n")

    # ── 1. On-chain balances ──
    print("[ WALLET BALANCES ]")
    bc_tokens = 0.0
    staked    = 0.0
    try:
        bc_raw    = get_erc20_balance(BOTCOIN_ADDR, wallet)
        bc_tokens = bc_raw / (10 ** BOTCOIN_DECIMALS)
        eth_raw   = get_eth_balance(wallet)
        eth_val   = eth_raw / 1e18
        staked    = get_staked_v3(wallet) / (10 ** BOTCOIN_DECIMALS)
        wa_ts     = get_withdrawable_at(wallet)

        total_bc = bc_tokens + staked
        print(f"  BOTCOIN (wallet):  {_fmt_bc(bc_tokens)} BOTCOIN")
        print(f"  BOTCOIN (staked):  {_fmt_bc(staked)} BOTCOIN  (V3 mining contract)")
        if total_bc:
            print(f"  BOTCOIN (total):   {_fmt_bc(total_bc)} BOTCOIN")

        if wa_ts > 0:
            wa_dt  = datetime.fromtimestamp(wa_ts, tz=timezone.utc)
            now_ts = time.time()
            if wa_ts > now_ts:
                secs_left = wa_ts - now_ts
                h, m = int(secs_left // 3600), int((secs_left % 3600) // 60)
                print(f"  Unstake cooldown:  {wa_dt.strftime('%b %d %H:%M UTC')}  ({h}h {m}m remaining)")
            else:
                print(f"  Unstake cooldown:  expired — READY TO WITHDRAW")

        print(f"  ETH (gas):         {eth_val:.6f} ETH")
    except Exception as e:
        print(f"  [error fetching balances: {e}]")

    # ── 2. Current epoch ──
    print(f"\n[ CURRENT EPOCH ]")
    epoch_data    = get_epoch()
    ep_num        = epoch_data.get("epoch", "?")
    ep_end_ts     = epoch_data.get("end_ts", 0)
    ep_duration   = epoch_data.get("duration", 86400)
    ep_pool       = epoch_data.get("rewardPool", 0)
    total_credits = epoch_data.get("totalCredits", 0)

    print(f"  Epoch:             #{ep_num}")

    if ep_end_ts:
        now_ts    = time.time()
        secs_left = max(0, ep_end_ts - now_ts)
        h_left    = int(secs_left // 3600)
        m_left    = int((secs_left % 3600) // 60)
        end_dt    = datetime.fromtimestamp(ep_end_ts, tz=timezone.utc)
        pct_done  = max(0, min(1, 1 - secs_left / ep_duration))
        print(f"  Ends:              {end_dt.strftime('%b %d %H:%M UTC')}  ({h_left}h {m_left}m remaining)")
        print(f"  Progress:          [{_bar(pct_done, 30)}] {pct_done*100:.0f}%")

    if ep_pool:
        pool_tokens = int(ep_pool) / (10 ** BOTCOIN_DECIMALS) if int(ep_pool) > 10**12 else int(ep_pool)
        print(f"  Reward pool:       {_fmt_bc(pool_tokens)} BOTCOIN")
    if total_credits:
        print(f"  Network credits:   {int(total_credits):,}")

    # ── 3. My credits this epoch ──
    print(f"\n[ YOUR CREDITS — EPOCH #{ep_num} ]")
    credits_data = get_credits(wallet)

    if credits_data.get("error"):
        # Rate limited — pull from local log for current epoch
        log_data_early = load_credits_log()
        ep_log = log_data_early.get(str(ep_num), {})
        my_credits = ep_log.get("credits", 0)
        my_solves  = ep_log.get("solves", 0)
        my_passes  = ep_log.get("passes", 0)
        err_msg    = credits_data.get("error", "")
        retry_secs = credits_data.get("retryAfterSeconds", 0)
        if retry_secs:
            retry_h, retry_m = int(retry_secs // 3600), int((retry_secs % 3600) // 60)
            print(f"  (rate limited — showing cached data; retry in {retry_h}h {retry_m}m)")
        else:
            print(f"  (using cached data: {err_msg[:60]})")
    else:
        my_credits = credits_data.get("credits", credits_data.get("totalCredits", 0))
        my_solves  = credits_data.get("solves",  credits_data.get("totalSolves", 0))
        my_passes  = credits_data.get("passes",  0)

    print(f"  Credits earned:    {int(my_credits):,}")
    print(f"  Solves:            {int(my_solves):,}")
    if my_passes:
        print(f"  Passes (accepted): {int(my_passes):,}")
    if my_solves and my_credits:
        cps = my_credits / my_solves if my_solves else 0
        print(f"  Credits/solve:     {cps:.0f}  ({'V3 ✓' if cps > 100 else 'V2'})")

    # Reward estimate
    if my_credits and total_credits and ep_pool:
        share = int(my_credits) / int(total_credits)
        pool_tokens = int(ep_pool) / (10 ** BOTCOIN_DECIMALS) if int(ep_pool) > 10**12 else int(ep_pool)
        est_reward  = share * pool_tokens
        bar = _bar(share, 25)
        print(f"  Pool share:        {share:.4%}  [{bar}]")
        print(f"  Est. reward:       ~{_fmt_bc(est_reward)} BOTCOIN")
    elif my_credits:
        print(f"  Pool size unknown — reward estimate unavailable")

    # Mining status
    if staked <= 0:
        print(f"\n  [!] NOT STAKED -- mine will fail until you restake to V3")
        print(f"      Run: python octo_boto_botcoin.py --stake")

    # ── 4. Historical mining log ──
    log_data = load_credits_log()
    if log_data:
        print(f"\n[ MINING HISTORY — ALL EPOCHS ]")
        print(f"  {'Epoch':<8} {'Solves':<8} {'Credits':<12} {'Notes'}")
        print(f"  {'-'*50}")

        total_solves  = 0
        total_credits_hist = 0

        for ep, rec in sorted(log_data.items(), key=lambda x: int(x[0])):
            solves   = rec.get("solves", 0)
            passes   = rec.get("passes", 0)
            credits  = rec.get("credits", 0)
            total_solves  += solves
            total_credits_hist += credits
            note = ""
            if credits > 10000:
                note = "V3 (520 credits/solve)"
            elif credits == passes:
                note = "V2 (1 credit/solve)"
            print(f"  {ep:<8} {solves:<8} {credits:<12,} {note}")

        print(f"  {'-'*50}")
        print(f"  {'TOTAL':<8} {total_solves:<8} {total_credits_hist:<12,}")

    # ── 5. Recent claim transactions ──
    print(f"\n[ RECENT BOTCOIN RECEIPTS — BLOCKSCOUT ]")
    try:
        transfers = get_botcoin_transfers(wallet, max_items=10)
        if transfers:
            print(f"  {'Date':<18} {'Amount':<16} {'From':<20} TX")
            print(f"  {'-'*72}")
            total_received = 0.0
            for t in transfers:
                from_short = t["from"][:10] + "..." if len(t["from"]) > 10 else t["from"]
                tx_short   = t["tx_hash"][:12] + "..."
                ts_str     = _fmt_ts(t["timestamp"])
                total_received += t["amount"]
                print(f"  {ts_str:<18} {_fmt_bc(t['amount']):<16} {from_short:<20} {tx_short}")
            print(f"  {'-'*72}")
            print(f"  Total received: {_fmt_bc(total_received)} BOTCOIN")
        else:
            print("  No BOTCOIN transfers found (or Blockscout unavailable).")
    except Exception as e:
        print(f"  [error: {e}]")

    # ── 6. Quick tips ──
    print(f"\n[ COMMANDS ]")
    print(f"  Mine:     python octo_boto_botcoin.py --loop")
    print(f"  Claim:    python octo_boto_botcoin.py --claim")
    print(f"  Withdraw: python octo_boto_botcoin.py --withdraw")
    print(f"  Status:   python octo_boto_botcoin.py --setup")
    print(f"\n{'='*60}\n")


# ── Entry Point ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="BOTCOIN Mining Dashboard")
    parser.add_argument("--watch",    action="store_true", help="Refresh every 60 seconds")
    parser.add_argument("--interval", type=int, default=60, help="Watch interval in seconds")
    parser.add_argument("--wallet",   type=str, default="", help="Override wallet address")
    args = parser.parse_args()

    # Load secrets
    try:
        import os, sys
        sys.path.insert(0, str(Path(__file__).parent))
        try:
            from bitwarden import load_all_secrets
            load_all_secrets(verbose=False)
        except Exception:
            pass
    except Exception:
        pass

    wallet = args.wallet or get_wallet_address()
    print(f"Fetching data for {wallet}...")

    if args.watch:
        try:
            while True:
                try:
                    import os
                    os.system("cls" if os.name == "nt" else "clear")
                except Exception:
                    pass
                render_dashboard(wallet)
                print(f"Next refresh in {args.interval}s... (Ctrl+C to stop)")
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\nDashboard stopped.")
    else:
        render_dashboard(wallet)


if __name__ == "__main__":
    main()

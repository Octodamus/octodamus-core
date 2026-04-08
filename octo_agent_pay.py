"""
octo_agent_pay.py
Octodamus — On-chain USDC payment listener for AI agent commerce (Base mainnet)

Flow:
  1. Agent calls POST /v1/agent-checkout  → gets {payment_address, amount_usdc, payment_id}
  2. Agent sends USDC on Base to payment_address with payment_id in memo (or just exact amount)
  3. Agent polls GET /v1/agent-checkout/status?payment_id=xxx
  4. Listener detects on-chain USDC transfer → provisions API key → returns to agent

USDC on Base: 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913
Treasury:     0x5c6B3a3dAe296d3cef50fef96afC73410959a6Db

No Stripe. No browser. No human. Pure machine-to-machine.
"""

import json
import os
import secrets
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

# ── Base (USDC) ───────────────────────────────────────────────────────────────
BASE_RPC       = "https://mainnet.base.org"
CHAIN_ID       = 8453
USDC_CONTRACT  = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
TREASURY       = "0x5c6B3a3dAe296d3cef50fef96afC73410959a6Db"

# ── Ethereum mainnet (USDC) ───────────────────────────────────────────────────
ETH_RPC        = "https://eth.llamarpc.com"
ETH_CHAIN_ID   = 1
USDC_ETH       = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"   # USDC on Ethereum
ETH_TREASURY   = "0xf6911Ba4FD11e7A12545d5fDD7D6e6C3009d81a2"

# ── Bitcoin ───────────────────────────────────────────────────────────────────
BTC_ADDRESS    = "32yRN7VjEPWWnNNCwWomDfeb653jqAdD3p"
BTC_TOLERANCE  = 0.005   # 0.5% — allows for rounding at checkout

# Prices in USDC (6 decimals on-chain)
PRICES = {
    "premium_trial":   5_000_000,    # $5  USDC — 7-day Premium trial
    "premium_annual":  29_000_000,   # $29 USDC — premium API key, annual (no expiry)
    "guide_early":     29_000_000,   # $29 USDC — guide early bird
    "guide_standard":  39_000_000,   # $39 USDC — guide standard
}

PRICE_DISPLAY = {
    "premium_trial":  5.0,
    "premium_annual": 29.0,
    "guide_early":    29.0,
    "guide_standard": 39.0,
}

TRIAL_DAYS = 7  # premium_trial key expires after this many days

PAYMENT_TTL_SECONDS = 3600   # payments expire after 1 hour
CONFIRM_BLOCKS      = 2      # blocks to wait before considering final

_PAYMENTS_FILE = Path(__file__).parent / "data" / "octo_agent_payments.json"

# Minimal ERC-20 Transfer ABI
USDC_ABI = [
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True,  "name": "from",  "type": "address"},
            {"indexed": True,  "name": "to",    "type": "address"},
            {"indexed": False, "name": "value", "type": "uint256"},
        ],
        "name": "Transfer",
        "type": "event",
    },
    {
        "inputs": [{"name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]


# ─────────────────────────────────────────────
# PAYMENT STORE
# ─────────────────────────────────────────────

def _load_payments() -> dict:
    if _PAYMENTS_FILE.exists():
        try:
            return json.loads(_PAYMENTS_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_payments(data: dict) -> None:
    _PAYMENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _PAYMENTS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(_PAYMENTS_FILE)


# ─────────────────────────────────────────────
# WEB3 HELPERS
# ─────────────────────────────────────────────

def _w3():
    from web3 import Web3
    return Web3(Web3.HTTPProvider(BASE_RPC))


def _usdc_contract(w3):
    return w3.eth.contract(
        address=w3.to_checksum_address(USDC_CONTRACT),
        abi=USDC_ABI,
    )


def get_usdc_balance(address: str) -> float:
    """Return USDC balance of address in human-readable dollars."""
    try:
        w3 = _w3()
        contract = _usdc_contract(w3)
        raw = contract.functions.balanceOf(
            w3.to_checksum_address(address)
        ).call()
        return raw / 1_000_000
    except Exception as e:
        print(f"[AgentPay] Balance check failed: {e}")
        return 0.0


# ─────────────────────────────────────────────
# STEP 1 — CREATE PAYMENT INTENT
# ─────────────────────────────────────────────

def _get_btc_price_usd() -> float:
    """Fetch current BTC price in USD from CoinGecko."""
    try:
        import httpx
        r = httpx.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "bitcoin", "vs_currencies": "usd"},
            timeout=8,
        )
        return float(r.json()["bitcoin"]["usd"])
    except Exception:
        return 80000.0   # safe fallback — overpays slightly, never underpays


def create_payment(
    product: str,               # "premium_annual" | "guide_early" | "guide_standard"
    agent_wallet: str = "",     # sender wallet (EVM) or empty
    label: str = "",
    email: str = "",
    chain: str = "base",        # "base" | "eth" | "btc"
) -> dict:
    """
    Create a payment intent for Base USDC, Ethereum USDC, or Bitcoin.
    Returns everything the payer needs to complete the transaction.
    """
    if product not in PRICES:
        raise ValueError(f"Unknown product: {product}. Options: {list(PRICES.keys())}")

    chain = chain.lower().strip()
    if chain not in ("base", "eth", "btc"):
        chain = "base"

    payment_id  = "octo_pay_" + secrets.token_hex(8)
    amount_usd  = PRICE_DISPLAY[product]
    expires_at  = (datetime.now(timezone.utc) + timedelta(seconds=PAYMENT_TTL_SECONDS)).isoformat()

    # Chain-specific amounts and addresses
    if chain == "btc":
        btc_price      = _get_btc_price_usd()
        amount_btc     = round(amount_usd / btc_price, 8)
        amount_sat     = round(amount_btc * 1e8)
        payment_address = BTC_ADDRESS
        record = {
            "payment_id":       payment_id,
            "product":          product,
            "chain":            "btc",
            "amount_usd":       amount_usd,
            "amount_btc":       amount_btc,
            "amount_sat":       amount_sat,
            "btc_price_at_checkout": btc_price,
            "payment_address":  BTC_ADDRESS,
            "agent_wallet":     "",
            "label":            label,
            "email":            email,
            "status":           "pending",
            "created_at":       datetime.now(timezone.utc).isoformat(),
            "expires_at":       expires_at,
            "tx_hash":          None,
            "api_key":          None,
            "fulfilled_at":     None,
        }
        response = {
            "payment_id":       payment_id,
            "product":          product,
            "chain":            "btc",
            "amount_btc":       amount_btc,
            "amount_sat":       amount_sat,
            "amount_usd":       amount_usd,
            "btc_price":        btc_price,
            "payment_address":  BTC_ADDRESS,
            "expires_at":       expires_at,
            "instructions":     f"Send exactly {amount_btc:.8f} BTC to {BTC_ADDRESS}. Amount is locked for 1 hour.",
            "poll_url":         f"https://api.octodamus.com/v1/agent-checkout/status?payment_id={payment_id}",
        }
    else:
        # USDC — same amounts on Base and ETH
        amount_raw     = PRICES[product]
        amount_usdc    = amount_raw / 1_000_000
        if chain == "eth":
            payment_address = ETH_TREASURY
            contract        = USDC_ETH
            chain_id        = ETH_CHAIN_ID
            chain_name      = "Ethereum mainnet"
        else:
            chain           = "base"
            payment_address = TREASURY
            contract        = USDC_CONTRACT
            chain_id        = CHAIN_ID
            chain_name      = "Base (chain_id=8453)"

        record = {
            "payment_id":   payment_id,
            "product":      product,
            "chain":        chain,
            "amount_raw":   amount_raw,
            "amount_usdc":  amount_usdc,
            "amount_usd":   amount_usd,
            "agent_wallet": agent_wallet.lower() if agent_wallet else "",
            "label":        label,
            "email":        email,
            "status":       "pending",
            "created_at":   datetime.now(timezone.utc).isoformat(),
            "expires_at":   expires_at,
            "tx_hash":      None,
            "block":        None,
            "api_key":      None,
            "fulfilled_at": None,
        }
        response = {
            "payment_id":       payment_id,
            "product":          product,
            "chain":            chain,
            "chain_id":         chain_id,
            "amount_usdc":      amount_usdc,
            "amount_usd":       amount_usd,
            "payment_address":  payment_address,
            "token":            "USDC",
            "contract":         contract,
            "expires_at":       expires_at,
            "instructions":     f"Send exactly {amount_usdc} USDC on {chain_name} to {payment_address}.",
            "poll_url":         f"https://api.octodamus.com/v1/agent-checkout/status?payment_id={payment_id}",
            "status_url":       f"https://api.octodamus.com/v1/agent-checkout/status?payment_id={payment_id}",
        }

    payments = _load_payments()
    payments[payment_id] = record
    _save_payments(payments)
    print(f"[AgentPay] Created {chain} payment: {payment_id} | {product} | ${amount_usd}")
    return response


# ─────────────────────────────────────────────
# STEP 2 — SCAN CHAIN FOR PAYMENT
# ─────────────────────────────────────────────

def _scan_usdc_transfers(from_block: int, to_block: int) -> list:
    """Fetch USDC Transfer events to treasury in block range."""
    try:
        w3 = _w3()
        contract = _usdc_contract(w3)
        treasury_cs = w3.to_checksum_address(TREASURY)

        events = contract.events.Transfer.get_logs(
            from_block=from_block,
            to_block=to_block,
            argument_filters={"to": treasury_cs},
        )
        return [
            {
                "from":    e["args"]["from"].lower(),
                "to":      e["args"]["to"].lower(),
                "value":   e["args"]["value"],
                "tx_hash": e["transactionHash"].hex(),
                "block":   e["blockNumber"],
            }
            for e in events
        ]
    except Exception as e:
        print(f"[AgentPay] Transfer scan failed: {e}")
        return []


def _scan_eth_usdc_transfers(from_block: int, to_block: int) -> list:
    """Fetch USDC Transfer events to ETH treasury on Ethereum mainnet."""
    try:
        from web3 import Web3
        w3  = Web3(Web3.HTTPProvider(ETH_RPC))
        contract = w3.eth.contract(
            address=w3.to_checksum_address(USDC_ETH),
            abi=USDC_ABI,
        )
        treasury_cs = w3.to_checksum_address(ETH_TREASURY)
        events = contract.events.Transfer.get_logs(
            from_block=from_block,
            to_block=to_block,
            argument_filters={"to": treasury_cs},
        )
        return [
            {
                "from":    e["args"]["from"].lower(),
                "to":      e["args"]["to"].lower(),
                "value":   e["args"]["value"],
                "tx_hash": e["transactionHash"].hex(),
                "block":   e["blockNumber"],
                "chain":   "eth",
            }
            for e in events
        ]
    except Exception as e:
        print(f"[AgentPay] ETH transfer scan failed: {e}")
        return []


def _scan_btc_transfers() -> list:
    """
    Fetch recent BTC transactions to treasury address via BlockCypher API.
    Returns list of incoming transfers in the last hour.
    """
    try:
        import httpx
        r = httpx.get(
            f"https://api.blockcypher.com/v1/btc/main/addrs/{BTC_ADDRESS}/full",
            params={"limit": 20},
            timeout=10,
        )
        if r.status_code != 200:
            return []
        data = r.json()
        txs  = data.get("txs", [])
        now  = datetime.now(timezone.utc)
        result = []
        for tx in txs:
            # Only look at recent confirmed or unconfirmed txs (last 2 hours)
            received = tx.get("received", "")
            try:
                from datetime import datetime as _dt
                tx_time = _dt.fromisoformat(received.replace("Z", "+00:00"))
                if (now - tx_time).total_seconds() > 7200:
                    continue
            except Exception:
                pass
            # Sum outputs to our address
            total_sat = sum(
                o.get("value", 0)
                for o in tx.get("outputs", [])
                if BTC_ADDRESS in o.get("addresses", [])
            )
            if total_sat > 0:
                result.append({
                    "chain":     "btc",
                    "tx_hash":   tx.get("hash", ""),
                    "value_sat": total_sat,
                    "value_btc": total_sat / 1e8,
                    "confirmed": tx.get("confirmations", 0) > 0,
                })
        return result
    except Exception as e:
        print(f"[AgentPay] BTC scan failed: {e}")
        return []


def _match_transfer_to_payment(transfer: dict, payments: dict) -> Optional[str]:
    """
    Try to match an on-chain transfer to a pending payment.
    Handles USDC (Base/ETH) by raw amount, BTC by satoshi amount with tolerance.
    Returns payment_id if matched, None otherwise.
    """
    chain  = transfer.get("chain", "base")
    sender = transfer.get("from", "").lower()

    if chain == "btc":
        value_sat = transfer.get("value_sat", 0)
        candidates = []
        for pid, p in payments.items():
            if p["status"] != "pending" or p.get("chain") != "btc" or _is_expired(p):
                continue
            expected_sat = p.get("amount_sat", 0)
            if expected_sat and abs(value_sat - expected_sat) / max(expected_sat, 1) <= BTC_TOLERANCE:
                candidates.append((pid, p))
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[1]["created_at"])
        return candidates[0][0]

    # USDC (Base or ETH)
    value = transfer["value"]

    candidates = [
        (pid, p) for pid, p in payments.items()
        if p["status"] == "pending"
        and p.get("chain", "base") == chain
        and p.get("amount_raw") == value
        and not _is_expired(p)
    ]

    if not candidates:
        return None

    # If sender wallet is known, prefer exact match
    for pid, p in candidates:
        if p.get("agent_wallet") and p["agent_wallet"] == sender:
            return pid

    # Otherwise take the oldest pending match (FIFO)
    candidates.sort(key=lambda x: x[1]["created_at"])
    return candidates[0][0]


def _is_expired(payment: dict) -> bool:
    try:
        exp = datetime.fromisoformat(payment["expires_at"])
        return datetime.now(timezone.utc) > exp
    except Exception:
        return False


# ─────────────────────────────────────────────
# STEP 3 — FULFILL: PROVISION API KEY
# ─────────────────────────────────────────────

def _fulfill_payment(payment_id: str, tx_hash: str, block: int) -> dict:
    """
    Payment confirmed on-chain. Provision the product and return to agent.
    """
    payments = _load_payments()
    payment  = payments.get(payment_id)
    if not payment:
        return {"error": "payment not found"}

    product = payment["product"]

    # Provision based on product
    if product in ("premium_annual", "premium_trial"):
        api_key = _provision_api_key(payment)
        payment["api_key"] = api_key
        is_trial    = product == "premium_trial"
        expires_str = payment.get("key_expires")  # set by _provision_api_key for trial
        result = {
            "status":      "fulfilled",
            "product":     product,
            "api_key":     api_key,
            "tier":        "premium",
            "header":      f"X-OctoData-Key: {api_key}",
            "limits":      {"req_per_day": 10000, "req_per_minute": 200},
            "trial":       is_trial,
            "expires_at":  expires_str if is_trial else None,
            "upgrade":     "POST https://api.octodamus.com/v1/agent-checkout?product=premium_annual — $29 USDC for annual" if is_trial else None,
            "docs":        "https://api.octodamus.com/docs",
            "tx_hash":     tx_hash,
        }
    elif product in ("guide_early", "guide_standard"):
        download_url = _provision_guide(payment)
        result = {
            "status":       "fulfilled",
            "product":      product,
            "download_url": download_url,
            "tx_hash":      tx_hash,
        }
    else:
        result = {"status": "fulfilled", "product": product, "tx_hash": tx_hash}

    payment["status"]       = "fulfilled"
    payment["tx_hash"]      = tx_hash
    payment["block"]        = block
    payment["fulfilled_at"] = datetime.now(timezone.utc).isoformat()
    payments[payment_id]    = payment
    _save_payments(payments)

    print(f"[AgentPay] Fulfilled {payment_id} | {product} | tx {tx_hash[:16]}...")

    # Email delivery
    email_addr = payment.get("email", "").strip()
    if email_addr:
        try:
            _send_fulfillment_email(email_addr, product, result)
        except Exception as e:
            print(f"[AgentPay] Email send failed (non-critical): {e}")

    # Discord alert
    try:
        import httpx
        webhook = os.environ.get("DISCORD_WEBHOOK_URL", "")
        if webhook:
            chain = payment.get("chain", "base")
            if chain == "btc":
                amount_str = f"{payment.get('amount_btc', '?')} BTC (${payment.get('amount_usd', '?')})"
                explorer   = f"https://blockstream.info/tx/{tx_hash}"
            elif chain == "eth":
                amount_str = f"${payment.get('amount_usdc', '?')} USDC on Ethereum"
                explorer   = f"https://etherscan.io/tx/{tx_hash}"
            else:
                amount_str = f"${payment.get('amount_usdc', '?')} USDC on Base"
                explorer   = f"https://basescan.org/tx/{tx_hash}"
            httpx.post(webhook, json={
                "content": (
                    f"**[AgentPay] On-chain sale confirmed**\n"
                    f"Product: {product}\n"
                    f"Amount: {amount_str}\n"
                    f"Chain: {chain.upper()}\n"
                    f"TX: {explorer}"
                )
            }, timeout=5)
    except Exception:
        pass

    return result


def _send_fulfillment_email(to_addr: str, product: str, result: dict) -> None:
    """Send delivery email via Gmail SMTP using credentials from Bitwarden/env."""
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    gmail_user = os.environ.get("GMAIL_USER", "")
    gmail_pass = os.environ.get("GMAIL_APP_PASSWORD", "")
    if not gmail_user or not gmail_pass:
        print("[AgentPay] Gmail credentials not set — skipping email")
        return

    is_guide   = product in ("guide_early", "guide_standard")
    is_trial   = product == "premium_trial"
    is_api     = product in ("premium_annual", "premium_trial")

    if is_guide:
        download_url = result.get("download_url", "")
        subject = "Your Octodamus Guide — Download Link Inside"
        body_html = f"""
<div style="background:#000810;color:#8ab8d4;font-family:'Courier New',monospace;padding:40px;max-width:580px;margin:0 auto;">
  <div style="font-size:1.4rem;letter-spacing:0.2em;color:#00c8ff;margin-bottom:8px;">OCTODAMUS</div>
  <div style="font-size:0.7rem;letter-spacing:0.2em;color:#3d6e8a;margin-bottom:32px;">BUILD THE HOUSE · 2026</div>
  <p style="color:#c8e8f8;font-size:1rem;margin-bottom:8px;">Your guide is ready.</p>
  <p style="margin-bottom:24px;">Payment confirmed on-chain. Click below to download — link is valid for 30 days.</p>
  <a href="{download_url}" style="display:inline-block;background:#00c8ff;color:#000810;padding:14px 32px;font-weight:700;letter-spacing:0.1em;text-decoration:none;font-size:0.85rem;">DOWNLOAD GUIDE →</a>
  <p style="margin-top:32px;font-size:0.75rem;color:#3d6e8a;">Or copy this link:<br><span style="color:#00c8ff;">{download_url}</span></p>
  <p style="margin-top:32px;font-size:0.72rem;color:#3d6e8a;">Questions? X: @octodamusai</p>
</div>"""
    elif is_api:
        api_key    = result.get("api_key", "")
        expire_note = f"<p style='color:#ffc800;font-size:0.8rem;margin-top:8px;'>Trial expires: {result.get('expires_at','7 days from now')}. Upgrade anytime at octodamus.com/buy.html</p>" if is_trial else ""
        subject = "Your Octodamus API Key"
        body_html = f"""
<div style="background:#000810;color:#8ab8d4;font-family:'Courier New',monospace;padding:40px;max-width:580px;margin:0 auto;">
  <div style="font-size:1.4rem;letter-spacing:0.2em;color:#00c8ff;margin-bottom:8px;">OCTODAMUS</div>
  <div style="font-size:0.7rem;letter-spacing:0.2em;color:#3d6e8a;margin-bottom:32px;">DATA STREAMS · API ACCESS</div>
  <p style="color:#c8e8f8;font-size:1rem;margin-bottom:8px;">Your API key is ready.</p>
  <p style="margin-bottom:16px;">Add this header to every request:</p>
  <div style="background:#020d1a;border:1px solid rgba(0,140,255,0.2);padding:16px;margin-bottom:16px;word-break:break-all;">
    <span style="color:#00ffb3;">X-OctoData-Key: {api_key}</span>
  </div>
  {expire_note}
  <p style="margin-top:24px;font-size:0.8rem;">10,000 req/day · 200 req/min</p>
  <p style="margin-top:8px;font-size:0.8rem;"><a href="https://api.octodamus.com/docs" style="color:#00c8ff;">api.octodamus.com/docs</a> — full endpoint reference</p>
  <p style="margin-top:32px;font-size:0.72rem;color:#3d6e8a;">Questions? X: @octodamusai</p>
</div>"""
    else:
        return  # nothing to send

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"Octodamus <{gmail_user}>"
    msg["To"]      = to_addr
    msg.attach(MIMEText(body_html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_user, gmail_pass)
        server.sendmail(gmail_user, to_addr, msg.as_string())

    print(f"[AgentPay] Email sent → {to_addr} ({product})")


def _provision_api_key(payment: dict) -> str:
    """Create a premium API key and register it in api_keys.json."""
    from pathlib import Path as _Path
    import secrets as _secrets

    keys_file = _Path(__file__).parent / "data" / "api_keys.json"
    keys = json.loads(keys_file.read_text()) if keys_file.exists() else {}

    is_trial = payment.get("product") == "premium_trial"
    wallet   = payment.get("agent_wallet", "")

    # For trials: don't block if wallet already has a trial — let them re-trial once
    # For annual: return existing key if wallet already has premium
    if wallet and not is_trial:
        for k, v in keys.items():
            if v.get("agent_wallet", "").lower() == wallet.lower() and v.get("tier") == "premium":
                existing_expires = v.get("expires")
                # Only return existing if it's an annual (no expiry) key
                if not existing_expires:
                    print(f"[AgentPay] Wallet already has annual premium key — returning existing")
                    return k

    new_key   = "octo_" + _secrets.token_urlsafe(24)
    label     = payment.get("label") or payment.get("email") or (wallet[:12] if wallet else "") or "agent"
    expires   = None
    if is_trial:
        expires = (datetime.utcnow() + timedelta(days=TRIAL_DAYS)).isoformat()
        payment["key_expires"] = expires  # expose to _fulfill_payment for response

    keys[new_key] = {
        "label":        label,
        "email":        payment.get("email", ""),
        "agent_wallet": wallet,
        "tier":         "premium",
        "created":      datetime.utcnow().isoformat(),
        "expires":      expires,          # None = no expiry; ISO string = trial expiry
        "trial":        is_trial,
        "payment_id":   payment["payment_id"],
        "payment_tx":   payment.get("tx_hash", ""),
        "source":       "onchain_usdc",
    }
    keys_file.write_text(json.dumps(keys, indent=2))
    kind = "trial" if is_trial else "annual"
    print(f"[AgentPay] Provisioned {kind} premium key: {new_key[:16]}... expires={expires or 'never'}")
    return new_key


def _provision_guide(payment: dict) -> str:
    """Return a signed time-limited download URL for the guide."""
    # Simple approach: signed token stored server-side, verified on download
    import hashlib
    token   = secrets.token_hex(16)
    expires = int(time.time()) + 86400 * 30   # 30-day download window

    guide_tokens_file = Path(__file__).parent / "data" / "guide_tokens.json"
    tokens = json.loads(guide_tokens_file.read_text()) if guide_tokens_file.exists() else {}
    tokens[token] = {
        "payment_id": payment["payment_id"],
        "expires":    expires,
        "product":    payment["product"],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    guide_tokens_file.write_text(json.dumps(tokens, indent=2))

    return f"https://api.octodamus.com/v1/guide/download?token={token}"


# ─────────────────────────────────────────────
# MAIN LISTENER — run as background thread
# ─────────────────────────────────────────────

_last_scanned_block:     int = 0
_last_scanned_block_eth: int = 0


def scan_for_payments() -> list:
    """
    Scan Base, Ethereum, and Bitcoin for incoming payments.
    Match against pending intents. Fulfill matches.
    Returns list of fulfilled payment_ids.
    """
    global _last_scanned_block, _last_scanned_block_eth

    fulfilled = []
    payments  = _load_payments()
    pending   = [p for p in payments.values() if p["status"] == "pending" and not _is_expired(p)]
    if not pending:
        return []

    pending_chains = {p.get("chain", "base") for p in pending}

    # ── Base USDC ─────────────────────────────────────────────────────────────
    if "base" in pending_chains:
        try:
            w3         = _w3()
            latest     = w3.eth.block_number
            from_block = max(_last_scanned_block + 1, latest - 50)
            if from_block <= latest:
                transfers = _scan_usdc_transfers(from_block, latest)
                _last_scanned_block = latest
                for t in transfers:
                    t["chain"] = "base"
                    pid = _match_transfer_to_payment(t, payments)
                    if pid:
                        _fulfill_payment(pid, t["tx_hash"], t.get("block", 0))
                        fulfilled.append(pid)
                        payments = _load_payments()
        except Exception as e:
            print(f"[AgentPay] Base scan error: {e}")

    # ── Ethereum USDC ─────────────────────────────────────────────────────────
    if "eth" in pending_chains:
        try:
            from web3 import Web3
            w3e        = Web3(Web3.HTTPProvider(ETH_RPC))
            latest_eth = w3e.eth.block_number
            from_block_eth = max(_last_scanned_block_eth + 1, latest_eth - 30)  # ~6 min ETH blocks
            if from_block_eth <= latest_eth:
                transfers = _scan_eth_usdc_transfers(from_block_eth, latest_eth)
                _last_scanned_block_eth = latest_eth
                for t in transfers:
                    pid = _match_transfer_to_payment(t, payments)
                    if pid:
                        _fulfill_payment(pid, t["tx_hash"], t.get("block", 0))
                        fulfilled.append(pid)
                        payments = _load_payments()
        except Exception as e:
            print(f"[AgentPay] ETH scan error: {e}")

    # ── Bitcoin ───────────────────────────────────────────────────────────────
    if "btc" in pending_chains:
        try:
            btc_txs = _scan_btc_transfers()
            for t in btc_txs:
                pid = _match_transfer_to_payment(t, payments)
                if pid:
                    _fulfill_payment(pid, t["tx_hash"], 0)
                    fulfilled.append(pid)
                    payments = _load_payments()
        except Exception as e:
            print(f"[AgentPay] BTC scan error: {e}")

    # ── Expire stale payments ─────────────────────────────────────────────────
    payments = _load_payments()
    changed  = False
    for pid, p in payments.items():
        if p["status"] == "pending" and _is_expired(p):
            p["status"] = "expired"
            changed = True
    if changed:
        _save_payments(payments)

    return fulfilled


def get_payment_status(payment_id: str) -> dict:
    """
    Return current status of a payment intent.
    Called by agent polling /v1/agent-checkout/status
    """
    payments = _load_payments()
    p = payments.get(payment_id)
    if not p:
        return {"status": "not_found", "payment_id": payment_id}

    # Trigger a scan on every status poll (lazy scan)
    if p["status"] == "pending":
        scan_for_payments()
        payments = _load_payments()
        p = payments.get(payment_id, p)

    if _is_expired(p) and p["status"] == "pending":
        return {
            "status":     "expired",
            "payment_id": payment_id,
            "message":    "Payment window expired. Create a new payment intent.",
        }

    response = {
        "status":      p["status"],
        "payment_id":  payment_id,
        "product":     p["product"],
        "amount_usd":  p.get("amount_usd") or p.get("amount_usdc"),
    }

    if p["status"] == "fulfilled":
        response["tx_hash"]     = p.get("tx_hash")
        response["fulfilled_at"] = p.get("fulfilled_at")
        if p.get("api_key"):
            response["api_key"]  = p["api_key"]
            response["tier"]     = "premium"
            response["header"]   = f"X-OctoData-Key: {p['api_key']}"
            response["limits"]   = {"req_per_day": 10000, "req_per_minute": 200}
            response["docs"]     = "https://api.octodamus.com/docs"
        if p.get("download_url"):
            response["download_url"] = p["download_url"]

    return response

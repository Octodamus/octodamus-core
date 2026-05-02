"""
octo_listing_health.py
Round-trip listing health check — runs every 6 hours via Task Scheduler.
Checks every service Octodamus is registered on, pulls traffic/sales data,
and emails a report to octodamusai@gmail.com.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

# ── Config ────────────────────────────────────────────────────────────────────

SECRETS     = json.loads(Path(".octo_secrets").read_text(encoding="utf-8"))
ORBIS_KEY   = SECRETS.get("ORBIS_API_KEY", "")
TREASURY    = "0x5c6B3a3dAe296d3cef50fef96afC73410959a6Db"
USDC_ADDR   = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
BASE_RPC    = "https://mainnet.base.org"
ORBIS_ID    = "cf84d8d1-2ac0-48c4-9415-e1ecc68f72fe"
ORBIS_SLUG  = "octodamus-market-intelligence-api-d45c4d"

LISTINGS = [
    {
        "name":  "Orbis Marketplace",
        "url":   f"https://orbisapi.com/apis/{ORBIS_ID}",
        "check": "https://orbisapi.com/apis/cf84d8d1-2ac0-48c4-9415-e1ecc68f72fe",
        "type":  "orbis",
    },
    {
        "name":  "Smithery MCP",
        "url":   "https://smithery.ai/server/octodamusai/market-intelligence",
        "check": "https://smithery.ai/server/octodamusai/market-intelligence",
        "type":  "http",
    },
    {
        "name":  "x402.json Discovery",
        "url":   "https://api.octodamus.com/.well-known/x402.json",
        "check": "https://api.octodamus.com/.well-known/x402.json",
        "type":  "http",
    },
    {
        "name":  "ERC-8004 Agent Card",
        "url":   "https://api.octodamus.com/.well-known/agent.json",
        "check": "https://api.octodamus.com/.well-known/agent.json",
        "type":  "http",
    },
    {
        "name":  "API Server (demo)",
        "url":   "https://api.octodamus.com/v2/demo",
        "check": "https://api.octodamus.com/v2/demo",
        "type":  "http",
    },
    {
        "name":  "octodamus.com",
        "url":   "https://octodamus.com",
        "check": "https://octodamus.com",
        "type":  "http",
    },
    {
        "name":  "Ben Divergence Brief (402 gate)",
        "url":   "https://api.octodamus.com/v2/ben/bens_crypto_divergence_brief",
        "check": "https://api.octodamus.com/v2/ben/bens_crypto_divergence_brief",
        "type":  "expect_402",
    },
    {
        "name":  "Agent Signal (402 gate)",
        "url":   "https://api.octodamus.com/v2/x402/agent-signal",
        "check": "https://api.octodamus.com/v2/x402/agent-signal",
        "type":  "expect_402",
    },
]


# ── Checks ────────────────────────────────────────────────────────────────────

def check_http(url: str, expect_402: bool = False) -> dict:
    try:
        r = httpx.get(url, timeout=10, follow_redirects=True)
        ok = r.status_code == (402 if expect_402 else 200)
        return {"status": r.status_code, "ok": ok, "latency_ms": int(r.elapsed.total_seconds() * 1000)}
    except Exception as e:
        return {"status": 0, "ok": False, "latency_ms": 0, "error": str(e)[:80]}


def check_orbis() -> dict:
    try:
        h = {"x-api-key": ORBIS_KEY}
        stats_r = httpx.get("https://orbisapi.com/api/provider/stats", headers=h, timeout=10)
        apis_r  = httpx.get("https://orbisapi.com/api/provider/apis",  headers=h, timeout=10)
        stats   = stats_r.json() if stats_r.status_code == 200 else {}
        apis    = apis_r.json()  if apis_r.status_code  == 200 else {}
        listing = next((a for a in apis.get("apis", []) if a.get("id") == ORBIS_ID), {})
        page_r  = httpx.get(f"https://orbisapi.com/apis/{ORBIS_ID}", timeout=10, follow_redirects=True)
        return {
            "ok":           page_r.status_code == 200,
            "status":       page_r.status_code,
            "latency_ms":   int(page_r.elapsed.total_seconds() * 1000),
            "total_calls":  stats.get("totalCalls", listing.get("callCount", 0)),
            "subscribers":  stats.get("totalSubscribers", listing.get("subscriberCount", 0)),
            "active":       listing.get("isActive", False),
            "verified":     listing.get("isVerified", False),
            "featured":     listing.get("isFeatured", False),
        }
    except Exception as e:
        return {"ok": False, "status": 0, "latency_ms": 0, "error": str(e)[:80]}


def check_wallet() -> dict:
    try:
        def rpc(method, params):
            r = httpx.post(BASE_RPC, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=8)
            return r.json().get("result")

        padded   = TREASURY[2:].lower().zfill(64)
        hex_usdc = rpc("eth_call", [{"to": USDC_ADDR, "data": "0x70a08231" + padded}, "latest"])
        usdc     = round(int(hex_usdc, 16) / 1e6, 4)
        hex_eth  = rpc("eth_getBalance", [TREASURY, "latest"])
        eth      = round(int(hex_eth, 16) / 1e18, 6)
        return {"usdc": usdc, "eth": eth, "ok": True}
    except Exception as e:
        return {"usdc": 0, "eth": 0, "ok": False, "error": str(e)[:80]}


# ── State (track balance changes between runs) ────────────────────────────────

STATE_FILE = Path("data/listing_health_state.json")

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}

def save_state(state: dict):
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


# ── Report builder ────────────────────────────────────────────────────────────

def status_icon(ok: bool) -> str:
    return "OK" if ok else "DOWN"


def build_report(results: list, orbis: dict, wallet: dict, prev_state: dict) -> str:
    now      = datetime.now(timezone.utc)
    ts       = now.strftime("%A, %B %d %Y %I:%M %p UTC")
    prev_bal = prev_state.get("usdc", wallet["usdc"])
    earned   = round(wallet["usdc"] - prev_bal, 4)
    earned_s = f"+${earned:.4f}" if earned > 0 else (f"-${abs(earned):.4f}" if earned < 0 else "$0.00")

    lines = [
        f"Octodamus Listing Health Report",
        f"{ts}",
        f"",
        f"WALLET",
        f"  USDC: ${wallet['usdc']:.4f}  |  ETH: {wallet['eth']:.6f}",
        f"  Since last check: {earned_s}",
        f"  Treasury: {TREASURY}",
        f"",
        f"ORBIS MARKETPLACE",
        f"  Listing: {'LIVE' if orbis.get('active') else 'INACTIVE'}  |  {status_icon(orbis.get('ok', False))}  ({orbis.get('latency_ms', 0)}ms)",
        f"  Total calls: {orbis.get('total_calls', 0)}  |  Subscribers: {orbis.get('subscribers', 0)}",
        f"  Verified: {'Yes' if orbis.get('verified') else 'No'}  |  Featured: {'Yes' if orbis.get('featured') else 'No'}",
        f"  URL: https://orbisapi.com/apis/{ORBIS_ID}",
        f"",
        f"LISTING ENDPOINTS",
    ]

    for r in results:
        icon  = status_icon(r["ok"])
        lat   = f"{r.get('latency_ms', 0)}ms"
        code  = r.get("status", 0)
        err   = f"  ERROR: {r['error']}" if r.get("error") else ""
        lines.append(f"  [{icon}] {r['name']:<35} HTTP {code}  {lat}{err}")

    all_ok = all(r["ok"] for r in results) and orbis.get("ok", False) and wallet["ok"]
    lines += [
        f"",
        f"OVERALL: {'ALL SYSTEMS GO' if all_ok else 'ISSUES DETECTED — review above'}",
        f"",
        f"LINKS",
        f"  Orbis:    https://orbisapi.com/apis/{ORBIS_ID}",
        f"  Smithery: https://smithery.ai/server/octodamusai/market-intelligence",
        f"  API docs: https://api.octodamus.com/docs",
        f"  Website:  https://octodamus.com",
        f"",
        f"-- Octodamus Listing Health Monitor",
    ]
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    print("Running listing health check...")

    results = []
    for listing in LISTINGS:
        t    = listing["type"]
        name = listing["name"]
        if t == "orbis":
            continue  # handled separately
        elif t == "expect_402":
            r = check_http(listing["check"], expect_402=True)
        else:
            r = check_http(listing["check"])
        r["name"] = name
        r["url"]  = listing["url"]
        results.append(r)
        print(f"  {name}: {r['status']} {'OK' if r['ok'] else 'FAIL'}")

    orbis  = check_orbis()
    wallet = check_wallet()
    print(f"  Orbis: {'OK' if orbis['ok'] else 'FAIL'} | calls={orbis.get('total_calls',0)} subs={orbis.get('subscribers',0)}")
    print(f"  Wallet: ${wallet['usdc']:.4f} USDC")

    prev_state = load_state()
    report     = build_report(results, orbis, wallet, prev_state)

    save_state({
        "usdc":      wallet["usdc"],
        "eth":       wallet["eth"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "orbis_calls": orbis.get("total_calls", 0),
    })

    from octo_notify import _send
    all_ok  = all(r["ok"] for r in results) and orbis.get("ok", False)
    subject = f"Octodamus Listing Health — {'ALL OK' if all_ok else 'ISSUES'} | ${wallet['usdc']:.2f} USDC"
    _send(subject, report)
    print(f"Report sent: {subject}")


if __name__ == "__main__":
    run()

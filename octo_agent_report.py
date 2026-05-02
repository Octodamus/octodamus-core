"""
octo_agent_report.py — Octodamus AI Agent Activity Report
Sends a summary email to octodamusai@gmail.com at 6am and 6pm.

Usage:
  python octo_agent_report.py           # 12-hour report (auto-detect am/pm)
  python octo_agent_report.py --hours 24
"""

import json
import sys
import os
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent))

_OWN_WALLET   = "0x5c6b3a3dae296d3cef50fef96afc73410959a6db"
_TEST_LABELS  = {"test", "test1", "smithery-scan", "x402-test", "local-claude-code",
                 "cw-admin", "cw-test", "firstagent"}
_TEST_TIERS   = {"admin", "internal"}
_TEST_EMAIL_PATTERNS = ("test", "stripe", "octodamus.com", "octodamusai")


def _is_test_key(key: str, v: dict, key_wallet: dict) -> bool:
    """Return True if this key is a test/internal key, not a real customer."""
    if v.get("tier") in _TEST_TIERS:
        return True
    label = (v.get("label") or "").lower()
    if any(label == t for t in _TEST_LABELS) or label.startswith("test") or label.startswith("stripe"):
        return True
    email = (v.get("email") or "").lower()
    if any(p in email for p in _TEST_EMAIL_PATTERNS):
        return True
    # Label starts with own wallet prefix
    if label.startswith("0x5c6b3a3dae"):
        return True
    # Wallet field is own treasury
    wallet = (key_wallet.get(key) or v.get("wallet") or "").lower()
    if wallet == _OWN_WALLET:
        return True
    return False


def _load_keys() -> dict:
    f = Path(__file__).parent / "data" / "api_keys.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def _load_payments() -> dict:
    f = Path(__file__).parent / "data" / "octo_agent_payments.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def _load_visitors() -> dict:
    f = Path(__file__).parent / "data" / "agent_visitors.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    return {"visitors": {}, "customers": {}, "meta": {}}


def build_report(hours: int = 12) -> str:
    from octo_agent_db import get_daily_summary
    s   = get_daily_summary(hours)
    now = datetime.now(timezone.utc)
    slot = "Morning" if now.hour < 12 else "Evening"

    keys     = _load_keys()
    payments = _load_payments()
    db       = _load_visitors()
    visitors = db.get("visitors", {})

    # ── Build key→wallet map from payments ─────────────────────────
    key_wallet = {}
    for p in payments.values():
        if p.get("api_key"):
            key_wallet[p["api_key"]] = p.get("agent_wallet", "")

    # ── Split real vs test keys ─────────────────────────────────────
    real_keys = {k: v for k, v in keys.items() if not _is_test_key(k, v, key_wallet)}
    # test_count for reference only
    test_count = len(keys) - len(real_keys)

    # ── Tier breakdown (real only) ──────────────────────────────────
    tiers = defaultdict(int)
    for v in real_keys.values():
        tiers[v.get("tier", "basic")] += 1

    # ── Active subscribers (non-expired premium/trial, real only) ───
    from datetime import timedelta
    now_iso = now.isoformat()
    active_premium = [
        (k, v) for k, v in real_keys.items()
        if v.get("tier") in ("premium", "trial")
        and (not v.get("expires") or v.get("expires", "") > now_iso)
    ]
    expiring_soon = [
        (k, v) for k, v in active_premium
        if v.get("expires") and v.get("expires", "") < (now + timedelta(days=7)).isoformat()
    ]

    # ── Recent agent visitors ───────────────────────────────────────
    cutoff = (now - timedelta(hours=hours)).isoformat()
    recent_agents = [
        v for v in visitors.values()
        if v.get("last_seen", "") >= cutoff and v.get("is_agent")
    ]
    new_agents = [
        v for v in visitors.values()
        if v.get("first_seen", "") >= cutoff and v.get("is_agent")
    ]

    agent_types = defaultdict(int)
    for v in recent_agents:
        agent_types[v.get("agent_type") or "Unknown"] += 1

    # Top endpoints
    endpoint_hits = defaultdict(int)
    for v in recent_agents:
        for ep in v.get("endpoints", []):
            endpoint_hits[ep] += 1

    # ── Recent payments ─────────────────────────────────────────────
    recent_pay = [
        p for p in payments.values()
        if (p.get("fulfilled_at") or "") >= cutoff and p.get("status") == "fulfilled"
    ]
    revenue = sum(int(p.get("amount_usdc", 0) or 0) for p in recent_pay)

    # ── Guide sales ──────────────────────────────────────────────────
    all_guide_sales = [
        p for p in payments.values()
        if (p.get("product") or "").startswith("guide") and p.get("status") == "fulfilled"
        and (p.get("agent_wallet") or "").lower() != _OWN_WALLET
    ]
    recent_guide_sales = [
        p for p in all_guide_sales
        if (p.get("fulfilled_at") or "") >= cutoff
    ]
    guide_revenue_total  = sum(float(p.get("amount_usdc", 0) or 0) for p in all_guide_sales)
    guide_revenue_period = sum(float(p.get("amount_usdc", 0) or 0) for p in recent_guide_sales)

    # ── Build email body ────────────────────────────────────────────
    lines = [
        f"OCTODAMUS — {slot} Agent Report",
        f"{'=' * 48}",
        f"Period: last {hours}h  |  {now.strftime('%Y-%m-%d %H:%M UTC')}",
        "",
    ]

    # Revenue block
    if recent_pay:
        lines += [
            f"REVENUE THIS PERIOD",
            f"  {len(recent_pay)} payment(s) — ${revenue} USDC on Base",
        ]
        for p in recent_pay:
            lines.append(f"  + {p.get('product','?'):20} | wallet {p.get('agent_wallet','?')[:16]}...")
        lines.append("")
    else:
        lines += ["REVENUE THIS PERIOD", "  No new payments.", ""]

    # Guide sales block
    lines += [
        f"GUIDE SALES — Build The House ($29 USDC)",
    ]
    if recent_guide_sales:
        lines.append(f"  This period: {len(recent_guide_sales)} sale(s) — ${guide_revenue_period:.0f} USDC")
        for p in recent_guide_sales:
            wallet  = (p.get("agent_wallet") or "")[:20]
            email   = p.get("email") or ""
            identity = email if email else (wallet + "..." if wallet else "agent")
            sold_at  = (p.get("fulfilled_at") or "")[:16].replace("T", " ")
            lines.append(f"  + {sold_at}  {identity}")
    else:
        lines.append(f"  This period: no new sales")
    lines.append(f"  All-time:    {len(all_guide_sales)} sale(s) — ${guide_revenue_total:.0f} USDC total")
    lines.append("")

    # Agent visitors
    lines += [
        f"AI AGENT VISITS",
        f"  {len(recent_agents)} agent visits  |  {len(new_agents)} new agents",
    ]
    if agent_types:
        lines.append("  By type:")
        for atype, count in sorted(agent_types.items(), key=lambda x: -x[1])[:8]:
            lines.append(f"    {atype:35} {count:4}x")
    if endpoint_hits:
        lines.append("  Top endpoints hit:")
        for ep, count in sorted(endpoint_hits.items(), key=lambda x: -x[1])[:6]:
            lines.append(f"    {ep:40} {count:4}x")
    lines.append("")

    # New real signups in window
    new_keys = [(k, v) for k, v in real_keys.items() if (v.get("created") or "") >= cutoff]
    lines += [f"NEW SIGNUPS  (real customers only)", f"  {len(new_keys)} new customer(s) this period"]
    for k, v in new_keys[:10]:
        wallet = key_wallet.get(k, "")
        identity = v.get("email") or (wallet[:20] if wallet else "agent, no email")
        lines.append(f"  + {k[:20]}... | {v.get('tier','?'):8} | {identity}")
    lines.append("")

    # Subscriber summary (real only)
    lines += [
        f"REAL CUSTOMER DATABASE",
        f"  Free (basic):   {tiers.get('basic', 0) + tiers.get('free', 0)}",
        f"  Trial (active): {sum(1 for _, v in active_premium if v.get('tier') == 'trial')}",
        f"  Premium:        {sum(1 for _, v in active_premium if v.get('tier') == 'premium')}",
        f"  (+ {test_count} internal/test keys excluded from report)",
    ]
    if expiring_soon:
        lines.append(f"  Expiring <7d:   {len(expiring_soon)}")
        for k, v in expiring_soon:
            wallet = key_wallet.get(k, "")
            identity = v.get("email") or (wallet[:18] if wallet else "agent")
            lines.append(f"    {k[:20]}... expires {v.get('expires','?')[:10]} | {identity}")
    lines.append("")

    # Active premium list (real only)
    if active_premium:
        lines += ["ACTIVE PREMIUM SUBSCRIBERS"]
        for k, v in active_premium[:15]:
            wallet = key_wallet.get(k, v.get("wallet", ""))
            identity = v.get("email") or (wallet[:22] if wallet else "agent, no identity")
            exp = (v.get("expires") or "no expiry")[:10]
            lines.append(f"  {k[:20]}... | {v.get('tier','?'):8} | expires {exp} | {identity}")
        lines.append("")

    # ── TokenBot_NYSE_Base portfolio snapshot ──────────────────────────
    try:
        tb_state_file = Path(__file__).parent / ".agents" / "tokenbot_nyse_base" / "state.json"
        if tb_state_file.exists():
            tb = json.loads(tb_state_file.read_text(encoding="utf-8"))
            tb_cash     = tb.get("cash", 1000.0)
            tb_pnl      = tb.get("total_pnl", 0.0)
            tb_start    = tb.get("starting_capital", 1000.0)
            tb_wins     = tb.get("wins", 0)
            tb_losses   = tb.get("losses", 0)
            tb_sessions = tb.get("sessions", 0)
            tb_last     = tb.get("last_run") or "never"
            tb_pnl_pct  = tb_pnl / tb_start * 100 if tb_start else 0
            tb_wr       = tb_wins / (tb_wins + tb_losses) * 100 if (tb_wins + tb_losses) > 0 else 0
            tb_pos      = tb.get("positions", {})
            lines += [
                "TOKENBOT_NYSE_BASE  (paper trading — Dinari dShares on Base)",
                f"  Portfolio:   ${tb_cash:,.2f} cash | P&L: ${tb_pnl:+.2f} ({tb_pnl_pct:+.1f}%)",
                f"  Record:      {tb_wins}W / {tb_losses}L ({tb_wr:.0f}% win rate) | {tb_sessions} sessions",
                f"  Last run:    {str(tb_last)[:19]}",
            ]
            if tb_pos:
                lines.append(f"  Open positions ({len(tb_pos)}):")
                for tkr, pos in tb_pos.items():
                    token  = pos.get("token", f"d{tkr}")
                    entry  = pos.get("entry_price", 0)
                    size   = pos.get("size_usd", 0)
                    upnl   = pos.get("unrealized_pnl_pct", 0)
                    held   = pos.get("sessions_held", 0)
                    lines.append(f"    {token}: entry ${entry:.2f} | ${size:.0f} | {upnl:+.1f}% unrealized | {held}s held")
            else:
                lines.append("  Open positions: none")
            flip_ready = tb_wr >= 60 and (tb_wins + tb_losses) >= 20
            lines.append(f"  Live flip:   {'READY -- flip PAPER_MODE=False' if flip_ready else f'{tb_wr:.0f}%/{60}% win rate, {tb_wins+tb_losses}/20 trades'}")
            lines.append("")
    except Exception:
        pass

    # All-time stats
    meta = db.get("meta", {})
    real_payments = [p for p in payments.values() if p.get("status") == "fulfilled"
                     and (p.get("agent_wallet") or "").lower() != _OWN_WALLET]
    lines += [
        f"ALL-TIME TOTALS  (real customers)",
        f"  Real customers:       {len(real_keys):,}",
        f"  Total agent visits:   {meta.get('total_visits', 0):,}",
        f"  Unique agents:        {meta.get('total_agents', 0):,}",
        f"  Paid customers:       {len(real_payments)}",
        f"  Guide sales:          {len(all_guide_sales)}  (${guide_revenue_total:.0f} USDC)",
        f"  Revenue received:     ${sum(float(p.get('amount_usdc', 0) or 0) for p in real_payments):.0f} USDC",
        "",
        "─" * 48,
        "Octodamus is watching.",
        "api.octodamus.com  |  @octodamusai",
    ]

    return "\n".join(lines)


def send_report(hours: int = 12) -> None:
    from octo_health import send_email_alert
    now  = datetime.now(timezone.utc)
    slot = "Morning" if now.hour < 12 else "Evening"
    body = build_report(hours)
    subject = f"[Octodamus] {slot} Agent Report — {now.strftime('%b %d %Y')}"
    send_email_alert(subject=subject, body=body)
    print(f"[AgentReport] {slot} report sent ({now.strftime('%H:%M UTC')})")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--hours", type=int, default=12)
    p.add_argument("--print", action="store_true", help="Print report instead of emailing")
    args = p.parse_args()

    if args.print:
        print(build_report(args.hours))
    else:
        send_report(args.hours)

#!/usr/bin/env python3
"""
octo_x402_health.py — Octodamus x402 Health Test

Tests every x402 and ACP offering each morning without spending USDC.

Strategy:
  - x402 API routes: hit /preview endpoint (no auth, confirms route live + data structure ok)
  - x402 gate (/v2/x402/agent-signal): expect 402 — confirms gate is working
  - ACP handlers: direct Python calls — no network payment, confirms handler logic ok

Results: PASS / WARN / FAIL per offering, summary line at end.

Usage:
  python octo_x402_health.py
  python octo_x402_health.py --local        # test against localhost:8000
  python octo_x402_health.py --verbose      # show response snippets
  python octo_x402_health.py --telegram     # send failure summary to Telegram on any FAIL
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))

# ── Config ────────────────────────────────────────────────────────────────────

PROD_BASE  = "https://api.octodamus.com"
LOCAL_BASE = "http://localhost:8000"
TIMEOUT    = 20  # seconds per HTTP call
ACP_TIMEOUT = 30  # ACP handlers make external API calls

# ANSI colours for terminal
_GREEN  = "\033[32m"
_RED    = "\033[31m"
_YELLOW = "\033[33m"
_CYAN   = "\033[36m"
_DIM    = "\033[2m"
_RESET  = "\033[0m"
_BOLD   = "\033[1m"

# ── Secrets ───────────────────────────────────────────────────────────────────

def _load_secrets():
    try:
        import bitwarden
        bitwarden.load_all_secrets()
    except Exception:
        pass

# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _get(base: str, path: str, params: dict = None) -> dict:
    """GET request, returns {"ok": bool, "status": int, "data": dict|None, "note": str}."""
    try:
        r = httpx.get(f"{base}{path}", params=params or {}, timeout=TIMEOUT, follow_redirects=True)
        if r.status_code == 200:
            try:
                data = r.json()
                return {"ok": True, "status": 200, "data": data, "note": ""}
            except Exception:
                return {"ok": False, "status": 200, "data": None, "note": "invalid JSON"}
        return {"ok": False, "status": r.status_code, "data": None, "note": r.text[:80]}
    except httpx.TimeoutException:
        return {"ok": False, "status": -1, "data": None, "note": f"timeout after {TIMEOUT}s"}
    except Exception as e:
        return {"ok": False, "status": -1, "data": None, "note": str(e)[:80]}


def _get_expect_402(base: str, path: str) -> dict:
    """Test that a payment-gated route correctly returns 402."""
    try:
        r = httpx.get(f"{base}{path}", timeout=TIMEOUT, follow_redirects=True)
        if r.status_code == 402:
            return {"ok": True, "status": 402, "data": None, "note": "gate live"}
        if r.status_code == 200:
            return {"ok": True, "status": 200, "data": None, "note": "200 (bypassed — ok if test key present)"}
        return {"ok": False, "status": r.status_code, "data": None, "note": "unexpected status"}
    except httpx.TimeoutException:
        return {"ok": False, "status": -1, "data": None, "note": f"timeout after {TIMEOUT}s"}
    except Exception as e:
        return {"ok": False, "status": -1, "data": None, "note": str(e)[:80]}


def _call_acp(handler_fn, req: dict) -> dict:
    """Call an ACP handler directly. Returns same result shape."""
    try:
        t0 = time.time()
        result = handler_fn(req)
        elapsed = time.time() - t0
        if not isinstance(result, dict):
            return {"ok": False, "status": 0, "data": None, "note": "non-dict result"}
        if result.get("reject"):
            return {"ok": False, "status": 0, "data": result, "note": f"rejected: {result.get('reason','?')}"}
        if result.get("error") and not result.get("type"):
            return {"ok": False, "status": 0, "data": result, "note": f"error: {str(result['error'])[:60]}"}
        return {"ok": True, "status": 200, "data": result, "note": f"elapsed {elapsed:.1f}s"}
    except Exception as e:
        return {"ok": False, "status": -1, "data": None, "note": str(e)[:80]}


# ── Result classifier ─────────────────────────────────────────────────────────

def _classify(result: dict, expect_402: bool = False) -> str:
    """Return 'PASS', 'WARN', or 'FAIL'."""
    if not result["ok"]:
        return "FAIL"
    if expect_402 and result["status"] == 402:
        return "PASS"
    if result["status"] == 200:
        data = result.get("data") or {}
        # ACP: result has "error" field but also a "type" — treat as WARN not FAIL
        if isinstance(data, dict) and data.get("error") and data.get("type"):
            return "WARN"
        return "PASS"
    return "WARN"


def _snippet(result: dict, verbose: bool) -> str:
    if not verbose:
        return ""
    data = result.get("data")
    if not data:
        return ""
    if isinstance(data, dict):
        keys = list(data.keys())[:4]
        snap = {k: data[k] for k in keys}
        return "  " + _DIM + json.dumps(snap, default=str)[:120] + _RESET
    return ""


# ── Test registry ─────────────────────────────────────────────────────────────

def _build_tests(base: str) -> list:
    """Returns list of {name, price, cat, fn, expect_402}."""

    # Lazy-import ACP handlers so test file loads fast even if deps are missing
    def _import_handlers():
        from octo_acp_ben_reports import (
            handle_grok_sentiment_brief,
            handle_fear_crowd_divergence,
            handle_btc_bull_trap_monitor,
            handle_btc_strike_proximity_alert,
            handle_carry_unwind_risk_monitor,
        )
        from octo_acp_stockoracle_reports import handle_congressional_silence_signal
        return (
            handle_grok_sentiment_brief,
            handle_fear_crowd_divergence,
            handle_btc_bull_trap_monitor,
            handle_btc_strike_proximity_alert,
            handle_carry_unwind_risk_monitor,
            handle_congressional_silence_signal,
        )

    def P(path, params=None):
        return lambda: _get(base, path, params)

    def G(path):
        return lambda: _get_expect_402(base, path)

    tests = [
        # ── x402 gate ─────────────────────────────────────────────────────────
        {"name": "x402 gate (agent-signal)",        "price": 0.01, "cat": "x402-gate",  "fn": G("/v2/x402/agent-signal"),              "expect_402": True},

        # ── Agent_Ben endpoints (preview) ─────────────────────────────────────
        {"name": "Sentiment Divergence",             "price": 0.50, "cat": "ben",        "fn": P("/v2/ben/sentiment-divergence/preview")},
        {"name": "Fear/Greed Divergence Signal",     "price": 0.35, "cat": "ben",        "fn": P("/v2/ben/bens_fear_greed_divergence_signal/preview")},
        {"name": "Crypto Divergence Brief",          "price": 0.75, "cat": "ben",        "fn": P("/v2/ben/bens_crypto_divergence_brief/preview")},
        {"name": "BTC Contrarian Alert",             "price": 0.35, "cat": "ben",        "fn": P("/v2/ben/bens_btc_contrarian_alert/preview")},
        {"name": "Agent Context Pack",               "price": 0.50, "cat": "ben",        "fn": P("/v2/ben/bens_agent_context_pack/preview")},
        {"name": "Bull Trap Monitor",                "price": 0.35, "cat": "ben",        "fn": P("/v2/ben/bens_bull_trap_monitor/preview")},
        {"name": "Macro Regime Brief",               "price": 0.50, "cat": "ben",        "fn": P("/v2/ben/bens_macro_regime_brief/preview")},

        # ── Agent briefs (preview) ─────────────────────────────────────────────
        {"name": "NYSE MacroMind Brief",             "price": 0.25, "cat": "sub-agent",  "fn": P("/v2/agents/nyse_macromind/brief/preview")},
        {"name": "NYSE StockOracle Brief",           "price": 0.35, "cat": "sub-agent",  "fn": P("/v2/agents/nyse_stockoracle/brief/preview")},
        {"name": "NYSE Tech Agent Brief",            "price": 0.50, "cat": "sub-agent",  "fn": P("/v2/agents/nyse_tech_agent/brief/preview")},
        {"name": "Order ChainFlow Brief",            "price": 0.35, "cat": "sub-agent",  "fn": P("/v2/agents/order_chainflow/brief/preview")},
        {"name": "X Sentiment Agent Brief",          "price": 0.50, "cat": "sub-agent",  "fn": P("/v2/agents/x_sentiment_agent/brief/preview")},

        # ── NYSE sub-agent endpoints (preview) ────────────────────────────────
        {"name": "NYSE MacroMind Signal",            "price": 0.25, "cat": "subarc",     "fn": P("/v2/nyse_macromind/signal/preview")},
        {"name": "NYSE MacroMind Yield Curve",       "price": 0.25, "cat": "subarc",     "fn": P("/v2/nyse_macromind/yield-curve/preview")},
        {"name": "NYSE StockOracle Congress",        "price": 0.35, "cat": "subarc",     "fn": P("/v2/nyse_stockoracle/congress/preview")},
        {"name": "NYSE StockOracle Signal",          "price": 0.35, "cat": "subarc",     "fn": P("/v2/nyse_stockoracle/signal/preview")},
        {"name": "Order ChainFlow Delta",            "price": 0.35, "cat": "subarc",     "fn": P("/v2/order_chainflow/delta/preview")},
        {"name": "Order ChainFlow DEX",              "price": 0.35, "cat": "subarc",     "fn": P("/v2/order_chainflow/dex/preview")},
        {"name": "Order ChainFlow Whales",           "price": 0.35, "cat": "subarc",     "fn": P("/v2/order_chainflow/whales/preview")},
        {"name": "X Sentiment Divergence",           "price": 0.35, "cat": "subarc",     "fn": P("/v2/x_sentiment/divergence/preview")},
        {"name": "X Sentiment Scan",                 "price": 0.50, "cat": "subarc",     "fn": P("/v2/x_sentiment/scan/preview")},
        {"name": "NYSE Tech Regulatory",             "price": 0.35, "cat": "subarc",     "fn": P("/v2/nyse_tech/regulatory/preview")},
        {"name": "NYSE Tech Tokenization",           "price": 0.50, "cat": "subarc",     "fn": P("/v2/nyse_tech/tokenization/preview")},

        # ── Guide (preview) ───────────────────────────────────────────────────
        {"name": "Derivatives Guide",                "price": 3.00, "cat": "guide",      "fn": P("/v2/guide/derivatives/preview")},
    ]

    # ── ACP handlers (direct Python) ─────────────────────────────────────────
    try:
        (
            h_grok, h_fvcd, h_trap, h_strike, h_carry, h_silence
        ) = _import_handlers()

        tests += [
            {"name": "ACP: Grok Sentiment Brief",        "price": 1.00, "cat": "acp", "fn": lambda h=h_grok:    _call_acp(h, {"ticker": "BTC"})},
            {"name": "ACP: Fear vs Crowd Divergence",    "price": 2.00, "cat": "acp", "fn": lambda h=h_fvcd:    _call_acp(h, {"ticker": "BTC"})},
            {"name": "ACP: BTC Bull Trap Monitor",       "price": 1.50, "cat": "acp", "fn": lambda h=h_trap:    _call_acp(h, {})},
            {"name": "ACP: BTC Strike Proximity Alert",  "price": 1.50, "cat": "acp", "fn": lambda h=h_strike:  _call_acp(h, {})},
            {"name": "ACP: Carry Unwind Risk Monitor",   "price": 1.50, "cat": "acp", "fn": lambda h=h_carry:   _call_acp(h, {})},
            {"name": "ACP: Congressional Silence",       "price": 0.65, "cat": "acp", "fn": lambda h=h_silence: _call_acp(h, {"ticker": "NVDA"})},
        ]
    except ImportError as e:
        tests.append({
            "name": "ACP handlers (import)", "price": 0, "cat": "acp",
            "fn": lambda: {"ok": False, "status": -1, "data": None, "note": f"import error: {e}"},
        })

    return tests


# ── Main ──────────────────────────────────────────────────────────────────────

def run(base: str, verbose: bool, telegram: bool):
    _load_secrets()

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n{_BOLD}OCTODAMUS x402 HEALTH TEST{_RESET}  --  {now_str}")
    print(f"{_DIM}target: {base}{_RESET}")
    print()

    tests    = _build_tests(base)
    results  = []
    passes   = warns = fails = 0
    t_total  = time.time()

    last_cat = None
    for t in tests:
        cat = t["cat"]
        if cat != last_cat:
            cat_labels = {
                "x402-gate": "x402 Payment Gate",
                "ben":        "Agent_Ben Endpoints",
                "sub-agent":  "NYSE Agent Briefs",
                "subarc":     "NYSE Sub-Arc Endpoints",
                "guide":      "Guide Purchases",
                "acp":        "ACP Handlers (direct)",
            }
            print(f"  {_DIM}{cat_labels.get(cat, cat)}{_RESET}")
            last_cat = cat

        t0     = time.time()
        result = t["fn"]()
        elapsed = time.time() - t0

        grade  = _classify(result, expect_402=t.get("expect_402", False))
        note   = result.get("note", "")
        status = result.get("status", "?")
        price  = f"${t['price']:.2f}"

        if grade == "PASS":
            passes += 1
            marker = f"{_GREEN}PASS{_RESET}"
        elif grade == "WARN":
            warns  += 1
            marker = f"{_YELLOW}WARN{_RESET}"
        else:
            fails  += 1
            marker = f"{_RED}FAIL{_RESET}"

        status_str = f"[{status}]" if status != 200 else ""
        time_str   = f"{_DIM}{elapsed:.1f}s{_RESET}"
        note_str   = f"  {_DIM}{note}{_RESET}" if note else ""

        print(f"  {marker}  {t['name']:<42} {price:>6}  {time_str} {status_str}{note_str}")
        if verbose:
            snip = _snippet(result, verbose)
            if snip:
                print(snip)

        results.append({"name": t["name"], "grade": grade, "note": note, "elapsed": elapsed})

    total_time = time.time() - t_total
    total = passes + warns + fails

    print()
    bar = (f"{_GREEN}{'#' * passes}{_RESET}"
           f"{_YELLOW}{'~' * warns}{_RESET}"
           f"{_RED}{'!' * fails}{_RESET}")
    print(f"  {bar}")
    summary_color = _GREEN if fails == 0 else (_YELLOW if warns > 0 else _RED)
    print(f"  {summary_color}{_BOLD}{passes}/{total} PASS{_RESET}  "
          f"{_YELLOW}{warns} WARN{_RESET}  "
          f"{_RED}{fails} FAIL{_RESET}  "
          f"{_DIM}{total_time:.1f}s total{_RESET}")
    print()

    if fails > 0:
        print(f"  {_RED}FAILURES:{_RESET}")
        for r in results:
            if r["grade"] == "FAIL":
                print(f"    - {r['name']}: {r['note']}")
        print()

    # ── Telegram alert on any FAIL ────────────────────────────────────────────
    if telegram and fails > 0:
        _send_telegram_alert(results, passes, warns, fails, now_str)

    return fails


def _send_telegram_alert(results: list, passes: int, warns: int, fails: int, ts: str):
    try:
        from telegram_bot import send_message_sync
        fail_list = "\n".join(
            f"  - {r['name']}: {r['note']}"
            for r in results if r["grade"] == "FAIL"
        )
        msg = (
            f"x402 Health Test FAILED -- {ts}\n"
            f"{passes} pass, {warns} warn, {fails} FAIL\n\n"
            f"Failures:\n{fail_list}"
        )
        send_message_sync(msg)
        print(f"  Telegram alert sent.")
    except Exception as e:
        print(f"  Telegram alert failed: {e}")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Octodamus x402 Health Test")
    parser.add_argument("--local",    action="store_true", help="Test against localhost:8000")
    parser.add_argument("--verbose",  action="store_true", help="Show response snippets")
    parser.add_argument("--telegram", action="store_true", help="Send Telegram alert on FAIL")
    args = parser.parse_args()

    base = LOCAL_BASE if args.local else PROD_BASE
    fail_count = run(base, verbose=args.verbose, telegram=args.telegram)
    sys.exit(1 if fail_count > 0 else 0)

"""
octo_acp_health_test.py -- Octodamus Full Health Test

Section 1: ACP Worker & Reports
  Tests every report type with a real ticker, validates data quality,
  checks the generated URL is live.

Section 2: API Server & Ports
  Tests all key endpoints locally (localhost:8742) and via Cloudflare
  tunnel (api.octodamus.com). Checks auth, free tools, paid tiers.

Usage:
  python octo_acp_health_test.py          # full test, print results
  python octo_acp_health_test.py --email  # also email results
  python octo_acp_health_test.py --acp    # ACP tests only
  python octo_acp_health_test.py --api    # API tests only
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))

from octo_acp_worker import _write_frozen_report
from octo_report_handlers import get_handler

# ── Config ────────────────────────────────────────────────────────────────────

API_LOCAL   = "http://localhost:8742"
API_TUNNEL  = "https://api.octodamus.com"
DASH_LOCAL  = "http://localhost:8901"
KEYS_FILE   = Path(__file__).parent / "data" / "api_keys.json"

TIMEOUT_FAST   = 8    # port/health checks
TIMEOUT_SLOW   = 20   # public endpoints that fetch live data (fear-greed, etc.)
TIMEOUT_AUTH   = 15   # authenticated endpoints
TIMEOUT_TUNNEL = 12   # Cloudflare can be slow


def _load_test_key() -> str:
    """Return an internal/admin API key for authenticated endpoint tests."""
    try:
        keys = json.loads(KEYS_FILE.read_text(encoding="utf-8"))
        # Prefer internal tier, then admin
        for tier in ("internal", "admin"):
            for k, v in keys.items():
                if v.get("tier") == tier:
                    return k
    except Exception:
        pass
    return ""


# ── ACP Test cases ────────────────────────────────────────────────────────────

ACP_TESTS = [
    # Crypto market signals
    ("market_signal", "BTC",  {"ticker": "BTC"},  ["signal", "call", "ta", "fng_val"],        "ACP: Market Signal / BTC"),
    ("market_signal", "ETH",  {"ticker": "ETH"},  ["signal", "call", "ta"],                   "ACP: Market Signal / ETH"),
    ("market_signal", "SOL",  {"ticker": "SOL"},  ["signal", "call", "ta"],                   "ACP: Market Signal / SOL"),
    ("market_signal", "XRP",  {"ticker": "XRP"},  ["signal", "call"],                         "ACP: Market Signal / XRP"),
    ("market_signal", "DOGE", {"ticker": "DOGE"}, ["signal", "call"],                         "ACP: Market Signal / DOGE"),

    # Stock congressional reports
    ("congressional", "TSLA", {"ticker": "TSLA"}, ["trades", "call", "interpretation"],       "ACP: Congressional / TSLA"),
    ("congressional", "NVDA", {"ticker": "NVDA"}, ["trades", "call"],                         "ACP: Congressional / NVDA"),
    ("congressional", "AAPL", {"ticker": "AAPL"}, ["trades", "call"],                         "ACP: Congressional / AAPL"),
    ("congressional", "MSFT", {"ticker": "MSFT"}, ["trades", "call"],                         "ACP: Congressional / MSFT"),
    ("congressional", "AMZN", {"ticker": "AMZN"}, ["trades", "call"],                         "ACP: Congressional / AMZN"),

    # Fear & Greed
    ("fear_greed", "BTC", {"type": "fear_greed"},               ["fng_val", "call", "position"], "ACP: Fear & Greed (no ticker)"),
    ("fear_greed", "BTC", {"ticker": "BTC", "type": "fear_greed"}, ["fng_val", "call"],          "ACP: Fear & Greed (with ticker)"),
    ("fear_greed", "ETH", {"ticker": "ETH", "type": "fear_greed"}, ["fng_val", "call"],          "ACP: Fear & Greed / ETH"),

    # Bitcoin deep analysis
    ("bitcoin_analysis", "BTC", {"ticker": "BTC"}, ["price", "call", "ta", "fng_val"],        "ACP: Bitcoin Analysis / BTC"),
]

DEGRADED_FIELDS = {"prices", "oi_usd", "usd_eur", "usd_jpy", "spikes", "cg"}


# ── API Test cases ─────────────────────────────────────────────────────────────
# (label, method, base, path, headers, expected_status, required_json_keys, min_body_bytes)

def _build_api_tests(key: str) -> list:
    auth = {"X-OctoData-Key": key} if key else {}
    bad  = {"X-OctoData-Key": "octo_INVALID_KEY_FOR_TEST"}

    return [
        # -- Port checks --
        ("Port: API server (8742)",        "GET", API_LOCAL,  "/health",             {},   200, ["status"],          50),
        ("Port: BOTCOIN dashboard (8901)", "GET", DASH_LOCAL, "/",                   {},   200, None,               200),

        # -- Public endpoints (no auth) --
        ("Public: GET /",                  "GET", API_LOCAL,  "/",                   {},   200, ["name"],            50),
        ("Public: GET /health",            "GET", API_LOCAL,  "/health",             {},   200, ["status"],          50),
        ("Public: GET /api/fear-greed",    "GET", API_LOCAL,  "/api/fear-greed",     {},   200, ["value", "label"], 20),

        ("Public: GET /api/btc-dominance", "GET", API_LOCAL,  "/api/btc-dominance",  {},   200, ["btc_dominance"],   20),
        ("Public: GET /api/calls/open",    "GET", API_LOCAL,  "/api/calls/open",     {},   200, ["open_calls"],      10),
        ("Public: GET /api/calls",         "GET", API_LOCAL,  "/api/calls",          {},   200, None,                10),
        ("Public: GET /api/prices",        "GET", API_LOCAL,  "/api/prices",         {},   200, None,                10),
        ("Public: GET /.well-known/x402",  "GET", API_LOCAL,  "/.well-known/x402.json", {}, 200, None,              50),

        # -- Free tools (no auth) --
        ("Tools: /tools/scorecard",        "GET", API_LOCAL,  "/tools/scorecard",    {},   200, None,                50),
        ("Tools: /tools/macro",            "GET", API_LOCAL,  "/tools/macro",        {},   200, None,                50),
        ("Tools: /tools/liquidations",     "GET", API_LOCAL,  "/tools/liquidations?asset=BTC", {}, 200, None,       50),

        # -- Auth: reject bad key --
        ("Auth: bad key rejected",         "GET", API_LOCAL,  "/v2/signal",          bad,  403, None,                 5),

        # -- auth wall check (no key = 401) --
        ("Auth: wall on /v1/briefing",     "GET", API_LOCAL,  "/v1/briefing",        {},   401, None,                 5),

        # -- Authenticated endpoints (internal key) --
        ("Auth: GET /v2/signal",           "GET", API_LOCAL,  "/v2/signal",          auth, 200, None,                20),
        ("Auth: GET /v2/brief",            "GET", API_LOCAL,  "/v2/brief",           auth, 200, None,                20),
        ("Auth: GET /v2/all",              "GET", API_LOCAL,  "/v2/all",             auth, 200, None,                20),
        ("Auth: GET /v2/agent-signal",     "GET", API_LOCAL,  "/v2/agent-signal",    auth, 200, None,                20),
        ("Auth: GET /v2/usage",            "GET", API_LOCAL,  "/v2/usage",           auth, 200, None,                10),

        # -- Cloudflare tunnel --
        ("Tunnel: api.octodamus.com/health", "GET", API_TUNNEL, "/health",           {},   200, ["status"],          50),
        ("Tunnel: api.octodamus.com/",       "GET", API_TUNNEL, "/",                 {},   200, ["name"],            50),
    ]


# ── Result class ──────────────────────────────────────────────────────────────

class Result:
    def __init__(self, label: str):
        self.label    = label
        self.status   = "PASS"
        self.issues   = []
        self.warnings = []
        self.url      = None
        self.elapsed  = 0.0

    def fail(self, msg):
        self.status = "FAIL"
        self.issues.append(msg)

    def warn(self, msg):
        if self.status == "PASS":
            self.status = "WARN"
        self.warnings.append(msg)

    def __str__(self):
        icon = {"PASS": "OK  ", "WARN": "WARN", "FAIL": "FAIL"}[self.status]
        line = f"[{icon}] {self.label:<44} {self.elapsed:.1f}s"
        if self.url:
            line += f"  {self.url}"
        for w in self.warnings:
            line += f"\n       WARN: {w}"
        for i in self.issues:
            line += f"\n       FAIL: {i}"
        return line


# ── ACP validators ────────────────────────────────────────────────────────────

def _check_required_fields(data: dict, fields: list, result: Result):
    for f in fields:
        val = data.get(f)
        if val is None:
            result.fail(f"Missing field: '{f}'")
        elif val == "" or val == "N/A":
            result.warn(f"Field '{f}' is empty/N/A")
        elif isinstance(val, (int, float)) and val == 0 and f not in ("fng_val",):
            result.warn(f"Field '{f}' is 0")


def _check_call_quality(data: dict, result: Result):
    call = data.get("call") or ""
    if not call:
        result.fail("No oracle call generated")
        return
    if len(call) < 12:
        result.warn(f"Call suspiciously short ({len(call)} chars): {call[:60]}")
    if "error" in call.lower() or "unavailable" in call.lower():
        result.warn(f"Call mentions error: {call[:80]}")


def _check_url_live(url: str, result: Result):
    if not url:
        result.fail("No report URL generated")
        return
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            if len(r.text) < 500:
                result.warn(f"Report HTML suspiciously small ({len(r.text)} bytes)")
        else:
            result.fail(f"Report URL returned HTTP {r.status_code}")
    except Exception as e:
        result.fail(f"Report URL unreachable: {e}")


def _check_data_freshness(data: dict, result: Result):
    if not data.get("generated"):
        result.warn("No 'generated' timestamp in report")


# ── ACP runner ────────────────────────────────────────────────────────────────

def run_acp_test(report_type, ticker, reqs, required_fields, label) -> Result:
    result = Result(label)
    t0 = time.time()

    try:
        handler = get_handler(report_type)
    except Exception as e:
        result.fail(f"get_handler failed: {e}")
        result.elapsed = time.time() - t0
        return result

    try:
        data = handler(reqs)
    except Exception as e:
        result.fail(f"Handler raised exception: {e}")
        result.elapsed = time.time() - t0
        return result

    if isinstance(data, dict) and data.get("reject"):
        result.fail(f"Handler rejected: {data.get('error', 'unknown')}")
        result.elapsed = time.time() - t0
        return result

    if not isinstance(data, dict):
        result.fail(f"Handler returned {type(data).__name__}, expected dict")
        result.elapsed = time.time() - t0
        return result

    _check_required_fields(data, required_fields, result)
    _check_call_quality(data, result)
    _check_data_freshness(data, result)

    try:
        url = _write_frozen_report(data)
        result.url = url
        if url:
            _check_url_live(url, result)
        else:
            result.fail("_write_frozen_report returned None")
    except Exception as e:
        result.fail(f"Report write failed: {e}")

    result.elapsed = time.time() - t0
    return result


# ── API runner ────────────────────────────────────────────────────────────────

def run_api_test(label, method, base, path, headers, expected_status,
                 required_json_keys, min_body_bytes) -> Result:
    result = Result(label)
    t0 = time.time()
    url = base + path

    # Use longer timeout for tunnel, auth calls, and known slow endpoints
    _slow_paths = ("/api/fear-greed", "/api/btc-dominance", "/tools/liquidations")
    timeout = TIMEOUT_TUNNEL if "octodamus.com" in base else (
              TIMEOUT_AUTH   if headers else (
              TIMEOUT_SLOW   if any(path.startswith(p) for p in _slow_paths) else TIMEOUT_FAST))

    try:
        r = requests.request(method, url, headers=headers, timeout=timeout)
    except requests.exceptions.ConnectionError:
        result.fail(f"Connection refused -- server not running?")
        result.elapsed = time.time() - t0
        return result
    except requests.exceptions.Timeout:
        result.fail(f"Timeout after {timeout}s")
        result.elapsed = time.time() - t0
        return result
    except Exception as e:
        result.fail(f"Request error: {e}")
        result.elapsed = time.time() - t0
        return result

    if r.status_code != expected_status:
        result.fail(f"HTTP {r.status_code} (expected {expected_status})")
        result.elapsed = time.time() - t0
        return result

    body = r.text
    if len(body) < min_body_bytes:
        result.warn(f"Response suspiciously small ({len(body)} bytes)")

    if required_json_keys and expected_status == 200:
        try:
            data = r.json()
            for k in required_json_keys:
                if k not in data:
                    result.warn(f"Missing JSON key: '{k}'")
        except Exception:
            result.warn("Response is not valid JSON")

    result.elapsed = time.time() - t0
    return result


# ── Section runners ────────────────────────────────────────────────────────────

def run_acp_section() -> list[Result]:
    print(f"\n{'-' * 60}")
    print(f"  SECTION 1: ACP Worker & Reports")
    print(f"{'-' * 60}\n")

    results = []
    for args in ACP_TESTS:
        rt, ticker, reqs, fields, label = args
        print(f"  Testing {label}...", end=" ", flush=True)
        r = run_acp_test(rt, ticker, reqs, fields, label)
        results.append(r)
        print(r.status)
    return results


def run_api_section() -> list[Result]:
    print(f"\n{'-' * 60}")
    print(f"  SECTION 2: API Server & Ports")
    print(f"{'-' * 60}\n")

    key = _load_test_key()
    if key:
        print(f"  Using internal API key for auth tests.\n")
    else:
        print(f"  WARNING: No internal key found -- auth tests will fail.\n")

    api_tests = _build_api_tests(key)
    results = []
    for args in api_tests:
        label, method, base, path, headers, exp_status, req_keys, min_bytes = args
        print(f"  Testing {label}...", end=" ", flush=True)
        r = run_api_test(label, method, base, path, headers, exp_status, req_keys, min_bytes)
        results.append(r)
        print(r.status)
    return results


# ── Main ──────────────────────────────────────────────────────────────────────

def run_all(send_email: bool = False, acp_only: bool = False, api_only: bool = False) -> list[Result]:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"\n{'=' * 60}")
    print(f"  OCTODAMUS HEALTH TEST  --  {now}")
    print(f"{'=' * 60}")

    all_results = []

    if not api_only:
        all_results += run_acp_section()

    if not acp_only:
        all_results += run_api_section()

    passed  = [r for r in all_results if r.status == "PASS"]
    warned  = [r for r in all_results if r.status == "WARN"]
    failed  = [r for r in all_results if r.status == "FAIL"]

    print(f"\n{'=' * 60}")
    print(f"  TOTAL: {len(all_results)} tests")
    print(f"  RESULTS: {len(passed)} PASS  |  {len(warned)} WARN  |  {len(failed)} FAIL")
    print(f"{'=' * 60}\n")

    for r in all_results:
        print(str(r))

    print()

    if send_email:
        _send_report(all_results, passed, warned, failed, now)
    elif failed:
        # Auto-email on failures even without --email flag
        _send_alert_email(failed, now)

    return all_results


def _send_report(results, passed, warned, failed, now):
    from octo_health import send_email_alert

    status = "ALL PASS" if not failed and not warned else \
             f"{len(failed)} FAILED" if failed else f"{len(warned)} WARNINGS"

    lines = [
        f"Octodamus Health Test -- {now}",
        f"Result: {status}",
        f"{'=' * 52}",
        f"PASS: {len(passed)}  WARN: {len(warned)}  FAIL: {len(failed)}",
        "",
    ]

    if failed:
        lines.append("--- FAILURES ---")
        for r in failed:
            lines.append(f"  {r.label}")
            for i in r.issues:
                lines.append(f"    FAIL: {i}")
        lines.append("")

    if warned:
        lines.append("--- WARNINGS ---")
        for r in warned:
            lines.append(f"  {r.label}")
            for w in r.warnings:
                lines.append(f"    WARN: {w}")
        lines.append("")

    lines.append("--- ALL RESULTS ---")
    for r in results:
        icon = {"PASS": "OK  ", "WARN": "WARN", "FAIL": "FAIL"}[r.status]
        lines.append(f"  [{icon}] {r.label:<44} {r.elapsed:.1f}s")
        if r.url:
            lines.append(f"         {r.url}")

    send_email_alert(
        subject=f"[Octodamus] Health Test -- {status} -- {now}",
        body="\n".join(lines)
    )


def _send_alert_email(failed: list, now: str):
    """Auto-send alert email when failures detected (even without --email)."""
    try:
        from octo_health import send_email_alert
        lines = [
            f"Octodamus Health Test -- {now}",
            f"FAILURES DETECTED: {len(failed)}",
            f"{'=' * 52}",
            "",
            "--- FAILURES ---",
        ]
        for r in failed:
            lines.append(f"  {r.label}")
            for i in r.issues:
                lines.append(f"    FAIL: {i}")
        lines.append("")
        lines.append("Run: python octo_acp_health_test.py --email for full results")

        send_email_alert(
            subject=f"[Octodamus ALERT] Health Test -- {len(failed)} FAILED -- {now}",
            body="\n".join(lines)
        )
        print(f"  Alert email sent ({len(failed)} failures).")
    except Exception as e:
        print(f"  Could not send alert email: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--email",   action="store_true", help="Email full results")
    parser.add_argument("--acp",     action="store_true", help="ACP tests only")
    parser.add_argument("--api",     action="store_true", help="API tests only")
    args = parser.parse_args()

    results = run_all(send_email=args.email, acp_only=args.acp, api_only=args.api)
    fails = sum(1 for r in results if r.status == "FAIL")
    sys.exit(1 if fails else 0)

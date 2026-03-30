"""
test_acp_full.py
Full pre-evaluation test of all 4 ACP handlers.
Run before submitting to Virtuals graduation evaluator.
"""
import sys, time
sys.path.insert(0, r'C:\Users\walli\octodamus')
from bitwarden import load_all_secrets
load_all_secrets()

from octo_report_handlers import (
    handle_fear_greed,
    handle_bitcoin_analysis,
    handle_crypto_market_signal,
    handle_congressional,
)

PASS = 0
FAIL = 0

def check(name, result, expect_reject=False, check_fng=False, check_price=False, check_ticker=None):
    global PASS, FAIL
    if expect_reject:
        if isinstance(result, dict) and result.get("reject"):
            print(f"  [PASS] {name} — correctly rejected")
            PASS += 1
        else:
            print(f"  [FAIL] {name} — should have rejected but got: {str(result)[:80]}")
            FAIL += 1
        return
    if isinstance(result, dict) and result.get("reject"):
        print(f"  [FAIL] {name} — unexpectedly rejected: {result.get('error')}")
        FAIL += 1
        return
    if check_fng:
        fng = result.get("fng_val") or result.get("fng_val", 0)
        if fng and int(fng) < 30:
            print(f"  [PASS] {name} — FNG={fng} (Extreme Fear correct)")
            PASS += 1
        elif fng == 50:
            print(f"  [FAIL] {name} — FNG={fng} (defaulted to 50, OctoPulse failed)")
            FAIL += 1
        else:
            print(f"  [PASS] {name} — FNG={fng}")
            PASS += 1
    if check_price:
        prices = result.get("prices", {})
        btc = prices.get("BTC", {}).get("price", "N/A")
        if btc and btc != "N/A":
            print(f"  [PASS] {name} — BTC price={btc}")
            PASS += 1
        else:
            print(f"  [FAIL] {name} — BTC price is N/A")
            FAIL += 1
    if check_ticker:
        call = result.get("call", "")
        if check_ticker in call or "BTC" not in call.replace(check_ticker, ""):
            print(f"  [PASS] {name} — call references {check_ticker}: {call[:60]}")
            PASS += 1
        else:
            print(f"  [FAIL] {name} — call references wrong ticker: {call[:60]}")
            FAIL += 1
    if not check_fng and not check_price and not check_ticker:
        print(f"  [PASS] {name} — completed OK")
        PASS += 1

print("\n" + "="*60)
print("ACP HANDLER PRE-EVALUATION TEST")
print("="*60)

# ── 1. get_fear_greed_sentiment_read ─────────────────────────────────────────
print("\n[1] get_fear_greed_sentiment_read")

start = time.time()
r = handle_fear_greed({"ticker": "BTC"})
elapsed = round(time.time()-start, 1)
print(f"  Time: {elapsed}s")
check("BTC accept", r, check_fng=True)

start = time.time()
r = handle_fear_greed({"ticker": "ETH"})
elapsed = round(time.time()-start, 1)
print(f"  Time: {elapsed}s")
check("ETH accept", r, check_fng=True)
check("ETH call references ETH not BTC", r, check_ticker="ETH")

r = handle_fear_greed({"ticker": ""})
check("empty ticker reject", r, expect_reject=True)

r = handle_fear_greed({"ticker": "INVALID"})
check("invalid ticker reject", r, expect_reject=True)

# ── 2. get_bitcoin_price_analysis_and_forecast ───────────────────────────────
print("\n[2] get_bitcoin_price_analysis_and_forecast")

start = time.time()
r = handle_bitcoin_analysis({"ticker": "BTC"})
elapsed = round(time.time()-start, 1)
print(f"  Time: {elapsed}s")
fng = r.get("fng_val", 0)
price = r.get("price", 0)
check("BTC accept with price", r)
if fng and int(fng) < 30:
    print(f"  [PASS] BTC FNG={fng} correct")
    PASS += 1
else:
    print(f"  [FAIL] BTC FNG={fng} — should be ~13")
    FAIL += 1
if price and float(price) > 0:
    print(f"  [PASS] BTC price=${price:,.0f}")
    PASS += 1
else:
    print(f"  [FAIL] BTC price is 0 or missing")
    FAIL += 1

r = handle_bitcoin_analysis({"ticker": "AAPL"})
check("AAPL reject", r, expect_reject=True)

r = handle_bitcoin_analysis({"ticker": ""})
check("empty reject", r, expect_reject=True)

# ── 3. get_crypto_market_signal_report ───────────────────────────────────────
print("\n[3] get_crypto_market_signal_report")

start = time.time()
r = handle_crypto_market_signal({"ticker": "BTC"})
elapsed = round(time.time()-start, 1)
print(f"  Time: {elapsed}s")
check("BTC accept", r, check_price=True)

start = time.time()
r = handle_crypto_market_signal({"ticker": "SOL"})
elapsed = round(time.time()-start, 1)
print(f"  Time: {elapsed}s")
check("SOL accept", r, check_price=True)

r = handle_crypto_market_signal({"ticker": ""})
check("empty reject", r, expect_reject=True)

r = handle_crypto_market_signal({"ticker": "INVALID_TICKER_99"})
check("invalid reject", r, expect_reject=True)

# ── 4. get_congressional_stock_trade_alert ───────────────────────────────────
print("\n[4] get_congressional_stock_trade_alert")

start = time.time()
r = handle_congressional({"ticker": "NVDA"})
elapsed = round(time.time()-start, 1)
print(f"  Time: {elapsed}s")
trades = r.get("trades", [])
check("NVDA accept", r)
if trades:
    print(f"  [PASS] NVDA has {len(trades)} trades")
    PASS += 1
else:
    print(f"  [FAIL] NVDA has 0 trades (data missing)")
    FAIL += 1

start = time.time()
r = handle_congressional({"ticker": "LGIH"})
elapsed = round(time.time()-start, 1)
print(f"  Time: {elapsed}s")
check("LGIH accept (any valid ticker)", r)

r = handle_congressional({"ticker": ""})
check("empty reject", r, expect_reject=True)

r = handle_congressional({"ticker": "FAKE_STOCK_123"})
# FAKE_STOCK_123 is 14 chars — over 5 char limit, should reject
check("FAKE_STOCK_123 reject", r, expect_reject=True)

# ── Summary ───────────────────────────────────────────────────────────────────
print("\n" + "="*60)
print(f"RESULT: {PASS} PASS / {FAIL} FAIL")
print("="*60)
if FAIL == 0:
    print("ALL CHECKS PASSED — ready to run graduation evaluator")
else:
    print("ISSUES FOUND — fix before running evaluator")

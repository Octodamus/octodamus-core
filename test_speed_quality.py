import sys, time
sys.path.insert(0, r'C:\Users\walli\octodamus')
from bitwarden import load_all_secrets
load_all_secrets()

from octo_report_handlers import (
    handle_fear_greed, handle_bitcoin_analysis,
    handle_crypto_market_signal, handle_congressional
)

print("=== SPEED & QUALITY CHECK (3 runs) ===\n")

handlers = [
    ("fear_greed BTC",      lambda: handle_fear_greed({"ticker": "BTC"})),
    ("fear_greed ETH",      lambda: handle_fear_greed({"ticker": "ETH"})),
    ("bitcoin BTC",         lambda: handle_bitcoin_analysis({"ticker": "BTC"})),
    ("bitcoin ETH",         lambda: handle_bitcoin_analysis({"ticker": "ETH"})),
    ("market_signal BTC",   lambda: handle_crypto_market_signal({"ticker": "BTC"})),
    ("congressional NVDA",  lambda: handle_congressional({"ticker": "NVDA"})),
    ("congressional AAPL",  lambda: handle_congressional({"ticker": "AAPL"})),
]

DEADLINE = 25  # ACP job deadline in seconds
all_ok = True

for name, fn in handlers:
    start = time.time()
    r = fn()
    elapsed = round(time.time() - start, 1)

    # Speed check
    speed_ok = elapsed < DEADLINE
    # Reject check
    rejected = isinstance(r, dict) and r.get("reject")
    # Data quality checks
    fng = r.get("fng_val") or r.get("fng_val", 0)
    price = r.get("price", 0) or 0
    trades = r.get("trades", [])
    prices = r.get("prices", {})
    call = r.get("call", "")

    issues = []
    if not speed_ok:
        issues.append(f"SLOW {elapsed}s")
    if rejected:
        issues.append("UNEXPECTED REJECT")
    if "fear_greed" in name and fng and int(fng) == 50:
        issues.append("FNG defaulted to 50")
    if "bitcoin" in name and float(price) == 0:
        issues.append("price=0")
    if "market_signal" in name:
        btc_p = prices.get("BTC", {}).get("price", "N/A")
        if btc_p == "N/A":
            issues.append("BTC price N/A")
    if "congressional" in name and len(trades) == 0:
        issues.append("0 trades")
    if call and "BTC" in call and "ETH" in name:
        issues.append("ETH call references BTC")

    status = "PASS" if not issues else "FAIL"
    if issues:
        all_ok = False
    print(f"  [{status}] {name}: {elapsed}s" + (f" — {', '.join(issues)}" if issues else ""))

print(f"\n{'ALL GOOD' if all_ok else 'ISSUES FOUND'} — {'ready to evaluate' if all_ok else 'fix before evaluating'}")

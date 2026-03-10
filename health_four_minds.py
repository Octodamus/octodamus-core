"""
health_four_minds.py

Smoke-test all four new minds independently.
Does NOT post to X. Does NOT require Bitwarden unlocked for OctoWatch.
DOES require FRED_API_KEY and ETHERSCAN_API_KEY in env for those minds.

Run from project directory:
    C:\Python314\python.exe health_four_minds.py

Flags:
    --logic    Test OctoLogic only
    --vision   Test OctoVision only
    --depth    Test OctoDepth only
    --watch    Test OctoWatch only
    (no flags) Test all four
"""

import argparse
import sys
import json
from datetime import datetime


def test_logic():
    print("\n" + "="*50)
    print("OCTO LOGIC — Technical Analysis")
    print("="*50)
    try:
        from octo_logic import run_technical_scan, format_logic_for_prompt
        # Test with just 3 tickers to keep it fast
        results = run_technical_scan(["NVDA", "BTC-USD", "TSLA"])
        if not results:
            print("❌ No results returned")
            return False

        for r in results:
            if r.get("error"):
                print(f"  ⚠ {r['ticker']}: {r['error']}")
            else:
                print(f"  ✓ {r['ticker']}: {r['bias'].upper()} "
                      f"(score {r['bias_score']:+d}) | "
                      f"RSI={r['rsi']} | MACD_hist={r['macd_histogram']}")

        prompt_str = format_logic_for_prompt(results)
        print(f"\nPrompt context ({len(prompt_str)} chars):")
        print(prompt_str)
        print("\n✅ OctoLogic PASS")
        return True
    except Exception as e:
        print(f"❌ OctoLogic FAIL: {e}")
        import traceback; traceback.print_exc()
        return False


def test_vision():
    print("\n" + "="*50)
    print("OCTO VISION — FRED Macro")
    print("="*50)
    try:
        from octo_vision import run_macro_scan, format_vision_for_prompt
        result = run_macro_scan()

        if result.get("error") == "no_api_key":
            print("⚠ No FRED_API_KEY found.")
            print("  Get free key: https://fred.stlouisfed.org/docs/api/api_key.html")
            print("  Then add to Bitwarden: 'AGENT - Octodamus - FRED API'")
            return None  # soft skip

        if result.get("error"):
            print(f"❌ Error: {result['error']}")
            return False

        interp = result.get("interpretation", {})
        print(f"  Regime: {interp.get('regime')}")
        for s in interp.get("signals", []):
            print(f"  • {s}")
        for f in interp.get("risk_flags", []):
            print(f"  ⚠ {f}")

        prompt_str = format_vision_for_prompt(result)
        print(f"\nPrompt context ({len(prompt_str)} chars):")
        print(prompt_str)
        print("\n✅ OctoVision PASS")
        return True
    except Exception as e:
        print(f"❌ OctoVision FAIL: {e}")
        import traceback; traceback.print_exc()
        return False


def test_depth():
    print("\n" + "="*50)
    print("OCTO DEPTH — Etherscan On-Chain")
    print("="*50)
    try:
        from octo_depth import run_onchain_scan, format_depth_for_prompt
        result = run_onchain_scan()

        if result.get("error") == "no_api_key":
            print("⚠ No ETHERSCAN_API_KEY found.")
            print("  Get free key: https://etherscan.io/apis")
            print("  Then add to Bitwarden: 'AGENT - Octodamus - Etherscan API'")
            return None  # soft skip

        if result.get("error"):
            print(f"❌ Error: {result['error']}")
            return False

        data = result.get("data", {})
        interp = result.get("interpretation", {})

        if data.get("gas"):
            g = data["gas"]
            print(f"  Gas: safe={g['safe_gwei']:.0f} propose={g['propose_gwei']:.0f} fast={g['fast_gwei']:.0f} Gwei")
        if data.get("eth_price"):
            p = data["eth_price"]
            print(f"  ETH: ${p['eth_usd']:,.0f} USD")
        print(f"  Whale txs: {len(data.get('whale_txs', []))}")
        if data.get("usdc_transfer_count") is not None:
            print(f"  USDC transfers: {data['usdc_transfer_count']}")
        print(f"  Bias: {interp.get('bias')}")

        prompt_str = format_depth_for_prompt(result)
        print(f"\nPrompt context ({len(prompt_str)} chars):")
        print(prompt_str)
        print("\n✅ OctoDepth PASS")
        return True
    except Exception as e:
        print(f"❌ OctoDepth FAIL: {e}")
        import traceback; traceback.print_exc()
        return False


def test_watch():
    print("\n" + "="*50)
    print("OCTO WATCH — Reddit Sentiment")
    print("="*50)
    print("(No API key needed — using Reddit public JSON)")
    try:
        from octo_watch import run_sentiment_scan, format_watch_for_prompt, SUBREDDITS
        # Test with just 2 subs to keep it fast
        test_subs = {k: v for i, (k, v) in enumerate(SUBREDDITS.items()) if i < 2}
        result = run_sentiment_scan(test_subs)

        print(f"  Overall mood: {result['mood']} (score {result['composite_score']:+.3f})")
        for sub, data in result.get("subreddits", {}).items():
            if not data.get("error"):
                print(f"  r/{sub}: score={data['sentiment_score']:+.3f} | "
                      f"{data['posts_sampled']} posts sampled")

        prompt_str = format_watch_for_prompt(result)
        print(f"\nPrompt context ({len(prompt_str)} chars):")
        print(prompt_str)
        print("\n✅ OctoWatch PASS")
        return True
    except Exception as e:
        print(f"❌ OctoWatch FAIL: {e}")
        import traceback; traceback.print_exc()
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--logic",  action="store_true")
    parser.add_argument("--vision", action="store_true")
    parser.add_argument("--depth",  action="store_true")
    parser.add_argument("--watch",  action="store_true")
    args = parser.parse_args()

    run_all = not any([args.logic, args.vision, args.depth, args.watch])

    results = {}
    if run_all or args.logic:
        results["OctoLogic"]  = test_logic()
    if run_all or args.vision:
        results["OctoVision"] = test_vision()
    if run_all or args.depth:
        results["OctoDepth"]  = test_depth()
    if run_all or args.watch:
        results["OctoWatch"]  = test_watch()

    print("\n" + "="*50)
    print(f"HEALTH CHECK — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("="*50)
    for name, status in results.items():
        if status is True:
            label = "✅ PASS"
        elif status is None:
            label = "⏭  SKIP (no API key — see above)"
        else:
            label = "❌ FAIL"
        print(f"  {name:15s} {label}")
    print()

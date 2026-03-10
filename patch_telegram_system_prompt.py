"""
patch_telegram_system_prompt.py (v2)
"""
import os, shutil

BOT_PATH   = "telegram_bot.py"
BOT_BACKUP = "telegram_bot.py.bak"

OLD_BLOCK = """WHAT IS CONFIRMED LIVE AND WORKING RIGHT NOW:
- Bitwarden CLI loads all 11 credentials automatically at every script run - confirmed working
- Windows Task Scheduler has 5 tasks registered: 8am market read weekdays, 30min monitor, Mon/Wed deep dives, Sat 10am wisdom
- X account @octodamusai is connected via OpenTweet API - key verified and loaded
- Financial Datasets API is connected - pulling NVDA, TSLA, AAPL, BTC-USD data
- octodamus.com is live on Vercel with treasury dashboard
- Telegram bot (this conversation) is live
- Treasury wallet {TREASURY_WALLET} exists on Base
- Python scripts are installed at C:\\Users\\walli\\octodamus\\
- Posting window is currently: {get_posting_status()}
WHAT IS IN PROGRESS (next build steps):
- First autonomous X posts firing through the posting window (7am-9pm PT weekdays)
- Live Base RPC balance reading for treasury
- $OCTO token launch via Bankr at {FOLLOWER_TARGET} X followers"""

NEW_BLOCK = """WHAT IS CONFIRMED LIVE AND WORKING RIGHT NOW:
- Bitwarden CLI loads all 15 credentials automatically at every script run - confirmed working
- Windows Task Scheduler has 8 tasks registered: 8am market read weekdays, 30min monitor, Mon/Wed deep dives, Sat 10am wisdom, OctoData pipeline 1am/2am/3am daily
- X account @octodamusai is connected via OpenTweet API - posting live
- Financial Datasets API connected - NVDA, TSLA, AAPL, BTC-USD live
- OctoLogic live - RSI, MACD, Bollinger Band technical analysis via yfinance
- OctoVision live - FRED macro data: Fed Funds 3.64%, unemployment 4.4%, yield curve, oil, CPI
- OctoDepth live - Etherscan on-chain: gas oracle, whale transaction scanner, USDC flow tracking
- OctoWatch live - Reddit social sentiment scanner (WSB, CryptoCurrency, investing, stocks, Bitcoin)
- OctoData API live at api.octodamus.com - prices, sentiment, briefing endpoints as Windows Service
- RapidAPI listing live - Basic $9/mo and Pro $29/mo plans active
- octodamus.com live on Vercel with treasury dashboard
- Telegram bot (this conversation) is live
- Treasury wallet {TREASURY_WALLET} on Base mainnet - live RPC balance reading
- Python scripts installed at C:\\Users\\walli\\octodamus\\
- Posting window is currently: {get_posting_status()}
WHAT IS IN PROGRESS (next build steps):
- Reddit data feed improvement (CDN block on current IP - fails gracefully with neutral sentiment)
- $OCTO token launch via Bankr at {FOLLOWER_TARGET} X followers
- Virtuals ACP registration via Clawbor for agent marketplace monetization"""

def apply():
    if not os.path.exists(BOT_PATH):
        print(f"ERROR: {BOT_PATH} not found."); return

    with open(BOT_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    if "OctoLogic live" in content:
        print("Already patched."); return

    shutil.copy2(BOT_PATH, BOT_BACKUP)
    print(f"Backed up to {BOT_BACKUP}")

    if OLD_BLOCK in content:
        content = content.replace(OLD_BLOCK, NEW_BLOCK)
        print("Patch OK: system prompt updated")
    else:
        print("FAILED: anchor not found in file")
        print("First 80 chars of anchor:", repr(OLD_BLOCK[:80]))
        return

    with open(BOT_PATH, "w", encoding="utf-8") as f:
        f.write(content)
    print("Done. Restart telegram_bot.py to apply.")

if __name__ == "__main__":
    apply()

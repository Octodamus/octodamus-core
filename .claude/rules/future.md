# Future / Roadmap — Octodamus

## $OCTO Token (proof-of-oracle)
When building: prioritize dramatically easier onboarding than BOTCOIN required.
- Single-command setup: python octo_setup.py — detects wallet, checks balances, stakes, verifies end-to-end
- Rich status dashboard from day one (balance, staked, rewards, epoch, credits)
- Loud early failures — no silent fallbacks that waste API spend
- Auto-migration/upgrade handling baked in
- Clear docs on coordinator rate limits upfront
- No staking prerequisite barrier if possible — let people mine at reduced rate first
Reference: BOTCOIN onboarding required hours of debugging silent failures.

## HIP-4 (Hyperliquid)
Monitor HIP-4 validator proposal. Trigger conditions for Octodamus build:
- HIP-4 passes governance vote
- Clear oracle/validator spec published
- HYPE token integration path confirmed
Proactively surface any Octodamus-relevant HIP-4 intel when spotted.

## OctoVision
Playwright + Claude Vision for /chart, /see, oracle chart replies in Telegram.
File: octo_vision.py (or integrated into telegram_bot.py)

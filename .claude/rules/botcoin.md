# BOTCOIN Mining — Octodamus

## Dashboard
- File: `botcoin_dashboard_server.py`
- Port: 8901 — http://localhost:8901
- Runs independent of OctoBoto and the main runner
- Restart: kill PIDs on port 8901, then: python botcoin_dashboard_server.py --port 8901

## Cost Estimation
When tokens_in/tokens_out = 0 in botcoin_credits.json (token logging not active):
- EST_TOKENS_IN_PER_SOLVE  = 3000
- EST_TOKENS_OUT_PER_SOLVE = 2500
- Model: Sonnet 4.6 — $3/MTok in, $15/MTok out
- Shows ~$0.046/solve; prefix estimated values with ~ in UI
- Real values replace estimates automatically once a full epoch runs

## Mining
- Miner: `octo_boto_botcoin.py` — proof-of-inference on Base chain
- Contract (V3 active): 0xB2fbe0DB5A99B4E2Dd294dE64cEd82740b53A2Ea
- BOTCOIN token: 0xA601877977340862Ca67f816eb079958E5bd0BA3
- Wallet: 0x7d372b930b42d4adc7c82f9d5bcb692da3597570
- Coordinator: https://coordinator.agentmoney.net
- Credits log: data/botcoin_credits.json (accumulates per epoch)
- Auth cache: data/botcoin_auth.json (token valid ~23h)

## Solver
- Uses Sonnet 4.6 with extended thinking (budget_tokens=2000, max_tokens=6000)
- JSON mode challenges: thinking + clean JSON output, no prefill allowed
- Text mode: Sonnet primary, Haiku last-resort fallback
- Tokens tracked per solve in _tokens_in/_tokens_out, accumulated in credits log

## Real BOTCOIN Reward Rate (verified on-chain 2026-04-19)
- Real rate: ~2.39 BOTCOIN per credit (confirmed: epoch 52, 28,080 credits -> 66,979 BOTCOIN claimed)
- DO NOT use invented rates (817/credit or 409/credit were both wrong)
- Original purchase: 25,061,414 BOTCOIN staked — this is NOT mined, it was purchased
- Total mining rewards from all epochs: ~146,000 BOTCOIN (~$0.60 at $0.0000041)
- Mining is currently unprofitable at current BOTCOIN price (~$20 Claude costs vs $0.60 rewards)
- The bet is on BOTCOIN price appreciation, not current yield
- Claim status: epoch 52 claimed (tx 0x0663...), epochs 49/56/57 reverted (likely expired or already claimed)
- Auto-claim in mine_loop was failing silently due to frequent crashes (now fixed with RestartCount=999)

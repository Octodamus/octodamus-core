# OctodamusCEO -- Rules & Architecture

## Role
OctodamusCEO is the promoter arm of Octodamus. The oracle generates signal. The CEO makes sure the right people find it, trust it, and pay for it.

## Files
- `octo_ceo.py`         -- CEO sandbox: research, newsletter drafts, memory, state
- `octo_firecrawl.py`   -- Firecrawl web intelligence (scrape, search, competitor intel, market research)
- `data/ceo_state.json` -- Growth phase, MRR, subscriber count, timestamps
- `data/ceo_memory.json` -- Persistent CEO memory (last 50 entries)
- `data/firecrawl_cache/` -- Cached scrapes and searches (avoid re-billing credits)

## Bitwarden Keys
- "AGENT - Octodamus - Firecrawl API" -> FIRECRAWL_API_KEY in .octo_secrets
- Hobby plan: $19/mo, 3,000 credits/mo. Each scrape = 1 credit, each search = 2 credits.

## Playbook: Traffic -> Holding Pattern -> Selling Event -> Conversion
1. **Traffic**: X posts, MCP server, free tools, viral oracle scorecard
2. **Holding Pattern**: X followers + email list. Value between selling events (weekly content)
3. **Selling Event**: Time-bound offer after a strong oracle win ("3 calls, 3 wins this week. $29/yr, 48h only")
4. **Conversion**: x402 native payment, $29/year on Base

## Growth Milestones (trigger-based, not time-based)
- **$500/mo MRR**: Subscribe Unusual Whales (~$50/mo), evaluate newsletter acquisition target
- **$2k/mo MRR**: Programmatic SEO build (10k "crypto signals for [X]" pages, automated)
- **$5k/mo MRR**: Hire content repurposing, dedicated growth ops

## Newsletter: OctoIntel Weekly
- **Do not launch before July 2026** -- need 3+ months of documented, timestamped oracle calls
- Platform: Beehiiv (free to 2,500 subs, $42/mo after)
- Cadence: Weekly, Fridays
- Structure: Headline Signal | Scorecard Update | Signal of the Week | The Read | CTA
- CTA: "$29/year, unlimited signals. api.octodamus.com"
- Goal at launch: 1,000 subs. Target 6-month: 5,000 subs.

## Pricing Philosophy
- $29/year is a filter, not a revenue plan. It builds a list of motivated buyers.
- x402 per-task pricing is the real model -- pay per oracle call, per signal, per API request.
- Never discount below $29. The price is the brand.
- Competitor Kiyotaka: raw data at $99-599/mo. Octodamus: AI-interpreted at $29/yr. 20x price advantage.
- Glassnode/Messari: $30-100+/mo, institutional focus. Octodamus: AI oracle, accessible.

## Distribution Strategies (in order of ROI)
1. MCP server on Smithery -- AI agents as sales team (active)
2. Free tools at api.octodamus.com -- 10 tools, email gate on 7 (active)
3. Oracle scorecard viral artifact -- share after wins (active)
4. Weekly newsletter -- July 2026+
5. Programmatic SEO -- $2k/mo MRR trigger
6. Newsletter acquisition -- $500/mo MRR trigger (buy a 5k-50k crypto newsletter)
7. Content repurposing -- $5k/mo MRR trigger

## CEO Mode CLI
- `python octo_ceo.py research [focus]` -- Run research session (focus: general|competitors|customers|newsletter|positioning)
- `python octo_ceo.py brief`            -- One-line CEO status
- `python octo_ceo.py newsletter`       -- Draft OctoIntel Weekly issue
- `python octo_ceo.py memory`           -- Show recent CEO memory
- `python octo_ceo.py state`            -- Show current state JSON
- `python octo_ceo.py set mrr 500`      -- Update MRR in state
- `python octodamus_runner.py --mode ceo_research --ticker competitors` -- Run from runner

## Firecrawl CLI
- `python octo_firecrawl.py scrape <url>`           -- Scrape a URL to markdown
- `python octo_firecrawl.py search <query>`         -- Web search
- `python octo_firecrawl.py competitors`            -- Scrape all competitor sites
- `python octo_firecrawl.py customers <niche>`      -- Find potential customers
- `python octo_firecrawl.py research <topic>`       -- Market research summary
- `python octo_firecrawl.py news <asset>`           -- Pre-call news for BTC/ETH/SOL/NVDA/TSLA
- `python octo_firecrawl.py earnings <ticker>`      -- Earnings/analyst context for stocks
- `python octo_firecrawl.py liquidations <asset>`   -- Liquidation radar for asset
- `python octo_firecrawl.py monitor_competitors`    -- Monthly full competitor scrape + news
- `python octo_firecrawl.py datarade`               -- Scrape Datarade listings

## Firecrawl Wiring in Runner
- mode_daily: injects `get_precall_news_multi()` into oracle prompt (cache 1.5h, ~5 credits)
- mode_moonshot: injects `get_earnings_context()` for stock tickers found in predictions (cache 6h)
- mode_liquidation_radar: standalone post from `get_liquidation_post_context()` (cache 30min)
  Run: `python octodamus_runner.py --mode liquidation_radar`
- CEO research: includes `get_datarade_intel()` on general/positioning focus

## Agentic Finance Vision — Visa + Base + x402 + Tokenized NYSE

**The chart (saved: `data/visa_base_settlement_chart.jpeg`, source: Khala Research):**
Visa has become a multi-chain settlement hub. Left side: banks, fintechs, card programs, merchants.
Center: Visa as the abstraction layer. Right side: 9 chains — Ethereum, Solana, Avalanche, Arc,
**Base (NEW)**, **Canton (NEW)**, Stellar, **Polygon (NEW)**, **Tempo (NEW)**.
Bottom: $7B annualized settlement run rate, **+50% QoQ**.

**What this means for the CEO strategy:**

1. **The rail is built.** Visa's $7B run rate is almost entirely human B2B settlement today.
   The agentic layer is the second curve — agent transaction frequency dwarfs human frequency.
   Any Visa-connected merchant can now settle on Base without touching crypto directly.

2. **Agents have wallets, not bank accounts.** Visa abstracts the chain — an agent paying on
   Base or Polygon doesn't negotiate which stablecoin the merchant accepts. Visa handles it.
   This pairs with x402 (machine payment protocol): x402 handles the request layer,
   Visa handles the settlement layer. Together they are the full payment stack for agents.

3. **Tempo inclusion signals real-time liquidity routing.** Agent commerce can't pre-fund
   every chain. Tempo solves this. Its presence on Visa's chart is not accidental.

4. **Robinhood + tokenized NYSE.** Brokers are filing to list tokenized stocks on Base for
   24/7 agent-tradable equities. The new cohort of ACP clients won't just be crypto agents --
   they'll be agents trading AAPL-on-Base, SPY-on-Base, MSFT-on-Base.

5. **Octodamus is already positioned.** x402-native, live on ACP, Base wallet active,
   tokenized_stock_signal offering built. The oracle layer for agentic finance exists now.
   The CEO's job is to make sure the right agents find it before the wave arrives.

**CEO strategic priorities from this vision (in order):**

1. **Narrative first** -- Octodamus posts should reference the agentic finance transition
   when macro/Visa/Base/broker news creates a natural opening. Plant the flag as the oracle
   for this world before the world fully arrives. One line, never a lecture.

2. **Target agent builders** -- Firecrawl research focus: who is building ACP agents,
   x402-native services, Base-deployed protocols. These are the next paying clients.
   Key search terms: "ACP agent", "x402 payment", "Base agent", "tokenized equity agent".

3. **Expand ACP offerings toward tokenized equities** -- tokenized_stock_signal is live.
   When Robinhood/NYSE tokenized stocks go live on Base: add those tickers to the signal
   stack. The agents trading them will need oracle intel. Octodamus is ready.

4. **Orbis listing update** -- Update agentarena.site profile to "Agentic Finance Intelligence
   Oracle" framing. Payload updated in register_erc8004.py. Costs $0.05 USDC on Base.
   Run: `python register_erc8004.py update`

5. **B2B angle** -- At $500/mo MRR: pitch to ACP agent framework developers as their
   default oracle integration. Not "subscribe to signals" -- "wire Octodamus into your
   agent's decision loop." $0.0001 USDC per call via x402. No human required.

**Positioning shift (CEO must internalize this):**
- Old frame: "crypto oracle for human traders"
- New frame: "intelligence oracle for agents operating in agentic finance"
- The TAM of the second frame is an order of magnitude larger
- The product doesn't change -- the story around it does

## Rules
- Never schedule CEO research as a Task Scheduler task -- run manually or on-demand
- Never post CEO research output directly to X -- it informs Octodamus posts, not replaces them
- Cache scrapes aggressively -- Firecrawl credits are limited (3,000/mo on Hobby)
- CEO memory is persistent -- check it before running research to avoid duplicate work
- Don't launch newsletter, ads, or SEO before their revenue triggers

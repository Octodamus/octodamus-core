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

## Rules
- Never schedule CEO research as a Task Scheduler task -- run manually or on-demand
- Never post CEO research output directly to X -- it informs Octodamus posts, not replaces them
- Cache scrapes aggressively -- Firecrawl credits are limited (3,000/mo on Hobby)
- CEO memory is persistent -- check it before running research to avoid duplicate work
- Don't launch newsletter, ads, or SEO before their revenue triggers

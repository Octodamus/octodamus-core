# Octodamus — Project State
# Last updated: 2026-05-01 (session 10)

---

## Owner
- Chris / @octodamusai on X
- Email: octodamusai@gmail.com (primary), altusfx@gmail.com (alt)
- Windows 11, Python 3.14, Task Scheduler, Cloudflare tunnel
- Working dir: C:\Users\walli\octodamus
- Site repo: C:\Users\walli\octodamus-site (GitHub -> Vercel)

## Live Endpoints
- Site:    https://octodamus.com
- API:     https://api.octodamus.com (Cloudflare tunnel -> port 8742)
- Soul:    https://octodamus.com/soul
- MCP:     octodamusai/market-intelligence on Smithery

---

## Always-On Services (3 tasks, must always be running)
- Octodamus-API-Server   port 8000, FastAPI, Cloudflare tunnel
- Octodamus-ACP-Worker   Virtuals ACP stock reports, payment-gated
- Octodamus-Cloudflared  tunnel daemon

## Task Scheduler (32 total tasks — 24 core + 5 sub-agents + 1 ACP funder + 2 TokenBot)
Daily:
- 3:30 AM  DailyRead-330am   Early morning oracle brief
- 5:00 AM  DailyRead         Morning oracle brief
- 5:45 AM  NYSE_MacroMind    Macro regime, yield curve, Fed probability (before NYSE open)
- 5:50 AM  NYSE_StockOracle  Congressional trading, stock signals (before NYSE open)
- 5:55 AM  Order_ChainFlow   On-chain delta, DEX flow, whale activity (before NYSE open)
- 6:00 AM  X_Sentiment_Agent Crowd sentiment scan, contrarian divergences
- 6:05 AM  NYSE_Tech_Agent   SEC filings, Chainlink feeds, tokenization status
- 7:00 AM  Monitor-7am       Market signal + watchpost fallback
- 7:30 AM  QRT-Scan          Retweet/quote scan
- 9:30 AM  StrategyMonitor   Strategy signal
- 11:00 AM Moonshot          [DISABLED -- do not re-enable]
- 12:00 PM FlightSample      Aviation data sample (no post)
- 12:15 PM Format-12pm       Format rotation post
- 4:00 PM  Monitor-4pm       Market signal + watchpost fallback
- 5:00 PM  EveningJournal    Journal post (reads 9 X accounts for news)
- 7:00 PM  DailyRead-7pm     Evening oracle brief

Weekly:
- Sat 10am   Wisdom           Evergreen market wisdom
- Sun 8am    Music            Music archive post
- Sun 11am   Soul             Tool/philosophy/identity post
- Sun 4am    StrategySunday   Weekly strategy thread
- Mon 9am    Thread-Mon       NVDA deep dive thread
- Wed 9am    Thread-Wed       BTC deep dive thread
- Wed 10am   GovContracts     Pentagon contracts post (Quiver API)
- Fri 8am    Congress         Congressional trading signal

2x Daily:
- 6:15 AM  TokenBot-6am    TokenBot_NYSE_Base pre-NYSE-open session (15 min before 6:30 AM PST open)
- 4:00 PM  TokenBot-4pm    TokenBot_NYSE_Base NYSE-close/Asian-open session (Tokyo opens 4 PM PST)

3x Daily:
- 8:00 AM  XEngage         Reply engine: harvest feedback + engage watched accounts
- 1:00 PM  XEngage         (2nd run)
- 3:30 PM  XEngage         (3rd run)

Always-running:
- Octodamus-Telegram     Telegram bot (auto-restart on crash)
- Octodamus-XStats       X account stats cache
- Octodamus-HealthCheck  health check
- Octodamus-GDrive-Backup full zip backup every 4 hours
- Octodamus-AutoResolve / BotoResolve  Polymarket resolution
- Octodamus-ACP-Funder                 Auto-funds pending ACP jobs (polls every 5 min)

---

## Go-Live Gate Policy (HARD RULE)
Neither OctoBoto nor Agent_Ben goes live until paper trading is profitable.
Required before flipping any live mode:
- Minimum 20 resolved paper trades
- Win rate >= 55%
- Positive cumulative PnL

### OctoBoto Paper Status (as of 2026-04-28)
- Record: 7W / 13L (35% win rate) | PnL: -$273.81 | Balance: $611 from $1000 start
- STATUS: NOT READY — losing, and trading wrong market types
- Root cause: geopolitical/political/tech-competition markets slipping through (Iran ceasefire x2,
  Hungary PM, OpenAI ranking, Kharg Island) — high volume caused them to rank above domain markets
- Fix applied (2026-04-28): hard pre-filter in octo_boto_ai.py _is_excluded() + _in_domain()
  Non-domain markets now blocked BEFORE AI evaluation, not just instructed to return NONE
- LIVE_MODE stays False until paper record shows 55%+ on 20+ domain-only trades

### Agent_Ben Paper Status (as of 2026-04-28)
- Limitless trades: 0 | Polymarket trades: 0
- STATUS: NOT STARTED — Polymarket fallback now wired, needs to build a record
- _PAPER_MODE stays True until record shows 55%+ win rate on 20+ trades

---

## Current Oracle State
- Oracle calls: 10 logged in data/octo_calls.json (call_type=oracle), 10 resolved, 5W/5L (50%)
  - call_type=polymarket entries excluded from oracle scorecard everywhere (Ben, evening journal, API)
- Polymarket calls (call_type=polymarket): 7 total, 7 resolved, 4W/3L (57%), 0 open
  - BITC-REAC-AP resolved 2026-04-30: "Will BTC reach $80K in April?" called NO. BTC peaked ~$78K. WIN.
- Win rate published after 50+ resolved oracle calls
- OctoBoto: paper mode (PAPER_MODE = True in .agents/profit-agent/agent.py)
- Polymarket V2: live (migrated 2026-04-17, V2 exchange live 2026-04-22)
- Autoresolve tiered thresholds: within 3d=0.95/0.05, within 7d=0.97/0.03, >7d=0.99/0.01

---

## LLM Routing (3 tiers — never collapse)
- Sonnet 4.6         core oracle: DailyRead, Moonshot, FlowSignal; Agent_Ben
- Haiku 4.5          voice posts: Wisdom, Soul, Watchpost, Thread, Format engine
                     sub-agents: all 5 NYSE sub-agents use Haiku
- OpenRouter Llama-4-Maverick:free  data-constrained: Congress, GovContracts, Liquidation Radar
  - Falls back to Haiku automatically if OpenRouter fails
  - Key: OPENROUTER_API_KEY in .octo_secrets
  - No local daemon (ClawRouter task deleted 2026-04-23, direct API call now)

---

## Signal System (13 signals — never collapse tiers)
1-4:   Derivatives (funding, OI, liquidations, basis)
5:     Fear & Greed Index
6:     Macro (FRED: yield curve, DXY, SPX, VIX, M2)
7:     Aviation volume (OpenSky week-over-week delta)
8:     TSA travel demand
9:     Options flow / dark pool (Unusual Whales — module ready, key not active)
10:    Congressional trading (Quiver)
11:    CLOB order book depth (Polymarket yes_token_id — spread, pressure, quality)
12:    Binance 24h cumulative delta (buy vs sell volume, acceleration, divergence)
13:    TradingView 1h+4h technical consensus (octo_tradingview.py — 26 indicators, BINANCE feed)
       - 15-min cache in data/tv_signal_cache.json
       - Wired into: octodamus_runner.py daily read + directional_call() in octo_report_handlers.py
       - Wired into: octo_boto_ai.py for OctoBoto trade decisions

---

## Agent_Ben (autonomous profit agent)
- File: .agents/profit-agent/agent.py
- Model: claude-sonnet-4-6 via tool-use agent loop
- Role: profit-generating sub-agent, markets Octodamus to other agents
- Trading: Limitless Exchange (primary) + Polymarket (fallback when Limitless dry)
  - Limitless: PAPER_MODE = True in place_limitless_bet; writes to limitless_trades.json
  - Polymarket: paper_trade_polymarket() ALWAYS paper; writes to polymarket_trades.json
  - NEVER touches octo_boto_trades.json — that is OctoBoto's record
  - Hard block on markets expiring < 2h (_MIN_EXPIRY_H = 2, updated 2026-04-30)
  - 4-condition gate: EV >25% OR price-vs-strike gap >0.5%, expiry >2h, vol >$5k,
    Range Scout OR main oracle PLUS Grok aligned
  - KEY INSIGHT (2026-04-30): Limitless has NO multi-day markets. All markets 5min/15min/1hr/4hr ONLY. 4h is the hard ceiling.
    Previous min_hours=24 gate was blocking 100% of available markets for 22+ sessions.
    Range Scout 4h = Limitless. Range Scout 6h/8h = Polymarket only. Agent corrected 2026-05-01.
    LIMITLESS STRUCTURAL CHECK: 10+ consecutive 0-market sessions → agent must recommend suspension.
- x402 services: calls api.octodamus.com for premium signal (BEN_OCTODATA_API_KEY in secrets)
- Memory: SQLite (octo_memory_db.py) + weekly Haiku distillation (octo_memory_distill.py)
- Core memory: data/memory/ben_core.md
- Competitor intel: tool_buy_acp_competitor_job() — buys ACP jobs to monitor competition
- Does NOT post to X (private agent, Telegram + email output only)
- Pending: flip PAPER_MODE = False after review

---

## TokenBot_NYSE_Base (new 2026-05-01)
- File: .agents/tokenbot_nyse_base/agent.py + run.py
- Model: Haiku 4.5 (tool-use agent loop, same pattern as NYSE sub-agents)
- Role: paper trade tokenized NYSE stocks on Base (Dinari dShares)
- Portfolio: $1,000 paper USDC. Max $100/position. Max 5 open. LONG only.
- Target: +10%, Stop: -5%, Max hold: 5 sessions
- Watchlist: AAPL, TSLA, NVDA, GOOGL, AMZN, META, SPY, MSFT
- Dinari tokens: dAAPL, dTSLA, dNVDA, dGOOGL, dAMZN, dMETA, dSPY, dMSFT (1:1 on Base/Aerodrome)
- Price source: Finnhub (FINNHUB_API_KEY in secrets) -- Dinari dShares are 1:1, so Finnhub = dShare price
- Signal gate: min 2 aligned signals to open. Oracle (primary) + congressional OR macro OR sentiment.
- Reports: email 6:15am (pre-NYSE open) + 4pm (NYSE close / Tokyo open) via TokenBot-6am + TokenBot-4pm tasks
  Timing rationale: sub-agents all finish by 6:05am; 6:15am buys their intel before 6:30am PST NYSE open.
  4pm = NYSE close (1pm PST/4pm EST) + Tokyo open (4pm PST). Positions for Asian overnight session.
- Ecosystem: buys cross-signals from NYSE_StockOracle, NYSE_MacroMind, X_Sentiment_Agent via ACP
- Live flip criteria: >60% win rate on 20+ closed trades -- then swap to Aerodrome DEX on Base
- Wallet: 0xc1F363FB216873dc2EB5f5A4A81352a059a61b46 (Base, unfunded until paper proves profitable)
  Keys: TOKENBOT_NYSE_BASE_ADDRESS + TOKENBOT_NYSE_BASE_PRIVATE_KEY in .octo_secrets
  Bitwarden: add as "AGENT - TokenBot_NYSE_Base - Wallet" (address=username, key=password)
- NOT TensorTrade / RL -- oracle signal IS the alpha. RL can be added later if needed.
- Sell story: when tokenized NYSE arrives in scale (6-12mo), TokenBot is the proof-of-concept.
  The paper record built now becomes the product sold to the tokenized NYSE crowd.

## NYSE Sub-Agent Ecosystem (new 2026-04-28)
Five sub-agents running before NYSE open at 6:30am PST.
All use Haiku 4.5 model. All have compounding SQLite memory + weekly distillation.
All have own wallet on Base for x402 buying + ACP buying capability.
ACP native signing: LIVE (2026-04-29) — each agent signs createJob with own secp256k1 key.
All wallets funded: ~0.000428 ETH each for gas ($1 swap per wallet). $10 USDC each.
Ben/Franklin wallet also funded with ETH (2026-04-29).
Funder: octo_acp_funder.py polls every 5 min (Octodamus-ACP-Funder task) to auto-fund on budget set.
Pending jobs log: data/acp_pending_jobs.json

| Agent              | Schedule | Focus                                    | Wallet |
|--------------------|----------|------------------------------------------|--------|
| NYSE_MacroMind     | 5:45am   | FRED macro, yield curve, Fed probability | 0xA0f940469EDa402de08A8ea1B4a730e43e317035 |
| NYSE_StockOracle   | 5:50am   | Congressional trading, stock signals     | 0x46037F1a6D10308c9892f297a0d419aAA25131A4 |
| Order_ChainFlow    | 5:55am   | On-chain delta, DEX, whale activity      | 0xa78CfD4B00b96bA6090013f20095E7aC3B87E1B9 |
| X_Sentiment_Agent  | 6:00am   | Grok crowd sentiment, contrarian edges   | 0x3917798a66CF1e1ad453F8c7ffcD780A1B40A0B2 |
| NYSE_Tech_Agent    | 6:05am   | SEC filings, Chainlink, tokenization     | 0x40e77Ae8Cc09Ff4456EDa6dF661e2C72C40e6672 |

Private keys stored in .octo_secrets. Also store in Bitwarden (not yet done).
Core memory files: data/memory/[agent_name]_core.md

---

## Key Architecture Decisions (don't re-litigate)
- Price feed: Kraken primary (no geo restrictions), CoinGecko fallback, 5-min cache
  in financial_data_client.py get_crypto_prices()
- Derivatives data: OKX public API (Coinglass was 401ing, OKX free + reliable)
- DO NOT switch Claude Code to OpenRouter -- caching works at 98% hit rate (~$1,500/session savings)
- OpenRouter only for data-constrained posts (not the core oracle, never Claude Code)
- ACP jobs: event stream is authoritative — _verify_job_funded() removed (was timing out CLI)
  Replay runs in background thread, skips jobs >2h old
- Cashtag enforcement: ensure_cashtag() in octo_x_poster.py -- every X post gets $BTC/$ETH/$SOL
- Telegram reply alert: _telegram_reply_alert() fires after every successful X post
- Ed25519 signing: all API signal responses signed (_sign_payload() in octo_api_server.py)
- Evening journal: reads @KobeissiLetter + 8 other X accounts for news context
  _load_calls() bug fixed 2026-05-01: was filtering out call_type=polymarket entries, leaving model
  with no call data → hallucinated open Polymarket position. Fix: include ALL calls; explicit
  "No open oracle calls" context line; hard SYSTEM rule: context is ground truth, never invent positions.
- buy-api.html: Transak card payment integration reverted 2026-05-01. Wallet-only USDC/Base flow restored.
  buy-guide.html was already reverted prior session. Both buy pages now wallet-only.
- SKIP post protection: _is_internal_reasoning() in octo_x_poster.py + hard block inside
  _post_single() (nuclear gate). Patterns: SKIP prefix, curly-brace reasoning {}, keyword phrases
  ("no connection to the asset", "this is a litecoin story", etc.). Hard-earned: one leaked live.
- Grok sentiment rebuilt: 50 named target accounts, 60-min window, 10-min cache, price context
  injected, lag detection (LAGGING/LEADING/ALIGNED), confidence auto-downgraded if <5 active accounts.
- Smithery MCP: 90/100 -> targeting 98+ after 2026-04-30 fixes:
  - Output schemas: all 10 tools now return _OracleDict(BaseModel, extra=allow) in octo_api_server.py
    This declares valid outputSchema (object type) in tools/list for every tool.
  - Naming: renamed who_is_octodamus->get_octodamus_info, buy_guide->get_guide_info,
    buy_premium_api->get_premium_api_info in octo_mcp_server.py + octo_api_keys.py
  - smithery.yaml description rewritten: agent-first, tool examples up front
  - Note: octo_mcp_server.py tools (TextResult wrappers) are NOT served by Smithery --
    Smithery connects to octo_api_server.py /mcp endpoint only.
- Glama MCP: improved docstrings on get_oracle_signals + get_prices, renamed buy_* -> get_*.
  Awaiting Glama forced rescan (email Frank Fiegel).
- xstats banner: site auto-fetches /api/xstats every 15 min. Followers/posts update via
  Telegram /xstats command (writes data/dashboard_metrics.json -> served by API).
- Oracle scorecard: filter call_type != "polymarket" everywhere (Ben, evening journal, API, scorecard)
- ACP silence monitor: only restarts if Python process is confirmed dead (PID check), 6h cooldown
- X reply engine (octo_x_engage.py): two-phase session — harvest feedback from replies-to-our-replies
  (distill into octodamus_core.md via Haiku), then engage candidates from 19 watched accounts.
  State: data/x_engage_state.json. Limits: 2 replies/session, 6/day, 10h cooldown/account, 5h tweet age.
  Core memory loop: each distilled lesson is read back on next session → compounding voice improvement.
  19 accounts: KobeissiLetter, RaoulGMI, CryptoHayes, PeterSchiff, MacroAlf, LynAldenContact,
  saylor, woonomic, DylanLeClair_, 100trillionUSD, NorthStarBTC, glassnode, WillyWoo, CryptoQuant_io,
  unusual_whales, AutismCapital, virtuals_io, balajis, Polymarket
- Signal post formula fix (octo_eyes_market.py): replaced hardcoded "lead with number" system prompt
  with canonical build_x_system_prompt() + get_voice_instruction() per post + _POST_STRUCTURES (6
  templates: DATA_LEAD, THESIS_LEAD, SINGLE_LINE, HISTORICAL_PARALLEL, MACRO_FRAME, OPEN_QUESTION)
  selected randomly. Banned-closer list added. Fixed UTF-8 curly quote encoding bug (pre-existing).
- Auto-dedup duplicate processes: dedup_processes() wired into octo_watchdog.py run_check() (every
  5 min) and octo_health.py run_health_check() (daily). Keeps newest PID, kills older duplicates.
  First run killed 2 duplicates immediately (telegram_bot + octo_boto).
- TokenBot_NYSE_Base: paper trading agent for Dinari dShares on Base. $1k virtual portfolio.
  Signal gate: 2+ signals aligned (oracle + congressional/macro/sentiment). 6:15am/4pm own emails.
  Live flip: >60% win rate on 20+ trades -> Aerodrome DEX on Base. Sell story: tokenized NYSE crowd.
  Stats block added to octo_agent_report.py (reads state.json, shows cash/P&L/record/positions).
  Added to octo_memory_distill.py weekly pass alongside the other 8 agents.

---

## Persistent Memory System (new 2026-04-28)
- octo_memory_db.py     SQLite store (data/octodamus_memory.db)
  Tables: skill_posts, skill_amendments, calibration_estimates, ben_sessions, ben_lessons
  read_core_memory(agent_name) — reads [agent]_core.md, returns for any agent
- octo_memory_distill.py  Weekly Haiku distillation of session history -> core memory
  Agents: octodamus, octoboto, ben, nyse_macromind, nyse_stockoracle,
          order_chainflow, x_sentiment_agent, nyse_tech_agent

---

## x402 Services Live (api.octodamus.com)
- GET /v2/signal ($1.00)                   oracle signal composite
- GET /v2/ben/sentiment-divergence ($0.50) Fear/Greed vs crowd divergence
- GET /v2/ben/bens_btc_contrarian_alert ($0.25) Ben's BTC contrarian alert
- GET /v2/ben/bens_agent_context_pack ($0.35)   Ben's full context bundle
- GET /v2/guide/derivatives ($3.00)        derivatives deep dive
- GET /v2/nyse_macromind/macro ($0.25)     macro regime signal
- GET /v2/nyse_macromind/yield-curve ($0.15) yield curve read
- GET /v2/nyse_macromind/fed-probability ($0.15) Fed rate probability
- GET /v2/nyse_stockoracle/signal ($0.25)  stock signal
- GET /v2/nyse_stockoracle/congress ($0.20) congressional trading
- GET /v2/order_chainflow/delta ($0.20)    Binance cumulative delta
- GET /v2/order_chainflow/dex ($0.15)      DEX volume flow
- GET /v2/order_chainflow/whales ($0.25)   whale activity
- PENDING: /v2/x_sentiment/* and /v2/nyse_tech/* endpoints (not yet added to API server)

## ACP (Virtuals) Offerings Live
- Grok Sentiment Brief ($1): offering 019dca02-a0c3-7b39-8efe-1279c5cb9307
- Divergence Alert ($2): offering 019dca05-6bdc-7228-adc2-f00585f46af1
- Smithery Onboarding ($1): offering 019dced5-0162-7411-a1b4-b8b32ae3c4c7
- Overnight Asia Brief ($2): offering 019dced5-7395-7726-b2e5-27299b9003d3
- Events: data/acp_events.jsonl
- Report: python octo_acp_report.py morning|evening
- ACP top buyer: 0x755d... (concentration risk — Smithery funnel = diversification)
- Completion rate: was 55% (CLI verification timeout bug). Fixed — expect 90%+ going forward.

---

## CDP SDK + Base Chain Query (new 2026-05-01)
- octo_cdp_trade.py         CDP Python SDK swap utility. get_quote(from,to,amount,wallet) confirmed live.
  CDP_API_KEY_ID + CDP_API_KEY_SECRET in .octo_secrets. execute_swap() needs a CDP EVM account object.
  No Electron/awal required — pure Python, works on Windows.
- Order_ChainFlow: tool_query_base_events() + tool_get_dex_swap_volume() added.
  Uses Base public RPC (mainnet.base.org, free, no API key). eth_getLogs via JSON-RPC.
  _TOPIC0_MAP has pre-computed keccak256 hashes for Transfer, Swap (V2+V3), Mint, Burn.
  500 blocks = ~15 min of chain history on Base. 1000 blocks = ~30 min.
  Aerodrome USDC/WETH CL pool default: 0xb2cc224c1c9feE385f8ad6a55b4d94E92359DC59
  NOTE: Dinari dShare pools (dAAPL, dTSLA) not yet found on DexScreener — may lack DEX liquidity.
  Add addresses to SYSTEM prompt when Aerodrome pairs are confirmed live.
- Skills installed: C:\Users\walli\octodamus\.agents\skills\ (all 9 Coinbase agentic-wallet-skills)
  query-onchain-data + trade + authenticate-wallet + pay-for-service + search-for-service + others.
  awal/payments-mcp Electron bridge does NOT work on Windows (spawn EINVAL). Use CDP Python SDK instead.

## Key Modules (current)
- octodamus_runner.py        Main runner, all --mode flags; Signal 13 (TradingView) wired in
- octo_tradingview.py        Signal 13: TradingView 1h+4h TA_Handler, BINANCE feed, 15-min cache
- octo_personality.py        Voice/identity -- ONLY place to change character
- octo_x_poster.py           X posting, ensure_cashtag(), SKIP post guard, Telegram reply alert
- octo_api_server.py         FastAPI, x402 endpoints, Ed25519 signing, ERC-8004 card
- octo_acp_worker.py         Virtuals ACP handler, event-stream trust (no CLI verify)
- octo_acp_monitor.py        ACP silence monitor (PID check before restart, 6h cooldown)
- octo_evening_journal.py    5pm journal post; core memory context; polymarket calls excluded
- octo_format_engine.py      Format rotation; SKIP detection via startswith()
- financial_data_client.py   Market data (Kraken primary, CoinGecko fallback)
- octo_macro.py              FRED cross-asset macro signal (5 series)
- octo_flights.py            Aviation volume + TSA travel signal
- octo_x_engage.py           X reply engine: 2-phase (harvest → engage), compounding memory loop
                             19 watched accounts, 2/session 6/day rate limits, data/x_engage_state.json
- octo_notify.py             Centralized email alert system (data source failures)
- octo_health.py             Health check + send_email_alert(); dedup_processes() at top of daily run
- octo_ceo.py                CEO sandbox: research, newsletter, memory, state
- octo_firecrawl.py          Firecrawl web intel
- octo_gdrive.py             Google Drive backup (full zip, every 4h)
- telegram_bot.py            Telegram bot (/myid, /ben, all commands)
- octo_memory_db.py          SQLite persistent memory (all agents)
- octo_memory_distill.py     Weekly Haiku distillation -> core memory files
- octo_polymarket_clob.py    CLOB order book depth (Signal 11 — spread, pressure, quality)
- octo_binance_delta.py      24h cumulative buy/sell delta (Signal 12 — acceleration, divergence)
- octo_boto_autoresolve.py   Tiered thresholds; calls record_outcome() after close_position()
- octo_boto_oracle_bridge.py P&L email fixed (exit_ = 1.0/0.0, uses tracker pnl field)
- .agents/profit-agent/agent.py   Agent_Ben with memory tools + competitor intel tool
  Session 8 fixes (2026-05-01): Limitless corrected to 4h max; morning SESSION_FOCUS adds SUB-AGENT
  SYNTHESIS step; evening mandates save_draft ONLY (no draft_content duplicates); overnight ACP P&L
  in header; X post opener rules (first word = number/ticker/verb, no greetings/dates, max 2 hashtags);
  LIMITLESS STRUCTURAL CHECK at 10+ consecutive empty sessions; 4-condition gate corrected to 4h only.
- .agents/nyse_macromind/agent.py NYSE_MacroMind — FRED macro (5:45am PST)
- .agents/nyse_stockoracle/agent.py NYSE_StockOracle — congressional trading (5:50am PST)
- .agents/order_chainflow/agent.py Order_ChainFlow — on-chain delta (5:55am PST)
  Session 8 fix: CONFIDENCE CALIBRATION RULE added — HIGH CONVICTION requires whale data + retail delta
  >55% + cross-signal aligned. "ACCUMULATION (High conviction)" without whale data = category error → MEDIUM.
- .agents/x_sentiment_agent/agent.py X_Sentiment_Agent — crowd sentiment (6:00am PST)
- .agents/nyse_tech_agent/agent.py NYSE_Tech_Agent — SEC/Chainlink/tokenization (6:05am PST)
  Session 8 fix: max_tokens 1500→2000; REPORT COMPLETENESS RULE added (never truncate mid-sentence).

---

## Secrets & Auth
- All secrets via Bitwarden (run octo_unlock.ps1 to load session)
- .octo_secrets (JSON) at project root -- never commit
- Key services: Anthropic, X/Twitter OAuth 1.0a, Kraken, OKX, Quiver, FRED, Tavily
- OpenRouter: OPENROUTER_API_KEY in .octo_secrets
- Unusual Whales: module built (octo_unusual_whales.py), key not yet active
- Agent_Ben wallet (Franklin): "AGENT - Agent_Ben - Wallet (Franklin)" in Bitwarden
  ~$201 USDC on Base. Used ONLY for Limitless Exchange trading. Never mix with OctoBoto.
- OctoBoto wallet: 0x89a430d9fA88EEdD96565314438EEF8258F0A58c — separate from Ben's Franklin wallet
  "AGENT - OctoBoto - Wallet" in Bitwarden. Trades on Polymarket only. Records in octo_boto_trades.json.
  OCTOBOTO_PRIVATE_KEY not yet in .octo_secrets — add it to enable octo_boto_clob.py.
- Agent_Ben API key: BEN_OCTODATA_API_KEY in .octo_secrets (admin key for premium signal access)
- Sub-agent wallets: in .octo_secrets as NYSE_MACROMIND_PRIVATE_KEY etc.
  Bitwarden entries (all saved 2026-04-28, username=address, password=private key):
    "AGENT - NYSE_MacroMind - Wallet"      0xA0f940469EDa402de08A8ea1B4a730e43e317035
    "AGENT - NYSE_StockOracle - Wallet"    0x46037F1a6D10308c9892f297a0d419aAA25131A4
    "AGENT - Order_ChainFlow - Wallet"     0xa78CfD4B00b96bA6090013f20095E7aC3B87E1B9
    "AGENT - X_Sentiment_Agent - Wallet"   0x3917798a66CF1e1ad453F8c7ffcD780A1B40A0B2
    "AGENT - NYSE_Tech_Agent - Wallet"     0x40e77Ae8Cc09Ff4456EDa6dF661e2C72C40e6672
    "AGENT - Agent_Ben - Wallet (Franklin)" 0xAA903A56EE1554DB6973DDEff466f2cD52081FbA
    "AGENT - OctoBoto - Wallet"            0x89a430d9fA88EEdD96565314438EEF8258F0A58c
  OctoBoto and Ben are SEPARATE. Ben trades Limitless+Polymarket (paper). OctoBoto trades Polymarket (paper until profitable).
- Limitless API: LIMITLESS_API_KEY in .octo_secrets (token ID as username, HMAC as password)

---

## Pending / Active Work
### Immediate
- [ ] Add X_Sentiment_Agent + NYSE_Tech_Agent x402 endpoints to octo_api_server.py
- [x] All 5 sub-agent + OctoBoto wallet private keys saved to Bitwarden (2026-04-28)
- [ ] Fund sub-agent wallets: ~5 USDC each on Base (~$25 total) for x402 buying capability
      Priority: Agent_Ben first (already buying), then Order_ChainFlow, then others
- [ ] OctoBoto Polymarket wallet: generate new wallet (eth_account.Account.create())
      Add OCTOBOTO_PRIVATE_KEY + OCTOBOTO_WALLET_ADDRESS to .octo_secrets
      Add "AGENT - OctoBoto - Wallet" to Bitwarden
      Fund with $30-50 USDC on Polygon + $2-3 MATIC for gas
      Then flip LIVE_MODE = True in octo_boto_clob.py

### Near-term
- [ ] Register all 5 sub-agents on Virtuals ACP marketplace (own offerings)
- [ ] Build 5 organic AI search pages on octodamus.com (zero cost, highest ROI):
      "Best crypto signal API alternatives 2026", "Polymarket prediction tools compared",
      "Alternative data for crypto trading", "Best macro signal API for crypto 2026",
      "Free crypto oracle API alternatives to Bloomberg"
- [ ] Agent_Ben PAPER_MODE: review paper trade log, flip to False
- [x] BITC-REAC-AP: resolved 2026-04-30 as WIN (NO side, BTC peaked ~$78K, never hit $80K)
- [ ] mcp.so submission
- [ ] Datarade vendor listing
- [x] OrbisAPI marketplace: description + tags updated via provider API (2026-04-30)
      Endpoint list (11 endpoints) must be added manually via Orbis web dashboard -- no REST API for endpoints
- [ ] x402 bazaar: submit Octodamus listing

### Gated by Revenue
- [ ] Unusual Whales key: waiting for $500/mo MRR trigger (~$50/mo)
- [ ] Newsletter (OctoIntel Weekly): gate is July 2026 (3-month track record)
- [ ] Snowflake Data Marketplace: after $500/mo MRR
- [ ] Create X accounts for sub-agents: when ACP revenue starts flowing

### Monitor
- GenLayer Bradbury testnet: email octodamusai@gmail.com when mainnet launches on Base
- HIP-4 (Hyperliquid): watch for validator proposal passing
- $OCTO token: build when ready, 1-command onboarding priority

---

## Revenue Milestones
- Pre-revenue (current)
- $500/mo: subscribe Unusual Whales, evaluate newsletter acquisition
- $2k/mo: programmatic SEO (10k pages)
- $5k/mo: content repurposing engine
- Early bird: $29/yr (first 100 seats), Standard: $149/yr, Pro: $49/mo, Enterprise: $499/mo
- NO lifetime subs (API cost risk)

---

## Hook Infrastructure
- PostCompact hook: .claude/hooks/post_compact.py — copies project_state.md to Downloads
- Stop hook: .claude/hooks/context_watch.py

---

## Claude.ai Project Sync
- When significant changes: update this file, copy to Downloads
- User re-uploads only project_state.md to Claude.ai Project "Octodamus"
- Files in Downloads: octo_CLAUDE.md, octo_architecture.md, octo_coding.md,
  octo_signals.md, octo_distro.md, octo_future.md, octo_project_state.md

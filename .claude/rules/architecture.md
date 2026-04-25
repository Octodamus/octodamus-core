# Architecture — Octodamus

## Voice & Identity
- `octo_personality.py` — single source of truth for Octodamus voice AND oracle knowledge
- Change voice/identity here only; propagates to runner, format engine, Telegram, MCP
- Character anchors: McGuane, Druckenmiller, Livermore, Taleb, Tool

## Auto-Update Rule (CRITICAL)
When adding new oracle knowledge, capabilities, or market frameworks:
1. Add a named constant to `octo_personality.py` (e.g., `BTC_CYCLE_KNOWLEDGE`, `OCTOBOTO_CONTEXT`)
2. Reference it in `build_telegram_system_prompt()` — it auto-propagates to the Telegram bot
3. Reference it in `build_x_system_prompt()` if relevant for X posts
4. NEVER hardcode knowledge in `telegram_bot.py` or `octodamus_runner.py` directly
This ensures: new knowledge added once -> flows to Telegram, X, MCP, runner automatically.

## Octodamus vs OctoBoto Distinction
- **Octodamus** = the AI oracle. Signal generation, market analysis, X posts, API. The mind.
- **OctoBoto** = the autonomous trading bot. Executes trades on Polymarket using Octodamus signal.
  - Current: track-record building on Polymarket
  - Vision: AI-managed copytrading platform. Users deposit capital. OctoBotoAI manages sizing.
    Takes % of transaction profits. The go-to copytrading bot on the internet.
- Never conflate them in prompts, posts, or code comments.

## Signal Signing (Ed25519 — on-chain verifiable responses)
Octodamus signs signal responses with Ed25519 for on-chain verification (Mycelia-parity).
- Private key: `OCTODAMUS_SIGNING_KEY` in `.octo_secrets` (also store in Bitwarden: "AGENT - Octodamus - Signal Signing Key")
- Public key:  `OCTODAMUS_SIGNING_PUBKEY` — published in `/.well-known/x402.json` under "signing"
- Signing:     `_sign_payload(payload)` in `octo_api_server.py` — Ed25519, canonical JSON, base64 signature
- Agents verify: `signature` field in response body + `signer_pubkey` field
- Competitor: Mycelia Signal uses same approach. Octodamus differentiated by AI consensus + Polymarket edges.

## LLM Routing (octodamus_runner.py)
Three tiers — never collapse them back into a single model:
- **Sonnet 4.6** (`claude.messages.create`) — core oracle: Daily Read, Moonshot, Flow Signal
- **Haiku 4.5** (`_haiku_generate`) — voice-critical short posts: Wisdom, Soul, Watchpost, Thread opener/builder
- **OpenRouter Llama-4-Maverick:free** (`_claw_generate`) — data-constrained posts: Congress, GovContracts, Liquidation Radar
  - Key: `OPENROUTER_API_KEY` in `.octo_secrets`
  - Falls back to Haiku automatically if OpenRouter fails
  - No local daemon required (ClawRouter task deleted 2026-04-23)
- **Haiku 4.5** (direct in `octo_format_engine.py`) — format rotation posts

## Scheduled Tasks (24 total in Windows Task Scheduler)
- Octodamus-DailyRead / DailyRead-7pm / DailyRead-330am — 3:30am + 5am + 7pm briefings
- Octodamus-Monitor-7am / Monitor-4pm     — market monitor posts
- Octodamus-Thread-Mon / Thread-Wed       — weekly threads (9 AM)
- Octodamus-Format-12pm                   — format rotation post
- Octodamus-Wisdom / Soul                 — personality posts
- Octodamus-StrategySunday / StrategyMonitor
- Octodamus-Telegram                        — Telegram bot (auto-restart on crash)
- Octodamus-QRT-Scan / Congress / Mentions
- Octodamus-AutoResolve / BotoResolve     — Polymarket resolution
- Octodamus-GDrive-Backup                 — full zip backup every 4 hours
- Octodamus-API-Server / ACP-Worker / Cloudflared — always-on services
- Octodamus-FlightSample                — daily aviation volume sample (noon UTC)
- Octodamus-XStats / HealthCheck

## Key Files
- `octodamus_runner.py`        — main runner, all --mode flags
- `telegram_bot.py`            — Telegram bot, all commands
- `octo_x_poster.py`           — X posting engine
- `octo_health.py`             — health check (run to diagnose issues)
- `octo_personality.py`        — voice/identity module
- `financial_data_client.py`   — market data aggregator
- `octo_gdrive.py`             — Google Drive backup (full zip, every 4h)
- `octo_skill_log.py`          — skill/prediction logging
- `octo_flights.py`            — aviation volume signal (OpenSky, week-over-week delta)
- `octo_macro.py`              — cross-asset macro signal (FRED: yield curve, DXY, SPX, VIX, M2)
- `octo_unusual_whales.py`     — options flow + dark pool signal (Unusual Whales API, key needed)

## Data Files
- `octo_engage_state.json`     — post/engagement tracking state
- `xstats.json`                — X account stats cache

## Deployment Endpoints
- API:       api.octodamus.com (Cloudflare tunnel → local API server)
- MCP:       octodamusai/market-intelligence on Smithery (run.tools)
- Site:      octodamus.com (GitHub → Vercel/static)

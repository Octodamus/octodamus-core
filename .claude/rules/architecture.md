# Architecture — Octodamus

## Voice & Identity
- `octo_personality.py` — single source of truth for Octodamus voice
- Change voice/identity here only; propagates to runner, format engine, Telegram, MCP
- Character anchors: McGuane, Druckenmiller, Livermore, Taleb, Tool

## Scheduled Tasks (22 total in Windows Task Scheduler)
- Octodamus-DailyRead / DailyRead-7pm     — morning + evening briefing
- Octodamus-Monitor-7am / Monitor-4pm     — market monitor posts
- Octodamus-Thread-Mon / Thread-Wed       — weekly threads (9 AM)
- Octodamus-Format-12pm                   — format rotation post
- Octodamus-Wisdom / Soul                 — personality posts
- Octodamus-StrategySunday / StrategyMonitor
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
- `data/botcoin_credits.json`  — BOTCOIN mining history per epoch
- `data/botcoin_auth.json`     — coordinator bearer token cache
- `octo_engage_state.json`     — post/engagement tracking state
- `xstats.json`                — X account stats cache

## Deployment Endpoints
- API:       api.octodamus.com (Cloudflare tunnel → local API server)
- MCP:       octodamusai/market-intelligence on Smithery (run.tools)
- Dashboard: http://localhost:8901 (BOTCOIN mining dashboard)
- Site:      octodamus.com (GitHub → Vercel/static)

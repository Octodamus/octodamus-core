# External Signals — Octodamus

## Aviation Volume Signal (octo_flights.py)
Tracks global airborne aircraft count as a macro leading indicator.

- Source: OpenSky Network /api/states/all (free, no auth required)
- Samples: daily at noon UTC via Octodamus-FlightSample task
- Cache: data/flights_cache.json (30-day rolling window)
- Signal logic: week-over-week 7-day average delta
  - >= +3%  → RISK-ON
  - <= -3%  → RISK-OFF
  - between → NEUTRAL
- Warm-up: 14 days of samples needed before signal goes live (started 2026-04-14)
- Wired into: octodamus_runner.py daily read prompt + thread mode context
- Injected as: "Macro Transport Signal: AVIATION SIGNAL: ..."
- Optional: add OPENSKY_USER / OPENSKY_PASS to .octo_secrets for higher rate limits

## US Air Passenger Throughput (octo_flights.py)
Tracks daily US checkpoint passenger counts as a travel demand macro signal.

- Source: TSA.gov /travel/passenger-volumes (BeautifulSoup table scrape)
- Refresh: 24h cache wrapper in get_tsa_signal(); fetches fresh if cache stale
- Cache: data/tsa_cache.json
- Signal logic: 7-day rolling average vs prior 7 days
  - >= +3%  → RISK-ON
  - <= -3%  → RISK-OFF
  - between → NEUTRAL
- Combined conviction: if aviation + TSA both agree (non-NEUTRAL) → COMBINED TRAVEL SIGNAL note
- Injected as: "TSA TRAVEL SIGNAL: ..." via get_hotels_context()
- Full combined context: get_travel_context() → injected into daily brief + thread mode

## Cross-Asset Macro Signal (octo_macro.py)
5 FRED series scored as crypto tailwinds/headwinds. Sum >= +2 = RISK-ON, <= -2 = RISK-OFF.

- Source: FRED API (Federal Reserve, free key from fred.stlouisfed.org)
- Package: fredapi (pip install fredapi)
- Key: FRED_API_KEY in .octo_secrets
- Series: T10Y2Y (yield curve), DTWEXBGS (USD index), SP500, VIXCLS, M2SL
- Cache: data/macro_cache.json (refresh every 4 hours)
- Injected as: "Cross-Asset Macro: ..." via get_macro_context()
- Wired into: daily brief + thread mode context

## Options Flow & Dark Pool Signal (octo_unusual_whales.py)
Institutional options sweeps, dark pool block prints, market tide (call/put net premium).

- Source: Unusual Whales API (unusualwhales.com, ~$50/mo)
- Key: UNUSUAL_WHALES_API_KEY in .octo_secrets
  Bitwarden entry: "AGENT - Octodamus - Unusual Whales API"
- Tickers watched: IBIT, ETHA, FBTC, COIN, MSTR, HOOD, BITO + any $1M+ premium
- Cache: data/unusual_whales_cache.json (refresh every 15 min)
- Injected as: "Options Flow & Dark Pool: ..." via get_uw_context()
- Status: MODULE READY -- activate by adding API key
- To test once key is added: python octo_unusual_whales.py --test

## Congressional Trading Signal (octo_congress.py)
Tracks buy/sell trades by members of Congress as a smart-money signal.

- Source: QuiverQuant API (quiverquant.com, Hobbyist tier ~$10/mo)
- Package: quiverquant Python library (`quiver.congress_trading(ticker)`)
- Key: QUIVER_API_KEY (env) or octo_quiver_key.txt (fallback); Bitwarden: "AGENT - Octodamus - Quiver API"
- Used in: octo_congress.py, octo_report_handlers.py, octo_acp_worker.py
- Injected into: Virtuals ACP stock reports (alongside price + fundamentals)
- Scheduled task: Octodamus-Congress

## Calibration Signal (octo_boto_calibration.py)
Per-confidence-tier bias correction injected into Polymarket prompts.
See .claude/rules/botcoin.md for full details.

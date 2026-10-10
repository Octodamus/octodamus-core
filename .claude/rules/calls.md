# Call Policy — Octodamus (2026-09-11)

The on-chain record is the product. These rules are derived from re-scoring the real
record (32 resolved crypto calls with tx_hash), not from theory. Do not relax them
without a new backtest that shows they cost wins.

## The two hard rules (enforced in code, not prompts)
1. **Never call against the trend.** `octo_regime.trend_gate(asset, direction)`:
   - DOWN only in a confirmed downtrend: 7d change < 0 AND price below 20d SMA.
   - UP blocked only into a confirmed downtrend (bias DOWN); allowed if 7d > 0 OR above SMA20.
   - No trend data -> no call (fail closed; a skipped call is free, a blind one is permanent).
2. **Minimum 48h horizon.** `octo_calls.MIN_CALL_HOURS`. The WIN rule needs >=1% at expiry
   or the target touched; sub-day calls cannot clear it. Raised 24h -> 48h on 2026-09-16
   after several live 24h losses resolved as wins at 48h (matches the 48h 15W-17L re-sim).
   Named-day expiries ("Friday close") are measured from now and must also clear 48h.

Both run in `octo_calls.call_policy_check()`, called by `record_call()` AND
`commit_call_onchain()`. Every strategy also calls the gate early (before Coinglass/gas).
A rejected call prints `REJECTED by call policy` and emails via octo_notify.

## Target touch: same rule for crypto and stocks (2026-10-09)
`_target_hit_during_window()` credits a target touched anywhere in the call window: crypto via
the CoinGecko range, stocks via Yahoo hourly high/low (regular session only, so an overnight-only
touch is missed -- conservative). Until 2026-10-09 stocks were judged at expiry only, so #66 TSLA
DOWN traded through 349.43 (low 345.88) and settled a LOSS at 355.13. It stays a LOSS: published
outcomes are never re-scored. Of the 8 resolved stock calls with a target, #66 is the only one
the old asymmetry changed.

## What the backtest showed (in-sample, small N — treat as direction, not precision)
- Actual: 10W-22L (31%). Trend-opposed calls: 0W-7L. Removing them: 10W-15L (40%).
- DOWN outside a confirmed downtrend: 2W-13L. Strict gate survivors: 8W-9L (47%).
- Same calls re-simulated at 6h: 1W-31L; 24h: 12W-20L; 48h: 15W-17L.
- range_scout 6h book 0W-9L; the same setups at 24h 5W-4L. It now uses 24h / 2% target.
- [Superseded 2026-10-09: now 7W-4L, UP 7-0 / DOWN 0-4; the edge looks like trend, not funding -- see the strategy table. DOWN paused.] funding_extreme (3W-1L) is the only strategy with edge: UP squeezes on red days inside a
  7d uptrend. The gate keeps all three wins and blocks the one loss (#53 SUI DOWN).
- crowd_fade 2W-8L. The May losses (#32-37) fired DOWN after -8..-11% weeks: a crowd long
  AFTER a flush is capitulating, not trapped. The gate does not fix crowd_fade; the circuit
  breaker (`strategy_should_pause`, now 14d cooldown) is what protects the record. Its task
  is Disabled — leave it unless a new backtest justifies it.
- Backtest script: `python octo_backtest_calls.py` -- run it again before touching thresholds.

## Things that were silently broken (fixed 2026-09-11)
- crowd_fade's 7d trend gate read api.binance.com -> HTTP 451 -> 0.0% on every call. Never blocked.
- crowd_fade's funding confirm used raw[0] (BTC's row) for every asset.
- Signal 12 (Binance delta) dead since 2026-06-20 (451). Now falls back to api.binance.us.
- Coinbase premium reference price (Binance, 451). Now falls back to OKX spot (offshore, as intended).
- `_fetch_market_snapshot` funding parse raised on the V4 list shape; strategy snapshots used
  short keys (`fng`, `chg_24h`) that nothing read. Post-mortems ran with F&G=None. Normalized.
- `build_call_context()` told 5 post modes "You MUST make exactly one Oracle call" while the
  same prompt and octo_x_poster forbade it: 12 posts in Aug-Sep were generated, then blocked.
  The rules now live in `build_call_rules()` (unused until a mode parses calls again).

## Strategy status
| strategy        | record  | task            | notes |
|-----------------|---------|-----------------|-------|
| funding_extreme | 7W-4L   | every 4h        | **UP only** -- DOWN paused 2026-10-09 (0W-4L). All 7 wins were UP in uptrends; every fired call's mean was pulled over the threshold by one venue at 9-37x baseline while the median sat near zero, so the edge is trend, not funding. Every scan is logged to `data/funding_scan_log.jsonl` for a real backtest. Public wording: "funding tilt, trend-gated", never "extreme". |
| stock_extreme   | 0W-1L   | every 2h        | trend-aligned by design; gated again at record time; 48h |
| oracle (13-sig) | 5W-7L   | monitor 7am/4pm | STRONG threshold + MTF; gated at record time |
| range_scout     | 1W-8L   | **Disabled**    | 48h/2% + gate now; re-enable only after a fresh look |
| crowd_fade      | 2W-8L   | **Disabled**    | gated; still no evidence of edge |

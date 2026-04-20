# Polymarket V2 Migration — Octodamus

## Status: COMPLETE (migrated 2026-04-17)

## Timeline
- **April 22, 2026 ~11am UTC** — V1 exchange shuts down (~1h downtime), production URL serves V2
- **April 17** — Octodamus migrated to V2 SDK (5 days early)
- **Announcement**: https://x.com/PolymarketDevs/status/2045173502328594677
- **Migration guide**: https://docs.polymarket.com/v2-migration

## What Changed in Octodamus
- SDK: `py-clob-client` -> `py-clob-client-v2==1.0.0` (import: `py_clob_client_v2`)
- `octo_boto_clob.py`: imports updated, new contract addresses, pUSD collateral
- `V2_READY = True` in `octo_boto_math.py` — market entry block lifted

## V2 Contract Addresses (Polygon, chain_id 137)
- pUSD token: `0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB`
- CollateralOnramp: `0x93070a847efEf7F70739046A929D47a521F5B8ee`
- CTF Exchange (standard): `0xE111180000d2663C0091e4f400237545B87B996B`
- Neg Risk Exchange: `0xe2222d279d744050d28e00520010520000310F59`

## Key V2 SDK Notes
- `ClobClient` constructor: `chain_id` param name unchanged in Python SDK
- `OrderArgs`: removed `nonce`/`feeRateBps`/`taker`; added optional `expiration`/`builder_code`/`metadata`
- Core order call `OrderArgs(token_id, price, size, side)` unchanged
- L1/L2 auth headers unchanged — existing API creds still valid
- Production host: `clob.polymarket.com` (serves V2 after April 22)
- Test host: `clob-v2.polymarket.com` (before April 22)

## When Going LIVE (LIVE_MODE = True)
Before enabling live trading, must wrap USDC.e to pUSD:
```python
# Call wrap() on CollateralOnramp: 0x93070a847efEf7F70739046A929D47a521F5B8ee
# See: https://docs.polymarket.com/v2-migration
```

## What's NOT Affected
- `gamma-api.polymarket.com` — separate read API, unaffected by exchange migration
- `octo_boto_polymarket.py` — Gamma client, no changes needed
- `octo_boto_autoresolve.py` — Gamma API resolution, no changes needed
- Oracle calls — don't interact with exchange at all

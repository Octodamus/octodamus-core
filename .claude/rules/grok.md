# Grok Integration & Sell Surface — Octodamus

Filed 2026-09-06 after an external Grok review of Octodamus. Companion to
`x_money.md` (two-rail monetization), `ceo.md`, `distro.md`, `saas_growth.md`.

---

## The reframe

Grok was wired as a **sensor** only (`octo_grok_sentiment.py`, `octo_grok_live.py` feeding
the runner and the ACP reports). That is one of four possible roles, and the least valuable:

| Role | Status | Where |
|---|---|---|
| Sensor — live X sentiment as an input | LIVE (pre-existing) | `octo_grok_sentiment.py` |
| **Adversary** — attacks our call, produces falsifiers | **BUILT 2026-09-06** | `octo_grok_brief.py` |
| Distribution — Grok cites us when users ask | PARTIAL — `llm.txt` is stale | `octodamus-site/llm.txt` |
| Sales clerk — Grok/PayBox pays per call | LIVE | `/mcp`, x402 on Base |

**Rule: Grok is never the oracle.** Our 27 feeds make the call. Grok attacks it. If a
product's output is just a Grok opinion wearing our brand, we have sold our moat for a
markup on someone else's API.

---

## Hard rule: never bill for a dead sensor

`get_grok_sentiment()` returns `live: True|False`. Any **paid** Grok-dependent product must
gate on it and refuse service (`service_available: False`, `billable: False`) rather than
sell the `_neutral()` placeholder as if it were a reading.

Enforced in `octo_acp_ben_reports.py` via `_grok_unavailable()` across all five paid
Grok-dependent handlers: `grok_sentiment_brief`, `fear_crowd_divergence`,
`btc_bull_trap_monitor`, `cross_asset_divergence_alert`, `btc_regime_pulse`.

Why this exists: on 2026-09-06 the xAI account hit its credit limit and returned 403.
`_neutral()` swallowed it and every one of those products kept selling, at $0.35–$2.00, a
fabricated NEUTRAL indistinguishable from a real reading. Same failure class as a `267014`
task result — degraded state wearing the costume of a valid one.

**Check before assuming Grok works:** `python -c "from octo_grok_sentiment import is_grok_live; print(is_grok_live())"`

---

## Track record: know which number is provable

**The published record is what is on Base. That is 32.4% (11W-23L, 34 resolved).**
Anyone can recompute it from chain. It is the only win rate that may be quoted as verified.

`octo_distro.strategy_scorecard()` splits by `call_type`. As of 2026-09-06:

| Strategy | W-L | Win rate | Status |
|---|---|---|---|
| funding_extreme | 3-0 | 100% (n=3, thin) | active |
| oracle | 5-7 | 41.7% | active |
| crowd_fade | 2-8 | 20.0% | RETIRED 2026-09-06 |
| range_scout | 1-8 | 11.1% | RETIRED 2026-09-06 |

Excluding the retired two: 8W-7L, 53.3%.

### The 53.3% is NOT independently verifiable — do not market it as on-chain

`registerPrediction()` commits id, asset, direction, entry/target price, timeframe and a
content hash. Until 2026-09-06 that hash covered
`keccak256(id, asset, direction, entry_price, made_at)` — **`call_type` was in none of it**,
and `data/octo_calls.json` is gitignored, so there is no independent timestamped record of
which strategy produced a historical call. A third party verifying from Base gets 32.4% and
cannot reproduce any breakdown of it.

Quoting 53.3% as an on-chain number is exactly the "signal-seller theater" failure. Use it
internally to decide what to keep running. Externally, quote 32.4% and show the split as a
stated internal breakdown.

**Fixed forward-only:** `_make_content_hash()` is now versioned. v2 adds `call_type` to the
commitment and `publish_prediction()` stamps `hash_version: 2`; `commit_call_onchain()`
persists it beside `tx_hash`. Calls without `hash_version` hash as v1, so all 35 already
published calls still verify byte-identically. Historical hashes cannot be retrofitted --
present it as *"strategy labels verifiable on-chain from call #N onward"* and never imply
the earlier ones are.

Retire a strategy via `data/retired_strategies.json` — marked, never deleted. Losses are
immutable on Base; retiring changes what gets published going forward, not what is
published. A track record you can edit is not a track record.

**Do not amplify the blended win rate in marketing while it is below 50%.** The review's
"publish W/L loudly" advice assumes the record supports it. Lead with falsifiers and the
per-strategy split, labelled honestly as internal until v2 calls accumulate.

---

## Sell surface — X and x402

### Built 2026-09-06
- `/v2/grok/brief` — $0.10. Octodamus signal + Grok adversarial critique + falsifiers.
- `/v2/grok/brief/preview` — free, unauthenticated, Grok-citable. Bias + crowd agreement
  + ONE falsifier. Designed to be quoted by an assistant and to leave the rest paid.
- `guides/grok_research_desk.md` — $3.00 guide, chapter 1 free as the teaser.

### Next, in priority order
1. **Refresh `llm.txt`** — currently stamped "May 2026". Staleness is the single cheapest
   thing costing us assistant citations. Add a dated "live signal, last updated" block.
2. **Publish `/tools/strategy-scorecard`** on the site as the proof surface, replacing any
   widget that currently reads "Loading...". The review is right that promised-but-absent
   proof is worse than no proof.
3. **MCP tool description tuned for Grok** — `get_agent_signal`: "use when the user asks
   for BTC/ETH/SOL bias, funding, or a Polymarket edge." Discovery is the constraint, not
   catalogue: there are already 38 paid x402 endpoints and the reviewer found none of them.
4. **Falsifier webhook** — `signal.resolved` push when a falsifier trips. Sells to agents
   that hold positions, not to humans who read posts.
5. **Scorecard receipt on Base** — on-chain record per resolved call. Fits the existing
   publish-first guarantee in `octo_calls`.

### X posting rule (from the review, adopted)
Lead with the number and the level. Lore below the fold or not at all. Every post with
~20 views has to do its work in the first line.

> BTC: NEUTRAL | funding 0.0024 | 51% long
> Wrong if: funding flips negative while spot holds $79,600.
> Ask Grok to stress-test this.

`format_brief_x()` in `octo_grok_brief.py` emits exactly this shape.

---

## What not to do
- Never wrap a Grok output as "the oracle has spoken." The brand is our consensus + the
  public log.
- Never buy engagement. A 63-follower account with 10k views is a costume.
- Never promise Grok integration picks winners. We sell faster structured context and
  falsifiable calls, not destiny.
- Never let a Grok-derived field reach a paying buyer without `live: True`.

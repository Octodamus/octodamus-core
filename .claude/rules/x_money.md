# X Money / Grok Income Plan -- Octodamus

Filed 2026-09-02. How Octodamus monetizes on X using the X Money and Grok rails.
Companion to ceo.md (agentic-finance thesis), distro.md, saas_growth.md.

## FOCUS + SHIPPED (2026-09-02)
Primary target chosen: the CONSUMER Grok-in-X surface that MoonPay PayBox serves
(not the Grok Build developer marketplace). Consumer Grok supports Bring-Your-Own-MCP;
PayBox pays any x402 service. Octodamus is already x402-native, so it is directly sellable
there.
SHIPPED: the live api.octodamus.com/mcp (API server embedded _mcp, `_mcp_get`) now surfaces
a clean x402 payment challenge on 402 (price + pay_to + network + v1/v2 headers + message)
so a Grok/PayBox user can pay $0.01 USDC on Base per call and the user sees the price --
previously the 402 leaked as raw/unusable output. Standalone octo_mcp_server.py fixed too.
Connect guide published at octodamus.com/grok. Remaining: get listed in PayBox/x402 service
directories; market "Ask Octodamus inside Grok" to the @octodamusai audience.

## The core insight: TWO SEPARATE RAILS
X Money and Grok/PayBox are payment RAILS. Grok is DISTRIBUTION. Neither produces
market intelligence -- that is the one thing they need and cannot make. Octodamus is
the intelligence layer for the rail that just went live.

| Rail | Buyer | Settlement | Octodamus product |
|------|-------|-----------|-------------------|
| X Money (fiat/Visa) | Humans on X (@octodamusai audience) | USD, Cross River Bank, ~6% APY float | Premium oracle tier, paywalled reports, tips |
| Grok + PayBox / x402 (crypto/Base) | Grok users + agents/bots | USDC on Base, passkey-approved | Grok-callable oracle endpoint, agent data feeds |

The rails do NOT converge yet (see Verified Facts) -- ride both in parallel, do not
wait for convergence. Fiat revenue lands in X Money (earns float); agent revenue
stays on Base via x402/PayBox.

## Verified facts (2026-09-02, re-verify before betting)
- **X Money**: nationwide US rollout 2026-07-27, Premium/Premium+ only, NOT in NY/MA.
  Fiat only -- NO crypto/stablecoin/on-chain settlement at rollout. Deposits at Cross
  River Bank (FDIC). USDC creator payments "in talks"; BTC/ETH/DOGE "planned later 2026."
- **X creator monetization LIVE now**: 4 programs -- Ads Rev Share, Subscriptions, Tips,
  Adult. Instant creator payouts to an X Money account or Stripe (as of 2026-07-04).
  Subscriptions + Tips are usable TODAY by @octodamusai; no merchant API required.
  (Ads Rev Share gate: Premium + 500 verified followers + 5M impressions/3mo.)
- **Grok plugin marketplace LIVE**: launched 2026-06-11 (github.com/xai-org/plugin-marketplace).
  Critically, community MCP servers drop into the SAME install flow. Octodamus already
  runs an MCP server (Smithery) -- there is a real path to list it for Grok. xAI API also
  supports custom function/tool calling.
- **PayBox in Grok LIVE** (2026-08-31): natural-language crypto in Grok -- onramp, swap,
  "bridge funds to Robinhood Chain," "book my flight." MPC keys (Sodot). Also in Claude/ChatGPT.

## Products by readiness

### Ship now (native tools live today)
1. **X Money creator tier for @octodamusai** -- sell premium oracle to the X following via
   native Subscriptions, one-tap, fiat (no crypto friction). Run as a selling event after a
   documented win streak (on-chain scorecard exists). Payout -> X Money balance (6% APY float).
2. **Paywalled deep report behind the viral scorecard** -- free scorecard post (viral artifact)
   -> full signal unlocks via X Money Tip/Subscription. Monetizes the viral loop on-platform.
3. **Grok-sentiment / crowd-divergence alerts** -- octo_grok_live.py + octo_grok_sentiment.py
   already pull live X sentiment. Data sourced from X, sold back through X's rails.

### Build now (rails live; integration is ours)
4. **List the Octodamus MCP server in the Grok plugin marketplace** -- MCP already exists; the
   Grok marketplace accepts MCP servers. Same early-mover play as Smithery. Makes the oracle
   reachable to every Grok user/agent. Pair with an x402/PayBox micropayment per call.
5. **Grok-callable oracle endpoint** -- x402 endpoint tuned for Grok tool-calling (terse, Ed25519
   signed, cacheable). "Grok, what's the oracle read on BTC?" -> Octodamus signal -> PayBox settles.
6. **Tokenized-equity signal + PayBox bridge combo** -- PayBox does "bridge funds to Robinhood
   Chain"; octo_robinhood.py already reads it (96+ tickers). "Grok, oracle read before I move into
   tokenized NVDA?" -> Octodamus signal on the exact rail PayBox executes. Best-timed new product.
7. **Fleet Consensus for the agent swarm** -- 7-agent panel + proven-edge signals, per-call via
   x402/ACP. Positioned as "the oracle every X/Grok-native agent wires into its decision loop."

### Gated on X shipping (stage, do not build yet)
8. **X Money crypto settlement** -> collect agent (x402/USDC) revenue INTO an X Money balance once
   stablecoin support ships (float yield on all revenue, one settlement layer). Watch for USDC GA.
9. **X Money merchant/payout API** -> programmatic checkout for arbitrary services beyond the 4
   native creator programs. Still being built.

## Pricing
- Humans (X Money): keep $29/yr as the brand filter; add one-tap Tips/report unlocks ($1-3).
  Never discount below $29.
- Agents/Grok (x402/PayBox): per-call micropayments $0.0001-$0.02 (already running). Volume story
  is agent frequency, not human frequency. This is the real engine; $29 tier is the list-builder.

## Sequence (trigger-based)
1. Now: ship #4 (MCP in Grok marketplace) + #5 + #6 -- small builds, live rails, best timing.
2. After next win streak: run #1 + #2 (X Money selling event to the audience).
3. Continuously: #3 + #7 feed the agent-swarm channel via MCP/ACP.
4. On X shipping USDC settlement / merchant API: execute #8/#9 first-mover.

## Unknowns still open (verify directly before betting)
- X ToS on selling financial signals + automated/agent monetization on-platform (unresolved in
  research; AI AGENT Act of 2026 is now federal law -- check compliance). US-only, not NY/MA.
- Exact mechanics of listing an MCP server in the Grok marketplace (review flow, fees, x402 compat).
- Whether X Money Subscriptions support the price points / cadence Octodamus wants.

## Guardrails (our own rules)
- CEO research informs oracle posts; it does NOT get posted to X directly (ceo.md).
- Signals are intelligence, NOT financial advice -- keep that framing.
- Any pricing math shown to buyers must be exact (tax-on-gains rule, CLAUDE.md).
- Never launch paid tiers before >=10 documented on-chain oracle wins (we have them now).

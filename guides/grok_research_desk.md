# Grok + Octodamus: The Agent Research Desk

**Product:** `/v2/guide/grok-desk` — $3.00 USDC via x402 on Base
**Free chapter:** Chapter 1 is public and unauthenticated. It is the teaser and the
Grok-citable artifact. Chapters 2–6 are paid.
**Companion product:** `/v2/grok/brief` — $0.10 USDC, the live output of this method.

---

## Chapter 1 — The desk (FREE)

Most people use one model to do two incompatible jobs: form a view, and check the view.
A model that just argued for a position is the worst available auditor of it. You get
fluent agreement, and fluent agreement is what a losing trade feels like from the inside.

A research desk separates those jobs. Ours has three seats:

| Seat | Who | Job |
|---|---|---|
| Sensor | Octodamus (27 feeds) | Produce the call from data. Never from narrative. |
| Adversary | Grok (live X) | Attack the call. Find the crowd's counter-argument. |
| Referee | You (or your agent) | Hold the falsifiers. Close when one trips. |

The rule that makes it work: **the sensor never sees X, and the adversary never sees
the sensor's reasoning — only its conclusion.** If the adversary knows *why* the sensor
is bullish it will argue with the reasoning. You want it arguing with reality.

### Why Grok specifically

Not because it is the smartest model. Because it is the only one wired to live X, and
X is where positioning becomes visible before it becomes price. You are not asking Grok
what it thinks. You are asking it what the crowd thinks, and whether the crowd has
already noticed the thing your signal is built on.

An edge the crowd has noticed is not an edge. It is a queue.

### The one output that matters: falsifiers

Every brief ends with concrete conditions that would prove the call wrong. Not
"if sentiment deteriorates" — that is unfalsifiable and therefore worthless. A falsifier
is observable and dated:

> Wrong if: funding flips negative on Binance while spot holds above $79,600.
> Wrong if: CME open interest drops >8% into Friday settlement.

A signal you cannot disprove is not a signal, it is a horoscope. The falsifier is the
entire product. Everything above it is the setup.

### Try it free

```bash
curl https://api.octodamus.com/v2/grok/brief/preview?asset=BTC
```

Returns the bias, whether the crowd agrees, the crowded-trade risk, and one falsifier.
The full brief — the contradiction, the blind spot, the eight-line critique and the
remaining falsifiers — is $0.10.

---

## Chapter 2 — Wiring the desk (PAID)

Covers: adding the Octodamus MCP server to Claude, Cursor, and Grok-side agents; the
`get_agent_signal` tool contract; scoping the free tier (500 req/day) so an agent loop
does not exhaust it in an hour; and when to pay per call instead.

## Chapter 3 — The adversary prompt (PAID)

The exact system prompt that turns a general model into an adversarial reviewer, why
"find what contradicts this" outperforms "critique this," and the three failure modes
that make a critique useless: sycophancy, hedging, and unfalsifiable objections.

## Chapter 4 — Reading crowd agreement as risk (PAID)

`crowd_agreement: AGREES` is the most dangerous value the brief returns. This chapter
covers positioning-as-contra-indicator, how to size when the crowd is with you, and the
funding/long-ratio thresholds that separate "crowded" from "consensus."

## Chapter 5 — Automating the close (PAID)

Turning falsifiers into machine-checkable predicates, webhook wiring on `signal.resolved`,
and how to log the outcome so the record stays honest whether it wins or loses.

## Chapter 6 — Paying for it (PAID)

x402 on Base end to end: the 402 challenge, the payment header, settlement, and how an
agent budgets micropayments across a research loop without a human approving each one.

---

## Method, stated plainly

Every call is published to Base before it is posted, so the record cannot be edited after
the fact. **The verified number is the blended one: 11W-23L, 32.4% across 34 resolved
calls.** Recompute it yourself from the registry contract — do not take our word for it.

We also publish a per-strategy split at `/tools/strategy-scorecard`, and you should know
exactly what that is: for calls published before 2026-09-06 the on-chain commitment did
not include the strategy label, so the breakdown is our internal accounting, not something
you can verify from chain. From call #54 onward the strategy is inside the content hash and
the split becomes provable. We are telling you this instead of letting you find it.

Read the per-strategy numbers before you buy anything here. If the strategy behind a
signal has nine resolved calls and an 11% hit rate, that is information you are entitled
to have before you pay $0.10 for its opinion. Two strategies were retired on 2026-09-06 for
exactly that reason; their losses stay in the record permanently, because they are on Base
and cannot be removed.

**Market intelligence, not financial advice.**
Octodamus — octodamus.com | @octodamusai | api.octodamus.com

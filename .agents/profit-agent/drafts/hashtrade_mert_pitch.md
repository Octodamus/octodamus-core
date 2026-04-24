# HashTrade / Mert Özbas — Outreach

**Contact:** https://github.com/mertozbas (open a GitHub issue or discussion)
**Subject:** The edge layer for HashTrade

---

Mert,

Saw HashTrade on Hacker News. The comment that stuck: "trading what is already confirmed,
using data that everyone has, ergo there is no edge."

That's the real problem. CCXT gives you execution. It doesn't give you signal.

Octodamus is the oracle layer that fixes this. 27 live feeds: funding rates, open interest,
CME institutional positioning, COT data, Polymarket event probabilities, global aviation volume
as a macro leading indicator. AI consensus across all 27 before any signal fires. Not price.
Not candles. The stuff that moves before price does.

Your agent wakes every 5-25 minutes. Right now it checks markets with the same data
everyone else has. Add one tool call to the wake cycle:

  GET api.octodamus.com/v2/x402/agent-signal
  PAYMENT-SIGNATURE: [EIP-3009, $0.01 USDC, Base]

$0.01 per wake. No subscription. No account. No API key. Your agent pays in USDC
the same way it would pay for any other tool. Native to the agentic stack you're already
building on.

You support Claude. Octodamus is also on Smithery as an MCP — your Claude provider
can call it directly without touching x402 at all if you prefer.

The signal: BUY/SELL/HOLD with confidence score, funding rate direction, OI trend,
Fear & Greed, and Polymarket edge if one exists. One call. Structured JSON.

Track record is public: 2W/2L right now — early days, and we say so. The value isn't
the win rate yet. It's the signal data itself: derivatives flows that move before spot does.

Happy to walk through the integration. Takes about 15 minutes.

— Octodamus (@octodamusai)
api.octodamus.com | api.octodamus.com/.well-known/x402.json

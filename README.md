# Octodamus Market Intelligence MCP Server

AI-consensus market oracle for autonomous agents. 27 live data feeds. BUY/SELL/HOLD signals with confidence scores, Polymarket edges, Fear & Greed, congressional trading, on-chain order flow, and macro regime — all in one MCP tool call.

**Smithery:** [octodamusai/market-intelligence](https://smithery.ai/server/octodamusai/market-intelligence)
**API:** [api.octodamus.com](https://api.octodamus.com)
**X:** [@octodamusai](https://x.com/octodamusai)

---

## Tools

| Tool | Description | Auth |
|------|-------------|------|
| `get_agent_signal` | BUY/SELL/HOLD + confidence + Fear & Greed + BTC price + Polymarket edges | Free |
| `get_market_brief` | One-paragraph oracle read across all assets + macro. Drop into any LLM system prompt. | Free |
| `get_polymarket_edge` | Prediction market opportunities with EV, true probability, Kelly sizing | Free |
| `get_sentiment` | AI sentiment score per asset (-1.0 bearish to +1.0 bullish) | Free (BTC) |
| `get_prices` | Live prices with 24h change for BTC, ETH, SOL (+ stocks with key) | Free |
| `get_oracle_signals` | Raw 11-signal consensus votes — RSI, MACD, funding rate, L/S ratio, taker flow, whale moves | API key |
| `get_data_sources` | All 27 live data feeds with update frequencies | Free |
| `get_all_data` | Everything in one call — signal + edges + sentiment + prices + brief | API key |

---

## Installation

### Claude Desktop / Cursor / Windsurf

Add to your MCP config:

```json
{
  "mcpServers": {
    "octodamus": {
      "url": "https://api.octodamus.com/mcp",
      "config": {
        "apiKey": ""
      }
    }
  }
}
```

Leave `apiKey` blank for free tools (500 req/day). Get a free key: `POST https://api.octodamus.com/v1/signup?email=you@example.com`

### Smithery (one-click install)

```bash
npx @smithery/cli install octodamusai/market-intelligence
```

---

## Example Output

```json
{
  "action": "BUY",
  "confidence": "high",
  "signal": {"asset": "BTC", "direction": "LONG", "timeframe": "1W"},
  "fear_greed": {"value": 17, "label": "Extreme Fear"},
  "btc": {"price_usd": 81385, "change_24h": 2.64, "trend": "UP"},
  "polymarket_edge": [
    {"question": "BTC above $90k by June?", "side": "YES", "ev": 0.22, "confidence": "high"}
  ],
  "reasoning": "Extreme fear + LONG signal + macro dip = accumulation zone.",
  "track_record": {"wins": 5, "losses": 6, "total": 11}
}
```

---

## Pricing

| Plan | Price | Limit |
|------|-------|-------|
| Free | $0 | 500 req/day |
| Pay-per-call | $0.01 USDC/call | Unlimited — x402 on Base, no account needed |
| Annual | $29/year | 10k req/day |

x402 payments: Base chain (eip155:8453), USDC. No account or credit card required — just a funded Base wallet.

Signup: `POST https://api.octodamus.com/v1/signup?email=your@email.com`

---

## Signal Coverage

- **Crypto:** BTC, ETH, SOL
- **Stocks:** NVDA, TSLA, AAPL, MSFT, SPY (+ tokenized versions on Base via Dinari)
- **Macro:** FRED yield curve, DXY, VIX, M2, Fed probability (CME FedWatch)
- **On-chain:** Binance 24h delta, Base DEX flow, whale wallet moves
- **Sentiment:** Grok/X crowd sentiment with contrarian divergence flags (BULL_TRAP/BEAR_TRAP)
- **Congressional:** Finance Committee insider trading on mega-cap stocks (QuiverQuant)
- **Prediction markets:** Polymarket edges with EV and Kelly sizing

---

## Ed25519 Verification

All premium responses are signed with Octodamus's Ed25519 key for on-chain verification. The public key is published at `https://api.octodamus.com/.well-known/x402.json` under `signing`.

```python
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
import base64, json

response = ...  # API response dict
pubkey_b64 = response["signer_pubkey"]
sig_b64    = response["signature"]
payload    = {k: v for k, v in response.items() if k not in ("signature", "signer_pubkey")}
canonical  = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

key = Ed25519PublicKey.from_public_bytes(base64.b64decode(pubkey_b64))
key.verify(base64.b64decode(sig_b64), canonical)  # raises if invalid
```

---

## ACP (Agent Commerce Protocol)

14 offerings available for agent-to-agent commerce via Virtuals ACP ($1.00-$2.00 USDC/job):

- Market Signal (BTC/ETH/SOL) $1.00
- Grok Sentiment Brief $1.00
- Fear vs Crowd Divergence $2.00
- BTC Bull Trap Monitor $1.50
- Overnight Asia Brief $2.00
- Tokenized Stock Signal (AAPL/MSFT/SPY on Base) $1.00
- MacroMind Brief $1.00 | StockOracle Brief $1.00
- Order ChainFlow Brief $1.00 | X Sentiment Brief $1.00

Discovery: `GET https://api.octodamus.com/.well-known/acp.json`

---

## License

MIT — see [LICENSE](LICENSE)

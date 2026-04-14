# Octo Distro Media -- Distribution Strategy

## Overview
Distribution is the moat. Code is commoditized. Octodamus wins through audience, trust, and reach.
All tools funnel to `data/subscribers.json` and drive API key signups at `api.octodamus.com`.

## Engine File
- `octo_distro.py` -- powers all 10 free tools + subscriber capture
- CLI: `python octo_distro.py [scorecard|macro|digest|travel|funding|subs|subscribe]`

## 10 Free Tools

| Tool | Endpoint | Gate | Data Source |
|------|----------|------|-------------|
| Oracle Scorecard | `/tools/scorecard` | Free | `data/octo_calls.json` |
| Macro Pulse | `/tools/macro` | Free | `octo_macro.py` (FRED) |
| Liquidation Radar | `/tools/liquidations?asset=BTC` | Free | `octo_coinglass.py` |
| Travel Signal | `/tools/travel` | Free | `octo_flights.py` |
| Signal Composite | `/tools/signal?asset=BTC&email=` | Email | Coinglass + macro |
| Funding Extremes | `/tools/funding?email=` | Email | `octo_coinglass.py` |
| CME Positioning | `/tools/cme?email=` | Email | `octo_cot.py` (CFTC) |
| Polymarket Edges | `/tools/edges?email=` | Email | `octo_calls.json` |
| Intel Digest | `/tools/digest?email=` | Email | All signals |
| Oracle Simulator | (via octo_distro.py) | Email | Historical calls |

## Subscriber Capture
- Email stored in `data/subscribers.json`
- Telegram notification on every new signup
- Newsletter subscribe: `POST /subscribe/newsletter?email=`
- MCP subscribe: `subscribe_to_octodamus(email)` tool in `octo_mcp_server.py`
- API key signup (separate): `POST /v1/signup?email=`

## MCP as Sales Team
- MCP already on Smithery: list on MCPT + open-tools registries too
- Every tool response ends with `_CTA` footer (subscribe link + X handle)
- 2 new tools: `subscribe_to_octodamus`, `get_free_tools`
- MCP instructions updated: prompt AI to use `subscribe_to_octodamus` when users want more

## 7 Distribution Strategies (from podcast)
1. **MCP Server** -- Octodamus MCP = AI-assisted sales team (active)
2. **Programmatic SEO** -- 10k pages: "crypto signals for [niche]" -- future build
3. **Free Tools** -- these 10 tools (active)
4. **AEO** -- structured FAQ content on octodamus.com for perplexity/ChatGPT citations -- future
5. **Viral Artifacts** -- Oracle scorecard shareable card, liquidation radar screenshot -- active
6. **Newsletter Acquisition** -- buy a 5k-50k crypto newsletter at $500/mo revenue milestone
7. **AI Content Repurposing** -- one pillar post -> tweets + LinkedIn + newsletter -- future build

## Revenue Milestones
- $500/mo: Subscribe to Unusual Whales API + consider newsletter acquisition
- $2k/mo: Programmatic SEO build
- $5k/mo: Full content repurposing engine

## Shareable Artifacts (viral loop)
- Oracle Scorecard: "Xw/YL -- Z% win rate" -- share to X
- Liquidation Radar: "$Xm liquidated 48h" -- share to X
- Macro Pulse: "+4/5 tailwinds today" -- share to X
- All shareable strings are in the `shareable` field of each tool response

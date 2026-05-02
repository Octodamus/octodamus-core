"""
octo_boto_filters.py — OctoBoto Market Filter Rules

Lightweight module (no Telegram, no heavy imports) so it can be safely
imported by both octo_boto.py and octo_boto_oracle_bridge.py without
triggering Telegram bot initialization.

Octodamus = the oracle (BTC/ETH/SOL directional calls via 9/11 consensus)
OctoBoto  = the Telegram paper-trading bot (Polymarket positions)

OctoBoto has signal edge in: crypto price markets, macro events, AI/tech stocks.
OctoBoto has NO signal edge in: geopolitical events, elections, political treaties.
"""

# ── Sector → keyword map (must match octo_boto.py _SECTOR_MAP) ───────────────

_SECTOR_MAP = {
    "crypto":      ["bitcoin", "btc", "ethereum", "eth", "solana", "sol",
                    "crypto", "defi", "nft", "blockchain", "altcoin",
                    "coinbase", "binance", "polymarket"],
    "macro":       ["fed", "federal reserve", "interest rate", "cpi", "inflation",
                    "gdp", "recession", "unemployment", "s&p", "nasdaq", "dow",
                    "treasury", "yield curve"],
    "ai_tech":     ["openai", "gpt", "claude", "gemini", "ai model", "llm",
                    "artificial intelligence", "nvidia", "microsoft", "google",
                    "apple", "meta", "amazon", "tech stock"],
    "geopolitics": ["war", "conflict", "ceasefire", "sanctions", "nato",
                    "ukraine", "russia", "china", "taiwan", "middle east",
                    "iran", "israel", "north korea"],
}

# ── Sectors OctoBoto has no signal edge in ───────────────────────────────────
BLOCKED_SECTORS = {"geopolitics"}

# ── Additional question-level political keywords ──────────────────────────────
# Pure political event markets: outcomes driven by human decisions, not price
# signals. OctoBoto's model (EV, funding, on-chain, sentiment) cannot forecast
# whether a peace deal gets signed or who wins an election.
_POLITICAL_KEYWORDS = [
    "election", "elected", "president", "prime minister", "parliament",
    "vote", "referendum", "ceasefire", "peace deal", "treaty",
    "war ends", "invasion", "sanctions lifted",
]


def get_market_sector(question: str) -> str:
    """Return the primary sector for a Polymarket question."""
    q = question.lower()
    for sector, keywords in _SECTOR_MAP.items():
        if any(kw in q for kw in keywords):
            return sector
    return "other"


def is_no_edge_market(question: str) -> bool:
    """
    Return True if OctoBoto should skip this market entirely.

    Blocks geopolitical/political markets where OctoBoto's signal stack
    (EV scoring, sentiment, on-chain, macro) has no predictive edge.
    Crypto price markets, macro events, and tech stocks remain open.
    """
    if get_market_sector(question) in BLOCKED_SECTORS:
        return True
    q = question.lower()
    return any(kw in q for kw in _POLITICAL_KEYWORDS)

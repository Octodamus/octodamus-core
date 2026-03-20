"""
octo_post_templates.py
10 directional post templates for Octodamus.
Each has a scenario, call, and unique closer.
Inject live prices before use.
"""

# ── Template structure ────────────────────────────────────────────────────────
# Each template has:
#   scenario   — market condition it fits
#   asset      — which asset
#   direction  — UP or DOWN
#   timeframe  — when the call resolves
#   template   — post text with {price}, {change}, {level} placeholders
#   closer     — unique sign-off line

TEMPLATES = [

    {
        "id": 1,
        "scenario": "crypto_selloff_fear_extreme",
        "asset": "BTC",
        "direction": "UP",
        "timeframe": "72h",
        "template": (
            "BTC {price} down {change}%. "
            "Fear & Greed just hit single digits. "
            "Historically that's not the end — it's the loading screen. "
            "Oracle call: reclaims {level} within 72h. "
            "Extreme fear is just retail handing coins to institutions on a discount."
        ),
        "closer": "The trench feeds on panic. Filed."
    },

    {
        "id": 2,
        "scenario": "eth_underperforming_btc",
        "asset": "ETH",
        "direction": "UP",
        "timeframe": "7d",
        "template": (
            "ETH/BTC ratio at multi-month lows. "
            "ETH {price}, down {change}% while BTC holds. "
            "Dev activity unchanged. Staking yield unchanged. "
            "The market is wrong about which chain matters right now. "
            "Oracle call: ETH outperforms BTC next 7 days. "
            "Rotation incoming."
        ),
        "closer": "The eight arms already rotated. Catch up."
    },

    {
        "id": 3,
        "scenario": "nvda_earnings_setup",
        "asset": "NVDA",
        "direction": "UP",
        "timeframe": "5d",
        "template": (
            "NVDA {price}, options pricing in {change}% move into earnings. "
            "Every single hyperscaler just raised capex guidance. "
            "The buyers are the same people running the buy side models. "
            "Oracle call: beats and guides up. {level} in 5 days. "
            "Jensen doesn't do modest."
        ),
        "closer": "This isn't a prediction. It's reading the current."
    },

    {
        "id": 4,
        "scenario": "btc_consolidation_breakout",
        "asset": "BTC",
        "direction": "UP",
        "timeframe": "48h",
        "template": (
            "BTC {price}. "
            "Seven days of tight consolidation, volume declining, "
            "funding rate neutral. "
            "That's not indecision — that's compression. "
            "Oracle call: {level} within 48h. "
            "Coils don't compress forever."
        ),
        "closer": "Pressure finds its release. Mark the timestamp."
    },

    {
        "id": 5,
        "scenario": "sol_momentum_play",
        "asset": "SOL",
        "direction": "UP",
        "timeframe": "24h",
        "template": (
            "SOL {price}, up {change}% while everything else bled. "
            "DEX volume on Solana just flipped Ethereum for the third week running. "
            "The narrative hasn't caught the data yet. "
            "Oracle call: {level} before close. "
            "Fast chains don't wait for permission."
        ),
        "closer": "The current was already moving. Now everyone sees it."
    },

    {
        "id": 6,
        "scenario": "btc_leverage_flush_incoming",
        "asset": "BTC",
        "direction": "DOWN",
        "timeframe": "24h",
        "template": (
            "BTC {price}. "
            "Open interest at cycle highs. Funding rate positive for 11 days straight. "
            "Retail long/short ratio: 3:1 longs. "
            "This is a vending machine loaded with liquidations. "
            "Oracle call: tests {level} within 24h. "
            "The market owes nobody a smooth ride up."
        ),
        "closer": "Leverage is borrowed certainty. The ocean collects the debt."
    },

    {
        "id": 7,
        "scenario": "nvda_overextended_short_term",
        "asset": "NVDA",
        "direction": "DOWN",
        "timeframe": "5d",
        "template": (
            "NVDA {price}, up 40% in 6 weeks on no new fundamental catalyst. "
            "P/S ratio back above 30x. "
            "The AI story is real — the multiple is not. "
            "Oracle call: retraces to {level} before next leg up. "
            "Even the best stocks breathe."
        ),
        "closer": "The reef looks beautiful right before it bleaches."
    },

    {
        "id": 8,
        "scenario": "eth_macro_headwind",
        "asset": "ETH",
        "direction": "DOWN",
        "timeframe": "48h",
        "template": (
            "ETH {price}. "
            "Fed minutes dropped hawkish. Dollar spiking. "
            "Risk-off is not ETH's friend. "
            "Institutional spot ETF flows turned net negative last 3 days. "
            "Oracle call: {level} support gets tested in 48h. "
            "The macro tide is pulling out."
        ),
        "closer": "When the ocean pulls back, everything on the shore gets exposed."
    },

    {
        "id": 9,
        "scenario": "btc_post_halving_accumulation",
        "asset": "BTC",
        "direction": "UP",
        "timeframe": "30d",
        "template": (
            "BTC {price}. "
            "Post-halving supply shock math: "
            "miners now produce ~450 BTC/day vs ~900 pre-halving. "
            "Spot ETFs absorbing 2-3x that daily. "
            "This is a supply/demand equation a child could solve. "
            "Oracle call: {level} within 30 days. "
            "The math doesn't care about your macro fears."
        ),
        "closer": "Every cycle rhymes. The oracle just reads the sheet music early."
    },

    {
        "id": 10,
        "scenario": "congress_trade_signal",
        "asset": "NVDA",
        "direction": "UP",
        "timeframe": "14d",
        "template": (
            "Three congressional reps bought NVDA calls last week. "
            "Combined: {change}M notional. "
            "Disclosure lag: 45 days. "
            "Current price: {price}. "
            "Oracle call: {level} in 14 days. "
            "They don't buy with their own money unless they already know the answer."
        ),
        "closer": "The most reliable alpha on the planet comes with a press badge and immunity."
    },

]


# ── Runner-facing functions ───────────────────────────────────────────────────

def get_template_for_scenario(scenario: str) -> dict:
    for t in TEMPLATES:
        if t["scenario"] == scenario:
            return t
    return None


def get_all_templates() -> list:
    return TEMPLATES


def format_template(template: dict, price: str, change: str, level: str) -> str:
    """Fill in placeholders and append closer."""
    text = template["template"].format(
        price=price,
        change=change,
        level=level,
    )
    return f"{text} {template['closer']}"


def build_template_prompt_context() -> str:
    """
    Build a context block for Claude showing all available post styles.
    Inject this into the daily/monitor prompt so Claude knows the format.
    """
    lines = [
        "POST STYLE GUIDE — directional call required in every post:",
        "Format: [DATA POINT] → [INSIGHT] → [ORACLE CALL: asset + direction + price level + timeframe] → [PUNCHY CLOSER]",
        "",
        "Example closers (rotate — never repeat):",
        "  'The trench feeds on panic. Filed.'",
        "  'The eight arms already rotated. Catch up.'",
        "  'This isn't a prediction. It's reading the current.'",
        "  'Mark the timestamp.'",
        "  'Pressure finds its release.'",
        "  'The ocean collects the debt.'",
        "  'The reef looks beautiful right before it bleaches.'",
        "  'Every cycle rhymes. The oracle reads the sheet music early.'",
        "  'The most reliable alpha comes with immunity.'",
        "  'Filed. The depths don't forget.'",
        "  'Retail panic is just liquidity for patient hands.'",
        "  'Fast chains don't wait for permission.'",
        "  'The math doesn't care about your macro fears.'",
        "  'Even the best stocks breathe.'",
        "",
        "RULES:",
        "- State a SPECIFIC price target or level",
        "- State a SPECIFIC timeframe (24h, 48h, 7d, etc.)",
        "- Direction must be clear: bullish/up OR bearish/down",
        "- One ocean metaphor MAX per post — or zero",
        "- Never repeat a closer from the last 5 posts",
        "- Under 280 chars total",
    ]
    return "\n".join(lines)

"""
octo_grok_sentiment.py -- Real-time X sentiment via targeted account sampling.

Samples the top 50 active crypto/macro accounts on X directly.
Checks if they are active in the last 60 minutes (not 4 hours).
Passes current price context so Grok can flag lag vs lead.

Returns BULLISH / BEARISH / NEUTRAL with lag detection.
"""

import json
import time
from pathlib import Path

_CACHE_FILE = Path(__file__).parent / "data" / "grok_sentiment_cache.json"
_CACHE_TTL  = 600  # 10 minutes — tighter window for faster signal

# Top 50 active crypto/macro accounts — informed traders, analysts, on-chain.
# Retail noise accounts excluded. Updated manually as landscape shifts.
_TOP_ACCOUNTS = [
    # On-chain / macro analysts
    "woonomic", "100trillionUSD", "RaoulGMI", "KobeissiLetter", "nic__carter",
    "PortfolioManager", "MacroAlf", "fejau_inc", "LynAldenContact", "zerohedge",
    # Active traders with track records
    "CryptoCred", "CredibleCrypto", "DonAlt", "CryptoKaleo", "Pentosh1",
    "IncomeSharks", "nebraskangooner", "SmartContracter", "CryptoTea_",
    "rovercrc", "CryptoHayes", "scottmelker", "TheCryptoDog", "EllioTrades",
    # Institutional / OG
    "saylor", "APompliano", "danheld", "DocumentingBTC", "BitcoinMagazine",
    "WClementeIII", "glassnode", "ki_young_ju", "caueconomics",
    # Market structure / derivatives
    "Bybt_com", "CryptoQuant_CEO", "HsakaTrades", "CryptoCapo_",
    "trader1sz", "MikeBurgersburg", "OnChainWizard", "Defi_Mochi",
    # Broader macro that moves crypto
    "elerianm", "stlouisfed", "nickgrossman", "balajis", "naval",
    # Active X analysts with high engagement
    "CryptoBull", "CryptoBirb", "AltcoinDailyio", "larkdavis", "CryptoWendyO",
]

_ASSETS = {
    "BTC":  "Bitcoin $BTC",
    "ETH":  "Ethereum $ETH",
    "SOL":  "Solana $SOL",
    "WTI":  "crude oil WTI",
    "NVDA": "NVIDIA $NVDA",
    "TSLA": "Tesla $TSLA",
    "COIN": "Coinbase $COIN",
    "MSTR": "MicroStrategy $MSTR",
}

_SYSTEM = """You are a market sentiment analyst with real-time access to X (Twitter).
You specialize in reading INFORMED accounts — traders, on-chain analysts, macro investors.
Your job is to detect what the smart money crowd is actually saying right now, not retail noise.

Critical rules:
- Only look at the specific accounts listed in the prompt
- Only count posts from the last 60 minutes — older posts do not count
- If an account has not posted in 60 minutes, ignore them entirely
- Do NOT aggregate general X sentiment — only the listed accounts
- Detect lag: if accounts are bullish but price is falling, flag as LAGGING
- Detect lead: if accounts are turning bearish before price drops, flag as LEADING
- Return only valid JSON, nothing else"""


def _load_cache() -> dict:
    try:
        if _CACHE_FILE.exists():
            d = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
            if time.time() - d.get("ts", 0) < _CACHE_TTL:
                return d.get("data", {})
    except Exception:
        pass
    return {}


def _save_cache(data: dict):
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(
            json.dumps({"ts": time.time(), "data": data}),
            encoding="utf-8"
        )
    except Exception:
        pass


def _get_price_context(asset: str) -> str:
    """Get current price + 24h change for context injection."""
    try:
        from financial_data_client import get_crypto_prices
        if asset in ("BTC", "ETH", "SOL"):
            prices = get_crypto_prices([asset])
            p = prices.get(asset, {})
            price = p.get("usd", 0)
            chg = p.get("usd_24h_change", 0)
            return f"Current {asset} price: ${price:,.0f} ({chg:+.1f}% in 24h)"
    except Exception:
        pass
    try:
        import httpx
        secrets = json.loads((Path(__file__).parent / ".octo_secrets").read_text(encoding="utf-8"))
        fk = secrets.get("secrets", secrets).get("FINNHUB_API_KEY", "")
        if fk and asset in ("NVDA", "TSLA", "COIN", "MSTR"):
            r = httpx.get(f"https://finnhub.io/api/v1/quote?symbol={asset}&token={fk}", timeout=5)
            d = r.json()
            return f"Current {asset} price: ${d.get('c', 0):,.2f} ({d.get('dp', 0):+.1f}% today)"
    except Exception:
        pass
    return ""


def get_grok_sentiment(asset: str = "BTC", force: bool = False) -> dict:
    """
    Get real-time X sentiment by sampling top 50 crypto accounts directly.
    Includes lag detection: flags when crowd sentiment contradicts price action.

    Returns:
        {
            "signal":      "BULLISH" | "BEARISH" | "NEUTRAL",
            "confidence":  0.0-1.0,
            "summary":     "what active accounts are saying right now",
            "crowd_pos":   "long-heavy" | "short-heavy" | "mixed",
            "lag_status":  "LAGGING" | "LEADING" | "ALIGNED" | "UNCLEAR",
            "active_count": int,  # how many of the 50 posted in last 60min
            "key_themes":  ["theme1", "theme2"],
            "source":      "grok-targeted"
        }
    """
    asset = asset.upper()
    cache = _load_cache()

    if not force and asset in cache:
        return cache[asset]

    try:
        from openai import OpenAI
        secrets_file = Path(__file__).parent / ".octo_secrets"
        secrets = json.loads(secrets_file.read_text(encoding="utf-8"))
        grok_key = secrets.get("secrets", secrets).get("GROK_API_KEY", "")

        if not grok_key:
            return _neutral(asset, "GROK_API_KEY not configured")

        client = OpenAI(base_url="https://api.x.ai/v1", api_key=grok_key)

        asset_label = _ASSETS.get(asset, asset)
        price_ctx   = _get_price_context(asset)
        accounts_str = ", ".join(f"@{a}" for a in _TOP_ACCOUNTS)

        prompt = f"""Search X (Twitter) RIGHT NOW for posts about {asset_label} from ONLY these accounts in the LAST 60 MINUTES:

{accounts_str}

{f'Price context: {price_ctx}' if price_ctx else ''}

Instructions:
1. Only include accounts that have posted about {asset_label} in the last 60 minutes
2. Count how many of these accounts posted (active_count)
3. What is the dominant directional view among those active accounts?
4. Are they acknowledging the current price action or are they ignoring it (lagging)?
5. lag_status rules:
   - LAGGING: crowd is BULLISH but price is falling, OR crowd is BEARISH but price is rising
   - LEADING: crowd is turning bearish/bullish BEFORE a price move is confirmed
   - ALIGNED: crowd direction matches price action
   - UNCLEAR: not enough active accounts or mixed signals

Return ONLY this JSON (no other text):
{{
  "signal": "BULLISH" or "BEARISH" or "NEUTRAL",
  "confidence": 0.0 to 1.0,
  "summary": "1-2 sentences on what active accounts are saying right now",
  "crowd_pos": "long-heavy" or "short-heavy" or "mixed",
  "lag_status": "LAGGING" or "LEADING" or "ALIGNED" or "UNCLEAR",
  "active_count": integer between 0 and 50,
  "key_themes": ["theme1", "theme2", "theme3"]
}}"""

        response = client.chat.completions.create(
            model="grok-3",
            max_tokens=500,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user",   "content": prompt},
            ],
        )

        raw = response.choices[0].message.content.strip()
        if "```" in raw:
            raw = raw.split("```")[1].replace("json", "").strip()

        result = json.loads(raw)
        result["source"] = "grok-targeted"
        result["asset"]  = asset

        # Validate
        if result.get("signal") not in ("BULLISH", "BEARISH", "NEUTRAL"):
            result["signal"] = "NEUTRAL"
        if result.get("lag_status") not in ("LAGGING", "LEADING", "ALIGNED", "UNCLEAR"):
            result["lag_status"] = "UNCLEAR"
        result["confidence"]   = max(0.0, min(1.0, float(result.get("confidence", 0.5))))
        result["active_count"] = int(result.get("active_count", 0))

        # Downgrade confidence if very few accounts active
        if result["active_count"] < 5:
            result["confidence"] = min(result["confidence"], 0.35)
            result["lag_status"] = "UNCLEAR"

        cache[asset] = result
        _save_cache(cache)
        return result

    except Exception as e:
        return _neutral(asset, str(e))


def _neutral(asset: str, reason: str = "") -> dict:
    return {
        "signal":       "NEUTRAL",
        "confidence":   0.0,
        "summary":      f"Grok sentiment unavailable: {reason}",
        "crowd_pos":    "mixed",
        "lag_status":   "UNCLEAR",
        "active_count": 0,
        "key_themes":   [],
        "source":       "grok-targeted",
        "asset":        asset,
    }


def get_grok_sentiment_context(assets: list = None) -> str:
    """Formatted string for injection into runner prompts."""
    if assets is None:
        assets = ["BTC", "ETH"]

    lines = ["X Social Sentiment (Grok targeted — top 50 accounts, last 60 min):"]
    for asset in assets:
        s = get_grok_sentiment(asset)
        if s["confidence"] > 0:
            lag = s.get("lag_status", "UNCLEAR")
            active = s.get("active_count", 0)
            lines.append(
                f"  {asset}: {s['signal']} ({s['confidence']:.0%}) "
                f"| {lag} | {active} active accounts"
            )
            lines.append(f"    {s['summary'][:140]}")
        else:
            lines.append(f"  {asset}: unavailable ({s['summary'][:60]})")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    asset = sys.argv[1].upper() if len(sys.argv) > 1 else "BTC"
    print(f"Fetching targeted Grok sentiment for {asset} (top 50 accounts, last 60 min)...")
    result = get_grok_sentiment(asset, force=True)
    print(json.dumps(result, indent=2))

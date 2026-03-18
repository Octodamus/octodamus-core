import os, json, logging, requests
from datetime import datetime, timezone

log = logging.getLogger("OctoStreams")

def _get_bw_password(item_name):
    import subprocess
    session = os.environ.get("BW_SESSION")
    if not session:
        log.warning(f"BW_SESSION not set — skipping {item_name}")
        return None
    try:
        result = subprocess.run(
            ["bw", "get", "password", item_name],
            env={**os.environ, "BW_SESSION": session},
            capture_output=True, text=True, timeout=10
        )
        return result.stdout.strip() or None
    except Exception as e:
        log.warning(f"BW lookup failed for {item_name}: {e}")
        return None

def get_stock_prices(tickers=["NVDA","TSLA","AAPL"]):
    api_key = _get_bw_password("AGENT - Octodamus - Data - AlphaVantage")
    if not api_key:
        return {}
    results = {}
    import time
    for ticker in tickers:
        try:
            r = requests.get(
                f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={ticker}&apikey={api_key}",
                timeout=10)
            data = r.json().get("Global Quote", {})
            if data:
                results[ticker] = {
                    "price": float(data.get("05. price", 0)),
                    "change": float(data.get("09. change", 0)),
                    "change_pct": data.get("10. change percent", "0%").replace("%",""),
                    "volume": int(data.get("06. volume", 0)),
                    "timestamp": data.get("07. latest trading day", ""),
                }
        except Exception as e:
            log.error(f"AlphaVantage error {ticker}: {e}")
        time.sleep(12)
    return results

def get_crypto_prices(coins=["BTC","ETH"]):
    ids = {"BTC":"bitcoin","ETH":"ethereum","SOL":"solana"}
    api_key = _get_bw_password("AGENT - Octodamus - Data - CoinGecko")
    coin_ids = ",".join([ids.get(c, c.lower()) for c in coins])
    headers = {"accept":"application/json"}
    if api_key:
        headers["x-cg-demo-api-key"] = api_key
    try:
        r = requests.get(
            f"https://api.coingecko.com/api/v3/simple/price?ids={coin_ids}&vs_currencies=usd&include_24hr_change=true",
            headers=headers, timeout=10)
        raw = r.json()
        rev = {v:k for k,v in ids.items()}
        return {rev.get(k,k.upper()): {
            "price_usd": v.get("usd",0),
            "change_24h": round(v.get("usd_24h_change",0),2)
        } for k,v in raw.items()}
    except Exception as e:
        log.error(f"CoinGecko error: {e}")
        return {}

def get_treasury_balance(wallet="0x5c6B3a3dAe296d3cef50fef96afC73410959a6Db"):
    try:
        r = requests.post("https://mainnet.base.org",
            json={"jsonrpc":"2.0","method":"eth_getBalance","params":[wallet,"latest"],"id":1},
            timeout=10)
        wei = int(r.json().get("result","0x0"), 16)
        return {"wallet": wallet, "balance_eth": round(wei/1e18, 6), "balance_wei": wei}
    except Exception as e:
        log.error(f"Base RPC error: {e}")
        return {}

def get_market_news(max_articles=5):
    api_key = _get_bw_password("AGENT - Octodamus - Data - NewsAPI")
    if not api_key:
        return []
    try:
        r = requests.get("https://newsapi.org/v2/everything", params={
            "q": '"cryptocurrency" OR "stock market" OR "bitcoin"',
            "sortBy": "publishedAt",
            "pageSize": max_articles,
            "language": "en",
            "apiKey": api_key
        }, timeout=10)
        return [{"title": a.get("title",""), "source": a.get("source",{}).get("name",""),
                 "published_at": a.get("publishedAt","")} for a in r.json().get("articles",[])]
    except Exception as e:
        log.error(f"NewsAPI error: {e}")
        return []

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

    print("\n--- Treasury Balance ---")
    print(json.dumps(get_treasury_balance(), indent=2))

    print("\n--- Crypto (CoinGecko) ---")
    print(json.dumps(get_crypto_prices(), indent=2))

    if os.environ.get("BW_SESSION"):
        print("\n--- Stocks (Alpha Vantage) ---")
        print(json.dumps(get_stock_prices(), indent=2))
        print("\n--- News (NewsAPI) ---")
        for n in get_market_news():
            print(f"  [{n['source']}] {n['title'][:80]}")
    else:
        print("\n[!] Set BW_SESSION first: export BW_SESSION=$(bw unlock --raw)")

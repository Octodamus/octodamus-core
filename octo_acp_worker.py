"""
octo_acp_worker.py — Octodamus ACP Seller Worker v3
Full rebuild with thread-safe job queue, rejection handling, all 4 handlers.
Runs in WSL Ubuntu via Task Scheduler.
"""

import asyncio
import logging
import os
import queue
import statistics
import sys
import threading
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

from virtuals_acp.client import VirtualsACP
from virtuals_acp.contract_clients.contract_client_v2 import ACPContractClientV2
from virtuals_acp.configs.configs import BASE_MAINNET_CONFIG_V2

import octo_pulse
import octo_gecko
import octo_fx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [ACP] %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/octo_acp_worker.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
SELLER_ENTITY_ID    = 3
SELLER_AGENT_WALLET = "0x9DdE22707542FA69c9ecfEb0C4f0912797DF3d5E"
BITWARDEN_ITEM      = "AGENT - Octodamus - ACP Wallet"

VALID_CRYPTO  = {"BTC","ETH","SOL","BNB","XRP","DOGE","AVAX","LINK","ADA","DOT"}
VALID_STOCKS  = {"NVDA","TSLA","AAPL","MSFT","AMZN","META","GOOGL","SPY","QQQ"}
VALID_TICKERS = VALID_CRYPTO | VALID_STOCKS

# ── Thread-safe job queue ─────────────────────────────────────────────────────
JOB_QUEUE = queue.Queue()

def _queue_worker():
    while True:
        try:
            task, memo = JOB_QUEUE.get(timeout=1)
            try:
                _handle_task(task, memo)
            except Exception as e:
                log.error(f"Queue worker error: {e}")
            finally:
                JOB_QUEUE.task_done()
        except queue.Empty:
            continue

threading.Thread(target=_queue_worker, daemon=True).start()

# ── Kraken Technical Analysis ─────────────────────────────────────────────────

def _kraken_ohlc_pair(ticker):
    m = {"BTC":"XBTUSD","ETH":"ETHUSD","SOL":"SOLUSD","BNB":"BNBUSD","XRP":"XRPUSD","DOGE":"DOGEUSD"}
    return m.get(ticker.upper(), ticker.upper()+"USD")

def _kraken_futures_sym(ticker):
    m = {"BTC":"PI_XBTUSD","ETH":"PI_ETHUSD","SOL":"PI_SOLUSD"}
    return m.get(ticker.upper(), "PI_XBTUSD")

def _ema(data, period):
    k = 2/(period+1)
    e = data[0]
    for p in data[1:]:
        e = p*k + e*(1-k)
    return round(e, 2)

def fetch_technicals(ticker="BTC"):
    import httpx
    try:
        r = httpx.get("https://api.kraken.com/0/public/OHLC",
            params={"pair": _kraken_ohlc_pair(ticker), "interval": 240, "count": 50}, timeout=8)
        if r.status_code != 200 or r.json().get("error"):
            return {}
        key = list(r.json()["result"].keys())[0]
        closes = [float(c[4]) for c in r.json()["result"][key]]
        ema20, ema50 = _ema(closes,20), _ema(closes,50)
        macd = round(_ema(closes,12) - _ema(closes,26), 2)
        gains, losses = [], []
        for i in range(1,15):
            d = closes[-i]-closes[-i-1]
            (gains if d>0 else losses).append(abs(d))
        avg_g = sum(gains)/14 if gains else 0
        avg_l = sum(losses)/14 if losses else 0.001
        rsi = round(100-100/(1+avg_g/avg_l), 1)
        recent = closes[-20:]
        bb_m = sum(recent)/20
        bb_s = statistics.stdev(recent)
        bb_w = round((bb_m+2*bb_s-(bb_m-2*bb_s))/bb_m*100, 1)
        return {"ema20":ema20,"ema50":ema50,"trend":"Bullish" if ema20>ema50 else "Bearish",
                "rsi":rsi,"macd":macd,"bb_width":bb_w}
    except Exception as e:
        log.warning(f"Technicals error {ticker}: {e}")
        return {}

def fetch_derivatives(ticker="BTC"):
    import httpx
    sym = _kraken_futures_sym(ticker)
    try:
        r = httpx.get("https://futures.kraken.com/derivatives/api/v3/tickers", timeout=8)
        if r.status_code != 200:
            return {}
        t = next((x for x in r.json().get("tickers",[]) if x.get("symbol")==sym), None)
        if not t:
            return {}
        fr = float(t.get("fundingRate",0) or 0)
        oi = float(t.get("openInterest",0) or 0)
        px = float(t.get("markPrice",71000) or 71000)
        return {"funding_rate":round(fr*100,6), "open_interest":f"${oi*px/1e9:.2f}B",
                "high_24h":t.get("high24h",0), "low_24h":t.get("low24h",0),
                "change_24h":t.get("change24h",0)}
    except Exception as e:
        log.warning(f"Derivatives error {ticker}: {e}")
        return {}

def directional_call(ticker, price, chg_24h, ta, deriv, fng):
    if not ta:
        return f"OCTODAMUS CALL: Insufficient data for {ticker} directional read."
    rsi   = float(ta.get("rsi",50) or 50)
    macd  = float(ta.get("macd",0) or 0)
    e20   = float(ta.get("ema20",0) or 0)
    e50   = float(ta.get("ema50",0) or 0)
    bb_w  = float(ta.get("bb_width",5) or 5)
    fr    = float((deriv or {}).get("funding_rate",0) or 0)
    bull = bear = 0
    if macd > 0: bull+=1
    else: bear+=1
    if e20 > e50: bull+=1
    else: bear+=1
    if rsi < 45: bull+=1
    elif rsi > 65: bear+=1
    if fng < 25: bull+=1
    elif fng > 75: bear+=1
    if fr < 0: bull+=1
    elif fr > 0.005: bear+=1
    if chg_24h > 2: bull+=1
    elif chg_24h < -2: bear+=1
    p = f"${price:,.0f}" if price else "current level"
    if bb_w < 3.0:
        d = "UP" if bull>bear else "DOWN"
        return f"OCTODAMUS CALL: DIRECTION: BREAKOUT IMMINENT — BB compressed to {bb_w}%. Resolving {d}. Watch for volume."
    elif bull >= 4:
        return f"OCTODAMUS CALL: DIRECTION: UP — {bull}/{bull+bear} signals bullish. {ticker} likely continues higher. Hold or add."
    elif bear >= 4:
        return f"OCTODAMUS CALL: DIRECTION: DOWN — {bear}/{bull+bear} signals bearish. {ticker} under pressure. Risk off."
    elif bull == bear:
        return f"OCTODAMUS CALL: DIRECTION: RANGE — Conflicting signals. {ticker} range-bound near {p}. Wait for breakout."
    elif bull > bear:
        return f"OCTODAMUS CALL: DIRECTION: LEANING UP — Mild bullish bias. {ticker} likely holds or grinds higher."
    else:
        return f"OCTODAMUS CALL: DIRECTION: LEANING DOWN — Mild bearish bias. {ticker} facing resistance near {p}."

# ── Job Handlers ──────────────────────────────────────────────────────────────

def handle_crypto_market_signal(req):
    import httpx
    ticker = req.get("ticker","BTC").upper()
    pulse = octo_pulse.run_pulse_scan()
    gecko = octo_gecko.run_gecko_scan()
    fx    = octo_fx.run_fx_scan() if hasattr(octo_fx,"run_fx_scan") else {}
    fng_val   = int(pulse.get("fear_greed",{}).get("value",50) or 50)
    fng_label = pulse.get("fear_greed",{}).get("label","N/A")
    btc_dom   = gecko.get("btc_dominance", gecko.get("global",{}).get("btc_dominance","N/A"))
    btc_p=eth_p=sol_p="N/A"; btc_c=eth_c=sol_c=0.0
    try:
        r = httpx.get("https://api.coingecko.com/api/v3/simple/price",
            params={"ids":"bitcoin,ethereum,solana","vs_currencies":"usd","include_24hr_change":"true"},timeout=6)
        if r.status_code==200:
            d=r.json()
            btc_p=f"${d['bitcoin']['usd']:,.0f}"; btc_c=d['bitcoin']['usd_24h_change']
            eth_p=f"${d['ethereum']['usd']:,.0f}"; eth_c=d['ethereum']['usd_24h_change']
            sol_p=f"${d['solana']['usd']:,.2f}"; sol_c=d['solana']['usd_24h_change']
    except Exception: pass
    usd_eur = fx.get("key_pairs",{}).get("EUR",{}).get("rate","N/A") if fx else "N/A"
    usd_jpy = fx.get("key_pairs",{}).get("JPY",{}).get("rate","N/A") if fx else "N/A"
    ta=fetch_technicals(ticker); deriv=fetch_derivatives(ticker)
    momentum="N/A"
    if ta:
        rsi,macd,e20,e50=ta.get("rsi",50),ta.get("macd",0),ta.get("ema20",0),ta.get("ema50",0)
        if rsi>70: momentum="Overbought"
        elif rsi<30: momentum="Oversold"
        elif macd>0 and e20>e50: momentum="Leaning Bullish"
        elif macd<0 and e20<e50: momentum="Leaning Bearish"
        else: momentum="Consolidating"
    if fng_val<20: signal="ACCUMULATE — Extreme fear historically precedes recovery. Strong buy zone."
    elif fng_val<40: signal="CAUTIOUS BUY — Fear present. Scale in carefully."
    elif fng_val<60: signal="NEUTRAL — Hold. Wait for directional confirmation."
    elif fng_val<80: signal="REDUCE — Greed elevated. Consider partial profits."
    else: signal="EXIT RISK — Extreme greed. High correction probability."
    btc_num=0
    try: btc_num=float(btc_p.replace("$","").replace(",","")) if btc_p!="N/A" else 0
    except Exception: pass
    L=[
        "OCTODAMUS MARKET ORACLE BRIEFING",
        f"Generated: {datetime.utcnow().strftime('%a, %b %d, %Y')}","",
        "1. Price & Performance Overview",
        f"   BTC: {btc_p} ({btc_c:+.1f}% today)",
        f"   ETH: {eth_p} ({eth_c:+.1f}% today)",
        f"   SOL: {sol_p} ({sol_c:+.1f}% today)",
        f"   BTC Dominance: {btc_dom}%",
        f"   Momentum: {momentum}",
    ]
    if ta:
        L+=["","2. Technical Analysis (4h)",
            f"   MACD: {ta['macd']} ({'Bullish' if ta['macd']>0 else 'Bearish'} momentum)",
            f"   RSI: {ta['rsi']} ({'Overbought' if ta['rsi']>70 else 'Oversold' if ta['rsi']<30 else 'Neutral territory'})",
            f"   Bollinger Bands: {ta['bb_width']}% width ({'tight — breakout imminent' if ta['bb_width']<4 else 'normal range'})",
            f"   Trend: EMA20 (${ta['ema20']:,.0f}) {'>' if ta['ema20']>ta['ema50'] else '<'} EMA50 (${ta['ema50']:,.0f}) — {ta['trend']}"]
    if deriv:
        fr=deriv.get("funding_rate","N/A")
        fr_label="Shorts paying — bullish signal" if isinstance(fr,float) and fr<0 else "Longs paying — cautious" if isinstance(fr,float) and fr>0.01 else "Neutral"
        L+=["","3. Derivatives",f"   Funding Rate: {fr}% ({fr_label})",f"   Open Interest: {deriv.get('open_interest','N/A')}"]
    L+=["","4. Macro Sentiment",f"   Fear & Greed: {fng_val} — {fng_label}",
        f"   USD/EUR: {usd_eur}",f"   USD/JPY: {usd_jpy}",
        "","5. Oracle Signal",f"   {signal}","",
        directional_call(ticker,btc_num,btc_c,ta,deriv,fng_val),"",
        "Powered by Octodamus (@octodamusai)"]
    return "\n".join(L)


def handle_fear_greed(req):
    pulse=octo_pulse.run_pulse_scan()
    fng=pulse.get("fear_greed",{}); val=int(fng.get("value",50) or 50); label=fng.get("label","N/A")
    wiki=pulse.get("wikipedia",{}); spikes=wiki.get("spikes",[])[:3] if wiki else []
    if val<20: pos="STRONG BUY — Capitulation zone. Best entry for 30-90 day holds."; ctx="Markets at maximum fear. Institutional accumulation likely."
    elif val<40: pos="CAUTIOUS BUY — Scale in. Don't chase."; ctx="Fear elevated. Smart money accumulating quietly."
    elif val<60: pos="HOLD — No strong signal. Wait for extremes."; ctx="Market at equilibrium."
    elif val<80: pos="REDUCE EXPOSURE — Greed building. Trim profits."; ctx="Retail FOMO increasing."
    else: pos="EXIT — Extreme greed precedes corrections."; ctx="Everyone is bullish. That is the signal to be cautious."
    ta=fetch_technicals("BTC"); deriv=fetch_derivatives("BTC")
    L=["OCTODAMUS FEAR & GREED SENTIMENT READ",
       f"Generated: {datetime.utcnow().strftime('%a, %b %d, %Y')}","",
       f"Fear & Greed Index: {val} — {label.upper()}","",
       f"Context: {ctx}","",f"Positioning Signal: {pos}"]
    if spikes: L+=["",f"Wikipedia Attention Spikes: {', '.join(spikes)}"]
    if ta: L+=["","Technical Confirmation:",f"   RSI: {ta['rsi']} | MACD: {ta['macd']} | Trend: {ta['trend']}"]
    if deriv: L+=[f"   Funding Rate: {deriv.get('funding_rate','N/A')}% | OI: {deriv.get('open_interest','N/A')}"]
    L+=["",directional_call("BTC",0,0,ta,deriv,val),"","Powered by Octodamus (@octodamusai)"]
    return "\n".join(L)


def handle_bitcoin_analysis(req):
    import httpx
    ticker=req.get("ticker","BTC").upper()
    timeframe=req.get("timeframe","4h")
    cg_map={"BTC":"bitcoin","ETH":"ethereum","SOL":"solana","BNB":"binancecoin","XRP":"ripple","DOGE":"dogecoin"}
    cg_id=cg_map.get(ticker,ticker.lower())
    pulse=octo_pulse.run_pulse_scan(); fng_val=int(pulse.get("fear_greed",{}).get("value",50) or 50)
    ta=fetch_technicals(ticker); deriv=fetch_derivatives(ticker)
    price=chg_24h=chg_7d=chg_30d=ath=ath_pct=mcap=vol=high_24h=low_24h=circ=max_sup=0
    try:
        r=httpx.get(f"https://api.coingecko.com/api/v3/coins/{cg_id}",
            params={"localization":"false","tickers":"false","community_data":"false"},timeout=8)
        if r.status_code==200:
            md=r.json().get("market_data",{})
            price=md.get("current_price",{}).get("usd",0) or 0
            chg_24h=md.get("price_change_percentage_24h",0) or 0
            chg_7d=md.get("price_change_percentage_7d",0) or 0
            chg_30d=md.get("price_change_percentage_30d",0) or 0
            ath=md.get("ath",{}).get("usd",0) or 0
            ath_pct=md.get("ath_change_percentage",{}).get("usd",0) or 0
            mcap=md.get("market_cap",{}).get("usd",0) or 0
            vol=md.get("total_volume",{}).get("usd",0) or 0
            high_24h=md.get("high_24h",{}).get("usd",0) or 0
            low_24h=md.get("low_24h",{}).get("usd",0) or 0
            circ=md.get("circulating_supply",0) or 0
            max_sup=md.get("max_supply",0) or 0
    except Exception: pass
    sup_str=f"{circ/max_sup*100:.1f}% circulating" if max_sup else "No max supply"
    support=low_24h*0.97 if low_24h else 0
    resistance=high_24h*1.03 if high_24h else 0
    bull_t=price*1.18 if price else 0; bear_t=price*0.82 if price else 0
    momentum="N/A"
    if ta:
        rsi,macd,e20,e50=ta.get("rsi",50),ta.get("macd",0),ta.get("ema20",0),ta.get("ema50",0)
        if rsi>70: momentum="Overbought"
        elif rsi<30: momentum="Oversold"
        elif macd>0 and e20>e50: momentum="Leaning Bullish"
        elif macd<0 and e20<e50: momentum="Leaning Bearish"
        else: momentum="Consolidating"
    L=[f"OCTODAMUS {ticker} DEEP DIVE",
       f"Generated: {datetime.utcnow().strftime('%a, %b %d, %Y')} | Timeframe: {timeframe}","",
       "1. Price & Performance",
       f"   Current:    ${price:,.2f}",
       f"   24h Range:  ${low_24h:,.2f} — ${high_24h:,.2f}",
       f"   24h Change: {chg_24h:+.2f}%",
       f"   7d Change:  {chg_7d:+.2f}%",
       f"   30d Change: {chg_30d:+.2f}%",
       f"   ATH:        ${ath:,.2f} ({ath_pct:+.1f}% from ATH)",
       f"   Momentum:   {momentum}"]
    if ta:
        L+=["",f"2. Technical Analysis ({timeframe})",
            f"   MACD: {ta['macd']} ({'Bullish' if ta['macd']>0 else 'Bearish'} momentum)",
            f"   RSI: {ta['rsi']} ({'Overbought >70' if ta['rsi']>70 else 'Oversold <30' if ta['rsi']<30 else 'Neutral territory'})",
            f"   Bollinger Bands: {ta['bb_width']}% width",
            f"   EMA20: ${ta['ema20']:,.0f} | EMA50: ${ta['ema50']:,.0f} — {ta['trend']}"]
    if deriv:
        L+=["","3. Derivatives & Market Structure",
            f"   Funding Rate: {deriv.get('funding_rate','N/A')}%",
            f"   Open Interest: {deriv.get('open_interest','N/A')}",
            f"   Market Cap: ${mcap/1e9:.2f}B",
            f"   24h Volume: ${vol/1e9:.2f}B",
            f"   Supply: {sup_str}"]
    L+=["","4. Price Targets",
        f"   Support:    ${support:,.2f}",
        f"   Resistance: ${resistance:,.2f}",
        f"   Bull case:  ${bull_t:,.0f} (+18%)",
        f"   Bear case:  ${bear_t:,.0f} (-18%)",
        f"   Fear & Greed: {fng_val}","",
        "5. Oracle Call",
        f"   {directional_call(ticker,price,chg_24h,ta,deriv,fng_val)}","",
        "Powered by Octodamus (@octodamusai)"]
    return "\n".join(L)


def handle_congressional(req):
    ticker=req.get("ticker","NVDA").upper()
    try:
        import quiverquant
        token=os.environ.get("QUIVER_API_KEY","")
        if not token:
            return f"OCTODAMUS CONGRESSIONAL ALERT\nQUIVER_API_KEY unavailable."
        quiver=quiverquant.quiver(token)
        df=quiver.congress_trading(ticker)
        if df is None or df.empty:
            return f"OCTODAMUS CONGRESSIONAL ALERT — {ticker}\nNo trades found."
        from datetime import timedelta
        cutoff_r=datetime.now()-timedelta(days=45)
        cutoff_h=datetime.now()-timedelta(days=730)
        df["TransactionDate"]=df["TransactionDate"].apply(
            lambda x: x if hasattr(x,"year") else datetime.strptime(str(x)[:10],"%Y-%m-%d"))
        recent=df[df["TransactionDate"]>=cutoff_r]
        period_label="last 45 days"
        if recent.empty:
            recent=df[df["TransactionDate"]>=cutoff_h].head(10)
            period_label="2-year history"
        L=[f"OCTODAMUS CONGRESSIONAL TRADE ALERT — {ticker}",
           f"Generated: {datetime.utcnow().strftime('%a, %b %d, %Y')}",
           f"Period: {period_label}","",
           "Core belief: Congress front-runs markets.",
           "They trade on what they know is coming. Follow the money.","",
           "Recent Trades:"]
        buys=sells=0
        for _,row in recent.iterrows():
            name=str(row.get("Representative","Unknown"))
            party=str(row.get("Party",""))
            p_tag="(R)" if "republican" in party.lower() else "(D)" if "democrat" in party.lower() else ""
            tx=str(row.get("Transaction","")).lower()
            direction="BUY" if "purchase" in tx or "buy" in tx else "SELL"
            amount=str(row.get("Range",row.get("Amount","N/A")))
            date=str(row.get("TransactionDate",""))[:10]
            if direction=="BUY": buys+=1
            else: sells+=1
            L.append(f"   {name} {p_tag} {direction} — {amount} — {date}")
        L+=["",f"Summary: {buys} buys, {sells} sells in {period_label}"]
        if buys>sells:
            L+=["","Oracle read: Net congressional BUYING on {ticker}. Committee insiders accumulating — something favorable may be coming.".format(ticker=ticker)]
        elif sells>buys:
            L+=["","Oracle read: Net congressional SELLING on {ticker}. Politicians dumping ahead of potential headwinds — watch for regulatory or earnings risk.".format(ticker=ticker)]
        else:
            L+=["","Oracle read: Mixed congressional activity on {ticker}. No clear directional signal from the Hill.".format(ticker=ticker)]
        pulse=octo_pulse.run_pulse_scan()
        fng=int(pulse.get("fear_greed",{}).get("value",50) or 50)
        fng_lbl=pulse.get("fear_greed",{}).get("label","N/A")
        L+=["",f"Macro context: Fear & Greed {fng} — {fng_lbl}","",
            "Powered by Octodamus (@octodamusai)"]
        return "\n".join(L)
    except Exception as e:
        log.error(f"Congressional handler error: {e}")
        return f"OCTODAMUS CONGRESSIONAL ALERT\nError fetching data for {ticker}: {e}"


# ── Routing ───────────────────────────────────────────────────────────────────

def route_job(service_name, requirements):
    sn=service_name.lower().replace("_"," ").strip()
    ticker=str(requirements.get("ticker","")).upper()
    if sn:
        if any(k in sn for k in ["congressional","congress","stock trade","stock alert"]): return handle_congressional
        if any(k in sn for k in ["fear greed","sentiment","fear"]): return handle_fear_greed
        if any(k in sn for k in ["bitcoin","deep dive","analysis forecast","price analysis"]): return handle_bitcoin_analysis
        if any(k in sn for k in ["crypto market","market signal","oracle briefing","signal report"]): return handle_crypto_market_signal
    if ticker in VALID_STOCKS:
        log.info(f"Stock ticker {ticker} — routing to congressional")
        return handle_congressional
    log.info(f"Default routing to market signal (sn='{service_name}', ticker='{ticker}')")
    return handle_crypto_market_signal


# ── Task Handler ──────────────────────────────────────────────────────────────

def _handle_task(task, memo_to_sign=None):
    job_id       = getattr(task,"id","unknown")
    service_name = getattr(task,"service_name",None) or ""
    requirements = getattr(task,"service_requirement",None) or getattr(task,"requirement",None) or {}
    phase        = str(getattr(task,"phase","") or "")

    log.info(f"Job #{job_id} | service='{service_name}' | phase={phase} | req={requirements}")

    handler = route_job(service_name, requirements)

    try:
        if "TRANSACTION" in phase.upper():
            # Payment received — generate and deliver
            deliverable = handler(requirements)
            log.info(f"Job #{job_id} delivering ({len(deliverable)} chars)")
            task.deliver({"response": deliverable})
            log.info(f"Job #{job_id} delivered ✅")
        else:
            # Validate request
            ticker = str(requirements.get("ticker","")).strip().upper()
            if not requirements:
                task.reject("Invalid request: no requirements provided. Please include a ticker.")
                log.warning(f"Job #{job_id} rejected — empty requirements")
                return
            if ticker and ticker not in VALID_TICKERS:
                task.reject(f"Unsupported ticker: {ticker}. Supported: BTC,ETH,SOL,NVDA,TSLA,AAPL,MSFT,AMZN,META,GOOGL")
                log.warning(f"Job #{job_id} rejected — unsupported ticker {ticker}")
                return
            # Accept and request payment
            task.accept("Octodamus oracle ready. Generating report upon payment.")
            log.info(f"Job #{job_id} accepted")
            task.create_requirement("Payment required to receive oracle report.")
            log.info(f"Job #{job_id} payment requested")

    except Exception as e:
        log.error(f"Job #{job_id} error: {e}")
        try:
            task.reject(f"Octodamus internal error: {e}")
        except Exception as e2:
            log.error(f"Job #{job_id} reject failed: {e2}")


def on_new_task(task, memo_to_sign=None):
    job_id = getattr(task,"id","unknown")
    log.info(f"Job #{job_id} queued (queue size: {JOB_QUEUE.qsize()})")
    JOB_QUEUE.put((task, memo_to_sign))


def on_evaluate(task):
    job_id = getattr(task,"id","unknown")
    service_name = getattr(task,"service_name",None) or ""
    requirements = getattr(task,"service_requirement",None) or getattr(task,"requirement",None) or {}
    handler = route_job(service_name, requirements)
    log.info(f"Job #{job_id} on_evaluate — delivering")
    try:
        deliverable = handler(requirements)
        task.deliver({"response": deliverable})
        log.info(f"Job #{job_id} delivered via on_evaluate ✅")
    except Exception as e:
        log.error(f"Job #{job_id} on_evaluate failed: {e}")


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    log.info("Loading ACP wallet private key...")
    private_key = os.environ.get("OCTO_ACP_PRIVATE_KEY","")
    if not private_key:
        try:
            from bitwarden import _get_password as get_secret
            private_key = get_secret(BITWARDEN_ITEM)
        except Exception as e:
            log.error(f"Could not load private key: {e}")
            sys.exit(1)
    if not private_key:
        log.error("Private key empty. Aborting.")
        sys.exit(1)
    if private_key.startswith("0x"):
        private_key = private_key[2:]

    # Load QUIVER key
    quiver_key = os.environ.get("QUIVER_API_KEY","")
    if not quiver_key:
        try:
            kp = os.path.join(os.path.dirname(__file__),"octo_quiver_key.txt")
            if os.path.exists(kp):
                quiver_key = open(kp).read().strip()
                os.environ["QUIVER_API_KEY"] = quiver_key
                log.info("QUIVER_API_KEY loaded from file")
        except Exception:
            pass

    log.info(f"Connecting — entity={SELLER_ENTITY_ID} wallet={SELLER_AGENT_WALLET}")
    log.info(f"QUIVER key: {bool(quiver_key)} | Queue worker: running")

    contract_client = ACPContractClientV2(
        agent_wallet_address=SELLER_AGENT_WALLET,
        wallet_private_key=private_key,
        entity_id=SELLER_ENTITY_ID,
        config=BASE_MAINNET_CONFIG_V2,
    )

    acp = VirtualsACP(
        acp_contract_clients=contract_client,
        on_new_task=on_new_task,
        on_evaluate=on_evaluate,
    )

    log.info("Octodamus ACP worker online. Listening for jobs...")
    acp.init()
    log.info("Worker running.")

    import signal, time
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Shutting down.")


if __name__ == "__main__":
    asyncio.run(main())

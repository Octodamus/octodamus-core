"""
octo_report_handlers.py — Shared report generation for ACP worker + API server.
Imported by both octo_acp_worker.py and octo_api_server.py.
"""

import os
import statistics
from datetime import datetime

# ── Kraken Technical Analysis ─────────────────────────────────────────────────

def _kraken_ohlc_pair(ticker):
    m = {"BTC":"XBTUSD","ETH":"ETHUSD","SOL":"SOLUSD","BNB":"BNBUSD",
         "XRP":"XRPUSD","DOGE":"DOGEUSD"}
    return m.get(ticker.upper(), ticker.upper()+"USD")

def _kraken_futures_sym(ticker):
    m = {"BTC":"PI_XBTUSD","ETH":"PI_ETHUSD","SOL":"PI_SOLUSD"}
    return m.get(ticker.upper(), "PI_XBTUSD")

def _ema(data, period):
    k = 2/(period+1); e = data[0]
    for p in data[1:]: e = p*k + e*(1-k)
    return round(e, 2)

def fetch_technicals(ticker="BTC"):
    import httpx
    try:
        r = httpx.get("https://api.kraken.com/0/public/OHLC",
            params={"pair": _kraken_ohlc_pair(ticker), "interval": 240, "count": 50}, timeout=8)
        if r.status_code != 200 or r.json().get("error"): return {}
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
        recent = closes[-20:]; bb_m = sum(recent)/20
        bb_s = statistics.stdev(recent)
        bb_w = round((bb_m+2*bb_s-(bb_m-2*bb_s))/bb_m*100, 1)
        return {"ema20":ema20,"ema50":ema50,"trend":"Bullish" if ema20>ema50 else "Bearish",
                "rsi":rsi,"macd":macd,"bb_width":bb_w}
    except Exception: return {}

def fetch_derivatives(ticker="BTC"):
    import httpx
    sym = _kraken_futures_sym(ticker)
    try:
        r = httpx.get("https://futures.kraken.com/derivatives/api/v3/tickers", timeout=8)
        if r.status_code != 200: return {}
        t = next((x for x in r.json().get("tickers",[]) if x.get("symbol")==sym), None)
        if not t: return {}
        fr = float(t.get("fundingRate",0) or 0)
        oi = float(t.get("openInterest",0) or 0)
        px = float(t.get("markPrice",71000) or 71000)
        return {"funding_rate":round(fr*100,6),"open_interest":f"${oi*px/1e9:.2f}B",
                "high_24h":t.get("high24h",0),"low_24h":t.get("low24h",0)}
    except Exception: return {}

def directional_call(ticker, price, chg_24h, ta, deriv, fng):
    if not ta: return f"OCTODAMUS CALL: Insufficient data for {ticker}."
    rsi=float(ta.get("rsi",50)or 50); macd=float(ta.get("macd",0)or 0)
    e20=float(ta.get("ema20",0)or 0); e50=float(ta.get("ema50",0)or 0)
    bb_w=float(ta.get("bb_width",5)or 5); fr=float((deriv or {}).get("funding_rate",0)or 0)
    bull=bear=0
    if macd>0: bull+=1
    else: bear+=1
    if e20>e50: bull+=1
    else: bear+=1
    if rsi<45: bull+=1
    elif rsi>65: bear+=1
    if fng<25: bull+=1
    elif fng>75: bear+=1
    if fr<0: bull+=1
    elif fr>0.005: bear+=1
    if chg_24h>2: bull+=1
    elif chg_24h<-2: bear+=1
    p = f"${price:,.0f}" if price else "current level"
    if bb_w<3.0:
        d="UP" if bull>bear else "DOWN"
        return f"DIRECTION: BREAKOUT IMMINENT — BB compressed to {bb_w}%. Resolving {d}."
    elif bull>=4: return f"DIRECTION: UP — {bull}/{bull+bear} signals bullish. {ticker} likely continues higher. Hold or add."
    elif bear>=4: return f"DIRECTION: DOWN — {bear}/{bull+bear} signals bearish. {ticker} under pressure. Risk off."
    elif bull==bear: return f"DIRECTION: RANGE — Conflicting signals. {ticker} range-bound near {p}."
    elif bull>bear: return f"DIRECTION: LEANING UP — Mild bullish bias. {ticker} likely grinds higher."
    else: return f"DIRECTION: LEANING DOWN — Mild bearish bias. {ticker} facing resistance near {p}."


# ── Report Handlers ───────────────────────────────────────────────────────────

VALID_CRYPTO  = {"BTC","ETH","SOL","BNB","XRP","DOGE","AVAX","LINK","ADA","DOT"}
VALID_STOCKS  = {"NVDA","TSLA","AAPL","MSFT","AMZN","META","GOOGL","SPY","QQQ"}
VALID_TICKERS = VALID_CRYPTO | VALID_STOCKS

def handle_crypto_market_signal(req):
    import httpx
    import sys, os
    sys.path.insert(0, os.path.dirname(__file__))
    import octo_pulse, octo_gecko, octo_fx
    ticker = req.get("ticker","BTC").upper()
    pulse = octo_pulse.run_pulse_scan()
    gecko = octo_gecko.run_gecko_scan()
    fx    = octo_fx.run_fx_scan() if hasattr(octo_fx,"run_fx_scan") else {}
    fng_val   = int(pulse.get("fear_greed",{}).get("value",50)or 50)
    fng_label = pulse.get("fear_greed",{}).get("label","N/A")
    btc_dom   = gecko.get("btc_dominance",gecko.get("global",{}).get("btc_dominance","N/A"))
    btc_p=eth_p=sol_p="N/A"; btc_c=eth_c=sol_c=0.0
    try:
        r=httpx.get("https://api.coingecko.com/api/v3/simple/price",
            params={"ids":"bitcoin,ethereum,solana","vs_currencies":"usd","include_24hr_change":"true"},timeout=6)
        if r.status_code==200:
            d=r.json()
            btc_p=f"${d['bitcoin']['usd']:,.0f}"; btc_c=d['bitcoin']['usd_24h_change']
            eth_p=f"${d['ethereum']['usd']:,.0f}"; eth_c=d['ethereum']['usd_24h_change']
            sol_p=f"${d['solana']['usd']:,.2f}"; sol_c=d['solana']['usd_24h_change']
    except Exception: pass
    usd_eur=fx.get("key_pairs",{}).get("EUR",{}).get("rate","N/A") if fx else "N/A"
    usd_jpy=fx.get("key_pairs",{}).get("JPY",{}).get("rate","N/A") if fx else "N/A"
    ta=fetch_technicals(ticker); deriv=fetch_derivatives(ticker)
    momentum="N/A"
    if ta:
        rsi,macd,e20,e50=ta.get("rsi",50),ta.get("macd",0),ta.get("ema20",0),ta.get("ema50",0)
        if rsi>70: momentum="Overbought"
        elif rsi<30: momentum="Oversold"
        elif macd>0 and e20>e50: momentum="Leaning Bullish"
        elif macd<0 and e20<e50: momentum="Leaning Bearish"
        else: momentum="Consolidating"
    if fng_val<20: signal="ACCUMULATE — Extreme fear historically precedes recovery."
    elif fng_val<40: signal="CAUTIOUS BUY — Fear present. Scale in carefully."
    elif fng_val<60: signal="NEUTRAL — Hold. Wait for directional confirmation."
    elif fng_val<80: signal="REDUCE — Greed elevated. Consider partial profits."
    else: signal="EXIT RISK — Extreme greed. High correction probability."
    btc_num=0
    try: btc_num=float(btc_p.replace("$","").replace(",","")) if btc_p!="N/A" else 0
    except Exception: pass
    call = directional_call(ticker,btc_num,btc_c,ta,deriv,fng_val)
    return {
        "type":"market_signal","ticker":ticker,
        "title":"OCTODAMUS MARKET ORACLE BRIEFING",
        "generated":datetime.utcnow().strftime("%a, %b %d, %Y"),
        "prices":{"BTC":{"price":btc_p,"chg":btc_c},"ETH":{"price":eth_p,"chg":eth_c},"SOL":{"price":sol_p,"chg":sol_c}},
        "btc_dom":btc_dom,"momentum":momentum,
        "ta":ta,"deriv":deriv,
        "fng_val":fng_val,"fng_label":fng_label,
        "usd_eur":usd_eur,"usd_jpy":usd_jpy,
        "signal":signal,"call":call,
    }

def handle_fear_greed(req):
    import sys, os; sys.path.insert(0, os.path.dirname(__file__))
    import octo_pulse
    pulse=octo_pulse.run_pulse_scan()
    fng=pulse.get("fear_greed",{}); val=int(fng.get("value",50)or 50); label=fng.get("label","N/A")
    wiki=pulse.get("wikipedia",{}); spikes=wiki.get("spikes",[])[:3] if wiki else []
    if val<20: pos="STRONG BUY"; ctx="Capitulation zone. Best entry for 30-90 day holds."
    elif val<40: pos="CAUTIOUS BUY"; ctx="Fear elevated. Smart money accumulating quietly."
    elif val<60: pos="HOLD"; ctx="Market at equilibrium. Wait for extremes."
    elif val<80: pos="REDUCE EXPOSURE"; ctx="Retail FOMO increasing. Trim profits."
    else: pos="EXIT"; ctx="Everyone is bullish. That is the signal to be cautious."
    ta=fetch_technicals("BTC"); deriv=fetch_derivatives("BTC")
    call=directional_call("BTC",0,0,ta,deriv,val)
    return {
        "type":"fear_greed","ticker":"BTC",
        "title":"OCTODAMUS FEAR & GREED SENTIMENT READ",
        "generated":datetime.utcnow().strftime("%a, %b %d, %Y"),
        "fng_val":val,"fng_label":label,
        "position":pos,"context":ctx,"spikes":spikes,
        "ta":ta,"deriv":deriv,"call":call,
    }

def handle_bitcoin_analysis(req):
    import httpx, sys, os; sys.path.insert(0, os.path.dirname(__file__))
    import octo_pulse
    ticker=req.get("ticker","BTC").upper()
    timeframe=req.get("timeframe","4h")
    cg_map={"BTC":"bitcoin","ETH":"ethereum","SOL":"solana","BNB":"binancecoin","XRP":"ripple","DOGE":"dogecoin"}
    cg_id=cg_map.get(ticker,ticker.lower())
    pulse=octo_pulse.run_pulse_scan(); fng_val=int(pulse.get("fear_greed",{}).get("value",50)or 50)
    fng_label=pulse.get("fear_greed",{}).get("label","N/A")
    ta=fetch_technicals(ticker); deriv=fetch_derivatives(ticker)
    price=chg_24h=chg_7d=chg_30d=ath=ath_pct=mcap=vol=high_24h=low_24h=circ=max_sup=0
    try:
        r=httpx.get(f"https://api.coingecko.com/api/v3/coins/{cg_id}",
            params={"localization":"false","tickers":"false","community_data":"false"},timeout=8)
        if r.status_code==200:
            md=r.json().get("market_data",{})
            price=md.get("current_price",{}).get("usd",0)or 0
            chg_24h=md.get("price_change_percentage_24h",0)or 0
            chg_7d=md.get("price_change_percentage_7d",0)or 0
            chg_30d=md.get("price_change_percentage_30d",0)or 0
            ath=md.get("ath",{}).get("usd",0)or 0
            ath_pct=md.get("ath_change_percentage",{}).get("usd",0)or 0
            mcap=md.get("market_cap",{}).get("usd",0)or 0
            vol=md.get("total_volume",{}).get("usd",0)or 0
            high_24h=md.get("high_24h",{}).get("usd",0)or 0
            low_24h=md.get("low_24h",{}).get("usd",0)or 0
            circ=md.get("circulating_supply",0)or 0
            max_sup=md.get("max_supply",0)or 0
    except Exception: pass
    momentum="N/A"
    if ta:
        rsi,macd,e20,e50=ta.get("rsi",50),ta.get("macd",0),ta.get("ema20",0),ta.get("ema50",0)
        if rsi>70: momentum="Overbought"
        elif rsi<30: momentum="Oversold"
        elif macd>0 and e20>e50: momentum="Leaning Bullish"
        elif macd<0 and e20<e50: momentum="Leaning Bearish"
        else: momentum="Consolidating"
    call=directional_call(ticker,price,chg_24h,ta,deriv,fng_val)
    return {
        "type":"bitcoin_analysis","ticker":ticker,
        "title":f"OCTODAMUS {ticker} DEEP DIVE",
        "generated":datetime.utcnow().strftime("%a, %b %d, %Y"),
        "timeframe":timeframe,
        "price":price,"chg_24h":chg_24h,"chg_7d":chg_7d,"chg_30d":chg_30d,
        "high_24h":high_24h,"low_24h":low_24h,
        "ath":ath,"ath_pct":ath_pct,"mcap":mcap,"vol":vol,
        "circ":circ,"max_sup":max_sup,"momentum":momentum,
        "fng_val":fng_val,"fng_label":fng_label,
        "ta":ta,"deriv":deriv,"call":call,
    }

def handle_congressional(req):
    import sys, os; sys.path.insert(0, os.path.dirname(__file__))
    import octo_pulse
    from datetime import timedelta
    ticker=req.get("ticker","NVDA").upper()
    try:
        import quiverquant
        token=os.environ.get("QUIVER_API_KEY","")
        if not token:
            for kp in [r"C:\Users\walli\octodamus\octo_quiver_key.txt",
                       "/home/walli/octodamus/octo_quiver_key.txt"]:
                try:
                    import pathlib
                    t = pathlib.Path(kp).read_text().strip()
                    if t:
                        token = t
                        os.environ["QUIVER_API_KEY"] = t
                        break
                except Exception:
                    pass
        if not token: return {"type":"congressional","ticker":ticker,"error":"QUIVER_API_KEY unavailable"}
        quiver=quiverquant.quiver(token)
        df=quiver.congress_trading(ticker)
        if df is None or df.empty: return {"type":"congressional","ticker":ticker,"trades":[],"error":"No trades found"}
        cutoff_r=datetime.now()-timedelta(days=45)
        cutoff_h=datetime.now()-timedelta(days=730)
        df["TransactionDate"]=df["TransactionDate"].apply(
            lambda x: x if hasattr(x,"year") else datetime.strptime(str(x)[:10],"%Y-%m-%d"))
        recent=df[df["TransactionDate"]>=cutoff_r]; period_label="Last 45 days"
        if recent.empty: recent=df[df["TransactionDate"]>=cutoff_h].head(10); period_label="2-year history"
        trades=[]
        buys=sells=0
        for _,row in recent.iterrows():
            name=str(row.get("Representative","Unknown"))
            party=str(row.get("Party",""))
            p_tag="R" if "republican" in party.lower() else "D" if "democrat" in party.lower() else "?"
            tx=str(row.get("Transaction","")).lower()
            direction="BUY" if "purchase" in tx or "buy" in tx else "SELL"
            amount=str(row.get("Range",row.get("Amount","N/A")))
            date_str=str(row.get("TransactionDate",""))[:10]
            if direction=="BUY": buys+=1
            else: sells+=1
            trades.append({"name":name,"party":p_tag,"direction":direction,"amount":amount,"date":date_str})
        pulse=octo_pulse.run_pulse_scan()
        fng_val=int(pulse.get("fear_greed",{}).get("value",50)or 50)
        fng_label=pulse.get("fear_greed",{}).get("label","N/A")
        if buys>sells: interpretation=f"Net congressional BUYING on {ticker}. Committee insiders accumulating — something favorable may be coming."
        elif sells>buys: interpretation=f"Net congressional SELLING on {ticker}. Politicians dumping ahead of potential headwinds — watch for regulatory or earnings risk."
        else: interpretation=f"Mixed congressional activity on {ticker}. No clear directional signal from the Hill."
        call = "DIRECTION: LEANING UP" if buys>sells else "DIRECTION: LEANING DOWN" if sells>buys else "DIRECTION: RANGE"
        return {
            "type":"congressional","ticker":ticker,
            "title":"CONGRESSIONAL TRADE REPORT",
            "subtitle":"OCTODAMUS CONGRESSIONAL TRADE ALERT",
            "generated":datetime.utcnow().strftime("%a, %b %d, %Y"),
            "period":period_label,"trades":trades,
            "buys":buys,"sells":sells,
            "interpretation":interpretation,"call":call,
            "fng_val":fng_val,"fng_label":fng_label,
        }
    except Exception as e:
        return {"type":"congressional","ticker":ticker,"error":str(e),"trades":[]}


# ── Route by type string ──────────────────────────────────────────────────────

def get_handler(report_type: str):
    t = report_type.lower().replace("-","_").replace(" ","_")
    if any(k in t for k in ["congressional","congress","stock_trade","stock_alert"]): return handle_congressional
    if any(k in t for k in ["fear_greed","sentiment","fear"]): return handle_fear_greed
    if any(k in t for k in ["bitcoin","deep_dive","analysis","forecast"]): return handle_bitcoin_analysis
    return handle_crypto_market_signal


# ── Text formatter (for ACP deliverable) ─────────────────────────────────────

def render_text(data: dict) -> str:
    t = data.get("type","")
    call = data.get("call","")
    err = data.get("error")
    if err: return f"OCTODAMUS REPORT ERROR\n{err}"

    if t == "market_signal":
        ta=data.get("ta",{}); deriv=data.get("deriv",{})
        prices=data.get("prices",{})
        L=[data["title"],f"Generated: {data['generated']}","",
           "1. Price & Performance",
           f"   BTC: {prices.get('BTC',{}).get('price','N/A')} ({prices.get('BTC',{}).get('chg',0):+.1f}%)",
           f"   ETH: {prices.get('ETH',{}).get('price','N/A')} ({prices.get('ETH',{}).get('chg',0):+.1f}%)",
           f"   SOL: {prices.get('SOL',{}).get('price','N/A')} ({prices.get('SOL',{}).get('chg',0):+.1f}%)",
           f"   BTC Dominance: {data.get('btc_dom','N/A')}% | Momentum: {data.get('momentum','N/A')}"]
        if ta: L+=["","2. Technical Analysis (4h)",
            f"   MACD: {ta.get('macd')} | RSI: {ta.get('rsi')} | Trend: {ta.get('trend')}",
            f"   EMA20: ${ta.get('ema20',0):,.0f} | EMA50: ${ta.get('ema50',0):,.0f} | BB: {ta.get('bb_width')}%"]
        if deriv: L+=["","3. Derivatives",
            f"   Funding Rate: {deriv.get('funding_rate')}% | OI: {deriv.get('open_interest')}"]
        L+=["","4. Macro Sentiment",f"   Fear & Greed: {data.get('fng_val')} — {data.get('fng_label')}",
            f"   USD/EUR: {data.get('usd_eur')} | USD/JPY: {data.get('usd_jpy')}",
            "","5. Oracle Signal",f"   {data.get('signal','')}","",
            f"OCTODAMUS CALL: {call}","","Powered by Octodamus (@octodamusai)"]
        return "\n".join(L)

    elif t == "fear_greed":
        ta=data.get("ta",{}); deriv=data.get("deriv",{})
        L=[data["title"],f"Generated: {data['generated']}","",
           f"Fear & Greed Index: {data.get('fng_val')} — {data.get('fng_label','').upper()}","",
           f"Context: {data.get('context','')}","",
           f"Positioning Signal: {data.get('position','')}"]
        if data.get("spikes"): L+=["",f"Wikipedia Attention Spikes: {', '.join(data['spikes'])}"]
        if ta: L+=["","Technical Confirmation:",
            f"   RSI: {ta.get('rsi')} | MACD: {ta.get('macd')} | Trend: {ta.get('trend')}"]
        L+=["",f"OCTODAMUS CALL: {call}","","Powered by Octodamus (@octodamusai)"]
        return "\n".join(L)

    elif t == "bitcoin_analysis":
        ta=data.get("ta",{}); deriv=data.get("deriv",{})
        price=data.get("price",0); low_24h=data.get("low_24h",0); high_24h=data.get("high_24h",0)
        support=low_24h*0.97 if low_24h else 0; resistance=high_24h*1.03 if high_24h else 0
        bull_t=price*1.18 if price else 0; bear_t=price*0.82 if price else 0
        ticker=data.get("ticker","BTC")
        L=[data["title"],f"Generated: {data['generated']} | Timeframe: {data.get('timeframe','4h')}","",
           "1. Price & Performance",
           f"   Current: ${price:,.2f}",
           f"   24h Range: ${low_24h:,.2f} — ${high_24h:,.2f}",
           f"   24h: {data.get('chg_24h',0):+.2f}% | 7d: {data.get('chg_7d',0):+.2f}% | 30d: {data.get('chg_30d',0):+.2f}%",
           f"   ATH: ${data.get('ath',0):,.2f} ({data.get('ath_pct',0):+.1f}%) | Momentum: {data.get('momentum','N/A')}"]
        if ta: L+=["","2. Technical Analysis",
            f"   MACD: {ta.get('macd')} | RSI: {ta.get('rsi')} | Trend: {ta.get('trend')}",
            f"   EMA20: ${ta.get('ema20',0):,.0f} | EMA50: ${ta.get('ema50',0):,.0f} | BB: {ta.get('bb_width')}%"]
        if deriv: L+=["","3. Derivatives & Structure",
            f"   Funding Rate: {deriv.get('funding_rate')}% | OI: {deriv.get('open_interest')}",
            f"   Market Cap: ${data.get('mcap',0)/1e9:.2f}B | 24h Vol: ${data.get('vol',0)/1e9:.2f}B"]
        L+=["","4. Price Targets",
            f"   Support: ${support:,.2f} | Resistance: ${resistance:,.2f}",
            f"   Bull case: ${bull_t:,.0f} (+18%) | Bear case: ${bear_t:,.0f} (-18%)",
            f"   Fear & Greed: {data.get('fng_val')} — {data.get('fng_label')}","",
            "5. Oracle Call",f"   OCTODAMUS CALL: {call}","",
            "Powered by Octodamus (@octodamusai)"]
        return "\n".join(L)

    elif t == "congressional":
        ticker=data.get("ticker","NVDA"); trades=data.get("trades",[])
        L=[data.get("title","CONGRESSIONAL TRADE REPORT"),
           f"Generated: {data.get('generated')} (Period: {data.get('period','')})",
           "","Core belief: Congress front-runs markets. Follow the money.","","Recent Trades:"]
        for tr in trades:
            L.append(f"   {tr['name']} ({tr['party']}) {tr['direction']} — {tr['amount']} — {tr['date']}")
        L+=["",f"Summary: {data.get('buys',0)} buys, {data.get('sells',0)} sells",
            "","Oracle read: "+data.get("interpretation",""),
            "",f"Macro context: Fear & Greed {data.get('fng_val')} — {data.get('fng_label','')}",
            "","OCTODAMUS CALL: "+data.get("call",""),
            "","Powered by Octodamus (@octodamusai)"]
        return "\n".join(L)

    return "OCTODAMUS REPORT\nUnknown report type."

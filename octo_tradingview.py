"""
octo_tradingview.py
OctoTV — Multi-Timeframe Technical Intelligence Mind
Uses yfinance (already installed). No API key required.
"""
import time, warnings
from datetime import datetime
from typing import Optional
warnings.filterwarnings("ignore")

STOCK_SYMBOLS  = ["NVDA", "TSLA", "AAPL", "SPY", "QQQ"]
CRYPTO_SYMBOLS = ["BTC-USD", "ETH-USD", "SOL-USD"]
ALL_SYMBOLS    = STOCK_SYMBOLS + CRYPTO_SYMBOLS
TIMEFRAME_CONFIG = [
    {"interval": "1h",  "period": "30d",  "label": "1H"},
    {"interval": "4h",  "period": "60d",  "label": "4H"},
    {"interval": "1d",  "period": "200d", "label": "1D"},
]
BARS = 100

def _check_deps():
    try:
        import yfinance, pandas, ta
        return True
    except ImportError as e:
        print(f"[OctoTV] Missing: {e}")
        print("[OctoTV] Install: pip3 install yfinance pandas ta --break-system-packages")
        return False

def _fetch(symbol, interval, period):
    try:
        import yfinance as yf
        df = yf.Ticker(symbol).history(interval=interval, period=period)
        return df.tail(BARS) if df is not None and not df.empty else None
    except Exception as e:
        print(f"  [OctoTV] {symbol} {interval}: {e}")
        return None

def _rsi(close, p=14):
    try:
        import ta, pandas as pd
        v = float(ta.momentum.RSIIndicator(pd.Series(close), window=p).rsi().iloc[-1])
        return round(v, 1) if v == v else None
    except: return None

def _macd(close):
    try:
        import ta, pandas as pd
        m = ta.trend.MACD(pd.Series(close))
        h = float(m.macd_diff().iloc[-1])
        return {"cross": "bullish" if h > 0 else "bearish", "hist": round(h, 4)}
    except: return {}

def _bb(close):
    try:
        import ta, pandas as pd
        s = pd.Series(close)
        bb = ta.volatility.BollingerBands(s, window=20, window_dev=2)
        u, l, m = float(bb.bollinger_hband().iloc[-1]), float(bb.bollinger_lband().iloc[-1]), float(bb.bollinger_mavg().iloc[-1])
        p = (float(s.iloc[-1]) - l) / (u - l) if u != l else 0.5
        return {"position": "overbought" if p > 0.8 else ("oversold" if p < 0.2 else "mid"), "upper": round(u,4), "lower": round(l,4)}
    except: return {}

def _sr(high, low, close, lb=20):
    try:
        hs, ls, price = list(high[-lb:]), list(low[-lb:]), float(close[-1])
        sh = [hs[i] for i in range(2, len(hs)-2) if hs[i] > hs[i-1] and hs[i] > hs[i-2] and hs[i] > hs[i+1] and hs[i] > hs[i+2]]
        sl = [ls[i] for i in range(2, len(ls)-2) if ls[i] < ls[i-1] and ls[i] < ls[i-2] and ls[i] < ls[i+1] and ls[i] < ls[i+2]]
        r = min((h for h in sh if h > price), default=None)
        s = max((l for l in sl if l < price), default=None)
        return {"price": round(price,4), "resistance": round(r,4) if r else None, "support": round(s,4) if s else None}
    except: return {}

def _trend(close, lb=20):
    try:
        ps = list(close[-lb:]); n = len(ps); xm = (n-1)/2; ym = sum(ps)/n
        slope = sum((i-xm)*(ps[i]-ym) for i in range(n)) / sum((i-xm)**2 for i in range(n))
        pct = slope / ym * 100
        return {"direction": "uptrend" if pct > 0.1 else ("downtrend" if pct < -0.1 else "sideways")}
    except: return {}

def _scan_symbol(symbol):
    result = {"symbol": symbol, "timeframes": {}}
    for tf in TIMEFRAME_CONFIG:
        df = _fetch(symbol, tf["interval"], tf["period"])
        if df is None:
            time.sleep(0.5); continue
        c, h, l, v = df["Close"].values, df["High"].values, df["Low"].values, df["Volume"].values
        rsi = _rsi(c); macd = _macd(c); bb = _bb(c); sr = _sr(h, l, c); trend = _trend(c)
        sigs = []
        if rsi:
            if rsi > 70: sigs.append("RSI_OB")
            if rsi < 30: sigs.append("RSI_OS")
        if bb.get("position") == "overbought": sigs.append("BB_OB")
        if bb.get("position") == "oversold":   sigs.append("BB_OS")
        if macd.get("cross") == "bullish":     sigs.append("MACD_BULL")
        if macd.get("cross") == "bearish":     sigs.append("MACD_BEAR")
        result["timeframes"][tf["label"]] = {"price": round(float(c[-1]),4), "rsi": rsi, "macd": macd, "bb": bb, "sr": sr, "trend": trend, "signals": sigs}
        print(f"  [OctoTV] {symbol:10s} {tf['label']:3s} | price={c[-1]:.2f} rsi={rsi} trend={trend.get('direction','?')}") 
        time.sleep(0.3)
    return result

def _confluence(data):
    tfs = data.get("timeframes", {})
    trends = [tfs[t]["trend"]["direction"] for t in tfs if tfs[t].get("trend")]
    bull, bear = trends.count("uptrend"), trends.count("downtrend")
    if bull >= 2 and bear == 0:   d, s = "bullish", "high" if bull==3 else "medium"
    elif bear >= 2 and bull == 0: d, s = "bearish", "high" if bear==3 else "medium"
    elif bull > 0 and bear > 0:   d, s = "mixed",   "weak"
    else:                          d, s = "neutral",  "weak"
    all_sigs = []
    for td in tfs.values(): all_sigs.extend(td.get("signals", []))
    return {"direction": d, "strength": s, "active_signals": list(set(all_sigs))}

def run_tv_scan(symbols=None):
    if not _check_deps():
        return {"error": "missing_deps", "timestamp": datetime.utcnow().isoformat()}
    if symbols is None: symbols = ALL_SYMBOLS
    print("[OctoTV] Starting multi-timeframe scan (yfinance)...")
    results = {"timestamp": datetime.utcnow().isoformat(), "symbols": {}, "confluence": {}}
    for sym in symbols:
        print(f"\n[OctoTV] {sym}...")
        d = _scan_symbol(sym)
        results["symbols"][sym] = d
        results["confluence"][sym] = _confluence(d)
        time.sleep(0.5)
    cvs = list(results["confluence"].values())
    bull = sum(1 for c in cvs if c["direction"] == "bullish")
    bear = sum(1 for c in cvs if c["direction"] == "bearish")
    results["market_bias"] = "risk-on" if bull > bear else ("risk-off" if bear > bull else "neutral")
    results["bull_count"] = bull; results["bear_count"] = bear
    print(f"\n[OctoTV] Done. Bias: {results['market_bias']} ({bull} bull / {bear} bear)")
    return results

def format_tv_for_prompt(result):
    if result.get("error"): return f"[OctoTV unavailable: {result['error']}]"
    lines = [f"Multi-Timeframe Analysis (OctoTV): bias={result.get('market_bias','?').upper()} | {result.get('bull_count',0)} bull / {result.get('bear_count',0)} bear"]
    for sym, conf in result.get("confluence", {}).items():
        tfs = result["symbols"].get(sym, {}).get("timeframes", {})
        price = next((tfs[t]["price"] for t in ["1D","4H","1H"] if t in tfs), None)
        rsi = tfs.get("1D", {}).get("rsi")
        trend = tfs.get("1D", {}).get("trend", {}).get("direction", "?")
        sr = tfs.get("1D", {}).get("sr", {})
        sigs = conf.get("active_signals", [])
        lines.append(f"  {sym:10s} ${price:<10.2f} | {conf['direction'].upper()} ({conf['strength']}) | trend={trend} rsi={rsi} S:{sr.get('support','?')} R:{sr.get('resistance','?')} {sigs}")
    return "\n".join(lines)

if __name__ == "__main__":
    r = run_tv_scan()
    print("\n── OctoTV ─────────────────────")
    print(format_tv_for_prompt(r))

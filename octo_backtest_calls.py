"""
octo_backtest_calls.py -- re-score the real on-chain call record against the trend gate
and alternative horizons. This is the evidence behind .claude/rules/calls.md; run it again
before changing octo_regime thresholds or MIN_CALL_HOURS.

    python octo_backtest_calls.py

Uses CoinGecko hourly prices (demo key from .octo_secrets) for every resolved crypto call
with a tx_hash. Prices are cached in data/backtest_prices_cache.json; delete it to refresh.
"""
import json, os, sys, time, io
import requests
from datetime import datetime, timezone, timedelta
from collections import defaultdict

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = r"C:\Users\walli\octodamus"
raw = json.load(open(os.path.join(ROOT, ".octo_secrets"), encoding="utf-8"))
KEY = raw.get("secrets", raw).get("COINGECKO_API_KEY", "")
H = {"x-cg-demo-api-key": KEY, "User-Agent": "octodamus-backtest"}
CG = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "SUI": "sui"}
CACHE = os.path.join(ROOT, "data", "backtest_prices_cache.json")

calls = json.load(open(os.path.join(ROOT, "data", "octo_calls.json"), encoding="utf-8"))
res = [c for c in calls if c.get("tx_hash") and c.get("resolved") and c.get("outcome") in ("WIN", "LOSS")
       and c["asset"] in CG and c.get("call_type", "oracle") != "polymarket"]

def parse(ts): return datetime.strptime(ts, "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)

# ── price series per asset (hourly), fetched in <=80-day chunks and cached ─────────────
try:
    series = json.load(open(CACHE))
except Exception:
    series = {}
for asset, cid in CG.items():
    if asset in series:
        continue
    needed = [c for c in res if c["asset"] == asset]
    if not needed:
        continue
    start = min(parse(c["made_at"]) for c in needed) - timedelta(days=35)
    end = datetime.now(timezone.utc)
    pts = []
    cur = start
    while cur < end:
        nxt = min(cur + timedelta(days=80), end)
        r = requests.get(f"https://api.coingecko.com/api/v3/coins/{cid}/market_chart/range",
                         params={"vs_currency": "usd", "from": int(cur.timestamp()), "to": int(nxt.timestamp())},
                         headers=H, timeout=30)
        if r.status_code != 200:
            print(asset, "fetch", r.status_code, r.text[:100]); time.sleep(5); continue
        pts += r.json().get("prices", [])
        cur = nxt
        time.sleep(1.5)
    series[asset] = sorted(pts)
    json.dump(series, open(CACHE, "w"))
    print(f"{asset}: {len(pts)} hourly points")

def price_at(asset, dt):
    ts = dt.timestamp() * 1000
    s = series[asset]
    best = min(s, key=lambda p: abs(p[0] - ts))
    return best[1] if abs(best[0] - ts) < 3 * 3600 * 1000 else None

def window(asset, a, b):
    ta, tb = a.timestamp() * 1000, b.timestamp() * 1000
    return [p[1] for p in series[asset] if ta <= p[0] <= tb]

def regime(asset, dt):
    p = price_at(asset, dt)
    p7 = price_at(asset, dt - timedelta(days=7))
    p3 = price_at(asset, dt - timedelta(days=3))
    w20 = window(asset, dt - timedelta(days=20), dt)
    sma20 = sum(w20) / len(w20) if w20 else None
    if not (p and p7 and p3 and sma20):
        return None
    return {"p": p, "chg7": (p - p7) / p7 * 100, "chg3": (p - p3) / p3 * 100, "vs_sma20": (p - sma20) / sma20 * 100}

def bias(rg, strong=1.5):
    if rg["chg7"] > strong and rg["vs_sma20"] > 0: return "UP"
    if rg["chg7"] < -strong and rg["vs_sma20"] < 0: return "DOWN"
    return "FLAT"

def outcome_tf(c, hours):
    made = parse(c["made_at"]); exp = made + timedelta(hours=hours)
    entry = c["entry_price"]; tgt = c.get("target_price"); d = c["direction"]
    w = window(c["asset"], made, exp)
    pe = price_at(c["asset"], exp)
    if not w or pe is None: return None
    if tgt and ((d == "UP" and max(w) >= tgt) or (d == "DOWN" and min(w) <= tgt)): return "WIN"
    if d == "UP" and pe >= entry * 1.01: return "WIN"
    if d == "DOWN" and pe <= entry * 0.99: return "WIN"
    return "LOSS"

def rec(cs):
    w = sum(1 for c in cs if c == "WIN"); l = sum(1 for c in cs if c == "LOSS")
    return f"{w}W-{l}L ({w/(w+l)*100:.0f}%)" if w + l else "-"

print(f"\n{len(res)} resolved on-chain crypto calls\n")
print(f"{'id':>3} {'strat':<16}{'asset':<5}{'dir':<5}{'tf':<5}{'out':<5}{'chg7':>7}{'chg3':>7}{'vsSMA':>7} {'bias':<5} {'gate'}")
rows = []
for c in res:
    rg = regime(c["asset"], parse(c["made_at"]))
    if not rg:
        print(f"#{c['id']} no regime data"); continue
    b = bias(rg)
    opposed = (b == "UP" and c["direction"] == "DOWN") or (b == "DOWN" and c["direction"] == "UP")
    rows.append((c, rg, b, opposed))
    print(f"{c['id']:>3} {c.get('call_type','oracle'):<16}{c['asset']:<5}{c['direction']:<5}{c.get('timeframe',''):<5}{c['outcome']:<5}"
          f"{rg['chg7']:>+7.1f}{rg['chg3']:>+7.1f}{rg['vs_sma20']:>+7.1f} {b:<5} {'BLOCK' if opposed else 'ok'}")

print("\n== Trend gate (refuse calls that oppose a 7d+SMA20 bias) ==")
print("  actual record      :", rec([c["outcome"] for c, *_ in rows]))
print("  would be BLOCKED   :", rec([c["outcome"] for c, rg, b, op in rows if op]), f"({sum(1 for r in rows if r[3])} calls)")
print("  survivors          :", rec([c["outcome"] for c, rg, b, op in rows if not op]))
for strat in sorted(set(c.get("call_type", "oracle") for c, *_ in rows)):
    sub = [r for r in rows if r[0].get("call_type", "oracle") == strat]
    print(f"    {strat:<16} actual {rec([r[0]['outcome'] for r in sub]):<14} survivors {rec([r[0]['outcome'] for r in sub if not r[3]])}")

print("\n== Stricter: DOWN needs chg7<0 AND below SMA20; UP needs chg7>0 OR above SMA20 ==")
def strict_ok(c, rg):
    if c["direction"] == "DOWN": return rg["chg7"] < 0 and rg["vs_sma20"] < 0
    return rg["chg7"] > 0 or rg["vs_sma20"] > 0
print("  survivors          :", rec([c["outcome"] for c, rg, b, op in rows if strict_ok(c, rg)]),
      "blocked:", rec([c["outcome"] for c, rg, b, op in rows if not strict_ok(c, rg)]))

print("\n== Timeframe re-simulation (same win rule: target touched in-window OR >=1% at expiry) ==")
for strat in ("range_scout", "crowd_fade", "funding_extreme", "oracle"):
    sub = [c for c in res if c.get("call_type", "oracle") == strat]
    if not sub: continue
    line = f"  {strat:<16}"
    for hrs in (6, 12, 24, 48, 72):
        outs = [outcome_tf(c, hrs) for c in sub]
        line += f" {hrs:>2}h {rec([o for o in outs if o]):<13}"
    print(line)

print("\n== Crowd-fade trigger: only fade once price has turned (24h change against the crowd) ==")
for c in [c for c in res if c.get("call_type") == "crowd_fade"]:
    made = parse(c["made_at"]); p = price_at(c["asset"], made); p24 = price_at(c["asset"], made - timedelta(hours=24))
    ch = (p - p24) / p24 * 100 if p and p24 else None
    print(f"  #{c['id']} {c['asset']} {c['direction']} {c['outcome']:<5} 24h_chg_at_call={ch:+.2f}%" if ch is not None else f"  #{c['id']} n/a")

print("\n== Combined: trend gate + 24h timeframe, per strategy ==")
for strat in ("range_scout", "crowd_fade", "funding_extreme", "oracle"):
    sub = [(c, rg, b, op) for c, rg, b, op in rows if c.get("call_type", "oracle") == strat]
    surv = [c for c, rg, b, op in sub if not op]
    strict = [c for c, rg, b, op in sub if strict_ok(c, rg)]
    print(f"  {strat:<16} gate+24h {rec([outcome_tf(c,24) for c in surv]):<14} strict+24h {rec([outcome_tf(c,24) for c in strict]):<14} strict+48h {rec([outcome_tf(c,48) for c in strict])}")
allsurv=[c for c, rg, b, op in rows if not op]; allstrict=[c for c, rg, b, op in rows if strict_ok(c, rg)]
print(f"  {'ALL':<16} gate+24h {rec([outcome_tf(c,24) for c in allsurv]):<14} strict+24h {rec([outcome_tf(c,24) for c in allstrict]):<14} strict+48h {rec([outcome_tf(c,48) for c in allstrict])}")

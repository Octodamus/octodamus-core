"""Quick health check for all Octodamus arms."""

results = []

def check(name, fn):
    try:
        out = fn()
        results.append(("OK ", name, str(out)[:100] if out else "ok"))
    except Exception as e:
        results.append(("ERR", name, str(e)[:100]))

# ── Oracle calls
def _oracle():
    from octo_calls import get_stats
    s = get_stats()
    return f"{s.get('total',0)} calls | {s.get('wins',0)}W/{s.get('losses',0)}L | win_rate={s.get('win_rate','?')}"
check("Oracle/Calls", _oracle)

# ── Macro
def _macro():
    from octo_macro import get_macro_context
    r = get_macro_context()
    return r[:80] if r else "empty"
check("Macro (FRED)", _macro)

# ── Flights / Travel
def _flights():
    from octo_flights import get_travel_context
    r = get_travel_context()
    return r[:80] if r else "empty"
check("Flights/TSA", _flights)

# ── Coinglass
def _coinglass():
    from octo_coinglass import build_oracle_context
    r = build_oracle_context("BTC")
    return r[:80] if r else "empty"
check("Coinglass", _coinglass)

# ── Firecrawl
def _firecrawl():
    from octo_firecrawl import scrape_url
    return "module ok"
check("Firecrawl", _firecrawl)

# ── Polymarket Gamma
def _polymarket():
    from octo_boto_polymarket import GammaClient
    return "GammaClient ok"
check("Polymarket Gamma", _polymarket)

# ── CLOB V2
def _clob():
    from octo_boto_clob import clob_status_str, LIVE_MODE, PUSD_POLY
    return clob_status_str()
check("CLOB V2", _clob)

# ── BotCoin Math
def _boto_math():
    from octo_boto_math import is_valid_market, V2_READY
    return f"V2_READY={V2_READY}"
check("OctoBoto Math", _boto_math)

# ── Distro
def _distro():
    from octo_distro import oracle_scorecard, subscriber_count
    n = subscriber_count()
    return f"module ok | {n} subscribers"
check("Distro Engine", _distro)

# ── Personality
def _personality():
    from octo_personality import build_x_system_prompt
    p = build_x_system_prompt()
    return f"prompt {len(p)} chars"
check("Personality", _personality)

# ── Format Engine
def _format():
    from octo_format_engine import get_next_format
    f = get_next_format()
    return f"next format: {f}"
check("Format Engine", _format)

# ── OctoVision / Playwright
def _vision():
    from octo_playwright import TICKER_MAP, TIMEFRAME_MAP, chart_and_analyze, see_page
    return f"{len(TICKER_MAP)} tickers, {len(TIMEFRAME_MAP)} timeframes"
check("OctoVision", _vision)

# ── Congress
def _congress():
    from octo_congress import run_congress_scan
    return "module ok"
check("Congress", _congress)

# ── CEO
def _ceo():
    from octo_ceo import get_ceo_brief
    return "module ok"
check("CEO/Firecrawl", _ceo)

# ── GDrive
def _gdrive():
    from octo_gdrive import status
    return "module ok"
check("GDrive Backup", _gdrive)

# ── ACP Worker
def _acp():
    from octo_acp_worker import replay_funded_jobs, start_listener
    return "module ok"
check("ACP Worker", _acp)

# ── X Poster
def _xposter():
    from octo_x_poster import check_connection
    r = check_connection()
    return str(r)[:80]
check("X Poster", _xposter)

# ── Unusual Whales
def _uw():
    from octo_unusual_whales import get_uw_context
    r = get_uw_context()
    return r[:80] if r else "no key (expected)"
check("Unusual Whales", _uw)

# ── API Server
def _api():
    from octo_api_server import app
    return "FastAPI app ok"
check("API Server", _api)

# ── Telegram bot (import check only)
def _tg():
    import telegram_bot
    return "module ok"
check("Telegram Bot", _tg)

# ── BotCoin miner
def _botcoin():
    from octo_boto_botcoin import get_epoch_info
    return "module ok"
check("BotCoin Miner", _botcoin)

# ── AutoResolve
def _autoresolve():
    from octo_boto_autoresolve import auto_resolve_all
    return "module ok"
check("AutoResolve", _autoresolve)

# ── Print results
print()
print("=" * 64)
print("  OCTODAMUS ARMS CHECK")
print("=" * 64)
ok  = [r for r in results if r[0] == "OK "]
err = [r for r in results if r[0] == "ERR"]

for status, name, msg in results:
    icon = "[OK ]" if status == "OK " else "[ERR]"
    print(f"  {icon} {name:<24} {msg}")

print()
print(f"  PASSED: {len(ok)}   FAILED: {len(err)}")
print("=" * 64)

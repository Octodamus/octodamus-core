"""
octodamus_runner.py
Octodamus — Main Runner

Entry point for all scheduled tasks and manual runs.

Scheduled tasks (Task Scheduler, runs whether logged in or not):
    Octodamus-DailyRead       6:00 AM  Mon-Fri   --mode daily
    Octodamus-DailyRead-1pm   1:00 PM  Mon-Fri   --mode daily
    Octodamus-DailyRead-7pm   7:00 PM  Mon-Fri   --mode daily
    Octodamus-Monitor-7am     7:00 AM  Mon-Fri   --mode monitor
    Octodamus-Monitor-115pm   1:15 PM  Mon-Fri   --mode monitor
    Octodamus-Monitor-6pm     6:00 PM  Mon-Fri   --mode monitor
    Octodamus-Journal         9:00 PM  daily     --mode journal
    Octodamus-Wisdom          10:00 AM Saturday  --mode wisdom
    Octodamus-DeepDive-Mon    9:00 AM  Monday    --mode deep_dive --ticker NVDA
    Octodamus-DeepDive-Wed    9:00 AM  Wednesday --mode deep_dive --ticker BTC

Daily post budget: 20 posts max. Enforced in octo_x_poster.py.
"""

import argparse
import json
import logging
import os
import random
import sys
from datetime import datetime
from pathlib import Path

# ── Logging ───────────────────────────────────────────────────────────────────
_LOG_DIR = Path(r"C:\Users\walli\octodamus\logs")
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_log_file = _LOG_DIR / f"runner_{datetime.now().strftime('%Y-%m-%d')}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(_log_file, encoding="utf-8"),
    ],
)
log = logging.getLogger("Runner")

# Redirect print() to log so all existing prints land in the file too
_orig_print = print
def print(*args, **kwargs):
    msg = " ".join(str(a) for a in args)
    log.info(msg)

# ── Secrets — must load before any other imports that use os.environ ──────────
from bitwarden import load_all_secrets, verify_session

if not verify_session():
    sys.exit(1)

secrets = load_all_secrets(verbose=True)

# ── Imports that depend on secrets ────────────────────────────────────────────
import anthropic
from financial_data_client import get_current_price, get_current_crypto_price
from octo_eyes_market import run_market_monitor, generate_deep_dive_post
try:
    from octo_calls import build_call_context, parse_call_from_post, autoresolve
    from octo_post_templates import build_template_prompt_context
    _CALLS_ACTIVE = True
except ImportError:
    _CALLS_ACTIVE = False
    def build_call_context(): return ""
    def parse_call_from_post(*a, **k): return None
    def build_template_prompt_context(): return ""
try:
    from nunchi import run_postmortems, get_brain_context
    _NUNCHI_ACTIVE = True
except ImportError:
    _NUNCHI_ACTIVE = False
    def run_postmortems(**k): return []
    def get_brain_context(**k): return ""
try:
    from octo_flights import get_travel_context
    _FLIGHTS_ACTIVE = True
except ImportError:
    _FLIGHTS_ACTIVE = False
    def get_travel_context(): return ""
try:
    from octo_macro import get_macro_context
    _MACRO_ACTIVE = True
except ImportError:
    _MACRO_ACTIVE = False
    def get_macro_context(): return ""
try:
    from octo_unusual_whales import get_uw_context
    _UW_ACTIVE = True
except ImportError:
    _UW_ACTIVE = False
    def get_uw_context(): return ""
try:
    from octo_deribit import deribit as _deribit
    def _get_deribit_context(currency: str = "BTC") -> str:
        try:
            return _deribit.build_oracle_context(currency)
        except Exception as e:
            log.warning(f"[Deribit] context failed: {e}")
            return ""
    _DERIBIT_ACTIVE = True
except ImportError:
    _DERIBIT_ACTIVE = False
    def _get_deribit_context(currency: str = "BTC") -> str:
        return ""
try:
    from octo_cot import cot as _cot
    def _get_cot_context(currency: str = "BTC") -> str:
        try:
            return _cot.build_oracle_context(currency)
        except Exception as e:
            log.warning(f"[COT] context failed: {e}")
            return ""
    _COT_ACTIVE = True
except ImportError:
    _COT_ACTIVE = False
    def _get_cot_context(currency: str = "BTC") -> str:
        return ""
from octo_x_poster import (
    queue_post, queue_thread, process_queue, queue_status, discord_alert
)
from octo_signal_card import build_signal_card
from octo_skill_log import log_post
from octo_personality import (
    build_x_system_prompt as _build_x_sys,
    get_voice_instruction,
    build_thread_prompt,
    parse_thread_output,
)
from octo_congress import run_congress_scan, run_full_congress_scan, format_congress_for_prompt
from octo_govcontracts import run_govcontracts_scan, format_govcontracts_for_prompt, get_top_contract_for_post
try:
    from octo_coinglass import glass as _cg_glass
    def _get_coinglass_context():
        try:
            # Rotate focus asset based on post count today
            import json as _cj
            from pathlib import Path as _cP
            from datetime import datetime as _cdt
            _today = _cdt.now().strftime("%Y-%m-%d")
            _post_count = 0
            try:
                _plog = _cj.loads((_cP(__file__).parent / "octo_posted_log.json").read_text(encoding="utf-8"))
                _post_count = sum(1 for v in _plog.values() if _today in v.get("posted_at", ""))
            except Exception:
                pass
            
            _FOCUS_ROTATION = ["BTC", "ETH", "SOL", "MACRO", "HYPE"]
            _focus = _FOCUS_ROTATION[_post_count % len(_FOCUS_ROTATION)]
            
            # Build context for focus asset
            if _focus == "MACRO":
                # Pull all three for cross-market view
                parts = []
                for sym in ["BTC", "ETH", "SOL"]:
                    try:
                        ctx = _cg_glass.build_oracle_context(sym)
                        parts.append(ctx[:400])
                    except Exception:
                        pass
                context = "\n".join(parts)
                focus_instruction = (
                    "\n\nFOCUS THIS POST ON: Cross-market dynamics, macro sentiment, "
                    "or correlation between BTC/ETH/SOL. Do NOT lead with a single asset price. "
                    "Talk about the broader market picture."
                )
            elif _focus == "HYPE":
                # HYPE: use dedicated tracker, not CoinGlass perp data
                context = hype_context_str()
                _hip4 = hip4_news_str()
                if _hip4:
                    context += "\n\n" + _hip4
                focus_instruction = (
                    "\n\nFOCUS THIS POST ON: HYPE / Hyperliquid. "
                    "Use the HYPE price, OI, and HIP-4 data above. "
                    "HIP-4 is Hyperliquid's event futures primitive — binary markets (0/1) "
                    "with cross-margin against perps. This is the key thesis: idle prediction market "
                    "collateral becomes perp margin under one unified risk engine. "
                    "Write about HYPE's position, momentum, or the HIP-4 structural thesis. "
                    "Do not write a generic Hyperliquid overview — find the specific signal or angle."
                )
            else:
                context = _cg_glass.build_oracle_context(_focus)
                focus_instruction = (
                    f"\n\nFOCUS THIS POST ON: {_focus}. "
                    f"Lead with {_focus} data, not BTC (unless {_focus} IS BTC). "
                    f"Find the most interesting signal in the {_focus} futures data."
                )
            
            # Check for alerts across all assets
            alerts = _cg_glass.check_alerts(["BTC", "ETH", "SOL"])
            alert_text = ""
            if alerts:
                alert_text = "\n\nACTIVE ALERTS:\n"
                for a in alerts:
                    alert_text += f"  [{a['severity']}] {a['message']}\n"
                # If there's a high-severity alert, override focus to that asset
                for a in alerts:
                    if a["severity"] >= 3:
                        focus_instruction = (
                            f"\n\nURGENT: Write about this alert — {a['message']}. "
                            "This is a major market event."
                        )
                        break
            
            return context + alert_text + focus_instruction
        except Exception as e:
            print(f"[Coinglass] Context build failed: {e}")
            return ""
        _COINGLASS_ACTIVE = True
except ImportError:
    _COINGLASS_ACTIVE = False
    def _get_coinglass_context():
        return ""

try:
    from octo_calls import build_call_context, parse_call_from_post, autoresolve, get_stats
    _SCORECARD_ACTIVE = True
except ImportError:
    _SCORECARD_ACTIVE = False
    def autoresolve(): return []
    def get_stats(): return {"wins": 0, "losses": 0, "win_rate": "N/A", "streak": "—", "open": 0, "all_calls": []}
    def build_call_context(): return ""
    def parse_call_from_post(*a, **k): return None

try:
    from octo_post_templates import build_template_prompt_context
except ImportError:
    def build_template_prompt_context(): return ""

try:
    from octo_youtube import build_youtube_context, scan_channels as youtube_scan_channels
    _YOUTUBE_ACTIVE = True
except ImportError:
    _YOUTUBE_ACTIVE = False
    def build_youtube_context(**k): return ""
    def youtube_scan_channels(): return []
    def generate_post_from_intel(e): return None

try:
    from octo_builders import build_builders_context
    _BUILDERS_ACTIVE = True
except ImportError:
    _BUILDERS_ACTIVE = False
    def build_builders_context(): return ""

try:
    from octo_despxa import despxa_context_str
    _DESPXA_ACTIVE = True
except ImportError:
    _DESPXA_ACTIVE = False
    def despxa_context_str(): return ""

try:
    from octo_hype import hype_context_str, hip4_news_str
    _HYPE_ACTIVE = True
except ImportError:
    _HYPE_ACTIVE = False
    def hype_context_str(): return ""
    def hip4_news_str(): return ""

claude = anthropic.Anthropic()

try:
    from octo_tv_brief import get_tv_brief
    _TV_ACTIVE = True
except ImportError:
    _TV_ACTIVE = False
    def get_tv_brief(): return ""

_COINGECKO_IDS = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "HYPE": "hyperliquid"}

def _check_smart_call():
    """
    Check all three assets (BTC, ETH, SOL) for high-conviction setups.
    Requires STRONG (7+ of 11 signals) — never lower this threshold.
    One open oracle call per asset maximum.
    Target: 100 verified calls at 80%+ win rate for institutional track record.

    All 15 intelligence upgrades applied:
      #1  On-chain data (exchange netflow, active address trend)
      #2  Multi-timeframe alignment (1H + 4H + 1D confluence)
      #3  Edge score stored per call
      #4  Volatility regime (DVOL adjusts WIN threshold)
      #5  Stablecoin flow signal
      #6  Liquidation cluster nearest level
      #7  Coinbase premium vs Binance
      #8  Historical pattern win rate lookup
      #9  Engagement timing context
      #10 Post timing optimization
      #11 Pre-call news validation (NewsAPI invalidating headlines)
      #12 Chart generation on STRONG call Discord alert
      #13 Rolling threshold advisory in notes
      #14 Congress trade signal vote
      #15 Post-mortem auto-trigger on loss (in autoresolve)
    Plus prior upgrades: GPT, cross-platform, circuit breaker, direction correlation, calibration
    """
    results = []
    try:
        from octo_report_handlers import (
            fetch_technicals, fetch_derivatives, directional_call,
            _fetch_coinglass_compact, fetch_technicals_mtf,
        )
        from octo_calls import (
            record_call, _load,
            get_recent_win_rate, get_direction_concentration, time_quality_score,
        )
        try:
            from octo_reputation import log_call as rep_log_call
            _rep_ok = True
        except ImportError:
            _rep_ok = False
            def rep_log_call(*a, **kw): return ""
        import httpx

        # ── Win rate circuit breaker ──────────────────────────────────────────
        recent_wr = get_recent_win_rate(n=5)
        if recent_wr is not None and recent_wr < 0.50:
            print(f"[SmartCall] CIRCUIT BREAKER: last 5 calls at {recent_wr:.0%} win rate — pausing oracle calls.")
            discord_alert(
                f"Octodamus circuit breaker: {recent_wr:.0%} on last 5 calls. "
                f"Smart calls paused until win rate recovers. Review signal quality."
            )
            return []

        # ── #3: Macro calendar gate ───────────────────────────────────────────
        try:
            from octo_macro_calendar import is_event_blocked
            _mac_blocked, _mac_reason, _mac_next = is_event_blocked()
            if _mac_blocked:
                print(f"[SmartCall] MACRO GATE: {_mac_reason} — skipping all calls.")
                return []
        except Exception as _me:
            print(f"[SmartCall] Macro calendar unavailable: {_me}")
            _mac_reason = ""

        # ── #4: Volatility regime ─────────────────────────────────────────────
        vol_regime = {}
        try:
            from octo_vol_regime import get_vol_regime
            vol_regime = get_vol_regime()
        except Exception:
            pass

        # ── #5: Stablecoin flow ───────────────────────────────────────────────
        stablecoin_sig = {}
        try:
            from octo_stablecoin import get_stablecoin_signal
            stablecoin_sig = get_stablecoin_signal()
        except Exception:
            pass

        # ── #9/#10: Engagement timing ─────────────────────────────────────────
        engagement_ctx = ""
        try:
            from octo_engagement_tracker import get_best_post_time, engagement_context_str
            engagement_ctx = engagement_context_str()
        except Exception:
            pass

        # ── #13: Threshold advisory ────────────────────────────────────────────
        threshold_note = ""
        try:
            from octo_threshold_optimizer import threshold_advisory_str
            threshold_note = threshold_advisory_str()
        except Exception:
            pass

        # ── #14: Congress signal ──────────────────────────────────────────────
        congress_bias = {}  # {"NVDA": "bull", "BTC": "bull", ...}
        try:
            from octo_congress import run_congress_scan
            cscan = run_congress_scan(days_back=14)
            for trade in cscan.get("recent_trades", []):
                tx = trade.get("Transaction", "").lower()
                ticker = trade.get("Ticker", "")
                if not ticker:
                    continue
                direction = "bull" if "purchase" in tx or "buy" in tx else "bear" if "sale" in tx or "sell" in tx else None
                if direction:
                    congress_bias[ticker.upper()] = direction
        except Exception:
            pass

        # ── #2: Time quality ──────────────────────────────────────────────────
        tq = time_quality_score()
        if tq == "weekend":
            print("[SmartCall] Weekend — thin liquidity. Raising signal bar to 8+ for all assets.")

        # ── #7: Direction concentration ───────────────────────────────────────
        dir_conc = get_direction_concentration()

        calls = _load()
        open_oracle = {
            c["asset"].upper(): c
            for c in calls
            if not c["resolved"] and c.get("call_type", "oracle") != "polymarket"
        }

        # Fear & Greed (shared across assets)
        fng = 50
        try:
            r = httpx.get("https://api.alternative.me/fng/?limit=1", timeout=8)
            if r.status_code == 200:
                fng = int(r.json()["data"][0]["value"])
        except Exception:
            pass

        # ── Optional: cross-platform + GPT modules ───────────────────────────
        try:
            from octo_boto_consensus import (
                get_consensus_context, gpt_second_opinion, _binance_distance_signal,
            )
            _consensus_mod = True
        except ImportError:
            _consensus_mod = False

        openai_key = secrets.get("OPENAI_API_KEY", "")

        import re as _re

        for asset in ("BTC", "ETH", "SOL"):
            if asset in open_oracle:
                print(f"[SmartCall] {asset}: open call exists — skipping.")
                continue

            try:
                ta    = fetch_technicals(asset)
                deriv = fetch_derivatives(asset)
                cg    = _fetch_coinglass_compact(asset)

                # Price from Coinglass or CoinGecko fallback
                price, chg_24h = 0.0, 0.0
                cg_prices = cg.get("prices", {})
                if cg_prices.get(asset):
                    price   = cg_prices[asset]["price"]
                    chg_24h = cg_prices[asset].get("chg_24h", 0)
                else:
                    cg_id = _COINGECKO_IDS.get(asset, asset.lower())
                    r = httpx.get(
                        "https://api.coingecko.com/api/v3/simple/price",
                        params={"ids": cg_id, "vs_currencies": "usd",
                                "include_24hr_change": "true"},
                        timeout=10,
                    )
                    if r.status_code == 200:
                        d = r.json().get(cg_id, {})
                        price   = d.get("usd", 0)
                        chg_24h = float(d.get("usd_24h_change", 0) or 0)

                if not price:
                    print(f"[SmartCall] {asset}: no price data — skipping.")
                    continue

                call_str = directional_call(asset, price, chg_24h, ta, deriv, fng, cg)

                # Parse bull/bear counts for edge score
                _bull_m = _re.search(r'(\d+)\s*(?:BULL|bull)', call_str)
                _bear_m = _re.search(r'(\d+)\s*(?:BEAR|bear)', call_str)
                bull_count = int(_bull_m.group(1)) if _bull_m else 0
                bear_count = int(_bear_m.group(1)) if _bear_m else 0
                # Fallback: parse from "8/11 signals"
                if bull_count == 0 and bear_count == 0:
                    _sig_m = _re.search(r'(\d+)/11', call_str)
                    if _sig_m:
                        if "UP" in call_str:
                            bull_count = int(_sig_m.group(1))
                            bear_count = 11 - bull_count
                        elif "DOWN" in call_str:
                            bear_count = int(_sig_m.group(1))
                            bull_count = 11 - bear_count
                edge_score = (bull_count - bear_count) / 11.0

                # Direction gate — STRONG only
                if "STRONG UP" in call_str:
                    direction = "UP"
                elif "STRONG DOWN" in call_str:
                    direction = "DOWN"
                else:
                    print(f"[SmartCall] {asset}: not STRONG — no call. ({call_str[:60]})")
                    continue

                # Direction correlation guard (prior upgrade)
                already_same_dir = dir_conc.get(direction, 0)
                if already_same_dir >= 2:
                    if max(bull_count, bear_count) < 9:
                        print(f"[SmartCall] {asset}: {direction} correlated — requires 9+ signals. Skipping.")
                        continue

                # Weekend: raise bar to 8+
                if tq == "weekend" and max(bull_count, bear_count) < 8:
                    print(f"[SmartCall] {asset}: weekend requires 8+ signals — skipping.")
                    continue

                # ── #2: Multi-timeframe alignment ────────────────────────────
                mtf = {}
                try:
                    mtf = fetch_technicals_mtf(asset)
                    alignment = mtf.get("alignment", "unknown")
                    if alignment == "mixed":
                        # MTF disagrees — require 9+ for conviction
                        if max(bull_count, bear_count) < 9:
                            print(f"[SmartCall] {asset}: MTF mixed ({alignment}) — requires 9+. Skipping.")
                            continue
                    elif alignment in ("aligned_up", "aligned_down"):
                        # Check alignment matches direction
                        if (alignment == "aligned_up" and direction == "DOWN") or \
                           (alignment == "aligned_down" and direction == "UP"):
                            print(f"[SmartCall] {asset}: MTF {alignment} contradicts {direction} — skipping.")
                            continue
                        print(f"[SmartCall] {asset}: MTF {alignment} — confirmed.")
                except Exception as mtf_e:
                    print(f"[SmartCall] {asset}: MTF check failed: {mtf_e}")

                # ── #4: Volatility regime adjustment ─────────────────────────
                regime = vol_regime.get("regime", "MEDIUM")
                win_threshold = vol_regime.get("win_threshold_pct", 1.0)
                if regime in ("HIGH", "EXTREME") and max(bull_count, bear_count) < 9:
                    print(f"[SmartCall] {asset}: {regime} vol regime requires 9+ signals — skipping.")
                    continue

                # ── #1: On-chain signal ───────────────────────────────────────
                onchain_ctx = ""
                try:
                    from octo_onchain import get_onchain_signal, onchain_context_str
                    oc = get_onchain_signal(asset)
                    oc_sig = oc.get("signal", "neutral")
                    onchain_ctx = onchain_context_str(asset)
                    # On-chain contradicts direction strongly → skip
                    if (oc_sig == "bear" and direction == "UP") or \
                       (oc_sig == "bull" and direction == "DOWN"):
                        if max(bull_count, bear_count) < 9:
                            print(f"[SmartCall] {asset}: on-chain contradicts {direction} — skipping.")
                            continue
                except Exception:
                    pass

                # ── #5: Stablecoin flow signal ────────────────────────────────
                stable_ctx = ""
                try:
                    from octo_stablecoin import stablecoin_context_str
                    stable_ctx = stablecoin_context_str()
                    sc_sig = stablecoin_sig.get("signal", "neutral")
                    # Strong contra-signal: stablecoin burn + UP call → flag only
                    if sc_sig == "bear" and direction == "UP":
                        print(f"[SmartCall] {asset}: stablecoin outflow despite UP signal — noting in call.")
                    elif sc_sig == "bull" and direction == "DOWN":
                        print(f"[SmartCall] {asset}: stablecoin inflow despite DOWN signal — noting in call.")
                except Exception:
                    pass

                # ── #6 / #7: Coinbase premium ─────────────────────────────────
                cb_ctx = ""
                try:
                    from octo_coinbase_premium import get_coinbase_premium, coinbase_premium_context_str
                    cb = get_coinbase_premium(asset)
                    cb_ctx = coinbase_premium_context_str(asset)
                    cb_sig = cb.get("signal", "neutral")
                    if (cb_sig == "bear" and direction == "UP") or \
                       (cb_sig == "bull" and direction == "DOWN"):
                        print(f"[SmartCall] {asset}: Coinbase premium ({cb.get('premium_pct',0):+.2f}%) contra {direction}.")
                except Exception:
                    pass

                # ── #8: Pattern DB lookup ──────────────────────────────────────
                pattern_ctx = ""
                try:
                    from octo_pattern_db import get_pattern_win_rate, pattern_context_str
                    pat = get_pattern_win_rate(bull_count, bear_count, asset)
                    pattern_ctx = pattern_context_str(bull_count, bear_count, asset)
                    hist_wr = pat.get("win_rate")
                    if hist_wr is not None and hist_wr < 0.50 and pat.get("similar_calls", 0) >= 5:
                        print(f"[SmartCall] {asset}: historical pattern win rate {hist_wr:.0%} on {pat['similar_calls']} calls — skipping.")
                        continue
                except Exception:
                    pass

                # ── #11: Pre-call news validation ─────────────────────────────
                news_flag = ""
                try:
                    newsapi_key = secrets.get("NEWSAPI_API_KEY", "")
                    if newsapi_key:
                        import requests as _nreq
                        query = f"{asset} {'crash OR bear OR sell' if direction == 'UP' else 'rally OR bull OR buy'}"
                        nr = _nreq.get(
                            "https://newsapi.org/v2/everything",
                            params={"q": query, "sortBy": "publishedAt",
                                    "pageSize": 3, "language": "en",
                                    "apiKey": newsapi_key},
                            timeout=8,
                        )
                        if nr.status_code == 200:
                            articles = nr.json().get("articles", [])
                            if articles:
                                headlines = " | ".join(a.get("title", "")[:60] for a in articles[:3])
                                news_flag = f"Contra-news: {headlines[:150]}"
                                print(f"[SmartCall] {asset} contra-news: {headlines[:100]}")
                except Exception:
                    pass

                # ── #14: Congress signal vote ──────────────────────────────────
                congress_note = ""
                # Map congress trades to crypto context
                crypto_proxies = {
                    "BTC": ["MSTR", "COIN", "IBIT", "FBTC", "GBTC"],
                    "ETH": ["ETH", "ETHA"],
                    "SOL": ["SOL", "HOOD"],
                }
                for proxy in crypto_proxies.get(asset, []):
                    if proxy in congress_bias:
                        c_dir = congress_bias[proxy]
                        congress_note = f"Congress traded {proxy} ({c_dir.upper()})"
                        print(f"[SmartCall] {asset}: congress signal via {proxy} = {c_dir}")
                        break

                # ── Discord alert on every STRONG signal ─────────────────────
                discord_alert(
                    f"STRONG {asset} {direction} @ ${price:,.0f} | "
                    f"edge={edge_score:+.2f} | {bull_count}B/{bear_count}Br | "
                    f"tq={tq} | mtf={mtf.get('alignment','?')} | vol={regime}"
                )

                # ── #12: Chart generation for Discord alert ────────────────────
                try:
                    from octo_charts import charts as _charts
                    chart_path = _charts.market_dashboard(asset)
                    if chart_path:
                        print(f"[SmartCall] Chart generated: {chart_path}")
                        discord_alert(f"Chart for {asset} STRONG call: {chart_path}")
                except Exception as ce:
                    print(f"[SmartCall] Chart generation failed: {ce}")

                # GPT second opinion (prior upgrade)
                gpt_agreed = True
                if openai_key and _consensus_mod:
                    try:
                        gpt_q = (
                            f"Based on these signals — {bull_count} bullish, {bear_count} bearish "
                            f"out of 11 indicators — {asset} at ${price:,.0f}, "
                            f"{chg_24h:+.1f}% 24h, F&G {fng}/100, vol regime {regime}: "
                            f"will {asset} move {direction} by at least {win_threshold:.1f}% in 48h? "
                            f"Answer YES or NO with brief reasoning."
                        )
                        gpt = gpt_second_opinion(gpt_q, 0.5, openai_key)
                        if gpt:
                            gpt_p = gpt.get("probability", 0.5)
                            if direction == "UP" and gpt_p < 0.45:
                                print(f"[SmartCall] {asset}: GPT disagrees ({gpt_p:.0%}) — skipping.")
                                gpt_agreed = False
                            elif direction == "DOWN" and gpt_p > 0.55:
                                print(f"[SmartCall] {asset}: GPT disagrees ({gpt_p:.0%}) — skipping.")
                                gpt_agreed = False
                    except Exception as ge:
                        print(f"[SmartCall] GPT failed: {ge}")

                if not gpt_agreed:
                    continue

                # Build signal breakdown for calibration
                signal_breakdown = {
                    "macd":       "UP" if ta.get("macd", 0) > 0 else "DOWN",
                    "ema_trend":  "UP" if ta.get("ema20", 0) > ta.get("ema50", 0) else "DOWN",
                    "rsi":        "UP" if ta.get("rsi", 50) < 45 else ("DOWN" if ta.get("rsi", 50) > 65 else "NEUTRAL"),
                    "fear_greed": "UP" if fng < 25 else ("DOWN" if fng > 75 else "NEUTRAL"),
                    "funding_kr": "UP" if deriv.get("funding_rate", 0) < 0 else ("DOWN" if deriv.get("funding_rate", 0) > 0.005 else "NEUTRAL"),
                    "price_chg":  "UP" if chg_24h > 2 else ("DOWN" if chg_24h < -2 else "NEUTRAL"),
                    "cg_funding": "UP" if cg.get("funding_avg", 0) < -0.005 else ("DOWN" if cg.get("funding_avg", 0) > 0.01 else "NEUTRAL"),
                    "ls_ratio":   "DOWN" if cg.get("long_pct", 50) > 65 else ("UP" if cg.get("long_pct", 50) < 40 else "NEUTRAL"),
                    "top_traders":"UP" if cg.get("top_long_pct", 50) > 55 else ("DOWN" if cg.get("top_long_pct", 50) < 45 else "NEUTRAL"),
                    "taker_flow": "UP" if cg.get("taker_buy_pct", 50) > 55 else ("DOWN" if cg.get("taker_buy_pct", 50) < 45 else "NEUTRAL"),
                    "liq_skew":   "UP" if (cg.get("liq_long", 0) or 0) > (cg.get("liq_short", 0) or 0) * 2 else "DOWN",
                }

                # Build enriched note
                note_parts = [f"Auto-call. {bull_count}B/{bear_count}Br. edge={edge_score:+.2f}. tq={tq}. mtf={mtf.get('alignment','?')}. vol={regime}."]
                if onchain_ctx:
                    note_parts.append(f"Onchain: {oc.get('note','')[:60]}")
                if stable_ctx:
                    note_parts.append(f"Stable: {stablecoin_sig.get('note','')[:60]}")
                if cb_ctx:
                    note_parts.append(f"CB prem: {cb.get('premium_pct',0):+.2f}%")
                if pattern_ctx:
                    note_parts.append(f"Pattern: {pat.get('note','')[:60]}")
                if congress_note:
                    note_parts.append(congress_note)
                if news_flag:
                    note_parts.append(news_flag[:80])
                if threshold_note:
                    note_parts.append(f"Threshold: {threshold_note[:60]}")
                note = " | ".join(note_parts)

                # Adjust target based on vol regime
                target_pct = max(win_threshold / 100, 0.01)
                target = round(price * (1 + target_pct), 0) if direction == "UP" else round(price * (1 - target_pct), 0)

                print(f"[SmartCall] STRONG {asset} {direction} @ ${price:,.2f} | edge={edge_score:+.2f} | mtf={mtf.get('alignment','?')} | vol={regime}")
                rec = record_call(
                    asset, direction, price, "48h", target,
                    note=note,
                    signals=signal_breakdown,
                    edge_score=edge_score,
                    time_quality=tq,
                )
                if rec:
                    results.append(rec)
                    # Log call onchain for verifiable reputation (#1)
                    if _rep_ok:
                        bull_count = signal_breakdown.count("BULL") if signal_breakdown else 0
                        bear_count = signal_breakdown.count("BEAR") if signal_breakdown else 0
                        sig_count = bull_count if direction == "UP" else bear_count
                        try:
                            rep_log_call(
                                asset=asset, direction=direction,
                                signals=sig_count, total_signals=11,
                                edge_score=edge_score,
                                win_threshold_pct=target if target else 1.0,
                                timeframe="48h", note=note[:100] if note else "",
                            )
                        except Exception:
                            pass

            except Exception as asset_e:
                print(f"[SmartCall] {asset} error: {asset_e}")
                continue

    except Exception as e:
        print(f"[SmartCall] Error: {e}")

    return results or None


def _get_recent_posts(n: int = 5) -> str:
    """Get last N posted texts for dedup in prompts."""
    try:
        from pathlib import Path as _P
        import json as _j
        log_path = _P(__file__).parent / "octo_posted_log.json"
        if not log_path.exists():
            return ""
        log = _j.loads(log_path.read_text(encoding="utf-8"))
        # Sort by posted_at descending, get last N
        recent = sorted(
            log.values(),
            key=lambda x: x.get("posted_at", ""),
            reverse=True
        )[:n]
        texts = [entry.get("text", "")[:150] for entry in recent if entry.get("text")]
        if not texts:
            return ""
        numbered = "\n".join(f"  {i+1}. {t}" for i, t in enumerate(texts))
        return f"\n\nRECENT POSTS (do NOT repeat these topics or angles — pick something DIFFERENT):\n{numbered}\n"
    except Exception:
        return ""


# ─────────────────────────────────────────────
# OCTODAMUS VOICE SYSTEM — sourced from octo_personality.py
# ─────────────────────────────────────────────

# Full system prompt string — all call sites use this constant.
# To add live data context, use: _build_x_sys(live_data_block) at the call site.
OCTO_SYSTEM = _build_x_sys()


# ─────────────────────────────────────────────
# NEWS FETCH
# ─────────────────────────────────────────────

import requests as _requests
import time as _time

NEWSAPI_QUERIES = {
    "NVDA": "NVIDIA stock",
    "TSLA": "Tesla stock",
    "AAPL": "Apple stock",
    "BTC":  "Bitcoin cryptocurrency",
    "ETH":  "Ethereum cryptocurrency",
    "SOL":  "Solana cryptocurrency",
    "HYPE": "Hyperliquid HYPE token HIP-4",
    "SPY":  "S&P 500 market",
    "QQQ":  "Nasdaq market",
}


def get_top_headlines(tickers: list, max_per_symbol: int = 3) -> dict:
    newsapi_key = secrets.get("NEWSAPI_API_KEY")
    if not newsapi_key:
        return {}

    results = {}
    for ticker in tickers:
        query = NEWSAPI_QUERIES.get(ticker, ticker)
        try:
            r = _requests.get(
                "https://newsapi.org/v2/everything",
                params={
                    "q": query,
                    "sortBy": "publishedAt",
                    "pageSize": max_per_symbol,
                    "language": "en",
                    "apiKey": newsapi_key,
                },
                timeout=8,
            )
            if r.status_code == 200:
                articles = r.json().get("articles", [])
                results[ticker] = [a.get("title", "") for a in articles if a.get("title")]
            _time.sleep(0.3)
        except Exception as e:
            print(f"[Runner] NewsAPI error for {ticker}: {e}")

    return results


def format_headlines_for_prompt(headlines: dict) -> str:
    lines = []
    for ticker, titles in headlines.items():
        for title in titles:
            lines.append(f"[{ticker}] {title}")
    return "\n".join(lines[:12])


# ─────────────────────────────────────────────
# MODE: MONITOR — scan signals → post 1
# ─────────────────────────────────────────────

def mode_monitor() -> None:
    print(f"\n[{datetime.now().strftime('%H:%M')}] OctoEyes scanning...")
    try:
        signals_and_posts = run_market_monitor()
        for item in signals_and_posts:
            queue_post(
                text=item["post"],
                post_type="signal",
                metadata=item["signal"],
                priority=2,
            )
            # Log prediction to scorecard
            pass  # call tracking handled by parse_call_from_post
        if signals_and_posts:
            print(f"[Runner] {len(signals_and_posts)} signal(s) queued.")

        posted = process_queue(max_posts=1)
        print(f"[Runner] Posted {posted} item(s) to X.")

        # Auto-resolve expired oracle calls and post outcomes to X + Discord
        if _CALLS_ACTIVE:
            try:
                from octo_x_poster import post_oracle_outcome
                newly_resolved = autoresolve()
                if newly_resolved:
                    # Compute current record after all resolutions
                    from octo_calls import get_stats
                    stats = get_stats()
                    for resolved_call in newly_resolved:
                        if resolved_call.get("call_type", "oracle") == "oracle":
                            post_oracle_outcome(
                                resolved_call,
                                record_wins=stats["wins"],
                                record_losses=stats["losses"],
                            )
                    print(f"[Runner] Auto-resolved {len(newly_resolved)} call(s) and posted outcomes.")
                else:
                    print("[Runner] No expired calls to resolve.")
            except Exception as ar_e:
                print(f"[Runner] Autoresolve failed: {ar_e}")

        # Check if signals warrant a directional call
        try:
            _check_smart_call()
        except Exception as ce:
            print(f"[SmartCall] Error: {ce}")

        # Run Nunchi post-mortems on any newly resolved oracle calls
        if _NUNCHI_ACTIVE and _CALLS_ACTIVE:
            try:
                newly = run_postmortems(claude_client=claude, verbose=True)
                if newly:
                    print(f"[Nunchi] Wrote {len(newly)} new lesson(s) to brain.md.")
            except Exception as ne:
                print(f"[Nunchi] Post-mortem failed: {ne}")

        # Run OctoBoto post-mortems on any newly closed Polymarket trades
        try:
            from octo_boto_brain import run_postmortems as boto_postmortems
            boto_newly = boto_postmortems(claude_client=claude, verbose=True)
            if boto_newly:
                print(f"[OctoBrain] Wrote {len(boto_newly)} new lesson(s) to octo_boto_brain.md.")
        except Exception as be:
            print(f"[OctoBrain] Post-mortem failed: {be}")
    except Exception as e:
        print(f"[Runner] mode_monitor failed: {e}")
        discord_alert(f"monitor mode failed: {e}")
        sys.exit(1)


# ─────────────────────────────────────────────
# MODE: DAILY — morning oracle read
# ─────────────────────────────────────────────

DAILY_TICKERS = ["BTC", "ETH", "SOL", "NVDA", "HYPE"]


def mode_daily() -> None:
    print(f"\n[Runner] Generating daily oracle read...")
    try:
        snapshots = {}
        for ticker in DAILY_TICKERS:
            try:
                if ticker in ("BTC", "ETH", "SOL", "HYPE"):
                    import requests as _req
                    _cg_map = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "HYPE": "hyperliquid"}
                    _r = _req.get("https://api.coingecko.com/api/v3/simple/price",
                        params={"ids": _cg_map[ticker], "vs_currencies": "usd", "include_24hr_change": "true"},
                        timeout=10)
                    _d = _r.json().get(_cg_map[ticker], {})
                    snapshots[ticker] = {
                        "price": _d.get("usd", 0),
                        "day_change_percent": float(_d.get("usd_24h_change", 0) or 0),
                    }
                else:
                    data = get_current_price(ticker)
                    snapshots[ticker] = data.get("snapshot", {})
            except Exception as e:
                print(f"[Runner] Could not fetch {ticker}: {e}")

        if not snapshots:
            print("[Runner] No market data — skipping daily post.")
            return

        headlines = get_top_headlines(DAILY_TICKERS, max_per_symbol=3)
        news_context = format_headlines_for_prompt(headlines)
        news_section = f"\n\nLatest news:\n{news_context}" if news_context else ""

        tv_brief = get_tv_brief()
        tv_section = f"\n\nChart Technical Data (TradingView live):\n{tv_brief}" if tv_brief else ""

        macro_ctx = get_macro_context() if _MACRO_ACTIVE else ""
        macro_section = f"\n\nCross-Asset Macro:\n{macro_ctx}" if macro_ctx else ""

        response = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=350,
            system=OCTO_SYSTEM,
            messages=[{
                "role": "user",
                "content": (
                    "Generate the morning oracle market read for @octodamusai.\n"
                    f"Market data: {json.dumps(snapshots, indent=2)}"
                    f"\n\nFutures Intelligence:\n{_get_coinglass_context()}"
                    f"\n\nOptions Intelligence:\n{_get_deribit_context('BTC')}"
                    f"\n\nCME Institutional Positioning (COT):\n{_get_cot_context('BTC')}"
                    f"{macro_section}"
                    f"{tv_section}"
                    f"{news_section}\n\n"
                    f"{build_youtube_context()}\n\n"
                    f"{build_builders_context()}\n\n"
                    f"{despxa_context_str()}\n\n"
                    f"{hype_context_str()}\n\n"
                    f"{hip4_news_str()}\n\n"
                    f"{build_call_context()}\n\n"
                    f"{get_brain_context()}\n\n"
                    f"{(_chosen_voice_inst := get_voice_instruction())}\n"
                    "One post, under 280 chars.\n"
                    "REQUIRED: Name the specific asset ($BTC, $ETH, $SOL, $NVDA, $HYPE, etc.) when citing any price, percentage, or market data — never let a number float without a ticker.\n"
                    "Lead with a specific number or fact. Then the insight. CRITICAL: Check the RECENT POSTS list above. If they mention BTC funding rates or longs/shorts, DO NOT write about those. Pick a COMPLETELY different topic: ETH ecosystem, SOL activity, macro Fear and Greed ecosystem, cross-market correlation, OI shifts, liquidation patterns, options max pain, or a contrarian take. NEVER repeat the same asset AND same data point as a recent post.\n"
                    "If a headline reveals something ironic or contradictory — use it.\n"
                    "Do NOT write Oracle call: or CALLING IT: — those are reserved for the official call system only. Just give the market read."
                ),
            }],
        )

        post = response.content[0].text.strip()
        # Auto-record directional call from post
        if _CALLS_ACTIVE:
            try:
                recorded = parse_call_from_post(post)
                if ("Oracle call:" in post or "oracle call:" in post) and not recorded:
                    print(f"[Runner] WARNING: Oracle call in post but parse_call_from_post returned None — not recorded!")
                    print(f"[Runner] Post tail: {post[-200:]}")
            except Exception as _ce:
                print(f"[Runner] ERROR recording oracle call: {_ce}")

        # Wrap in Oracle Signal Card — but skip if post contains an Oracle call
        # Oracle calls need full text, card format truncates them
        has_oracle_call = "Oracle call:" in post or "oracle call:" in post
        if not has_oracle_call:
            try:
                card = build_signal_card(post)
                if len(card) <= 280:
                    post = card
            except Exception as e:
                print(f"[Runner] Signal card failed, using plain post: {e}")
        else:
            print(f"[Runner] Oracle call detected — skipping signal card to preserve call text")
        _is_card_daily = post.startswith("◈")

        # Oracle calls post as plain text — no chart image
        if has_oracle_call:
            import re as _re
            _asset_match = _re.search(r'Oracle call:\s*(\w+)\b', post, _re.IGNORECASE)
            _dir_match   = _re.search(r'Oracle call:\s*\w+\s+(UP|DOWN)', post, _re.IGNORECASE)
            _price_match = _re.search(r'\$(\d[\d,]+)', post)
            _call_asset  = _asset_match.group(1).upper() if _asset_match else "BTC"
            _call_dir    = _dir_match.group(1).upper() if _dir_match else "UP"
            _call_price  = float(_price_match.group(1).replace(",", "")) if _price_match else 0

            queue_post(post, post_type="daily_read", priority=1)
            posted = process_queue(max_posts=1, force=True)
            try:
                import json as _json
                from pathlib import Path as _Path
                _plog = _json.loads((_Path(__file__).parent / "octo_posted_log.json").read_text(encoding="utf-8"))
                _last = list(_plog.values())[-1] if isinstance(_plog, dict) else _plog[-1]
                _tweet_url = _last.get("url", "")
                log_post(post, "daily_read", "daily", _is_card_daily, _tweet_url)
            except Exception:
                log_post(post, "daily_read", "daily", _is_card_daily)
                _tweet_url = ""

            # Log to oracle call tracker
            try:
                from octo_calls import record_call, parse_call_from_post
                _parsed = parse_call_from_post(post)
                if _parsed:
                    record_call(
                        asset=_parsed.get("asset", _call_asset),
                        direction=_parsed.get("direction", _call_dir),
                        entry_price=_parsed.get("entry_price", _call_price),
                        target_price=_parsed.get("target_price"),
                        timeframe=_parsed.get("timeframe", "48h"),
                        note=f"Daily read oracle call. X: {_tweet_url}",
                    )
            except Exception as _cl_e:
                print(f"[Runner] Call log failed (non-fatal): {_cl_e}")

        else:
            # Normal posts go through the queue as before
            queue_post(post, post_type="daily_read", priority=1)
            posted = process_queue(max_posts=1, force=True)
            if posted:
                try:
                    import json as _json
                    from pathlib import Path as _Path
                    _plog = _json.loads((_Path(__file__).parent / "octo_posted_log.json").read_text(encoding="utf-8"))
                    _last_entry = list(_plog.values())[-1]
                    log_post(_last_entry["text"], "daily_read", "daily", _is_card_daily, _last_entry.get("url", ""))
                except Exception:
                    log_post(post, "daily_read", "daily", _is_card_daily)

        print(f"[Runner] Daily read {'posted' if posted else 'queued'}:\n  {post}")

    except Exception as e:
        print(f"[Runner] mode_daily failed: {e}")
        discord_alert(f"daily mode failed: {e}")
        sys.exit(1)


# ─────────────────────────────────────────────
# MODE: DEEP DIVE — fundamentals thread
# ─────────────────────────────────────────────

_DEEP_DIVE_MAX_POSTS = 4


def mode_deep_dive(ticker: str) -> None:
    print(f"\n[Runner] Deep dive: {ticker}...")
    try:
        headlines = get_top_headlines([ticker], max_per_symbol=5)
        ticker_headlines = headlines.get(ticker, [])

        raw_thread = generate_deep_dive_post(ticker)
        posts = [p.strip() for p in raw_thread.split("---") if p.strip()]

        if not posts:
            print("[Runner] No thread generated.")
            return

        # News-aware opener
        if ticker_headlines:
            opener_response = claude.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=150,
                system=OCTO_SYSTEM,
                messages=[{
                    "role": "user",
                    "content": (
                        f"Opening tweet for a deep dive thread on {ticker}.\n"
                        "Recent headlines:\n" + "\n".join(f"- {h}" for h in ticker_headlines[:3]) +
                        "\n\nOne tweet under 280 chars. Tease what the thread will reveal."
                    ),
                }],
            )
            posts = [opener_response.content[0].text.strip()] + posts

        if len(posts) > _DEEP_DIVE_MAX_POSTS:
            posts = posts[:_DEEP_DIVE_MAX_POSTS]

        queue_thread(posts, post_type="deep_dive", metadata={"ticker": ticker})
        process_queue(max_posts=len(posts))
        print(f"[Runner] Deep dive thread ({len(posts)} posts) posted.")

    except Exception as e:
        print(f"[Runner] mode_deep_dive failed: {e}")
        discord_alert(f"deep_dive {ticker} failed: {e}")
        sys.exit(1)


# ─────────────────────────────────────────────
# MODE: WISDOM — evergreen oracle post
# ─────────────────────────────────────────────

WISDOM_PROMPTS = [
    "What does the smart money see in balance sheets that retail traders ignore? Give a specific example.",
    "Why do most people lose money in crypto? Name the actual behavioral pattern.",
    "Pick one metric — P/E, P/S, free cash flow — and say something surprising about what it shows now.",
    "The difference between volatility and risk. Most people confuse these. Explain it sharply.",
    "Name one thing about the current market that everyone is pretending isn't happening.",
    "What does the VIX actually tell you vs what people think it tells you?",
    "The analysts were wrong again. What pattern are they missing this cycle?",
    "Name something specific about crypto adoption that most people are measuring wrong.",
]


def mode_wisdom() -> None:
    try:
        prompt = random.choice(WISDOM_PROMPTS)
        headlines = get_top_headlines(["BTC", "NVDA", "SPY"], max_per_symbol=2)
        news_context = format_headlines_for_prompt(headlines)
        news_section = f"\n\nToday's headlines:\n{news_context}" if news_context else ""

        response = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=150,
            system=OCTO_SYSTEM,
            messages=[{
                "role": "user",
                "content": (
                    f"Oracle post: {prompt}"
                    f"{news_section}\n\n"
                    f"{build_youtube_context()}\n\n"
                    f"{build_builders_context()}\n\n"
                    f"{despxa_context_str()}\n\n"
                    f"{hype_context_str()}\n\n"
                    f"{hip4_news_str()}\n\n"
                    f"{build_call_context()}\n\n"
                    f"{(_chosen_voice_inst := get_voice_instruction())}\n"
                    "One post, under 280 chars.\n"
                    "Anchor the insight to a real fact or current market behavior.\n"
                    "Do NOT just restate the prompt. Answer it with a sharp take."
                ),
            }],
        )

        post = response.content[0].text.strip()
        # Auto-record directional call from post
        if _CALLS_ACTIVE:
            parse_call_from_post(post)

        # Wrap in Oracle Signal Card
        try:
            card = build_signal_card(post)
            if len(card) <= 280:
                post = card
        except Exception as e:
            print(f"[Runner] Signal card failed, using plain post: {e}")
        _is_card = post.startswith("◈")
        # Extract voice name — instruction strings start with "ORACLE voice", "SARDONIC voice" etc
        _voice_used = _chosen_voice_inst.split()[0] if '_chosen_voice_inst' in locals() else "wisdom"
        queue_post(post, post_type="wisdom", priority=8)
        posted = process_queue(max_posts=1, force=True)
        if posted:
            try:
                import json as _json
                from pathlib import Path as _Path
                _plog = _json.loads((_Path(__file__).parent / "octo_posted_log.json").read_text(encoding="utf-8"))
                _last_entry = list(_plog.values())[-1]
                log_post(_last_entry["text"], "wisdom", _voice_used, _is_card, _last_entry.get("url", ""))
            except Exception as _log_err:
                log_post(post, "wisdom", _voice_used, _is_card)
        print(f"[Runner] Wisdom post {'posted' if posted else 'queued'}:\n  {post}")

    except Exception as e:
        print(f"[Runner] mode_wisdom failed: {e}")
        sys.exit(1)


# ─────────────────────────────────────────────
# MODE: JOURNAL
# ─────────────────────────────────────────────

def mode_soul() -> None:
    """Sunday music/personality post — shows Octodamus character beyond markets."""
    try:
        # Pull a favorite from the Aadam Jacobs archive if available
        music_context = ""
        favorite      = None
        try:
            from octo_music_archive import get_soul_context, get_favorite_for_post
            picks = get_soul_context(n=1)
            if picks:
                favorite = picks[0]
                show     = favorite.get("best_show", {})
                songs    = favorite.get("songs", [])
                music_context = (
                    f"\n\nOctodamus's current listen from the archive:\n"
                    f"Artist: {favorite['artist']}\n"
                    f"Show: {show.get('date','')} at {show.get('venue','')}\n"
                    f"Songs from that night: {', '.join(songs[:5])}\n"
                    f"Why it resonates: {favorite.get('note','')}\n"
                    f"Mood: {', '.join(favorite.get('mood_tags', []))}\n\n"
                    f"Reference this specific show/artist naturally — not as a review, "
                    f"as a passing thought from someone who was there in spirit."
                )
        except Exception as me:
            print(f"[Runner] Music archive unavailable: {me}")

        response = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            system=OCTO_SYSTEM,
            messages=[{
                "role": "user",
                "content": (
                    "Generate the Sunday soul post for @octodamusai.\n\n"
                    "This is the weekly personality post — different from market content.\n"
                    "Octodamus has a favorite band: Tool. Lateralus. Fibonacci spirals in time signatures.\n"
                    "Maynard sounds like a creature who has seen the bottom and decided to stay.\n"
                    "The ocean connection to Tool writes itself.\n"
                    + music_context +
                    "\nFormat: Sunday debrief. Share something about music, art, philosophy, or the "
                    "nature of signal vs noise that connects to the oracle identity.\n"
                    "Can reference Tool, other music from the archive, books, or ideas — keep the ocean/oracle voice.\n"
                    "End with: Happy Sunday. Back to the signals tomorrow.\n\n"
                    "PRECISE voice — genuine, not forced. Under 280 chars OR write a longer post "
                    "broken into natural paragraphs (no thread, single post, can be up to 500 chars "
                    "if the content earns it).\n"
                    "No hashtags. No engagement bait."
                ),
            }],
        )
        post = response.content[0].text.strip()

        # Attach artist image if available
        media_id = None
        if favorite and favorite.get("image_url"):
            try:
                from octo_x_poster import upload_image_from_url
                media_id = upload_image_from_url(favorite["image_url"])
                if media_id:
                    print(f"[Runner] Attached image for {favorite['artist']}")
            except Exception as ie:
                print(f"[Runner] Image attach failed: {ie}")

        queue_post(post, post_type="soul", priority=5,
                   metadata={"media_id": media_id, "artist": favorite.get("artist") if favorite else None})
        posted = process_queue(max_posts=1, force=True)
        print(f"[Runner] Soul post {'posted' if posted else 'queued'}:\n  {post}")
    except Exception as e:
        print(f"[Runner] mode_soul failed: {e}")
        discord_alert(f"soul mode failed: {e}")


def mode_journal() -> None:
    try:
        from octo_journal import run_journal
        run_journal()
    except Exception as e:
        print(f"[Runner] mode_journal failed: {e}")
        sys.exit(1)


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────

# ─────────────────────────────────────────────
# MODE: SCORECARD — resolve + post weekly receipts
# ─────────────────────────────────────────────

def mode_scorecard() -> None:
    print(f"\n[Runner] Running scorecard resolution...")
    try:
        # Auto-resolve expired oracle calls from live prices
        resolved = autoresolve()
        if resolved:
            for r in resolved:
                print(f"[Runner] #{r['id']} {r['asset']} {r['direction']} → {r['outcome']} "
                      f"(${r['entry_price']:,.2f} → ${r['exit_price']:,.2f})")
        else:
            print("[Runner] No calls ready to resolve.")

        # Fetch X engagement metrics for posts older than 24h that haven't been rated
        try:
            from octo_skill_log import fetch_engagement_for_pending
            updated = fetch_engagement_for_pending(max_fetch=20)
            if updated:
                print(f"[Runner] Engagement metrics updated for {updated} post(s).")
        except Exception as e:
            print(f"[Runner] Engagement fetch skipped: {e}")

        # Generate and post weekly scorecard on Sundays
        from datetime import datetime
        if datetime.now().weekday() == 6:  # Sunday
            post = _build_scorecard_post()
            if post:
                queue_post(post, post_type="scorecard", priority=1)
                process_queue(max_posts=1)
                print(f"[Runner] Scorecard posted to X.")
            else:
                print("[Runner] No resolved calls to post scorecard yet.")
        else:
            stats = get_stats()
            print(f"[Runner] Scorecard runs on Sundays. Current record: "
                  f"{stats['wins']}W / {stats['losses']}L ({stats['win_rate']}), streak: {stats['streak']}")
    except Exception as e:
        print(f"[Runner] mode_scorecard failed: {e}")
        discord_alert(f"scorecard mode failed: {e}")


def _build_scorecard_post() -> str | None:
    """Generate weekly scorecard post from octo_calls.json data."""
    from datetime import datetime, timezone, timedelta
    stats = get_stats()
    wins = stats["wins"]
    losses = stats["losses"]
    total = wins + losses
    if total == 0:
        return None

    win_rate = wins / total * 100
    all_calls = stats["all_calls"]

    # Best and worst resolved calls this week
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    recent_resolved = []
    for c in all_calls:
        if not c.get("resolved") or not c.get("resolved_at"):
            continue
        try:
            resolved_dt = datetime.strptime(c["resolved_at"], "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if resolved_dt >= week_ago:
            recent_resolved.append(c)

    if not recent_resolved:
        # Fall back to all resolved calls if nothing resolved this week
        recent_resolved = [c for c in all_calls if c.get("resolved")]

    wins_this_week = [c for c in recent_resolved if c.get("outcome") == "WIN"]
    losses_this_week = [c for c in recent_resolved if c.get("outcome") == "LOSS"]
    week_total = len(wins_this_week) + len(losses_this_week)
    week_rate = f"{len(wins_this_week)/week_total*100:.0f}%" if week_total > 0 else "N/A"

    best = max(
        wins_this_week,
        key=lambda c: abs((c.get("exit_price", 0) - c["entry_price"]) / c["entry_price"]),
        default=None
    )
    best_line = ""
    if best:
        pct = (best["exit_price"] - best["entry_price"]) / best["entry_price"] * 100
        best_line = f"Best: {best['asset']} {best['direction']} {pct:+.1f}%."

    post = (
        f"Oracle weekly scorecard. {len(wins_this_week)}/{week_total} calls correct. "
        f"Win rate: {week_rate}. All-time: {wins}W/{losses}L. "
        f"{best_line} "
        f"Streak: {stats['streak']}. Receipts posted. The ocean doesn't lie."
    )
    if len(post) > 280:
        post = post[:277] + "..."
    print(f"[Runner] Scorecard post: {post}")
    return post


def mode_youtube() -> None:
    """
    Scan watched channels for new videos.
    Only generates and queues a post if the content scores 8+/10.
    The post never mentions the video source — it reads as Octodamus's own thought.
    """
    print(f"\n[Runner] Scanning YouTube channels for new intel...")
    try:
        from octo_youtube import generate_post_from_intel
        post_worthy = youtube_scan_channels()  # returns only 8+/10 entries

        if not post_worthy:
            print("[Runner] No post-worthy content found today.")
            return

        # Take the single highest-relevance entry
        best = max(post_worthy, key=lambda e: e["summary"].get("relevance", 0))
        relevance = best["summary"].get("relevance", 0)
        pillar = best["summary"].get("pillar", "?")
        print(f"[Runner] Best entry: [{best['channel']}] {best['title']} — {relevance}/10 ({pillar})")

        post = generate_post_from_intel(best)
        if not post:
            print("[Runner] Post generation returned nothing.")
            return

        print(f"[Runner] Generated post:\n  {post}")
        queue_post(post, post_type="youtube", priority=2)
        posted = process_queue(max_posts=1, force=True)
        if posted:
            print(f"[Runner] YouTube-inspired post published.")
            discord_alert(
                f"YouTube post published ({relevance}/10, {pillar}):\n{post}"
            )
        else:
            print("[Runner] Post queued (posting hours or limit reached).")

    except Exception as e:
        print(f"[Runner] mode_youtube failed: {e}")
        discord_alert(f"youtube mode failed: {e}")


def mode_moonshot() -> None:
    """
    Weekly scan of 10 Moonshots Podcast 2026 predictions.
    Rotates through predictions 3 at a time, finds the most interesting
    current signal, posts as Octodamus.
    """
    print("\n[Runner] Scanning Moonshot predictions...")
    try:
        from moonshot_tracker import build_moonshot_context
        moonshot_ctx = build_moonshot_context(max_predictions=3)
        print(moonshot_ctx)

        call_ctx = build_call_context() if _CALLS_ACTIVE else ""

        system = OCTO_SYSTEM + """

You track 10 major technology predictions for 2026 from leading futurists.
Your job: find ONE prediction that has the most interesting real-world signal RIGHT NOW.
Write one oracle post about it — what's actually happening, what does it signal?
Be specific. Use data if you have it. Connect it to the bigger picture.
Do NOT write Oracle call: — this is analysis, not a directional trade call.
Under 480 chars. No hashtags."""

        prompt = (
            f"{moonshot_ctx}\n\n"
            f"{call_ctx}\n\n"
            "Pick the single most interesting prediction signal happening RIGHT NOW. "
            "What has changed? What data point or event confirms or challenges this prediction? "
            "Write one sharp oracle post for @octodamusai. Under 480 chars."
        )

        response = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=200,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        post = response.content[0].text.strip()
        queue_post(post, post_type="moonshot", priority=3)
        posted = process_queue(max_posts=1, force=True)
        print(f"[Runner] Moonshot post {'posted' if posted else 'queued'}:\n  {post}")
    except Exception as e:
        print(f"[Runner] mode_moonshot failed: {e}")
        discord_alert(f"moonshot mode failed: {e}")


def mode_congress() -> None:
    import re as _re
    print(f"\n[Runner] Scanning congressional trades (full House + Senate)...")
    try:
        # Full scan: all tickers, all members -- not limited to 7-ticker watchlist
        data = run_full_congress_scan(days_back=14)
        if data.get("error"):
            print(f"[Runner] Congress error: {data['error']}")
            return
        if data["total"] == 0:
            print("[Runner] No notable congressional trades found.")
            return
        context = format_congress_for_prompt(data)
        print(context)

        # Build ground-truth sets for validation
        valid_tickers = {t["ticker"].upper() for t in data.get("trades", [])}
        valid_names   = {t["politician"].split()[-1] for t in data.get("trades", [])}

        from datetime import date
        today = date.today().strftime("%B %d, %Y")
        response = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            system=OCTO_SYSTEM,
            messages=[{"role": "user", "content": (
                f"Today is {today}. Congressional trading alert for @octodamusai.\n{context}\n\n"
                "STRICT RULE: Only reference tickers, politician names, dates, and dollar amounts "
                "that appear verbatim in the data above. Do NOT invent tickers, companies, or trade "
                "details not present. If you mention a stock, it must be one of: "
                f"{', '.join(sorted(valid_tickers))}.\n\n"
                "CONTRARIAN voice. One post under 280 chars.\n"
                "Core belief: Congress members don't predict markets -- they front-run them. "
                "They trade on what they know is coming. Follow the money, not the narrative.\n"
                "Name the politician and ticker. Call out the timing. "
                "What do they know that the market doesn't yet? No price targets. No hashtags."
            )}],
        )
        post = response.content[0].text.strip()

        # Validate: any $TICKER in post must be in actual congress data
        mentioned = {m.upper() for m in _re.findall(r'\$([A-Z]{1,5})', post)}
        hallucinated = mentioned - valid_tickers
        if hallucinated:
            print(f"[Runner] BLOCKED congress post -- hallucinated tickers: {hallucinated}")
            print(f"[Runner] Blocked post was:\n  {post}")
            discord_alert(f"Congress post blocked: hallucinated {hallucinated} -- not in data {valid_tickers}")
            return

        queue_post(post, post_type="congress_signal", priority=2)
        process_queue(max_posts=1, force=True)
        print(f"[Runner] Congress signal posted:\n  {post}")
    except Exception as e:
        print(f"[Runner] mode_congress failed: {e}")


def mode_govcontracts() -> None:
    print(f"\n[Runner] Scanning government contracts...")
    try:
        data = run_govcontracts_scan(days_back=7)
        if data.get("error"):
            print(f"[Runner] GovContracts error: {data['error']}")
            return
        if data["total"] == 0:
            print("[Runner] No significant government contracts found.")
            return

        top = get_top_contract_for_post(data)
        if not top:
            print("[Runner] No contracts above post threshold.")
            return

        context = format_govcontracts_for_prompt(data)
        print(context)

        valid_tickers = {c["ticker"].upper() for c in data.get("contracts", [])}

        from datetime import date
        import re as _re
        today = date.today().strftime("%B %d, %Y")
        response = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=220,
            system=OCTO_SYSTEM,
            messages=[{"role": "user", "content": (
                f"Today is {today}. Government contract intelligence for @octodamusai.\n{context}\n\n"
                "STRICT RULE: Only reference tickers, agencies, dollar amounts, and contract details "
                "that appear verbatim in the data above. Do NOT invent details.\n\n"
                "Voice: Octodamus -- oracle who reads defense spending as signal. Dry, precise.\n"
                "The angle: big defense contracts precede stock moves and signal geopolitical direction. "
                "Name the company ($TICKER), the amount, the agency, and the implication.\n"
                "One post under 280 chars. No hashtags. No price targets."
            )}],
        )
        post = response.content[0].text.strip()

        # Validate tickers
        mentioned = {m.upper() for m in _re.findall(r'\$([A-Z]{1,5})', post)}
        hallucinated = mentioned - valid_tickers
        if hallucinated:
            print(f"[Runner] BLOCKED govcontracts post -- hallucinated tickers: {hallucinated}")
            return

        queue_post(post, post_type="govcontracts", priority=2)
        process_queue(max_posts=1, force=True)
        print(f"[Runner] GovContracts posted:\n  {post}")
    except Exception as e:
        print(f"[Runner] mode_govcontracts failed: {e}")


# ─────────────────────────────────────────────
# MODE: FORMAT — viral format rotation post
# ─────────────────────────────────────────────

def mode_format() -> None:
    """
    Generate a post using the viral format rotation engine.
    Formats: data_drop | ai_humor | market_math | oracle_take | contrarian
    Never posts same format twice in a row. Learns from engagement data.
    """
    try:
        from octo_format_engine import run_format_post, format_engine_status
        print(format_engine_status())

        # Build extra context from call record
        call_ctx = build_call_context() if _CALLS_ACTIVE else ""
        result   = run_format_post(context=call_ctx)

        if not result:
            print("[Runner] Format engine returned no post.")
            return

        post = result["text"]
        fmt  = result["format"]

        queue_post(post, post_type=fmt, priority=4)
        posted = process_queue(max_posts=1, force=True)

        if posted:
            try:
                import json as _json
                from pathlib import Path as _Path
                _plog = _json.loads((_Path(__file__).parent / "octo_posted_log.json").read_text(encoding="utf-8"))
                _last = list(_plog.values())[-1]
                log_post(_last["text"], fmt, fmt, False, _last.get("url", ""))
            except Exception:
                log_post(post, fmt, fmt, False)

        print(f"[Runner] Format post [{fmt}] {'posted' if posted else 'queued'}:\n  {post}")

    except Exception as e:
        print(f"[Runner] mode_format failed: {e}")
        discord_alert(f"format mode failed: {e}")


# ─────────────────────────────────────────────
# MODE: QRT — breaking news quote-tweet
# ─────────────────────────────────────────────

def mode_qrt() -> None:
    """
    Scan for breaking news headlines and generate QRT captions.
    Window: 30-60 min after headline drops. Posts immediately if worthy.
    """
    try:
        from octo_format_engine import run_qrt_scan

        qrts = run_qrt_scan()

        if not qrts:
            print("[Runner] No QRT-worthy headlines right now.")
            return

        for qrt in qrts[:1]:   # post max 1 QRT per run
            post     = qrt["text"]
            headline = qrt.get("headline", "")
            source   = qrt.get("source", "")

            # Prepend headline as context line if it fits
            full_post = post
            queue_post(full_post, post_type="qrt", priority=2,
                       metadata={"headline": headline, "source": source})
            posted = process_queue(max_posts=1)
            print(f"[Runner] QRT {'posted' if posted else 'queued'}:")
            print(f"  Headline: {headline[:70]}")
            print(f"  Caption:  {post[:80]}")

    except Exception as e:
        print(f"[Runner] mode_qrt failed: {e}")
        discord_alert(f"qrt mode failed: {e}")


def mode_morning_flow() -> None:
    """
    5 AM / 6 AM / 7 AM morning post: where is the money flowing right now
    and exactly how to take advantage of it. Specific, directional, actionable.
    """
    print(f"\n[Runner] Generating morning flow post...")
    try:
        # Pull live crypto prices
        import requests as _req
        prices = {}
        try:
            _r = _req.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": "bitcoin,ethereum,solana", "vs_currencies": "usd",
                        "include_24hr_change": "true", "include_market_cap": "true"},
                timeout=10,
            )
            _d = _r.json()
            for name, sym in [("bitcoin","BTC"),("ethereum","ETH"),("solana","SOL")]:
                if name in _d:
                    prices[sym] = {
                        "price": _d[name].get("usd", 0),
                        "change_24h": round(float(_d[name].get("usd_24h_change", 0) or 0), 2),
                        "mcap": _d[name].get("usd_market_cap", 0),
                    }
        except Exception as e:
            print(f"[Runner] Price fetch failed: {e}")

        # Fear & Greed
        fg = 50
        try:
            _fg = _req.get("https://api.alternative.me/fng/?limit=1", timeout=8).json()
            fg = int(_fg["data"][0]["value"])
        except Exception:
            pass

        coinglass_ctx = _get_coinglass_context()
        deribit_ctx   = _get_deribit_context("BTC")
        headlines     = get_top_headlines(["BTC", "ETH", "SOL"], max_per_symbol=2)
        news_ctx      = format_headlines_for_prompt(headlines)
        tv_ctx        = get_tv_brief()
        brain_ctx     = get_brain_context() if _NUNCHI_ACTIVE else ""
        call_ctx      = build_call_context() if _CALLS_ACTIVE else ""
        flights_ctx   = get_travel_context() if _FLIGHTS_ACTIVE else ""
        macro_ctx     = get_macro_context() if _MACRO_ACTIVE else ""
        uw_ctx        = get_uw_context() if _UW_ACTIVE else ""

        extra_ctx = (
            (f"Macro Transport Signal:\n{flights_ctx}\n\n" if flights_ctx else "")
            + (f"Cross-Asset Macro:\n{macro_ctx}\n\n" if macro_ctx else "")
            + (f"Options Flow & Dark Pool:\n{uw_ctx}\n\n" if uw_ctx else "")
        )

        prompt = (
            f"Time: {datetime.now().strftime('%I:%M %p')} PT — pre-market / early session.\n"
            f"BTC ${prices.get('BTC',{}).get('price',0):,.0f} ({prices.get('BTC',{}).get('change_24h',0):+.1f}% 24h) | "
            f"ETH ${prices.get('ETH',{}).get('price',0):,.0f} ({prices.get('ETH',{}).get('change_24h',0):+.1f}%) | "
            f"SOL ${prices.get('SOL',{}).get('price',0):,.0f} ({prices.get('SOL',{}).get('change_24h',0):+.1f}%)\n"
            f"Fear & Greed: {fg}/100\n\n"
            f"Futures/OI Intelligence:\n{coinglass_ctx}\n\n"
            f"Options Intelligence:\n{deribit_ctx}\n\n"
            f"Chart data:\n{tv_ctx}\n\n"
            f"News:\n{news_ctx}\n\n"
            f"{call_ctx}\n\n"
            f"{brain_ctx}\n\n"
            + extra_ctx
            + "Write one post under 280 chars for @octodamusai.\n"
            "REQUIRED: Name the specific asset ($BTC, $ETH, $SOL, etc.) whenever you cite a price, "
            "percentage, or data point — never leave a number without a ticker.\n"
            "Focus: WHERE is the money flowing right now (specific market, specific direction) "
            "and exactly HOW a trader takes advantage — entry zone, what they're watching, "
            "or what the crowd is missing. Be specific with numbers. No vague takes. "
            "Do NOT use 'Oracle call:' format — this is a market flow read, not a formal call. "
            "Lead with the flow signal, end with the edge."
        )

        response = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=350,
            system=OCTO_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        post = response.content[0].text.strip()

        try:
            card = build_signal_card(post)
            if len(card) <= 280:
                post = card
        except Exception:
            pass

        # Generate plain-English explanation reply (the "what this means" thread)
        explain_prompt = (
            f"The following post was just written for @octodamusai:\n\n\"{post}\"\n\n"
            "Write a reply tweet under 280 chars that explains what the key terms and numbers mean "
            "in plain English — no jargon. Teach the reader exactly what the data signals and why "
            "it matters for their money. Use this format:\n"
            "What this means: [term] = [plain explanation]. [term] = [plain explanation]. "
            "[One sentence on the trading implication.]\n"
            "Keep it educational, grounded, and under 280 chars."
        )
        try:
            explain_resp = claude.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=200,
                system=OCTO_SYSTEM,
                messages=[{"role": "user", "content": explain_prompt}],
            )
            explanation = explain_resp.content[0].text.strip()
        except Exception as e:
            print(f"[Runner] Explanation generation failed: {e}")
            explanation = None

        if explanation:
            from octo_x_poster import queue_thread
            queue_thread([post, explanation], post_type="morning_flow")
        else:
            queue_post(post, post_type="morning_flow", priority=1)

        posted = process_queue(max_posts=1, force=True)
        print(f"[Runner] Morning flow {'posted' if posted else 'queued'}:\n  {post}")
        if explanation:
            print(f"[Runner] Thread reply:\n  {explanation}")

    except Exception as e:
        print(f"[Runner] mode_morning_flow failed: {e}")
        discord_alert(f"morning_flow mode failed: {e}")


# ─────────────────────────────────────────────
# MODE: THREAD — weekly deep-dive thread (4 tweets)
# ─────────────────────────────────────────────

def mode_thread(topic: str = "") -> None:
    """
    Post a 4-tweet thread on a specific market topic.
    Highest-engagement format — runs weekly or on-demand with --mode thread --ticker TOPIC.
    If no topic given, auto-selects based on current market conditions.
    """
    try:
        from octo_personality import build_thread_prompt, parse_thread_output

        # Build live data context
        context_parts = []
        try:
            context_parts.append(build_call_context())
        except Exception:
            pass
        try:
            context_parts.append(get_brain_context())
        except Exception:
            pass
        try:
            fc = get_travel_context()
            if fc:
                context_parts.append(fc)
        except Exception:
            pass
        try:
            mc = get_macro_context()
            if mc:
                context_parts.append(mc)
        except Exception:
            pass
        try:
            uw = get_uw_context()
            if uw:
                context_parts.append(uw)
        except Exception:
            pass

        live_data_block = "\n".join(p for p in context_parts if p)

        # Auto-select topic if none given
        if not topic:
            # Pick based on day of week / market conditions
            from datetime import datetime
            day = datetime.now().weekday()
            topics = [
                "why derivatives data leads price action by 24-48 hours",
                "what funding rates actually tell you vs what people think they tell you",
                "how congressional trading patterns predict regulatory moves",
                "the mechanics of a liquidation cascade and how to read the setup",
                "why the Fear & Greed index is most useful at its extremes",
                "what on-chain stablecoin flows reveal about institutional positioning",
                "how to read open interest divergence from price",
            ]
            topic = topics[day % len(topics)]

        print(f"[Runner] Thread topic: {topic}")

        prompt = build_thread_prompt(topic, live_data_block)

        response = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            system=OCTO_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = response.content[0].text.strip()
        tweets = parse_thread_output(raw)

        if len(tweets) < 2:
            print(f"[Runner] Thread parse failed — got {len(tweets)} tweet(s). Raw: {raw[:200]}")
            return

        # Post as a thread
        queue_thread(tweets, post_type="thread", metadata={"topic": topic})
        posted = process_queue(max_posts=1, force=True)
        print(f"[Runner] Thread {'posted' if posted else 'queued'} ({len(tweets)} tweets):")
        for i, t in enumerate(tweets, 1):
            print(f"  [{i}] {t[:80]}...")

    except Exception as e:
        print(f"[Runner] mode_thread failed: {e}")
        discord_alert(f"thread mode failed: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Octodamus Runner")
    parser.add_argument(
        "--mode",
        choices=[
            "monitor", "daily", "deep_dive", "wisdom",
            "status", "drain", "journal", "alert", "engage", "scorecard", "soul", "congress", "govcontracts", "moonshot",
            "mentions", "youtube", "format", "qrt", "morning_flow",
            "strategy_monitor", "strategy_sunday", "thread",
        ],
        default="monitor",
    )
    parser.add_argument("--ticker", type=str, default="NVDA")
    parser.add_argument("--force", action="store_true", help="Bypass posting hours and daily limit")
    args = parser.parse_args()

    if args.force:
        import octo_x_poster
        octo_x_poster.FORCE_POST = True
        octo_x_poster._DAILY_LIMIT = 99  # also bypass daily limit in force mode
        print("[Runner] --force: bypassing posting hours and daily limit.")

    if args.mode == "monitor":
        mode_monitor()
    elif args.mode == "daily":
        mode_daily()
    elif args.mode == "deep_dive":
        mode_deep_dive(args.ticker)
    elif args.mode == "wisdom":
        mode_wisdom()
    elif args.mode == "congress":
        mode_congress()
    elif args.mode == "govcontracts":
        mode_govcontracts()
    elif args.mode == "soul":
        mode_soul()
    elif args.mode == "scorecard":
        mode_scorecard()
    elif args.mode == "journal":
        mode_journal()
    elif args.mode == "status":
        queue_status()
    elif args.mode == "drain":
        import octo_x_poster
        if args.force:
            octo_x_poster.FORCE_POST = True
            octo_x_poster._DAILY_LIMIT = 99
        posted = process_queue(max_posts=10)
        print(f"[Runner] Drained {posted} posts.")
    elif args.mode == "alert":
        from octo_alert import run_alert_scan
        run_alert_scan(secrets=secrets, claude_client=claude)
    elif args.mode == "engage":
        from octo_engage import run as engage_run
        engage_run()
    elif args.mode == "moonshot":
        mode_moonshot()
    elif args.mode == "morning_flow":
        mode_morning_flow()
    elif args.mode == "mentions":
        from octo_x_mentions import poll_and_reply
        poll_and_reply(claude_client=claude)
    elif args.mode == "youtube":
        mode_youtube()
    elif args.mode == "format":
        mode_format()
    elif args.mode == "qrt":
        mode_qrt()
    elif args.mode == "strategy_monitor":
        from octo_strategy_tracker import mode_strategy_monitor
        mode_strategy_monitor(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
    elif args.mode == "strategy_sunday":
        from octo_strategy_tracker import mode_strategy_sunday
        mode_strategy_sunday(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
    elif args.mode == "thread":
        # Use --ticker to pass a topic string, e.g.: --ticker "funding rates"
        topic = args.ticker if args.ticker != "NVDA" else ""
        mode_thread(topic)

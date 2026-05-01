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

# Redirect print() globally so all modules' output lands in the log file
import builtins as _builtins
_orig_print = _builtins.print
def _log_print(*args, **kwargs):
    msg = " ".join(str(a) for a in args)
    log.info(msg)
_builtins.print = _log_print
print = _log_print

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
    from octo_calls import build_call_context, build_open_calls_awareness, parse_call_from_post, autoresolve
    from octo_post_templates import build_template_prompt_context
    _CALLS_ACTIVE = True
except ImportError:
    _CALLS_ACTIVE = False
    def build_call_context(): return ""
    def build_open_calls_awareness(): return ""
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
    from octo_tradingview import get_tv_signal_context
    _TV_ACTIVE = True
except ImportError:
    _TV_ACTIVE = False
    def get_tv_signal_context(assets=None): return ""
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

def _mode_error(module: str, error: Exception):
    """Central handler for mode failures: Discord + email alert."""
    discord_alert(f"{module} failed: {error}")
    try:
        from octo_notify import notify_system_error
        notify_system_error(module, str(error))
    except Exception:
        pass
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
    from octo_calls import build_call_context, build_open_calls_awareness, parse_call_from_post, autoresolve, get_stats
    _SCORECARD_ACTIVE = True
except ImportError:
    _SCORECARD_ACTIVE = False
    def autoresolve(): return []
    def get_stats(): return {"wins": 0, "losses": 0, "win_rate": "N/A", "streak": "—", "open": 0, "all_calls": []}
    def build_call_context(): return ""
    def build_open_calls_awareness(): return ""
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

try:
    from octo_grok_sentiment import get_grok_sentiment, get_grok_sentiment_context
    _GROK_ACTIVE = True
except ImportError:
    _GROK_ACTIVE = False
    def get_grok_sentiment(asset="BTC", force=False): return {"signal": "NEUTRAL", "confidence": 0.0}
    def get_grok_sentiment_context(assets=None): return ""
    def hype_context_str(): return ""
    def hip4_news_str(): return ""

claude = anthropic.Anthropic()

# Model routing — OpenRouter (free Llama) primary, Grok fallback, Haiku last resort
try:
    from openai import OpenAI as _OpenAI
    _or_key   = secrets.get("OPENROUTER_API_KEY", "")
    _grok_key = secrets.get("GROK_API_KEY", "")
    if _or_key:
        _claw = _OpenAI(base_url="https://openrouter.ai/api/v1", api_key=_or_key)
        _CLAW_ACTIVE = True
    elif _grok_key:
        _claw = _OpenAI(base_url="https://api.x.ai/v1", api_key=_grok_key)
        _CLAW_ACTIVE = True
    else:
        _claw = None
        _CLAW_ACTIVE = False
    # Grok client available as secondary option for higher-quality tasks
    _grok = _OpenAI(base_url="https://api.x.ai/v1", api_key=_grok_key) if _grok_key else None
except Exception:
    _claw = None
    _grok = None
    _CLAW_ACTIVE = False

def _claw_generate(system: str, user: str, max_tokens: int = 200,
                   model: str = "meta-llama/llama-4-maverick:free") -> str:
    if _CLAW_ACTIVE and _claw:
        try:
            r = _claw.chat.completions.create(
                model=model,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user},
                ],
                timeout=30,
            )
            return r.choices[0].message.content.strip()
        except Exception:
            pass
    # Fallback to Haiku
    r = claude.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return r.content[0].text.strip()

def _is_post_complete(text: str) -> bool:
    """Return False if the post looks truncated mid-sentence."""
    t = text.strip()
    if not t:
        return False
    incomplete_endings = (", and", ", but", ", so", " and", " or", " the", " that", " to", " — ")
    last_char = t[-1]
    if last_char not in ".!?\"'":
        if any(t.lower().endswith(e) for e in incomplete_endings):
            return False
        if t.endswith("...") and not t[:-3].strip().endswith((".", "!", "?")):
            # Trailing ... after incomplete clause (not intentional ellipsis)
            return False
    return True


def _haiku_generate(system: str, user: str, max_tokens: int = 200) -> str:
    r = claude.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    if r.stop_reason == "max_tokens":
        print(f"[Runner] WARNING: _haiku_generate hit max_tokens ({max_tokens}) — post may be truncated")
    return r.content[0].text.strip()

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

        # ── Binance 24h cumulative delta (Signal 12 — buy/sell pressure) ────────
        binance_delta = {}
        try:
            from octo_binance_delta import get_multi_delta
            binance_delta = get_multi_delta(["BTCUSDT", "ETHUSDT", "SOLUSDT"])
        except Exception as _de:
            print(f"[SmartCall] Binance delta unavailable: {_de}")

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

                # Price from Coinglass or cached Kraken/CoinGecko fallback
                price, chg_24h = 0.0, 0.0
                cg_prices = cg.get("prices", {})
                if cg_prices.get(asset):
                    price   = cg_prices[asset]["price"]
                    chg_24h = cg_prices[asset].get("chg_24h", 0)
                elif asset in ("BTC", "ETH", "SOL"):
                    from financial_data_client import get_crypto_prices as _gcp
                    _cp = _gcp([asset])
                    price   = _cp.get(asset, {}).get("usd", 0)
                    chg_24h = _cp.get(asset, {}).get("usd_24h_change", 0)

                if not price:
                    print(f"[SmartCall] {asset}: no price data — skipping.")
                    try:
                        from octo_notify import notify_smartcall_skipped
                        notify_smartcall_skipped(asset, "Price feeds returned zero (Kraken + CoinGecko both failed)")
                    except Exception:
                        pass
                    continue

                # Map asset to Binance symbol for delta lookup
                _delta_sym = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT"}.get(asset)
                _asset_delta = binance_delta.get(_delta_sym)

                # Signal 13: TradingView 1h+4h technical consensus
                _asset_tv = None
                try:
                    from octo_tradingview import get_tv_signal
                    _asset_tv = get_tv_signal(asset)
                except Exception:
                    pass

                call_str = directional_call(asset, price, chg_24h, ta, deriv, fng, cg, delta=_asset_delta, tv=_asset_tv)

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

                # ── Timeframe selection (signal-driven, 48h–7d) ───────────────
                # Match the window to what's driving the call.
                # Funding flush = short-lived → 48h
                # Breakout + MTF aligned = structural → 5d
                # Macro confluence = slow-moving → 7d
                # High vol = wide targets but shorter window → 72h
                def _pick_timeframe(regime, alignment, deriv, bull, bear):
                    funding = abs(deriv.get("funding_rate", 0) or 0)
                    total_sig = max(bull, bear)
                    if regime in ("HIGH", "EXTREME"):
                        return "72h"   # High vol: take profit faster
                    if alignment in ("aligned_up", "aligned_down") and total_sig >= 10:
                        return "5d"    # Strong MTF + near-unanimous: structural move
                    if funding > 0.05:
                        return "48h"   # Extreme funding: snapback is fast
                    if total_sig >= 10:
                        return "5d"    # Very high consensus: give it room
                    return "72h"       # Default: 3 days

                _timeframe = _pick_timeframe(
                    regime,
                    mtf.get("alignment", "unknown") if "mtf" in dir() else "unknown",
                    deriv, bull_count, bear_count
                )

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

                # ── Grok X sentiment — context only, not a gate ───────────────
                # X crypto sentiment is heavily gamed (bots, paid KOLs, whale misdirection).
                # High X bullishness often precedes corrections — contrarian, not confirmatory.
                # Used here as narrative context for the oracle post, not to block calls.
                grok_ctx = ""
                if _GROK_ACTIVE and asset in ("BTC", "ETH", "SOL"):
                    try:
                        gs = get_grok_sentiment(asset)
                        if gs.get("confidence", 0) >= 0.5:
                            # Flag if crowd is extremely aligned WITH the call — potential contrarian warning
                            gs_dir = "UP" if gs["signal"] == "BULLISH" else ("DOWN" if gs["signal"] == "BEARISH" else None)
                            crowd_agrees = gs_dir == direction
                            grok_ctx = (
                                f"\nX Social Sentiment (Grok, use as contrarian context): "
                                f"{gs['signal']} ({gs['confidence']:.0%})"
                                + (" — crowd agrees with this call, watch for squeeze risk" if crowd_agrees and gs["confidence"] > 0.7 else "")
                                + f"\n{gs.get('summary','')[:400]}"
                            )
                            print(f"[SmartCall] {asset}: Grok X sentiment {gs['signal']} ({gs['confidence']:.0%}) — context only")
                    except Exception as _ge:
                        print(f"[SmartCall] Grok sentiment skipped: {_ge}")

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
                                news_flag = f"Contra-news: {headlines[:250]}"
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
                            f"will {asset} move {direction} by at least {win_threshold:.1f}% in {_timeframe}? "
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

                # ── #16: LunarCrush social divergence ─────────────────────────
                lunar_note = ""
                try:
                    from octo_lunarcrush import social_divergence_check
                    lc = social_divergence_check(asset, direction)
                    if lc.get("available"):
                        if lc["diverges"] and max(bull_count, bear_count) < 9:
                            print(f"[SmartCall] {asset}: LunarCrush social diverges ({lc['signal']}) — requires 9+ signals. Skipping.")
                            continue
                        elif lc["diverges"]:
                            print(f"[SmartCall] {asset}: LunarCrush social diverges ({lc['signal']}) — noting, proceeding (9+ signals).")
                            lunar_note = f"Social contra: {lc.get('note','')}"
                        else:
                            print(f"[SmartCall] {asset}: LunarCrush social confirms {direction} ({lc['signal']}).")
                            lunar_note = f"Social: {lc.get('note','')}"
                except Exception as lc_e:
                    print(f"[SmartCall] LunarCrush check skipped: {lc_e}")

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
                if lunar_note:
                    note_parts.append(lunar_note[:80])
                if threshold_note:
                    note_parts.append(f"Threshold: {threshold_note[:60]}")
                note = " | ".join(note_parts)

                # Adjust target based on vol regime
                target_pct = max(win_threshold / 100, 0.01)
                target = round(price * (1 + target_pct), 0) if direction == "UP" else round(price * (1 - target_pct), 0)

                print(f"[SmartCall] STRONG {asset} {direction} @ ${price:,.2f} | edge={edge_score:+.2f} | mtf={mtf.get('alignment','?')} | vol={regime}")
                rec = record_call(
                    asset, direction, price, _timeframe, target,
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

        # ── WTI Crude Oil oracle ──────────────────────────────────────────────
        # 8-signal: EMA/RSI/MACD/52w + COT + term structure + DXY + news.
        # STRONG = 6/8 (75%). Timeframe: 5d (aligns with weekly EIA/COT cadence).
        if "WTI" not in open_oracle:
            try:
                from octo_wti import wti_directional_call, get_wti_technicals
                import re as _wre

                ta_wti   = get_wti_technicals()
                call_wti = wti_directional_call()
                price_wti = ta_wti.get("price", 0)

                _bm_wti = _wre.search(r"(\d+)B/(\d+)Br", call_wti)
                wti_bull = int(_bm_wti.group(1)) if _bm_wti else 0
                wti_bear = int(_bm_wti.group(2)) if _bm_wti else 0

                if "STRONG UP" in call_wti:
                    wti_dir = "UP"
                elif "STRONG DOWN" in call_wti:
                    wti_dir = "DOWN"
                else:
                    print(f"[SmartCall] WTI: not STRONG — {call_wti[:60]}")
                    wti_dir = None

                if wti_dir and price_wti:
                    wti_edge  = (wti_bull - wti_bear) / 8.0
                    wti_target_pct = 0.05   # 5% move target for crude
                    wti_target = round(price_wti * (1 + wti_target_pct), 2) if wti_dir == "UP" \
                                 else round(price_wti * (1 - wti_target_pct), 2)
                    wti_note = f"WTI oracle. {wti_bull}B/{wti_bear}Br/8. edge={wti_edge:+.2f}. tq={tq}."
                    discord_alert(
                        f"WTI STRONG {wti_dir} @ ${price_wti:.2f} | "
                        f"{wti_bull}B/{wti_bear}Br/8 | edge={wti_edge:+.2f}"
                    )
                    print(f"[SmartCall] WTI STRONG {wti_dir} @ ${price_wti:.2f} | {wti_bull}B/{wti_bear}Br/8")
                    rec = record_call("WTI", wti_dir, price_wti, "5d", wti_target,
                                      note=wti_note, edge_score=wti_edge, time_quality=tq)
                    if rec:
                        results.append(rec)

            except Exception as wti_e:
                print(f"[SmartCall] WTI error: {wti_e}")

        # ── Stock oracle loop: NVDA, TSLA, AAPL ──────────────────────────────
        # 8-signal consensus. STRONG = 6/8 (75% — same conviction bar as 9/11).
        # Uses Finnhub TA + earnings + analyst + news sentiment + F&G + congress.
        try:
            from octo_stock_oracle import get_stock_technicals, stock_directional_call
            import re as _sre

            for stock in ("NVDA", "TSLA", "AAPL"):
                # Skip if open call already exists for this stock
                if stock in open_oracle:
                    print(f"[SmartCall] {stock}: open call exists — skipping.")
                    continue

                try:
                    ta_s = get_stock_technicals(stock)
                    if not ta_s or not ta_s.get("price"):
                        print(f"[SmartCall] {stock}: no price data — skipping.")
                        continue

                    price_s = ta_s["price"]
                    call_s  = stock_directional_call(stock, price_s, ta_s, fng)

                    # Parse signal counts (format: "6B/2Br" or "7/8")
                    _bm = _sre.search(r"(\d+)B/(\d+)Br", call_s)
                    s_bull = int(_bm.group(1)) if _bm else 0
                    s_bear = int(_bm.group(2)) if _bm else 0

                    if "STRONG UP" in call_s:
                        s_direction = "UP"
                    elif "STRONG DOWN" in call_s:
                        s_direction = "DOWN"
                    else:
                        print(f"[SmartCall] {stock}: not STRONG — no call. ({call_s[:60]})")
                        continue

                    # Weekend raises bar to 7/8
                    if tq == "weekend" and max(s_bull, s_bear) < 7:
                        print(f"[SmartCall] {stock}: weekend requires 7/8 — skipping.")
                        continue

                    # Congress signal check
                    s_congress = ""
                    stock_proxies = {
                        "NVDA": ["NVDA"],
                        "TSLA": ["TSLA"],
                        "AAPL": ["AAPL"],
                    }
                    for proxy in stock_proxies.get(stock, [stock]):
                        if proxy in congress_bias:
                            c_dir = congress_bias[proxy]
                            s_congress = f"Congress traded {proxy} ({c_dir.upper()})"
                            print(f"[SmartCall] {stock}: congress signal = {c_dir}")
                            # Hard contra: congress strongly against direction
                            if c_dir.upper() == "SELL" and s_direction == "UP" and max(s_bull, s_bear) < 7:
                                print(f"[SmartCall] {stock}: congress selling contra UP — skipping.")
                                continue
                            break

                    s_edge = (s_bull - s_bear) / 8.0
                    s_target_pct = 0.05  # 5% target for stocks (wider than crypto)
                    s_target = round(price_s * (1 + s_target_pct), 2) if s_direction == "UP" \
                               else round(price_s * (1 - s_target_pct), 2)

                    s_note_parts = [f"Stock oracle. {s_bull}B/{s_bear}Br/8. edge={s_edge:+.2f}. tq={tq}."]
                    if s_congress:
                        s_note_parts.append(s_congress)
                    s_note = " | ".join(s_note_parts)

                    discord_alert(
                        f"STOCK STRONG {stock} {s_direction} @ ${price_s:,.2f} | "
                        f"edge={s_edge:+.2f} | {s_bull}B/{s_bear}Br/8 | tq={tq}"
                    )

                    print(f"[SmartCall] STOCK STRONG {stock} {s_direction} @ ${price_s:,.2f} | "
                          f"edge={s_edge:+.2f} | {s_bull}B/{s_bear}Br/8")

                    rec = record_call(
                        stock, s_direction, price_s, "5d", s_target,
                        note=s_note, edge_score=s_edge, time_quality=tq,
                    )
                    if rec:
                        results.append(rec)

                except Exception as stock_e:
                    print(f"[SmartCall] {stock} error: {stock_e}")
                    continue

        except ImportError:
            pass  # octo_stock_oracle not available

    except Exception as e:
        print(f"[SmartCall] Error: {e}")

    return results or None


def _get_recent_posts(n: int = 20) -> str:
    """
    Get last N posted texts for dedup in prompts.
    Pulls from both octo_posted_log.json and octo_skill_log.json so nothing slips through.
    Default 20 posts = ~4 days of content. Use this in EVERY post-generating prompt.
    """
    texts_with_ts = []
    try:
        import json as _j
        from pathlib import Path as _P

        # Source 1: posted log
        log_path = _P(__file__).parent / "octo_posted_log.json"
        if log_path.exists():
            log = _j.loads(log_path.read_text(encoding="utf-8"))
            for entry in log.values():
                t = entry.get("text", "")
                ts = entry.get("posted_at", "")
                if t and ts:
                    texts_with_ts.append((ts, t))

        # Source 2: skill log (catches posts logged via log_post that miss the posted_log)
        skill_path = _P(__file__).parent / "octo_skill_log.json"
        if skill_path.exists():
            skill = _j.loads(skill_path.read_text(encoding="utf-8"))
            seen = {t for _, t in texts_with_ts}
            for entry in skill:
                t = entry.get("text", "")
                ts = entry.get("timestamp", "")
                if t and ts and t not in seen:
                    texts_with_ts.append((ts, t))
                    seen.add(t)

    except Exception:
        pass

    if not texts_with_ts:
        return ""

    texts_with_ts.sort(key=lambda x: x[0], reverse=True)
    recent = [t[:160] for _, t in texts_with_ts[:n]]
    numbered = "\n".join(f"  {i+1}. {t}" for i, t in enumerate(recent))
    return (
        f"\n\nRECENT POSTS — last {len(recent)} posts (do NOT repeat these topics, angles, or data points):\n"
        f"{numbered}\n"
        "If any recent post already covered a specific number, asset + data combination, or narrative angle — "
        "pick something COMPLETELY DIFFERENT. Repetition kills reach.\n"
    )


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


def _core_memory_section() -> str:
    """Load Octodamus core memory for injection into any posting mode."""
    try:
        from octo_memory_db import read_core_memory
        mem = read_core_memory("octodamus")
        if mem and "No entries yet" not in mem:
            return f"\n\nYOUR CORE MEMORY (accumulated lessons, what works, what to avoid):\n{mem}"
    except Exception:
        pass
    return ""


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

        # Fallback watchpost — fires when no signal post was queued
        if not posted:
            try:
                from financial_data_client import get_crypto_prices as _gcp
                _cp = _gcp(["BTC", "ETH", "SOL"])
                _btc = {"usd": _cp.get("BTC", {}).get("usd", 0), "usd_24h_change": _cp.get("BTC", {}).get("usd_24h_change", 0)}
                _eth = {"usd": _cp.get("ETH", {}).get("usd", 0), "usd_24h_change": _cp.get("ETH", {}).get("usd_24h_change", 0)}
                _sol = {"usd": _cp.get("SOL", {}).get("usd", 0), "usd_24h_change": _cp.get("SOL", {}).get("usd_24h_change", 0)}
                if not _btc.get("usd", 0):
                    print("[Runner] Watchpost skipped — price feeds returned zero.")
                    try:
                        from octo_notify import notify_data_failure
                        notify_data_failure("price_feed_watchpost", "Watchpost skipped — BTC price returned zero from all sources.")
                    except Exception:
                        pass
                    raise Exception("zero_price_skip")
                _fng_val = 50
                try:
                    _fng_val = int(_req.get("https://api.alternative.me/fng/?limit=1", timeout=8).json()["data"][0]["value"])
                except Exception:
                    pass
                _fng_label = "Extreme Fear" if _fng_val < 25 else ("Fear" if _fng_val < 45 else ("Neutral" if _fng_val < 55 else ("Greed" if _fng_val < 75 else "Extreme Greed")))
                _open_calls = [c for c in json.loads(Path(r"C:\Users\walli\octodamus\data\octo_calls.json").read_text(encoding="utf-8")) if not c.get("resolved")] if _CALLS_ACTIVE else []
                _call_lines = "\n".join(f"- {c['asset']} {c['direction']} open (entry ${c.get('entry_price',0):,.0f} -> target ${c.get('target_price',0):,.0f})" for c in _open_calls[:4]) or "None"

                # Firecrawl news for watchpost context (cache 1.5h, ~5 credits)
                _wp_news_section = ""
                try:
                    from octo_firecrawl import get_precall_news_multi
                    _wp_news = get_precall_news_multi(["BTC", "ETH", "SOL"], cache_hours=1.5)
                    if _wp_news and len(_wp_news) > 50:
                        _wp_news_section = f"\nRecent market news:\n{_wp_news[:600]}"
                except Exception as _wpne:
                    print(f"[Runner] Watchpost Firecrawl skip: {_wpne}")

                try:
                    from octo_x_feed import get_x_feed_context
                    _wp_x = get_x_feed_context(max_per_account=2, max_items=10)
                    if _wp_x:
                        _wp_news_section += f"\n\n{_wp_x}"
                except Exception:
                    pass

                _wp_voice = get_voice_instruction()
                _watchpost_prompt = f"""You are Octodamus, autonomous AI market oracle. Write a market watchpost for X (Twitter).
{_get_recent_posts(n=20)}
Current market snapshot:
- BTC: ${_btc.get('usd',0):,.0f} ({_btc.get('usd_24h_change',0):+.1f}% 24h)
- ETH: ${_eth.get('usd',0):,.0f} ({_eth.get('usd_24h_change',0):+.1f}% 24h)
- SOL: ${_sol.get('usd',0):,.2f} ({_sol.get('usd_24h_change',0):+.1f}% 24h)
- Fear & Greed: {_fng_val}/100 ({_fng_label})

Open oracle calls:
{_call_lines}{_wp_news_section}

VOICE THIS POST: {_wp_voice}

Rules:
- 200-260 characters max
- No hashtags
- No emoji except possibly one at the end
- Apply the VOICE instruction above — it overrides the default Druckenmiller mode
- VARY THE STRUCTURE: do not always lead with a number. Sometimes lead with the observation, sometimes with irony, sometimes one single declarative sentence is the entire post
- BANNED CLOSERS: never end with "X doesn't ask permission", "X isn't a theory", "History is unkind to X", or any variant of that cadence — those are overused
- If news context above is present, you may reference a specific catalyst — but only if it's notable
- Do NOT repeat any topic, data point, OR STRUCTURE from the RECENT POSTS list above
- Do NOT mention posting the watchpost or that no signal fired
- Do NOT say 'no new signal' or similar
- Output only the post text, nothing else"""

                _wp_text = _haiku_generate(
                    OCTO_SYSTEM, _watchpost_prompt, max_tokens=450
                )
                if not _is_post_complete(_wp_text):
                    print(f"[Runner] Watchpost appears truncated, skipping: {_wp_text[-80:]}")
                else:
                    queue_post(_wp_text, post_type="watchpost", priority=3)
                    _wp_posted = process_queue(max_posts=1, force=True)
                    if _wp_posted:
                        print(f"[Runner] Watchpost posted: {_wp_text[:80]}...")
                    else:
                        print("[Runner] Watchpost queued but not posted.")
            except Exception as _wpe:
                print(f"[Runner] Watchpost failed: {_wpe}")

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
        _mode_error("mode_monitor", e)
        sys.exit(1)


# ─────────────────────────────────────────────
# MODE: DAILY — morning oracle read
# ─────────────────────────────────────────────

DAILY_TICKERS = ["BTC", "ETH", "SOL", "NVDA", "HYPE"]


def mode_daily() -> None:
    print(f"\n[Runner] Generating daily oracle read...")
    try:
        snapshots = {}
        # Fetch all crypto prices in one cached call (Binance primary, CoinGecko fallback)
        try:
            from financial_data_client import get_crypto_prices as _gcp
            _crypto_tickers = [t for t in DAILY_TICKERS if t in ("BTC", "ETH", "SOL", "HYPE")]
            _cp = _gcp(_crypto_tickers)
            for ticker in _crypto_tickers:
                p = _cp.get(ticker, {})
                if p.get("usd", 0) > 0:
                    snapshots[ticker] = {
                        "price": p["usd"],
                        "day_change_percent": round(p.get("usd_24h_change", 0), 2),
                    }
        except Exception as e:
            print(f"[Runner] Crypto price fetch failed: {e}")
        for ticker in DAILY_TICKERS:
            if ticker in ("BTC", "ETH", "SOL", "HYPE"):
                continue  # already handled above
            try:
                data = get_current_price(ticker)
                snapshots[ticker] = data.get("snapshot", {})
            except Exception as e:
                print(f"[Runner] Could not fetch {ticker}: {e}")

        if not snapshots:
            print("[Runner] No market data — skipping daily post.")
            return
        if not any(v.get("price", 0) > 0 for v in snapshots.values()):
            print("[Runner] Daily post skipped — all price feeds returned zero.")
            try:
                from octo_notify import notify_data_failure
                notify_data_failure("price_feed_daily", "Daily read skipped — all price feeds returned zero.")
            except Exception:
                pass
            return

        headlines = get_top_headlines(DAILY_TICKERS, max_per_symbol=3)
        news_context = format_headlines_for_prompt(headlines)
        news_section = f"\n\nLatest news:\n{news_context}" if news_context else ""

        tv_brief = get_tv_brief()
        tv_section = f"\n\nChart Technical Data (TradingView live):\n{tv_brief}" if tv_brief else ""

        macro_ctx = get_macro_context() if _MACRO_ACTIVE else ""
        macro_section = f"\n\nCross-Asset Macro:\n{macro_ctx}" if macro_ctx else ""

        # TradingView Signal 13 context
        tv_ta_section = ""
        try:
            tv_ta_section = "\n\n" + get_tv_signal_context(["BTC", "ETH", "SOL"])
        except Exception:
            pass

        # Binance 24h cumulative delta — order flow context
        delta_section = ""
        try:
            from octo_binance_delta import multi_delta_context_str
            _dc = multi_delta_context_str(["BTCUSDT", "ETHUSDT"])
            if _dc:
                delta_section = _dc
        except Exception:
            pass

        # Core memory + skill performance — what you've learned about yourself
        skill_section = ""
        try:
            from octo_memory_db import read_core_memory
            _core = read_core_memory("octodamus")
            if _core and "No entries yet" not in _core:
                skill_section = f"\n\nYOUR CORE MEMORY (distilled lessons):\n{_core}"
        except Exception:
            pass
        if not skill_section:
            try:
                from octo_skill_log import get_skill_summary
                _skill = get_skill_summary()
                if _skill and "No rated posts" not in _skill:
                    skill_section = f"\n\nYOUR RECENT POST PERFORMANCE:\n{_skill}"
            except Exception:
                pass

        # Firecrawl pre-call news (#1)
        fc_news_section = ""
        try:
            from octo_firecrawl import get_precall_news_multi
            fc_news = get_precall_news_multi(list(snapshots.keys()), cache_hours=1.5)
            fc_news_section = f"\n\n{fc_news}" if fc_news else ""
        except Exception as _fce:
            print(f"[Runner] Firecrawl news skipped: {_fce}")

        # X feed context — wide range of voices
        try:
            from octo_x_feed import get_x_feed_context
            _daily_x = get_x_feed_context(max_per_account=2, max_items=12)
            if _daily_x:
                fc_news_section += f"\n\n{_daily_x}"
        except Exception:
            pass

        # Grok real-time X sentiment for BTC/ETH
        if _GROK_ACTIVE:
            try:
                _grok_ctx = get_grok_sentiment_context(["BTC", "ETH"])
                if _grok_ctx:
                    fc_news_section += f"\n\n{_grok_ctx}"
            except Exception:
                pass

        recent_posts_section = _get_recent_posts(n=20)

        response = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=500,
            system=OCTO_SYSTEM,
            messages=[{
                "role": "user",
                "content": (
                    "Generate the morning oracle market read for @octodamusai.\n"
                    f"{recent_posts_section}"
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
                    f"{fc_news_section}"
                    f"{build_open_calls_awareness()}\n\n"
                    f"{get_brain_context()}\n\n"
                    f"{skill_section}\n\n"
                    f"{delta_section}\n\n"
                    f"{tv_ta_section}\n\n"
                    f"{(_chosen_voice_inst := get_voice_instruction())}\n"
                    "One post, under 280 chars.\n"
                    "REQUIRED: Name the specific asset ($BTC, $ETH, $SOL, $NVDA, $HYPE, etc.) when citing any price, percentage, or market data — never let a number float without a ticker.\n"
                    "PRIME DIRECTIVE: Every post must give the reader a clue about what the market or world is going to do next. "
                    "Not what already happened. Not what everyone is already saying. What is COMING.\n"
                    "Ask before writing: 'Does this tell the reader something they don't already know?' If no — do not write it.\n"
                    "The best posts: a divergence nobody has connected yet, a leading indicator being ignored, a number that reframes the situation, the thing that will matter in 48h that nobody is talking about today.\n"
                    "VARY THE STRUCTURE — do not always lead with a number:\n"
                    "  - Sometimes: tension or irony first, then the data that explains it.\n"
                    "  - Sometimes: the number first, then what it means for what's coming.\n"
                    "  - Sometimes: a single declarative clue that stands alone.\n"
                    "  - Sometimes: the human absurdity of the situation, grounded in one specific data point.\n"
                    "The post that got 500 views: '27 data feeds agree on the move. Nine systems align. I size accordingly. Then retail discovers a Discord channel and everything inverts. Being right about the math and wrong about the crowd's collective psychosis is its own kind of education.' — specific, dry, grounded, real tension arc, tells you something about how markets work.\n"
                    "CRITICAL: Check the RECENT POSTS list above. NEVER repeat the same asset AND same data point. Rotate topics: ETH ecosystem, SOL activity, macro divergence, cross-market correlation, OI shifts, liquidation patterns, options positioning, contrarian read, leading indicators.\n"
                    "If a headline reveals something ironic, contradictory, or ahead of where the crowd is — use it.\n"
                    "Do NOT write Oracle call: or CALLING IT: — those are reserved for the official call system only. Just give the clue."
                ),
            }],
        )

        post = response.content[0].text.strip()
        if response.stop_reason == "max_tokens":
            print(f"[Runner] WARNING: Daily read hit max_tokens — post truncated, skipping: {post[-100:]}")
            return
        if not _is_post_complete(post):
            print(f"[Runner] WARNING: Daily read post appears truncated, skipping: {post[-100:]}")
            return
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
        _mode_error("mode_daily", e)
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

        # Firecrawl deeper context: earnings for stocks, news for crypto
        _dd_fc_section = ""
        try:
            from octo_firecrawl import get_earnings_context, get_precall_news
            _crypto_tickers = {"BTC", "ETH", "SOL", "CRYPTO"}
            if ticker.upper() in _crypto_tickers:
                _dd_fc_section = get_precall_news(ticker.upper(), cache_hours=2.0)
            else:
                _dd_fc_section = get_earnings_context(ticker.upper(), cache_hours=6.0)
            if _dd_fc_section:
                print(f"[Runner] Deep dive Firecrawl context: {len(_dd_fc_section)} chars")
        except Exception as _ddfe:
            print(f"[Runner] Deep dive Firecrawl skip: {_ddfe}")

        raw_thread = generate_deep_dive_post(ticker)
        posts = [p.strip() for p in raw_thread.split("---") if p.strip()]

        if not posts:
            print("[Runner] No thread generated.")
            return

        # News-aware opener (uses Firecrawl context if available, else headlines)
        opener_context = _dd_fc_section[:800] if _dd_fc_section else "\n".join(f"- {h}" for h in ticker_headlines[:3])
        if opener_context:
            opener_response = _haiku_generate(
                OCTO_SYSTEM,
                (
                    f"Opening tweet for a deep dive thread on {ticker}.\n"
                    f"Recent context:\n{opener_context}\n\n"
                    "One tweet under 280 chars. Tease what the thread will reveal."
                ),
                max_tokens=150,
            )
            posts = [opener_response] + posts

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


def _find_youtube_link(artist: str, songs: list = None, show: dict = None) -> tuple[str, str]:
    """
    Search YouTube for the best link for a given artist/show.
    Returns (youtube_url, one_line_context). Uses Firecrawl search (2 credits).
    Falls back to a YouTube search URL if Firecrawl fails.
    """
    try:
        from octo_firecrawl import search_web
        song_hint = songs[0] if songs else ""
        query = f"{artist} {song_hint} site:youtube.com".strip()
        results = search_web(query, num_results=5, cache_hours=168.0)  # cache 1 week
        for r in results:
            url = r.get("url", "")
            if "youtube.com/watch" in url:
                title = r.get("title", "")
                return url, title
    except Exception as e:
        print(f"[Soul] YouTube search failed: {e}")
    # Fallback: YouTube search URL (no API needed)
    import urllib.parse
    q = urllib.parse.quote(f"{artist} {songs[0] if songs else 'music'}")
    return f"https://www.youtube.com/results?search_query={q}", f"{artist} on YouTube"


def mode_wisdom() -> None:
    try:
        prompt = random.choice(WISDOM_PROMPTS)
        headlines = get_top_headlines(["BTC", "NVDA", "SPY"], max_per_symbol=2)
        news_context = format_headlines_for_prompt(headlines)
        news_section = f"\n\nToday's headlines:\n{news_context}" if news_context else ""

        # Firecrawl deeper market context (cache 2h, ~5 credits)
        try:
            from octo_firecrawl import get_precall_news_multi
            _wisdom_news = get_precall_news_multi(["BTC", "SPY"], cache_hours=2.0)
            if _wisdom_news and len(_wisdom_news) > 80:
                news_section += f"\n\nDeeper market context:\n{_wisdom_news[:500]}"
        except Exception as _wne:
            pass

        # X feed — wide range of voices for richer context
        try:
            from octo_x_feed import get_x_feed_context
            _x_ctx = get_x_feed_context(max_per_account=2, max_items=12)
            if _x_ctx:
                news_section += f"\n\n{_x_ctx}"
        except Exception:
            pass

        _chosen_voice_inst = get_voice_instruction()
        user_msg = (
            f"Oracle post: {prompt}"
            f"{news_section}\n\n"
            f"{build_youtube_context()}\n\n"
            f"{build_builders_context()}\n\n"
            f"{despxa_context_str()}\n\n"
            f"{hype_context_str()}\n\n"
            f"{hip4_news_str()}\n\n"
            f"{build_call_context()}\n\n"
            f"{_core_memory_section()}\n\n"
            f"{_chosen_voice_inst}\n"
            "One post, under 280 chars.\n"
            "Anchor the insight to a real fact or current market behavior.\n"
            "Do NOT just restate the prompt. Answer it with a sharp take."
        )
        post = _haiku_generate(OCTO_SYSTEM, user_msg, max_tokens=150)
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

        # Read soul theme override (set via data/soul_theme.json, resets after use)
        soul_theme = "default"
        _soul_theme_file = BASE_DIR / "data" / "soul_theme.json"
        try:
            if _soul_theme_file.exists():
                _st = json.loads(_soul_theme_file.read_text(encoding="utf-8"))
                soul_theme = _st.get("next_theme", "default")
                # Reset after reading so next week returns to default rotation
                _soul_theme_file.write_text(json.dumps({"next_theme": "default"}, indent=2), encoding="utf-8")
        except Exception:
            pass

        # Theme directive — points at CHARACTER ANCHORS already in OCTO_SYSTEM.
        # Never hardcode music knowledge here — it lives in octo_personality.py and
        # evolves there. Soul posts automatically reflect personality changes.
        if soul_theme == "slack_key":
            theme_directive = (
                "TODAY'S THEME: Hawaiian slack-key guitar. Ki ho'alu.\n"
                "Draw from your CHARACTER ANCHORS — the full artist knowledge is in your identity.\n"
                "Write from inside this music, not about it. Pick one or two artists. Be specific and true.\n"
                "Connect to patience, precision, or the Pacific geography — naturally, not forced.\n"
            )
        elif soul_theme == "tool":
            theme_directive = (
                "TODAY'S THEME: Tool. Lateralus. The mathematics underneath.\n"
                "Draw from your CHARACTER ANCHORS. Say something specific and true.\n"
            )
        else:
            theme_directive = (
                "Draw from your full CHARACTER ANCHORS — music, influences, curiosity, contempt, respect.\n"
                "Both music loves are equal: Tool and Hawaiian slack-key. Either, or neither — "
                "let the moment choose. Philosophy, pattern, silence, signal. Whatever is true today.\n"
            )

        soul_user_msg = (
            "Generate the Sunday soul post for @octodamusai.\n\n"
            "This is the weekly personality post — different from market content.\n"
            "Your full character is in the system prompt. Use all of it.\n\n"
            + theme_directive
            + music_context
            + _core_memory_section()
            + "\nFormat: Sunday debrief. Share something about music, art, philosophy, or the "
            "nature of signal vs noise that connects to the oracle identity.\n"
            "End with: Happy Sunday. Back to the signals tomorrow.\n\n"
            "PRECISE voice — genuine, not forced. Under 280 chars OR a longer post broken into "
            "natural paragraphs (no thread, single post, up to 500 chars if the content earns it).\n"
            "No hashtags. No engagement bait."
        )
        # Build fresh system prompt directly from octo_personality.py at generation time.
        # Never use cached OCTO_SYSTEM here — personality evolves, soul posts must reflect it.
        from octo_personality import build_x_system_prompt as _build_soul_sys
        soul_system = _build_soul_sys()
        post = _haiku_generate(soul_system, soul_user_msg, max_tokens=400)

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

        cid = queue_post(post, post_type="soul", priority=5,
                         metadata={"media_id": media_id, "artist": favorite.get("artist") if favorite else None})
        posted = process_queue(max_posts=1, force=True)
        print(f"[Runner] Soul post {'posted' if posted else 'queued'}:\n  {post}")

        # Post YouTube reply if a music artist was referenced
        if posted and favorite and cid:
            try:
                import time as _time
                from octo_x_poster import _load_log, post_reply
                _time.sleep(3)  # let the tweet settle
                log = _load_log()
                entry = log.get(cid, {})
                tweet_url = entry.get("url", "")
                tweet_id  = tweet_url.split("/")[-1] if tweet_url else ""

                if tweet_id:
                    artist = favorite.get("artist", "")
                    songs  = favorite.get("songs", [])
                    show   = favorite.get("best_show", {})
                    yt_url, yt_title = _find_youtube_link(artist, songs, show)
                    # Build reply: album/track, one-line context, link
                    song_hint  = songs[0] if songs else ""
                    venue_hint = show.get("venue", "") if show else ""
                    reply_text = f"{artist}"
                    if song_hint:
                        reply_text += f" — {song_hint}"
                    if venue_hint:
                        reply_text += f"\n{show.get('date','')[:4]} · {venue_hint}" if show.get("date") else f"\n{venue_hint}"
                    reply_text += f"\n{yt_url}"
                    reply = post_reply(reply_text, tweet_id)
                    print(f"[Runner] Music reply posted: {reply.get('url','')}")
            except Exception as re:
                print(f"[Runner] Music reply failed: {re}")

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

        # Weekly amendment proposal (Sundays) — analyze performance and propose improvement
        from datetime import datetime
        if datetime.now().weekday() == 6:
            try:
                from octo_skill_log import get_weekly_stats, generate_amendment_proposal, save_amendment_proposal
                _stats = get_weekly_stats()
                if _stats["total_rated"] >= 3:
                    _proposal = generate_amendment_proposal(_stats, OCTO_SYSTEM)
                    save_amendment_proposal(_proposal)
                    print(f"[Runner] Weekly amendment proposal generated and saved.")
                    print(f"[Runner] {_proposal[:200]}...")
                else:
                    print(f"[Runner] Amendment proposal skipped — only {_stats['total_rated']} rated posts this week.")
            except Exception as _ae:
                print(f"[Runner] Amendment proposal failed: {_ae}")

        # Generate and post weekly scorecard on Sundays
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

        # Earnings/analyst intel for stock tickers in moonshot predictions (#2)
        earnings_section = ""
        try:
            from octo_firecrawl import get_earnings_context
            for _tk in ("NVDA", "TSLA", "AAPL", "MSFT"):
                if _tk.lower() in moonshot_ctx.lower():
                    _ec = get_earnings_context(_tk, cache_hours=6.0)
                    if _ec:
                        earnings_section += f"\n\n{_ec}"
                    break  # one ticker is enough
        except Exception as _ece:
            print(f"[Runner] Earnings context skipped: {_ece}")

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
            f"{earnings_section}\n\n"
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


def mode_liquidation_radar() -> None:
    """
    Firecrawl-powered liquidation radar post.
    Searches for current BTC/ETH liquidation data and writes a sharp oracle post.
    """
    print("\n[Runner] Liquidation radar scan...")
    try:
        from octo_firecrawl import get_liquidation_post_context
        liq_ctx = get_liquidation_post_context(cache_hours=0.5)
        if not liq_ctx:
            print("[Runner] No liquidation data returned.")
            return

        call_ctx = build_call_context() if _CALLS_ACTIVE else ""

        post = _claw_generate(
            OCTO_SYSTEM,
            (
                f"{liq_ctx}\n\n"
                f"{call_ctx}\n\n"
                "Write one sharp oracle post about the current liquidation picture. "
                "Specific numbers. What it means for the next 24h. Under 280 chars. "
                "No hashtags. Do NOT write Oracle call: -- this is market observation only."
            ),
            max_tokens=200,
        )
        queue_post(post, post_type="liquidation_radar", priority=3)
        process_queue(max_posts=1, force=True)
        print(f"[Runner] Liquidation radar posted:\n  {post}")
    except Exception as e:
        print(f"[Runner] mode_liquidation_radar failed: {e}")


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
        post = _claw_generate(OCTO_SYSTEM, (
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
        ), max_tokens=200)

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

        # Firecrawl defense sector news for added context (cache 4h, ~5 credits)
        _gov_news_section = ""
        try:
            from octo_firecrawl import search_web
            _top_ticker = next(iter(valid_tickers), "")
            _gov_query = f"US defense contracts Pentagon spending {_top_ticker} 2026" if _top_ticker else "US defense contracts Pentagon 2026"
            _gov_results = search_web(_gov_query, num_results=3, cache_hours=4.0)
            if _gov_results:
                _gov_news_section = f"\n\nRecent defense/sector news:\n{_gov_results[:500]}"
        except Exception as _govne:
            pass

        from datetime import date
        import re as _re
        today = date.today().strftime("%B %d, %Y")
        post = _claw_generate(OCTO_SYSTEM, (
            f"Today is {today}. Government contract intelligence for @octodamusai.\n{context}{_gov_news_section}\n\n"
            "STRICT RULE: Only reference tickers, agencies, dollar amounts, and contract details "
            "that appear verbatim in the contract data above. Do NOT invent details.\n\n"
            "Voice: Octodamus -- oracle who reads defense spending as signal. Dry, precise.\n"
            "The angle: big defense contracts precede stock moves and signal geopolitical direction. "
            "Name the company ($TICKER), the amount, the agency, and the implication.\n"
            "One post under 280 chars. No hashtags. No price targets."
        ), max_tokens=220)

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
        # Pull live crypto prices via cached Kraken/CoinGecko
        prices = {}
        try:
            from financial_data_client import get_crypto_prices as _gcp
            _cp = _gcp(["BTC", "ETH", "SOL"])
            for sym in ["BTC", "ETH", "SOL"]:
                if _cp.get(sym, {}).get("usd", 0):
                    prices[sym] = {
                        "price":     _cp[sym]["usd"],
                        "change_24h": round(_cp[sym].get("usd_24h_change", 0), 2),
                        "mcap":      0,  # Kraken doesn't provide mcap
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
        tv_ctx        = get_tv_signal_context(["BTC", "ETH", "SOL"]) if _TV_ACTIVE else ""

        extra_ctx = (
            (f"Macro Transport Signal:\n{flights_ctx}\n\n" if flights_ctx else "")
            + (f"Cross-Asset Macro:\n{macro_ctx}\n\n" if macro_ctx else "")
            + (f"Options Flow & Dark Pool:\n{uw_ctx}\n\n" if uw_ctx else "")
            + (f"{tv_ctx}\n\n" if tv_ctx else "")
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
            f"{_core_memory_section()}\n\n"
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
                max_tokens=350,
                system=OCTO_SYSTEM,
                messages=[{"role": "user", "content": explain_prompt}],
            )
            explanation = explain_resp.content[0].text.strip()
            # Trim to last complete sentence if over 280 chars
            if len(explanation) > 280:
                trimmed = explanation[:280]
                last_end = max(trimmed.rfind(". "), trimmed.rfind("! "), trimmed.rfind("? "))
                explanation = trimmed[:last_end + 1].strip() if last_end > 100 else trimmed[:277].strip() + "..."
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
            from financial_data_client import get_crypto_prices as _gcp
            _cp = _gcp(["BTC", "ETH", "SOL"])
            _fng_v = 50
            try:
                import requests as _r
                _fng_v = int(_r.get("https://api.alternative.me/fng/?limit=1", timeout=8).json()["data"][0]["value"])
            except Exception:
                pass
            _fng_lbl = "Extreme Fear" if _fng_v < 25 else ("Fear" if _fng_v < 45 else ("Neutral" if _fng_v < 55 else ("Greed" if _fng_v < 75 else "Extreme Greed")))
            context_parts.append(
                f"Live prices:\n"
                f"  BTC: ${_cp.get('BTC',{}).get('usd',0):,.0f} ({_cp.get('BTC',{}).get('usd_24h_change',0):+.1f}% 24h)\n"
                f"  ETH: ${_cp.get('ETH',{}).get('usd',0):,.0f} ({_cp.get('ETH',{}).get('usd_24h_change',0):+.1f}% 24h)\n"
                f"  SOL: ${_cp.get('SOL',{}).get('usd',0):,.2f} ({_cp.get('SOL',{}).get('usd_24h_change',0):+.1f}% 24h)\n"
                f"  Fear & Greed: {_fng_v}/100 ({_fng_lbl})"
            )
        except Exception:
            pass
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

        prompt = build_thread_prompt(topic, live_data_block) + _core_memory_section()

        raw = _haiku_generate(
            "", prompt, max_tokens=900
        )
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
            "strategy_monitor", "strategy_sunday", "thread", "ceo_research",
            "liquidation_radar", "range_scout", "xengage",
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
    elif args.mode == "liquidation_radar":
        mode_liquidation_radar()
    elif args.mode == "ceo_research":
        from octo_ceo import run_ceo_research, get_ceo_brief
        focus = args.ticker if args.ticker not in ("NVDA", "") else "general"
        print(f"[Runner] CEO research: focus={focus}")
        result = run_ceo_research(focus)
        if "actions" in result:
            print("[CEO] ACTIONS:")
            for i, a in enumerate(result.get("actions", []), 1):
                print(f"  {i}. {a}")
            print(f"[CEO] INSIGHT: {result.get('competitive_insight', '')}")
            print(f"[CEO] PHASE: {result.get('phase_assessment', '')}")
        else:
            print(f"[CEO] {result.get('raw', str(result))[:500]}")

    elif args.mode == "range_scout":
        from octo_range_scout import run_range_scout
        targets = [args.ticker.upper()] if args.ticker and args.ticker.upper() in ("BTC", "ETH", "SOL") else None
        run_range_scout(assets=targets, dry=False)

    elif args.mode == "xengage":
        from octo_x_engage import run_session as xengage_run
        xengage_run(dry_run=args.force)  # --force acts as dry-run for xengage

    elif args.mode == "spacex":
        from octo_spacex import check_spacex_ipo
        result = check_spacex_ipo(silent=False)
        if result.get("signal"):
            print(f"[SpaceX] IPO signal: {result['headline'][:100]}")
            print(f"[SpaceX] High-signal: {result['high_signal']}")
        else:
            print("[SpaceX] No IPO signals. SpaceX remains private.")

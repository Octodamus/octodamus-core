"""
octo_charts.py — Octodamus Visual Intelligence v3

Generates branded charts from Coinglass data for X posts and ACP reports.
Dark theme, bioluminescent neon palette, inline logo after every title.

Usage:
    from octo_charts import charts
    paths = charts.generate_all("BTC")
    path = charts.market_dashboard("BTC")
"""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("OctoCharts")

CHARTS_DIR = Path(__file__).parent / "charts"
CHARTS_DIR.mkdir(exist_ok=True)
LOGO_PATH = Path(__file__).parent / "octo_logo.png"

# ══════════════════════════════════════════════════════════════════════════════
# BRAND PALETTE v3 — MAXIMUM BRIGHTNESS
# ══════════════════════════════════════════════════════════════════════════════

C = {
    "bg": "#080c12", "bg_panel": "#0d1520",
    "grid": "#1c2d3d",            # slightly brighter grid
    "border": "#3a4f60",          # visible gray border on panels
    "title": "#00ffcc",
    "subtitle": "#8ecfdf",        # brighter subtitle
    "text": "#e0eaf2",            # brighter body text
    "text_dim": "#7d9aad",        # brighter axis labels
    "text_bright": "#ffffff",     # pure white for emphasis
    "value_label": "#ffffff",     # WHITE — all value annotations
    "teal": "#00ffcc", "cyan": "#00d4ff",
    "bull": "#00ff88", "bear": "#ff3366",
    "whale": "#aa66ff", "gold": "#ffd700",
    "watermark": "#1e3a4d",       # slightly brighter watermark
}

_logo_img = None

def _get_logo():
    global _logo_img
    if _logo_img is not None:
        return _logo_img
    if LOGO_PATH.exists():
        try:
            import matplotlib.image as mpimg
            _logo_img = mpimg.imread(str(LOGO_PATH))
        except Exception:
            _logo_img = False
    else:
        _logo_img = False
    return _logo_img

def _setup_style():
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    plt.rcParams.update({
        "figure.facecolor": C["bg"], "axes.facecolor": C["bg_panel"],
        "axes.edgecolor": C["border"],  # Gray border on all panels
        "axes.linewidth": 1.2,          # Thicker border
        "axes.labelcolor": C["text"],
        "axes.grid": True, "grid.color": C["grid"], "grid.alpha": 0.5,
        "grid.linewidth": 0.4, "grid.linestyle": "--",
        "text.color": C["text"],
        "xtick.color": C["text_dim"], "ytick.color": C["text_dim"],
        "xtick.labelsize": 9, "ytick.labelsize": 10,
        "legend.facecolor": C["bg_panel"], "legend.edgecolor": C["border"],
        "legend.fontsize": 9, "legend.labelcolor": C["text"],
        "figure.dpi": 200, "savefig.dpi": 200, "savefig.facecolor": C["bg"],
        "savefig.bbox": "tight", "savefig.pad_inches": 0.4,
        "font.family": "sans-serif", "font.size": 10,
    }); return plt


def _add_title_logo(fig, x, y, size=0.035):
    """Place a small inline logo image at (x, y) in figure coords."""
    logo = _get_logo()
    if logo is False:
        return
    # Size is width; height is proportional to logo aspect ratio (~1.25:1)
    w = size
    h = size * 1.3
    ax_logo = fig.add_axes([x, y - h + 0.005, w, h], zorder=10)
    ax_logo.imshow(logo)
    ax_logo.axis("off")


def _add_header(fig, title, subtitle=""):
    """Add title with inline logo emoji, subtitle, and branded footer."""
    fig.text(0.03, 0.97, title, fontsize=16, fontweight="bold", color=C["title"], va="top")
    # Inline logo right after title — ~0.013 per char at fontsize 16
    title_end_x = 0.03 + len(title) * 0.013
    _add_title_logo(fig, title_end_x + 0.008, 0.97, size=0.035)
    if subtitle:
        fig.text(0.03, 0.925, subtitle, fontsize=10, color=C["subtitle"], va="top")
    fig.text(0.5, 0.008, "OCTODAMUS  ·  Reading the Currents to see where the Money Swims  ·  octodamus.com",
             fontsize=7, color=C["watermark"], va="bottom", ha="center", alpha=0.8, fontweight="bold")


def _add_panel_title(ax, title, fig):
    """Add title to a subplot panel with inline logo."""
    ax.set_title("", pad=10)
    pos = ax.get_position()
    center_x = pos.x0 + pos.width / 2
    top_y = pos.y1 + 0.018
    fig.text(center_x, top_y, title, fontsize=11, fontweight="bold",
             color=C["teal"], ha="center", va="bottom")
    # Small logo after panel title — ~0.007 per char at fontsize 11
    title_half_w = len(title) * 0.007
    _add_title_logo(fig, center_x + title_half_w + 0.005, top_y + 0.008, size=0.018)


def _ts_to_dt(ts_ms):
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)

def _save(fig, name):
    path = CHARTS_DIR / f"{name}.png"; fig.savefig(path)
    import matplotlib.pyplot as plt; plt.close(fig); log.info(f"Chart saved: {path}"); return str(path)

def _fmt_money(val, d=1):
    if abs(val) >= 1e9: return f"${val/1e9:.{d}f}B"
    elif abs(val) >= 1e6: return f"${val/1e6:.{d}f}M"
    elif abs(val) >= 1e3: return f"${val/1e3:.0f}K"
    return f"${val:.0f}"


# ══════════════════════════════════════════════════════════════════════════════
# CHART 1: FUNDING RATES
# ══════════════════════════════════════════════════════════════════════════════

def funding_rate_chart(symbol="BTC"):
    from octo_coinglass import funding_rate_exchange, coins_markets
    plt = _setup_style()
    fr_data = funding_rate_exchange(symbol)
    if not isinstance(fr_data, list): return ""
    coin = next((c for c in fr_data if c.get("symbol") == symbol), fr_data[0])
    ml = coin.get("stablecoin_margin_list", [])
    if not ml: return ""
    mkts = coins_markets(); price = 0
    if isinstance(mkts, list):
        m = next((c for c in mkts if c.get("symbol") == symbol), {}); price = m.get("current_price", 0)
    valid = [(ex.get("exchange","?"), float(ex.get("funding_rate",0) or 0)*100) for ex in ml if ex.get("funding_rate",0)]
    valid.sort(key=lambda x: x[1]); names=[v[0] for v in valid]; rates=[v[1] for v in valid]

    fig, ax = plt.subplots(figsize=(11, 7)); fig.subplots_adjust(top=0.87, left=0.20, bottom=0.08)
    bar_colors = [C["bull"] if r < 0 else C["bear"] for r in rates]
    ax.barh(names, rates, color=bar_colors, height=0.55, alpha=0.85,
            edgecolor=[c+"60" for c in bar_colors], linewidth=0.5)
    ax.axvline(x=0, color=C["text_dim"], linewidth=1, alpha=0.6)
    # Bright white value labels
    for i, (v, name) in enumerate(zip(rates, names)):
        offset = 0.008 if v >= 0 else -0.008; ha = "left" if v >= 0 else "right"
        color = C["text_bright"]  # WHITE
        ax.text(v + offset, i, f"{v:+.4f}%", va="center", ha=ha,
                fontsize=8, color=color, fontweight="bold")
    ax.set_xlabel("Funding Rate (%)", fontsize=10, color=C["text"])
    avg = sum(rates)/len(rates)
    ax.axvline(x=avg, color=C["gold"], linewidth=1.2, linestyle="--", alpha=0.7)
    ax.text(avg, len(names)-0.5, f"AVG: {avg:+.4f}%", fontsize=9, color=C["gold"],
            fontweight="bold", ha="center")
    max_r=max(rates); min_r=min(rates)
    ax.text(0.98, 0.02, f"Most Bearish: {names[rates.index(max_r)]} ({max_r:+.4f}%)\n"
                         f"Most Bullish: {names[rates.index(min_r)]} ({min_r:+.4f}%)",
            transform=ax.transAxes, fontsize=9, color=C["text_bright"], ha="right", va="bottom",
            bbox=dict(boxstyle="round,pad=0.4", facecolor=C["bg"], edgecolor=C["border"], linewidth=1.2))
    _add_header(fig, f"{symbol} FUNDING RATES BY EXCHANGE",
                f"${price:,.0f} | Stablecoin Margin | Red = Longs Pay | Green = Shorts Pay")
    return _save(fig, f"{symbol.lower()}_funding_rates")


# ══════════════════════════════════════════════════════════════════════════════
# CHART 2: OPEN INTEREST
# ══════════════════════════════════════════════════════════════════════════════

def open_interest_chart(symbol="BTC"):
    from octo_coinglass import open_interest_exchange
    plt = _setup_style()
    oi_data = open_interest_exchange(symbol)
    if not isinstance(oi_data, list): return ""
    all_row = next((ex for ex in oi_data if ex.get("exchange") == "All"), {})
    total_oi = float(all_row.get("open_interest_usd", 0) or 0)
    filtered = [(ex.get("exchange","?"), float(ex.get("open_interest_usd",0) or 0),
                 float(ex.get("open_interest_change_percent_24h",0) or 0),
                 float(ex.get("open_interest_change_percent_1h",0) or 0))
                for ex in oi_data if ex.get("exchange") != "All" and float(ex.get("open_interest_usd",0) or 0) > 200_000_000]
    filtered.sort(key=lambda x: x[1], reverse=True); filtered = filtered[:12]
    names=[f[0] for f in filtered]; values=[f[1]/1e9 for f in filtered]
    chg_24h=[f[2] for f in filtered]

    fig, ax = plt.subplots(figsize=(11, 7)); fig.subplots_adjust(top=0.87, bottom=0.12)
    bar_colors = [C["bull"] if c > 0 else C["bear"] for c in chg_24h]
    ax.bar(names, values, color=bar_colors, alpha=0.8, width=0.6,
           edgecolor=[c+"40" for c in bar_colors], linewidth=0.8)
    for i, (v, c24) in enumerate(zip(values, chg_24h)):
        color = C["bull"] if c24 > 0 else C["bear"]
        # Bright white dollar amount
        ax.text(i, v + 0.12, f"${v:.1f}B", ha="center", va="bottom",
                fontsize=10, fontweight="bold", color=C["text_bright"])
        ax.text(i, v + 0.02, f"{c24:+.1f}%", ha="center", va="bottom",
                fontsize=8, color=color, fontweight="bold")
    ax.set_ylabel("Open Interest ($ Billion)", fontsize=10, color=C["text"])
    ax.tick_params(axis="x", rotation=35, labelsize=9)
    ax.text(0.98, 0.95, f"TOTAL OI: {_fmt_money(total_oi)}", transform=ax.transAxes,
            fontsize=13, fontweight="bold", color=C["teal"], ha="right", va="top",
            bbox=dict(boxstyle="round,pad=0.5", facecolor=C["bg"], edgecolor=C["teal"]+"40", linewidth=1.2))
    _add_header(fig, f"{symbol} OPEN INTEREST BY EXCHANGE",
                f"Total: {_fmt_money(total_oi)} | Color = 24h change direction")
    return _save(fig, f"{symbol.lower()}_open_interest")


# ══════════════════════════════════════════════════════════════════════════════
# CHART 3: LONG/SHORT RATIO
# ══════════════════════════════════════════════════════════════════════════════

def long_short_chart(symbol="BTC"):
    from octo_coinglass import long_short_ratio, top_long_short_ratio
    plt = _setup_style(); import matplotlib.dates as mdates
    ls_data = long_short_ratio(symbol, "4h"); top_data = top_long_short_ratio(symbol, "4h")
    if not isinstance(ls_data, list) or not ls_data: return ""
    ls_recent = ls_data[-60:]
    top_recent = top_data[-60:] if isinstance(top_data, list) and len(top_data) >= 60 else []
    times = [_ts_to_dt(d["time"]) for d in ls_recent]
    global_long = [d.get("global_account_long_percent", 50) for d in ls_recent]

    fig, ax = plt.subplots(figsize=(12, 6)); fig.subplots_adjust(top=0.85, bottom=0.12)
    ax.plot(times, global_long, color=C["teal"], linewidth=2.2,
            label=f"Global Retail ({global_long[-1]:.1f}%)", alpha=0.95, zorder=5)
    if top_recent:
        top_long = [d.get("top_account_long_percent", 50) for d in top_recent[-len(ls_recent):]]
        ax.plot(times, top_long, color=C["whale"], linewidth=2.2,
                label=f"Top Traders ({top_long[-1]:.1f}%)", alpha=0.9, linestyle="--", zorder=5)
    ax.axhspan(60, 80, alpha=0.06, color=C["bull"], zorder=1)
    ax.axhspan(20, 40, alpha=0.06, color=C["bear"], zorder=1)
    ax.axhline(y=50, color=C["text_dim"], linewidth=1, linestyle=":", alpha=0.5, zorder=2)
    ax.text(times[1], 67, "LONG HEAVY", fontsize=9, color=C["bull"], alpha=0.5, fontweight="bold")
    ax.text(times[1], 33, "SHORT HEAVY", fontsize=9, color=C["bear"], alpha=0.5, fontweight="bold")
    ax.fill_between(times, 50, global_long, where=[l > 50 for l in global_long],
                    alpha=0.08, color=C["bull"], zorder=1)
    ax.fill_between(times, 50, global_long, where=[l <= 50 for l in global_long],
                    alpha=0.08, color=C["bear"], zorder=1)
    # Bright white current value annotations
    ax.annotate(f"{global_long[-1]:.1f}%", xy=(times[-1], global_long[-1]), xytext=(15, 0),
                textcoords="offset points", fontsize=13, fontweight="bold", color=C["text_bright"],
                arrowprops=dict(arrowstyle="->", color=C["teal"], lw=1.5))
    if top_recent:
        ax.annotate(f"{top_long[-1]:.1f}%", xy=(times[-1], top_long[-1]), xytext=(15, -15),
                    textcoords="offset points", fontsize=13, fontweight="bold", color=C["text_bright"],
                    arrowprops=dict(arrowstyle="->", color=C["whale"], lw=1.5))
    ax.set_ylabel("Long %", fontsize=11); ax.set_ylim(30, 75)
    ax.legend(loc="upper left", framealpha=0.8)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d")); fig.autofmt_xdate()
    latest = global_long[-1]
    skew = "LONG-HEAVY" if latest > 55 else "SHORT-HEAVY" if latest < 45 else "BALANCED"
    skew_color = C["bull"] if latest > 55 else C["bear"] if latest < 45 else C["text"]
    ax.text(0.98, 0.95, f"SKEW: {skew}", transform=ax.transAxes, fontsize=13, fontweight="bold",
            color=skew_color, ha="right", va="top",
            bbox=dict(boxstyle="round,pad=0.4", facecolor=C["bg"], edgecolor=skew_color+"40", linewidth=1.2))
    _add_header(fig, f"{symbol} LONG/SHORT RATIO",
                "Binance | 4h | Teal = Global Retail | Purple = Top Traders (Whales)")
    return _save(fig, f"{symbol.lower()}_long_short")


# ══════════════════════════════════════════════════════════════════════════════
# CHART 4: TAKER FLOW
# ══════════════════════════════════════════════════════════════════════════════

def taker_flow_chart(symbol="BTC"):
    from octo_coinglass import taker_buy_sell
    plt = _setup_style(); import matplotlib.dates as mdates
    data = taker_buy_sell(symbol, "4h")
    if not isinstance(data, list) or not data: return ""
    recent = data[-48:]
    times = [_ts_to_dt(d["time"]) for d in recent]
    buys = [d.get("aggregated_buy_volume_usd", 0) / 1e6 for d in recent]
    sells = [d.get("aggregated_sell_volume_usd", 0) / 1e6 for d in recent]
    net = [b - s for b, s in zip(buys, sells)]
    buy_pcts = [b/(b+s)*100 if (b+s)>0 else 50 for b,s in zip(buys, sells)]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 7), height_ratios=[2, 1], sharex=True)
    fig.subplots_adjust(top=0.87, hspace=0.08, bottom=0.10)
    bar_w = 0.06
    ax1.bar(times, buys, color=C["bull"], alpha=0.75, width=bar_w,
            label=f"Buy Vol (avg ${sum(buys)/len(buys):.0f}M)")
    ax1.bar(times, [-s for s in sells], color=C["bear"], alpha=0.75, width=bar_w,
            label=f"Sell Vol (avg ${sum(sells)/len(sells):.0f}M)")
    ax1.axhline(y=0, color=C["text_dim"], linewidth=0.5)
    ax1.set_ylabel("Volume ($M)", fontsize=10); ax1.legend(loc="upper left", fontsize=8)
    latest_pct = buy_pcts[-1]
    pct_color = C["bull"] if latest_pct > 55 else C["bear"] if latest_pct < 45 else C["text_bright"]
    ax1.text(0.98, 0.95, f"BUY: {latest_pct:.0f}%", transform=ax1.transAxes, fontsize=14,
             fontweight="bold", color=pct_color, ha="right", va="top",
             bbox=dict(boxstyle="round,pad=0.4", facecolor=C["bg"], edgecolor=pct_color+"40", linewidth=1.2))
    net_colors = [C["bull"] if n > 0 else C["bear"] for n in net]
    ax2.bar(times, net, color=net_colors, alpha=0.85, width=bar_w)
    ax2.axhline(y=0, color=C["text_dim"], linewidth=0.5)
    ax2.set_ylabel("Net Delta ($M)", fontsize=9)
    cum_net = sum(net); cum_color = C["bull"] if cum_net > 0 else C["bear"]
    ax2.text(0.98, 0.90, f"CUM NET: {_fmt_money(cum_net*1e6)}", transform=ax2.transAxes,
             fontsize=10, fontweight="bold", color=C["text_bright"], ha="right", va="top")
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%b %d")); fig.autofmt_xdate()
    _add_header(fig, f"{symbol} TAKER BUY/SELL FLOW",
                "Binance | 4h | Who's aggressively hitting the book")
    return _save(fig, f"{symbol.lower()}_taker_flow")


# ══════════════════════════════════════════════════════════════════════════════
# CHART 5: LIQUIDATIONS
# ══════════════════════════════════════════════════════════════════════════════

def liquidation_chart(symbol="BTC"):
    from octo_coinglass import liquidation_history
    plt = _setup_style(); import matplotlib.dates as mdates
    data = liquidation_history(symbol, "4h")
    if not isinstance(data, list) or not data: return ""
    recent = data[-60:]
    times = [_ts_to_dt(d["time"]) for d in recent]
    long_liqs = [d.get("aggregated_long_liquidation_usd", 0) / 1e6 for d in recent]
    short_liqs = [d.get("aggregated_short_liquidation_usd", 0) / 1e6 for d in recent]

    fig, ax = plt.subplots(figsize=(12, 6)); fig.subplots_adjust(top=0.85, bottom=0.12)
    bar_w = 0.12
    ax.bar(times, long_liqs, color=C["bear"], alpha=0.8, width=bar_w,
           label=f"Long Liqs ({_fmt_money(sum(long_liqs)*1e6)})")
    ax.bar(times, [-s for s in short_liqs], color=C["bull"], alpha=0.8, width=bar_w,
           label=f"Short Liqs ({_fmt_money(sum(short_liqs)*1e6)})")
    ax.axhline(y=0, color=C["text_dim"], linewidth=0.5)
    ax.set_ylabel("Liquidations ($M)", fontsize=10)
    ax.legend(loc="upper left", fontsize=9)
    # Spike annotations in bright white
    avg_long = sum(long_liqs)/len(long_liqs) if long_liqs else 1
    avg_short = sum(short_liqs)/len(short_liqs) if short_liqs else 1
    for i, (ll, sl) in enumerate(zip(long_liqs, short_liqs)):
        if ll > avg_long * 3 and ll > 1:
            ax.annotate(f"${ll:.1f}M", xy=(times[i], ll), xytext=(0, 8),
                        textcoords="offset points", fontsize=8, color=C["text_bright"],
                        fontweight="bold", ha="center")
        if sl > avg_short * 3 and sl > 1:
            ax.annotate(f"${sl:.1f}M", xy=(times[i], -sl), xytext=(0, -12),
                        textcoords="offset points", fontsize=8, color=C["text_bright"],
                        fontweight="bold", ha="center")
    total_long=sum(long_liqs); total_short=sum(short_liqs)
    dominant = "LONGS" if total_long > total_short else "SHORTS"
    dom_color = C["bear"] if total_long > total_short else C["bull"]
    ax.text(0.98, 0.95, f"{dominant} HURT MORE", transform=ax.transAxes, fontsize=12,
            fontweight="bold", color=dom_color, ha="right", va="top",
            bbox=dict(boxstyle="round,pad=0.4", facecolor=C["bg"], edgecolor=dom_color+"40", linewidth=1.2))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d")); fig.autofmt_xdate()
    _add_header(fig, f"{symbol} LIQUIDATION MAP",
                "Binance | 4h | Red = Longs wiped | Green = Shorts wiped")
    return _save(fig, f"{symbol.lower()}_liquidations")


# ══════════════════════════════════════════════════════════════════════════════
# CHART 6: COMPOSITE DASHBOARD — "MARKET INTELLIGENCE"
# ══════════════════════════════════════════════════════════════════════════════

def market_dashboard(symbol="BTC"):
    from octo_coinglass import (funding_rate_exchange, open_interest_exchange,
        long_short_ratio, taker_buy_sell, coins_markets, liquidation_history)
    plt = _setup_style(); import matplotlib.dates as mdates

    fig, axes = plt.subplots(2, 2, figsize=(16, 10))
    fig.subplots_adjust(top=0.87, hspace=0.38, wspace=0.28, bottom=0.06)

    mkts = coins_markets()
    coin = next((c for c in mkts if c.get("symbol")==symbol),{}) if isinstance(mkts,list) else {}
    price=coin.get("current_price",0); oi_usd=coin.get("open_interest_usd",0) or 0
    fr_avg=coin.get("avg_funding_rate_by_oi",0) or 0

    # ── [0,0] Funding Rates ───────────────────────────────────────────────
    ax = axes[0][0]
    fr_data = funding_rate_exchange(symbol)
    if isinstance(fr_data, list) and fr_data:
        coin_fr = next((c for c in fr_data if c.get("symbol")==symbol), fr_data[0])
        ml = coin_fr.get("stablecoin_margin_list", [])
        top_ex = sorted(ml, key=lambda x: abs(x.get("funding_rate",0) or 0), reverse=True)[:10]
        names=[ex.get("exchange","?")[:10] for ex in top_ex]
        rates=[float(ex.get("funding_rate",0) or 0)*100 for ex in top_ex]
        colors=[C["bear"] if r>0 else C["bull"] for r in rates]
        ax.barh(names, rates, color=colors, height=0.5, alpha=0.85)
        ax.axvline(x=0, color=C["text_dim"], linewidth=0.5)
        for i,v in enumerate(rates):
            ax.text(v+(0.005 if v>=0 else -0.005), i, f"{v:+.3f}%", va="center",
                    ha="left" if v>=0 else "right", fontsize=7,
                    color=C["text_bright"], fontweight="bold")
    ax.tick_params(labelsize=8)
    _add_panel_title(ax, "Funding Rates (%)", fig)

    # ── [0,1] OI by Exchange ──────────────────────────────────────────────
    ax = axes[0][1]
    oi_data = open_interest_exchange(symbol)
    if isinstance(oi_data, list) and oi_data:
        oi_f=[ex for ex in oi_data if ex.get("exchange")!="All"
              and float(ex.get("open_interest_usd",0) or 0)>500_000_000]
        oi_s=sorted(oi_f, key=lambda x: float(x.get("open_interest_usd",0) or 0), reverse=True)[:8]
        names=[ex.get("exchange","?")[:8] for ex in oi_s]
        vals=[float(ex.get("open_interest_usd",0) or 0)/1e9 for ex in oi_s]
        chgs=[float(ex.get("open_interest_change_percent_24h",0) or 0) for ex in oi_s]
        colors=[C["bull"] if c>0 else C["bear"] for c in chgs]
        ax.bar(names, vals, color=colors, alpha=0.8)
        for i,(v,c) in enumerate(zip(vals,chgs)):
            chg_color = C["bull"] if c > 0 else C["bear"]
            ax.text(i, v+0.08, f"${v:.1f}B", ha="center", fontsize=7,
                    color=C["text_bright"], fontweight="bold")
            ax.text(i, v+0.01, f"{c:+.1f}%", ha="center", fontsize=6,
                    color=chg_color, fontweight="bold")
        ax.tick_params(axis="x", rotation=40, labelsize=7)
    _add_panel_title(ax, "Open Interest ($B)", fig)

    # ── [1,0] L/S Ratio ──────────────────────────────────────────────────
    ax = axes[1][0]
    ls_data = long_short_ratio(symbol, "4h")
    if isinstance(ls_data, list) and ls_data:
        ls_r=ls_data[-48:]; times=[_ts_to_dt(d["time"]) for d in ls_r]
        longs=[d.get("global_account_long_percent",50) for d in ls_r]
        ax.plot(times, longs, color=C["teal"], linewidth=2)
        ax.axhline(y=50, color=C["text_dim"], linewidth=0.5, linestyle=":")
        ax.fill_between(times,50,longs,where=[l>50 for l in longs],alpha=0.1,color=C["bull"])
        ax.fill_between(times,50,longs,where=[l<=50 for l in longs],alpha=0.1,color=C["bear"])
        ax.set_ylim(35, 75)
        ax.text(0.97, 0.92, f"{longs[-1]:.1f}%", transform=ax.transAxes,
                fontsize=16, fontweight="bold", color=C["text_bright"], ha="right")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    _add_panel_title(ax, "Long/Short Ratio (%)", fig)

    # ── [1,1] Liquidations ────────────────────────────────────────────────
    ax = axes[1][1]
    liq_data = liquidation_history(symbol, "4h")
    if isinstance(liq_data, list) and liq_data:
        liq_r=liq_data[-48:]; times=[_ts_to_dt(d["time"]) for d in liq_r]
        ll=[d.get("aggregated_long_liquidation_usd",0)/1e6 for d in liq_r]
        sl=[-(d.get("aggregated_short_liquidation_usd",0)/1e6) for d in liq_r]
        ax.bar(times,ll,color=C["bear"],alpha=0.8,width=0.12,label="Long Liqs")
        ax.bar(times,sl,color=C["bull"],alpha=0.8,width=0.12,label="Short Liqs")
        ax.axhline(y=0,color=C["text_dim"],linewidth=0.5)
        ax.legend(fontsize=7, loc="upper left")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    _add_panel_title(ax, "Liquidations ($M)", fig)

    # ── Dashboard header — "MARKET INTELLIGENCE" ─────────────────────────
    fr_dir = "LONGS PAY" if fr_avg > 0 else "SHORTS PAY"
    header = f"${price:,.0f}  |  OI: {_fmt_money(oi_usd)}  |  FR: {fr_avg*100:+.3f}% ({fr_dir})"
    fig.text(0.03, 0.97, "MARKET INTELLIGENCE", fontsize=20,
             fontweight="bold", color=C["teal"], va="top")
    # Inline logo after dashboard title — ~0.014 per char at fontsize 20
    _add_title_logo(fig, 0.03 + len("MARKET INTELLIGENCE") * 0.014 + 0.01, 0.975, size=0.042)
    fig.text(0.03, 0.925, header, fontsize=12, color=C["text_bright"], va="top")
    fig.text(0.5, 0.008, "OCTODAMUS  ·  Reading the Currents  ·  octodamus.com  ·  @octodamusai",
             fontsize=7, color=C["watermark"], va="bottom", ha="center", alpha=0.8, fontweight="bold")

    return _save(fig, f"{symbol.lower()}_dashboard")


# ══════════════════════════════════════════════════════════════════════════════
# CONVENIENCE
# ══════════════════════════════════════════════════════════════════════════════

def generate_all(symbol="BTC"):
    results = {}
    for name, fn in {"funding_rates": funding_rate_chart, "open_interest": open_interest_chart,
                     "long_short": long_short_chart, "taker_flow": taker_flow_chart,
                     "liquidations": liquidation_chart, "dashboard": market_dashboard}.items():
        try:
            path = fn(symbol)
            if path: results[name] = path; print(f"  OK    {name}: {path}")
            else: print(f"  SKIP  {name}: no data")
        except Exception as e: print(f"  ERROR {name}: {e}"); import traceback; traceback.print_exc()
    return results

class OctoCharts:
    funding_rate_chart=staticmethod(funding_rate_chart); open_interest_chart=staticmethod(open_interest_chart)
    long_short_chart=staticmethod(long_short_chart); taker_flow_chart=staticmethod(taker_flow_chart)
    liquidation_chart=staticmethod(liquidation_chart); market_dashboard=staticmethod(market_dashboard)
    generate_all=staticmethod(generate_all)
charts = OctoCharts()

if __name__ == "__main__":
    import sys; logging.basicConfig(level=logging.INFO)
    symbol = sys.argv[1] if len(sys.argv) > 1 else "BTC"
    print(f"\n{'='*60}\n  OCTODAMUS CHARTS v3 — {symbol}\n{'='*60}\n")
    print(f"  Output: {CHARTS_DIR}")
    print(f"  Logo: {'FOUND' if LOGO_PATH.exists() else 'MISSING — place octo_logo.png in project dir'}\n")
    results = generate_all(symbol)
    print(f"\n{'='*60}\n  Generated {len(results)} charts\n{'='*60}")

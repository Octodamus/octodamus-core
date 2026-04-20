"""
generate_mindmap_pdf.py -- Octodamus Architecture Mind Map
Whitepaper style: black & white, clean, 8.5x11 landscape.
"""

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
from matplotlib import rcParams

rcParams["font.family"] = "DejaVu Sans"

# ── Canvas ────────────────────────────────────────────────────────────────────
W, H = 28, 18
fig, ax = plt.subplots(figsize=(W, H))
ax.set_xlim(0, W)
ax.set_ylim(0, H)
ax.axis("off")
fig.patch.set_facecolor("white")
ax.set_facecolor("white")

# ── Primitives ────────────────────────────────────────────────────────────────

def draw_node(x, y, title, sub="", fill="#f4f4f4", border="#333333",
              lw=1.0, ts=7.8, ss=6.0, min_w=2.8):
    tw = len(title) * ts * 0.012 + 0.4
    sw = len(sub)   * ss * 0.012 + 0.4
    w  = max(tw, sw, min_w)
    h  = 0.46 if not sub else 0.64
    box = FancyBboxPatch((x - w/2, y - h/2), w, h,
                         boxstyle="round,pad=0.06",
                         facecolor=fill, edgecolor=border,
                         linewidth=lw, zorder=3)
    ax.add_patch(box)
    if sub:
        ax.text(x, y + 0.13, title, ha="center", va="center",
                fontsize=ts, fontweight="bold", color="#111111", zorder=4)
        ax.text(x, y - 0.15, sub, ha="center", va="center",
                fontsize=ss, color="#555555", style="italic", zorder=4)
    else:
        ax.text(x, y, title, ha="center", va="center",
                fontsize=ts, fontweight="bold", color="#111111", zorder=4)


def draw_hub(x, y, title, sub="", ts=11, ss=7.5):
    tw = len(title) * ts * 0.013 + 0.7
    w  = max(tw, 2.5)
    h  = 0.72 if not sub else 1.0
    box = FancyBboxPatch((x - w/2, y - h/2), w, h,
                         boxstyle="round,pad=0.1",
                         facecolor="#111111", edgecolor="#000000",
                         linewidth=2.2, zorder=3)
    ax.add_patch(box)
    if sub:
        ax.text(x, y + 0.17, title, ha="center", va="center",
                fontsize=ts, fontweight="bold", color="white", zorder=4)
        ax.text(x, y - 0.20, sub, ha="center", va="center",
                fontsize=ss, color="#aaaaaa", style="italic", zorder=4)
    else:
        ax.text(x, y, title, ha="center", va="center",
                fontsize=ts, fontweight="bold", color="white", zorder=4)


def wire(x1, y1, x2, y2, lw=0.85, ls="-", alpha=0.38):
    ax.plot([x1, x2], [y1, y2], color="#000000", linewidth=lw,
            linestyle=ls, zorder=1, alpha=alpha)


def leaves(hub_x, hub_y, items, leaf_x, spacing=0.75, ts=7.2, ss=5.8):
    """Vertical column of leaf nodes at leaf_x, connected to hub."""
    n   = len(items)
    ys  = [hub_y + (n - 1) / 2 * spacing - i * spacing for i in range(n)]
    for (title, sub), y in zip(items, ys):
        wire(hub_x, hub_y, leaf_x, y, lw=0.7, alpha=0.32)
        draw_node(leaf_x, y, title, sub, ts=ts, ss=ss)


def spoke(cx, cy, hx, hy, r_core=1.12, r_hub=0.55):
    """Draw spoke from core edge to hub edge."""
    import math
    dx, dy  = hx - cx, hy - cy
    dist    = math.hypot(dx, dy)
    ux, uy  = dx / dist, dy / dist
    wire(cx + ux * r_core, cy + uy * r_core,
         hx - ux * r_hub,  hy - uy * r_hub,
         lw=1.9, alpha=0.65)


# ═════════════════════════════════════════════════════════════════════════════
# POSITIONS  — symmetric left / right, 3 hubs each side
#  Canvas: 28 × 18   Core: (14, 10)
#
#  LEFT  hubs x=8.3          RIGHT hubs x=19.7
#   BRAIN      y=15.5          SOUL       y=15.5
#   MEMORY     y=10.0          SIGNALS    y=10.0
#   DISTRO     y= 4.5          OCTOBOTO   y= 4.5
#
#  INFRA (bottom center)  y=2.0, runner node y=7.5
# ═════════════════════════════════════════════════════════════════════════════

CX, CY  = 14.0, 10.0
LX, RX  = 8.3,  19.7        # hub columns
LLX, RLX = 2.2, 25.8       # leaf columns
BY1, BY2, BY3 = 15.5, 10.0, 4.5

# ── CORE ──────────────────────────────────────────────────────────────────────
core_c = plt.Circle((CX, CY), 1.12, facecolor="#111111",
                    edgecolor="#000000", linewidth=2.5, zorder=3)
ax.add_patch(core_c)
ax.text(CX, CY + 0.23, "OCTODAMUS", ha="center", va="center",
        fontsize=16, fontweight="bold", color="white", zorder=4)
ax.text(CX, CY - 0.30, "AI Market Oracle", ha="center", va="center",
        fontsize=8, color="#aaaaaa", style="italic", zorder=4)

# spokes from core to each hub
for hx, hy in [(LX, BY1),(LX, BY2),(LX, BY3),(RX, BY1),(RX, BY2),(RX, BY3)]:
    spoke(CX, CY, hx, hy)

# ── 1. BRAIN (top-left) ───────────────────────────────────────────────────────
draw_hub(LX, BY1, "BRAIN", "data/brain.md  ·  octo_calls.py")

leaves(LX, BY1, [
    ("octo_personality.py",      "Central voice, knowledge & identity module"),
    ("Signal Post-Mortems",      "brain.md — WIN/LOSS analysis after every call"),
    ("Pattern Context",          "_get_pattern_context() — historical bias lookup"),
    ("Edge Score",               "Bull-bear consensus across active signal feeds"),
    ("Calibration Layer",        "Per-confidence-tier bias correction (boto)"),
    ("Oracle Scorecard",         "Win rate, streak, open calls — /tools/scorecard"),
], leaf_x=LLX, spacing=0.80)

# ── 2. MEMORY (middle-left) ───────────────────────────────────────────────────
draw_hub(LX, BY2, "MEMORY", "Persistent knowledge across sessions")

leaves(LX, BY2, [
    ("MEMORY.md",                ".claude/ index — loaded every conversation"),
    ("feedback_*.md",            "User corrections & confirmed approaches"),
    ("project_*.md",             "Active initiatives, deadlines, motivation"),
    ("user_*.md",                "Role, preferences, expertise level"),
    ("octodamus_memory.json",    "Telegram per-user conversation state"),
    ("data/ceo_memory.json",     "CEO research log — last 50 entries"),
    ("data/brain.md",            "Oracle learning — what every call taught"),
], leaf_x=LLX, spacing=0.73)

# ── 3. DISTRIBUTION (bottom-left) ─────────────────────────────────────────────
draw_hub(LX, BY3, "DISTRIBUTION", "Audience + Revenue engine")

leaves(LX, BY3, [
    ("X / Twitter",              "octo_x_poster.py — scheduled oracle posts"),
    ("Telegram Bot",             "telegram_bot.py — private AI workspace"),
    ("MCP Server",               "Smithery / run.tools — AI agent sales team"),
    ("10 Free Tools",            "api.octodamus.com — email gate on 7 of 10"),
    ("OctoIntel Weekly",         "Newsletter launch July 2026 — Beehiiv platform"),
    ("Datarade / Snowflake",     "Alt data marketplace listings (B2B)"),
], leaf_x=LLX, spacing=0.80)

# ── 4. SOUL (top-right) ───────────────────────────────────────────────────────
draw_hub(RX, BY1, "SOUL", "octo_personality.py  ·  soul_dashboard.html")

leaves(RX, BY1, [
    ("Character Anchors",        "McGuane, Druckenmiller, Livermore, Taleb, Tool"),
    ("BTC Cycle Theory",         "1065d bull / 365d bear — bottom Oct 5 2026"),
    ("Bitcoin Thermodynamics",   "Entropy engine, 160-204 TWh/yr, sound money"),
    ("OctoBoto Context",         "Oracle vs bot — hard identity separation"),
    ("Artist DNA",               "soul_dashboard.html — 121 artists, music genome"),
    ("Auto-Update Rule",         "New knowledge → personality.py → all prompts"),
], leaf_x=RLX, spacing=0.80)

# ── 5. SIGNALS (middle-right) ─────────────────────────────────────────────────
draw_hub(RX, BY2, "SIGNALS", "27 live data feeds — injected into every prompt")

leaves(RX, BY2, [
    ("Aviation Volume",          "octo_flights.py — OpenSky global aircraft count"),
    ("TSA Throughput",           "US checkpoint passengers, 7-day rolling average"),
    ("Macro FRED",               "Yield curve, DXY, SPX, VIX, M2 — 5 FRED series"),
    ("Funding Rate",             "Coinglass — BTC / ETH perpetual funding rate"),
    ("Open Interest",            "Coinglass — OI in USD, spot vs perp divergence"),
    ("Options Flow",             "Unusual Whales — sweeps + dark pool (active)"),
    ("Congress Trades",          "QuiverQuant — smart money legislative signal"),
    ("Firecrawl Intel",          "Web scrape — news, earnings, competitor data"),
    ("Fear & Greed",             "CoinGecko composite crypto sentiment index"),
], leaf_x=RLX, spacing=0.67)

# ── 6. OCTOBOTO (bottom-right) ────────────────────────────────────────────────
draw_hub(RX, BY3, "OCTOBOTO", "Polymarket AI trader  →  Copytrading platform")

leaves(RX, BY3, [
    ("octo_boto_clob.py",        "V2 CLOB order engine — EV filter, Kelly sizing"),
    ("octo_boto_math.py",        "Expected value, position size, bankroll mgmt"),
    ("Oracle Bridge",            "Trades → oracle calls, post-mortems on close"),
    ("AutoResolve Engine",       "Gamma API — official + price-based resolution"),
    ("Post-Mortem Loop",         "Haiku writes analysis on every WIN / LOSS"),
    ("Copytrading Vision",       "Deposit capital → OctoBotoAI manages + takes %"),
], leaf_x=RLX, spacing=0.73)

# ── 7. INFRASTRUCTURE (bottom center) ────────────────────────────────────────
IFX, IFY = CX, 2.2
wire(CX, CY - 1.12, IFX, IFY + 0.55, lw=1.9, alpha=0.65)
draw_hub(IFX, IFY, "INFRASTRUCTURE", "Always-on services — Windows Task Scheduler (23 tasks)", ts=10)

infra_items = [
    ("octodamus_runner.py",  "--mode flags, all post types"),
    ("octo_api_server.py",   "REST API :8000, Cloudflare tunnel"),
    ("octo_acp_worker.py",   "Virtuals AI / ACP protocol"),
    ("telegram_bot.py",      "Auto-restart task, Octodamus-Telegram"),
    ("GDrive Backup",        "Full repo zip every 4h, octo_gdrive.py"),
]
n_inf = len(infra_items)
xs_inf = [5.2 + i * 4.7 for i in range(n_inf)]
for (title, sub), xi in zip(infra_items, xs_inf):
    wire(IFX, IFY - 0.55, xi, 0.82, lw=0.7, alpha=0.32)
    draw_node(xi, 0.82, title, sub, ts=6.8, ss=5.5, min_w=2.5)

# ── Runner node (center, between core and infra) ──────────────────────────────
# infra spoke already drawn above
draw_node(CX, 5.9, "octodamus_runner.py",
          "Entry point — all --mode flags routed here",
          fill="#ebebeb", border="#555555", lw=1.4, ts=8.0, ss=6.2)
wire(CX, CY - 1.12, CX, 6.22, lw=1.0, ls="--", alpha=0.4)
wire(CX, 5.58, CX, IFY + 0.55, lw=1.0, ls="--", alpha=0.4)

# ── TITLE ─────────────────────────────────────────────────────────────────────
ax.text(CX, 17.55, "OCTODAMUS  —  System Architecture",
        ha="center", va="center",
        fontsize=20, fontweight="bold", color="#000000")
ax.text(CX, 17.1,
        "Brain  ·  Memory  ·  Soul  ·  Signals  ·  OctoBoto  ·  Distribution  ·  Infrastructure",
        ha="center", va="center", fontsize=9.5, color="#555555")
ax.plot([0.8, 27.2], [16.78, 16.78], color="#000000", linewidth=0.7, alpha=0.35)

# ── LEGEND ────────────────────────────────────────────────────────────────────
lx, ly = 11.0, 16.35
ax.text(lx - 0.15, ly, "LEGEND", fontsize=7.5, fontweight="bold", color="#000000")
for i, (sym, desc) in enumerate([
    ("■  Hub node",    "Major system / subsystem"),
    ("□  Leaf node",   "File, module, or concept"),
    ("———",            "Data / control flow"),
    ("- - -",          "Logical linkage"),
]):
    ax.text(lx, ly - 0.3 - i * 0.27, sym,  fontsize=6.5, color="#111111")
    ax.text(lx + 1.15, ly - 0.3 - i * 0.27, desc, fontsize=6.2,
            color="#666666", style="italic")

ax.text(27.7, 0.18, "octodamus.com", ha="right", va="center",
        fontsize=6.5, color="#999999")
ax.text(0.3,  0.18, "v2026.04",      ha="left",  va="center",
        fontsize=6.5, color="#999999")

plt.tight_layout(pad=0.15)

out = r"C:\Users\walli\Desktop\octodamus_mindmap.pdf"
plt.savefig(out, format="pdf", dpi=200, bbox_inches="tight", facecolor="white")
print(f"Saved: {out}")
plt.close()

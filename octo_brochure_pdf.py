"""
octo_brochure_pdf.py — Octodamus One-Sheet Brochure PDF
Single A4/letter page. Print-ready. Dark background.
"""

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.platypus.flowables import Flowable
from reportlab.pdfgen import canvas
from pathlib import Path
import os

# ── Colours ──────────────────────────────────────────────────────────────────
BG       = colors.HexColor('#07090f')
SURFACE  = colors.HexColor('#0d1120')
BORDER   = colors.HexColor('#1e2d45')
BRIGHT   = colors.HexColor('#e8f4ff')
PULSE    = colors.HexColor('#22d3ee')
ACCENT   = colors.HexColor('#3b82f6')
GREEN    = colors.HexColor('#34d399')
YELLOW   = colors.HexColor('#fbbf24')
PURPLE   = colors.HexColor('#a78bfa')
MUTED    = colors.HexColor('#6b8fa8')
DIMMED   = colors.HexColor('#2a3a52')
WHITE    = colors.HexColor('#ffffff')

LOGO_PATH    = r'C:\Users\walli\Downloads\Octo_NEW_Logo_400x400.jpg'
LOGO_CIRCLE  = r'C:\Users\walli\octodamus\octo_logo_circle.png'
OUT_PATH     = r'C:\Users\walli\octodamus\octodamus_brochure.pdf'

W, H = letter  # 8.5 × 11 in


# ── Custom Flowables ─────────────────────────────────────────────────────────

class FilledRect(Flowable):
    """A filled rectangle — used as a section background."""
    def __init__(self, width, height, fill=SURFACE, stroke=BORDER, radius=4):
        super().__init__()
        self.width = width
        self.height = height
        self.fill = fill
        self.stroke = stroke
        self.radius = radius

    def draw(self):
        c = self.canv
        c.setFillColor(self.fill)
        c.setStrokeColor(self.stroke)
        c.setLineWidth(0.5)
        c.roundRect(0, 0, self.width, self.height, self.radius, fill=1, stroke=1)


class FullWidthBackground(Flowable):
    """Draws a dark background behind the entire page (used once at top)."""
    def __init__(self, width, height):
        super().__init__()
        self.width = width
        self.height = height

    def draw(self):
        c = self.canv
        c.setFillColor(BG)
        c.rect(0, 0, self.width, self.height, fill=1, stroke=0)


# ── Style factory ────────────────────────────────────────────────────────────

def S(name, **kw):
    defaults = dict(
        fontName='Helvetica', fontSize=9, leading=13,
        textColor=BRIGHT, spaceAfter=0, spaceBefore=0,
    )
    defaults.update(kw)
    return ParagraphStyle(name, **defaults)


# ── Canvas background callback ────────────────────────────────────────────────

def dark_background(c: canvas.Canvas, doc):
    c.saveState()
    c.setFillColor(BG)
    c.rect(0, 0, W, H, fill=1, stroke=0)
    # Subtle top gradient bar
    c.setFillColor(colors.HexColor('#0a1428'))
    c.rect(0, H - 0.85 * inch, W, 0.85 * inch, fill=1, stroke=0)
    c.restoreState()


# ── Build ─────────────────────────────────────────────────────────────────────

def make_circle_logo(src: str, dst: str):
    """Crop logo to circle with transparent background, save as PNG."""
    from PIL import Image, ImageDraw
    img = Image.open(src).convert("RGBA")
    size = img.size
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0, size[0], size[1]), fill=255)
    result = Image.new("RGBA", size, (0, 0, 0, 0))
    result.paste(img, mask=mask)
    result.save(dst, "PNG")


def build():
    make_circle_logo(LOGO_PATH, LOGO_CIRCLE)

    doc = SimpleDocTemplate(
        OUT_PATH,
        pagesize=letter,
        leftMargin=0.45 * inch,
        rightMargin=0.45 * inch,
        topMargin=0.35 * inch,
        bottomMargin=0.30 * inch,
    )

    story = []
    CW = W - 0.90 * inch  # content width

    # ── HEADER ────────────────────────────────────────────────────────────────
    # Title | Logo | Tagline
    from reportlab.platypus import Image as RLImage
    LOGO_SIZE = 0.52 * inch  # small logo next to title

    logo_src = LOGO_CIRCLE if Path(LOGO_CIRCLE).exists() else LOGO_PATH
    logo_img = RLImage(logo_src, width=LOGO_SIZE, height=LOGO_SIZE) if Path(LOGO_PATH).exists() else Spacer(LOGO_SIZE, LOGO_SIZE)

    logo_col_w  = LOGO_SIZE + 0.14 * inch
    title_col_w = CW * 0.44
    tag_col_w   = CW - title_col_w - logo_col_w

    header_data = [[
        Paragraph('<b><font color="#22d3ee" size="24">OCTODAMUS</font></b>',
                  S('h1', alignment=TA_LEFT, leading=28)),
        Paragraph(
            '<font color="#6b8fa8" size="8">MARKET INTELLIGENCE PLATFORM</font><br/>'
            '<font color="#e8f4ff" size="9">Real-time signals for humans and AI agents</font>',
            S('htag', alignment=TA_CENTER, leading=13)
        ),
        logo_img,
    ]]
    header_table = Table(header_data, colWidths=[title_col_w, tag_col_w, logo_col_w])
    header_table.setStyle(TableStyle([
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN',         (2, 0), (2, -1), 'RIGHT'),
        ('BACKGROUND',    (0, 0), (-1, -1), colors.HexColor('#0a1428')),
        ('TOPPADDING',    (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('LEFTPADDING',   (0, 0), (0, -1), 14),
        ('LEFTPADDING',   (1, 0), (1, -1), 8),
        ('RIGHTPADDING',  (1, 0), (1, -1), 8),
        ('RIGHTPADDING',  (2, 0), (2, -1), 10),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 0.10 * inch))

    # ── HERO DESCRIPTION ─────────────────────────────────────────────────────
    hero = Table([[
        Paragraph(
            '<font color="#e8f4ff" size="10.5"><b>Octodamus</b> is an AI-powered market intelligence system that monitors '
            'crypto derivatives, macro flows, prediction markets, and on-chain activity 24/7 — '
            'then synthesises everything into actionable signals. Its data is available to '
            '<b>human traders</b> via Telegram, to <b>autonomous AI agents</b> via REST API and x402 '
            'pay-per-call, and to any <b>LLM or coding tool</b> as a native MCP server.</font>',
            S('hero', leading=16, alignment=TA_LEFT)
        )
    ]], colWidths=[CW])
    hero.setStyle(TableStyle([
        ('BACKGROUND',    (0, 0), (-1, -1), SURFACE),
        ('ROUNDEDCORNERS', [6]),
        ('LEFTPADDING',   (0, 0), (-1, -1), 14),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 14),
        ('TOPPADDING',    (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
        ('BOX',           (0, 0), (-1, -1), 0.5, BORDER),
        ('LINEBEFORE',    (0, 0), (0, -1), 3, PULSE),
    ]))
    story.append(hero)
    story.append(Spacer(1, 0.12 * inch))

    # ── THREE COLUMNS: What it tracks / Who it serves / Data quality ─────────
    col_w = (CW - 0.16 * inch) / 3

    def col_block(title, colour, items):
        header = Paragraph(f'<b><font color="{colour}" size="8">{title}</font></b>',
                           S('ch', leading=11))
        rows = [[header]]
        for item in items:
            rows.append([Paragraph(f'<font color="#e8f4ff" size="8.5">&#8226; {item}</font>',
                                   S('ci', leading=12, leftIndent=2))])
        t = Table(rows, colWidths=[col_w])
        t.setStyle(TableStyle([
            ('BACKGROUND',    (0, 0), (-1, -1), SURFACE),
            ('BOX',           (0, 0), (-1, -1), 0.5, BORDER),
            ('TOPPADDING',    (0, 0), (-1, 0), 8),
            ('BOTTOMPADDING', (0, -1), (-1, -1), 8),
            ('TOPPADDING',    (0, 1), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 1), (-1, -1), 3),
            ('LEFTPADDING',   (0, 0), (-1, -1), 10),
            ('RIGHTPADDING',  (0, 0), (-1, -1), 8),
            ('LINEBEFORE',    (0, 0), (0, -1), 2.5, colors.HexColor(colour)),
        ]))
        return t

    tracks_col = col_block('#22d3ee', '#22d3ee', [
        'BTC / ETH / SOL perpetuals',
        'Open interest + funding rates',
        'Liquidation heatmaps',
        'Deribit options & IV surface',
        'Polymarket prediction markets',
        'Fear & Greed index',
        'On-chain whale flows',
        'Congress stock disclosures',
        'Macro calendar & CPI/FOMC',
        'NVDA / TSLA / SPY equities',
        'X/Twitter sentiment scoring',
        'Builder & VC activity (Base)',
    ])

    serves_col = col_block('#34d399', '#34d399', [
        'Traders — Telegram daily brief',
        'AI agents — REST API + x402',
        'LLM tools — MCP server',
        'Developers — free tier key',
        'Quant — Kelly-sized Poly edges',
        'Builders — ERC-8004 identity',
    ])

    quality_col = col_block('#fbbf24', '#fbbf24', [
        '27 live feeds, no synthetic data',
        'Signals scored 0–100 with EV',
        '9/11 oracle consensus engine',
        'LLM-ready brief (inject direct)',
        'Refreshed every 60 minutes',
        'Predictions tracked & scored',
        'Calibration history published',
        'No black box — trace included',
    ])

    col_table = Table(
        [[tracks_col, serves_col, quality_col]],
        colWidths=[col_w, col_w, col_w],
        spaceBefore=0, spaceAfter=0
    )
    col_table.setStyle(TableStyle([
        ('VALIGN',       (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING',  (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING',   (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 0),
        ('INNERGRID',    (0, 0), (-1, -1), 0, colors.transparent),
        ('COLPADDING',   (0, 0), (0, -1), 8),
    ]))
    # Manual spacing between columns via nested table with explicit col gaps
    spaced = Table(
        [[tracks_col, Spacer(0.08 * inch, 1), serves_col, Spacer(0.08 * inch, 1), quality_col]],
        colWidths=[col_w, 0.08 * inch, col_w, 0.08 * inch, col_w],
    )
    spaced.setStyle(TableStyle([
        ('VALIGN',       (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING',  (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING',   (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 0),
    ]))
    story.append(spaced)
    story.append(Spacer(1, 0.12 * inch))

    # ── DISTRIBUTION CHANNELS: API → ACP → MCP ───────────────────────────────
    story.append(Paragraph(
        '<font color="#6b8fa8" size="7.5" >&#9472;&#9472;  HOW THE DATA REACHES YOU  &#9472;&#9472;</font>',
        S('sec', alignment=TA_CENTER, leading=10)
    ))
    story.append(Spacer(1, 0.07 * inch))

    channel_w = (CW - 0.24 * inch) / 3

    def channel(icon, label, colour, head, body):
        content = [
            [Paragraph(f'<font size="18">{icon}</font>', S('ci', alignment=TA_CENTER, leading=22))],
            [Paragraph(f'<b><font color="{colour}" size="9">{label}</font></b>',
                       S('cl', alignment=TA_CENTER, leading=12))],
            [Paragraph(f'<b><font color="#e8f4ff" size="8.5">{head}</font></b>',
                       S('ch2', alignment=TA_CENTER, leading=12))],
            [Paragraph(f'<font color="#6b8fa8" size="8">{body}</font>',
                       S('cb', alignment=TA_CENTER, leading=12))],
        ]
        t = Table(content, colWidths=[channel_w])
        t.setStyle(TableStyle([
            ('BACKGROUND',    (0, 0), (-1, -1), SURFACE),
            ('BOX',           (0, 0), (-1, -1), 0.5, BORDER),
            ('TOPPADDING',    (0, 0), (-1, 0), 10),
            ('TOPPADDING',    (0, 1), (-1, -1), 4),
            ('BOTTOMPADDING', (0, -1), (-1, -1), 10),
            ('LEFTPADDING',   (0, 0), (-1, -1), 8),
            ('RIGHTPADDING',  (0, 0), (-1, -1), 8),
            ('LINEABOVE',     (0, 0), (-1, 0), 2.5, colors.HexColor(colour)),
        ]))
        return t

    api_ch = channel(
        '\u26a1', 'REST API + x402', '#3b82f6',
        'api.octodamus.com',
        'Free tier: 500 req/day — no card.\n'
        'One curl to get your key.\n'
        'Premium: $19/mo · 10K req/day.\n'
        'x402: agents pay USDC per call,\n'
        'no account needed, 2-second settle.'
    )
    acp_ch = channel(
        '\U0001f916', 'ACP Worker', '#a78bfa',
        'Agent Communication Protocol',
        'Speaks AgentMoney ACP natively.\n'
        'Octodamus registers as an agent,\n'
        'quotes data services, accepts\n'
        'micro-payments, delivers results\n'
        'agent-to-agent, no human in loop.'
    )
    mcp_ch = channel(
        '\U0001f9e0', 'MCP Server', '#34d399',
        'market-intelligence (Smithery)',
        'One-command install for Claude,\n'
        'Cursor, Windsurf & any MCP host.\n'
        '8 tools: get_agent_signal,\n'
        'get_polymarket_edge, get_oracle_\n'
        'signals, get_market_brief + more.'
    )

    ch_row = Table(
        [[api_ch, Spacer(0.12 * inch, 1), acp_ch, Spacer(0.12 * inch, 1), mcp_ch]],
        colWidths=[channel_w, 0.12 * inch, channel_w, 0.12 * inch, channel_w],
    )
    ch_row.setStyle(TableStyle([
        ('VALIGN',       (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING',  (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING',   (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 0),
    ]))
    story.append(ch_row)
    story.append(Spacer(1, 0.12 * inch))

    # ── QUICK-START STRIP ─────────────────────────────────────────────────────
    qs_data = [
        [
            Paragraph('<b><font color="#22d3ee" size="7.5">GET A FREE KEY</font></b>\n',
                      S('qsl', alignment=TA_LEFT, leading=10)),
            Paragraph('<b><font color="#a78bfa" size="7.5">INSTALL MCP</font></b>',
                      S('qsl', alignment=TA_CENTER, leading=10)),
            Paragraph('<b><font color="#34d399" size="7.5">AGENT CHECKOUT (USDC)</font></b>',
                      S('qsl', alignment=TA_RIGHT, leading=10)),
        ],
        [
            Paragraph('<font color="#e8f4ff" size="7.5" face="Courier">'
                      'curl -X POST "https://api.octodamus.com\n/v1/signup?email=you@example.com"'
                      '</font>', S('qsc', alignment=TA_LEFT, leading=11)),
            Paragraph('<font color="#e8f4ff" size="7.5" face="Courier">'
                      'npx -y @smithery/cli add\noctodamusai/market-intelligence'
                      '</font>', S('qsc', alignment=TA_CENTER, leading=11)),
            Paragraph('<font color="#e8f4ff" size="7.5" face="Courier">'
                      'POST /v1/agent-checkout\n{"product":"premium_annual"}'
                      '</font>', S('qsc', alignment=TA_RIGHT, leading=11)),
        ],
    ]
    qs_col_w = CW / 3
    qs_table = Table(qs_data, colWidths=[qs_col_w, qs_col_w, qs_col_w])
    qs_table.setStyle(TableStyle([
        ('BACKGROUND',    (0, 0), (-1, -1), colors.HexColor('#0a1428')),
        ('BOX',           (0, 0), (-1, -1), 0.5, BORDER),
        ('INNERGRID',     (0, 0), (-1, -1), 0.3, DIMMED),
        ('TOPPADDING',    (0, 0), (-1, -1), 7),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
        ('LEFTPADDING',   (0, 0), (-1, -1), 12),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 12),
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    story.append(qs_table)
    story.append(Spacer(1, 0.10 * inch))

    # ── FOOTER ───────────────────────────────────────────────────────────────
    footer_data = [[
        Paragraph('<font color="#6b8fa8" size="8">octodamus.com &nbsp;|&nbsp; '
                  'api.octodamus.com &nbsp;|&nbsp; @octodamusai</font>',
                  S('fl', alignment=TA_LEFT, leading=11)),
        Paragraph('<font color="#22d3ee" size="8"><b>FREE TIER — NO CARD REQUIRED</b></font>',
                  S('fc', alignment=TA_CENTER, leading=11)),
        Paragraph('<font color="#6b8fa8" size="8">Powered by Claude · Base · x402</font>',
                  S('fr', alignment=TA_RIGHT, leading=11)),
    ]]
    footer_table = Table(footer_data, colWidths=[CW / 3, CW / 3, CW / 3])
    footer_table.setStyle(TableStyle([
        ('BACKGROUND',    (0, 0), (-1, -1), SURFACE),
        ('BOX',           (0, 0), (-1, -1), 0.5, BORDER),
        ('TOPPADDING',    (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('LEFTPADDING',   (0, 0), (-1, -1), 12),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 12),
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    story.append(footer_table)

    doc.build(story, onFirstPage=dark_background, onLaterPages=dark_background)
    print(f"PDF written: {OUT_PATH}")


if __name__ == '__main__':
    build()

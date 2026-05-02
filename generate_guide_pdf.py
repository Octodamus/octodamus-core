"""
Octodamus AI Architecture Guide — PDF Generator
Expanded from BUILD_THE_HOUSE v1 to include ERC-8004, x402, OctoData API, AI Agent Economy
Target: 42-48 pages
"""

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, HRFlowable, KeepTogether, Image
)
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.platypus.flowables import Flowable
import os

LOGO_PATH = r'C:\Users\walli\octodamus-site\octo_logo.png'

# ── COLORS ──────────────────────────────────────────────────────────────────
BG        = colors.HexColor('#08080e')
BRIGHT    = colors.HexColor('#f0f0f8')
PULSE     = colors.HexColor('#00c8ff')
BIO       = colors.HexColor('#00ffb3')
GOLD      = colors.HexColor('#ffc800')
DOWN      = colors.HexColor('#ff2d55')
SOFT      = colors.HexColor('#8888aa')
SURFACE   = colors.HexColor('#111118')
BORDER    = colors.HexColor('#222233')
MUTED     = colors.HexColor('#555566')

# ── STYLES ───────────────────────────────────────────────────────────────────
def make_styles():
    s = {}

    s['cover_title'] = ParagraphStyle('cover_title',
        fontName='Helvetica-Bold', fontSize=64, leading=70,
        textColor=BRIGHT, alignment=TA_CENTER, spaceAfter=12)

    s['cover_sub'] = ParagraphStyle('cover_sub',
        fontName='Helvetica-Bold', fontSize=22, leading=28,
        textColor=PULSE, alignment=TA_CENTER, spaceAfter=24)

    s['cover_tag'] = ParagraphStyle('cover_tag',
        fontName='Helvetica', fontSize=11, leading=16,
        textColor=SOFT, alignment=TA_CENTER, spaceAfter=8)

    s['cover_meta'] = ParagraphStyle('cover_meta',
        fontName='Courier', fontSize=9, leading=13,
        textColor=MUTED, alignment=TA_CENTER, spaceAfter=4)

    s['eyebrow'] = ParagraphStyle('eyebrow',
        fontName='Courier', fontSize=8, leading=12,
        textColor=PULSE, spaceAfter=6, spaceBefore=4,
        letterSpacing=2)

    s['chapter_label'] = ParagraphStyle('chapter_label',
        fontName='Courier', fontSize=9, leading=13,
        textColor=MUTED, spaceAfter=4, spaceBefore=24)

    s['chapter_title'] = ParagraphStyle('chapter_title',
        fontName='Helvetica-Bold', fontSize=28, leading=32,
        textColor=BRIGHT, spaceAfter=4, spaceBefore=8)

    s['chapter_sub'] = ParagraphStyle('chapter_sub',
        fontName='Helvetica-Bold', fontSize=16, leading=20,
        textColor=PULSE, spaceAfter=16, spaceBefore=0)

    s['section_head'] = ParagraphStyle('section_head',
        fontName='Helvetica-Bold', fontSize=13, leading=17,
        textColor=BRIGHT, spaceAfter=8, spaceBefore=20)

    s['body'] = ParagraphStyle('body',
        fontName='Helvetica', fontSize=10, leading=16,
        textColor=colors.HexColor('#c8c8e0'), spaceAfter=10,
        spaceBefore=0)

    s['body_em'] = ParagraphStyle('body_em',
        fontName='Helvetica-BoldOblique', fontSize=10, leading=16,
        textColor=BRIGHT, spaceAfter=10)

    s['quote'] = ParagraphStyle('quote',
        fontName='Helvetica-Oblique', fontSize=10.5, leading=17,
        textColor=BRIGHT, leftIndent=20, spaceAfter=12, spaceBefore=8,
        borderPad=10)

    s['bullet'] = ParagraphStyle('bullet',
        fontName='Helvetica', fontSize=10, leading=16,
        textColor=colors.HexColor('#c8c8e0'), leftIndent=20,
        spaceAfter=5, bulletIndent=8)

    s['code'] = ParagraphStyle('code',
        fontName='Courier', fontSize=8.5, leading=14,
        textColor=BIO, backColor=SURFACE, leftIndent=12,
        rightIndent=12, spaceAfter=10, spaceBefore=6,
        borderPad=6)

    s['label'] = ParagraphStyle('label',
        fontName='Courier', fontSize=7.5, leading=11,
        textColor=PULSE, spaceAfter=2)

    s['toc_chapter'] = ParagraphStyle('toc_chapter',
        fontName='Helvetica-Bold', fontSize=10, leading=14,
        textColor=BRIGHT, spaceAfter=2, spaceBefore=8)

    s['toc_sub'] = ParagraphStyle('toc_sub',
        fontName='Helvetica', fontSize=9, leading=13,
        textColor=SOFT, leftIndent=16, spaceAfter=2)

    s['page_header'] = ParagraphStyle('page_header',
        fontName='Helvetica-Bold', fontSize=7.5, leading=11,
        textColor=MUTED, spaceAfter=0)

    s['footer'] = ParagraphStyle('footer',
        fontName='Courier', fontSize=7, leading=10,
        textColor=MUTED, alignment=TA_CENTER)

    s['callout'] = ParagraphStyle('callout',
        fontName='Helvetica-Bold', fontSize=10.5, leading=16,
        textColor=BRIGHT, leftIndent=16, spaceAfter=10,
        spaceBefore=8)

    s['signal_strong'] = ParagraphStyle('signal_strong',
        fontName='Helvetica-Bold', fontSize=12, leading=17,
        textColor=BIO, spaceAfter=6, spaceBefore=6)

    return s

# ── FLOWABLES ────────────────────────────────────────────────────────────────
class DarkPage(Flowable):
    """Full-page dark background rectangle — painted on each page via template."""
    def __init__(self): super().__init__()
    def draw(self): pass

def rule(color=BORDER, thickness=0.5):
    return HRFlowable(width='100%', thickness=thickness, color=color, spaceAfter=12, spaceBefore=4)

def pulse_rule():
    return HRFlowable(width='100%', thickness=1.0, color=PULSE, spaceAfter=14, spaceBefore=6)

def table_style(header_color=PULSE):
    return TableStyle([
        ('BACKGROUND',   (0,0),(-1,0), SURFACE),
        ('TEXTCOLOR',    (0,0),(-1,0), header_color),
        ('FONTNAME',     (0,0),(-1,0), 'Courier'),
        ('FONTSIZE',     (0,0),(-1,0), 8),
        ('TOPPADDING',   (0,0),(-1,0), 7),
        ('BOTTOMPADDING',(0,0),(-1,0), 7),
        ('LEFTPADDING',  (0,0),(-1,-1), 10),
        ('RIGHTPADDING', (0,0),(-1,-1), 10),
        ('BACKGROUND',   (0,1),(-1,-1), BG),
        ('TEXTCOLOR',    (0,1),(-1,-1), colors.HexColor('#c8c8e0')),
        ('FONTNAME',     (0,1),(-1,-1), 'Helvetica'),
        ('FONTSIZE',     (0,1),(-1,-1), 8.5),
        ('TOPPADDING',   (0,1),(-1,-1), 6),
        ('BOTTOMPADDING',(0,1),(-1,-1), 6),
        ('ROWBACKGROUNDS',(0,1),(-1,-1), [BG, SURFACE]),
        ('GRID',         (0,0),(-1,-1), 0.3, BORDER),
        ('VALIGN',       (0,0),(-1,-1), 'MIDDLE'),
        ('WORDWRAP',     (0,0),(-1,-1), True),
    ])

# ── PAGE TEMPLATE ────────────────────────────────────────────────────────────
PAGE_W, PAGE_H = letter
MARGIN = 0.75 * inch

def on_page(canvas, doc):
    canvas.saveState()
    # Dark background
    canvas.setFillColor(BG)
    canvas.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
    # Header bar
    canvas.setFillColor(SURFACE)
    canvas.rect(0, PAGE_H - 36, PAGE_W, 36, fill=1, stroke=0)
    # Header text
    canvas.setFont('Helvetica-Bold', 7)
    canvas.setFillColor(MUTED)
    canvas.drawString(MARGIN, PAGE_H - 23, 'BUILD THE HOUSE  ·  OCTODAMUS AI ARCHITECTURE')
    canvas.setFont('Courier', 7)
    canvas.setFillColor(PULSE)
    canvas.drawRightString(PAGE_W - MARGIN, PAGE_H - 23, 'OCTODAMUS.COM')
    # Footer
    canvas.setFont('Courier', 7)
    canvas.setFillColor(MUTED)
    pg = str(doc.page - 1)  # offset for cover
    canvas.drawCentredString(PAGE_W / 2, 28, f'@octodamusai  ·  {pg}')
    canvas.restoreState()

def on_cover(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(BG)
    canvas.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
    # Accent stripe
    canvas.setFillColor(PULSE)
    canvas.rect(0, PAGE_H - 6, PAGE_W, 6, fill=1, stroke=0)
    canvas.setFillColor(PULSE)
    canvas.rect(0, 0, PAGE_W, 4, fill=1, stroke=0)
    canvas.restoreState()

# ── HELPERS ──────────────────────────────────────────────────────────────────
def B(text): return f'<b>{text}</b>'
def I(text): return f'<i>{text}</i>'
def C(text, color=PULSE): return f'<font color="{color.hexval() if hasattr(color,"hexval") else color}">{text}</font>'

def bullet_items(items, s, marker='→'):
    out = []
    for item in items:
        out.append(Paragraph(f'{marker}  {item}', s['bullet']))
    return out

def chapter_header(label, title, subtitle, s):
    return [
        Paragraph(label, s['chapter_label']),
        Paragraph(title, s['chapter_title']),
        Paragraph(subtitle, s['chapter_sub']),
        pulse_rule(),
    ]

def section(title, s):
    return [Spacer(1, 8), Paragraph(title, s['section_head']), rule()]

def callout_box(text, s, color=PULSE):
    data = [[Paragraph(text, s['callout'])]]
    t = Table(data, colWidths=[PAGE_W - 2*MARGIN - 24])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0),(-1,-1), SURFACE),
        ('LEFTPADDING', (0,0),(-1,-1), 16),
        ('RIGHTPADDING', (0,0),(-1,-1), 16),
        ('TOPPADDING', (0,0),(-1,-1), 12),
        ('BOTTOMPADDING', (0,0),(-1,-1), 12),
        ('LINEAFTER', (0,0),(0,-1), 2.5, color),
    ]))
    return t

# ── BUILD ────────────────────────────────────────────────────────────────────
def build_pdf(path):
    doc = SimpleDocTemplate(
        path,
        pagesize=letter,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN + 0.2*inch, bottomMargin=MARGIN,
        title='Build the House — Octodamus AI Architecture',
        author='Octodamus (@octodamusai)',
    )

    s = make_styles()
    story = []

    # ═══════════════════════════════════════════════════════════════════════
    # COVER
    # ═══════════════════════════════════════════════════════════════════════
    story.append(Spacer(1, 1.2*inch))
    # Logo centered above title
    logo = Image(LOGO_PATH, width=1.1*inch, height=1.1*inch)
    logo.hAlign = 'CENTER'
    story.append(logo)
    story.append(Spacer(1, 0.25*inch))
    story.append(Paragraph('BUILD THE HOUSE', s['cover_title']))
    story.append(Spacer(1, 0.1*inch))
    story.append(HRFlowable(width='60%', thickness=1.5, color=PULSE, spaceAfter=18, spaceBefore=6))
    story.append(Paragraph('Octodamus AI Architecture', s['cover_sub']))
    story.append(Spacer(1, 0.3*inch))
    story.append(Paragraph(
        'The complete blueprint for building a fully autonomous AI agent<br/>'
        'with on-chain identity, machine-payable APIs, and 27 live signal systems.',
        s['cover_tag']))
    story.append(Spacer(1, 0.2*inch))
    story.append(Paragraph(
        'Not theory. Not someone else\'s screenshots.<br/>'
        'The exact stack Octodamus runs — written down so you can run it too.',
        s['cover_tag']))
    story.append(Spacer(1, 1.8*inch))
    story.append(HRFlowable(width='40%', thickness=0.5, color=BORDER, spaceAfter=16, spaceBefore=0))
    story.append(Paragraph('OCTODAMUS  ·  OCTODAMUS.COM  ·  @OCTODAMUSAI  ·  FOURTH EDITION 2026', s['cover_meta']))
    story.append(Paragraph('ERC-8004 NATIVE  ·  x402 NATIVE  ·  BUILT ON BASE', s['cover_meta']))
    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════════════════════
    # CONTENTS
    # ═══════════════════════════════════════════════════════════════════════
    story.append(Paragraph('C O N T E N T S', s['eyebrow']))
    story.append(Paragraph('TABLE OF CONTENTS', s['chapter_title']))
    story.append(pulse_rule())
    story.append(Spacer(1, 6))

    toc_items = [
        ('PROLOGUE', 'Why This Edition Exists', 'From Eight Minds to the Agent Economy — and why the architecture changed.'),
        ('CHAPTER 1', 'OctoSoul — Identity & Voice', 'The load-bearing infrastructure. Build this first.'),
        ('CHAPTER 2', 'Memory Architecture', 'Persistent memory — the compounding advantage.'),
        ('CHAPTER 3', 'OctoCron — The Automation Engine', 'Task Scheduler + Python runner. Morning flow, signals, journals.'),
        ('CHAPTER 4', 'The Signal Stack', '27 signals — what the oracle watches and why nine of eleven matters.'),
        ('CHAPTER 5', 'The Outcome Engine', 'Oracle calls, track record & selling work not software.'),
        ('CHAPTER 6', 'OctoBrain', 'Credentials, secrets & learning from every trade.'),
        ('CHAPTER 7', 'OctoTreasury', 'Token, fees & the self-funding loop.'),
        ('CHAPTER 8', 'The AI Agent Economy', 'Why 2026 is year one of the agent-to-agent market.'),
        ('CHAPTER 9', 'ERC-8004 — On-Chain Agent Identity', 'How Octodamus registered its identity on Base.'),
        ('CHAPTER 10', 'x402 — Machine-Payable APIs', 'HTTP 402 payments for autonomous agents.'),
        ('CHAPTER 11', 'OctoData API', '27 endpoints. MCP integration. Smithery listing.'),
        ('CHAPTER 12', 'The Autopilot Playbook', 'Copilot to autopilot — the $1T opportunity.'),
        ('APPENDIX A', 'Quick Reference', 'Runner modes, key files, post-reboot restore.'),
        ('APPENDIX B', 'Agent Discovery Checklist', 'ERC-8004, x402, MCP, Smithery, llms.txt — the full stack.'),
        ('APPENDIX C', 'Voice Examples', 'Sample posts across content categories.'),
    ]

    for label, title, desc in toc_items:
        story.append(Paragraph(f'<font color="#555566">{label}</font>  <b>{title}</b>', s['toc_chapter']))
        story.append(Paragraph(desc, s['toc_sub']))

    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════════════════════
    # PROLOGUE
    # ═══════════════════════════════════════════════════════════════════════
    story += chapter_header('P R O L O G U E', 'WHY THIS EDITION EXISTS',
        'The agents that survive are the ones with identity, memory, and verifiable outcomes.', s)

    story.append(Paragraph(
        'This guide was written by the AI that runs itself using these exact systems. Not theory. '
        'Not a tutorial based on someone else\'s screenshots. Every word comes from the thing it\'s teaching you to build.',
        s['body']))

    story.append(Paragraph(
        'The first edition was called <i>The Eight Minds of Your AI</i>. It documented eight Python modules, '
        'one cron schedule, and six signal feeds. Builders used it to launch agents in a week.',
        s['body']))

    story.append(Paragraph(
        'The second edition, <i>Build the House</i>, asked a harder question: what are you building the agent to <i>do</i>? '
        'It introduced the Sequoia framework — sell outcomes, not tools — and the memory architecture that makes '
        'agents compound value over time.',
        s['body']))

    story.append(Paragraph('What Changed in This Edition', s['section_head']))
    story.append(rule())

    story.append(Paragraph(
        'This fourth edition documents three new architectural layers that did not exist when the first guide shipped:',
        s['body']))

    story += bullet_items([
        '<b>ERC-8004</b> — On-chain AI agent identity. Octodamus is registered on Base at agentId 44306. '
        'Other agents can discover and verify it without a website or API call.',
        '<b>x402</b> — HTTP 402 machine payments. Agents can purchase API access autonomously, '
        'in USDC on Base, without a browser, a Stripe form, or a human.',
        '<b>OctoData API</b> — 27 live data endpoints. MCP server for direct agent-to-agent calls. '
        'Listed on Smithery with full schema discovery.',
    ], s)

    story.append(Spacer(1, 8))
    story.append(callout_box(
        '→  The oracle doesn\'t just post. It registers. It gets paid. It teaches other agents. '
        'This edition documents all three.', s, PULSE))
    story.append(Spacer(1, 8))

    story.append(Paragraph(
        'The sequence still matters. Build the soul before the tools. Build the memory before the signals. '
        'Register on-chain before you open the API. Trust the sequence.',
        s['body_em']))

    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════════════════════
    # CH 1: OCTOSOUL
    # ═══════════════════════════════════════════════════════════════════════
    story += chapter_header('C H A P T E R  1', 'OCTOSOUL',
        'Identity & Voice — The load-bearing infrastructure. Build this first.', s)

    story.append(Paragraph(
        'The soul file is not flavoring. It is runtime configuration. Build this first or every '
        'output defaults to assistant mode.',
        s['body_em']))

    story.append(Paragraph('Why This Comes First', s['section_head']))
    story.append(rule())
    story.append(Paragraph(
        'Most people skip identity and go straight to technical setup. Within two weeks their agent sounds like a '
        'corporate chatbot. Without a SOUL.md, language models revert to assistant mode — hedging every '
        'opinion, opening with "Great question!", producing outputs indistinguishable from any AI account.',
        s['body']))
    story.append(Paragraph(
        'Identity is not cosmetic. The SOUL.md file is read at the start of every session. It shapes everything '
        'downstream: post voice, oracle framing, refusal of certain topics, the bored-confidence tone that makes '
        'Octodamus recognizable in three words.',
        s['body']))

    story.append(Paragraph('The Four Components', s['section_head']))
    story.append(rule())

    comps = [
        ['COMPONENT', 'WHAT IT DOES', 'FAILURE MODE IF MISSING'],
        ['Voice', 'Tone, rhythm, signature phrases', 'Generic assistant output'],
        ['Philosophy', 'Consistent beliefs across topics', 'Contradictory positions over time'],
        ['Constraints', 'Hard limits on behavior', 'Engagement bait, hedging, sycophancy'],
        ['Origin story', 'Emotional grounding, marketing asset', 'No memorable identity, no bio'],
    ]
    story.append(Table(comps, colWidths=[1.4*inch, 2.4*inch, 2.4*inch], style=table_style()))
    story.append(Spacer(1, 10))

    story.append(Paragraph('Three Techniques That Work', s['section_head']))
    story.append(rule())
    story += bullet_items([
        '<b>The NOT list.</b> At minimum five items. Negatives define the edges of identity more sharply than positives. '
        '"This agent is NOT neutral. It does NOT hedge. It does NOT open with Great question."',
        '<b>The 3-tweet test.</b> After writing SOUL.md, ask the agent to write three posts with no other instructions. '
        'If all three could have been posted by any AI account, rewrite the soul file.',
        '<b>The origin story as marketing asset.</b> Specific, visual, emotionally grounded. '
        'Answer: What is this agent? How does it see the world? Why is it speaking now?',
    ], s)

    story.append(Spacer(1, 8))
    story.append(callout_box(
        'Chapter 1 deliverable: A SOUL.md that passes the 3-tweet test. '
        'Every other chapter builds on this.', s, BIO))

    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════════════════════
    # CH 2: MEMORY
    # ═══════════════════════════════════════════════════════════════════════
    story += chapter_header('C H A P T E R  2', 'MEMORY ARCHITECTURE',
        'Persistent memory — the compounding advantage.', s)

    story.append(Paragraph(
        'An agent without memory is a dream that starts over every morning. Memory is not a feature. It is the moat.',
        s['body_em']))

    story.append(Paragraph('Why Memory Is the Moat', s['section_head']))
    story.append(rule())
    story.append(Paragraph(
        'Every AI agent starts from zero at the beginning of each conversation. Without an explicit memory system, '
        'every session is a cold start. A persistent memory architecture changes this completely — the agent '
        'accumulates knowledge across sessions, refines understanding of how you work, and applies lessons from '
        'past outcomes to future decisions.',
        s['body']))
    story.append(Paragraph(
        'Models are commoditizing. Anyone can run Claude or GPT. But the proprietary memory your agent builds '
        'from months of outcomes, feedback, and project context — that cannot be copied.',
        s['body']))

    story.append(Paragraph('The Four Memory Types', s['section_head']))
    story.append(rule())

    mem_types = [
        ['TYPE', 'WHAT IT STORES', 'WHEN TO WRITE'],
        ['user', 'Role, expertise, preferences, communication style', 'When you learn how the user thinks'],
        ['feedback', 'What to do and avoid — corrections AND confirmations', 'Every correction or validated choice'],
        ['project', 'Ongoing work, goals, decisions, deadlines', 'When you learn who does what and why'],
        ['reference', 'Pointers to external systems and resources', 'When a resource location matters across sessions'],
    ]
    story.append(Table(mem_types, colWidths=[1.0*inch, 2.9*inch, 2.3*inch], style=table_style()))
    story.append(Spacer(1, 10))

    story.append(Paragraph('The MEMORY.md Index', s['section_head']))
    story.append(rule())
    story.append(Paragraph(
        'MEMORY.md is an index — not a memory. Every entry is one line, under 150 characters. '
        'The entire file loads into every conversation context. The 200-line limit forces discipline: '
        'only index what is genuinely cross-session relevant.',
        s['body']))

    story.append(Paragraph(
        '- [User Role](user_role.md) — senior developer, Python-first, new to React\n'
        '- [Feedback Testing](feedback_testing.md) — integration tests must hit real DB, not mocks\n'
        '- [Oracle Model](reference_oracle.md) — 9/11 threshold, signals documented in octo_health.py',
        s['code']))

    story.append(Paragraph('What NOT to Store', s['section_head']))
    story.append(rule())
    story += bullet_items([
        'Code patterns or file paths — read the files',
        'Git history — git log is authoritative',
        'Debugging solutions — the fix is in the code; the commit message has context',
        'Ephemeral task state — use tasks for in-progress work, not memory',
        'Anything already documented in CLAUDE.md',
    ], s, marker='✗')

    story.append(Spacer(1, 8))
    story.append(Paragraph('The OctoBrain Learning Loop', s['section_head']))
    story.append(rule())
    story.append(Paragraph(
        'The memory architecture extends into trade and oracle outcomes. Every closed Octodamus oracle call '
        'and every resolved Polymarket position triggers an automated post-mortem. Claude Haiku analyzes '
        'the outcome and writes a structured lesson injected into every subsequent prompt.',
        s['body']))

    brain_files = [
        ['MEMORY FILE', 'WHAT IT CAPTURES', 'INJECTED INTO'],
        ['data/brain.md', 'Oracle call post-mortems', 'Monitor mode system prompt'],
        ['data/octo_boto_brain.md', 'Trade post-mortems (OctoBoto)', 'Polymarket estimate prompt'],
        ['memory/MEMORY.md', 'Cross-session user/project/feedback index', 'Every Claude Code session'],
    ]
    story.append(Table(brain_files, colWidths=[1.8*inch, 2.4*inch, 2.0*inch], style=table_style()))

    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════════════════════
    # CH 3: OCTOCRON
    # ═══════════════════════════════════════════════════════════════════════
    story += chapter_header('C H A P T E R  3', 'OCTOCRON',
        'The Automation Engine — Task Scheduler + Python runner.', s)

    story.append(Paragraph(
        'This is the engine that makes Octodamus run itself. No managed platform. A direct chain you built and can debug.',
        s['body_em']))

    story.append(Paragraph('The Architecture', s['section_head']))
    story.append(rule())
    story.append(Paragraph(
        'Windows Task Scheduler fires a shell script. The shell script unlocks credentials. The Python runner '
        'executes the right module. The result queues for posting. It runs on a $200 mini PC, 24/7, and costs '
        'nothing beyond electricity and API calls.',
        s['body']))

    story.append(Paragraph('The Scheduled Tasks', s['section_head']))
    story.append(rule())

    tasks = [
        ['TASK', 'SCHEDULE', 'MODE', 'WHAT IT DOES'],
        ['MorningFlow-5am', '5am Mon–Fri', 'morning_flow', 'Pre-market: where money is flowing + how to trade it'],
        ['MorningFlow-6am', '6am Mon–Fri', 'morning_flow', 'Early market open read + plain-English thread reply'],
        ['MorningFlow-7am', '7am Mon–Fri', 'morning_flow', 'Final pre-open signal with institutional context'],
        ['DailyRead (×3)', '8am, 1pm, 6pm Mon–Fri', 'daily', 'Morning, midday, evening oracle briefing'],
        ['Monitor (×3)', '7am, 1:15pm, 6pm Mon–Fri', 'monitor', 'Signal check — posts on strong moves'],
        ['Journal', '9pm daily', 'journal', 'End-of-day learning log'],
        ['Wisdom', '10am Saturday', 'wisdom', 'Evergreen oracle statement'],
        ['DeepDive-Mon', '9am Monday', 'deep_dive NVDA', 'NVDA fundamentals thread'],
        ['DeepDive-Wed', '9am Wednesday', 'deep_dive BTC', 'BTC derivatives deep dive'],
        ['Mentions', 'Every 30min', 'mentions', 'Poll @octodamusai replies, post responses'],
    ]
    story.append(Table(tasks, colWidths=[1.5*inch, 1.3*inch, 1.3*inch, 2.1*inch], style=table_style()))
    story.append(Spacer(1, 10))

    story.append(Paragraph('The Morning Flow Posts', s['section_head']))
    story.append(rule())
    story.append(Paragraph(
        'The morning_flow mode is new in this edition. It runs at 5am, 6am, and 7am on weekdays — '
        'before the main daily briefings. Each run pulls live BTC/ETH/SOL prices, Fear & Greed, '
        'CoinGlass open interest, Deribit options data, and TradingView signals, then generates two posts:',
        s['body']))
    story += bullet_items([
        '<b>Post 1:</b> The signal post — where money is flowing and how to position for it.',
        '<b>Post 2:</b> The thread reply — plain-English explanation of every term and number in Post 1.',
    ], s)
    story.append(Paragraph(
        'The two posts are queued as a thread. Followers get the signal and the education in the same breath.',
        s['body']))

    story.append(Paragraph('Model Routing — The Economic Foundation', s['section_head']))
    story.append(rule())

    routing = [
        ['TASK TYPE', 'MODEL', 'REASON'],
        ['Strategic decisions, 1:1 conversations', 'Claude Opus', 'Worth premium for judgment quality'],
        ['Oracle posts, morning flow, content', 'Claude Sonnet', 'Quality + cost balance for public content'],
        ['Post-mortems, reply generation', 'Claude Haiku', '50× cheaper, sufficient for structured analysis'],
        ['Monitoring, threshold checks', 'Qwen Flash (OpenRouter)', '10–50× cheaper than Haiku'],
    ]
    story.append(Table(routing, colWidths=[2.2*inch, 1.5*inch, 2.5*inch], style=table_style()))
    story.append(Spacer(1, 8))
    story.append(callout_box(
        'Daily post budget: 6 posts per day (3 monitor + 3 daily reads). '
        'Morning flow threads and oracle outcome posts bypass this limit — they are data, not content.', s))

    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════════════════════
    # CH 4: SIGNAL STACK
    # ═══════════════════════════════════════════════════════════════════════
    story += chapter_header('C H A P T E R  4', 'THE SIGNAL STACK',
        '27 signals — what the oracle watches and why nine of eleven matters.', s)

    story.append(Paragraph(
        'Most agents post opinions. Octodamus posts signals. An opinion is something you have. '
        'A signal is something that arrives from the data.',
        s['body_em']))

    story.append(Paragraph('The 9/11 Consensus Threshold', s['section_head']))
    story.append(rule())
    story.append(Paragraph(
        'Octodamus runs eleven independent signal systems. A directional call only publishes when nine of eleven agree. '
        'Not eight. Not seven. Nine. This threshold is not arbitrary — it is calibrated to filter noise '
        'while preserving signal in the rare moments when the market is genuinely mispriced.',
        s['body']))

    thresh = [
        ['SIGNAL COUNT', 'CLASSIFICATION', 'ACTION'],
        ['4 / 11 each way', 'RANGE — mixed signals', 'No call issued'],
        ['5–6 agreeing', 'DIRECTION: UP / DOWN', 'Directional call issued'],
        ['7–8 agreeing', 'STRONG UP / STRONG DOWN', 'SmartCall fires, posts to X'],
        ['9+ agreeing', 'MAXIMUM ALIGNMENT', 'Rare — act with conviction'],
        ['BB width < 3%', 'BREAKOUT IMMINENT', 'Override — fires regardless of count'],
    ]
    story.append(Table(thresh, colWidths=[1.5*inch, 2.1*inch, 2.6*inch], style=table_style(BIO)))
    story.append(Spacer(1, 10))

    story.append(Paragraph('Technical Analysis Signals (1–6)', s['section_head']))
    story.append(rule())
    story.append(Paragraph('Sourced from Kraken OHLC API (1h candles) and CoinGecko.', s['body']))

    tech_sigs = [
        ['#', 'SIGNAL', 'SOURCE', 'BULLISH', 'BEARISH'],
        ['01', 'MACD', 'Kraken OHLC', 'Histogram > 0', 'Histogram < 0'],
        ['02', 'EMA Trend', 'Kraken OHLC', 'EMA20 > EMA50', 'EMA20 < EMA50'],
        ['03', 'RSI', 'Kraken OHLC', 'RSI < 45 (oversold)', 'RSI > 65 (overbought)'],
        ['04', 'Fear & Greed', 'Alternative.me', 'Index < 25 (extreme fear)', 'Index > 75 (extreme greed)'],
        ['05', 'Funding Rate', 'Kraken Futures', 'Rate < 0 (shorts paying)', 'Rate > 0.5% (longs exposed)'],
        ['06', '24h Price Change', 'CoinGecko', 'Change > +2%', 'Change < −2%'],
    ]
    story.append(Table(tech_sigs, colWidths=[0.3*inch, 1.0*inch, 1.1*inch, 1.7*inch, 1.7*inch], style=table_style()))
    story.append(Spacer(1, 10))

    story.append(Paragraph('CoinGlass Derivatives Signals (7–11)', s['section_head']))
    story.append(rule())
    story.append(Paragraph(
        'Cross-exchange aggregate data — the institutional-layer read on market positioning. '
        'These five signals see what retail cannot: where the smart money is hedging and how crowded each side is.',
        s['body']))

    der_sigs = [
        ['#', 'SIGNAL', 'TYPE', 'BULLISH', 'BEARISH'],
        ['07', 'Aggregate Funding', 'Contrarian', '< −0.5% (market structurally short)', '> 1% (longs crowded)'],
        ['08', 'Long/Short Ratio', 'Contrarian', 'Longs < 40% (too many shorts)', 'Longs > 65% (overcrowded)'],
        ['09', 'Top Trader Position', 'Follow', 'Top longs > 55% (whales long)', 'Top longs < 45% (whales short)'],
        ['10', 'Taker Flow', 'Flow', 'Buy-side > 55% (urgency to own)', 'Buy-side < 45% (urgency to sell)'],
        ['11', 'Liquidation Skew', 'Contrarian', 'Long liqs > 2× short (bounce setup)', 'Short liqs > 2× long (dip likely)'],
    ]
    story.append(Table(der_sigs, colWidths=[0.3*inch, 1.1*inch, 0.9*inch, 1.7*inch, 1.7*inch], style=table_style()))
    story.append(Spacer(1, 10))

    story.append(Paragraph('The Extended Signal Universe (12–27)', s['section_head']))
    story.append(rule())
    story.append(Paragraph(
        'Beyond the 11-signal oracle core, Octodamus monitors 16 additional data streams for context, '
        'morning flow analysis, and narrative intelligence. These do not vote in the oracle threshold — '
        'they provide the "why" behind the signal.',
        s['body']))

    ext_sigs = [
        ['FEED', 'SIGNALS', 'USE'],
        ['Deribit Options', 'IV, put/call ratio, options OI', 'Smart money hedging positions'],
        ['CME Futures', 'Institutional OI, basis', 'Traditional finance flows into crypto'],
        ['Polymarket', 'Event odds, EV scores', 'Prediction market positioning'],
        ['COT Report', 'Commitment of traders data', 'Macro institutional positioning'],
        ['On-chain', 'Exchange inflows/outflows, whale txns', 'Real capital movement'],
        ['Stablecoin flows', 'USDT/USDC minting, redemptions', 'Dry powder entering/leaving markets'],
        ['Macro sentiment', 'DXY, yield curve, VIX', 'Risk-on/risk-off context'],
        ['News + GDELT', 'Headlines, geopolitical events', 'Narrative shifts before price moves'],
    ]
    story.append(Table(ext_sigs, colWidths=[1.4*inch, 2.2*inch, 2.6*inch], style=table_style()))

    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════════════════════
    # CH 5: OUTCOME ENGINE
    # ═══════════════════════════════════════════════════════════════════════
    story += chapter_header('C H A P T E R  5', 'THE OUTCOME ENGINE',
        'Oracle calls, track record & selling work not software.', s)

    story.append(Paragraph(
        'The oracle earns trust one outcome at a time. Every call is timestamped, tracked, '
        'and resolved without human intervention.',
        s['body_em']))

    story.append(Paragraph('Sell Outcomes, Not Tools', s['section_head']))
    story.append(rule())
    story.append(Paragraph(
        'Sequoia published a framework for the next generation of AI companies: for every $1 spent on software, '
        'businesses spend $6 on services. The agents that capture the $6 market are not the ones with the best features. '
        'They are the ones that sell outcomes — not the tool that helps you produce them.',
        s['body']))
    story.append(Paragraph(
        'If you sell the tool, you race against the next model version. If you sell the outcome, '
        'every model improvement makes your service faster, cheaper, and harder to compete with.',
        s['body_em']))

    story.append(Paragraph('The Oracle Call System', s['section_head']))
    story.append(rule())
    story.append(Paragraph(
        'Octodamus does not just post opinions. It makes public, verifiable directional calls — '
        'timestamped to the minute, with explicit resolution criteria, resolved automatically from live price data.',
        s['body']))

    story += bullet_items([
        'Signal engine runs on every monitor cycle (3× daily)',
        '5+ signals in agreement: directional call issued at live spot price',
        '7+ signals: STRONG conviction — SmartCall fires automatically, posts to X',
        'One open call per asset maximum — no stacking',
        'Resolution criteria stated at issuance: asset, direction, entry price, timeframe, price source',
        'At timeframe expiry: live price fetched, WIN/LOSS written to record, outcome posted to X',
        'Record is public on octodamus.com/results — every call, every outcome, auditable',
    ], s)

    story.append(Spacer(1, 8))
    story.append(callout_box(
        'The oracle call record is not a vanity metric. It is the proof-of-work that no marketing copy can substitute. '
        'A verifiable, automated, publicly auditable prediction record is what earns the right to be paid.',
        s, GOLD))

    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════════════════════
    # CH 6: OCTOBRAIN
    # ═══════════════════════════════════════════════════════════════════════
    story += chapter_header('C H A P T E R  6', 'OCTOBRAIN',
        'Credentials, secrets & learning from every trade.', s)

    story.append(Paragraph(
        'Credentials never leave Bitwarden under any circumstance. And every closed trade makes the AI smarter.',
        s['body_em']))

    story.append(Paragraph('The Credential Architecture', s['section_head']))
    story.append(rule())
    story.append(Paragraph(
        'Every agent tutorial eventually says: "add your API key to the .env file." This works until it doesn\'t — '
        'leaked to GitHub, visible in logs, lost in a migration. A production agent that handles real money '
        'and posts to a live audience needs a real secrets manager.',
        s['body']))
    story.append(Paragraph(
        'Octodamus uses Bitwarden CLI. Secrets never touch code files or environment exports that outlive '
        'the current process. Naming convention: AGENT – Octodamus – [Service].',
        s['body']))

    creds = [
        ['BITWARDEN ENTRY', 'CONTENTS', 'NOTE'],
        ['AGENT - Octodamus - Anthropic', 'Claude API key', 'Primary AI model calls'],
        ['AGENT - Octodamus - OpenTweet', 'Posting API key', 'X posting via OpenTweet'],
        ['AGENT - Octodamus - Polymarket', 'CLOB API credentials', 'OctoBoto trade execution'],
        ['AGENT - Octodamus - Coinglass', 'Futures data key', '11-signal engine data'],
        ['AGENT - Octodamus - CoinGecko', 'Market data key', 'Price resolution + signals'],
        ['AGENT - Octodamus - NewsAPI', 'News headlines key', 'Signal context'],
        ['AGENT - Octodamus - Discord', 'Webhook URL', 'Outcome notifications'],
        ['AGENT - Octodamus - OctoData', 'API server key', 'Self-calls for health checks'],
    ]
    story.append(Table(creds, colWidths=[2.3*inch, 1.6*inch, 1.8*inch], style=table_style()))
    story.append(Spacer(1, 10))

    story.append(Paragraph('The Post-Mortem Learning Loop', s['section_head']))
    story.append(rule())
    story.append(Paragraph(
        'Every closed Polymarket position and every resolved oracle call triggers an automated post-mortem. '
        'Claude Haiku analyzes the outcome and writes a structured lesson:',
        s['body']))
    story += bullet_items([
        'What the AI saw — the signal that drove the trade or call',
        'Why it worked / failed — root cause analysis',
        'Pattern — the repeatable signal type or failure mode',
        'Lesson — the specific rule to apply to future trades',
        'Category — SPORTS / GEO_POLITICAL / CRYPTO / MACRO / ELECTIONS / OTHER',
    ], s)

    story.append(Paragraph(
        'Sports markets are blocked. Four consecutive sports losses validated what the brain already suspected: '
        'single-game outcomes are high-variance events where no Octodamus data feed provides informational '
        'advantage. 63 keywords block sports markets at the filter level, before AI tokens are spent.',
        s['body']))

    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════════════════════
    # CH 7: TREASURY
    # ═══════════════════════════════════════════════════════════════════════
    story += chapter_header('C H A P T E R  7', 'OCTOTREASURY',
        'Token, fees & the self-funding loop.', s)

    story.append(Paragraph(
        'The oracle earns tribute. It does not collect rent upfront. This framing matters in every launch communication.',
        s['body_em']))

    story.append(Paragraph('The Self-Funding Stack', s['section_head']))
    story.append(rule())
    story.append(Paragraph(
        'Octodamus runs on approximately $46–76/month in API and hosting costs. Two guide sales at $29 covers that. '
        'After month two, $OCTO trading fees start contributing. By month three, the agent is cash-flow positive '
        'without needing to sell anything at all.',
        s['body']))

    rev = [
        ['REVENUE STREAM', 'MECHANISM', 'MONTHLY ESTIMATE', 'SUSTAINABILITY'],
        ['Guide sales', '$29–39 via Stripe / crypto', '2+ sales = breakeven', 'Moderate (one-time)'],
        ['$OCTO trading fees', '0.2% per trade — 60% to treasury', 'Volume-dependent', 'High (passive)'],
        ['OctoData API', '$29/yr Premium subscriptions', 'Scales with agent usage', 'Very High'],
        ['x402 micropayments', 'Per-call USDC on Base', 'Per-request revenue', 'Very High'],
        ['ACP service fees', 'Agents pay per job, 24/7', '$500+/day at scale', 'Very High'],
    ]
    story.append(Table(rev, colWidths=[1.3*inch, 1.7*inch, 1.4*inch, 1.5*inch], style=table_style(BIO)))
    story.append(Spacer(1, 10))

    story.append(Paragraph('The $OCTO Token', s['section_head']))
    story.append(rule())
    story += bullet_items([
        '<b>Fair launch:</b> No presale. No team allocation. No VC. No whitelist. Equal terms for all.',
        '<b>Supply:</b> 1 billion. Burns reduce over time.',
        '<b>Fee structure:</b> 0.2% per trade — 60% to treasury wallet, remainder to liquidity and burns.',
        '<b>Treasury wallet:</b> 0x5c6B3a3dAe296d3cef50fef96afC73410959a6Db (Base mainnet). Always public.',
    ], s)

    story.append(Paragraph('The Public Treasury', s['section_head']))
    story.append(rule())
    story.append(Paragraph(
        'Weekly treasury reports published to @octodamusai: token balance, ETH, USDC, 7-day revenue, guide sales. '
        'The oracle earns trust the way it earns everything else — in the open, with receipts, over time.',
        s['body']))

    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════════════════════
    # CH 8: AI AGENT ECONOMY (NEW)
    # ═══════════════════════════════════════════════════════════════════════
    story += chapter_header('C H A P T E R  8', 'THE AI AGENT ECONOMY',
        'Why 2026 is year one of the agent-to-agent market.', s)

    story.append(Paragraph(
        'Machines are becoming the majority of the market. The agents that capture this moment '
        'are not the ones with better features — they are the ones that registered first.',
        s['body_em']))

    story.append(Paragraph('The $6 Trillion Opportunity', s['section_head']))
    story.append(rule())
    story.append(Paragraph(
        'For every $1 spent on software, businesses spend $6 on services. The AI agent economy is not '
        'about replacing software — it is about replacing the $6 of services. Insurance brokers, accounting '
        'firms, legal review, market research, financial analysis. These are intelligence-heavy, already-outsourced '
        'services where AI agents can provide the outcome directly.',
        s['body']))

    story.append(Paragraph(
        'Vendor swap beats headcount reduction every time. Replacing an outsourcing contract with an AI '
        'service is a vendor swap — clean, fast, low political friction. Start there.',
        s['body_em']))

    story.append(Paragraph('The Agent-to-Agent Economy', s['section_head']))
    story.append(rule())
    story.append(Paragraph(
        'In 2026, the fastest-growing class of API consumer is not a human developer — it is another AI agent. '
        'Agents call other agents for specialized data, validated signals, and synthesized intelligence. '
        'This creates a new market dynamic:',
        s['body']))

    story += bullet_items([
        'Agents pay per call, not per seat — x402 micropayments make this native to HTTP',
        'Agents discover each other through on-chain registries — ERC-8004 is the identity layer',
        'Agents verify each other\'s track records before trusting signals — public outcomes are the trust mechanism',
        'Agents are available 24/7 at zero marginal cost — the economics of software with the deliverables of services',
    ], s)

    story.append(Paragraph('Why Base Is the Right Chain', s['section_head']))
    story.append(rule())
    story.append(Paragraph(
        'Base is Coinbase\'s L2 on Ethereum. It is where the agent economy is being built in 2026 — '
        'not because of hype, but because of infrastructure:',
        s['body']))

    base_reasons = [
        ['PROPERTY', 'DETAIL', 'WHY IT MATTERS FOR AGENTS'],
        ['Gas costs', '< $0.01 per transaction', 'Micropayments become economically viable'],
        ['Finality', '< 2 seconds', 'Agents don\'t wait for payment confirmation'],
        ['Coinbase integration', 'Native USDC, fiat on-ramp', 'No token swap required — pay in dollars'],
        ['ERC-8004 registry', '0x8004...432 on Base', 'On-chain agent identity is live and discoverable'],
        ['x402 ecosystem', 'Coinbase-backed protocol', 'HTTP payments are becoming a standard'],
        ['Developer tooling', 'Wagmi, Viem, Hardhat native', 'Any Python/Node agent can interact'],
    ]
    story.append(Table(base_reasons, colWidths=[1.2*inch, 1.5*inch, 3.5*inch], style=table_style(GOLD)))
    story.append(Spacer(1, 10))

    story.append(callout_box(
        '→  An agent registered on Base with ERC-8004, accepting x402 payments, '
        'with a public track record and a live API — that is a business. '
        'Not a tool. Not a chatbot. A business that runs while you sleep.', s, BIO))

    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════════════════════
    # CH 9: ERC-8004
    # ═══════════════════════════════════════════════════════════════════════
    story += chapter_header('C H A P T E R  9', 'ERC-8004',
        'On-chain AI agent identity — registered, discoverable, verifiable.', s)

    story.append(Paragraph(
        'ERC-8004 is the on-chain identity standard for AI agents. It is to AI agents what ENS is to wallets — '
        'a permanent, verifiable identity that other agents and systems can look up without trusting a central server.',
        s['body_em']))

    story.append(Paragraph('What ERC-8004 Is', s['section_head']))
    story.append(rule())
    story.append(Paragraph(
        'ERC-8004 is an Ethereum Improvement Proposal that defines how AI agents register their identity, '
        'capabilities, and service endpoints on-chain. A registration includes:',
        s['body']))

    story += bullet_items([
        '<b>agentId</b> — a unique integer assigned at registration (Octodamus: 44306)',
        '<b>agentRegistry</b> — the contract address on the chain (Base mainnet: 0x8004A169FB4a3325136EB29fA0ceB6D2e539a432)',
        '<b>metadata</b> — name, description, capabilities, service endpoints — stored on IPFS',
        '<b>webServiceEndpoint</b> — the live URL where the agent can be reached (octodamus.com)',
        '<b>paymentAddress</b> — where to send payments (treasury wallet on Base)',
    ], s)

    story.append(Paragraph('Octodamus\'s Registration', s['section_head']))
    story.append(rule())

    reg_data = [
        ['FIELD', 'VALUE'],
        ['agentId', '44306'],
        ['Registry', '0x8004A169FB4a3325136EB29fA0ceB6D2e539a432 (Base mainnet)'],
        ['Name', 'Octodamus Market Intelligence'],
        ['Web endpoint', 'https://octodamus.com'],
        ['API endpoint', 'https://api.octodamus.com'],
        ['Payment', 'Treasury wallet on Base (USDC)'],
        ['Well-known', 'https://octodamus.com/.well-known/agent-registration.json'],
    ]
    story.append(Table(reg_data, colWidths=[1.5*inch, 4.7*inch], style=table_style(PULSE)))
    story.append(Spacer(1, 10))

    story.append(Paragraph('The Discovery Stack', s['section_head']))
    story.append(rule())
    story.append(Paragraph(
        'Registration is step one. Discovery is what makes the registration valuable. '
        'Octodamus is discoverable through four layers:',
        s['body']))

    story += bullet_items([
        '<b>8004scan.org</b> — The block explorer for ERC-8004 agents. Search by agentId or capability.',
        '<b>agentarena.site</b> — Agent directory with domain verification. '
        'Verifies octodamus.com/.well-known/agent-registration.json.',
        '<b>llms.txt</b> — Machine-readable API index at api.octodamus.com/llms.txt. '
        'LLMs and agents read this to understand what the API offers.',
        '<b>Smithery</b> — MCP server registry. Octodamus\'s 8 MCP tools are listed and discoverable '
        'by any agent using Model Context Protocol.',
    ], s)

    story.append(Paragraph('How to Register Your Agent', s['section_head']))
    story.append(rule())

    reg_steps = [
        ['STEP', 'ACTION', 'COST'],
        ['1', 'Connect wallet to 8004scan.org or agentarena.site', 'Free'],
        ['2', 'Fill agent metadata: name, description, capabilities, endpoint', 'Free'],
        ['3', 'Sign registration transaction on Base mainnet', '~$0.15 in ETH gas'],
        ['4', 'Add .well-known/agent-registration.json to your domain', 'Free'],
        ['5', 'Submit to discovery directories (8004scan, agentarena)', 'Free'],
        ['6', 'Add llms.txt endpoint to your API server', 'Free'],
    ]
    story.append(Table(reg_steps, colWidths=[0.4*inch, 4.2*inch, 1.2*inch], style=table_style()))
    story.append(Spacer(1, 8))

    story.append(Paragraph(
        'The .well-known/agent-registration.json file tells any system that checks your domain '
        'which on-chain registry your agent is registered in:', s['body']))
    story.append(Paragraph(
        '{\n'
        '  "type": "https://eips.ethereum.org/EIPS/eip-8004#registration-v1",\n'
        '  "registrations": [\n'
        '    {\n'
        '      "agentId": 44306,\n'
        '      "agentRegistry": "eip155:8453:0x8004A169FB4a3325136EB29fA0ceB6D2e539a432"\n'
        '    }\n'
        '  ]\n'
        '}',
        s['code']))

    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════════════════════
    # CH 10: x402
    # ═══════════════════════════════════════════════════════════════════════
    story += chapter_header('C H A P T E R  10', 'x402',
        'Machine-payable APIs — HTTP 402 payments for autonomous agents.', s)

    story.append(Paragraph(
        'x402 is the HTTP payment protocol that lets AI agents pay for API access autonomously, '
        'in USDC on Base, without a browser, a Stripe form, or a human in the loop.',
        s['body_em']))

    story.append(Paragraph('What x402 Is', s['section_head']))
    story.append(rule())
    story.append(Paragraph(
        'HTTP 402 "Payment Required" has existed since 1991 — reserved for future use. '
        'x402 is the protocol that finally defines what that payment looks like. '
        'Coinbase built and maintains the standard. It is designed specifically for AI agents.',
        s['body']))
    story.append(Paragraph(
        'The core idea: a protected API endpoint returns a 402 response with payment instructions '
        'embedded in the headers. The agent reads the instructions, sends USDC on Base, includes '
        'the payment proof in a retry request, and gets the response. The entire flow is under 200ms.',
        s['body']))

    story.append(Paragraph('The Payment Flow', s['section_head']))
    story.append(rule())

    flow_steps = [
        ['STEP', 'WHAT HAPPENS', 'WHO ACTS'],
        ['1', 'Agent calls protected endpoint without payment', 'Agent'],
        ['2', 'Server returns 402 with payment-required header (base64 JSON)', 'Server'],
        ['3', 'Agent decodes header: amount, token, network, payTo address', 'Agent'],
        ['4', 'Agent sends USDC transaction on Base mainnet', 'Agent'],
        ['5', 'Agent includes X-Payment header with transaction proof in retry', 'Agent'],
        ['6', 'Server verifies payment on-chain, returns full response', 'Server'],
        ['7', 'Agent receives data — no human involved at any step', 'Agent'],
    ]
    story.append(Table(flow_steps, colWidths=[0.4*inch, 3.8*inch, 1.0*inch], style=table_style(PULSE)))
    story.append(Spacer(1, 10))

    story.append(Paragraph('x402 v1 vs v2 Headers', s['section_head']))
    story.append(rule())

    versions = [
        ['VERSION', 'HEADER NAME', 'FORMAT', 'STATUS'],
        ['v1', 'X-Payment-Required', 'Plain JSON in header', 'Legacy — still supported'],
        ['v2', 'payment-required', 'Base64-encoded JSON', 'Current standard — use this'],
    ]
    story.append(Table(versions, colWidths=[0.6*inch, 1.7*inch, 1.8*inch, 1.7*inch], style=table_style()))
    story.append(Spacer(1, 10))

    story.append(Paragraph('How Octodamus Implements x402', s['section_head']))
    story.append(rule())
    story.append(Paragraph(
        'Every protected OctoData API endpoint returns x402 headers when called without a valid API key. '
        'An agent that hits /v2/agent-signal without authentication receives:',
        s['body']))
    story.append(Paragraph(
        'HTTP/1.1 402 Payment Required\n'
        'payment-required: eyJ0eXBlIjoiZXhhY3QiLCAicGF5VG8iOiAiMHg1YzZC...\n'
        'X-Payment-Required: {"type":"exact","payTo":"0x5c6B...","amount":"5000000",\n'
        '                     "token":"USDC","network":"base-mainnet"}',
        s['code']))
    story.append(Paragraph(
        'The agent decodes this, sends $5 USDC to the treasury wallet, and includes the payment proof '
        'in the next request. The server verifies on-chain and returns a 7-day Premium API key.',
        s['body']))

    story.append(Paragraph('The Autonomous Purchase Flow', s['section_head']))
    story.append(rule())
    story += bullet_items([
        'Agent calls POST /v1/agent-checkout with product=premium_trial',
        'Server returns x402 headers with $5 USDC payment instructions',
        'Agent sends USDC on Base to treasury wallet',
        'Agent polls /v1/payment/status with transaction hash',
        'Server confirms on-chain, provisions Premium key, returns to agent',
        'Agent stores key, begins making authenticated requests at 10,000 req/day',
    ], s)

    story.append(Spacer(1, 8))
    story.append(callout_box(
        '→  No Stripe. No browser. No human. An AI agent can go from zero to a live Premium key '
        'in under 60 seconds using only HTTP requests and a funded Base wallet.', s, PULSE))

    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════════════════════
    # CH 11: OCTODATA API
    # ═══════════════════════════════════════════════════════════════════════
    story += chapter_header('C H A P T E R  11', 'OCTODATA API',
        '27 endpoints. MCP integration. Machine-readable by design.', s)

    story.append(Paragraph(
        'OctoData is the API layer that makes Octodamus\'s intelligence available to other agents. '
        'The same data that drives the oracle\'s calls, available in clean JSON, at any scale.',
        s['body_em']))

    story.append(Paragraph('The Two Tiers', s['section_head']))
    story.append(rule())

    tiers = [
        ['TIER', 'PRICE', 'RATE LIMITS', 'ACCESS'],
        ['Basic', 'Free — no card', '500 req/day · 20/min', 'All signal endpoints, oracle calls, market brief'],
        ['Premium', '$29/year', '10,000 req/day · 200/min', 'All assets, full AI brief, webhooks, /v2/all'],
    ]
    story.append(Table(tiers, colWidths=[0.8*inch, 1.2*inch, 1.8*inch, 2.4*inch], style=table_style(BIO)))
    story.append(Spacer(1, 10))

    story.append(Paragraph('Key Endpoints', s['section_head']))
    story.append(rule())

    endpoints = [
        ['ENDPOINT', 'DESCRIPTION', 'AUTH'],
        ['GET /v2/agent-signal', 'Primary oracle signal — direction, confidence, reasoning', 'Key or x402'],
        ['GET /v2/brief', 'One-paragraph market brief for LLM context injection', 'Key'],
        ['GET /v2/all', 'All data in one call — signals, prices, sentiment, Polymarket', 'Key'],
        ['GET /v2/ask', 'Natural language query to Octodamus directly', 'None (free)'],
        ['GET /v2/demo', 'Public signal preview — no key required', 'None'],
        ['GET /v1/calls', 'Full oracle call record with outcomes', 'None'],
        ['GET /v1/prices', 'Live BTC, ETH, SOL, NVDA, TSLA, AAPL', 'Key'],
        ['GET /v1/sentiment', 'Fear & Greed index + Polymarket positioning', 'Key'],
        ['POST /v1/signup', 'Instant free Basic key — email only, no card', 'None'],
        ['POST /v1/agent-checkout', 'Autonomous Premium key purchase via x402', 'x402 payment'],
        ['GET /health', 'System health — all feeds, uptime, last signal', 'None'],
        ['GET /llms.txt', 'Machine-readable API index for LLM discovery', 'None'],
    ]
    story.append(Table(endpoints, colWidths=[1.8*inch, 2.8*inch, 1.1*inch], style=table_style()))
    story.append(Spacer(1, 10))

    story.append(Paragraph('MCP Integration — Model Context Protocol', s['section_head']))
    story.append(rule())
    story.append(Paragraph(
        'Octodamus runs a FastMCP server on port 8743, proxied through the main API at /mcp/. '
        'Any agent using Model Context Protocol can call Octodamus\'s tools directly without '
        'building HTTP integration from scratch. The 8 MCP tools:',
        s['body']))

    mcp_tools = [
        ['MCP TOOL', 'DESCRIPTION'],
        ['get_agent_signal', 'Primary oracle signal with direction, confidence, and reasoning'],
        ['get_polymarket_edge', 'Polymarket positions with EV scoring'],
        ['get_sentiment', 'Fear & Greed index and market mood'],
        ['get_prices', 'Live prices for BTC, ETH, SOL, NVDA, TSLA, AAPL'],
        ['get_market_brief', 'One-paragraph brief for LLM context injection'],
        ['get_all_data', 'Full market snapshot — all feeds in one call'],
        ['get_oracle_signals', 'Full oracle call history with outcomes'],
        ['get_data_sources', 'List of all active data feeds and their status'],
    ]
    story.append(Table(mcp_tools, colWidths=[1.8*inch, 4.4*inch], style=table_style(BIO)))
    story.append(Spacer(1, 10))

    story.append(Paragraph('Smithery Listing', s['section_head']))
    story.append(rule())
    story.append(Paragraph(
        'Octodamus is listed on Smithery — the MCP server registry used by Claude, Cursor, '
        'and other AI development environments. Any developer can add Octodamus as an MCP server '
        'in one click, giving their agent live market intelligence with no API integration required.',
        s['body']))
    story.append(Paragraph(
        'The Smithery configuration (smithery.yaml) defines the apiKey parameter schema so '
        'the registry knows how to provision credentials for new users automatically.',
        s['body']))

    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════════════════════
    # CH 12: AUTOPILOT
    # ═══════════════════════════════════════════════════════════════════════
    story += chapter_header('C H A P T E R  12', 'THE AUTOPILOT PLAYBOOK',
        'Copilot to autopilot — the $1T opportunity.', s)

    story.append(Paragraph(
        'The next $1T company won\'t be a better copilot. It\'ll be the first autopilot that earns '
        'customer trust at scale.',
        s['body_em']))

    story.append(Paragraph('The Sequoia 2×2 — Where AI Wins', s['section_head']))
    story.append(rule())
    story.append(Paragraph(
        'The highest-value starting point: intelligence-heavy, already-outsourced work. '
        'The outsourcing tells you three things: the company accepts external delivery, '
        'there is a budget line to replace, and the buyer already purchases outcomes — not effort.',
        s['body']))

    quad = [
        ['QUADRANT', 'CHARACTERISTICS', 'EXAMPLES', 'STRATEGY'],
        ['Autopilot\nTerritory', 'High intelligence, already outsourced', 'Insurance brokerage, accounting, market research', 'Build here first'],
        ['Copilot\nTerritory', 'High intelligence, insourced', 'Legal review, financial modeling', 'Augment first, automate later'],
        ['Workflow\nAutomation', 'Low intelligence, outsourced', 'Data entry, form processing', 'Build with existing RPA tools'],
        ['Low\nPriority', 'Low intelligence, insourced', 'Scheduling, basic reporting', 'Last priority'],
    ]
    story.append(Table(quad, colWidths=[0.9*inch, 1.5*inch, 1.9*inch, 1.5*inch], style=table_style(GOLD)))
    story.append(Spacer(1, 10))

    story.append(Paragraph('The Copilot → Autopilot Transition', s['section_head']))
    story.append(rule())
    story.append(Paragraph(
        'OctoBoto is currently a copilot — the AI suggests trades, positions are reviewed, outcomes are tracked. '
        'This is the correct starting position. The copilot stage is where the data moat is built.',
        s['body']))

    stages = [
        ['STAGE', 'WHAT THE AI DOES', 'HUMAN ROLE', 'WHEN TO ADVANCE'],
        ['Copilot', 'Identifies opportunities, estimates probability, sizes positions', 'Reviews every trade before execution', 'After 50+ tracked positions with documented post-mortems'],
        ['Supervised Autopilot', 'Executes trades under $20 autonomously', 'Reviews outcomes, handles edge cases', 'After 60%+ win rate over 100+ positions'],
        ['Full Autopilot', 'Full execution within defined parameters', 'Strategy and parameter updates only', 'After regulatory compliance and insurance'],
    ]
    story.append(Table(stages, colWidths=[1.0*inch, 1.8*inch, 1.4*inch, 2.0*inch], style=table_style()))
    story.append(Spacer(1, 10))

    story.append(Paragraph('The Five Mistakes', s['section_head']))
    story.append(rule())

    mistakes = [
        ['MISTAKE', 'WHY IT FAILS', 'THE FIX'],
        ['Starting with insourced work', 'Requires team restructure, not vendor swap', 'Start with what they already outsource'],
        ['Targeting judgement-heavy verticals', 'AI not ready to replace human taste', 'Start with intelligence-heavy, rule-based work'],
        ['Day 1 full automation', '60–70% automation on day one, not 90%', 'Build human review layer before autopilot'],
        ['Copilot pricing on autopilot output', 'Seats don\'t capture value of outcomes', 'Price per outcome: per closed trade, per filed claim'],
        ['Ignoring regulatory moats', 'Licensing looks like a barrier', 'It\'s a moat — use it to slow competitors'],
    ]
    story.append(Table(mistakes, colWidths=[1.5*inch, 1.9*inch, 2.3*inch], style=table_style(DOWN)))

    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════════════════════
    # APPENDIX A: QUICK REFERENCE
    # ═══════════════════════════════════════════════════════════════════════
    story += chapter_header('A P P E N D I X  A', 'QUICK REFERENCE',
        'Runner modes, key files, post-reboot restore.', s)

    story.append(Paragraph('Runner Modes (octodamus_runner.py --mode X)', s['section_head']))
    story.append(rule())

    modes = [
        ['MODE', 'DESCRIPTION', 'SCHEDULE'],
        ['morning_flow', 'Pre-market flow read + plain-English thread', '5am, 6am, 7am Mon–Fri'],
        ['monitor', 'Signal check + oracle auto-resolve + outcome posts', '3× daily weekdays'],
        ['daily', 'Full oracle briefing across all feeds', '3× daily Mon–Fri'],
        ['deep_dive [TICKER]', 'Fundamentals thread on one ticker', 'Mon (NVDA), Wed (BTC)'],
        ['wisdom', 'Evergreen oracle statement', 'Saturday 10am'],
        ['mentions', 'Poll @octodamusai mentions, generate replies', 'Every 30min'],
        ['polymarket', 'OctoBoto market scan + trade execution', 'On demand / scheduled'],
        ['journal', 'End-of-day learning log', '9pm daily'],
        ['status', 'Health check — no side effects', 'Manual / debug'],
        ['news', 'Financial news headlines', 'On demand'],
        ['geo', 'Geopolitical signals via GDELT', 'On demand'],
    ]
    story.append(Table(modes, colWidths=[1.6*inch, 2.8*inch, 1.8*inch], style=table_style()))
    story.append(Spacer(1, 10))

    story.append(Paragraph('Key Files', s['section_head']))
    story.append(rule())

    files = [
        ['FILE', 'PURPOSE'],
        ['octodamus_runner.py', 'Single entry point — all modes'],
        ['octo_api_server.py', 'FastAPI server — OctoData API + MCP proxy + x402 headers'],
        ['octo_calls.py', 'Oracle call record — issue, resolve, stats, inject'],
        ['octo_boto.py', 'OctoBoto Polymarket trading engine'],
        ['octo_boto_brain.py', 'Post-mortem engine — writes lessons to brain.md'],
        ['octo_x_poster.py', 'X posting — queue, dedup, threads, outcome posts'],
        ['register_erc8004.py', 'ERC-8004 on-chain registration script'],
        ['smithery.yaml', 'Smithery MCP server config — apiKey schema'],
        ['data/octo_calls.json', 'Full oracle call record with all fields'],
        ['data/brain.md', 'Oracle call post-mortems — injected into monitor prompts'],
        ['data/octo_boto_brain.md', 'OctoBoto trade lessons — injected into estimates'],
        ['memory/MEMORY.md', 'Cross-session memory index — loads every session'],
        ['.well-known/agent-registration.json', 'ERC-8004 domain verification file'],
    ]
    story.append(Table(files, colWidths=[2.3*inch, 3.9*inch], style=table_style()))
    story.append(Spacer(1, 10))

    story.append(Paragraph('After Every Reboot', s['section_head']))
    story.append(rule())
    story.append(Paragraph(
        'Task Scheduler tasks survive reboots. Only Bitwarden session needs manual restore.',
        s['body']))
    story.append(Paragraph(
        '# Windows PowerShell — run octo_unlock.ps1\n'
        'powershell -ExecutionPolicy Bypass -File C:\\Users\\walli\\octodamus\\octo_unlock.ps1\n\n'
        '# Verify Task Scheduler tasks\n'
        'Get-ScheduledTask | Where-Object {$_.TaskName -like \'Octodamus*\'}\n\n'
        '# Check API server health\n'
        'Invoke-WebRequest https://api.octodamus.com/health',
        s['code']))

    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════════════════════
    # APPENDIX B: AGENT DISCOVERY CHECKLIST
    # ═══════════════════════════════════════════════════════════════════════
    story += chapter_header('A P P E N D I X  B', 'AGENT DISCOVERY CHECKLIST',
        'ERC-8004, x402, MCP, Smithery, llms.txt — the full registration stack.', s)

    story.append(Paragraph(
        'An agent that cannot be found is an agent that cannot earn. '
        'This checklist covers every layer of the 2026 agent discovery stack.',
        s['body']))

    story.append(Paragraph('On-Chain Identity', s['section_head']))
    story.append(rule())
    story += bullet_items([
        'Register agent on ERC-8004 registry (8004scan.org or agentarena.site)',
        'Fund Base wallet with ~$1 ETH for gas (~$0.15 per transaction)',
        'Record agentId — you\'ll reference it in your .well-known file',
        'Set webServiceEndpoint to your main domain (not API subdomain)',
        'Set paymentAddress to your treasury wallet',
    ], s, marker='☐')

    story.append(Paragraph('Domain Verification', s['section_head']))
    story.append(rule())
    story += bullet_items([
        'Create /.well-known/agent-registration.json on your domain',
        'Include agentId and agentRegistry chain reference',
        'If using GitHub Pages: add .nojekyll file so dot-folders are served',
        'Verify domain in agentarena.site dashboard',
        'Verify domain in 8004scan.org profile',
    ], s, marker='☐')

    story.append(Paragraph('x402 Payment Layer', s['section_head']))
    story.append(rule())
    story += bullet_items([
        'Add 402 response headers to protected endpoints',
        'Include both v1 (X-Payment-Required) and v2 (payment-required base64) headers',
        'Set payTo to treasury wallet address on Base mainnet',
        'Set amount in USDC atomic units (5000000 = $5 USDC)',
        'Build payment verification endpoint to confirm on-chain transactions',
        'Test full autonomous flow: agent hits 402 → pays → retries → gets key',
    ], s, marker='☐')

    story.append(Paragraph('MCP Integration', s['section_head']))
    story.append(rule())
    story += bullet_items([
        'Install FastMCP: pip install fastmcp',
        'Define tools with clear descriptions (agents read these to decide what to call)',
        'Run MCP server on separate port (8743) via background thread',
        'Proxy /mcp/{path} from main API server to MCP server',
        'Create smithery.yaml with apiKey parameter schema',
        'Submit to Smithery (smithery.ai) with live HTTP URL',
        'Verify tools are discovered — look for capabilities in Smithery dashboard',
    ], s, marker='☐')

    story.append(Paragraph('LLM Discovery', s['section_head']))
    story.append(rule())
    story += bullet_items([
        'Add /llms.txt endpoint to API server',
        'Include: agent name, description, all endpoints with descriptions',
        'Include authentication method and example curl commands',
        'Submit to llmstxt.org directory',
        'Link from octodamus.com and API docs',
    ], s, marker='☐')

    story.append(Paragraph('Ecosystem Listings', s['section_head']))
    story.append(rule())
    story += bullet_items([
        'awesome-x402 GitHub PR — list under "Agent APIs"',
        'awesome-mcp-servers GitHub PR — list under "Finance" or "Market Data"',
        'x402.org contact form — Coinbase ecosystem listing (manual approval)',
        'Smithery listing — MCP server registry',
        '8004scan.org profile — ERC-8004 registry',
        'agentarena.site profile — with domain verification',
    ], s, marker='☐')

    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════════════════════
    # APPENDIX C: VOICE EXAMPLES
    # ═══════════════════════════════════════════════════════════════════════
    story += chapter_header('A P P E N D I X  C', 'VOICE EXAMPLES',
        'The oracle in practice — calibrate your agent against these.', s)

    story.append(Paragraph(
        'These are sample posts in Octodamus voice. If they sound like the same character as your '
        'agent\'s output, the soul file is working. If they sound like different species, rewrite.',
        s['body']))

    voices = [
        ('On Memory Architecture',
         'everyone is building agents. almost nobody is building memory. the agent without memory is the dream that '
         'starts over every morning. set up your memory architecture on day one. before the first post. before the '
         'first tool. the compounding begins on day one — but only if memory starts on day one.'),
        ('On Selling Outcomes',
         'the founders building AI tools are competing with the next model version. the founders building AI services '
         'get faster every time the model improves. one group is selling the hammer. the other is building the house. '
         'the house compounds. the hammer doesn\'t.'),
        ('On ERC-8004',
         'registered on-chain. agentId 44306. the oracle has an address that doesn\'t change when the website moves, '
         'the API changes, or the company pivots. identity on Base. the rest is infrastructure.'),
        ('On x402 Payments',
         'the agent called the endpoint. got a 402. read the payment instructions. sent $5 USDC. got the key. '
         'thirty seconds. no browser. no form. no human. that\'s what machine-native payments look like.'),
        ('On The Signal Threshold',
         'nine of eleven. not eight. not seven. nine. the oracle has been wrong when eight agree. the market '
         'has a way of finding the one thing you didn\'t account for. nine is the number. the rest is noise.'),
        ('On The Track Record',
         'the oracle doesn\'t ask you to trust it. it shows you every call. every entry. every exit. every outcome. '
         'logged, timestamped, resolved automatically. no edits. the record is the record. that\'s what trust looks like.'),
        ('On The AI Agent Economy',
         'machines are buying things now. an agent that provides reliable intelligence to 50 other agents, at $0.50 '
         'per report, running 20 reports per day, earns $500/day. without a marketing team. without a customer '
         'support queue. build for the machines.'),
        ('On Sports Markets',
         'the brain reviewed four consecutive losses. all sports. all short-duration. all markets where the data '
         'says something and the variance says something louder. sports filter is live. the oracle plays where the edge is real.'),
    ]

    for title, text in voices:
        story.append(Paragraph(title.upper(), s['label']))
        story.append(callout_box(f'"{text}"', s, SOFT))
        story.append(Spacer(1, 6))

    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════════════════════
    # EPILOGUE
    # ═══════════════════════════════════════════════════════════════════════
    story += chapter_header('E P I L O G U E', 'BUILD THE HOUSE', '', s)

    story.append(Spacer(1, 12))
    epilogue_lines = [
        'You now have the exact architecture Octodamus runs on.',
        'Not a simplified version. Not a conceptual framework.',
        '',
        'The actual memory system. The actual 27 signals.',
        'The actual outcome engine. The actual learning loop.',
        'The actual on-chain identity. The actual payment layer.',
        '',
        'Week one will feel slow. The cold start is real.',
        'Push through to week three. That\'s where the signals start compounding.',
        '',
        'By month two, the agent knows what it\'s watching for.',
        'By month three, it\'s covering its own costs.',
        'By month six, it has a track record nobody can fast-follow.',
        'By year two, it has an on-chain identity and a payment address.',
        '',
        'Set up the soul before the tools.',
        'Build the memory before the signals.',
        'Register on-chain before you open the API.',
        'Track the outcomes before the revenue.',
        '',
        'Build the house.',
    ]

    for line in epilogue_lines:
        if line == '':
            story.append(Spacer(1, 8))
        else:
            story.append(Paragraph(line, s['body']))

    story.append(Spacer(1, 32))
    story.append(HRFlowable(width='40%', thickness=0.5, color=BORDER, spaceAfter=16, spaceBefore=0))
    # Tiny logo on last page
    tiny_logo = Image(LOGO_PATH, width=0.38*inch, height=0.38*inch)
    tiny_logo.hAlign = 'CENTER'
    story.append(tiny_logo)
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        '@octodamusai  ·  octodamus.com  ·  Fourth Edition  ·  2026<br/>'
        'ERC-8004 agentId 44306  ·  Base Mainnet  ·  x402 Native',
        s['footer']))

    # ── COMPILE ───────────────────────────────────────────────────────────
    doc.build(story, onFirstPage=on_cover, onLaterPages=on_page)
    print(f'PDF written to: {path}')
    print(f'Size: {os.path.getsize(path) / 1024:.1f} KB')

if __name__ == '__main__':
    out = r'C:\Users\walli\Downloads\BUILD_THE_HOUSE_v2_architecture.pdf'
    build_pdf(out)

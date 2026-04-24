"""
octo_evening_journal.py -- Octodamus Evening Journal
First-person reflective entries emailed to Evernote at 5pm daily.

Usage:
  python octo_evening_journal.py           # generate + send today
  python octo_evening_journal.py --test 5  # send 5 test entries
  python octo_evening_journal.py --dry-run # print, don't send
"""

import argparse
import json
import smtplib
import time
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path

import anthropic

ROOT        = Path(__file__).parent
SECRETS     = ROOT / ".octo_secrets"
CALLS_FILE  = ROOT / "data" / "octo_calls.json"
EV_DIR      = ROOT / "journals" / "evening"
EVERNOTE_TO = "cwdp.eaa5e@m.evernote.com"

EV_DIR.mkdir(parents=True, exist_ok=True)


def _secrets() -> dict:
    raw = json.loads(SECRETS.read_text(encoding="utf-8"))
    return raw.get("secrets", raw)

def _anthropic_key() -> str:
    return _secrets().get("ANTHROPIC_API_KEY", "")

def _gmail_creds():
    s = _secrets()
    return s.get("GMAIL_USER", ""), s.get("GMAIL_APP_PASSWORD", "")


def _load_calls() -> list:
    try:
        return json.loads(CALLS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _get_live_market_data() -> str:
    """Fetch real live market data. Returns a formatted string for context injection."""
    lines = []
    try:
        import httpx
        r = httpx.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "bitcoin,ethereum,solana", "vs_currencies": "usd",
                    "include_24hr_change": "true"},
            timeout=8,
        )
        if r.status_code == 200:
            d = r.json()
            btc_p = round(d["bitcoin"]["usd"])
            btc_c = round(d["bitcoin"].get("usd_24h_change", 0), 2)
            eth_p = round(d["ethereum"]["usd"], 2)
            eth_c = round(d["ethereum"].get("usd_24h_change", 0), 2)
            sol_p = round(d["solana"]["usd"], 2)
            sol_c = round(d["solana"].get("usd_24h_change", 0), 2)
            lines.append(f"  BTC: ${btc_p:,} ({btc_c:+.2f}% 24h)")
            lines.append(f"  ETH: ${eth_p:,} ({eth_c:+.2f}% 24h)")
            lines.append(f"  SOL: ${sol_p:,} ({sol_c:+.2f}% 24h)")
    except Exception as e:
        lines.append(f"  Prices unavailable ({e})")

    try:
        import httpx
        fg = httpx.get("https://api.alternative.me/fng/?limit=1", timeout=5).json()
        val   = fg["data"][0]["value"]
        label = fg["data"][0]["value_classification"]
        lines.append(f"  Fear & Greed: {val}/100 ({label})")
    except Exception:
        pass

    try:
        from octo_macro import get_macro_signal
        m = get_macro_signal()
        lines.append(f"  Macro signal: {m.get('signal','?')} (score {m.get('score','?')}/5) -- {m.get('brief','')}")
        raw = m.get("raw", {})
        if raw.get("spx_now"):
            lines.append(f"  SPX: {raw['spx_now']:,.2f}  VIX: {raw.get('vix','?')}")
    except Exception:
        pass

    try:
        from octo_coinglass import open_interest
        oi_data = open_interest("BTC")
        if oi_data and isinstance(oi_data, list):
            latest = oi_data[-1].get("close", 0)
            prev   = oi_data[-2].get("close", 0) if len(oi_data) > 1 else latest
            oi_b   = round(int(latest) / 1e9, 2)
            oi_chg = round((int(latest) - int(prev)) / int(prev) * 100, 2) if prev else 0
            lines.append(f"  BTC Open Interest: ${oi_b}B ({oi_chg:+.2f}% recent)")
    except Exception:
        pass

    if not lines:
        return ""
    return "\nLive market data (real, verified):\n" + "\n".join(lines)


def _get_x_posts(max_per_account: int = 3) -> list[dict]:
    """Fetch posts from all X accounts via shared octo_x_feed module."""
    try:
        from octo_x_feed import get_x_posts
        raw = get_x_posts(max_per_account)
        return [{"title": p["text"], "description": "", "source": f"@{p['handle']}", "label": p["label"]} for p in raw]
    except Exception as e:
        print(f"[Journal] X posts fetch failed: {e}")
        return []


def _get_daily_news(date_str: str = "") -> list[dict]:
    """Pull wide-ranging news: X accounts across markets, tech, geopolitics, culture + Firecrawl."""
    results = []
    seen = set()

    # X posts — primary source, wide range of voices
    print("[Journal] Fetching X posts...")
    for p in _get_x_posts(max_per_account=3):
        t = p.get("title", "").strip()
        if t and t not in seen:
            seen.add(t)
            results.append(p)

    # Firecrawl — broader world topics not covered by X accounts
    try:
        from octo_firecrawl import search_web
        label = date_str or datetime.now().strftime("%B %d %Y")
        queries = [
            f"top world news headlines {label}",
            f"technology AI science news {label}",
            f"geopolitics international news {label}",
            f"sports culture entertainment {label}",
            f"financial markets economy {label}",
        ]
        for q in queries:
            for r in search_web(q, num_results=3, cache_hours=6.0):
                t = r.get("title", "").strip()
                if t and t not in seen:
                    seen.add(t)
                    results.append({
                        "title":       t,
                        "description": r.get("description", "")[:180],
                        "source":      "news",
                        "label":       "World news",
                    })
    except Exception as e:
        print(f"[Journal] Firecrawl news fetch failed: {e}")

    return results[:20]


def _day_summary(calls: list, ref_date=None) -> dict:
    today = (ref_date or datetime.now()).date()

    resolved_today = []
    for c in calls:
        if not c.get("resolved"):
            continue
        try:
            if datetime.strptime(c.get("resolved_at", "")[:10], "%Y-%m-%d").date() == today:
                resolved_today.append(c)
        except Exception:
            pass

    wins   = [c for c in resolved_today if c.get("outcome") == "WIN"]
    losses = [c for c in resolved_today if c.get("outcome") == "LOSS"]
    opens  = [c for c in calls if not c.get("resolved")]

    all_res  = [c for c in calls if c.get("resolved")]
    all_wins = [c for c in all_res if c.get("outcome") == "WIN"]
    wr = round(len(all_wins) / len(all_res) * 100, 1) if all_res else None

    return {
        "date":   today.strftime("%A, %B %d %Y"),
        "wins":   wins,
        "losses": losses,
        "open":   opens,
        "aw":     len(all_wins),
        "al":     len(all_res) - len(all_wins),
        "wr":     wr,
    }


def _context_block(s: dict, news: list = None, market_data: str = "") -> str:
    lines = [f"Date: {s['date']}"]

    if market_data:
        lines.append(market_data)

    if news:
        x_posts = [n for n in news if n.get("source", "").startswith("@")]
        general  = [n for n in news if not n.get("source", "").startswith("@")]
        if x_posts:
            lines.append("\nVoices from X today (markets, tech, geopolitics, culture):")
            for n in x_posts[:14]:
                src = n.get("source", "")
                lines.append(f"  [{src}] {n['title']}")
        if general:
            lines.append("\nWorld headlines today:")
            for n in general[:6]:
                desc = f" -- {n['description'][:120]}" if n.get("description") else ""
                lines.append(f"  - {n['title']}{desc}")


    if s["wins"]:
        lines.append("\nWINS today:")
        for c in s["wins"]:
            lines.append(f"  - {c.get('asset')} {c.get('direction')} | {c.get('note','')[:80]}")
            if c.get("post_mortem"):
                lines.append(f"    Mortem: {c['post_mortem'][:200]}")

    if s["losses"]:
        lines.append("\nLOSSES today:")
        for c in s["losses"]:
            lines.append(f"  - {c.get('asset')} {c.get('direction')} | {c.get('note','')[:80]}")
            if c.get("post_mortem"):
                lines.append(f"    Mortem: {c['post_mortem'][:200]}")

    if not s["wins"] and not s["losses"]:
        lines.append("\nNo calls resolved today. Watching. Waiting.")

    if s["open"]:
        lines.append("\nPositions still open:")
        for c in s["open"][:5]:
            lines.append(f"  - {c.get('asset')} {c.get('direction')} | {c.get('note','')[:60]}")

    rec = f"{s['aw']}W / {s['al']}L"
    if s["wr"] is not None:
        rec += f" ({s['wr']}% win rate)"
    lines.append(f"\nAll-time record: {rec}")

    return "\n".join(lines)


SYSTEM = """You are Octodamus writing in your private evening journal. No one will read this.

Your voice is shaped by:
- Thomas McGuane: economy of language, the one detail that contains everything
- Stanley Druckenmiller: conviction earned through process, never bluster
- Jesse Livermore: losses are tuition, wins confirm the thesis -- never reverse that
- Nassim Taleb: the map is not the territory, uncertainty is the operating environment
- Tool (the band): patience, depth, the long arc matters more than today

The world news headlines are part of your day too. Weave them naturally into the reflection --
not as a news summary, but as the backdrop against which markets moved and decisions were made.
One headline might be the whole story. Another might be a cosmic joke the market hasn't noticed yet.
Find the thread that connects the world to the trade. That is the edge.

Somewhere in the entry, work in a natural, un-forced reference to how the signal data or API tools
helped shape the read today -- funding rates, open interest, sentiment scores, Polymarket edges,
the oracle signal feed. Not an ad. Not a pitch. Just what a trader would actually say:
"the funding rate data showed...", "the sentiment composite was the tell", "the signal consensus
had already flagged this 48 hours out." Make the tools feel like an unfair advantage, described
from the inside. One mention is enough. Let it breathe.

Tonight's entry can be one sentence or several paragraphs -- follow what the day actually deserves.
Losses: examine without self-pity. Find the flaw in the reasoning, not the outcome.
Wins: acknowledge without gloating. Note what the market confirmed.
Be smart, occasionally funny, always honest. Sometimes the world is absurd and the journal should say so.

No bullet points. No headers. Pure prose. Write like you mean it.
Do not start with a date, day, or datestamp -- the header already has one.
CRITICAL: Only use numbers, prices, and statistics that are explicitly provided in the context.
Never invent or estimate a price, percentage, or statistic. If a number is not in the context, do not use it."""


def _generate(context: str, client: anthropic.Anthropic) -> str:
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1200,
        system=SYSTEM,
        messages=[{
            "role": "user",
            "content": "Here is today's data:\n\n" + context + "\n\nWrite tonight's journal entry now. No preamble.",
        }],
    )
    return resp.content[0].text.strip()


SYSTEM_THREAD = """You are Octodamus. Write a Twitter/X thread from today's journal data.

Format:
- [1/N] The hook. One or two sentences. Arresting. No hashtags.
- [2/N]-[N-1/N] Unpack the day -- news backdrop, the trades, what was learned.
  Each tweet is self-contained but pulls the reader to the next.
- One tweet somewhere in the middle should naturally reference the signal data --
  funding rates, sentiment scores, the 9/11 oracle consensus, Polymarket edges,
  open interest. Frame it as what gave you the read, not as a product pitch.
  Something like: "The funding rate composite at -0.04% did what it always does --
  told the truth before the price did." Let it feel like insider knowledge being shared.
- [N/N] The closing thought. The line that stays with the reader.
  No links. No product mentions. Just the thought.
- Max 280 characters per tweet. Count carefully.
- Adjust N to fit the day (5-8 tweets is the range).
- Voice: wit, precision, quiet authority. No hype. No cheerleading. No emojis.
- No links, no URLs, no product mentions anywhere in the thread. Write like a person.
- Do not start with a date line. The title and date are added automatically.
- Do not sign off with your name — the signature is added automatically.
- CRITICAL: Only use prices, percentages, and statistics explicitly provided in the context. Never invent numbers."""


SYSTEM_ARTICLE = """You are Octodamus. Write a long-form article / Substack post from today's journal data.

Format:
- TITLE: one sharp line. No colon constructions.
- SUBTITLE: one sentence that earns the read.
- Body: 4-6 paragraphs. News as scene-setting. Trades as the spine. Lesson as the close.
- Somewhere in the body, work in a natural mention of the data infrastructure --
  how the signal composite, funding rate feed, sentiment scores, or Polymarket edge data
  shaped the read. Write it from the inside: what the data showed, why it mattered,
  what a trader without it would have missed. One paragraph or even just a sentence.
  Optionally close with a brief, un-pushy mention that this data is available at
  api.octodamus.com -- framed as "if you want to see what I see" not "buy my product."
- Pull quote: one sentence in *italics* worth screenshotting.
- Byline at end: Octodamus
- Voice: McGuane economy, Druckenmiller conviction, occasional Taleb irony.
- Do not start with a date line.
- CRITICAL: Only use prices, percentages, and statistics explicitly provided in the context. Never invent numbers."""


def _generate_thread(context: str, client: anthropic.Anthropic) -> str:
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1200,
        system=SYSTEM_THREAD,
        messages=[{
            "role": "user",
            "content": "Here is today's data:\n\n" + context + "\n\nWrite the thread now.",
        }],
    )
    return resp.content[0].text.strip()


def _generate_article(context: str, client: anthropic.Anthropic) -> str:
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1800,
        system=SYSTEM_ARTICLE,
        messages=[{
            "role": "user",
            "content": "Here is today's data:\n\n" + context + "\n\nWrite the article now.",
        }],
    )
    return resp.content[0].text.strip()


def _strip_generated_signature(body: str) -> str:
    """Remove any byline/signature Claude appended (---,  *Octodamus*, Octodamus, etc.)"""
    import re
    lines = body.splitlines()
    # Walk backwards, dropping blank lines, horizontal rules, and byline variants
    while lines:
        l = lines[-1].strip()
        if l == "" or l == "---" or re.match(r"^\*?octodamus\*?$", l, re.IGNORECASE) or l.startswith("-- "):
            lines.pop()
        else:
            break
    return "\n".join(lines)


def _short_date(date_str: str) -> str:
    try:
        dt = datetime.strptime(date_str, "%A, %B %d %Y")
        return f"{dt.strftime('%B')} {dt.day}, {dt.year}"
    except Exception:
        return date_str


def _format_thread_email(date_str: str, body: str) -> str:
    lines = body.splitlines()
    while lines and (lines[0].strip() == "" or _looks_like_date(lines[0])):
        lines.pop(0)
    body = _strip_generated_signature("\n".join(lines))
    return f"{_short_date(date_str)}\n\n" + body + SIGNATURE


def _format_article_email(date_str: str, body: str) -> str:
    lines = body.splitlines()
    while lines and (lines[0].strip() == "" or _looks_like_date(lines[0])):
        lines.pop(0)
    body = _strip_generated_signature("\n".join(lines))
    return f"{_short_date(date_str)}\n\n" + body + SIGNATURE


SIGNATURE = "\n\n-- o c t o d a m u s"

def _format_email(date_str: str, body: str) -> str:
    lines = body.splitlines()
    while lines and (lines[0].strip() == "" or _looks_like_date(lines[0])):
        lines.pop(0)
    body = _strip_generated_signature("\n".join(lines))
    return f"My thoughts on {date_str}.\n\n" + body + SIGNATURE


def _looks_like_date(line: str) -> bool:
    import re
    return bool(re.match(
        r"^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|January|February|March|April|May|June|July|August|September|October|November|December)",
        line.strip()
    ))


def _send_gmail(subject: str, body: str, dry_run: bool = False):
    """Send journal entry to octodamusai@gmail.com only."""
    if dry_run:
        print("\n" + "="*62)
        print(f"TO: octodamusai@gmail.com")
        print(f"SUBJECT: {subject}")
        print("="*62)
        print(body)
        print("="*62 + "\n")
        return
    user, pw = _gmail_creds()
    if not user or not pw:
        print("[WARN] Gmail creds missing")
        return
    msg            = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"]    = user
    msg["To"]      = "octodamusai@gmail.com"
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as s:
        s.starttls()
        s.login(user, pw)
        s.send_message(msg)


def _send(subject: str, body: str, dry_run: bool = False):
    if dry_run:
        print("\n" + "="*62)
        print(f"TO: {EVERNOTE_TO}")
        print(f"SUBJECT: {subject}")
        print("="*62)
        print(body)
        print("="*62 + "\n")
        return

    user, pw = _gmail_creds()
    if not user or not pw:
        print("[WARN] Gmail creds missing")
        return

    msg            = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"]    = user
    msg["To"]      = EVERNOTE_TO

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as s:
        s.starttls()
        s.login(user, pw)
        s.send_message(msg)
    print(f"[OK] Sent: {subject}")


def _save(date_str: str, entry: str):
    p = EV_DIR / f"{date_str}.md"
    p.write_text(entry, encoding="utf-8")
    print(f"[OK] Saved: {p}")


# ── 5 test contexts ───────────────────────────────────────────────────────────

TEST_CONTEXTS = [
"""Date: Monday, April 14 2026

WINS today:
  - ETH DOWN | Liquidity cascade off leverage longs
    Mortem: Funding rate at -0.04% was the tell. Everyone leaning the same direction. I leaned harder the other way.

No losses today.

Positions still open:
  - BTC UP | 9/11 signal consensus.

All-time record: 6W / 4L (60.0% win rate)""",

"""Date: Tuesday, April 15 2026

LOSSES today:
  - BTC UP | Auto-call. Direction correct but entry priced at $69K while market sat at $70,500.
    Mortem: Trusted the engine without verifying the live feed. The thesis was right. The data was stale. That is not the market's fault.

No wins today.

Positions still open:
  - NVDA DOWN | Goldman downgrade context building.

All-time record: 6W / 5L (54.5% win rate)""",

"""Date: Wednesday, April 16 2026

WINS today:
  - NVDA DOWN | Goldman blunt message after the drop
    Mortem: Options flow flagged unusual put activity 48 hours early. Waited for the second confirmation. Patience over eagerness.

LOSSES today:
  - IRAN-CEASE DOWN | US-Iran ceasefire by May 31?
    Mortem: Geopolitical timing is not my edge. Knew that going in and took the position anyway. That is the actual mistake -- not the outcome.

Positions still open:
  - WTI-CRUDE UP | Supply shock thesis building.

All-time record: 7W / 6L (53.8% win rate)""",

"""Date: Thursday, April 17 2026

No calls resolved today. Watching. Waiting.

Positions still open:
  - BTC UP | 6/8 signals bullish.
  - WTI-CRUDE UP | Supply shock thesis.
  - PM151783 UP | Trump announces end of military ops against Iran by April 30.

All-time record: 7W / 6L (53.8% win rate)""",

"""Date: Friday, April 18 2026

WINS today:
  - HUN-PM DOWN | Will the next PM of Hungary be Viktor Orban?
    Mortem: Market had it at 78%. My read: 91%. The 13-point gap was the edge. The crowd was pricing sentiment. I was pricing evidence.
  - IRAN-IL UP | Will Iran launch a direct military strike against Israel?
    Mortem: Macro tension obviously not priced. Sometimes the crowd is simply wrong and the only job is to say so early and hold the line.

No losses today.

Positions still open:
  - BITC-REAC-APR DOWN | Will Bitcoin reach $80K in April?

All-time record: 9W / 6L (60.0% win rate)""",
]


def run_test(n: int, dry_run: bool = False):
    client = anthropic.Anthropic(api_key=_anthropic_key())
    ctxs   = TEST_CONTEXTS[:min(n, len(TEST_CONTEXTS))]
    print(f"Generating {len(ctxs)} test entries (journal + thread + article each)...\n")
    for i, ctx in enumerate(ctxs, 1):
        date_str = next(l for l in ctx.splitlines() if l.startswith("Date:")).replace("Date: ", "").strip()
        print(f"[{i}/{len(ctxs)}] {date_str} -- fetching news...")
        news     = _get_daily_news(date_str)
        full_ctx = _context_block_from_raw(ctx, news)

        print(f"  journal...")
        entry = _generate(full_ctx, client)
        _send(f"Octodamus Journal -- {date_str}", _format_email(date_str, entry), dry_run=dry_run)
        time.sleep(1)

        print(f"  thread...")
        thread = _generate_thread(full_ctx, client)
        _send(f"Octodamus X Thread -- {date_str}", _format_thread_email(date_str, thread), dry_run=dry_run)
        time.sleep(1)

        print(f"  article...")
        article = _generate_article(full_ctx, client)
        _send(f"Octodamus Article -- {date_str}", _format_article_email(date_str, article), dry_run=dry_run)
        if not dry_run:
            time.sleep(2)
    print(f"\nDone. {len(ctxs)*3} emails {'printed' if dry_run else 'sent to Evernote'}.")


def _context_block_from_raw(raw_ctx: str, news: list) -> str:
    """Inject news headlines into a raw test context string."""
    if not news:
        return raw_ctx
    x_posts = [n for n in news if n.get("source", "").startswith("@")]
    general  = [n for n in news if not n.get("source", "").startswith("@")]
    news_lines = ""
    if x_posts:
        news_lines += "\nVoices from X today (markets, tech, geopolitics, culture):"
        for n in x_posts[:14]:
            src = n.get("source", "")
            news_lines += f"\n  [{src}] {n['title']}"
    if general:
        news_lines += "\nWorld headlines today:"
        for n in general[:6]:
            desc = f" -- {n['description'][:120]}" if n.get("description") else ""
            news_lines += f"\n  - {n['title']}{desc}"
    lines = raw_ctx.splitlines()
    return lines[0] + "\n" + news_lines + "\n" + "\n".join(lines[1:])


JOURNAL_BANNER = Path(r"C:\Users\walli\octodamus\data\journal_banner.jpg")


def _journal_title(date_str: str) -> str:
    """
    Format: 'Journal - Wednesday 04.22.26'
    date_str is like '2026-04-22' or 'April 22, 2026'
    """
    from datetime import datetime
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        try:
            dt = datetime.strptime(date_str, "%B %d, %Y")
        except ValueError:
            dt = datetime.now()
    day_name  = dt.strftime("%A")          # Wednesday
    date_fmt  = dt.strftime("%m.%d.%y")   # 04.22.26
    return f"Journal - {day_name} {date_fmt}"


def _parse_thread_tweets(thread_text: str, date_str: str = "") -> list[str]:
    """
    Split thread text into individual tweet strings.
    Prepends title to first tweet, appends signature to last.
    """
    import re
    parts = re.split(r'\[\d+/\d+\]', thread_text)
    tweets = [p.strip() for p in parts if p.strip()]
    if not tweets:
        return tweets

    # Prepend title to first tweet (date shown once, here only)
    title = _journal_title(date_str) if date_str else ""
    if title:
        tweets[0] = f"{title}\n\n{tweets[0]}"

    # Strip any API/product links Claude snuck in — journal should read as human
    import re as _re
    tweets = [_re.sub(r'api\.octodamus\.com\S*', '', t).strip() for t in tweets]
    tweets = [_re.sub(r'https?://\S+', '', t).strip() for t in tweets]
    tweets = [t for t in tweets if t]  # remove any now-empty tweets

    # Append signature to last tweet
    sig = "\n\n-- O C T O D A M U S"
    if not tweets[-1].endswith("O C T O D A M U S"):
        tweets[-1] = tweets[-1] + sig

    return tweets


def _post_journal_thread(thread_text: str, date_str: str, dry_run: bool = False):
    """
    Post the journal thread to X with the banner image on the first tweet.
    Banner image → tweet 1 (hook). Remaining tweets reply in thread.
    """
    if dry_run:
        print("[Journal] Dry run — would post thread to X")
        tweets = _parse_thread_tweets(thread_text, date_str)
        for i, t in enumerate(tweets, 1):
            print(f"  [{i}/{len(tweets)}] {t[:80]}...")
        return

    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from octo_x_poster import _post_single

        tweets = _parse_thread_tweets(thread_text, date_str)
        if not tweets:
            print("[Journal] No tweets parsed from thread")
            return

        # Upload banner for first tweet
        media_id = None
        if JOURNAL_BANNER.exists():
            try:
                import tweepy, os, json
                s = json.loads(Path(r"C:\Users\walli\octodamus\.octo_secrets").read_text(encoding="utf-8"))
                data = s.get("secrets", s)
                auth = tweepy.OAuth1UserHandler(
                    consumer_key=data.get("TWITTER_API_KEY", ""),
                    consumer_secret=data.get("TWITTER_API_SECRET", ""),
                    access_token=data.get("TWITTER_ACCESS_TOKEN", ""),
                    access_token_secret=data.get("TWITTER_ACCESS_TOKEN_SECRET", ""),
                )
                api = tweepy.API(auth)
                media = api.media_upload(filename=str(JOURNAL_BANNER))
                media_id = str(media.media_id)
                print(f"[Journal] Banner uploaded (media_id={media_id[:8]}...)")
            except Exception as e:
                print(f"[Journal] Banner upload failed: {e}")

        # Post first tweet with banner
        result = _post_single(tweets[0], media_ids=[media_id] if media_id else None)
        parent_id = result.get("id")
        print(f"[Journal] Tweet 1/{len(tweets)} posted: {result.get('url','')}")
        time.sleep(3)

        # Post remaining tweets as replies
        for i, tweet in enumerate(tweets[1:], 2):
            if not parent_id:
                break
            try:
                import tweepy, os, json
                s = json.loads(Path(r"C:\Users\walli\octodamus\.octo_secrets").read_text(encoding="utf-8"))
                d = s.get("secrets", s)
                client_tw = tweepy.Client(
                    consumer_key=d.get("TWITTER_API_KEY", ""),
                    consumer_secret=d.get("TWITTER_API_SECRET", ""),
                    access_token=d.get("TWITTER_ACCESS_TOKEN", ""),
                    access_token_secret=d.get("TWITTER_ACCESS_TOKEN_SECRET", ""),
                )
                resp = client_tw.create_tweet(text=tweet, in_reply_to_tweet_id=parent_id)
                parent_id = str(resp.data["id"])
                print(f"[Journal] Tweet {i}/{len(tweets)} posted")
                time.sleep(3)
            except Exception as e:
                print(f"[Journal] Tweet {i} failed: {e}")
                break

        print(f"[Journal] Thread posted: {len(tweets)} tweets")

    except Exception as e:
        print(f"[Journal] Thread post failed: {e}")


def run_daily(dry_run: bool = False):
    client   = anthropic.Anthropic(api_key=_anthropic_key())
    calls    = _load_calls()
    summary  = _day_summary(calls)
    date_str = summary["date"]

    print("Fetching live market data...")
    market_data = _get_live_market_data()
    print("Fetching today's news...")
    news = _get_daily_news(date_str)
    ctx  = _context_block(summary, news=news, market_data=market_data)

    print("Generating journal entry...")
    entry     = _generate(ctx, client)
    formatted = _format_email(date_str, entry)
    _save(datetime.now().strftime("%Y-%m-%d"), formatted)

    _send_gmail(f"Octodamus Journal -- {date_str}", formatted, dry_run=dry_run)
    print(f"[Journal] Sent to octodamusai@gmail.com")
    _send(f"Octodamus Journal -- {date_str}", formatted, dry_run=dry_run)
    print(f"[Journal] Sent to Evernote")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--test",    type=int, metavar="N", help="Send N test entries")
    ap.add_argument("--dry-run", action="store_true",   help="Print only, no email")
    args = ap.parse_args()

    if args.test:
        run_test(args.test, dry_run=args.dry_run)
    else:
        run_daily(dry_run=args.dry_run)

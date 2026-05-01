"""
octo_x_engage.py -- Octodamus X account reply engine.

Two-phase per session:
  Phase 1 (Harvest): Fetch replies received on our previously posted replies,
                     distill lessons via Haiku, append to octodamus_core.md
                     so the personality compounds from every engagement.
  Phase 2 (Engage):  Discover high-value original tweets from a curated watch
                     list, read core memory for what angles have worked, ask
                     Haiku to generate a reply or SKIP, post, record for harvest.

Schedule: Octodamus-XEngage (3x/day: 8am, 1pm, 3:30pm via Task Scheduler)
CLI:
  python octo_x_engage.py              # full session (harvest + engage)
  python octo_x_engage.py --dry-run    # generate but don't post
  python octo_x_engage.py --harvest-only
  python octo_x_engage.py --engage-only
  python octo_x_engage.py --status
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import tweepy

PROJECT_DIR = Path(__file__).parent
STATE_FILE  = PROJECT_DIR / "data" / "x_engage_state.json"

# ── Hard limits ────────────────────────────────────────────────────────────────
MAX_REPLIES_PER_SESSION  = 2
MAX_REPLIES_PER_DAY      = 6
ACCOUNT_COOLDOWN_HOURS   = 10    # min hours between replies to same account
TWEET_MAX_AGE_HOURS      = 5     # ignore tweets older than this
REPLY_MAX_CHARS          = 255   # safety margin under X's 280
FEEDBACK_MIN_AGE_HOURS   = 2     # wait before harvesting (give replies time to arrive)
FEEDBACK_MAX_AGE_HOURS   = 36    # stop checking after 36h (threads go cold)

# ── Curated watch list ─────────────────────────────────────────────────────────
# These are not all follows -- specifically selected for domain overlap and audience quality.
WATCH_ACCOUNTS = [
    # Macro / Finance
    "KobeissiLetter", "RaoulGMI", "CryptoHayes", "PeterSchiff", "MacroAlf",
    "LynAldenContact",
    # BTC / Crypto analysts
    "saylor", "woonomic", "DylanLeClair_", "100trillionUSD", "NorthStarBTC",
    # On-chain / Data
    "glassnode", "WillyWoo", "CryptoQuant_io",
    # Markets / Options flow
    "unusual_whales", "AutismCapital",
    # AI / Agents
    "virtuals_io", "balajis",
    # Prediction markets
    "Polymarket",
]
# Twitter search query max is 512 chars. Batch size of 12 keeps each query safe.
_BATCH_SIZE = 12

# ── Domain keywords -- tweet must match at least one ──────────────────────────
_DOMAIN_KW = [
    "bitcoin", "btc", "ethereum", "eth", "crypto", "macro", "fed", "inflation",
    "interest rate", "yield", "markets", "stock", "nvda", "nvidia", "tesla", "tsla",
    "polymarket", "prediction", "oracle", "ai agent", "defi", "liquidation",
    "fear", "greed", "rally", "dump", "bull", "bear", "recession", "gdp", "cpi",
    "dollar", "dxy", "gold", "oil", "tariff", "debt", "onchain", "stablecoin",
    "usdc", "base", "solana", "sol", "options", "futures", "funding rate",
    "powell", "treasury", "rate cut", "rate hike", "quantitative",
]


# ══════════════════════════════════════════════════════════════════════════════
# STATE
# ══════════════════════════════════════════════════════════════════════════════

def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "replied_tweet_ids":  [],
        "account_last_reply": {},
        "daily_counts":       {},
        "pending_feedback":   [],
        "session_log":        [],
    }


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


# ══════════════════════════════════════════════════════════════════════════════
# DAILY CAP
# ══════════════════════════════════════════════════════════════════════════════

def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _daily_count(state: dict) -> int:
    return state.get("daily_counts", {}).get(_today(), 0)


def _increment_daily(state: dict) -> None:
    counts = state.setdefault("daily_counts", {})
    counts[_today()] = counts.get(_today(), 0) + 1
    keep = {(datetime.now(timezone.utc) - timedelta(days=i)).strftime("%Y-%m-%d")
            for i in range(7)}
    state["daily_counts"] = {k: v for k, v in counts.items() if k in keep}


# ══════════════════════════════════════════════════════════════════════════════
# TWITTER CLIENT + LIVE CONTEXT
# ══════════════════════════════════════════════════════════════════════════════

def _get_client() -> tweepy.Client:
    return tweepy.Client(
        bearer_token        = os.environ.get("TWITTER_BEARER_TOKEN", ""),
        consumer_key        = os.environ.get("TWITTER_API_KEY", ""),
        consumer_secret     = os.environ.get("TWITTER_API_SECRET", ""),
        access_token        = os.environ.get("TWITTER_ACCESS_TOKEN", ""),
        access_token_secret = os.environ.get("TWITTER_ACCESS_TOKEN_SECRET", ""),
        wait_on_rate_limit  = False,
    )


def _live_context() -> str:
    try:
        from financial_data_client import get_crypto_prices, get_fear_greed
        prices = get_crypto_prices()
        fg     = get_fear_greed()
        btc    = prices.get("bitcoin", {}).get("usd", 0)
        fg_val = fg.get("value", "?")
        fg_lbl = fg.get("label", "")
        return f"BTC ${btc:,.0f} | Fear & Greed: {fg_val}/100 ({fg_lbl})"
    except Exception:
        return ""


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 1 -- HARVEST FEEDBACK
# Checks replies to our previously posted replies.
# Distills lessons into octodamus_core.md so future sessions learn from them.
# ══════════════════════════════════════════════════════════════════════════════

def _fetch_replies_to_ours(client: tweepy.Client, original_tweet_id: str,
                            our_reply_id: str, posted_after: str) -> list[dict]:
    """
    Search the conversation for tweets that replied specifically to our reply.
    Uses conversation_id search + client-side filter on referenced_tweets.
    """
    try:
        resp = client.search_recent_tweets(
            query        = f"conversation_id:{original_tweet_id} -from:octodamusai",
            max_results  = 20,
            tweet_fields = ["author_id", "created_at", "text",
                            "public_metrics", "referenced_tweets"],
            expansions   = ["author_id", "referenced_tweets.id"],
            user_fields  = ["username"],
            start_time   = posted_after,
        )
    except tweepy.errors.TweepyException as e:
        print(f"[XEngage/Harvest] Twitter error: {e}")
        return []

    if not resp.data:
        return []

    user_map = {}
    if resp.includes and "users" in resp.includes:
        for u in resp.includes["users"]:
            user_map[u.id] = u.username

    our_id_int = int(our_reply_id) if our_reply_id.isdigit() else -1
    results = []
    for tweet in resp.data:
        refs = tweet.referenced_tweets or []
        is_reply_to_ours = any(
            r.id == our_id_int and r.type == "replied_to"
            for r in refs
        )
        if not is_reply_to_ours:
            continue
        m = tweet.public_metrics or {}
        results.append({
            "author": user_map.get(tweet.author_id, "unknown"),
            "text":   tweet.text,
            "likes":  m.get("like_count", 0),
        })

    return results


def _fetch_our_reply_metrics(client: tweepy.Client, our_reply_id: str) -> dict:
    try:
        resp = client.get_tweet(our_reply_id, tweet_fields=["public_metrics"])
        if resp.data:
            return dict(resp.data.public_metrics or {})
    except Exception:
        pass
    return {}


def _distill_lesson(account: str, original_text: str, our_reply: str,
                     responses: list[dict], metrics: dict) -> str | None:
    """
    Ask Haiku to extract 1-2 actionable lessons from engagement feedback.
    Returns lesson string or None if nothing worth capturing.
    """
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None

    likes = metrics.get("like_count", 0)
    rts   = metrics.get("retweet_count", 0)
    resp_block = "\n".join(
        f"  @{r['author']} ({r['likes']} likes): {r['text'][:200]}"
        for r in responses[:5]
    ) if responses else "  (no replies received)"

    prompt = f"""You are analyzing engagement results for Octodamus, an AI market oracle.

Original tweet by @{account}:
"{original_text[:300]}"

Octodamus replied:
"{our_reply}"

Our reply performance: {likes} likes, {rts} retweets
Replies we received:
{resp_block}

Extract 1-2 specific, actionable lessons for future replies:
- What angle or data point landed well? (if engagement was positive)
- What pushback came and was it valid?
- What does @{account}'s audience respond to?
- What to repeat or avoid with this account or similar accounts?

Be specific and concrete. Max 3 sentences total.
If there is genuinely nothing to learn (zero engagement, zero replies, no signal): output NOTHING."""

    try:
        claude = anthropic.Anthropic(api_key=api_key)
        msg = claude.messages.create(
            model      = "claude-haiku-4-5-20251001",
            max_tokens = 120,
            messages   = [{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text.strip()
        return None if text.upper() == "NOTHING" or not text else text
    except Exception as e:
        print(f"[XEngage/Harvest] Haiku distill error: {e}")
        return None


def harvest_feedback(state: dict, dry_run: bool = False) -> int:
    """
    Phase 1: process pending reply feedback, distill into octodamus_core.md.
    Returns number of entries processed.
    """
    from octo_memory_db import append_core_memory

    pending   = state.get("pending_feedback", [])
    now       = datetime.now(timezone.utc)
    client    = _get_client()
    processed = 0

    for entry in pending:
        if entry.get("feedback_checked"):
            continue

        posted_at = datetime.fromisoformat(entry["posted_at"])
        age_hours = (now - posted_at).total_seconds() / 3600

        if age_hours < FEEDBACK_MIN_AGE_HOURS:
            continue  # too fresh -- let replies accumulate
        if age_hours > FEEDBACK_MAX_AGE_HOURS:
            entry["feedback_checked"] = True  # thread gone cold
            continue

        acct = entry["account"]
        print(f"[XEngage/Harvest] Checking feedback for reply to @{acct}...")

        metrics   = _fetch_our_reply_metrics(client, entry["our_reply_id"])
        responses = _fetch_replies_to_ours(
            client,
            original_tweet_id = entry["original_tweet_id"],
            our_reply_id      = entry["our_reply_id"],
            posted_after      = entry["posted_at"],
        )

        likes = metrics.get("like_count", 0)
        rts   = metrics.get("retweet_count", 0)
        print(f"[XEngage/Harvest]   {likes}L {rts}RT | {len(responses)} reply(ies)")

        lesson = _distill_lesson(
            account       = acct,
            original_text = entry["original_text"],
            our_reply     = entry["reply_text"],
            responses     = responses,
            metrics       = metrics,
        )

        # Mark regardless so we don't re-check
        entry["feedback_checked"] = True
        entry["reply_likes"]      = likes
        entry["reply_rts"]        = rts
        entry["responses_count"]  = len(responses)

        if lesson:
            print(f"[XEngage/Harvest]   Lesson: {lesson}")
            if not dry_run:
                append_core_memory(
                    "octodamus",
                    f"Engagement Learning -- @{acct}",
                    lesson,
                )
        else:
            print("[XEngage/Harvest]   No lesson distilled.")

        processed += 1
        time.sleep(1.5)

    # Prune entries older than 7 days
    cutoff = (now - timedelta(days=7)).isoformat()
    state["pending_feedback"] = [
        e for e in pending if e.get("posted_at", "9999") > cutoff
    ]

    return processed


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 2 -- DISCOVER + ENGAGE
# ══════════════════════════════════════════════════════════════════════════════

def _fetch_candidates(state: dict) -> list[dict]:
    """
    Search for recent original tweets from WATCH_ACCOUNTS via Twitter v2 search.
    Filters: domain relevance, age, account cooldown, already-replied dedup.
    Returns candidates sorted by engagement (high-reach first).
    """
    client        = _get_client()
    replied_ids   = set(state.get("replied_tweet_ids", []))
    account_last  = state.get("account_last_reply", {})
    now           = datetime.now(timezone.utc)
    cutoff_time   = now - timedelta(hours=TWEET_MAX_AGE_HOURS)
    cooldown_td   = timedelta(hours=ACCOUNT_COOLDOWN_HOURS)

    # Batch accounts to stay under Twitter's 512-char query limit
    batches = [WATCH_ACCOUNTS[i:i + _BATCH_SIZE]
               for i in range(0, len(WATCH_ACCOUNTS), _BATCH_SIZE)]

    all_tweets = []
    user_map   = {}

    for batch in batches:
        from_clause = " OR ".join(f"from:{a}" for a in batch)
        query = f"({from_clause}) -is:reply -is:retweet lang:en"
        try:
            resp = client.search_recent_tweets(
                query        = query,
                max_results  = 20,
                tweet_fields = ["author_id", "created_at", "text",
                                "public_metrics", "conversation_id"],
                expansions   = ["author_id"],
                user_fields  = ["username"],
                start_time   = cutoff_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
            if resp.data:
                all_tweets.extend(resp.data)
            if resp.includes and "users" in resp.includes:
                for u in resp.includes["users"]:
                    user_map[u.id] = u.username
        except tweepy.errors.TweepyException as e:
            print(f"[XEngage] Twitter API error (batch): {e}")
        time.sleep(1)  # stay inside rate limits between batch calls

    if not all_tweets:
        print("[XEngage] No recent tweets from watch list.")
        return []

    candidates = []
    for tweet in all_tweets:
        tweet_id = str(tweet.id)
        account  = user_map.get(tweet.author_id, "unknown")
        text     = tweet.text

        if tweet_id in replied_ids:
            continue

        if account in account_last:
            last_dt = datetime.fromisoformat(account_last[account])
            if now - last_dt < cooldown_td:
                continue

        if not any(kw in text.lower() for kw in _DOMAIN_KW):
            continue

        m = tweet.public_metrics or {}
        candidates.append({
            "tweet_id":       tweet_id,
            "conversation_id": str(tweet.conversation_id) if tweet.conversation_id else tweet_id,
            "account":        account,
            "text":           text,
            "created_at":     tweet.created_at.isoformat() if tweet.created_at else now.isoformat(),
            "likes":          m.get("like_count", 0),
            "retweets":       m.get("retweet_count", 0),
        })

    # Sort by weighted engagement -- more reach = more value in a reply
    candidates.sort(key=lambda x: x["likes"] + x["retweets"] * 3, reverse=True)
    print(f"[XEngage] {len(candidates)} candidate(s) after filtering.")
    return candidates[:6]


def _generate_reply(tweet: dict, live_ctx: str, core_memory: str) -> str | None:
    """
    Ask Haiku: does this tweet warrant a reply? If yes, generate it.
    Injects relevant engagement learnings from core_memory as context.
    Returns reply text or None (SKIP).
    """
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None

    # Extract just the Engagement Learning sections from core memory
    mem_excerpt = ""
    if "Engagement Learning" in core_memory:
        lines = core_memory.split("\n")
        in_section, relevant = False, []
        for line in lines:
            if "Engagement Learning" in line and line.startswith("##"):
                in_section = True
            elif line.startswith("## ") and in_section:
                in_section = False
            if in_section:
                relevant.append(line)
        mem_excerpt = "\n".join(relevant[-40:])  # last 40 lines keeps it tight

    prompt = f"""You are Octodamus, an AI oracle for crypto and macro markets.
Voice: dry, data-first, contrarian when data supports it. No hype. No filler.

A tweet has been flagged as a reply opportunity. Decide if you can add specific
insight the original tweet does NOT already contain. If yes, write the reply.
If no, output SKIP.

Live market context: {live_ctx}

What has worked in past replies (your own memory):
{mem_excerpt if mem_excerpt else "No engagement history yet -- build it."}

Tweet by @{tweet["account"]} ({tweet["likes"]} likes, {tweet["retweets"]} retweets):
"{tweet["text"]}"

Reply rules:
- Must add a data point, signal reading, prediction, or non-obvious angle
- Never agree/disagree without adding NEW information
- Max 255 characters (enforced at posting)
- No hashtags -- they read as spam in threads
- No emojis unless the context absolutely earns it
- Never start with: "Great point", "Interesting", "Exactly", or any validation phrase
- Never start with a greeting, a date, or an @mention as the first word
- If the tweet is pure opinion with no market data hook: SKIP
- If it is a meme, political take unrelated to markets, or sports: SKIP
- If you would need to invent data to have something to say: SKIP
- If the account is bearish and the data agrees: say so plainly with the number
- If the account is bullish and the data contradicts: say so plainly with the number

Output ONLY the reply text or the single word SKIP. No preamble, no explanation."""

    try:
        claude = anthropic.Anthropic(api_key=api_key)
        msg = claude.messages.create(
            model      = "claude-haiku-4-5-20251001",
            max_tokens = 160,
            messages   = [{"role": "user", "content": prompt}],
        )
        reply = msg.content[0].text.strip()

        if reply.upper().startswith("SKIP") or reply.lower() == "skip":
            return None

        # Trim to char limit at sentence boundary
        if len(reply) > REPLY_MAX_CHARS:
            trimmed = reply[:REPLY_MAX_CHARS]
            last_dot = trimmed.rfind(".")
            reply = trimmed[:last_dot + 1] if last_dot > 80 else trimmed

        return reply

    except Exception as e:
        print(f"[XEngage] Haiku error: {e}")
        return None


def engage(state: dict, dry_run: bool = False) -> int:
    """
    Phase 2: fetch candidates, generate replies, post, record for feedback harvest.
    Returns number of replies posted this session.
    """
    from octo_x_poster import post_reply, _is_internal_reasoning
    from octo_memory_db import read_core_memory

    if _daily_count(state) >= MAX_REPLIES_PER_DAY:
        print(f"[XEngage] Daily cap ({MAX_REPLIES_PER_DAY}) reached. Skipping.")
        return 0

    candidates = _fetch_candidates(state)
    if not candidates:
        return 0

    live_ctx    = _live_context()
    core_memory = read_core_memory("octodamus")
    print(f"[XEngage] Market context: {live_ctx}")

    posted   = 0
    now_str  = datetime.now(timezone.utc).isoformat()

    for tweet in candidates:
        if posted >= MAX_REPLIES_PER_SESSION:
            break
        if _daily_count(state) >= MAX_REPLIES_PER_DAY:
            break

        print(f"[XEngage] Evaluating @{tweet['account']}: {tweet['text'][:80]}...")
        reply_text = _generate_reply(tweet, live_ctx, core_memory)

        if not reply_text:
            print("[XEngage]   -> SKIP")
            time.sleep(0.5)
            continue

        if _is_internal_reasoning(reply_text):
            print("[XEngage]   -> SKIP (internal reasoning leaked)")
            continue

        print(f"[XEngage]   -> REPLY ({len(reply_text)} chars): {reply_text}")

        if dry_run:
            print("[XEngage]   [DRY RUN -- not posted]")
            posted += 1
            continue

        try:
            result       = post_reply(reply_text, tweet["tweet_id"])
            our_reply_id = result.get("id", "")
            print(f"[XEngage]   Posted: {result.get('url', '')}")

            # Queue for feedback harvest (Phase 1 next session)
            state.setdefault("pending_feedback", []).append({
                "original_tweet_id": tweet["tweet_id"],
                "conversation_id":   tweet["conversation_id"],
                "our_reply_id":      our_reply_id,
                "account":           tweet["account"],
                "original_text":     tweet["text"][:300],
                "reply_text":        reply_text,
                "posted_at":         now_str,
                "feedback_checked":  False,
            })

            state.setdefault("replied_tweet_ids", []).append(tweet["tweet_id"])
            # Keep dedup list bounded
            state["replied_tweet_ids"] = state["replied_tweet_ids"][-2000:]

            state.setdefault("account_last_reply", {})[tweet["account"]] = now_str
            _increment_daily(state)

            state.setdefault("session_log", []).append({
                "posted_at": now_str,
                "account":   tweet["account"],
                "tweet_id":  tweet["tweet_id"],
                "reply_id":  our_reply_id,
                "original":  tweet["text"][:120],
                "reply":     reply_text,
                "url":       result.get("url", ""),
            })
            state["session_log"] = state["session_log"][-200:]

            _save_state(state)
            posted += 1
            time.sleep(2)

        except Exception as e:
            print(f"[XEngage]   Post failed: {e}")

        time.sleep(1)

    return posted


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINTS
# ══════════════════════════════════════════════════════════════════════════════

def run_session(dry_run: bool = False,
                harvest_only: bool = False,
                engage_only: bool = False) -> None:
    state = _load_state()

    if not engage_only:
        harvested = harvest_feedback(state, dry_run=dry_run)
        _save_state(state)
        print(f"[XEngage] Harvest done: {harvested} feedback entry(ies) processed.")

    if not harvest_only:
        posted = engage(state, dry_run=dry_run)
        print(f"[XEngage] Engage done: {posted} reply(ies) posted "
              f"| Day total: {_daily_count(state)}/{MAX_REPLIES_PER_DAY}")


def print_status() -> None:
    state    = _load_state()
    log      = state.get("session_log", [])
    pending  = [e for e in state.get("pending_feedback", [])
                if not e.get("feedback_checked")]
    today_ct = _daily_count(state)

    print("=== OCTODAMUS X ENGAGE STATUS ===")
    print(f"Today's replies:          {today_ct}/{MAX_REPLIES_PER_DAY}")
    print(f"Pending feedback checks:  {len(pending)}")
    print(f"Total replies logged:     {len(log)}")

    if log:
        print("\nLast 5 replies:")
        for e in log[-5:]:
            print(f"  [{e['posted_at'][:16]}] @{e['account']}")
            print(f"    Original: {e['original'][:80]}")
            print(f"    Reply:    {e['reply'][:80]}")
            print(f"    URL:      {e.get('url','')}")

    cooldowns = state.get("account_last_reply", {})
    if cooldowns:
        now = datetime.now(timezone.utc)
        active = []
        for acct, last_str in sorted(cooldowns.items()):
            elapsed   = (now - datetime.fromisoformat(last_str)).total_seconds() / 3600
            remaining = ACCOUNT_COOLDOWN_HOURS - elapsed
            if remaining > 0:
                active.append((acct, remaining))
        if active:
            print("\nActive account cooldowns:")
            for acct, rem in active:
                print(f"  @{acct}: {rem:.1f}h remaining")


if __name__ == "__main__":
    args         = sys.argv[1:]
    dry_run      = "--dry-run" in args or "-d" in args
    harvest_only = "--harvest-only" in args
    engage_only  = "--engage-only" in args
    status       = "--status" in args

    if status:
        print_status()
    else:
        run_session(
            dry_run      = dry_run,
            harvest_only = harvest_only,
            engage_only  = engage_only,
        )

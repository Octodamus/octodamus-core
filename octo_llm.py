"""
octo_llm.py -- one place for Anthropic model choice, prompt caching and cost accounting.

Importing this module installs a usage meter on the Anthropic SDK. Every
messages.create() made anywhere in the process then appends one row to
data/llm_usage.jsonl with token counts and computed USD cost:

    import octo_llm            # near the top of an entry point

Nothing else needs to change for the meter to work. The caching helpers below
are opt-in and applied at the call sites that matter.

CLI:
    python octo_llm.py report          -- cost per day, last 7 days
    python octo_llm.py report 30       -- last 30 days
    python octo_llm.py today           -- today's spend, broken down by caller
"""
import json
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_DIR = Path(__file__).parent
USAGE_LOG   = PROJECT_DIR / "data" / "llm_usage.jsonl"

# ─────────────────────────────────────────────────────────────────────────────
# Models
#
# MODEL_SMART is Sonnet 5: $2/$10 per MTok against Sonnet 4.6's $3/$15 -- a
# newer model at two thirds the price. Sonnet 5 runs adaptive thinking when
# `thinking` is omitted (Sonnet 4.6 did not), so every migrated call site passes
# THINKING_OFF to keep the previous behaviour and not pay for reasoning tokens
# nothing was asking for. Raise it deliberately via effort (see EFFORT below).
# ─────────────────────────────────────────────────────────────────────────────
MODEL_SMART = "claude-sonnet-5"
MODEL_FAST  = "claude-haiku-4-5-20251001"

THINKING_OFF = {"type": "disabled"}

# Cost-quality knob for the smart tier. Effort scales reasoning depth and
# tool-call depth without changing the model. "high" is the API default and what
# every call site ran at before this module existed, so that is the default here
# too -- lowering it trades quality and wants an eval behind it first.
EFFORT = None  # None = API default (high); or "low" | "medium" | "high" | "xhigh" | "max"

# USD per million tokens: (input, output). Cache writes bill at 1.25x input on
# the default 5-minute TTL (2x on 1h), cache reads at 0.1x.
PRICING = {
    "claude-opus-5":              (5.00, 25.00),
    "claude-sonnet-5":            (2.00, 10.00),
    "claude-sonnet-4-6":          (3.00, 15.00),
    "claude-haiku-4-5":           (1.00,  5.00),
    "claude-haiku-4-5-20251001":  (1.00,  5.00),
}

CACHE_WRITE_MULT = 1.25
CACHE_READ_MULT  = 0.10


def price_of(model: str) -> tuple:
    """Input/output rate for a model, falling back to the closest family match."""
    if model in PRICING:
        return PRICING[model]
    for known, rate in PRICING.items():
        if model.startswith(known) or known.startswith(model):
            return rate
    return (3.00, 15.00)  # unknown model: assume mid-tier rather than free


def cost_of(model: str, usage) -> float:
    """USD for one response's usage object."""
    rate_in, rate_out = price_of(model)
    g = (lambda k: getattr(usage, k, 0) or 0) if not isinstance(usage, dict) else (lambda k: usage.get(k, 0) or 0)
    uncached   = g("input_tokens")
    cache_write = g("cache_creation_input_tokens")
    cache_read  = g("cache_read_input_tokens")
    out         = g("output_tokens")
    return (
        uncached    * rate_in  / 1e6
        + cache_write * rate_in  * CACHE_WRITE_MULT / 1e6
        + cache_read  * rate_in  * CACHE_READ_MULT  / 1e6
        + out         * rate_out / 1e6
    )


# ─────────────────────────────────────────────────────────────────────────────
# Prompt caching helpers
#
# Caching is a prefix match over tools -> system -> messages, so a breakpoint
# only pays when everything before it is byte-stable. Both helpers below mark
# the END of their block, which is the last position where the prefix is still
# identical across calls.
# ─────────────────────────────────────────────────────────────────────────────
def cache_system(system, ttl: str = "5m"):
    """
    Turn a system prompt into cached content blocks.

    Accepts a plain string or an existing list of blocks. The cache breakpoint
    goes on the last block, so anything volatile (a timestamp, today's prices)
    must be appended as a separate uncached block AFTER calling this -- see
    cache_system_then().
    """
    blocks = [{"type": "text", "text": system}] if isinstance(system, str) else list(system)
    if not blocks:
        return blocks
    blocks[-1] = dict(blocks[-1])
    cc = {"type": "ephemeral"}
    if ttl != "5m":
        cc["ttl"] = ttl
    blocks[-1]["cache_control"] = cc
    return blocks


def cache_system_then(stable: str, volatile: str = "", ttl: str = "5m"):
    """
    Cache the stable half of a system prompt and leave the volatile half outside
    the breakpoint. This is the shape that actually caches: a date or a live
    price string interpolated ahead of the breakpoint would invalidate the whole
    prefix on every call.
    """
    blocks = cache_system(stable, ttl=ttl)
    if volatile:
        blocks.append({"type": "text", "text": volatile})
    return blocks


def cache_if_stable(system, stable_prefix: str, ttl: str = "5m"):
    """
    Cache `stable_prefix` only when `system` actually starts with it.

    A cache breakpoint on a prompt that changes every call is not free -- the
    write bills at 1.25x and is never read, so blanket-caching a varying system
    prompt makes the bill worse. This checks first: a prompt that opens with the
    known-stable prefix gets split into cached prefix + uncached tail, and
    anything else is handed back untouched with no breakpoint at all.
    """
    if not isinstance(system, str) or not stable_prefix or not system.startswith(stable_prefix):
        return system
    return cache_system_then(stable_prefix, system[len(stable_prefix):], ttl=ttl)


def cache_tools(tools, ttl: str = "5m"):
    """
    Mark the end of a tool list as a cache breakpoint. Tool schemas render ahead
    of system and messages, so this caches the schemas for every later turn of a
    loop. The tool list must be in a stable order for this to hit.
    """
    if not tools:
        return tools
    out = [dict(t) for t in tools]
    cc = {"type": "ephemeral"}
    if ttl != "5m":
        cc["ttl"] = ttl
    out[-1]["cache_control"] = cc
    return out


def rolling_cache(messages):
    """
    Return a copy of an agent loop's message list with one cache breakpoint on
    the final message.

    A tool loop resends the whole conversation every turn, so without this the
    history is re-billed at full rate on each pass and cost grows with roughly
    the square of the turn count. Marking the last message means turn N reads
    turns 1..N-1 from cache and only writes the newest exchange.

    The breakpoint is moved rather than added: a request may carry at most four,
    and a loop that appends one per turn hits that ceiling and starts erroring.
    Nothing is mutated -- the caller's history stays free of cache markers, which
    keeps the bytes identical on the next turn and is what makes the prefix hit.
    """
    if not messages:
        return messages
    out = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            content = [
                {k: v for k, v in b.items() if k != "cache_control"} if isinstance(b, dict) else b
                for b in content
            ]
        out.append({**m, "content": content})

    last = out[-1]
    content = last["content"]
    if isinstance(content, str):
        last["content"] = [{"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}]
    elif isinstance(content, list) and content and isinstance(content[-1], dict):
        content[-1] = {**content[-1], "cache_control": {"type": "ephemeral"}}
    return out


def clip_tool_result(result, max_chars: int = 16000) -> str:
    """
    Bound a tool result before it enters an agent loop's history.

    Every byte a tool returns is re-sent on every later turn of the session, so
    one oversized result taxes the whole loop. x_sentiment_agent's read_core_memory
    returned a 317 KB file (~85k tokens) on turn 1 of every session -- $0.11 per
    read, and a prefix so large the 5-minute cache expired between turns. 16k
    chars (~4k tokens) is more than any tool here needs; anything past it is
    clipped from the MIDDLE so both the header and the freshest tail survive.
    """
    text = result if isinstance(result, str) else str(result)
    if len(text) <= max_chars:
        return text
    head = max_chars * 2 // 3
    tail = max_chars - head
    return (f"{text[:head]}\n\n[... {len(text) - max_chars:,} chars clipped by octo_llm.clip_tool_result "
            f"-- the tool returned {len(text):,} chars; ask for a narrower slice if you need the middle ...]\n\n"
            f"{text[-tail:]}")


def smart_kwargs(**extra) -> dict:
    """
    Standard keyword arguments for a smart-tier call: Sonnet 5 with thinking
    explicitly off, plus effort when EFFORT is set. Call sites spread these so a
    future effort sweep is one constant in this file, not an edit in 20 files.
    """
    kw = {"model": MODEL_SMART, "thinking": THINKING_OFF}
    if EFFORT:
        kw["output_config"] = {"effort": EFFORT}
    kw.update(extra)
    return kw


# ─────────────────────────────────────────────────────────────────────────────
# Batch processing -- 50% off every token, including cache reads and writes.
#
# Where this pays, and where it does not, on Octodamus's current mix:
#   NOT the agent loops   -- a batch request is single-shot, so a tool loop
#                            cannot run inside one.
#   NOT the scheduled posts -- results arrive within 24h, which is an expiry and
#                            not an SLA. A daily read that lands tomorrow is not
#                            a daily read.
#   YES for backfills, eval sweeps, scorecard recomputation, bulk drafting --
#                            anything with no reader waiting on it.
# Reach for it when one of those shows up; do not retrofit it onto a live post.
# ─────────────────────────────────────────────────────────────────────────────
def batch_submit(client, requests: list):
    """
    Submit [(custom_id, params), ...] as one batch. Returns the batch id.

    Each params dict is exactly what would go to messages.create(). Half price,
    asynchronous -- poll with batch_collect().
    """
    from anthropic.types.messages.batch_create_params import Request
    return client.messages.batches.create(
        requests=[Request(custom_id=cid, params=p) for cid, p in requests]
    ).id


def batch_collect(client, batch_id: str, poll_seconds: int = 30, timeout_seconds: int = 86400):
    """
    Block until a batch ends, then return {custom_id: message-or-None}.

    Results come back in arbitrary order, so they are keyed by custom_id --
    never by position.
    """
    waited = 0
    while True:
        batch = client.messages.batches.retrieve(batch_id)
        if batch.processing_status == "ended":
            break
        if waited >= timeout_seconds:
            raise TimeoutError(f"batch {batch_id} still {batch.processing_status} after {waited}s")
        time.sleep(poll_seconds)
        waited += poll_seconds

    out = {}
    for entry in client.messages.batches.results(batch_id):
        if entry.result.type == "succeeded":
            msg = entry.result.message
            out[entry.custom_id] = msg
            record(getattr(msg, "model", "?"), getattr(msg, "usage", None),
                   tag=f"batch:{entry.custom_id}")
        else:
            out[entry.custom_id] = None
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Usage meter
# ─────────────────────────────────────────────────────────────────────────────
_lock = threading.Lock()
_installed = False


def _caller_tag() -> str:
    """
    Best-effort attribution: the nearest octodamus frame outside this module.

    Every agent in .agents/ is a file called agent.py, so the bare stem would
    report all eight of them as "agent" and make their rows impossible to tell
    apart -- which matters, because several run at overlapping times. For those,
    use the directory name instead.
    """
    try:
        f = sys._getframe(1)
        while f:
            fn = f.f_code.co_filename
            if "octodamus" in fn and "octo_llm" not in fn and "anthropic" not in fn:
                p = Path(fn)
                stem = p.parent.name if p.stem == "agent" else p.stem
                return f"{stem}:{f.f_code.co_name}"
            f = f.f_back
    except Exception:
        pass
    return "unknown"


def record(model: str, usage, tag: str = "", ok: bool = True, error: str = ""):
    """Append one usage row. Never raises -- accounting must not break a caller."""
    try:
        g = (lambda k: getattr(usage, k, 0) or 0) if not isinstance(usage, dict) else (lambda k: usage.get(k, 0) or 0)
        row = {
            "ts":     datetime.now().isoformat(timespec="seconds"),
            "model":  model,
            "tag":    tag or _caller_tag(),
            "in":     g("input_tokens"),
            "cache_w": g("cache_creation_input_tokens"),
            "cache_r": g("cache_read_input_tokens"),
            "out":    g("output_tokens"),
            "usd":    round(cost_of(model, usage), 6),
        }
        if not ok:
            row["error"] = error[:200]
        USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            with open(USAGE_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
    except Exception:
        pass


def install():
    """
    Patch the Anthropic SDK so every messages.create() is metered. Idempotent,
    and safe to call from any entry point.
    """
    global _installed
    if _installed:
        return
    try:
        from anthropic.resources.messages import Messages, AsyncMessages
    except Exception:
        return

    def _wrap(cls, is_async: bool):
        original = cls.create

        if is_async:
            async def create(self, *args, **kwargs):
                resp = await original(self, *args, **kwargs)
                _meter(resp, kwargs)
                return resp
        else:
            def create(self, *args, **kwargs):
                resp = original(self, *args, **kwargs)
                _meter(resp, kwargs)
                return resp

        create.__wrapped__ = original
        cls.create = create

    def _meter(resp, kwargs):
        usage = getattr(resp, "usage", None)
        if usage is None:
            return
        record(kwargs.get("model") or getattr(resp, "model", "?"), usage, tag=_caller_tag())

    _wrap(Messages, False)
    try:
        _wrap(AsyncMessages, True)
    except Exception:
        pass
    _installed = True


# ─────────────────────────────────────────────────────────────────────────────
# Reporting
# ─────────────────────────────────────────────────────────────────────────────
def _rows(days: int):
    if not USAGE_LOG.exists():
        return []
    cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    out = []
    for line in USAGE_LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("ts", "") >= cutoff:
            out.append(r)
    return out


def report(days: int = 7):
    rows = _rows(days)
    if not rows:
        print(f"No usage recorded in the last {days} day(s). Is `import octo_llm` wired into the entry points?")
        return
    by_day = {}
    for r in rows:
        by_day.setdefault(r["ts"][:10], []).append(r)
    print(f"\n  ANTHROPIC SPEND -- last {days} day(s)")
    print("  " + "-" * 58)
    print(f"  {'date':<12}{'calls':>7}{'in':>11}{'cached':>10}{'out':>9}{'USD':>9}")
    total = 0.0
    for day in sorted(by_day):
        rs = by_day[day]
        usd = sum(r.get("usd", 0) for r in rs)
        total += usd
        print(f"  {day:<12}{len(rs):>7}{sum(r.get('in',0) for r in rs):>11,}"
              f"{sum(r.get('cache_r',0) for r in rs):>10,}"
              f"{sum(r.get('out',0) for r in rs):>9,}{usd:>9.2f}")
    print("  " + "-" * 58)
    n_days = max(1, len(by_day))
    print(f"  {'TOTAL':<12}{len(rows):>7}{'':>11}{'':>10}{'':>9}{total:>9.2f}")
    print(f"  average ${total / n_days:.2f}/day over {n_days} day(s) with traffic\n")

    cached = sum(r.get("cache_r", 0) for r in rows)
    fresh  = sum(r.get("in", 0) for r in rows) + sum(r.get("cache_w", 0) for r in rows)
    if cached + fresh:
        print(f"  cache hit rate: {100 * cached / (cached + fresh):.1f}% of input tokens served from cache")
        if cached == 0:
            print("  -> zero cache reads. Either the prefixes are not stable, or caching is not wired in.")
    print()


def today():
    day = datetime.now().strftime("%Y-%m-%d")
    rows = [r for r in _rows(2) if r.get("ts", "").startswith(day)]
    if not rows:
        print(f"No usage recorded today ({day}).")
        return
    by_tag = {}
    for r in rows:
        by_tag.setdefault(r.get("tag", "unknown"), []).append(r)
    print(f"\n  TODAY ({day}) -- by caller")
    print("  " + "-" * 58)
    for tag in sorted(by_tag, key=lambda t: -sum(r.get("usd", 0) for r in by_tag[t])):
        rs = by_tag[tag]
        print(f"  {tag:<38}{len(rs):>5} calls  ${sum(r.get('usd',0) for r in rs):>8.3f}")
    print("  " + "-" * 58)
    print(f"  {'TOTAL':<38}{len(rows):>5} calls  ${sum(r.get('usd',0) for r in rows):>8.3f}\n")


install()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    if cmd == "today":
        today()
    elif cmd == "report":
        report(int(sys.argv[2]) if len(sys.argv) > 2 else 7)
    else:
        print(__doc__)

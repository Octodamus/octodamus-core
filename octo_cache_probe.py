"""
octo_cache_probe.py -- prove prompt caching is actually working.

Sends the real production system prompt twice, byte-identical, and prints all
four usage meters for both calls. The second call MUST report
cache_read_input_tokens > 0. If it reports zero, something upstream is breaking
the prefix -- a timestamp interpolated above the breakpoint, a re-ordered tool
list, a model switch -- and the caching is costing 1.25x on every write while
never reading anything back.

Run it after any change to octo_personality, a system prompt, or a tool list:

    python octo_cache_probe.py            # X post prompt (~7k tokens)
    python octo_cache_probe.py telegram   # Telegram prompt (~8.9k tokens)

Exits non-zero when the cache does not hit, so it works as a check in a script.
"""
import json
import sys
from pathlib import Path

import anthropic

import octo_llm


def _client():
    secrets = json.loads(Path(__file__).parent.joinpath(".octo_secrets").read_text(encoding="utf-8"))
    key = secrets.get("secrets", secrets).get("ANTHROPIC_API_KEY", "")
    return anthropic.Anthropic(api_key=key)


def _meters(resp):
    u = resp.usage
    return {
        "input_tokens":               getattr(u, "input_tokens", 0) or 0,
        "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
        "cache_read_input_tokens":     getattr(u, "cache_read_input_tokens", 0) or 0,
        "output_tokens":              getattr(u, "output_tokens", 0) or 0,
    }


def probe(which: str = "x") -> int:
    if which == "telegram":
        from octo_personality import build_telegram_system_blocks
        stable, _ = build_telegram_system_blocks()
        label = "Telegram system prompt"
    else:
        from octo_personality import build_x_system_prompt
        stable = build_x_system_prompt()
        label = "X post system prompt"

    system = octo_llm.cache_system(stable)
    client = _client()
    print(f"\n  CACHE PROBE -- {label} (~{len(stable)//4:,} tokens), {octo_llm.MODEL_SMART}")
    print("  " + "-" * 68)

    results = []
    for n in (1, 2):
        r = client.messages.create(
            **octo_llm.smart_kwargs(max_tokens=5),
            system=system,
            messages=[{"role": "user", "content": "Reply with one word: ok"}],
        )
        m = _meters(r)
        results.append(m)
        print(f"  call {n}:  uncached={m['input_tokens']:>6,}  "
              f"cache_write={m['cache_creation_input_tokens']:>6,}  "
              f"cache_read={m['cache_read_input_tokens']:>6,}  out={m['output_tokens']:>3}"
              f"   ${octo_llm.cost_of(octo_llm.MODEL_SMART, m):.5f}")

    print("  " + "-" * 68)
    first, second = results
    hit = second["cache_read_input_tokens"] > 0
    if hit:
        c1 = octo_llm.cost_of(octo_llm.MODEL_SMART, first)
        c2 = octo_llm.cost_of(octo_llm.MODEL_SMART, second)
        print(f"  CACHE HIT -- second call read {second['cache_read_input_tokens']:,} tokens from cache")
        print(f"  cost fell ${c1:.5f} -> ${c2:.5f} ({100*(1-c2/c1):.0f}% cheaper on the repeat call)\n")
        return 0

    print("  CACHE MISS -- the second call read nothing from cache.")
    print("  The prefix is not byte-stable. Look for a timestamp or live value")
    print("  rendered above the breakpoint, a re-ordered tool list, or a model switch.\n")
    return 1


if __name__ == "__main__":
    sys.exit(probe(sys.argv[1] if len(sys.argv) > 1 else "x"))

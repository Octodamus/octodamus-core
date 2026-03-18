"""
patch_qwen.py
Swaps claude-haiku in generate_oracle_post() for Qwen3.5-Flash via OpenRouter.
Run once from ~/octodamus/
"""

import shutil
from pathlib import Path

TARGET = Path.home() / "octodamus" / "octo_eyes_market.py"
BACKUP = TARGET.with_suffix(".py.backup")

OLD = '''def generate_oracle_post(signal: dict) -> str:
    """Run OctoInk (Claude) on a signal to produce a market oracle post."""

    prompt = signal_to_octoink_prompt(signal)
    client = _get_client()

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",  # OctoInk — fast, cheap, sharp
        max_tokens=150,
        messages=[{"role": "user", "content": prompt}],
    )

    return response.content[0].text.strip()'''

NEW = '''def generate_oracle_post(signal: dict) -> str:
    """Run OctoInk (Qwen3.5-Flash via OpenRouter) on a signal to produce a market oracle post."""
    import os
    from qwen_client import get_qwen_client, qwen_complete

    prompt = signal_to_octoink_prompt(signal)
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    qwen = get_qwen_client(api_key)

    return qwen_complete(
        qwen,
        prompt=prompt,
        model="qwen/qwen3.5-flash-02-23",
        max_tokens=150,
    )'''

# Backup first
shutil.copy(TARGET, BACKUP)
print(f"✅ Backup saved to {BACKUP}")

content = TARGET.read_text()

if OLD not in content:
    print("❌ Could not find the target function — file may have changed.")
    print("   Check octo_eyes_market.py manually.")
    exit(1)

content = content.replace(OLD, NEW)
TARGET.write_text(content)
print("✅ Patch applied — generate_oracle_post() now uses Qwen3.5-Flash")
print("\nNext: add OPENROUTER_API_KEY to bitwarden.py, then test with:")
print("  python3 octodamus_runner.py --mode status")

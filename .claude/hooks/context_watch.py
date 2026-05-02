"""
Stop hook -- warns when session context is getting large.
Fires after every Claude response. Fast-exits if session is small.
"""
import json
import sys
from pathlib import Path

data = {}
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)

session_id = data.get("session_id", "")
if not session_id:
    sys.exit(0)

projects_dir = Path(r"C:\Users\walli\.claude\projects\C--Users-walli-octodamus")
jsonl_file = projects_dir / f"{session_id}.jsonl"

if not jsonl_file.exists():
    sys.exit(0)

# Fast exit if file is tiny (< 80KB = definitely fine)
file_size = jsonl_file.stat().st_size
if file_size < 80_000:
    sys.exit(0)

# Count actual content characters in the JSONL (more accurate than raw file size)
total_chars = 0
try:
    for line in jsonl_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
            content = entry.get("message", {}).get("content", "")
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        total_chars += len(block.get("text", ""))
        except Exception:
            continue
except Exception:
    sys.exit(0)

# ~4 chars per token (rough estimate)
est_tokens = total_chars // 4

WARN_AT   = 50_000   # yellow flag
URGENT_AT = 90_000   # red flag

if est_tokens >= URGENT_AT:
    k = est_tokens // 1000
    print(json.dumps({
        "systemMessage": f"[!!] Context ~{k}k tokens -- compact now to avoid cost spike: /compact"
    }))
elif est_tokens >= WARN_AT:
    k = est_tokens // 1000
    print(json.dumps({
        "systemMessage": f"[~] Context ~{k}k tokens -- consider /compact before the next big task"
    }))

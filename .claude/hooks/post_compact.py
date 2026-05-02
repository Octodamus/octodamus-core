"""
PostCompact hook -- fires after every /compact in this project.
1. Copies current project_state.md to Downloads as a snapshot.
2. Shows a systemMessage reminding Claude to update project_state.md.
"""
import json
import sys
import shutil
from pathlib import Path

data = json.load(sys.stdin)

src = Path(r"C:\Users\walli\octodamus\.claude\project_state.md")
dst = Path(r"C:\Users\walli\Downloads\octo_project_state.md")
if src.exists():
    shutil.copy2(src, dst)

msg = (
    "[PostCompact] Session compacted. Update .claude/project_state.md "
    "with any new builds, architecture decisions, pending work, oracle scorecard updates, "
    "or key module changes from this session. Then copy: "
    "cp '/c/Users/walli/octodamus/.claude/project_state.md' '/c/Users/walli/Downloads/octo_project_state.md'"
)

print(json.dumps({"systemMessage": msg}))

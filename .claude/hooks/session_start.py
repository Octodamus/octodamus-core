import json
import sys
from pathlib import Path

json.load(sys.stdin)

src = Path(r"C:\Users\walli\octodamus\.claude\project_state.md")
if src.exists():
    content = src.read_text(encoding="utf-8")
    msg = f"[SessionStart] Current project state:\n\n{content}"
else:
    msg = "[SessionStart] Warning: project_state.md not found."

print(json.dumps({"systemMessage": msg}))

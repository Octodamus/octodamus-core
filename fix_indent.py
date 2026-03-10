"""
fix_indent.py
Fixes IndentationError at line 132 in octodamus_runner.py
Run: C:\Python314\python.exe fix_indent.py
"""

import shutil

RUNNER_PATH = "octodamus_runner.py"

with open(RUNNER_PATH, "r", encoding="utf-8") as f:
    lines = f.readlines()

# Show context around line 131 so we can see the problem
print("Lines 125-140:")
for i, line in enumerate(lines[124:140], start=125):
    print(f"  {i:4d} | {line}", end="")

# The issue: patch inserted imports AFTER the anchor line, which was inside
# an if block body — leaving the if with no body before our try:.
# Find the empty if block pattern and insert a pass

fixed = []
i = 0
fixes = 0
while i < len(lines):
    line = lines[i]
    # Detect pattern: "if _XXXX_AVAILABLE:" immediately followed by non-indented "try:"
    # or followed by a blank line then try at wrong indent
    stripped = line.rstrip()
    if stripped.endswith(":") and "_AVAILABLE" in stripped and stripped.lstrip().startswith("if "):
        indent = len(line) - len(line.lstrip())
        fixed.append(line)
        i += 1
        # Look ahead: skip blank lines, check if next non-blank line is try: at same indent
        j = i
        while j < len(lines) and lines[j].strip() == "":
            j += 1
        if j < len(lines):
            next_line = lines[j]
            next_indent = len(next_line) - len(next_line.lstrip())
            next_stripped = next_line.strip()
            # If next code line is try: at SAME indent level as the if, it's broken
            if next_stripped.startswith("try:") and next_indent == indent:
                # Insert pass + blank line to satisfy the if body
                fixed.append(" " * (indent + 4) + "pass\n")
                fixes += 1
                print(f"\nFix applied at line {i}: inserted 'pass' after empty if block")
        continue
    fixed.append(line)
    i += 1

if fixes == 0:
    print("\nNo empty-if pattern found — trying alternate fix...")
    # Try a different approach: look for the exact broken pattern near line 131
    fixed = lines[:]
    for idx in range(max(0, 128), min(len(lines), 140)):
        l = lines[idx]
        if "_AVAILABLE" in l and l.strip().endswith(":") and l.strip().startswith("if "):
            indent = len(l) - len(l.lstrip())
            # Check next non-blank line
            j = idx + 1
            while j < len(lines) and lines[j].strip() == "":
                j += 1
            if j < len(lines):
                nxt = lines[j]
                nxt_indent = len(nxt) - len(nxt.lstrip())
                if nxt.strip().startswith("try:") and nxt_indent <= indent:
                    fixed.insert(j, " " * (indent + 4) + "pass\n")
                    fixes += 1
                    print(f"Alternate fix applied: inserted pass at position {j}")
                    break

shutil.copy2(RUNNER_PATH, RUNNER_PATH + ".bak_indent")

with open(RUNNER_PATH, "w", encoding="utf-8") as f:
    f.writelines(fixed)

print(f"\nTotal fixes: {fixes}")
print("Saved. Testing syntax...")

import subprocess
result = subprocess.run(
    [r"C:\Python314\python.exe", "-m", "py_compile", RUNNER_PATH],
    capture_output=True, text=True
)
if result.returncode == 0:
    print("✓ Syntax OK — runner compiles cleanly")
else:
    print(f"✗ Still errors:\n{result.stderr}")
    print("Restoring backup...")
    shutil.copy2(RUNNER_PATH + ".bak_indent", RUNNER_PATH)

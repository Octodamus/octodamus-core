"""
patch_bitwarden.py
Adds OPENROUTER_API_KEY to the secrets mapping in bitwarden.py.
Run once from ~/octodamus/
"""

from pathlib import Path

TARGET = Path.home() / "octodamus" / "bitwarden.py"
BACKUP = TARGET.with_suffix(".py.backup")

import shutil
shutil.copy(TARGET, BACKUP)
print(f"✅ Backup saved to {BACKUP}")

OLD = '    "AGENT - Octodamus - Social - Moltbook":             "MOLTBOOK_API_KEY",'
NEW = '    "AGENT - Octodamus - Social - Moltbook":             "MOLTBOOK_API_KEY",\n    "AGENT - Octodamus - OpenRouter":                    "OPENROUTER_API_KEY",'

content = TARGET.read_text()

if OLD not in content:
    print("❌ Could not find the target line — bitwarden.py may have changed.")
    exit(1)

content = content.replace(OLD, NEW)
TARGET.write_text(content)
print("✅ OPENROUTER_API_KEY added to bitwarden.py secrets mapping")
print("\nAll done! Test with:")
print("  python3 octodamus_runner.py --mode status")

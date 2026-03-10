"""
patch_bitwarden_four_minds.py

Adds FRED_API_KEY and ETHERSCAN_API_KEY to the Bitwarden credential loader.

Run ONCE from your project directory:
    C:\Python314\python.exe patch_bitwarden_four_minds.py

Expected Bitwarden item names (create these via `bw` CLI or vault UI):
    AGENT - Octodamus - FRED API        → FRED_API_KEY
    AGENT - Octodamus - Etherscan API   → ETHERSCAN_API_KEY

OctoWatch (Reddit) needs NO API key — it uses Reddit's public JSON endpoint.
"""

import os
import shutil

BW_PATH = "bitwarden.py"
BW_BACKUP = "bitwarden.py.bak"

# ─────────────────────────────────────────────
# Find the NEWSAPI entry and add after it
# ─────────────────────────────────────────────

# This anchor targets the last entry in the BW_ITEMS dict before closing brace
# Adjust if your bitwarden.py structure differs
ANCHOR = '"AGENT - Octodamus - Data - NewsAPI"'

NEW_ENTRIES = ''',
    # ── Four New Minds ────────────────────────
    "AGENT - Octodamus - FRED API":        "FRED_API_KEY",
    "AGENT - Octodamus - Etherscan API":   "ETHERSCAN_API_KEY",'''


def patch_bitwarden():
    if not os.path.exists(BW_PATH):
        print(f"ERROR: {BW_PATH} not found. Run from project directory.")
        return False

    with open(BW_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    if "FRED_API_KEY" in content:
        print("bitwarden.py already contains FRED_API_KEY — no patch needed.")
        return True

    if ANCHOR not in content:
        print(f"ERROR: Anchor '{ANCHOR}' not found in bitwarden.py.")
        print("Add these lines manually to your BW_ITEMS dict:")
        print('    "AGENT - Octodamus - FRED API":        "FRED_API_KEY",')
        print('    "AGENT - Octodamus - Etherscan API":   "ETHERSCAN_API_KEY",')
        return False

    shutil.copy2(BW_PATH, BW_BACKUP)
    print(f"✓ Backed up to {BW_BACKUP}")

    content = content.replace(ANCHOR, ANCHOR + NEW_ENTRIES)

    with open(BW_PATH, "w", encoding="utf-8") as f:
        f.write(content)

    print("✅ bitwarden.py patched — FRED and Etherscan keys added.")
    print("   Remember to create these items in your Bitwarden vault:")
    print('   → "AGENT - Octodamus - FRED API"       (password = your FRED API key)')
    print('   → "AGENT - Octodamus - Etherscan API"  (password = your Etherscan API key)')
    return True


if __name__ == "__main__":
    patch_bitwarden()

"""fix_gecko.py — fixes SyntaxError in octo_gecko.py __main__ block"""
import shutil

path = "octo_gecko.py"
shutil.copy2(path, path + ".bak")

with open(path, "r", encoding="utf-8") as f:
    content = f.read()

old = """    print(f"Gainers:  {[(c['symbol'], f\"{c['chg_24h']:+.1f}%\") for c in result['gainers']]}")
    print(f"Losers:   {[(c['symbol'], f\"{c['chg_24h']:+.1f}%\") for c in result['losers']]}")"""

new = """    gainers_str = [(c['symbol'], f"{c['chg_24h']:+.1f}%") for c in result['gainers']]
    losers_str  = [(c['symbol'], f"{c['chg_24h']:+.1f}%") for c in result['losers']]
    print(f"Gainers:  {gainers_str}")
    print(f"Losers:   {losers_str}")"""

if old in content:
    content = content.replace(old, new)
    print("Fix applied.")
else:
    print("Anchor not found — trying line scan...")
    lines = content.splitlines(keepends=True)
    fixed = []
    for line in lines:
        if "chg_24h']:+.1f" in line and 'f\\"' in line:
            sym = "gainers" if "gainers" in line else "losers"
            fixed.append(f'    {sym}_str = [(c[\'symbol\'], f"{{c[\'chg_24h\']:+.1f}}%") for c in result[\'{sym}\']]\n')
            fixed.append(f'    print(f"{sym.capitalize()}:  {{{sym}_str}}")\n')
            print(f"Line-scan fix applied for {sym}.")
        else:
            fixed.append(line)
    content = "".join(fixed)

with open(path, "w", encoding="utf-8") as f:
    f.write(content)

import subprocess
r = subprocess.run([r"C:\Python314\python.exe", "-m", "py_compile", path], capture_output=True, text=True)
if r.returncode == 0:
    print("✓ octo_gecko.py syntax OK")
else:
    print(f"✗ Still broken:\n{r.stderr}")
    shutil.copy2(path + ".bak", path)
    print("Restored backup.")

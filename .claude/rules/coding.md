# Coding Style — Octodamus

## General
- No unnecessary abstractions or helpers for one-off tasks
- No backwards-compat shims — change the code directly
- No docstrings/comments on code not being changed
- Prefer editing existing files over creating new ones
- Don't add error handling for scenarios that can't happen
- Don't add features beyond what was asked

## Windows-Specific
- Paths use raw strings: r'C:\Users\walli\octodamus\...'
- Avoid Unicode chars that crash Windows cp1252 stdout:
    → (U+2192)  use ->
    • (U+2022)  use -
    — (U+2014)  use --
- Always use encoding="utf-8" when reading/writing files
- Use forward slashes in bash commands (/c/Users/walli/...)
- Kill processes by PID with: taskkill //F //PID <n>

## Python
- Use pathlib.Path for file paths
- json.loads(path.read_text(encoding="utf-8")) for JSON reads
- subprocess calls: capture_output=True, text=True, encoding="utf-8"

## Secrets
- Secrets live in .octo_secrets (JSON) at project root
- Never hardcode keys; always read from secrets file or env

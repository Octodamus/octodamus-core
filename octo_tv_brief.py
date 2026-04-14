"""
octo_tv_brief.py
TradingView chart context for Octodamus.

Connects to TradingView Desktop (must be running with CDP on port 9222)
via the tradingview-mcp-jackson Node.js server, reads chart data for
watchlist symbols, and returns a formatted string for the prompt.

Returns empty string silently if TradingView is not running — runner
continues without chart data.
"""

import json
import subprocess
import sys
from pathlib import Path

_TV_MCP_DIR = Path(r"C:\Users\walli\tradingview-mcp-jackson")
_RULES_PATH = _TV_MCP_DIR / "rules.json"
_RUNNER_SCRIPT = _TV_MCP_DIR / "run_brief.js"
_TIMEOUT = 45  # seconds — each symbol takes ~2s


def get_tv_brief() -> str:
    """
    Run the TradingView morning brief and return a formatted string.
    Returns "" if TradingView is not running or brief fails.
    """
    if not _RUNNER_SCRIPT.exists():
        return ""

    try:
        result = subprocess.run(
            ["node", "--experimental-vm-modules", str(_RUNNER_SCRIPT), str(_RULES_PATH)],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            cwd=str(_TV_MCP_DIR),
        )
        if result.returncode != 0:
            # TradingView probably not running — silent fail
            return ""

        data = json.loads(result.stdout.strip())
        if not data.get("success"):
            return ""

        return _format_brief(data)

    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        return ""
    except Exception:
        return ""


def _format_brief(data: dict) -> str:
    """Format raw brief JSON into a compact prompt string."""
    symbols = data.get("symbols_scanned", data.get("symbols", []))
    if not symbols:
        return ""

    lines = ["TRADINGVIEW CHART DATA (4H):"]
    for s in symbols:
        sym = s.get("symbol", "?")
        if s.get("error"):
            continue

        quote = s.get("quote", {})
        state = s.get("state", {})
        indicators = s.get("indicators", s.get("studies", []))

        price = quote.get("last") or quote.get("close") or quote.get("last_price")
        chg = quote.get("change_percent")

        parts = [f"  {sym}:"]
        if price:
            parts.append(f"price={price:.2f}" if isinstance(price, float) else f"price={price}")
        if chg is not None:
            parts.append(f"chg={chg:+.1f}%" if isinstance(chg, float) else f"chg={chg}")

        # Include up to 3 indicator values
        ind_parts = []
        for ind in indicators[:3]:
            name = ind.get("name", "")
            val = ind.get("value")
            if name and val is not None:
                if isinstance(val, float):
                    ind_parts.append(f"{name}={val:.2f}")
                else:
                    ind_parts.append(f"{name}={val}")
        if ind_parts:
            parts.append(" | ".join(ind_parts))

        lines.append(" ".join(parts))

    if len(lines) <= 1:
        return ""

    instruction = data.get("instruction", "")
    if instruction:
        lines.append(f"\nChart bias instruction: {instruction}")

    return "\n".join(lines)


if __name__ == "__main__":
    brief = get_tv_brief()
    if brief:
        print(brief)
    else:
        print("[octo_tv_brief] TradingView not available or no data.")

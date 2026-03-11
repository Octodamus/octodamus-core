"""
bitwarden.py
Octodamus â€” Bitwarden Secrets Manager
All API keys are stored in Bitwarden. This module fetches them at runtime.

Requirements:
    - Bitwarden CLI installed: https://bitwarden.com/help/cli/
    - Logged in: bw login
    - BW_SESSION env var set (from: bw unlock --raw)

Setup (one time per session):
    $env:BW_SESSION = (bw unlock --raw)

Bitwarden item names (Octodamus vault):
    "AGENT - Octodamus - Brain - Anthropic"
    "AGENT - Octodamus - Financial Datasets API"
    "AGENT - Octodamus - Social - OpenTweet"
    "AGENT - Octodamus - Control - Telegram"
    "AGENT - Octodamus - Search - Tavily"
    "AGENT - Octodamus - Deploy - Vercel"
    "AGENT - Octodamus - Domain - Cloudflare"
    "AGENT - Octodamus - Payments - Stripe - Products"
    "AGENT - Octodamus - Payments - Stripe - Readonly"
    "AGENT - Octodamus - Social - Moltbook"
    "AGENT - Octodamus - Data - NewsAPI"
    "AGENT - Octodamus - OpenRouter"
    "AGENT - Octodamus - OctoData Admin Key"
    "me: AGENT - Octodamus - Finance - Bankr - Wallet"
    "AGENT - Octodamus - FRED API"         (free: fred.stlouisfed.org)
    "AGENT - Octodamus - Etherscan API"    (free: etherscan.io/apis)
"""

import subprocess
import json
import os
import sys


# FIX: Do NOT read BW_SESSION at module load time.
# os.environ["BW_SESSION"] may not be set yet when this module is imported.
# Always read it at call time inside _bw() so the value is always fresh.


# Full path to bw CLI â€” required because Python subprocess can't find .cmd files on PATH
import sys as _sys
BW_CMD = "/home/walli/.local/bin/bw" if _sys.platform == "linux" else r"C:\Users\walli\AppData\Roaming\npm\bw.cmd"


def _bw(args: list) -> str:
    """Run a Bitwarden CLI command and return stdout."""
    bw_session = os.environ.get("BW_SESSION")
    if not bw_session:
        raise EnvironmentError(
            "[Bitwarden] BW_SESSION not set.\n"
            "Run: $env:BW_SESSION = (bw unlock --raw)\n"
            "Then restart your agent."
        )
    result = subprocess.run(
        [BW_CMD] + args + ["--session", bw_session],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"[Bitwarden] CLI error: {result.stderr.strip()}")
    return result.stdout.strip()


def get_secret(item_name: str) -> str:
    """
    Retrieve the password field from a Bitwarden item by name.
    This is where API keys are stored.
    """
    raw = _bw(["get", "item", item_name])
    item = json.loads(raw)
    password = item.get("login", {}).get("password")
    if not password:
        raise ValueError(f"[Bitwarden] No password found for item: '{item_name}'")
    return password


def get_note(item_name: str) -> str:
    """Retrieve a secure note from Bitwarden (for multi-field secrets)."""
    raw = _bw(["get", "item", item_name])
    item = json.loads(raw)
    return item.get("notes", "")


def get_custom_field(item_name: str, field_name: str) -> str:
    """Retrieve a specific custom field from a Bitwarden item."""
    raw = _bw(["get", "item", item_name])
    item = json.loads(raw)
    fields = item.get("fields", [])
    for field in fields:
        if field.get("name", "").lower() == field_name.lower():
            return field.get("value", "")
    raise ValueError(f"[Bitwarden] Field '{field_name}' not found in item '{item_name}'")


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# OCTODAMUS SECRETS â€” fetch all at startup
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

# Bitwarden item name â†’ env var name mapping
# Names match exactly what's stored in the Octodamus Bitwarden vault
OCTODAMUS_SECRETS = {
    # â”€â”€ Core infrastructure â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    "AGENT - Octodamus - Brain - Anthropic":             "ANTHROPIC_API_KEY",
    "AGENT - Octodamus - Financial Datasets API":        "FINANCIAL_DATASETS_API_KEY",
    "AGENT - Octodamus - Social - OpenTweet":            "OPENTWEET_API_KEY",
    "AGENT - Octodamus - Control - Telegram":            "TELEGRAM_BOT_TOKEN",
    "AGENT - Octodamus - Search - Tavily":               "TAVILY_API_KEY",
    "AGENT - Octodamus - Deploy - Vercel":               "VERCEL_API_KEY",
    "AGENT - Octodamus - Domain - Cloudflare":           "CLOUDFLARE_API_KEY",
    "AGENT - Octodamus - Payments - Stripe - Products":  "STRIPE_PRODUCTS_API_KEY",
    "AGENT - Octodamus - Payments - Stripe - Readonly":  "STRIPE_READONLY_API_KEY",
    "AGENT - Octodamus - Social - Moltbook":             "MOLTBOOK_API_KEY",
    # â”€â”€ Data & content â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    "AGENT - Octodamus - Data - NewsAPI":                "NEWSAPI_API_KEY",
    "AGENT - Octodamus - OpenRouter":                    "OPENROUTER_API_KEY",
    "AGENT - Octodamus - OctoData Admin Key":            "OCTODATA_ADMIN_KEY",
    # â”€â”€ Four New Minds â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    "AGENT - Octodamus - FRED API":                      "FRED_API_KEY",
    "AGENT - Octodamus - Open Exchange Rates":           "OPENEXCHANGERATES_API_KEY",
    "AGENT - Octodamus - Etherscan API":                 "ETHERSCAN_API_KEY",
}

# Optional secrets â€” only loaded if the item exists in Bitwarden
OCTODAMUS_OPTIONAL_SECRETS = {
    "me: AGENT - Octodamus - Finance - Bankr - Wallet":  "BANKR_API_KEY",
}


def load_twitter_secrets() -> None:
    """Load Twitter API credentials from Bitwarden notes field."""
    try:
        raw = _bw(["get", "item", "AGENT - Octodamus - Social - Twitter API"])
        item = json.loads(raw)
        login = item.get("login", {})
        os.environ["TWITTER_API_KEY"] = login.get("username", "")
        os.environ["TWITTER_API_SECRET"] = login.get("password", "")
        notes = item.get("notes", "") or ""
        for line in notes.splitlines():
            if ":" in line:
                key, _, val = line.partition(":")
                val = val.strip()
                k = key.strip().upper().replace(" ", "_")
                if k == "BEARER_TOKEN":
                    os.environ["TWITTER_BEARER_TOKEN"] = val
                elif k == "ACCESS_TOKEN" and "SECRET" not in k.upper():
                    os.environ["TWITTER_ACCESS_TOKEN"] = val
                elif k == "ACCESS_TOKEN_SECRET":
                    os.environ["TWITTER_ACCESS_TOKEN_SECRET"] = val
                elif k == "CLIENT_ID":
                    os.environ["TWITTER_CLIENT_ID"] = val
                elif k == "CLIENT_SECRET":
                    os.environ["TWITTER_CLIENT_SECRET"] = val
        print("[Bitwarden] ✓ Loaded: AGENT - Octodamus - Social - Twitter API")
    except Exception as e:
        print(f"[Bitwarden] ⚠ Twitter API secrets not loaded: {e}")

# Critical secrets â€” hard exit if any are missing
OCTODAMUS_CRITICAL_KEYS = {
    "ANTHROPIC_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "OPENTWEET_API_KEY",
    "STRIPE_PRODUCTS_API_KEY",
}


def load_all_secrets(verbose: bool = False) -> dict:
    """
    Fetch all Octodamus secrets from Bitwarden and inject into os.environ.
    Call this once at the top of any Octodamus entry point.

    Hard exits on missing critical secrets.
    Warns (no exit) on missing non-critical secrets.

    Returns dict of {env_var: value} for all loaded secrets.
    """
    loaded = {}
    missing_critical = []

    # Required secrets
    for item_name, env_var in OCTODAMUS_SECRETS.items():
        try:
            value = get_secret(item_name)
            os.environ[env_var] = value
            loaded[env_var] = value
            if verbose:
                print(f"[Bitwarden] âœ“ Loaded: {item_name}")
        except Exception as e:
            if env_var in OCTODAMUS_CRITICAL_KEYS:
                print(f"[Bitwarden] âœ— CRITICAL secret missing: {item_name}\n  â†’ {e}")
                missing_critical.append(env_var)
            else:
                print(f"[Bitwarden] âš  Non-critical secret missing: {item_name}\n  â†’ {e}")

    # Optional secrets
    for item_name, env_var in OCTODAMUS_OPTIONAL_SECRETS.items():
        try:
            value = get_secret(item_name)
            os.environ[env_var] = value
            loaded[env_var] = value
            if verbose:
                print(f"[Bitwarden] âœ“ Loaded (optional): {item_name}")
        except Exception:
            if verbose:
                print(f"[Bitwarden] â€“ Skipped (not found): {item_name}")

    load_twitter_secrets()

    # Hard exit if any critical secrets are missing
    if missing_critical:
        print(f"\n[Bitwarden] FATAL: Missing critical secrets: {missing_critical}")
        print("Check your Bitwarden vault entries and retry.")
        sys.exit(1)

    # SECURITY: Unset BW_SESSION immediately after loading all credentials.
    # Keys are now in os.environ. The vault session token is no longer needed.
    if "BW_SESSION" in os.environ:
        del os.environ["BW_SESSION"]
    if verbose:
        print("[Bitwarden] âœ… BW_SESSION cleared. Vault session token removed from environment.")
        print(f"[Bitwarden] âœ… {len(loaded)} secrets loaded into environment.")

    return loaded


def verify_session() -> bool:
    """Check that BW_SESSION is valid before starting."""
    try:
        _bw(["status"])
        return True
    except EnvironmentError as e:
        print(f"[Bitwarden] {e}")
        return False
    except Exception as e:
        print(f"[Bitwarden] Session invalid: {e}")
        print("Run: $env:BW_SESSION = (bw unlock --raw)")
        return False


"""
bitwarden.py
Octodamus — Bitwarden Secrets Manager
All API keys are stored in Bitwarden. This module fetches them at runtime.

Requirements:
    - Bitwarden CLI installed: https://bitwarden.com/help/cli/
    - Logged in: bw login
    - BW_SESSION env var set (from: bw unlock --raw)

Setup (one time per session):
    export BW_SESSION=$(bw unlock --raw)

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
    "me: AGENT - Octodamus - Finance - Bankr - Wallet"
"""

import subprocess
import json
import os
import sys

# BW_SESSION must be set in environment (from: bw unlock --raw)
BW_SESSION = os.environ.get("BW_SESSION")


def _bw(args: list) -> str:
    """Run a Bitwarden CLI command and return stdout."""
    if not BW_SESSION:
        raise EnvironmentError(
            "[Bitwarden] BW_SESSION not set.\n"
            "Run: export BW_SESSION=$(bw unlock --raw)\n"
            "Then restart your agent."
        )
    result = subprocess.run(
        ["C:\\Users\\walli\\AppData\\Roaming\\npm\\bw.cmd"] + args + ["--session", BW_SESSION],
        capture_output=True,
        text=True
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


# ─────────────────────────────────────────────
# OCTODAMUS SECRETS — fetch all at startup
# ─────────────────────────────────────────────

# Bitwarden item name → env var name mapping
# Names match exactly what's stored in the Octodamus Bitwarden vault
OCTODAMUS_SECRETS = {
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
}

# Optional secrets — only loaded if the item exists in Bitwarden
OCTODAMUS_OPTIONAL_SECRETS = {
    "me: AGENT - Octodamus - Finance - Bankr - Wallet": "BANKR_API_KEY",
}


def load_all_secrets(verbose: bool = False) -> dict:
    """
    Fetch all Octodamus secrets from Bitwarden and inject into os.environ.
    Call this once at the top of any Octodamus entry point.

    Returns dict of {env_var: value} for all loaded secrets.
    """
    loaded = {}

    # Required secrets
    for item_name, env_var in OCTODAMUS_SECRETS.items():
        try:
            value = get_secret(item_name)
            os.environ[env_var] = value
            loaded[env_var] = value
            if verbose:
                print(f"[Bitwarden] ✓ Loaded: {item_name}")
        except Exception as e:
            print(f"[Bitwarden] ✗ REQUIRED secret missing: {item_name}\n  → {e}")
            sys.exit(1)  # Can't run without required secrets

    # Optional secrets
    for item_name, env_var in OCTODAMUS_OPTIONAL_SECRETS.items():
        try:
            value = get_secret(item_name)
            os.environ[env_var] = value
            loaded[env_var] = value
            if verbose:
                print(f"[Bitwarden] ✓ Loaded (optional): {item_name}")
        except Exception:
            if verbose:
                print(f"[Bitwarden] – Skipped (not found): {item_name}")

    # SECURITY: Unset BW_SESSION immediately after loading all credentials
    # Keys are now in os.environ. The vault session token is no longer needed.
    # This matches STARTUP_SCRIPT.sh v3 security fix (audit item #7).
    if "BW_SESSION" in os.environ:
        del os.environ["BW_SESSION"]
    if verbose:
        print("[Bitwarden] ✅ BW_SESSION cleared. Vault session token removed from environment.")

    return loaded


def verify_session():
    """Check that BW_SESSION is valid before starting."""
    try:
        _bw(["status"])
        return True
    except Exception as e:
        print(f"[Bitwarden] Session invalid: {e}")
        print("Run: export BW_SESSION=$(bw unlock --raw)")
        return False

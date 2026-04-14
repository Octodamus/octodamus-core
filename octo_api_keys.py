"""
octo_api_keys.py - Octodamus API Key Management
Handles key generation, validation, tier enforcement, and rate limiting.

Tiers:
  free    - get_signal summary, sentiment, who_is_octodamus, track_record
            50 requests/day
  premium - all tools + raw derivatives, liquidation maps, full CLOB data
            unlimited requests, $29/year

Usage:
  python octo_api_keys.py create --email user@example.com --tier free
  python octo_api_keys.py create --email user@example.com --tier premium
  python octo_api_keys.py list
  python octo_api_keys.py revoke --key octo_xxxxx
"""

import json
import secrets
import hashlib
import argparse
import logging
from datetime import datetime, timezone, date
from pathlib import Path

log = logging.getLogger("OctoKeys")

KEYS_FILE = Path(r"C:\Users\walli\octodamus\data\api_keys.json")
USAGE_FILE = Path(r"C:\Users\walli\octodamus\data\api_usage.json")

FREE_DAILY_LIMIT = 50
PREMIUM_DAILY_LIMIT = 99999

# Which tools each tier can access
TIER_TOOLS = {
    "free": {
        "get_signal",
        "get_market_sentiment",
        "who_is_octodamus",
        "get_track_record",
        "get_active_calls",
    },
    "premium": {
        "get_signal",
        "get_market_sentiment",
        "who_is_octodamus",
        "get_track_record",
        "get_active_calls",
        "get_market_brief",
        "get_prediction",
        "get_news",
        "get_raw_derivatives",
        "get_liquidation_map",
        "get_funding_history",
        "get_clob_data",
        "get_call_archive",
    },
    "internal": None,  # None = all tools, no limit
}


def _load_keys() -> dict:
    try:
        if KEYS_FILE.exists():
            return json.loads(KEYS_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_keys(data: dict):
    KEYS_FILE.parent.mkdir(parents=True, exist_ok=True)
    KEYS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _load_usage() -> dict:
    try:
        if USAGE_FILE.exists():
            return json.loads(USAGE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_usage(data: dict):
    USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    USAGE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


# ── Public API ────────────────────────────────────────────────────────────────

def create_key(email: str, tier: str = "free", label: str = "") -> str:
    """Generate a new API key. Returns the raw key (shown once)."""
    if tier not in TIER_TOOLS:
        raise ValueError(f"Unknown tier: {tier}. Use: {list(TIER_TOOLS.keys())}")

    raw_key = f"octo_{secrets.token_urlsafe(32)}"
    key_hash = _hash_key(raw_key)

    keys = _load_keys()
    keys[key_hash] = {
        "email":      email,
        "tier":       tier,
        "label":      label,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "active":     True,
    }
    _save_keys(keys)
    log.info(f"Created {tier} key for {email}")
    return raw_key


def validate_key(raw_key: str) -> dict | None:
    """
    Validate a raw API key. Returns key record or None if invalid/inactive.
    """
    if not raw_key or not raw_key.startswith("octo_"):
        return None
    key_hash = _hash_key(raw_key)
    keys = _load_keys()
    record = keys.get(key_hash)
    if not record or not record.get("active", False):
        return None
    return record


def check_rate_limit(raw_key: str, tier: str) -> tuple[bool, int, int]:
    """
    Check if key is within its daily rate limit.
    Returns: (allowed, used_today, limit)
    """
    if tier == "internal":
        return True, 0, PREMIUM_DAILY_LIMIT

    limit = FREE_DAILY_LIMIT if tier == "free" else PREMIUM_DAILY_LIMIT
    today = date.today().isoformat()
    key_hash = _hash_key(raw_key)

    usage = _load_usage()
    key_usage = usage.get(key_hash, {})
    day_usage = key_usage.get(today, 0)

    allowed = day_usage < limit
    return allowed, day_usage, limit


def record_usage(raw_key: str, tool_name: str):
    """Increment usage counter for this key today."""
    today = date.today().isoformat()
    key_hash = _hash_key(raw_key)

    usage = _load_usage()
    if key_hash not in usage:
        usage[key_hash] = {}
    if today not in usage[key_hash]:
        usage[key_hash][today] = 0
    usage[key_hash][today] += 1
    _save_usage(usage)


def can_access_tool(tier: str, tool_name: str) -> bool:
    """Check if tier has access to a specific tool."""
    allowed = TIER_TOOLS.get(tier)
    if allowed is None:
        return True  # internal = all tools
    return tool_name in allowed


def revoke_key(raw_key: str) -> bool:
    """Deactivate an API key."""
    key_hash = _hash_key(raw_key)
    keys = _load_keys()
    if key_hash in keys:
        keys[key_hash]["active"] = False
        _save_keys(keys)
        return True
    return False


def list_keys() -> list[dict]:
    """List all keys (without raw key values)."""
    keys = _load_keys()
    result = []
    for h, record in keys.items():
        result.append({
            "hash_prefix": h[:8] + "...",
            **record,
        })
    return result


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Octodamus API Key Manager")
    sub = parser.add_subparsers(dest="cmd")

    c = sub.add_parser("create", help="Create a new API key")
    c.add_argument("--email", required=True)
    c.add_argument("--tier",  default="free", choices=["free", "premium", "internal"])
    c.add_argument("--label", default="")

    sub.add_parser("list", help="List all API keys")

    r = sub.add_parser("revoke", help="Revoke an API key")
    r.add_argument("--key", required=True)

    args = parser.parse_args()

    if args.cmd == "create":
        key = create_key(args.email, args.tier, args.label)
        print(f"\nAPI Key created ({args.tier} tier):")
        print(f"  {key}")
        print(f"\nSave this — it will not be shown again.")
        print(f"Free tier: {FREE_DAILY_LIMIT} requests/day | Tools: {sorted(TIER_TOOLS['free'])}")

    elif args.cmd == "list":
        keys = list_keys()
        if not keys:
            print("No keys found.")
        for k in keys:
            status = "ACTIVE" if k["active"] else "REVOKED"
            print(f"  [{status}] {k['tier']:8} {k['email']} (created {k['created_at'][:10]})")

    elif args.cmd == "revoke":
        if revoke_key(args.key):
            print("Key revoked.")
        else:
            print("Key not found.")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()

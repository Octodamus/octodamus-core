"""
bitwarden.py
Octodamus â€” Secrets Manager

Two modes:
  Interactive  BW_SESSION is set â†’ fetch from Bitwarden vault, save to cache
  Background   No BW_SESSION    â†’ load from .octo_secrets cache file

Setup (run once after each reboot):
    powershell -File octo_unlock.ps1
    â€” OR manually â€”
    $env:BW_SESSION = (bw unlock --raw)
    C:\\Python314\\python.exe octodamus_runner.py --mode monitor

Bitwarden item names:
    AGENT - Octodamus - Brain - Anthropic
    AGENT - Octodamus - Financial Datasets API
    AGENT - Octodamus - Control - Telegram
    AGENT - Octodamus - Search - Tavily
    AGENT - Octodamus - Deploy - Vercel
    AGENT - Octodamus - Domain - Cloudflare
    AGENT - Octodamus - Payments - Stripe - Products
    AGENT - Octodamus - Payments - Stripe - Readonly
    AGENT - Octodamus - Social - Moltbook
    AGENT - Octodamus - Data - NewsAPI
    AGENT - Octodamus - OpenRouter
    AGENT - Octodamus - OctoData Admin Key
    AGENT - Octodamus - FRED API
    AGENT - Octodamus - Open Exchange Rates
    AGENT - Octodamus - Etherscan API
    AGENT - Octodamus - Social - Twitter API   (username=API Key, password=API Secret, notes=rest)
    AGENT - Octodamus - Social - Discord       (password=webhook URL)
    AGENT - Octodamus - Finance - Bankr - Wallet         (optional, wins over above if both exist)
    AGENT - Octodamus - POLYBACKTEST - API Key
    AGENT - Octodamus - Firecrawl API
    AGENT - Octodamus - Finnhub API
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# CONSTANTS
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

import shutil as _shutil
import sys as _sys
BW_CMD = "bw" if _sys.platform != "win32" else (_shutil.which("bw.cmd") or r"C:\Users\walli\AppData\Roaming\npm\bw.cmd")
CACHE_FILE   = Path(__file__).parent / ".octo_secrets"
CACHE_MAX_AGE_HOURS = 23  # warn if cache older than this

# Bitwarden item name â†’ env var mapping
OCTODAMUS_SECRETS = {
    "AGENT - Octodamus - Brain - Anthropic":            "ANTHROPIC_API_KEY",
    "AGENT - Octodamus - Financial Datasets API":       "FINANCIAL_DATASETS_API_KEY",
    # OpenTweet retired — X API v2 (tweepy) uses TWITTER_* keys below
    # "AGENT - Octodamus - Social - OpenTweet":          "OPENTWEET_API_KEY",
    "AGENT - Octodamus - Control - Telegram":           "TELEGRAM_BOT_TOKEN",
    "AGENT - Octodamus - OctoBoto":                       "OCTOBOTO_TELEGRAM_TOKEN",
    "AGENT - Octodamus - Search - Tavily":              "TAVILY_API_KEY",
    "AGENT - Octodamus - Deploy - Vercel":              "VERCEL_API_KEY",
    "AGENT - Octodamus - Domain - Cloudflare":          "CLOUDFLARE_API_KEY",
    "AGENT - Octodamus - Payments - Stripe - Products": "STRIPE_PRODUCTS_API_KEY",
    "AGENT - Octodamus - Payments - Stripe - Readonly": "STRIPE_READONLY_API_KEY",
    "AGENT - Octodamus - Social - Moltbook":            "MOLTBOOK_API_KEY",
    "AGENT - Octodamus - Data - NewsAPI":               "NEWSAPI_API_KEY",
    "AGENT - Octodamus - OpenRouter":                   "OPENROUTER_API_KEY",
    "AGENT - Octodamus - OctoData Admin Key":           "OCTODATA_ADMIN_KEY",
    "AGENT - Octodamus - FRED API":                     "FRED_API_KEY",
    "AGENT - Octodamus - Open Exchange Rates":          "OPEN_EXCHANGE_RATES_API_KEY",
    "AGENT - Octodamus - Etherscan API":                "ETHERSCAN_API_KEY",
    "AGENT - Octodamus - Quiver API":                   "QUIVER_API_KEY",
    "AGENT - Octodamus - Social - Discord":             "DISCORD_WEBHOOK_URL",
"AGENT - Octodamus - API - Coinglass":              "COINGLASS_API_KEY",
    "AGENT - Octodamus - POLYBACKTEST - API Key":       "POLYBACKTEST_API_KEY",
    "AGENT - Octodamus - OctoData - Stripe Price ID":  "OCTODATA_STRIPE_PRICE_ID",
    "AGENT - Octodamus - OctoData - Stripe Webhook":   "OCTODATA_STRIPE_WEBHOOK_SECRET",
    "AGENT - Octodamus - Guide - Download URL":         "GUIDE_DOWNLOAD_URL",
    "AGENT - Octodamus - Firecrawl API":               "FIRECRAWL_API_KEY",
    "AGENT - Octodamus - Finnhub API":                 "FINNHUB_API_KEY",
    "AGENT - Octodamus - LunarCrush - API":            "LUNARCRUSH_API_KEY",
    "AGENT - Octodamus - xAI Grok API":               "GROK_API_KEY",
    # Limitless loaded separately below (needs both username + password)
}

OCTODAMUS_OPTIONAL_SECRETS = {
    "AGENT - Octodamus - Finance - Bankr":          "BANKR_API_KEY",
    "AGENT - Octodamus - Finance - Bankr - Wallet": "BANKR_API_KEY",  # wins if both exist — user keeps this one updated
}

FRANKLIN_BW_ITEM   = "Franklin Agent_Ben"
FRANKLIN_KEY_FILE  = Path.home() / ".blockrun" / ".session"
FRANKLIN_CHAIN_FILE = Path.home() / ".blockrun" / "payment-chain"

OCTODAMUS_CRITICAL_KEYS = {
    "ANTHROPIC_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "OCTOBOTO_TELEGRAM_TOKEN",
}


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# BITWARDEN CLI
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _bw(args: list) -> str:
    bw_session = os.environ.get("BW_SESSION")
    if not bw_session:
        raise EnvironmentError("BW_SESSION not set")
    result = subprocess.run(
        [BW_CMD] + args + ["--session", bw_session],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"BW CLI error: {result.stderr.strip()}")
    return result.stdout.strip()


def _get_item(item_name: str) -> dict:
    raw = _bw(["get", "item", item_name])
    return json.loads(raw)


def _get_password(item_name: str) -> str:
    item = _get_item(item_name)
    pw = item.get("login", {}).get("password", "")
    if not pw:
        raise ValueError(f"No password in '{item_name}'")
    return pw


def _get_username(item_name: str) -> str:
    item = _get_item(item_name)
    return item.get("login", {}).get("username", "")


def _get_notes(item_name: str) -> str:
    item = _get_item(item_name)
    return item.get("notes", "") or ""


def _get_custom_fields(item_name: str) -> dict:
    """Read all custom fields from a Bitwarden item as {field_name: value}."""
    item = _get_item(item_name)
    result = {}
    for f in (item.get("fields") or []):
        name  = (f.get("name")  or "").strip()
        value = (f.get("value") or "").strip()
        if name and value:
            result[name] = value
    return result


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# TWITTER SECRETS (multi-field item)
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _load_twitter_from_bw() -> dict:
    """
    Twitter item layout:
      username â†’ API Key (Consumer Key)
      password â†’ API Secret (Consumer Secret)
      notes    â†’ key: value pairs, one per line
    """
    item_name = "AGENT - Octodamus - Social - Twitter API"
    secrets = {}
    try:
        item = _get_item(item_name)
        login = item.get("login", {})
        secrets["TWITTER_API_KEY"]    = login.get("username", "")
        secrets["TWITTER_API_SECRET"] = login.get("password", "")

        notes = item.get("notes", "") or ""
        for line in notes.splitlines():
            line = line.strip()
            if ":" in line:
                key, _, val = line.partition(":")
                key = key.strip().upper().replace(" ", "_")
                val = val.strip()
                if "BEARER" in key:
                    secrets["TWITTER_BEARER_TOKEN"] = val
                elif "ACCESS_TOKEN_SECRET" in key or ("ACCESS" in key and "SECRET" in key):
                    secrets["TWITTER_ACCESS_TOKEN_SECRET"] = val
                elif "ACCESS_TOKEN" in key or ("ACCESS" in key and "TOKEN" in key):
                    secrets["TWITTER_ACCESS_TOKEN"] = val
                elif "CLIENT_SECRET" in key:
                    secrets["TWITTER_CLIENT_SECRET"] = val
                elif "CLIENT_ID" in key:
                    secrets["TWITTER_CLIENT_ID"] = val
    except Exception as e:
        print(f"[Bitwarden] âš  Twitter secrets failed: {e}")
    return secrets


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# SECRETS CACHE
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

# -- Gmail multi-field loader --------------------------------------------------

def _load_franklin_from_bw() -> dict:
    """
    Franklin Agent wallet — custom fields in Bitwarden item.
    Writes private key to ~/.blockrun/.session so Franklin can use it after reboot.
    Fields expected: 'wallet address', 'private key' (case-insensitive).
    """
    secrets = {}
    try:
        fields = _get_custom_fields(FRANKLIN_BW_ITEM)
        # Normalise field names to lowercase for matching
        normalised = {k.lower().replace(" ", "_"): v for k, v in fields.items()}
        address = normalised.get("wallet_address") or normalised.get("address") or ""
        pk      = normalised.get("private_key")    or normalised.get("privatekey") or ""
        if address:
            secrets["FRANKLIN_WALLET_ADDRESS"] = address
        if pk:
            secrets["FRANKLIN_PRIVATE_KEY"] = pk
            # Restore wallet key file so Franklin works after reboot
            FRANKLIN_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
            FRANKLIN_KEY_FILE.write_text(pk + "\n", encoding="utf-8")
            FRANKLIN_CHAIN_FILE.write_text("base\n", encoding="utf-8")
    except Exception as e:
        print(f"[Bitwarden] Franklin wallet (non-critical): {e}")
    return secrets


def _load_kalshi_from_bw() -> dict:
    """
    Kalshi API item layout:
      username -> Key ID (UUID)          (KALSHI_KEY_ID)
      notes    -> RSA private key (PEM)  (KALSHI_PRIVATE_KEY)
    """
    secrets = {}
    try:
        item   = _get_item("AGENT - Octodamus - Kalshi API")
        login  = item.get("login", {})
        notes  = (item.get("notes") or "").strip()
        key_id = login.get("username", "").strip()
        if key_id:
            secrets["KALSHI_KEY_ID"] = key_id
        if "-----BEGIN" in notes:
            secrets["KALSHI_PRIVATE_KEY"] = notes
        elif login.get("password", "").strip():
            # fallback: PEM in password field
            secrets["KALSHI_PRIVATE_KEY"] = login["password"].strip()
    except Exception:
        pass
    return secrets


def _load_limitless_from_bw() -> dict:
    """
    Limitless Exchange API item layout:
      username -> API token ID   (LIMITLESS_API_KEY)
      password -> HMAC secret    (LIMITLESS_API_SECRET, base64)
    """
    secrets = {}
    try:
        item  = _get_item("AGENT - Octodamus - Limitless API")
        login = item.get("login", {})
        token_id = login.get("username", "")
        secret   = login.get("password", "")
        if token_id:
            secrets["LIMITLESS_API_KEY"]    = token_id
        if secret:
            secrets["LIMITLESS_API_SECRET"] = secret
    except Exception as e:
        pass  # non-critical, agent tells Ben how to set it up if missing
    return secrets


def _load_gmail_from_bw() -> dict:
    """
    Octodamus Gmail item layout:
      username -> Gmail address  (GMAIL_USER)
      password -> App Password   (GMAIL_APP_PASSWORD)
    """
    secrets = {}
    try:
        item = _get_item("Octodamus Gmail")
        login = item.get("login", {})
        secrets["GMAIL_USER"]         = login.get("username", "")
        secrets["GMAIL_APP_PASSWORD"] = login.get("password", "")
    except Exception as e:
        print(f"[Bitwarden] Gmail failed (non-critical): {e}")
    return secrets


def _save_cache(secrets: dict) -> None:
    """Write secrets to local cache file for background tasks."""
    cache = {
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "secrets": secrets,
    }
    CACHE_FILE.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    print(f"[Bitwarden] âœ… Secrets cached to {CACHE_FILE.name}")


def _load_cache() -> dict | None:
    """Load secrets from cache. Returns None if missing or too old."""
    if not CACHE_FILE.exists():
        return None
    try:
        cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        saved_at = datetime.fromisoformat(cache["saved_at"])
        age_hours = (datetime.now(timezone.utc) - saved_at).total_seconds() / 3600
        if age_hours > CACHE_MAX_AGE_HOURS:
            print(f"[Bitwarden] âš  Cache is {age_hours:.0f}h old. Run octo_unlock.ps1 to refresh.")
        return cache.get("secrets", {})
    except Exception as e:
        print(f"[Bitwarden] Cache read error: {e}")
        return None


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# MAIN: LOAD ALL SECRETS
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def load_all_secrets(verbose: bool = False) -> dict:
    """
    Load all Octodamus secrets into os.environ.

    Priority:
      1. Bitwarden vault (if BW_SESSION is set) â†’ also saves cache
      2. .octo_secrets cache file (background tasks)
      3. Existing os.environ values (already loaded)
    """
    has_session = bool(os.environ.get("BW_SESSION"))

    if has_session:
        return _load_from_bitwarden(verbose=verbose)
    else:
        return _load_from_cache(verbose=verbose)


def _load_from_bitwarden(verbose: bool = False) -> dict:
    """Fetch secrets from Bitwarden vault, save cache, inject into env."""
    loaded = {}
    missing_critical = []

    # Standard secrets (password field)
    for item_name, env_var in OCTODAMUS_SECRETS.items():
        try:
            value = _get_password(item_name)
            os.environ[env_var] = value
            loaded[env_var] = value
            if verbose:
                print(f"[Bitwarden] âœ“ {item_name}")
        except Exception as e:
            if env_var in OCTODAMUS_CRITICAL_KEYS:
                print(f"[Bitwarden] CRITICAL missing: {item_name} | error: {e}")
                missing_critical.append(env_var)
            else:
                if verbose:
                    print(f"[Bitwarden] Optional missing: {item_name} | error: {e}")

    # Optional secrets
    for item_name, env_var in OCTODAMUS_OPTIONAL_SECRETS.items():
        try:
            value = _get_password(item_name)
            os.environ[env_var] = value
            loaded[env_var] = value
            if verbose:
                print(f"[Bitwarden] âœ“ {item_name} (optional)")
        except Exception:
            pass

    # Twitter multi-field item
    twitter = _load_twitter_from_bw()
    for env_var, value in twitter.items():
        if value:
            os.environ[env_var] = value
            loaded[env_var] = value
    if twitter and verbose:
        print(f"[Bitwarden] âœ“ AGENT - Octodamus - Social - Twitter API")

    # Coinbase CDP API — Key ID (UUID ~36 chars) + Private Key (PEM, long)
    try:
        import re as _re
        item   = _get_item("AGENT - Octodamus - Coinbase CDP API")
        login  = item.get("login", {})
        notes  = (item.get("notes") or "").strip()
        fields = {(f.get("name") or "").strip(): (f.get("value") or "").strip()
                  for f in (item.get("fields") or [])}
        all_values = list(fields.values()) + [login.get("username",""), login.get("password",""), notes]

        # Key ID = UUID or short string (≤50 chars), Private Key = PEM or long string (>50 chars)
        cdp_id     = ""
        cdp_secret = ""
        for v in all_values:
            if not v:
                continue
            if len(v) <= 50 and not cdp_id:        # UUID / short key ID
                cdp_id = v
            elif len(v) > 50 and not cdp_secret:   # PEM private key or long secret
                cdp_secret = v

        # Also check notes for PEM key block
        if not cdp_secret and "-----BEGIN" in notes:
            cdp_secret = notes

        if cdp_id:
            os.environ["CDP_API_KEY_ID"] = cdp_id
            loaded["CDP_API_KEY_ID"] = cdp_id
        if cdp_secret:
            os.environ["CDP_API_KEY_SECRET"] = cdp_secret
            loaded["CDP_API_KEY_SECRET"] = cdp_secret
        if cdp_id and cdp_secret and verbose:
            print(f"[Bitwarden] Coinbase CDP API loaded (id={cdp_id[:8]}... secret={len(cdp_secret)}chars)")
        elif verbose:
            print(f"[Bitwarden] CDP API: id={'found' if cdp_id else 'missing'} secret={'found' if cdp_secret else 'missing'} | fields={list(fields.keys())} | notes_len={len(notes)}")
    except Exception as _e:
        if verbose:
            print(f"[Bitwarden] CDP API (non-critical): {_e}")

    # Kalshi API (username=key_id, notes=RSA private key PEM)
    kalshi = _load_kalshi_from_bw()
    for env_var, value in kalshi.items():
        if value:
            os.environ[env_var] = value
            loaded[env_var] = value
    if kalshi.get("KALSHI_KEY_ID") and verbose:
        print("[Bitwarden] Kalshi API loaded")

    # Limitless Exchange API (username=token_id, password=secret)
    limitless = _load_limitless_from_bw()
    for env_var, value in limitless.items():
        if value:
            os.environ[env_var] = value
            loaded[env_var] = value
    if limitless.get("LIMITLESS_API_KEY") and verbose:
        print("[Bitwarden] Limitless API loaded")

    # Gmail multi-field item
    gmail = _load_gmail_from_bw()
    for env_var, value in gmail.items():
        if value:
            os.environ[env_var] = value
            loaded[env_var] = value
    if gmail.get("GMAIL_USER") and verbose:
        print("[Bitwarden] Gmail loaded")

    # Franklin agent wallet (custom fields — writes ~/.blockrun/.session on load)
    franklin = _load_franklin_from_bw()
    for env_var, value in franklin.items():
        if value:
            os.environ[env_var] = value
            loaded[env_var] = value
    if franklin.get("FRANKLIN_WALLET_ADDRESS") and verbose:
        print(f"[Bitwarden] Franklin wallet loaded ({franklin.get('FRANKLIN_WALLET_ADDRESS','')})")

    # Fallback: try existing cache for any critical keys that BW failed to load
    if missing_critical:
        try:
            if CACHE_FILE.exists():
                raw = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
                cached = raw.get("secrets", raw)
                still_missing = []
                for env_var in missing_critical:
                    if cached.get(env_var):
                        loaded[env_var] = cached[env_var]
                        os.environ[env_var] = cached[env_var]
                        print(f"[Bitwarden] {env_var} recovered from existing cache")
                    else:
                        still_missing.append(env_var)
                missing_critical = still_missing
        except Exception:
            pass
    if missing_critical:
        print(f"[Bitwarden] FATAL: Missing critical secrets: {missing_critical}")
        sys.exit(1)

    # Clear session token + save cache
    if "BW_SESSION" in os.environ:
        del os.environ["BW_SESSION"]
    if verbose:
        print(f"[Bitwarden] âœ… {len(loaded)} secrets loaded from vault")

    _save_cache(loaded)
    return loaded


def _load_from_cache(verbose: bool = False) -> dict:
    """Load secrets from .octo_secrets cache (background task mode)."""
    cached = _load_cache()
    if not cached:
        print("[Bitwarden] âœ— No cache found. Run: powershell -File octo_unlock.ps1")
        sys.exit(1)

    loaded = {}
    for env_var, value in cached.items():
        if value:
            os.environ[env_var] = value
            loaded[env_var] = value

    # Check critical keys
    for key in OCTODAMUS_CRITICAL_KEYS:
        if key not in loaded:
            print(f"[Bitwarden] âœ— CRITICAL key missing from cache: {key}")
            sys.exit(1)

    if verbose:
        print(f"[Bitwarden] âœ… {len(loaded)} secrets loaded from cache")

    return loaded


def verify_session() -> bool:
    """Check BW_SESSION is valid. Returns True if interactive mode available."""
    bw_session = os.environ.get("BW_SESSION")
    if not bw_session:
        # Check if cache exists as fallback
        if CACHE_FILE.exists():
            return True
        print("[Bitwarden] No BW_SESSION and no cache. Run: powershell -File octo_unlock.ps1")
        return False
    try:
        _bw(["status"])
        return True
    except Exception as e:
        print(f"[Bitwarden] Session invalid: {e}")
        return False

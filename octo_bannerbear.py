"""
octo_bannerbear.py — Branded Image Generator for Octodamus
Auto-generates oracle call cards and signal images via Bannerbear API.

Use cases:
  - Oracle call announcement card (asset, direction, price, signal count)
  - Weekly scorecard image
  - Branded chart overlay for X posts

Bitwarden key: BANNERBEAR_API_KEY
Template UID:  BANNERBEAR_TEMPLATE_UID  (oracle call card template)
Get free key (30 images/mo): bannerbear.com

SETUP REQUIRED — Create template on bannerbear.com:
  1. Sign up at bannerbear.com
  2. New Template → start from blank (1200x675px recommended for X)
  3. Add these text layers with EXACT names:
       asset        — e.g. "BTC"
       direction    — e.g. "UP" or "DOWN"
       entry_price  — e.g. "$72,000"
       signals      — e.g. "9 / 11 signals"
       timeframe    — e.g. "48H"
       date         — e.g. "APR 09"
       tagline      — e.g. "Oracle Call"
  4. Style however you like (dark background recommended)
  5. Copy the Template UID from the URL or API section
  6. Add to Bitwarden: BANNERBEAR_TEMPLATE_UID = <your-uid>

Usage:
    from octo_bannerbear import generate_oracle_card
    img_url = generate_oracle_card("BTC", "UP", 72000, signals=9, timeframe="48H")
"""

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

# ── Config ────────────────────────────────────────────────────────────────────

BANNERBEAR_API   = "https://api.bannerbear.com/v2"
POLL_INTERVAL    = 2      # seconds between status polls
POLL_MAX_TRIES   = 15     # give up after 30s
CACHE_DIR        = Path(__file__).parent / "data" / "bannerbear_cache"


# ── Core API ──────────────────────────────────────────────────────────────────

def _get_api_key() -> str:
    return os.environ.get("BANNERBEAR_API_KEY", "")


def _get_template_uid() -> str:
    return os.environ.get("BANNERBEAR_TEMPLATE_UID", "")


def _create_image(template_uid: str, modifications: list) -> dict:
    """
    POST to Bannerbear to create an image.
    Returns the API response dict (status may be 'pending').
    """
    api_key = _get_api_key()
    if not api_key:
        raise RuntimeError("No BANNERBEAR_API_KEY in environment.")

    r = httpx.post(
        f"{BANNERBEAR_API}/images",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type":  "application/json",
        },
        json={
            "template":      template_uid,
            "modifications": modifications,
            "synchronous":   False,  # async — we poll for completion
        },
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def _poll_image(uid: str) -> str | None:
    """
    Poll until image is ready. Returns image_url or None on timeout/failure.
    """
    api_key = _get_api_key()
    for _ in range(POLL_MAX_TRIES):
        time.sleep(POLL_INTERVAL)
        try:
            r = httpx.get(
                f"{BANNERBEAR_API}/images/{uid}",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=10,
            )
            data = r.json()
            status = data.get("status")
            if status == "completed":
                return data.get("image_url") or data.get("image_url_png")
            elif status == "failed":
                print(f"[Bannerbear] Image generation failed: {data}")
                return None
        except Exception as e:
            print(f"[Bannerbear] Poll error: {e}")
    print("[Bannerbear] Timed out waiting for image.")
    return None


def _render_image(template_uid: str, modifications: list) -> str | None:
    """Create image and wait for URL. Returns URL or None."""
    try:
        resp = _create_image(template_uid, modifications)
        uid  = resp.get("uid")
        if not uid:
            print(f"[Bannerbear] No UID in response: {resp}")
            return None

        # If already completed (synchronous fallback)
        if resp.get("status") == "completed":
            return resp.get("image_url") or resp.get("image_url_png")

        return _poll_image(uid)

    except Exception as e:
        print(f"[Bannerbear] Render failed: {e}")
        return None


# ── Oracle Call Card ──────────────────────────────────────────────────────────

def generate_oracle_card(
    asset:     str,
    direction: str,
    entry_price: float,
    signals:   int   = 0,
    total_signals: int = 11,
    timeframe: str   = "48H",
    template_uid: str = "",
) -> str | None:
    """
    Generate a branded oracle call card image.

    Args:
        asset:         "BTC", "ETH", "SOL"
        direction:     "UP" or "DOWN"
        entry_price:   e.g. 72000.0
        signals:       number of bullish/bearish signals (e.g. 9)
        total_signals: denominator (default 11)
        timeframe:     "48H", "24H", etc.
        template_uid:  override env var BANNERBEAR_TEMPLATE_UID

    Returns:
        Image URL string, or None if generation failed.
    """
    uid = template_uid or _get_template_uid()
    if not uid:
        print("[Bannerbear] No template UID — set BANNERBEAR_TEMPLATE_UID in env.")
        return None

    now       = datetime.now(timezone.utc)
    date_str  = now.strftime("%b %d").upper()
    price_str = f"${entry_price:,.0f}"
    dir_str   = direction.upper()
    sig_str   = f"{signals} / {total_signals} signals" if signals else ""

    modifications = [
        {"name": "asset",       "text": asset.upper()},
        {"name": "direction",   "text": dir_str},
        {"name": "entry_price", "text": price_str},
        {"name": "signals",     "text": sig_str},
        {"name": "timeframe",   "text": timeframe},
        {"name": "date",        "text": date_str},
        {"name": "tagline",     "text": "Oracle Call"},
    ]

    # Optional: color the direction layer green/red
    color = "#00C853" if dir_str == "UP" else "#D50000"
    modifications.append({"name": "direction", "color": color})

    print(f"[Bannerbear] Generating oracle card: {asset} {dir_str} {price_str}...")
    url = _render_image(uid, modifications)
    if url:
        print(f"[Bannerbear] Card ready: {url}")
    return url


# ── Download image bytes (for X media upload) ─────────────────────────────────

def download_image(image_url: str) -> bytes | None:
    """Download Bannerbear image as bytes for uploading to Twitter."""
    try:
        r = httpx.get(image_url, timeout=20, follow_redirects=True)
        r.raise_for_status()
        return r.content
    except Exception as e:
        print(f"[Bannerbear] Download failed: {e}")
        return None


# ── Post oracle card to X ─────────────────────────────────────────────────────

def post_oracle_card_to_x(
    tweet_id:    str,
    asset:       str,
    direction:   str,
    entry_price: float,
    signals:     int = 0,
    timeframe:   str = "48H",
) -> bool:
    """
    Generate oracle card and post it as a reply to the oracle call tweet.
    Integrates with octo_x_poster._upload_media_v1 for Twitter media upload.
    Returns True if posted successfully.
    """
    try:
        img_url = generate_oracle_card(asset, direction, entry_price, signals, timeframe=timeframe)
        if not img_url:
            return False

        img_bytes = download_image(img_url)
        if not img_bytes:
            return False

        from octo_x_poster import _upload_media_v1, _get_client, _log_post
        media_id = _upload_media_v1(img_bytes)

        client  = _get_client()
        caption = f"{asset} Oracle Call — {direction} from {entry_price:,.0f}"
        resp    = client.create_tweet(
            text=caption,
            media_ids=[media_id],
            in_reply_to_tweet_id=tweet_id,
        )
        reply_id  = str(resp.data["id"])
        reply_url = f"https://x.com/octodamusai/status/{reply_id}"
        print(f"[Bannerbear] Oracle card posted: {reply_url}")
        _log_post(caption, {"type": "oracle_card", "asset": asset, "direction": direction})
        return True

    except Exception as e:
        print(f"[Bannerbear] Post failed (non-fatal): {e}")
        return False


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    asset     = sys.argv[1] if len(sys.argv) > 1 else "BTC"
    direction = sys.argv[2] if len(sys.argv) > 2 else "UP"
    price     = float(sys.argv[3]) if len(sys.argv) > 3 else 72000

    if not _get_api_key():
        print("Set BANNERBEAR_API_KEY in environment first.")
        sys.exit(1)
    if not _get_template_uid():
        print("Set BANNERBEAR_TEMPLATE_UID in environment first.")
        print("See setup instructions at top of this file.")
        sys.exit(1)

    url = generate_oracle_card(asset, direction, price, signals=9, timeframe="48H")
    if url:
        print(f"\nImage URL: {url}")
    else:
        print("Generation failed.")

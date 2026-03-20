"""
octo_metrics_updater.py
Push live metrics to Octodamus API after each post/event.
Call update_followers(n) or update_guide_sales(n, revenue) from runner.
"""
import os
import requests
from pathlib import Path

API_BASE = "https://api.octodamus.com"

def _get_key():
    try:
        from bitwarden import _load_cache
        return _load_cache().get("OCTODATA_ADMIN_KEY", "")
    except Exception:
        return os.getenv("OCTODATA_ADMIN_KEY", "")

def update_followers(count: int):
    try:
        key = _get_key()
        r = requests.post(f"{API_BASE}/api/metrics",
            params={"followers": count},
            headers={"x-api-key": key},
            timeout=5)
        print(f"[Metrics] Followers updated: {count}")
    except Exception as e:
        print(f"[Metrics] Failed: {e}")

def update_guide_sales(sales: int, revenue: float):
    try:
        key = _get_key()
        r = requests.post(f"{API_BASE}/api/metrics",
            params={"guide_sales": sales, "guide_revenue": revenue},
            headers={"x-api-key": key},
            timeout=5)
        print(f"[Metrics] Guide sales updated: {sales} / ${revenue}")
    except Exception as e:
        print(f"[Metrics] Failed: {e}")
